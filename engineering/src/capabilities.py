"""G01: deterministic, side-effect-free capability preflight.

This module validates actual inputs, not boolean promises that data exist. It
does not grant permissions, run an LLM, persist a snapshot, or dispatch actions.
Registered computations are local pure functions; a successful preflight is
not business acceptance and is not a replacement for execution-time validation.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import math
from numbers import Real

from pydantic import ValidationError

VERSION = "1.0.1"

# Semantic names are deliberate: legacy Hs never satisfies a request for H.
REGISTRY = {
    "legacy_health": {
        "semantics": "legacy_demo_0.1.1",
        "outputs": ["Rs", "Hs", "completeness_percent", "grade"],
        "required": ["recompute_request"], "optional": [],
        "callable": "src.adapters.scoring.validate_recompute", "version": "0.1.1",
    },
    "legacy_health_explanation": {
        "semantics": "legacy_demo_0.1.1",
        "outputs": ["indicators", "deduction_rank", "reconciliation", "legacy_completeness_percent", "missing_indicators"],
        "required": ["indicator_scores", "model_version"], "optional": [],
        "callable": "src.adapters.explanations.explain_legacy_health", "version": "1.0.0",
    },
    "given_isolation_topology": {
        "semantics": "topological_reachability",
        "outputs": ["reachable_node_ids", "newly_unreachable_node_ids", "affected_service_ids", "potentially_disconnected_counts"],
        "required": ["snapshot", "request"], "optional": [],
        "callable": "src.simulation.topology.simulate", "version": "0.1.0",
    },
    "plan_constraints": {
        "semantics": "window_aggregate_constraints",
        "outputs": ["feasibility"],
        "required": ["snapshot", "request", "plan_id"], "optional": [],
        "callable": "src.decision.contracts.check_plan_constraints", "version": "1.0.0",
    },
    "plan_effects": {
        "semantics": "topological_and_declared_backup_conditions",
        "outputs": ["impact", "metrics", "restored_service_ids", "newly_affected_service_ids", "baseline_key"],
        "required": ["snapshot", "request", "plan_id"], "optional": ["offline_provenance"],
        "callable": "src.decision.contracts.evaluate_plan_effects", "version": "1.0.0",
    },
    "fixed_utility": {
        "semantics": "fixed_interval_weighted_utility",
        "outputs": ["utility", "utility_breakdown"],
        "required": ["metrics", "value_profile"], "optional": ["baseline_key"],
        "callable": "src.decision.contracts.score_fixed_utility", "version": "1.0.0",
    },
    "candidate_plan_ranking": {
        "semantics": "given_candidates_only",
        "outputs": ["plans", "recommended_plan_id", "tie_plan_ids"],
        "required": ["snapshot", "request"], "optional": ["offline_provenance"],
        "callable": "src.decision.contracts.rank_candidate_plans", "version": "1.0.0",
    },
    "recovery_times": {
        "semantics": "declared_event_timeline",
        "outputs": ["plan_completion_hours", "service_first_restored_hours", "first_target_service_restored_hours", "all_target_services_restored_hours"],
        "required": ["recovery_request"], "optional": [],
        "callable": "src.decision.recovery.compute_recovery_times", "version": "1.0.0",
    },
    "plan_service_timeline": {
        "semantics": "planned_topological_reachability",
        "outputs": ["timeline_request", "recovery_times"],
        "required": ["snapshot", "request", "plan_id", "target_service_ids"], "optional": ["action_schedule"],
        "callable": "src.decision.timeline.build_plan_service_timeline", "version": "1.0.1",
    },
}

UNSUPPORTED_REASONS = {
    "formal_health": "正式P/C/R/H的可调用实现与版本尚未交付；不能以Rs/Hs替代。",
    "failure_probability": "缺少给定期限的专业失效概率模型，不能由风险分转换。",
    "rul": "剩余寿命专业模型尚未接入。",
    "hydraulic_compliance": "压力、流量与水质达标模型尚未接入。",
    "minimum_isolation": "当前仅分析给定隔离方案，未实现自动最小隔离。",
    "global_plan_optimization": "当前仅比较给定候选，未实现全局组合优化。",
}


def list_calculation_capabilities() -> list[dict]:
    """Machine-readable scope for tool registration; no access is granted."""
    return [{"capability_id": name, **deepcopy(spec)} for name, spec in REGISTRY.items()]


def _dispatch(capability_id: str, inputs: dict) -> dict:
    if capability_id == "legacy_health":
        from src.adapters.scoring import validate_recompute
        from src.platform.schemas import RecomputeRequest
        validate_recompute(RecomputeRequest.model_validate(inputs["recompute_request"]))
        return {"status": "succeeded", "validation_mode": "inputs_only"}
    if capability_id == "legacy_health_explanation":
        from src.adapters.explanations import explain_legacy_health
        return explain_legacy_health(inputs["indicator_scores"], inputs["model_version"])
    if capability_id == "given_isolation_topology":
        from src.platform.schemas import ScenarioRequest, SnapshotPayload
        from src.simulation.topology import simulate
        raw = inputs["request"]
        if not isinstance(raw, Mapping) or not isinstance(inputs["snapshot"], Mapping):
            raise ValueError("scenario and snapshot must be mappings")
        horizon = raw.get("horizon_hours", 8)
        if isinstance(horizon, bool) or not isinstance(horizon, Real) or not math.isfinite(horizon):
            raise ValueError("horizon_hours must be a finite number")
        payload = inputs["snapshot"]["payload"]
        SnapshotPayload.model_validate(payload)
        for table, names in [("nodes", ("source", "active", "boundary", "boundary_confirmed")),
                             ("edges", ("operable",)), ("services", ("critical",))]:
            for row in payload.get(table, []):
                if any(row.get(name) is not None and not isinstance(row[name], bool) for name in names):
                    raise ValueError("operating-state flags must be booleans or null where allowed")
        return simulate(inputs["snapshot"], ScenarioRequest.model_validate(inputs["request"]))
    if capability_id == "recovery_times":
        from src.decision.recovery import RecoveryTimeRequest, compute_recovery_times
        return compute_recovery_times(RecoveryTimeRequest.model_validate(inputs["recovery_request"]))
    if capability_id == "plan_service_timeline":
        from src.decision.timeline import build_plan_service_timeline
        return build_plan_service_timeline(**inputs)
    from src.decision import contracts
    function = {
        "plan_constraints": contracts.check_plan_constraints,
        "plan_effects": contracts.evaluate_plan_effects,
        "fixed_utility": contracts.score_fixed_utility,
        "candidate_plan_ranking": contracts.rank_candidate_plans,
    }[capability_id]
    return function(**inputs)


def _time_available(value) -> bool:
    if not isinstance(value, dict):
        return False
    state = value.get("status")
    return isinstance(state, str) and state in {"succeeded", "already_available", "not_applicable", "not_recovered_within_horizon"}


def _available_outputs(capability_id: str, result: dict, requested: list[str], inputs: dict) -> list[str]:
    state = result["status"]
    if state in {"invalid_input", "unsupported"}:
        return []
    if capability_id == "recovery_times":
        return [key for key in requested if (
            key == "service_first_restored_hours" and isinstance(result.get(key), dict)
            and all(_time_available(v) for v in result[key].values())
        ) or (key != "service_first_restored_hours" and _time_available(result.get(key)))]
    if capability_id == "legacy_health_explanation" and state == "insufficient_data":
        return [key for key in requested if key in {"missing_indicators", "legacy_completeness_percent", "indicators"} and key in result]
    if capability_id == "plan_service_timeline":
        return [key for key in requested if (
            key == "timeline_request" and result.get("timeline_status") == "succeeded"
        ) or (
            key == "recovery_times" and (result.get("recovery_times") or {}).get("status") == "succeeded"
        )]
    if capability_id == "given_isolation_topology" and state in {"succeeded", "partial"}:
        services = inputs["snapshot"]["payload"].get("services", [])
        return [key for key in requested if key in result and (
            key not in {"affected_service_ids", "potentially_disconnected_counts"} or services
        ) and (key != "potentially_disconnected_counts" or all(v is not None for v in result[key].values()))]
    if capability_id == "plan_constraints":
        return requested if state in {"succeeded", "infeasible"} else []
    if capability_id == "candidate_plan_ranking" and state == "infeasible":
        # An established absence of a feasible candidate is a computed outcome.
        return requested
    if capability_id == "legacy_health":
        return requested if state == "succeeded" else []
    if state == "succeeded":
        return [key for key in requested if key in result]
    return []


def check_calculation_capability(capability_id, requested_outputs, requested_semantics, input_manifest) -> dict:
    """Check exact semantics and actual inputs before a deterministic calculation.

    input_manifest contains the actual in-memory request/snapshot fields from
    REGISTRY, never arbitrary paths or credentials. Partial output availability
    is explicit. calculation_status preserves an infeasible or partial outcome;
    it must not be interpreted as feasible merely because the check succeeded.
    """
    base = {"registry_version": VERSION, "capability_id": capability_id if isinstance(capability_id, str) else None,
            "status": "invalid_input", "can_compute": False, "supported_outputs": [],
            "unsupported_outputs": [], "available_outputs": [], "missing_items": [],
            "available_alternative": None, "evidence_refs": [], "output_values": None,
            "validation_status": "not_business_validated", "authorization_checked": False}
    if (not isinstance(capability_id, str) or not capability_id.strip()
            or not isinstance(requested_semantics, str) or not requested_semantics.strip()
            or not isinstance(requested_outputs, list) or not 1 <= len(requested_outputs) <= 64
            or any(not isinstance(x, str) or not x.strip() for x in requested_outputs)
            or len(set(requested_outputs)) != len(requested_outputs)
            or not isinstance(input_manifest, Mapping)):
        return {**base, "missing_items": ["capability_id、semantics、唯一非空输出列表及对象型input_manifest必填。"]}
    spec = REGISTRY.get(capability_id)
    if spec is None or requested_semantics != spec["semantics"]:
        reason = UNSUPPORTED_REASONS.get(capability_id, "能力或所请求语义未注册；不能自动换成另一类结论。")
        alternative = "legacy_health（只提供Rs/Hs；需另行选择该任务）" if capability_id in {"formal_health", "failure_probability", "rul"} else None
        return {**base, "status": "unsupported", "unsupported_outputs": requested_outputs,
                "output_values": dict.fromkeys(requested_outputs), "missing_items": [reason],
                "available_alternative": alternative}
    base.update(algorithm_version=spec["version"], requested_semantics=requested_semantics,
                supported_outputs=[k for k in requested_outputs if k in spec["outputs"]],
                unsupported_outputs=[k for k in requested_outputs if k not in spec["outputs"]])
    if base["unsupported_outputs"]:
        return {**base, "status": "unsupported", "output_values": dict.fromkeys(requested_outputs),
                "missing_items": ["请求包含当前函数不支持的输出，未执行任何计算。"]}
    unknown = set(input_manifest) - set(spec["required"] + spec["optional"])
    if unknown:
        return {**base, "missing_items": ["未定义输入字段: " + ", ".join(sorted(str(x) for x in unknown))]}
    missing = [key for key in spec["required"] if key not in input_manifest or input_manifest[key] is None]
    if missing:
        return {**base, "status": "insufficient_data", "missing_items": missing}
    inputs = deepcopy(dict(input_manifest))
    from src.platform.errors import InputIssues
    try:
        result = _dispatch(capability_id, inputs)
    except ValidationError as exc:
        errors = exc.errors(include_input=False, include_url=False)
        absent = all(item["type"] == "missing" for item in errors)
        return {**base, "status": "insufficient_data" if absent else "invalid_input",
                "missing_items": [{"field": ".".join(map(str, e["loc"])), "reason": e["msg"]} for e in errors]}
    except InputIssues as exc:
        # The legacy adapter predates typed failure codes. Distinguish absent
        # observations from invalid values using the referenced input cell.
        raw = inputs.get("recompute_request", {})
        def absent(issue):
            if not isinstance(issue, dict):
                return False
            if issue.get("field") == "动态记录":
                return True
            table = {"static": "static_rows", "dynamic": "dynamic_rows"}.get(issue.get("table"))
            index = issue.get("record_index")
            if table and isinstance(index, int):
                return raw.get(table, [])[index].get(issue.get("field")) in (None, "")
            return False
        status = "insufficient_data" if all(absent(issue) for issue in exc.issues) else "invalid_input"
        return {**base, "status": status, "missing_items": exc.issues}
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        # These indicate invalid in-memory input shapes, not lack of capability.
        return {**base, "status": "invalid_input", "missing_items": [f"输入不满足函数契约: {type(exc).__name__}"]}
    available = _available_outputs(capability_id, result, requested_outputs, inputs)
    calculation_status = result["status"]
    if calculation_status in {"invalid_input", "unsupported"}:
        status = calculation_status
    elif len(available) == len(requested_outputs):
        status = "succeeded"
    else:
        status = "partial" if available else "insufficient_data"
    reasons = result.get("missing_items") or result.get("reasons") or result.get("issues") or ([result["reason"]] if result.get("reason") else [])
    unavailable = [key for key in requested_outputs if key not in available]
    return {**base, "status": status, "can_compute": status == "succeeded", "calculation_status": calculation_status,
            "available_outputs": available, "unavailable_outputs": unavailable,
            "missing_items": reasons or (["当前数据无法计算: " + ", ".join(unavailable)] if unavailable else []),
            "evidence_refs": result.get("evidence_refs", []),
            "validation_mode": result.get("validation_mode", "pure_computation_preflight")}
