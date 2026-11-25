"""Compile only explicitly approved bound records into replay policies."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from .pilot_contract import PilotThreshold, PilotThresholdRule


def compile_approved_rules(
    rules: Iterable[PilotThresholdRule], issue: Callable[..., None],
) -> tuple[PilotThreshold, ...]:
    groups = defaultdict(list)
    for entry in rules:
        if entry.approval_status == "approved":
            groups[entry.sensor_id, entry.version].append(entry)
        else:
            issue("threshold_rules", "threshold_not_approved", entry.source.source_row, "approval_status", severity="warning")
    by_sensor = defaultdict(list)
    for _, members in sorted(groups.items()):
        ordered = tuple(sorted(members, key=lambda item: item.threshold_type))
        first = ordered[0]
        compared = ("sensor_id", "asset_id", "metric_id", "unit", "cadence_seconds", "sustained_samples",
                    "missing_samples", "effective_from", "effective_to")
        bounds = {item.threshold_type: item.threshold_value for item in ordered}
        if (len(bounds) != len(ordered)
            or any(any(getattr(item, field) != getattr(first, field) for field in compared) for item in ordered[1:])):
            issue("threshold_rules", "inconsistent_approved_version", first.source.source_row, "version")
            continue
        if "lower" in bounds and "upper" in bounds and bounds["lower"] >= bounds["upper"]:
            issue("threshold_rules", "invalid_approved_bounds", first.source.source_row, "threshold_value")
            continue
        policy = PilotThreshold(
            source=first.source, threshold_id=first.rule_id, sensor_id=first.sensor_id,
            version=first.version, unit=first.unit, lower=bounds.get("lower"), upper=bounds.get("upper"),
            cadence_seconds=first.cadence_seconds, sustained_samples=first.sustained_samples,
            missing_samples=first.missing_samples, operator_id=first.approved_by,
            approval_ref=first.evidence_reference, approved_at=first.approved_at,
            effective_from=first.effective_from, effective_until=first.effective_to, rule_inputs=ordered,
        )
        by_sensor[first.sensor_id].append(policy)
    compiled = []
    for policies in by_sensor.values():
        last = None
        for policy in sorted(policies, key=lambda item: item.effective_from):
            if last is not None and policy.effective_from < last:
                issue("threshold_rules", "overlapping_approved_policy", policy.source.source_row, "effective_from")
            last = max(last, policy.effective_until) if last is not None else policy.effective_until
            compiled.append(policy)
    return tuple(compiled)
