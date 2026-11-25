"""Read-only semantic contracts for Report projection; no business computation.

Run and Snapshot must be supplied by the authorized platform store. This checks
their result relationships, not a cryptographic proof of execution. Only the
registered result types below are accepted; arbitrary execution receipts are not.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

from pydantic import ValidationError

from src.platform.schemas import DecisionRequest, RecomputeRequest, ScenarioRequest, SnapshotPayload
from src.platform.store import fingerprint
from src.unified.contracts import SimulationRunResult, parse_run_result


class IntegrityConflict(ValueError):
    pass


def _fail(path: str) -> None:
    # Do not return conflicting data or identities through the HTTP error body.
    raise IntegrityConflict(f"Report结果完整性合同不满足：{path}")


def _equal(actual: Any, expected: Any, path: str) -> None:
    # JSON comparison distinguishes bool from number, unlike Python ==.
    if fingerprint(actual) != fingerprint(expected):
        _fail(path)


def _object(value: Any, path: str, allowed: set[str], required=()) -> dict:
    if not isinstance(value, dict) or set(value) - allowed or set(required) - set(value):
        _fail(path)
    return value


def _number(value: Any, path: str, *, nullable=False, integer=False) -> None:
    # Validate the stored JSON value without coercing or replacing it. In
    # particular, bool is not a number and nonfinite values must not become null.
    if value is None and nullable:
        return
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        _fail(path)
    if integer and int(value) != value:
        _fail(path)


def _ids(value: Any, allowed: set[str], path: str) -> None:
    if (not isinstance(value, list) or any(type(x) is not str or x not in allowed for x in value)
            or len(value) != len(set(value))):
        _fail(path)


def _instant(value: Any, path: str) -> datetime:
    if not isinstance(value, str):
        _fail(path)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.utcoffset() is None:
            _fail(path)
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        _fail(path)


ENVELOPE = set("status snapshot_id as_of data_mode source_status validation_status engine_type algorithm_version evidence_refs limitations".split())
PERSISTED = {"run_id", "task_version", "artifact_ref"}
TOPOLOGY = ENVELOPE | set("scenario source_node_ids normal_unreachable_node_ids newly_unreachable_node_ids affected_service_ids affected_critical_facility_ids potentially_disconnected_counts known_count_subtotals unknown_count_service_ids isolated_valve_ids restored_edge_ids valve_overrides reachable_node_ids normal_path_examples alternative_path_examples missing_items".split())
CONSTRAINT = set("status decision_algorithm_version plan_id label action_ids checks known_total_cost_fen total_cost_fen resource_usage resource_usage_complete missing_items violation_items reasons evidence_refs utility".split())
EFFECT = CONSTRAINT | set("baseline_key baseline baseline_impact impact metrics metric_units restoration_hours_semantics restored_service_ids newly_affected_service_ids remaining_disconnected_service_ids backup_covered_critical_service_ids backup_conditions coverage_scope warnings constraints utility_breakdown value_profile_key selection_reason".split())
DECISION = ENVELOPE | PERSISTED | set("decision_algorithm_version baseline_key baseline scenario baseline_impact plans recommended_plan_id recommendation_status tie_plan_ids ranked_plan_ids value_profile missing_items decision_limitations".split())
ASSESSMENT = PERSISTED | set("status snapshot_id data_mode model_family model_version assessment_count validation_status evidence_refs".split())
UNITS = {"remaining_disconnected_services": "service_record", "total_cost_fen": "fen",
         "restoration_hours": "hour", "backup_covered_critical_services": "service_record"}


class ReportAuthority:
    """Finite validation tree, rooted only in authorized Run/Snapshot inputs."""

    def __init__(self, run: dict, snapshot: dict):
        self.run, self.snapshot = run, snapshot
        self.rid, self.sid = run.get("run_id"), snapshot.get("snapshot_id")
        if any(type(x) is not str or not x.strip() for x in (self.rid, self.sid)):
            _fail("Run/Snapshot identity")
        self.data = SnapshotPayload.model_validate(snapshot["payload"])
        self.payload = self.data.model_dump(mode="json")
        digest = fingerprint(snapshot["payload"])
        _equal(snapshot.get("sha256"), digest, "Snapshot.sha256")
        _equal(self.sid, "snap-" + digest[:24], "Snapshot.snapshot_id")
        for field in ("as_of", "data_mode", "source_status", "name"):
            _equal(snapshot.get(field), snapshot["payload"].get(field), "Snapshot." + field)
        self.request = run.get("request")
        self.kind = run.get("kind")
        self.req = None
        if self.kind in {"simulation", "decision"}:
            model = DecisionRequest if self.kind == "decision" else ScenarioRequest
            self.req = model.model_validate(self.request)
            if self.req.snapshot_id != self.sid or self.req.as_of != self.data.as_of:
                _fail("Run.request")
        self.nodes = {n.node_id for n in self.data.nodes}
        self.edges = {e.edge_id for e in self.data.edges}
        self.valves = {e.edge_id for e in self.data.edges if e.is_valve}
        self.services = {s.service_id for s in self.data.services}
        self.actions = {a.action_id: a for a in self.data.actions}
        self.plans = {p.plan_id: p for p in self.data.plans}

    def anchors(self, refs: Any, path: str) -> None:
        if not isinstance(refs, list) or any(type(ref) is not str or not ref.strip() for ref in refs):
            _fail(path)
        if "snapshot:" + self.sid not in refs:
            _fail(path)
        for ref in refs:
            kind, _, identity = ref.partition(":")
            if kind in {"snapshot", "run"} and identity != {"snapshot": self.sid, "run": self.rid}[kind]:
                _fail(path)
            # These are registered source labels, not execution authentication.
            if kind not in {"snapshot", "run", "scenario", "isolation", "plan", "action", "rule", "algorithm"}:
                _fail(path)

    def envelope(self, block: dict, path: str) -> None:
        for key, expected in {"snapshot_id": self.sid, "data_mode": self.data.data_mode,
                              "source_status": self.data.source_status,
                              "validation_status": "not_business_validated"}.items():
            _equal(block.get(key), expected, path + "." + key)
        if _instant(block.get("as_of"), path + ".as_of") != self.data.as_of:
            _fail(path + ".as_of")
        _equal(block.get("algorithm_version"), "0.1.0", path + ".algorithm_version")
        self.anchors(block.get("evidence_refs"), path + ".evidence_refs")

    def scenario(self, block: Any, path: str, *, scenario_only=False) -> None:
        model = ScenarioRequest if scenario_only else type(self.req)
        required = set(model.model_fields)
        _object(block, path, required, required)
        expected = self.req.model_dump(mode="json")
        if scenario_only:
            expected = {k: v for k, v in expected.items() if k in required}
        # Only timestamps are normalized. Do not let Pydantic coerce ID/bool fields.
        actual = dict(block)
        actual["as_of"] = _instant(actual["as_of"], path + ".as_of").isoformat()
        expected["as_of"] = self.req.as_of.astimezone(timezone.utc).isoformat()
        _equal(actual, expected, path)

    def baseline_value(self, targets=None) -> dict:
        payload = dict(self.payload)
        payload["as_of"] = self.data.as_of.astimezone(timezone.utc).isoformat()
        return {"snapshot_id": self.sid, "payload_sha256": fingerprint(payload),
                "as_of": self.req.as_of.astimezone(timezone.utc).isoformat(),
                "fault_edge_id": self.req.fault_edge_id, "isolation_plan_id": self.req.isolation_plan_id,
                "horizon_hours": self.req.horizon_hours,
                "target_service_ids": sorted(self.services if targets is None else targets),
                "service_criterion_id": "topological_reachability", "service_criterion_version": "1.0",
                "topology_engine_version": "0.1.0", "metric_definition_version": "registered-service-metrics-1.0",
                "coverage_scope": "registered_services_only"}

    def baseline(self, block: Any, key: Any, path: str, *, targets=None) -> None:
        expected = self.baseline_value(targets)
        _object(block, path, set(expected), set(expected))
        _equal(block, expected, path)
        _equal(key, fingerprint(expected), path + "_key")

    def topology(self, block: Any, path: str, *, root=False, plan=None) -> None:
        _object(block, path, TOPOLOGY | (PERSISTED if root else set()))
        SimulationRunResult.model_validate(block)
        self.envelope(block, path)
        _equal(block.get("engine_type"), "topology", path + ".engine_type")
        if not root and "scenario" not in block:
            _fail(path + ".scenario")
        if "scenario" in block:
            self.scenario(block["scenario"], path + ".scenario")
        isolation = next((p for p in self.data.isolation_plans if p.plan_id == self.req.isolation_plan_id), None)
        if isolation is None:
            _fail(path + ".isolation_plan")
        if "source_node_ids" in block:
            _equal(block["source_node_ids"], sorted(n.node_id for n in self.data.nodes if n.source and n.active), path + ".source_node_ids")
        _equal(block.get("isolated_valve_ids"), isolation.valve_ids, path + ".isolated_valve_ids")
        allowed_restores = set()
        allowed_switches = {(vid, "closed") for vid in isolation.valve_ids}
        if plan is not None:
            for aid in plan.action_ids:
                action = self.actions[aid]
                allowed_restores.update(action.restore_edge_ids)
                allowed_switches.update((v, "open") for v in action.open_valve_ids)
                allowed_switches.update((v, "closed") for v in action.close_valve_ids)
        for field, ids in {
            "source_node_ids": self.nodes, "normal_unreachable_node_ids": self.nodes,
            "newly_unreachable_node_ids": self.nodes, "reachable_node_ids": self.nodes,
            "affected_service_ids": self.services, "affected_critical_facility_ids": self.services,
            "unknown_count_service_ids": self.services, "isolated_valve_ids": self.valves,
            "restored_edge_ids": allowed_restores,
        }.items():
            if field in block:
                _ids(block[field], ids, path + "." + field)
        if "valve_overrides" in block:
            overrides = _object(block["valve_overrides"], path + ".valve_overrides", self.valves)
            if any(v not in {"open", "closed"} for v in overrides.values()):
                _fail(path + ".valve_overrides")
            if not set(overrides.items()) <= allowed_switches:
                _fail(path + ".valve_overrides.plan")
        for field in ("normal_path_examples", "alternative_path_examples"):
            if field not in block:
                continue
            examples = _object(block[field], path + "." + field, self.nodes)
            for target, value in examples.items():
                _object(value, path + "." + field, {"nodes", "edges"}, {"nodes", "edges"})
                _ids(value["nodes"], self.nodes, path + ".path.nodes")
                _ids(value["edges"], self.edges, path + ".path.edges")
                if not value["nodes"] or value["nodes"][-1] != target:
                    _fail(path + ".path.target")

    def plan_identity(self, row: dict, path: str):
        pid = row.get("plan_id")
        if type(pid) is not str or pid not in self.req.plan_ids or pid not in self.plans:
            _fail(path + ".plan_id")
        plan = self.plans[pid]
        _equal(row.get("action_ids"), plan.action_ids, path + ".action_ids")
        _equal(row.get("decision_algorithm_version"), "1.0.0", path + ".decision_algorithm_version")
        return plan

    def constraints(self, row: Any, path: str, *, outer=False) -> None:
        _object(row, path, EFFECT if outer else CONSTRAINT)
        plan = self.plan_identity(row, path)
        refs = [self.actions[aid].evidence_ref for aid in plan.action_ids if self.actions[aid].evidence_ref]
        _equal(row.get("evidence_refs"), refs, path + ".evidence_refs")
        checks = row.get("checks")
        if not isinstance(checks, list):
            _fail(path + ".checks")
        for check in checks:
            _object(check, path + ".checks", {"constraint_id", "state", "reason", "left", "right", "unit"}, {"constraint_id", "state", "reason"})
            if check["state"] not in {"pass", "fail", "unknown"} or type(check["constraint_id"]) is not str:
                _fail(path + ".checks")
            if type(check["reason"]) is not str:
                _fail(path + ".checks.reason")

    def plan(self, row: Any, path: str) -> None:
        _object(row, path, EFFECT)
        plan = self.plan_identity(row, path)
        effect_fields = {"baseline", "baseline_impact", "impact", "metrics", "constraints"}
        if not (effect_fields & set(row)):
            if row.get("utility") is not None or row.get("status") == "succeeded":
                _fail(path + ".missing_effect")
            self.constraints(row, path, outer=True)
            return
        if not effect_fields <= set(row):
            _fail(path + ".effect")
        self.baseline(row["baseline"], row.get("baseline_key"), path + ".baseline")
        self.topology(row["baseline_impact"], path + ".baseline_impact")
        if plan.offline_effect is not None:
            # The current platform Run request persists no offline_provenance
            # sidecar. Do not certify a provider's self-declared execution.
            _fail(path + ".offline_effect_requires_authoritative_provenance")
        self.topology(row["impact"], path + ".impact", plan=plan)
        self.constraints(row["constraints"], path + ".constraints")
        _equal(row["constraints"].get("status"), "succeeded", path + ".constraints.status")
        _equal(row["constraints"].get("plan_id"), plan.plan_id, path + ".constraints.plan_id")
        refs = list(dict.fromkeys(self.actions[aid].evidence_ref for aid in plan.action_ids if self.actions[aid].evidence_ref))
        _equal(row.get("evidence_refs"), refs, path + ".evidence_refs")
        _equal(row.get("metric_units"), UNITS, path + ".metric_units")
        _object(row["metrics"], path + ".metrics", set(UNITS), set(UNITS))
        for metric, value in row["metrics"].items():
            # A missing metric is distinct from an invalid numeric value;
            # restoration_hours can be null even in a successful candidate.
            _number(value, path + ".metrics." + metric, nullable=True,
                    integer=metric != "restoration_hours")
        _equal(row.get("coverage_scope"), "registered_services_only", path + ".coverage_scope")
        _equal(row.get("restoration_hours_semantics"), "legacy_last_network_effect", path + ".restoration_hours_semantics")
        profile = self.data.value_profile.model_dump(mode="json")
        _equal(row.get("value_profile_key"), fingerprint(profile), path + ".value_profile_key")
        for field in ("restored_service_ids", "newly_affected_service_ids", "remaining_disconnected_service_ids", "backup_covered_critical_service_ids"):
            _ids(row.get(field), self.services, path + "." + field)
        conditions = row.get("backup_conditions")
        if not isinstance(conditions, list):
            _fail(path + ".backup_conditions")
        for condition in conditions:
            _object(condition, path + ".backup_conditions", {"action_id", "capacity_sufficient", "lasts_to_horizon", "starts_after_hours", "note"}, {"action_id"})
            if condition["action_id"] not in plan.action_ids:
                _fail(path + ".backup_conditions.action_id")
        criteria = {c.metric: c.model_dump(mode="json") for c in self.data.value_profile.criteria}
        breakdown = row.get("utility_breakdown")
        if not isinstance(breakdown, list):
            _fail(path + ".utility_breakdown")
        for item in breakdown:
            _object(item, path + ".utility_breakdown", {"metric", "direction", "lower", "upper", "weight", "value", "unit", "normalized", "contribution", "clipped"})
            metric = item.get("metric")
            if type(metric) is not str or metric not in criteria:
                _fail(path + ".utility_breakdown.metric")
            for key, value in criteria[metric].items():
                _equal(item.get(key), value, path + ".utility_breakdown." + key)
            _equal(item.get("unit"), UNITS[metric], path + ".utility_breakdown.unit")
            # Missing selected metrics produce no breakdown row. An existing
            # row contains real numbers and a boolean, never coerced strings.
            for field in ("value", "normalized", "contribution"):
                _number(item.get(field), path + ".utility_breakdown." + field,
                        integer=field == "value" and metric != "restoration_hours")
            if type(item.get("clipped")) is not bool:
                _fail(path + ".utility_breakdown.clipped")

    def validate(self, result: dict) -> None:
        _equal(result.get("run_id"), self.rid, "result.run_id")
        _equal(result.get("snapshot_id"), self.sid, "result.snapshot_id")
        _equal(result.get("status"), self.run.get("status"), "result.status")
        if "task_version" in result:
            _equal(result["task_version"], "0.1.0", "result.task_version")
        self.anchors(result.get("evidence_refs"), "result.evidence_refs")
        if parse_run_result(self.run) is None:
            _fail("result.kind")
        if self.kind == "assessment":
            _object(result, "assessment", ASSESSMENT)
            _equal(result.get("model_version"), "0.1.1", "assessment.model_version")
            _equal(result.get("data_mode"), self.data.data_mode, "assessment.data_mode")
            _equal(result.get("assessment_count"), len(self.data.assessments), "assessment.assessment_count")
            _equal(result.get("validation_status"), "not_business_validated", "assessment.validation_status")
            request = RecomputeRequest.model_validate(self.request)
            for key in ("name", "data_mode", "source_refs"):
                _equal(getattr(self.data, key), getattr(request, key), "assessment.request." + key)
            if self.data.as_of != request.as_of:
                _fail("assessment.request.as_of")
        elif self.kind == "simulation":
            self.topology(result, "simulation", root=True)
        elif self.kind == "decision":
            _object(result, "decision", DECISION)
            self.envelope(result, "decision")
            _equal(result.get("decision_algorithm_version"), "1.0.0", "decision.decision_algorithm_version")
            _equal(result.get("baseline_key"), fingerprint(self.baseline_value()), "decision.baseline_key")
            if "scenario" in result:
                self.scenario(result["scenario"], "decision.scenario")
            if "baseline" in result:
                self.baseline(result["baseline"], result["baseline_key"], "decision.baseline")
            if "baseline_impact" in result:
                self.topology(result["baseline_impact"], "decision.baseline_impact")
            if "value_profile" in result:
                _equal(result["value_profile"], self.data.value_profile.model_dump(mode="json"), "decision.value_profile")
            rows = result["plans"]
            _equal([r.get("plan_id") for r in rows], self.req.plan_ids, "decision.plans")
            for i, row in enumerate(rows):
                self.plan(row, f"decision.plans[{i}]")
            for field in ("ranked_plan_ids", "tie_plan_ids"):
                _ids(result.get(field, []), set(self.req.plan_ids), "decision." + field)
            if result.get("recommended_plan_id") is not None and result["recommended_plan_id"] not in self.req.plan_ids:
                _fail("decision.recommended_plan_id")
        # Locator consistency only; never read or recreate an artifact as a receipt.
        if "artifact_ref" in result:
            _equal(result["artifact_ref"], f"outputs/platform/{self.rid}/result.json", "result.artifact_ref")

    def timeline(self, value: Any) -> None:
        """Optional same-context attachment, not a receipt for this Run.

        The HTTP Report endpoint does not supply this parameter. Legacy internal
        callers may attach a G01 result; it must match the declared context and
        is explicitly labelled as not authenticated execution in Report limits.
        """
        from src.decision.recovery import RecoveryTimeRequest
        fields = set("algorithm_version criterion_id criterion_version time_basis validation_status limitations status timeline_status snapshot_id plan_id data_mode source_status baseline_key baseline scenario timeline_scope timeline_request pre_action_availability graph_events ignored_topological_action_ids beyond_horizon_action_ids deferred_validation_issues plan_feasibility_checked recovery_times issues".split())
        block = _object(value, "timeline", fields)
        if self.req is None:
            _fail("timeline.requires_scenario")
        for k, expected in {"snapshot_id": self.sid, "algorithm_version": "1.0.1",
                            "data_mode": self.data.data_mode, "source_status": self.data.source_status,
                            "criterion_id": "topological_reachability", "criterion_version": "1.0",
                            "time_basis": "planned", "validation_status": "not_business_validated"}.items():
            _equal(block.get(k), expected, "timeline." + k)
        pid = block.get("plan_id")
        if type(pid) is not str or pid not in self.plans:
            _fail("timeline.plan_id")
        if self.kind == "decision" and pid not in self.req.plan_ids:
            _fail("timeline.plan_id")
        raw_scenario = block.get("scenario")
        full_decision = isinstance(raw_scenario, dict) and "plan_ids" in raw_scenario
        self.scenario(raw_scenario, "timeline.scenario", scenario_only=not full_decision)
        request = RecoveryTimeRequest.model_validate(block.get("timeline_request"))
        _equal(request.plan_id, pid, "timeline.request.plan_id")
        _equal(request.action_ids, self.plans[pid].action_ids, "timeline.request.action_ids")
        _ids(request.target_service_ids, self.services, "timeline.request.targets")
        if request.as_of != self.req.as_of or request.horizon_hours != self.req.horizon_hours:
            _fail("timeline.request.clock")
        # The public TimelineResult intentionally omits both internal baseline
        # fields. Raw engine attachments may include both; validate if present.
        if "baseline" in block or "baseline_key" in block:
            self.baseline(block.get("baseline"), block.get("baseline_key"), "timeline.baseline", targets=request.target_service_ids)
        for k in ("criterion_id", "criterion_version", "time_basis"):
            _equal(getattr(request, k), block[k], "timeline.request." + k)
        refs = ["snapshot:" + self.sid] + [self.actions[aid].evidence_ref for aid in request.action_ids
                                          if self.actions[aid].evidence_ref]
        if "snapshot:" + self.sid not in request.event_evidence_refs or not set(request.event_evidence_refs) <= set(refs):
            _fail("timeline.request.event_evidence_refs")
        recovery_fields = set("algorithm_version plan_id as_of time_basis criterion_id criterion_version event_evidence_refs limitations status plan_completion_hours service_first_restored_hours first_target_service_restored_hours all_target_services_restored_hours".split())
        recovery = _object(block.get("recovery_times"), "timeline.recovery", recovery_fields, recovery_fields)
        _equal(recovery["algorithm_version"], "1.0.0", "timeline.recovery.algorithm_version")
        for k in ("plan_id", "time_basis", "criterion_id", "criterion_version", "event_evidence_refs"):
            _equal(recovery[k], getattr(request, k), "timeline.recovery." + k)
        if _instant(recovery["as_of"], "timeline.recovery.as_of") != request.as_of:
            _fail("timeline.recovery.as_of")
        per_service = _object(recovery["service_first_restored_hours"], "timeline.recovery.services", set(request.target_service_ids), request.target_service_ids)
        metric_fields = set("value_hours status reason criterion_id time_basis observation_horizon_hours beyond_horizon lower_bound_hours lower_bound_exclusive".split())
        for metric in [recovery[k] for k in ("plan_completion_hours", "first_target_service_restored_hours", "all_target_services_restored_hours")] + list(per_service.values()):
            _object(metric, "timeline.recovery.metric", metric_fields)
            for k, expected in {"criterion_id": request.criterion_id, "time_basis": request.time_basis,
                                "observation_horizon_hours": request.horizon_hours}.items():
                _equal(metric.get(k), expected, "timeline.recovery.metric." + k)
        for field in ("ignored_topological_action_ids", "beyond_horizon_action_ids"):
            _ids(block.get(field), set(request.action_ids), "timeline." + field)
        _object(block.get("pre_action_availability"), "timeline.pre_action_availability", set(request.target_service_ids))
        if not isinstance(block.get("graph_events"), list):
            _fail("timeline.graph_events")
        for item in block["graph_events"]:
            _object(item, "timeline.graph_events", {"at_hours", "action_ids", "restored_edge_ids", "valve_overrides", "service_availability"})
            _ids(item.get("action_ids"), set(request.action_ids), "timeline.graph_events.action_ids")
            _ids(item.get("restored_edge_ids"), self.edges, "timeline.graph_events.restored_edge_ids")
            _object(item.get("valve_overrides"), "timeline.graph_events.valves", self.valves)
            _object(item.get("service_availability"), "timeline.graph_events.services", set(request.target_service_ids))


def validate_report_integrity(run: dict, snapshot: dict, *, timeline=None, feedback=None) -> ReportAuthority:
    """Validate, never repair; errors are converted to ReportNotReady by caller."""
    try:
        authority = ReportAuthority(run, snapshot)
        authority.validate(run["result"])
        if timeline is not None:
            authority.timeline(timeline)
        if feedback is not None:
            if not isinstance(feedback, list):
                _fail("feedback")
            for item in feedback:
                if not isinstance(item, dict) or item.get("run_id") != authority.rid:
                    _fail("feedback.run_id")
        return authority
    except IntegrityConflict:
        raise
    except (ValidationError, KeyError, TypeError, ValueError, AttributeError, OverflowError):
        # Invalid stored shapes are conflicts, not an unhandled HTTP 500.
        raise IntegrityConflict("Report结果结构不符合已登记的计算合同") from None
