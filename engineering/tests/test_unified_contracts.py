"""UNIFY-PRODUCT-01 任务B：先用真实响应冻结 DTO，再记录尚未类型化的接口。

本文件不修改路由；它验证“统一合同层里的 DTO 与既有 HTTP 响应对齐”，
并把“仍是自由对象/尚未实现”的接口固化为显式断言，避免文档写成已完成后无人复核。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.openapi_export import build_openapi
from src.unified import VERSION
from src.unified.contracts import (
    AssessmentEnvelope,
    AssessmentRunResult,
    CapabilitiesResponse,
    DecisionRunResult,
    FeedbackListEnvelope,
    ReportDocument,
    RunView,
    SimulationRunResult,
    SnapshotListEnvelope,
    SnapshotSummary,
    parse_run_result,
)
from src.unified.report import ReportNotReady, build_report_from_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/synthetic/m1a_unified.json"
INPUTS = ROOT / "data/synthetic/m1a_unified_inputs.json"
HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "unified-contract"}
PIPE = "syn-pipe-fault"
FAULT = "syn-fault"
ISOLATION = "syn-isolate"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    for name in ("WATER_API_TOKEN", "WATER_PRINCIPALS", "AGENT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WATER_API_TOKEN", "unify-contract-synthetic-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    with TestClient(app, headers={"X-Water-Token": "unify-contract-synthetic-token"}) as instance:
        instance.app_state = app.state  # type: ignore[attr-defined]
        yield instance


def import_fixture(client) -> str:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    response = client.post("/api/v1/data-batches", json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()["snapshot_id"]


def run_sync(client, path: str, body: dict) -> dict:
    """入队后由测试显式领取一次，避免后台线程与文件锁。"""
    accepted = client.post(path, json=body, headers=HEADERS)
    assert accepted.status_code == 202, accepted.text
    client.app_state.runtime.run_one()
    return client.get(f"/api/v1/runs/{accepted.json()['run_id']}").json()


def scenario_body(snapshot_id: str, as_of: str) -> dict:
    return {"snapshot_id": snapshot_id, "as_of": as_of, "fault_edge_id": FAULT,
            "isolation_plan_id": ISOLATION, "horizon_hours": 8}


def recompute_body() -> dict:
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    return {"name": inputs["source"], "as_of": inputs["as_of"],
            "window_start": inputs["window_start"], "window_end": inputs["window_end"],
            "static_rows": inputs["static_rows"], "dynamic_rows": inputs["dynamic_rows"],
            "data_mode": "synthetic", "source_refs": ["data/synthetic/m1a_unified_inputs.json"]}


def test_unified_contract_version_is_pinned():
    assert VERSION == "1.0.0"


def test_existing_read_endpoints_conform_to_frozen_dtos(client):
    snapshot_id = import_fixture(client)

    snapshots = client.get("/api/v1/snapshots").json()
    parsed = SnapshotListEnvelope.model_validate(snapshots)
    assert [item.snapshot_id for item in parsed.items] == [snapshot_id]
    assert parsed.items[0].data_mode == "synthetic"
    assert parsed.items[0].counts.assessments == 6

    detail = client.get(f"/api/v1/snapshots/{snapshot_id}").json()
    summary = SnapshotSummary.model_validate({k: v for k, v in detail.items() if k != "payload"})
    assert summary.snapshot_id == snapshot_id
    assert len(detail["payload"]["assessments"]) == 6

    assessments = client.get("/api/v1/assessments", params={"snapshot_id": snapshot_id}).json()
    envelope = AssessmentEnvelope.model_validate(assessments)
    assert envelope.total == 6
    assert {row.pipe_id for row in envelope.items} == {
        "syn-pipe-fault", "syn-pipe-hospital", "syn-pipe-feed",
        "syn-pipe-residents", "syn-pipe-alternative", "syn-pipe-v1"}

    capabilities = CapabilitiesResponse.model_validate(client.get("/api/v1/capabilities").json())
    assert capabilities.platform_version == "0.1.1"
    assert capabilities.scoring == "legacy_demo_0.1.1"
    assert capabilities.formal_scoring == "unavailable"
    assert {item.capability_id for item in capabilities.algorithms} >= {
        "legacy_health", "given_isolation_topology", "candidate_plan_ranking", "plan_service_timeline"}
    assert capabilities.data_modes == ["synthetic"]


def test_run_envelopes_and_typed_results(client):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot_id = import_fixture(client)

    simulation = run_sync(client, "/api/v1/simulation-runs", scenario_body(snapshot_id, payload["as_of"]))
    assert RunView.model_validate(simulation).kind == "simulation"
    result = SimulationRunResult.model_validate(simulation["result"])
    assert result.engine_type == "topology"
    assert result.algorithm_version == "0.1.0"
    assert result.affected_service_ids == ["syn-hospital-001", "syn-households-C", "syn-households-U"]
    assert result.potentially_disconnected_counts == {"facility": 1, "household": 200, "person": 0}
    assert result.unknown_count_service_ids == []
    assert result.isolated_valve_ids == ["syn-v1"]
    assert result.validation_status == "not_business_validated"

    decision_body = {**scenario_body(snapshot_id, payload["as_of"]),
                     "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                     "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2},
                     "mandatory_action_ids": []}
    decision = run_sync(client, "/api/v1/decision-runs", decision_body)
    decision_result = DecisionRunResult.model_validate(decision["result"])
    assert decision_result.engine_type == "demo_policy"
    assert decision_result.recommended_plan_id is not None
    assert decision_result.recommended_plan_id in decision_result.ranked_plan_ids
    assert len(decision_result.plans) == 3
    assert all(row.utility is not None for row in decision_result.plans)
    assert parse_run_result(decision).__class__ is DecisionRunResult

    assessment = run_sync(client, "/api/v1/assessment-runs", recompute_body())
    assessment_result = AssessmentRunResult.model_validate(assessment["result"])
    assert assessment_result.model_family == "legacy_demo"
    assert assessment_result.assessment_count == 6
    assert assessment_result.snapshot_id.startswith("snap-")


def test_unknown_candidate_is_not_reported_as_infeasible(client):
    """资源容量未给出时平台返回 insufficient_data，不得当成“不可行”。"""
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot_id = import_fixture(client)
    body = {**scenario_body(snapshot_id, payload["as_of"]),
            "plan_ids": ["syn-plan-repair", "syn-plan-backup"],
            "budget_fen": 500000, "resource_capacities": {"repair_team": 2},
            "mandatory_action_ids": []}
    run = run_sync(client, "/api/v1/decision-runs", body)
    result = DecisionRunResult.model_validate(run["result"])
    statuses = {row.plan_id: row.status for row in result.plans}
    assert statuses["syn-plan-repair"] == "succeeded"
    assert statuses["syn-plan-backup"] == "insufficient_data"
    assert result.recommended_plan_id is None
    assert result.ranked_plan_ids == ["syn-plan-repair"]


def test_feedback_endpoint_matches_frozen_record(client):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot_id = import_fixture(client)
    body = {**scenario_body(snapshot_id, payload["as_of"]),
            "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
            "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2},
            "mandatory_action_ids": []}
    run = run_sync(client, "/api/v1/decision-runs", body)
    assert run["review_status"] == "pending_review"

    accepted = client.post("/api/v1/feedback", json={
        "run_id": run["run_id"], "review_status": "accepted", "reviewer": "unify-test",
        "reason_type": "explanation", "reason": "同一 Run 报告核对", "expected_revision": 0,
    }, headers=HEADERS)
    assert accepted.status_code == 201, accepted.text
    listed = FeedbackListEnvelope.model_validate(client.get("/api/v1/feedback", params={"run_id": run["run_id"]}).json())
    assert len(listed.items) == 1
    assert listed.items[0].revision == 1
    assert listed.items[0].execution_status == "not_dispatched"


def test_report_projection_reuses_run_values_without_recomputing(client):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot_id = import_fixture(client)
    body = {**scenario_body(snapshot_id, payload["as_of"]),
            "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
            "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2},
            "mandatory_action_ids": []}
    run = run_sync(client, "/api/v1/decision-runs", body)
    snapshot = client.get(f"/api/v1/snapshots/{snapshot_id}").json()
    view = client.get(f"/api/v1/datasets/{snapshot_id}/view").json()
    timeline = client.post("/api/v1/timeline/evaluate", json={
        "snapshot_id": snapshot_id, "fault_edge_id": FAULT, "isolation_plan_id": ISOLATION,
        "plan_id": "syn-plan-combined", "target_service_ids": ["syn-hospital-001"], "horizon_hours": 8,
    }, headers=HEADERS).json()

    report = build_report_from_run(run, snapshot, timeline=timeline["result"])
    assert isinstance(report, ReportDocument)
    assert report.recomputation == "none"
    assert report.source == "existing_run_only"
    assert report.run_id == run["run_id"]
    assert report.run_status == run["status"]
    assert report.dataset["data_version"] == snapshot["sha256"]
    assert report.dataset["as_of"] == snapshot["as_of"]

    # 报告中的决策数值与 Run 结果逐字段相同，报告不重排、不四舍五入。
    assert report.decision == run["result"]
    assert [row["plan_id"] for row in report.decision["plans"]] == [row["plan_id"] for row in run["result"]["plans"]]
    assert report.decision["recommended_plan_id"] == run["result"]["recommended_plan_id"]

    # 评分来自快照原文，与数据集视图一致，且标记未重算。
    pipe_view = next(item for item in view["pipes"] if item["pipe_id"] == PIPE)
    assert report.pipe is not None
    assert report.pipe.pipe_id == PIPE
    assert report.pipe.hs == pipe_view["score"]["hs"] == 78.5
    assert report.pipe.rs == pipe_view["score"]["rs"] == 1.86
    assert report.pipe.recomputed is False

    assert report.algorithms[0].capability_id == "candidate_plan_ranking"
    assert report.algorithms[0].version == run["result"]["decision_algorithm_version"] == "1.0.0"
    assert report.timeline["algorithm_version"] == "1.0.1"
    assert {"snapshot:" + snapshot_id, "run:" + run["run_id"]} <= set(report.evidence_ids)
    assert all(item.startswith(("snapshot:", "run:", "scenario:", "plan:", "isolation:"))
               or ":" in item for item in report.evidence_ids)

    # 同一 Run + 同一快照 + 同一版本 => 同一报告指纹；不同 Run 不允许复用指纹。
    again = build_report_from_run(run, snapshot, timeline=timeline["result"])
    assert again.report_id == report.report_id
    assert again.model_dump() == report.model_dump()


def test_report_refuses_unfinished_or_mismatched_runs(client):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    snapshot_id = import_fixture(client)
    snapshot = client.get(f"/api/v1/snapshots/{snapshot_id}").json()
    accepted = client.post("/api/v1/simulation-runs", json=scenario_body(snapshot_id, payload["as_of"]), headers=HEADERS)
    queued = client.get(f"/api/v1/runs/{accepted.json()['run_id']}").json()
    assert queued["status"] == "queued"
    with pytest.raises(ReportNotReady):
        build_report_from_run(queued, snapshot)

    client.app_state.runtime.run_one()
    finished = client.get(f"/api/v1/runs/{accepted.json()['run_id']}").json()
    other = {"snapshot_id": "snap-other", "sha256": "0" * 64, "created_at": "2026-01-01T00:00:00+00:00",
             "name": "x", "data_mode": "synthetic", "source_status": "constructed",
             "as_of": "2026-01-01T00:00:00+08:00", "validation_status": "not_business_validated",
             "counts": {"assessments": 0, "nodes": 0, "edges": 0, "services": 0, "plans": 0},
             "payload": {"schema_version": "0.1.0", "assessments": [], "edges": []}}
    with pytest.raises(ReportNotReady):
        build_report_from_run(finished, other)


def test_report_http_route_requires_an_existing_run(client):
    snapshot_id = import_fixture(client)
    response = client.get("/api/v1/runs/run-does-not-exist/report")
    assert response.status_code == 404
    assert client.get(f"/api/v1/snapshots/{snapshot_id}").status_code == 200


def test_openapi_typed_surface_includes_m1fix2_routes():
    schema = build_openapi()
    typed: dict[str, bool] = {}
    for path, operations in schema["paths"].items():
        for method, operation in operations.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            responses = operation.get("responses", {})
            content = next((item.get("content") for code, item in responses.items()
                            if code in {"200", "201", "202", "204"}), {}) or {}
            schema_ref = next(iter(content.values()), {}).get("schema", {}).get("$ref", "")
            typed[f"{method.upper()} {path}"] = "#/components/schemas/" in schema_ref
    # M1-FIX2 may add protected Agent evidence routes; the frozen paths remain.
    assert len(schema["paths"]) >= 25
    assert len(typed) >= 26  # /api/v1/feedback 同时提供 GET 与 POST
    assert typed["GET /api/v1/runs/{run_id}/report"] is True
    assert typed["GET /api/v1/runs/{run_id}"] is True
    assert typed["POST /api/v1/simulation-runs"] is True
    assert typed["POST /api/v1/decision-runs"] is True
    assert typed["POST /api/v1/assessment-runs"] is True
    assert typed["POST /api/v1/agent/chat"] is False
