"""知识切片：保留来源定位、版本和表格/段落语义。"""
from __future__ import annotations

import hashlib
import re

from src.agents.rag.manifest import KnowledgeDocument
from src.platform.schemas import Contract

HEADING = re.compile(r"^(#{1,6})\s+(.+)$")
LOCATOR = re.compile(r"^\[(P\d{4}|T\d{3}R\d{3})\]")


class KnowledgeChunk(Contract):
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
    embedding_profile: str = "unindexed"
    corpus_id: str = "default"
    source_content: str | None = None
    source_length: int | None = None
    source_truncated: bool = False


def _flush(doc: KnowledgeDocument, title: str, lines: list[str], start: int, end: int,
           corpus_id: str = "default") -> KnowledgeChunk | None:
    content = "\n".join(lines).strip()
    if not content:
        return None
    locator = next((m.group(1) for line in lines if (m := LOCATOR.match(line.strip()))), None)
    location = locator or f"L{start}-{end}"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    stored = content[:6000]
    return KnowledgeChunk(chunk_id=f"{doc.document_id}-{digest[:12]}", document_id=doc.document_id,
                          document_version=doc.version, document_title=doc.title,
                          location=location, title=title or doc.title,
                          content=stored, content_hash=digest, scope=doc.scope,
                          rule_status=doc.rule_status, sensitivity=doc.sensitivity,
                          corpus_id=corpus_id, source_content=content, source_length=len(content),
                          source_truncated=len(content) > len(stored))


def chunk_document(doc: KnowledgeDocument, text: str, corpus_id: str = "default") -> list[KnowledgeChunk]:
    """按标题和段落聚合；每片不超过2400字符，表格行不会被无提示截断。"""
    lines = text.replace("\r\n", "\n").splitlines()
    chunks, title, block, start = [], doc.title, [], 1
    for number, line in enumerate(lines, 1):
        heading = HEADING.match(line)
        starts_paragraph = bool(LOCATOR.match(line.strip()))
        starts_table = line.startswith("|")
        if heading or (not line.strip() and block) or (starts_paragraph and block) or (starts_table and block and len("\n".join(block)) > 1200):
            chunk = _flush(doc, title, block, start, number - 1, corpus_id)
            if chunk:
                chunks.append(chunk)
            block = []
            start = number
        if heading:
            title = heading.group(2).strip()
            continue
        block.append(line)
        if len("\n".join(block)) >= 2400:
            chunk = _flush(doc, title, block, start, number, corpus_id)
            if chunk:
                chunks.append(chunk)
            # 保留后一段重叠，避免定义和解释被硬切断。
            block = block[-3:]
            start = max(start, number - 2)
    chunk = _flush(doc, title, block, start, len(lines), corpus_id)
    if chunk:
        chunks.append(chunk)
    return chunks
