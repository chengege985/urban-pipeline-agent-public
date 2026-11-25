"""REPORT-INTEGRITY-01: original eleven leaf failures and legal controls.

Tests use synthetic Runs and temporary persistence; no result is repaired or
recomputed during report reads. Existing 306 regressions remain unchanged.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from test_report_identity import completed_run, _persist_nested_clone
from test_report_integrity import full_decision, _execute_variant, _guard
from src.unified.report import ReportNotReady, build_report_from_run


CASES = [
    ("metric-restoration_hours-nan", ("plans", 0, "metrics", "restoration_hours"), float("nan")),
    ("metric-restoration_hours-positive_inf", ("plans", 0, "metrics", "restoration_hours"), float("inf")),
    ("metric-restoration_hours-negative_inf", ("plans", 0, "metrics", "restoration_hours"), float("-inf")),
    ("metric-restoration_hours-bool", ("plans", 0, "metrics", "restoration_hours"), True),
    ("breakdown-value-object", ("plans", 0, "utility_breakdown", 0, "value"), {"CANARY-INVALID": "not-a-number"}),
    ("breakdown-value-nan", ("plans", 0, "utility_breakdown", 0, "value"), float("nan")),
    ("check-reason-object", ("plans", 0, "constraints", "checks", 0, "reason"), {"CANARY-INVALID": "not-text"}),
    ("cost-metric-bool", ("plans", 0, "metrics", "total_cost_fen"), True),
    ("cost-metric-numeric-string", ("plans", 0, "metrics", "total_cost_fen"), "250000"),
    ("breakdown-normalized-object", ("plans", 0, "utility_breakdown", 0, "normalized"), {"value": 1}),
    ("breakdown-clipped-string", ("plans", 0, "utility_breakdown", 0, "clipped"), "false"),
]


def _put(obj, path, value):
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = deepcopy(value)


def _digest(value):
    # Test evidence only: allow nonfinite tokens so NaN equality cannot mask
    # input/persistence changes. Production output still must be valid JSON.
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=True)
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.mark.parametrize("layer", ["function", "http"])
@pytest.mark.parametrize("case,path,value", CASES, ids=[c[0] for c in CASES])
def test_original_eleven_leaf_conflicts(full_decision, monkeypatch, record_property, layer, case, path, value):
    client, app, original, snapshot = full_decision
    bad = deepcopy(original)
    _put(bad["result"], path, value)
    tested = _persist_nested_clone(app, bad) if layer == "http" else bad
    before = _digest((tested, snapshot, app.state.store.get_task(original["run_id"])))
    record_property("original_leaf_case", case)
    record_property("result_sha256", _digest(tested["result"]))
    with _guard(app, snapshot, monkeypatch, record_property):
        if layer == "function":
            with pytest.raises(ReportNotReady) as exc:
                build_report_from_run(tested, snapshot)
            reason = str(exc.value)
            record_property("actual", "ReportNotReady")
        else:
            response = client.get(f"/api/v1/runs/{tested['run_id']}/report")
            record_property("actual", response.status_code)
            assert response.status_code == 409, response.text
            reason = response.json()["detail"]
            assert _digest(app.state.store.get_task(tested["run_id"])) == _digest(tested)
        assert path[-1] in reason and "artifact_ref" not in reason
        assert "CANARY-INVALID" not in reason
        assert _digest((tested, snapshot, app.state.store.get_task(original["run_id"]))) == before


@pytest.mark.parametrize("layer", ["function", "http"])
@pytest.mark.parametrize("case", ["complete", "nullable_restoration", "zero_values", "typed_breakdown",
                                  "text_reason", "unknown_constraints", "partial_population",
                                  "missing_selected_metric", "unknown_cost"])
def test_legal_leaf_values_and_states(full_decision, monkeypatch, record_property, layer, case):
    fixture = full_decision
    if case in ("unknown_constraints", "partial_population", "missing_selected_metric", "unknown_cost"):
        def change(payload, request):
            if case == "unknown_constraints":
                request["resource_capacities"].pop("water_truck")
            elif case == "missing_selected_metric":
                payload["value_profile"]["criteria"] = [
                    {"metric": "restoration_hours", "direction": "min", "lower": 0, "upper": 8, "weight": 1}]
            elif case == "unknown_cost":
                next(a for a in payload["actions"] if a["action_id"] == "syn-backup")["cost_fen"] = None
            else:
                next(s for s in payload["services"] if s["service_id"] == "syn-households-C")["count_value"] = None
        fixture = _execute_variant(full_decision, change)
    client, app, run, snapshot = fixture
    tested = deepcopy(run)
    row = tested["result"]["plans"][0]
    if case == "nullable_restoration":
        # Producer legitimately emits this for successful backup-only candidates.
        backup = next(p for p in tested["result"]["plans"] if p["plan_id"] == "syn-plan-backup")
        assert backup["status"] == "succeeded" and backup["metrics"]["restoration_hours"] is None
    elif case == "zero_values":
        row["metrics"] = {k: 0 for k in row["metrics"]}
    elif case == "typed_breakdown":
        item = row["utility_breakdown"][0]
        item.update(value=0, normalized=0.5, contribution=0.25, clipped=True)
    elif case == "text_reason":
        row["constraints"]["checks"][0]["reason"] = "合成条件说明：已检查"
    elif case == "unknown_constraints":
        assert tested["status"] == "insufficient_data"
        backup = next(p for p in tested["result"]["plans"] if p["plan_id"] == "syn-plan-backup")
        assert backup["utility"] is None and any(c["state"] == "unknown" for c in backup["checks"])
    elif case == "missing_selected_metric":
        backup = next(p for p in tested["result"]["plans"] if p["plan_id"] == "syn-plan-backup")
        assert tested["status"] == backup["status"] == "insufficient_data"
        assert backup["metrics"]["restoration_hours"] is None and backup["utility"] is None
        assert backup["utility_breakdown"] == []
    elif case == "unknown_cost":
        backup = next(p for p in tested["result"]["plans"] if p["plan_id"] == "syn-plan-backup")
        assert tested["status"] == backup["status"] == "insufficient_data"
        assert backup["total_cost_fen"] is None and backup["utility"] is None
        assert any(c["state"] == "unknown" and c["left"] is None for c in backup["checks"])
    elif case == "partial_population":
        impact = tested["result"]["baseline_impact"]
        assert impact["status"] == "partial" and impact["potentially_disconnected_counts"]["household"] is None
    # A legitimate boolean here denotes operability, not a business number.
    checks = row.get("constraints", row).get("checks", [])
    assert any(type(c.get("left")) is bool for c in checks)
    if layer == "http":
        tested = _persist_nested_clone(app, tested)
    before = _digest((tested, snapshot))
    with _guard(app, snapshot, monkeypatch, record_property):
        if layer == "function":
            report = build_report_from_run(tested, snapshot).model_dump(mode="json")
        else:
            response = client.get(f"/api/v1/runs/{tested['run_id']}/report")
            assert response.status_code == 200, response.text
            report = response.json()
            assert app.state.store.get_task(tested["run_id"]) == tested
        assert report["decision"] == tested["result"]
        assert report["recomputation"] == "none"
        assert _digest((tested, snapshot)) == before
