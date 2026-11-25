#!/usr/bin/env python3
"""Bounded, loopback-only gas development supervisor. Never a production service."""
from __future__ import annotations
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import re
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from urllib.request import ProxyHandler, Request, build_opener

REPO=Path(__file__).resolve().parents[1]
GATEWAY_SHA="caf64df4d8f6e0fe5a2d794c75f88ca46fa3aef8"
GATEWAY_ARCHIVE = Path("/workdata/WaterPipNet/01_工程项目/experiments/gas_oneclick_20261117/gateway-source.tar.gz")
GATEWAY_ARCHIVE_SHA256 = "a4dc55af9b00e314d42807e6e1af81b584dc10e00242d383ac635113d04033c1"
GATEWAY_SOURCE_FILES = {
    "gateway/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "gateway/app.py": "517388bff2ad2bdd29efe360a31d89081e9836f01f52e2a1620e589c6c6551a9",
    "gateway/static/admin.js": "d93fea64435d8c693591deb84ed5a0ba9f235cdfe508a71b6f2a0fd3ce49cfbc",
    "gateway/static/index.html": "2b9c6e12216e11c55d1bcb82d2d898aa337629a8888a7267fee5a9ddf831121c",
}
MODEL_METADATA={'config.json': '191e0af232104ed8b65258cf3fb2b842e288008baca7633c11b82a1ac7203aab', 'model.safetensors.index.json': '77042094076611b69791a610065f28b7013b8c621795fa86ddccc8bac7d1b9df', 'tokenizer_config.json': 'b11349aafa7cdc6a320767cf7ceb29ed82f7eda5d65e8e0819e76f0ce947bf27'}
RESERVED={24073,24081}
stop_event=threading.Event()
loopback_http=build_opener(ProxyHandler({}))

def private_root(path: Path)->Path:
    path=path.absolute()
    if path.is_symlink():raise RuntimeError("runtime symlink forbidden")
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    info=path.stat()
    if info.st_uid!=os.geteuid() or info.st_mode&0o077:raise RuntimeError("runtime must be owned by current user and mode 700")
    # Object storage does not provide a safe online DB/secret directory.
    fs=subprocess.check_output(["stat","-f","-c","%T",str(path)],text=True).strip()
    if fs.startswith(("fuse","nfs")):raise RuntimeError("runtime needs private local storage, not object/NFS storage")
    return path.resolve()

def write_json(path:Path,value):
    tmp=path.with_suffix(".tmp")
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,"w") as f:json.dump(value,f,ensure_ascii=False,indent=2)
    os.replace(tmp,path)

def read_json(path:Path):
    try:return json.loads(path.read_text())
    except (OSError,ValueError):return {}

def proc_identity(pid:int):
    try:
        fields=Path(f"/proc/{pid}/stat").read_text().rsplit(")",1)[1].split()
        return fields[19] if fields[0]!="Z" else None
    except (OSError,IndexError):return None

def alive(row):
    return bool(row and row.get("pid") and row.get("start") and proc_identity(row["pid"])==row["start"])

def identity(pid):
    return {"pid":pid,"start":proc_identity(pid)}

def available(port):
    with socket.socket() as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:s.bind(("127.0.0.1",port));return True
        except OSError:return False

