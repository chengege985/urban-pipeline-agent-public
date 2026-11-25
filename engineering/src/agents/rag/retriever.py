"""混合检索：BM25 + 向量 + RRF，先按语料权限/版本过滤，再生成引用。"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import math
from pathlib import Path
import re
from typing import Protocol

import jieba
from rank_bm25 import BM25Plus

from src.agents.outbound import RequestClassification, classify_documents, require_outbound
from src.agents.rag.chunking import KnowledgeChunk, chunk_document
from src.agents.rag.manifest import KnowledgeDocument, KnowledgeManifest, load_manifest
from src.platform.schemas import Contract

LOGGER = logging.getLogger(__name__)

# 低于门槛的片段不进入引用：向量用余弦相似度，BM25用原始词项得分。
MIN_VECTOR_SCORE = 0.55
MIN_BM25_SCORE = 0.5
COVERAGE_STOPWORDS = {
    "如何", "怎么", "什么", "哪些", "这个", "那个", "可以", "请", "帮我", "看看",
    "一下", "为什么", "是否", "以及", "相关", "进行", "需要", "今天", "现在",
}


LEGACY_CORPUS_PREFIX = "legacy-unverified"


def _require_corpus_scope(corpus_id: str | None) -> str:
    """检索/写入必须带明确语料归属；空值与隔离语料一律拒绝。"""
    value = str(corpus_id or "").strip()
    if not value:
        raise ValueError("缺少可信语料归属")
    if value.startswith(LEGACY_CORPUS_PREFIX):
        raise ValueError("隔离语料不可写入或检索，请先人工确认归属")
    return value


def _normalize(vector: list[float]) -> list[float]:
    values = [float(value) for value in vector]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("向量包含NaN或Infinity")
    norm = math.sqrt(sum(value * value for value in values))
    if norm == 0.0:
        raise ValueError("零向量不能用于余弦检索")
    return [value / norm for value in values]


@dataclass(frozen=True)
class CorpusAuthority:
    """Current corpus identity and scope-to-chunk allowlist."""

    revision: str
    allowed_ids: dict[str, set[str]]
    corpus_id: str = "default"

    @classmethod
    def from_chunks(cls, chunks: list[KnowledgeChunk]) -> "CorpusAuthority":
        corpus_ids = {chunk.corpus_id for chunk in chunks}
        if len(corpus_ids) > 1:
            raise ValueError("一次检索只能绑定一个语料归属")
        corpus_id = next(iter(corpus_ids), "default")
        rows = sorted((chunk.corpus_id, chunk.document_id, chunk.document_version, chunk.scope,
                       chunk.rule_status, chunk.sensitivity, chunk.chunk_id, chunk.content_hash)
                      for chunk in chunks)
        revision = hashlib.sha256(json.dumps(rows, ensure_ascii=False).encode("utf-8")).hexdigest()
        allowed: dict[str, set[str]] = {}
        for chunk in chunks:
            allowed.setdefault(chunk.scope, set()).add(chunk.chunk_id)
        return cls(revision=revision, allowed_ids=allowed, corpus_id=corpus_id)


def _excerpts(content: str) -> list[dict]:
    """Trusted paragraph excerpts with offsets; context is never substring-trimmed."""
    rows = []
    cursor = 0
    for block in content.split("\n\n"):
        text = block.strip()
        if text:
            start = content.find(text, cursor)
            end = start + len(text)
            rows.append({"text": text, "start": start, "end": end,
                         "hash": hashlib.sha256(text.encode("utf-8")).hexdigest()})
            cursor = end
    return rows


def _query_coverage(query: str, text: str) -> float:
    """查询实词在片段中的覆盖率；用于无答案/越域问题不硬凑引用。"""
    terms = [t for t in jieba.lcut(query.lower())
             if len(t) >= 2 and t not in COVERAGE_STOPWORDS and not t.isdigit()]
    if not terms:
        return 0.0
    return sum(1 for term in terms if term in text.lower()) / len(terms)


class EmbeddingProvider(Protocol):
    profile: str
    service_id: str

    def embed(self, texts: list[str], *, kind: str = "document",
              classification: RequestClassification | None = None) -> list[list[float]]: ...


class FakeEmbedding:
    """测试用确定性向量，不冒充真实Embedding；生产必须显式配置openai。"""
    profile = "fake-hash-v1"
    service_id = "fake-embedding"

    def __init__(self, dimensions=64):
        self.dimensions = dimensions

    def embed(self, texts, *, kind="document", classification=None):
        vectors = []
        for text in texts:
            vector = [0.0] * self.dimensions
            for token in jieba.lcut(text.lower()):
                if token.strip():
                    slot = int(hashlib.sha256((kind + ":" + token).encode()).hexdigest()[:8], 16) % self.dimensions
                    vector[slot] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors


class OpenAIEmbedding:
    """OpenAI兼容的Embedding适配；维度由本地MRL截断并重新归一化。

    Qwen3-VL-Embedding 的 SGLang 接口只返回原生 2048 维，不接受 ``dimensions``
    参数。这里不把未验证的截断当作模型原生输出：只在请求维度小于原生维度时
    取前 N 维并重新 L2 归一化，profile 明确记录 ``mrl``。
    """
    def __init__(self, base_url: str, model: str, dimensions: int, query_prefix: str = "",
                 document_prefix: str = "", timeout: float = 60.0, client=None):
        from src.agents.http_guard import build_guarded_client
        self.base_url = base_url.rstrip("/")
        self.model, self.dimensions = model, dimensions
        self.query_prefix, self.document_prefix = query_prefix, document_prefix
        self.profile = f"qwen3-vl-embedding:{model}:{dimensions}:mrl-l2"
        self.service_id = f"embedding:{model}"
        self.endpoint = self.base_url
        self.timeout = timeout
        # 传输层绑定批准端点；注入客户端只复用 transport，不继承可变配置。
        self._client = build_guarded_client(
            allowed_endpoint=self.base_url, timeout=timeout, injected=client)
        self._owns_client = client is None

    @property
    def actual_endpoint(self) -> str:
        return getattr(self._client, "allowed_endpoint", "") or self.endpoint

    def _endpoint(self) -> str:
        return self.base_url if self.base_url.endswith("/embeddings") else self.base_url + "/embeddings"

    def _normalize(self, vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def _call(self, inputs: list[str]) -> list[list[float]]:
        last_error = None
        for attempt in range(2):
            try:
                response = self._client.post(self._endpoint(), json={"model": self.model, "input": inputs})
                response.raise_for_status()
                payload = response.json()
                data = sorted(payload["data"], key=lambda item: item.get("index", 0))
                vectors = [item["embedding"] for item in data]
                if len(vectors) != len(inputs):
                    raise RuntimeError("Embedding返回数量与输入不一致")
                return vectors
            except Exception as exc:  # 暂态网络错误最多重试1次
                last_error = exc
                if attempt == 0:
                    continue
        raise RuntimeError(f"Embedding服务不可用：{last_error}") from last_error

    def embed(self, texts, *, kind="document", classification=None):
        if not texts:
            return []
        require_outbound(
            classification, purpose="query_embedding" if kind == "query" else "document_embedding",
            model_service=self.service_id, endpoint=self.actual_endpoint or None)
        # 活动客户端必须仍是受控客户端且绑定同一批准端点；被替换为真实 httpx 客户端则 0 请求。
        import httpx as _httpx

        from src.agents.http_guard import GuardedHTTPClient, normalize_endpoint
        client = getattr(self, "_client", None)
        if isinstance(client, _httpx.Client) and not isinstance(client, GuardedHTTPClient):
            raise PermissionError("Embedding活动HTTP客户端不是受控客户端，禁止外发")
        if isinstance(client, GuardedHTTPClient) and (
                normalize_endpoint(client.allowed_endpoint) != normalize_endpoint(self.base_url)):
            raise PermissionError("Embedding活动客户端与当前base_url不一致，禁止外发")
        prefix = self.query_prefix if kind == "query" else self.document_prefix
        vectors = self._call([prefix + text for text in texts])
        result = []
        for vector in vectors:
            if len(vector) < self.dimensions:
                raise RuntimeError(f"Embedding维度{len(vector)}小于配置{self.dimensions}")
            result.append(self._normalize(vector[: self.dimensions]))
        return result

    def close(self):
        if self._owns_client:
            self._client.close()


class InMemoryVectorIndex:
    def __init__(self, chunks: list[KnowledgeChunk], vectors: list[list[float]], profile: str):
        if len(chunks) != len(vectors):
            raise ValueError("内存向量数量与切片数量不一致")
        self.chunks, self.profile = chunks, profile
        self.vectors = [_normalize(vector) for vector in vectors]
        self.dimensions = len(self.vectors[0]) if self.vectors else None
        if any(len(vector) != self.dimensions for vector in self.vectors):
            raise ValueError("内存向量维度不一致")

    def search(self, vector: list[float], allowed_ids: set[str], limit: int,
               corpus_id: str | None = None) -> list[tuple[KnowledgeChunk, float]]:
        if not allowed_ids:
            return []
        if corpus_id is None:
            raise ValueError("缺少可信语料归属，拒绝无范围检索")
        _require_corpus_scope(corpus_id)
        query = _normalize(vector)
        if self.dimensions is not None and len(query) != self.dimensions:
            raise ValueError("查询向量维度与内存索引不一致")
        scored = []
        for chunk, candidate in zip(self.chunks, self.vectors):
            if chunk.chunk_id not in allowed_ids:
                continue
            if chunk.corpus_id != corpus_id:
                continue
            if chunk.embedding_profile != self.profile:
                continue
            score = sum(a * b for a, b in zip(query, candidate))
            scored.append((chunk, score))
        return sorted(scored, key=lambda x: (-x[1], x[0].chunk_id))[:limit]


class PgVectorIndex:
    """pgvector检索；维度与embedding profile固定，切换模型必须新索引。"""
    def __init__(self, dsn: str, dimensions: int, profile: str | None = None):
        self.dsn, self.dimensions = dsn, dimensions
        self._profile = profile or f"pgvector:{dimensions}"
        # 不同 profile/维度独立建表，避免 1024 与 2048 向量混写或互相覆盖。
        suffix = hashlib.sha256(self._profile.encode("utf-8")).hexdigest()[:12]
        self.table = f"agent_chunks_{suffix}"

    def ensure_schema(self):
        """建立或安全迁移索引；旧表归属不明时隔离，不自动当作有效语料。

        返回本次实际动作：created / verified / migrated_legacy。
        """
        import psycopg
        from pgvector.psycopg import register_vector
        with psycopg.connect(self.dsn) as conn:
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            register_vector(conn)
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                corpus_id text NOT NULL, chunk_id text NOT NULL,
                document_id text NOT NULL, document_version text NOT NULL,
                document_title text NOT NULL DEFAULT '', location text NOT NULL, title text NOT NULL,
                content text NOT NULL, content_hash text NOT NULL, scope text NOT NULL,
                rule_status text NOT NULL, sensitivity text NOT NULL, embedding_profile text NOT NULL,
                source_content text, source_length integer, source_truncated boolean NOT NULL DEFAULT false,
                embedding vector({self.dimensions}) NOT NULL,
                PRIMARY KEY (corpus_id, chunk_id))""")
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS document_title text NOT NULL DEFAULT ''")
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS source_content text")
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS source_length integer")
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS source_truncated boolean NOT NULL DEFAULT false")
            conn.execute(f"ALTER TABLE {self.table} ADD COLUMN IF NOT EXISTS corpus_id text")
            columns = self._primary_key(conn)
            if columns == ["corpus_id", "chunk_id"]:
                return "verified"
            if columns == ["chunk_id"]:
                self._migrate_legacy(conn)
                return "migrated_legacy"
            raise ValueError("pgvector索引主键结构未知，拒绝自动使用；请先人工重建")

    def _primary_key(self, conn) -> list[str]:
        rows = conn.execute(
            """SELECT a.attname FROM pg_index i
               JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
               WHERE i.indrelid = to_regclass(%s) AND i.indisprimary
               ORDER BY array_position(i.indkey::int2[], a.attnum::int2)""",
            (self.table,)).fetchall()
        return [row[0] for row in rows]

    def _migrate_legacy(self, conn) -> None:
        """旧表只有 chunk_id 主键，历史行归属不可信。

        迁移只做两件事：先把全部旧行标为隔离语料，再把主键改为 (corpus_id, chunk_id)。
        不删除任何行，也不把任何旧行当成 default 语料；必要时由人工确认后重建。"""
        quarantine = "legacy-unverified:" + self.table
        conn.execute(f"UPDATE {self.table} SET corpus_id = %s", (quarantine,))
        conn.execute(f"ALTER TABLE {self.table} ALTER COLUMN corpus_id SET NOT NULL")
        conn.execute(f"ALTER TABLE {self.table} DROP CONSTRAINT IF EXISTS {self.table}_pkey")
        conn.execute(f"ALTER TABLE {self.table} ADD PRIMARY KEY (corpus_id, chunk_id)")

    @staticmethod
    def _require_corpus(corpus_id: str) -> str:
        return _require_corpus_scope(corpus_id)

    def upsert(self, chunk: KnowledgeChunk, vector: list[float]):
        vector = _normalize(vector)
        if len(vector) != self.dimensions:
            raise ValueError("写入向量维度与pgvector索引不一致")
        corpus_id = self._require_corpus(chunk.corpus_id)
        import psycopg
        from pgvector.psycopg import register_vector
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            conn.execute(f"""INSERT INTO {self.table} (chunk_id, corpus_id, document_id, document_version, document_title,
                location, title, content, content_hash, scope, rule_status, sensitivity, embedding_profile,
                source_content, source_length, source_truncated, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (corpus_id, chunk_id) DO UPDATE SET
                document_id=EXCLUDED.document_id,
                document_version=EXCLUDED.document_version, document_title=EXCLUDED.document_title,
                location=EXCLUDED.location, title=EXCLUDED.title, content=EXCLUDED.content,
                content_hash=EXCLUDED.content_hash, scope=EXCLUDED.scope,
                rule_status=EXCLUDED.rule_status, sensitivity=EXCLUDED.sensitivity,
                embedding_profile=EXCLUDED.embedding_profile, source_content=EXCLUDED.source_content,
                source_length=EXCLUDED.source_length, source_truncated=EXCLUDED.source_truncated,
                embedding=EXCLUDED.embedding""",
                (chunk.chunk_id, corpus_id, chunk.document_id, chunk.document_version,
                 chunk.document_title, chunk.location, chunk.title, chunk.content, chunk.content_hash,
                 chunk.scope, chunk.rule_status, chunk.sensitivity, chunk.embedding_profile,
                 chunk.source_content, chunk.source_length, chunk.source_truncated, vector))

    def prune(self, corpus_id: str, valid_ids: set[str]):
        """Delete stale chunks only within one explicit corpus."""
        if not corpus_id or not str(corpus_id).strip():
            raise ValueError("缺少可信语料归属，拒绝删除")
        import psycopg
        with psycopg.connect(self.dsn) as conn:
            if valid_ids:
                conn.execute(f"DELETE FROM {self.table} WHERE corpus_id = %s AND NOT (chunk_id = ANY(%s))",
                             (corpus_id, list(valid_ids)))
            else:
                conn.execute(f"DELETE FROM {self.table} WHERE corpus_id = %s", (corpus_id,))

    def search(self, vector, allowed_ids: set[str], limit: int, corpus_id: str | None = None):
        if not allowed_ids:
            return []
        if corpus_id is None:
            raise ValueError("缺少可信语料归属，拒绝无范围检索")
        corpus_id = _require_corpus_scope(corpus_id)
        query = _normalize(vector)
        if len(query) != self.dimensions:
            raise ValueError("查询向量维度与pgvector索引不一致")
        import psycopg
        from pgvector.psycopg import register_vector
        with psycopg.connect(self.dsn) as conn:
            register_vector(conn)
            where = "chunk_id = ANY(%s) AND embedding_profile = %s AND corpus_id = %s"
            params = [list(allowed_ids), self.profile, corpus_id]
            params.extend([query, limit])
            rows = conn.execute(f"""SELECT chunk_id, corpus_id, document_id, document_version, document_title,
                location, title, content, content_hash, scope, rule_status, sensitivity, embedding_profile,
                source_content, source_length, source_truncated,
                1 - (embedding <=> %s::vector) AS score FROM {self.table}
                WHERE {where}
                ORDER BY embedding <=> %s::vector, chunk_id LIMIT %s""",
                (query, *params)).fetchall()
        hits = [
            (KnowledgeChunk(chunk_id=row[0], corpus_id=row[1], document_id=row[2],
                            document_version=row[3], document_title=row[4], location=row[5],
                            title=row[6], content=row[7], content_hash=row[8], scope=row[9],
                            rule_status=row[10], sensitivity=row[11], embedding_profile=row[12],
                            source_content=row[13], source_length=row[14],
                            source_truncated=bool(row[15])), float(row[16]))
            for row in rows
        ]
        return sorted(hits, key=lambda x: (-x[1], x[0].chunk_id))

    @property
    def profile(self):
        return self._profile


