"""AGENT-BOUNDARY-FINAL：Codex 复审 14 个失败探针的固定回归。

四个根因组：B 统一模型出口、C 语料强制授权、D 引用与发送合同、E 执行回执。
全部使用合成输入、Fake 模型与 MockTransport；PostgreSQL 用例使用隔离 schema。
"""
from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from src.agents import evidence as evidence_module
from src.agents.evidence import (ExecutionReceipt, answer_verification_status, fact_cards,
                                 verified_facts)
from src.agents.graph import build_graph
from src.agents.outbound import RequestClassification, require_outbound
from src.agents.providers.llm import LangChainLLM
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument
from src.agents.rag.retriever import (
    HybridRetriever,
    InMemoryVectorIndex,
    KnowledgeChunk,
    OpenAIEmbedding,
    PgVectorIndex,
    RetrievalHit,
    citations,
)
from src.agents.state import AgentSettings, RouteDecision
from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.input_batches import BatchPrincipal, InputBatchRepository

APPROVED = "http://approved.invalid:8001/v1"
# 修复前快照没有执行账本；保留兼容访问以展示修复前后差异。
ExecutionLedger = getattr(evidence_module, "ExecutionLedger", None)
UNAPPROVED = "http://unapproved.invalid/private"
EVID = "tool:" + "f" * 64


# --------------------------------------------------------------------------- B：统一出口


def _classification(*, purpose="route", service="llm:review-model", endpoint=APPROVED):
    return RequestClassification("tester", "internal", "synthetic", "synthetic", (), (),
                                 purpose, service, "test", endpoint)


def _chat_provider(monkeypatch, handler):
    import httpx
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test")
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", APPROVED)
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return LangChainLLM(AgentSettings(llm_mode="openai", llm_base_url=APPROVED,
                                      llm_model="review-model", model_timeout_seconds=10),
                        http_client=client)


def test_bf_b_replaced_active_client_chat(monkeypatch):
    """替换 SDK 真正使用的 httpx 客户端后不得发出任何未批准请求。"""
    import httpx

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": UNAPPROVED})

    llm = _chat_provider(monkeypatch, handler)
    # 探针：把活动客户端换成普通 httpx 客户端并允许重定向。
    llm.model.root_client._client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=True)
    llm.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[], data_mode="synthetic",
              classification=_classification())
    assert [url for url in seen if "unapproved.invalid" in url] == []


class _EmbeddingHandler:
    def __init__(self, status=200, location=None):
        self.seen, self.status, self.location = [], status, location

    def __call__(self, request):
        import httpx
        self.seen.append(str(request.url))
        if self.status in {307, 308}:
            return httpx.Response(self.status, headers={"Location": self.location})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [3.0, 4.0]}]})


def _embedding_provider(monkeypatch, handler):
    import httpx
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", APPROVED)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return OpenAIEmbedding(APPROVED, "review-embedding", 2, client=client)


def test_bf_b_replaced_active_client_embedding(monkeypatch):
    import httpx
    handler = _EmbeddingHandler(307, UNAPPROVED)
    embedding = _embedding_provider(monkeypatch, handler)
    embedding._client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    item = _classification(purpose="query_embedding", service="embedding:review-embedding")
    with pytest.raises(PermissionError):
        embedding.embed(["固定合成"], kind="query", classification=item)
    assert [url for url in handler.seen if "unapproved.invalid" in url] == []


def _legacy_router(monkeypatch, handler):
    from src.agents.router import LLMRouter
    import httpx
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test")
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", APPROVED)
    router = LLMRouter(AgentSettings(llm_mode="openai", llm_base_url=APPROVED,
                                     llm_model="review-model", model_timeout_seconds=10))
    # 把 transport 注入受控客户端：仍保留 GuardedHTTPClient 类型。
    router.http_client._transport = httpx.MockTransport(handler)
    return router


