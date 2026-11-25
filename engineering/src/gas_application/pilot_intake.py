"""Preview mappings, validate relations and normalize an offline pilot package."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from .data_package import Identifier, IntakeError, MAX_MANIFEST_BYTES, _file_bytes, _unique_fields
from .pilot_contract import (
    ROW_MODELS, SYNTHETIC_PREFIX, PilotGrant, PilotIdentity, PilotManifest, PilotSnapshot, PilotSource,
)
from .pilot_formats import read_native
from .pilot_policy import compile_approved_rules

MAX_PILOT_FILE_BYTES = 16 * 1024 * 1024
ID = TypeAdapter(Identifier)
CORE = {"nodes", "pipes", "sensors", "observations"}
OPTIONAL_EXTENSION = {"threshold_rules", "truth_windows", "truth_events"}


@dataclass(frozen=True)
class PilotInspection:
    identity: PilotIdentity
    report: dict
    snapshot: PilotSnapshot | None
    approval_ref: str | None
    grant_scope_sha256: str | None = None


def package_grant(
    identity: PilotIdentity, grants: tuple[PilotGrant, ...], *, directory: Path | None = None,
    approval_ref: str | None = None,
) -> PilotGrant | None:
    if identity.data_mode == "synthetic":
        return None
    now = datetime.now(UTC)
    matches = [
        grant for grant in grants
        if grant.dataset_id == identity.dataset_id and grant.version == identity.version
        and grant.manifest_sha256 == identity.manifest_sha256 and grant.valid_from <= now < grant.valid_until
        and (directory is None or grant.directory == str(directory))
        and (approval_ref is None or grant.approval_ref == approval_ref)
    ]
    if len(matches) != 1:
        raise IntakeError("approval_required")
    return matches[0]


def _directory(path: Path) -> tuple[Path, int]:
    try:
        if path.is_symlink():
            raise ValueError("source symlink")
        directory = path.resolve(strict=True)
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        return directory, fd
    except (OSError, RuntimeError, ValueError):
        raise IntakeError("missing_directory") from None


def _value(value: object) -> object:
    if isinstance(value, bool):
        raise ValueError("unexpected boolean")
    if isinstance(value, str):
        value = value.strip()
        if len(value) > 512 * 1024 or "\x00" in value:
            raise ValueError("invalid cell text")
        return value or None
    return value


def _read_tables(directory: Path, fd: int, manifest: PilotManifest, issue) -> tuple[dict, dict, dict]:
    rows = {table: {} for table in ROW_MODELS}
    mapping = {}
    received = {table: 0 for table in ROW_MODELS}
    for table, (model, key) in ROW_MODELS.items():
        source = manifest.files.get(table)
        if source is None:
            if (table not in OPTIONAL_EXTENSION
                and not (table == "thresholds" and "threshold_rules" in manifest.files)):
                issue(table, "missing_source", severity="error" if table in CORE else "warning")
            continue
        try:
            raw = _file_bytes(directory, fd, source.filename, MAX_PILOT_FILE_BYTES, table)
            if sha256(raw).hexdigest() != source.sha256:
                raise IntakeError("hash_mismatch", table=table)
            header, native = read_native(table, source, raw)
        except IntakeError as error:
            issue(table, error.code, error.row)
            continue
        received[table] = len(native)
        required = {"source_record_id"} | {name for name, field in model.model_fields.items()
                                           if name != "source" and field.is_required()}
        allowed = (set(model.model_fields) - {"source"}) | {"source_record_id"}
        mapping[table] = {
            "source_id": source.source_id, "version": source.version, "sha256": source.sha256,
            "native_columns": list(header), "columns": source.columns, "sheet": source.sheet,
            "missing_fields": sorted(required - set(source.columns)),
        }
        if not required <= set(source.columns) <= allowed or not set(source.columns.values()) <= set(header):
            issue(table, "field_mapping_required")
            continue
        source_ids = set()
        for entry in native:
            if entry.error:
                issue(table, entry.error, entry.number)
                continue
            try:
                values = {field: _value(entry.values.get(column)) for field, column in source.columns.items()}
                source_id = ID.validate_python(values.pop("source_record_id"))
                provenance = PilotSource(
                    table=table, sheet=source.sheet, source_id=source.source_id, source_version=source.version,
                    source_sha256=source.sha256, source_record_id=source_id, source_row=entry.number,
                )
                parsed = model.model_validate({**values, "source": provenance})
            except ValidationError as error:
                fields = [str(item["loc"][0]) if item["loc"] else None for item in error.errors(include_input=False)]
                for field in dict.fromkeys(fields):
                    code = "invalid_record"
                    if field:
                        code = "missing_required_value" if field in required and values.get(field) is None else "invalid_field"
                    issue(table, code, entry.number, field)
                continue
            except (ValueError, OverflowError, RecursionError):
                issue(table, "invalid_cell", entry.number)
                continue
            record_id = getattr(parsed, key)
            if record_id in rows[table] or source_id in source_ids:
                issue(table, "duplicate_identity", entry.number, key)
                continue
            if (manifest.data_mode == "synthetic") != record_id.startswith(SYNTHETIC_PREFIX):
                issue(table, "record_mode_mismatch", entry.number, key)
                continue
            if source.filename.endswith(".geojson") and parsed.crs != "EPSG:4326":
                issue(table, "geojson_crs_mismatch", entry.number, "crs")
                continue
            rows[table][record_id] = parsed
            source_ids.add(source_id)
        if not native:
            issue(table, "empty_table", severity="warning")
    return rows, mapping, received


def _validate_relations(rows: dict, grant: PilotGrant | None, issue) -> None:
    nodes, pipes = rows["nodes"], rows["pipes"]
    sensors, observations = rows["sensors"], rows["observations"]
    for pipe in pipes.values():
        for field in ("from_node_id", "to_node_id"):
            if getattr(pipe, field) not in nodes:
                issue("pipes", "unknown_node", pipe.source.source_row, field)
    for table in ("gis", "sensors"):
        for row in rows[table].values():
            if row.pipe_id not in pipes:
                issue(table, "unknown_pipe", row.source.source_row, "pipe_id")
            if table == "gis" and grant is not None and row.scope_ref != grant.scope_ref:
                issue(table, "geometry_scope_mismatch", row.source.source_row, "scope_ref")
    slots = set()
    latest_by_sensor = {}
    for obs in observations.values():
        sensor = sensors.get(obs.sensor_id)
        if sensor is None:
            issue("observations", "unknown_sensor", obs.source.source_row, "sensor_id")
        elif obs.unit != sensor.unit:
            issue("observations", "unit_mismatch", obs.source.source_row, "unit")
        slot = (obs.sensor_id, obs.observed_at)
        if slot in slots:
            issue("observations", "duplicate_time", obs.source.source_row, "observed_at")
        slots.add(slot)
        latest = latest_by_sensor.get(obs.sensor_id, obs.observed_at)
        if obs.observed_at < latest:
            issue("observations", "source_observations_out_of_order", obs.source.source_row, "observed_at", severity="warning")
        latest_by_sensor[obs.sensor_id] = max(latest, obs.observed_at)
        if obs.quality != "valid":
            issue("observations", "quality_" + obs.quality, obs.source.source_row, "quality", severity="warning")
    for event in rows["events"].values():
        if (len(set(event.pipe_ids)) != len(event.pipe_ids) or len(set(event.observation_ids)) != len(event.observation_ids)):
            issue("events", "duplicate_relation", event.source.source_row)
        if any(pipe_id not in pipes for pipe_id in event.pipe_ids):
            issue("events", "unknown_pipe", event.source.source_row, "pipe_ids")
        for observation_id in event.observation_ids:
            obs = observations.get(observation_id)
            sensor = sensors.get(obs.sensor_id) if obs else None
            if obs is None or sensor is None or sensor.pipe_id not in event.pipe_ids or obs.observed_at > event.recorded_at:
                issue("events", "invalid_observation_link", event.source.source_row, "observation_ids")
                break
    policies = defaultdict(list)
    for policy in rows["thresholds"].values():
        sensor = sensors.get(policy.sensor_id)
        if sensor is None:
            issue("thresholds", "unknown_sensor", policy.source.source_row, "sensor_id")
        elif policy.unit != sensor.unit:
            issue("thresholds", "unit_mismatch", policy.source.source_row, "unit")
        policies[policy.sensor_id].append(policy)
    for group in policies.values():
        end = None
        versions = set()
        for policy in sorted(group, key=lambda item: item.effective_from):
            if end is not None and policy.effective_from < end:
                issue("thresholds", "overlapping_policy", policy.source.source_row, "effective_from")
            if policy.version in versions:
                issue("thresholds", "duplicate_policy_version", policy.source.source_row, "version")
            end = max(end, policy.effective_until) if end is not None else policy.effective_until
            versions.add(policy.version)


def _validate_scope(rows: dict, grant: PilotGrant, issue) -> None:
    in_scope_nodes = set()
    for pipe in rows["pipes"].values():
        if pipe.pipe_id not in grant.asset_ids:
            issue("pipes", "asset_outside_approved_scope", pipe.source.source_row, "pipe_id")
        if pipe.region_ref != grant.region_ref:
            issue("pipes", "region_outside_approved_scope", pipe.source.source_row, "region_ref")
        in_scope_nodes.update((pipe.from_node_id, pipe.to_node_id))
    for node in rows["nodes"].values():
        if node.node_id not in in_scope_nodes:
            issue("nodes", "node_without_approved_pipe", node.source.source_row, "node_id")
    for table, name in (("observations", "observed_at"), ("events", "recorded_at")):
        for row in rows[table].values():
            if not grant.history_from <= getattr(row, name) < grant.history_to:
                issue(table, "time_outside_approved_scope", row.source.source_row, name)
    for row in rows["truth_windows"].values():
        if row.start < grant.history_from or row.end > grant.history_to:
            issue("truth_windows", "time_outside_approved_scope", row.source.source_row, "start")


def _validate_review(rows: dict, files: dict, issue) -> None:
    sensors, events, windows = rows["sensors"], rows["events"], rows["truth_windows"]
    for rule in rows["threshold_rules"].values():
        sensor = sensors.get(rule.sensor_id)
        if sensor is None or (rule.asset_id, rule.metric_id, rule.unit) != (sensor.pipe_id, sensor.metric, sensor.unit):
            issue("threshold_rules", "threshold_binding_mismatch", rule.source.source_row, "sensor_id")
    complete = defaultdict(list)
    for window in windows.values():
        if window.sensor_id not in sensors:
            issue("truth_windows", "unknown_sensor", window.source.source_row, "sensor_id")
        if window.coverage_status == "complete":
            complete[window.sensor_id].append(window)
            if "truth_events" not in files:
                issue("truth_events", "complete_review_requires_events_table")
    for group in complete.values():
        end = None
        for window in sorted(group, key=lambda item: item.start):
            if end is not None and window.start < end:
                issue("truth_windows", "overlapping_review_windows", window.source.source_row, "start")
            end = max(end, window.end) if end is not None else window.end
    by_window = defaultdict(list)
    occupied = set()
    for item in rows["truth_events"].values():
        window = windows.get(item.truth_window_id)
        event = events.get(item.event_id)
        sensor = sensors.get(window.sensor_id) if window else None
        if (window is None or event is None or sensor is None or sensor.pipe_id not in event.pipe_ids
            or not any(rows["observations"].get(obs_id) is not None
                       and rows["observations"][obs_id].sensor_id == sensor.sensor_id for obs_id in event.observation_ids)
            or not window.start <= item.started_at < window.end
            or (item.recovered_at is not None and item.recovered_at > window.end)):
            issue("truth_events", "invalid_truth_binding", item.source.source_row, "truth_window_id")
            continue
        key = (window.sensor_id, item.event_id)
        if key in occupied:
            issue("truth_events", "duplicate_truth_event", item.source.source_row, "event_id")
        occupied.add(key)
        by_window[item.truth_window_id].append(item)
    for window_id, items in by_window.items():
        end = None
        for item in sorted(items, key=lambda row: row.started_at):
            if end is not None and item.started_at < end:
                issue("truth_events", "overlapping_truth_event", item.source.source_row, "started_at")
            item_end = item.recovered_at or windows[window_id].end
            end = max(end, item_end) if end is not None else item_end


def inspect_pilot(path: Path, *, grants: tuple[PilotGrant, ...] = ()) -> PilotInspection:
    directory, fd = _directory(path)
    issues = []
    counts = {"error": 0, "warning": 0}

    def issue(table, code, row=None, field=None, *, severity="error"):
        counts[severity] += 1
        if len(issues) < 4000:
            issues.append({"table": table, "code": code, "row": row, "field": field, "severity": severity})

    try:
        raw = _file_bytes(directory, fd, "manifest.json", MAX_MANIFEST_BYTES)
        try:
            manifest = PilotManifest.model_validate(json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_fields))
        except (ValueError, RecursionError):
            raise IntakeError("invalid_manifest") from None
        identity = PilotIdentity(
            dataset_id=manifest.dataset_id, version=manifest.version, data_mode=manifest.data_mode,
            manifest_sha256=sha256(raw).hexdigest(),
        )
        grant = package_grant(identity, grants, directory=directory)
        if grant and any(os.path.lexists(parent / ".git") for parent in (directory, *directory.parents)):
            raise IntakeError("real_source_in_git")
        rows, mapping, received = _read_tables(directory, fd, manifest, issue)
    finally:
        os.close(fd)

    _validate_relations(rows, grant, issue)
    if grant:
        _validate_scope(rows, grant, issue)
        if rows["thresholds"]:
            issue("thresholds", "legacy_thresholds_are_fixture_only")
    _validate_review(rows, manifest.files, issue)
    approved_policies = compile_approved_rules(rows["threshold_rules"].values(), issue)
    if grant and not approved_policies:
        issue("threshold_rules", "no_approved_threshold", severity="warning")
    applicable = approved_policies if grant or "threshold_rules" in manifest.files else rows["thresholds"].values()
    for table in ("nodes", "pipes"):
        if not rows[table]:
            issue(table, "empty_required_entities")
    coverage = {
        "pipes": len(rows["pipes"]),
        "pipes_with_gis": len({row.pipe_id for row in rows["gis"].values()} & rows["pipes"].keys()),
        "pipes_with_sensors": len({row.pipe_id for row in rows["sensors"].values()} & rows["pipes"].keys()),
        "sensors_with_readings": len({row.sensor_id for row in rows["observations"].values()} & rows["sensors"].keys()),
        "sensors_with_thresholds": len({row.sensor_id for row in applicable} & rows["sensors"].keys()),
    }
    for table, key, total in (
        ("gis", "pipes_with_gis", len(rows["pipes"])),
        ("sensors", "pipes_with_sensors", len(rows["pipes"])),
        ("observations", "sensors_with_readings", len(rows["sensors"])),
        ("thresholds", "sensors_with_thresholds", len(rows["sensors"])),
    ):
        if coverage[key] < total:
            issue(table, "incomplete_coverage", severity="warning")
    status = "rejected" if counts["error"] else "partial" if counts["warning"] else "ready"
    report = {
        **identity.model_dump(), "status": status, "received": received,
        "structural_rows": {name: len(items) for name, items in rows.items()},
        "coverage": coverage, "issue_counts": counts, "issues": issues, "field_preview": mapping,
    }
    snapshot = None if status == "rejected" else PilotSnapshot(
        **identity.model_dump(), approved_policies=approved_policies, formal_rules_declared="threshold_rules" in manifest.files,
        **{name: tuple(items.values()) for name, items in rows.items()},
    )
    return PilotInspection(identity, report, snapshot, grant.approval_ref if grant else None,
                           grant.scope_digest() if grant else None)
