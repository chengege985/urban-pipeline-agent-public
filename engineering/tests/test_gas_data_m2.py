from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.gas_application.data_m2 import GasM2Dataset, LocalGasModel
from src.gas_application.service import GasFixtureService
from src.gas_application.standalone import create_gas_app


PATH = "/api/v1/gas/engineering/data-m2"
GAS = "syn-gas-network-html54-0.1.0"
READER = {"X-Water-Token": "synthetic-m2-reader-test"}
DENIED = {"X-Water-Token": "synthetic-m2-denied-test"}
SOURCES = [
    "gas54_network.json", "gas54_case.json", "gas_monitoring_replay/manifest.json",
    "gas_monitoring_replay/assets.csv", "gas_monitoring_replay/monitoring.csv",
]


def entry(identity: str, fixture: Path, **changes) -> dict:
    own = identity == "OWN-K001"
    values = {
        "dataset_id": identity, "title": identity, "source_uri": "https://example.org/gas",
        "version": "0.1.0" if own else "1.0.0", "sha256": "a" * 64,
        "source_kind": "project_fixture" if own else "research_repository",
        "data_kind": "internal_synthetic" if own else "historical_network",
        "geo_scope": "synthetic" if own else "Europe",
        "coordinate_system": "schematic" if own else "RFC7946",
        "statistics_time": None, "schema_status": "verified", "quality_status": "partial",
        "license_status": "internal_fixture" if own else "CC-BY-4.0",
        "synthetic_flag": own, "usage_scope": "synthetic_demo" if own else "research_gis",
        "ingest_status": "candidate_ready", "sources": [
            {"name": name, "sha256": hashlib.sha256((fixture / name).read_bytes()).hexdigest()}
            for name in SOURCES
        ] if own else [],
        "field_mappings": [], "transformations": [], "normalized_sha256": "c" * 64,
        "quarantine_sha256": None if own else "d" * 64, "record_counts": {}, "issues": [],
        "facts": ["经审核的研究目录。"],
    }
    return GasM2Dataset.model_validate(values | changes).model_dump()


@pytest.fixture
def resources(monkeypatch, tmp_path):
    for var in ("WATER_API_TOKEN", "WATER_TRUSTED_HOSTS", "WATER_CORS_ORIGINS", "GAS_AGENT_MODEL_MODE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    principals = {
        READER["X-Water-Token"]: {"actor_id": "reader-m2", "scope": "internal", "modes": ["synthetic"], "datasets": [GAS]},
        DENIED["X-Water-Token"]: {"actor_id": "denied-m2", "scope": "internal", "modes": ["synthetic"], "datasets": ["other"]},
    }
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals))
    fixture = Path(__file__).resolve().parents[1] / "experiments/gas_m0/data"
    registry = tmp_path / "registry.json"
    contents = {"schema_version": "gas-data-m2/1", "datasets": [entry("OWN-K001", fixture), entry("EU-SCIGRID-IGGIN", fixture)]}

    def update():
        registry.write_text(json.dumps(contents, ensure_ascii=False))
        digest = hashlib.sha256(registry.read_bytes()).hexdigest()
        monkeypatch.setenv("GAS_DATA_M2_REGISTRY_FILE", str(registry))
        monkeypatch.setenv("GAS_DATA_M2_REGISTRY_SHA256", digest)
        return digest

    update()
    with TestClient(create_gas_app(gas_service=GasFixtureService(fixture))) as client:
        yield client, contents, update


def lookup(client, identity):
    response = client.get(f"{PATH}/datasets/{identity}", headers=READER)
    assert response.status_code == 200, response.text
    return response.json()


def as_query(binding):
    return {key: binding[key] for key in ("registry_sha256", "version")}


