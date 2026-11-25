"""Input contract for an isolated gas pilot, independent of GAS-M0 fixtures."""

from __future__ import annotations

from datetime import UTC, date, datetime
from hashlib import sha256
import json
from math import atan, degrees, isfinite, sinh
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from .data_package import Digest, Identifier, IntakeMode
from .intake_rows import METRIC_UNITS
from .monitoring import MonitoringMetric, MonitoringRowEvidence

Table = Literal[
    "nodes", "pipes", "gis", "sensors", "observations", "events", "thresholds",
    "threshold_rules", "truth_windows", "truth_events",
]
Finite = Annotated[float, Field(allow_inf_nan=False)]
Quality = Literal["valid", "suspect", "invalid", "missing"]
Crs = Literal["EPSG:4326", "EPSG:3857"]
RecordIds = Annotated[tuple[Identifier, ...], Field(min_length=1, max_length=64)]
ReadingUse = Literal["historical_replay", "asset_geometry", "local_qwen", "threshold_validation"]
SYNTHETIC_PREFIX = "syn-gas-pilot-"
WEB_MERCATOR_LIMIT = 20037508.342789244


def _timestamp(value: object) -> object:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})", value,
    ):
        return value
    raise ValueError("expected ISO 8601 time with offset")


UtcTime = Annotated[AwareDatetime, BeforeValidator(_timestamp), AfterValidator(lambda at: at.astimezone(UTC))]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PilotFile(Contract):
    filename: Annotated[str, Field(min_length=5, max_length=128)]
    source_id: Identifier
    version: Identifier
    sha256: Digest
    columns: dict[str, Annotated[str, Field(min_length=1, max_length=128)]]
    sheet: Annotated[str, Field(min_length=1, max_length=31)] | None = None

    @model_validator(mode="after")
    def represented_format(self) -> PilotFile:
        if not re.fullmatch(r"[^/\\:*?<>|\x00-\x1f]{1,119}\.(csv|xlsx|geojson)", self.filename):
            raise ValueError("expected a local source filename")
        if self.filename.endswith(".xlsx") != (self.sheet is not None):
            raise ValueError("xlsx requires an explicit sheet")
        if len(self.columns) != len(set(self.columns.values())):
            raise ValueError("one native column per canonical field")
        return self


class PilotManifest(Contract):
    schema_version: Literal["gas-pilot/1"]
    dataset_id: Identifier
    version: Identifier
    data_mode: IntakeMode
    files: dict[Table, PilotFile]

    @model_validator(mode="after")
    def manifest_scope(self) -> PilotManifest:
        if (self.data_mode == "synthetic") != self.dataset_id.startswith(SYNTHETIC_PREFIX):
            raise ValueError("synthetic identifier namespace and data mode disagree")
        if len({entry.filename for entry in self.files.values()}) != len(self.files):
            raise ValueError("each table requires a distinct file")
        return self


class PilotGrant(Contract):
    approval_ref: Identifier
    scope_ref: Identifier
    directory: str
    dataset_id: Identifier
    version: Identifier
    manifest_sha256: Digest
    valid_from: UtcTime
    valid_until: UtcTime
    uses: frozenset[ReadingUse]
    subjects: Annotated[frozenset[Identifier], Field(min_length=1, max_length=128)]
    import_operators: Annotated[frozenset[Identifier], Field(min_length=1, max_length=32)]
    region_ref: Identifier
    asset_ids: Annotated[frozenset[Identifier], Field(min_length=1, max_length=2048)]
    history_from: UtcTime
    history_to: UtcTime

    @model_validator(mode="after")
    def bounded_grant(self) -> PilotGrant:
        if (self.valid_until <= self.valid_from or self.history_to <= self.history_from
            or "historical_replay" not in self.uses):
            raise ValueError("approval requires an active replay scope")
        return self

    def scope_digest(self) -> str:
        return sha256(json.dumps([
            self.scope_ref, self.region_ref, sorted(self.asset_ids),
            self.history_from.isoformat(), self.history_to.isoformat(),
        ], separators=(",", ":")).encode()).hexdigest()


class PilotIdentity(Contract):
    dataset_id: Identifier
    version: Identifier
    data_mode: IntakeMode
    manifest_sha256: Digest


