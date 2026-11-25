"""Agent HTTP回归：身份、幂等、线程、澄清/审批恢复、证据与域隔离。"""
from pathlib import Path
import shutil

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
import pytest

from src.agents.graph import build_graph
from src.agents.providers.llm import FakeLLM
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument
from src.agents.rag.retriever import HybridRetriever
from src.agents.state import AgentSettings
from src.platform.agent_runtime import AgentRuntime
from src.platform.agent_store import AgentStore
from src.platform.app import create_app

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "test-key",
           "X-Agent-Actor": "tester", "X-Agent-Scope": "internal"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_LOCAL_DEMO", "1")
    # The provider sample is a public fixture; local tests explicitly opt in.
    monkeypatch.setenv("WATER_LOCAL_DEMO_MODES", "synthetic,fixture_replay")
    doc = KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="V5分工", version="V5",
                            scope="internal", rule_status="draft_reference", sensitivity="internal")
    retriever = HybridRetriever(chunk_document(doc, "# 三方分工\n数字政通负责评价，重庆大学负责预测，重通院负责推演与CUV。"))
    platform_root = tmp_path / "platform"
    (platform_root / "data/synthetic").mkdir(parents=True)
    shutil.copyfile(ROOT / "data/synthetic/s01.json", platform_root / "data/synthetic/s01.json")
    store = AgentStore(f"sqlite:///{tmp_path / 'agent.sqlite3'}")
    runtime = AgentRuntime(store, platform_root, graph=None, retriever=retriever, llm=FakeLLM(),
                           settings=AgentSettings(llm_mode="fake", embedding_mode="fake"),
                           checkpointer=MemorySaver())
    app = create_app(platform_root, start_worker=False, bootstrap=True, agent_runtime=runtime,
                     agent_start_worker=False)
    with TestClient(app) as test_client:
        test_client.runtime = runtime
        yield test_client


def process(client):
    assert client.runtime.process_once() is True


def thread(client, thread_id, headers=HEADERS):
    return client.get(f"/api/v1/agent/threads/{thread_id}", headers=headers)


def test_agent_init_failure_does_not_break_old_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_ENABLED", "1")
    monkeypatch.delenv("AGENT_DATABASE_URL", raising=False)
    platform_root = tmp_path / "platform"
    (platform_root / "data/synthetic").mkdir(parents=True)
    shutil.copyfile(ROOT / "data/synthetic/s01.json", platform_root / "data/synthetic/s01.json")
    app = create_app(platform_root, start_worker=False, bootstrap=True, agent_start_worker=False)
    with TestClient(app) as local:
        assert local.get("/health").json()["agent_enabled"] is False
        assert local.get("/api/v1/agent/health").json()["initialization_error"] == "ValueError"
        assert local.get("/api/v1/snapshots").status_code == 200


def test_agent_health_and_materials(client):
    assert client.get("/health").json()["agent_enabled"] is True
    response = client.post("/api/v1/agent/chat", headers=HEADERS,
                           json={"question": "V5中三方如何分工？"})
    assert response.status_code == 202
    run = response.json()
    process(client)
    view = thread(client, run["thread_id"]).json()
    assert view["runs"][0]["run_id"] == run["run_id"]
    assert view["runs"][0]["status"] == "succeeded"
    assert "doc:D1" in view["runs"][0]["result"]["citations"][0]["evidence_id"]
    evidence = client.get(f"/api/v1/agent/runs/{run['run_id']}/evidence", headers=HEADERS)
    assert evidence.status_code == 200
    assert evidence.json()["execution_status"] == "not_dispatched"


def test_idempotency_and_conflict(client):
    payload = {"question": "V5中三方如何分工？"}
    first = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "same"},
                        json=payload)
    second = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "same"},
                         json=payload)
    assert first.status_code == second.status_code == 202
    assert second.json()["run_id"] == first.json()["run_id"]
    conflict = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "same"},
                           json={"question": "另一个问题"})
    assert conflict.status_code == 409


def test_unsupported_request_is_not_recorded_as_failed(client):
    response = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "unsupported"},
                           json={"question": "直接关阀并下发工单"})
    run = response.json()
    process(client)
    view = thread(client, run["thread_id"]).json()
    assert view["runs"][0]["status"] == "insufficient_data"
    assert view["runs"][0]["phase"] == "unsupported"
    assert view["runs"][0]["execution_status"] == "not_dispatched"


