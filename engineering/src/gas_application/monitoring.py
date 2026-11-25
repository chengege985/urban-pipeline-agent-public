"""Replay explicitly synthetic gas monitoring against a versioned demo policy."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite
from typing import Literal

from pydantic import BaseModel

from .data_package import IntakeSnapshot
from .intake_rows import METRIC_UNITS, MonitorQuality, MonitoringRow, RowProvenance, SourceRef


MonitoringMetric = Literal["pressure", "flow"]
MonitoringState = Literal["observing", "normal", "limit_exceeded", "data_missing"]
LimitDirection = Literal["above_upper", "below_lower"]
MonitoringCause = Literal[
    "above_upper", "below_lower", "no_usable_observation", "within_limits", "observations_resumed"
]


@dataclass(frozen=True, slots=True)
class MonitoringChannelPolicy:
    asset_id: str
    sensor_id: str
    metric: MonitoringMetric
    unit: str
    lower: float | None
    upper: float | None


@dataclass(frozen=True, slots=True)
class SyntheticMonitoringPolicy:
    policy_id: str
    window_start: datetime
    cadence: timedelta
    sustained_samples: int
    missing_samples: int
    channels: tuple[MonitoringChannelPolicy, ...]


class MonitoringSourceEvidence(BaseModel):
    source_id: str
    source_version: str
    source_sha256: str


class MonitoringRowEvidence(MonitoringSourceEvidence):
    source_record_id: str
    source_row: int


class MonitoringObservationEvidence(MonitoringRowEvidence):
    observed_at: datetime
    value: float
    unit: str
    quality: MonitorQuality


class MonitoringEvent(BaseModel):
    kind: Literal["limit_exceeded", "data_missing", "recovered"]
    at: datetime
    previous_state: MonitoringState
    cause: MonitoringCause
    evidence: list[MonitoringObservationEvidence]
    missing_slots: list[datetime]


class MonitoringChannelResult(BaseModel):
    asset_id: str
    sensor_id: str
    metric: MonitoringMetric
    unit: str
    lower: float | None
    upper: float | None
    state: MonitoringState
    last_usable_at: datetime | None
    off_schedule_observations: int
    asset_evidence: MonitoringRowEvidence
    events: list[MonitoringEvent]


class GasMonitoringReport(BaseModel):
    data_mode: Literal["synthetic"]
    validation_status: Literal["not_business_validated"]
    dataset_id: str
    dataset_version: str
    manifest_sha256: str
    policy_id: str
    window_start: datetime
    evaluated_at: datetime
    cadence_seconds: float
    sustained_samples: int
    missing_samples: int
    monitoring_source: MonitoringSourceEvidence | None
    channels: list[MonitoringChannelResult]


def _source_evidence(source: SourceRef) -> MonitoringSourceEvidence:
    return MonitoringSourceEvidence(
        source_id=source.source_id, source_version=source.version, source_sha256=source.sha256
    )


def _row_evidence(provenance: RowProvenance) -> MonitoringRowEvidence:
    return MonitoringRowEvidence(
        **_source_evidence(provenance.source).model_dump(),
        source_record_id=provenance.source_record_id,
        source_row=provenance.row,
    )


def _observation_evidence(row: MonitoringRow) -> MonitoringObservationEvidence:
    return MonitoringObservationEvidence(
        **_row_evidence(row.provenance).model_dump(),
        observed_at=row.observed_at,
        value=row.value,
        unit=row.unit,
        quality=row.quality,
    )


def _utc(at: datetime) -> datetime:
    if at.utcoffset() is None:
        raise ValueError("monitoring replay requires a timezone-aware clock")
    return at.astimezone(UTC)


def _validate_policy(policy: SyntheticMonitoringPolicy) -> dict[tuple[str, str], MonitoringChannelPolicy]:
    if (
        not policy.policy_id.strip()
        or not policy.channels
        or policy.cadence <= timedelta(0)
        or policy.sustained_samples < 2
        or policy.missing_samples < 1
    ):
        raise ValueError("invalid synthetic monitoring policy")
    channels: dict[tuple[str, str], MonitoringChannelPolicy] = {}
    for channel in policy.channels:
        key = (channel.sensor_id, channel.metric)
        if (
            not channel.asset_id.strip()
            or not channel.sensor_id.strip()
            or channel.unit not in METRIC_UNITS.get(channel.metric, ())
            or (channel.lower is None and channel.upper is None)
            or (channel.lower is not None and not isfinite(channel.lower))
            or (channel.upper is not None and not isfinite(channel.upper))
            or (channel.lower is not None and channel.upper is not None and channel.lower >= channel.upper)
            or key in channels
        ):
            raise ValueError("invalid synthetic monitoring channel")
        channels[key] = channel
    return channels


def _limit_direction(value: float, channel: MonitoringChannelPolicy) -> LimitDirection | None:
    if channel.lower is not None and value < channel.lower:
        return "below_lower"
    if channel.upper is not None and value > channel.upper:
        return "above_upper"
    return None


def _event(
    kind: Literal["limit_exceeded", "data_missing", "recovered"],
    at: datetime,
    previous_state: MonitoringState,
    cause: MonitoringCause,
    rows: list[MonitoringRow],
    *,
    missing_slots: list[datetime] | None = None,
) -> MonitoringEvent:
    return MonitoringEvent(
        kind=kind,
        at=at,
        previous_state=previous_state,
        cause=cause,
        evidence=[_observation_evidence(row) for row in rows],
        missing_slots=list(missing_slots or ()),
    )


def evaluate_scheduled_channel(
    channel: MonitoringChannelPolicy,
    observations: dict[datetime, MonitoringRow],
    off_schedule: int,
    asset_evidence: RowProvenance,
    *,
    cadence: timedelta,
    sustained_samples: int,
    missing_samples: int,
    start: datetime,
    end: datetime,
) -> MonitoringChannelResult:
    state: MonitoringState = "observing"
    events: list[MonitoringEvent] = []
    last_usable: MonitoringRow | None = None
    missing_slots: list[datetime] = []
    unusable: list[MonitoringRow] = []
    limit_run: list[MonitoringRow] = []
    limit_direction: LimitDirection | None = None
    active_direction: LimitDirection | None = None
    for slot_index in range((end - start) // cadence + 1):
        slot = start + slot_index * cadence
        row = observations.get(slot)
        if row is None or row.quality != "valid" or row.unit != channel.unit:
            limit_run.clear()
            if state == "data_missing":
                continue
            missing_slots.append(slot)
            if row is not None:
                unusable.append(row)
            if len(missing_slots) == missing_samples:
                evidence = [last_usable] if last_usable is not None else []
                events.append(_event(
                    "data_missing", slot, state, "no_usable_observation",
                    evidence + unusable, missing_slots=missing_slots,
                ))
                state = "data_missing"
                active_direction = None
            elif state != "limit_exceeded":
                state = "observing"
            continue

        missing_slots.clear()
        unusable.clear()
        last_usable = row
        if state == "data_missing":
            events.append(_event("recovered", slot, state, "observations_resumed", [row]))
            state = "observing"

        direction = _limit_direction(row.value, channel)
        if direction is None:
            limit_run.clear()
            if state == "limit_exceeded":
                events.append(_event("recovered", slot, state, "within_limits", [row]))
            state = "normal"
            active_direction = None
            continue
        if direction == active_direction:
            limit_run.clear()
            continue
        if direction != limit_direction:
            limit_run.clear()
        limit_direction = direction
        limit_run.append(row)
        if len(limit_run) == sustained_samples:
            events.append(_event("limit_exceeded", slot, state, direction, limit_run))
            state = "limit_exceeded"
            active_direction = direction
        elif state != "limit_exceeded":
            state = "observing"

    return MonitoringChannelResult(
        asset_id=channel.asset_id,
        sensor_id=channel.sensor_id,
        metric=channel.metric,
        unit=channel.unit,
        lower=channel.lower,
        upper=channel.upper,
        state=state,
        last_usable_at=last_usable.observed_at if last_usable is not None else None,
        off_schedule_observations=off_schedule,
        asset_evidence=_row_evidence(asset_evidence),
        events=events,
    )


def evaluate_monitoring(
    snapshot: IntakeSnapshot, policy: SyntheticMonitoringPolicy, *, evaluated_at: datetime
) -> GasMonitoringReport:
    """Evaluate only exact scheduled slots through the explicitly chosen replay clock."""

    if snapshot.data_mode != "synthetic" or not snapshot.dataset_id.startswith("syn-gas-"):
        raise ValueError("synthetic monitoring policy requires a synthetic intake snapshot")
    start = _utc(policy.window_start)
    end = _utc(evaluated_at)
    if end < start:
        raise ValueError("monitoring replay clock precedes the window")
    channels = _validate_policy(policy)
    assets = {asset.asset_id: asset.provenance for asset in snapshot.rows.assets}
    if any(channel.asset_id not in assets for channel in channels.values()):
        raise ValueError("monitoring policy refers to an unknown asset")

    observations: dict[tuple[str, str], dict[datetime, MonitoringRow]] = defaultdict(dict)
    off_schedule: dict[tuple[str, str], int] = defaultdict(int)
    for row in snapshot.rows.monitoring:
        key = (row.sensor_id, row.metric)
        if key not in channels or not start <= row.observed_at <= end:
            continue
        if row.asset_id != channels[key].asset_id:
            raise ValueError("monitoring policy and observation disagree on asset identity")
        if (row.observed_at - start) % policy.cadence:
            off_schedule[key] += 1
            continue
        if row.observed_at in observations[key]:
            raise ValueError("duplicate monitoring slot")
        observations[key][row.observed_at] = row

    source = snapshot.sources.get("monitoring")
    return GasMonitoringReport(
        data_mode="synthetic",
        validation_status="not_business_validated",
        dataset_id=snapshot.dataset_id,
        dataset_version=snapshot.version,
        manifest_sha256=snapshot.manifest_sha256,
        policy_id=policy.policy_id,
        window_start=start,
        evaluated_at=end,
        cadence_seconds=policy.cadence.total_seconds(),
        sustained_samples=policy.sustained_samples,
        missing_samples=policy.missing_samples,
        monitoring_source=_source_evidence(source) if source is not None else None,
        channels=[
            evaluate_scheduled_channel(
                channel, observations[key], off_schedule[key], assets[channel.asset_id],
                cadence=policy.cadence, sustained_samples=policy.sustained_samples, missing_samples=policy.missing_samples,
                start=start, end=end,
            )
            for key, channel in channels.items()
        ],
    )
