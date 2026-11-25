"""LangGraph主图回归：四路、澄清、审批、证据与越权。

使用FakeLLM/FakeEmbedding明确替代未部署模型；这些用例不构成PPU验收。
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
import pytest

from src.adapters.scoring import provided_sample
from src.agents.graph import build_graph
from src.agents.providers.llm import FakeLLM
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument
from src.agents.rag.retriever import HybridRetriever
from src.agents.state import AgentSettings
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.schemas import DecisionRequest, RecomputeRequest, ScenarioRequest, SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]


def make_batch():
    static = {"管线编号": "syn-001", "材质": "PE", "投运年份": 2005, "管径": 300,
              "起点埋深": 1, "终点埋深": 1, "压力": "低压"}
    dynamic = {"管线编号": "syn-001", "动态压力": .3, "余氯": .4, "浊度": .2,
               "瞬时流量": 1, "风险点数量": 0}
    return RecomputeRequest(as_of="2026-01-02T00:00:00+08:00", window_start="2026-01-01T00:00:00+08:00",
                            window_end="2026-01-02T00:00:00+08:00", static_rows=[static],
                            dynamic_rows=[{**dynamic, "时间": f"2026-01-01T0{i}:00:00+08:00"} for i in range(2)],
                            data_mode="synthetic", source_refs=["synthetic:图回归"])


def sample_snapshot():
    data = provided_sample()
    return {"snapshot_id": "snap-500", "payload": data.model_dump(mode="json"), "as_of": data.as_of.isoformat(),
            "data_mode": data.data_mode, "source_status": data.source_status, "sha256": "fixture"}


def s01_snapshot():
    data = SnapshotPayload.model_validate_json((ROOT / "data/synthetic/s01.json").read_text())
    return {"snapshot_id": "snap-s01", "payload": data.model_dump(mode="json"), "as_of": data.as_of.isoformat(),
            "data_mode": "synthetic", "source_status": "constructed", "sha256": "fixture"}


@pytest.fixture
def principal():
    return BatchPrincipal(actor_id="tester", scope="internal")


@pytest.fixture
def retriever():
    doc = KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="V5分工", version="V5",
                            scope="internal", rule_status="draft_reference", sensitivity="internal")
    return HybridRetriever(chunk_document(doc, "# 三方分工\n数字政通负责评价，重庆大学负责预测，重通院负责推演与CUV。"))


def graph(tmp_path, principal, retriever):
    snapshots = {"snap-500": sample_snapshot(), "snap-s01": s01_snapshot()}
    return build_graph(llm=FakeLLM(), retriever=retriever, snapshot_lookup=snapshots.__getitem__,
                       batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver(),
                       settings=AgentSettings(max_tool_calls=8))


def state(principal, **updates):
    value = {"thread_id": "thread-test", "run_id": "run-test", "principal": principal.model_dump(),
             "question": "查询管段评分", "snapshot_id": "snap-500", "batch_refs": [],
             "scenario": None, "decision": None, "status": "running", "phase": "started",
             "tool_results": [], "citations": [], "evidence_refs": [], "missing": [], "errors": [],
             "allowed_tools": []}
    value.update(updates)
    return value


def test_assessment_with_full_batch_uses_real_calculator(tmp_path, principal, retriever):
    ref = InputBatchRepository(tmp_path).register(make_batch(), principal)
    app = graph(tmp_path, principal, retriever)
    result = app.invoke(state(principal, question="查询这个批次的健康评分", batch_refs=[ref.batch_ref]),
                        config={"configurable": {"thread_id": "thread-a"}})
    assert result["status"] == "succeeded"
    assert result["tool_results"][0]["tool_name"] == "calculate_health"
    assert result["tool_results"][0]["result"]["persisted"] is False
    assert result["answer_mode"] == "deterministic_facts"
    assert "tool:" in result["answer"]


def test_sqlite_checkpointer_survives_graph_rebuild(tmp_path, principal, retriever, monkeypatch):
    from src.platform.agent_runtime import build_checkpointer
    monkeypatch.setenv("AGENT_CHECKPOINT", "sqlite")
    monkeypatch.setenv("AGENT_CHECKPOINT_SQLITE", str(tmp_path / "checkpoints.sqlite3"))
    pid = sample_snapshot()["payload"]["assessments"][0]["pipe_id"]
    snapshots = {"snap-500": sample_snapshot(), "snap-s01": s01_snapshot()}
    kwargs = dict(llm=FakeLLM(), retriever=retriever, snapshot_lookup=snapshots.__getitem__,
                  batches=InputBatchRepository(tmp_path), settings=AgentSettings(max_tool_calls=8))
    first_graph = build_graph(checkpointer=build_checkpointer(tmp_path), **kwargs)
    first = first_graph.invoke(state(principal, question=f"查询管段 {pid} 的评分", snapshot_id="snap-500"),
                               config={"configurable": {"thread_id": "thread-restart"}})
    assert first["__interrupt__"][0].value["type"] == "clarification"
    second_graph = build_graph(checkpointer=build_checkpointer(tmp_path), **kwargs)
    resumed = second_graph.invoke(Command(resume={"action": "clarify", "values": {"pipe_id": pid}}),
                                  config={"configurable": {"thread_id": "thread-restart"}})
    assert resumed["status"] == "succeeded"


def test_clarification_interrupt_and_resume(tmp_path, principal, retriever):
    app = graph(tmp_path, principal, retriever)
    pid = sample_snapshot()["payload"]["assessments"][0]["pipe_id"]
    first = app.invoke(state(principal, question=f"查询管段 {pid} 的评分", snapshot_id="snap-500"),
                       config={"configurable": {"thread_id": "thread-b"}})
    interrupt = first["__interrupt__"][0].value
    assert interrupt["type"] == "clarification"
    resumed = app.invoke(Command(resume={"action": "clarify", "values": {"pipe_id": pid}}),
                         config={"configurable": {"thread_id": "thread-b"}})
    assert resumed["status"] == "succeeded"
    assert resumed["tool_results"][0]["tool_name"] == "query_assessment"


def test_combined_simulation_and_decision_requires_review(tmp_path, principal, retriever):
    snap = s01_snapshot()
    scenario = ScenarioRequest(snapshot_id="snap-s01", as_of=snap["as_of"], fault_edge_id="syn-fault",
                               isolation_plan_id="syn-isolate")
    decision = DecisionRequest(**scenario.model_dump(), plan_ids=["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                               budget_fen=400000, resource_capacities={"repair_team": 1, "water_truck": 1})
    app = graph(tmp_path, principal, retriever)
    first = app.invoke(state(principal, question="分析故障影响并比较方案", snapshot_id="snap-s01",
                             scenario=scenario.model_dump(mode="json"), decision=decision.model_dump(mode="json")),
                       config={"configurable": {"thread_id": "thread-c"}})
    assert first["tool_results"][0]["tool_name"] == "simulate_outage"
    assert first["tool_results"][1]["tool_name"] == "compare_action_plans"
    interrupt = first["__interrupt__"][0].value
    assert interrupt["type"] == "approval"
    resumed = app.invoke(Command(resume={"action": "accept", "reason": "人工复核通过"}),
                         config={"configurable": {"thread_id": "thread-c"}})
    assert resumed["status"] == "succeeded"
    assert resumed["approval_result"]["action"] == "accept"
    assert resumed["tool_results"][0]["execution_status"] == "not_dispatched"


def test_revise_pauses_without_reusing_old_result(tmp_path, principal, retriever):
    snap = s01_snapshot()
    scenario = ScenarioRequest(snapshot_id="snap-s01", as_of=snap["as_of"], fault_edge_id="syn-fault",
                               isolation_plan_id="syn-isolate")
    decision = DecisionRequest(**scenario.model_dump(), plan_ids=["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                               budget_fen=400000, resource_capacities={"repair_team": 1, "water_truck": 1})
    app = graph(tmp_path, principal, retriever)
    app.invoke(state(principal, question="比较方案", snapshot_id="snap-s01",
                     scenario=scenario.model_dump(mode="json"), decision=decision.model_dump(mode="json")),
               config={"configurable": {"thread_id": "thread-revise"}})
    resumed = app.invoke(Command(resume={"action": "revise", "reason": "预算改为0"}),
                         config={"configurable": {"thread_id": "thread-revise"}})
    assert resumed["status"] == "paused"
    assert resumed["phase"] == "revision_required"
    assert "重新提交" in resumed["answer"]


def test_materials_route_returns_citation_and_blocks_control(tmp_path, principal, retriever):
    app = graph(tmp_path, principal, retriever)
    result = app.invoke(state(principal, question="三方分工", snapshot_id=None),
                        config={"configurable": {"thread_id": "thread-d"}})
    assert result["status"] == "succeeded"
    assert result["citations"][0]["document_id"] == "D1"
    assert result["tool_results"][0]["tool_name"] == "knowledge_retrieval"
    blocked = app.invoke(state(principal, question="直接关阀并下发工单", snapshot_id=None),
                         config={"configurable": {"thread_id": "thread-e"}})
    assert blocked["status"] == "insufficient_data"
    assert "unsupported" in blocked["phase"]


def test_missing_context_never_fabricates(tmp_path, principal, retriever):
    app = graph(tmp_path, principal, retriever)
    result = app.invoke(state(principal, question="比较方案", snapshot_id="snap-s01", scenario=None, decision=None),
                        config={"configurable": {"thread_id": "thread-f"}})
    assert result["__interrupt__"][0].value["type"] == "clarification"
    resumed = app.invoke(Command(resume={"action": "cancel"}), config={"configurable": {"thread_id": "thread-f"}})
    assert resumed["status"] == "cancelled"


def test_new_run_never_reuses_previous_answer_or_clarification(tmp_path, principal, retriever):
    class ToggleRetriever:
        enabled = True
        def search(self, *args, **kwargs):
            return retriever.search(*args, **kwargs) if self.enabled else ([], "bm25")
    source = ToggleRetriever()
    app = build_graph(llm=FakeLLM(), retriever=source, snapshot_lookup=lambda _: s01_snapshot(),
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "same-thread-new-run"}}
    first = app.invoke(state(principal, question="三方分工", snapshot_id=None), config=config)
    source.enabled = False
    second = app.invoke(state(principal, run_id="second", question="V5接口依据", snapshot_id=None), config=config)
    assert second["status"] == "insufficient_data"
    assert second["answer"] != first["answer"]
    assert "V5接口依据" in second["answer"]
    assert "没有检索到可引用资料" in second["answer"]
    assert second["clarification"] == {}
    assert second["approval_result"] == {}


def test_clarification_adds_scenario_and_rebuilds_grant(tmp_path, principal, retriever):
    app = graph(tmp_path, principal, retriever)
    config = {"configurable": {"thread_id": "add-scenario"}}
    first = app.invoke(state(principal, question="分析故障影响", snapshot_id="snap-s01"), config=config)
    assert first["__interrupt__"]
    scenario = ScenarioRequest(snapshot_id="snap-s01", as_of=s01_snapshot()["as_of"],
                               fault_edge_id="syn-fault", isolation_plan_id="syn-isolate")
    result = app.invoke(Command(resume={"action": "clarify", "values": {"scenario": scenario.model_dump(mode="json")}}), config=config)
    assert result["status"] == "succeeded"
    assert result["allowed_tools"] == ["simulate_outage"]
    assert result["tool_results"][0]["result"]["affected_service_ids"] == [
        "syn-hospital-001", "syn-households-C", "syn-households-U"]


def test_clarification_pipe_rebuilds_grant_and_denies_unknown_fields(tmp_path, principal, retriever):
    app = graph(tmp_path, principal, retriever)
    config = {"configurable": {"thread_id": "clarify-pipe-grant"}}
    first = app.invoke(state(principal, question="你好"), config=config)
    pid = sample_snapshot()["payload"]["assessments"][0]["pipe_id"]
    refused = app.invoke(Command(resume={"action": "clarify", "values": {"pipe_id": pid, "allowed_tools": ["run_shell"]}}), config=config)
    assert refused.get("__interrupt__")
    assert refused["allowed_tools"] == []
    assert refused["tool_results"] == []
    result = app.invoke(Command(resume={"action": "clarify", "values": {"pipe_id": pid}}), config=config)
    assert result["status"] == "succeeded"
    assert result["tool_results"][0]["tool_name"] == "query_assessment"


def test_domain_numbers_bypass_model_prose(tmp_path, principal, retriever):
    class WrongAnswer(FakeLLM):
        def answer(self, *args, **kwargs):
            raise AssertionError("Domain metrics must never be rewritten by the model")
    snap = sample_snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    app = build_graph(llm=WrongAnswer(), retriever=retriever, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    result = app.invoke(state(principal, pipe_id=pid), config={"configurable": {"thread_id": "bound-domain"}})
    assert result["status"] == "succeeded"
    assert result["answer_mode"] == "deterministic_facts"
    assert "管段" + pid in result["answer"]
    assert str(snap["payload"]["assessments"][0]["legacy_result"]["Hs"]) in result["answer"]
    assert result["verification_status"] == "deterministic_bound"
    assert result["verified_facts"]
    assert all(item["verification_status"] == "verified" for item in result["verified_facts"])
