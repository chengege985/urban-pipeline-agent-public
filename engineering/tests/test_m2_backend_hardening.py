from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from src.gas_application.adapter import evaluate_fixture, fixture_root, FixtureUnavailable
from src.platform.authz import Principal
from src.platform.app import create_app
from src.platform.dataset_api import build_dataset_view
from src.platform.provenance import load_trusted_synthetic_fixture
from src.platform.schemas import FeedbackRequest, SnapshotPayload
from src.platform.store import TaskRow

ROOT = Path(__file__).resolve().parents[1]


def _principal(actor_id: str) -> Principal:
    return Principal(
        actor_id=actor_id,
        authenticated=True,
        modes=frozenset({"synthetic"}),
        scope="internal",
    )


def _request_body() -> dict:
    return {
        "data_mode": "synthetic",
        "source_refs": ["synthetic:test"],
    }


def _seed_run(app, principal: Principal, key: str) -> str:
    return app.state.store.enqueue("assessment", _request_body(), key, principal)["run_id"]


def _make_feedback(app, principal: Principal, run_id: str, key: str) -> None:
    with app.state.store.session.begin() as db:
        row = db.get(TaskRow, run_id)
        row.status = "succeeded"
        row.review_status = "pending_review"
        row.review_revision = 0
    app.state.store.feedback(
        FeedbackRequest(
            run_id=run_id,
            review_status="accepted",
            reviewer=principal.actor_id,
            reason_type="fact",
            reason="synthetic pagination test",
            expected_revision=0,
        ),
        key,
        principal,
    )


def test_runs_filter_owner_before_limit(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "WATER_PRINCIPALS",
        json.dumps({
            "alice-token": {"actor_id": "alice", "scope": "internal", "modes": ["synthetic"]},
            "bob-token": {"actor_id": "bob", "scope": "internal", "modes": ["synthetic"]},
        }),
    )
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    alice = _principal("alice")
    bob = _principal("bob")
    with TestClient(app) as client:
        foreign = [_seed_run(app, bob, f"bob-{i}") for i in range(3)]
        time.sleep(0.01)
        own = _seed_run(app, alice, "alice-newest")
        response = client.get("/api/v1/runs?limit=1", headers={"X-Water-Token": "alice-token"})
    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == [own]
    assert all(item["run_id"] not in foreign for item in response.json()["items"])


def test_feedback_filter_owner_before_limit(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "WATER_PRINCIPALS",
        json.dumps({
            "alice-token": {"actor_id": "alice", "scope": "internal", "modes": ["synthetic"]},
            "bob-token": {"actor_id": "bob", "scope": "internal", "modes": ["synthetic"]},
        }),
    )
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    alice = _principal("alice")
    bob = _principal("bob")
    with TestClient(app) as client:
        for i in range(3):
            run_id = _seed_run(app, bob, f"bob-feedback-{i}")
            _make_feedback(app, bob, run_id, f"bob-feedback-key-{i}")
        time.sleep(0.01)
        own_run = _seed_run(app, alice, "alice-feedback")
        _make_feedback(app, alice, own_run, "alice-feedback-key")
        response = client.get(
            "/api/v1/feedback?limit=1",
            headers={"X-Water-Token": "alice-token"},
        )
    assert response.status_code == 200
    assert [item["run_id"] for item in response.json()["items"]] == [own_run]


def test_dataset_missing_topology_or_plan_is_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_API_TOKEN", "synthetic-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    payload = json.loads((ROOT / "data/synthetic/s01.json").read_text(encoding="utf-8"))
    payload["edges"] = []
    payload["actions"] = []
    payload["plans"] = []
    payload["isolation_plans"] = []
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    stored_id = app.state.store.save_snapshot(
        SnapshotPayload.model_validate(payload), "m2-partial-dataset"
    )["snapshot_id"]
    view = build_dataset_view(app.state.store.get_snapshot(stored_id))
    assert view.status == "partial"
    assert "该数据集没有完整网络拓扑" in view.missing_items
    assert "该数据集没有候选方案或措施" in view.missing_items
    algorithms = {item.capability_id: item for item in view.algorithms}
    assert algorithms["given_isolation_topology"].available is False
    assert algorithms["candidate_plan_ranking"].available is False


def test_gas_fixture_root_requires_server_allowlist(tmp_path, monkeypatch):
    source = fixture_root()
    isolated = tmp_path / "gas"
    isolated.mkdir()
    for name in ("gas54_network.json", "gas54_case.json"):
        shutil.copy2(source / name, isolated / name)
    monkeypatch.setenv("WATER_GAS_FIXTURE_ROOT", str(isolated))
    monkeypatch.delenv("WATER_GAS_FIXTURE_ALLOWED_ROOTS", raising=False)
    with pytest.raises(FixtureUnavailable):
        fixture_root()
    monkeypatch.setenv("WATER_GAS_FIXTURE_ALLOWED_ROOTS", str(isolated))
    assert evaluate_fixture()["counts"]["nodes"] == 54


