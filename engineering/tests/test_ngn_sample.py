
import json
import os
import struct
from pathlib import Path

import pytest

from src.gas_application import ngn_sample


def test_geopackage_xyzm_geometry():
    header = b"GP\0\1" + struct.pack("<i", 27700)
    first = struct.pack("<BIIdddd", 1, 3002, 1, 300000, 450000, 0, 0)
    second = struct.pack("<BIIdddd", 1, 3002, 1, 300001, 450005, 1, 2)
    geometry = header + struct.pack("<BII", 1, 3005, 2) + first + second
    assert ngn_sample._geometry(geometry) == [[[300000, 450000]], [[300001, 450005]]]


def test_snapshot_missing_or_corrupt(tmp_path, monkeypatch):
    monkeypatch.delenv("NGN_SAMPLE_DATA_ROOT", raising=False)
    assert ngn_sample.registry_item()["status"] == "not_installed"
    monkeypatch.setenv("NGN_SAMPLE_DATA_ROOT", str(tmp_path))
    (tmp_path / "revision.txt").write_text(ngn_sample.SOURCE_REVISION, encoding="ascii")
    for name in ngn_sample.SOURCE_HASHES:
        (tmp_path / name).write_bytes(b"not the published file")
    with pytest.raises(OSError, match="SHA-256"):
        ngn_sample.overview()


def test_published_sample_hydraulics(monkeypatch):
    root = Path(__file__).resolve().parents[1] / "outputs/research/ngn-sample"
    if not root.is_dir() or not os.getenv("NGN_SAMPLE_SOLVER_PYTHON"):
        pytest.skip("published data and pinned pandapipes interpreter required")
    monkeypatch.setenv("NGN_SAMPLE_DATA_ROOT", str(root))
    overview = ngn_sample.overview()
    assert overview["counts"] == dict(pipes=103, nodes=98, sources=2, sinks=24, periods=10)
    assert overview["input_sha256"] == ngn_sample.SOURCE_HASHES
    version = overview["data_version"]
    with pytest.raises(ValueError):
        ngn_sample.run(version, "11", None)
    with pytest.raises(ValueError):
        ngn_sample.run(version, "1", "unknown")
    first = ngn_sample.run(version, "1", "LINE_10077")
    second = ngn_sample.run(version, "1", "LINE_10077")
    assert first["status"] == "ok", first
    assert set(first["after"]["unavailable_sinks"]) == {"HOUSE_09868"}
    assert "HOUSE_09868" not in first["after"]["sink_paths"]
    assert "LINE_10077" not in first["after"]["flow_kg_s"]
    assert first["before"]["max_nodal_mass_error_kg_s"] <= 1e-6
    assert first["after"]["max_nodal_mass_error_kg_s"] <= 1e-6
    assert first["stable_result_sha256"] == second["stable_result_sha256"]
    assert first["execution_id"] != second["execution_id"]
    series = ngn_sample.run(version, "base", None, trend=True)
    assert len(series["series"]) == 10
    assert all(row["status"] == "ok" for row in series["series"].values())
    json.dumps(series, allow_nan=False)
