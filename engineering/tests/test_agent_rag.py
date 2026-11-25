"""混合RAG长期回归：版本/权限过滤、切片定位、RRF与引用。"""
from pathlib import Path

import pytest

from src.agents.evidence import deterministic_answer, validate_answer
from src.agents.rag.chunking import chunk_document
from src.agents.rag.manifest import KnowledgeDocument, KnowledgeManifest
from src.agents.rag.retriever import FakeEmbedding, HybridRetriever, build_retriever, citations


def doc(document_id="D1", scope="internal", rule_status="current"):
    return KnowledgeDocument(document_id=document_id, path="knowledge/a.md", title="测试资料",
                             version="v1", scope=scope, rule_status=rule_status, sensitivity="internal")


def test_chunk_keeps_locator_and_heading():
    text = "# 管龄规则\n[P0012] 管龄超过设计寿命80%时风险上升。\n\n## 数据质量\n未知值不能按零处理。"
    chunks = chunk_document(doc(), text)
    assert len(chunks) >= 2
    assert any(c.location == "P0012" for c in chunks)
    assert any(c.title == "数据质量" for c in chunks)
    assert all(c.document_id == "D1" and c.document_version == "v1" for c in chunks)


def test_manifest_rejects_traversal_and_duplicate_ids():
    with pytest.raises(ValueError):
        KnowledgeDocument(document_id="D1", path="../secret.md", title="x", version="1",
                          scope="internal", rule_status="current", sensitivity="internal")
    with pytest.raises(ValueError):
        KnowledgeManifest(documents=[doc("D1"), doc("D1")])


def test_hybrid_search_filters_scope_and_fuses_rrf(tmp_path):
    (tmp_path / "data/knowledge").mkdir(parents=True)
    (tmp_path / "knowledge").mkdir()
    (tmp_path / "knowledge/a.md").write_text("# 管龄\n管龄超过设计寿命时风险上升。\n# 材质\n铸铁管易腐蚀。",
                                             encoding="utf-8")
    (tmp_path / "knowledge/b.md").write_text("# 内部\n管龄内部说明。", encoding="utf-8")
    manifest = KnowledgeManifest(documents=[doc("D1","internal"), doc("D2","competition")])
    (tmp_path / "data/knowledge/manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    # 两个文档路径相同会去重ID；用不同路径重建。
    (tmp_path / "data/knowledge/manifest.json").write_text(KnowledgeManifest(documents=[
        doc("D1", "internal"), doc("D2", "competition").model_copy(update={"path": "knowledge/b.md"})
    ]).model_dump_json(), encoding="utf-8")
    retriever, _ = build_retriever(tmp_path, FakeEmbedding(32))
    hits, mode = retriever.search("管龄 风险", scope="internal", limit=5)
    assert hits and mode == "hybrid"
    assert all(h.scope == "internal" for h in hits)
    assert hits[0].bm25_rank is not None and hits[0].vector_rank is not None
    assert hits[0].score > 0
    refs = citations(hits)
    assert refs[0]["evidence_id"].startswith("doc:D1:")
    assert refs[0]["document_version"] == "v1"


def test_retrieval_without_embedding_is_labeled_bm25(tmp_path):
    retriever = HybridRetriever(chunk_document(doc(), "# 管龄\n管龄超过设计寿命时风险上升。"))
    hits, mode = retriever.search("管龄", scope="internal")
    assert hits and mode == "bm25"
    assert all(h.vector_rank is None for h in hits)


def test_answer_requires_real_evidence_id():
    facts = [{"tool_name": "query_assessment", "status": "succeeded", "evidence_id": "tool:abc",
              "result": {"assessment": {"pipe_id": "p1", "legacy_result": {"Hs": 80, "grade": "基本健康"}}}}]
    assert validate_answer("结论[tool:abc]。", facts, [])[0]
    assert validate_answer("结论[tool:abc]，", facts, [])[0]
    assert not validate_answer("Hs=80 MPa [tool:abc]", facts, [])[0]
    assert not validate_answer("Hs=99 MPa [tool:abc]", facts, [])[0]
    assert not validate_answer("结论[tool:not-exist]。", facts, [])[0]
    assert not validate_answer("没有引用", facts, [])[0]
    text = deterministic_answer("为什么", facts, [], ["仅演示"])
    assert "tool:abc" in text and "80" in text


def test_business_answer_cannot_swap_entity_value_or_unit():
    facts = [{"tool_name": "query_assessment", "status": "succeeded", "evidence_id": "tool:a",
              "result": {"assessment": {"pipe_id": "A", "legacy_result": {"Hs": 42.5, "grade": "a"}}}},
             {"tool_name": "query_assessment", "status": "succeeded", "evidence_id": "tool:b",
              "result": {"assessment": {"pipe_id": "B", "legacy_result": {"Hs": 87.5, "grade": "b"}}}}]
    assert not validate_answer("管段A健康分87.5分。[tool:a]", facts, [])[0]
    assert not validate_answer("管段A健康分42.5 MPa。[tool:a]", facts, [])[0]
    text = deterministic_answer("评分", facts, [], [])
    assert "管段A：Hs=42.5分" in text and "管段B：Hs=87.5分" in text
    assert "Hs=87.5分" not in text.split("管段A：")[1].split("\n")[0]


def test_renderer_distinguishes_registered_zero_and_unknown_values():
    facts = [{"tool_name": "simulate_outage", "status": "partial", "evidence_id": "tool:sim",
              "result": {"affected_service_ids": ["hospital"],
                         "potentially_disconnected_counts": {"person": 0, "household": None}}},
             {"tool_name": "query_assessment", "status": "insufficient_data", "evidence_id": "tool:a",
              "result": {"assessment": {"pipe_id": "A", "legacy_result": {"Hs": None, "Rs": None, "grade": None}}}}]
    answer = deterministic_answer("影响", facts, [], [])
    assert "已登记受影响服务的人计数：0" in answer
    assert "不代表实际零人受影响" in answer
    assert "户计数：未知" in answer
    assert "Hs=未知分" in answer and "None" not in answer
