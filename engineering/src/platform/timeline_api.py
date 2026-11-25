"""M1-A timeline preflight and evaluation.

Both endpoints call the existing deterministic functions. They never persist a
snapshot, dispatch maintenance, or reinterpret a topological result as hydraulic
or water-quality recovery. A successful HTTP call does not make the result a
business-validated conclusion.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .api_models import CLIENT_HEADER, COMMON_ERRORS, OPTIONAL_SECURITY, WATER_TOKEN_SCHEME

from src.capabilities import REGISTRY, check_calculation_capability
from src.decision.timeline import build_plan_service_timeline

from .authz import authorize_snapshot, resolve_principal

CAPABILITY_ID = "plan_service_timeline"
SEMANTICS = REGISTRY[CAPABILITY_ID]["semantics"]
ALGORITHM_VERSION = REGISTRY[CAPABILITY_ID]["version"]
REQUESTED_OUTPUTS = ["timeline_request", "recovery_times"]
CHECK_LIMITATIONS = [
    "预检只校验能力、输入与输出可用性；成功不代表方案满足预算、资源或现场执行条件。",
    "图通路恢复不是水力、水质、实际供水量或真实工单结论。",
]
EVALUATION_LIMITATIONS = [
    "计划图通路回放，不代表水力、水质、实际供水量或现场执行。",
    "方案完成时间与服务恢复时间分别计算；缺少任务结束记录时不得补造完成时间。",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _hours(value: Any, *, positive: bool = False) -> float:
    """Reject bool/NaN/Infinity before Pydantic can coerce them."""
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为小时数")
    if not isinstance(value, (int, float)):
        raise ValueError("小时数必须是数字")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("小时数必须是有限数值")
    if number < 0:
        raise ValueError("小时数不能为负")
    if positive and number == 0:
        raise ValueError("小时数必须大于0")
    return number


class TimelineSchedule(_Strict):
    action_id: str = Field(min_length=1)
    finish_offset_hours: float | None = Field(default=None, ge=0)
    start_offset_hours: float | None = Field(default=None, ge=0)
    duration_hours: float | None = Field(default=None, ge=0)

    @field_validator("finish_offset_hours", "start_offset_hours", "duration_hours", mode="before")
    @classmethod
    def strict_hours(cls, value):
        return None if value is None else _hours(value)


class TimelineRequest(_Strict):
    snapshot_id: str = Field(min_length=1)
    as_of: str | None = None
    fault_edge_id: str = Field(min_length=1)
    isolation_plan_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    target_service_ids: list[str]
    horizon_hours: float = Field(default=8, gt=0, le=720,
                                 description="观察窗口，单位小时；拒绝布尔值、NaN 与 Infinity")
    action_schedule: list[TimelineSchedule] = Field(default_factory=list)

    @field_validator("target_service_ids")
    @classmethod
    def unique_targets(cls, values: list[str]) -> list[str]:
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("目标服务ID必须是非空字符串")
        if len(values) != len(set(values)):
            raise ValueError("目标服务ID不能重复")
        return values

    @field_validator("horizon_hours", mode="before")
    @classmethod
    def strict_horizon(cls, value):
        return _hours(value, positive=True)

    @field_validator("as_of")
    @classmethod
    def aware_as_of(cls, value: str | None) -> str | None:
        if value is None:
            return value
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("as_of 必须是 ISO 8601 时间") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("as_of 必须带时区")
        return value


class AvailabilityEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    at_hours: float
    changes: dict[str, bool]


class GraphEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    at_hours: float
    action_ids: list[str] = Field(default_factory=list)
    restored_edge_ids: list[str] = Field(default_factory=list)
    valve_overrides: dict[str, str] = Field(default_factory=dict)
    service_availability: dict[str, bool] = Field(default_factory=dict)


class TimelineSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    as_of: str
    horizon_hours: float
    plan_id: str
    action_ids: list[str] = Field(default_factory=list)
    action_schedule: list[TimelineSchedule] = Field(default_factory=list)
    target_service_ids: list[str] = Field(default_factory=list)
    time_basis: str
    criterion_id: str
    criterion_version: str
    initial_availability: dict[str, bool | None] = Field(default_factory=dict)
    availability_events: list[AvailabilityEvent] = Field(default_factory=list)
    timeline_complete_until_hours: float | None = None
    event_evidence_refs: list[str] = Field(default_factory=list)


class TimeMetric(BaseModel):
    model_config = ConfigDict(extra="allow")

    value_hours: float | None = Field(
        default=None, description="小时，相对请求的 as_of；null 表示缺数或未在窗口内恢复")
    status: str
    reason: str = ""
    criterion_id: str | None = None
    time_basis: str | None = None
    observation_horizon_hours: float | None = None
    beyond_horizon: bool | None = None
    lower_bound_hours: float | None = None
    lower_bound_exclusive: bool | None = None


class RecoveryTimes(BaseModel):
    model_config = ConfigDict(extra="allow")

    status: str
    algorithm_version: str | None = None
    plan_completion_hours: TimeMetric | None = None
    service_first_restored_hours: dict[str, TimeMetric] = Field(default_factory=dict)
    first_target_service_restored_hours: TimeMetric | None = None
    all_target_services_restored_hours: TimeMetric | None = None


class TimelineResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    algorithm_version: str
    snapshot_id: str | None = None
    plan_id: str | None = None
    data_mode: str | None = None
    source_status: str | None = None
    time_basis: str | None = None
    scenario: dict[str, Any] | None = None
    timeline_scope: str | None = None
    criterion_id: str | None = None
    criterion_version: str | None = None
    timeline_status: str | None = None
    timeline_request: TimelineSnapshotRequest | None = None
    recovery_times: RecoveryTimes | None = None
    pre_action_availability: dict[str, bool | None] | None = None
    graph_events: list[GraphEvent] = Field(default_factory=list)
    ignored_topological_action_ids: list[str] = Field(default_factory=list)
    beyond_horizon_action_ids: list[str] = Field(default_factory=list)
    deferred_validation_issues: list[str] = Field(default_factory=list)
    plan_feasibility_checked: bool | None = None
    issues: list[Any] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    validation_status: str | None = None


class CapabilityCheckResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capability_id: str | None = None
    status: str
    can_compute: bool = Field(
        description="原有字段：仅当请求的全部输出完整可得时为 true；"
                    "false 不代表所有指标都不可计算，请读取 field_availability。")
    calculation_status: str | None = None
    supported_outputs: list[str] = Field(default_factory=list)
    unsupported_outputs: list[str] = Field(default_factory=list)
    available_outputs: list[str] = Field(default_factory=list)
    unavailable_outputs: list[str] = Field(default_factory=list)
    available_alternative: str | None = None
    missing_items: list[Any] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    registry_version: str
    algorithm_version: str | None = None
    requested_semantics: str | None = None
    validation_status: str = "not_business_validated"
    authorization_checked: bool = False
    validation_mode: str | None = None


class DatasetReference(BaseModel):
    dataset_id: str
    snapshot_id: str
    data_mode: str
    source_status: str
    as_of: str
    data_version: str
    schema_version: str


class AlgorithmReference(BaseModel):
    capability_id: str
    semantics: str
    version: str
    recovery_summary_version: str | None = None
    criterion_id: str | None = None
    criterion_version: str | None = None
    validation_status: str


class FieldAvailability(BaseModel):
    """Per-output availability; do not collapse into a single can_compute flag."""

    field: str
    status: Literal["available", "partial", "missing", "unsupported", "not_applicable", "not_checked"]
    in_scope: bool = True
    missing_items: list[Any] = Field(default_factory=list)
    unsupported_reason: str | None = None


class TimelineCheckResponse(BaseModel):
    status: str
    dataset: DatasetReference
    algorithm: AlgorithmReference
    result: CapabilityCheckResult
    field_availability: list[FieldAvailability]
    has_any_calculable_output: bool = Field(
        description="至少一个本次范围内的指标有确定结果（含显式不适用）。")
    all_required_outputs_calculable: bool = Field(
        description="本次范围内的全部指标都有完整结果；与 can_compute 的完整可得语义对齐。")
    evidence_ids: list[str]
    missing_items: list[Any]
    limitations: list[str]


class TimelineEvaluationResponse(BaseModel):
    status: str
    dataset: DatasetReference
    algorithm: AlgorithmReference
    result: TimelineResult
    evidence_ids: list[str]
    missing_items: list[Any]
    limitations: list[str]


def _dataset_reference(snapshot: dict[str, Any]) -> DatasetReference:
    payload = snapshot["payload"]
    return DatasetReference(
        dataset_id=snapshot["snapshot_id"],
        snapshot_id=snapshot["snapshot_id"],
        data_mode=snapshot["data_mode"],
        source_status=snapshot["source_status"],
        as_of=snapshot["as_of"],
        data_version=snapshot["sha256"],
        schema_version=payload.get("schema_version") or "0.1.0",
    )


def _algorithm_reference(result: dict[str, Any]) -> AlgorithmReference:
    recovery = result.get("recovery_times") or {}
    return AlgorithmReference(
        capability_id=CAPABILITY_ID,
        semantics=SEMANTICS,
        version=result.get("algorithm_version") or ALGORITHM_VERSION,
        recovery_summary_version=recovery.get("algorithm_version"),
        criterion_id=result.get("criterion_id") or "topological_reachability",
        criterion_version=result.get("criterion_version") or "1.0",
        validation_status=result.get("validation_status") or "not_business_validated",
    )


def _scenario(request: TimelineRequest, snapshot: dict[str, Any]) -> dict[str, Any]:
    as_of = request.as_of or snapshot["as_of"]
    try:
        requested = datetime.fromisoformat(as_of)
        actual = datetime.fromisoformat(snapshot["as_of"])
    except ValueError as exc:
        raise HTTPException(422, "as_of 必须是带时区的 ISO 8601 时间") from exc
    if requested.tzinfo is None or actual.tzinfo is None:
        raise HTTPException(422, "as_of 必须带时区")
    if requested.astimezone(timezone.utc) != actual.astimezone(timezone.utc):
        raise HTTPException(422, "as_of 与快照评价时点不一致")
    return {
        "snapshot_id": request.snapshot_id,
        "as_of": as_of,
        "fault_edge_id": request.fault_edge_id,
        "isolation_plan_id": request.isolation_plan_id,
        "horizon_hours": request.horizon_hours,
    }


def _schedule(request: TimelineRequest) -> list[dict[str, Any]]:
    return [entry.model_dump(exclude_none=True) for entry in request.action_schedule]


def _base_evidence(snapshot: dict[str, Any], request: TimelineRequest) -> list[str]:
    return [
        "snapshot:" + snapshot["snapshot_id"],
        "scenario:" + request.fault_edge_id,
        "plan:" + request.plan_id,
        "isolation:" + request.isolation_plan_id,
    ]


def _evidence(snapshot: dict[str, Any], request: TimelineRequest, result: dict[str, Any]) -> list[str]:
    refs = _base_evidence(snapshot, request)
    timeline = result.get("timeline_request") or {}
    refs.extend(timeline.get("event_evidence_refs") or [])
    return list(dict.fromkeys(refs))


def _missing(result: dict[str, Any]) -> list[Any]:
    values: list[Any] = []
    for key in ("issues", "deferred_validation_issues"):
        item = result.get(key)
        if isinstance(item, list):
            values.extend(item)
    return values


CHECK_FIELDS = (
    "capability_id", "registry_version", "status", "can_compute", "calculation_status",
    "supported_outputs", "unsupported_outputs", "available_outputs", "unavailable_outputs",
    "available_alternative", "missing_items", "evidence_refs", "validation_status",
    "authorization_checked", "algorithm_version", "requested_semantics", "validation_mode",
)
RESULT_FIELDS = (
    "status", "algorithm_version", "criterion_id", "criterion_version", "timeline_status",
    "snapshot_id", "plan_id", "data_mode", "source_status", "time_basis", "scenario",
    "timeline_scope", "timeline_request", "recovery_times", "pre_action_availability",
    "graph_events", "ignored_topological_action_ids", "beyond_horizon_action_ids",
    "deferred_validation_issues", "plan_feasibility_checked", "issues", "limitations",
    "validation_status",
)


def _public(value: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Keep the response contract stable; internal baseline fields stay server-side."""
    return {key: value[key] for key in fields if key in value}


