"""Exercise the one-click demo workflow using original synthetic evidence identities."""

import json

import pytest
from fastapi.testclient import TestClient

from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.standalone import create_gas_app

GAS = "/api/v1/gas/engineering"
MONITORING = f"{GAS}/monitoring"
OBSERVATION = f"{MONITORING}/observation"
COLLABORATION = f"{GAS}/cuv-replay/collaboration"
AGENT = f"{GAS}/agent/query"
FAULT = "syn-gas-event-fault"
DISPOSITION = f"{GAS}/events/{FAULT}/disposition"
PIPE = "syn-gas-pipe-K-001"
TRIGGER = "syn-gas-replay-pressure-k001-m12"
READER = {"X-Water-Token": "synthetic-reader"}
OPERATOR = {"X-Water-Token": "synthetic-operator"}
OTHER_DATASET = {"X-Water-Token": "other-dataset"}


@pytest.fixture
def preview(monkeypatch):
    for name in ("WATER_API_TOKEN", "WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "GAS_CUV_COLLABORATION_AUDIT_DB"):
        monkeypatch.delenv(name, raising=False)
    common = {"modes": ["synthetic"], "scope": "internal", "datasets": [GAS_DATASET_ID]}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "synthetic-reader": {**common, "actor_id": "demo-reader"},
        "synthetic-operator": {**common, "actor_id": "demo-operator",
                               "permissions": ["gas:disposition", "gas:cuv:collaborate"]},
        "other-dataset": {**common, "actor_id": "unassigned", "datasets": []},
    }))
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    monkeypatch.setenv("GAS_DISPOSITION_DEMO", "1")
    monkeypatch.setenv("GAS_CUV_COLLABORATION_DEMO", "1")
    with TestClient(create_gas_app(model_starting=lambda: True), client=("127.0.0.1", 50123)) as client:
        report = client.get(MONITORING, headers=READER).json()
        yield client, {"observation_id": TRIGGER, "manifest_sha256": report["manifest_sha256"]}


def test_selected_observation_has_original_row_and_exact_case_clock(preview):
    client, ref = preview
    assert client.get(OBSERVATION, params=ref).status_code == 401
    assert client.get(OBSERVATION, params=ref, headers=OTHER_DATASET).status_code == 403
    linked = client.get(OBSERVATION, params=ref, headers=READER)
    assert linked.status_code == 200
    assert linked.headers["Cache-Control"] == "no-store"
    body = linked.json()
    assert body["reference"] == ref
    assert body["observation"]["observed_at"] == body["event"]["replay_at"] == "2026-11-18T01:12:00Z"
    assert body["observation"]["value"] == 6.9
    assert body["observation"]["unit"] == "kPa"
    assert body["upper"] == 6
    assert body["event"]["event_id"] == FAULT
    assert body["asset_id"] == PIPE
    unrelated = client.get(OBSERVATION, headers=READER, params={
        **ref, "observation_id": "syn-gas-replay-pressure-k001-m03",
    }).json()
    assert unrelated["event"] is None
    for changed, status in (("observation_id", 404), ("manifest_sha256", 409)):
        response = client.get(OBSERVATION, headers=READER, params={**ref, changed: "0" * 64})
        assert response.status_code == status


def test_agent_draft_and_confirmed_comparison_keep_exact_version_and_audit_links(preview):
    client, ref = preview
    health = client.get(f"{GAS}/agent/health", headers=READER).json()
    assert health["model_status"] == "starting" and health["tools_status"] == "ready"
    scope = {"monitoring_ref": ref, "event_id": FAULT, "asset_id": PIPE}
    question = {**scope, "question": "查询当前选中异常"}
    result = client.post(AGENT, headers=READER, json=question).json()
    assert result["parser_source"] == "local_rule_not_llm"
    assert result["intent"] == "monitoring_query"
    assert result["tools"][0]["data"]["monitoring_context"]["reference"] == ref
    conflict = client.post(AGENT, headers=READER, json={**question, "asset_id": "syn-gas-pipe-K-003"})
    assert conflict.status_code == 409
    related = client.post(AGENT, headers=READER, json={**scope, "question": "关联管段有哪些？"}).json()
    assert related["tools"][0]["data"]["related_pipe_ids"] == [PIPE]
    assert related["tools"][0]["data"]["monitoring_context"]["reference"] == ref

    base = client.get(COLLABORATION, headers=OPERATOR).json()
    criteria = {"objective": "min_topology_restoration_hours", "max_peak_affected_services": 10, "restore_by_hours": 4.5}
    draft = {"criteria": criteria, "expected_revision": 0, "input_sha256": base["input_sha256"]}
    compare = {**scope, "question": "比较 CUV 两案", "tool_hint": "cuv.query"}
    assert client.post(AGENT, headers=READER, json={**compare, "cuv_draft": draft}).status_code == 403
    proposal = client.post(AGENT, headers=OPERATOR, json={**compare, "cuv_draft": draft}).json()["tools"][0]["data"]
    change = proposal["comparison_change"]
    assert change["status"] == "preview" and change["confirmed_revision"] is None
    assert change["before"]["revision"] == 0
    assert change["before"]["comparison"]["leading_plan_ids"] == ["syn-gas-cuv-plan-staged-release"]
    assert change["after_comparison"]["leading_plan_ids"] == ["syn-gas-cuv-plan-hold-isolation"]
    assert client.get(COLLABORATION, headers=OPERATOR).json()["audit"] == []

    confirmation = {**draft, "monitoring_ref": ref, "confirmed": True, "feedback": "合成验收"}
    assert client.post(COLLABORATION, headers=READER, json=confirmation).status_code == 403
    saved = client.post(COLLABORATION, headers=OPERATOR, json=confirmation).json()
    assert saved["audit"][0]["actor_id"] == "demo-operator"
    assert saved["audit"][0]["monitoring_ref"] == ref
    assert client.post(AGENT, headers=OPERATOR, json={**compare, "cuv_draft": draft}).status_code == 409
    current = client.post(AGENT, headers=OPERATOR, json=compare).json()["tools"][0]["data"]
    assert current["confirmed_comparison"]["revision"] == 1
    assert current["comparison_change"]["confirmed_revision"] == 1
    assert current["comparison_change"]["before"]["revision"] == 0
    assert "合成验收" not in json.dumps(current, ensure_ascii=False)
    public = client.post(AGENT, headers=READER, json=compare).json()["tools"][0]["data"]
    assert public["confirmed_comparison"] is public["comparison_change"] is None
    assert all(item["value"] is None for item in current["unknown_metrics"])

    assert client.post(AGENT, headers=OPERATOR, json={
        **scope, "question": "执行事件处置并关闭阀门",
    }).json()["tools"] == []
    assert client.get(DISPOSITION, headers=OPERATOR).json()["revision"] == 0
    ack = {"monitoring_ref": ref, "expected_revision": 0, "target_status": "acknowledged", "reason": "合成回执"}
    assert client.post(DISPOSITION, headers=READER, json=ack).status_code == 403
    wrong = f"{GAS}/events/syn-gas-event-isolation/disposition"
    assert client.post(wrong, headers=OPERATOR, json=ack).status_code == 409
    receipt = client.post(DISPOSITION, headers=OPERATOR, json=ack).json()["audit"][0]
    assert receipt["event_id"] == FAULT
    assert receipt["from_status"] == "pending" and receipt["to_status"] == "acknowledged"
    assert receipt["actor_id"] == "demo-operator" and receipt["occurred_at"]
    assert receipt["revision"] == 1 and receipt["monitoring_ref"] == ref
