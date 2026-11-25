from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError

from .adapter import FixtureUnavailable as AdapterFixtureUnavailable, fixture_root as registered_fixture_root
from .agent_evidence import GasAgentCaseBinding, GasAgentEvidenceScope
from .contracts import (
    AssetResponse,
    DistrictResponse,
    EventResponse,
    GasAsset,
    GasEvent,
    GAS_DATASET_ID,
)

from .cuv_evidence import GasCuvReplayResponse, link_case_events, link_monitoring
from .cuv_replay import compare_cuv_replay
from .data_package import IntakeError, IntakeSnapshot, read_package
from .network import _digest
from .monitoring_bindings import GAS_M0_MONITORING_CHANNELS, K001_PRESSURE_CHANNEL, K003_FLOW_CHANNEL
from .monitoring import (
    GasMonitoringReport,
    MonitoringChannelPolicy,
    SyntheticMonitoringPolicy,
    evaluate_monitoring,
)

NETWORK_FILE = "gas54_network.json"
CASE_FILE = "gas54_case.json"
CUV_REPLAY_FILE = "gas_cuv_replay.json"
MONITORING_PACKAGE = "gas_monitoring_replay"
MONITORING_DATASET_ID = "syn-gas-monitoring-replay-01"
MONITORING_END = datetime(2026, 11, 18, 1, 12, tzinfo=UTC)
MONITORING_POLICY = SyntheticMonitoringPolicy(
    policy_id="syn-gas-monitoring-policy-0.1.0",
    window_start=datetime(2026, 11, 18, 1, 0, tzinfo=UTC),
    cadence=timedelta(minutes=1),
    sustained_samples=3,
    missing_samples=2,
    channels=(
        MonitoringChannelPolicy(*K001_PRESSURE_CHANNEL, lower=4, upper=6),
        MonitoringChannelPolicy(*K003_FLOW_CHANNEL, lower=12, upper=30),
    ),
)


class GasFixtureUnavailable(RuntimeError):
    """内部合成 Fixture 不可用；路由会映射为脱敏 503。"""


