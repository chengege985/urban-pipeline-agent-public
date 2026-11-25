"""Rule consistency, version-boundary deduplication and verified history comparison."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal, Protocol

from .data_package import Identifier
from .monitoring import LimitDirection, MonitoringChannelResult
from .pilot_contract import Contract, PilotSnapshot, PilotSource, PilotThreshold, PilotTruthWindow, UtcTime


class ReplaySegment(Protocol):
    threshold: PilotThreshold
    start: datetime
    end: datetime
    channel: MonitoringChannelResult


class PilotAlarm(Contract):
    threshold_id: Identifier
    threshold_version: Identifier
    at: UtcTime
    direction: LimitDirection


class PilotDuplicateAlarm(Contract):
    alarm: PilotAlarm
    original: PilotAlarm


class PilotTruthComparison(Contract):
    event_id: Identifier
    truth_event_id: Identifier
    truth_source: PilotSource
    verified_start: UtcTime
    verified_recovery: UtcTime | None
    detected_at: UtcTime | None
    detection_delay_seconds: float | None
    recovery_detected_at: UtcTime | None
    recovery_delay_seconds: float | None


class PilotTruthMetrics(Contract):
    mode: Literal["synthetic_truth_check", "operator_reviewed_history"]
    windows: tuple[PilotTruthWindow, ...]
    matches: tuple[PilotTruthComparison, ...]
    false_alarms: tuple[PilotAlarm, ...]
    missed_events: int
    recoveries_reviewed: int
    recoveries_detected: int
    recoveries_missed: int


class PilotReplayValidation(Contract):
    status: Literal["rule_only", "truth_comparison"]
    reason: Literal["complete_review", "no_eligible_review_window", "review_use_outside_scope"]
    duplicate_alarms: tuple[PilotDuplicateAlarm, ...]
    metrics: PilotTruthMetrics | None


def _alarms(segments: list[ReplaySegment]) -> tuple[list[PilotAlarm], list[PilotDuplicateAlarm]]:
    accepted, repeated = [], []
    last_segment = None
    last_active = None
    for segment in segments:
        active = None
        for index, event in enumerate(segment.channel.events):
            if event.kind != "limit_exceeded" or event.cause not in {"above_upper", "below_lower"}:
                active = None
                continue
            alarm = PilotAlarm(
                threshold_id=segment.threshold.threshold_id, threshold_version=segment.threshold.version,
                at=event.at, direction=event.cause,
            )
            joined = (
                index == 0 and last_segment is not None and last_active is not None
                and last_active.direction == alarm.direction
                and last_segment.channel.state == "limit_exceeded"
                and last_segment.channel.last_usable_at == last_segment.end
                and last_segment.threshold.effective_until == segment.threshold.effective_from
                and last_segment.end + timedelta(seconds=last_segment.threshold.cadence_seconds) == segment.start
                and event.evidence and event.evidence[0].observed_at == segment.start
            )
            if joined:
                repeated.append(PilotDuplicateAlarm(alarm=alarm, original=last_active))
                active = last_active
            else:
                accepted.append(alarm)
                active = alarm
        last_segment, last_active = segment, active
    return accepted, repeated


def _covered_by_rules(window: PilotTruthWindow, policies: tuple[PilotThreshold, ...]) -> bool:
    cursor = window.start
    for policy in policies:
        if not policy.rule_inputs or policy.effective_until <= cursor:
            continue
        if policy.effective_from > cursor:
            return False
        cursor = policy.effective_until
        if cursor >= window.end:
            return True
    return False


def _evaluate_window(snapshot, window, alarms, recoveries):
    remaining = [alarm for alarm in alarms if window.start <= alarm.at < window.end]
    truth = sorted((row for row in snapshot.truth_events if row.truth_window_id == window.truth_window_id),
                   key=lambda row: row.started_at)
    matches = []
    for index, row in enumerate(truth):
        incident_end = row.recovered_at or window.end
        match = next((alarm for alarm in remaining
                      if row.started_at <= alarm.at < incident_end and alarm.direction == row.direction), None)
        recovery = None
        if match is not None:
            remaining.remove(match)
            if row.recovered_at is not None:
                next_truth = truth[index + 1].started_at if index + 1 < len(truth) else window.end
                next_alarm = next((alarm.at for alarm in alarms if alarm.at > match.at), window.end)
                end = min(window.end, next_truth, next_alarm)
                recovery = next((at for at in recoveries if row.recovered_at <= at < end), None)
        matches.append(PilotTruthComparison(
            event_id=row.event_id, truth_event_id=row.truth_event_id, truth_source=row.source,
            verified_start=row.started_at, verified_recovery=row.recovered_at if row.recovered_at != window.end else None,
            detected_at=match.at if match else None,
            detection_delay_seconds=(match.at - row.started_at).total_seconds() if match else None,
            recovery_detected_at=recovery,
            recovery_delay_seconds=(recovery - row.recovered_at).total_seconds() if recovery else None,
        ))
    return matches, remaining


def validate_replay(
    snapshot: PilotSnapshot, sensor_id: str, start: datetime | None, end: datetime | None,
    policies: tuple[PilotThreshold, ...], segments: list[ReplaySegment], *, allow_truth: bool,
) -> PilotReplayValidation:
    alarms, repeated = _alarms(segments)
    windows = tuple(sorted((
        window for window in snapshot.truth_windows
        if allow_truth and window.sensor_id == sensor_id and window.coverage_status == "complete"
        and start is not None and end is not None and start <= window.start < window.end <= end
        and _covered_by_rules(window, policies)
    ), key=lambda item: item.start))
    if not windows:
        return PilotReplayValidation(
            status="rule_only", reason="no_eligible_review_window" if allow_truth else "review_use_outside_scope",
            duplicate_alarms=repeated, metrics=None,
        )
    recoveries = sorted(event.at for segment in segments for event in segment.channel.events
                        if event.kind == "recovered" and event.cause == "within_limits")
    matches, false_alarms = [], []
    for window in windows:
        matched, false = _evaluate_window(snapshot, window, alarms, recoveries)
        matches.extend(matched)
        false_alarms.extend(false)
    reviewed = sum(match.verified_recovery is not None for match in matches)
    detected = sum(match.recovery_detected_at is not None for match in matches)
    return PilotReplayValidation(
        status="truth_comparison", reason="complete_review", duplicate_alarms=repeated,
        metrics=PilotTruthMetrics(
            mode="synthetic_truth_check" if snapshot.data_mode == "synthetic" else "operator_reviewed_history",
            windows=windows, matches=matches, false_alarms=false_alarms,
            missed_events=sum(match.detected_at is None for match in matches),
            recoveries_reviewed=reviewed, recoveries_detected=detected, recoveries_missed=reviewed - detected,
        ),
    )
