"""PILOT-03 quality, trace and access regression using synthetic metadata and records."""

import csv
from hashlib import sha256
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from pilot02_fixture import DATASET, OBSERVATION, PIPE, PREFIX, SENSOR, build_synthetic_validation, seal, time_at
from pilot03_fixture import FLOW, FLOW_PIPE, REASON, SUSPECT, build_rejected, build_workbench
from src.gas_application.model_provider import LocalGasModel, MODEL_ID
from src.gas_application.pilot_cli import template
from src.gas_application.pilot_intake import inspect_pilot
from src.gas_application.pilot_store import PilotStore
from src.gas_application.standalone import create_gas_app

BASE = "/api/v1/gas/engineering/pilot/datasets"
V3 = f"{BASE}/{DATASET}/versions/demo-v3"
V2 = f"{BASE}/{DATASET}/versions/demo-v2"
READER = {"X-Water-Token": "pilot03-reader"}


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    origin = build_workbench(tmp_path / "source")
    old = build_synthetic_validation(tmp_path / "prior")
    other = build_synthetic_validation(tmp_path / "other")
    metadata = json.loads((other / "manifest.json").read_text())
    metadata["dataset_id"] = PREFIX + "other"
    seal(other, metadata)
    db = tmp_path / "isolated"
    db.mkdir(mode=0o700)
    store = PilotStore(db)
    for package in (old, other, origin, build_rejected(origin, tmp_path / "reject")):
        store.prepare(inspect_pilot(package))
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("GAS_PILOT_STORE", str(db))
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "pilot03-reader": {"actor_id": "qa-pilot", "modes": ["synthetic"], "datasets": [DATASET]},
        "other": {"actor_id": "qa-other", "modes": ["synthetic"], "datasets": [PREFIX + "other"]},
    }))
    monkeypatch.setattr(LocalGasModel, "from_environment", lambda: None)
    with TestClient(create_gas_app()) as http:
        yield http, store, origin


def request_body(entry, question, *, observation=OBSERVATION, threshold="pilot-r1",
                 start=time_at(0), end=time_at(22)):
    return {
        "question": question, "observation_id": observation,
        "binding": {"manifest_sha256": entry["manifest_sha256"], "threshold_version": threshold,
                    "window_start": start, "window_end": end},
    }


def test_quality_findings_and_receipts_are_scoped_and_sanitized(pilot):
    http, _, _ = pilot
    assert http.get(BASE).status_code == 401
    assert http.get(V3, headers={"X-Water-Token": "other"}).status_code == 403
    assert http.get(BASE, headers={"X-Water-Token": "other"}).json()["rejections"] == []
    data = http.get(BASE, headers=READER).json()
    assert {item["version"] for item in data["items"]} == {"demo-v2", "demo-v3"}
    rejected, = data["rejections"]
    assert rejected["version"] == "demo-v3-rejected"
    assert rejected["quality"]["missing_fields"] == [{"table": "observations", "fields": ["observed_at"]}]
    assert rejected["quality"]["issue_counts"]["error"] > 0
    assert "native_columns" not in json.dumps(rejected)
    declared = http.get(V3, headers=READER).json()
    assert declared["access"] == {"status": "synthetic", "valid_from": None, "valid_until": None,
                                  "asset_geometry": True, "local_qwen": True}
    assert declared["quality"]["issue_counts"] == {"error": 0, "warning": 5}
    assert {finding["code"] for finding in declared["quality"]["issues"]} >= {"quality_suspect", "quality_missing"}


def test_intake_integrity_failure_can_be_reported_without_source_values(pilot):
    http, store, origin = pilot
    (origin / "observations.csv").write_text("this is a synthetic invalid raw file")
    issue = inspect_pilot(origin)
    assert issue.snapshot is None
    assert store.prepare(issue)["status"] == "rejected"
    receipt = http.get(BASE, headers=READER).json()["rejections"][0]
    assert any(entry["code"] == "hash_mismatch" and entry["table"] == "observations"
               for entry in receipt["quality"]["issues"])
    assert "synthetic invalid raw file" not in json.dumps(receipt)


