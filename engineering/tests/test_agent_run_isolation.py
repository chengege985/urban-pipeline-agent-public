"""AGENT04-R01/R06: new runs must not inherit authorization or verified facts."""
from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from src.agents.graph import build_graph, request_fingerprint
from src.agents.providers.llm import LangChainLLM
from src.agents.state import RouteDecision
from src.platform.input_batches import InputBatchRepository
from src.adapters.scoring import provided_sample
from src.platform.schemas import SnapshotPayload


ROOT = Path(__file__).resolve().parents[1]


class RouteModel:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return RouteDecision(intent="assessment", tasks=["assessment"], confidence=1, reason="spy")


def snapshot():
    sample = provided_sample()
    data = sample.model_dump(mode="json")
    data["data_mode"] = "synthetic"
    data["source_status"] = "constructed"
    for index, assessment in enumerate(data["assessments"]):
        assessment["pipe_id"] = f"syn-run-{index:03d}"
    return {"snapshot_id": "run-isolation", "payload": data,
            "as_of": sample.as_of.isoformat(), "data_mode": "synthetic",
            "source_status": "constructed", "sha256": "fixture"}


def real_llm():
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode = "openai"
    llm.service_id = "test-llm"
    llm.endpoint = "http://127.0.0.1:30000/v1"
    llm.model = RouteModel()
    return llm


def run_state(pipe_id, *, run_id, question, sensitivity=None):
    value = {"thread_id": "cross-run", "run_id": run_id,
             "principal": {"actor_id": "tester", "scope": "internal"},
             "question": question, "snapshot_id": "run-isolation", "pipe_id": pipe_id,
             "batch_refs": [], "scenario": None, "decision": None}
    if sensitivity is not None:
        value["question_sensitivity"] = sensitivity
    return value


def test_new_run_without_classification_calls_no_model(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "cross-run"}}
    app.invoke(run_state(pid, run_id="r1", question="查询评分", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    assert before == 1
    app.invoke(run_state(pid, run_id="r2", question="查询评分"), config=config)
    assert len(llm.model.calls) == before


def test_new_run_clears_verified_facts_after_success(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "cross-run-facts"}}
    first = app.invoke(run_state(pid, run_id="r1", question="查询评分", sensitivity="synthetic"),
                       config=config)
    assert first["verified_facts"] and first["verification_status"] == "deterministic_bound"
    second = app.invoke(run_state(None, run_id="r2", question="预测失效概率"),
                        config=config)
    assert second["verified_facts"] == []
    assert second["verification_status"] != "deterministic_bound"
    assert str(snap["payload"]["assessments"][0]["legacy_result"]["Hs"]) not in second["answer"]


def test_new_run_with_unknown_or_invalid_classification_is_denied(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "cross-run-unknown"}}
    app.invoke(run_state(pid, run_id="r1", question="查询评分", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    for run_id, sensitivity in (("r2", "unknown"), ("r3", "invalid")):
        app.invoke(run_state(pid, run_id=run_id, question="查询评分", sensitivity=sensitivity),
                   config=config)
        assert len(llm.model.calls) == before


def test_same_paused_run_resume_keeps_its_authorization(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "resume-same-run"}}
    first = app.invoke(run_state(None, run_id="r1", question="查询评分", sensitivity="synthetic"),
                       config=config)
    assert first.get("__interrupt__")
    resumed = app.invoke(__import__("langgraph.types", fromlist=["Command"]).Command(
        resume={"action": "clarify", "values": {"pipe_id": pid}}), config=config)
    assert resumed["status"] == "succeeded"
    assert resumed["run_context"]["run_id"] == "r1"
    assert resumed["run_context"]["question_sensitivity"] == "synthetic"
    assert resumed["verified_facts"]


def test_different_principal_same_thread_cannot_see_old_facts(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "principal-isolation"}}
    first = app.invoke(run_state(pid, run_id="r1", question="查询评分", sensitivity="synthetic"),
                       config=config)
    assert first["verified_facts"]
    second = {"thread_id": "principal-isolation", "run_id": "r2",
              "principal": {"actor_id": "other", "scope": "internal"},
              "question": "预测失效概率", "snapshot_id": "run-isolation",
              "batch_refs": [], "scenario": None, "decision": None}
    out = app.invoke(second, config=config)
    assert out["verified_facts"] == []
    assert str(snap["payload"]["assessments"][0]["legacy_result"]["Hs"]) not in out["answer"]


