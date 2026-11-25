"""Compare explicitly scheduled synthetic gas graph scenarios."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .contracts import GasConsumerCounts
from .network import _digest, _maps, _reachable, _refs, _state, evaluate_case


StepKind = Literal["fault", "isolate", "staged_release", "assumed_clearance"]
ConstraintStatus = Literal["met", "blocked", "assumed", "unknown", "not_applicable"]


class GasCuvConstraint(BaseModel):
    code: str
    status: ConstraintStatus
    explanation: str


class GasCuvStep(BaseModel):
    at: datetime
    elapsed_hours: float
    kind: StepKind
    status: Literal["evaluated", "blocked"]
    affected_service_ids: list[str] | None
    affected_node_ids: list[str] | None
    affected_consumers: GasConsumerCounts | None
    restored_service_ids: list[str] | None
    blocked_asset_ids: list[str]
    constraints: list[GasCuvConstraint]


class GasCuvPlan(BaseModel):
    plan_id: str
    label: str
    description: str
    status: Literal["replayed", "blocked"]
    steps: list[GasCuvStep]
    topology_restoration_hours: float | None
    peak_affected_service_count: int | None
    affected_service_hours: float | None


class GasCuvTopologyEvidence(BaseModel):
    network_content_sha256: str
    case_content_sha256: str
    html_source_sha256: str | None
    replay_content_sha256: str


class GasCuvUnknownMetric(BaseModel):
    code: str
    status: Literal["unknown"]
    value: None
    reason: str


class GasCuvMissingItem(BaseModel):
    code: str
    description: str


class GasCuvReplayReport(BaseModel):
    schema_version: Literal["gas-cuv-replay-0.1.0"]
    data_mode: Literal["synthetic"]
    validation_status: Literal["not_business_validated"]
    semantic: Literal["graph_connectivity_only"]
    status: Literal["partial"]
    case_id: str
    network_id: str
    scenario_id: str
    scenario_at: datetime
    analysis_window_hours: float
    fault_asset_ids: list[str]
    algorithm_version: str
    topology_evidence: GasCuvTopologyEvidence
    plans: list[GasCuvPlan]
    unknown_metrics: list[GasCuvUnknownMetric]
    missing_items: list[GasCuvMissingItem]
    comparison_basis: str


class _Definition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _StepDefinition(_Definition):
    elapsed_hours: float = Field(gt=0, strict=True, allow_inf_nan=False)
    kind: Literal["isolate", "staged_release", "assumed_clearance"]
    disabled_edge_ids: list[str]
    closed_valve_ids: list[str]


class _PlanDefinition(_Definition):
    plan_id: str = Field(pattern=r"^syn-gas-[a-z0-9-]+$")
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    steps: list[_StepDefinition] = Field(min_length=1)


class _ReplayDefinition(_Definition):
    schema_version: Literal["gas-cuv-scenario-0.1.0"]
    data_mode: Literal["synthetic"]
    case_id: str
    scenario_id: str
    scenario_at: AwareDatetime
    plans: list[_PlanDefinition] = Field(min_length=2)


UNKNOWN_METRICS = {
    "actual_gas_supply": "缺少经过校核的网络水力模型和授权供气量测。",
    "pressure": "合成监测仅用于时点背景，缺少可用于预测的水力模型和边界条件。",
    "flow": "缺少正式水力模型、授权需求和流量数据。",
    "physical_restoration_hours": "缺少实际修复、检漏、置换、验收和批准放行记录。",
    "failure_probability": "缺少正式故障模型和实测验证样本。",
    "operational_cost": "缺少经确认的人工、设备、物资与恢复成本。",
}

MISSING_ITEMS = {
    "authorized_inventory_and_monitoring": "正式台账、一致的资产与测点映射、授权监测及采样质量口径。",
    "calibrated_hydraulic_model": "校核后的燃气水力模型、管道参数、气源与用户需求边界。",
    "field_restoration_acceptance": "现场作业授权、修复和检验记录、验收与恢复供气审批。",
    "resource_and_risk_models": "经确认的资源、成本、工期约束及风险评价模型。",
}

COMPARISON_BASIS = (
    "相对正常基线统计新增失去图通路的服务对象。累计服务对象·小时按两个状态之间的"
    "图状态不变计算。方案时间、修复与放行为合成假设。图通路不能代表实际供气、压力或安全复气。"
)

PHYSICAL_CLEARANCE = (
    "现场授权、实际隔离、修复和复气验收仍未知。约束只评价固定合成拓扑。"
)


def _constraint(code: str, status: ConstraintStatus, explanation: str) -> GasCuvConstraint:
    return GasCuvConstraint(code=code, status=status, explanation=explanation)


def _candidate_constraints(
    step: _StepDefinition, isolation: dict, faults: set[str], cuts: set[str], valves: set[str],
    previous_cuts: set[str], previous_valves: set[str],
) -> list[GasCuvConstraint]:
    if step.kind == "isolate":
        matches = (
            step.elapsed_hours == isolation["time_hours"]
            and cuts == set(isolation["disabled_edge_ids"])
            and valves == set(isolation["closed_valve_ids"])
        )
        return [_constraint(
            "given_isolation_state", "met" if matches else "blocked",
            "采用原合成事件中给定的隔离时点、切边和阀门节点。"
            if matches else "隔离时点或资产范围与给定合成隔离事件不一致。",
        )]
    if step.kind == "staged_release":
        respects_cuts = faults <= cuts <= previous_cuts and valves <= previous_valves
        changed = (cuts, valves) != (previous_cuts, previous_valves)
        valid = respects_cuts and changed
        return [_constraint(
            "progressive_graph_release", "met" if valid else "blocked",
            "分段恢复只解除上一切片中的部分限制，并保留全部故障边。"
            if valid else "分段恢复必须保留故障边，且仅解除上一切片中的部分限制。",
        )]
    released = not cuts and not valves
    return [_constraint(
        "assumed_repair_and_release", "assumed" if released else "blocked",
        "按本合成场景假定修复和放行，图状态回到正常基线。"
        if released else "该合成放行切片须解除方案限制，保留原正常基线限制。",
    )]


def _step_result(
    anchor: datetime,
    elapsed: float,
    kind: StepKind,
    graph: dict | None,
    previously_affected: set[str],
    excluded_assets: list[str],
    constraints: list[GasCuvConstraint],
) -> GasCuvStep:
    affected = graph["newly_affected_service_ids"] if graph is not None else None
    constraints.append(_constraint("field_acceptance", "unknown", PHYSICAL_CLEARANCE))
    return GasCuvStep(
        at=anchor + timedelta(hours=elapsed),
        elapsed_hours=elapsed,
        kind=kind,
        status="evaluated" if graph is not None else "blocked",
        affected_service_ids=affected,
        affected_node_ids=graph["newly_unreachable_node_ids"] if graph is not None else None,
        affected_consumers=graph["potentially_interrupted_consumers"] if graph is not None else None,
        restored_service_ids=sorted(previously_affected - set(affected)) if affected is not None else None,
        blocked_asset_ids=excluded_assets,
        constraints=constraints,
    )


def _summary_metrics(steps: list[GasCuvStep], horizon: float) -> tuple[float | None, int, float]:
    counts = []
    for step in steps:
        if step.affected_service_ids is None:
            raise ValueError("cannot summarize an unknown CUV graph state")
        counts.append(len(step.affected_service_ids))
    peak = max(counts)
    end_times = [step.elapsed_hours for step in steps[1:]] + [horizon]
    affected_hours = sum(
        count * (end - step.elapsed_hours)
        for step, end, count in zip(steps, end_times, counts, strict=True)
    )
    restored = None
    if peak:
        restored = next(
            (step.elapsed_hours for step, count in zip(steps[1:], counts[1:], strict=True) if count == 0),
            None,
        )
    return restored, peak, affected_hours


def _validated_schedule(
    plan: _PlanDefinition, horizon: float, edges: dict, valves_by_id: dict,
) -> list[tuple[_StepDefinition, set[str], set[str]]]:
    schedule = []
    previous_time = 0.0
    for index, step in enumerate(plan.steps):
        if not previous_time < step.elapsed_hours <= horizon:
            raise ValueError("CUV replay times must strictly increase within the case window")
        if (step.kind == "isolate") != (index == 0):
            raise ValueError("CUV replay requires one initial isolation")
        if step.kind == "assumed_clearance" and index != len(plan.steps) - 1:
            raise ValueError("CUV replay clearance must be the last step")
        cuts = _refs(step.disabled_edge_ids, edges, "CUV cut edges")
        valves = _refs(step.closed_valve_ids, valves_by_id, "CUV cut valves")
        schedule.append((step, cuts, valves))
        previous_time = step.elapsed_hours
    return schedule


def _replay_plan(
    network: dict, base_result: dict, definitions: _ReplayDefinition, plan: _PlanDefinition,
    case: dict, faults: set[str], normal: set[str],
) -> GasCuvPlan:
    graph = _maps(network)
    edges, valves_by_id = graph["edges"], graph["valves"]

    def assets(edge_ids: set[str], valve_ids: set[str]) -> list[str]:
        return sorted({edges[key]["pipe_id"] for key in edge_ids} | valve_ids)

    endpoint_ids = {edges[key][end] for key in faults for end in ("from_node_id", "to_node_id")}
    isolation = next(event for event in case["events"] if event["kind"] == "given_isolation")
    anchor = definitions.scenario_at.astimezone(UTC)
    initial = base_result["states"][0]
    steps = [_step_result(anchor, 0, "fault", initial, set(), assets(faults, set()), [
        _constraint("given_fault_edge", "assumed", "依据固定合成故障事件给定失效边。"),
        _constraint("fault_endpoints_graph_isolation", "not_applicable", "故障发生切片尚未执行合成隔离。"),
    ])]
    previous_edges: set[str] = set()
    previous_valves: set[str] = set()
    affected = set(initial["newly_affected_service_ids"])
    horizon = base_result["analysis_window_hours"]
    schedule = _validated_schedule(plan, horizon, edges, valves_by_id)
    for index, (step, cuts, valves) in enumerate(schedule, start=1):
        constraints = _candidate_constraints(
            step, isolation, faults, cuts, valves, previous_edges, previous_valves,
        )
        active_faults = set() if step.kind == "assumed_clearance" else faults
        state = None
        if constraints[0].status != "blocked":
            state = _state(network, normal, {
                "event_id": f"{plan.plan_id}-{index}",
                "time_hours": step.elapsed_hours,
                "kind": step.kind,
                "fault_edge_ids": active_faults,
                "disabled_edge_ids": cuts,
                "closed_valve_ids": valves,
            })
            if active_faults:
                has_source_path = not endpoint_ids.isdisjoint(state["current_reachable_node_ids"])
                constraints.append(_constraint(
                    "fault_endpoints_graph_isolation", "blocked" if has_source_path else "met",
                    "故障至少一端仍有图通路连接气源。" if has_source_path else "故障两端均无图通路连接气源。",
                ))
                if has_source_path:
                    state = None
        steps.append(_step_result(
            anchor, step.elapsed_hours, step.kind, state, affected, assets(cuts | active_faults, valves), constraints,
        ))
        if state is None:
            return GasCuvPlan(
                plan_id=plan.plan_id, label=plan.label, description=plan.description, status="blocked", steps=steps,
                topology_restoration_hours=None, peak_affected_service_count=None, affected_service_hours=None,
            )
        previous_edges, previous_valves = cuts, valves
        affected = set(state["newly_affected_service_ids"])
    restored, peak, total = _summary_metrics(steps, horizon)
    return GasCuvPlan(
        plan_id=plan.plan_id, label=plan.label, description=plan.description, status="replayed", steps=steps,
        topology_restoration_hours=restored, peak_affected_service_count=peak, affected_service_hours=total,
    )


def compare_cuv_replay(network: dict, case: dict, scenario: dict) -> GasCuvReplayReport:
    """Evaluate only explicit synthetic plan steps under the original gas graph semantics."""
    if not isinstance(scenario, dict) or not isinstance(scenario.get("scenario_at"), str):
        raise ValueError("CUV replay requires an explicit zoned scenario time")
    definition = _ReplayDefinition.model_validate(scenario)
    base_result = evaluate_case(network, case)
    identity = base_result["identity"]
    if definition.case_id != identity["case_id"] or definition.scenario_id != identity["scenario_id"]:
        raise ValueError("CUV replay case and scenario identity mismatch")
    if len({plan.plan_id for plan in definition.plans}) != len(definition.plans):
        raise ValueError("CUV replay plan identities must be unique")

    baseline = network["baseline"]
    normal = _reachable(
        network, disabled_edges=baseline["disabled_edge_ids"], closed_valves=baseline["closed_valve_ids"],
        disabled_sources=baseline["disabled_source_ids"],
    )
    faults = set(case["scenario"]["fault_edge_ids"])
    return GasCuvReplayReport(
        schema_version="gas-cuv-replay-0.1.0",
        data_mode="synthetic",
        validation_status="not_business_validated",
        semantic="graph_connectivity_only",
        status="partial",
        case_id=definition.case_id,
        network_id=identity["network_id"],
        scenario_id=definition.scenario_id,
        scenario_at=definition.scenario_at.astimezone(UTC),
        analysis_window_hours=base_result["analysis_window_hours"],
        fault_asset_ids=sorted({edge["pipe_id"] for edge in network["edges"] if edge["edge_id"] in faults}),
        algorithm_version=base_result["algorithm_version"],
        topology_evidence=GasCuvTopologyEvidence(**base_result["evidence"], replay_content_sha256=_digest(scenario)),
        plans=[_replay_plan(network, base_result, definition, plan, case, faults, normal) for plan in definition.plans],
        unknown_metrics=[
            GasCuvUnknownMetric(code=code, status="unknown", value=None, reason=reason)
            for code, reason in UNKNOWN_METRICS.items()
        ],
        missing_items=[
            GasCuvMissingItem(code=code, description=description) for code, description in MISSING_ITEMS.items()
        ],
        comparison_basis=COMPARISON_BASIS,
    )
