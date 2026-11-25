"""Trusted request classification and outbound model policy.

Every network exit must carry a classification derived from server context:
principal, actual snapshot/corpus mode, question classification, fact/citation
sensitivity, purpose and target model service. Unknown or conflicting input is
never coerced to synthetic and therefore cannot leave the process.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
import posixpath
from typing import Any
from urllib.parse import urlparse

LEVELS = {"public": 0, "synthetic": 1, "internal": 2, "real": 3}
NAMES = {value: key for key, value in LEVELS.items()}
PURPOSES = {"route", "answer", "document_embedding", "query_embedding"}
UNKNOWN = "unknown"
# 发送片段上限：chunking/retriever 只把前 2000 字符作为 sent 片段，模型消息必须与声明完全一致。
MAX_SENT_CHARS = 2000


@dataclass(frozen=True)
class RequestClassification:
    principal_id: str
    principal_scope: str
    data_mode: str
    question_sensitivity: str
    fact_sensitivities: tuple[str, ...] = ()
    citation_sensitivities: tuple[str, ...] = ()
    purpose: str = "answer"
    model_service: str = "unknown"
    source: str = "unknown"
    endpoint: str = ""

    def _level(self, value: Any) -> int | None:
        return LEVELS.get(str(value)) if value is not None else None

    def levels(self) -> list[int] | None:
        values = [self.data_mode, *self.fact_sensitivities, *self.citation_sensitivities]
        if self.purpose != "document_embedding":
            values.append(self.question_sensitivity)
        mapped = [self._level(value) for value in values]
        if any(value is None for value in mapped):
            return None
        return mapped

    @property
    def effective_sensitivity(self) -> str:
        levels = self.levels()
        return UNKNOWN if levels is None else NAMES[max(levels)]

    def audit(self) -> dict:
        return {
            "principal_id": self.principal_id, "principal_scope": self.principal_scope,
            "data_mode": self.data_mode, "question_sensitivity": self.question_sensitivity,
            "fact_sensitivities": list(self.fact_sensitivities),
            "citation_sensitivities": list(self.citation_sensitivities),
            "purpose": self.purpose, "model_service": self.model_service,
            "endpoint": self.endpoint,
            "effective_sensitivity": self.effective_sensitivity, "source": self.source,
        }


def _attr(principal: Any, name: str, default: str = "") -> str:
    if isinstance(principal, dict):
        return str(principal.get(name) or default)
    return str(getattr(principal, name, None) or default)


def classify_request(principal: Any, question: str, purpose: str, *, snapshot: dict | None = None,
                     facts=(), citations=(), model_service: str = "unknown",
                     question_sensitivity: str | None = None,
                     source: str = "server_context", endpoint: str = "") -> RequestClassification:
    if purpose not in PURPOSES:
        raise ValueError(f"未知模型用途：{purpose}")
    data_mode = ((snapshot or {}).get("payload") or {}).get("data_mode") or UNKNOWN
    if purpose == "document_embedding":
        question_sensitivity = "not_applicable"
    elif question_sensitivity == UNKNOWN:
        question_sensitivity = UNKNOWN
    elif question_sensitivity in LEVELS:
        question_sensitivity = str(question_sensitivity)
    elif question_sensitivity is not None:
        question_sensitivity = "invalid"
    else:
        # Deployment env is not sufficient for free text. Only an explicit
        # server-side classification can make a question sendable.
        question_sensitivity = UNKNOWN
    fact_sensitivities = tuple(str(item.get("sensitivity") or data_mode) for item in facts)
    citation_sensitivities = tuple(str(item.get("sensitivity") or UNKNOWN) for item in citations)
    return RequestClassification(
        principal_id=_attr(principal, "actor_id"),
        principal_scope=_attr(principal, "scope"),
        data_mode=str(data_mode),
        question_sensitivity=question_sensitivity,
        fact_sensitivities=fact_sensitivities,
        citation_sensitivities=citation_sensitivities,
        purpose=purpose,
        model_service=model_service or UNKNOWN,
        source=source,
        endpoint=endpoint,
    )


def classify_documents(documents, *, model_service: str,
                       principal_id: str | None = None, endpoint: str = "") -> RequestClassification:
    """Classify the local corpus for indexing; scope labels alone are not trusted."""
    sensitivities = tuple(sorted({str(getattr(doc, "sensitivity", UNKNOWN)) for doc in documents}))
    if not sensitivities:
        data_mode = UNKNOWN
    elif any(value == "internal" for value in sensitivities):
        data_mode = "internal"
    elif all(value in {"synthetic", "public"} for value in sensitivities):
        data_mode = "synthetic"
    else:
        data_mode = UNKNOWN
    return RequestClassification(
        principal_id=principal_id or os.getenv("AGENT_INDEX_PRINCIPAL", "local-indexer"),
        principal_scope="internal",
        data_mode=data_mode,
        question_sensitivity="not_applicable",
        fact_sensitivities=(),
        citation_sensitivities=sensitivities,
        purpose="document_embedding",
        model_service=model_service or UNKNOWN,
        source="server_index",
        endpoint=endpoint,
    )


def normalize_endpoint(value: str) -> str:
    """Canonical scheme/host/port/path; reject aliases and path traversal."""
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("端点为空或含多余空白")
    if any(char in value for char in ("%", "\\", "\x00", "\r", "\n", "\t")):
        raise ValueError("端点含编码/反斜杠/控制字符")
    parsed = urlparse(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("端点scheme或host非法")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("端点不得含凭据、查询或片段")
    # urlparse 把 /v1;private 的最后一段拆进 params；SDK 却使用原字符串，
    # 只比较 path 会让检查目标与实际请求不一致，因此直接拒绝分号参数。
    if parsed.params or ";" in value:
        raise ValueError("端点含路径参数分隔符")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("端点端口非法") from exc
    raw_path = parsed.path or "/"
    segments = raw_path.split("/")
    if any(segment == ".." for segment in segments):
        raise ValueError("端点路径含上级目录")
    path = posixpath.normpath(raw_path)
    if not path.startswith("/"):
        path = "/" + path
    if path == "//":
        path = "/"
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    default_port = 443 if parsed.scheme.lower() == "https" else 80
    port = port or default_port
    return f"{parsed.scheme.lower()}://{host}:{port}{path}"


def _endpoint_allowed(value: str) -> bool:
    try:
        normalized = normalize_endpoint(value)
        parsed = urlparse(normalized)
    except (TypeError, ValueError):
        return False
    configured = set()
    for item in os.getenv("AGENT_ALLOWED_MODEL_ENDPOINTS", "").split(","):
        if item.strip():
            try:
                configured.add(normalize_endpoint(item.strip()))
            except ValueError:
                continue
    if configured:
        return normalized in configured
    return parsed.hostname in {"127.0.0.1", "localhost", "::1"}


def _check_inputs(classification: RequestClassification, facts, citations) -> None:
    if facts is not None:
        expected = tuple(str(item.get("sensitivity") or classification.data_mode) for item in facts)
        if expected != classification.fact_sensitivities:
            raise PermissionError("工具事实分类与实际输入不一致，禁止外发")
    if citations is not None:
        expected = tuple(str(item.get("sensitivity") or UNKNOWN) for item in citations)
        if expected != classification.citation_sensitivities:
            raise PermissionError("引用分类与实际输入不一致，禁止外发")
        for citation in citations:
            content = citation.get("content")
            sent_hash = citation.get("sent_hash")
            if content is None:
                # 只有不带任何可验证字段的旧式摘要可以只靠 snippet 外发；它不会成为可信摘录。
                if (sent_hash or citation.get("citation_id") or citation.get("content_hash")
                        or citation.get("integrity_status")):
                    raise PermissionError("引用声明了可验证字段但缺少发送正文，禁止外发")
                snippet = citation.get("snippet")
                if not isinstance(snippet, str) or not snippet.strip():
                    raise PermissionError("引用既无发送正文也无摘要，禁止外发")
                if len(snippet) > MAX_SENT_CHARS:
                    raise PermissionError("引用发送正文超过合同上限，禁止外发")
                continue
            if not isinstance(content, str) or not content:
                raise PermissionError("引用缺少可验证的发送正文，禁止外发")
            if len(content) > MAX_SENT_CHARS:
                raise PermissionError("引用发送正文超过合同上限，禁止外发")
            if sent_hash:
                if hashlib.sha256(str(content).encode("utf-8")).hexdigest() != sent_hash:
                    raise PermissionError("引用发送片段哈希不一致，禁止外发")
                if citation.get("sent_length") is not None and int(citation["sent_length"]) != len(str(content)):
                    raise PermissionError("引用发送片段长度不一致，禁止外发")


def require_outbound(classification: RequestClassification | None, *, purpose: str | None = None,
                     model_service: str | None = None, endpoint: str | None = None,
                     facts=None, citations=None) -> None:
    """Raise before any HTTP/model call when classification is not trustworthy."""
    if not isinstance(classification, RequestClassification):
        raise PermissionError("缺少可信请求分类，禁止外发")
    if classification.purpose not in PURPOSES:
        raise PermissionError("模型用途未分类，禁止外发")
    if not classification.principal_id:
        raise PermissionError("缺少可信主体，禁止外发")
    if classification.principal_scope not in {"internal", "competition"}:
        raise PermissionError("主体授权范围未知，禁止外发")
    if classification.principal_scope == "competition" and any(
            classification._level(value) is not None and classification._level(value) >= LEVELS["internal"]
            for value in [classification.data_mode, *classification.fact_sensitivities,
                          *classification.citation_sensitivities, classification.question_sensitivity]):
        raise PermissionError("比赛主体无权外发 internal/real 数据")
    if not classification.model_service or classification.model_service == UNKNOWN:
        raise PermissionError("缺少目标模型服务标识，禁止外发")
    levels = classification.levels()
    if levels is None:
        raise PermissionError("数据分类未知或冲突，禁止外发")
    if purpose is not None and purpose != classification.purpose:
        raise PermissionError("实际调用用途与分类不一致，禁止外发")
    if model_service is not None and model_service != classification.model_service:
        raise PermissionError("实际模型服务与分类不一致，禁止外发")
    if endpoint is not None or classification.endpoint:
        if not classification.endpoint or not endpoint:
            raise PermissionError("分类或实际调用未绑定端点，禁止外发")
        try:
            actual_endpoint = normalize_endpoint(endpoint)
            classified_endpoint = normalize_endpoint(classification.endpoint)
        except (TypeError, ValueError):
            raise PermissionError("端点无法规范化，禁止外发") from None
        if actual_endpoint != classified_endpoint:
            raise PermissionError("实际端点与分类不一致，禁止外发")
        if not _endpoint_allowed(actual_endpoint):
            raise PermissionError("实际模型端点未获允许，禁止外发")
    _check_inputs(classification, facts, citations)
    allowed_services = {
        item.strip() for item in os.getenv("AGENT_ALLOWED_MODEL_SERVICES", "").split(",") if item.strip()
    }
    if allowed_services and classification.model_service not in allowed_services:
        raise PermissionError("目标模型服务不在允许列表，禁止外发")
    explicitly_allowed = (
        os.getenv("AGENT_ALLOW_NON_SYNTHETIC") == "1"
        or os.getenv("WATER_LLM_ALLOW_NON_SYNTHETIC") == "1"
    )
    if not explicitly_allowed and any(level >= LEVELS["internal"] for level in levels):
        raise PermissionError("非合成/非公开数据未获准外发")