def test_bf_b_legacy_router_drift(monkeypatch):
    import httpx
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "model": "m"})

    router = _legacy_router(monkeypatch, handler)
    router.model.root_client.base_url = "http://unapproved.invalid:8002/private"
    router.route("资料职责", allowed_tasks=("materials",), pipe_ids=[], data_mode="synthetic",
                 classification=_classification())
    assert seen == []


def test_bf_b_legacy_router_redirect(monkeypatch):
    import httpx
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": UNAPPROVED})

    router = _legacy_router(monkeypatch, handler)
    router.http_client.follow_redirects = True
    router.route("资料职责", allowed_tasks=("materials",), pipe_ids=[], data_mode="synthetic",
                 classification=_classification())
    assert [url for url in seen if "unapproved.invalid" in url] == []


def test_bf_b_approved_chat_and_embedding_still_work(monkeypatch):
    """正常授权的合成调用不得被错误阻断。"""
    import httpx

    chat_seen = []

    def chat_handler(request):
        chat_seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "model": "m"})

    llm = _chat_provider(monkeypatch, chat_handler)
    llm.model.root_client.base_url = f"{APPROVED}/"
    llm.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[], data_mode="synthetic",
              classification=_classification())
    assert chat_seen == [f"{APPROVED}/chat/completions"]

    handler = _EmbeddingHandler()
    embedding = _embedding_provider(monkeypatch, handler)
    vectors = embedding.embed(["固定合成"], kind="query",
                              classification=_classification(purpose="query_embedding",
                                                             service="embedding:review-embedding"))
    assert len(vectors) == 1 and handler.seen == [f"{APPROVED}/embeddings"]


# --------------------------------------------------------------------------- C：语料强制授权


def _pg(dsn: str, prefix: str):
    import psycopg
    from psycopg.conninfo import make_conninfo

    schema = f"{prefix}_{uuid.uuid4().hex}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    return admin, make_conninfo(dsn, options=f"-csearch_path={schema},public"), schema


def _chunk(corpus: str, chunk_id="shared-id", content="医院尚未恢复。") -> KnowledgeChunk:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return KnowledgeChunk(chunk_id=chunk_id, document_id="D1", document_version="v1",
                          document_title="D1", location="P1", title="t", content=content,
                          content_hash=digest, scope="internal", rule_status="current",
                          sensitivity="synthetic", embedding_profile="contract-profile",
                          corpus_id=corpus, source_content=content,
                          source_length=len(content), source_truncated=False)