class PilotSource(MonitoringRowEvidence):
    table: Table
    sheet: str | None = None


class PilotRow(Contract):
    source: PilotSource


class PilotNode(PilotRow):
    node_id: Identifier


class PilotPipe(PilotRow):
    pipe_id: Identifier
    from_node_id: Identifier
    to_node_id: Identifier
    material: Literal["steel", "pe", "ductile_iron", "other", "unknown"]
    diameter_mm: Annotated[Finite, Field(gt=0)]
    pressure_class: Identifier
    region_ref: Identifier | None = None

    @model_validator(mode="after")
    def distinct_endpoints(self) -> PilotPipe:
        if self.from_node_id == self.to_node_id:
            raise ValueError("identical endpoints")
        return self


def json_value(value: object) -> object:
    return json.loads(value) if isinstance(value, str) else value


def _position(value: object) -> object:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("expected 2D coordinate")
    if any(type(item) not in (float, int) or not isfinite(item) for item in value):
        raise ValueError("coordinate is not finite")
    return value


Position = Annotated[tuple[float, float], BeforeValidator(_position)]


class PilotLine(Contract):
    type: Literal["LineString"]
    coordinates: Annotated[tuple[Position, ...], Field(min_length=2, max_length=4096)]


class PilotGeometry(PilotRow):
    geometry_id: Identifier
    pipe_id: Identifier
    crs: Crs
    geometry: Annotated[PilotLine, BeforeValidator(json_value)]
    scope_ref: Identifier
    valid_from: date
    valid_until: date

    @model_validator(mode="after")
    def usable_line(self) -> PilotGeometry:
        if self.valid_from > self.valid_until:
            raise ValueError("invalid validity date range")
        points = self.geometry.coordinates
        if any(left == right for left, right in zip(points, points[1:])):
            raise ValueError("line contains a zero-length segment")
        x_max, y_max = (180, 90) if self.crs == "EPSG:4326" else (WEB_MERCATOR_LIMIT, WEB_MERCATOR_LIMIT)
        if any(abs(x) > x_max or abs(y) > y_max for x, y in points):
            raise ValueError("coordinate outside CRS domain")
        return self

    def wgs84(self) -> PilotLine:
        if self.crs == "EPSG:4326":
            return self.geometry
        return PilotLine(type="LineString", coordinates=tuple(
            (max(-180.0, min(180.0, degrees(x / 6378137))), degrees(atan(sinh(y / 6378137))))
            for x, y in self.geometry.coordinates
        ))


class PilotSensor(PilotRow):
    sensor_id: Identifier
    pipe_id: Identifier
    metric: MonitoringMetric
    unit: Identifier | Literal["m3/h", "Nm3/h"]
    device_status: Literal["active", "inactive", "maintenance", "unknown"]

    @model_validator(mode="after")
    def matching_unit(self) -> PilotSensor:
        if self.unit not in METRIC_UNITS[self.metric]:
            raise ValueError("unit incompatible with metric")
        return self


class PilotObservation(PilotRow):
    observation_id: Identifier
    sensor_id: Identifier
    observed_at: UtcTime
    value: Finite | None
    unit: str
    quality: Quality
    quality_reason: Annotated[str, Field(min_length=1, max_length=240, pattern=r"^[^\x00-\x1f\x7f]+$")] | None = None

    @model_validator(mode="after")
    def value_and_quality(self) -> PilotObservation:
        if (self.value is None) != (self.quality == "missing"):
            raise ValueError("missing value and quality disagree")
        return self


class PilotEvent(PilotRow):
    event_id: Identifier
    recorded_at: UtcTime
    observation_ids: Annotated[RecordIds, BeforeValidator(json_value)]
    pipe_ids: Annotated[RecordIds, BeforeValidator(json_value)]
    status: Literal["open", "acknowledged", "resolved", "unknown"]
    evidence_ref: Identifier


