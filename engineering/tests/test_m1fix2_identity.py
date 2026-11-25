"""Backend identities, ACLs and asynchronous scoring. Synthetic TestClient only."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.schemas import SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]


def principals(**changes):
    result = {
        "token-a": {"actor_id": "alice", "scope": "internal", "modes": ["synthetic"]},
        "token-b": {"actor_id": "bob", "scope": "internal", "modes": ["synthetic"]},
    }
    result.update(changes)
    return result


def headers(token="token-a", key="test"):
    return {"X-Water-Client": "unified-react", "X-Water-Token": token, "Idempotency-Key": key}


def recompute(mode="synthetic"):
    inputs = json.loads((ROOT / "data/synthetic/m1a_unified_inputs.json").read_text())
    return {**{k: inputs[k] for k in ("as_of", "window_start", "window_end", "static_rows", "dynamic_rows")},
            "data_mode": mode, "source_refs": ["synthetic:m1fix2"]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    for key in ("WATER_API_TOKEN", "WATER_DEV_MODES", "WATER_DEV_DATASETS", "WATER_LOCAL_DEMO_MODES",
                "AGENT_ENABLED", "AGENT_LOCAL_DEMO", "AGENT_DATABASE_URL", "AGENT_VECTOR_DSN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("WATER_LLM_MODE", "mock")
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals()))
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    payload = json.loads((ROOT / "data/synthetic/m1a_unified.json").read_text())
    sid = app.state.store.save_snapshot(SnapshotPayload.model_validate(payload), "seed")["snapshot_id"]
    with TestClient(app) as client:
        yield app, client, sid, payload


def test_identity_is_required_for_all_writes_and_private_reads(setup, monkeypatch):
    app, client, sid, payload = setup
    monkeypatch.delenv("WATER_PRINCIPALS")
    assert client.get(f"/api/v1/snapshots/{sid}").status_code == 401
    assert client.get("/api/v1/runs").status_code == 401
    assert client.get("/api/v1/feedback").status_code == 401
    assert client.post("/api/v1/data-batches", json=payload, headers=headers()).status_code == 401
    assert client.post("/api/v1/assessment-runs", json=recompute(), headers=headers()).status_code == 401


@pytest.mark.parametrize("scope", [None, [], "*", ["snap-other"]])
def test_real_requires_explicit_matching_dataset(setup, monkeypatch, scope):
    app, client, _, payload = setup
    payload.update(data_mode="real", source_status="declared_real")
    sid = app.state.store.save_snapshot(SnapshotPayload.model_validate(payload), "synthetic-as-real")["snapshot_id"]
    entry = {"actor_id": "alice", "modes": ["real"]}
    if scope is not None:
        entry["datasets"] = scope
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"token-a": entry}))
    assert client.get(f"/api/v1/snapshots/{sid}", headers=headers()).status_code == 403
    assert client.post("/api/v1/data-batches", json=payload, headers=headers()).status_code == 403
    assert client.post("/api/v1/assessment-runs", json=recompute("real"), headers=headers()).status_code == 403


def test_real_with_current_exact_dataset_is_readable(setup, monkeypatch):
    app, client, _, payload = setup
    payload.update(data_mode="real", source_status="declared_real")
    sid = app.state.store.save_snapshot(SnapshotPayload.model_validate(payload), "synthetic-as-real")["snapshot_id"]
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"token-a": {
        "actor_id": "alice", "modes": ["real"], "datasets": [sid]}}))
    assert client.get(f"/api/v1/snapshots/{sid}", headers=headers()).status_code == 200


def test_assessment_poll_result_and_owner_grant(setup, monkeypatch):
    app, client, _, _ = setup
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals(**{
        "token-a": {"actor_id": "alice", "modes": ["synthetic"], "datasets": []},
        "token-b": {"actor_id": "bob", "modes": ["synthetic"], "datasets": []}})))
    response = client.post("/api/v1/assessment-runs", json=recompute(), headers=headers())
    assert response.status_code == 202, response.text
    rid = response.json()["run_id"]
    poll = client.get(f"/api/v1/runs/{rid}", headers=headers())
    assert poll.status_code == 200 and poll.json()["status"] == "queued"
    assert client.get(f"/api/v1/runs/{rid}", headers=headers("token-b")).status_code == 403
    assert app.state.runtime.run_one()
    result = client.get(f"/api/v1/runs/{rid}", headers=headers())
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "succeeded", result.text
    sid = result.json()["result"]["snapshot_id"]
    assert client.get(f"/api/v1/snapshots/{sid}", headers=headers()).status_code == 200
    assert client.get(f"/api/v1/snapshots/{sid}", headers=headers("token-b")).status_code == 403
    report = client.get(f"/api/v1/runs/{rid}/report", headers=headers())
    assert report.status_code == 200, report.text
    assert report.json()["dataset"]["snapshot_id"] == sid
    assert report.json()["recomputation"] == "none"


def test_queued_run_rechecks_current_permission_before_algorithm(setup, monkeypatch):
    app, client, _, _ = setup
    created = client.post("/api/v1/assessment-runs", json=recompute(), headers=headers()).json()
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals(**{
        "token-a": {"actor_id": "alice", "modes": ["synthetic"], "permissions": []}})))
    calls = []
    monkeypatch.setattr("src.platform.services.calculate_assessment", lambda _: calls.append(1))
    assert app.state.runtime.run_one()
    result = app.state.store.get_task(created["run_id"])
    assert calls == []
    assert result["status"] == "failed"


def test_current_dataset_revocation_stops_queued_simulation(setup, monkeypatch):
    app, client, sid, payload = setup
    body = {"snapshot_id": sid, "as_of": payload["as_of"], "fault_edge_id": "syn-fault",
            "isolation_plan_id": "syn-isolate", "horizon_hours": 8}
    created = client.post("/api/v1/simulation-runs", json=body, headers=headers()).json()
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals(**{
        "token-a": {"actor_id": "alice", "modes": ["synthetic"], "datasets": []}})))
    calls = []
    monkeypatch.setattr("src.platform.services.simulate_snapshot", lambda *_: calls.append(1))
    assert app.state.runtime.run_one()
    assert app.state.store.get_task(created["run_id"])["status"] == "failed"
    assert calls == []


def test_viewer_and_forged_client_identity_cannot_create_run(setup, monkeypatch):
    _, client, _, _ = setup
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"token-a": {
        "actor_id": "alice", "modes": ["synthetic"], "permissions": []}}))
    forged = {**headers(), "X-Agent-Actor": "administrator", "X-Water-Data-Scope": "authorized"}
    assert client.post("/api/v1/assessment-runs", json=recompute(), headers=forged).status_code == 403
    assert client.post("/api/v1/assessment-runs", json=recompute(), headers=headers("fake")).status_code == 401


def test_configured_principals_take_precedence_over_shared_developer_token(setup, monkeypatch):
    _, client, _, _ = setup
    monkeypatch.setenv("WATER_API_TOKEN", "legacy-token")
    assert client.get("/api/v1/capabilities", headers=headers()).status_code == 200
    assert client.get("/api/v1/capabilities", headers=headers("legacy-token")).status_code == 401


def test_protected_openapi_does_not_advertise_anonymous_access(setup):
    app, _, _, _ = setup
    schema = app.openapi()
    for path in ("/api/v1/runs", "/api/v1/runs/{run_id}", "/api/v1/runs/{run_id}/report",
                 "/api/v1/feedback", "/api/v1/assessment-runs"):
        for method in ("get", "post"):
            operation = schema["paths"][path].get(method)
            if operation:
                assert operation.get("security") == [{"WaterToken": []}], path
    operation = schema["paths"]["/api/v1/assessment-runs"]["post"]
    marker = next(p for p in operation["parameters"] if p["name"] == "X-Water-Client")
    assert set(marker["schema"]["enum"]) == {"unified-react", "local-prototype"}


def test_historical_local_demo_owner_is_not_an_authenticated_grant(setup, monkeypatch):
    app, client, sid, payload = setup
    old = app.state.store.enqueue('simulation', {'snapshot_id': sid}, 'historical')
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps({'token-a': {
        'actor_id': 'local-demo', 'modes': ['synthetic']}}))
    assert client.get(f"/api/v1/runs/{old['run_id']}", headers=headers()).status_code == 403


def test_legacy_assistant_without_corpus_does_not_open_materials(setup, monkeypatch):
    app, client, sid, _ = setup
    opened = []

    class Search:
        def __init__(self, root):
            opened.append(str(root))
        def search(self, query):
            return [{"evidence_id": "doc:synthetic", "text": "合成资料标记"}]

    monkeypatch.setattr("src.agents.materials.MaterialSearch", Search)
    created = client.post("/api/v1/assistant/tasks", json={"snapshot_id": sid, "question": "查找资料"},
                          headers=headers()).json()
    app.state.runtime.run_one()
    result = app.state.store.get_task(created["run_id"])["result"]
    assert opened == []
    assert result.get("citations", []) == []


def test_legacy_assistant_cannot_route_to_live_gateway(setup, monkeypatch):
    app, client, sid, _ = setup
    calls = []
    app.state.runtime.gateway.mode = "openai"
    monkeypatch.setattr(app.state.runtime.gateway, "classify", lambda *_: calls.append(1))
    created = client.post("/api/v1/assistant/tasks", json={"snapshot_id": sid, "question": "查找资料"},
                          headers=headers()).json()
    app.state.runtime.run_one()
    assert app.state.store.get_task(created["run_id"])["status"] == "failed"
    assert calls == []


def test_legacy_materials_require_explicit_root_and_recheck_history_acl(setup, monkeypatch, tmp_path):
    app, client, sid, _ = setup
    corpus_principal = {"actor_id": "alice", "modes": ["synthetic"], "corpora": ["legacy-materials"]}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"token-a": corpus_principal}))
    monkeypatch.delenv("WATER_LEGACY_MATERIALS_ROOT", raising=False)
    opened = []
    materials = tmp_path / "synthetic-materials"
    materials.mkdir()
    (materials / "readme.md").write_text("完全合成的供水案例资料", encoding="utf-8")

    class Search:
        def __init__(self, root):
            opened.append(str(root))
            self.text = (root / "readme.md").read_text(encoding="utf-8")
        def search(self, query):
            return [{"evidence_id": "doc:synthetic", "text": self.text}]

    monkeypatch.setattr("src.agents.materials.MaterialSearch", Search)
    body = {"snapshot_id": sid, "question": "查找资料"}
    no_root = client.post("/api/v1/assistant/tasks", json=body, headers=headers(key="no-root")).json()
    app.state.runtime.run_one()
    assert opened == []
    assert app.state.store.get_task(no_root["run_id"])["result"].get("citations", []) == []
    monkeypatch.setenv("WATER_LEGACY_MATERIALS_ROOT", str(materials))
    created = client.post("/api/v1/assistant/tasks", json=body, headers=headers(key="with-root")).json()
    app.state.runtime.run_one()
    response = client.get(f"/api/v1/runs/{created['run_id']}", headers=headers())
    assert response.status_code == 200
    assert opened == [str(materials)]
    assert response.json()["result"]["required_corpora"] == ["legacy-materials"]
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"token-a": {**corpus_principal, "corpora": []}}))
    assert client.get(f"/api/v1/runs/{created['run_id']}", headers=headers()).status_code == 403
    assert client.get(f"/api/v1/runs/{created['run_id']}/export", headers=headers()).status_code == 403


def test_owner_without_review_permission_cannot_approve(setup, monkeypatch):
    app, client, sid, payload = setup
    decision = {"snapshot_id": sid, "as_of": payload["as_of"], "fault_edge_id": "syn-fault",
                "isolation_plan_id": "syn-isolate", "horizon_hours": 8,
                "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2}}
    created = client.post("/api/v1/decision-runs", json=decision, headers=headers()).json()
    app.state.runtime.run_one()
    rid = created["run_id"]
    assert app.state.store.get_task(rid)["review_status"] == "pending_review"
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"token-a": {
        "actor_id": "alice", "modes": ["synthetic"], "permissions": ["run"]}}))
    feedback = {"run_id": rid, "review_status": "accepted", "reviewer": "administrator",
                "reason_type": "other", "reason": "合成审批反例", "expected_revision": 0}
    result = client.post("/api/v1/feedback", json=feedback, headers=headers())
    assert result.status_code == 403
    assert app.state.store.get_task(rid)["review_revision"] == 0
    assert app.state.store.feedbacks(rid) == []