def test_gas_corrupt_fixture_is_sanitized_503(tmp_path, monkeypatch):
    source = fixture_root()
    isolated = tmp_path / "gas-corrupt"
    isolated.mkdir()
    shutil.copy2(source / "gas54_network.json", isolated / "gas54_network.json")
    (isolated / "gas54_case.json").write_text("{broken", encoding="utf-8")
    monkeypatch.setenv("WATER_GAS_FIXTURE_ROOT", str(isolated))
    monkeypatch.setenv("WATER_GAS_FIXTURE_ALLOWED_ROOTS", str(isolated))
    monkeypatch.setenv("WATER_API_TOKEN", "gas-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    monkeypatch.setenv("WATER_DEV_DATASETS", "syn-gas-network-html54-0.1.0")
    app = create_app(tmp_path / "state", start_worker=False, bootstrap=False)
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/gas/cases/syn-gas-case-K-001",
            headers={"X-Water-Token": "gas-token"},
        )
    assert response.status_code == 503
    assert str(isolated) not in response.text
    assert "traceback" not in response.text.lower()


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(lambda network: network["nodes"][0].__setitem__("name", 123), id="wrong-field-type"),
        pytest.param(lambda network: network["nodes"][0].pop("node_id"), id="missing-required-field"),
        pytest.param(lambda network: network["nodes"][0]["layout"].__setitem__("x", {"bad": True}), id="nested-object-type"),
        pytest.param(lambda network: network["edges"].__setitem__(0, ["not-an-object"]), id="array-element-type"),
    ],
)
def test_gas_structural_fixture_errors_are_503_for_list_and_detail(tmp_path, monkeypatch, mutation):
    source = fixture_root()
    isolated = tmp_path / "gas-structural"
    isolated.mkdir()
    for name in ("gas54_network.json", "gas54_case.json"):
        shutil.copy2(source / name, isolated / name)
    network_path = isolated / "gas54_network.json"
    network = json.loads(network_path.read_text(encoding="utf-8"))
    mutation(network)
    network_path.write_text(json.dumps(network, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("WATER_GAS_FIXTURE_ROOT", str(isolated))
    monkeypatch.setenv("WATER_GAS_FIXTURE_ALLOWED_ROOTS", str(isolated))
    monkeypatch.setenv("WATER_API_TOKEN", "gas-structure-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    monkeypatch.setenv("WATER_DEV_DATASETS", "syn-gas-network-html54-0.1.0")
    app = create_app(tmp_path / "state", start_worker=False, bootstrap=False)
    headers = {"X-Water-Token": "gas-structure-token"}
    with TestClient(app, raise_server_exceptions=False) as client:
        detail = client.get("/api/v1/gas/cases/syn-gas-case-K-001", headers=headers)
        listing = client.get("/api/v1/gas/cases", headers=headers)
    for response in (detail, listing):
        assert response.status_code == 503
        assert str(isolated) not in response.text
        assert "traceback" not in response.text.lower()


def test_valid_gas_case_detail_and_list_keep_54_nodes_73_edges(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_API_TOKEN", "gas-valid-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    monkeypatch.setenv("WATER_DEV_DATASETS", "syn-gas-network-html54-0.1.0")
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    headers = {"X-Water-Token": "gas-valid-token"}
    with TestClient(app) as client:
        detail = client.get("/api/v1/gas/cases/syn-gas-case-K-001", headers=headers)
        listing = client.get("/api/v1/gas/cases", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["counts"]["nodes"] == 54
    assert detail.json()["counts"]["edges"] == 73
    assert listing.status_code == 200
    assert listing.json()["items"][0]["counts"]["nodes"] == 54
    assert listing.json()["items"][0]["counts"]["edges"] == 73


def test_synthetic_fixture_root_requires_server_allowlist(tmp_path, monkeypatch):
    source = ROOT / "data/synthetic"
    isolated = tmp_path / "synthetic"
    shutil.copytree(source, isolated)
    monkeypatch.setenv("WATER_SYNTHETIC_FIXTURE_ROOT", str(isolated))
    monkeypatch.delenv("WATER_SYNTHETIC_FIXTURE_ALLOWED_ROOTS", raising=False)
    with pytest.raises(HTTPException) as denied:
        load_trusted_synthetic_fixture()
    assert getattr(denied.value, "status_code", None) == 503
    monkeypatch.setenv("WATER_SYNTHETIC_FIXTURE_ALLOWED_ROOTS", str(isolated))
    assert load_trusted_synthetic_fixture().fixture_id == "m1a-unified-1.0.0"
