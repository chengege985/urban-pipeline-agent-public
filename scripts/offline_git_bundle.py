"""Offline Git transfer with a shared ancestor; never merges or overwrites a tree.

export writes a new bundle plus manifest. receive validates both and imports one
new review branch into a clean repository. Transport and human review are separate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    if result.returncode:
        raise ValueError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def check_branch(repo: Path, branch: str) -> None:
    git(repo, "check-ref-format", "--branch", branch)
    if not branch.startswith(("refactor/", "feature/", "integration/", "fix/")):
        raise ValueError("Only a named review branch may be transferred; main is not a receive target")


def export_bundle(repo: Path, base: str, branch: str, bundle: Path) -> dict:
    repo, bundle = repo.resolve(), bundle.resolve()
    check_branch(repo, branch)
    if git(repo, "status", "--porcelain"):
        raise ValueError("Commit or preserve work first; dirty source is not exported")
    base_sha = git(repo, "rev-parse", "--verify", base + "^{commit}")
    head = git(repo, "rev-parse", "--verify", "refs/heads/" + branch + "^{commit}")
    git(repo, "merge-base", "--is-ancestor", base_sha, head)
    if head == base_sha:
        raise ValueError("No new commits to transfer")
    manifest = bundle.with_suffix(bundle.suffix + ".json")
    if bundle.exists() or manifest.exists():
        raise ValueError("Refuse to overwrite an existing transfer")
    bundle.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "bundle", "create", str(bundle), base_sha + "..refs/heads/" + branch)
    payload = {
        "format": "urban-pipeline-offline-bundle/v1", "base_commit": base_sha,
        "head_commit": head, "branch": branch,
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "changed_paths": git(repo, "diff", "--name-status", base_sha, head).splitlines(),
        "does_not_merge_or_deploy": True,
    }
    with manifest.open("x", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False, indent=2)
        output.write("\n")
    return payload


def receive_bundle(repo: Path, bundle: Path, manifest: Path, branch: str) -> dict:
    repo, bundle, manifest = repo.resolve(), bundle.resolve(), manifest.resolve()
    check_branch(repo, branch)
    if git(repo, "status", "--porcelain"):
        raise ValueError("Dirty target: no checkout, reset or overwrite is permitted")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    if payload.get("format") != "urban-pipeline-offline-bundle/v1" or payload.get("branch") != branch:
        raise ValueError("Transfer format or branch mismatch")
    if hashlib.sha256(bundle.read_bytes()).hexdigest() != payload.get("bundle_sha256"):
        raise ValueError("Bundle SHA-256 mismatch")
    base, head = payload["base_commit"], payload["head_commit"]
    for value in (base, head):
        if len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Expected a complete Git commit SHA")
    if git(repo, "branch", "--list", branch):
        raise ValueError("Target review branch already exists; inspect it instead of overwriting")
    git(repo, "cat-file", "-e", base + "^{commit}")
    git(repo, "merge-base", "--is-ancestor", base, "HEAD")
    git(repo, "bundle", "verify", str(bundle))
    expected = head + " refs/heads/" + branch
    heads = git(repo, "bundle", "list-heads", str(bundle)).splitlines()
    if heads != [expected]:
        raise ValueError("Bundle must advertise exactly the expected review branch")
    # Import objects privately and prove the ancestry/diff before creating a branch.
    git(repo, "fetch", "--no-tags", str(bundle), "refs/heads/" + branch)
    if git(repo, "rev-parse", "FETCH_HEAD") != head:
        raise ValueError("Fetched head mismatch")
    git(repo, "merge-base", "--is-ancestor", base, head)
    if git(repo, "diff", "--name-status", base, head).splitlines() != payload["changed_paths"]:
        raise ValueError("Changed-path manifest mismatch")
    git(repo, "branch", branch, head)
    return {"status": "review_branch_imported", "base_commit": base, "head_commit": head,
            "branch": branch, "current_branch_unchanged": True, "merged": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    send = commands.add_parser("export")
    send.add_argument("--repo", type=Path, required=True)
    send.add_argument("--base", required=True)
    send.add_argument("--branch", required=True)
    send.add_argument("--bundle", type=Path, required=True)
    receive = commands.add_parser("receive")
    receive.add_argument("--repo", type=Path, required=True)
    receive.add_argument("--bundle", type=Path, required=True)
    receive.add_argument("--manifest", type=Path, required=True)
    receive.add_argument("--branch", required=True)
    args = parser.parse_args()
    if args.command == "export":
        result = export_bundle(args.repo, args.base, args.branch, args.bundle)
    else:
        result = receive_bundle(args.repo, args.bundle, args.manifest, args.branch)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
