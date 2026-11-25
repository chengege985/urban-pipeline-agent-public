from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.schemas import RecomputeRequest

ROOT = Path(__file__).resolve().parents[1]
TRUSTED_REFS = [
    "data/synthetic/s01.json#topology",
    "data/synthetic/m1a_unified_inputs.json#sha256=f2b98b37247f161ccd2f1ecbb4af98cee8717bf55dcd3fdc0962b48d374b4a41",
    "experiments/fixtures/build_m1_unified.py#scoring-recompute-0.1.1",
    "synthetic:not_business_validated",
]


def _clear_env(monkeypatch):
    for name in (
        "WATER_API_TOKEN", "WATER_PRINCIPALS", "WATER_DEV_MODES",
        "WATER_SYNTHETIC_FIXTURE_ROOT", "WATER_SYNTHETIC_FIXTURE_ALLOWED_ROOTS",
        "AGENT_ENABLED", "AGENT_LOCAL_DEMO", "AGENT_DATABASE_URL", "AGENT_VECTOR_DSN",
    ):
        monkeypatch.delenv(name, raising=False)


def _body(source_refs=None):
    raw = json.loads((ROOT / "data/synthetic/m1a_unified_inputs.json").read_text())
    body = {key: raw[key] for key in ("as_of", "window_start", "window_end",
                                      "static_rows", "dynamic_rows")}
    body.update(data_mode="synthetic", source_refs=source_refs or ["external:unverified"])
    return body


def _headers(token="token-a", key="provenance"):
    return {"X-Water-Client": "unified-react", "X-Water-Token": token,
            "Idempotency-Key": key}


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "token-a": {"actor_id": "alice", "scope": "internal", "modes": ["synthetic"]},
        "token-b": {"actor_id": "bob", "scope": "internal", "modes": ["synthetic"]},
    }))
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    with TestClient(app) as client:
        yield app, client


def test_source_label_is_not_authority_and_persisted_request_is_sanitized(app_client):
    app, client = app_client
    response = client.post("/api/v1/assessment-runs",
                           json=_body(["real:external-record-001"]),
                           headers=_headers(key="source-label"))
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    queued = app.state.store.get_task(run_id)
    assert queued["request"]["source_refs"] == TRUSTED_REFS
    assert "real:external-record-001" not in queued["request"]["source_refs"]
    assert app.state.runtime.run_one() is True
    result = app.state.store.get_task(run_id)
    assert result["status"] == "succeeded"
    snapshot = app.state.store.get_snapshot(result["result"]["snapshot_id"])
    assert snapshot["payload"]["source_refs"] == TRUSTED_REFS
    assert snapshot["data_mode"] == "synthetic"


def test_unregistered_content_is_rejected_before_enqueue(app_client):
    app, client = app_client
    body = _body(["fixture:m1a-unified-1.0.0"])
    body["static_rows"][0]["管径"] = body["static_rows"][0]["管径"] + 1
    response = client.post("/api/v1/assessment-runs", json=body,
                           headers=_headers(key="changed-content"))
    assert response.status_code == 403
    assert app.state.store.tasks(100) == []


def test_synthetic_snapshot_import_is_server_verified(app_client):
    app, client = app_client
    payload = json.loads((ROOT / "data/synthetic/m1a_unified.json").read_text())
    payload["source_refs"] = ["real:forged-label"]
    response = client.post("/api/v1/data-batches", json=payload,
                           headers={**_headers(key="import-good"), "X-Water-Client": "unified-react"})
    assert response.status_code == 201, response.text
    stored = app.state.store.get_snapshot(response.json()["snapshot_id"])
    assert stored["payload"]["source_refs"] == TRUSTED_REFS
    payload["name"] = "changed fixture"
    denied = client.post("/api/v1/data-batches", json=payload,
                         headers={**_headers(key="import-bad"), "X-Water-Client": "unified-react"})
    assert denied.status_code == 403


def test_fixture_bytes_change_is_unavailable_and_does_not_enqueue(tmp_path, monkeypatch):
    _clear_env(monkeypatch)
    source = ROOT / "data/synthetic"
    fixture_root = tmp_path / "synthetic"
    shutil.copytree(source, fixture_root)
    inputs = fixture_root / "m1a_unified_inputs.json"
    inputs.write_text(inputs.read_text() + "\n", encoding="utf-8")
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "token-a": {"actor_id": "alice", "scope": "internal", "modes": ["synthetic"]},
    }))
    monkeypatch.setenv("WATER_SYNTHETIC_FIXTURE_ROOT", str(fixture_root))
    app = create_app(tmp_path / "state", start_worker=False, bootstrap=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/assessment-runs", json=_body(),
                               headers=_headers(key="mutated-fixture"))
    assert response.status_code == 503
    assert app.state.store.tasks(100) == []


def test_private_batch_reference_keeps_cross_subject_acl(tmp_path):
    raw = json.loads((ROOT / "data/synthetic/m1a_unified_inputs.json").read_text())
    request = RecomputeRequest(
        as_of=raw["as_of"], window_start=raw["window_start"], window_end=raw["window_end"],
        static_rows=raw["static_rows"], dynamic_rows=raw["dynamic_rows"],
        data_mode="synthetic", source_refs=["fixture:test"],
    )
    repo = InputBatchRepository(tmp_path)
    alice = BatchPrincipal(actor_id="alice", scope="internal")
    bob = BatchPrincipal(actor_id="bob", scope="internal")
    reference = repo.register(request, alice)
    with pytest.raises(KeyError):
        repo.load(reference.batch_ref, bob)