class RetrievalHit(Contract):
    chunk_id: str
    document_id: str
    document_version: str
    document_title: str = ""
    location: str
    title: str
    content: str
    content_hash: str
    scope: str
    rule_status: str
    sensitivity: str
    embedding_profile: str
    corpus_id: str = "default"
    source_content: str | None = None
    source_length: int | None = None
    source_truncated: bool = False
    score: float
    bm25_rank: int | None = None
    vector_rank: int | None = None
    retrieval_mode: str = "hybrid"


class HybridRetriever:
    def __init__(self, chunks: list[KnowledgeChunk], authority: CorpusAuthority | None = None):
        self.chunks = chunks
        self.authority = authority or CorpusAuthority.from_chunks(chunks)
        self.corpus_id = self.authority.corpus_id
        self.authority_provider = None
        # 标题/文档名也是可检索语义；切片正文不重复标题，但索引需要它。
        self.tokens = [list(jieba.cut(c.document_title + " " + c.title + " " + c.content)) for c in chunks]
        # BM25Plus在小语料上仍保持正值权重，避免只有1—2份文档时IDF为0。
        self.bm25 = BM25Plus(self.tokens) if chunks else None
        self.embedding = None
        self.vector_index = None
        self.embedding_service_id = "unknown"
        self.embedding_endpoint = ""
        self.vector_status = {"available": False, "reason": "未配置向量索引", "mode": "bm25"}

    def set_authority_provider(self, provider):
        self.authority_provider = provider
        return self

    def set_embedding(self, provider: EmbeddingProvider, index=None, *,
                      classification: RequestClassification | None = None):
        self.embedding = provider
        self.embedding_service_id = getattr(provider, "service_id", "unknown")
        self.embedding_endpoint = getattr(provider, "endpoint", "")
        for chunk in self.chunks:
            chunk.embedding_profile = provider.profile
        if index is not None:
            # pgvector 已在 build_retriever 中按同一 profile 写入，避免重复全量编码。
            self.vector_index = index
            self.vector_status = {"available": True, "reason": "", "mode": "hybrid"}
            return self
        try:
            vectors = provider.embed(
                [c.document_title + "\n" + c.title + "\n" + c.content for c in self.chunks],
                kind="document", classification=classification,
            ) if self.chunks else []
            self.vector_index = InMemoryVectorIndex(self.chunks, vectors, provider.profile)
            self.vector_status = {"available": True, "reason": "", "mode": "hybrid"}
        except (PermissionError, RuntimeError, ValueError) as exc:
            self.vector_index = None
            self.vector_status = {"available": False, "reason": str(exc)[:300], "mode": "bm25"}
        return self

    def search(self, query: str, *, scope: str, limit: int = 5,
               classification: RequestClassification | None = None) -> tuple[list[RetrievalHit], str]:
        # 无明确 corpus 身份时不进入 BM25/向量，也不返回可引用正文。
        try:
            _require_corpus_scope(self.corpus_id)
        except ValueError:
            self.vector_status = {"available": False, "reason": "缺少可信语料归属", "mode": "none"}
            return [], "unscoped"
        current = self.authority
        if self.authority_provider is not None:
            try:
                current = self.authority_provider()
            except Exception:
                current = None
        if current is None or current.revision != self.authority.revision or current.corpus_id != self.corpus_id:
            self.vector_status = {"available": False, "reason": "语料版本或权限已变化，旧缓存不可用",
                                  "mode": "stale"}
            return [], "stale"
        allowed = set(current.allowed_ids.get(scope, set()))
        if not allowed:
            return [], "none"
        bm25_hits = []
        if self.bm25 is not None:
            scores = self.bm25.get_scores(list(jieba.cut(query)))
            bm25_hits = sorted(((c, float(s)) for c, s in zip(self.chunks, scores) if c.chunk_id in allowed and s > 0),
                               key=lambda x: (-x[1], x[0].chunk_id))[:10]
        coverage = max((_query_coverage(query, c.document_title + " " + c.title + " " + c.content)
                        for c, _ in bm25_hits[:5]), default=0.0)
        vector_hits = []
        stale_candidates = []
        mode = "bm25"
        vector_available = False
        if self.embedding and self.vector_index:
            try:
                vector = self.embedding.embed(
                    [query], kind="query", classification=classification)[0]
                threshold = MIN_VECTOR_SCORE if self.embedding.profile.startswith("qwen3-vl") else -1.0
                raw_vector_hits = self.vector_index.search(
                    vector, allowed, 10, corpus_id=self.corpus_id)
                expected = {chunk.chunk_id: chunk for chunk in self.chunks}
                vector_hits, stale_candidates = [], []
                for chunk, score in raw_vector_hits:
                    current_chunk = expected.get(chunk.chunk_id)
                    if (current_chunk is None
                            or chunk.corpus_id != self.corpus_id
                            or chunk.document_version != current_chunk.document_version
                            or chunk.scope != current_chunk.scope
                            or chunk.sensitivity != current_chunk.sensitivity
                            or chunk.content_hash != current_chunk.content_hash
                            or chunk.rule_status != current_chunk.rule_status):
                        stale_candidates.append(chunk.chunk_id)
                        continue
                    if score >= threshold:
                        vector_hits.append((chunk, score))
                if stale_candidates:
                    self.vector_status = {"available": False, "mode": "bm25",
                                          "reason": "数据库候选版本/语料与当前授权不一致，已跳过"}
                    vector_available = False
                    mode = "bm25"
                else:
                    vector_available = True
                    mode = "hybrid"
                    self.vector_status = {"available": True, "reason": "", "mode": "hybrid"}
            except (PermissionError, RuntimeError, ValueError) as exc:
                # 权限拒绝/服务故障降为同权限BM25；不退回无过滤全文。
                LOGGER.warning("向量检索失败，降级BM25：%s", type(exc).__name__)
                self.vector_status = {"available": False, "reason": str(exc)[:300], "mode": "bm25"}
                mode = "bm25"
        if vector_available and not vector_hits and coverage < 0.5:
            return [], "none"
        if not vector_available and (not bm25_hits or bm25_hits[0][1] < MIN_BM25_SCORE or coverage < 0.34):
            return [], "none"
        if not vector_hits and not bm25_hits:
            if stale_candidates:
                return [], "stale"
            return [], "none"
        fused = {}
        for rank, (chunk, score) in enumerate(bm25_hits, 1):
            fused[chunk.chunk_id] = {**chunk.model_dump(), "score": 0.0, "bm25_rank": rank, "vector_rank": None}
        for rank, (chunk, score) in enumerate(vector_hits, 1):
            item = fused.setdefault(chunk.chunk_id, {**chunk.model_dump(), "score": 0.0,
                                                     "bm25_rank": None, "vector_rank": None})
            item["vector_rank"] = rank
        for item in fused.values():
            item["score"] = sum(1 / (60 + r) for r in (item["bm25_rank"], item["vector_rank"]) if r)
            item["retrieval_mode"] = mode
        hits = [RetrievalHit(**item) for item in sorted(fused.values(), key=lambda x: (-x["score"], x["chunk_id"]))]
        return hits[:limit], mode