def test_pressure_threshold_windows_and_flow_geometry_absence(pilot):
    http, _, _ = pilot
    pressure = http.get(V3 + "/replay", params={"sensor_id": SENSOR}, headers=READER).json()
    assert [item["version"] for item in pressure["applied_thresholds"]] == ["pilot-r1", "pilot-r2"]
    assert pressure["applied_thresholds"][1]["effective_from"] == time_at(8)
    assert pressure["observations"][14]["observation"]["quality_reason"] == REASON
    # Effective policy stays visible even with zero on-schedule readings in an explicit sub-window.
    interval = http.get(V3 + "/replay", params={"sensor_id": SENSOR, "start": time_at(4, 1),
                        "end": time_at(4, 30)}, headers=READER).json()
    assert interval["segments"] == []
    assert [rule["version"] for rule in interval["applied_thresholds"]] == ["pilot-r1"]
    flow = http.get(V3 + "/replay", params={"sensor_id": FLOW}, headers=READER).json()
    assert flow["sensor"]["unit"] == "Nm3/h"
    assert flow["segments"][0]["channel"]["events"][0]["kind"] == "limit_exceeded"
    selected = http.get(V3 + "/pipes/" + FLOW_PIPE, params={"at": time_at(2)}, headers=READER).json()
    assert selected["geometry_status"] == "not_available" and selected["geometries_wgs84"] == []
    assert selected["pipe"]["from_node_id"] != selected["pipe"]["to_node_id"]


@pytest.mark.parametrize(("question", "focus", "observation", "threshold", "phrase"), [
    ("为什么这条压力记录触发了告警？", "alarm", OBSERVATION, "pilot-r1", "触发证据含 3 条原始观测"),
    ("这个测点当时使用哪个阈值版本？", "threshold", OBSERVATION, "pilot-r1", "该观测对应版本 pilot-r1"),
    ("为什么这条记录被标记为可疑？", "quality", SUSPECT, "pilot-r2", REASON),
    ("切换阈值版本后，哪些告警结果变化了？", "version_change", OBSERVATION, "pilot-r1", "连续越限换版去重 1 条"),
    ("当前管段关联哪些历史事件？", "asset_events", OBSERVATION, "pilot-r1", "有 2 条历史关联事件"),
])
def test_questions_return_grounded_facts_and_openable_references(pilot, monkeypatch, question, focus, observation, threshold, phrase):
    http, _, _ = pilot
    model_inputs = []

    def complete(request):
        prompt = json.loads(json.loads(request.content)["messages"][-1]["content"])
        model_inputs.append(prompt)
        return httpx.Response(200, json={"model": MODEL_ID, "choices": [{"message": {"content": '{"sentence_ids":[0]}'}}]})

    monkeypatch.setattr(LocalGasModel, "from_environment", lambda: LocalGasModel(
        "http://127.0.0.1:12901", "synthetic-test", transport=httpx.MockTransport(complete)))
    data = http.get(V3, headers=READER).json()
    response = http.post(V3 + "/agent/query", json=request_body(data, question, observation=observation, threshold=threshold),
                         headers=READER)
    assert response.status_code == 200
    result = response.json()
    assert result["focus"] == focus and result["asset_id"] == PIPE
    assert result["threshold_version"] == threshold and result["observation_id"] == observation
    assert result["model_state"] == "online" and result["model_calls"] == 1
    assert phrase in result["answer"]
    assert model_inputs[0]["question"] == question
    assert not any("syn-gas-pilot-other" in fact for fact in model_inputs[0]["evidence_sentences"])
    citations = result["citations"]
    assert any(item["kind"] == "asset" and item["record_id"] == PIPE for item in citations)
    if focus == "alarm":
        assert {item["record_id"] for item in citations if item["kind"] == "observation"} == {
            PREFIX + "obs-p1", PREFIX + "obs-p2", OBSERVATION,
        }
    if focus == "version_change":
        assert {item["threshold_version"] for item in citations if item["kind"] == "threshold"} == {"pilot-r1", "pilot-r2"}
        assert all("批准引用" in item["detail"] for item in citations if item["kind"] == "threshold")
    if focus == "asset_events":
        assert set(result["event_ids"]) == {PREFIX + "event-a", PREFIX + "event-b"}
        assert any("synthetic-ticket-002" in (item["detail"] or "") for item in citations)


