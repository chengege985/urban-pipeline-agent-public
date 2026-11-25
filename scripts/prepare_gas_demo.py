"""Restore a pinned demo checkout and production frontend on the Pod's local disk."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def sha256(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def frontend_archive(workspace, cache, checksum):
    """Keep the verified release archive on local disk for warm starts."""
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("invalid frontend archive SHA-256")
    local = cache / f"frontend-{checksum}.tar.gz"
    if local.is_symlink():
        raise ValueError("unexpected frontend archive link in cache")
    if not local.exists():
        with tempfile.TemporaryDirectory(prefix="frontend-", dir=cache) as tmp:
            staged = Path(tmp) / "web-dist.tar.gz"
            shutil.copyfile(workspace / "web-dist.tar.gz", staged)
            if sha256(staged) != checksum:
                raise ValueError("release frontend archive SHA-256 mismatch")
            staged.rename(local)
    elif not local.is_file() or sha256(local) != checksum:
        raise ValueError("cached frontend archive differs from release; use a new local cache directory")
    return local


def check_frontend(web, frontend, index_sha256, *, restore=False):
    """Compare the full served file set with the verified release archive."""
    with tarfile.open(frontend, "r:gz") as archive:
        members = archive.getmembers()
        expected = {}
        for member in members:
            path = PurePosixPath(member.name)
            if (not path.parts or path.is_absolute() or path.parts[0] != "dist"
                    or ".." in path.parts or not (member.isfile() or member.isdir())):
                raise ValueError("unexpected frontend archive entry")
            if member.isfile():
                with archive.extractfile(member) as file:
                    expected[path] = hashlib.file_digest(file, "sha256").hexdigest()
        if expected.get(PurePosixPath("dist/index.html")) != index_sha256:
            raise ValueError("frontend index differs from release")
        if restore:
            archive.extractall(web, members=members, filter="data")

    error = "cached frontend differs from release; use a new local cache directory"
    dist = web / "dist"
    if web.is_symlink() or dist.is_symlink() or not dist.is_dir():
        raise ValueError(error)
    actual = {}
    for file in dist.rglob("*"):
        if file.is_symlink():
            raise ValueError(error)
        if file.is_file():
            actual[file.relative_to(web)] = sha256(file)
        elif not file.is_dir():
            raise ValueError(error)
    if actual != expected:
        raise ValueError(error)


def check_contracts(checkout: Path, contract_files: dict[str, str]) -> None:
    for name, expected in contract_files.items():
        path = (checkout / name).resolve()
        if not path.is_relative_to(checkout) or sha256(path) != expected:
            raise ValueError("source contract SHA-256 differs from release")


def prepare(workspace, *, management=False):
    release = json.loads((workspace / "gas-demo-release.json").read_text())
    commit, tree = release["commit"], release["tree"]
    if not all(re.fullmatch(r"[0-9a-f]{40}", value) for value in (commit, tree)):
        raise ValueError("invalid release identity")
    cache = Path(os.environ.get("WPN_GAS_DEMO_CACHE", "/nix/wpn-gas-mainline-cache")).absolute()
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    if cache.is_symlink() or cache.stat().st_uid != os.geteuid() or cache.stat().st_mode & 0o077:
        raise ValueError("cache must be an owner-only local directory")
    fs = subprocess.check_output(["stat", "-f", "-c", "%T", str(cache)], text=True).strip()
    if fs.startswith(("fuse", "nfs")):
        raise ValueError("cache needs local storage")
    checkout = cache / commit
    with (cache / "restore.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if checkout.exists():
            if (checkout.is_symlink() or git(checkout, "rev-parse", "HEAD", "HEAD^{tree}").splitlines() != [commit, tree]
                or git(checkout,"status","--porcelain","--untracked-files=no")):
                raise ValueError("cached source differs from release; use a new local cache directory")
            if management:
                return checkout
            frontend = frontend_archive(workspace, cache, release["frontend_sha256"])
            check_contracts(checkout, release.get("contract_files", {}))
            check_frontend(checkout / "web", frontend, release["index_sha256"])
            return checkout
        frontend = frontend_archive(workspace, cache, release["frontend_sha256"])
        bundle = workspace / "source.bundle"
        if sha256(bundle) != release["bundle_sha256"]:
            raise ValueError("release artifact SHA-256 mismatch")
        with tempfile.TemporaryDirectory(prefix="restore-", dir=cache) as tmp:
            staged = Path(tmp) / "source"
            subprocess.run(["git","clone","--quiet","--no-checkout",str(bundle),str(staged)],check=True)
            git(staged,"bundle","verify",str(bundle))
            git(staged,"switch","--quiet","--detach",commit)
            if git(staged,"rev-parse","HEAD^{tree}") != tree:
                raise ValueError("source Tree differs from release")
            check_contracts(staged, release.get("contract_files", {}))
            check_frontend(staged / "web", frontend, release["index_sha256"], restore=True)
            staged.rename(checkout)
    return checkout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("--management", action="store_true",
                        help="use verified cached source for stop/status even if frontend assets are damaged")
    args = parser.parse_args()
    try:
        print(prepare(args.workspace.resolve(), management=args.management))
    except (OSError, KeyError, ValueError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f"燃气演示恢复失败。{error}",file=sys.stderr)
        sys.exit(1)
