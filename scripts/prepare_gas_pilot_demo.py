"""Verify the sealed synthetic pilot sources and seed a release-local store."""

from __future__ import annotations

import argparse
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile

from gas_dev import private_root

DATASET = "syn-gas-pilot-history-01"
PROFILE = "gas-pilot-workflow"
VERSIONS = {"demo-v2": "admitted", "demo-v3": "admitted", "demo-v3-rejected": "rejected"}


def _verified_store(store, digests: dict[str, str]) -> None:
    expected = {name: digests[name] for name, state in VERSIONS.items() if state == "admitted"}
    entries = store.entries(frozenset({DATASET}))
    actual = {entry.version: entry.manifest_sha256 for entry, _ in entries
              if entry.data_mode == "synthetic" and entry.admission_state == "admitted"}
    rejected = store.rejected(frozenset({DATASET}))
    if (actual != expected or len(entries) != len(expected)
        or [entry.version for entry, _ in entries if entry.preferred] != ["demo-v3"]
        or [(entry.version, entry.manifest_sha256) for entry, _, _ in rejected]
        != [("demo-v3-rejected", digests["demo-v3-rejected"])]):
        raise ValueError("synthetic store differs from release profile")


def prepare_pilot(workspace: Path, runtime: Path) -> Path:
    from src.gas_application.pilot_intake import inspect_pilot
    from src.gas_application.pilot_store import PilotStore

    release = json.loads((workspace / "gas-demo-release.json").read_text(encoding="utf-8"))
    commit = release["commit"]
    demo = release.get("pilot_demo", {})
    digests = demo.get("manifest_sha256", {})
    if (release.get("demo_profile") != PROFILE or not re.fullmatch(r"[0-9a-f]{40}", commit)
        or demo.get("dataset_id") != DATASET or set(digests) != set(VERSIONS)
        or any(not re.fullmatch(r"[0-9a-f]{64}", value) for value in digests.values())):
        raise ValueError("invalid synthetic release profile")
    root = private_root(runtime)
    target = root / ("pilot-" + commit)
    with os.fdopen(os.open(root / "pilot-seed.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "wb") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        packages = []
        for version, expected in VERSIONS.items():
            source = workspace / "pilot-synthetic" / version
            path = source / "manifest.json"
            if path.is_symlink() or sha256(path.read_bytes()).hexdigest() != digests[version]:
                raise ValueError("synthetic manifest digest differs")
            package = inspect_pilot(source)
            if any(issue["code"] in {"hash_mismatch", "file_unavailable", "file_too_large"}
                   for issue in package.report["issues"]):
                raise ValueError("synthetic source bytes differ from release")
            identity = package.identity
            if ((identity.dataset_id, identity.version, identity.data_mode, identity.manifest_sha256)
                != (DATASET, version, "synthetic", digests[version])
                or (package.snapshot is None) != (expected == "rejected")):
                raise ValueError("synthetic package validation differs from release")
            packages.append(package)
        if target.exists() or target.is_symlink():
            _verified_store(PilotStore(target), digests)
            return target
        with tempfile.TemporaryDirectory(prefix="pilot-seed-", dir=root) as stage:
            directory = Path(stage) / "store"
            directory.mkdir(mode=0o700)
            store = PilotStore(directory)
            for package in packages:
                store.prepare(package, actor="gas-release-synthetic")
            _verified_store(store, digests)
            directory.rename(target)
    return target


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("workspace", type=Path)
    cli.add_argument("runtime", type=Path)
    args = cli.parse_args()
    try:
        print(prepare_pilot(args.workspace, args.runtime))
    except (OSError, RuntimeError, ValueError, KeyError, TypeError):
        cli.exit(1, "发布包的合成试点数据校验失败。请核对发布清单和私有运行目录。\n")
