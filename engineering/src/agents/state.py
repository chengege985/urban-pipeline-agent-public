"""LangGraph主状态与路由结果。

状态只保存引用和已授权上下文，不把原始大表或凭据带入提示词。
"""
from __future__ import annotations

from typing import Literal, TypedDict

from pydantic import Field

from src.platform.schemas import Contract, ID

IntentName = Literal["materials", "assessment", "simulation", "decision", "clarify", "unsupported"]
TaskName = Literal["materials", "assessment", "simulation", "decision"]


class RouteDecision(Contract):
    """模型只能返回允许的任务组合；对象ID仍要由服务端核对快照。"""
    intent: IntentName
    tasks: list[TaskName] = Field(default_factory=list, max_length=4)
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=800)
    pipe_id: ID | None = None


class AgentState(TypedDict, total=False):
    thread_id: str
    run_id: str
    principal: dict
    question: str
    question_sensitivity: str | None
    run_context: dict
    snapshot_id: str | None
    pipe_id: str | None
    entity_resolution: dict
    capability_status: str
    error_kind: str | None
    batch_refs: list[str]
    scenario: dict | None
    decision: dict | None
    allowed_tools: list[str]
    route: dict
    missing: list[str]
    clarification: dict
    tool_results: list[dict]
    execution_receipts: list[dict]
    evidence_refs: list[str]
    citations: list[dict]
    verified_facts: list[dict]
    verification_status: str
    answer: str
    answer_mode: str
    status: str
    phase: str
    approval: dict
    approval_result: dict
    errors: list[str]


class AgentSettings(Contract):
    """从环境注入的Agent配置；不包含任何密钥值。"""
    llm_mode: Literal["fake", "openai"] = "fake"
    llm_base_url: str = ""
    llm_model: str = ""
    embedding_mode: Literal["fake", "openai"] = "fake"
    embedding_base_url: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = Field(default=1024, ge=64, le=2048)
    embedding_query_prefix: str = ""
    embedding_document_prefix: str = ""
    max_tool_calls: int = Field(default=8, ge=1, le=8)
    max_graph_steps: int = Field(default=32, ge=8, le=64)
    request_timeout_seconds: int = Field(default=300, ge=30, le=900)
    model_timeout_seconds: int = Field(default=60, ge=10, le=180)
