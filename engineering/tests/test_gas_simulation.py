
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.gas_application.agent import GasAgentQueryRequest, GasAgentService
from src.gas_application.public_data import (
    DRYAD_DATASET_ID,
    PHMSA_DATASET_ID,
    GasPublicDataService,
)
from src.gas_application.service import GasFixtureService
from src.gas_application.simulation import GasSimulationRequest, GasSimulationService
from src.gas_application.standalone import create_gas_app
from src.platform.authz import resolve_actor
from test_gas_public_data import configure_principals, write_fixture


def test_simulation_catalog_and_synthetic_replay(monkeypatch):
    configure_principals(monkeypatch)
    reader = resolve_actor("reader", "internal")
    service = GasSimulationService(GasFixtureService(), GasPublicDataService())
    catalog = service.catalog(reader)
    assert catalog.status == "ready"
    modes = {item.mode: item.status for item in catalog.capabilities}
    assert modes == {
        "synthetic_topology": "ready",
        "experiment_replay": "ready",
        "historical_incident": "ready",
        "solver_benchmark": "candidate",
        "physical_chongqing": "unavailable",
    }
    replay = service.run(reader, GasSimulationRequest(mode="synthetic_topology"))
    assert replay.status == "partial"
    assert replay.dataset_id == "syn-gas-network-html54-0.1.0"
    assert replay.report.report_sha256
    assert any(ref.startswith("gas-cuv-replay:sha256:") for ref in replay.evidence_refs)
    replay_again = service.run(reader, GasSimulationRequest(mode="synthetic_topology"))
    assert replay.report.report_sha256 == replay_again.report.report_sha256
    assert replay.report.report_id == replay_again.report.report_id


def test_public_replays_and_physical_gate(tmp_path, monkeypatch):
    configure_principals(monkeypatch)
    write_fixture(tmp_path)
    reader = resolve_actor("reader", "internal")
    service = GasSimulationService(GasFixtureService(), GasPublicDataService(root=tmp_path))
    dryad = service.run(reader, GasSimulationRequest(mode="experiment_replay", experiment_id="Experiment_1"))
    assert dryad.status == "succeeded"
    assert dryad.dataset_id == DRYAD_DATASET_ID
    assert dryad.result["record_count"] == 2
    phmsa = service.run(reader, GasSimulationRequest(mode="historical_incident", category="gas_distribution"))
    assert phmsa.status == "succeeded"
    assert phmsa.dataset_id == PHMSA_DATASET_ID
    assert phmsa.result["total"] == 2
    physical = service.run(reader, GasSimulationRequest(mode="physical_chongqing"))
    assert physical.status == "unavailable"
    assert physical.result["calculation"] == "unavailable"
    assert any("授权" in item for item in physical.limitations)


def test_simulation_routes_and_agent_tool(tmp_path, monkeypatch):
    configure_principals(monkeypatch)
    write_fixture(tmp_path)
    monkeypatch.setenv("GAS_PUBLIC_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("WATER_TRUSTED_HOSTS", "testserver,127.0.0.1")
    app = create_gas_app()
    with TestClient(app) as client:
        headers = {"X-Water-Token": "reader"}
        catalog = client.get("/api/v1/gas/engineering/simulation/catalog", headers=headers)
        assert catalog.status_code == 200
        assert catalog.json()["workbench_version"] == "gas-simulation-workbench-0.1.0"
        replay = client.post(
            "/api/v1/gas/engineering/simulation/run",
            headers=headers,
            json={"mode": "synthetic_topology"},
        )
        assert replay.status_code == 200
        assert replay.json()["report"]["report_sha256"]
        dryad = client.post(
            "/api/v1/gas/engineering/simulation/run",
            headers=headers,
            json={"mode": "experiment_replay", "experiment_id": "Experiment_1"},
        )
        assert dryad.status_code == 200
        assert dryad.json()["dataset_id"] == DRYAD_DATASET_ID
        denied = client.post(
            "/api/v1/gas/engineering/simulation/run",
            headers={"X-Water-Token": "limited"},
            json={"mode": "experiment_replay"},
        )
        assert denied.status_code == 403
        agent = client.post(
            "/api/v1/gas/engineering/agent/query",
            headers=headers,
            json={"question": "仿真 K-001 合成拓扑", "tool_hint": "simulation.query"},
        )
        assert agent.status_code == 200
        body = agent.json()
        assert body["intent"] == "simulation_query"
        assert body["tools"][0]["tool_name"] == "simulation.query"
        assert body["tools"][0]["status"] == "partial"


def test_simulation_invalid_dataset_conflict(monkeypatch):
    configure_principals(monkeypatch)
    reader = resolve_actor("reader", "internal")
    service = GasSimulationService(GasFixtureService(), GasPublicDataService())
    try:
        service.run(reader, GasSimulationRequest(mode="synthetic_topology", dataset_id="wrong"))
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("dataset conflict was not rejected")
