from __future__ import annotations

import json
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from src.gas_application.adapter import fixture_root
from src.gas_application.service import GasFixtureService
from src.platform.app import create_app

DATASET = "syn-gas-network-html54-0.1.0"
BASE = "/api/v1/gas/engineering"
PATHS = [
    BASE + "/districts",
    BASE + "/assets",
    BASE + "/assets/syn-gas-node-N53",
    BASE + "/events",
    BASE + "/events/syn-gas-event-fault",
]


def app_for(tmp_path, monkeypatch, service=None):
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "limited-mode": {
            "actor_id": "limited-mode", "scope": "internal",
            "modes": [], "datasets": [], "permissions": [],
        },
        "limited-dataset": {
            "actor_id": "limited-dataset", "scope": "internal",
            "modes": ["synthetic"], "datasets": [], "permissions": [],
        },
        "gas-token": {
            "actor_id": "gas-user", "scope": "internal",
            "modes": ["synthetic"], "datasets": [DATASET], "permissions": [],
        },
    }))
    return create_app(tmp_path, bootstrap=False, start_worker=False, gas_service=service)


def test_all_engineering_reads_require_mode_and_dataset_acl(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assert all(client.get(path).status_code == 401 for path in PATHS)
        assert all(client.get(path, headers={"X-Water-Token": "wrong"}).status_code == 401 for path in PATHS)
        assert all(client.get(path, headers={"X-Water-Token": "limited-mode"}).status_code == 403 for path in PATHS)
        assert all(client.get(path, headers={"X-Water-Token": "limited-dataset"}).status_code == 403 for path in PATHS)
        assert [client.get(path, headers={"X-Water-Token": "gas-token"}).status_code for path in PATHS] == [200] * len(PATHS)


def test_limited_token_cannot_read_legacy_cases_without_dataset_acl(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get("/api/v1/gas/cases/syn-gas-case-K-001", headers={"X-Water-Token": "limited-dataset"})
    assert response.status_code == 403


def test_client_labels_and_query_values_cannot_escalate(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get(
            BASE + "/assets?data_mode=synthetic&dataset_id=" + DATASET,
            headers={
                "X-Water-Token": "limited-mode",
                "X-Water-Data-Scope": "authorized",
                "X-Water-Client": "trusted-internal",
            },
        )
    assert response.status_code == 403
    assert response.json() == {"detail": "当前主体无权读取该数据集"}


def test_rejected_request_does_not_touch_fixture_service(tmp_path, monkeypatch):
    class ExplodingService:
        def districts(self):
            raise AssertionError("fixture read occurred before authorization")

    app = app_for(tmp_path, monkeypatch, service=ExplodingService())
    with TestClient(app) as client:
        response = client.get(BASE + "/districts")
    assert response.status_code == 401


def test_authorized_lists_keep_total_pagination_and_node_types(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        assets = client.get(BASE + "/assets?limit=2&offset=2", headers={"X-Water-Token": "gas-token"})
        regulators = client.get(BASE + "/assets?asset_type=regulator", headers={"X-Water-Token": "gas-token"})
        junctions = client.get(BASE + "/assets?asset_type=junction", headers={"X-Water-Token": "gas-token"})
        sources = client.get(BASE + "/assets?asset_type=source", headers={"X-Water-Token": "gas-token"})
    assert assets.status_code == 200
    assert assets.json()["total"] == 204
    assert len(assets.json()["items"]) == 2
    assert assets.json()["validation_status"] == "not_business_validated"
    assert regulators.json()["total"] == 2 and all(item["asset_type"] == "regulator" for item in regulators.json()["items"])
    assert junctions.json()["total"] == 19 and all(item["asset_type"] == "junction" for item in junctions.json()["items"])
    assert sources.json()["total"] == 2 and all(item["asset_type"] == "source" for item in sources.json()["items"])


def test_event_assets_include_pipes_and_closed_valves(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get(BASE + "/events/syn-gas-event-isolation", headers={"X-Water-Token": "gas-token"})
        valve_n11 = client.get(BASE + "/assets/syn-gas-valve-N11", headers={"X-Water-Token": "gas-token"})
    body = response.json()
    assert response.status_code == 200
    assert body["closed_valve_ids"] == ["syn-gas-valve-N11", "syn-gas-valve-N13"]
    assert "syn-gas-pipe-K-001" in body["asset_ids"]
    assert "syn-gas-valve-N11" in body["asset_ids"]
    assert valve_n11.status_code == 200


def test_corrupt_fixture_is_sanitized_503_after_authorization(tmp_path, monkeypatch):
    source = fixture_root()
    root = tmp_path / "fixture"
    root.mkdir()
    for name in ("gas54_network.json", "gas54_case.json"):
        shutil.copy2(source / name, root / name)
    network = json.loads((root / "gas54_network.json").read_text(encoding="utf-8"))
    network["nodes"][0]["name"] = 123
    (root / "gas54_network.json").write_text(json.dumps(network), encoding="utf-8")
    app = app_for(tmp_path / "store", monkeypatch, service=GasFixtureService(root))
    with TestClient(app) as client:
        response = client.get(BASE + "/assets", headers={"X-Water-Token": "gas-token"})
    assert response.status_code == 503
    assert str(root) not in response.text
    assert "Traceback" not in response.text


def test_engineering_missing_or_illegal_fixture_returns_503_after_acl(tmp_path, monkeypatch):
    missing = tmp_path / "missing-root"
    monkeypatch.delenv("WATER_GAS_FIXTURE_ROOT", raising=False)
    app = app_for(tmp_path / "state", monkeypatch)
    monkeypatch.setenv("WATER_GAS_FIXTURE_ROOT", str(missing))
    with TestClient(app) as client:
        response = client.get(BASE + "/assets", headers={"X-Water-Token": "gas-token"})
    assert response.status_code == 503
    assert str(missing) not in response.text
    assert "Traceback" not in response.text

    illegal = tmp_path / "illegal-root"
    illegal.mkdir()
    monkeypatch.setenv("WATER_GAS_FIXTURE_ROOT", str(illegal))
    monkeypatch.delenv("WATER_GAS_FIXTURE_ALLOWED_ROOTS", raising=False)
    with TestClient(app) as client:
        response = client.get(BASE + "/events", headers={"X-Water-Token": "gas-token"})
    assert response.status_code == 503
    assert str(illegal) not in response.text


def test_engineering_acl_precedes_missing_fixture(tmp_path, monkeypatch):
    missing = tmp_path / "missing-before-auth"
    monkeypatch.setenv("WATER_GAS_FIXTURE_ROOT", str(missing))
    app = app_for(tmp_path / "state", monkeypatch)
    with TestClient(app) as client:
        assert client.get(BASE + "/districts").status_code == 401
        assert client.get(BASE + "/assets", headers={"X-Water-Token": "limited-mode"}).status_code == 403
