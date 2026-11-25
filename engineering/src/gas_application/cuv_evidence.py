"""Join synthetic evidence by stable asset ID and an explicit replay clock."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from .contracts import GasEvent
from .cuv_replay import GasCuvReplayReport
from .monitoring import (
    GasMonitoringReport,
    MonitoringEvent,
    MonitoringMetric,
    MonitoringSourceEvidence,
    MonitoringState,
)


class CuvEventEvidence(BaseModel):
    event_id: str
    title: str
    event_kind: Literal["given_fault", "given_isolation", "synthetic_observation"]
    asset_ids: list[str]
    elapsed_hours: float
    replay_at: datetime
    source_sha256: str
    evidence_refs: list[str]


class CuvMonitoringEvidence(BaseModel):
    asset_id: str
    sensor_id: str
    metric: MonitoringMetric
    unit: str
    state: MonitoringState
    relation: Literal["fault_asset", "isolation_asset"]
    last_usable_at: datetime | None
    transition: MonitoringEvent | None


class CuvMonitoringContext(BaseModel):
    status: Literal["available", "unavailable"]
    as_of: datetime
    dataset_id: str | None = None
    dataset_version: str | None = None
    manifest_sha256: str | None = None
    policy_id: str | None = None
    source: MonitoringSourceEvidence | None = None
    channels: list[CuvMonitoringEvidence]


class GasCuvReplayResponse(GasCuvReplayReport):
    event_evidence: list[CuvEventEvidence]
    monitoring_evidence: CuvMonitoringContext


def link_case_events(
    events: list[GasEvent],
    elapsed_by_id: dict[str, float],
    *,
    scenario_at: datetime,
    case_content_sha256: str,
) -> list[CuvEventEvidence]:
    """Project original case-relative times onto the explicit synthetic anchor."""

    return [
        CuvEventEvidence(
            event_id=event.event_id,
            title=event.title,
            event_kind=event.event_kind,
            asset_ids=event.asset_ids,
            elapsed_hours=elapsed_by_id[event.event_id],
            replay_at=scenario_at + timedelta(hours=elapsed_by_id[event.event_id]),
            source_sha256=case_content_sha256,
            evidence_refs=event.evidence_refs,
        )
        for event in events
    ]


def link_monitoring(
    report: GasMonitoringReport | None,
    *,
    scenario_at: datetime,
    fault_assets: set[str],
    isolation_assets: set[str],
) -> CuvMonitoringContext:
    """Bind observations to the same replay clock. Later states cannot stand in for earlier ones."""

    if report is None or report.evaluated_at != scenario_at or report.monitoring_source is None:
        return CuvMonitoringContext(status="unavailable", as_of=scenario_at, channels=[])

    channels = []
    for channel in report.channels:
        if channel.asset_id not in fault_assets and channel.asset_id not in isolation_assets:
            continue
        transition = max(
            (event for event in channel.events if event.at <= scenario_at),
            key=lambda event: event.at,
            default=None,
        )
        channels.append(CuvMonitoringEvidence(
            asset_id=channel.asset_id,
            sensor_id=channel.sensor_id,
            metric=channel.metric,
            unit=channel.unit,
            state=channel.state,
            relation="fault_asset" if channel.asset_id in fault_assets else "isolation_asset",
            last_usable_at=channel.last_usable_at,
            transition=transition,
        ))

    return CuvMonitoringContext(
        status="available",
        as_of=scenario_at,
        dataset_id=report.dataset_id,
        dataset_version=report.dataset_version,
        manifest_sha256=report.manifest_sha256,
        policy_id=report.policy_id,
        source=report.monitoring_source,
        channels=channels,
    )
