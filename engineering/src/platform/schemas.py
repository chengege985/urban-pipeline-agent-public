"""平台边界的类型化契约。

所有ID按字符串接收，时间必须带时区，数值必须有限；未知数值用None而非0。
这里校验结构与引用关系，业务可计算性由引擎返回insufficient_data。
原评分程序的兼容行为不改写：新输入必须先经过平台门禁。
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, JsonValue,
    field_validator, model_validator,
)

ID = Annotated[str, Field(strict=True, min_length=1, max_length=128, pattern=r"^[^\s/\\]+$")]
NonNegative = Annotated[FiniteFloat, Field(ge=0)]
Positive = Annotated[FiniteFloat, Field(gt=0)]
Mode = Literal["fixture_replay", "synthetic", "real"]


class Contract(BaseModel):
    """拒绝未声明字段，避免调用方误以为额外参数已经生效。"""
    model_config = ConfigDict(extra="forbid", validate_default=True)


class LegacyResult(Contract):
    Rs: Annotated[FiniteFloat, Field(ge=1, le=5)] | None = None
    Hs: Annotated[FiniteFloat, Field(ge=0, le=100)] | None = None
    completeness_percent: Annotated[FiniteFloat, Field(ge=0, le=100)] | None = None
    grade: str


class Assessment(Contract):
    pipe_id: ID
    as_of: AwareDatetime
    model_family: Literal["legacy_demo"] = "legacy_demo"
    model_version: str = "0.1.1"
    legacy_result: LegacyResult
    indicator_scores: dict[str, FiniteFloat] = Field(default_factory=dict)
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    monitoring: dict[str, JsonValue] = Field(default_factory=dict)
    main_risk: str = ""
    data_issues: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(min_length=1)


class Node(Contract):
    node_id: ID
    label: str = ""
    source: bool = False
    active: bool | None
    boundary: bool = False
    boundary_confirmed: bool = False
    # Optional display-only layout coordinates. Never interpret these as GIS.
    x: FiniteFloat | None = None
    y: FiniteFloat | None = None


class Edge(Contract):
    edge_id: ID
    pipe_id: ID
    from_node: ID
    to_node: ID
    direction: Literal["both", "forward", "reverse", "unknown"] = "unknown"
    state: Literal["open", "closed", "unknown"] = "unknown"
    is_valve: bool = False
    operable: bool | None = None


class Service(Contract):
    service_id: ID
    label: str
    node_ids: list[ID] = Field(min_length=1)
    supply_rule: Literal["any", "all"]
    count_value: Annotated[int, Field(strict=True, ge=0)] | None = None
    count_unit: Literal["household", "person", "facility"]
    critical: bool = False


class IsolationPlan(Contract):
    plan_id: ID
    fault_edge_ids: list[ID] = Field(min_length=1)
    valve_ids: list[ID] = Field(default_factory=list)
    evidence_ref: str


class Action(Contract):
    """措施改变明确的网络状态，备用供水单独评价，不从图断连数中直接减去。"""
    action_id: ID
    label: str
    fault_edge_ids: list[ID] = Field(min_length=1)
    cost_fen: Annotated[int, Field(strict=True, ge=0)] | None = None
    effective_after_hours: NonNegative | None = None
    evidence_ref: str | None = None
    restore_edge_ids: list[ID] = Field(default_factory=list)
    open_valve_ids: list[ID] = Field(default_factory=list)
    close_valve_ids: list[ID] = Field(default_factory=list)
    resource_requirements: dict[ID, NonNegative] | None = None
    requires_ids: list[ID] = Field(default_factory=list)
    conflicts_with: list[ID] = Field(default_factory=list)
    backup_service_ids: list[ID] = Field(default_factory=list)
    backup_capacity_m3h: NonNegative | None = None
    backup_demand_m3h: Positive | None = None
    backup_duration_hours: NonNegative | None = None


class OfflineEffect(Contract):
    """离线专业情景随所属快照冻结；事件、隔离和窗口必须与当前任务一致。"""
    as_of: AwareDatetime
    fault_edge_id: ID
    isolation_plan_id: ID
    horizon_hours: Positive
    evidence_ref: Annotated[str, Field(min_length=1)]
    remaining_disconnected_service_ids: list[ID]
    restoration_hours: NonNegative | None = None
    backup_covered_critical_service_ids: list[ID] = Field(default_factory=list)


class ActionPlan(Contract):
    plan_id: ID
    label: str
    action_ids: list[ID] = Field(min_length=1, max_length=10)
    offline_effect: OfflineEffect | None = None

    @field_validator("action_ids")
    @classmethod
    def unique_actions(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("同一方案不能重复计入措施")
        return values


METRICS = Literal[
    "remaining_disconnected_services", "total_cost_fen", "restoration_hours",
    "backup_covered_critical_services",
]


class Criterion(Contract):
    metric: METRICS
    direction: Literal["min", "max"]
    lower: FiniteFloat
    upper: FiniteFloat
    weight: Positive

    @model_validator(mode="after")
    def bounds(self):
        if self.upper <= self.lower:
            raise ValueError("效用映射上界必须大于下界")
        return self


class ValueProfile(Contract):
    profile_id: ID
    version: str
    policy_type: Literal["demo_policy"] = "demo_policy"
    approval_status: Literal["demo_only"] = "demo_only"
    criteria: list[Criterion] = Field(min_length=1, max_length=4)
    evidence_ref: str

    @model_validator(mode="after")
    def weights(self):
        if len({c.metric for c in self.criteria}) != len(self.criteria):
            raise ValueError("效用指标不能重复")
        if not math.isclose(sum(c.weight for c in self.criteria), 1, abs_tol=1e-9):
            raise ValueError("权重之和必须为1")
        return self


class SnapshotPayload(Contract):
    """一个数据空间的一次冻结快照；禁止把提供方500条样例与虚构网络拼接。"""
    schema_version: Literal["0.1.0"] = "0.1.0"
    name: Annotated[str, Field(min_length=1, max_length=120)]
    data_mode: Mode
    source_status: Literal["source_unknown", "constructed", "declared_real"]
    as_of: AwareDatetime
    source_refs: list[str] = Field(min_length=1, max_length=20)
    coordinate_system: Literal["schematic", "wgs84", "local"] | None = None
    assessments: list[Assessment] = Field(default_factory=list, max_length=10000)
    nodes: list[Node] = Field(default_factory=list, max_length=5000)
    edges: list[Edge] = Field(default_factory=list, max_length=10000)
    services: list[Service] = Field(default_factory=list, max_length=10000)
    isolation_plans: list[IsolationPlan] = Field(default_factory=list, max_length=30)
    actions: list[Action] = Field(default_factory=list, max_length=30)
    plans: list[ActionPlan] = Field(default_factory=list, max_length=3)
    value_profile: ValueProfile | None = None

    @model_validator(mode="after")
    def references(self):
        expected = {"fixture_replay": "source_unknown", "synthetic": "constructed", "real": "declared_real"}
        if self.source_status != expected[self.data_mode]:
            raise ValueError("数据模式与来源状态不一致")
        if self.data_mode == "fixture_replay" and (self.nodes or self.edges or self.actions or self.services):
            raise ValueError("提供方评分样例不能附加未经核实的网络、服务或措施")
        groups = [(self.assessments, "pipe_id"), (self.nodes, "node_id"), (self.edges, "edge_id"),
                  (self.services, "service_id"), (self.isolation_plans, "plan_id"),
                  (self.actions, "action_id"), (self.plans, "plan_id")]
        for items, key in groups:
            ids = [getattr(x, key) for x in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"{key}存在重复，禁止覆盖")
            if self.data_mode == "synthetic" and any(not x.startswith("syn-") for x in ids):
                raise ValueError("合成对象ID必须使用syn-前缀")
        if any(a.as_of != self.as_of for a in self.assessments):
            raise ValueError("评分与快照评价时点不一致")
        nodes, edges = {n.node_id for n in self.nodes}, {e.edge_id: e for e in self.edges}
        services, actions = {s.service_id for s in self.services}, {a.action_id for a in self.actions}
        for e in self.edges:
            if self.data_mode == "synthetic" and not e.pipe_id.startswith("syn-"):
                raise ValueError("合成计算边不能挂接来源未确认的真实管段ID")
            if e.from_node not in nodes or e.to_node not in nodes:
                raise ValueError(f"边{e.edge_id}存在悬空端点")
        for s in self.services:
            if not set(s.node_ids) <= nodes or len(s.node_ids) != len(set(s.node_ids)):
                raise ValueError(f"服务{s.service_id}节点引用无效或重复")
        for p in self.isolation_plans:
            if not set(p.fault_edge_ids) <= edges.keys():
                raise ValueError(f"隔离方案{p.plan_id}引用未知故障边")
            if any(v not in edges or not edges[v].is_valve for v in p.valve_ids):
                raise ValueError(f"隔离方案{p.plan_id}引用非阀门边")
        for a in self.actions:
            if not set(a.fault_edge_ids + a.restore_edge_ids) <= edges.keys():
                raise ValueError(f"措施{a.action_id}引用未知边")
            if any(v not in edges or not edges[v].is_valve for v in a.open_valve_ids + a.close_valve_ids):
                raise ValueError(f"措施{a.action_id}引用非阀门边")
            if not set(a.backup_service_ids) <= services:
                raise ValueError(f"措施{a.action_id}引用未知服务")
            if not set(a.requires_ids + a.conflicts_with) <= actions:
                raise ValueError(f"措施{a.action_id}引用未知依赖措施")
        for p in self.plans:
            if not set(p.action_ids) <= actions:
                raise ValueError(f"方案{p.plan_id}引用未知措施")
            if p.offline_effect:
                effect = p.offline_effect
                if (effect.as_of != self.as_of or effect.fault_edge_id not in edges
                        or effect.isolation_plan_id not in {p.plan_id for p in self.isolation_plans}):
                    raise ValueError("离线情景的时点、故障对象或隔离方案与快照不一致")
                for ids in (effect.remaining_disconnected_service_ids, effect.backup_covered_critical_service_ids):
                    if not set(ids) <= services or len(ids) != len(set(ids)):
                        raise ValueError("离线情景服务对象重复或不属于该快照")
        if not self.assessments and not self.nodes:
            raise ValueError("快照至少包含评分或网络")
        has_coordinates = any(node.x is not None or node.y is not None for node in self.nodes)
        if has_coordinates and self.coordinate_system is None:
            raise ValueError("提供节点坐标时必须声明coordinate_system")
        if self.coordinate_system == "wgs84" and any(
                node.x is not None and not -180 <= node.x <= 180 or
                node.y is not None and not -90 <= node.y <= 90 for node in self.nodes):
            raise ValueError("经纬度坐标超出允许范围")
        return self


class ScenarioRequest(Contract):
    snapshot_id: ID
    as_of: AwareDatetime
    fault_edge_id: ID
    isolation_plan_id: ID
    horizon_hours: Annotated[FiniteFloat, Field(gt=0, le=720)] = 8


class DecisionRequest(ScenarioRequest):
    plan_ids: list[ID] = Field(min_length=2, max_length=3)
    budget_fen: Annotated[int, Field(strict=True, ge=0)]
    resource_capacities: dict[ID, NonNegative] = Field(default_factory=dict)
    mandatory_action_ids: list[ID] = Field(default_factory=list)

    @field_validator("plan_ids")
    @classmethod
    def no_duplicate_plans(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("候选方案不能重复")
        return values


class RecomputeRequest(Contract):
    """受控重算仅接收明确列名的内存记录，不接受文件路径或执行代码。"""
    name: str = "受控评分重算"
    as_of: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    static_rows: list[dict[str, JsonValue]] = Field(min_length=1, max_length=2000)
    dynamic_rows: list[dict[str, JsonValue]] = Field(min_length=1, max_length=50000)
    data_mode: Mode
    source_refs: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def window(self):
        if self.window_start >= self.window_end or self.window_end > self.as_of:
            raise ValueError("统计窗必须满足start < end <= as_of，采用[start,end)")
        return self


class AssistantRequest(Contract):
    snapshot_id: ID
    question: Annotated[str, Field(min_length=1, max_length=4000)]
    as_of: AwareDatetime | None = None
    pipe_id: ID | None = None
    fault_edge_id: ID | None = None
    isolation_plan_id: ID | None = None
    plan_ids: list[ID] = Field(default_factory=list, max_length=3)
    budget_fen: Annotated[int, Field(strict=True, ge=0)] | None = None
    resource_capacities: dict[ID, NonNegative] = Field(default_factory=dict)
    horizon_hours: Annotated[FiniteFloat, Field(gt=0, le=720)] = 8
    mandatory_action_ids: list[ID] = Field(default_factory=list)


class Intent(Contract):
    task_type: Literal["assessment", "simulation", "decision", "materials", "unsupported", "clarify"]
    pipe_id: ID | None = None
    fault_edge_id: ID | None = None


class FeedbackRequest(Contract):
    run_id: ID
    review_status: Literal["accepted", "rejected", "revised"]
    reviewer: Annotated[str, Field(min_length=1, max_length=80)]
    reason_type: Literal["fact", "constraint", "goal", "explanation", "other"]
    reason: Annotated[str, Field(min_length=1, max_length=2000)]
    expected_revision: Annotated[int, Field(strict=True, ge=0)] = 0