def test_catalog_and_records_keep_exact_fixture_scope(resources):
    client, _, _ = resources
    listing = client.get(f"{PATH}/datasets", headers=READER)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "no-store"
    assert {item["dataset_id"] for item in listing.json()["datasets"]} == {"OWN-K001", "EU-SCIGRID-IGGIN"}
    entry = lookup(client, "OWN-K001")
    query = as_query(entry["binding"])
    url = f"{PATH}/datasets/OWN-K001/records"
    counts = {
        ("assets", "pipe"): 73, ("assets", "node"): 54, ("assets", "valve"): 31,
        ("events", None): 3, ("topology", None): 127, ("observations", None): 20,
    }
    for (kind, asset_type), count in counts.items():
        params = {**query, **({"asset_type": asset_type} if asset_type else {})}
        response = client.get(f"{url}/{kind}", headers=READER, params=params)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["total"] == count and len(data["items"]) == count
        assert data["coordinate_system"] == "schematic"
        assert set(data["capabilities"].values()) == {"unavailable"}
    item = client.get(f"{url}/assets", headers=READER, params=query | {"record_id": "syn-gas-pipe-K-001"}).json()["items"][0]
    assert item["district_id"] is None and "diameter_mm" in item
    assert client.get(f"{url}/assets", headers=READER, params=query | {"record_id": "K-999"}).status_code == 404


def test_fail_closed_on_bad_scope_version_fingerprint_and_method(resources, monkeypatch):
    client, catalog, update = resources
    assert client.get(f"{PATH}/datasets").status_code == 401
    assert client.get(f"{PATH}/datasets", headers=DENIED).status_code == 403
    assert client.get(f"{PATH}/datasets/UNKNOWN-ID", headers=READER).status_code == 404
    external = lookup(client, "EU-SCIGRID-IGGIN")
    assert client.get(f"{PATH}/datasets/EU-SCIGRID-IGGIN/records/topology",
                      headers=READER, params=as_query(external["binding"])).status_code == 409
    fixture = lookup(client, "OWN-K001")
    path = f"{PATH}/datasets/OWN-K001/records/events"
    old = as_query(fixture["binding"])
    assert client.get(path, headers=READER, params=old | {"version": "stale"}).status_code == 409
    assert client.get(path, headers=READER, params=old | {"asset_type": "node"}).status_code == 422
    assert client.post(f"{PATH}/datasets", headers=READER, json={}).status_code == 405
    catalog["datasets"][0]["sources"][0]["sha256"] = "b" * 64
    fresh = update()
    assert client.get(path, headers=READER, params=old | {"registry_sha256": fresh}).status_code == 503
    monkeypatch.setenv("GAS_DATA_M2_REGISTRY_SHA256", "f" * 64)
    assert client.get(f"{PATH}/datasets", headers=READER).status_code == 503


def test_qwen_selects_deterministic_evidence_and_never_receives_a_stale_binding(resources, monkeypatch):
    client, _, _ = resources
    calls = []

    class Provider:
        calls = 0
        usage = {"total_tokens": 77}

        def explain(self, sentences, *, question):
            assert question == "解释数据来源"
            self.calls += 1
            calls.extend(sentences)
            return sentences[1]

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: Provider()))
    bound = lookup(client, "EU-SCIGRID-IGGIN")["binding"]
    payload = {"binding": bound, "section": "provenance", "question": "解释数据来源"}
    invalid = {**payload, "binding": {**bound, "version": "invalid"}}
    assert client.post(f"{PATH}/agent/query", headers=READER, json=invalid).status_code == 409
    assert not calls
    response = client.post(f"{PATH}/agent/query", headers=READER, json=payload)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["request_id"] == response.headers["X-Request-ID"]
    assert result["model_calls"] == 1 and result["explanation_source"] == "qwen_evidence_selection"
    assert all(line in result["facts"] for line in result["answer"].splitlines())
    assert set(result["capabilities"].values()) == {"unavailable"}


