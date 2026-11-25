"""Run isolated GAS-M0 tests with Python network and outside-write guards."""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"
TEMP = EVIDENCE / "run_tmp"
TEMP.mkdir(parents=True, exist_ok=True)
tempfile.tempdir = str(TEMP)
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))
NODE = Path(shutil.which("node") or "missing-node").resolve()
COUNTS = {"network_attempts": 0, "outside_write_attempts": 0,
          "rejected_processes": 0, "approved_node_processes": 0}
BLOCKED_WRITES = []
ACTIVE_COMMANDS = []
ORIGINAL_POPEN = subprocess.Popen


def contained(value):
    if isinstance(value, int):
        return True  # existing stdout/stderr descriptors, not a new path
    if not isinstance(value, (str, bytes, os.PathLike)):
        return False
    p = Path(os.fsdecode(value)).resolve()
    return p == ROOT or ROOT in p.parents


def require_write_path(value):
    if not contained(value):
        COUNTS["outside_write_attempts"] += 1
        BLOCKED_WRITES.append(os.fsdecode(value) if isinstance(value, (str, bytes, os.PathLike)) else repr(value))
        raise PermissionError("GAS-M0 test attempted a write outside its isolated workspace")


def guarded_popen(args, *positional, **kwargs):
    # Validate the original argv before Windows converts it to a command-line string.
    allowed = isinstance(args, (list, tuple)) and len(args) >= 2 and not kwargs.get("shell")
    if allowed:
        executable = Path(shutil.which(args[0]) or args[0]).resolve()
        allowed = executable == NODE and Path(args[1]).resolve() == ROOT / "tools/extract_gas_network.mjs"
        rest = args[2:]
        allowed = allowed and len(rest) % 2 == 0 and "--output-root" in rest
        if allowed:
            allowed = all(rest[i] in {"--source", "--output-root"} and contained(rest[i + 1])
                          for i in range(0, len(rest), 2))
    if not allowed or kwargs.get("executable") is not None:
        COUNTS["rejected_processes"] += 1
        raise PermissionError("Only the reviewed extractor with isolated output may be launched")
    ACTIVE_COMMANDS.append((list(args), subprocess.list2cmdline(args)))
    try:
        return ORIGINAL_POPEN(args, *positional, **kwargs)
    finally:
        ACTIVE_COMMANDS.pop()


def audit(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.bind", "socket.getaddrinfo"}:
        COUNTS["network_attempts"] += 1
        raise PermissionError("GAS-M0 tests have no network access")
    if event == "open":
        filename, mode, flags = args
        write = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (
            isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if write:
            require_write_path(filename)
    elif event in {"os.remove", "os.rmdir", "os.mkdir", "os.chmod", "os.truncate", "os.utime"}:
        require_write_path(args[0])
    elif event in {"os.rename", "os.link", "os.symlink"}:
        require_write_path(args[0]); require_write_path(args[1])
    elif event == "subprocess.Popen":
        executable, argv, cwd, env = args
        allowed = bool(ACTIVE_COMMANDS) and argv in ACTIVE_COMMANDS[-1]
        if not allowed:
            COUNTS["rejected_processes"] += 1
            raise PermissionError("Only the reviewed offline Node extractor may be a test subprocess")
        COUNTS["approved_node_processes"] += 1
    elif event in {"os.system", "os.startfile"}:
        COUNTS["rejected_processes"] += 1
        raise PermissionError("Shell execution is disabled during GAS-M0 tests")


class RecordedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cases = []
        self.subtest_failures = []

    def addSuccess(self, test):
        super().addSuccess(test); self.cases.append({"id": test.id(), "status": "passed"})

    def addFailure(self, test, err):
        super().addFailure(test, err); self.cases.append({"id": test.id(), "status": "failed"})

    def addError(self, test, err):
        super().addError(test, err); self.cases.append({"id": test.id(), "status": "error"})

    def addSkip(self, test, reason):
        super().addSkip(test, reason); self.cases.append({"id": test.id(), "status": "skipped", "reason": reason})

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self.subtest_failures.append(subtest.id())


def main():
    os.chdir(ROOT)
    tracked = sorted([*ROOT.glob("src/**/*.py"), *ROOT.glob("tools/*.py"),
                      *ROOT.glob("tools/*.mjs"), *ROOT.glob("tests/*.py"), *ROOT.glob("data/*")])
    fingerprints = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in tracked if p.is_file()}
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    sys.addaudithook(audit)
    subprocess.Popen = guarded_popen
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern="test_*.py")
    stream = io.StringIO(); tick = time.monotonic()
    result = unittest.TextTestRunner(stream=stream, verbosity=2, resultclass=RecordedResult).run(suite)
    duration = time.monotonic() - tick
    source_drift = [name for name, digest in fingerprints.items()
                    if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]
    success = result.wasSuccessful() and not source_drift
    payload = {"task": "CODEX-GAS-START-01", "status": "development_tests_only",
        "started_at": started, "python": sys.version, "platform": sys.platform,
        "windows_version": list(sys.getwindowsversion()) if hasattr(sys, "getwindowsversion") else None,
        "tests_run": result.testsRun, "passed": sum(c["status"] == "passed" for c in result.cases),
        "failures": len(result.failures), "errors": len(result.errors), "skipped": len(result.skipped),
        "duration_seconds": duration, "successful": success, "cases": result.cases,
        "subtest_failures": result.subtest_failures, "guard_counts": COUNTS,
        "blocked_write_paths": BLOCKED_WRITES,
        "source_sha256": fingerprints, "source_drift": source_drift,
        "limits": ["No HTTP/platform/browser/real-model test was run.",
                   "Python audit guards cover this process. Node executes only a SHA-pinned reviewed extraction block.",
                   "Candidate correctness does not approve business rules or deployment."]}
    (EVIDENCE / "test_results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (EVIDENCE / "tests.log").write_text(stream.getvalue(), encoding="utf-8")
    print(stream.getvalue(), end="")
    print(json.dumps({k: payload[k] for k in ["tests_run", "passed", "failures", "errors", "skipped", "successful", "guard_counts", "source_drift"]}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
