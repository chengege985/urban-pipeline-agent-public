"""Competition-only plan timeline regression: explicit events, gates and HTTP parity."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.demo_engine import load_dataset
from server.llm_assistant import _execute_tool
from server.timeline import ALGORITHM_VERSION, build_timeline, check_timeline

client = TestClient(app, raise_server_exceptions=False)


def repair(**overrides):
    request = {
        "scenarioId": "SC-HOSP",
        "isolationPlanId": "ISO-HOSP-MIN",
        "planId": "PLAN-REPAIR",
        "targetServiceIds": ["SV-HOSP", "SV-SCHOOL"],
        "horizonHours": 8,
    }
    request.update(overrides)
    return request


def test_dataset_has_explicit_synthetic_timeline_events():
    dataset = load_dataset()
    assert dataset["timeline"]["version"] == "synthetic-timeline-2026-11-01"
    assert len(dataset["timeline"]["events"]) == 10
    assert len(dataset["timeline"]["tasks"]) == 15
    repair_event = next(
        event for event in dataset["timeline"]["events"]
        if event["scenarioId"] == "SC-HOSP" and event["planId"] == "PLAN-REPAIR"
    )
    assert repair_event["edgeIds"] == ["WP-016"]
    assert repair_event["effectiveOffsetHours"] == 6
    assert "synthetic:assumption:repair-restores-at-6h" in repair_event["evidenceIds"]


def test_default_repair_keeps_6h_restoration_and_missing_completion():
    gate_response = client.post("/api/v1/demo/timeline/check", json=repair())
    assert gate_response.status_code == 200
    gate = gate_response.json()
    assert gate["status"] == "succeeded"
    assert gate["canCompute"] is True
    assert gate["calculationStatus"] == "partial"
    assert "service_first_restored" in gate["availableOutputs"]
    assert "plan_completion" not in gate["availableOutputs"]
    assert any("抢修任务" in item for item in gate["missingItems"])

    response = client.post("/api/v1/demo/timeline", json=repair())
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "partial"
    assert result["timelineStatus"] == "succeeded"
    assert result["algorithmVersion"] == ALGORITHM_VERSION
    assert result["asOf"] == "2026-11-01T08:00:00+08:00"
    assert result["planCompletion"]["status"] == "insufficient_data"
    assert result["planCompletion"]["valueHours"] is None
    assert result["firstTargetServiceRestored"] == {
        "valueHours": 6,
        "status": "succeeded",
        "reason": "初始不可用目标中最早首次恢复的时刻。",
        "criterionId": "topological_reachability",
        "timeBasis": "planned",
        "observationHorizonHours": 8,
    }
    assert result["allTargetServicesRestored"]["valueHours"] == 6
    assert result["serviceFirstRestored"]["SV-HOSP"]["valueHours"] == 6
    assert result["serviceFirstRestored"]["SV-SCHOOL"]["status"] == "already_available"
    assert result["events"] == [{
        "atHours": 6,
        "actionIds": ["PLAN-REPAIR-action"],
        "restoredEdgeIds": ["WP-016"],
        "serviceAvailability": {"SV-HOSP": True, "SV-SCHOOL": True},
        "evidenceIds": [
            "synthetic:timeline:SC-HOSP:PLAN-REPAIR",
            "scenario:SC-HOSP",
            "plan:PLAN-REPAIR",
            "synthetic:assumption:repair-restores-at-6h",
        ],
    }]


def test_user_supplied_finish_is_not_clamped_to_horizon():
    request = repair(actionSchedule=[{"actionId": "PLAN-REPAIR-action", "finishOffsetHours": 9}])
    response = client.post("/api/v1/demo/timeline", json=request)
    result = response.json()
    assert result["status"] == "succeeded"
    assert result["planCompletion"]["valueHours"] == 9
    assert result["planCompletion"]["beyondHorizon"] is True
    assert result["serviceFirstRestored"]["SV-HOSP"]["valueHours"] == 6


def test_start_plus_duration_and_contradiction():
    derived = client.post("/api/v1/demo/timeline", json=repair(actionSchedule=[{
        "actionId": "PLAN-REPAIR-action", "startOffsetHours": 1, "durationHours": 8,
    }])).json()
    assert derived["status"] == "succeeded"
    assert derived["planCompletion"]["valueHours"] == 9

    contradictory = client.post("/api/v1/demo/timeline", json=repair(actionSchedule=[{
        "actionId": "PLAN-REPAIR-action", "finishOffsetHours": 7,
        "startOffsetHours": 1, "durationHours": 8,
    }]))
    assert contradictory.status_code == 422


def test_replacement_outside_window_has_no_fake_time():
    request = repair(planId="PLAN-REPLACE", targetServiceIds=["SV-HOSP"])
    result = client.post("/api/v1/demo/timeline", json=request).json()
    assert result["status"] == "succeeded"
    assert result["events"] == []
    assert result["planCompletion"] == {
        "valueHours": 24,
        "status": "succeeded",
        "reason": "全部任务结束时间已声明；方案完成时间不等于服务恢复时间。",
        "criterionId": "topological_reachability",
        "timeBasis": "planned",
        "observationHorizonHours": 8,
        "beyondHorizon": True,
    }
    assert result["serviceFirstRestored"]["SV-HOSP"]["valueHours"] is None
    assert result["serviceFirstRestored"]["SV-HOSP"]["status"] == "not_recovered_within_horizon"
    assert result["allTargetServicesRestored"]["valueHours"] is None

    wide = repair(planId="PLAN-REPLACE", targetServiceIds=["SV-HOSP"], horizonHours=30)
    wide_result = client.post("/api/v1/demo/timeline", json=wide).json()
    assert wide_result["serviceFirstRestored"]["SV-HOSP"]["valueHours"] == 24
    assert wide_result["allTargetServicesRestored"]["valueHours"] == 24
    assert wide_result["planCompletion"]["beyondHorizon"] is False


def test_empty_missing_and_unknown_targets_are_distinct():
    empty = client.post("/api/v1/demo/timeline", json=repair(targetServiceIds=[])).json()
    assert empty["targetServiceIds"] == []
    assert empty["serviceFirstRestored"] == {}
    assert empty["firstTargetServiceRestored"]["status"] == "not_applicable"
    assert empty["allTargetServicesRestored"]["status"] == "not_applicable"
    assert empty["allTargetServicesRestored"]["valueHours"] is None

    missing_payload = repair()
    missing_payload.pop("targetServiceIds")
    missing = client.post("/api/v1/demo/timeline", json=missing_payload).json()
    assert missing["status"] == "invalid_input"
    assert missing["events"] == []
    assert missing["planCompletion"]["valueHours"] is None

    unknown = client.post("/api/v1/demo/timeline", json=repair(targetServiceIds=["SV-GHOST"])).json()
    assert unknown["status"] == "invalid_input"
    assert unknown["events"] == []

    duplicate = client.post("/api/v1/demo/timeline", json=repair(targetServiceIds=["SV-HOSP", "SV-HOSP"])).json()
    assert duplicate["status"] == "invalid_input"


def test_invalid_schedule_has_no_numbers_or_events():
    for schedule in (
        [{"actionId": "ghost-action", "finishOffsetHours": 6}],
        [{"actionId": "PLAN-REPAIR-action", "finishOffsetHours": 6},
         {"actionId": "PLAN-REPAIR-action", "finishOffsetHours": 9}],
    ):
        gate = client.post("/api/v1/demo/timeline/check", json=repair(actionSchedule=schedule)).json()
        assert gate["status"] == "invalid_input"
        assert gate["canCompute"] is False
        assert gate["availableOutputs"] == []
        assert gate["evidenceIds"] == []

        result = client.post("/api/v1/demo/timeline", json=repair(actionSchedule=schedule)).json()
        assert result["status"] == "invalid_input"
        assert result["planCompletion"]["valueHours"] is None
        assert result["firstTargetServiceRestored"]["valueHours"] is None
        assert result["allTargetServicesRestored"]["valueHours"] is None
        assert result["serviceFirstRestored"] == {}
        assert result["events"] == []
        assert result["evidenceIds"] == []


@pytest.mark.parametrize("schedule", [
    [{"actionId": "PLAN-REPAIR-action", "finishOffsetHours": True}],
    [{"actionId": "PLAN-REPAIR-action", "finishOffsetHours": "6"}],
    [{"actionId": "PLAN-REPAIR-action", "startOffsetHours": -1}],
])
def test_structurally_invalid_schedule_is_422(schedule):
    response = client.post("/api/v1/demo/timeline", json=repair(actionSchedule=schedule))
    assert response.status_code == 422


def test_unknown_references_are_not_business_success():
    assert client.post("/api/v1/demo/timeline", json=repair(scenarioId="SC-GHOST")).status_code == 404
    assert client.post("/api/v1/demo/timeline", json=repair(planId="PLAN-GHOST")).status_code == 404
    assert client.post("/api/v1/demo/timeline", json=repair(isolationPlanId="ISO-GHOST")).status_code == 404
    assert client.post("/api/v1/demo/timeline", json=repair(isolationPlanId="ISO-SCHOOL-MIN")).status_code == 422
    assert client.post("/api/v1/demo/timeline", json=repair(horizonHours=0)).status_code == 422
    assert client.post("/api/v1/demo/timeline", json=repair(horizonHours=721)).status_code == 422


def test_direct_engine_rejects_non_finite_and_bool_time_values():
    result = build_timeline(load_dataset(), repair(actionSchedule=[{
        "actionId": "PLAN-REPAIR-action", "finishOffsetHours": float("inf"),
    }]))
    assert result["status"] == "invalid_input" and result["events"] == []
    result = build_timeline(load_dataset(), repair(actionSchedule=[{
        "actionId": "PLAN-REPAIR-action", "finishOffsetHours": True,
    }]))
    assert result["status"] == "invalid_input" and result["planCompletion"]["valueHours"] is None
    gate = check_timeline(load_dataset(), repair(actionSchedule={"actionId": "PLAN-REPAIR-action"}))
    assert gate["status"] == "invalid_input" and gate["canCompute"] is False


def test_monitoring_completion_does_not_imply_service_restoration():
    result = client.post("/api/v1/demo/timeline", json=repair(
        planId="PLAN-MONITOR", targetServiceIds=["SV-HOSP"],
    )).json()
    assert result["status"] == "succeeded"
    assert result["planCompletion"]["valueHours"] == 2
    assert result["events"] == []
    assert result["serviceFirstRestored"]["SV-HOSP"]["status"] == "not_recovered_within_horizon"


def test_assistant_explains_timeline_without_model_or_governance_approval():
    context = {
        "scenarioId": "SC-HOSP",
        "isolationPlanId": "ISO-HOSP-MIN",
        "planId": "PLAN-REPAIR",
        "targetServiceIds": ["SV-HOSP"],
        "horizonHours": 8,
    }
    with patch.dict("os.environ", {"COMPETITION_LLM_BASE_URL": ""}):
        response = client.post("/api/v1/demo/assistant", json={"question": "这个方案多久恢复？", "context": context})
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "partial"
    assert "6" in answer["answer"] and "待补数据" in answer["answer"]
    assert answer["refused"] is False
    assert answer["toolCalls"][0]["tool"] == "analyze_plan_timeline"
    assert answer["toolCalls"][0]["status"] == "partial"
    assert not any("接受" in item or "审批" in item for item in [answer["answer"]])


def test_timeline_tool_uses_frozen_context_and_cannot_change_plan_or_targets():
    context = {
        "scenarioId": "SC-HOSP",
        "isolationPlanId": "ISO-HOSP-MIN",
        "planId": "PLAN-REPAIR",
        "targetServiceIds": ["SV-HOSP"],
        "horizonHours": 8,
    }
    result = _execute_tool(load_dataset(), "analyze_plan_timeline", {}, context)
    assert result["ok"] is False
    assert result["timeline"]["planId"] == "PLAN-REPAIR"
    assert result["timeline"]["serviceFirstRestored"]["SV-HOSP"]["valueHours"] == 6
    assert result["gate"]["calculationStatus"] == "partial"

    with pytest.raises(ValueError, match="不得改变"):
        _execute_tool(load_dataset(), "analyze_plan_timeline", {"planId": "PLAN-REPLACE"}, context)
    with pytest.raises(ValueError, match="不得改变"):
        _execute_tool(load_dataset(), "analyze_plan_timeline", {"targetServiceIds": ["SV-SCHOOL"]}, context)
    with pytest.raises(ValueError, match="不得改变"):
        _execute_tool(load_dataset(), "analyze_plan_timeline", {"horizonHours": 24}, context)
    assert _execute_tool(load_dataset(), "analyze_plan_timeline", {}, {"scenarioId": "SC-HOSP"})["ok"] is False
