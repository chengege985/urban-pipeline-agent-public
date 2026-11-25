"""AGENT05-CLOSEOUT：Codex 17 项失败探针的固定回归。

每个用例对应 CODEX-REVIEW-AGENT05 中的一个失败案例 ID；命名前缀 `co_` 便于单独运行。
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
from src.agents import graph as graph_module
from src.agents.evidence import (ExecutionLedger, answer_verification_status, fact_cards,
                                 verified_facts)
from src.agents.graph import build_graph
from src.agents.outbound import RequestClassification, normalize_endpoint, require_outbound
from src.agents.providers.llm import LangChainLLM
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument
from src.agents.rag.retriever import HybridRetriever, OpenAIEmbedding, PgVectorIndex, RetrievalHit, citations
from src.agents.state import AgentSettings, RouteDecision
from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.input_batches import BatchPrincipal, InputBatchRepository

ROOT = Path(__file__).resolve().parents[1]
EVID = "tool:" + "f" * 64
# 修复前快照没有这些符号；保留兼容访问以便展示修复前/后的行为差异。
request_fingerprint = getattr(graph_module, "request_fingerprint", None)
ExecutionLedger = getattr(evidence_module, "ExecutionLedger", None)


# --------------------------------------------------------------------------- 运行合同


class _RouteModel:
    def __init__(self):
        self.calls = []

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        self.calls.append(messages)
        return RouteDecision(intent="assessment", tasks=["assessment"], confidence=1, reason="spy")


def _llm():
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode, llm.service_id, llm.endpoint = "openai", "test-llm", "http://127.0.0.1:30000/v1"
    llm.model = _RouteModel()
    return llm


def _snapshot():
    payload = {
        "schema_version": "0.1.0", "name": "closeout", "data_mode": "synthetic",
        "source_status": "constructed", "as_of": "2026-11-03T08:00:00+08:00",
        "source_refs": ["synthetic:closeout"],
        "assessments": [{
            "pipe_id": "syn-closeout-001", "as_of": "2026-11-03T08:00:00+08:00",
            "model_family": "legacy_demo", "model_version": "0.1.1",
            "legacy_result": {"Rs": 1.3, "Hs": 92.5, "completeness_percent": 100.0, "grade": "健康"},
            "evidence_refs": ["controlled_recompute:pipe_health_0.1.1"],
        }],
    }
    return {"snapshot_id": "co-snapshot", "payload": payload, "as_of": payload["as_of"],
            "data_mode": "synthetic", "source_status": "constructed", "sha256": "fixture"}


def _state(*, run_id, question="查询评分", principal="tester", snapshot_id="co-snapshot", sensitivity=None):
    value = {"thread_id": "co-thread", "run_id": run_id,
             "principal": {"actor_id": principal, "scope": "internal"},
             "question": question, "snapshot_id": snapshot_id, "pipe_id": "syn-closeout-001",
             "batch_refs": [], "scenario": None, "decision": None}
    if sensitivity is not None:
        value["question_sensitivity"] = sensitivity
    return value


def _graph(tmp_path, snapshot=None):
    snap = snapshot or _snapshot()
    llm = _llm()
    app = build_graph(llm=llm, retriever=None, snapshot_lookup=lambda _: snap,
                      batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    return app, llm, snap


def test_co_r01_reused_run_id_is_rejected_without_model_calls(tmp_path):
    app, llm, snap = _graph(tmp_path)
    config = {"configurable": {"thread_id": "co-thread"}}
    app.invoke(_state(run_id="r1", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    assert before == 1

    out = app.invoke(_state(run_id="r1", question="另一条三方分工问题"), config=config)
    assert len(llm.model.calls) == before, "同一 run_id 的新问题不得调用模型"
    assert out["phase"] == "run_contract_rejected"
    assert out["error_kind"] == "run_contract_violation"
    assert out["verified_facts"] == []
    assert str(snap["payload"]["assessments"][0]["legacy_result"]["Hs"]) not in out["answer"]


def test_co_r01_same_run_id_other_principal_is_rejected(tmp_path):
    app, llm, _ = _graph(tmp_path)
    config = {"configurable": {"thread_id": "co-thread"}}
    app.invoke(_state(run_id="r1", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    out = app.invoke(_state(run_id="r1", principal="intruder"), config=config)
    assert len(llm.model.calls) == before
    assert out["phase"] == "run_contract_rejected"


def test_co_r01_same_run_id_other_snapshot_is_rejected(tmp_path):
    app, llm, _ = _graph(tmp_path)
    config = {"configurable": {"thread_id": "co-thread"}}
    app.invoke(_state(run_id="r1", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    out = app.invoke(_state(run_id="r1", snapshot_id="co-other"), config=config)
    assert len(llm.model.calls) == before
    assert out["phase"] == "run_contract_rejected"


def test_co_r01_new_run_id_proceeds_without_inheriting_classification(tmp_path):
    app, llm, _ = _graph(tmp_path)
    config = {"configurable": {"thread_id": "co-thread"}}
    app.invoke(_state(run_id="r1", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    out = app.invoke(_state(run_id="r2", question="查询评分"), config=config)
    assert len(llm.model.calls) == before, "新 run 缺可信分类时必须保持 unknown"
    assert out["phase"] != "run_contract_rejected"
    assert out["run_context"]["request_fingerprint"] == request_fingerprint(_state(run_id="r2", question="查询评分"))


def test_co_r01_explicit_run_context_requires_matching_fingerprint(tmp_path):
    app, llm, _ = _graph(tmp_path)
    config = {"configurable": {"thread_id": "co-explicit"}}
    app.invoke(_state(run_id="r1", sensitivity="synthetic"), config=config)
    before = len(llm.model.calls)
    forged = _state(run_id="r2")
    forged["run_context"] = {"run_id": "r2", "principal_id": "tester", "snapshot_id": "co-snapshot",
                             "question_sensitivity": "synthetic",
                             "request_fingerprint": "0" * 64,
                             "authorization_context": {"scope": "internal"}}
    app.invoke(forged, config=config)
    assert len(llm.model.calls) == before, "指纹不匹配的注入上下文不得授予分类"

    legit = _state(run_id="r3")
    legit["run_context"] = {"run_id": "r3", "principal_id": "tester", "snapshot_id": "co-snapshot",
                            "question_sensitivity": "synthetic",
                            "request_fingerprint": request_fingerprint(legit),
                            "authorization_context": {"scope": "internal"}}
    app.invoke(legit, config=config)
    assert len(llm.model.calls) == before + 1


# --------------------------------------------------------------------------- HTTP 出口


def _classification(*, purpose="route", service="llm:test-model", endpoint="http://approved.invalid:8001/v1"):
    return RequestClassification("tester", "internal", "synthetic", "synthetic", (), (),
                                 purpose, service, "test", endpoint)


def test_co_r02_semicolon_path_parameter_is_rejected():
    with pytest.raises(ValueError):
        normalize_endpoint("http://approved.invalid:8001/v1;private")
    item = _classification(endpoint="http://approved.invalid:8001/v1;private")
    with pytest.raises(PermissionError):
        require_outbound(item, purpose="route", model_service="llm:test-model",
                         endpoint="http://approved.invalid:8001/v1;private")


def _chat_llm(monkeypatch, handler, base="http://approved.invalid:8001/v1"):
    import httpx
    monkeypatch.setenv("AGENT_LLM_API_KEY", "test")
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", base)
    monkeypatch.delenv("AGENT_ALLOW_NON_SYNTHETIC", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return LangChainLLM(AgentSettings(llm_mode="openai", llm_base_url=base, llm_model="test-model",
                                      model_timeout_seconds=10), http_client=client)


def test_co_r02_mutated_shared_client_cannot_follow_redirects(monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "http://unapproved.invalid:8002/private"})

    llm = _chat_llm(monkeypatch, handler)
    llm.http_client.follow_redirects = True  # 构造后被改成允许跟随
    llm.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[], data_mode="synthetic",
              classification=_classification())
    assert seen and all("unapproved.invalid" not in url for url in seen)


def test_co_r02_sdk_base_url_drift_sends_no_request(monkeypatch):
    import httpx

    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "model": "m"})

    llm = _chat_llm(monkeypatch, handler)
    # 检查对象 endpoint 仍指向批准地址，但 SDK 实际 base_url 被改到未批准主机。
    llm.model.root_client = httpx.Client(base_url="http://unapproved.invalid:8002/private",
                                         transport=httpx.MockTransport(handler))
    llm.route("查询评分", allowed_tasks=("assessment",), pipe_ids=[], data_mode="synthetic",
              classification=_classification())
    assert seen == [], "未批准目标不得收到任何请求"


class _EmbeddingHandler:
    def __init__(self, response_status=200, location=None):
        self.seen, self.status, self.location = [], response_status, location

    def __call__(self, request):
        import httpx
        self.seen.append(str(request.url))
        if self.status in {307, 308}:
            return httpx.Response(self.status, headers={"Location": self.location})
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [3.0, 4.0]}]})


def _embedding(monkeypatch, handler, base="http://approved.invalid:8001/v1"):
    import httpx
    monkeypatch.setenv("AGENT_ALLOWED_MODEL_ENDPOINTS", base)
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)
    return OpenAIEmbedding(base, "qwen3-test", 2, client=client)


def test_co_r02_mutated_embedding_client_cannot_follow_redirects(monkeypatch):
    handler = _EmbeddingHandler(307, "http://unapproved.invalid:8002/private")
    embedding = _embedding(monkeypatch, handler)
    embedding._client.follow_redirects = True
    item = _classification(purpose="query_embedding", service="embedding:qwen3-test")
    with pytest.raises(Exception):
        embedding.embed(["测试"], kind="query", classification=item)
    assert all("unapproved.invalid" not in url for url in handler.seen)


def test_co_r02_embedding_base_url_drift_sends_no_request(monkeypatch):
    handler = _EmbeddingHandler()
    embedding = _embedding(monkeypatch, handler)
    # 实际用于拼接请求 URL 的 base_url 被改到未批准主机
    embedding.base_url = "http://unapproved.invalid:8002/private"
    item = _classification(purpose="query_embedding", service="embedding:qwen3-test")
    with pytest.raises(Exception):
        embedding.embed(["测试"], kind="query", classification=item)
    assert handler.seen == []


def test_co_r02_approved_endpoint_still_works(monkeypatch):
    handler = _EmbeddingHandler()
    embedding = _embedding(monkeypatch, handler)
    item = _classification(purpose="query_embedding", service="embedding:qwen3-test")
    vectors = embedding.embed(["测试"], kind="query", classification=item)
    assert len(vectors) == 1
    assert handler.seen == ["http://approved.invalid:8001/v1/embeddings"]


# --------------------------------------------------------------------------- 引用完整性


def _hit(content, *, source=None, truncated=False, content_hash=None, version="v1"):
    digest = content_hash or hashlib.sha256((source if source is not None else content).encode("utf-8")).hexdigest()
    return RetrievalHit(chunk_id="D1-abc", document_id="D1", document_version=version, document_title="D1",
                        location="P1", title="t", content=content, content_hash=digest,
                        scope="internal", rule_status="current", sensitivity="synthetic",
                        embedding_profile="p", corpus_id="co", source_content=source,
                        source_length=len(source) if source is not None else None,
                        source_truncated=truncated, score=1.0, bm25_rank=1, retrieval_mode="bm25")


def test_co_r03_document_version_tamper_is_not_verified():
    row = citations([_hit("服务尚未恢复。", source="服务尚未恢复。")])[0]
    assert row["integrity_status"] == "verified"
    tampered = {**row, "document_version": "v2"}
    assert answer_verification_status("服务尚未恢复。[doc:D1:P1]", [], [tampered]) != "verified_excerpt"
    assert answer_verification_status("服务尚未恢复。[doc:D1:P1]", [], [tampered]) == "invalid_evidence"


def test_co_r03_content_hash_tamper_is_not_verified():
    row = citations([_hit("服务尚未恢复。", source="服务尚未恢复。")])[0]
    tampered = {**row, "content_hash": "0" * 64}
    assert answer_verification_status("服务尚未恢复。[doc:D1:P1]", [], [tampered]) == "invalid_evidence"


def test_co_r05_sent_hash_tamper_is_not_verified():
    row = citations([_hit("服务尚未恢复。", source="服务尚未恢复。")])[0]
    tampered = {**row, "sent_hash": "0" * 64}
    assert answer_verification_status("服务尚未恢复。[doc:D1:P1]", [], [tampered]) == "invalid_evidence"


def test_co_r05_source_and_stored_content_mismatch_is_invalid():
    stored = "服务已经恢复。"
    source = "服务尚未恢复。"
    hit = _hit(stored, source=source, content_hash=hashlib.sha256(source.encode("utf-8")).hexdigest())
    row = citations([hit])[0]
    assert row["provenance_status"] == "invalid_provenance"
    assert row["integrity_status"] == "invalid_provenance"
    assert row["verification_status"] == "unverified"
    assert row["excerpts"] == []
    assert answer_verification_status(stored + "[doc:D1:P1]", [], [row]) == "invalid_evidence"


# --------------------------------------------------------------------------- 工具执行回执


def _fact(tool="query_assessment", evidence=EVID, hs=80.0):
    return {"tool_name": tool, "tool_version": "domain-tools-1", "status": "succeeded",
            "evidence_id": evidence,
            "result": {"status": "succeeded", "assessment": {
                "pipe_id": "A", "model_version": "0.1.1",
                "legacy_result": {"Hs": hs, "Rs": 2, "grade": "基本健康"}}}}


def _real_fact(tmp_path, *, run_id):
    """受控执行一次 query_assessment，返回 (事实卡片, 账本, 工具输出)。"""
    access = ToolAccess(principal=BatchPrincipal(actor_id="tester", scope="internal"),
                        snapshot_id="co-snapshot", allowed_tools=frozenset({"query_assessment"}))
    ledger = ExecutionLedger()
    tools = DomainTools(access, lambda _: _snapshot(), InputBatchRepository(tmp_path),
                        ledger=ledger, run_id=run_id)
    output = tools.execute("query_assessment", {"pipe_id": "syn-closeout-001"})
    return fact_cards([output], []), ledger, output


def test_co_r07_unregistered_evidence_id_cannot_verify():
    forged = _fact()
    ledger = ExecutionLedger()
    assert verified_facts([forged], run_id="NEW_RUN", snapshot_id="co-snapshot",
                          principal_id="tester", ledger=ledger) == []
    assert verified_facts([forged]) == []


def test_co_r07_old_run_record_cannot_verify_new_run(tmp_path):
    cards, ledger, _ = _real_fact(tmp_path, run_id="OLD_RUN")
    assert verified_facts(cards, run_id="OLD_RUN", snapshot_id="co-snapshot",
                          principal_id="tester", ledger=ledger)
    # 新 run 使用同一账本：记录所属 run 不同，必须为 0。
    assert verified_facts(cards, run_id="NEW_RUN", snapshot_id="co-snapshot",
                          principal_id="tester", ledger=ledger) == []


def test_co_r07_record_must_match_result_hash(tmp_path):
    cards, ledger, _ = _real_fact(tmp_path, run_id="RUN")
    tampered = []
    for card in cards:
        result = {**card["result"], "assessment": {
            **card["result"]["assessment"],
            "legacy_result": {**card["result"]["assessment"]["legacy_result"], "Hs": 99.0}}}
        tampered.append({**card, "result": result})
    assert verified_facts(tampered, run_id="RUN", snapshot_id="co-snapshot",
                          principal_id="tester", ledger=ledger) == []


def test_co_r07_real_tool_execution_still_verifies(tmp_path):
    app, llm, snap = _graph(tmp_path)
    config = {"configurable": {"thread_id": "co-receipt"}}
    out = app.invoke(_state(run_id="r1", sensitivity="synthetic"), config=config)
    assert out["execution_receipts"], "真实工具执行必须留下当前 run 的回执"
    assert out["verified_facts"], "真实确定性工具结果仍应产生 verified 事实"
    assert all(item["verification_status"] == "verified" for item in out["verified_facts"])
    assert out["verification_status"] == "deterministic_bound"


# --------------------------------------------------------------------------- PostgreSQL 语料隔离


def _pg(dsn: str, prefix: str):
    import psycopg
    from psycopg.conninfo import make_conninfo

    schema = f"{prefix}_{uuid.uuid4().hex}"
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    return admin, make_conninfo(dsn, options=f"-csearch_path={schema},public")


def _pg_chunk(corpus: str, chunk_id: str = "shared-id", content: str = "医院尚未恢复。") -> object:
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return RetrievalHit(chunk_id=chunk_id, document_id="D1", document_version="v1", document_title="D1",
                        location="P1", title="t", content=content, content_hash=digest,
                        scope="internal", rule_status="current", sensitivity="synthetic",
                        embedding_profile="contract-profile", corpus_id=corpus,
                        source_content=content, source_length=len(content), source_truncated=False,
                        score=1.0)


def test_co_r04_same_chunk_id_two_corpora_are_isolated():
    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    admin, scoped = _pg(dsn, "co_corpus")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        assert index.ensure_schema() in {"created", "verified"}
        chunk = _pg_chunk("corpus-A")
        index.upsert(chunk, [1.0, 0.0])
        index.upsert(chunk.model_copy(update={"corpus_id": "corpus-B"}), [0.0, 1.0])
        a = index.search([1.0, 0.0], {chunk.chunk_id}, 5, corpus_id="corpus-A")
        b = index.search([0.0, 1.0], {chunk.chunk_id}, 5, corpus_id="corpus-B")
        assert [hit.corpus_id for hit, _ in a] == ["corpus-A"]
        assert [hit.corpus_id for hit, _ in b] == ["corpus-B"]
        # 更新 A 不得影响 B
        index.upsert(chunk.model_copy(update={"document_version": "v2"}), [1.0, 0.0])
        b_after = index.search([0.0, 1.0], {chunk.chunk_id}, 5, corpus_id="corpus-B")
        assert b_after[0][0].document_version == "v1"
        # prune A 不得删除 B
        index.prune("corpus-A", set())
        assert index.search([0.0, 1.0], {chunk.chunk_id}, 5, corpus_id="corpus-B")
        assert index.search([1.0, 0.0], {chunk.chunk_id}, 5, corpus_id="corpus-A") == []
    finally:
        admin.execute(f'DROP SCHEMA "{scoped.split("search_path=")[1].split(",")[0]}" CASCADE')
        admin.close()


def test_co_r04_natural_chunking_collision_keeps_both_corpora():
    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    doc = KnowledgeDocument(document_id="SharedDoc", path="knowledge/shared.md", title="共享",
                            version="v1", scope="internal", rule_status="current", sensitivity="synthetic")
    text = "# 共享\n医院尚未恢复。"
    a_chunks = chunk_document(doc, text, corpus_id="corpus-A")
    b_chunks = chunk_document(doc, text, corpus_id="corpus-B")
    a_chunks[0].embedding_profile = "contract-profile"
    b_chunks[0].embedding_profile = "contract-profile"
    assert a_chunks[0].chunk_id == b_chunks[0].chunk_id, "自然切片同正文应产生同 chunk_id（用于验证不被覆盖）"
    admin, scoped = _pg(dsn, "co_natural")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        index.upsert(a_chunks[0], [1.0, 0.0])
        index.upsert(b_chunks[0], [0.0, 1.0])
        assert index.search([1.0, 0.0], {a_chunks[0].chunk_id}, 5, corpus_id="corpus-A")
        assert index.search([0.0, 1.0], {b_chunks[0].chunk_id}, 5, corpus_id="corpus-B")
    finally:
        admin.execute(f'DROP SCHEMA "{scoped.split("search_path=")[1].split(",")[0]}" CASCADE')
        admin.close()


def test_co_r04_legacy_table_rows_are_quarantined_not_default():
    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    import psycopg
    from psycopg.conninfo import make_conninfo

    admin, scoped = _pg(dsn, "co_legacy")
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
                embedding) VALUES ('legacy-1','default','D9','v1','P1','t','历史正文',%s,'internal',
                'current','synthetic','contract-profile','[1,0]')""",
                (hashlib.sha256("历史正文".encode("utf-8")).hexdigest(),))
        assert index.ensure_schema() == "migrated_legacy"
        assert index.search([1.0, 0.0], {"legacy-1"}, 5, corpus_id="default") == []
        quarantined = "legacy-unverified:" + index.table
        with pytest.raises(ValueError):
            index.search([1.0, 0.0], {"legacy-1"}, 5, corpus_id=quarantined)
        with pytest.raises(ValueError):
            index.upsert(_pg_chunk(quarantined, chunk_id="legacy-2"), [1.0, 0.0])
    finally:
        admin.execute(f'DROP SCHEMA "{scoped.split("search_path=")[1].split(",")[0]}" CASCADE')
        admin.close()


