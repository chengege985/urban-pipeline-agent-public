"""Build and pin a gas-only one-click release from a clean Git commit."""

from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from prepare_gas_demo import check_frontend, git, sha256
from prepare_gas_pilot_demo import DATASET, PROFILE, VERSIONS


REPO = Path(__file__).resolve().parents[1]
CONTRACTS = (
    "engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json",
    "web/src/generated/platformOpenapi.ts",
    "engineering/configs/platform_requirements.lock.txt",
    "web/package-lock.json",
)
LAUNCH_FILES = (
    ".vscode/settings.json",
    ".vscode/tasks.json",
    "scripts/start_gas_demo.sh",
    "scripts/gas_demo.sh",
    "scripts/prepare_gas_demo.py",
    "docs/GAS_DEMO_QUICKSTART.md",
    "docs/SERVER_WORKFLOW.md",
    "docs/development/GAS-CUV-COLLABORATION.md",
    "docs/development/GAS-PILOT-04-HANDOFF.md",
)


def verify_source(commit: str, tree: str) -> None:
    if git(REPO, "rev-parse", "HEAD", "HEAD^{tree}").splitlines() != [commit, tree]:
        raise ValueError("source changed during release build")
    if git(REPO, "status", "--porcelain"):
        raise ValueError("release build requires a clean worktree")


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _archive(dist: Path, target: Path) -> None:
    def reproducible(member: tarfile.TarInfo) -> tarfile.TarInfo:
        member.mtime = member.uid = member.gid = 0
        member.uname = member.gname = ""
        return member

    with target.open("wb") as file, gzip.GzipFile(filename="", fileobj=file, mode="wb", mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            archive.add(dist, arcname="dist", filter=reproducible)


def add_pilot_profile(stage: Path) -> dict:
    data = stage / "pilot-synthetic"
    data.mkdir()
    fixtures = REPO / "engineering/tests"
    subprocess.run([sys.executable, "-B", str(fixtures / "pilot02_fixture.py"), str(data / "demo-v2")], check=True)
    subprocess.run([sys.executable, "-B", str(fixtures / "pilot03_fixture.py"),
                    str(data / "demo-v3"), str(data / "demo-v3-rejected")], check=True)
    return {"dataset_id": DATASET, "manifest_sha256": {
        version: sha256(data / version / "manifest.json") for version in VERSIONS
    }}


def package(destination: Path, commit: str, *, profile: str = PROFILE) -> dict:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"release directory already exists: {destination}")
    tree = git(REPO, "rev-parse", commit + "^{tree}")
    verify_source(commit, tree)
    with tempfile.TemporaryDirectory(prefix="wpn-release-build-", dir=REPO.parent) as local:
        build = Path(local)
        dist = build / "dist"
        subprocess.run(
            ["npm", "run", "build:gas-demo", "--", "--outDir", str(dist)], cwd=REPO / "web",
            env={**os.environ, "VITE_WATERPULSE_PLATFORM_URL": ""}, check=True,
        )
        verify_source(commit, tree)
        product = json.loads((dist / "frontend-product.json").read_text())
        if product.get("edition") != "project" or product.get("waterRuntimeModuleCount") != 0:
            raise ValueError("release must contain the gas-only project build")
        frontend = build / "web-dist.tar.gz"
        _archive(dist, frontend)
        index_sha256 = sha256(dist / "index.html")
        check_frontend(build, frontend, index_sha256)

        bundle = build / "source.bundle"
        git(REPO, "bundle", "create", str(bundle), "HEAD")
        git(REPO, "bundle", "verify", str(bundle))
        if git(REPO, "bundle", "list-heads", str(bundle), "HEAD") != f"{commit} HEAD":
            raise ValueError("bundle commit does not match build source")
        manifest = {
            "release": destination.name,
            "commit": commit,
            "tree": tree,
            "bundle_sha256": sha256(bundle),
            "frontend_sha256": sha256(frontend),
            "index_sha256": index_sha256,
            "contract_files": {name: sha256(REPO / name) for name in CONTRACTS},
            "frontend_files": {file.relative_to(dist).as_posix(): sha256(file) for file in sorted(dist.rglob("*")) if file.is_file()},
            "demo_profile": profile,
        }

        with tempfile.TemporaryDirectory(prefix=f".{destination.name}-", dir=destination.parent) as publishing:
            stage = Path(publishing) / "release"
            stage.mkdir()
            for artifact in (bundle, frontend):
                shutil.copyfile(artifact, stage / artifact.name)
            for name in LAUNCH_FILES:
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(REPO / name, target)
            if profile == PROFILE:
                manifest["pilot_demo"] = add_pilot_profile(stage)
            verify_source(commit, tree)
            write_json(stage / "gas-demo-release.json", manifest)
            files = {file.relative_to(stage).as_posix(): sha256(file) for file in sorted(stage.rglob("*")) if file.is_file()}
            if files[bundle.name] != manifest["bundle_sha256"] or files[frontend.name] != manifest["frontend_sha256"]:
                raise ValueError("persistent copy differs from verified artifacts")
            write_json(stage / "release-files.sha256.json", files)
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(f"release directory appeared during build: {destination}")
            stage.rename(destination)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--profile", choices=(PROFILE, "gas-m0-workflow"), default=PROFILE)
    args = parser.parse_args()
    manifest = package(args.destination.absolute(), args.commit, profile=args.profile)
    print(json.dumps({key: manifest[key] for key in ("release", "commit", "tree", "bundle_sha256", "frontend_sha256")}))
