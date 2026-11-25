from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from src.gas_application.adapter import DEFAULT_FIXTURE_ROOT, load_case
from src.gas_application.cuv_replay import compare_cuv_replay
from src.gas_application.network import evaluate_case


@pytest.fixture
def inputs():
    network, case = load_case()
    scenario_path = Path(DEFAULT_FIXTURE_ROOT) / "gas_cuv_replay.json"
    return network, case, json.loads(scenario_path.read_text(encoding="utf-8"))


def test_plans_reuse_graph_impacts_and_compare_over_the_same_window(inputs):
    network, case, scenario = inputs
    report = compare_cuv_replay(network, case, scenario)
    assert report.data_mode == "synthetic"
    assert report.semantic == "graph_connectivity_only"
    assert report.validation_status == "not_business_validated"
    assert report.scenario_at == datetime(2026, 11, 18, 1, 12, tzinfo=UTC)
    assert report.fault_asset_ids == ["syn-gas-pipe-K-001"]
    assert report.analysis_window_hours == 8
    evidence = evaluate_case(network, case)["evidence"]
    assert report.topology_evidence.network_content_sha256 == evidence["network_content_sha256"]
    assert report.topology_evidence.case_content_sha256 == evidence["case_content_sha256"]
    assert len(report.topology_evidence.replay_content_sha256) == 64

    staged, hold = report.plans
    assert staged.status == hold.status == "replayed"
    assert staged.peak_affected_service_count == hold.peak_affected_service_count == 10
    assert (staged.affected_service_hours, hold.affected_service_hours) == (35, 37)
    assert (staged.topology_restoration_hours, hold.topology_restoration_hours) == (5, 4)
    assert staged.steps[:2] == hold.steps[:2]
    assert [step.elapsed_hours for step in staged.steps] == [0, 0.5, 2, 5]
    assert [step.elapsed_hours for step in hold.steps] == [0, 0.5, 4]
    assert len(staged.steps[0].affected_service_ids) == 4
    assert len(staged.steps[1].affected_service_ids) == 10
    step = staged.steps[2]
    assert step.at == datetime(2026, 11, 18, 3, 12, tzinfo=UTC)
    assert len(step.affected_service_ids) == 6
    assert step.affected_consumers.model_dump() == {"household": 727, "business": 69, "facility": 0}
    assert set(step.affected_node_ids) >= {"syn-gas-node-N00", "syn-gas-node-N01"}
    assert set(step.restored_service_ids) == set(staged.steps[1].affected_service_ids).difference(step.affected_service_ids)
    assert staged.steps[-1].affected_service_ids == []
    assert staged.steps[-1].restored_service_ids == step.affected_service_ids
    assert hold.steps[-1].at == datetime(2026, 11, 18, 5, 12, tzinfo=UTC)


def test_physical_metrics_and_authorization_remain_unknown_for_all_plans(inputs):
    report = compare_cuv_replay(*inputs)
    assert {metric.code for metric in report.unknown_metrics} == {
        "actual_gas_supply", "pressure", "flow", "physical_restoration_hours", "failure_probability", "operational_cost",
    }
    assert all(metric.status == "unknown" and metric.value is None and metric.reason for metric in report.unknown_metrics)
    assert "field_restoration_acceptance" in {item.code for item in report.missing_items}
    for plan in report.plans:
        for step in plan.steps:
            assert ("field_acceptance", "unknown") in {(item.code, item.status) for item in step.constraints}
        assert ("assumed_repair_and_release", "assumed") in {
            (item.code, item.status) for item in plan.steps[-1].constraints
        }


