"""Run preserved synthetic regressions without original samples or model calls.

Writes only a fresh output folder; copied engineering source is not modified.
The test selection is an intentional migration smoke scope, not full acceptance.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile

TESTS = [
    "tests/test_scoring_contracts.py",
    "tests/test_decision_contracts.py",
    "tests/test_recovery_contract.py",
    "tests/test_service_timeline.py",
    "tests/test_capabilities.py",
    "tests/test_m1a_dataset_timeline.py",
    "tests/test_unified_contracts.py",
    "tests/test_unified_case_consistency.py",
    "tests/test_report_identity.py",
    "tests/test_report_integrity.py",
    "tests/test_report_leaf_integrity.py",
    "tests/test_agent_ledger_close.py",
    "tests/test_agent_boundary_final.py",
    "tests/test_agent_closeout_regressions.py",
]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend-root", type=Path, required=True)
    p.add_argument("--output-dir", type=Path)
    p.add_argument("--scope", choices=["unified", "kernel"], default="unified")
    a = p.parse_args()
    kernel_only = {
        "tests/test_scoring_contracts.py", "tests/test_decision_contracts.py",
        "tests/test_recovery_contract.py", "tests/test_service_timeline.py",
        "tests/test_capabilities.py", "tests/test_agent_ledger_close.py",
        "tests/test_agent_boundary_final.py", "tests/test_agent_closeout_regressions.py",
    }
    selected_tests = TESTS if a.scope == "unified" else [name for name in TESTS if name in kernel_only]
    root = a.backend_root.resolve(strict=True)
    output = a.output_dir.resolve() if a.output_dir else Path(tempfile.mkdtemp(prefix="waterpulse-restore-check-"))
    if a.output_dir:
        output.mkdir(parents=True, exist_ok=False)
    if output.is_relative_to(root):
        p.error("output-dir must be outside the backend source")
    for key in list(os.environ):
        if key.startswith(("WATER_", "AGENT_", "OPENAI_", "LANGCHAIN_", "LANGSMITH_")):
            os.environ.pop(key)
    os.environ.update(WATER_LLM_MODE="mock", AGENT_ENABLED="0", PYTHONDONTWRITEBYTECODE="1")
    sys.dont_write_bytecode = True
    calls = []
    def blocked(self, address):
        calls.append(str(address))
        raise PermissionError("migration synthetic regression forbids outbound sockets")
    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    os.chdir(root)
    sys.path.insert(0, str(root))
    def hashes():
        return {str(f.relative_to(root)): hashlib.sha256(f.read_bytes()).hexdigest()
                for folder in ["src", "tests", "configs", "data/synthetic"]
                for f in sorted((root / folder).rglob("*"))
                if f.is_file() and "__pycache__" not in f.parts}
    before = hashes()
    import pytest
    code = pytest.main(["-q", "-o", "junit_family=xunit1", "-p", "no:cacheprovider", "--basetemp", str(output / "pytest-temp"),
        "--junitxml", str(output / "junit.xml"), *selected_tests])
    changed = before != hashes()
    summary = {"exit_code": int(code), "python": sys.version, "tests": selected_tests,
               "source_unchanged": not changed, "outbound_attempt_count": len(calls),
               "scope": a.scope,
               "limitation": "kernel subset does not replace unified platform tests; neither is full independent acceptance"}
    (output / "restore_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    raise SystemExit(code or (2 if changed else 0))

if __name__ == "__main__":
    main()
