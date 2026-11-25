"""M1-FIX-A: trusted authorization for every snapshot read path.

All data are synthetic S01 copies relabeled for the test. The denied response
body is checked for content leakage.
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.adapters.scoring import provided_sample
from src.platform.app import create_app
from src.platform.schemas import SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]
POST_HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "m1fix-a"}


def s01_payload() -> dict:
    return json.loads((ROOT / "data/synthetic/s01.json").read_text(encoding="utf-8"))


def relabel(payload: dict, mode: str) -> dict:
    value = deepcopy(payload)
    value["data_mode"] = mode
    value["source_status"] = {"real": "declared_real", "fixture_replay": "source_unknown"}[mode]
    if mode == "fixture_replay":
        # Provider samples must not carry unverified network or services.
        for key in ("nodes", "edges", "services", "isolation_plans", "actions", "plans", "value_profile"):
            value[key] = []
    return value


def save(store, payload: dict, key: str) -> str:
    return store.save_snapshot(SnapshotPayload.model_validate(payload), key)["snapshot_id"]


def make_app(tmp_path, monkeypatch, **env):
    for name in ("WATER_API_TOKEN", "WATER_PRINCIPALS", "WATER_DEV_MODES",
                 "WATER_DEV_DATASETS", "WATER_LOCAL_DEMO_MODES",
                 "ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "WATER_RUNTIME_MODE", "WATER_BIND_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # Existing anonymous synthetic-read tests now use the explicit loopback demo
    # exception; the security default is covered separately.
    if not env:
        monkeypatch.setenv("ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "true")
        monkeypatch.setenv("WATER_RUNTIME_MODE", "local")
        monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")
    return create_app(tmp_path, start_worker=False, bootstrap=False)


@pytest.fixture()
def open_app(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        yield app, client


def timeline_body(snapshot_id):
    return {"snapshot_id": snapshot_id, "fault_edge_id": "syn-fault",
            "isolation_plan_id": "syn-isolate", "plan_id": "syn-plan-combined",
            "target_service_ids": ["syn-hospital-001"], "horizon_hours": 8}


def test_real_snapshot_is_denied_on_every_read_path(open_app):
    app, client = open_app
    store = app.state.store
    synthetic_id = save(store, s01_payload(), "synthetic")
    real_id = save(store, relabel(s01_payload(), "real"), "real")

    assert client.get(f"/api/v1/snapshots/{synthetic_id}").status_code == 200
    for path in (
        f"/api/v1/snapshots/{real_id}",
        f"/api/v1/datasets/{real_id}/view",
        f"/api/v1/assessments?snapshot_id={real_id}",
    ):
        response = client.get(path)
        assert response.status_code == 403, path
        assert "S01" not in response.text and "syn-H" not in response.text and "payload" not in response.text

    listed = client.get("/api/v1/snapshots").json()["items"]
    assert [item["snapshot_id"] for item in listed] == [synthetic_id]
    capabilities = client.get("/api/v1/capabilities").json()
    assert [item["snapshot_id"] for item in capabilities["datasets"]] == [synthetic_id]
    assert capabilities["data_modes"] == ["synthetic"]

    denied = client.post("/api/v1/timeline/check", json=timeline_body(real_id), headers=POST_HEADERS)
    assert denied.status_code == 401
    assert "syn-H" not in denied.text
    scenario = {"snapshot_id": real_id, "as_of": "2026-10-29T08:00:00+08:00",
                "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate", "horizon_hours": 8}
    assert client.post("/api/v1/simulation-runs", json=scenario, headers=POST_HEADERS).status_code == 401


def test_forged_scope_header_cannot_authorize(open_app):
    app, client = open_app
    real_id = save(app.state.store, relabel(s01_payload(), "real"), "real-forged")
    response = client.get(f"/api/v1/snapshots/{real_id}",
                          headers={"X-Water-Data-Scope": "authorized"})
    assert response.status_code == 403
    assert "S01" not in response.text


def test_shared_token_does_not_grant_real_by_default(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch, WATER_API_TOKEN="shared-secret")
    with TestClient(app) as client:
        real_id = save(app.state.store, relabel(s01_payload(), "real"), "real-token")
        synthetic_id = save(app.state.store, s01_payload(), "synthetic-token")

        assert client.get(f"/api/v1/snapshots/{real_id}").status_code == 401
        assert client.get(f"/api/v1/snapshots/{real_id}",
                          headers={"X-Water-Token": "wrong"}).status_code == 401
        assert client.get(f"/api/v1/snapshots/{synthetic_id}",
                          headers={"X-Water-Token": "shared-secret"}).status_code == 200
        denied = client.get(f"/api/v1/snapshots/{real_id}",
                            headers={"X-Water-Token": "shared-secret"})
        assert denied.status_code == 403
        assert "S01" not in denied.text


def test_authenticated_principal_requires_explicit_dataset_scope(tmp_path, monkeypatch):
    app = make_app(tmp_path, monkeypatch,
                   WATER_PRINCIPALS=json.dumps({
                       "tok-other": {"actor_id": "analyst", "modes": ["real"], "datasets": ["snap-other"]},
                       "tok-ok": {"actor_id": "analyst", "modes": ["real"], "datasets": []},
                   }))
    with TestClient(app) as client:
        real_id = save(app.state.store, relabel(s01_payload(), "real"), "real-scope")
        client.app.state.store  # keep a reference for clarity
        # A principal with real mode but a different dataset is denied.
        other = client.get(f"/api/v1/snapshots/{real_id}", headers={"X-Water-Token": "tok-other"})
        assert other.status_code == 403
        # A principal explicitly scoped to this dataset is allowed.
        scoped = {"tok-ok": {"actor_id": "analyst", "modes": ["real"], "datasets": [real_id]}}
        monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(scoped))
        allowed = client.get(f"/api/v1/snapshots/{real_id}", headers={"X-Water-Token": "tok-ok"})
        assert allowed.status_code == 200


def test_run_and_export_are_protected_indirectly(open_app):
    app, client = open_app
    store = app.state.store
    real_id = save(store, relabel(s01_payload(), "real"), "real-run")
    run = store.enqueue("simulation", {
        "snapshot_id": real_id, "as_of": "2026-10-29T08:00:00+08:00",
        "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate", "horizon_hours": 8,
    }, "real-run-key")

    assert client.get(f"/api/v1/runs/{run['run_id']}").status_code == 401
    assert client.get(f"/api/v1/runs/{run['run_id']}/export").status_code == 401
    assert client.post(f"/api/v1/runs/{run['run_id']}/replay",
                       headers=POST_HEADERS).status_code == 401
    assert client.get("/api/v1/runs").status_code == 401
    assert client.get("/api/v1/feedback?run_id=" + run["run_id"]).status_code == 401


def test_fixture_replay_is_denied_by_default_and_can_be_enabled(open_app, monkeypatch):
    app, client = open_app
    sample_id = app.state.store.save_snapshot(provided_sample(), "sample")["snapshot_id"]
    assert client.get(f"/api/v1/snapshots/{sample_id}").status_code == 403

    monkeypatch.setenv("WATER_LOCAL_DEMO_MODES", "synthetic,fixture_replay")
    assert client.get(f"/api/v1/snapshots/{sample_id}").status_code == 403
    monkeypatch.setenv("WATER_API_TOKEN", "fixture-reader")
    monkeypatch.setenv("WATER_DEV_MODES", "fixture_replay")
    assert client.get(f"/api/v1/snapshots/{sample_id}", headers={"X-Water-Token": "fixture-reader"}).status_code == 200
    assert client.get(f"/api/v1/assessments?snapshot_id={sample_id}&limit=1", headers={"X-Water-Token": "fixture-reader"}).status_code == 200


def test_synthetic_data_remains_available_without_auth(open_app):
    app, client = open_app
    snapshot_id = save(app.state.store, s01_payload(), "synthetic-normal")
    assert client.get(f"/api/v1/datasets/{snapshot_id}/view").status_code == 200
    assert client.get(f"/api/v1/snapshots/{snapshot_id}").status_code == 200
    assert client.get(f"/api/v1/assessments?snapshot_id={snapshot_id}").status_code == 200
    assert client.post("/api/v1/timeline/evaluate", json=timeline_body(snapshot_id),
                       headers=POST_HEADERS).status_code == 401


def test_agent_snapshot_gate_uses_platform_principal(open_app, monkeypatch):
    from types import SimpleNamespace
    from starlette.requests import Request
    from src.platform.agent_api import _authorize_run_snapshot

    app, client = open_app
    real_id = save(app.state.store, relabel(s01_payload(), "real"), "agent-real")
    synthetic_id = save(app.state.store, s01_payload(), "agent-synthetic")
    request = Request({"type": "http", "headers": []})
    runtime = SimpleNamespace(snapshots=app.state.store)
    with pytest.raises(Exception):
        _authorize_run_snapshot(request, runtime, real_id)
    with pytest.raises(Exception):
        _authorize_run_snapshot(request, runtime, synthetic_id)
    monkeypatch.setenv("WATER_API_TOKEN", "agent-synthetic-reader")
    request = Request({"type": "http", "headers": [(b"x-water-token", b"agent-synthetic-reader")]})
    _authorize_run_snapshot(request, runtime, synthetic_id)


def test_real_import_requires_authenticated_real_mode(open_app, tmp_path, monkeypatch):
    app, client = open_app
    real_payload = relabel(s01_payload(), "real")
    denied = client.post("/api/v1/data-batches", json=real_payload, headers={
        "X-Water-Client": "local-prototype", "Idempotency-Key": "real-import"})
    assert denied.status_code == 401

    token_app = make_app(tmp_path / "token", monkeypatch,
                         WATER_API_TOKEN="secret", WATER_DEV_MODES="real")
    with TestClient(token_app) as token_client:
        allowed = token_client.post("/api/v1/data-batches", json=real_payload, headers={
            "X-Water-Client": "local-prototype", "Idempotency-Key": "real-import",
            "X-Water-Token": "secret"})
        assert allowed.status_code == 403  # real mode alone is not a dataset grant
        from src.platform.store import fingerprint
        digest = fingerprint(SnapshotPayload.model_validate(real_payload).model_dump(mode="json"))
        monkeypatch.setenv("WATER_DEV_DATASETS", "snap-" + digest[:24])
        scoped = token_client.post("/api/v1/data-batches", json=real_payload, headers={
            "X-Water-Client": "local-prototype", "Idempotency-Key": "real-import",
            "X-Water-Token": "secret"})
        assert scoped.status_code == 201
