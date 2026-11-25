"""Build a sealed synthetic version for PILOT-02 replay and browser acceptance."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

FIXTURE = Path(__file__).parent / "fixtures/gas_pilot_synthetic"
PREFIX = "syn-gas-pilot-"
DATASET = PREFIX + "history-01"
SENSOR = PREFIX + "pressure-a"
PIPE = PREFIX + "pipe-a"
OBSERVATION = PREFIX + "obs-p3"


def time_at(minute: int, second: int = 0) -> str:
    return f"2026-11-18T01:{minute:02}:{second:02}Z"


def _read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def set_row(source: Path, table: str, index: int, **changes: object) -> None:
    path = source / f"{table}.csv"
    rows = _read(path)
    rows[index].update(changes)
    _write(path, rows)
    seal(source)


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def seal(source: Path, manifest: dict | None = None) -> None:
    path = source / "manifest.json"
    if manifest is None:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    for entry in manifest["files"].values():
        entry["sha256"] = sha256((source / entry["filename"]).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _rule(identifier, version, upper, start, end, status="approved"):
    return dict(
        source_record_id=f"synthetic-rule-{identifier}",
        rule_id=PREFIX + f"rule-{identifier}",
        version=version,
        metric_id="pressure",
        asset_id=PIPE,
        sensor_id=SENSOR,
        unit="kPa",
        threshold_type="upper",
        threshold_value=upper,
        effective_from=time_at(start),
        effective_to=time_at(end),
        approval_status=status,
        approved_by="synthetic-reviewer" if status == "approved" else None,
        approved_at="2026-11-17T08:00:00Z" if status == "approved" else None,
        evidence_reference=f"synthetic-threshold-reference-{identifier}",
        cadence_seconds=60,
        sustained_samples=3,
        missing_samples=2,
    )


def build_synthetic_validation(destination: Path) -> Path:
    """Keep the legacy sample and produce a separately identified new source package."""
    shutil.copytree(FIXTURE, destination)
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    manifest["version"] = "demo-v2"
    del manifest["files"]["thresholds"]
    (destination / "thresholds.csv").unlink()

    values = (5, *([7.2] * 11), 5, None, 9, 5, 7.1, 7.1, 7.1, 5, 6.8, 5.1, 5.2)
    observations = [
        dict(source_record_id=f"p-{minute}", observation_id=PREFIX + f"obs-p{minute}",
             sensor_id=SENSOR, observed_at=time_at(minute), value=value, unit="kPa",
             quality={13: "missing", 14: "suspect"}.get(minute, "valid"))
        for minute, value in enumerate(values)
    ]
    observations.extend(row for row in _read(destination / "observations.csv") if row["sensor_id"] != SENSOR)
    legacy_event, = _read(destination / "events.csv")
    events = [legacy_event, {
        **legacy_event, "event_id": PREFIX + "event-b", "source_record_id": "event-source-row-b",
        "recorded_at": time_at(22), "observation_ids": json.dumps([PREFIX + "obs-p20"]),
        "evidence_ref": "synthetic-ticket-002",
    }]
    window = PREFIX + "review-a"
    truth_windows = [
        dict(source_record_id="synthetic-review-row", truth_window_id=window, sensor_id=SENSOR,
             start=time_at(0), end=time_at(22), coverage_status="complete", reviewed_by="synthetic-reviewer",
             reviewed_at=time_at(24), evidence_reference="synthetic-review-ledger"),
    ]
    truth_events = [
        dict(source_record_id=f"synthetic-verified-row-{label}", truth_event_id=PREFIX + f"verified-{label}",
             truth_window_id=window, event_id=PREFIX + f"event-{label}", direction="above_upper",
             started_at=started_at, recovered_at=recovered_at, evidence_reference=f"synthetic-verified-ticket-{label}")
        for label, started_at, recovered_at in (("a", time_at(1), time_at(11, 30)), ("b", time_at(20), time_at(21)))
    ]
    tables = {
        "observations": observations,
        "events": events,
        "threshold_rules": [
            _rule("a1", "pilot-r1", 6, 0, 8),
            _rule("a2", "pilot-r2", 6.5, 8, 24),
            _rule("draft", "draft-ignored", 4, 0, 24, status="draft"),
            _rule("revoked", "revoked-ignored", 4, 0, 24, status="revoked"),
        ],
        "truth_windows": truth_windows,
        "truth_events": truth_events,
    }
    for table, rows in tables.items():
        filename = table + ".csv"
        _write(destination / filename, rows)
        manifest["files"][table] = dict(
            filename=filename, source_id=PREFIX + f"{table}-source", version="fixture-v2",
            columns={field: field for field in rows[0]},
        )
    seal(destination, manifest)
    return destination


if __name__ == "__main__":
    build_synthetic_validation(Path(sys.argv[1]))