def test_fix1_gaslib_checks_every_nomination_before_claiming_fixed_balance(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from gas_data_m2_prepare import apply_profile

    balanced = {
        "fixed_flow": True, "flow_balance_consistent": True, "fixed_balance_residual": "0.00",
        "flow_unit": "1000m_cube_per_hour", "supply_bounds": ["200", "200"], "demand_bounds": ["200", "200"],
        "nodes_without_explicit_nomination_pressure": 0, "flow_outside_network_bounds": 0,
    }
    unbalanced = {
        **balanced, "flow_balance_consistent": False, "fixed_balance_residual": "100",
        "demand_bounds": ["100", "100"], "nodes_without_explicit_nomination_pressure": 2,
    }
    result = apply_profile(
        {"dataset_id": "DE-GASLIB"},
        {"instances": [{"node_count": 11, "xsd_documents_passed": 3, "scenarios": [balanced, unbalanced]}]},
        [{"name": "synthetic-review.zip", "sha256": "a" * 64}], "b" * 64, None, "test",
    )
    issues = {row["code"]: row for row in result["issues"]}
    assert issues["nomination_flow_unbalanced"]["count"] == 1
    assert "工况 2" in issues["nomination_flow_unbalanced"]["detail"]
    assert issues["nomination_pressure_missing"]["count"] == 2
    assert len([fact for fact in result["facts"] if "固定进出流量总量一致" in fact]) == 1
    assert "工况 1" in result["facts"][0]


@pytest.fixture
def unregistered_sensor_fixture(tmp_path):
    app_fixture = Path(__file__).resolve().parents[1] / "experiments/gas_m0/data"
    for name in SOURCES:
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((app_fixture / name).read_bytes())
    replay = tmp_path / "gas_monitoring_replay"
    observations = replay / "monitoring.csv"
    raw = observations.read_bytes()
    assert raw.count(b"syn-gas-monitor-pressure-k001") == 11
    observations.write_bytes(raw.replace(b"syn-gas-monitor-pressure-k001", b"syn-gas-unregistered-pressure", 1))
    manifest_path = replay / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["sources"]["monitoring"]["sha256"] = hashlib.sha256(observations.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return tmp_path


def test_fix1_offline_profile_rejects_an_unregistered_sensor_on_a_known_asset(monkeypatch, unregistered_sensor_fixture):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    from gas_data_m2_profiles import k001

    with pytest.raises(ValueError, match="sensor binding"):
        k001(unregistered_sensor_fixture)


def test_fix1_serving_rejects_an_unregistered_sensor_even_with_matching_file_hashes(unregistered_sensor_fixture):
    from src.gas_application.service import GasFixtureUnavailable

    hashes = {name: hashlib.sha256((unregistered_sensor_fixture / name).read_bytes()).hexdigest() for name in SOURCES}
    service = GasFixtureService(unregistered_sensor_fixture)
    with pytest.raises(GasFixtureUnavailable, match="sensor binding"):
        service.m2_fixture(hashes)
    with pytest.raises(GasFixtureUnavailable, match="sensor binding"):
        service.monitoring()


def test_fix1_agent_boundary_rejects_unlisted_text_and_logs_only_the_violation_code(resources, monkeypatch, caplog):
    client, _, _ = resources
    unlisted = "仅供测试且缺少证据来源的句子。"

    class Provider:
        calls = 0
        usage = {"total_tokens": 12}

        def explain(self, sentences, *, question):
            self.calls += 1
            sentences.append(unlisted)
            return sentences[1] + "\n" + unlisted

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: Provider()))
    binding = lookup(client, "EU-SCIGRID-IGGIN")["binding"]
    with caplog.at_level("INFO", logger="gas.agent"):
        response = client.post(f"{PATH}/agent/query", headers=READER,
                               json={"binding": binding, "section": "provenance", "question": "查询数据来源"})
    assert response.status_code == 200
    result = response.json()
    assert result["explanation_source"] == "backend_facts"
    assert result["model_status"] == "unavailable"
    assert result["model_id"] is None
    assert result["model_calls"] == 1
    assert result["model_error"] == "model_evidence_whitelist_violation"
    assert result["answer"].splitlines() == result["facts"]
    events = [json.loads(r.message) for r in caplog.records if r.name == "gas.agent"]
    assert any(e["request_id"] == result["request_id"] and e["model_error"] == result["model_error"] for e in events)
    assert unlisted not in response.text and unlisted not in caplog.text


