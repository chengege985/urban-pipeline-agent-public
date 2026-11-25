"""R03: in-memory and pgvector indexes share contract, filtering and ordering."""
from __future__ import annotations

import hashlib
import math
import os
import uuid

import pytest

from src.agents.rag.chunking import KnowledgeChunk, chunk_document
from src.agents.rag.manifest import KnowledgeDocument
from src.agents.rag.retriever import FakeEmbedding, HybridRetriever, InMemoryVectorIndex, PgVectorIndex


def documents():
    return [
        KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="内部资料", version="v1",
                          scope="internal", rule_status="current", sensitivity="public"),
        KnowledgeDocument(document_id="D2", path="knowledge/b.md", title="比赛资料", version="v1",
                          scope="competition", rule_status="current", sensitivity="public"),
    ]


def chunks_and_vectors():
    docs = documents()
    chunks = []
    chunks.extend(chunk_document(docs[0], "# 管龄\n管龄超过设计寿命时风险上升。\n# 材质\n铸铁管易腐蚀。"))
    chunks.extend(chunk_document(docs[1], "# 管龄\n比赛资料中的管龄说明。"))
    embedding = FakeEmbedding(8)
    vectors = embedding.embed([c.document_title + "\n" + c.title + "\n" + c.content for c in chunks])
    profile = "contract-profile"
    for chunk in chunks:
        chunk.embedding_profile = profile
    return chunks, vectors, profile


def test_inmemory_filters_allowed_ids_and_profile():
    chunks, vectors, profile = chunks_and_vectors()
    index = InMemoryVectorIndex(chunks, vectors, profile)
    allowed = {c.chunk_id for c in chunks if c.scope == "internal"}
    hits = index.search(vectors[0], allowed, 5, corpus_id="default")
    assert hits and all(chunk.scope == "internal" for chunk, _ in hits)
    assert [score for _, score in hits] == sorted([score for _, score in hits], reverse=True)
    with pytest.raises(ValueError):
        index.search(vectors[0][:-1], allowed, 5, corpus_id="default")
    mismatched = InMemoryVectorIndex(chunks, vectors, "other-profile")
    assert mismatched.search(vectors[0], allowed, 5, corpus_id="default") == []


def make_chunk(chunk_id="c1", content="同一正文", version="v1", scope="internal",
               sensitivity="public", profile="contract-profile", corpus_id="default"):
    return KnowledgeChunk(
        chunk_id=chunk_id, document_id="D1", document_version=version,
        document_title="标题", location="P1", title="段落", content=content,
        content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
        scope=scope, rule_status="current", sensitivity=sensitivity,
        embedding_profile=profile, corpus_id=corpus_id,
    )


def test_cosine_contract_for_non_unit_vectors_and_ties():
    chunks = [make_chunk("c1"), make_chunk("c2", content="另一正文"), make_chunk("c3", content="第三正文")]
    index = InMemoryVectorIndex(chunks, [[1.0, 0.0], [2.0, 0.0], [0.0, 1.0]], "contract-profile")
    hits = index.search([3.0, 0.0], {chunk.chunk_id for chunk in chunks}, 2, corpus_id="default")
    assert [chunk.chunk_id for chunk, _ in hits] == ["c1", "c2"]
    assert all(abs(score - 1.0) < 1e-9 for _, score in hits)
    assert index.search([1.0, 0.0], set(), 2, corpus_id="default") == []
    with pytest.raises(ValueError):
        index.search([1.0, 0.0, 0.0], {chunk.chunk_id for chunk in chunks}, 2, corpus_id="default")
    with pytest.raises(ValueError):
        InMemoryVectorIndex(chunks[:1], [[0.0, 0.0]], "contract-profile")
    with pytest.raises(ValueError):
        InMemoryVectorIndex(chunks[:1], [[math.nan, 1.0]], "contract-profile")


