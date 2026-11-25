"""Versioned historical replay against the recorded threshold time windows."""

from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta
from typing import Literal

from fastapi import HTTPException

from .data_package import Identifier
from .intake_rows import MonitoringRow, RowProvenance, SourceRef
from .monitoring import MonitoringChannelPolicy, MonitoringChannelResult, evaluate_scheduled_channel
from .pilot_validation import PilotReplayValidation, validate_replay
from .pilot_contract import (
    Contract, PilotEvent, PilotIdentity, PilotObservation, PilotPipe, PilotSensor, PilotSnapshot,
    PilotSource, PilotThreshold, UtcTime,
)

MAX_WINDOW = timedelta(days=7)
DEFAULT_WINDOW = timedelta(hours=1)
MAX_SLOTS = 10_000
MAX_RESULT_OBSERVATIONS = 5_000
RULE_VERSION = "gas-scheduled-replay-1"
Classification = Literal["normal", "anomaly", "excluded", "off_schedule", "no_policy"]


class PilotReplayRequest(Contract):
    sensor_id: Identifier
    start: UtcTime | None = None
    end: UtcTime | None = None


class PilotReplayPoint(Contract):
    observation: PilotObservation
    classification: Classification
    threshold_id: Identifier | None
    threshold_version: Identifier | None
    event_ids: tuple[Identifier, ...]


class PilotReplaySegment(Contract):
    threshold: PilotThreshold
    start: UtcTime
    end: UtcTime
    channel: MonitoringChannelResult


class PilotReplayResult(PilotIdentity):
    pipe_id: Identifier
    sensor: PilotSensor
    rule_version: Literal["gas-scheduled-replay-1"] = RULE_VERSION
    window_start: UtcTime | None
    window_end: UtcTime | None
    observations: tuple[PilotReplayPoint, ...]
    segments: tuple[PilotReplaySegment, ...]
    related_events: tuple[PilotEvent, ...]
    validation: PilotReplayValidation
    applied_thresholds: tuple[PilotThreshold, ...] = ()


def sensor_policies(snapshot: PilotSnapshot, sensor_id: str) -> tuple[PilotThreshold, ...]:
    rows = snapshot.approved_policies if snapshot.data_mode == "real_authorized" or snapshot.formal_rules_declared else snapshot.thresholds
    return tuple(sorted((row for row in rows if row.sensor_id == sensor_id),
                        key=lambda row: row.effective_from))


def effective_policy(policies: tuple[PilotThreshold, ...], at: datetime) -> PilotThreshold | None:
    index = bisect_right(policies, at, key=lambda row: row.effective_from) - 1
    if index < 0:
        return None
    candidate = policies[index]
    return candidate if at < candidate.effective_until else None


def classify(row: PilotObservation, policy: PilotThreshold | None) -> Classification:
    if row.quality != "valid" or row.value is None:
        return "excluded"
    if policy is None:
        return "no_policy"
    if (row.observed_at - policy.effective_from) % timedelta(seconds=policy.cadence_seconds):
        return "off_schedule"
    high = policy.upper is not None and row.value > policy.upper
    low = policy.lower is not None and row.value < policy.lower
    return "anomaly" if high or low else "normal"


def _provenance(source: PilotSource) -> RowProvenance:
    return RowProvenance(SourceRef(source.source_id, source.source_version, source.source_sha256),
                         source.source_row, source.source_record_id)


