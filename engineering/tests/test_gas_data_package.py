"""Gas package intake from files to a bounded, value-free quality report."""
from __future__ import annotations

import builtins
import csv
from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from src.gas_application.data_package import ApprovedPackage, IntakeError, read_package

DATASET_ID = "syn-gas-intake-qa-01"
VERSION = "1.0.0"
APPROVAL_REF = "qa-simulated-approval"
COLUMNS = {
    "assets": ("source_record_id", "asset_id", "asset_type", "name", "district_id"),
    "coordinates": ("source_record_id", "asset_id", "crs", "longitude", "latitude"),
    "monitoring": ("source_record_id", "sensor_id", "asset_id", "observed_at", "metric", "value", "unit", "quality"),
}
GOOD_ROWS = {
    "assets": [("a-qa-1", "syn-qa-pipe-1", "pipe", "qa-private-label", "")],
    "coordinates": [("c-qa-1", "syn-qa-pipe-1", "WGS84", "100.217", "20.419")],
    "monitoring": [
        ("m-qa-1", "qa-private-meter", "syn-qa-pipe-1", "2026-11-18T10:00:00+08:00", "pressure", "12.34567", "kPa", "valid"),
    ],
}


def write_manifest(directory: Path, data: dict) -> str:
    payload = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
    (directory / "manifest.json").write_bytes(payload)
    return sha256(payload).hexdigest()


def make_package(directory: Path, rows=None, *, mode="synthetic") -> ApprovedPackage:
    directory.mkdir(mode=0o700, parents=True)
    rows = GOOD_ROWS if rows is None else rows
    sources = {}
    for table, records in rows.items():
        source_file = directory / f"{table}.csv"
        with source_file.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS[table])
            writer.writerows(records)
        sources[table] = {
            "source_id": f"qa-{table}",
            "version": VERSION,
            "sha256": sha256(source_file.read_bytes()).hexdigest(),
        }
    manifest = {
        "schema_version": 1, "dataset_id": DATASET_ID, "version": VERSION,
        "data_mode": mode, "sources": sources,
    }
    digest = write_manifest(directory, manifest)
    return ApprovedPackage(directory=directory, dataset_id=DATASET_ID, manifest_sha256=digest, approval_ref=APPROVAL_REF)


def fail_on_csv_open(monkeypatch):
    def guard(original_open):
        def open_checked(file, *args, **kwargs):
            if isinstance(file, (str, bytes, os.PathLike)):
                candidate = Path(os.fsdecode(file)).name
                if candidate.endswith(".csv"):
                    pytest.fail(f"source file opened before approval passed: {candidate}")
            return original_open(file, *args, **kwargs)
        return open_checked

    os_open = os.open
    captured_open = guard(os_open)
    if os_open in os.supports_dir_fd:
        # Exercise the same directory-FD path the Linux intake normally uses.
        monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {captured_open})
    monkeypatch.setattr(os, "open", captured_open)
    for module in (builtins, io):
        monkeypatch.setattr(module, "open", guard(module.open))


def test_synthetic_fixture_is_validated_and_has_redacted_quality_report(tmp_path):
    package = make_package(tmp_path / "package")
    snapshot = read_package(package.directory)
    assert (snapshot.dataset_id, snapshot.version, snapshot.data_mode) == (DATASET_ID, VERSION, "synthetic")
    assert snapshot.manifest_sha256 == package.manifest_sha256
    assert not snapshot.rows.issues
    asset, = snapshot.rows.assets
    coordinate, = snapshot.rows.coordinates
    reading, = snapshot.rows.monitoring
    assert asset.asset_id == coordinate.asset_id == reading.asset_id == "syn-qa-pipe-1"
    assert coordinate.coordinate.coordinate_system == "wgs84"
    assert reading.observed_at == datetime(2026, 11, 18, 2, tzinfo=timezone.utc)
    assert reading.provenance.source_record_id == "m-qa-1"

    report = snapshot.quality_report()
    assert report["dataset_id"] == DATASET_ID
    assert report["version"] == VERSION
    assert report["data_mode"] == "synthetic"
    assert report["status"] == "ready"
    assert report["counts"] == report["received_counts"] == dict.fromkeys(COLUMNS, 1)
    assert not report["issues"]
    for table, source in report["sources"].items():
        assert source["sha256"] == sha256((package.directory / f"{table}.csv").read_bytes()).hexdigest()
    encoded = json.dumps(report)
    for private in ("qa-private-label", "qa-private-meter", "syn-qa-pipe-1", "100.217", "20.419", "12.34567", str(tmp_path)):
        assert private not in encoded


