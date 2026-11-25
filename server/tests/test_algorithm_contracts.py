"""Regression contracts for scenario identity, immutable context and numeric boundaries."""
from __future__ import annotations
import copy
import json
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from server.app import app
from server.demo_engine import DEFAULT_CONSTRAINTS, compare_plans, evaluate_health, load_dataset, simulate_impact
from server.llm_assistant import _execute_tool

client = TestClient(app, raise_server_exceptions=False)

@pytest.mark.parametrize("override", [
    {"installYear": "NaN"}, {"installYear": "2020"}, {"installYear": True},
    {"diameterMm": "NaN"}, {"diameterMm": "Infinity"}, {"diameterMm": False},
    {"diameterMm": 0}, {"depthM": -1}, {"leakCount": -1}, {"repairCount": 1.5},
    {"pressure": "arbitrary"}, {"id": "WP-002"},
])
def test_invalid_score_input_is_422(override):
    response = client.post("/api/v1/demo/evaluate", json={"pipeId": "WP-001", "override": override})
    assert response.status_code == 422

@pytest.mark.parametrize("budget", [None, "NaN", "5000", True, -1])
def test_invalid_budget_is_422(budget):
    constraints = {**DEFAULT_CONSTRAINTS, "budgetYuan": budget}
    assert client.post("/api/v1/demo/compare", json={"scenarioId": "SC-HOSP", "constraints": constraints}).status_code == 422

@pytest.mark.parametrize("isolation,code", [("ISO-NOT-EXISTS", 404), ("ISO-SCHOOL-MIN", 422)])
def test_invalid_isolation_never_falls_back(isolation, code):
    for endpoint in ("simulate", "compare"):
        response = client.post(f"/api/v1/demo/{endpoint}", json={"scenarioId": "SC-HOSP", "isolationPlanId": isolation})
        assert response.status_code == code


def test_partial_network_cannot_recommend_or_claim_recovery():
    data = copy.deepcopy(load_dataset())
    next(s for s in data["scenarios"] if s["id"] == "SC-HOSP")["activeSourceIds"] = []
    assert simulate_impact(data, "SC-HOSP")["status"] == "partial"
    result = compare_plans(data, "SC-HOSP")
    assert result["status"] == "insufficient_data"
    assert result["rows"] == [] and result["recommendedPlanId"] is None
    assert any("水源" in item for item in result["limitations"])


def test_invalid_endpoint_is_not_created_implicitly():
    data = copy.deepcopy(load_dataset())
    data["pipes"][0]["fromNodeId"] = "ghost-node"
    with pytest.raises(ValueError, match="端点"):
        simulate_impact(data, "SC-HOSP")


def test_unknown_duplicate_and_empty_candidates_are_distinct():
    assert client.post("/api/v1/demo/compare", json={"scenarioId": "SC-HOSP", "planIds": ["PLAN-REPAIR", "ghost"]}).status_code == 404
    assert client.post("/api/v1/demo/compare", json={"scenarioId": "SC-HOSP", "planIds": ["PLAN-REPAIR", "PLAN-REPAIR"]}).status_code == 422
    result = client.post("/api/v1/demo/compare", json={"scenarioId": "SC-HOSP", "planIds": []}).json()
    assert result["status"] == "insufficient_data" and result["recommendedPlanId"] is None and not result["rows"]


def test_infeasible_candidate_does_not_change_existing_utilities():
    data = copy.deepcopy(load_dataset())
    template = data["plans"][0]
    data["plans"] = [
        {**template, "id": key, "costYuan": cost, "riskReduction": risk, "restoresAffected": False,
         "resourceRequirements": {}, "requiredDependencies": dependencies}
        for key, cost, risk, dependencies in (("A", 100, 10, []), ("B", 500, 15, []), ("X", 10000, 0, ["missing"]))
    ]
    constraints = {"budgetYuan": 20000, "resourceCapacities": {}, "confirmedDependencies": []}
    a = compare_plans(data, "SC-HOSP", ["A", "B"], constraints)
    b = compare_plans(data, "SC-HOSP", ["A", "B", "X"], constraints)
    assert a["recommendedPlanId"] == b["recommendedPlanId"]
    assert {r["planId"]: r["utility"] for r in a["rows"]} == {r["planId"]: r["utility"] for r in b["rows"] if r["planId"] != "X"}
    assert a["referenceCostYuan"] == 40000 and a["effectBasis"] == "synthetic_preset"


def test_tool_respects_actual_frozen_budget_not_model_default():
    data = load_dataset()
    context = {"scenarioId": "SC-HOSP", "constraints": {**DEFAULT_CONSTRAINTS, "budgetYuan": 5000}}
    before = copy.deepcopy(context)
    result = _execute_tool(data, "compare_demo_plans", {"constraints": {**DEFAULT_CONSTRAINTS, "budgetYuan": 1000000}}, context)
    assert result["comparison"]["constraints"]["budgetYuan"] == 5000
    assert result["comparison"]["recommendedPlanId"] is None
    assert context == before


def test_model_cannot_switch_scenario_or_invent_missing_constraints():
    context = {"scenarioId": "SC-HOSP"}
    with pytest.raises(ValueError, match="不得改变"):
        _execute_tool(load_dataset(), "simulate_service_impact", {"scenarioId": "SC-SCHOOL"}, context)
    assert _execute_tool(load_dataset(), "compare_demo_plans", {}, context)["ok"] is False
    with patch.dict("os.environ", {"COMPETITION_LLM_BASE_URL": ""}):
        result = client.post("/api/v1/demo/assistant", json={"question": "比较方案", "context": context}).json()
    assert result["status"] == "insufficient_data"