class PilotThresholdRule(PilotRow):
    rule_id: Identifier
    version: Identifier
    metric_id: MonitoringMetric
    asset_id: Identifier
    sensor_id: Identifier
    unit: str
    threshold_type: Literal["lower", "upper"]
    threshold_value: Finite
    effective_from: UtcTime
    effective_to: UtcTime
    approval_status: Literal["draft", "pending", "approved", "rejected", "revoked"]
    approved_by: Identifier | None
    approved_at: UtcTime | None
    evidence_reference: Identifier | None
    cadence_seconds: Annotated[int, Field(ge=1, le=86400)]
    sustained_samples: Annotated[int, Field(ge=2, le=60)]
    missing_samples: Annotated[int, Field(ge=1, le=60)]

    @model_validator(mode="after")
    def rule_window(self) -> PilotThresholdRule:
        if self.effective_from >= self.effective_to:
            raise ValueError("threshold effective interval is empty")
        if self.approval_status == "approved" and (
            self.approved_by is None or self.evidence_reference is None or self.approved_at is None
            or self.approved_at > self.effective_from
        ):
            raise ValueError("approved threshold requires an earlier approval and evidence")
        return self


class PilotTruthWindow(PilotRow):
    truth_window_id: Identifier
    sensor_id: Identifier
    start: UtcTime
    end: UtcTime
    coverage_status: Literal["complete", "partial"]
    reviewed_by: Identifier
    reviewed_at: UtcTime
    evidence_reference: Identifier

    @model_validator(mode="after")
    def reviewed_window(self) -> PilotTruthWindow:
        if not self.start < self.end <= self.reviewed_at:
            raise ValueError("truth review must follow a valid history window")
        return self


class PilotTruthEvent(PilotRow):
    truth_event_id: Identifier
    truth_window_id: Identifier
    event_id: Identifier
    direction: Literal["above_upper", "below_lower"]
    started_at: UtcTime
    recovered_at: UtcTime | None
    evidence_reference: Identifier

    @model_validator(mode="after")
    def incident_order(self) -> PilotTruthEvent:
        if self.recovered_at is not None and self.recovered_at <= self.started_at:
            raise ValueError("truth incident recovery must follow its start")
        return self


class PilotThreshold(PilotRow):
    threshold_id: Identifier
    sensor_id: Identifier
    version: Identifier
    unit: str
    lower: Finite | None
    upper: Finite | None
    cadence_seconds: Annotated[int, Field(ge=1, le=86400)]
    sustained_samples: Annotated[int, Field(ge=2, le=60)]
    missing_samples: Annotated[int, Field(ge=1, le=60)]
    operator_id: Identifier
    approval_ref: Identifier
    approved_at: UtcTime
    effective_from: UtcTime
    effective_until: UtcTime
    rule_inputs: tuple[PilotThresholdRule, ...] = ()

    @model_validator(mode="after")
    def policy_window(self) -> PilotThreshold:
        if not self.approved_at <= self.effective_from < self.effective_until:
            raise ValueError("threshold must have a prior approval and a valid time window")
        if self.lower is None and self.upper is None:
            raise ValueError("threshold requires a bound")
        if self.lower is not None and self.upper is not None and self.lower >= self.upper:
            raise ValueError("invalid limit order")
        return self


ROW_MODELS = {
    "nodes": (PilotNode, "node_id"),
    "pipes": (PilotPipe, "pipe_id"),
    "gis": (PilotGeometry, "geometry_id"),
    "sensors": (PilotSensor, "sensor_id"),
    "observations": (PilotObservation, "observation_id"),
    "events": (PilotEvent, "event_id"),
    "thresholds": (PilotThreshold, "threshold_id"),
    "threshold_rules": (PilotThresholdRule, "rule_id"),
    "truth_windows": (PilotTruthWindow, "truth_window_id"),
    "truth_events": (PilotTruthEvent, "truth_event_id"),
}


class PilotSnapshot(PilotIdentity):
    nodes: tuple[PilotNode, ...]
    pipes: tuple[PilotPipe, ...]
    gis: tuple[PilotGeometry, ...]
    sensors: tuple[PilotSensor, ...]
    observations: tuple[PilotObservation, ...]
    events: tuple[PilotEvent, ...]
    thresholds: tuple[PilotThreshold, ...]
    threshold_rules: tuple[PilotThresholdRule, ...] = ()
    truth_windows: tuple[PilotTruthWindow, ...] = ()
    truth_events: tuple[PilotTruthEvent, ...] = ()
    approved_policies: tuple[PilotThreshold, ...] = ()
    formal_rules_declared: bool = False
