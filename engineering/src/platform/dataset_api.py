"""M1-A dataset view: a read-only projection of an existing frozen snapshot.

This module never recomputes health, topology or plan effects. It only exposes
allow-listed fields that the unified React frontend needs for display, together
with explicit availability and limitation metadata. Real-network snapshots are
denied unless the deployment explicitly enables a scoped read.
"""
from __future__ import annotations

import os
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, ConfigDict, Field

from src.capabilities import REGISTRY, UNSUPPORTED_REASONS

from . import VERSION as PLATFORM_VERSION
from .api_models import COMMON_ERRORS, OPTIONAL_SECURITY, WATER_TOKEN_SCHEME
from .authz import authorize_snapshot, resolve_principal

DEFAULT_LIMITATIONS = [
    "只读投影：本接口不重新计算健康度、拓扑影响、方案效用或恢复时间。",
    "legacy_demo_0.1.1 的 Hs 是简化演示评分，不是正式健康度 H，也不是失效概率或 RUL。",
    "未提供节点显示坐标时，前端不得自行编造地理位置；coordinate_system=schematic 仅为示意布局，不是GIS经纬度。",
    "数据模式与来源状态按快照原样返回；fixture_replay 样例不能附加未核实的网络或服务关系。",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LegacyScore(_Strict):
    """Legacy 0.1.1 score with explicit semantics; never reported as formal H."""

    semantics: Literal["legacy_demo_0.1.1"] = "legacy_demo_0.1.1"
    rs: float | None = None
    hs: float | None = None
    grade: str
    completeness_percent: float | None = None
    model_family: str = "legacy_demo"
    model_version: str = "0.1.1"
    evidence_refs: list[str] = Field(default_factory=list)


class AssetHints(_Strict):
    """Known upstream property names mapped for display only; units are unverified."""

    material_hint: str | None = None
    diameter_mm_hint: float | None = Field(default=None, description="上游列“管径”，单位 mm；仅展示提示，未重新核实")
    install_year_hint: int | None = None
    risk_point_count_hint: int | None = None


class DatasetPipe(_Strict):
    pipe_id: str
    name: str | None = None
    from_node_id: str | None = None
    to_node_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    asset_hints: AssetHints = Field(default_factory=AssetHints)
    score: LegacyScore | None = None
    score_issue: str | None = None


class DatasetNode(_Strict):
    node_id: str
    label: str = ""
    source: bool = False
    active: bool | None = None
    boundary: bool = False
    boundary_confirmed: bool = False
    x: float | None = Field(default=None, description="仅示意布局坐标，不是GIS经度")
    y: float | None = Field(default=None, description="仅示意布局坐标，不是GIS纬度")


class DatasetEdge(_Strict):
    edge_id: str
    pipe_id: str
    from_node: str
    to_node: str
    direction: Literal["both", "forward", "reverse", "unknown"] = "unknown"
    state: Literal["open", "closed", "unknown"] = "unknown"
    is_valve: bool = False
    operable: bool | None = None


class DatasetService(_Strict):
    service_id: str
    label: str
    node_ids: list[str]
    supply_rule: Literal["any", "all"]
    count_value: int | None = None
    count_unit: Literal["household", "person", "facility"]
    critical: bool = False


class DatasetIsolationPlan(_Strict):
    plan_id: str
    fault_edge_ids: list[str]
    valve_ids: list[str] = Field(default_factory=list)
    evidence_ref: str


class DatasetAction(_Strict):
    action_id: str
    label: str
    fault_edge_ids: list[str]
    cost_fen: int | None = None
    effective_after_hours: float | None = None
    evidence_ref: str | None = None
    restore_edge_ids: list[str] = Field(default_factory=list)
    open_valve_ids: list[str] = Field(default_factory=list)
    close_valve_ids: list[str] = Field(default_factory=list)
    resource_requirements: dict[str, float] | None = None
    requires_ids: list[str] = Field(default_factory=list)
    conflicts_with: list[str] = Field(default_factory=list)
    backup_service_ids: list[str] = Field(default_factory=list)


class DatasetPlan(_Strict):
    plan_id: str
    label: str
    action_ids: list[str]
    offline_effect: dict[str, Any] | None = None


class DatasetMeta(_Strict):
    dataset_id: str
    snapshot_id: str
    name: str
    schema_version: str
    data_mode: Literal["fixture_replay", "synthetic", "real"]
    source_status: str
    as_of: str
    sha256: str
    created_at: str
    source_refs: list[str]
    platform_version: str
    display_coordinates_available: bool = False
    coordinate_system: Literal["schematic", "wgs84", "local"] | None = None


class AlgorithmAvailability(_Strict):
    capability_id: str
    semantics: str
    version: str | None = None
    available: bool
    reason: str | None = None


class AssessmentCoverage(_Strict):
    total_pipes: int
    scored_pipes: int
    coverage_ratio: float = Field(description="有合法评分记录的管段占比 0—1；不是模型预测置信度")
    missing_pipe_ids: list[str]
    incompatible_pipe_ids: list[str]
    status: Literal["complete", "partial", "missing"]
    coverage_semantics: Literal["assessment_coverage_not_model_confidence"] = (
        "assessment_coverage_not_model_confidence")
    note: str = "覆盖率是“有合法评分记录的管段占比”，不是模型预测置信度；单管置信度见 score.completeness_percent。"


class DatasetView(_Strict):
    status: Literal["succeeded", "partial"]
    meta: DatasetMeta
    assessment_coverage: AssessmentCoverage
    pipes: list[DatasetPipe]
    nodes: list[DatasetNode]
    edges: list[DatasetEdge]
    services: list[DatasetService]
    actions: list[DatasetAction]
    plans: list[DatasetPlan]
    isolation_plans: list[DatasetIsolationPlan]
    algorithms: list[AlgorithmAvailability]
    formal_scoring_available: bool = False
    missing_items: list[str]
    limitations: list[str]
    evidence_ids: list[str]


def _project(model, row: dict[str, Any]):
    """Pass only declared fields that are present and non-null; defaults stay in force."""
    return model(**{key: row[key] for key in model.model_fields if row.get(key) is not None})


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _integer(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


SCORE_FAMILY = "legacy_demo"
SCORE_VERSION = "0.1.1"


def _score(assessment: dict[str, Any] | None) -> tuple[LegacyScore | None, str | None]:
    """Only expose a score when the model identity is exactly the known baseline."""
    if not assessment:
        return None, None
    family = assessment.get("model_family")
    version = assessment.get("model_version")
    if family != SCORE_FAMILY or version != SCORE_VERSION:
        return None, (f"评分模型 {family or '缺失'}/{version or '缺失'} 与 "
                      f"{SCORE_FAMILY}/{SCORE_VERSION} 不兼容，未重新贴标签")
    legacy = assessment.get("legacy_result")
    if not isinstance(legacy, dict):
        return None, "评分记录缺少 legacy_result，不能用演示规则补齐"
    return LegacyScore(
        rs=_number(legacy.get("Rs")),
        hs=_number(legacy.get("Hs")),
        grade=legacy.get("grade") or "未知",
        completeness_percent=_number(legacy.get("completeness_percent")),
        model_family=family,
        model_version=version,
        evidence_refs=list(assessment.get("evidence_refs") or []),
    ), None


def _asset_hints(properties: dict[str, Any]) -> AssetHints:
    """Only map names that the upstream sample actually uses; never infer values."""
    return AssetHints(
        material_hint=properties.get("材质") if isinstance(properties.get("材质"), str) else None,
        diameter_mm_hint=_number(properties.get("管径")),
        install_year_hint=_integer(properties.get("投运年份")),
        risk_point_count_hint=_integer(properties.get("风险点数量")),
    )


def _pipes(payload: dict[str, Any]) -> list[DatasetPipe]:
    assessments = {row["pipe_id"]: row for row in payload.get("assessments", [])}
    edges: dict[str, list[dict[str, Any]]] = {}
    for edge in payload.get("edges", []):
        edges.setdefault(edge["pipe_id"], []).append(edge)
    rows = []
    for pipe_id in sorted(set(assessments) | set(edges)):
        linked = edges.get(pipe_id, [])
        assessment = assessments.get(pipe_id)
        properties = dict((assessment or {}).get("properties") or {})
        score, issue = _score(assessment)
        rows.append(DatasetPipe(
            pipe_id=pipe_id,
            name=properties.get("名称") if isinstance(properties.get("名称"), str) else None,
            from_node_id=linked[0]["from_node"] if linked else None,
            to_node_id=linked[0]["to_node"] if linked else None,
            properties=properties,
            asset_hints=_asset_hints(properties),
            score=score,
            score_issue=issue,
        ))
    return rows


def _algorithms(payload: dict[str, Any], pipes: list[DatasetPipe]) -> list[AlgorithmAvailability]:
    has_scores = any(pipe.score is not None for pipe in pipes)
    has_network = bool(payload.get("nodes") and payload.get("edges"))
    has_services = bool(payload.get("services"))
    has_plans = bool(payload.get("plans") and payload.get("actions"))
    incompatible = [pipe.pipe_id for pipe in pipes if pipe.score_issue]
    checks = [
        ("legacy_health", has_scores,
         ("评分模型版本不兼容或缺少字段：" + "、".join(incompatible)) if incompatible
         else "该数据集没有评分记录；不会现场补算或重新贴标签"),
        ("given_isolation_topology", has_network and has_services, "缺少节点、边或服务对象"),
        ("candidate_plan_ranking", has_plans, "缺少候选方案或措施"),
        ("plan_service_timeline", has_plans and has_services, "缺少方案、措施或服务对象"),
    ]
    rows: list[AlgorithmAvailability] = []
    for capability_id, available, reason in checks:
        spec = REGISTRY[capability_id]
        rows.append(AlgorithmAvailability(
            capability_id=capability_id, semantics=spec["semantics"], version=spec["version"],
            available=available, reason=None if available else reason,
        ))
    for capability_id, reason in UNSUPPORTED_REASONS.items():
        rows.append(AlgorithmAvailability(
            capability_id=capability_id, semantics="unavailable", version=None,
            available=False, reason=reason,
        ))
    return rows


def build_dataset_view(snapshot: dict[str, Any]) -> DatasetView:
    """Project an already validated snapshot; no computation or persistence."""
    payload = snapshot["payload"]
    pipes = _pipes(payload)
    nodes = [_project(DatasetNode, node) for node in payload.get("nodes", [])]
    edges = [_project(DatasetEdge, edge) for edge in payload.get("edges", [])]
    services = [_project(DatasetService, service) for service in payload.get("services", [])]
    actions = [_project(DatasetAction, action) for action in payload.get("actions", [])]
    plans = [DatasetPlan(plan_id=plan["plan_id"], label=plan["label"],
                         action_ids=list(plan["action_ids"]), offline_effect=plan.get("offline_effect"))
             for plan in payload.get("plans", [])]
    isolation = [_project(DatasetIsolationPlan, plan) for plan in payload.get("isolation_plans", [])]

    scored = [pipe for pipe in pipes if pipe.score is not None]
    missing_ids = [pipe.pipe_id for pipe in pipes if pipe.score is None]
    incompatible = [pipe.pipe_id for pipe in pipes if pipe.score_issue]
    coverage_status = ("complete" if pipes and not missing_ids else "partial" if scored else "missing")
    coverage = AssessmentCoverage(
        total_pipes=len(pipes),
        scored_pipes=len(scored),
        coverage_ratio=round(len(scored) / len(pipes), 4) if pipes else 0.0,
        missing_pipe_ids=missing_ids,
        incompatible_pipe_ids=incompatible,
        status=coverage_status,
    )

    missing = []
    if coverage_status != "complete":
        if missing_ids:
            missing.append("缺少评分记录：" + "、".join(missing_ids))
        if incompatible:
            missing.append("评分模型版本不兼容：" + "、".join(incompatible))
        if not pipes:
            missing.append("该数据集没有可展示管段")
    if not payload.get("nodes") or not payload.get("edges"):
        missing.append("该数据集没有完整网络拓扑")
    if not payload.get("services"):
        missing.append("该数据集没有服务对象")
    if not payload.get("plans") or not payload.get("actions"):
        missing.append("该数据集没有候选方案或措施")
    if any(node.get("x") is None for node in payload.get("nodes", [])):
        missing.append("节点未提供显示坐标")

    meta = DatasetMeta(
        dataset_id=snapshot["snapshot_id"],
        snapshot_id=snapshot["snapshot_id"],
        name=snapshot["name"],
        schema_version=payload.get("schema_version") or "0.1.0",
        data_mode=snapshot["data_mode"],
        source_status=snapshot["source_status"],
        as_of=snapshot["as_of"],
        sha256=snapshot["sha256"],
        created_at=snapshot["created_at"],
        source_refs=list(payload.get("source_refs") or []),
        platform_version=PLATFORM_VERSION,
        display_coordinates_available=bool(payload.get("nodes")) and all(
            node.get("x") is not None and node.get("y") is not None for node in payload["nodes"]),
        coordinate_system=payload.get("coordinate_system"),
    )
    complete = (
        coverage_status == "complete"
        and bool(payload.get("nodes"))
        and bool(payload.get("edges"))
        and bool(payload.get("services"))
        and bool(payload.get("plans"))
        and bool(payload.get("actions"))
    )
    return DatasetView(
        status="succeeded" if complete else "partial",
        meta=meta,
        assessment_coverage=coverage,
        pipes=pipes,
        nodes=nodes,
        edges=edges,
        services=services,
        actions=actions,
        plans=plans,
        isolation_plans=isolation,
        algorithms=_algorithms(payload, pipes),
        missing_items=missing,
        limitations=DEFAULT_LIMITATIONS,
        evidence_ids=["snapshot:" + snapshot["snapshot_id"]],
    )


def dataset_router(store) -> APIRouter:
    router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

    @router.get("/{dataset_id}/view", response_model=DatasetView,
                responses=COMMON_ERRORS,
                openapi_extra={"security": OPTIONAL_SECURITY,
                               "parameters": [{"name": "X-Water-Token", "in": "header", "required": False,
                                               "schema": {"type": "string"},
                                               "description": "可选令牌；真实数据还需服务端数据集授权"}]})
    def dataset_view(dataset_id: str, request: Request,
                     _token: str | None = Security(WATER_TOKEN_SCHEME)):
        principal = resolve_principal(request)
        try:
            snapshot = store.get_snapshot(dataset_id)
        except KeyError as exc:
            raise HTTPException(404, "数据集或快照不存在") from exc
        authorize_snapshot(principal, snapshot, store=store)
        return build_dataset_view(snapshot)

    return router