def test_missing_optional_sources_does_not_make_up_values(tmp_path):
    package = make_package(tmp_path / "package", {"assets": GOOD_ROWS["assets"]})
    snapshot = read_package(package.directory)
    assert len(snapshot.rows.assets) == 1
    assert not snapshot.rows.coordinates and not snapshot.rows.monitoring
    report = snapshot.quality_report()
    assert report["status"] == "partial"
    assert report["counts"] == {"assets": 1, "coordinates": 0, "monitoring": 0}
    assert {(issue["table"], issue["code"]) for issue in report["issues"]} == {
        ("coordinates", "missing_source"), ("monitoring", "missing_source"),
        ("coordinates", "incomplete_asset_coverage"), ("monitoring", "incomplete_asset_coverage"),
    }
    assert report["coverage"] == {
        "assets_with_coordinates": 0, "assets_without_coordinates": 1,
        "assets_with_monitoring": 0, "assets_without_monitoring": 1,
    }


def test_table_checksum_must_match_manifest_even_for_synthetic_input(tmp_path):
    package = make_package(tmp_path / "package")
    target = package.directory / "monitoring.csv"
    with target.open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory)
    assert failure.value.code == "hash_mismatch"


def test_rejected_values_remain_missing_and_suspect_is_distinct(tmp_path):
    assets = GOOD_ROWS["assets"] + [
        ("a-qa-2", "syn-qa-pipe-2", "pipe", "qa-duplicate-label", ""),
        ("a-qa-3", "syn-qa-pipe-2", "pipe", "qa-second-label", ""),
        ("a-qa-4", "syn-qa-pipe-3", "pipe", "qa-bad-crs-label", ""),
        ("a-qa-5", "syn-qa-pump", "pump", "qa-unknown-type-label", ""),
    ]
    coordinates = GOOD_ROWS["coordinates"] + [
        ("c-qa-2", "syn-qa-pipe-3", "UNSPECIFIED", "999.913", "999.941"),
        ("c-qa-3", "syn-qa-pipe-2", "WGS84", "100.1", "20.1"),
    ]
    good = GOOD_ROWS["monitoring"][0]
    monitoring = [
        (*good[:-1], "suspect"),
        ("m-qa-2", "qa-orphan-sensor", "syn-qa-missing", good[3], "pressure", "345.6731", "kPa", "valid"),
        ("m-qa-3", "qa-no-zone", good[2], "2026-11-18T10:00:00", "pressure", "4.2", "kPa", "valid"),
        ("m-qa-4", "qa-invalid-reading", good[2], good[3], "pressure", "73654.123", "kPa", "invalid"),
        ("m-qa-5", "qa-unsupported-unit", good[2], good[3], "pressure", "4.2", "psi", "valid"),
        ("m-qa-6", "qa-duplicate-reading", good[2], "2026-11-18T10:05:00+08:00", "pressure", "1.2", "kPa", "valid"),
        ("m-qa-7", "qa-duplicate-reading", good[2], "2026-11-18T02:05:00Z", "pressure", "3.4", "kPa", "valid"),
    ]
    package = make_package(
        tmp_path / "package", {"assets": assets, "coordinates": coordinates, "monitoring": monitoring},
    )
    snapshot = read_package(package.directory)
    report = snapshot.quality_report()
    assert report["status"] == "partial"
    assert report["counts"] == {"assets": 2, "coordinates": 1, "monitoring": 1}
    assert report["received_counts"] == {"assets": 5, "coordinates": 3, "monitoring": 7}
    assert {asset.asset_id for asset in snapshot.rows.assets} == {"syn-qa-pipe-1", "syn-qa-pipe-3"}
    reading, = snapshot.rows.monitoring
    assert reading.quality == "suspect"
    issues = report["issues"]
    actual_codes = {(issue["table"], issue["code"]) for issue in issues}
    assert {
        ("assets", "duplicate_asset_id"), ("assets", "unsupported_asset_type"),
        ("coordinates", "unsupported_crs"), ("coordinates", "orphan_asset"),
        ("monitoring", "orphan_asset"), ("monitoring", "timezone_required"),
        ("monitoring", "invalid_quality"), ("monitoring", "unsupported_unit"),
        ("monitoring", "duplicate_reading"), ("monitoring", "suspect_quality"),
    } <= actual_codes
    assert all("row" in issue and "field" in issue for issue in issues)
    encoded = json.dumps(report)
    for private in ("qa-duplicate-label", "qa-orphan-sensor", "999.913", "345.6731", "73654.123", "UNSPECIFIED"):
        assert private not in encoded


