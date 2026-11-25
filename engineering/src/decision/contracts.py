"""D01-D04: deterministic, registered-service decision contracts, version 1.0.0.

No scheduling solver, hydraulic model, or learned value weights. All input effects
remain declared scenario assumptions. Public calls validate before computation.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone
import hashlib
import json
import math
from numbers import Real

from pydantic import ValidationError

from src.platform.schemas import DecisionRequest, ScenarioRequest, SnapshotPayload, ValueProfile
from src.simulation.topology import VERSION as TOPOLOGY_VERSION, check_inputs, envelope, simulate

VERSION = "1.0.0"
METRIC_VERSION = "registered-service-metrics-1.0"
METRIC_UNITS = {
    "remaining_disconnected_services": "service_record",
    "total_cost_fen": "fen",
    "restoration_hours": "hour",
    "backup_covered_critical_services": "service_record",
}
_NUMERIC_KEYS = {"horizon_hours", "budget_fen", "cost_fen", "effective_after_hours",
                 "backup_capacity_m3h", "backup_demand_m3h", "backup_duration_hours",
                 "restoration_hours", "lower", "upper", "weight"}


def _result(status, **fields):
    return {"status": status, "decision_algorithm_version": VERSION, **fields}


def _numeric_guards(value, parent=""):
    # Pydantic's legacy FiniteFloat accepts numeric strings/bools. Public business
    # functions require actual JSON numbers; the old snapshot schema stays intact.
    if isinstance(value, dict):
        for key, item in value.items():
            numeric = key in _NUMERIC_KEYS or parent in {"resource_requirements", "resource_capacities"}
            if numeric and item is not None:
                if isinstance(item, bool) or not isinstance(item, Real):
                    raise ValueError(f"{key}必须是有限数值，不接受bool或字符串")
                try:
                    finite = math.isfinite(item)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ValueError(f"{key}超出有限数值范围")
            _numeric_guards(item, key)
    elif isinstance(value, list):
        for item in value:
            _numeric_guards(item, parent)


def _inputs(snapshot, request, *, scenario_only=False):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("snapshot_id"), str):
        raise ValueError("snapshot必须包含snapshot_id和payload")
    raw = snapshot.get("payload")
    _numeric_guards(raw)
    data = SnapshotPayload.model_validate(raw)
    reqraw = request.model_dump(mode="json") if isinstance(request, ScenarioRequest) else request
    _numeric_guards(reqraw)
    if scenario_only and isinstance(reqraw, dict):
        reqraw = {k: v for k, v in reqraw.items() if k in ScenarioRequest.model_fields}
    req = (ScenarioRequest if scenario_only else DecisionRequest).model_validate(reqraw)
    if req.snapshot_id != snapshot["snapshot_id"] or req.as_of != data.as_of:
        raise ValueError("请求对象或时点与冻结快照不一致")
    return data, req


def _invalid(exc):
    return _result("invalid_input", reasons=[str(exc)], missing_items=[], utility=None)


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_decision_baseline(snapshot, request, *, target_service_ids=None):
    """Fingerprint the actual frozen payload, not a caller-supplied SHA string.

    Budget/candidate selection are excluded from the effect baseline. The full
    payload (including source, mappings, actions and profiles) is hashed. Different
    value profiles therefore create a new baseline even when topology is unchanged.
    """
    try:
        data, req = _inputs(snapshot, request, scenario_only=True)
        registered = {s.service_id for s in data.services}
        targets = sorted(registered) if target_service_ids is None else target_service_ids
        if not isinstance(targets, list) or any(not isinstance(x, str) for x in targets):
            raise ValueError("target_service_ids必须是字符串列表")
        if len(targets) != len(set(targets)) or not set(targets) <= registered:
            raise ValueError("目标服务重复或不属于当前快照")
        payload = data.model_dump(mode="json")
        payload["as_of"] = data.as_of.astimezone(timezone.utc).isoformat()
        baseline = {
            "snapshot_id": snapshot["snapshot_id"], "payload_sha256": _digest(payload),
            "as_of": req.as_of.astimezone(timezone.utc).isoformat(),
            "fault_edge_id": req.fault_edge_id, "isolation_plan_id": req.isolation_plan_id,
            "horizon_hours": req.horizon_hours, "target_service_ids": sorted(targets),
            "service_criterion_id": "topological_reachability", "service_criterion_version": "1.0",
            "topology_engine_version": TOPOLOGY_VERSION, "metric_definition_version": METRIC_VERSION,
            "coverage_scope": "registered_services_only",
        }
        return _result("succeeded", baseline_key=_digest(baseline), baseline=baseline)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return _invalid(exc)


def check_plan_constraints(snapshot, request, plan_id):
    """D01: pass/fail/unknown checks; known violations dominate missing values."""
    try:
        data, req = _inputs(snapshot, request)
        plans = {p.plan_id: p for p in data.plans}
        actions = {a.action_id: a for a in data.actions}
        if not isinstance(plan_id, str) or plan_id not in plans:
            raise ValueError("方案不属于当前快照")
        if plan_id not in req.plan_ids:
            raise ValueError("方案不属于本次请求的候选集合")
        if not set(req.mandatory_action_ids) <= actions.keys():
            raise ValueError("强制措施不属于当前快照")
        plan = plans[plan_id]
        selected = [actions[x] for x in plan.action_ids]
        chosen = set(plan.action_ids)
        edges = {e.edge_id: e for e in data.edges}
        checks = []
        def add(name, state, reason, left=None, right=None, unit=None):
            checks.append({"constraint_id": name, "state": state, "reason": reason,
                           "left": left, "right": right, "unit": unit})
        issues = check_inputs(snapshot, req)
        add("scenario", "unknown" if issues else "pass", "；".join(issues) or "运行网络及给定隔离可计算")
        isolation = next((p for p in data.isolation_plans if p.plan_id == req.isolation_plan_id), None)
        if isolation is not None:
            for vid in isolation.valve_ids:
                operable = edges[vid].operable
                add("isolation:operable:"+vid, "unknown" if operable is None else ("pass" if operable else "fail"),
                    "检查给定隔离阀门可操作性", operable, True)
        add("mandatory", "pass" if set(req.mandatory_action_ids) <= chosen else "fail", "检查强制措施同选")
        known_cost, cost_complete, usage, usage_complete = 0, True, defaultdict(float), True
        unsupported = []
        overrides = {}
        for a in selected:
            aid = a.action_id
            add(aid+":applicability", "pass" if req.fault_edge_id in a.fault_edge_ids else "fail", "检查措施适用故障")
            add(aid+":dependencies", "pass" if set(a.requires_ids) <= chosen else "fail", "前置措施同选；不代表已校验施工先后")
            add(aid+":conflicts", "fail" if set(a.conflicts_with) & chosen else "pass", "检查互斥措施")
            if a.cost_fen is None:
                cost_complete = False
            else:
                known_cost += a.cost_fen
            add(aid+":cost", "unknown" if a.cost_fen is None else "pass", "措施费用是否提供", a.cost_fen, unit="fen")
            if a.resource_requirements is None:
                usage_complete = False
                add(aid+":resources", "unknown", "资源需求未提供；空字典表示明确无需求")
            else:
                add(aid+":resources", "pass", "资源需求已提供")
                for resource, amount in a.resource_requirements.items():
                    usage[resource] += amount
                    if not math.isfinite(usage[resource]):
                        raise ValueError("资源累计溢出")
            for vid in sorted(set(a.open_valve_ids+a.close_valve_ids)):
                op = edges[vid].operable
                add(aid+":operable:"+vid, "unknown" if op is None else ("pass" if op else "fail"),
                    "检查措施阀门可操作性", op, True)
            for vid, state in [(v,"open") for v in a.open_valve_ids]+[(v,"closed") for v in a.close_valve_ids]:
                if vid in overrides and overrides[vid] != state:
                    add(aid+":valve_sequence:"+vid, "unknown", "同一阀门存在相反操作，终点合同不推断操作时序")
                overrides[vid] = state
            if set(a.restore_edge_ids)-{req.fault_edge_id}:
                unsupported.append("当前仅支持显式恢复本次指定故障边")
            if plan.offline_effect is None:
                add(aid+":effect", "pass" if a.evidence_ref and a.evidence_ref.strip() and a.effective_after_hours is not None else "unknown",
                    "检查效果依据及生效偏移；不是任务结束时间")
                if a.backup_service_ids:
                    values = (a.backup_capacity_m3h, a.backup_demand_m3h, a.backup_duration_hours)
                    add(aid+":backup", "unknown" if any(x is None for x in values) else "pass", "检查备用容量、总需求及持续时间")
        budget_state = "fail" if known_cost > req.budget_fen else ("pass" if cost_complete else "unknown")
        add("budget", budget_state, "已知费用超过预算" if budget_state=="fail" else "完整费用与共同预算比较", known_cost, req.budget_fen, "fen")
        for resource, used in sorted(usage.items()):
            cap = req.resource_capacities.get(resource)
            state = "unknown" if cap is None else ("fail" if used > cap else ("pass" if usage_complete else "unknown"))
            add("resource:"+resource, state, "整个窗口累计资源；单位由相同资源ID字典定义", used, cap, "resource_unit:"+resource)
        violations = [x["reason"] for x in checks if x["state"]=="fail"]
        missing = [x["reason"] for x in checks if x["state"]=="unknown"]
        status = "infeasible" if violations else ("unsupported" if unsupported else ("insufficient_data" if missing else "succeeded"))
        return _result(status, plan_id=plan_id, label=plan.label, action_ids=plan.action_ids,
                       checks=checks, known_total_cost_fen=known_cost,
                       total_cost_fen=known_cost if cost_complete else None,
                       resource_usage=dict(usage), resource_usage_complete=usage_complete,
                       missing_items=missing, violation_items=violations, reasons=violations+missing+unsupported,
                       evidence_refs=[a.evidence_ref for a in selected if a.evidence_ref], utility=None)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return _invalid(exc)


def evaluate_plan_effects(snapshot, request, plan_id, *, offline_provenance=None):
    """D02: recompute a whole plan from the same frozen fault baseline.

    offline_provenance is a sidecar declaration, never an assertion that we verified
    a professional model. It is required because legacy OfflineEffect lacks hashes.
    """
    checked = check_plan_constraints(snapshot, request, plan_id)
    if checked["status"] != "succeeded":
        return checked
    try:
        data, req = _inputs(snapshot, request)
        baseline = build_decision_baseline(snapshot, req)
        base = simulate(snapshot, req)
        if not data.services or base["status"] not in {"succeeded", "partial"}:
            return _result("insufficient_data", plan_id=plan_id, reasons=base.get("missing_items", []) or ["缺少服务映射"], utility=None)
        plan = next(p for p in data.plans if p.plan_id==plan_id)
        actions = {a.action_id:a for a in data.actions}
        selected = [actions[x] for x in plan.action_ids]
        backup, notes, restored, overrides, times = set(), [], set(), {}, []
        evidence = list(checked["evidence_refs"])
        if plan.offline_effect is not None:
            effect = plan.offline_effect
            required = {"baseline_key", "effect_model_version", "metric_definition_version", "service_criterion_id", "evidence_ref"}
            if not isinstance(offline_provenance, dict) or any(not isinstance(offline_provenance.get(k), str) or not offline_provenance[k].strip() for k in required):
                return _result("insufficient_data", plan_id=plan_id, baseline_key=baseline["baseline_key"], reasons=["离线效果缺少基准指纹、模型版本、指标定义、服务判据及来源旁路声明"], utility=None)
            if (offline_provenance["baseline_key"] != baseline["baseline_key"]
                or offline_provenance["metric_definition_version"] != METRIC_VERSION
                or offline_provenance["service_criterion_id"] != "topological_reachability"
                or effect.as_of != req.as_of or effect.fault_edge_id != req.fault_edge_id
                or effect.isolation_plan_id != req.isolation_plan_id or effect.horizon_hours != req.horizon_hours):
                return _result("insufficient_data", plan_id=plan_id, reasons=["离线效果的内容指纹、情景或指标口径与当前基准不一致"], utility=None)
            if effect.restoration_hours is not None and effect.restoration_hours > req.horizon_hours:
                return _result("insufficient_data", plan_id=plan_id, reasons=["离线恢复时间在分析窗口外"], utility=None)
            if not set(effect.backup_covered_critical_service_ids) <= set(base["affected_critical_facility_ids"]):
                return _result("insufficient_data", plan_id=plan_id, reasons=["离线备用覆盖不属于原故障影响的关键服务"], utility=None)
            affected = sorted(effect.remaining_disconnected_service_ids)
            normal_nodes = {n.node_id for n in data.nodes} - set(base["normal_unreachable_node_ids"])
            normal_services = {s.service_id for s in data.services
                               if (any if s.supply_rule == "any" else all)(n in normal_nodes for n in s.node_ids)}
            if not set(affected) <= normal_services:
                raise ValueError("离线新增断连不能包含正常状态已不可达的服务记录")
            critical_backup = set(effect.backup_covered_critical_service_ids)
            restoration = effect.restoration_hours
            semantics = ("provider_declared_legacy_last_network_effect"
                         if offline_provenance.get("restoration_hours_semantics") == "legacy_last_network_effect"
                         else "provider_declared_unverified")
            impact = {"status":"succeeded", "engine_type":"offline_scenario", "affected_service_ids":affected,
                      "evidence_refs":[effect.evidence_ref], "note":"接收同指纹声明的离线结果，未在本地复算专业模型"}
            evidence += [effect.evidence_ref, offline_provenance["evidence_ref"]]
        else:
            for a in sorted(selected, key=lambda x:(x.effective_after_hours,x.action_id)):
                if a.effective_after_hours > req.horizon_hours:
                    continue
                restored.update(a.restore_edge_ids)
                overrides.update({v:"open" for v in a.open_valve_ids})
                overrides.update({v:"closed" for v in a.close_valve_ids})
                if a.restore_edge_ids or a.open_valve_ids or a.close_valve_ids:
                    times.append(a.effective_after_hours)
                if a.backup_service_ids:
                    enough = a.backup_capacity_m3h >= a.backup_demand_m3h
                    lasts = a.backup_duration_hours >= req.horizon_hours-a.effective_after_hours
                    if enough and lasts:
                        backup.update(a.backup_service_ids)
                    notes.append({"action_id":a.action_id, "capacity_sufficient":enough,
                                  "lasts_to_horizon":lasts, "starts_after_hours":a.effective_after_hours,
                                  "note":"仅检查输入总容量/需求及从启动到终点持续条件；不证明启动前、水质、输送及接驳达标"})
            impact = simulate(snapshot, req, restored=restored, overrides=overrides)
            if impact["status"] not in {"succeeded", "partial"}:
                return _result("insufficient_data", plan_id=plan_id, reasons=impact.get("missing_items", []), utility=None)
            affected = impact["affected_service_ids"]
            critical_backup = backup & set(base["affected_critical_facility_ids"])
            restoration = max(times) if times and not affected else None
            semantics = "legacy_last_network_effect"
        metrics = {"remaining_disconnected_services":len(affected), "total_cost_fen":checked["total_cost_fen"],
                   "restoration_hours":restoration, "backup_covered_critical_services":len(critical_backup)}
        # Unknown population counts do not erase known service-record metrics.
        warnings = list(dict.fromkeys(base.get("missing_items", [])+impact.get("missing_items", [])))
        return _result("succeeded", plan_id=plan_id, label=plan.label, action_ids=plan.action_ids,
                       baseline_key=baseline["baseline_key"], baseline=baseline["baseline"],
                       baseline_impact=base, impact=impact, metrics=metrics,
                       metric_units=dict(METRIC_UNITS), restoration_hours_semantics=semantics,
                       restored_service_ids=sorted(set(base["affected_service_ids"])-set(affected)),
                       newly_affected_service_ids=sorted(set(affected)-set(base["affected_service_ids"])),
                       remaining_disconnected_service_ids=sorted(affected),
                       backup_covered_critical_service_ids=sorted(critical_backup), backup_conditions=notes,
                       resource_usage=checked["resource_usage"], evidence_refs=list(dict.fromkeys(evidence)),
                       coverage_scope="registered_services_only", warnings=warnings, reasons=[], utility=None,
                       constraints=checked)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return _invalid(exc)


def score_fixed_utility(metrics, value_profile, *, baseline_key=None):
    """D03: fixed anchors/weights. Missing selected metrics never reweight others."""
    try:
        if not isinstance(metrics, dict):
            raise ValueError("metrics必须是指标对象")
        raw = value_profile.model_dump(mode="json") if isinstance(value_profile, ValueProfile) else value_profile
        _numeric_guards(raw)
        profile = ValueProfile.model_validate(raw)
        if not profile.evidence_ref.strip() or not profile.version.strip():
            raise ValueError("价值配置必须有版本及来源")
        if not math.isclose(sum(c.weight for c in profile.criteria), 1, rel_tol=0, abs_tol=1e-9):
            raise ValueError("权重之和须在绝对1e-9容差内等于1")
        if baseline_key is not None and (not isinstance(baseline_key,str) or len(baseline_key)!=64 or any(c not in "0123456789abcdef" for c in baseline_key)):
            raise ValueError("baseline_key必须为64位小写SHA256或None")
        breakdown, missing, total, warnings = [], [], 0.0, []
        for c in profile.criteria:
            value = metrics.get(c.metric)
            if value is None:
                missing.append("效用指标"+c.metric+"缺少可用值")
                continue
            if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value) or value<0:
                raise ValueError(c.metric+"必须是有限非负数值")
            if c.metric != "restoration_hours" and int(value)!=value:
                raise ValueError(c.metric+"必须是整数计数/分")
            width = c.upper-c.lower
            if not math.isfinite(width) or width<=0:
                raise ValueError("效用尺度跨度必须为有限正数")
            ratio = (value-c.lower)/width
            if not math.isfinite(ratio):
                raise ValueError("归一运算超出有限数值范围")
            normalized = max(0.0,min(1.0,ratio if c.direction=="max" else 1-ratio))
            contribution = normalized*c.weight
            total += contribution
            clipped = value<c.lower or value>c.upper
            if clipped:
                warnings.append(c.metric+"超出固定锚点，已按clip截断")
            breakdown.append({**c.model_dump(),"value":value,"unit":METRIC_UNITS[c.metric],
                              "normalized":normalized,"contribution":contribution,"clipped":clipped})
        return _result("insufficient_data" if missing else "succeeded", utility=None if missing else round(total,10),
                       utility_breakdown=breakdown, baseline_key=baseline_key,
                       value_profile=profile.model_dump(mode="json"), value_profile_key=_digest(profile.model_dump(mode="json")),
                       reasons=missing, missing_items=missing, warnings=warnings)
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return _invalid(exc)


def rank_candidate_plans(snapshot, request, *, offline_provenance=None):
    """D04: compare only given candidates. Unknown effects suppress recommendation."""
    try:
        data, req = _inputs(snapshot, request)
        base = simulate(snapshot,req)
        baseline = build_decision_baseline(snapshot,req)
        if base["status"] not in {"succeeded","partial"}:
            return {**base,"decision_algorithm_version":VERSION,"recommended_plan_id":None,"tie_plan_ids":[]}
        if not data.services or data.value_profile is None:
            return envelope(snapshot,"insufficient_data",decision_algorithm_version=VERSION,
                            recommended_plan_id=None,tie_plan_ids=[],missing_items=["方案比较需要服务映射与明确价值配置"])
        if not set(req.plan_ids) <= {p.plan_id for p in data.plans}:
            raise ValueError("候选方案不属于当前快照")
        if offline_provenance is not None and not isinstance(offline_provenance,dict):
            raise ValueError("offline_provenance须按plan_id组织")
        rows=[]
        for pid in req.plan_ids:
            row=evaluate_plan_effects(snapshot,req,pid,offline_provenance=(offline_provenance or {}).get(pid))
            if row["status"]=="succeeded":
                if (any(c.metric == "restoration_hours" for c in data.value_profile.criteria)
                    and row["restoration_hours_semantics"] == "provider_declared_unverified"):
                    score = _result("insufficient_data", utility=None, utility_breakdown=[], warnings=[],
                                    reasons=["离线restoration_hours未声明与legacy_last_network_effect同口径，不能参与时间效用比较"])
                else:
                    score=score_fixed_utility(row["metrics"],data.value_profile,baseline_key=baseline["baseline_key"])
                row.update(status=score["status"], utility=score["utility"],utility_breakdown=score.get("utility_breakdown",[]),
                           reasons=score.get("reasons",[]),value_profile_key=score.get("value_profile_key"))
                row["warnings"]+=score.get("warnings",[])
            rows.append(row)
        feasible=sorted([r for r in rows if r["status"]=="succeeded"],key=lambda r:(-r["utility"],r["plan_id"]))
        unknown=[r for r in rows if r["status"] not in {"succeeded","infeasible"}]
        unresolved_statuses = {r["status"] for r in unknown}
        status = next((s for s in ("invalid_input", "unsupported", "insufficient_data") if s in unresolved_statuses),
                      "insufficient_data" if unknown else ("succeeded" if feasible else "infeasible"))
        recommended=feasible[0]["plan_id"] if feasible and not unknown else None
        ties=[r["plan_id"] for r in feasible if r["utility"]==feasible[0]["utility"]] if feasible else []
        for row in rows:
            if row["status"]=="succeeded":
                row["selection_reason"]="在已给定的可行候选中按固定配置比较；同分按ID展示，不表示唯一最优。" if not unknown else "候选存在未知结果，仅展示已知得分，不作最终推荐。"
            else:
                row["selection_reason"]="；".join(row.get("reasons",[]))
        return envelope(snapshot,status,engine_type="demo_policy",decision_algorithm_version=VERSION,
                        baseline_key=baseline["baseline_key"],baseline=baseline["baseline"],
                        scenario=req.model_dump(mode="json"),baseline_impact=base,plans=rows,
                        recommended_plan_id=recommended,recommendation_status="pending_review" if recommended else None,
                        tie_plan_ids=ties if len(ties)>1 else [],ranked_plan_ids=[r["plan_id"] for r in feasible],
                        value_profile=data.value_profile.model_dump(mode="json"),
                        missing_items=[s for r in unknown for s in r.get("reasons",[])],
                        decision_limitations=["只比较给定候选，不是全局优化；配置为demo_only。",
                                              "资源为整个窗口累计占用，未做分时调度。",
                                              "指标按已登记服务记录统计，不证明映射或人口覆盖完整。",
                                              "备用覆盖仅为容量与时长条件，不是水力、水质或接驳验证。"])
    except (ValidationError, ValueError, TypeError, OverflowError) as exc:
        return {**_invalid(exc),"recommended_plan_id":None,"tie_plan_ids":[]}