def request(url,body=None,headers=None,timeout=2):
    req=Request(url,data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type":"application/json",**(headers or {})})
    with loopback_http.open(req,timeout=timeout) as response:return json.load(response)

def event(root,kind,**data):
    fd=os.open(root/"events.jsonl",os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,"a") as f:f.write(json.dumps({"at":time.time(),"kind":kind,**data},ensure_ascii=False)+"\n")

def busy_ports(cfg, names):
    return sorted(cfg[name] for name in names if not available(cfg[name]))


def service_checks(cfg):
    checks = {
        "python": Path(cfg["python"]).is_file(),
        "build": (Path(cfg["repo"]) / "web/dist/index.html").is_file(),
        "ports_free": not busy_ports(cfg, ("web_port", "api_port")),
    }
    try:
        subprocess.run(
            [cfg["python"], "-c", "import uvicorn, httpx, fastapi, pydantic"],
            check=True, capture_output=True, timeout=15,
        )
        checks["python_imports"] = True
    except (OSError, subprocess.SubprocessError):
        checks["python_imports"] = False
    return checks


def sglang_running():
    for proc in Path("/proc").iterdir():
        if not proc.name.isdecimal():
            continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if b"sglang.launch_server" in args or any(a.startswith(b"sglang::") for a in args[:1]):
            return True
        if len(args) >= 3 and args[1].endswith(b"/gas_dev_services.py") and args[2] == b"model":
            return True
    return False


def gateway_source_matches(checkout):
    try:
        present = {path.relative_to(checkout).as_posix() for path in (checkout/"gateway").rglob("*")
                   if path.is_file() or path.is_symlink()}
        if present != set(GATEWAY_SOURCE_FILES):
            return False
        for name, expected in GATEWAY_SOURCE_FILES.items():
            path = checkout / name
            if path.resolve() != path or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                return False
        return True
    except OSError:
        return False


def recover_gateway(root,cfg):
    if Path(cfg["gateway_repo"]).exists():
        return
    target = root / "pinned-gateway"
    if not target.exists():
        with GATEWAY_ARCHIVE.open("rb") as archive_file:
            if hashlib.file_digest(archive_file,"sha256").hexdigest() != GATEWAY_ARCHIVE_SHA256:
                raise RuntimeError("gateway archive SHA-256 mismatch")
        with tempfile.TemporaryDirectory(prefix="gateway-",dir=root) as temp:
            checkout = Path(temp) / "source"
            with tarfile.open(GATEWAY_ARCHIVE,"r:gz") as archived:
                members = archived.getmembers()
                files = [m for m in members if m.isfile()]
                if (len(files)!=len(GATEWAY_SOURCE_FILES) or {m.name for m in files}!=set(GATEWAY_SOURCE_FILES)
                    or any(not (m.isfile() or m.isdir()) for m in members)):
                    raise RuntimeError("unexpected gateway archive entries")
                archived.extractall(checkout,members=files,filter="data")
            if not gateway_source_matches(checkout):
                raise RuntimeError("gateway source differs from pinned commit")
            checkout.rename(target)
    cfg["gateway_repo"] = str(target)
    cfg["gateway_archived_source"] = True
    write_json(root/"config.json",cfg)


def model_environment(cfg):
    env = os.environ.copy()
    device = cfg.get("model_ppu")
    if device is not None:
        env["CUDA_VISIBLE_DEVICES"] = str(device)
    return env


def model_checks(cfg):
    device = cfg.get("model_ppu")
    checks = {
        "model_ports_free": not busy_ports(cfg, ("model_port", "gateway_port")),
    }
    if device is None:
        checks["sglang_idle"] = not sglang_running()
    if not all(checks.values()):
        return checks
    gateway_checkout = Path(cfg["gateway_repo"]).absolute()
    if gateway_checkout == REPO or cfg.get("gateway_archived_source"):
        checks["gateway_identity"] = gateway_source_matches(gateway_checkout)
    else:
        try:
            head=subprocess.check_output(["git","-C",cfg["gateway_repo"],"rev-parse","HEAD"],text=True,timeout=10).strip()
            clean=subprocess.check_output(["git","-C",cfg["gateway_repo"],"status","--porcelain"],text=True,timeout=10).strip()==""
            checks["gateway_identity"]=head==GATEWAY_SHA and clean
        except (OSError,subprocess.SubprocessError):checks["gateway_identity"]=False
    try:
        m=Path(cfg["model_dir"])
        idx=json.loads((m/"model.safetensors.index.json").read_text())
        shards=set(idx["weight_map"].values())
        checks["weights"]=len(shards)==18 and all((m/n).is_file() and (m/n).stat().st_size>0 for n in shards)
        checks["weight_bytes"]=sum((m/n).stat().st_size for n in shards)==55563006776
        checks["model_metadata"]=all(hashlib.sha256((m/n).read_bytes()).hexdigest()==sha for n,sha in MODEL_METADATA.items())
        checks["model_config"]=json.loads((m/"config.json").read_text()).get("model_type")=="qwen3_5"
    except (OSError,ValueError,KeyError):checks["weights"]=False
    try:
        smi = ["/usr/local/PPU_SDK/ppu-smi/bin/ppu-smi"]
        if device is not None:
            smi.extend(["-i", str(device)])
        output = subprocess.check_output(smi, text=True, timeout=10)
        cards = output.count("PPU-ZW810E")
        checks["ppu"] = cards >= 2 if device is None else cards == 1
        checks["ppu_free"]="No running processes found" in output
    except (OSError,subprocess.SubprocessError):checks["ppu"]=False
    try:
        probe = "import sglang,torch,shutil; assert shutil.which('ninja'); assert torch.cuda.is_available()"
        if device is None:
            probe += "; assert torch.cuda.device_count() >= 2"
        else:
            # Verify the effective mask and that the selected CUDA device is idle.
            probe += "; assert torch.cuda.device_count() == 1; free,total = torch.cuda.mem_get_info(0); assert free * 10 >= total * 9"
        subprocess.run(["bash","-c",'source /usr/local/PPU_SDK/envsetup.sh >/dev/null 2>&1; export PATH=/usr/local/bin:$PATH; exec "$@"',
            "gas-doctor",cfg["model_python"],"-c",probe],check=True,capture_output=True,timeout=20,env=model_environment(cfg))
        checks["model_imports"]=True
    except (OSError,subprocess.SubprocessError):checks["model_imports"]=False
    return checks


def doctor(cfg):
    checks = service_checks(cfg)
    if cfg["model"] != "off":
        checks.update(model_checks(cfg))
    return checks


def group_alive(row):
    if alive(row):return True
    owner=row.get("group_owner")
    if owner is None:return False
    # A dead process-group leader does not mean its inference workers exited.
    # Bind surviving members to both the recorded PGID and inherited supervisor ID.
    marker=("GAS_DEV_PARENT="+str(owner)).encode()
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():continue
        try:
            fields=(proc/"stat").read_text().rsplit(")",1)[1].split()
            if fields[0]!="Z" and int(fields[2])==row["pid"] and marker in (proc/"environ").read_bytes().split(b"\0"):
                return True
        except (OSError,ValueError,IndexError):continue
    return False

def kill_children(state):
    for child in reversed(state.get("children",[])):
        if group_alive(child):
            try:os.killpg(child["pid"],signal.SIGTERM)
            except ProcessLookupError:pass
    deadline=time.monotonic()+12
    while time.monotonic()<deadline and any(group_alive(c) for c in state.get("children",[])):time.sleep(.2)
    for child in reversed(state.get("children",[])):
        if group_alive(child):
            try:os.killpg(child["pid"],signal.SIGKILL)
            except ProcessLookupError:pass

def preview_identity(username, *, actor_id=None):
    return {
        "username": username,
        "password": secrets.token_urlsafe(24),
        "platform_token": secrets.token_urlsafe(32),
        "actor": actor_id or "gas-dev-" + secrets.token_hex(8),
    }


def drain(root,pipe,name):
    fd=os.open(root/(name+".log"),os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,"a",buffering=1) as out:
        for line in iter(pipe.readline,""):
            keys=[p.read_text().strip() for p in root.glob("*.token")]
            access=read_json(root/"access.json")
            if isinstance(access, dict):
                profiles = (access, access.get("operator"))
                keys.extend(profile.get(key, "") for profile in profiles if isinstance(profile, dict)
                            for key in ("password", "platform_token"))
            for key in keys:
                if key:line=line.replace(key,"<redacted>")
            out.write(line)
    pipe.close()

def spawn(root,cfg,name,state):
    env=model_environment(cfg) if name == "model" else os.environ.copy()
    env["GAS_DEV_PARENT"]=str(os.getpid())
    child_script=str(REPO/"scripts/gas_dev_services.py")
    args=[cfg["python"],"-B","-u",child_script,name,str(root)]
    if name=="model":
        # Use only the approved existing SDK environment; secrets never in shell arguments.
        args=["bash","-c",'source /usr/local/PPU_SDK/envsetup.sh >/dev/null 2>&1; export PATH=/usr/local/bin:$PATH; exec "$@"',
              "gas-model",cfg["model_python"],"-u",child_script,name,str(root)]
    p=subprocess.Popen(args,env=env,start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1)
    row={**identity(p.pid),"component":name,"group_owner":os.getpid()};state["children"].append(row);write_json(root/"state.json",state)
    threading.Thread(target=drain,args=(root,p.stdout,name),daemon=True).start()
    return p

def supervisor(root):
    cfg=read_json(root/"config.json")
    state={"supervisor":identity(os.getpid()),"children":[],"started_at":time.time(),
           "expires_at":time.time()+cfg["ttl_minutes"]*60,"status":"starting","model_status":"starting" if cfg["model"]!="off" else "disabled"}
    write_json(root/"state.json",state)
    for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop_event.set())
    # Independent watcher handles even SIGKILL of the supervisor, with PID/start-time binding.
    reaper=subprocess.Popen([cfg["python"],str(REPO/"scripts/gas_dev.py"),"reap","--runtime",str(root)],
        start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    # Keep the local ledger owner stable while each restart rotates credentials.
    runtime_identity = hashlib.sha256(str(root).encode()).hexdigest()[:16]
    access = {**preview_identity("developer", actor_id="gas-dev-reader-" + runtime_identity),
              "expires_at": state["expires_at"]}
    if cfg.get("cuv_collaboration_demo"):
        # Private per-runtime audit keeps the synthetic operator identity across credential rotation.
        access["operator"] = preview_identity("operator", actor_id="gas-cuv-demo-operator")
    elif cfg.get("disposition_demo"):
        access["operator"] = preview_identity("operator", actor_id="gas-dev-operator-" + runtime_identity)
    write_json(root/"access.json",access)
    for n in ("model","admin"):
        fd=os.open(root/(n+".token"),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,"w") as f:f.write(secrets.token_urlsafe(32))
    children={}
    last_resource=0
    try:
        children["services"]=spawn(root,cfg,"services",state)
        state["status"]="running";write_json(root/"state.json",state)
        event(root,"started",ports={n:cfg[n] for n in ("web_port","api_port","model_port","gateway_port")},ttl_minutes=cfg["ttl_minutes"])
        if cfg["model"] != "off":
            try:
                recover_gateway(root,cfg)
                checks = model_checks(cfg)
            except (OSError,RuntimeError,subprocess.SubprocessError,tarfile.TarError):
                checks = {"gateway_restore": False}
            if stop_event.is_set():
                return
            if children["services"].poll() is not None:
                raise RuntimeError("preview_process_exited")
            if all(checks.values()):
                children["model"] = spawn(root, cfg, "model", state)
                children["gateway"] = spawn(root, cfg, "gateway", state)
            else:
                state["model_status"] = "blocked_by_env"
                failed = [name for name, ok in checks.items() if not ok]
                state["error"] = "model_preflight: " + ", ".join(failed)
                if "model_ports_free" in failed:
                    state["error"] += "; busy ports " + ", ".join(map(str,busy_ports(cfg,("model_port","gateway_port"))))
                write_json(root/"state.json",state)
                event(root,"model_degraded",checks=checks)
        key_ready=False
        while not stop_event.wait(.5) and time.time()<state["expires_at"]:
            if children["services"].poll() is not None:raise RuntimeError("preview_process_exited")
            for name in ("model","gateway"):
                if name in children and children[name].poll() is not None:
                    state["model_status"]="unavailable"
                    state["error"]=name+"_process_exited"
                    (root/"gas-client.token").unlink(missing_ok=True)
                    write_json(root/"state.json",state)
                    event(root,"model_degraded",error_code=state["error"])
                    for other in ("model","gateway"):
                        kill_children({"children":[c for c in state["children"] if c["component"]==other]})
                        children.pop(other,None)
                    break
            if "gateway" in children and not key_ready:
                try:
                    health=request(f"http://127.0.0.1:{cfg['gateway_port']}/health")
                    if health.get("state")=="ready":
                        created=request(f"http://127.0.0.1:{cfg['gateway_port']}/admin/api/keys",
                            {"name":"gas-dev-limited","scope":"test","quota_tokens":100000,"rpm_limit":60,"concurrent_limit":1},
                            {"X-Admin-Token":(root/"admin.token").read_text().strip()})
                        fd=os.open(root/"gas-client.token",os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                        with os.fdopen(fd,"w") as f:f.write(created["key"])
                        key_ready=True;state["model_status"]="ready";write_json(root/"state.json",state)
                        event(root,"model_ready",model="Qwen3.8-27B")
                except (OSError,ValueError,KeyError):pass
                if time.time()-state["started_at"]>cfg.get("model_start_timeout",480) and not key_ready:
                    for name in ("model","gateway"):
                        kill_children({"children":[c for c in state["children"] if c["component"]==name]})
                    state["model_status"]="startup_failed"
                    state["error"]="model_startup_timeout"
                    write_json(root/"state.json",state)
                    children.pop("gateway",None);children.pop("model",None)
                    event(root,"model_degraded",error_code=state["error"])
            if time.time()-last_resource>30:
                resources=[]
                for row in state["children"]:
                    if alive(row):
                        try:
                            status=Path(f"/proc/{row['pid']}/status").read_text().splitlines()
                            rss=next((line.split(':',1)[1].strip() for line in status if line.startswith("VmRSS:")),"unknown")
                            resources.append({"component":row["component"],"rss":rss})
                        except OSError:pass
                state["resources"]=resources;write_json(root/"state.json",state)
                gpu=[]
                try:
                    output=subprocess.check_output(["/usr/local/PPU_SDK/ppu-smi/bin/ppu-smi"],text=True,timeout=5)
                    gpu=[{"used_mib":int(a),"total_mib":int(b)} for a,b in re.findall(r"(\d+)MiB\s*/\s*(\d+)MiB",output)]
                except (OSError,subprocess.SubprocessError):pass
                state["ppu_memory"]=gpu;write_json(root/"state.json",state)
                event(root,"resource_sample",resources=resources,ppu_memory=gpu);last_resource=time.time()
    except Exception as exc:
        state["error"] = "supervisor_error: " + (str(exc) if str(exc) == "preview_process_exited" else type(exc).__name__)
        event(root,"supervisor_error",error_code=state["error"])
    finally:
        kill_children(state)
        state["status"]="stopped";state["stopped_at"]=time.time();write_json(root/"state.json",state)
        for p in list(root.glob("*.token"))+[root/"access.json"]:p.unlink(missing_ok=True)
        event(root,"stopped",reason="ttl_or_stop_or_error")
        try:reaper.wait(timeout=5)
        except subprocess.TimeoutExpired:reaper.terminate()

def reap(root):
    deadline=time.time()+30
    state=read_json(root/"state.json");owner=state.get("supervisor")
    while alive(owner) and time.time()<state.get("expires_at",deadline)+15:
        if state.get("status")=="stopped" or state.get("supervisor")!=owner:return
        time.sleep(.5);state=read_json(root/"state.json")
    lock=os.open(root/"control.lock",os.O_RDWR|os.O_CREAT,0o600)
    with os.fdopen(lock,"w"):
        fcntl.flock(lock,fcntl.LOCK_EX)
        state=read_json(root/"state.json")
        # A newer start owns its credentials/processes; an old guardian must never alter them.
        if state.get("supervisor")!=owner or state.get("status")=="stopped":return
        kill_children(state)
        state["status"]="stopped_after_supervisor_exit";write_json(root/"state.json",state)
        for p in list(root.glob("*.token"))+[root/"access.json"]:p.unlink(missing_ok=True)
        event(root,"orphan_cleanup")


def status(root,cfg):
    state = read_json(root/"state.json")
    cfg = read_json(root/"config.json") or cfg
    running = alive(state.get("supervisor"))
    result = {
        "running": running,
        "state": state.get("status","stopped") if running else "stopped",
        "source": cfg.get("source"),
        "expires_at": state.get("expires_at") if running else None,
        "frontend": "stopped",
        "fastapi": "stopped",
        "agent": "unavailable",
        "agent_status": "unavailable",
        "model_status": "unavailable",
        "disposition_demo": bool(cfg.get("disposition_demo")),
        "cuv_collaboration_demo": bool(cfg.get("cuv_collaboration_demo")),
        "model_ppu": cfg.get("model_ppu"),
        "qwen_status": "stopped",
        "resources": state.get("resources",[]) if running else [],
        "ppu_memory": state.get("ppu_memory",[]) if running else [],
        "startup_error": state.get("error"),
        "url": f"http://127.0.0.1:{cfg['web_port']}/#/gas",
    }
    if not running:
        result["stale_owned_pids"] = [c["pid"] for c in state.get("children",[]) if group_alive(c)]
        return result
    access = read_json(root/"access.json")
    try:
        login = base64.b64encode(f"{access['username']}:{access['password']}".encode()).decode()
        page = Request(result["url"].split("#",1)[0],headers={"Authorization":"Basic "+login})
        with loopback_http.open(page,timeout=2) as response:
            if response.status == 200 and response.headers.get_content_type() == "text/html":
                result["frontend"] = "running"
    except (OSError,ValueError,KeyError):
        pass
    try:
        health = request(f"http://127.0.0.1:{cfg['api_port']}/api/v1/gas/engineering/agent/health",
                         headers={"X-Water-Token":access["platform_token"]},timeout=3)
        result["fastapi"] = "running"
        result["agent"] = health
        result["agent_status"] = health.get("tools_status","unavailable")
        result["model_status"] = health.get("model_status","unavailable")
        result["model_id"] = "Qwen3.8-27B" if result["model_status"] == "ready" else None
        result["inference_source"] = "local_ppu_via_pinned_gateway" if result["model_id"] else "local_rule_not_llm"
    except (OSError,ValueError,KeyError):
        pass
    model_phase = state.get("model_status")
    if result.get("model_id"):
        result["qwen_status"] = "ready"
    elif model_phase == "starting":
        result["qwen_status"] = "starting"
    elif model_phase not in (None,"disabled"):
        result["qwen_status"] = "failed"
        result["startup_error"] = result["startup_error"] or "model_health_unavailable"
    return result


def stop_runtime(root):
    state = read_json(root/"state.json")
    owner = state.get("supervisor")
    if alive(owner):
        try:
            os.kill(owner["pid"],signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic()+25
        while time.monotonic() < deadline and alive(owner):
            time.sleep(.2)
        if alive(owner):
            try:
                os.kill(owner["pid"],signal.SIGKILL)
            except ProcessLookupError:
                pass
            deadline = time.monotonic()+3
            while time.monotonic() < deadline and alive(owner):
                time.sleep(.1)
    # Re-read after the supervisor stops so cleanup includes late-starting children.
    state = read_json(root/"state.json") or state
    if state.get("supervisor") != owner or alive(state.get("supervisor")):
        return False
    kill_children(state)
    if any(group_alive(c) for c in state.get("children",[])):
        return False
    state["status"] = "stopped"
    write_json(root/"state.json",state)
    for path in list(root.glob("*.token"))+[root/"access.json"]:
        path.unlink(missing_ok=True)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["doctor","start","status","stop","credentials","supervise","reap"])
    parser.add_argument("--runtime",type=Path,default=Path("/nix/wpn-gas-fastdev06-runtime"))
    parser.add_argument("--python",default="/tmp/gas-fix21-venv/bin/python")
    parser.add_argument("--model-python",default="/usr/local/bin/python3")
    parser.add_argument("--gateway-repo", default=str(REPO),
                        help="gateway source checkout (defaults to the pinned source in this repository)")
    parser.add_argument("--model-dir",default="/workdata/VLM/models/Qwen3.8-27B")
    parser.add_argument("--model",choices=["auto","off"],default="auto")
    parser.add_argument("--model-ppu", type=int, help="use only this idle physical PPU for private Qwen inference")
    parser.add_argument("--disposition-demo", action="store_true", help="enable local synthetic disposition")
    parser.add_argument("--cuv-collaboration-demo", action="store_true", help="enable local synthetic CUV review")
    parser.add_argument("--credential-role", choices=["reader", "operator"], default="reader")
    parser.add_argument("--ttl-minutes",type=int,default=60)
    parser.add_argument("--model-start-timeout",type=int,default=480)
    for name,default in [("web-port",24673),("api-port",24681),("gateway-port",24695),("model-port",30006)]:
        parser.add_argument("--"+name,type=int,default=default)
    args=parser.parse_args()
    try:
        root=private_root(args.runtime)
        cfg=vars(args).copy();cfg.pop("command");cfg.pop("runtime");cfg.pop("credential_role")
        cfg.update(repo=str(REPO),source=subprocess.check_output(["git","rev-parse","HEAD"],cwd=REPO,text=True).strip())
        ports=[cfg[n] for n in ("web_port","api_port","gateway_port","model_port")]
        if len(set(ports))!=4 or any(p in RESERVED or not 1024<=p<=65535 for p in ports):raise RuntimeError("ports invalid or reserved")
        if not 1<=args.ttl_minutes<=180:raise RuntimeError("TTL must be 1..180 minutes")
        if not 1<=args.model_start_timeout<=900:raise RuntimeError("model-start-timeout must be 1..900 seconds")
        if args.model_ppu is not None and args.model_ppu < 0:raise RuntimeError("model-ppu must be a non-negative index")
        if args.command=="supervise":supervisor(root);return
        if args.command=="reap":reap(root);return
        lock=os.open(root/"control.lock",os.O_RDWR|os.O_CREAT,0o600)
        with os.fdopen(lock,"w"):
            fcntl.flock(lock,fcntl.LOCK_EX)
            old=read_json(root/"state.json")
            if args.command=="doctor":
                checks=doctor(cfg);print(json.dumps(checks));sys.exit(0 if all(checks.values()) else 1)
            if args.command=="status":
                print(json.dumps(status(root,cfg),ensure_ascii=False,indent=2));return
            if args.command=="credentials":
                if not sys.stdout.isatty():raise RuntimeError("credentials require an interactive SSH terminal")
                access=read_json(root/"access.json")
                profile = access if args.credential_role == "reader" else access.get("operator")
                if not isinstance(profile, dict) or not profile.get("username") or not profile.get("password"):
                    raise RuntimeError("requested temporary preview identity is unavailable")
                print("Temporary development login:", profile["username"], profile["password"])
                return
            if args.command=="stop":
                stopped = stop_runtime(root)
                print(json.dumps({"stopped": stopped}))
                if not stopped:
                    sys.exit(1)
                return
            if alive(old.get("supervisor")):
                active = status(root,cfg)
                if active["source"] != cfg["source"]:
                    raise RuntimeError("another source revision already owns this runtime")
                for flag in ("disposition_demo", "cuv_collaboration_demo", "model_ppu"):
                    if active[flag] != cfg[flag]:
                        raise RuntimeError("active preview mode differs; stop before changing " + flag)
                if active["frontend"] != "running" or active["fastapi"] != "running":
                    raise RuntimeError("supervisor is active but preview or FastAPI is unavailable")
                print(json.dumps({"already_running":True,**active},ensure_ascii=False))
                return
            stale = [c for c in old.get("children",[]) if group_alive(c)]
            if stale:
                event(root,"stale_children_detected",pids=[c["pid"] for c in stale])
                if not stop_runtime(root):
                    raise RuntimeError("could not clean up owned stale processes")
            occupied = busy_ports(cfg,("web_port","api_port"))
            if occupied:
                raise RuntimeError("loopback application ports already in use: " + ", ".join(map(str,occupied)))
            checks = service_checks(cfg)
            if not all(checks.values()):
                raise RuntimeError("required preview dependency/port check failed: " + json.dumps(checks))
            (root/"gas-client.token").unlink(missing_ok=True)
            write_json(root/"config.json",cfg)
            p=subprocess.Popen([cfg["python"],str(Path(__file__).resolve()),"supervise","--runtime",str(root)],
                start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            deadline = time.monotonic()+40
            try:
                while time.monotonic() < deadline:
                    state = read_json(root/"state.json")
                    if p.poll() is not None or (state.get("supervisor",{}).get("pid")==p.pid and state.get("status")=="stopped"):
                        raise RuntimeError(state.get("error") or "supervisor exited")
                    if state.get("supervisor",{}).get("pid")==p.pid and state.get("status")=="running":
                        result = status(root,cfg)
                        if result["frontend"] == "running" and result["fastapi"] == "running":
                            access = read_json(root/"access.json")
                            try:
                                case = request(f"http://127.0.0.1:{cfg['api_port']}/api/v1/gas/cases/syn-gas-case-K-001",
                                               headers={"X-Water-Token":access["platform_token"]},timeout=5)
                                if case.get("case_id") != "syn-gas-case-K-001" or case.get("data_mode") != "synthetic":
                                    raise ValueError("unexpected synthetic case identity")
                            except (OSError,KeyError,ValueError) as exc:
                                raise RuntimeError("GAS-M0 synthetic case is unavailable") from exc
                            print(json.dumps(result,ensure_ascii=False,indent=2))
                            return
                    time.sleep(.2)
                raise RuntimeError("frontend/FastAPI health check timed out")
            except (RuntimeError,KeyboardInterrupt):
                if p.poll() is None:
                    p.terminate()
                stop_runtime(root)
                raise
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        print(json.dumps({"status":"BLOCKED","error":str(exc)},ensure_ascii=False),file=sys.stderr);sys.exit(1)

if __name__=="__main__":main()