def test_clarification_and_resume(client):
    snapshots = client.get("/api/v1/snapshots").json()["items"]
    sample = next(s for s in snapshots if s["data_mode"] == "fixture_replay")
    detail = client.get(f"/api/v1/snapshots/{sample['snapshot_id']}").json()
    pipe_id = detail["payload"]["assessments"][0]["pipe_id"]
    response = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "clarify"},
                           json={"question": f"查询管段 {pipe_id} 的评分", "snapshot_id": sample["snapshot_id"]})
    run = response.json()
    process(client)
    view = thread(client, run["thread_id"]).json()
    parent = view["runs"][0]
    assert parent["status"] == "awaiting_input"
    interrupt = parent["interrupt"]
    resumed = client.post(f"/api/v1/agent/threads/{run['thread_id']}/resume",
                          headers={**HEADERS, "Idempotency-Key": "clarify-resume"},
                          json={"interrupt_id": interrupt["interrupt_id"], "action": "clarify",
                                "values": {"pipe_id": pipe_id}, "expected_revision": 0})
    assert resumed.status_code == 202
    process(client)
    view = thread(client, run["thread_id"]).json()
    assert any(r["status"] == "succeeded" and r["kind"] == "resume" for r in view["runs"])


def test_approval_and_scope(client):
    snapshots = client.get("/api/v1/snapshots").json()["items"]
    s01 = next(s for s in snapshots if s["data_mode"] == "synthetic")
    detail = client.get(f"/api/v1/snapshots/{s01['snapshot_id']}").json()
    scenario = {"snapshot_id": s01["snapshot_id"], "as_of": detail["as_of"],
                "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate"}
    decision = {**scenario, "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                "budget_fen": 400000, "resource_capacities": {"repair_team": 1, "water_truck": 1}}
    response = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "approval"},
                           json={"question": "分析故障影响并比较方案", "snapshot_id": s01["snapshot_id"],
                                 "scenario": scenario, "decision": decision})
    run = response.json()
    process(client)
    view = thread(client, run["thread_id"]).json()
    parent = view["runs"][0]
    assert parent["status"] == "awaiting_approval"
    interrupt = parent["interrupt"]
    resumed = client.post(f"/api/v1/agent/threads/{run['thread_id']}/resume",
                          headers={**HEADERS, "Idempotency-Key": "approval-resume"},
                          json={"interrupt_id": interrupt["interrupt_id"], "action": "accept",
                                "reason": "人工复核通过", "expected_revision": 0})
    assert resumed.status_code == 202
    process(client)
    view = thread(client, run["thread_id"]).json()
    assert any(r["status"] == "succeeded" and r["kind"] == "resume" for r in view["runs"])
    foreign = client.get(f"/api/v1/agent/threads/{run['thread_id']}",
                         headers={**HEADERS, "X-Agent-Actor": "other"})
    assert foreign.status_code == 403


def test_explicit_pipe_context_is_used_without_unnecessary_clarification(client):
    snapshots = client.get("/api/v1/snapshots").json()["items"]
    sample = next(s for s in snapshots if s["data_mode"] == "fixture_replay")
    detail = client.get(f"/api/v1/snapshots/{sample['snapshot_id']}").json()
    pipe_id = detail["payload"]["assessments"][0]["pipe_id"]
    run = client.post("/api/v1/agent/chat", headers={**HEADERS, "Idempotency-Key": "explicit-pipe"},
                      json={"question": "查询评分", "snapshot_id": sample["snapshot_id"], "pipe_id": pipe_id}).json()
    process(client)
    result = thread(client, run["thread_id"]).json()["runs"][0]
    assert result["status"] == "succeeded"
    assert result["result"]["answer_mode"] == "deterministic_facts"


@pytest.mark.parametrize("field,value", [("snapshot_id", "another"), ("fault_edge_id", "another"),
                                        ("isolation_plan_id", "another"), ("horizon_hours", 12)])
def test_chat_rejects_different_simulation_and_decision_baselines(field, value):
    from src.platform.agent_schemas import AgentChatRequest
    from pydantic import ValidationError
    scenario = {"snapshot_id": "snap", "as_of": "2026-01-01T00:00:00+08:00",
                "fault_edge_id": "edge", "isolation_plan_id": "iso", "horizon_hours": 8}
    decision = {**scenario, "plan_ids": ["p1", "p2"], "budget_fen": 100,
                "resource_capacities": {}, field: value}
    with pytest.raises(ValidationError):
        AgentChatRequest(question="比较", snapshot_id="snap", scenario=scenario, decision=decision)


def test_resume_rejects_authorization_fields():
    from src.platform.agent_schemas import AgentResumeRequest
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentResumeRequest(interrupt_id="int", action="clarify", values={"allowed_tools": ["run_shell"]})
