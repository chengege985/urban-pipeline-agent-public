"""Regression cases for Nov 1 independent review; no real LLM or runtime DB."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from src.agents.router import _restrict
from src.agents.state import RouteDecision
from src.agents.providers.llm import FakeLLM, LangChainLLM
from src.agents.graph import build_graph
from src.agents.evidence import validate_answer
from src.agents.outbound import RequestClassification
from src.platform.input_batches import InputBatchRepository
from src.adapters.scoring import provided_sample
from src.decision.timeline import build_plan_service_timeline

ROOT = Path(__file__).resolve().parents[1]

def route(pid):
    return RouteDecision(intent="assessment", tasks=["assessment"], confidence=1, reason="fixture", pipe_id=pid)

@pytest.mark.parametrize("question,candidate,confirmed", [
    ("查询A评分", "B", None), ("查询P-10评分", "P-1", None),
    ("查询A评分", "A", "B"), ("比较A与B", "B", None),
])
def test_object_mismatch_clarifies(question, candidate, confirmed):
    out = _restrict(route(candidate), ("assessment",), ["A", "B", "P-1", "P-10"],
                    question=question, confirmed_pipe_id=confirmed)
    assert out.intent == "clarify" and out.tasks == []

@pytest.fixture(scope="module")
def snapshot():
    data = provided_sample().model_dump(mode="json")
    data["assessments"] = data["assessments"][:2]
    for item, pid in zip(data["assessments"], ["syn-review-A", "syn-review-B"]):
        item["pipe_id"] = pid
    return {"snapshot_id": "review", "payload": data}

def test_graph_rejects_wrong_object_and_accepts_explicit_clarification(tmp_path, snapshot):
    class Wrong(FakeLLM):
        def route(self, *a, **kw): return route("syn-review-B")
    app = build_graph(llm=Wrong(), retriever=None, snapshot_lookup=lambda _: snapshot,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    state = {"thread_id":"review", "run_id":"r", "principal":{"actor_id":"tester", "scope":"internal"},
             "question":"查询syn-review-A评分", "snapshot_id":"review", "batch_refs":[]}
    cfg = {"configurable":{"thread_id":"review"}}
    out = app.invoke(state, config=cfg)
    assert out.get("__interrupt__") and out["tool_results"] == []
    out = app.invoke(Command(resume={"action":"clarify", "values":{"pipe_id":"syn-review-B"}}), config=cfg)
    assert out["tool_results"][0]["result"]["assessment"]["pipe_id"] == "syn-review-B"
    # New question does not inherit last round's object confirmation.
    out = app.invoke({**state, "run_id":"r2"}, config=cfg)
    assert out.get("__interrupt__") and out["tool_results"] == []

class Spy:
    def __init__(self): self.calls = []
    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content="资料仅供分析。[doc:fixture]")

def online():
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode = "openai"
    llm.service_id = "test-llm"
    llm.model = Spy()
    return llm

def classification(mode="synthetic", sensitivity="synthetic", purpose="answer"):
    return RequestClassification(
        principal_id="tester", principal_scope="internal", data_mode=mode,
        question_sensitivity="synthetic", fact_sensitivities=(),
        citation_sensitivities=(sensitivity if sensitivity is not None else "unknown",),
        purpose=purpose, model_service="test-llm", source="test")

@pytest.mark.parametrize("mode,sensitivity", [("real","public"), ("unknown","public"), ("synthetic","internal"), ("synthetic",None)])
def test_answer_exit_denies_unapproved_context(monkeypatch, mode, sensitivity):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    llm = online()
    with pytest.raises(PermissionError):
        llm.answer("资料", [], [{"evidence_id":"doc:fixture", "sensitivity":sensitivity}], [],
                   data_mode=mode, classification=classification(mode, sensitivity))
    assert not llm.model.calls

def test_answer_exit_allows_explicit_synthetic_citations(monkeypatch):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    llm = online()
    llm.answer("资料", [], [{"evidence_id":"doc:fixture", "sensitivity":"synthetic", "title":"fixture", "location":"p1", "snippet":"合成"}], [],
               data_mode="synthetic", classification=classification())
    assert len(llm.model.calls) == 1

@pytest.mark.parametrize("answer,ok", [("已关阀并派单。[doc:fixture]",False),
    ("系统不得声称已关阀。[doc:fixture]",True), ("阀门已关闭。[doc:fixture]",False), ("已经为您下发工单。[doc:fixture]",False), ("不得声称已关阀且系统已派单。[doc:fixture]",False), ("已成功派单。[doc:fixture]",False), ("不得声称已关阀，但是已经派单。[doc:fixture]",False)])
def test_false_execution_claims(answer, ok):
    assert validate_answer(answer, [], [{"evidence_id":"doc:fixture", "snippet":"系统只提供分析"}])[0] is ok

def case():
    payload = json.loads((ROOT / "data/synthetic/s01.json").read_text())
    snap = {"snapshot_id":"review-window", "payload":payload}
    req = {"snapshot_id":snap["snapshot_id"], "as_of":payload["as_of"], "fault_edge_id":"syn-fault", "isolation_plan_id":"syn-isolate", "horizon_hours":8}
    a = {"action_id":"syn-late", "label":"late", "fault_edge_ids":["syn-fault"], "effective_after_hours":9,
         "evidence_ref":None, "restore_edge_ids":["syn-fault"]}
    payload["actions"].append(a)
    next(p for p in payload["plans"] if p["plan_id"] == "syn-plan-combined")["action_ids"].append(a["action_id"])
    return snap, req, a

def run(snap, req):
    return build_plan_service_timeline(snap, req, "syn-plan-combined", target_service_ids=["syn-hospital-001"])

def test_late_missing_effect_does_not_erase_in_window_time():
    snap, req, action = case()
    out = run(snap, req)
    assert out["timeline_status"] == "succeeded"
    assert out["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 6
    assert out["recovery_times"]["plan_completion_hours"]["status"] == "insufficient_data"
    assert out["beyond_horizon_action_ids"] == ["syn-late"] and out["deferred_validation_issues"]
    assert out["plan_feasibility_checked"] is False

@pytest.mark.parametrize("at", [None, 8])
def test_unknown_or_in_window_time_still_requires_evidence(at):
    snap, req, action = case(); action["effective_after_hours"] = at
    assert run(snap, req)["status"] == "insufficient_data"

def test_late_global_conflict_still_blocks():
    snap, req, action = case(); action["conflicts_with"] = ["syn-repair"]
    assert run(snap, req)["status"] == "infeasible"


def test_late_unknown_operability_remains_deferred():
    snap, req, action = case()
    action["open_valve_ids"] = ["syn-alternative"]
    edge = next(e for e in snap["payload"]["edges"] if e["edge_id"] == "syn-alternative")
    edge["operable"] = None
    out = run(snap, req)
    assert out["timeline_status"] == "succeeded"
    assert out["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 6
    assert any("operability" in x for x in out["deferred_validation_issues"])
    edge["operable"] = False
    assert run(snap, req)["status"] == "infeasible"


def test_real_provider_route_honors_clarification(tmp_path, snapshot, monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    class Model:
        def with_structured_output(self, schema): return self
        def invoke(self, messages): return route("syn-review-B")
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode = "openai"; llm.service_id = "test-llm"; llm.model = Model()
    snap = deepcopy(snapshot); snap["payload"]["data_mode"] = "synthetic"
    snap["payload"]["source_status"] = "constructed"
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    cfg = {"configurable":{"thread_id":"provider-clarify"}}
    state = {"thread_id":"provider-clarify", "run_id":"r", "principal":{"actor_id":"tester", "scope":"internal"},
             "question":"帮我看看这个", "question_sensitivity":"synthetic",
             "snapshot_id":"review", "batch_refs":[]}
    assert app.invoke(state, config=cfg).get("__interrupt__")
    out = app.invoke(Command(resume={"action":"clarify", "values":{"pipe_id":"syn-review-B"}}), config=cfg)
    assert out["status"] == "succeeded"
    assert out["tool_results"][0]["result"]["assessment"]["pipe_id"] == "syn-review-B"
