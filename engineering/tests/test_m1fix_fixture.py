"""M1-FIX-C: independent synthetic fixture contract and reproducibility."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.adapters.scoring import recompute
from src.platform.app import create_app
from src.platform.schemas import RecomputeRequest, SnapshotPayload
from src.platform.store import fingerprint

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/synthetic/m1a_unified.json"
INPUTS = ROOT / "data/synthetic/m1a_unified_inputs.json"
MANIFEST = ROOT / "data/synthetic/m1a_unified_manifest.json"
HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "m1fix-c"}


def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_fixture_has_six_computed_scores_and_no_provider_rows():
    snapshot = SnapshotPayload.model_validate(fixture_payload())
    assert len(snapshot.assessments) == 6
    assert len({item.pipe_id for item in snapshot.assessments}) == 6
    assert all(item.model_family == "legacy_demo" for item in snapshot.assessments)
    assert all(item.model_version == "0.1.1" for item in snapshot.assessments)
    assert all(item.legacy_result.Hs is not None for item in snapshot.assessments)
    assert all(item.pipe_id.startswith("syn-") for item in snapshot.assessments)
    assert snapshot.data_mode == "synthetic"
    assert snapshot.source_status == "constructed"
    assert snapshot.coordinate_system == "schematic"
    assert all(node.x is not None and node.y is not None for node in snapshot.nodes)


def test_fixture_scores_match_independent_0_1_1_recompute():
    snapshot = SnapshotPayload.model_validate(fixture_payload())
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    request = RecomputeRequest(
        name=inputs["source"], as_of=inputs["as_of"],
        window_start=inputs["window_start"], window_end=inputs["window_end"],
        static_rows=inputs["static_rows"], dynamic_rows=inputs["dynamic_rows"],
        data_mode="synthetic",
        source_refs=["data/synthetic/m1a_unified_inputs.json"],
    )
    computed = recompute(request)
    expected = {item.pipe_id: item.legacy_result.Hs for item in computed.assessments}
    actual = {item.pipe_id: item.legacy_result.Hs for item in snapshot.assessments}
    assert actual == expected
    assert all(value is not None for value in actual.values())


def test_fixture_manifest_and_file_hashes_match():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    snapshot = SnapshotPayload.model_validate(fixture_payload())
    digest = fingerprint(snapshot.model_dump(mode="json"))
    assert manifest["snapshot_digest"] == digest
    assert manifest["snapshot_id"] == "snap-" + digest[:24]
    assert manifest["snapshot_file_sha256"] == hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    assert manifest["inputs_sha256"] == hashlib.sha256(INPUTS.read_bytes()).hexdigest()
    assert manifest["assessment_count"] == 6
    assert manifest["pipe_count"] == 6
    assert manifest["coordinate_system"] == "schematic"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_API_TOKEN", "synthetic-fixture-token")
    monkeypatch.delenv("WATER_PRINCIPALS", raising=False)
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    with TestClient(app, headers={"X-Water-Token": "synthetic-fixture-token"}) as instance:
        yield instance


def test_fixture_view_is_complete_and_keeps_schematic_coordinates(client):
    response = client.post("/api/v1/data-batches", json=fixture_payload(), headers=HEADERS)
    assert response.status_code == 201, response.text
    snapshot_id = response.json()["snapshot_id"]
    view = client.get(f"/api/v1/datasets/{snapshot_id}/view").json()

    assert view["status"] == "succeeded"
    assert len(view["pipes"]) == 6  # explicitly not the React 24-pipe demo
    assert view["assessment_coverage"]["status"] == "complete"
    assert view["assessment_coverage"]["scored_pipes"] == 6
    assert view["assessment_coverage"]["missing_pipe_ids"] == []
    assert all(pipe["score"]["semantics"] == "legacy_demo_0.1.1" for pipe in view["pipes"])
    assert view["meta"]["display_coordinates_available"] is True
    assert view["meta"]["coordinate_system"] == "schematic"
    assert view["missing_items"] == []
    algorithms = {item["capability_id"]: item for item in view["algorithms"]}
    assert algorithms["legacy_health"]["available"] is True

    dual = next(item for item in view["services"] if item["service_id"] == "syn-dual-inlet")
    assert dual["node_ids"] == ["syn-H", "syn-S"] and dual["supply_rule"] == "any"
    valves = {edge["edge_id"] for edge in view["edges"] if edge["is_valve"]}
    assert valves == {"syn-v1", "syn-alternative"}
    assert all(edge["pipe_id"].startswith("syn-pipe-") for edge in view["edges"] if edge["is_valve"])
    isolation = view["isolation_plans"][0]
    assert isolation["valve_ids"] == ["syn-v1"]
    assert "syn-v1" not in {pipe["pipe_id"] for pipe in view["pipes"]}

    timeline = client.post("/api/v1/timeline/evaluate", json={
        "snapshot_id": snapshot_id, "fault_edge_id": "syn-fault",
        "isolation_plan_id": "syn-isolate", "plan_id": "syn-plan-combined",
        "target_service_ids": ["syn-hospital-001"], "horizon_hours": 8,
    }, headers=HEADERS).json()
    assert timeline["result"]["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 6.0
