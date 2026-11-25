"""知识语料清单元数据；权限与规则效力分开记录。"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from src.platform.schemas import Contract, ID


class KnowledgeDocument(Contract):
    document_id: ID
    path: str
    title: str
    version: str
    scope: Literal["internal", "competition"]
    rule_status: Literal["current", "draft_reference", "deprecated"]
    sensitivity: Literal["internal", "public", "synthetic"]
    effective_from: AwareDatetime | None = None
    effective_to: AwareDatetime | None = None

    @field_validator("path")
    @classmethod
    def safe_path(cls, value):
        if Path(value).is_absolute() or ".." in Path(value).parts:
            raise ValueError("语料路径必须是工程内相对路径")
        if Path(value).suffix.lower() not in {".md", ".txt"}:
            raise ValueError("首期只索引Markdown或已核验文本")
        return value


class KnowledgeManifest(Contract):
    schema_version: Literal["knowledge-1"] = "knowledge-1"
    documents: list[KnowledgeDocument] = Field(min_length=1)

    @field_validator("documents")
    @classmethod
    def unique_ids(cls, docs):
        if len({d.document_id for d in docs}) != len(docs):
            raise ValueError("document_id不能重复")
        return docs


def load_manifest(root: Path, relative: str = "data/knowledge/manifest.json") -> KnowledgeManifest:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("清单路径超出工程")
    return KnowledgeManifest.model_validate_json(path.read_text(encoding="utf-8"))
