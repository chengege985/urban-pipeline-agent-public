"""R02: answer nodes receive filtered body text, citation IDs, versions and hashes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from langgraph.checkpoint.memory import MemorySaver

from src.agents.graph import build_graph
from src.agents.outbound import RequestClassification
from src.agents.providers.llm import FakeLLM, LangChainLLM
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument
from src.agents.rag.retriever import HybridRetriever, citations
from src.platform.input_batches import InputBatchRepository


def doc(document_id="D1", scope="internal"):
    return KnowledgeDocument(document_id=document_id, path="knowledge/a.md", title="测试资料",
                             version="v1", scope=scope, rule_status="current", sensitivity="public")


def test_model_answer_prompt_contains_body_version_hash_and_id(monkeypatch):
    monkeypatch.setenv("AGENT_ALLOW_NON_SYNTHETIC", "1")

    class Spy:
        def __init__(self):
            self.messages = []

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.messages.append(messages)
            return SimpleNamespace(content="依据正文。[doc:D1:P1]")

    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode = "openai"
    llm.service_id = "test-llm"
    llm.model = Spy()
    citation = citations([type("Hit", (), {
        "chunk_id": "D1-abc", "document_id": "D1", "document_version": "v1",
        "document_title": "测试资料", "location": "P1", "title": "管龄",
        "content": "管龄超过设计寿命80%时风险上升。", "content_hash": "hash-abc",
        "scope": "internal", "rule_status": "current", "sensitivity": "public",
        "embedding_profile": "fake", "score": 1.0, "bm25_rank": 1, "vector_rank": None,
        "retrieval_mode": "bm25",
    })()])[0]
    classification = RequestClassification(
        "tester", "internal", "synthetic", "synthetic", (), ("public",),
        "answer", "test-llm", "test")
    result = llm.answer("管龄规则", [], [citation], [],
                        data_mode="synthetic", classification=classification)
    assert "doc:D1:P1" in result
    prompt = str(llm.model.messages[0])
    assert "管龄超过设计寿命80%时风险上升" in prompt
    assert "version=v1" in prompt and "full_hash=hash-abc" in prompt
    assert "sent_hash=" in prompt and "truncated=False" in prompt


def test_citation_marks_truncation_and_sent_hash():
    content = "甲" * 2500
    hit = SimpleNamespace(
        chunk_id="D1-long", document_id="D1", document_version="v1", document_title="长文",
        location="P1", title="长段落", content=content, content_hash="full-hash",
        scope="internal", rule_status="current", sensitivity="public",
        embedding_profile="fake", score=1.0, bm25_rank=1, vector_rank=None, retrieval_mode="bm25",
    )
    citation = citations([hit])[0]
    assert citation["content_length"] == 2500
    assert citation["sent_length"] == 2000
    assert citation["content_truncated"] is True
    assert citation["content_hash"] == "full-hash"
    assert citation["sent_hash"] != citation["content_hash"]
    assert len(citation["sent_hash"]) == 64
    assert citation["citation_id"].startswith("doc:D1:v1:P1:")


def test_graph_marks_unverified_rag_prose_with_excerpt(tmp_path):
    class Contradiction(FakeLLM):
        def answer(self, question, facts, citations, limitations, *, data_mode="unknown", classification=None):
            return f"限值高于设计寿命。[{citations[0]['evidence_id']}]"

    doc = KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="限值", version="v1",
                            scope="internal", rule_status="current", sensitivity="public")
    retriever = HybridRetriever(chunk_document(doc, "# 限值\n限值低于设计寿命。"))
    graph = build_graph(llm=Contradiction(), retriever=retriever, snapshot_lookup=lambda _: None,
                        batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    state = {"thread_id": "mark", "run_id": "r", "principal": {"actor_id": "tester", "scope": "internal"},
             "question": "限值资料", "snapshot_id": None, "batch_refs": []}
    result = graph.invoke(state, config={"configurable": {"thread_id": "mark"}})
    assert result["answer_mode"] == "llm_with_excerpt"
    assert "未经语义蕴含验证" in result["answer"]
    assert "限值低于设计寿命" in result["answer"]


def test_graph_passes_only_scope_filtered_citations_with_body(tmp_path):
    class Capture(FakeLLM):
        def __init__(self):
            self.seen = None

        def answer(self, question, facts, citations, limitations, *, data_mode="unknown", classification=None):
            self.seen = citations
            return f"依据资料。[{citations[0]['evidence_id']}]" if citations else "无资料"

    internal = chunk_document(doc("D1", "internal"),
                              "# 三方分工\n数字政通负责评价，重庆大学负责预测，重通院负责推演。")
    competition = chunk_document(doc("D2", "competition"),
                                 "# 三方分工\n这是比赛范围的另一份资料，不应进入内部问答。")
    retriever = HybridRetriever(internal + competition)
    llm = Capture()
    graph = build_graph(llm=llm, retriever=retriever, snapshot_lookup=lambda _: None,
                        batches=InputBatchRepository(tmp_path), checkpointer=MemorySaver())
    state = {"thread_id": "rag-context", "run_id": "r", "principal": {"actor_id": "tester", "scope": "internal"},
             "question": "三方分工", "snapshot_id": None, "batch_refs": []}
    result = graph.invoke(state, config={"configurable": {"thread_id": "rag-context"}})
    assert result["status"] == "succeeded"
    assert llm.seen and len(llm.seen) == 1
    citation = llm.seen[0]
    assert citation["scope"] == "internal"
    assert "数字政通负责评价" in citation["content"]
    assert citation["document_version"] == "v1"
    assert len(citation["content_hash"]) == 64


def test_citation_distinguishes_source_stored_and_sent_layers():
    import hashlib
    source = "甲" * 7000
    stored = source[:6000]
    hit = SimpleNamespace(
        chunk_id="D1-long", document_id="D1", document_version="v1", document_title="长文",
        location="P1", title="长段落", content=stored,
        content_hash=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        source_content=source, source_length=len(source), source_truncated=True,
        scope="internal", rule_status="current", sensitivity="public",
        embedding_profile="fake", score=1.0, bm25_rank=1, vector_rank=None, retrieval_mode="bm25",
    )
    citation = citations([hit])[0]
    assert citation["source_length"] == 7000
    assert citation["content_length"] == 6000
    assert citation["sent_length"] == 2000
    assert citation["source_truncated"] is True
    assert citation["content_truncated"] is True
    assert citation["integrity_status"] == "verified"
    assert citation["sent_hash"] != citation["content_hash"]


def test_model_prompt_only_contains_hashed_sent_slice():
    class Capture:
        def __init__(self):
            self.prompts = []

        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            self.prompts.append(messages[0][1])
            return SimpleNamespace(content="依据资料。[doc:D1:P1]")

    content = "甲" * 2000 + "未发送来源尾部"
    citation = {
        "evidence_id": "doc:D1:P1", "citation_id": "doc:D1:v1:P1:abcdef123456",
        "document_version": "v1", "content_hash": "h",
        "sent_hash": __import__("hashlib").sha256(content[:2000].encode("utf-8")).hexdigest(),
        "sent_length": 2000, "content": content[:2000], "snippet": content[:900],
        "excerpts": [{"text": content, "start": 0, "end": len(content), "hash": "h"}],
        "sensitivity": "synthetic", "title": "t", "location": "P1",
        "document_title": "D1", "integrity_status": "verified",
    }
    facts = [{"tool_name": "knowledge_retrieval", "evidence_id": "doc:D1:P1",
              "status": "succeeded", "sensitivity": "synthetic", "result": {"citation": citation}}]
    llm = LangChainLLM.__new__(LangChainLLM)
    llm.mode = "openai"
    llm.service_id = "test-llm"
    llm.model = Capture()
    classification = RequestClassification("tester", "internal", "synthetic", "synthetic",
                                           ("synthetic",), ("synthetic",), "answer", "test-llm", "test")
    llm.answer("资料", facts, [citation], [], classification=classification)
    prompt = llm.model.prompts[0]
    assert content[:2000] in prompt
    assert "未发送来源尾部" not in prompt


def test_tampered_sent_hash_blocks_model_input():
    citation = {
        "evidence_id": "doc:D1:P1", "citation_id": "doc:D1:v1:P1:abc", "document_version": "v1",
        "content_hash": "full", "sent_hash": "0" * 64, "sent_length": 4,
        "content": "正文", "sensitivity": "synthetic", "integrity_status": "verified",
    }
    with pytest.raises(PermissionError):
        from src.agents.outbound import require_outbound, RequestClassification
        require_outbound(RequestClassification("tester", "internal", "synthetic", "synthetic",
                                               (), ("synthetic",), "answer", "test-llm"),
                         purpose="answer", citations=[citation])