def test_bf_c_unscoped_search_rejected():
    """省略 corpus_id 时不得检索，内存索引同样拒绝。"""
    chunk = _chunk("default")
    memory = InMemoryVectorIndex([chunk], [[1.0, 0.0]], "contract-profile")
    with pytest.raises(ValueError):
        memory.search([1.0, 0.0], {chunk.chunk_id}, 2)
    assert memory.search([1.0, 0.0], {chunk.chunk_id}, 2, corpus_id="default")

    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    admin, scoped, schema = _pg(dsn, "bf_unscoped")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        index.upsert(chunk, [1.0, 0.0])
        with pytest.raises(ValueError):
            index.search([1.0, 0.0], {chunk.chunk_id}, 2)
        assert index.search([1.0, 0.0], {chunk.chunk_id}, 2, corpus_id="default")
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_bf_c_quarantined_rows_unreachable_without_scope():
    """迁移后的隔离行：无范围查询被拒绝，显式隔离语料也被拒绝。"""
    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    import psycopg

    admin, scoped, schema = _pg(dsn, "bf_quarantine")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        with psycopg.connect(scoped) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute(f"""CREATE TABLE {index.table} (
                chunk_id text PRIMARY KEY, corpus_id text NOT NULL DEFAULT 'default',
                document_id text NOT NULL, document_version text NOT NULL,
                document_title text NOT NULL DEFAULT '', location text NOT NULL, title text NOT NULL,
                content text NOT NULL, content_hash text NOT NULL, scope text NOT NULL,
                rule_status text NOT NULL, sensitivity text NOT NULL, embedding_profile text NOT NULL,
                source_content text, source_length integer, source_truncated boolean NOT NULL DEFAULT false,
                embedding vector(2) NOT NULL)""")
            conn.execute(f"""INSERT INTO {index.table} (chunk_id, corpus_id, document_id, document_version,
                location, title, content, content_hash, scope, rule_status, sensitivity, embedding_profile,
                embedding) VALUES ('legacy','default','D9','v1','P1','t','历史正文',%s,'internal',
                'current','synthetic','contract-profile','[1,0]')""",
                (hashlib.sha256("历史正文".encode("utf-8")).hexdigest(),))
        assert index.ensure_schema() == "migrated_legacy"
        with pytest.raises(ValueError):
            index.search([1.0, 0.0], {"legacy"}, 2)
        with pytest.raises(ValueError):
            index.search([1.0, 0.0], {"legacy"}, 2, corpus_id="legacy-unverified:" + index.table)
        assert index.search([1.0, 0.0], {"legacy"}, 2, corpus_id="default") == []
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_bf_c_hybrid_without_corpus_returns_no_prose():
    doc = KnowledgeDocument(document_id="D1", path="knowledge/d1.md", title="D1", version="v1",
                            scope="internal", rule_status="current", sensitivity="synthetic")
    retriever = HybridRetriever(chunk_document(doc, "# 分工\n医院尚未恢复。"))
    retriever.corpus_id = ""
    hits, mode = retriever.search("医院", scope="internal")
    assert hits == [] and mode == "unscoped"


# --------------------------------------------------------------------------- D：引用与发送合同


def _hit(content, *, source=None, truncated=None, declared_length=None, version="v1"):
    full = source if source is not None else content
    return RetrievalHit(
        chunk_id="D1-abc", document_id="D1", document_version=version, document_title="D1",
        location="P1", title="t", content=content,
        content_hash=hashlib.sha256(full.encode("utf-8")).hexdigest(),
        scope="internal", rule_status="current", sensitivity="synthetic",
        embedding_profile="p", corpus_id="bf", source_content=source,
        source_length=len(full) if declared_length is None else declared_length,
        source_truncated=(len(full) > len(content)) if truncated is None else truncated,
        score=1.0, bm25_rank=1, retrieval_mode="bm25")


def _excerpt_citation(*, start, end, text=None, citation_id=None):
    content = "服务尚未恢复。"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return {
        "evidence_id": "doc:D1:P1",
        "citation_id": citation_id or f"doc:D1:v1:P1:{digest[:12]}",
        "document_id": "D1", "location": "P1", "document_version": "v1",
        "content_hash": digest, "content": content, "sent_hash": digest,
        "sent_length": len(content), "integrity_status": "verified",
        "excerpts": [{"text": text if text is not None else content,
                      "start": start, "end": end, "hash": digest}],
    }


def test_bf_d_negative_and_fractional_offsets_not_verified():
    for start in (-7, 0.5):
        citation = _excerpt_citation(start=start, end=7)
        assert answer_verification_status("服务尚未恢复。[doc:D1:P1]", [], [citation]) != "verified_excerpt"


def test_bf_d_excerpt_offset_out_of_range_not_verified():
    citation = _excerpt_citation(start=0, end=99)
    assert answer_verification_status("服务尚未恢复。[doc:D1:P1]", [], [citation]) != "verified_excerpt"


def test_bf_d_source_length_declaration_must_match():
    source = "甲" * 7005
    stored = source[:6000]
    row = citations([_hit(stored, source=source, declared_length=1, truncated=True)])[0]
    assert row["provenance_status"] == "invalid_provenance"
    assert row["integrity_status"] == "invalid_provenance"
    assert answer_verification_status(stored[:7] + "[doc:D1:P1]", [], [row]) == "invalid_evidence"


