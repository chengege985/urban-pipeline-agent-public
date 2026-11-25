"""Stage explicitly selected 179 source files for review; never overwrite or push."""
from __future__ import annotations
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]
FRONTENDS = {'main': '02_比赛作品', 'ui-enterprise-01': '02_比赛作品-ui-enterprise-01'}
SUFFIXES = {'.py','.ts','.tsx','.js','.mjs','.cjs','.json','.jsonl','.md','.txt','.css','.html','.toml','.yaml','.yml','.ini','.cfg','.inp','.svg','.woff2'}
FORBIDDEN = {'..','.git','.env','node_modules','dist','release','release_upload','__pycache__','outputs','deploy','01_原始材料','02_核查证据','05_RESTRICTED_MANIFEST'}

def validate_path(value: str) -> str:
    if not value or '\\' in value or re.search(r'[<>:"|?*]', value):
        raise ValueError('Use an explicit repository-relative POSIX file path')
    p=PurePosixPath(value)
    if p.is_absolute() or any(x in FORBIDDEN or x.startswith(('.env','.venv')) or x.endswith((' ','.')) or re.match(r'(?i)^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)', x) for x in p.parts):
        raise ValueError('Rejected source path: '+value)
    prefixes=('engineering/src/','engineering/tests/','engineering/configs/','engineering/ui/',
              'engineering/data/synthetic/','engineering/data/agent/eval/',
              'engineering/experiments/algorithm_validation/','engineering/04_实施方案/02_接口与数据/',
              'web/','server/','public_demo_data/')
    if not value.startswith(prefixes) or p.suffix.lower() not in SUFFIXES:
        raise ValueError('Outside the code/contract/synthetic allowlist: '+value)
    return p.as_posix()

def remote_path(path: str, source: str) -> str:
    if source not in FRONTENDS: raise ValueError('Unknown server worktree')
    path=validate_path(path)
    return ('01_工程项目/'+path[len('engineering/'):]) if path.startswith('engineering/') else FRONTENDS[source]+'/'+path

def decode_result(item: dict) -> bytes:
    data=base64.b64decode(item['data'],validate=True)
    if len(data)!=item['size'] or hashlib.sha256(data).hexdigest()!=item['sha256']:
        raise ValueError('Transport SHA/size mismatch')
    if re.search(rb'-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{50,}',data):
        raise ValueError('Possible credential content; rejected without printing it')
    return data

def is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',choices=FRONTENDS,required=True)
    p.add_argument('--paths',nargs='+',required=True)
    a=p.parse_args()
    paths=list(dict.fromkeys(validate_path(x) for x in a.paths))
    if len({x.casefold() for x in paths})!=len(paths):
        p.error('Selected paths collide on a case-insensitive filesystem')
    current=json.loads((ROOT/'docs/SOURCE_MANIFEST.json').read_text(encoding='utf-8'))
    expected='frontend-main' if a.source=='main' else 'frontend-ui-v3'
    if current['frontend_snapshot']!=expected:
        p.error('Current repository baseline does not match the selected server worktree')
    for rel in paths:
        target=ROOT/rel
        if not target.resolve().is_relative_to(ROOT) or any(is_link(x) for x in [target,*target.parents] if x!=ROOT.parent):
            p.error('Local path may not use links or escape the repository')
    request={x:remote_path(x,a.source) for x in paths}
    # Only this fixed command reaches the shell; paths travel as stdin data/code literals.
    remote='''import base64,hashlib,json,pathlib
root=pathlib.Path('/workdata/WaterPipNet').resolve()
request=json.loads(REQUEST)
results={}
for local,relative in request.items():
    path=root/relative
    if not path.resolve().is_relative_to(root): raise ValueError('Remote path escape')
    cursor=root
    for part in pathlib.PurePosixPath(relative).parts:
        cursor=cursor/part
        if cursor.is_symlink(): raise ValueError('Remote symlink refused')
    if not path.exists():
        results[local]={'status':'missing'}; continue
    if not path.is_file() or path.stat().st_size>16*1024*1024: raise ValueError('File type/size refused')
    before=path.stat(); data=path.read_bytes(); after=path.stat()
    if (before.st_mtime_ns,before.st_size)!=(after.st_mtime_ns,after.st_size): raise ValueError('Source changed during read')
    results[local]={'status':'ok','size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'data':base64.b64encode(data).decode()}
for local,relative in request.items():
    item=results[local]
    if item['status']=='ok' and hashlib.sha256((root/relative).read_bytes()).hexdigest()!=item['sha256']: raise ValueError('Batch changed during read')
print(json.dumps(results))
'''.replace('REQUEST',repr(json.dumps(request,ensure_ascii=False)))
    proc=subprocess.run(['ssh','-p','8888','-o','BatchMode=yes','-o','ConnectTimeout=15','root@172.30.60.179',
                         'cd /workdata/WaterPipNet && python3 -'],input=remote.encode('utf-8'),capture_output=True,timeout=120)
    if proc.returncode:
        raise RuntimeError('SSH export failed; no repository files were changed. '+proc.stderr.decode('utf-8',errors='replace')[-1200:])
    results=json.loads(proc.stdout)
    if set(results)!=set(paths): raise ValueError('Unexpected response paths')
    verified={rel:decode_result(item) for rel,item in results.items() if item['status']=='ok'}
    # Output is a new ignored staging directory. No product/source/manifest/Git state is written.
    base=ROOT/'.server-sync'
    if is_link(base) or not base.resolve().is_relative_to(ROOT):raise ValueError('Staging path escape')
    base.mkdir(exist_ok=True)
    stage=base/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:6])
    stage.mkdir()
    for rel in paths:
        item=results[rel]
        if item['status']=='missing':
            print('MISSING (local retained): '+rel);continue
        target=stage/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(verified[rel])
        local=ROOT/rel
        same=local.is_file() and hashlib.sha256(local.read_bytes()).hexdigest()==item['sha256']
        print(('UNCHANGED: ' if same else 'REVIEW DIFF: ')+rel)
    print('Staged at: '+str(stage))
    print('Review with git diff --no-index -- <local-file> <staged-file>. Merge colleague changes first.')
    print('No local source overwrite, deletion, commit or push was performed.')

if __name__=='__main__':main()
