"""Read-only adapter for the pinned synthetic gas case.

This is the first platform-facing gas capability. It evaluates the supplied
fault/isolation case with the candidate graph algorithm and never infers
pressure, flow, leakage probability, physical restoration, or field actions.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contracts import GasCaseResponse
from .network import evaluate_case, validate_network


class FixtureUnavailable(RuntimeError):
    """A server-owned fixture cannot be read or does not satisfy its contract."""


DEFAULT_FIXTURE_ROOT = Path(__file__).resolve().parents[2] / "experiments" / "gas_m0" / "data"
CASE_ID = "syn-gas-case-K-001"
FIXTURE_FILES = ("gas54_network.json", "gas54_case.json")


def _default_roots(repo_root: Path) -> tuple[Path, ...]:
    return (
        DEFAULT_FIXTURE_ROOT,
        repo_root / "experiments" / "gas_m0_fix_20261104" / "workspace" / "experiments" / "gas_m0_20261104" / "data",
        DEFAULT_FIXTURE_ROOT.parent / "gas_m0_fix_20261104" / "workspace" / "experiments" / "gas_m0_20261104" / "data",
    )


def _approved_roots(repo_root: Path) -> tuple[Path, ...]:
    roots = list(_default_roots(repo_root))
    configured = os.getenv("WATER_GAS_FIXTURE_ALLOWED_ROOTS", "")
    for item in configured.split(os.pathsep):
        if not item.strip():
            continue
        path = Path(item).expanduser()
        if path.is_symlink():
            raise FixtureUnavailable("fixture allowlist root must not be a symlink")
        try:
            roots.append(path.resolve(strict=True))
        except (OSError, RuntimeError) as exc:
            raise FixtureUnavailable("fixture allowlist is unavailable") from exc
    return tuple(roots)


def _validated_root(candidate: Path, *, approved_roots: tuple[Path, ...]) -> Path:
    try:
        raw = candidate.expanduser()
        if raw.is_symlink():
            raise FixtureUnavailable("fixture root must not be a symlink")
        root = raw.resolve(strict=True)
        if not root.is_dir() or not any(
            root == approved or root.is_relative_to(approved) for approved in approved_roots
        ):
            raise FixtureUnavailable("fixture root is outside the server allowlist")
        for name in FIXTURE_FILES:
            file_path = root / name
            if file_path.is_symlink():
                raise FixtureUnavailable("fixture file must not be a symlink")
            resolved = file_path.resolve(strict=True)
            if resolved.parent != root or not resolved.is_file():
                raise FixtureUnavailable("fixture file escapes its configured directory")
        return root
    except FixtureUnavailable:
        raise
    except (OSError, RuntimeError) as exc:
        raise FixtureUnavailable("fixture directory is unavailable") from exc


def fixture_root() -> Path:
    configured = os.getenv("WATER_GAS_FIXTURE_ROOT", "").strip()
    repo_root = Path(__file__).resolve().parents[3]
    approved_roots = _approved_roots(repo_root)
    if configured:
        return _validated_root(Path(configured), approved_roots=approved_roots)

    for candidate in _default_roots(repo_root):
        try:
            return _validated_root(candidate, approved_roots=approved_roots)
        except FixtureUnavailable:
            continue
    raise FixtureUnavailable("no approved fixture directory is available")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureUnavailable("fixture content is unreadable") from exc
    if not isinstance(value, dict):
        raise FixtureUnavailable("fixture root must be an object")
    return value


def load_case(case_id: str = CASE_ID) -> tuple[dict[str, Any], dict[str, Any]]:
    if case_id != CASE_ID:
        raise KeyError(case_id)
    root = fixture_root()
    try:
        network = _read_json(root / "gas54_network.json")
        case = _read_json(root / "gas54_case.json")
        validate_network(network)
        if case.get("case_id") != case_id:
            raise ValueError("fixture case identity mismatch")
        return network, case
    except FixtureUnavailable:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise FixtureUnavailable("fixture validation failed") from exc


def _scoring_contract() -> dict[str, Any]:
    return {
        "status": "unavailable",
        "rule_version": "gas-g15-arithmetic-candidate/0.1.0",
        "business_validation": "not_business_validated",
        "required_indicators": ["G01", "G02", "G04"],
        "available_indicators": [],
        "formula_status": "not_available",
        "h_status": "unsupported_pending_business_definition",
        "p_semantics": "P is a candidate condition/risk score, not failure probability",
        "coverage_semantics": "E is effective-weight data coverage, not prediction confidence",
        "note": "当前合成案例没有可追溯的逐管段G01-G15评分输入；页面显示未知，不补造Hs。",
    }


def _validate_response_contract(value: dict[str, Any]) -> None:
    """Turn a malformed server-owned fixture into the controlled 503 path.

    FastAPI response validation runs after the route returns. Letting a
    Pydantic ValidationError escape from there produces an unhelpful 500
    and makes the list and detail endpoints disagree about the same fixture.
    This boundary validates the complete result while the adapter still owns
    the fixture error mapping; unrelated programming exceptions are not
    swallowed.
    """
    try:
        GasCaseResponse.model_validate(value)
    except ValidationError as exc:
        raise FixtureUnavailable("fixture result does not satisfy the response contract") from exc


def evaluate_fixture(case_id: str = CASE_ID) -> dict[str, Any]:
    network, case = load_case(case_id)
    try:
        result = evaluate_case(network, case)
    except (ValueError, TypeError, KeyError) as exc:
        raise FixtureUnavailable("fixture evaluation failed") from exc
    counts = result["validation"]["counts"]
    value = {
        "schema_version": "gas-case-view-0.1.0",
        "case_id": result["identity"]["case_id"],
        "network_id": result["identity"]["network_id"],
        "scenario_id": result["identity"]["scenario_id"],
        "plan_id": result["identity"]["plan_id"],
        "data_mode": result["data_mode"],
        "status": result["status"],
        "model_status": result["model_status"],
        "validation_status": result["validation_status"],
        "semantic": result["semantic"],
        "algorithm_version": result["algorithm_version"],
        "analysis_window_hours": result["analysis_window_hours"],
        "evidence": result["evidence"],
        "counts": counts,
        "nodes": network["nodes"],
        "edges": network["edges"],
        "pipes": network["pipes"],
        "valves": network["valves"],
        "sources": network["sources"],
        "consumers": network["consumers"],
        "services": result["current"]["services"],
        "scenario": case["scenario"],
        "isolation_plan": case["isolation_plan"],
        "timeline_events": result["states"],
        "current": result["current"],
        "metrics": result["metrics"],
        "scoring": _scoring_contract(),
        "safety": result["safety"],
    }
    _validate_response_contract(value)
    return value


def list_cases() -> list[dict[str, Any]]:
    value = evaluate_fixture()
    return [{key: value[key] for key in (
        "case_id", "network_id", "scenario_id", "plan_id", "data_mode",
        "model_status", "validation_status", "semantic", "counts",
    )}]
