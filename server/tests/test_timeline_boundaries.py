"""Long-term synthetic topology and derived-number regressions (review K-C-02/03)."""
from copy import deepcopy
import json

from fastapi.testclient import TestClient
from server.app import app
from server.demo_engine import load_dataset
from server.timeline import build_timeline, check_timeline


def request(**changes):
    return {"scenarioId": "SC-HOSP", "isolationPlanId": "ISO-HOSP-MIN",
            "planId": "PLAN-REPAIR", "targetServiceIds": ["SV-HOSP"], "horizonHours": 8,
            **changes}


def test_isolated_active_source_is_reachable_without_an_incident_pipe():
    data = deepcopy(load_dataset())
    data["nodes"].append({**data["nodes"][0], "id": "SRC-ISOLATED"})
    data["services"].append({**data["services"][0], "id": "SV-ISOLATED", "nodeId": "SRC-ISOLATED"})
    scenario = next(s for s in data["scenarios"] if s["id"] == "SC-HOSP")
    scenario["activeSourceIds"].append("SRC-ISOLATED")
    args = request(targetServiceIds=["SV-ISOLATED"])
    metric = build_timeline(data, args)["serviceFirstRestored"]["SV-ISOLATED"]
    assert metric["status"] == "already_available" and metric["valueHours"] == 0
    scenario["activeSourceIds"].remove("SRC-ISOLATED")
    metric = build_timeline(data, args)["serviceFirstRestored"]["SV-ISOLATED"]
    assert metric["status"] == "not_recovered_within_horizon" and metric["valueHours"] is None


def test_finite_operands_with_nonfinite_sum_are_invalid_in_gate_and_calculation():
    args = request(actionSchedule=[{"actionId": "PLAN-REPAIR-action", "startOffsetHours": 1e308, "durationHours": 1e308}])
    result = build_timeline(load_dataset(), args)
    assert result["status"] == "invalid_input"
    assert result["planCompletion"]["valueHours"] is None
    assert result["events"] == []
    assert any("有限" in reason for reason in result["issues"])
    assert check_timeline(load_dataset(), args)["status"] == "invalid_input"
    json.dumps(result, allow_nan=False)  # A result must not serialize succeeded/Infinity.


def test_http_rejects_overflow_before_response_serialization():
    args = request(actionSchedule=[{"actionId": "PLAN-REPAIR-action", "startOffsetHours": 1e308, "durationHours": 1e308}])
    with TestClient(app) as client:
        response = client.post("/api/v1/demo/timeline", json=args)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert any(item["loc"][:2] == ["body", "actionSchedule"] for item in detail)
    assert "Out of range float" not in str(detail)


def test_response_freezes_request_and_data_version_without_changing_normal_time():
    args = request(actionSchedule=[{"actionId": "PLAN-REPAIR-action", "startOffsetHours": 1, "durationHours": 8}])
    data = load_dataset()
    result = build_timeline(data, args)
    assert result["planCompletion"]["valueHours"] == 9
    assert result["serviceFirstRestored"]["SV-HOSP"]["valueHours"] == 6
    assert result["timelineDataVersion"] == data["timeline"]["version"]
    args["actionSchedule"][0]["durationHours"] = 50
    assert result["inputRequest"]["actionSchedule"][0]["durationHours"] == 8
