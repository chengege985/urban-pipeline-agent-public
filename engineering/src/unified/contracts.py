"""UNIFY-PRODUCT-01 统一合同层。

本模块只做三件事：冻结 DTO、从既有 Run 组装报告、对同一案例的多次结果做一致性断言。
它不计算健康度、拓扑、效用或恢复时间，也不写数据库；所有业务数值都来自
`src/pipe_health`、`src/simulation`、`src/decision` 与既有 Run 结果。

冻结范围与实际状态：

- `SnapshotSummary` / `AssessmentEnvelope` / `CapabilitiesResponse` / `RunView` /
  `FeedbackRecord` 对应已经存在的 HTTP 接口；本轮只补类型，不改路由。
- `SimulationRunResult` / `DecisionRunResult` 对应 `/api/v1/simulation-runs` 与
  `/api/v1/decision-runs` 的 `result` 字段；HTTP 响应本身仍是未声明 schema 的自由对象。
- `ReportDocument` 同时对应 M1-FIX2 已实现的 `GET /api/v1/runs/{run_id}/report`；
  该路由只复用 `build_report_from_run`，不重算业务数值。
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, JsonValue

from src.platform.schemas import Assessment

VERSION = "1.0.0"

ID = Annotated[str, Field(min_length=1, max_length=128)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
DataSourceMode = Literal["fixture_replay", "synthetic", "real"]
RunKind = Literal["assessment", "simulation", "decision", "assistant"]
RunStatus = Literal["queued", "running", "succeeded", "partial", "insufficient_data", "infeasible", "failed"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Open(BaseModel):
    """Run 结果保留未声明的诊断字段；只有核心字段参与前端合同。"""

    model_config = ConfigDict(extra="allow")


class SnapshotCounts(_Strict):
    assessments: int = Field(ge=0)
    nodes: int = Field(ge=0)
    edges: int = Field(ge=0)
    services: int = Field(ge=0)
    plans: int = Field(ge=0)


class SnapshotSummary(_Strict):
    """`GET /api/v1/snapshots` 的元素。不含 payload。"""

    snapshot_id: ID
    sha256: Sha256
    created_at: str
    name: str
    data_mode: DataSourceMode
    source_status: Literal["source_unknown", "constructed", "declared_real"]
    as_of: str
    validation_status: Literal["not_business_validated"] = "not_business_validated"
    counts: SnapshotCounts


class SnapshotListEnvelope(_Strict):
    items: list[SnapshotSummary]


class AssessmentEnvelope(_Strict):
    """`GET /api/v1/assessments`：按快照与管段过滤后的评分行。"""

    snapshot_id: ID
    data_mode: DataSourceMode
    as_of: str
    total: int = Field(ge=0)
    items: list[Assessment]


class AlgorithmSummary(_Strict):
    capability_id: str
    semantics: str
    version: str | None = None


class CapabilityDataset(_Strict):
    """`/api/v1/capabilities` 内的数据集摘要：比 `SnapshotSummary` 少 sha256 与 created_at。"""

    snapshot_id: ID
    name: str
    data_mode: DataSourceMode
    source_status: Literal["source_unknown", "constructed", "declared_real"]
    as_of: str
    counts: SnapshotCounts
    validation_status: Literal["not_business_validated"] = "not_business_validated"


class CapabilitiesResponse(_Strict):
    scoring: str
    simulation: str
    decision: str
    llm_mode: Literal["mock", "openai"]
    formal_scoring: str
    professional_prediction: str
    hydraulic: str
    internal_cuv: str
    real_dispatch: str
    note: str
    platform_version: str
    datasets: list[CapabilityDataset]
    data_modes: list[DataSourceMode]
    algorithms: list[AlgorithmSummary]


class RunView(_Strict):
    """`GET /api/v1/runs/{run_id}` 与 enqueue 响应共用的任务视图。"""

    run_id: ID
    kind: RunKind
    status: RunStatus
    created_at: str
    finished_at: str | None = None
    request: dict[str, JsonValue]
    result: dict[str, JsonValue] | None = None
    review_status: Literal["accepted", "rejected", "revised", "pending_review"] | None = None
    review_revision: int = Field(ge=0)
    execution_status: Literal["not_dispatched"] = "not_dispatched"


class RunListEnvelope(_Strict):
    items: list[RunView]


class AssessmentRunResult(_Open):
    status: Literal["succeeded"]
    snapshot_id: ID
    data_mode: DataSourceMode
    model_family: Literal["legacy_demo"]
    model_version: str
    assessment_count: int = Field(ge=0)
    validation_status: str
    evidence_refs: list[str]
    run_id: ID


class SimulationRunResult(_Open):
    """`/simulation-runs` 的 result：S01 拓扑信封，不含水力或失效概率。"""

    status: Literal["succeeded", "partial", "insufficient_data"]
    engine_type: Literal["topology"]
    algorithm_version: str
    snapshot_id: ID
    as_of: AwareDatetime
    data_mode: DataSourceMode
    source_status: str
    validation_status: str
    affected_service_ids: list[str]
    affected_critical_facility_ids: list[str]
    potentially_disconnected_counts: dict[Literal["household", "person", "facility"], int | None]
    known_count_subtotals: dict[Literal["household", "person", "facility"], int]
    unknown_count_service_ids: list[str]
    newly_unreachable_node_ids: list[str]
    isolated_valve_ids: list[str]
    evidence_refs: list[str]
    limitations: list[str]
    missing_items: list[str]


class DecisionPlanRow(_Open):
    """候选方案行。`status` 为 unknown 类时不得解释为不可行。"""

    plan_id: str
    label: str
    status: Literal["succeeded", "infeasible", "insufficient_data", "unsupported", "invalid_input"]
    utility: FiniteFloat | None = None
    total_cost_fen: int | None = None
    metrics: dict[str, float | None] | None = None
    reasons: list[str] = Field(default_factory=list)
    selection_reason: str | None = None
    baseline_key: str | None = None


class DecisionRunResult(_Open):
    """`/decision-runs` 的 result：只比较给定候选，不生成方案。"""

    status: Literal["succeeded", "infeasible", "insufficient_data", "unsupported", "invalid_input"]
    engine_type: Literal["demo_policy"]
    decision_algorithm_version: str
    snapshot_id: ID
    as_of: AwareDatetime
    data_mode: DataSourceMode
    source_status: str
    validation_status: str
    baseline_key: Sha256
    plans: list[DecisionPlanRow]
    recommended_plan_id: str | None = None
    recommendation_status: Literal["pending_review"] | None = None
    tie_plan_ids: list[str] = Field(default_factory=list)
    ranked_plan_ids: list[str] = Field(default_factory=list)
    missing_items: list[JsonValue] = Field(default_factory=list)
    decision_limitations: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class FeedbackRecord(_Strict):
    run_id: ID
    review_status: Literal["accepted", "rejected", "revised"]
    reviewer: str
    reason_type: Literal["fact", "constraint", "goal", "explanation", "other"]
    reason: str
    expected_revision: int
    feedback_id: ID
    created_at: str
    revision: int = Field(ge=1)
    actor_identity: str
    execution_status: Literal["not_dispatched"]


class FeedbackListEnvelope(_Strict):
    items: list[FeedbackRecord]


class ReportEvidenceRef(_Strict):
    id: str = Field(min_length=1)
    kind: Literal["snapshot", "scenario", "isolation", "plan", "action", "run", "rule", "algorithm", "other"]


class ReportAlgorithmRef(_Strict):
    capability_id: str
    semantics: str
    version: str
    validation_status: str


class ReportAssessmentRef(_Strict):
    """评分原样引用；`recomputed=false` 表示报告未重跑评分。"""

    pipe_id: ID
    model_family: str
    model_version: str
    rs: FiniteFloat | None = None
    hs: FiniteFloat | None = None
    grade: str
    completeness_percent: FiniteFloat | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    recomputed: Literal[False] = False


class ReportDocument(_Strict):
    """`GET /api/v1/runs/{run_id}/report` 的响应体。

    报告只引用既有 Run 与已有快照字段，不重新生成业务分数。
    """

    report_schema_version: Literal["unified-report-1.0"] = "unified-report-1.0"
    report_id: str
    source: Literal["existing_run_only"] = "existing_run_only"
    business_status: str
    run_id: ID
    run_kind: RunKind
    run_status: RunStatus
    dataset: dict[str, JsonValue]
    algorithms: list[ReportAlgorithmRef]
    scenario: dict[str, JsonValue] | None = None
    pipe: ReportAssessmentRef | None = None
    simulation: dict[str, JsonValue] | None = None
    decision: dict[str, JsonValue] | None = None
    timeline: dict[str, JsonValue] | None = None
    feedback: list[FeedbackRecord] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    recomputation: Literal["none"] = "none"


def parse_run_result(run: dict[str, JsonValue]) -> BaseModel | None:
    """按 kind 校验 result；未完成或无结果时返回 None，不补造字段。"""
    result = run.get("result")
    if not isinstance(result, dict):
        return None
    kind = run.get("kind")
    if kind == "simulation":
        return SimulationRunResult.model_validate(result)
    if kind == "decision":
        return DecisionRunResult.model_validate(result)
    if kind == "assessment":
        return AssessmentRunResult.model_validate(result)
    return None