def test_staged_release_blocks_a_fault_endpoint_with_a_source_path(inputs):
    network, case, scenario = inputs
    scenario["plans"][0]["steps"][1]["disabled_edge_ids"] = ["syn-gas-edge-K-001"]
    report = compare_cuv_replay(network, case, scenario)
    blocked, hold = report.plans
    assert blocked.status == "blocked" and hold.status == "replayed"
    assert (blocked.topology_restoration_hours, blocked.peak_affected_service_count, blocked.affected_service_hours) == (
        None, None, None,
    )
    assert [step.kind for step in blocked.steps] == ["fault", "isolate", "staged_release"]
    state = blocked.steps[-1]
    assert state.status == "blocked"
    assert state.affected_service_ids is state.affected_node_ids is state.affected_consumers is None
    assert state.restored_service_ids is None
    assert ("fault_endpoints_graph_isolation", "blocked") in {(rule.code, rule.status) for rule in state.constraints}


@pytest.mark.parametrize(
    ("change_kind", "expected_rule"),
    [
        ("wrong_isolation_time", "given_isolation_state"),
        ("drop_fault", "progressive_graph_release"),
        ("clearance_keeps_cut", "assumed_repair_and_release"),
    ],
)
def test_graph_case_and_clearance_preconditions_block_inconsistent_steps(inputs, change_kind, expected_rule):
    network, case, scenario = inputs
    isolation, staged, clearance = scenario["plans"][0]["steps"]
    if change_kind == "wrong_isolation_time":
        isolation["elapsed_hours"] = 0.25
    elif change_kind == "drop_fault":
        staged["disabled_edge_ids"] = ["syn-gas-edge-K-003"]
    else:
        clearance["disabled_edge_ids"] = ["syn-gas-edge-K-003"]
    report = compare_cuv_replay(network, case, scenario)
    plan = report.plans[0]
    assert plan.status == "blocked"
    assert (expected_rule, "blocked") in {(rule.code, rule.status) for rule in plan.steps[-1].constraints}
    assert plan.steps[-1].affected_consumers is None
    assert plan.affected_service_hours is None


def test_no_clearance_leaves_full_graph_restoration_unknown(inputs):
    network, case, scenario = inputs
    scenario["plans"][1]["steps"].pop()
    hold = compare_cuv_replay(network, case, scenario).plans[1]
    assert hold.status == "replayed"
    assert hold.topology_restoration_hours is None
    assert hold.affected_service_hours == 77
    assert len(hold.steps[-1].affected_service_ids) == 10


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("data_mode", "real_authorized"),
        ("case_id", "syn-gas-case-unknown"),
        ("scenario_id", "syn-gas-scenario-unknown"),
        ("scenario_at", "2026-11-18T01:12:00"),
        ("scenario_at", 0),
    ],
)
def test_replay_requires_exact_synthetic_identity_and_explicit_zoned_time(inputs, field, value):
    network, case, scenario = inputs
    scenario[field] = value
    with pytest.raises(ValueError):
        compare_cuv_replay(network, case, scenario)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("elapsed_hours", 0.5),
        ("elapsed_hours", 9),
        ("elapsed_hours", True),
        ("disabled_edge_ids", ["syn-gas-edge-unknown"]),
        ("disabled_edge_ids", ["syn-gas-edge-K-001", "syn-gas-edge-K-001"]),
    ],
)
def test_replay_rejects_bad_schedule_references_and_time(inputs, field, value):
    network, case, scenario = inputs
    scenario["plans"][0]["steps"][1][field] = value
    with pytest.raises(ValueError):
        compare_cuv_replay(network, case, scenario)


def test_entire_plan_is_validated_even_if_an_earlier_step_would_block(inputs):
    network, case, scenario = inputs
    isolate, _, clearance = scenario["plans"][0]["steps"]
    isolate["elapsed_hours"] = 0.25
    clearance["elapsed_hours"] = 9
    with pytest.raises(ValueError, match="within the case window"):
        compare_cuv_replay(network, case, scenario)


def test_duplicate_plan_identity_cannot_be_compared(inputs):
    network, case, scenario = inputs
    scenario["plans"][1]["plan_id"] = scenario["plans"][0]["plan_id"]
    with pytest.raises(ValueError, match="identities must be unique"):
        compare_cuv_replay(network, case, scenario)