SCENARIOS = "/api/v1/gas/engineering/synthetic-scenarios"
K001 = "syn-gas-pipe-K-001"


@pytest.fixture
def scenario_resources(monkeypatch, tmp_path, request):
    if os.name != "posix":
        pytest.skip("Sprint02 uses the private Linux preview ledger")
    ledger = tmp_path / "scenario-ledger"
    ledger.mkdir(mode=0o700)
    monkeypatch.setenv("GAS_SCENARIO_AUDIT_DB", str(ledger / "audit.sqlite"))
    return request.getfixturevalue("resources")


def create_scenario(client, *, asset=K001, dataset_id="OWN-K001", headers=READER, fault_kind="synthetic_edge_exclusion"):
    return client.post(SCENARIOS, headers=headers, json={
        "binding": lookup(client, dataset_id)["binding"], "pipe_id": asset, "fault_kind": fault_kind,
    })


def analyze_scenario(client, path, revision):
    response = client.post(path + "/analyze", headers=READER, json={"expected_revision": revision})
    assert response.status_code == 200, response.text
    return response.json()


def test_scenario_k001_lifecycle_recomputes_and_retains_history(scenario_resources):
    client, registry, _ = scenario_resources
    draft = create_scenario(client)
    assert draft.status_code == 201 and draft.headers["cache-control"] == "no-store"
    scenario = draft.json()
    path = f"{SCENARIOS}/{scenario['scenario_id']}"
    assert scenario["analysis"] is None
    assert scenario["transition"]["current_status"] == "draft"
    analyzed = analyze_scenario(client, path, 1)
    impact = analyzed["analysis"]["impact"]
    assert (impact["baseline_reachable_node_count"], impact["scenario_reachable_node_count"]) == (54, 52)
    assert impact["newly_unreachable_node_ids"] == ["syn-gas-node-N00", "syn-gas-node-N10"]
    assert impact["affected_pipe_ids"] == [K001, "syn-gas-pipe-K-002"]
    assert all(analyzed["analysis"][field] == "unknown" for field in ("actual_supply", "actual_outage", "pressure", "flow", "leakage"))
    assert client.post(path + "/analyze", headers=READER, json={"expected_revision": 1}).status_code == 409
    rollback = client.post(path + "/rollback", headers=READER, json={"expected_revision": 2})
    assert rollback.status_code == 200
    assert rollback.json()["transition"]["current_status"] == "rolled_back"
    assert rollback.json()["analysis"] == analyzed["analysis"]
    assert analyze_scenario(client, path, 3)["analysis"] == analyzed["analysis"]
    report = client.get(path + "/report", params={"revision": 4}, headers=READER).json()
    assert [row["action"] for row in report["audit"]] == ["created", "analyzed", "rolled_back", "analyzed"]
    assert report["scenario"]["source"]["files"] == sorted(registry["datasets"][0]["sources"], key=lambda row: row["name"])
    assert "gas-scenario-analysis:sha256:" + analyzed["analysis"]["analysis_sha256"] in report["evidence_refs"]
    archived = client.get(path + "/report", params={"revision": 2}, headers=READER).json()
    assert archived["scenario"] == analyzed and len(archived["audit"]) == 2
    with TestClient(create_gas_app()) as restarted:
        assert restarted.get(path, headers=READER).json() == report["scenario"]
    fixture = Path(__file__).resolve().parents[1] / "experiments/gas_m0/data"
    assert all(hashlib.sha256((fixture / row["name"]).read_bytes()).hexdigest() == row["sha256"]
               for row in registry["datasets"][0]["sources"])


