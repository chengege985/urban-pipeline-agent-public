"""M1-A 数据集视图与时间线接口回归。

黄金值来自数据文件和确定性函数契约，不是从当前程序输出反向填写。
所有写入均在 pytest 临时目录，不触碰 experiments/platform 或冻结输出。
"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.openapi_export import build_openapi
from src.platform.schemas import SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "m1a-test"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WATER_API_TOKEN", "m1a-synthetic-token")
    monkeypatch.delenv("WATER_PRINCIPALS", raising=False)
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    monkeypatch.delenv("WATER_ALLOW_REAL_DATASET", raising=False)
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    with TestClient(app, headers={"X-Water-Token": "m1a-synthetic-token"}) as instance:
        yield instance


def import_payload(client, payload):
    response = client.post("/api/v1/data-batches", json=payload, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()["snapshot_id"]


def s01(client):
    payload = json.loads((ROOT / "data/synthetic/s01.json").read_text(encoding="utf-8"))
    return import_payload(client, payload), payload


def timeline_request(snapshot_id, **updates):
    body = {
        "snapshot_id": snapshot_id,
        "fault_edge_id": "syn-fault",
        "isolation_plan_id": "syn-isolate",
        "plan_id": "syn-plan-combined",
        "target_service_ids": ["syn-hospital-001"],
        "horizon_hours": 8,
    }
    body.update(updates)
    return body


def test_dataset_view_reports_real_counts_and_availability(client):
    snapshot_id, payload = s01(client)
    response = client.get(f"/api/v1/datasets/{snapshot_id}/view")
    assert response.status_code == 200, response.text
    view = response.json()

    # Independent expectations: S01 has 8 nodes, 6 edges, 6 distinct pipe IDs,
    # 5 services, 2 actions, 3 plans and 1 isolation plan.
    assert view["status"] == "partial"
    assert len(view["nodes"]) == len(payload["nodes"]) == 8
    assert len(view["edges"]) == len(payload["edges"]) == 6
    assert len({edge["pipe_id"] for edge in payload["edges"]}) == 6
    assert len(view["pipes"]) == 6
    assert len(view["services"]) == 5
    assert len(view["actions"]) == 2
    assert len(view["plans"]) == 3
    assert len(view["isolation_plans"]) == 1

    assert view["meta"]["dataset_id"] == snapshot_id
    assert view["meta"]["data_mode"] == "synthetic"
    assert view["meta"]["source_status"] == "constructed"
    assert view["meta"]["as_of"] == payload["as_of"]
    assert view["meta"]["platform_version"] == "0.1.1"
    assert view["meta"]["display_coordinates_available"] is False
    assert view["assessment_coverage"]["status"] == "missing"
    assert len(view["assessment_coverage"]["missing_pipe_ids"]) == 6
    assert any("缺少评分记录" in item for item in view["missing_items"])
    assert "节点未提供显示坐标" in view["missing_items"]
    assert "只读投影" in view["limitations"][0]
    assert view["evidence_ids"] == ["snapshot:" + snapshot_id]

    algorithms = {item["capability_id"]: item for item in view["algorithms"]}
    assert algorithms["legacy_health"]["available"] is False
    assert algorithms["given_isolation_topology"]["available"] is True
    assert algorithms["candidate_plan_ranking"]["available"] is True
    assert algorithms["plan_service_timeline"]["available"] is True
    assert algorithms["plan_service_timeline"]["version"] == "1.0.1"
    assert algorithms["formal_health"]["available"] is False


def test_dataset_view_is_not_found_and_real_data_is_denied(client, monkeypatch):
    response = client.get("/api/v1/datasets/snap-missing/view")
    assert response.status_code == 404

    payload = json.loads((ROOT / "data/synthetic/s01.json").read_text(encoding="utf-8"))
    real = deepcopy(payload)
    real["data_mode"] = "real"
    real["source_status"] = "declared_real"
    real["source_refs"] = ["test:declared-real"]
    store = client.app.state.store
    snapshot_id = store.save_snapshot(SnapshotPayload.model_validate(real), "m1a-real")["snapshot_id"]

    denied = client.get(f"/api/v1/datasets/{snapshot_id}/view")
    assert denied.status_code == 403
    assert "无权" in denied.json()["detail"]

    forged = client.get(f"/api/v1/datasets/{snapshot_id}/view",
                        headers={"X-Water-Data-Scope": "authorized"})
    assert forged.status_code == 403

    monkeypatch.setenv("WATER_API_TOKEN", "secret")
    monkeypatch.setenv("WATER_DEV_MODES", "real")
    monkeypatch.setenv("WATER_DEV_DATASETS", snapshot_id)
    allowed = client.get(f"/api/v1/datasets/{snapshot_id}/view",
                         headers={"X-Water-Token": "secret"})
    assert allowed.status_code == 200
    assert allowed.json()["meta"]["data_mode"] == "real"


def test_capabilities_lists_datasets_and_algorithms(client):
    snapshot_id, _ = s01(client)
    body = client.get("/api/v1/capabilities").json()
    assert body["formal_scoring"] == "unavailable"
    assert body["platform_version"] == "0.1.1"
    assert [item["snapshot_id"] for item in body["datasets"]] == [snapshot_id]
    assert body["data_modes"] == ["synthetic"]
    algorithms = {item["capability_id"]: item for item in body["algorithms"]}
    assert algorithms["legacy_health"]["version"] == "0.1.1"
    assert algorithms["plan_service_timeline"]["semantics"] == "planned_topological_reachability"


def test_timeline_check_is_partial_with_known_and_missing_outputs(client):
    snapshot_id, _ = s01(client)
    response = client.post("/api/v1/timeline/check", json=timeline_request(snapshot_id), headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "partial"
    assert body["result"]["can_compute"] is False
    assert body["result"]["calculation_status"] == "partial"
    assert body["result"]["available_outputs"] == ["timeline_request"]
    assert "recovery_times" in body["result"]["unavailable_outputs"]
    assert body["result"]["registry_version"] == "1.0.1"
    assert len(body["dataset"]["data_version"]) == 64
    assert body["algorithm"]["capability_id"] == "plan_service_timeline"
    assert body["algorithm"]["version"] == "1.0.1"
    assert body["algorithm"]["semantics"] == "planned_topological_reachability"
    assert body["algorithm"]["validation_status"] == "not_business_validated"
    assert any("recovery_times" in str(item) for item in body["missing_items"])


def test_timeline_evaluate_separates_completion_from_service_restoration(client):
    snapshot_id, payload = s01(client)
    response = client.post("/api/v1/timeline/evaluate", json=timeline_request(snapshot_id), headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    recovery = body["result"]["recovery_times"]

    # syn-repair becomes effective at 6h; syn-backup has no graph action and must
    # not be counted as topological recovery. No task finish record is supplied.
    assert body["status"] == "partial"
    assert body["result"]["timeline_status"] == "succeeded"
    assert recovery["plan_completion_hours"]["status"] == "insufficient_data"
    assert recovery["plan_completion_hours"]["value_hours"] is None
    assert recovery["service_first_restored_hours"]["syn-hospital-001"]["value_hours"] == 6.0
    assert recovery["all_target_services_restored_hours"]["value_hours"] == 6.0
    assert recovery["all_target_services_restored_hours"]["value_hours"] <= 8
    assert body["result"]["ignored_topological_action_ids"] == ["syn-backup"]
    assert body["result"]["deferred_validation_issues"] == []
    assert body["result"]["plan_feasibility_checked"] is False

    assert body["dataset"]["snapshot_id"] == snapshot_id
    assert body["dataset"]["data_version"]
    assert body["dataset"]["schema_version"] == payload["schema_version"]
    assert body["algorithm"]["criterion_id"] == "topological_reachability"
    assert body["algorithm"]["criterion_version"] == "1.0"
    assert "snapshot:" + snapshot_id in body["evidence_ids"]
    assert "plan:syn-plan-combined" in body["evidence_ids"]


def test_timeline_evaluate_keeps_known_time_when_task_finish_is_added(client):
    snapshot_id, _ = s01(client)
    schedule = [
        {"action_id": "syn-repair", "finish_offset_hours": 9},
        {"action_id": "syn-backup", "finish_offset_hours": 9},
    ]
    body = client.post("/api/v1/timeline/evaluate",
                       json=timeline_request(snapshot_id, action_schedule=schedule), headers=HEADERS).json()
    recovery = body["result"]["recovery_times"]
    assert recovery["plan_completion_hours"]["value_hours"] == 9.0
    assert recovery["plan_completion_hours"]["beyond_horizon"] is True
    assert recovery["all_target_services_restored_hours"]["value_hours"] == 6.0
    assert body["result"]["deferred_validation_issues"] == []


def test_timeline_empty_targets_are_not_applicable(client):
    snapshot_id, _ = s01(client)
    body = client.post("/api/v1/timeline/evaluate",
                       json=timeline_request(snapshot_id, target_service_ids=[]), headers=HEADERS).json()
    metric = body["result"]["recovery_times"]["all_target_services_restored_hours"]
    assert metric["status"] == "not_applicable"
    assert metric["value_hours"] is None
    # With no targets there is no resolved time metric, so the T01 summary is
    # insufficient_data even though the empty target set is explicitly valid.
    assert body["status"] == "insufficient_data"
    assert body["result"]["recovery_times"]["first_target_service_restored_hours"]["status"] == "not_applicable"


def test_timeline_invalid_and_unsupported_inputs_are_explicit(client):
    snapshot_id, _ = s01(client)

    unknown_plan = client.post("/api/v1/timeline/evaluate",
                               json=timeline_request(snapshot_id, plan_id="syn-plan-unknown"),
                               headers=HEADERS)
    assert unknown_plan.status_code == 200
    assert unknown_plan.json()["status"] == "invalid_input"
    assert unknown_plan.json()["result"]["recovery_times"] is None
    assert unknown_plan.json()["missing_items"]

    unknown_target = client.post("/api/v1/timeline/evaluate",
                                 json=timeline_request(snapshot_id, target_service_ids=["syn-missing"]),
                                 headers=HEADERS)
    assert unknown_target.json()["status"] == "invalid_input"

    assert client.post("/api/v1/timeline/check",
                       json=timeline_request(snapshot_id, horizon_hours=0), headers=HEADERS).status_code == 422
    assert client.post("/api/v1/timeline/check",
                       json=timeline_request(snapshot_id, target_service_ids=["a", "a"]),
                       headers=HEADERS).status_code == 422
    assert client.post("/api/v1/timeline/check",
                       json=timeline_request(snapshot_id, unexpected=1), headers=HEADERS).status_code == 422
    assert client.post("/api/v1/timeline/evaluate",
                       json=timeline_request("snap-missing"), headers=HEADERS).status_code == 404


def test_timeline_results_are_deterministic(client):
    snapshot_id, _ = s01(client)
    request = timeline_request(snapshot_id)
    first = client.post("/api/v1/timeline/evaluate", json=request, headers=HEADERS).json()
    second = client.post("/api/v1/timeline/evaluate", json=request, headers=HEADERS).json()
    assert first == second

    check_first = client.post("/api/v1/timeline/check", json=request, headers=HEADERS).json()
    check_second = client.post("/api/v1/timeline/check", json=request, headers=HEADERS).json()
    assert check_first == check_second


def test_timeline_post_keeps_local_client_guard(client):
    snapshot_id, _ = s01(client)
    response = client.post("/api/v1/timeline/check", json=timeline_request(snapshot_id))
    assert response.status_code == 403
    assert "受控客户端标识" in response.json()["detail"]


def test_openapi_matches_response_fields(client):
    snapshot_id, _ = s01(client)
    models = build_openapi()["components"]["schemas"]
    view = client.get(f"/api/v1/datasets/{snapshot_id}/view").json()
    assert set(view) <= set(models["DatasetView"]["properties"])
    evaluation = client.post("/api/v1/timeline/evaluate",
                             json=timeline_request(snapshot_id), headers=HEADERS).json()
    assert set(evaluation) <= set(models["TimelineEvaluationResponse"]["properties"])
    assert set(evaluation["result"]) <= set(models["TimelineResult"]["properties"])


def test_openapi_contains_new_paths_and_models():
    schema = build_openapi()
    paths = schema["paths"]
    assert "/api/v1/datasets/{dataset_id}/view" in paths
    assert "/api/v1/timeline/check" in paths
    assert "/api/v1/timeline/evaluate" in paths
    models = schema["components"]["schemas"]
    for name in ("DatasetView", "DatasetMeta", "TimelineCheckResponse",
                 "TimelineEvaluationResponse", "TimeMetric", "RecoveryTimes"):
        assert name in models
    check_schema = paths["/api/v1/timeline/check"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert check_schema["$ref"].endswith("TimelineCheckResponse")
