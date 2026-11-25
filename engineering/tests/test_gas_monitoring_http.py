import json
from shutil import copytree

import pytest
from fastapi.testclient import TestClient

from src.gas_application.adapter import DEFAULT_FIXTURE_ROOT
from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.service import MONITORING_PACKAGE, GasFixtureService
from src.gas_application.standalone import create_gas_app
from src.platform.app import create_app


URL = "/api/v1/gas/engineering/monitoring"
ASSETS_URL = "/api/v1/gas/engineering/assets/"


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "test-gas": {
            "actor_id": "gas-test", "scope": "internal", "modes": ["synthetic"],
            "datasets": [GAS_DATASET_ID], "permissions": [],
        },
        "test-other": {
            "actor_id": "other-test", "scope": "internal", "modes": ["synthetic"],
            "datasets": [], "permissions": [],
        },
    }))
    return {"X-Water-Token": "test-gas"}


@pytest.mark.parametrize("standalone", [True, False])
def test_monitoring_endpoint_acl_asset_links_and_events(tmp_path, credentials, standalone):
    app = (create_gas_app() if standalone
           else create_app(tmp_path, bootstrap=False, start_worker=False))
    with TestClient(app) as client:
        assert client.get(URL).status_code == 401
        assert client.get(URL, headers={"X-Water-Token": "test-other"}).status_code == 403
        response = client.get(URL, headers=credentials)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        assert client.post(URL, headers={**credentials, "X-Water-Client": "unified-react"}).status_code == 405
        report = response.json()
        assert report["data_mode"] == "synthetic"
        assert report["validation_status"] == "not_business_validated"
        assert report["policy_id"] == "syn-gas-monitoring-policy-0.1.0"
        assert report["evaluated_at"] == "2026-11-18T01:12:00Z"
        pressure, flow = report["channels"]
        assert (pressure["state"], flow["state"]) == ("limit_exceeded", "data_missing")
        for result in (pressure, flow):
            assert client.get(ASSETS_URL + result["asset_id"], headers=credentials).status_code == 200
            assert {event["kind"] for event in result["events"]} == {
                "limit_exceeded", "data_missing", "recovered",
            }
        latest_pressure = pressure["events"][-1]
        assert len(latest_pressure["evidence"]) == 3
        assert latest_pressure["evidence"][-1]["source_sha256"] == report["monitoring_source"]["source_sha256"]
        latest_flow = flow["events"][-1]
        assert [row["observed_at"] for row in latest_flow["evidence"]] == ["2026-11-18T01:10:00Z"]
        assert latest_flow["missing_slots"] == ["2026-11-18T01:11:00Z", "2026-11-18T01:12:00Z"]


@pytest.mark.parametrize("invalid", ["source_hash", "asset_link"])
def test_monitoring_unavailable_is_distinct_from_a_missing_signal(tmp_path, credentials, invalid):
    root = tmp_path / "gas-m0"
    copytree(DEFAULT_FIXTURE_ROOT, root)
    if invalid == "source_hash":
        path = root / MONITORING_PACKAGE / "monitoring.csv"
        path.write_bytes(path.read_bytes() + b"\n")
    else:
        path = root / "gas54_network.json"
        network = json.loads(path.read_text(encoding="utf-8"))
        network["pipes"] = [pipe for pipe in network["pipes"] if pipe["pipe_id"] != "syn-gas-pipe-K-003"]
        path.write_text(json.dumps(network), encoding="utf-8")
    app = create_gas_app(gas_service=GasFixtureService(root))
    with TestClient(app) as client:
        response = client.get(URL, headers=credentials)
        assert response.status_code == 503
        assert response.json() == {"detail": "fixture unavailable"}
