"""Resolve a selected synthetic exceedance to its original row and case evidence."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .cuv_evidence import CuvEventEvidence, GasCuvReplayResponse
from .monitoring import MonitoringObservationEvidence, MonitoringRowEvidence, MonitoringState
from .service import GasFixtureService, GasFixtureUnavailable


class GasObservationRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GasMonitoringObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: GasObservationRef
    dataset_id: str
    dataset_version: str
    data_mode: Literal["synthetic"] = "synthetic"
    validation_status: Literal["not_business_validated"] = "not_business_validated"
    asset_id: str
    asset_evidence: MonitoringRowEvidence
    sensor_id: str
    metric: Literal["pressure", "flow"]
    policy_id: str
    lower: float | None
    upper: float | None
    sustained_samples: int
    state: Literal["limit_exceeded"] = "limit_exceeded"
    previous_state: MonitoringState
    direction: Literal["above_upper", "below_lower"]
    triggered_at: datetime
    observation: MonitoringObservationEvidence
    sustaining_observations: tuple[MonitoringObservationEvidence, ...]
    event: CuvEventEvidence | None
    scenario_id: str | None

    def evidence_refs(self) -> list[str]:
        rows = (*self.sustaining_observations, self.asset_evidence)
        refs = [
            f"gas-monitoring:sha256:{self.reference.manifest_sha256}",
            *(f"gas-monitoring-row:{row.source_id}:{row.source_row}:sha256:{row.source_sha256}" for row in rows),
        ]
        if self.event:
            refs.extend(self.event.evidence_refs)
            refs.append(f"gas-event:{self.event.event_id}:sha256:{self.event.source_sha256}")
        return refs

    def explanation(self) -> str:
        row = self.observation
        metric = "压力" if self.metric == "pressure" else "流量"
        edge, bound = (
            ("上限", self.upper) if self.direction == "above_upper" else ("下限", self.lower)
        )
        relation = f"同一合成案例关联事件 {self.event.event_id}。" if self.event else "该时点暂无关联案例事件。"
        at = row.observed_at.isoformat().replace("+00:00", "Z")
        return (
            f"合成观测 {row.source_record_id}，{at}，资产 {self.asset_id}，"
            f"{metric} {row.value:g} {row.unit}。规则 {self.policy_id} 的样例{edge} {bound:g} {row.unit}，"
            f"连续 {self.sustained_samples} 个有效采样越限。{relation}"
        )


def resolve_observation(
    service: GasFixtureService, reference: GasObservationRef, *, replay: GasCuvReplayResponse | None = None,
) -> GasMonitoringObservation:
    report = service.monitoring()
    if reference.manifest_sha256 != report.manifest_sha256:
        raise HTTPException(409, "gas_monitoring_input_conflict")
    matches = [
        (channel, event, row)
        for channel in report.channels for event in channel.events if event.kind == "limit_exceeded"
        for row in event.evidence
        if row.source_record_id == reference.observation_id and row.observed_at == event.at and row.quality == "valid"
    ]
    if len(matches) != 1:
        raise HTTPException(404, "gas_monitoring_observation_not_found")
    channel, alert, row = matches[0]
    if replay is None:
        try:
            replay = service.cuv_replay()
        except GasFixtureUnavailable:
            pass
    # Only the observation selected by the case's existing monitoring evidence can
    # carry a case association. Matching the case's asset and clock does not infer a fault cause.
    candidates: list[CuvEventEvidence] = []
    if replay and replay.monitoring_evidence.manifest_sha256 == report.manifest_sha256:
        for entry in replay.monitoring_evidence.channels:
            if (entry.asset_id == channel.asset_id and entry.sensor_id == channel.sensor_id
                and entry.transition == alert):
                candidates.extend(event for event in replay.event_evidence
                                  if channel.asset_id in event.asset_ids and event.replay_at == alert.at)
                break
    event = candidates[0] if len(candidates) == 1 else None
    return GasMonitoringObservation(
        reference=reference, dataset_id=report.dataset_id, dataset_version=report.dataset_version,
        asset_id=channel.asset_id, asset_evidence=channel.asset_evidence, sensor_id=channel.sensor_id,
        metric=channel.metric, policy_id=report.policy_id, lower=channel.lower, upper=channel.upper,
        sustained_samples=report.sustained_samples, previous_state=alert.previous_state,
        direction="above_upper" if alert.cause == "above_upper" else "below_lower",
        triggered_at=alert.at, observation=row, sustaining_observations=tuple(alert.evidence),
        event=event, scenario_id=replay.scenario_id if event and replay else None,
    )


def require_observation_event(observation: GasMonitoringObservation, event_id: str | None) -> None:
    if observation.event is None or (event_id is not None and observation.event.event_id != event_id):
        raise HTTPException(409, "gas_monitoring_event_conflict")