def test_real_mode_requires_approval_before_source_read(tmp_path, monkeypatch):
    package = make_package(tmp_path / "package", mode="real_authorized")
    fail_on_csv_open(monkeypatch)
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory)
    assert failure.value.code == "approval_required"


@pytest.mark.parametrize("field", ["directory", "dataset_id", "manifest_sha256"])
def test_mismatched_approval_stops_before_any_source_open(tmp_path, monkeypatch, field):
    package = make_package(tmp_path / "package", mode="real_authorized")
    wrong = {"directory": tmp_path / "another", "dataset_id": "another-dataset", "manifest_sha256": "f" * 64}
    fail_on_csv_open(monkeypatch)
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory, approval=replace(package, **{field: wrong[field]}))
    assert failure.value.code == "approval_mismatch"


def test_test_only_matching_approval_enables_virtual_real_mode(tmp_path):
    package = make_package(tmp_path / "simulated-approval", mode="real_authorized")
    snapshot = read_package(package.directory, approval=package)
    assert snapshot.data_mode == "real_authorized"
    assert snapshot.manifest_sha256 == package.manifest_sha256
    report = snapshot.quality_report()
    assert report["status"] == "ready"
    assert report["validation_status"] == "not_business_validated"
    assert APPROVAL_REF not in json.dumps(report)


@pytest.mark.parametrize("git_marker", ["directory", "worktree_pointer"])
def test_simulated_authorized_source_is_blocked_inside_git_tree(tmp_path, monkeypatch, git_marker):
    repository = tmp_path / "repo"
    repository.mkdir()
    dot_git = repository / ".git"
    if git_marker == "directory":
        dot_git.mkdir()
    else:
        dot_git.write_text("gitdir: test-worktree-metadata\n", encoding="utf-8")
    package = make_package(repository / "materials" / "package", mode="real_authorized")
    fail_on_csv_open(monkeypatch)
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory, approval=package)
    assert failure.value.code == "real_source_in_git"


@pytest.mark.parametrize("entry", ["manifest.json", "coordinates.csv"])
def test_package_wont_follow_source_symlinks(tmp_path, entry):
    package = make_package(tmp_path / "package")
    original = package.directory / entry
    outside = tmp_path / entry
    original.rename(outside)
    original.symlink_to(outside)
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory)
    assert failure.value.code == "file_unavailable"


