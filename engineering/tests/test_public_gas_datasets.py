from __future__ import annotations

import hashlib
import json
import sqlite3
import zlib

import pytest
from fastapi.testclient import TestClient

from src.gas_application import public_datasets as public
from src.gas_application.standalone import create_gas_app


def _write_aer_snapshot(tmp_path, *, corrupt_hash: bool = False):
    db = tmp_path / "aer_catalog.sqlite3"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE pipes (
            source_object_id TEXT PRIMARY KEY, pipe_id TEXT, field_centre TEXT,
            segment_status TEXT, pipe_material TEXT, pipe_type TEXT, primary_substance TEXT,
            outside_diameter_mm REAL, licence_number TEXT, company_name TEXT,
            duplicate_segment_id_in_source INTEGER NOT NULL,
            min_lon REAL, min_lat REAL, max_lon REAL, max_lat REAL,
            properties_json TEXT NOT NULL, geometry_zlib BLOB NOT NULL
        );
        CREATE TABLE events (
            incident_id TEXT PRIMARY KEY, source_line_number INTEGER NOT NULL,
            incident_type TEXT, licence_type TEXT, licence_number TEXT, pipe_id_source TEXT,
            linked_pipe_id TEXT, link_status TEXT NOT NULL, incident_date_raw TEXT,
            incident_date TEXT, notification_date_raw TEXT, complete_date_raw TEXT,
            source_category TEXT, cause_category TEXT, cause_type TEXT, failure_type TEXT,
            source_location_description TEXT, pipe_damage_type TEXT, pipe_test_failure TEXT,
            pipe_material_source_value TEXT, segment_status_source_value TEXT, substances_json TEXT NOT NULL
        );
    """)
    geometry = zlib.compress(json.dumps({"type": "LineString", "coordinates": [[-114.1, 51.0], [-114.0, 51.0]]}).encode())
    conn.execute("INSERT INTO pipes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "oid-1", "pipe-1", "Calgary", "Operating", "Steel", "Transmission", "Gas", 100.0,
        "lic-1", "operator-1", 0, -114.1, 51.0, -114.0, 51.0, json.dumps({"source_field": "value"}), geometry,
    ))
    conn.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "event-1", 2, "Release", "Pipeline", "lic-1", "pipe-1", "pipe-1", "linked_unique_gas_segment",
        "2020-01-01", "2020-01-01", None, None, "source", "cause", "cause-type", "failure", None, None,
        None, "Steel", "Operating", "[]",
    ))
    conn.commit()
    conn.close()
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(json.dumps({
        "dataset_id": "AER-ENHANCED-PIPELINE+FIS-RELEASE-REPORT",
        "data_version": "test-version", "database": db.name,
        "database_sha256": "0" * 64 if corrupt_hash else digest,
    }), encoding="utf-8")
    return db


def test_aer_read_only_adapter_verifies_manifest_and_keeps_source_semantics(tmp_path, monkeypatch):
    _write_aer_snapshot(tmp_path)
    monkeypatch.setenv("GAS_AER_DATA_ROOT", str(tmp_path))
    public._verified_aer_snapshot.cache_clear()
    rows = public.aer_assets(field_centre="Calgary", limit=10)
    assert rows["data_version"] == "test-version"
    assert rows["total"] == 1
    assert rows["items"][0]["pipe_id"] == "pipe-1"
    assert rows["items"][0]["geometry"]["type"] == "LineString"
    events = public.aer_events(pipe_id="pipe-1")
    assert events["items"][0]["link_status"] == "linked_unique_gas_segment"
    assert "不等同于确认泄漏" in events["items"][0]["classification_warning"]
    page = public.public_overview(public.AER_ID, field_centre="__all__", limit=1, offset=1)
    assert page["scope"] == "全部分区"
    assert page["assets_page_offset"] == 1
    assert page["assets"] == []


def test_aer_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    _write_aer_snapshot(tmp_path, corrupt_hash=True)
    monkeypatch.setenv("GAS_AER_DATA_ROOT", str(tmp_path))
    public._verified_aer_snapshot.cache_clear()
    with pytest.raises(public.PublicDatasetUnavailable, match="SHA-256"):
        public.aer_assets(limit=1)


def test_sgn_adapter_preserves_lines_but_does_not_infer_topology(monkeypatch):
    def fake_fetch(url, *, timeout=12.0):
        assert "outSR=4326" in url
        assert "resultOffset=5" in url
        return {"features": [{"attributes": {"OBJECTID": 12, "MATERIAL": "PE"},
                              "geometry": {"paths": [[[-0.1, 50.8], [-0.09, 50.81]]]} }]}
    monkeypatch.setattr(public, "_fetch_json", fake_fetch)
    response = public.sgn_assets(offset=5, limit=1)
    assert response["offset"] == 5
    assert response["items"][0]["pipe_id"] == "12"
    assert response["items"][0]["geometry"]["type"] == "LineString"
    assert "from_node_id" not in response["items"][0]
    assert "拓扑连接" in response["limitation"]


def test_gaslib_parser_checks_complete_counts_and_never_claims_solver(tmp_path, monkeypatch):
    nodes = "".join(f'<source id="n{i}" geoWGS84Long="9.{i % 10}" geoWGS84Lat="47.{i % 10}" />' for i in range(582))
    edges = "".join(f'<pipe id="e{i}" from="n{i % 582}" to="n{(i + 1) % 582}" />' for i in range(609))
    (tmp_path / "GasLib-582-v2-test.net").write_text(
        f'<network xmlns="http://gaslib.zib.de/Gas"><nodes>{nodes}</nodes><connections>{edges}</connections></network>',
        encoding="utf-8",
    )
    monkeypatch.setenv("GASLIB_582_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("GASLIB_582_NETWORK_FILE", raising=False)
    result = public.gaslib_network()
    assert result["node_count"] == 582
    assert result["connection_count"] == 609
    assert result["solver_status"] == "unavailable"
    assert "不会用图可达性" in result["solver_reason"]
    assert public.public_overview(public.GASLIB_ID)["geometry_note"] == result["geometry_note"]


def test_gaslib_multiple_network_versions_require_explicit_file(tmp_path, monkeypatch):
    (tmp_path / "GasLib-582-v2-20210101.net").write_text("<network />", encoding="utf-8")
    (tmp_path / "GasLib-582-v2-20211129.net").write_text("<network />", encoding="utf-8")
    monkeypatch.setenv("GASLIB_582_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("GASLIB_582_NETWORK_FILE", raising=False)
    with pytest.raises(public.PublicDatasetUnavailable, match="多个582-v2"):
        public.gaslib_network()
    monkeypatch.setenv("GASLIB_582_NETWORK_FILE", "GasLib-582-v2-20211129.net")
    assert public._gaslib_network_file(tmp_path).name == "GasLib-582-v2-20211129.net"


def test_merrimack_evidence_labels_reported_customers_and_source_links():
    result = public.public_overview(public.MERRIMACK_ID)
    facts = {item["label"]: item["value"] for item in result["facts"]}
    assert facts["受影响客户"] == 10894
    assert facts["受损建筑"] == 131
    assert len(result["evidence"]) == 4
    assert any("客户影响口径" in item["label"] for item in result["evidence"])
    assert "本系统拓扑算法计算结果" in result["limitations"][1]


def test_catalog_has_distinct_modes_and_does_not_fallback_to_synthetic(tmp_path, monkeypatch):
    monkeypatch.setenv("GAS_AER_DATA_ROOT", str(tmp_path))
    monkeypatch.delenv("GASLIB_582_DATA_ROOT", raising=False)
    public._verified_aer_snapshot.cache_clear()
    catalog = public.dataset_registry()
    by_id = {item["dataset_id"]: item for item in catalog["datasets"]}
    assert by_id[public.AER_ID]["status"] == "unavailable"
    assert by_id[public.AER_ID]["default"] is True
    assert by_id[public.GASLIB_ID]["status"] == "not_installed"
    assert by_id[public.MERRIMACK_ID]["data_mode"] == "official_case_evidence"
    assert "不在来源切换时自动回退" in catalog["security_note"]


def _enable_local_public_read(monkeypatch, enabled: bool):
    monkeypatch.setenv("WATER_RUNTIME_MODE", "local")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "1")
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    monkeypatch.delenv("WATER_PRINCIPALS", raising=False)
    if enabled:
        monkeypatch.setenv("GAS_PUBLIC_DATA_READONLY", "1")
    else:
        monkeypatch.delenv("GAS_PUBLIC_DATA_READONLY", raising=False)


def test_public_read_routes_require_explicit_local_opt_in(monkeypatch):
    _enable_local_public_read(monkeypatch, False)
    with TestClient(create_gas_app()) as client:
        assert client.get("/api/v1/gas/public-data/catalog").status_code == 403

    _enable_local_public_read(monkeypatch, True)
    with TestClient(create_gas_app()) as client:
        response = client.get("/api/v1/gas/public-data/catalog")
    assert response.status_code == 200
    assert {item["dataset_id"] for item in response.json()["datasets"]} == {
        public.SYNTHETIC_ID, public.AER_ID, public.SGN_ID, public.GASLIB_ID, public.MERRIMACK_ID,
        "ngn_the_north", "charlottesville", "schutterwald", "sicily_transmission", "ngn_distribution_example",
    }


def test_sicily_requires_source_access_and_reports_missing_snapshot(monkeypatch):
    monkeypatch.delenv("GAS_SICILY_DATA_ROOT", raising=False)
    route = "/api/v1/gas/public-data/sicily_transmission"
    params = {"case_id": "20251115-12", "edge_id": "36", "version": "version"}
    _enable_local_public_read(monkeypatch, False)
    with TestClient(create_gas_app()) as client:
        assert client.post(route + "/run", params=params).status_code == 403
    _enable_local_public_read(monkeypatch, True)
    with TestClient(create_gas_app()) as client:
        assert client.post(route + "/run", params=params).status_code == 503
        assert client.get(route + "/run", params=params).status_code == 405


def test_ngn_sample_requires_opt_in_and_uses_explicit_post(monkeypatch):
    monkeypatch.delenv("NGN_SAMPLE_DATA_ROOT", raising=False)
    route = "/api/v1/gas/public-data/ngn_distribution_example"
    params = {"version": "unavailable", "period": "1", "edge_id": "LINE_10077"}
    _enable_local_public_read(monkeypatch, False)
    with TestClient(create_gas_app()) as client:
        assert client.post(route + "/run", params=params).status_code == 403
    _enable_local_public_read(monkeypatch, True)
    with TestClient(create_gas_app()) as client:
        assert client.get(route + "/overview").status_code == 503
        assert client.post(route + "/run", params=params).status_code == 503
        assert client.get(route + "/run", params=params).status_code == 405


def test_merrimack_overview_is_readable_without_fake_topology(monkeypatch):
    _enable_local_public_read(monkeypatch, True)
    with TestClient(create_gas_app()) as client:
        response = client.get(f"/api/v1/gas/public-data/{public.MERRIMACK_ID}/overview")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert "nodes" not in body and "edges" not in body
    assert body["facts"][0]["value"] == 10894