def test_missing_reason_refusal_stale_context_and_model_timeout(pilot, monkeypatch):
    http, _, _ = pilot
    old = http.get(V2, headers=READER).json()
    query = request_body(old, "为什么这条记录被标记为可疑？", observation=SUSPECT, threshold="pilot-r2")
    unavailable = http.post(V2 + "/agent/query", json=query, headers=READER).json()
    assert unavailable["model_state"] == "unavailable"
    assert "证据不足以判断具体成因" in unavailable["answer"]
    assert http.post(V3 + "/agent/query", json=query, headers=READER).status_code == 409
    assert http.post(V2 + "/agent/query", json=query).status_code == 401
    assert http.post(V2 + "/agent/query", json=query, headers={"X-Water-Token": "other"}).status_code == 403
    invalid = request_body(old, "为什么触发告警？", start=time_at(5))
    assert http.post(V2 + "/agent/query", json=invalid, headers=READER).status_code == 409

    def timeout(request):
        raise httpx.ReadTimeout("synthetic timeout")

    monkeypatch.setattr(LocalGasModel, "from_environment", lambda: LocalGasModel(
        "http://127.0.0.1:12901", "synthetic", transport=httpx.MockTransport(timeout)))
    reply = http.post(V2 + "/agent/query", json=query, headers=READER).json()
    assert reply["model_state"] == "fallback" and reply["fallback_reason"] == "timeout"
    assert "证据不足" in reply["answer"]
    refusal = http.post(V2 + "/agent/query", json={**query, "question": "执行阀门控制"},
                        headers=READER).json()
    assert refusal["status"] == "unavailable" and refusal["citations"] == [] and refusal["model_calls"] == 0


def test_pilot_keeps_versioned_context_after_switch_and_blocks_other_sources(pilot, monkeypatch):
    http, _, _ = pilot
    current = http.get(V3, headers=READER).json()
    previous = http.get(V2, headers=READER).json()
    question = "这个测点当时使用哪个阈值版本？"
    v3_query = request_body(current, question)
    v2_query = request_body(previous, question, end=time_at(13))
    assert http.post(V2 + "/agent/query", headers=READER, json=v3_query).status_code == 409
    measured = http.post(V2 + "/agent/query", headers=READER, json=v2_query)
    assert measured.status_code == 200
    result = measured.json()
    assert result["evidence_scope"] == {
        "scope": "pilot_history", "dataset_id": DATASET, "dataset_version": "demo-v2",
        "data_mode": "synthetic", "source_sha256": previous["manifest_sha256"],
    }
    assert "demo-v3" not in result["answer"] and result["threshold_version"] == "pilot-r1"

    def model():
        pytest.fail("a rejected reference reached the model")
    monkeypatch.setattr(LocalGasModel, "from_environment", model)
    for question, reason in (
        ("引用 demo-v3 中刚才的观测", "reference_outside_scope"),
        ("这个观测能关联 GAS-M0 的两案吗？", "cross_dataset_unverified"),
        ("这个测点能推断现场供气恢复时长吗？", "engineering_evidence_unavailable"),
    ):
        answer = http.post(V2 + "/agent/query", headers=READER, json=v2_query | {"question": question}).json()
        assert answer["blocked_reason"] == reason and answer["status"] == "unavailable"
        assert answer["model_calls"] == 0 and answer["evidence"] == answer["citations"] == []


def test_minimum_template_has_only_required_input_tables_and_verified_digests(tmp_path):
    source = tmp_path / "minimum"
    template(source, "syn-gas-pilot-minimum", "intake-v1", "synthetic", minimum=True)
    manifest = json.loads((source / "manifest.json").read_text())
    assert set(manifest["files"]) == {"nodes", "pipes", "sensors", "observations"}
    for table, spec in manifest["files"].items():
        path = source / spec["filename"]
        assert sha256(path.read_bytes()).hexdigest() == spec["sha256"]
        with path.open(newline="", encoding="utf-8") as data:
            fields, = list(csv.reader(data))
        assert fields == list(spec["columns"].keys()) and fields[0] == "source_record_id"
    assert "quality_reason" not in manifest["files"]["observations"]["columns"]
    assert "region_ref" in manifest["files"]["pipes"]["columns"]