def test_package_path_itself_cannot_be_a_symlink(tmp_path):
    package = make_package(tmp_path / "package")
    alias = tmp_path / "linked-package"
    alias.symlink_to(package.directory, target_is_directory=True)
    with pytest.raises(IntakeError) as failure:
        read_package(alias)
    assert failure.value.code == "missing_directory"


@pytest.mark.parametrize("outside", ["../outside.csv", "/tmp/outside.csv", r"C:\temp\outside.csv"])
def test_manifest_cannot_override_fixed_source_filenames(tmp_path, outside):
    package = make_package(tmp_path / "package")
    manifest = json.loads((package.directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["sources"]["assets"]["path"] = outside
    write_manifest(package.directory, manifest)
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory)
    assert failure.value.code == "invalid_manifest"



@pytest.mark.parametrize(("complete", "returncode", "status"), [(True, 0, "ready"), (False, 1, "partial")])
def test_cli_preserves_synthetic_quality_status(tmp_path, complete, returncode, status):
    data = None if complete else {"assets": GOOD_ROWS["assets"]}
    package = make_package(tmp_path / "package", data)
    result = subprocess.run(
        [sys.executable, "-B", "-m", "src.gas_application.data_package", str(package.directory)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == returncode
    assert json.loads(result.stdout)["status"] == status
    assert not result.stderr


def test_default_cli_declines_real_mode_and_prints_only_bounded_error(tmp_path):
    package = make_package(tmp_path / "simulated-real", mode="real_authorized")
    result = subprocess.run(
        [sys.executable, "-B", "-m", "src.gas_application.data_package", str(package.directory)],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 2
    report = json.loads(result.stdout)
    assert report["code"] == "approval_required"
    assert set(report) <= {"status", "code", "table", "row"}
    assert not result.stderr
    assert str(tmp_path) not in result.stdout



def test_declared_file_missing_is_an_integrity_error(tmp_path):
    package = make_package(tmp_path / "package")
    (package.directory / "monitoring.csv").unlink()
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory)
    assert failure.value.code == "file_unavailable"
    assert failure.value.table == "monitoring"


def test_asset_source_with_no_records_is_unavailable_not_an_empty_success(tmp_path):
    package = make_package(tmp_path / "package", {"assets": []})
    report = read_package(package.directory).quality_report()
    assert report["status"] == "unavailable"
    assert report["counts"] == dict.fromkeys(COLUMNS, 0)
    assert ("assets", "no_usable_rows") in {(issue["table"], issue["code"]) for issue in report["issues"]}


def test_nonfinite_monitoring_never_becomes_a_zero_observation(tmp_path):
    raw = GOOD_ROWS["monitoring"][0]
    defective = [(*raw[:5], "1e999", *raw[6:])]
    package = make_package(tmp_path / "package", {**GOOD_ROWS, "monitoring": defective})
    snapshot = read_package(package.directory)
    assert not snapshot.rows.monitoring
    report = snapshot.quality_report()
    assert report["status"] == "partial"
    assert report["counts"]["monitoring"] == 0
    assert ("monitoring", "invalid_number") in {(issue["table"], issue["code"]) for issue in report["issues"]}

def test_submicrosecond_timestamps_are_diagnosed_without_rounding(tmp_path):
    sample = list(GOOD_ROWS["monitoring"][0])
    sample[3] = "2026-11-18T10:00:00.1234567+08:00"
    package = make_package(tmp_path / "package", {**GOOD_ROWS, "monitoring": [tuple(sample)]})
    snapshot = read_package(package.directory)
    assert not snapshot.rows.monitoring
    assert any(issue.code == "invalid_timestamp" for issue in snapshot.rows.issues)
    assert "qa-private-label" not in repr(snapshot)


def test_real_grant_cannot_switch_to_a_synthetic_package(tmp_path, monkeypatch):
    package = make_package(tmp_path / "package")
    fail_on_csv_open(monkeypatch)
    with pytest.raises(IntakeError) as failure:
        read_package(package.directory, approval=package)
    assert failure.value.code == "approval_mismatch"