def _segment(
    policy: PilotThreshold, start: datetime, end: datetime, sensor: PilotSensor, pipe: PilotPipe,
    rows: list[PilotObservation], remaining_slots: int,
) -> tuple[PilotReplaySegment | None, int]:
    cadence = timedelta(seconds=policy.cadence_seconds)
    candidate_start = max(policy.effective_from, start)
    if candidate_start > end or candidate_start >= policy.effective_until:
        return None, 0
    difference = candidate_start - policy.effective_from
    first = policy.effective_from + ((difference + cadence - timedelta.resolution) // cadence) * cadence
    end_bound = min(end, policy.effective_until - timedelta.resolution)
    if first > end_bound:
        return None, 0
    count = (end_bound - first) // cadence + 1
    if count > remaining_slots:
        raise HTTPException(422, "回放时间范围超过采样上限，请缩短时间区间")
    last = first + (count - 1) * cadence
    values = {}
    off_schedule = 0
    for row in rows:
        if not candidate_start <= row.observed_at <= end or row.observed_at >= policy.effective_until:
            continue
        if (row.observed_at - policy.effective_from) % cadence:
            off_schedule += 1
        elif row.value is not None and row.quality in ("valid", "suspect"):
            values[row.observed_at] = MonitoringRow(
                sensor.sensor_id, sensor.pipe_id, row.observed_at, sensor.metric, row.value, row.unit,
                row.quality, _provenance(row.source),
            )
    channel = evaluate_scheduled_channel(
        MonitoringChannelPolicy(sensor.pipe_id, sensor.sensor_id, sensor.metric, sensor.unit, policy.lower, policy.upper),
        values, off_schedule, _provenance(pipe.source),
        cadence=cadence, sustained_samples=policy.sustained_samples, missing_samples=policy.missing_samples,
        start=first, end=last,
    )
    return PilotReplaySegment(threshold=policy, start=first, end=last, channel=channel), count


def _window(request: PilotReplayRequest, values: list[PilotObservation]) -> tuple[datetime | None, datetime | None]:
    if request.start is not None and request.end is not None:
        if request.end < request.start or request.end - request.start > MAX_WINDOW:
            raise HTTPException(422, "回放须按时间顺序，最长七天")
        return request.start, request.end
    if request.start is not None or request.end is not None:
        raise HTTPException(422, "回放起止时间须一起提供")
    if not values:
        return None, None
    end = values[-1].observed_at
    return max(values[0].observed_at, end - DEFAULT_WINDOW), end


def replay(snapshot: PilotSnapshot, request: PilotReplayRequest, *, allow_truth: bool = True) -> PilotReplayResult:
    sensor = next((row for row in snapshot.sensors if row.sensor_id == request.sensor_id), None)
    if sensor is None:
        raise HTTPException(404, "试点测点不存在")
    pipe = next(row for row in snapshot.pipes if row.pipe_id == sensor.pipe_id)
    values = sorted((row for row in snapshot.observations if row.sensor_id == sensor.sensor_id),
                    key=lambda row: row.observed_at)
    start, end = _window(request, values)
    policies = sensor_policies(snapshot, sensor.sensor_id)
    visible = [row for row in values if start is not None and end is not None and start <= row.observed_at <= end]
    if len(visible) > MAX_RESULT_OBSERVATIONS:
        raise HTTPException(422, "当前区间观测过多，请缩短时间区间")
    visible_ids = {row.observation_id for row in visible}
    event_ids: dict[str, list[str]] = {identifier: [] for identifier in visible_ids}
    events = []
    for event in snapshot.events:
        linked = visible_ids.intersection(event.observation_ids)
        if not linked:
            continue
        events.append(event)
        for identifier in linked:
            event_ids[identifier].append(event.event_id)

    remaining = MAX_SLOTS
    matching_start = matching_end = None
    segments = []
    if start is not None and end is not None:
        # Clamp configured windows to the recorded history span of this sensor.
        # A search outside that span is empty rather than a fabricated outage.
        matching_start = max(start, values[0].observed_at) if values else end
        matching_end = min(end, values[-1].observed_at) if values else start
        if values and matching_start <= matching_end:
            for policy in policies:
                segment, count = _segment(policy, matching_start, matching_end, sensor, pipe, visible, remaining)
                remaining -= count
                if segment:
                    segments.append(segment)
    points = []
    for row in visible:
        policy = effective_policy(policies, row.observed_at)
        points.append(PilotReplayPoint(
            observation=row, classification=classify(row, policy), threshold_id=policy.threshold_id if policy else None,
            threshold_version=policy.version if policy else None, event_ids=tuple(event_ids[row.observation_id]),
        ))
    return PilotReplayResult(
        **snapshot.model_dump(include=set(PilotIdentity.model_fields)), pipe_id=pipe.pipe_id, sensor=sensor,
        window_start=start, window_end=end, observations=points, segments=segments, related_events=events,
        applied_thresholds=tuple(policy for policy in policies if matching_start is not None and matching_end is not None
                                 and matching_start <= matching_end and policy.effective_from <= matching_end
                                 and matching_start < policy.effective_until),
        validation=validate_replay(snapshot, sensor.sensor_id, matching_start, matching_end, policies, segments, allow_truth=allow_truth),
    )
