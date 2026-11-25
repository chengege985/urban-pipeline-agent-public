"""AGENT02-01/07: trusted classification for every model/embedding exit."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from src.agents.gateway import LLMGateway
from src.agents.outbound import RequestClassification, classify_request, require_outbound
from src.agents.providers.llm import LangChainLLM
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument, KnowledgeManifest
from src.agents.rag.retriever import OpenAIEmbedding, build_retriever
from src.agents.state import RouteDecision


def classification(mode="synthetic", question="synthetic", facts=(), citations=(),
                   purpose="answer", service="test-llm", endpoint=""):
    return RequestClassification(
        "tester", "internal", mode, question, tuple(facts), tuple(citations),
        purpose, service, "test", endpoint)


class RouteSpy:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return RouteDecision(intent="assessment", tasks=["assessment"], confidence=1, reason="spy")


class AnswerSpy:
    def __init__(self, text="资料结论。[doc:D1:P1]"):
        self.calls = []
        self.text = text

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return SimpleNamespace(content=self.text)


def fake_llm(model):
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode = "openai"
    llm.service_id = "test-llm"
    llm.model = model
    return llm


class FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"data": [{"index": 0, "embedding": [3.0, 4.0]}]}


class FakeClient:
    def __init__(self):
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return FakeResponse()


def embedding_client():
    embedding = OpenAIEmbedding.__new__(OpenAIEmbedding)
    embedding.base_url = "http://example.invalid/v1"
    embedding.model = "test-embedding"
    embedding.dimensions = 2
    embedding.query_prefix = ""
    embedding.document_prefix = ""
    embedding.timeout = 1
    embedding.profile = "test:2"
    embedding.service_id = "embedding:test"
    embedding.endpoint = "http://127.0.0.1:30000/v1"
    embedding._client = FakeClient()
    embedding._owns_client = False
    return embedding


def test_synthetic_document_unknown_query_never_calls_embedding(monkeypatch):
    monkeypatch.delenv("AGENT_TRUSTED_QUESTION_CLASS", raising=False)
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    item = classify_request({"actor_id": "tester", "scope": "competition"}, "内部问题", "query_embedding",
                            snapshot={"payload": {"data_mode": "synthetic"}},
                            model_service="embedding:test")
    assert item.question_sensitivity == "unknown"
    embedding = embedding_client()
    with pytest.raises(PermissionError):
        embedding.embed(["内部问题"], classification=item)
    assert embedding._client.calls == []


def test_competition_scope_does_not_classify_question(monkeypatch):
    monkeypatch.delenv("AGENT_TRUSTED_QUESTION_CLASS", raising=False)
    item = classify_request({"actor_id": "tester", "scope": "competition"}, "问题", "answer",
                            snapshot={"payload": {"data_mode": "synthetic"}},
                            model_service="test-llm")
    assert item.question_sensitivity == "unknown"
    with pytest.raises(PermissionError):
        require_outbound(item)


def test_no_snapshot_and_internal_question_is_unknown(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "internal")
    item = classify_request({"actor_id": "tester", "scope": "internal"}, "内部问题", "answer",
                            snapshot=None, model_service="test-llm")
    assert item.data_mode == "unknown"
    with pytest.raises(PermissionError):
        require_outbound(item)


def test_synthetic_query_with_internal_fact_is_denied(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    item = classification(facts=("internal",))
    with pytest.raises(PermissionError):
        require_outbound(item)


def test_synthetic_query_with_internal_rag_is_denied(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    item = classification(citations=("internal",))
    with pytest.raises(PermissionError):
        require_outbound(item)


def test_route_may_pass_while_answer_is_denied(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    route_model, answer_model = RouteSpy(), AnswerSpy()
    routers = fake_llm(route_model)
    routers.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[], data_mode="synthetic",
                  classification=classification(purpose="route"))
    assert len(route_model.calls) == 1
    users = fake_llm(answer_model)
    with pytest.raises(PermissionError):
        users.answer("资料", [], [{"evidence_id": "doc:D1:P1", "sensitivity": "internal",
                                   "content": "内部", "document_version": "v1", "content_hash": "h"}],
                     [], data_mode="synthetic", classification=classification(citations=("internal",)))
    assert answer_model.calls == []


def test_unauthorized_document_indexing_falls_back_to_bm25(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge/a.md").write_text("# 管龄\n管龄超过设计寿命时风险上升。", encoding="utf-8")
    doc = KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="内部资料", version="v1",
                            scope="internal", rule_status="current", sensitivity="internal")
    manifest = KnowledgeManifest(documents=[doc])
    (tmp_path / "data/knowledge").mkdir(parents=True)
    (tmp_path / "data/knowledge/manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    embedding = embedding_client()
    retriever, _ = build_retriever(tmp_path, embedding)
    assert embedding._client.calls == []
    assert retriever.vector_status["available"] is False
    assert "未获准" in retriever.vector_status["reason"]
    hits, mode = retriever.search("管龄", scope="internal", classification=None)
    assert hits and mode == "bm25"


def test_explicit_synthetic_authorization_calls_once(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    embedding = embedding_client()
    vectors = embedding.embed(["合成正文"], kind="query", classification=classification(
        question="synthetic", purpose="query_embedding", service="embedding:test",
        endpoint="http://127.0.0.1:30000/v1"))
    assert len(embedding._client.calls) == 1
    assert vectors[0] == pytest.approx([0.6, 0.8])


def test_global_allow_does_not_rescue_unknown(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_NON_SYNTHETIC", "1")
    monkeypatch.delenv("AGENT_TRUSTED_QUESTION_CLASS", raising=False)
    unknown = classification(question="unknown", mode="unknown")
    with pytest.raises(PermissionError):
        require_outbound(unknown)
    internal = classification(mode="internal", question="internal", service="test-llm")
    require_outbound(internal)


def test_gateway_uses_same_policy_before_http(monkeypatch):
    monkeypatch.setenv("WATER_LLM_MODE", "openai")
    monkeypatch.setenv("WATER_LLM_BASE_URL", "http://example.invalid/v1")
    monkeypatch.setenv("WATER_LLM_MODEL", "test")
    monkeypatch.delenv("AGENT_TRUSTED_QUESTION_CLASS", raising=False)
    gateway = LLMGateway()
    with pytest.raises(PermissionError):
        gateway.classify("问题", "real", classification=classification(mode="real", question="real"))


def test_legacy_router_uses_same_denial(monkeypatch):
    from src.agents.router import LLMRouter
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    router = LLMRouter.__new__(LLMRouter)
    router.model = RouteSpy()
    router.service_id = "test-llm"
    result = router.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[],
                          data_mode="real", classification=classification(mode="real", question="real"))
    assert result.tasks == ["assessment"]
    assert router.model.calls == []


def test_env_synthetic_does_not_override_explicit_unknown(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    item = classification(mode="synthetic", question="unknown")
    with pytest.raises(PermissionError):
        require_outbound(item)
    embedding = embedding_client()
    with pytest.raises(PermissionError):
        embedding.embed(["自由文本"], kind="query", classification=item)
    assert embedding._client.calls == []


def test_purpose_and_endpoint_are_bound_to_actual_call(monkeypatch):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    embedding = embedding_client()
    document_permit = classification(
        purpose="document_embedding", service="embedding:test",
        endpoint="http://127.0.0.1:30000/v1")
    with pytest.raises(PermissionError):
        embedding.embed(["查询"], kind="query", classification=document_permit)
    assert embedding._client.calls == []

    wrong_endpoint = classification(
        purpose="query_embedding", service="embedding:test",
        endpoint="http://127.0.0.1:30001/v1")
    with pytest.raises(PermissionError):
        embedding.embed(["查询"], kind="query", classification=wrong_endpoint)
    assert embedding._client.calls == []


def test_model_identity_cannot_be_reused(monkeypatch):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    llm = fake_llm(AnswerSpy())
    other_model = classification(service="other-model", purpose="answer")
    with pytest.raises(PermissionError):
        llm.answer("资料", [], [], [], classification=other_model)
    assert llm.model.calls == []


def test_invalid_principal_and_unapproved_endpoint_are_rejected(monkeypatch):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    with pytest.raises(PermissionError):
        require_outbound(RequestClassification("", "internal", "synthetic", "synthetic", (),
                                               (), "answer", "test-llm", "test"))
    item = classification(service="test-llm", endpoint="https://example.invalid/v1")
    with pytest.raises(PermissionError):
        require_outbound(item, purpose="answer", model_service="test-llm",
                         endpoint="https://example.invalid/v1")


def test_explicit_unknown_stops_langgraph_model_exits(tmp_path, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from src.agents.graph import build_graph
    from src.platform.input_batches import InputBatchRepository

    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)

    class SpyBoth:
        def __init__(self):
            self.calls = []

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.calls.append(messages)
            return RouteDecision(intent="materials", tasks=["materials"], confidence=1, reason="spy")

    model = SpyBoth()
    llm = fake_llm(model)
    payload = json.loads((__import__("pathlib").Path(__file__).resolve().parents[1] /
                          "data/synthetic/s01.json").read_text(encoding="utf-8"))
    graph = build_graph(llm=llm, retriever=None,
                        snapshot_lookup=lambda _: {"payload": payload},
                        batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    state = {"thread_id": "unknown-route", "run_id": "r",
             "principal": {"actor_id": "tester", "scope": "internal"},
             "question": "资料依据", "question_sensitivity": "unknown",
             "snapshot_id": "s", "batch_refs": []}
    result = graph.invoke(state, config={"configurable": {"thread_id": "unknown-route"}})
    assert model.calls == []
    assert result["status"] in {"insufficient_data", "running", "succeeded"}


def test_fact_cards_preserve_internal_sensitivity(monkeypatch):
    from src.agents.evidence import fact_cards

    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    cards = fact_cards([], [{"evidence_id": "doc:D1:P1", "sensitivity": "internal",
                             "content": "内部正文", "document_version": "v1"}])
    assert cards[0]["sensitivity"] == "internal"
    item = classify_request({"actor_id": "tester", "scope": "internal"}, "问题", "answer",
                            snapshot={"payload": {"data_mode": "synthetic"}},
                            facts=cards, model_service="test-llm")
    assert "internal" in item.fact_sensitivities
    with pytest.raises(PermissionError):
        require_outbound(item)


def test_deployment_synthetic_env_does_not_classify_free_text(monkeypatch):
    monkeypatch.setenv("AGENT_TRUSTED_QUESTION_CLASS", "synthetic")
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    item = classify_request({"actor_id": "tester", "scope": "competition"}, "自由输入问题", "answer",
                            snapshot={"payload": {"data_mode": "synthetic"}},
                            model_service="test-llm")
    assert item.question_sensitivity == "unknown"
    with pytest.raises(PermissionError):
        require_outbound(item)
    embedding = embedding_client()
    with pytest.raises(PermissionError):
        embedding.embed(["自由输入问题"], kind="query", classification=item)
    assert embedding._client.calls == []


def test_llm_endpoint_binding_and_actual_fact_mismatch(monkeypatch):
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    llm = fake_llm(AnswerSpy())
    llm.endpoint = "http://127.0.0.1:30000/v1"
    wrong_endpoint = classification(service="test-llm", endpoint="http://127.0.0.1:30001/v1")
    with pytest.raises(PermissionError):
        llm.answer("资料", [], [], [], classification=wrong_endpoint)
    assert llm.model.calls == []

    permit = classification(citations=())
    with pytest.raises(PermissionError):
        require_outbound(permit, facts=[{"sensitivity": "internal"}])


def test_competition_principal_never_gets_internal_even_with_global_allow(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_NON_SYNTHETIC", "1")
    item = RequestClassification("tester", "competition", "synthetic", "synthetic",
                                 (), ("internal",), "answer", "test-llm", "test")
    with pytest.raises(PermissionError):
        require_outbound(item)
    allowed = RequestClassification("tester", "competition", "synthetic", "synthetic",
                                    (), ("public",), "answer", "test-llm", "test")
    require_outbound(allowed)


def test_explicit_server_synthetic_classification_is_the_only_allow_path(monkeypatch):
    monkeypatch.delenv("AGENT_TRUSTED_QUESTION_CLASS", raising=False)
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    item = classify_request({"actor_id": "tester", "scope": "competition"}, "固定合成问题", "query_embedding",
                            snapshot={"payload": {"data_mode": "synthetic"}},
                            model_service="embedding:test",
                            endpoint="http://127.0.0.1:30000/v1",
                            question_sensitivity="synthetic")
    assert item.question_sensitivity == "synthetic"
    embedding = embedding_client()
    embedding.embed(["固定合成问题"], kind="query", classification=item)
    assert len(embedding._client.calls) == 1


@pytest.mark.parametrize("bad", [
    "http://127.0.0.1:30000/v1/../evil",
    "http://127.0.0.1:30000/v1/%2e%2e/evil",
    "http://127.0.0.1:30000/v1/%2fevil",
])
def test_endpoint_normalization_rejects_traversal(monkeypatch, bad):
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", "http://127.0.0.1:30000/v1")
    item = classification(service="test-llm", endpoint=bad)
    with pytest.raises(PermissionError):
        require_outbound(item, purpose="answer", model_service="test-llm", endpoint=bad)


def test_endpoint_allowlist_is_exact_canonical_path(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", "http://127.0.0.1:30000/v1")
    exact = classification(service="test-llm", endpoint="http://127.0.0.1:30000/v1/")
    require_outbound(exact, purpose="answer", model_service="test-llm",
                     endpoint="http://127.0.0.1:30000/v1")
    other = classification(service="test-llm", endpoint="http://127.0.0.1:30000/v1/other")
    with pytest.raises(PermissionError):
        require_outbound(other, purpose="answer", model_service="test-llm",
                         endpoint="http://127.0.0.1:30000/v1/other")


def test_embedding_does_not_follow_redirects(monkeypatch):
    import httpx
    from src.agents.state import AgentSettings

    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "http://evil.invalid/steal"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    embedding = OpenAIEmbedding("http://127.0.0.1:30000/v1", "qwen3-test", 2, client=client)
    item = classification(purpose="query_embedding", service="embedding:qwen3-test",
                          endpoint="http://127.0.0.1:30000/v1")
    with pytest.raises(Exception):
        embedding.embed(["测试"], kind="query", classification=item)
    assert seen and all("evil.invalid" not in url for url in seen)


def test_real_chat_sdk_does_not_follow_redirects(monkeypatch):
    import httpx
    from src.agents.state import AgentSettings

    monkeypatch.setenv("AGENT_LLM_API_KEY", "test")
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "http://evil.invalid/steal"})

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    llm = LangChainLLM(AgentSettings(llm_mode="openai", llm_base_url="http://127.0.0.1:30000/v1",
                                     llm_model="test-model", model_timeout_seconds=10),
                       http_client=client)
    item = classification(purpose="route", service="llm:test-model",
                          endpoint="http://127.0.0.1:30000/v1")
    llm.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[], data_mode="synthetic",
              classification=item)
    assert seen and all("evil.invalid" not in url for url in seen)