def test_scenario_acl_and_binding_are_checked_on_each_operation(scenario_resources, monkeypatch):
    client, registry, update = scenario_resources
    assert create_scenario(client, headers={}).status_code == 401
    assert create_scenario(client, headers=DENIED).status_code == 403
    assert create_scenario(client, asset="syn-gas-pipe-K-999").status_code == 404
    assert create_scenario(client, dataset_id="EU-SCIGRID-IGGIN").status_code == 409
    scenario = create_scenario(client).json()
    path = f"{SCENARIOS}/{scenario['scenario_id']}"
    principals = json.loads(os.environ["WATER_PRINCIPALS"])
    principals["other-synthetic-reader"] = {**principals[READER["X-Water-Token"]], "actor_id": "different-reader"}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals))
    other = {"X-Water-Token": "other-synthetic-reader"}
    assert client.get(path, headers=other).status_code == 404
    assert client.get(path + "/report", params={"revision": 1}, headers=other).status_code == 404
    registry["datasets"][0]["version"] = "0.2.0"
    update()
    assert client.post(path + "/analyze", headers=READER, json={"expected_revision": 1}).status_code == 409
    assert client.get(path + "/report", params={"revision": 1}, headers=READER).status_code == 409


def test_scenario_qwen_only_reads_the_active_revision_and_returns_verified_facts(scenario_resources, monkeypatch, caplog):
    client, _, _ = scenario_resources
    draft = create_scenario(client, asset="syn-gas-pipe-K-003").json()
    path = f"{SCENARIOS}/{draft['scenario_id']}"
    analyzed = analyze_scenario(client, path, 1)
    assert analyzed["analysis"]["impact"]["newly_unreachable_node_ids"] == []
    digest = analyzed["analysis"]["analysis_sha256"]
    captured = []
    unlisted = "没有计算证据的物理推断。"

    class Provider:
        calls = 0
        usage = {"total_tokens": 22}

        def explain(self, sentences, *, question):
            captured.append((sentences.copy(), question))
            self.calls += 1
            return sentences[2] if len(captured) == 1 else sentences[2] + "\n" + unlisted

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: Provider()))
    question = {"expected_revision": 2, "analysis_sha256": digest, "question": "有哪些新增图不可达节点？"}
    assert client.post(path + "/explain", headers=READER, json={**question, "analysis_sha256": "1" * 64}).status_code == 409
    assert not captured
    with caplog.at_level("INFO", logger="gas.agent"):
        first = client.post(path + "/explain", headers=READER, json=question)
        second = client.post(path + "/explain", headers=READER, json=question)
    assert first.status_code == second.status_code == 200
    assert first.json()["explanation_source"] == "qwen_evidence_selection"
    assert first.json()["answer"].splitlines()[1] == captured[0][0][2]
    assert second.json()["explanation_source"] == "backend_facts"
    assert second.json()["model_error"] == "model_evidence_whitelist_violation"
    assert unlisted not in second.text and unlisted not in caplog.text
    assert first.headers["x-request-id"] in caplog.text
    assert client.post(path + "/rollback", headers=READER, json={"expected_revision": 2}).status_code == 200
    assert client.post(path + "/explain", headers=READER, json={**question, "expected_revision": 3}).status_code == 409
    assert len(captured) == 2


def scenario_ref(snapshot):
    return {"scenario_id": snapshot["scenario_id"], "revision": snapshot["transition"]["revision"],
            "analysis_sha256": snapshot["analysis"]["analysis_sha256"]}


