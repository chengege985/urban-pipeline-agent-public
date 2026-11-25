import json
from datetime import timedelta
from shutil import copytree

import pytest
from fastapi.testclient import TestClient

from src.gas_application.adapter import DEFAULT_FIXTURE_ROOT
from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.cuv_evidence import link_monitoring
from src.gas_application.service import CUV_REPLAY_FILE, MONITORING_PACKAGE, GasFixtureService
from src.gas_application.standalone import create_gas_app
from src.platform.app import create_app


ENGINEERING = "/api/v1/gas/engineering/"
REPLAY = ENGINEERING + "cuv-replay"
FAULT = "syn-gas-pipe-K-001"
ISOLATION = "syn-gas-pipe-K-003"


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
def test_replay_requires_gas_acl_and_links_exact_case_and_monitoring(tmp_path, credentials, standalone):
    app = (create_gas_app() if standalone
           else create_app(tmp_path, bootstrap=False, start_worker=False))
    with TestClient(app) as client:
        assert client.get(REPLAY).status_code == 401
        assert client.get(REPLAY, headers={"X-Water-Token": "test-other"}).status_code == 403
        result = client.get(REPLAY, headers=credentials)
        assert result.status_code == 200
        assert result.headers["Cache-Control"] == "no-store"
        assert client.post(REPLAY, headers={**credentials, "X-Water-Client": "unified-react"}).status_code == 405
        report = result.json()
        assert report["data_mode"] == "synthetic"
        assert report["validation_status"] == "not_business_validated"
        assert report["semantic"] == "graph_connectivity_only"
        assert report["scenario_at"] == "2026-11-18T01:12:00Z"
        assert report["fault_asset_ids"] == [FAULT]
        assert len(report["plans"]) == 2
        assert all(metric["status"] == "unknown" and metric["value"] is None
                   for metric in report["unknown_metrics"])
        for event in report["event_evidence"]:
            original = client.get(ENGINEERING + "events/" + event["event_id"], headers=credentials)
            assert original.status_code == 200
            assert event["asset_ids"] == original.json()["asset_ids"]
            assert event["evidence_refs"] == original.json()["evidence_refs"]
            assert event["source_sha256"] == report["topology_evidence"]["case_content_sha256"]
        fault, isolation, observation = report["event_evidence"]
        assert [item["elapsed_hours"] for item in (fault, isolation, observation)] == [0, 0.5, 1]
        assert [item["replay_at"] for item in (fault, isolation, observation)] == [
            "2026-11-18T01:12:00Z", "2026-11-18T01:42:00Z", "2026-11-18T02:12:00Z",
        ]
        context = report["monitoring_evidence"]
        assert context["as_of"] == report["scenario_at"]
        current_source = client.get(ENGINEERING + "monitoring", headers=credentials).json()
        assert context["manifest_sha256"] == current_source["manifest_sha256"]
        pressure, flow = context["channels"]
        assert (pressure["asset_id"], pressure["relation"]) == (FAULT, "fault_asset")
        assert (flow["asset_id"], flow["relation"]) == (ISOLATION, "isolation_asset")
        assert pressure["transition"] == current_source["channels"][0]["events"][-1]
        assert flow["transition"] == current_source["channels"][1]["events"][-1]
        assert pressure["transition"]["evidence"][-1]["observed_at"] == report["scenario_at"]
        assert flow["transition"]["missing_slots"] == ["2026-11-18T01:11:00Z", report["scenario_at"]]


def test_future_observations_are_not_relabelled_as_earlier_evidence():
    report = GasFixtureService().monitoring()
    earlier = report.evaluated_at - timedelta(minutes=1)
    context = link_monitoring(report, scenario_at=earlier, fault_assets={FAULT}, isolation_assets={ISOLATION})
    assert context.status == "unavailable"
    assert context.as_of == earlier
    assert context.channels == []
    assert context.source is None


@pytest.mark.parametrize("missing", ["monitoring", "scenario"])
def test_replay_distinguishes_unavailable_context_and_invalid_replay(tmp_path, credentials, missing):
    fixture = tmp_path / "gas-m0"
    copytree(DEFAULT_FIXTURE_ROOT, fixture)
    if missing == "monitoring":
        (fixture / MONITORING_PACKAGE / "manifest.json").unlink()
    else:
        (fixture / CUV_REPLAY_FILE).write_text("[]", encoding="utf-8")
    app = create_gas_app(gas_service=GasFixtureService(fixture))
    with TestClient(app) as client:
        result = client.get(REPLAY, headers=credentials)
        if missing == "monitoring":
            assert result.status_code == 200
            report = result.json()
            assert report["status"] == "partial"
            assert report["monitoring_evidence"]["status"] == "unavailable"
            assert report["monitoring_evidence"]["channels"] == []
            assert report["monitoring_evidence"]["manifest_sha256"] is None
        else:
            assert result.status_code == 503
            assert result.json() == {"detail": "fixture unavailable"}
