"""Complete input survives HTTP, template and model-tool boundaries; no online model."""
from unittest.mock import patch
from fastapi.testclient import TestClient
from server.app import app
from server.contracts import frozen_context
from server.demo_engine import load_dataset, template_assistant
from server.llm_assistant import _execute_tool
from server.timeline import build_timeline

REQUEST = {"scenarioId": "SC-HOSP", "isolationPlanId": "ISO-HOSP-MIN", "planId": "PLAN-REPAIR",
           "targetServiceIds": ["SV-HOSP"], "horizonHours": 8,
           "actionSchedule": [{"actionId": "PLAN-REPAIR-action", "startOffsetHours": 1, "durationHours": 8}]}

def test_complete_schedule_crosses_all_assistant_boundaries():
    data = load_dataset()
    context = frozen_context(REQUEST)
    assert context["actionSchedule"][0]["durationHours"] == 8
    assert build_timeline(data, REQUEST)["planCompletion"]["valueHours"] == 9
    tool = _execute_tool(data, "analyze_plan_timeline", {}, context)
    assert tool["timeline"]["planCompletion"]["valueHours"] == 9
    assert "9.0小时" in template_assistant(data, "恢复时间", context)["answer"]
    with patch("server.app.run_llm_assistant", return_value=None):
        response = TestClient(app).post("/api/v1/demo/assistant", json={"question": "恢复时间", "context": REQUEST})
    assert response.status_code == 200
    assert "9.0小时" in response.json()["answer"]

def test_client_result_is_not_trusted_and_schedule_overflow_is_rejected():
    context = {**REQUEST, "timeline": {"planCompletion": {"valueHours": 777}}}
    assert "timeline" not in frozen_context(context)
    response = TestClient(app).post("/api/v1/demo/assistant", json={"question":"恢复时间", "context": {
        **REQUEST, "actionSchedule": [{"actionId":"PLAN-REPAIR-action", "startOffsetHours":1e308,"durationHours":1e308}]}})
    assert response.status_code == 422