_METRIC_STATUS = {
    "succeeded": "available",
    "already_available": "available",
    "not_recovered_within_horizon": "available",
    "not_applicable": "not_applicable",
    "insufficient_data": "missing",
    "unsupported": "unsupported",
}


def _metric_field(name: str, metric: Any) -> FieldAvailability:
    if not isinstance(metric, dict):
        return FieldAvailability(field=name, status="missing",
                                 missing_items=["该指标未返回；缺少初始状态、事件覆盖或证据"])
    status = _METRIC_STATUS.get(metric.get("status"), "missing")
    missing = [metric.get("reason") or "缺少必要数据"] if status in {"missing", "unsupported"} else []
    return FieldAvailability(field=name, status=status, missing_items=missing,
                             unsupported_reason=metric.get("reason") if status == "unsupported" else None)


def _field_availability(result: dict[str, Any], targets: list[str]) -> list[FieldAvailability]:
    recovery = result.get("recovery_times") or {}
    events_ok = result.get("timeline_status") == "succeeded"
    fields = [
        FieldAvailability(field="timeline_events", status="available" if events_ok else "missing",
                          missing_items=[] if events_ok else list(result.get("issues") or [])),
        _metric_field("plan_completion_hours", recovery.get("plan_completion_hours")),
    ]
    per_service = recovery.get("service_first_restored_hours") or {}
    if not targets:
        fields.append(FieldAvailability(field="service_first_restored_hours", status="not_applicable"))
    elif not per_service:
        fields.append(FieldAvailability(field="service_first_restored_hours", status="missing",
                                        missing_items=["逐服务恢复时间未返回"]))
    else:
        items = [(_service, _metric_field(_service, per_service.get(_service))) for _service in targets]
        missing = [f"{service}: {item.missing_items[0]}" for service, item in items
                   if item.status in {"missing", "unsupported"} and item.missing_items]
        available = [item for _, item in items if item.status in {"available", "not_applicable"}]
        status = "available" if len(available) == len(items) else "partial" if available else "missing"
        fields.append(FieldAvailability(field="service_first_restored_hours", status=status,
                                        missing_items=missing))
    fields.append(_metric_field("first_target_service_restored_hours",
                                recovery.get("first_target_service_restored_hours")))
    fields.append(_metric_field("all_target_services_restored_hours",
                                recovery.get("all_target_services_restored_hours")))
    fields.append(FieldAvailability(
        field="plan_feasibility", status="not_checked", in_scope=False,
        unsupported_reason="本接口只回放计划图通路，不校核预算、资源、依赖或现场可行性；plan_feasibility_checked=false",
    ))
    return fields


