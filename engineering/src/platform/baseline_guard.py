"""升级前的长期基线保护工具：哈希清单、可恢复代码副本及SQLite一致性备份。

从工程根运行：../.venv-platform/bin/python -B -m src.platform.baseline_guard capture
核对：../.venv-platform/bin/python -B -m src.platform.baseline_guard verify <备份目录>

仅新建 experiments/agent/baselines/<时间与UUID>/，不修改原件、不清理文件。
SQLite在线数据使用backup接口，不能只复制主文件而丢失WAL中的已提交事务。
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from urllib.parse import quote
import uuid

ROOT = Path(__file__).resolve().parents[2]
COPY_DIRS = ("src", "ui", "tests", "configs", "outputs/baseline", "outputs/pipe_health",
             "experiments/baseline", "experiments/pipe_health", "data/synthetic")
HASH_DIRS = ("01_原始材料", "02_核查证据/程序包", "02_核查证据/结构化记录")


def digest(path: Path) -> str:
    """流式读取二进制，不将Office/模型文件作为文本处理。"""
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def contained(root: Path, relative: str) -> Path:
    """拒绝清单中的越界或绝对路径。"""
    path = (root / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(root.resolve()):
        raise ValueError("清单路径超出工程目录")
    return path


def files_in(root: Path, directories: tuple[str, ...]):
    """只遍历明确保护目录；不采集环境、缓存、凭据或编辑器目录。"""
    for directory in directories:
        for path in sorted((root / directory).rglob("*")):
            if path.is_file() and not path.is_symlink() and "__pycache__" not in path.parts:
                yield path


def sqlite_backup(source: Path, target: Path) -> dict:
    """读取一致性快照并检查备份完整性；不改变源数据库的日志模式。"""
    if target.exists():
        raise FileExistsError("不覆盖已有数据库备份")
    with closing(sqlite3.connect(f"file:{quote(str(source.resolve()))}?mode=ro", uri=True)) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst, pages=256, sleep=0.05)
            integrity = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("SQLite一致性备份未通过完整性检查")
            names = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            counts = {}
            for name in names:
                escaped = name.replace('"', '""')
                counts[name] = dst.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
    return {"file": target.name, "sha256": digest(target), "integrity_check": integrity,
            "table_rows": counts, "method": "sqlite_backup_api"}


def capture(root: Path = ROOT) -> Path:
    """生成不可覆盖的基线包；原始资料只登记哈希，不重复复制大文件。"""
    root = root.resolve()
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    dest = root / "experiments/agent/baselines" / name
    dest.mkdir(parents=True, exist_ok=False)
    manifest = {"schema_version": "1", "source_root": str(root), "status": "preparing",
                "created_at": datetime.now(timezone.utc).isoformat(), "files": [], "database": None}
    try:
        for do_copy, directories in ((True, COPY_DIRS), (False, HASH_DIRS)):
            for path in files_in(root, directories):
                relative = path.relative_to(root).as_posix()
                before = digest(path)
                entry = {"path": relative, "bytes": path.stat().st_size, "sha256": before,
                         "backup": "files/" + relative if do_copy else None}
                if do_copy:
                    target = dest / entry["backup"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                    if digest(target) != before or digest(path) != before:
                        raise RuntimeError(f"备份过程中源文件发生变化：{relative}")
                manifest["files"].append(entry)
        db_path = root / "experiments/platform/platform.sqlite3"
        if db_path.is_file():
            manifest["database"] = sqlite_backup(db_path, dest / "platform.sqlite3")
        manifest["status"] = "complete"
    except Exception as exc:
        manifest["status"] = "failed"
        manifest["error_type"] = type(exc).__name__
        raise
    finally:
        with (dest / "manifest.json").open("x", encoding="utf-8") as out:
            json.dump(manifest, out, ensure_ascii=False, indent=2)
    return dest


def verify(snapshot: Path, root: Path = ROOT) -> dict:
    """报告备份损坏与工程变更，二者分别列出；不会自动恢复或回写任何文件。"""
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "complete":
        raise ValueError("备份尚未完成，不能用作回退基线")
    changed, corrupt = [], []
    for entry in manifest["files"]:
        source = contained(root, entry["path"])
        if not source.is_file() or digest(source) != entry["sha256"]:
            changed.append(entry["path"])
        if entry["backup"]:
            backup = contained(snapshot, entry["backup"])
            if not backup.is_file() or digest(backup) != entry["sha256"]:
                corrupt.append(entry["backup"])
    db = manifest["database"]
    if db and (not (snapshot / db["file"]).is_file() or digest(snapshot / db["file"]) != db["sha256"]):
        corrupt.append(db["file"])
    return {"backup_intact": not corrupt, "corrupt_backups": corrupt, "changed_sources": changed,
            "file_count": len(manifest["files"]), "database": db}


def main():
    parser = argparse.ArgumentParser(description="工程基线保护，不自动回滚")
    parser.add_argument("operation", choices=("capture", "verify"))
    parser.add_argument("snapshot", nargs="?", type=Path)
    args = parser.parse_args()
    if args.operation == "capture":
        print(capture())
    else:
        if args.snapshot is None:
            parser.error("verify需要备份目录")
        report = verify(args.snapshot)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["backup_intact"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
