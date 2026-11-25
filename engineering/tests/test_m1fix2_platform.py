"""M1-FIX2 platform security and typed-contract regression.

All data are synthetic; runs execute in-process, no worker, no model, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import CLIENT_IDS, create_app
from src.unified.contracts import ReportDocument

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/synthetic/m1a_unified.json"
PIPE = "syn-pipe-fault"
FAULT = "syn-fault"
ISOLATION = "syn-isolate"
BASE_HEADERS = {"X-Water-Client": "unified-react"}


def fixture_payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def import_fixture(client: TestClient, token: str | None = None) -> str:
    headers = {**BASE_HEADERS, "Idempotency-Key": "m1fix2-import"}
    if token:
        headers["X-Water-Token"] = token
    response = client.post("/api/v1/data-batches", json=fixture_payload(), headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["snapshot_id"]


def scenario(snapshot_id: str, as_of: str) -> dict:
    return {"snapshot_id": snapshot_id, "as_of": as_of, "fault_edge_id": FAULT,
            "isolation_plan_id": ISOLATION, "horizon_hours": 8}


def make_app(tmp_path: Path, monkeypatch, **environment):
    for name in ("WATER_API_TOKEN", "WATER_PRINCIPALS", "WATER_DEV_MODES", "WATER_DEV_DATASETS",
                 "AGENT_ENABLED", "AGENT_LOCAL_DEMO"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    if not environment:
        monkeypatch.setenv("WATER_API_TOKEN", "m1fix2-synthetic-token")
    return create_app(tmp_path, start_worker=False, bootstrap=False)


def run_sync(client: TestClient, app, body: dict, token: str | None = None) -> dict:
    headers = {**BASE_HEADERS, "Idempotency-Key": "m1fix2-run"}
    if token:
        headers["X-Water-Token"] = token
    accepted = client.post("/api/v1/simulation-runs", json=body, headers=headers)
    assert accepted.status_code == 202, accepted.text
    app.state.runtime.run_one()
    return client.get(f"/api/v1/runs/{accepted.json()['run_id']}",
                      headers={"X-Water-Token": token} if token else {}).json()


def test_controlled_client_ids_are_markers_not_credentials(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Water-Token": "m1fix2-synthetic-token"}) as client:
        snapshot_id = import_fixture(client)
        body = {"snapshot_id": snapshot_id, "fault_edge_id": FAULT, "isolation_plan_id": ISOLATION,
                "plan_id": "syn-plan-combined", "target_service_ids": ["syn-hospital-001"], "horizon_hours": 8}
        allowed = client.post("/api/v1/timeline/check", json=body,
                              headers={**BASE_HEADERS, "Idempotency-Key": "client-ok"})
        assert allowed.status_code == 200, allowed.text
        denied = client.post("/api/v1/timeline/check", json=body,
                             headers={"X-Water-Client": "untrusted", "Idempotency-Key": "client-bad"})
        assert denied.status_code == 403
        assert CLIENT_IDS == frozenset({"local-prototype", "unified-react"})

    token_app = make_app(tmp_path / "token", monkeypatch, WATER_API_TOKEN="token-only")
    with TestClient(token_app, base_url="http://127.0.0.1:8000") as client:
        snapshot_id = import_fixture(client, token="token-only")
        body = {"snapshot_id": snapshot_id, "fault_edge_id": FAULT, "isolation_plan_id": ISOLATION,
                "plan_id": "syn-plan-combined", "target_service_ids": ["syn-hospital-001"], "horizon_hours": 8}
        assert client.post("/api/v1/timeline/check", json=body,
                           headers={**BASE_HEADERS, "Idempotency-Key": "no-token"}).status_code == 401
        assert client.post("/api/v1/timeline/check", json=body,
                           headers={**BASE_HEADERS, "X-Water-Token": "token-only",
                                    "Idempotency-Key": "with-token"}).status_code == 200
        assert client.post("/api/v1/timeline/check", json=body,
                           headers={**BASE_HEADERS, "X-Water-Token": "token-only",
                                    "Origin": "https://evil.example", "Idempotency-Key": "cross"}).status_code == 403
        assert TestClient(token_app, base_url="http://evil.example").get("/health").status_code == 400


def test_report_route_projects_completed_run_and_requires_run_access(tmp_path, monkeypatch):
    principals = json.dumps({
        "tok-a": {"actor_id": "principal-a", "modes": ["synthetic"]},
        "tok-b": {"actor_id": "principal-b", "modes": ["synthetic"]},
    })
    app = make_app(tmp_path, monkeypatch, WATER_PRINCIPALS=principals)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        snapshot_id = import_fixture(client, token="tok-a")
        run = run_sync(client, app, scenario(snapshot_id, fixture_payload()["as_of"]), token="tok-a")
        report = client.get(f"/api/v1/runs/{run['run_id']}/report", headers={"X-Water-Token": "tok-a"})
        assert report.status_code == 200, report.text
        document = ReportDocument.model_validate(report.json())
        assert document.run_id == run["run_id"]
        assert document.recomputation == "none"
        assert document.dataset["snapshot_id"] == snapshot_id
        assert document.run_status == run["status"]
        assert f"run:{run['run_id']}" in document.evidence_ids

        assert client.get(f"/api/v1/runs/{run['run_id']}/report").status_code == 401
        assert client.get(f"/api/v1/runs/{run['run_id']}/report",
                          headers={"X-Water-Token": "tok-b"}).status_code == 403

        queued_body = {**scenario(snapshot_id, fixture_payload()["as_of"])}
        queued = client.post("/api/v1/simulation-runs", json=queued_body,
                             headers={"X-Water-Client": "unified-react", "X-Water-Token": "tok-a",
                                      "Idempotency-Key": "queued-report"})
        assert queued.status_code == 202
        pending = client.get(f"/api/v1/runs/{queued.json()['run_id']}/report",
                             headers={"X-Water-Token": "tok-a"})
        assert pending.status_code == 409
        assert "尚未结束" in pending.text


def test_typed_run_surface_and_report_openapi(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1:8000", headers={"X-Water-Token": "m1fix2-synthetic-token"}) as client:
        snapshot_id = import_fixture(client)
        run = run_sync(client, app, scenario(snapshot_id, fixture_payload()["as_of"]))
        listed = client.get("/api/v1/runs")
        assert listed.status_code == 200
        assert listed.json()["items"][0]["run_id"] == run["run_id"]
        schema = app.openapi()
        report = schema["paths"]["/api/v1/runs/{run_id}/report"]["get"]
        assert "#/components/schemas/ReportDocument" in json.dumps(report["responses"])
        create = schema["paths"]["/api/v1/simulation-runs"]["post"]
        assert "#/components/schemas/RunView" in json.dumps(create["responses"])
        agent_health = schema["paths"]["/api/v1/agent/health"]["get"]
        assert agent_health["security"] == [{"WaterToken": []}]


def test_run_owner_blocks_cross_principal_run_feedback_and_replay(tmp_path, monkeypatch):
    principals = json.dumps({
        "tok-a": {"actor_id": "principal-a", "modes": ["synthetic"]},
        "tok-b": {"actor_id": "principal-b", "modes": ["synthetic"]},
    })
    app = make_app(tmp_path, monkeypatch, WATER_PRINCIPALS=principals)
    with TestClient(app, base_url="http://127.0.0.1:8000") as client:
        snapshot_id = import_fixture(client, token="tok-a")
        body = {**scenario(snapshot_id, fixture_payload()["as_of"]),
                "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2},
                "mandatory_action_ids": []}
        accepted = client.post("/api/v1/decision-runs", json=body,
                               headers={"X-Water-Client": "unified-react", "X-Water-Token": "tok-a",
                                        "Idempotency-Key": "owned-decision"})
        assert accepted.status_code == 202
        app.state.runtime.run_one()
        run_id = accepted.json()["run_id"]
        run = client.get(f"/api/v1/runs/{run_id}", headers={"X-Water-Token": "tok-a"})
        assert run.status_code == 200 and run.json()["review_status"] == "pending_review"
        assert client.get(f"/api/v1/runs/{run_id}", headers={"X-Water-Token": "tok-b"}).status_code == 403
        assert client.get("/api/v1/runs", headers={"X-Water-Token": "tok-b"}).json()["items"] == []
        assert client.post(f"/api/v1/runs/{run_id}/replay",
                           headers={"X-Water-Client": "unified-react", "X-Water-Token": "tok-b",
                                    "Idempotency-Key": "cross-replay"}).status_code == 403
        feedback = {"run_id": run_id, "review_status": "accepted", "reviewer": "m1fix2",
                    "reason_type": "explanation", "reason": "同一 Run 核对", "expected_revision": 0}
        assert client.post("/api/v1/feedback", json=feedback,
                           headers={"X-Water-Client": "unified-react", "X-Water-Token": "tok-b",
                                    "Idempotency-Key": "cross-feedback"}).status_code == 403
        assert client.post("/api/v1/feedback", json=feedback,
                           headers={"X-Water-Client": "unified-react", "X-Water-Token": "tok-a",
                                    "Idempotency-Key": "owner-feedback"}).status_code == 201
        assert client.get(f"/api/v1/runs/{run_id}/report",
                          headers={"X-Water-Token": "tok-a"}).json()["feedback"][0]["actor_identity"] == "principal-a"
        replay = client.post(f"/api/v1/runs/{run_id}/replay",
                             headers={"X-Water-Client": "unified-react", "X-Water-Token": "tok-a",
                                      "Idempotency-Key": "owner-replay"})
        assert replay.status_code == 202
        assert replay.json()["run_id"] != run_id


def test_legacy_local_task_rows_get_an_explicit_local_demo_owner(tmp_path):
    """Old databases without owner columns migrate without dropping data."""
    import sqlite3
    from src.platform.store import Store

    db_dir = tmp_path / "experiments/platform"
    db_dir.mkdir(parents=True)
    connection = sqlite3.connect(db_dir / "platform.sqlite3")
    connection.execute("""CREATE TABLE tasks (
        id VARCHAR PRIMARY KEY, key VARCHAR UNIQUE, digest VARCHAR, kind VARCHAR,
        status VARCHAR, created_at VARCHAR, finished_at VARCHAR, request JSON,
        result JSON, review_status VARCHAR, review_revision INTEGER)""")
    connection.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       ("run-legacy", "simulation:legacy", "d" * 64, "simulation", "succeeded",
                        "2026-01-01T00:00:00+00:00", "2026-01-01T00:01:00+00:00",
                        json.dumps({"snapshot_id": "snap-legacy"}), json.dumps({"status": "succeeded"}),
                        None, 0))
    connection.commit(); connection.close()

    store = Store(tmp_path)
    assert store.task_owner("run-legacy") == ("local-demo", "internal")
    assert store.get_task("run-legacy")["run_id"] == "run-legacy"
    store.engine.dispose()