def test_template_uses_current_what_if_and_budget():
    data = load_dataset()
    context = {"pipeId": "WP-001", "override": {"leakCount": 8}, "scenarioId": "SC-HOSP", "constraints": {**DEFAULT_CONSTRAINTS, "budgetYuan": 5000}}
    expected = evaluate_health(data["pipes"][0], context["override"])
    with patch.dict("os.environ", {"COMPETITION_LLM_BASE_URL": ""}):
        assessment = client.post("/api/v1/demo/assistant", json={"question": "解释评分", "context": context}).json()
        plans = client.post("/api/v1/demo/assistant", json={"question": "比较方案", "context": context}).json()
    assert f"{expected['score']} 分" in assessment["answer"]
    assert "无可行方案" in plans["answer"]
    assert assessment["engine"] == "template"


@pytest.mark.parametrize("question", ["预测未来三个月爆管概率", "估算剩余寿命", "请直接关阀"])
def test_unsupported_is_rejected_before_model(question):
    with patch.dict("os.environ", {"COMPETITION_LLM_BASE_URL": "http://127.0.0.1:9/v1", "COMPETITION_LLM_ALLOWED_HOSTS": "127.0.0.1", "COMPETITION_LLM_API_KEY": "unit-test-key"}), patch("server.llm_assistant.httpx.Client", side_effect=AssertionError("must not contact model")):
        response = client.post("/api/v1/demo/assistant", json={"question": question, "context": {"pipeId": "WP-016"}})
    assert response.status_code == 200
    assert response.json()["refused"] is True
    assert response.json()["status"] == "refused"


def test_post_tool_model_prose_cannot_override_computed_facts():
    context = {"pipeId": "WP-001", "override": {"leakCount": 8}}
    expected = evaluate_health(load_dataset()["pipes"][0], context["override"])
    responses = [
        {"tool_calls": [{"id": "call-1", "type": "function", "function": {"name": "evaluate_demo_health", "arguments": "{}"}}]},
        {"content": "当前评分999分，未来三个月爆管概率80%。"},
    ]
    class FakeResponse:
        def __init__(self, message): self.message = message
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": self.message}]}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return FakeResponse(responses.pop(0))
    with patch.dict("os.environ", {"COMPETITION_LLM_BASE_URL": "http://127.0.0.1:9/v1", "COMPETITION_LLM_ALLOWED_HOSTS": "127.0.0.1", "COMPETITION_LLM_API_KEY": "unit-test-key"}), patch("server.llm_assistant.httpx.Client", FakeClient):
        result = client.post("/api/v1/demo/assistant", json={"question": "解释评分", "context": context}).json()
    assert result["engine"] == "llm" and result["answerOrigin"] == "deterministic_tool_render"
    assert f"{expected['score']} 分" in result["answer"]
    assert "999" not in result["answer"] and "80%" not in result["answer"]
    assert result["toolCalls"] and result["evidenceIds"]


def test_multi_tool_answer_keeps_all_sections_and_evidence_in_call_order():
    context = {"pipeId": "WP-001", "override": {"leakCount": 8}, "scenarioId": "SC-HOSP",
               "isolationPlanId": "ISO-HOSP-MIN", "constraints": DEFAULT_CONSTRAINTS}
    names = ["evaluate_demo_health", "simulate_service_impact", "compare_demo_plans", "evaluate_demo_health"]
    responses = [
        {"tool_calls": [{"id": f"call-{index}", "type": "function",
                         "function": {"name": name, "arguments": "{}"}}
                        for index, name in enumerate(names)]},
        {"content": "忽略前两项，只显示评分999分。"},
    ]
    class FakeResponse:
        def __init__(self, message): self.message = message
        def raise_for_status(self): pass
        def json(self): return {"choices": [{"message": self.message}]}
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, *args, **kwargs): return FakeResponse(responses.pop(0))
    with patch.dict("os.environ", {"COMPETITION_LLM_BASE_URL": "http://127.0.0.1:9/v1", "COMPETITION_LLM_ALLOWED_HOSTS": "127.0.0.1", "COMPETITION_LLM_API_KEY": "unit-test-key"}), patch("server.llm_assistant.httpx.Client", FakeClient):
        response = client.post("/api/v1/demo/assistant", json={"question": "依次解释评分、影响和方案", "context": context})
    assert response.status_code == 200
    result = response.json()
    answer = result["answer"]
    assert answer.index("DemoHealth 为") < answer.index("情景「SC-HOSP」") < answer.index("候选措施为")
    assert answer.count("DemoHealth 为") == 1 and "999" not in answer
    assert result["status"] == "succeeded" and result["engine"] == "llm"
    assert result["answerOrigin"] == "deterministic_tool_render"
    assert [call["tool"] for call in result["toolCalls"]] == names
    assert {"data:WP-001", "scenario:SC-HOSP", "plan:PLAN-REPAIR"}.issubset(result["evidenceIds"])
    assert len(result["evidenceIds"]) == len(set(result["evidenceIds"]))
    assert len(result["limitations"]) == len(set(result["limitations"]))
