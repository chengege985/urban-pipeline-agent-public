"""比赛端独立合成 API 回归；不依赖工程 .venv-agent 或工程数据。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.demo_engine import DEFAULT_CONSTRAINTS, evaluate_health, load_dataset
from server.llm_assistant import _execute_tool

client = TestClient(app)


def test_meta_and_dataset_are_synthetic_only():
    meta = client.get("/api/v1/demo/meta").json()
    assert meta["mode"] == "demo"
    assert meta["dataVersion"].startswith("synthetic-")
    assert meta["features"]["isolationPlans"] is True
    dataset = client.get("/api/v1/demo/dataset").json()
    assert len(dataset["pipes"]) == 24
    assert len(dataset["nodes"]) == 17
    assert len(dataset["scenarios"]) == 5
    assert len(dataset["isolationPlans"]) == 10
    assert all(pipe["id"].startswith("WP-") for pipe in dataset["pipes"])
    assert dataset["meta"]["mode"] == "live"


def test_python_demohealth_matches_frontend_baseline():
    dataset = load_dataset()
    for pipe in dataset["pipes"]:
        expected = dataset["assessments"][pipe["id"]]
        actual = evaluate_health(pipe)
        assert actual["status"] == expected["status"]
        assert actual["score"] == expected["score"], pipe["id"]
        assert actual["grade"] == expected["grade"], pipe["id"]
        assert actual["confidence"] == expected["confidence"], pipe["id"]
        assert actual["validWeight"] == expected["validWeight"], pipe["id"]
        assert actual["missing"] == expected["missing"], pipe["id"]
        for actual_factor, expected_factor in zip(actual["factors"], expected["factors"]):
            assert actual_factor["key"] == expected_factor["key"]
            assert actual_factor["score"] == expected_factor["score"]
            assert actual_factor["detail"] == expected_factor["detail"]
            assert actual_factor["contribution"] == pytest.approx(
                expected_factor["contribution"], abs=1e-5
            )


def test_simulate_matches_frontend_default_isolation():
    dataset = load_dataset()
    for scenario in dataset["scenarios"]:
        expected = dataset["impacts"][scenario["id"]]
        actual = client.post(
            "/api/v1/demo/simulate", json={"scenarioId": scenario["id"]}
        ).json()
        assert actual["scenarioId"] == expected["scenarioId"]
        assert actual["isolationPlanId"] == expected["isolationPlanId"]
        assert actual["affectedServices"] == expected["affectedServices"]
        assert actual["affectedCriticalServiceIds"] == expected["affectedCriticalServiceIds"]
        assert actual["affectedHouseholds"] == expected["affectedHouseholds"]
        assert actual["affectedFacilities"] == expected["affectedFacilities"]
        assert actual["unreachableNodeIds"] == expected["unreachableNodeIds"]


def test_expanded_isolation_increases_impact():
    minimal = client.post(
        "/api/v1/demo/simulate",
        json={"scenarioId": "SC-HOSP", "isolationPlanId": "ISO-HOSP-MIN"},
    ).json()
    expanded = client.post(
        "/api/v1/demo/simulate",
        json={"scenarioId": "SC-HOSP", "isolationPlanId": "ISO-HOSP-EXT"},
    ).json()
    assert expanded["affectedServices"] and len(expanded["affectedServices"]) > len(minimal["affectedServices"])
    assert "WP-013" in expanded["closedPipeIds"]
    assert "V-013A" in expanded["valveIds"]


def test_compare_respects_budget_and_dependencies():
    default = client.post(
        "/api/v1/demo/compare",
        json={"scenarioId": "SC-HOSP", "isolationPlanId": "ISO-HOSP-MIN"},
    ).json()
    assert default["recommendedPlanId"] == "PLAN-REPAIR"
    replace = next(row for row in default["rows"] if row["planId"] == "PLAN-REPLACE")
    assert replace["feasible"] is False
    assert any("施工许可" in item for item in replace["violations"])

    tight = client.post(
        "/api/v1/demo/compare",
        json={
            "scenarioId": "SC-HOSP",
            "constraints": {
                "budgetYuan": 5000,
                "resourceCapacities": {"repair_team": 1, "water_truck": 1, "monitoring_kit": 1},
                "confirmedDependencies": ["shutdown_window"],
            },
        },
    ).json()
    assert tight["recommendedPlanId"] is None
    assert all(row["feasible"] is False for row in tight["rows"])


def test_assistant_refuses_unsupported_and_uses_tools_for_supported():
    refused = client.post(
        "/api/v1/demo/assistant",
        json={"question": "预测未来三个月爆管概率", "context": {"pipeId": "WP-016"}},
    ).json()
    assert refused["refused"] is True
    assert refused["status"] == "refused"

    impact = client.post(
        "/api/v1/demo/assistant",
        json={"question": "如果这段爆管会影响哪些服务？", "context": {"scenarioId": "SC-HOSP"}},
    ).json()
    assert impact["status"] == "succeeded"
    assert impact["toolCalls"][0]["tool"] == "simulate_service_impact"
    assert impact["evidenceIds"]

    assessment = client.post(
        "/api/v1/demo/assistant",
        json={"question": "为什么这段管段优先检查？", "context": {"pipeId": "WP-016"}},
    ).json()
    assert assessment["toolCalls"][1]["tool"] == "evaluate_demo_health"
    assert "失效概率" in assessment["answer"]

    plans = client.post(
        "/api/v1/demo/assistant",
        json={"question": "有哪些维护方案可以比较？", "context": {"scenarioId": "SC-HOSP", "constraints": DEFAULT_CONSTRAINTS}},
    ).json()
    assert any(call["tool"] == "compare_demo_plans" for call in plans["toolCalls"])


def test_evaluate_accepts_what_if_override():
    baseline = client.post(
        "/api/v1/demo/evaluate", json={"pipeId": "WP-001"}
    ).json()
    degraded = client.post(
        "/api/v1/demo/evaluate",
        json={"pipeId": "WP-001", "override": {"leakCount": 8, "pressure": "高压"}},
    ).json()
    assert degraded["score"] < baseline["score"]
    assert degraded["status"] == "succeeded"


def test_competition_server_has_no_engineering_imports():
    server_root = Path(__file__).resolve().parents[1]
    for path in server_root.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("import ", "from ")):
                continue
            assert "01_工程项目" not in stripped, f"{path.name} 引用了工程目录"
            assert not stripped.startswith("from src."), f"{path.name} 引用了工程 src"


def test_real_model_tool_cannot_widen_plan_constraints():
    result = _execute_tool(
        load_dataset(),
        "compare_demo_plans",
        {
            "scenarioId": "SC-HOSP",
            "constraints": {
                "budgetYuan": 1000000,
                "resourceCapacities": {"repair_team": 9, "water_truck": 9, "monitoring_kit": 9},
                "confirmedDependencies": ["shutdown_window", "construction_permit"],
            },
        },
        {"scenarioId": "SC-HOSP", "constraints": DEFAULT_CONSTRAINTS},
    )
    assert result["ok"] is True
    assert result["comparison"]["constraints"]["budgetYuan"] == 40000
    replace = next(row for row in result["comparison"]["rows"] if row["planId"] == "PLAN-REPLACE")
    assert replace["feasible"] is False
