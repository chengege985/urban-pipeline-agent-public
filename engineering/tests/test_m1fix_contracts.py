"""M1-FIX-B: field-level timeline availability, strict time types and score semantics."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.openapi_export import build_openapi
from src.platform.schemas import SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "m1fix-b"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_API_TOKEN", "m1contract-synthetic-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    monkeypatch.delenv("WATER_PRINCIPALS", raising=False)
    monkeypatch.delenv("WATER_LOCAL_DEMO_MODES", raising=False)
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    with TestClient(app, headers={"X-Water-Token": "m1contract-synthetic-token"}) as instance:
        yield instance


def s01_payload() -> dict:
    return json.loads((ROOT / "data/synthetic/s01.json").read_text(encoding="utf-8"))


def save(client, payload: dict, key: str) -> str:
    # These tests intentionally construct partial/invalid variants of S01 to
    # exercise timeline and dataset contracts. HTTP synthetic imports now
    # accept only the immutable server-registered fixture, so seed variants
    # directly in this isolated test Store and keep the HTTP provenance tests
    # in test_data_provenance.py.
    return client.app.state.store.save_snapshot(
        SnapshotPayload.model_validate(payload), "test-m1fix:" + key
    )["snapshot_id"]


def with_one_score(payload: dict, *, version: str = "0.1.1") -> dict:
    value = deepcopy(payload)
    # Keep this contract test independent from the optional provider metadata
    # asset; use the approved synthetic fixture's own valid assessment row.
    assessment = deepcopy(
        json.loads((ROOT / "data/synthetic/m1a_unified.json").read_text(encoding="utf-8"))["assessments"][0]
    )
    assessment["pipe_id"] = "syn-pipe-feed"
    assessment["as_of"] = value["as_of"]
    assessment["model_version"] = version
    assessment["model_family"] = "legacy_demo"
    value["assessments"] = [assessment]
    return value


def timeline_body(snapshot_id, **updates):
    body = {"snapshot_id": snapshot_id, "fault_edge_id": "syn-fault",
            "isolation_plan_id": "syn-isolate", "plan_id": "syn-plan-combined",
            "target_service_ids": ["syn-hospital-001"], "horizon_hours": 8}
    body.update(updates)
    return body


def test_check_reports_field_level_availability(client):
    snapshot_id = save(client, s01_payload(), "s01")
    body = client.post("/api/v1/timeline/check", json=timeline_body(snapshot_id), headers=HEADERS).json()

    fields = {item["field"]: item for item in body["field_availability"]}
    assert fields["timeline_events"]["status"] == "available"
    assert fields["plan_completion_hours"]["status"] == "missing"
    assert fields["service_first_restored_hours"]["status"] == "available"
    assert fields["first_target_service_restored_hours"]["status"] == "available"
    assert fields["all_target_services_restored_hours"]["status"] == "available"
    assert fields["plan_feasibility"]["status"] == "not_checked"
    assert fields["plan_feasibility"]["in_scope"] is False
    assert body["has_any_calculable_output"] is True
    assert body["all_required_outputs_calculable"] is False
    # Legacy field keeps its original "all requested outputs complete" meaning.
    assert body["result"]["can_compute"] is False


@pytest.mark.parametrize("field,value", [
    ("horizon_hours", True),
    ("horizon_hours", -1),
])
def test_timeline_rejects_non_finite_or_boolean_hours(client, field, value):
    snapshot_id = save(client, s01_payload(), "s01-hours")
    response = client.post("/api/v1/timeline/check", json=timeline_body(snapshot_id, **{field: value}),
                           headers=HEADERS)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail and "field" in detail[0] and "reason" in detail[0]


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
def test_timeline_rejects_non_finite_json_literals(client, literal):
    snapshot_id = save(client, s01_payload(), "s01-literals")
    raw = json.dumps(timeline_body(snapshot_id)).replace('"horizon_hours": 8',
                                                         f'"horizon_hours": {literal}')
    response = client.post("/api/v1/timeline/check", content=raw,
                           headers={**HEADERS, "Content-Type": "application/json"})
    assert response.status_code == 422, response.text
    assert "有限" in response.text or "JSON" in response.text


def test_schedule_rejects_boolean_hours(client):
    snapshot_id = save(client, s01_payload(), "s01-schedule")
    body = timeline_body(snapshot_id, action_schedule=[{"action_id": "syn-repair", "finish_offset_hours": True}])
    response = client.post("/api/v1/timeline/evaluate", json=body, headers=HEADERS)
    assert response.status_code == 422
    assert "布尔" in response.text


def test_timeline_rejects_string_hours_and_naive_as_of(client):
    snapshot_id = save(client, s01_payload(), "s01-types")
    assert client.post("/api/v1/timeline/check",
                       json=timeline_body(snapshot_id, horizon_hours="8"), headers=HEADERS).status_code == 422
    naive = client.post("/api/v1/timeline/check",
                        json=timeline_body(snapshot_id, as_of="2026-10-29T08:00:00"), headers=HEADERS)
    assert naive.status_code == 422
    assert "时区" in naive.text


def test_valid_integer_and_float_hours_are_accepted(client):
    snapshot_id = save(client, s01_payload(), "s01-valid")
    for horizon in (8, 8.5):
        response = client.post("/api/v1/timeline/evaluate",
                               json=timeline_body(snapshot_id, horizon_hours=horizon), headers=HEADERS)
        assert response.status_code == 200, response.text


def test_partial_assessment_coverage_lists_missing_pipes(client):
    snapshot_id = save(client, with_one_score(s01_payload()), "one-score")
    view = client.get(f"/api/v1/datasets/{snapshot_id}/view").json()
    coverage = view["assessment_coverage"]
    assert view["status"] == "partial"
    assert coverage["total_pipes"] == 6
    assert coverage["scored_pipes"] == 1
    assert coverage["coverage_ratio"] == round(1 / 6, 4)
    assert coverage["status"] == "partial"
    assert set(coverage["missing_pipe_ids"]) == {
        "syn-pipe-v1", "syn-pipe-fault", "syn-pipe-hospital", "syn-pipe-residents", "syn-pipe-alternative"}
    assert coverage["incompatible_pipe_ids"] == []
    scored = next(pipe for pipe in view["pipes"] if pipe["pipe_id"] == "syn-pipe-feed")
    assert scored["score"]["semantics"] == "legacy_demo_0.1.1"
    assert scored["score"]["hs"] is not None
    # No score is fabricated for the other five pipes.
    assert all(pipe["score"] is None for pipe in view["pipes"] if pipe["pipe_id"] != "syn-pipe-feed")


def test_incompatible_score_version_is_not_relabelled(client):
    snapshot_id = save(client, with_one_score(s01_payload(), version="demo-score-0.1.0"), "bad-score")
    view = client.get(f"/api/v1/datasets/{snapshot_id}/view").json()
    pipe = next(item for item in view["pipes"] if item["pipe_id"] == "syn-pipe-feed")
    assert pipe["score"] is None
    assert "demo-score-0.1.0" in pipe["score_issue"]
    assert "不兼容" in pipe["score_issue"]
    assert view["assessment_coverage"]["incompatible_pipe_ids"] == ["syn-pipe-feed"]
    assert view["assessment_coverage"]["scored_pipes"] == 0
    algorithms = {item["capability_id"]: item for item in view["algorithms"]}
    assert algorithms["legacy_health"]["available"] is False
    assert "不兼容" in algorithms["legacy_health"]["reason"]


def test_as_of_mismatch_returns_reason_not_silent_recalculation(client):
    snapshot_id = save(client, s01_payload(), "s01-asof")
    response = client.post("/api/v1/timeline/evaluate",
                           json=timeline_body(snapshot_id, as_of="2026-10-29T09:00:00+08:00"),
                           headers=HEADERS)
    assert response.status_code == 422
    assert "时点" in response.json()["detail"]


def test_openapi_declares_security_errors_and_actual_422_shape(client):
    snapshot_id = save(client, s01_payload(), "s01-openapi")
    schema = build_openapi()
    assert "WaterToken" in schema["components"]["securitySchemes"]
    for path in ("/api/v1/timeline/check", "/api/v1/timeline/evaluate",
                 "/api/v1/datasets/{dataset_id}/view", "/api/v1/snapshots/{snapshot_id}"):
        operation = schema["paths"][path]["get" if path.endswith("view") or "snapshot_id" in path else "post"]
        assert operation["security"], path
        assert any("WaterToken" in item for item in operation["security"])
        for code in ("401", "403", "404", "422"):
            assert code in operation["responses"], (path, code)
        ref = operation["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.endswith("ValidationErrorResponse")

    missing = client.post("/api/v1/timeline/check",
                          json={key: value for key, value in timeline_body(snapshot_id).items()
                                if key != "target_service_ids"}, headers=HEADERS)
    assert missing.status_code == 422
    item = missing.json()["detail"][0]
    assert set(item) == {"field", "reason", "type"}
    assert isinstance(item["field"], list)
