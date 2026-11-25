"""Synthetic acceptance of offline gas pilot packages, read scope and replay evidence."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sqlite3

from fastapi.testclient import TestClient
import httpx
from openpyxl import Workbook
import pytest

from src.gas_application.data_package import IntakeError
from src.gas_application.model_provider import MODEL_ID, LocalGasModel
from src.gas_application.pilot_intake import inspect_pilot
from src.gas_application.pilot_store import PilotStore
from src.gas_application.standalone import create_gas_app

FIXTURE = Path(__file__).parent / "fixtures/gas_pilot_synthetic"
PREF = "syn-gas-pilot-"
DATASET = PREF + "history-01"
BASE = "/api/v1/gas/engineering/pilot/datasets"
VERSION = f"{BASE}/{DATASET}/versions/demo-v1"
READER = {"X-Water-Token": "pilot-reader"}
OTHER = {"X-Water-Token": "no-pilot"}
OBSERVATION = PREF + "obs-p3"


@pytest.fixture
def source(tmp_path):
    return Path(shutil.copytree(FIXTURE, tmp_path / "source"))


@pytest.fixture
def store(tmp_path):
    directory = tmp_path / "isolated"
    directory.mkdir(mode=0o700)
    return PilotStore(directory)


def _manifest(source, update=None):
    path = source / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if update:
        update(manifest)
    for entry in manifest["files"].values():
        entry["sha256"] = sha256((source / entry["filename"]).read_bytes()).hexdigest()
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _edit_row(source, table, number, field, value):
    path = source / (table + ".csv")
    with path.open(encoding="utf-8", newline="") as in_file:
        reader = csv.DictReader(in_file)
        fields, rows = reader.fieldnames, list(reader)
    rows[number][field] = value
    with path.open("w", encoding="utf-8", newline="") as out:
        writer = csv.DictWriter(out, fields)
        writer.writeheader()
        writer.writerows(rows)
    _manifest(source)


def test_offline_preview_and_immutable_import(source, store):
    inspection = inspect_pilot(source)
    assert inspection.report["status"] == "partial"
    assert inspection.report["issue_counts"] == {"error": 0, "warning": 2}
    assert inspection.report["field_preview"]["sensors"]["columns"]["sensor_id"] == "测点编号"
    assert inspection.report["coverage"]["pipes_with_gis"] == 2
    assert inspection.snapshot.observations[1].observed_at.isoformat() == "2026-11-18T01:01:00+00:00"
    assert store.prepare(inspection) == {"status": "imported"}
    assert store.prepare(inspection) == {"status": "already_imported"}
    assert store.snapshot(inspection.identity) == inspection.snapshot
    _edit_row(source, "observations", 0, "value", "5.2")
    with pytest.raises(IntakeError, match="dataset_version_conflict"):
        store.prepare(inspect_pilot(source))
    assert store.database.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(("table", "row_index", "field", "value", "expected"), [
    ("nodes", 0, "node_id", "../invalid", "invalid_field"),
    ("pipes", 0, "from_node_id", "syn-gas-pilot-absent", "unknown_node"),
    ("observations", 0, "observed_at", "2026-11-18T01:00:00", "invalid_field"),
    ("observations", 0, "unit", "psi", "unit_mismatch"),
    ("observations", 1, "observed_at", "2026-11-18T09:00:00+08:00", "duplicate_time"),
    ("events", 0, "pipe_ids", '["syn-gas-pilot-pipe-b"]', "invalid_observation_link"),
    ("thresholds", 1, "effective_from", "2026-11-18T01:07:00Z", "overlapping_policy"),
])
def test_bad_rows_reject_atomically_and_record_refusal(source, store, table, row_index, field, value, expected):
    _edit_row(source, table, row_index, field, value)
    report = inspect_pilot(source)
    assert report.snapshot is None
    assert any(issue["code"] == expected for issue in report.report["issues"])
    assert store.prepare(report)["status"] == "rejected"
    assert store.entry(DATASET, "demo-v1") is None
    with sqlite3.connect(store.database) as db:
        row, = db.execute("SELECT report FROM rejections").fetchone()
    assert value not in row


def test_geometry_range_and_crs_mapping(source):
    path = source / "gis.geojson"
    feature = json.loads(path.read_text())
    feature["features"][0]["geometry"]["coordinates"][0][0] = 181
    path.write_text(json.dumps(feature))
    _manifest(source)
    result = inspect_pilot(source)
    assert result.snapshot is None
    assert any(item["table"] == "gis" and item["severity"] == "error" for item in result.report["issues"])
    fields = ("source_record_id", "geometry_id", "pipe_id", "crs", "geometry", "scope_ref", "valid_from", "valid_until")
    path = source / "gis.csv"
    with path.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fields)
        writer.writeheader()
        props = feature["features"][0]["properties"]
        writer.writerow({**props, "crs": "EPSG:3857",
                         "geometry": '{"type":"LineString","coordinates":[[0,0],[111319.490793,0]]}'})
    _manifest(source, lambda doc: doc["files"]["gis"].update(filename=path.name))
    converted = inspect_pilot(source).snapshot.gis[0].wgs84()
    assert converted.coordinates[1] == pytest.approx((1, 0))


def test_mapped_excel_sheet_and_formula_rejection(source):
    with (source / "nodes.csv").open(encoding="utf-8") as stream:
        rows = list(csv.reader(stream))
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "试点节点"
    for row in rows:
        sheet.append(row)
    xlsx = source / "pilot-nodes.xlsx"
    workbook.save(xlsx)
    _manifest(source, lambda doc: doc["files"]["nodes"].update(filename=xlsx.name, sheet=sheet.title))
    first = inspect_pilot(source).snapshot.nodes[0]
    assert first.source.sheet == sheet.title and first.source.source_row == 2
    sheet["B2"] = "=1+1"
    workbook.save(xlsx)
    _manifest(source)
    report = inspect_pilot(source)
    assert report.snapshot is None
    assert any(item["code"] == "formula_or_error_cell" for item in report.report["issues"])


def test_real_manifest_needs_external_approval_before_reading_source(source, monkeypatch):
    _manifest(source, lambda doc: doc.update(data_mode="real_authorized", dataset_id="qa-denied-placeholder"))

    def never_read(*args, **kwargs):
        pytest.fail("source was accessed before authorization")

    monkeypatch.setattr("src.gas_application.pilot_intake.read_native", never_read)
    with pytest.raises(IntakeError, match="approval_required"):
        inspect_pilot(source)
    with pytest.raises(IntakeError, match="approval_required"):
        inspect_pilot(source, grants=())


@pytest.fixture
def client(source, store, monkeypatch):
    store.prepare(inspect_pilot(source))
    _manifest(source, lambda doc: doc.update(dataset_id="syn-gas-pilot-other"))
    _edit_row(source, "observations", 3, "value", "99.9")
    store.prepare(inspect_pilot(source))
    monkeypatch.setenv("GAS_PILOT_STORE", str(store.root))
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "pilot-reader": {"actor_id": "pilot-test", "modes": ["synthetic"], "datasets": [DATASET]},
        "no-pilot": {"actor_id": "other-test", "modes": ["synthetic"], "datasets": []},
        "comp": {"actor_id": "competition", "modes": ["synthetic"], "scope": "competition", "datasets": [DATASET]},
        "wrong-mode": {"actor_id": "wrong-mode", "modes": ["real_authorized"], "datasets": [DATASET]},
    }))
    with TestClient(create_gas_app()) as http:
        yield http


def test_history_replay_asset_evidence_and_threshold_intervals(client):
    visible = client.get(BASE, headers=READER)
    assert [entry["dataset_id"] for entry in visible.json()["items"]] == [DATASET]
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers={"X-Water-Token": "comp"}).status_code == 403
    assert client.get(VERSION, headers={"X-Water-Token": "wrong-mode"}).status_code == 403
    params = {"sensor_id": PREF + "pressure-a"}
    assert client.get(VERSION + "/replay", params=params, headers=OTHER).status_code == 403
    replay = client.get(VERSION + "/replay", params=params, headers=READER)
    assert replay.status_code == 200
    assert replay.headers["Cache-Control"] == "no-store"
    report = replay.json()
    assert report["data_mode"] == "synthetic"
    assert [segment["threshold"]["version"] for segment in report["segments"]] == ["v1", "v2"]
    events = report["segments"][0]["channel"]["events"]
    assert [(event["kind"], event["at"][11:16]) for event in events] == [
        ("limit_exceeded", "01:03"), ("recovered", "01:04"), ("data_missing", "01:06"), ("recovered", "01:07"),
    ]
    assert events[0]["evidence"][-1]["value"] == 7.2
    assert report["observations"][8]["classification"] == "normal"
    assert report["observations"][9]["classification"] == "anomaly"
    assert report["observations"][3]["event_ids"] == ["syn-gas-pilot-event-a"]
    pipe = client.get(VERSION + "/pipes/" + PREF + "pipe-a", headers=READER, params={"at": events[0]["at"]})
    assert pipe.json()["geometry_status"] == "available"
    assert pipe.json()["geometries_wgs84"][0]["source"]["table"] == "gis"
    outside = client.get(VERSION + "/replay", params={**params, "start": "2026-11-19T01:00:00Z",
                         "end": "2026-11-19T01:10:00Z"}, headers=READER).json()
    assert outside["observations"] == outside["segments"] == []


def test_qwen_selects_authorized_evidence_and_cannot_read_another_dataset(client, monkeypatch):
    requests = []

    def complete(request):
        facts = json.loads(json.loads(request.content)["messages"][-1]["content"])["evidence_sentences"]
        requests.append(facts)
        assert all("99.9" not in sentence for sentence in facts)
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [
            {"message": {"content": '{"sentence_ids":[2,3]}'}},
        ]})

    model = LocalGasModel("http://127.0.0.1:12345", "synthetic-test-key", transport=httpx.MockTransport(complete))
    monkeypatch.setattr(LocalGasModel, "from_environment", lambda: model)
    payload = {"observation_id": OBSERVATION, "question": "解释阈值和关联事件"}
    denied = client.post(BASE + "/syn-gas-pilot-other/versions/demo-v1/agent/query", json=payload, headers=READER)
    assert denied.status_code == 403 and requests == []
    assert client.post(VERSION + "/agent/query", json=payload, headers=OTHER).status_code == 403
    result = client.post(VERSION + "/agent/query", json=payload, headers=READER).json()
    assert result["parser_source"] == "qwen_evidence_selection"
    assert result["model_calls"] == 1 and len(requests) == 1
    assert "7.2 kPa" in result["answer"] and "synthetic-ticket-001" in result["answer"]
    assert {source["table"] for source in result["evidence"]} == {"sensors", "pipes", "observations", "thresholds", "events"}
    refusal = client.post(VERSION + "/agent/query", json={**payload, "question": "执行阀门控制"}, headers=READER).json()
    assert refusal["status"] == "unavailable" and not refusal["evidence"] and len(requests) == 1