def test_pgvector_updates_version_and_scope_metadata():
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    schema = "agent_vector_update_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema},public")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        base = make_chunk()
        index.upsert(base, [1.0, 0.0])
        updated = base.model_copy(update={
            "document_version": "v2", "scope": "competition", "sensitivity": "synthetic",
            "document_title": "新标题"})
        index.upsert(updated, [1.0, 0.0])
        hits = index.search([1.0, 0.0], {base.chunk_id}, 1, corpus_id="default")
        assert len(hits) == 1
        chunk, score = hits[0]
        assert chunk.document_version == "v2"
        assert chunk.scope == "competition"
        assert chunk.document_title == "新标题"
        assert chunk.content_hash == base.content_hash
        assert abs(score - 1.0) < 1e-9
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_pgvector_ties_use_chunk_id_before_limit():
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    schema = "agent_vector_ties_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema},public")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        chunks = [make_chunk("c3"), make_chunk("c1"), make_chunk("c2")]
        for chunk in chunks:
            index.upsert(chunk, [1.0, 0.0])
        hits = index.search([1.0, 0.0], {chunk.chunk_id for chunk in chunks}, 1, corpus_id="default")
        assert [chunk.chunk_id for chunk, _ in hits] == ["c1"]
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_pgvector_matches_inmemory_contract():
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = __import__("os").getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    chunks, vectors, profile = chunks_and_vectors()
    schema = "agent_vector_test_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema},public")
    try:
        index = PgVectorIndex(scoped, 8, profile=profile)
        index.ensure_schema()
        for chunk, vector in zip(chunks, vectors):
            index.upsert(chunk, vector)
        allowed = {c.chunk_id for c in chunks if c.scope == "internal"}
        memory = InMemoryVectorIndex(chunks, vectors, profile).search(vectors[0], allowed, 3, corpus_id="default")
        database = index.search(vectors[0], allowed, 3, corpus_id="default")
        assert [chunk.chunk_id for chunk, _ in database] == [chunk.chunk_id for chunk, _ in memory]
        assert all(chunk.scope == "internal" for chunk, _ in database)
        for (_, left), (_, right) in zip(memory, database):
            assert abs(left - right) < 1e-6
        non_unit_query = [3.0, 4.0] + [0.0] * (len(vectors[0]) - 2)
        non_unit_memory = InMemoryVectorIndex(chunks, vectors, profile).search(non_unit_query, allowed, 3, corpus_id="default")
        non_unit_database = index.search(non_unit_query, allowed, 3, corpus_id="default")
        assert [chunk.chunk_id for chunk, _ in non_unit_database] == [
            chunk.chunk_id for chunk, _ in non_unit_memory]
        with pytest.raises(ValueError):
            index.search([0.0] * len(vectors[0]), allowed, 3, corpus_id="default")
        assert index.search(vectors[0], set(), 3, corpus_id="default") == []
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_retriever_cache_revokes_old_scope_and_deleted_chunks(tmp_path):
    from src.agents.rag.manifest import KnowledgeManifest
    from src.agents.rag.retriever import build_retriever

    (tmp_path / "knowledge").mkdir()
    (tmp_path / "data/knowledge").mkdir(parents=True)
    (tmp_path / "knowledge/a.md").write_text("# 管龄\n管龄超过设计寿命时风险上升。", encoding="utf-8")
    doc = KnowledgeDocument(document_id="D1", path="knowledge/a.md", title="内部资料", version="v1",
                            scope="competition", rule_status="current", sensitivity="public")
    manifest = KnowledgeManifest(documents=[doc])
    (tmp_path / "data/knowledge/manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")

    retriever, _ = build_retriever(tmp_path, FakeEmbedding(8))
    hits, mode = retriever.search("管龄", scope="competition")
    assert hits and mode in {"hybrid", "bm25"}

    # competition v1 -> internal v2: the old retriever must not serve v1.
    (tmp_path / "knowledge/a.md").write_text("# 管龄\n这是更新后的 v2 正文。", encoding="utf-8")
    updated = doc.model_copy(update={"version": "v2", "scope": "internal"})
    (tmp_path / "data/knowledge/manifest.json").write_text(
        KnowledgeManifest(documents=[updated]).model_dump_json(), encoding="utf-8")
    stale_hits, stale_mode = retriever.search("管龄", scope="competition")
    assert stale_hits == [] and stale_mode == "stale"
    assert retriever.vector_status["mode"] == "stale"

    # Deleted document: old retriever still must not return the deleted body.
    (tmp_path / "knowledge/a.md").unlink()
    deleted_hits, deleted_mode = retriever.search("管龄", scope="competition")
    assert deleted_hits == [] and deleted_mode == "stale"


def test_pgvector_prune_removes_deleted_chunks():
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    schema = "agent_vector_prune_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema},public")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        first, second = make_chunk("c1"), make_chunk("c2", content="另一正文")
        index.upsert(first, [1.0, 0.0])
        index.upsert(second, [1.0, 0.0])
        index.prune("default", {first.chunk_id})
        assert index.search([1.0, 0.0], {second.chunk_id}, 1, corpus_id="default") == []
        assert index.search([1.0, 0.0], {first.chunk_id}, 1, corpus_id="default")[0][0].chunk_id == "c1"
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_pgvector_prune_is_scoped_to_one_corpus():
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    schema = "agent_vector_owner_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema},public")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        a1 = make_chunk("a1", corpus_id="A")
        b1 = make_chunk("b1", content="B正文", corpus_id="B")
        index.upsert(a1, [1.0, 0.0])
        index.upsert(b1, [1.0, 0.0])
        with pytest.raises(ValueError):
            index.prune("", set())
        index.prune("A", set())
        assert index.search([1.0, 0.0], {"a1"}, 1, corpus_id="A") == []
        assert index.search([1.0, 0.0], {"b1"}, 1, corpus_id="B")[0][0].chunk_id == "b1"
        a2 = make_chunk("a2", content="A新正文", corpus_id="A")
        index.upsert(a2, [1.0, 0.0])
        index.prune("A", {"a2"})
        assert index.search([1.0, 0.0], {"b1"}, 1, corpus_id="B")[0][0].chunk_id == "b1"
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()


def test_pgvector_stale_version_candidate_is_not_returned():
    import psycopg
    from psycopg.conninfo import make_conninfo

    dsn = os.getenv("WATER_AGENT_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("需要隔离 PostgreSQL DSN；未配置时不做真实 pgvector 断言")
    schema = "agent_vector_stale_" + uuid.uuid4().hex
    admin = psycopg.connect(dsn, autocommit=True)
    admin.execute(f'CREATE SCHEMA "{schema}"')
    scoped = make_conninfo(dsn, options=f"-csearch_path={schema},public")
    try:
        index = PgVectorIndex(scoped, 2, profile="contract-profile")
        index.ensure_schema()
        old = make_chunk("same-chunk", corpus_id="A", version="v1")
        index.upsert(old, [1.0, 0.0])
        current = old.model_copy(update={"document_version": "v2"})
        embedding = FakeEmbedding(2)
        embedding.profile = "contract-profile"
        retriever = HybridRetriever([current])
        retriever.set_embedding(embedding, index)
        hits, mode = retriever.search("同一正文", scope="internal")
        assert all(hit.document_version == "v2" for hit in hits)
        assert mode in {"bm25", "stale"}
        assert "不一致" in retriever.vector_status["reason"]
    finally:
        admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        admin.close()