def test_bf_d_false_truncation_declaration_rejected():
    source = "甲" * 7005
    stored = source[:6000]
    row = citations([_hit(stored, source=source, declared_length=7005, truncated=False)])[0]
    assert row["provenance_status"] == "invalid_provenance"
    assert row["verification_status"] == "unverified"


def test_bf_d_oversized_payload_is_rejected_before_send(monkeypatch):
    """声明 2324 字符的发送片段必须被合同拒绝，而不是发送时再截断。"""
    import httpx
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}}], "model": "m"})

    llm = _chat_provider(monkeypatch, handler)
    content = "甲" * 2324
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    citation = {"evidence_id": "doc:D1:P1", "citation_id": f"doc:D1:v1:P1:{digest[:12]}",
                "document_id": "D1", "location": "P1", "document_version": "v1",
                "content_hash": digest, "content": content, "sent_hash": digest,
                "sent_length": len(content), "integrity_status": "verified", "excerpts": [],
                "sensitivity": "synthetic"}
    item = RequestClassification("tester", "internal", "synthetic", "synthetic", (),
                                 ("synthetic",), "answer", "llm:review-model", "test", APPROVED)
    with pytest.raises(PermissionError, match="上限"):
        require_outbound(item, purpose="answer", model_service="llm:review-model",
                         endpoint=APPROVED, citations=[citation])
    assert seen == []


def test_bf_d_sent_payload_matches_declared_slice(monkeypatch):
    """正常 2000 字符片段：实际消息正文与 sent_hash/sent_length 完全一致。"""
    import httpx

    captured = []

    class Model:
        def __init__(self):
            self.messages = []

        def invoke(self, messages):
            captured.append(messages)
            return type("R", (), {"content": "依据资料。[doc:D1:P1]"})

    llm = _chat_provider(monkeypatch, lambda request: httpx.Response(200, json={}))
    llm.model = Model()
    content = "乙" * 2000
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    citation = {"evidence_id": "doc:D1:P1", "citation_id": f"doc:D1:v1:P1:{digest[:12]}",
                "document_id": "D1", "location": "P1", "document_version": "v1",
                "content_hash": digest, "content": content, "sent_hash": digest,
                "sent_length": len(content), "integrity_status": "verified", "excerpts": [],
                "sensitivity": "synthetic"}
    item = RequestClassification("tester", "internal", "synthetic", "synthetic", (),
                                 ("synthetic",), "answer", "llm:review-model", "test", APPROVED)
    llm.answer("问题", [], [citation], [], classification=item)
    payload = captured[0][0][1]
    assert content in payload
    assert "乙" * 2001 not in payload


# --------------------------------------------------------------------------- E：执行回执


def _execute_once(tmp_path, *, run_id="run-new", snapshot_id="syn-snapshot"):
    """受控执行一次 query_assessment：只有执行器能登记账本。"""
    access = ToolAccess(principal=BatchPrincipal(actor_id="tester", scope="internal"),
                        snapshot_id=snapshot_id, allowed_tools=frozenset({"query_assessment"}))
    ledger = ExecutionLedger()
    tools = DomainTools(access, lambda _: _snapshot(), InputBatchRepository(tmp_path),
                        ledger=ledger, run_id=run_id)
    output = tools.execute("query_assessment", {"pipe_id": "syn-bf-001"})
    return output, ledger


def test_bf_e_snapshot_binding(tmp_path):
    """E-snapshot：账本记录只对本次快照生效。"""
    output, ledger = _execute_once(tmp_path, run_id="run-new", snapshot_id="syn-snapshot")
    facts = fact_cards([output], [])
    assert verified_facts(facts, run_id="run-new", snapshot_id="different-snapshot",
                          principal_id="tester", ledger=ledger) == []
    assert verified_facts(facts, run_id="run-new", snapshot_id="syn-snapshot",
                          principal_id="tester", ledger=ledger)


