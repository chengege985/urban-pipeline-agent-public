"""T01: summarize a supplied, evidenced service timeline; no hydraulic simulation.

Offsets are hours after as_of. Initial availability is the state at t=0.
Known states at t=0 and t=0 events must agree. Events at one time are atomic.
This module does not infer a timeline from action effective_after_hours.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, StrictBool,
    ValidationError, model_validator,
)

VERSION = "1.0.0"
Hours = Annotated[FiniteFloat, Field(ge=0, strict=True)]
Identifier = Annotated[str, Field(min_length=1, pattern=r"\S")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionSchedule(Contract):
    action_id: Identifier
    start_offset_hours: Hours | None = None
    duration_hours: Hours | None = None
    finish_offset_hours: Hours | None = None

    @model_validator(mode="after")
    def coherent(self):
        start, duration, finish = self.start_offset_hours, self.duration_hours, self.finish_offset_hours
        if start is not None and duration is not None and not math.isfinite(start + duration):
            raise ValueError("start plus duration must remain finite")
        if finish is not None:
            if start is not None and finish < start:
                raise ValueError("finish must not precede start")
            if duration is not None and finish < duration:
                raise ValueError("duration implies a negative start")
            if start is not None and duration is not None and not math.isclose(
                    finish, start + duration, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError("finish must equal start + duration")
        return self

    def finish(self):
        if self.finish_offset_hours is not None:
            return self.finish_offset_hours
        if self.start_offset_hours is not None and self.duration_hours is not None:
            return self.start_offset_hours + self.duration_hours
        return None


class AvailabilityEvent(Contract):
    at_hours: Hours
    changes: dict[Identifier, StrictBool]


class RecoveryTimeRequest(Contract):
    as_of: AwareDatetime
    horizon_hours: Annotated[FiniteFloat, Field(gt=0, strict=True)]
    plan_id: Identifier
    action_ids: list[Identifier] = Field(min_length=1)
    action_schedule: list[ActionSchedule] = Field(default_factory=list)
    target_service_ids: list[Identifier]
    time_basis: Literal["planned", "observed"]
    criterion_id: str
    criterion_version: Identifier
    initial_availability: dict[Identifier, StrictBool | None] = Field(default_factory=dict)
    availability_events: list[AvailabilityEvent] = Field(default_factory=list)
    timeline_complete_until_hours: Hours | None = None
    event_evidence_refs: list[Identifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def references(self):
        for values in (self.action_ids, self.target_service_ids):
            if len(values) != len(set(values)):
                raise ValueError("duplicate action or target IDs")
        schedule_ids = [a.action_id for a in self.action_schedule]
        if len(schedule_ids) != len(set(schedule_ids)) or not set(schedule_ids) <= set(self.action_ids):
            raise ValueError("action schedule contains duplicate or unknown IDs")
        targets = set(self.target_service_ids)
        if not set(self.initial_availability) <= targets:
            raise ValueError("initial availability references an unknown target")
        seen = {}
        for event in self.availability_events:
            if not set(event.changes) <= targets:
                raise ValueError("event references an unknown target")
            for service, available in event.changes.items():
                key = (event.at_hours, service)
                if key in seen and seen[key] != available:
                    raise ValueError("conflicting simultaneous availability events")
                if event.at_hours == 0 and self.initial_availability.get(service) is not None:
                    if self.initial_availability[service] != available:
                        raise ValueError("t=0 event contradicts initial availability")
                seen[key] = available
        return self


def compute_recovery_times(request: RecoveryTimeRequest | dict) -> dict:
    """Return field-level times and statuses without modifying input or legacy data."""
    try:
        req = request if isinstance(request, RecoveryTimeRequest) else RecoveryTimeRequest.model_validate(request)
    except ValidationError as exc:
        return {"status": "invalid_input", "algorithm_version": VERSION,
                "issues": [{"field": ".".join(map(str, e["loc"])), "reason": e["msg"]}
                           for e in exc.errors(include_context=False, include_input=False)]}
    base = {"algorithm_version": VERSION, "plan_id": req.plan_id,
            "as_of": req.as_of.isoformat(), "time_basis": req.time_basis,
            "criterion_id": req.criterion_id, "criterion_version": req.criterion_version,
            "event_evidence_refs": list(req.event_evidence_refs),
            "limitations": ["Summarizes the supplied timeline; does not validate physical supply or predict events."]}
    if req.criterion_id not in {"topological_reachability", "declared_service_availability"}:
        return {**base, "status": "unsupported", "reason": "No hydraulic/quality compliance engine is implemented.",
                "plan_completion_hours": None, "service_first_restored_hours": {},
                "first_target_service_restored_hours": None, "all_target_services_restored_hours": None}

    def metric(value=None, status="succeeded", reason="", **extra):
        return {"value_hours": value, "status": status, "reason": reason,
                "criterion_id": req.criterion_id, "time_basis": req.time_basis,
                "observation_horizon_hours": req.horizon_hours, **extra}

    schedules = {a.action_id: a for a in req.action_schedule}
    finishes = [schedules[aid].finish() if aid in schedules else None for aid in req.action_ids]
    if any(finish is None for finish in finishes):
        completion = metric(status="insufficient_data", reason="Every action needs a finish or start plus duration.")
    else:
        last_finish = max(finishes)
        completion = metric(last_finish, beyond_horizon=last_finish > req.horizon_hours)

    targets = req.target_service_ids
    first = {}
    initial = {service: req.initial_availability.get(service) for service in targets}
    groups = {}
    for event in req.availability_events:
        groups.setdefault(event.at_hours, {}).update(event.changes)
    initial.update(groups.get(0, {}))
    complete = req.timeline_complete_until_hours
    usable_timeline = complete is not None and bool(req.event_evidence_refs)
    coverage = min(complete, req.horizon_hours) if usable_timeline else None

    def unresolved(initial_known=True):
        if not initial_known or not usable_timeline:
            return metric(status="insufficient_data", reason="Initial state, timeline coverage or evidence is missing.")
        if coverage < req.horizon_hours:
            return metric(status="insufficient_data", reason="The event timeline does not cover the requested horizon.")
        return metric(status="not_recovered_within_horizon",
                      reason="Not restored within the complete observation window.",
                      lower_bound_hours=req.horizon_hours, lower_bound_exclusive=True)

    # An initial true state is exact at t=0 only when the supplied timeline has evidence.
    if usable_timeline:
        for service, available in initial.items():
            if available is True:
                first[service] = metric(0, "already_available")
        states = initial.copy()
        uncertain_all = bool(targets) and not any(v is False for v in states.values()) and any(
            v is None for v in states.values())
        all_time = metric(0, "already_available") if targets and all(v is True for v in states.values()) else None
        for at in sorted(t for t in groups if 0 < t <= coverage):
            states.update(groups[at])
            for service in targets:
                if service not in first and initial[service] is False and states[service] is True:
                    first[service] = metric(at)
            if targets and all_time is None:
                if all(v is True for v in states.values()) and not uncertain_all:
                    all_time = metric(at)
                elif not any(v is False for v in states.values()) and any(v is None for v in states.values()):
                    uncertain_all = True
    else:
        all_time, uncertain_all = None, True
    for service in targets:
        if service not in first:
            first[service] = unresolved(initial[service] is not None)

    if not targets:
        group_first = metric(status="not_applicable", reason="The target set is empty.")
        group_all = metric(status="not_applicable", reason="The target set is empty.")
    else:
        unavailable = [service for service in targets if initial[service] is False]
        candidates = [first[service]["value_hours"] for service in unavailable
                      if first[service]["value_hours"] is not None]
        if any(v is None for v in initial.values()):
            group_first = unresolved(False)
        elif not unavailable:
            group_first = metric(status="not_applicable", reason="No target was initially unavailable.")
        elif candidates:
            group_first = metric(min(candidates))
        else:
            group_first = unresolved()
        group_all = all_time if all_time is not None else unresolved(not uncertain_all)

    fields = [completion, group_first, group_all, *first.values()]
    incomplete = any(f["status"] == "insufficient_data" for f in fields)
    resolved = any(f["status"] in {"succeeded", "already_available", "not_recovered_within_horizon"}
                   for f in fields)
    status = "partial" if incomplete and resolved else "insufficient_data" if incomplete else "succeeded"
    return {**base, "status": status, "plan_completion_hours": completion,
            "service_first_restored_hours": first,
            "first_target_service_restored_hours": group_first,
            "all_target_services_restored_hours": group_all}
