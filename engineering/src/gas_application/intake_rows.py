"""Normalize authorized gas data tables after their package provenance is checked."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
import math
import re
from typing import Literal, cast, get_args
import unicodedata

from .contracts import AssetType, GasCoordinate


HEADERS: dict[str, tuple[str, ...]] = {
    "assets": ("source_record_id", "asset_id", "asset_type", "name", "district_id"),
    "coordinates": ("source_record_id", "asset_id", "crs", "longitude", "latitude"),
    "monitoring": ("source_record_id", "sensor_id", "asset_id", "observed_at", "metric", "value", "unit", "quality"),
}
ASSET_TYPES = frozenset(get_args(AssetType))
METRIC_UNITS = {"pressure": frozenset({"Pa", "kPa", "MPa"}), "flow": frozenset({"m3/h", "Nm3/h"})}
DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
OBSERVED_AT = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})?")
MonitorQuality = Literal["valid", "suspect"]


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_id: str
    version: str
    sha256: str


@dataclass(frozen=True, slots=True)
class Issue:
    table: str
    code: str
    row: int | None = None
    field: str | None = None


@dataclass(frozen=True, slots=True)
class RowProvenance:
    source: SourceRef
    row: int
    source_record_id: str


@dataclass(frozen=True, slots=True)
class AssetRow:
    asset_id: str
    asset_type: AssetType
    name: str
    district_id: str | None
    provenance: RowProvenance


@dataclass(frozen=True, slots=True)
class CoordinateRow:
    asset_id: str
    coordinate: GasCoordinate
    provenance: RowProvenance


@dataclass(frozen=True, slots=True)
class MonitoringRow:
    sensor_id: str
    asset_id: str
    observed_at: datetime
    metric: str
    value: float
    unit: str
    quality: MonitorQuality
    provenance: RowProvenance


@dataclass(frozen=True, slots=True)
class NormalizedRows:
    assets: tuple[AssetRow, ...]
    coordinates: tuple[CoordinateRow, ...]
    monitoring: tuple[MonitoringRow, ...]
    issues: tuple[Issue, ...]


def _contains_control(value: str) -> bool:
    return any(unicodedata.category(char).startswith("C") for char in value)


def _duplicate_values(rows: list[dict[str, str]], field: str) -> set[str]:
    counts = Counter(
        value.strip()
        for row in rows
        if (value := row.get(field)) and not _contains_control(value) and value.strip()
    )
    return {value for value, occurrences in counts.items() if occurrences > 1}


@dataclass(slots=True)
class _Row:
    table: str
    number: int
    values: dict[str, str]
    issues: list[Issue]
    valid: bool = True

    def reject(self, code: str, field: str) -> None:
        self.issues.append(Issue(self.table, code, self.number, field))
        self.valid = False

    def text(self, field: str, *, required: bool = True) -> str | None:
        value = self.values.get(field)
        if isinstance(value, str) and not _contains_control(value):
            if cleaned := value.strip():
                return cleaned
        elif value is not None:
            self.reject("invalid_text", field)
            return None
        if required:
            self.reject("missing_required_value", field)
        return None

    def numeric(self, field: str) -> float | None:
        if (value := self.text(field)) is None:
            return None
        if DECIMAL.fullmatch(value) is not None:
            parsed = float(value)
            if math.isfinite(parsed):
                return parsed
        self.reject("invalid_number", field)
        return None

    def datetime_utc(self, field: str) -> datetime | None:
        if (value := self.text(field)) is None:
            return None
        if OBSERVED_AT.fullmatch(value) is None:
            self.reject("invalid_timestamp", field)
            return None
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is None:
                self.reject("timezone_required", field)
                return None
            return parsed.astimezone(UTC)
        except (ValueError, OverflowError):
            self.reject("invalid_timestamp", field)
            return None

    def provenance(self, source: SourceRef, duplicate_records: set[str]) -> RowProvenance | None:
        record_id = self.text("source_record_id")
        if record_id is None:
            return None
        if record_id in duplicate_records:
            self.reject("duplicate_source_record_id", "source_record_id")
            return None
        return RowProvenance(source, self.number, record_id)

    def require_asset(self, asset_ids: set[str]) -> str | None:
        asset_id = self.text("asset_id")
        if asset_id is not None and asset_id not in asset_ids:
            self.reject("orphan_asset", "asset_id")
        return asset_id


def _assets(source: SourceRef, rows: list[dict[str, str]], issues: list[Issue]) -> tuple[AssetRow, ...]:
    duplicate_records = _duplicate_values(rows, "source_record_id")
    duplicate_assets = _duplicate_values(rows, "asset_id")
    result = []
    for number, values in enumerate(rows, 1):
        row = _Row("assets", number, values, issues)
        provenance = row.provenance(source, duplicate_records)
        asset_id = row.text("asset_id")
        asset_type = row.text("asset_type")
        name = row.text("name")
        district_id = row.text("district_id", required=False)
        if asset_id in duplicate_assets:
            row.reject("duplicate_asset_id", "asset_id")
        if asset_type is not None and asset_type not in ASSET_TYPES:
            row.reject("unsupported_asset_type", "asset_type")
        if row.valid:
            assert provenance is not None and asset_id is not None and name is not None
            result.append(AssetRow(asset_id, cast(AssetType, asset_type), name, district_id, provenance))
    return tuple(result)


def _coordinates(
    source: SourceRef, rows: list[dict[str, str]], asset_ids: set[str], issues: list[Issue]
) -> tuple[CoordinateRow, ...]:
    duplicate_records = _duplicate_values(rows, "source_record_id")
    duplicate_assets = _duplicate_values(rows, "asset_id")
    result = []
    for number, values in enumerate(rows, 1):
        row = _Row("coordinates", number, values, issues)
        provenance = row.provenance(source, duplicate_records)
        asset_id = row.require_asset(asset_ids)
        crs = row.text("crs")
        longitude = row.numeric("longitude")
        latitude = row.numeric("latitude")
        if asset_id in duplicate_assets:
            row.reject("duplicate_coordinate", "asset_id")
        if crs is not None and crs.lower() not in {"wgs84", "gcj02"}:
            row.reject("unsupported_crs", "crs")
        if longitude is not None and not -180 <= longitude <= 180:
            row.reject("coordinate_out_of_range", "longitude")
        if latitude is not None and not -90 <= latitude <= 90:
            row.reject("coordinate_out_of_range", "latitude")
        if row.valid:
            assert provenance is not None and asset_id is not None and crs is not None
            assert longitude is not None and latitude is not None
            coordinate = GasCoordinate(
                coordinate_system=cast(Literal["wgs84", "gcj02"], crs.lower()),
                longitude=longitude,
                latitude=latitude,
            )
            result.append(CoordinateRow(asset_id, coordinate, provenance))
    return tuple(result)


def _monitoring(
    source: SourceRef, rows: list[dict[str, str]], asset_ids: set[str], issues: list[Issue]
) -> tuple[MonitoringRow, ...]:
    duplicate_records = _duplicate_values(rows, "source_record_id")
    assignments: dict[str, set[str]] = defaultdict(set)
    reads: Counter[tuple[str, str, datetime]] = Counter()
    candidates: list[tuple[_Row, MonitoringRow]] = []
    for number, values in enumerate(rows, 1):
        row = _Row("monitoring", number, values, issues)
        provenance = row.provenance(source, duplicate_records)
        sensor_id = row.text("sensor_id")
        asset_id = row.require_asset(asset_ids)
        observed_at = row.datetime_utc("observed_at")
        metric = row.text("metric")
        value = row.numeric("value")
        unit = row.text("unit")
        quality = row.text("quality")
        if sensor_id is not None and asset_id is not None:
            assignments[sensor_id].add(asset_id)
        if sensor_id is not None and metric in METRIC_UNITS and observed_at is not None:
            reads[sensor_id, metric, observed_at] += 1
        if metric is not None and metric not in METRIC_UNITS:
            row.reject("unsupported_metric", "metric")
        if metric in METRIC_UNITS and unit is not None and unit not in METRIC_UNITS[metric]:
            row.reject("unsupported_unit", "unit")
        if quality == "invalid":
            row.reject("invalid_quality", "quality")
        elif quality is not None and quality not in {"valid", "suspect"}:
            row.reject("unsupported_quality", "quality")
        if row.valid:
            assert sensor_id is not None and asset_id is not None and observed_at is not None
            assert metric is not None and value is not None and unit is not None and provenance is not None
            candidates.append((row, MonitoringRow(
                sensor_id, asset_id, observed_at, metric, value, unit, cast(MonitorQuality, quality), provenance
            )))
    result = []
    for row, record in candidates:
        if len(assignments[record.sensor_id]) > 1:
            row.reject("conflicting_sensor_asset", "asset_id")
        if reads[record.sensor_id, record.metric, record.observed_at] > 1:
            row.reject("duplicate_reading", "observed_at")
        if row.valid:
            result.append(record)
            if record.quality == "suspect":
                issues.append(Issue("monitoring", "suspect_quality", row.number, "quality"))
    return tuple(result)


def normalize_rows(tables: dict[str, tuple[SourceRef, list[dict[str, str]]]]) -> NormalizedRows:
    """Validate vocabulary, values, and exact asset associations; preserve table order."""
    issues: list[Issue] = []
    assets = ()
    if "assets" in tables:
        source, records = tables["assets"]
        assets = _assets(source, records, issues)
    asset_ids = {asset.asset_id for asset in assets}

    coordinates = ()
    if "coordinates" in tables:
        source, records = tables["coordinates"]
        coordinates = _coordinates(source, records, asset_ids, issues)

    monitoring = ()
    if "monitoring" in tables:
        source, records = tables["monitoring"]
        monitoring = _monitoring(source, records, asset_ids, issues)
    return NormalizedRows(assets, coordinates, monitoring, tuple(issues))