def test_drill_presets_distinguish_source_union_path_and_component_structure(scenario_resources):
    client, _, _ = scenario_resources
    binding = lookup(client, "OWN-K001")["binding"]
    workbench = client.get(SCENARIOS + "/workspace", headers=READER, params=as_query(binding))
    assert workbench.status_code == 200
    assert workbench.json()["trust"]["observation_count"] == 20
    assert workbench.json()["trust"]["observation_quality"] == {"valid": 20}
    assert workbench.json()["trust"]["source_files_verified"] == 5
    presets = workbench.json()["presets"]
    assert [item["name"] for item in presets] == ["k001", "alternate", "dual-source"]
    results = []
    for preset in presets:
        draft = create_scenario(client, asset=preset["pipe_ids"][0], fault_kind=preset["fault_kind"])
        assert draft.status_code == 201
        result = analyze_scenario(client, SCENARIOS + "/" + draft.json()["scenario_id"], 1)["analysis"]
        assert result["algorithm"] == preset["algorithm"]
        assert result["graph"]["source_nodes"] == {
            "syn-gas-source-N20": "syn-gas-node-N20", "syn-gas-source-N38": "syn-gas-node-N38",
        }
        results.append(result)
    branch, alternate, split = results
    assert branch["graph"]["scenario"]["components"][0] == {
        "node_ids": ["syn-gas-node-N00", "syn-gas-node-N10"], "source_ids": [],
    }
    assert not branch["graph"]["alternative_path_edge_ids"]
    assert alternate["impact"]["scenario_reachable_node_count"] == 54
    assert len(alternate["graph"]["scenario"]["components"]) == 1
    assert len(alternate["graph"]["alternative_path_edge_ids"]) > 0
    assert alternate["impact"]["fault_edge_id"] not in alternate["graph"]["alternative_path_edge_ids"]
    groups = split["graph"]["scenario"]["components"]
    assert sorted(len(row["node_ids"]) for row in groups) == [2, 52]
    assert sorted(row["source_ids"] for row in groups) == [["syn-gas-source-N20"], ["syn-gas-source-N38"]]
    assert split["impact"]["scenario_reachable_node_count"] == 54
    assert split["graph"]["excluded_edge_ids"] == ["syn-gas-edge-K-026", "syn-gas-edge-K-041"]
    assert len({value["analysis_sha256"] for value in results}) == 3


def test_drill_comparison_and_tools_enforce_pinned_revisions_and_actor(scenario_resources, monkeypatch, caplog):
    client, _, _ = scenario_resources
    binding = lookup(client, "OWN-K001")["binding"]
    first = create_scenario(client).json()
    one = SCENARIOS + "/" + first["scenario_id"]
    r2 = analyze_scenario(client, one, 1)
    assert client.post(one + "/rollback", headers=READER, json={"expected_revision": 2}).status_code == 200
    r4 = analyze_scenario(client, one, 3)
    compare = SCENARIOS + "/comparison"
    tools = SCENARIOS + "/tool-query"
    selection = {"binding": binding, "before": scenario_ref(r2), "after": scenario_ref(r4)}
    response = client.post(compare, headers=READER, json=selection)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["newly_reachable_node_ids"] == result["newly_unreachable_node_ids"] == []
    assert result["before"]["report"]["scenario"]["transition"]["revision"] == 2
    assert result["after"]["report"]["scenario"]["transition"]["revision"] == 4
    assert [row["action"] for row in result["after"]["report"]["audit"]] == ["created", "analyzed", "rolled_back", "analyzed"]
    assert client.post(compare, headers=READER, json={**selection, "before": {**scenario_ref(r2), "revision": 3}}).status_code == 409
    assert client.post(compare, headers=READER, json={**selection, "before": {**scenario_ref(r2), "analysis_sha256": "9" * 64}}).status_code == 409
    assert create_scenario(client, fault_kind="synthetic_dual_source_split").status_code == 409
    principals = json.loads(os.environ["WATER_PRINCIPALS"])
    principals["peer-synthetic-reader"] = {**principals[READER["X-Water-Token"]], "actor_id": "different-reader"}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals))
    assert client.post(compare, headers={"X-Water-Token": "peer-synthetic-reader"}, json=selection).status_code == 404

    class Provider:
        calls = 0
        usage = {"total_tokens": 15}

        def explain(self, facts, *, question):
            self.calls += 1
            return facts[1]

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: Provider()))
    question = {"tool": "compare", "selection": selection, "expected_sha256": result["comparison_sha256"], "question": "比较版本"}
    assert client.post(tools, headers=READER, json={**question, "expected_sha256": "0" * 64}).status_code == 409
    with caplog.at_level("INFO", logger="gas.agent"):
        for name in ("compare", "lookup", "simulate", "report"):
            payload = question if name == "compare" else {**question, "tool": name, "selection": {**selection, "before": None},
                                                          "expected_sha256": r4["analysis"]["analysis_sha256"]}
            actual = client.post(tools, headers=READER, json=payload)
            assert actual.status_code == 200, actual.text
            receipt = actual.json()
            assert receipt["selection"] == payload["selection"]
            assert receipt["verified_sha256"] == payload["expected_sha256"]
            assert receipt["explanation_source"] == "qwen_evidence_selection"
            assert receipt["request_id"] in caplog.text
    assert client.get(one, headers=READER).json()["transition"]["revision"] == 4