class GasFixtureService:
    def __init__(self, fixture_root_path: Path | None = None):
        self.fixture_root = fixture_root_path.resolve() if fixture_root_path is not None else None

    def _root(self) -> Path:
        if self.fixture_root is not None:
            return self.fixture_root
        try:
            return registered_fixture_root()
        except AdapterFixtureUnavailable as exc:
            raise GasFixtureUnavailable("registered fixture root is unavailable") from exc

    def _load_json(self, name: str, *, expected_sha256: str | None = None) -> dict[str, Any]:
        root = self._root()
        path = (root / name).resolve()
        try:
            if path.parent != root or path.is_symlink():
                raise GasFixtureUnavailable("synthetic fixture path is outside the registered root")
            payload = path.read_bytes()
            if expected_sha256 is not None and hashlib.sha256(payload).hexdigest() != expected_sha256:
                raise GasFixtureUnavailable("synthetic fixture differs from audited source")
            value = json.loads(payload.decode("utf-8"))
        except GasFixtureUnavailable:
            raise
        except (FileNotFoundError, IsADirectoryError, PermissionError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GasFixtureUnavailable(f"synthetic fixture unavailable: {type(exc).__name__}") from exc
        if not isinstance(value, dict):
            raise GasFixtureUnavailable("synthetic fixture root must be an object")
        return value

    def _network(self, *, expected_sha256: str | None = None) -> dict[str, Any]:
        value = (self._load_json(NETWORK_FILE) if expected_sha256 is None
                 else self._load_json(NETWORK_FILE, expected_sha256=expected_sha256))
        if (value.get("data_mode") != "synthetic"
                or value.get("industry") != "gas"
                or value.get("network_id") != GAS_DATASET_ID
                or value.get("coordinate_system") != "schematic"):
            raise GasFixtureUnavailable("registered fixture classification is invalid")
        required = ("nodes", "edges", "pipes", "valves", "sources", "consumers")
        if any(not isinstance(value.get(key), list) for key in required):
            raise GasFixtureUnavailable("registered network fixture structure is invalid")
        return value

    def _case(self, *, expected_sha256: str | None = None) -> dict[str, Any]:
        value = (self._load_json(CASE_FILE) if expected_sha256 is None
                 else self._load_json(CASE_FILE, expected_sha256=expected_sha256))
        if (value.get("data_mode") != "synthetic"
                or value.get("network_id") != GAS_DATASET_ID
                or not isinstance(value.get("events"), list)):
            raise GasFixtureUnavailable("registered case fixture structure is invalid")
        return value

    def _monitoring_snapshot(self) -> IntakeSnapshot:
        snapshot = read_package(self._root() / MONITORING_PACKAGE)
        if any((row.asset_id, row.sensor_id, row.metric, row.unit) not in GAS_M0_MONITORING_CHANNELS
               for row in snapshot.rows.monitoring):
            raise GasFixtureUnavailable("registered monitoring sensor binding is invalid")
        return snapshot

    def m2_fixture(self, hashes: dict[str, str]) -> tuple[dict, dict, IntakeSnapshot, list[GasEvent]]:
        """Read serving inputs from the same files pinned by the candidate registry."""
        try:
            network = self._network(expected_sha256=hashes[NETWORK_FILE])
            case = self._case(expected_sha256=hashes[CASE_FILE])
            snapshot = self._monitoring_snapshot()
            expected_csvs = {"assets", "monitoring"}
            if (snapshot.dataset_id != MONITORING_DATASET_ID or snapshot.data_mode != "synthetic"
                or snapshot.manifest_sha256 != hashes[f"{MONITORING_PACKAGE}/manifest.json"]
                or set(snapshot.sources) != expected_csvs
                or any(snapshot.sources[name].sha256 != hashes[f"{MONITORING_PACKAGE}/{name}.csv"] for name in expected_csvs)
                or snapshot.rows.issues):
                raise GasFixtureUnavailable("monitoring differs from audited fixture")
        except (IntakeError, KeyError, ValueError) as exc:
            raise GasFixtureUnavailable("audited fixture data unavailable") from exc
        return network, case, snapshot, self._event_rows(network, case)

    def monitoring(self) -> GasMonitoringReport:
        return self._monitoring_for_network(self._network())

    def _monitoring_for_network(self, network: dict[str, Any]) -> GasMonitoringReport:
        active_pipe_ids = {
            asset.asset_id for asset in self._asset_rows(network) if asset.asset_type == "pipe"
        }
        if any(channel.asset_id not in active_pipe_ids for channel in MONITORING_POLICY.channels):
            raise GasFixtureUnavailable("monitoring replay asset is outside the active synthetic network")
        try:
            snapshot = self._monitoring_snapshot()
            if snapshot.dataset_id != MONITORING_DATASET_ID or "monitoring" not in snapshot.sources:
                raise GasFixtureUnavailable("registered monitoring source is unavailable")
            return evaluate_monitoring(snapshot, MONITORING_POLICY, evaluated_at=MONITORING_END)
        except (IntakeError, ValueError) as exc:
            raise GasFixtureUnavailable("registered synthetic monitoring replay is invalid") from exc

    def cuv_replay(
        self, *, case_binding: GasAgentCaseBinding | None = None, replay_sha256: str | None = None,
    ) -> GasCuvReplayResponse:
        network = self._network()
        case = self._case()
        replay = self._load_json(CUV_REPLAY_FILE)
        try:
            if (case_binding is not None and (
                case_binding.network_content_sha256 != _digest(network)
                or case_binding.case_content_sha256 != _digest(case)
            )) or (replay_sha256 is not None and replay_sha256 != _digest(replay)):
                raise HTTPException(409, "gas_cuv_evidence_version_conflict")
            report = compare_cuv_replay(network, case, replay)
            events = link_case_events(
                self._event_rows(network, case),
                {event["event_id"]: event["time_hours"] for event in case["events"]},
                scenario_at=report.scenario_at,
                case_content_sha256=report.topology_evidence.case_content_sha256,
            )
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            raise GasFixtureUnavailable("registered synthetic CUV replay is invalid") from exc

        try:
            monitoring = self._monitoring_for_network(network)
        except GasFixtureUnavailable:
            monitoring = None
        context = link_monitoring(
            monitoring,
            scenario_at=report.scenario_at,
            fault_assets=set(report.fault_asset_ids),
            isolation_assets={
                asset_id for event in events
                if event.event_kind == "given_isolation" for asset_id in event.asset_ids
            },
        )
        return GasCuvReplayResponse(
            **report.model_dump(), event_evidence=events, monitoring_evidence=context,
        )

    def districts(self) -> DistrictResponse:
        return DistrictResponse(
            status="unavailable",
            data_mode="unavailable",
            source_status="unavailable",
            validation_status="unknown",
            items=[],
            total=0,
            limitations=["未发现经授权的重庆区县GIS边界或设施点位；不生成虚构区县数据。"],
        )

    def _asset_rows(self, network: dict[str, Any]) -> list[GasAsset]:
        rows: list[GasAsset] = []
        try:
            for row in network["pipes"]:
                rows.append(GasAsset(
                    asset_id=row["pipe_id"], asset_type="pipe", name=row["pipe_id"],
                    material=row.get("material"), diameter_mm=row.get("diameter_mm"),
                    pressure_class=row.get("pressure_class"),
                    data_mode="synthetic", source_status="synthetic_fixture",
                    validation_status="not_business_validated",
                    status="synthetic_reference", evidence_refs=["gas-m0:gas54_network.json"],
                ))
            for row in network["valves"]:
                rows.append(GasAsset(
                    asset_id=row["valve_id"], asset_type="valve", name=row["valve_id"],
                    data_mode="synthetic", source_status="synthetic_fixture",
                    validation_status="not_business_validated",
                    status="synthetic_reference", evidence_refs=["gas-m0:gas54_network.json"],
                ))
            for row in network["nodes"]:
                node_type = row.get("node_type")
                mapped = node_type if node_type in {"regulator", "valve", "source", "junction"} else "node"
                rows.append(GasAsset(
                    asset_id=row["node_id"], asset_type=mapped, name=row.get("name") or row["node_id"],
                    data_mode="synthetic", source_status="synthetic_fixture",
                    validation_status="not_business_validated",
                    status="synthetic_reference", evidence_refs=["gas-m0:gas54_network.json"],
                ))
            for row in network["consumers"]:
                rows.append(GasAsset(
                    asset_id=row["consumer_id"], asset_type="consumer", name=row["consumer_id"],
                    data_mode="synthetic", source_status="synthetic_fixture",
                    validation_status="not_business_validated",
                    status="synthetic_reference", evidence_refs=["gas-m0:gas54_network.json"],
                ))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise GasFixtureUnavailable("registered network fixture fields are invalid") from exc
        return rows

    def asset_event_records(
        self,
    ) -> tuple[list[GasAsset], list[GasEvent], GasAgentCaseBinding, GasAgentEvidenceScope]:
        """Read records and their identity from the same fixture inputs."""
        network, case = self._network(), self._case()
        assets, events = self._asset_rows(network), self._event_rows(network, case)
        binding = GasAgentCaseBinding(network_content_sha256=_digest(network), case_content_sha256=_digest(case))
        scope = GasAgentEvidenceScope(
            scope="gas_m0_case", dataset_id=network["network_id"], dataset_version=case["schema_version"],
            data_mode="synthetic", source_sha256=binding.case_content_sha256,
        )
        return assets, events, binding, scope

    def assets(self, *, asset_type: str | None = None, district_id: str | None = None,
               bbox: str | None = None, limit: int = 200, offset: int = 0) -> AssetResponse:
        network = self._network()
        rows = self._asset_rows(network)
        if asset_type is not None:
            rows = [row for row in rows if row.asset_type == asset_type]
        if district_id is not None:
            # GAS-M0 has no authorized Chongqing district mapping.
            rows = []
        if bbox is not None:
            # Assets carry no authorized WGS84/GCJ02 coordinates yet.
            rows = []
        selected = rows[offset:offset + limit]
        return AssetResponse(
            status="partial",
            data_mode="synthetic",
            source_status="synthetic_fixture",
            validation_status="not_business_validated",
            items=selected,
            total=len(rows),
            limitations=[
                "当前仅提供GAS-M0合成资产身份；schematic坐标未映射为重庆GIS经纬度。",
                "资产监测值、压力、流量及正式风险评价未接入。",
            ],
        )

    def asset(self, asset_id: str) -> GasAsset:
        rows = self._asset_rows(self._network())
        indexed = {row.asset_id: row for row in rows}
        try:
            return indexed[asset_id]
        except KeyError:
            raise HTTPException(status_code=404, detail="燃气资产不存在") from None

    def _event_rows(self, network: dict[str, Any], case: dict[str, Any]) -> list[GasEvent]:
        try:
            edge_to_pipe = {row["edge_id"]: row["pipe_id"] for row in network["edges"]}
            rows: list[GasEvent] = []
            for event in case["events"]:
                pipe_ids = [
                    edge_to_pipe[value]
                    for value in event.get("fault_edge_ids", []) + event.get("disabled_edge_ids", [])
                    if value in edge_to_pipe
                ]
                closed_valve_ids = [
                    value for value in event.get("closed_valve_ids", [])
                    if isinstance(value, str) and value
                ]
                asset_ids = list(dict.fromkeys(pipe_ids + closed_valve_ids))
                rows.append(GasEvent(
                    event_id=event["event_id"],
                    title="合成案例 " + str(event["kind"]),
                    event_kind=event["kind"],
                    data_mode="synthetic",
                    source_status="synthetic_fixture",
                    validation_status="not_business_validated",
                    asset_ids=asset_ids,
                    closed_valve_ids=list(dict.fromkeys(closed_valve_ids)),
                    evidence_refs=["gas-m0:gas54_case.json", "gas-m0:gas54_network.json"],
                    limitation="这是给定的合成事件；没有实时告警、GIS定位或物理供气结论。",
                ))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError, ValidationError) as exc:
            raise GasFixtureUnavailable("registered case fixture fields are invalid") from exc
        return rows

    def events(self, *, limit: int = 100, district_id: str | None = None,
            status: str | None = None) -> EventResponse:
        rows = self._event_rows(self._network(), self._case())
        if district_id is not None:
            rows = []
        if status is not None:
            rows = [row for row in rows if row.state == status]
        return EventResponse(
            status="partial",
            data_mode="synthetic",
            source_status="synthetic_fixture",
            validation_status="not_business_validated",
            items=rows[:limit],
            total=len(rows),
            limitations=[
                "事件来自给定合成案例，不是企业监测告警。",
                "未发现经授权的重庆经纬度，地图定位状态为 not_available。",
                "风险评分、压力流量和实际供气状态未计算。",
            ],
        )

    def event(self, event_id: str) -> GasEvent:
        rows = self._event_rows(self._network(), self._case())
        indexed = {row.event_id: row for row in rows}
        try:
            return indexed[event_id]
        except KeyError:
            raise HTTPException(status_code=404, detail="燃气事件不存在") from None
