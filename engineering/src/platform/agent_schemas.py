"""Agent API的请求/恢复契约；身份与权限不放在客户端可伪造的字段里。"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from src.platform.schemas import Contract, DecisionRequest, ID, ScenarioRequest


class AgentChatRequest(Contract):
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    snapshot_id: ID | None = None
    pipe_id: ID | None = None
    batch_refs: list[str] = Field(default_factory=list, max_length=8)
    scenario: ScenarioRequest | None = None
    decision: DecisionRequest | None = None
    thread_id: ID | None = None

    @model_validator(mode="after")
    def coherent(self):
        if len(self.batch_refs) != len(set(self.batch_refs)):
            raise ValueError("输入批次引用不能重复")
        if self.decision and not self.scenario:
            raise ValueError("方案比较必须带同一情景")
        if self.scenario and self.scenario.snapshot_id != self.snapshot_id:
            raise ValueError("情景与任务的快照不一致")
        if self.decision and any(getattr(self.decision, field) != getattr(self.scenario, field)
                                 for field in ("snapshot_id", "as_of", "fault_edge_id",
                                               "isolation_plan_id", "horizon_hours")):
            raise ValueError("方案与情景的快照、时点、故障、隔离或分析窗口不一致")
        return self


class AgentClarificationValues(Contract):
    """只允许补充业务输入；身份、工具权限和模型规则不能通过澄清改写。"""
    pipe_id: ID | None = None
    batch_refs: list[str] = Field(default_factory=list, max_length=8)
    scenario: ScenarioRequest | None = None
    decision: DecisionRequest | None = None


class AgentResumeRequest(Contract):
    interrupt_id: ID
    action: Literal["clarify", "accept", "reject", "revise", "cancel"]
    values: dict[str, JsonValue] = Field(default_factory=dict, max_length=20)
    reason: Annotated[str, Field(default="", max_length=2000)] = ""
    expected_revision: Annotated[int, Field(strict=True, ge=0)] = 0

    @model_validator(mode="after")
    def valid_clarification(self):
        if self.action == "clarify":
            AgentClarificationValues.model_validate(self.values)
        elif self.values:
            raise ValueError("只有clarify动作可以提交澄清字段")
        return self


class AgentPrincipalView(Contract):
    actor_id: ID
    scope: Literal["internal", "competition"]