def test_drill_rejects_registry_observation_count_conflict(scenario_resources):
    client, registry, update = scenario_resources
    registry["datasets"][0]["record_counts"]["observations"] = 19
    update()
    binding = lookup(client, "OWN-K001")["binding"]
    assert client.get(SCENARIOS + "/workspace", headers=READER, params=as_query(binding)).status_code == 503
    assert create_scenario(client).status_code == 503


def test_topic_navigation_keeps_chongqing_empty_and_reads_audited_demo_assets(resources):
    client, _, _ = resources
    binding = lookup(client, "OWN-K001")["binding"]
    geo = as_query(binding) | {"scope": "chongqing", "district_id": "500157"}
    route = PATH + "/navigation"
    result = client.get(route, params=geo, headers=READER)
    assert result.status_code == 200, result.text
    region = result.json()
    assert region["level"] == "district" and region["selection"]["district_id"] == "500157"
    assert region["source_status"] == "administrative_reference_only"
    assert not region["authorized_coordinates"]
    assert region["registered_assets"] == region["registered_events"] == 0
    assert region["field_completeness"]["ratio"] is None and region["field_completeness"]["checked"] == 0
    assert region["subnets"] == region["assets"] == region["events"] == []
    assert client.get(route, params=geo | {"district_id": "500112"}, headers=READER).status_code == 404
    assert client.get(route, params=geo | {"scope": "demo"}, headers=READER).status_code == 422
    assert client.get(route, params=geo | {"subnet_id": GAS}, headers=READER).status_code == 404
    assert client.get(route, params=geo, headers=DENIED).status_code == 403
    assert client.get(route, params=geo | {"version": "old"}, headers=READER).status_code == 409
    demo = as_query(binding) | {"scope": "demo"}
    catalog = client.get(route, params=demo, headers=READER).json()
    assert catalog["level"] == "demo_catalog" and not catalog["authorized_coordinates"]
    subnet = catalog["subnets"][0]
    assert subnet["subnet_id"] == GAS and subnet["registered_assets"] == 204
    result = client.get(route, params=demo | {"subnet_id": GAS, "asset_id": K001}, headers=READER)
    assert result.status_code == 200, result.text
    selected = result.json()
    assert selected["registered_events"] == 3
    assert selected["selection"]["district_id"] is None
    assert selected["selected_asset"]["fields"]["edge_id"] == "syn-gas-edge-K-001"