def test_co_r05_database_tampered_content_is_not_verified():
    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    import psycopg

    admin, scoped = _pg(dsn, "co_tamper")
    try:
        source = "医院尚未恢复。"
        stored = "医院已经恢复。"
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        doc = KnowledgeDocument(document_id="D1", path="knowledge/d1.md", title="D1", version="v1",
                                scope="internal", rule_status="current", sensitivity="synthetic")
        chunks = chunk_document(doc, source, corpus_id="corpus-A")
        chunks[0].embedding_profile = "contract-profile"
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        index.upsert(chunks[0], [1.0, 0.0])
        # 只改存储正文，保留 authority 的版本/scope/哈希与来源
        with psycopg.connect(scoped) as conn:
            conn.execute(f"UPDATE {index.table} SET content = %s WHERE chunk_id = %s AND corpus_id = %s",
                         (stored, chunks[0].chunk_id, "corpus-A"))
        hits = index.search([1.0, 0.0], {chunks[0].chunk_id}, 5, corpus_id="corpus-A")
        assert hits, "篡改行仍可被检索，用于验证下游必须拒绝"
        chunk, score = hits[0]
        row = citations([RetrievalHit(**chunk.model_dump(), score=score, bm25_rank=1)])[0]
        assert row["content_hash"] == digest
        assert row["integrity_status"] == "invalid_provenance"
        assert answer_verification_status(stored + "[doc:D1:P1]", [], [row]) == "invalid_evidence"
    finally:
        admin.execute(f'DROP SCHEMA "{scoped.split("search_path=")[1].split(",")[0]}" CASCADE')
        admin.close()