def timeline_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/v1/timeline", tags=["timeline"])

    def _load(dataset_id: str, request: Request) -> dict[str, Any]:
        principal = resolve_principal(request)
        try:
            snapshot = store.get_snapshot(dataset_id)
        except KeyError as exc:
            raise HTTPException(404, "数据集或快照不存在") from exc
        authorize_snapshot(principal, snapshot, store=store)
        return snapshot

    @router.post("/check", response_model=TimelineCheckResponse, responses=COMMON_ERRORS,
                 openapi_extra={"security": OPTIONAL_SECURITY, "parameters": [CLIENT_HEADER]})
    def check(payload: TimelineRequest, request: Request,
              _token: str | None = Security(WATER_TOKEN_SCHEME)):
        snapshot = _load(payload.snapshot_id, request)
        scenario = _scenario(payload, snapshot)
        gate = check_calculation_capability(
            CAPABILITY_ID, REQUESTED_OUTPUTS, SEMANTICS,
            {"snapshot": snapshot, "request": scenario, "plan_id": payload.plan_id,
             "target_service_ids": payload.target_service_ids, "action_schedule": _schedule(payload)},
        )
        result = build_plan_service_timeline(
            snapshot, scenario, payload.plan_id,
            target_service_ids=payload.target_service_ids,
            action_schedule=_schedule(payload),
        )
        fields = _field_availability(result, payload.target_service_ids)
        required = [item for item in fields if item.in_scope]
        return TimelineCheckResponse(
            status=gate["status"],
            dataset=_dataset_reference(snapshot),
            algorithm=_algorithm_reference(gate),
            result=CapabilityCheckResult.model_validate(_public(gate, CHECK_FIELDS)),
            field_availability=fields,
            has_any_calculable_output=any(
                item.status in {"available", "partial", "not_applicable"} for item in required),
            all_required_outputs_calculable=all(
                item.status in {"available", "not_applicable"} for item in required),
            evidence_ids=_base_evidence(snapshot, payload),
            missing_items=list(gate.get("missing_items") or []),
            limitations=CHECK_LIMITATIONS,
        )

    @router.post("/evaluate", response_model=TimelineEvaluationResponse, responses=COMMON_ERRORS,
                 openapi_extra={"security": OPTIONAL_SECURITY, "parameters": [CLIENT_HEADER]})
    def evaluate(payload: TimelineRequest, request: Request,
                 _token: str | None = Security(WATER_TOKEN_SCHEME)):
        snapshot = _load(payload.snapshot_id, request)
        scenario = _scenario(payload, snapshot)
        result = build_plan_service_timeline(
            snapshot, scenario, payload.plan_id,
            target_service_ids=payload.target_service_ids,
            action_schedule=_schedule(payload),
        )
        return TimelineEvaluationResponse(
            status=result.get("status") or "insufficient_data",
            dataset=_dataset_reference(snapshot),
            algorithm=_algorithm_reference(result),
            result=TimelineResult.model_validate(_public(result, RESULT_FIELDS)),
            evidence_ids=_evidence(snapshot, payload, result),
            missing_items=_missing(result),
            limitations=list(result.get("limitations") or EVALUATION_LIMITATIONS),
        )

    return router