def test_quality_mapped_denominators_preparation_and_grounded_model(resources, monkeypatch):
    client, registry, update = resources
    registry["datasets"][0]["field_mappings"] = [
        {"raw_field": "network.pipes[].diameter_mm", "normalized_field": "diameter", "unit": "mm"},
        {"raw_field": "assets.csv:district_id", "normalized_field": "district_id", "unit": None},
        {"raw_field": "monitoring.csv:value WHERE metric=pressure", "normalized_field": "observation.value", "unit": "kPa"},
        {"raw_field": "unreviewed:unknown", "normalized_field": "unknown", "unit": None},
    ]
    update()
    binding = lookup(client, "OWN-K001")["binding"]
    reply = client.get(PATH + "/quality-workbench", params=as_query(binding), headers=READER)
    assert reply.status_code == 200, reply.text
    quality = reply.json()
    metrics = {row["id"]: row for row in quality["metrics"]}
    assert (metrics["completeness"]["valid"], metrics["completeness"]["checked"]) == (84, 86)
    assert quality["incomplete_mapping_fields"] == ["assets.csv:district_id"]
    assert quality["unsupported_mappings"] == ["unreviewed:unknown"]
    assert metrics["observations"]["valid"] == metrics["provenance"]["valid"] == 20
    assert metrics["linkage"]["valid"] == metrics["linkage"]["checked"]
    assert [row["code"] for row in quality["preparation"]] == [f"G{num:02d}" for num in range(1, 16)]
    assert quality["health_score"] == "unavailable"
    calls = []

    class Provider:
        calls = 1
        usage = {"total_tokens": 15}

        def explain(self, facts, *, question):
            calls.append(facts)
            return facts[1] + "\n不在事实合同里的推断。"

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: Provider()))
    body = {"binding": binding, "tool": "quality", "selection": None, "question": "报告质量指标"}
    assert client.post(PATH + "/topic-query", headers=READER, json=body | {"expected_sha256": "0" * 64}).status_code == 409
    assert not calls
    answer = client.post(PATH + "/topic-query", headers=READER, json=body | {"expected_sha256": quality["evidence_sha256"]})
    assert answer.status_code == 200
    assert answer.json()["explanation_source"] == "backend_facts"
    assert "不在事实合同里的推断" not in answer.json()["answer"]
    assert answer.json()["answer"].splitlines() == answer.json()["facts"]


def test_source_stress_uses_current_or_pinned_graph_without_ledger_changes(scenario_resources):
    client, _, _ = scenario_resources
    binding = lookup(client, "OWN-K001")["binding"]
    request = {"binding": binding, "scenario": None, "source_id": "syn-gas-source-N20"}
    route = SCENARIOS + "/source-stress"
    normal = client.post(route, headers=READER, json=request)
    assert normal.status_code == 200, normal.text
    assert len(normal.json()["after"]["reachable_node_ids"]) == 54
    assert normal.json()["alternate_source_id"] == "syn-gas-source-N38"
    assert normal.json()["alternate_source_path_edge_ids"]
    draft = create_scenario(client, asset="syn-gas-pipe-K-026", fault_kind="synthetic_dual_source_split").json()
    path = SCENARIOS + "/" + draft["scenario_id"]
    analyzed = analyze_scenario(client, path, 1)
    request["scenario"] = scenario_ref(analyzed)
    result = client.post(route, headers=READER, json=request)
    assert result.status_code == 200, result.text
    stress = result.json()
    assert len(stress["after"]["reachable_node_ids"]) == 52
    assert len(stress["before"]["components"]) == len(stress["after"]["components"]) == 2
    assert stress["newly_unreachable_node_ids"] == stress["single_source_dependencies"]["syn-gas-source-N20"]
    assert not stress["alternate_source_path_edge_ids"] and stress["alternate_source_id"] is None
    assert client.post(route, headers=READER, json=request).json() == stress
    assert client.post(route, headers=READER, json=request | {"source_id": "syn-gas-source-N99"}).status_code == 404
    assert client.post(route, headers=READER, json=request | {"scenario": request["scenario"] | {"revision": 1}}).status_code == 409
    assert client.post(route + "/explain", headers=READER, json={
        "selection": request, "expected_sha256": "0" * 64, "question": "查询图结果",
    }).status_code == 409
    assert client.get(path, headers=READER).json() == analyzed
