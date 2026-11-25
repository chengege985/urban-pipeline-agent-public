"""Event replay for a declared plan, restricted to topological reachability.

This is a planned, closed-world scenario: only the supplied fault, isolation and
explicit network actions occur. It is not an observed outage history, hydraulic
forecast, resource schedule or claim of physical service restoration. All changes
are made to in-memory graphs. Backup delivery never makes a graph node reachable.
"""
from __future__ import annotations

from collections import defaultdict

from pydantic import ValidationError

from src.decision.recovery import RecoveryTimeRequest, compute_recovery_times
from src.platform.schemas import DecisionRequest, ScenarioRequest, SnapshotPayload
from src.simulation.topology import simulate

VERSION = "1.0.1"
CRITERION_VERSION = "1.0"
LIMITATIONS = [
    "Planned closed-world replay: no unlisted failures, source changes or actions are predicted.",
    "Reachability is not pressure, flow, water-quality compliance or actual supply.",
    "Backup delivery has no topological effect; its start/expiry is not a graph service event.",
    "Action effective times are not task finish times; missing schedules remain unknown.",
    "Targets and results cover registered services only; no claim of complete network coverage.",
    "This replay does not certify budget, resource feasibility or execution scheduling.",
]


def build_plan_service_timeline(
    snapshot: dict,
    request: ScenarioRequest | DecisionRequest | dict,
    plan_id: str,
    *,
    target_service_ids: list[str],
    action_schedule: list[dict] | None = None,
    criterion_id: str = "topological_reachability",
) -> dict:
    """Replay simultaneous network actions atomically and call the T01 summarizer.

    Explicit targets may include already-unreachable services; they are never
    inferred from newly-affected counts. Opposite valve operations at different
    known times are legal; contradictory operations at the same time are invalid.
    At t=0, zero-offset actions have already been applied (right-continuous state);
    the pre-action state is returned separately. No task completion is inferred.
    """
    base = {"algorithm_version": VERSION, "criterion_id": criterion_id,
            "criterion_version": CRITERION_VERSION, "time_basis": "planned",
            "validation_status": "not_business_validated", "limitations": list(LIMITATIONS)}

    def stop(status, *reasons):
        return {**base, "status": status, "timeline_status": status,
                "issues": list(reasons), "timeline_request": None, "recovery_times": None}

    if criterion_id != "topological_reachability":
        return stop("unsupported", "Only explicit topological reachability can be generated locally.")
    try:
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("payload"), dict):
            return stop("invalid_input", "snapshot must contain a payload mapping")
        raw_request = request.model_dump() if isinstance(request, (ScenarioRequest, DecisionRequest)) else request
        if not isinstance(raw_request, dict):
            return stop("invalid_input", "request must be a scenario or decision mapping")
        # Legacy schema permits number coercion; this public boundary does not.
        horizon = raw_request.get("horizon_hours", 8)
        if isinstance(horizon, bool) or not isinstance(horizon, (int, float)):
            return stop("invalid_input", "horizon_hours must be a finite number, not a string or bool")
        for action in snapshot["payload"].get("actions", []):
            if isinstance(action, dict):
                value = action.get("effective_after_hours")
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                    return stop("invalid_input", "effective_after_hours must be numeric or null")
        cls = DecisionRequest if "plan_ids" in raw_request else ScenarioRequest
        req = cls.model_validate(raw_request)
        data = SnapshotPayload.model_validate(snapshot["payload"])
        if not isinstance(plan_id, str) or not plan_id.strip():
            return stop("invalid_input", "plan_id must be a nonempty string")
        if (not isinstance(target_service_ids, list)
                or any(not isinstance(s, str) or not s.strip() for s in target_service_ids)
                or len(target_service_ids) != len(set(target_service_ids))):
            return stop("invalid_input", "target_service_ids must be a unique list of service IDs")
        services = {s.service_id: s for s in data.services}
        if not set(target_service_ids) <= services.keys():
            return stop("invalid_input", "target_service_ids contains an unknown registered service")
        plan = next((p for p in data.plans if p.plan_id == plan_id), None)
        if plan is None:
            return stop("invalid_input", "plan_id is not in this snapshot")
        if isinstance(req, DecisionRequest) and plan_id not in req.plan_ids:
            return stop("invalid_input", "plan_id is not a requested candidate")
        if plan.offline_effect is not None:
            return stop("unsupported", "An offline endpoint effect cannot establish an event timeline.")
        # Includes full payload, clock, fault/isolation/horizon, target set and versions.
        from src.decision.contracts import build_decision_baseline
        baseline = build_decision_baseline(snapshot, req, target_service_ids=target_service_ids)
        if baseline["status"] != "succeeded":
            return {**base, **baseline, "timeline_status": baseline["status"],
                    "timeline_request": None, "recovery_times": None}
        base.update({"snapshot_id": snapshot["snapshot_id"], "plan_id": plan_id,
                     "data_mode": data.data_mode, "source_status": data.source_status,
                     "baseline_key": baseline["baseline_key"], "baseline": baseline["baseline"]})
        initial_impact = simulate(snapshot, req)
        if initial_impact["status"] not in {"succeeded", "partial"}:
            return stop("insufficient_data", *initial_impact.get("missing_items", []))
        if not data.services:
            return stop("insufficient_data", "No registered service mapping is available")

        by_id = {a.action_id: a for a in data.actions}
        selected = [by_id[aid] for aid in plan.action_ids]
        ids = set(plan.action_ids)
        edges = {e.edge_id: e for e in data.edges}
        groups = defaultdict(list)
        ignored, evidence, missing, violations = [], ["snapshot:" + snapshot["snapshot_id"]], [], []
        deferred = []
        for action in selected:
            if req.fault_edge_id not in action.fault_edge_ids:
                violations.append(f"Action {action.action_id} is not applicable to this fault")
            if not set(action.requires_ids) <= ids:
                violations.append(f"Action {action.action_id} is missing a co-selected prerequisite")
            if set(action.conflicts_with) & ids:
                violations.append(f"Action {action.action_id} conflicts with a co-selected action")
            if set(action.open_valve_ids) & set(action.close_valve_ids):
                return stop("invalid_input", f"Action {action.action_id} opens and closes the same valve")
            if set(action.restore_edge_ids) - {req.fault_edge_id}:
                return stop("unsupported", "Restoring an edge other than the declared fault is not modeled")
            network_action = bool(action.restore_edge_ids or action.open_valve_ids or action.close_valve_ids)
            if not network_action:
                ignored.append(action.action_id)
                continue
            if action.effective_after_hours is None:
                missing.append(f"Network action {action.action_id} has no effective time")
            outside = action.effective_after_hours is not None and action.effective_after_hours > req.horizon_hours
            effect_missing = deferred if outside else missing
            if not action.evidence_ref or not action.evidence_ref.strip():
                effect_missing.append(f"Network action {action.action_id} has no effect evidence")
            else:
                evidence.append(action.evidence_ref)
            for vid in action.open_valve_ids + action.close_valve_ids:
                if edges[vid].operable is False:
                    violations.append(f"Valve {vid} is not operable")
                elif edges[vid].operable is None:
                    effect_missing.append(f"Valve {vid} operability is unknown")
            if action.effective_after_hours is not None:
                groups[action.effective_after_hours].append(action)
        if violations:
            return stop("infeasible", *(violations + missing))
        if missing:
            return stop("insufficient_data", *missing)

        operations = {}
        for at, actions in groups.items():
            restores, switches = set(), {}
            for action in actions:
                restores.update(action.restore_edge_ids)
                for vid, state in [(v, "open") for v in action.open_valve_ids] + [(v, "closed") for v in action.close_valve_ids]:
                    if vid in switches and switches[vid] != state:
                        return stop("invalid_input", f"Opposite simultaneous operations on {vid} at {at} h")
                    switches[vid] = state
            operations[at] = (restores, switches)

        def availability(impact):
            reachable = set(impact["reachable_node_ids"])
            return {sid: (any if services[sid].supply_rule == "any" else all)(
                        n in reachable for n in services[sid].node_ids)
                    for sid in sorted(target_service_ids)}

        pre_action = availability(initial_impact)
        states = pre_action.copy()
        restored, overrides, events, graph_events = set(), {}, [], []
        for at in sorted(t for t in operations if t <= req.horizon_hours):
            restores, switches = operations[at]
            restored.update(restores)
            overrides.update(switches)
            impact = simulate(snapshot, req, restored=restored, overrides=overrides)
            updated = availability(impact)
            changes = {sid: val for sid, val in updated.items() if states[sid] != val}
            graph_events.append({"at_hours": at, "action_ids": sorted(a.action_id for a in groups[at]),
                                 "restored_edge_ids": sorted(restores), "valve_overrides": switches,
                                 "service_availability": updated})
            if changes and at > 0:
                events.append({"at_hours": at, "changes": changes})
            states = updated
        # T01 t=0 denotes the right-continuous state after any zero-offset actions.
        initial = next((e["service_availability"] for e in graph_events if e["at_hours"] == 0), pre_action)
        timeline = RecoveryTimeRequest.model_validate({
            "as_of": req.as_of, "horizon_hours": req.horizon_hours,
            "plan_id": plan_id, "action_ids": plan.action_ids,
            "action_schedule": [] if action_schedule is None else action_schedule,
            "target_service_ids": sorted(target_service_ids), "time_basis": "planned",
            "criterion_id": criterion_id, "criterion_version": CRITERION_VERSION,
            "initial_availability": initial, "availability_events": events,
            "timeline_complete_until_hours": req.horizon_hours,
            "event_evidence_refs": sorted(set(evidence)),
        })
        recovery = compute_recovery_times(timeline)
        return {**base, "status": recovery["status"], "timeline_status": "succeeded",
                "scenario": req.model_dump(mode="json"),
                "timeline_scope": "closed_world_declared_actions", "timeline_request": timeline.model_dump(mode="json"),
                "pre_action_availability": pre_action, "graph_events": graph_events,
                "ignored_topological_action_ids": sorted(ignored),
                "beyond_horizon_action_ids": sorted(a.action_id for t, acts in groups.items()
                                                     if t > req.horizon_hours for a in acts),
                "deferred_validation_issues": deferred,
                "plan_feasibility_checked": False,
                "recovery_times": recovery}
    except (ValidationError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValidationError):
            return stop("invalid_input", *[".".join(map(str, e["loc"])) + ": " + e["msg"]
                                           for e in exc.errors(include_context=False, include_input=False)])
        return stop("invalid_input", str(exc))