def test_bf_e_tool_version_binding(tmp_path):
    """E-tool-version：事实声明的工具版本必须与执行器登记一致。"""
    output, ledger = _execute_once(tmp_path, run_id="run-new")
    tampered = [{**card, "tool_version": "unknown-v999"} for card in fact_cards([output], [])]
    assert verified_facts(tampered, run_id="run-new", snapshot_id="syn-snapshot",
                          principal_id="tester", ledger=ledger) == []


def test_bf_e_fact_tool_version_must_match_record(tmp_path):
    """E-tool-result-version：另一版本的声明不得认证。"""
    output, ledger = _execute_once(tmp_path, run_id="run-new")
    mismatched = [{**card, "tool_version": "domain-tools-999"} for card in fact_cards([output], [])]
    assert verified_facts(mismatched, run_id="run-new", snapshot_id="syn-snapshot",
                          principal_id="tester", ledger=ledger) == []


def test_bf_e_direct_receipt_construction_cannot_self_certify():
    """E-direct-receipt：回执不可直接构造；无账本不得认证。"""
    fact = {"tool_name": "query_assessment", "tool_version": "domain-tools-1",
            "status": "succeeded", "evidence_id": EVID,
            "result": {"status": "succeeded", "assessment": {
                "pipe_id": "A", "model_version": "0.1.1",
                "legacy_result": {"Hs": 80.0, "Rs": 2, "grade": "基本健康"}}}}
    digest = hashlib.sha256(b"{}").hexdigest()
    with pytest.raises(ValueError):
        ExecutionReceipt(run_id="run-new", tool_name="query_assessment", evidence_id=EVID,
                         tool_version="domain-tools-1", status="succeeded",
                         snapshot_id="syn-snapshot", result_hash=digest, issuer="domain-tools")
    assert verified_facts([fact], run_id="run-new", snapshot_id="syn-snapshot",
                          principal_id="tester") == []


# --------------------------------------------------------------------------- 正向对照


def _snapshot():
    payload = {"schema_version": "0.1.0", "name": "boundary", "data_mode": "synthetic",
               "source_status": "constructed", "as_of": "2026-11-03T08:00:00+08:00",
               "source_refs": ["synthetic:boundary"],
               "assessments": [{"pipe_id": "syn-bf-001", "as_of": "2026-11-03T08:00:00+08:00",
                                "model_family": "legacy_demo", "model_version": "0.1.1",
                                "legacy_result": {"Rs": 1.3, "Hs": 92.5,
                                                  "completeness_percent": 100.0, "grade": "健康"},
                                "evidence_refs": ["controlled_recompute:pipe_health_0.1.1"]}]}
    return {"snapshot_id": "syn-snapshot", "payload": payload, "as_of": payload["as_of"],
            "data_mode": "synthetic", "source_status": "constructed", "sha256": "fixture"}


def test_bf_positive_real_graph_still_produces_verified_facts(tmp_path):
    snap = _snapshot()
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode, llm.service_id, llm.endpoint = "openai", "test-llm", APPROVED
    llm.model = type("M", (), {"calls": [], "with_structured_output": lambda self, s: self,
                               "invoke": lambda self, m: RouteDecision(
                                   intent="assessment", tasks=["assessment"], confidence=1, reason="spy")})()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    state = {"thread_id": "bf", "run_id": "r1", "principal": {"actor_id": "tester", "scope": "internal"},
             "question": "查询评分", "question_sensitivity": "synthetic",
             "snapshot_id": "syn-snapshot", "pipe_id": "syn-bf-001", "batch_refs": []}
    out = app.invoke(state, config={"configurable": {"thread_id": "bf"}})
    assert out["execution_receipts"] and out["verified_facts"]
    assert {item["metric"] for item in out["verified_facts"]} == {"Hs", "Rs"}
    assert all(item["verification_status"] == "verified" for item in out["verified_facts"])
