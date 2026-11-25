from collections import Counter
import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from src.gas_application import service as fixture_module
from src.gas_application.agent import GasAgentQueryRequest, GasAgentService
from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.cuv_engineering import GRAPH_CAPABILITY, engineering_assessment
from src.gas_application.model_provider import LocalGasModel
from src.gas_application.standalone import create_gas_app


REPLAY = "/api/v1/gas/engineering/cuv-replay"
ENGINEERING = REPLAY + "/engineering"
AGENT = "/api/v1/gas/engineering/agent/query"
PHYSICAL = ("steady_pressure_flow", "supply_adequacy", "transient_recovery")


def source_binding(report):
    return {name: report["topology_evidence"][name] for name in (
        "network_content_sha256", "case_content_sha256", "replay_content_sha256",
    )}


def forbidden(*args, **kwargs):
    pytest.fail("unavailable capability or stale source reached the runner or model")


def test_engineering_status_is_bound_to_current_synthetic_replay(monkeypatch):
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    common = {"scope": "internal", "modes": ["synthetic"], "permissions": []}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "reader": {**common, "actor_id": "engineering-reader", "datasets": [GAS_DATASET_ID]},
        "other": {**common, "actor_id": "outside-scope-reader", "datasets": []},
    }))
    headers = {"X-Water-Token": "reader"}
    with TestClient(create_gas_app()) as client:
        response = client.get(REPLAY, headers=headers)
        assert response.status_code == 200
        replay = response.json()
        binding = source_binding(replay)
        assert client.get(ENGINEERING, params=binding).status_code == 401
        assert client.get(ENGINEERING, headers={"X-Water-Token": "other"}, params=binding).status_code == 403
        response = client.get(ENGINEERING, headers=headers, params=binding)
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "no-store"
        data = response.json()
        assert data["binding"] == binding and data["case_id"] == replay["case_id"]
        assert data["evidence_scope"]["data_mode"] == "synthetic"
        assert data["plan_ids"] == [plan["plan_id"] for plan in replay["plans"]]
        statuses = {item["capability_id"]: item for item in data["capabilities"]}
        assert statuses[GRAPH_CAPABILITY]["status"] == "ready_synthetic"
        assert statuses[GRAPH_CAPABILITY]["model_version"] == replay["algorithm_version"]
        assert all(statuses[code]["status"] == "blocked" and statuses[code]["method"] is None
                   and statuses[code]["missing_input_ids"] for code in PHYSICAL)
        assert [plan["affected_service_hours"] for plan in replay["plans"]] == [35, 37]
        assert all(metric["value"] is None for metric in replay["unknown_metrics"])

        monkeypatch.setattr(fixture_module, "compare_cuv_replay", forbidden)
        monkeypatch.setattr(LocalGasModel, "from_environment", forbidden)
        for name in binding:
            stale = binding | {name: "0" * 64}
            conflict = client.get(ENGINEERING, headers=headers, params=stale)
            assert conflict.status_code == 409 and conflict.json()["detail"] == "gas_cuv_evidence_version_conflict"
        conflict = client.post(AGENT, headers=headers, json={
            "question": "解释 CUV 工程模型的缺项。", "tool_hint": "cuv.engineering.query",
            "cuv_replay_sha256": "0" * 64,
        })
        assert conflict.status_code == 409


@pytest.mark.parametrize("capability", PHYSICAL)
def test_missing_engineering_inputs_prevent_execution(monkeypatch, capability):
    monkeypatch.setattr(fixture_module, "compare_cuv_replay", forbidden)
    monkeypatch.setattr(LocalGasModel, "from_environment", forbidden)
    reply = GasAgentService(fixture_module.GasFixtureService()).query(GasAgentQueryRequest(
        question="计算当前 CUV 工程方案的结果。", tool_hint="cuv.query", cuv_capability=capability,
    ))
    assert reply.status == "unavailable" and reply.error_code == "engineering_capability_blocked"
    tool = reply.tools[0]
    assert tool.data == {} and tool.engineering_capability.capability_id == capability
    assert tool.engineering_capability.validation_status == "not_business_validated"
    assert tool.engineering_capability.missing_input_ids
    assert reply.model_calls == 0 and not reply.evidence_refs


def test_operator_map_covers_all_109_current_fields_and_registered_input_groups():
    repository = Path(__file__).resolve().parents[2]
    folder = repository / "docs" / "templates"
    with (folder / "gas-pilot-02-fields.csv").open(encoding="utf-8-sig", newline="") as file:
        source = {(row["数据表"], row["字段名称"]): row["交付级别"] for row in csv.DictReader(file)}
    with (folder / "gas-cuv-engineering-field-map.csv").open(encoding="utf-8-sig", newline="") as file:
        mapping = list(csv.DictReader(file))
    assert len(source) == len(mapping) == 109
    assert {(row["数据表"], row["字段名称"]): row["交付级别"] for row in mapping} == source
    assert Counter(row["交付级别"] for row in mapping) == {"A": 49, "B": 51, "C": 9}
    inputs = engineering_assessment(fixture_module.GasFixtureService().cuv_replay()).inputs
    known_groups = {record.input_id for record in inputs}
    assert all(set(row["输入组"].split("|")) <= known_groups and row["校核说明"] for row in mapping)
