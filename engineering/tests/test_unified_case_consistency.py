"""UNIFY-PRODUCT-01 任务D：统一六管段案例的四路径一致性。

四路径：

1. 页面：`GET /api/v1/datasets/{id}/view` 的评分投影（页面只读这一份数据）。
2. 后端：`/assessment-runs`、`/simulation-runs` 与 `/timeline/evaluate` 的正式算法结果。
3. Agent：`DomainTools` 受控确定性工具调用（query_assessment / calculate_health / simulate_outage）。
4. 报告：`build_report_from_run` 从既有 Run 组装，不重算。

不一致一律失败；未具备条件的入口（HTTP 报告接口、真实模型 Agent）另设断言标记 BLOCKED。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.app import create_app
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.schemas import DecisionRequest, RecomputeRequest, ScenarioRequest
from src.unified.case_consistency import (
    ImpactCase,
    PipeCase,
    TimelineCase,
    compare_pipe_cases,
)
from src.unified.report import build_report_from_run

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "data/synthetic/m1a_unified.json"
INPUTS = ROOT / "data/synthetic/m1a_unified_inputs.json"
HEADERS = {"X-Water-Client": "local-prototype", "Idempotency-Key": "unified-case"}
_SEQUENCE = iter(range(1, 10_000))
PIPE = "syn-pipe-fault"
FAULT = "syn-fault"
ISOLATION = "syn-isolate"
PLAN = "syn-plan-combined"
TARGET = "syn-hospital-001"


@pytest.fixture()
def platform(tmp_path, monkeypatch):
    for name in ("WATER_API_TOKEN", "WATER_PRINCIPALS", "AGENT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WATER_API_TOKEN", "unify-case-synthetic-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    app = create_app(tmp_path, start_worker=False, bootstrap=False)
    with TestClient(app, headers={"X-Water-Token": "unify-case-synthetic-token"}) as client:
        client.app_state = app.state  # type: ignore[attr-defined]
        client.root = tmp_path  # type: ignore[attr-defined]
        yield client


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def import_fixture(client) -> str:
    response = client.post("/api/v1/data-batches", json=load_fixture(), headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()["snapshot_id"]


def recompute_request() -> RecomputeRequest:
    inputs = json.loads(INPUTS.read_text(encoding="utf-8"))
    return RecomputeRequest(
        name=inputs["source"], as_of=inputs["as_of"], window_start=inputs["window_start"],
        window_end=inputs["window_end"], static_rows=inputs["static_rows"],
        dynamic_rows=inputs["dynamic_rows"], data_mode="synthetic",
        source_refs=["data/synthetic/m1a_unified_inputs.json"],
    )


def run_sync(client, path: str, body: dict) -> dict:
    """入队后由测试显式领取一次；每次请求使用独立幂等键（内容不同不得复用键）。"""
    headers = {"X-Water-Client": "local-prototype", "Idempotency-Key": f"unified-case-{next(_SEQUENCE)}"}
    accepted = client.post(path, json=body, headers=headers)
    assert accepted.status_code == 202, accepted.text
    client.app_state.runtime.run_one()
    return client.get(f"/api/v1/runs/{accepted.json()['run_id']}").json()


def scenario_body(snapshot_id: str, as_of: str) -> dict:
    return {"snapshot_id": snapshot_id, "as_of": as_of, "fault_edge_id": FAULT,
            "isolation_plan_id": ISOLATION, "horizon_hours": 8}


def decision_body(snapshot_id: str, as_of: str) -> dict:
    return {**scenario_body(snapshot_id, as_of),
            "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
            "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2},
            "mandatory_action_ids": []}


def timeline_body(snapshot_id: str) -> dict:
    return {"snapshot_id": snapshot_id, "fault_edge_id": FAULT, "isolation_plan_id": ISOLATION,
            "plan_id": PLAN, "target_service_ids": [TARGET], "horizon_hours": 8}


def tool(name: str, platform, snapshot_id: str, arguments: dict | None = None, **kwargs) -> dict:
    principal = BatchPrincipal(actor_id="unify-case", scope="internal")
    batches = InputBatchRepository(platform.root)
    access = ToolAccess(principal=principal, snapshot_id=snapshot_id, allowed_tools=frozenset({name}), **kwargs)
    tools = DomainTools(access, platform.app_state.store.get_snapshot, batches)
    return tools.execute(name, arguments or {})


def test_page_entry_reads_the_single_frozen_score(platform):
    """路径一：页面只读数据集视图；六条评分齐全且语义标注正确。"""
    snapshot_id = import_fixture(platform)
    view = platform.get(f"/api/v1/datasets/{snapshot_id}/view").json()
    assert view["status"] == "succeeded"
    assert view["assessment_coverage"]["status"] == "complete"
    assert view["assessment_coverage"]["coverage_ratio"] == 1.0
    page = {pipe["pipe_id"]: pipe for pipe in view["pipes"]}
    assert len(page) == 6
    assert page[PIPE]["score"]["semantics"] == "legacy_demo_0.1.1"
    assert page[PIPE]["score"]["hs"] == 78.5
    assert page[PIPE]["score"]["evidence_refs"] == [
        "controlled_recompute:pipe_health_0.1.1",
        "controlled_recompute:pipe_health_0.1.1#pipe=syn-pipe-fault",
    ]
    assert page[PIPE]["score_issue"] is None


def test_four_entries_agree_on_pipe_score(platform):
    snapshot_id = import_fixture(platform)
    fixture = {row["pipe_id"]: row for row in load_fixture()["assessments"]}

    # 路径一：页面视图
    view = platform.get(f"/api/v1/datasets/{snapshot_id}/view").json()
    page_case = PipeCase.from_dataset_view(next(p for p in view["pipes"] if p["pipe_id"] == PIPE))

    # 路径二：正式评分 Run（同一 0.1.1 函数、独立快照）
    assessed = run_sync(platform, "/api/v1/assessment-runs", recompute_request().model_dump(mode="json"))
    recomputed_snapshot = platform.get(f"/api/v1/snapshots/{assessed['result']['snapshot_id']}").json()
    backend_row = next(row for row in recomputed_snapshot["payload"]["assessments"] if row["pipe_id"] == PIPE)
    backend_case = PipeCase.from_assessment(backend_row)

    # 路径三：Agent 受控工具（只读取已授权快照，不重算）
    output = tool("query_assessment", platform, snapshot_id, arguments={"pipe_id": PIPE})
    assert output["status"] == "succeeded"
    assert output["evidence_id"].startswith("tool:")
    assert len(output["evidence_id"]) == len("tool:") + 64
    tool_case = PipeCase.from_tool(output, PIPE)

    # 路径四：报告引用既有 Run 与快照原文
    simulation = run_sync(platform, "/api/v1/simulation-runs", scenario_body(snapshot_id, load_fixture()["as_of"]))
    report = build_report_from_run(simulation, platform.get(f"/api/v1/snapshots/{snapshot_id}").json())
    assert report.pipe is not None
    report_case = PipeCase(
        pipe_id=report.pipe.pipe_id, model_family=report.pipe.model_family,
        model_version=report.pipe.model_version, rs=report.pipe.rs, hs=report.pipe.hs,
        grade=report.pipe.grade, completeness_percent=report.pipe.completeness_percent,
        evidence_refs=tuple(report.pipe.evidence_refs))

    comparison = compare_pipe_cases(
        ("page_dataset_view", page_case), ("backend_assessment_run", backend_case),
        ("agent_domain_tool", tool_case), ("report_from_run", report_case))
    assert comparison["consistent"] is True, comparison
    assert {entry["hs"] for entry in comparison["entries"]} == {78.5}
    assert {entry["model_version"] for entry in comparison["entries"]} == {"0.1.1"}

    # 与 fixture 文件本身（独立验证过的基准）一致；不是本轮程序互相印证。
    assert fixture[PIPE]["legacy_result"]["Hs"] == page_case.hs == 78.5
    assert fixture[PIPE]["legacy_result"]["Rs"] == page_case.rs == 1.86

    # 六条管段整体一致，避免只核对单条。
    for row in recomputed_snapshot["payload"]["assessments"]:
        assert row["legacy_result"]["Hs"] == fixture[row["pipe_id"]]["legacy_result"]["Hs"]


def test_agent_recompute_uses_same_declared_inputs(platform):
    """Agent 的 calculate_health 走同一 0.1.1 与同一登记批次，数值必须与 fixture 相同。"""
    snapshot_id = import_fixture(platform)
    principal = BatchPrincipal(actor_id="unify-case", scope="internal")
    batches = InputBatchRepository(platform.root)
    reference = batches.register(recompute_request(), principal)
    access = ToolAccess(principal=principal, snapshot_id=snapshot_id,
                        allowed_tools=frozenset({"calculate_health"}), batch_refs=frozenset({reference.batch_ref}))
    output = DomainTools(access, platform.app_state.store.get_snapshot, batches).execute(
        "calculate_health", {"batch_ref": reference.batch_ref})
    assert output["status"] == "succeeded"
    fixture = {row["pipe_id"]: row for row in load_fixture()["assessments"]}
    computed = {row["pipe_id"]: row["legacy_result"]["Hs"] for row in output["result"]["assessments"]}
    assert computed == {pipe_id: row["legacy_result"]["Hs"] for pipe_id, row in fixture.items()}
    assert output["result"]["model_version"] == "0.1.1"
    assert output["result"]["persisted"] is False


def test_agent_tool_refuses_without_authorization(platform):
    """工具不因同一快照存在就自动授权：未授予的工具与批次直接拒绝。"""
    snapshot_id = import_fixture(platform)
    principal = BatchPrincipal(actor_id="unify-case", scope="internal")
    access = ToolAccess(principal=principal, snapshot_id=snapshot_id, allowed_tools=frozenset({"query_assessment"}))
    tools = DomainTools(access, platform.app_state.store.get_snapshot, InputBatchRepository(platform.root))
    with pytest.raises(PermissionError):
        tools.execute("simulate_outage", {})
    with pytest.raises(PermissionError):
        tools.execute("calculate_health", {"batch_ref": "batch-" + "0" * 64})


def test_impact_and_timeline_agree_across_backend_and_agent(platform):
    snapshot_id = import_fixture(platform)
    fixture = load_fixture()
    simulation = run_sync(platform, "/api/v1/simulation-runs", scenario_body(snapshot_id, fixture["as_of"]))
    backend = ImpactCase.from_simulation_result(simulation["result"])
    assert backend.status == "succeeded"
    assert backend.affected_service_ids == ("syn-hospital-001", "syn-households-C", "syn-households-U")
    assert backend.counts == (("facility", 1), ("household", 200), ("person", 0))
    assert backend.snapshot_ref == "snapshot:" + snapshot_id

    principal = BatchPrincipal(actor_id="unify-case", scope="internal")
    access = ToolAccess(principal=principal, snapshot_id=snapshot_id,
                        allowed_tools=frozenset({"simulate_outage"}),
                        scenario=ScenarioRequest.model_validate(scenario_body(snapshot_id, fixture["as_of"])))
    output = DomainTools(access, platform.app_state.store.get_snapshot, InputBatchRepository(platform.root)).execute(
        "simulate_outage", {})
    assert output["status"] == "succeeded"
    agent = ImpactCase.from_simulation_result(output["result"])
    assert agent == backend

    # 决策路径的基准影响必须是同一拓扑结果，不能另算一套。
    decision = run_sync(platform, "/api/v1/decision-runs", decision_body(snapshot_id, fixture["as_of"]))
    baseline = ImpactCase.from_simulation_result(decision["result"]["baseline_impact"])
    assert baseline.affected_service_ids == backend.affected_service_ids
    assert baseline.counts == backend.counts

    # 时间线：任务完成时间未知（没有结束记录），服务恢复时间在窗口内有确定值。
    check = platform.post("/api/v1/timeline/check", json=timeline_body(snapshot_id), headers=HEADERS).json()
    evaluation = platform.post("/api/v1/timeline/evaluate", json=timeline_body(snapshot_id), headers=HEADERS).json()
    assert check["status"] == "partial"
    assert check["all_required_outputs_calculable"] is False
    availability = {item["field"]: item["status"] for item in check["field_availability"]}
    assert availability["plan_completion_hours"] == "missing"
    assert availability["all_target_services_restored_hours"] == "available"
    assert availability["plan_feasibility"] == "not_checked"

    case = TimelineCase.from_evaluation(evaluation)
    assert case.algorithm_version == "1.0.0"
    assert case.criterion_id == "topological_reachability"
    assert case.criterion_version == "1.0"
    assert case.plan_completion == ("insufficient_data", None)
    assert case.hospital_first == ("succeeded", 6.0)
    assert case.all_targets == ("succeeded", 6.0)
    assert "snapshot:" + snapshot_id in case.event_evidence


def test_decision_recommendation_comes_only_from_computed_rows(platform):
    snapshot_id = import_fixture(platform)
    fixture = load_fixture()
    full = run_sync(platform, "/api/v1/decision-runs", decision_body(snapshot_id, fixture["as_of"]))
    result = full["result"]
    assert result["status"] == "succeeded"
    assert result["recommended_plan_id"] in result["ranked_plan_ids"]
    assert set(result["ranked_plan_ids"]) == {row["plan_id"] for row in result["plans"]}
    by_plan = {row["plan_id"]: row for row in result["plans"]}
    assert by_plan["syn-plan-combined"]["metrics"]["remaining_disconnected_services"] == 0
    assert by_plan["syn-plan-combined"]["metrics"]["total_cost_fen"] == 370000
    assert by_plan["syn-plan-combined"]["utility"] is not None
    assert full["review_status"] == "pending_review"
    assert full["execution_status"] == "not_dispatched"

    # 缺少 water_truck 容量时，备用方案的结果是未知而不是不可行。
    partial_rows = {**decision_body(snapshot_id, fixture["as_of"]), "resource_capacities": {"repair_team": 2}}
    partial_run = run_sync(platform, "/api/v1/decision-runs", partial_rows)
    partial = partial_run["result"]
    statuses = {row["plan_id"]: row["status"] for row in partial["plans"]}
    assert statuses["syn-plan-backup"] == "insufficient_data"
    assert partial["recommended_plan_id"] is None

    # 报告不得把未知行改写成不可行，也不得补出推荐。
    report = build_report_from_run(
        partial_run,
        platform.get(f"/api/v1/snapshots/{snapshot_id}").json())
    assert report.decision["recommended_plan_id"] is None
    assert {row["plan_id"]: row["status"] for row in report.decision["plans"]} == statuses


def test_report_timeline_and_decision_share_one_run_identity(platform):
    snapshot_id = import_fixture(platform)
    fixture = load_fixture()
    decision = run_sync(platform, "/api/v1/decision-runs", decision_body(snapshot_id, fixture["as_of"]))
    snapshot = platform.get(f"/api/v1/snapshots/{snapshot_id}").json()
    evaluation = platform.post("/api/v1/timeline/evaluate", json=timeline_body(snapshot_id), headers=HEADERS).json()

    report = build_report_from_run(decision, snapshot, timeline=evaluation["result"])
    assert report.run_id == decision["run_id"]
    assert report.dataset["snapshot_id"] == snapshot_id
    assert report.dataset["data_version"] == snapshot["sha256"]
    assert report.decision == decision["result"]
    assert report.timeline == evaluation["result"]
    assert report.recomputation == "none"

    # 算法版本只来自本次实际调用，不写“最新版本”。
    versions = {item.capability_id: item.version for item in report.algorithms}
    assert versions == {"candidate_plan_ranking": "1.0.0"}
    assert report.scenario["fault_edge_id"] == FAULT
    assert report.pipe.pipe_id == PIPE
    assert report.pipe.hs == 78.5

    # 报告不重复解析 Run 结果，字段对象即 Run 结果的副本。
    assert report.decision["baseline_key"] == decision["result"]["baseline_key"]
    assert report.decision["plans"][0]["utility"] == decision["result"]["plans"][0]["utility"]


def test_agent_http_and_report_http_are_blocked_this_round(platform):
    """真实 Agent 与报告 HTTP 接口本轮不具备条件，明确标记 BLOCKED。"""
    import_fixture(platform)
    agent = platform.post("/api/v1/agent/chat", json={"snapshot_id": "x", "question": "评分"},
                          headers={**HEADERS, "Idempotency-Key": "unified-agent"})
    assert agent.status_code == 503 or agent.status_code == 401
    assert platform.get("/api/v1/agent/health").json()["enabled"] is False

    report = platform.get("/api/v1/runs/run-any/report")
    assert report.status_code == 404