def _load_chunks(root: Path, manifest_path: str,
                 corpus_id: str = "default") -> tuple[KnowledgeManifest, list[KnowledgeChunk]]:
    manifest = load_manifest(root, manifest_path)
    chunks = []
    for doc in manifest.documents:
        path = (root / doc.path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            LOGGER.warning("跳过不存在或越界的语料：%s", doc.path)
            continue
        chunks.extend(chunk_document(doc, path.read_text(encoding="utf-8-sig"), corpus_id))
    return manifest, chunks


def build_retriever(root: Path, embedding: EmbeddingProvider | None = None,
                    manifest_path: str = "data/knowledge/manifest.json",
                    vector_dsn: str | None = None,
                    dimensions: int = 1024, authority_provider=None,
                    corpus_id: str | None = None) -> tuple[HybridRetriever, KnowledgeManifest]:
    root = root.resolve()
    corpus_id = corpus_id or f"{root}::{manifest_path}"
    manifest, chunks = _load_chunks(root, manifest_path, corpus_id)
    retriever = HybridRetriever(chunks)
    retriever.set_authority_provider(
        authority_provider or (lambda: CorpusAuthority.from_chunks(
            _load_chunks(root, manifest_path, corpus_id)[1])))
    if embedding:
        classification = classify_documents(
            manifest.documents, model_service=getattr(embedding, "service_id", "unknown"),
            endpoint=getattr(embedding, "endpoint", ""))
        if vector_dsn:
            try:
                vectors = embedding.embed(
                    [c.document_title + "\n" + c.title + "\n" + c.content for c in chunks],
                    kind="document", classification=classification)
                index = PgVectorIndex(vector_dsn, dimensions, profile=embedding.profile)
                index.ensure_schema()
                for chunk, vector in zip(chunks, vectors):
                    chunk.embedding_profile = embedding.profile
                    index.upsert(chunk, vector)
                index.prune(corpus_id, {chunk.chunk_id for chunk in chunks})
                retriever.set_embedding(embedding, index, classification=classification)
            except (PermissionError, RuntimeError, ValueError) as exc:
                retriever.embedding = embedding
                retriever.embedding_service_id = getattr(embedding, "service_id", "unknown")
                retriever.vector_status = {"available": False, "reason": str(exc)[:300], "mode": "bm25"}
        else:
            retriever.set_embedding(embedding, classification=classification)
    return retriever, manifest


def _provenance(hit: RetrievalHit) -> tuple[str, str, str, str, bool]:
    """校验可信源→存储 chunk 关系。

    返回 (状态, 源哈希, 存储哈希, 存储长度, 哈希一致)。注意 ``content_hash`` 是**原文**
    哈希：正文超过 6000 字符时存储的是前缀，因此不能直接用存储哈希与之比较。
    """
    stored_hash = hashlib.sha256(hit.content.encode("utf-8")).hexdigest()
    source = getattr(hit, "source_content", None)
    source_truncated = bool(getattr(hit, "source_truncated", False))
    declared_length = getattr(hit, "source_length", None)
    if source is None:
        hash_ok = (stored_hash == hit.content_hash) if not source_truncated else True
        return "unverified_source", "", stored_hash, str(len(hit.content)), hash_ok
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
    hash_ok = source_hash == hit.content_hash
    actual_truncated = len(source) > len(hit.content)
    # 声明必须与实际一致：未截断声明、错误 source_length 都视为来源不可信。
    length_ok = declared_length is None or (
        isinstance(declared_length, int) and not isinstance(declared_length, bool)
        and declared_length == len(source))
    truncation_ok = source_truncated == actual_truncated
    prefix_ok = hit.content == source[:len(hit.content)]
    if not (hash_ok and prefix_ok and length_ok and truncation_ok):
        return "invalid_provenance", source_hash, stored_hash, str(len(hit.content)), hash_ok
    return "verified", source_hash, stored_hash, str(len(hit.content)), True


def citations(hits: list[RetrievalHit]) -> list[dict]:
    rows = []
    for hit in hits:
        sent = hit.content[:2000]
        source = getattr(hit, "source_content", None)
        source_length = getattr(hit, "source_length", None)
        source_truncated = bool(getattr(hit, "source_truncated", False))
        provenance, source_hash, stored_hash, stored_length, hash_ok = _provenance(hit)
        if provenance == "invalid_provenance":
            integrity = "invalid_content_hash" if not hash_ok else "invalid_provenance"
        elif not hit.content_hash:
            integrity = "unknown"
        elif not hash_ok:
            integrity = "invalid_content_hash"
        elif source is None and source_truncated:
            integrity = "unknown"
        else:
            integrity = "verified"
        trusted = integrity == "verified"
        rows.append({
            "evidence_id": f"doc:{hit.document_id}:{hit.location}",
            "citation_id": f"doc:{hit.document_id}:{hit.document_version}:{hit.location}:{hit.content_hash[:12]}",
            "chunk_id": hit.chunk_id, "corpus_id": getattr(hit, "corpus_id", "default"),
            "document_id": hit.document_id,
            "document_version": hit.document_version, "document_title": hit.document_title,
            "location": hit.location, "title": hit.title,
            "scope": hit.scope, "sensitivity": hit.sensitivity, "rule_status": hit.rule_status,
            "score": hit.score, "content_hash": hit.content_hash,
            "source_hash": source_hash, "source_length": source_length,
            "source_truncated": source_truncated, "provenance_status": provenance,
            "stored_hash": stored_hash, "stored_length": int(stored_length),
            "content_length": len(hit.content), "content_truncated": len(hit.content) > len(sent),
            "sent_truncated": len(hit.content) > len(sent),
            "sent_length": len(sent), "sent_hash": hashlib.sha256(sent.encode("utf-8")).hexdigest(),
            "integrity_status": integrity,
            "verification_status": "verified" if trusted else "unverified",
            "excerpts": _excerpts(sent) if trusted else [],
            "snippet": hit.content[:900], "content": sent,
            "retrieval_mode": hit.retrieval_mode,
        })
    return rows


def citation_text(hits: list[RetrievalHit]) -> str:
    return "\n".join(
        f"[doc:{h.document_id}:{h.location}] version={h.document_version} "
        f"hash={h.content_hash[:12]} {h.content[:1200]}" for h in hits)