def test_new_run_can_use_matching_run_context_classification(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "explicit-context"}}
    app.invoke(run_state(pid, run_id="r1", question="查询评分", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    state = run_state(pid, run_id="r2", question="查询评分")
    state["run_context"] = {"run_id": "r2", "principal_id": "tester",
                            "snapshot_id": "run-isolation",
                            "question_sensitivity": "synthetic",
                            "request_fingerprint": request_fingerprint(state),
                            "authorization_context": {"scope": "internal"}}
    app.invoke(state, config=config)
    assert len(llm.model.calls) == before + 1


def test_new_run_with_unknown_pipe_does_not_reuse_old_facts(tmp_path):
    snap = snapshot()
    pid = snap["payload"]["assessments"][0]["pipe_id"]
    llm = real_llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "cross-run-unknown-pipe"}}
    first = app.invoke(run_state(pid, run_id="r1", question="查询评分", sensitivity="synthetic"),
                       config=config)
    assert first["verified_facts"]
    second = app.invoke(run_state("syn-not-exist", run_id="r2", question="查询评分"),
                        config=config)
    assert second["verified_facts"] == []
    assert str(snap["payload"]["assessments"][0]["legacy_result"]["Hs"]) not in second["answer"]


def test_new_unclassified_run_calls_no_route_answer_or_query_embedding(tmp_path):
    from types import SimpleNamespace
    from src.agents.outbound import RequestClassification
    from src.agents.providers.llm import LangChainLLM
    from src.agents.rag.chunking import chunk_document
    from src.agents.rag.manifest import KnowledgeDocument
    from src.agents.rag.retriever import HybridRetriever, OpenAIEmbedding

    class RouteProxy:
        def __init__(self, model):
            self.model = model

        def invoke(self, messages):
            self.model.route_calls.append(messages)
            return RouteDecision(intent="materials", tasks=["materials"], confidence=1, reason="spy")

    class Model:
        def __init__(self):
            self.route_calls, self.answer_calls = [], []

        def with_structured_output(self, schema):
            return RouteProxy(self)

        def invoke(self, messages):
            self.answer_calls.append(messages)
            return SimpleNamespace(content="资料依据。[doc:D1:P1]")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"index": 0, "embedding": [3.0, 4.0]}]}

    class Client:
        def __init__(self):
            self.calls = []

        def post(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return Response()

    provider = OpenAIEmbedding.__new__(OpenAIEmbedding)
    provider.base_url = "http://127.0.0.1:30000/v1"
    provider.model = "embedding-test"
    provider.dimensions = 2
    provider.query_prefix = provider.document_prefix = ""
    provider.timeout = 1
    provider.profile = "test:2"
    provider.service_id = "embedding:test"
    provider.endpoint = "http://127.0.0.1:30000/v1"
    provider._client = Client()
    provider._owns_client = False
    doc = KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="D1", version="v1",
                            scope="internal", rule_status="current", sensitivity="synthetic")
    retriever = HybridRetriever(chunk_document(doc, "# 分工\n三方分工与职责。"))
    document_classification = RequestClassification(
        "tester", "internal", "synthetic", "not_applicable", (), ("synthetic",),
        "document_embedding", "embedding:test", "test", "http://127.0.0.1:30000/v1")
    retriever.set_embedding(provider, classification=document_classification)
    provider._client.calls.clear()

    model = Model()
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode, llm.service_id, llm.endpoint, llm.model = "openai", "test-llm", "http://127.0.0.1:30000/v1", model
    app = build_graph(llm=llm, retriever=retriever, snapshot_lookup=lambda _: None,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "all-exits"}}
    state = {"thread_id": "all-exits", "run_id": "r1",
             "principal": {"actor_id": "tester", "scope": "internal"},
             "question": "三方分工", "question_sensitivity": "synthetic",
             "snapshot_id": None, "batch_refs": []}
    app.invoke(state, config=config)
    before = (len(model.route_calls), len(model.answer_calls), len(provider._client.calls))
    second = {**state, "run_id": "r2"}
    second.pop("question_sensitivity")
    app.invoke(second, config=config)
    assert (len(model.route_calls), len(model.answer_calls), len(provider._client.calls)) == before
