"""Long-term synthetic regressions for graph event replay into T01."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.decision.timeline import build_plan_service_timeline

ROOT = Path(__file__).resolve().parents[1]
TARGETS = ["syn-hospital-001", "syn-households-C", "syn-households-U"]


@pytest.fixture
def case():
    payload = json.loads((ROOT / "data/synthetic/s01.json").read_text())
    snap = {"snapshot_id": "snap-timeline", "payload": payload}
    req = {"snapshot_id": snap["snapshot_id"], "as_of": payload["as_of"],
           "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate", "horizon_hours": 8}
    return snap, req


def run(case, plan="syn-plan-combined", **kwargs):
    return build_plan_service_timeline(*case, plan, target_service_ids=kwargs.pop("targets", TARGETS), **kwargs)


def repair(case):
    return case[0]["payload"]["actions"][0]


def add_action(case, action_id, at, *, open_valves=None, close_valves=None, restore=None):
    action = {"action_id": action_id, "label": action_id, "fault_edge_ids": ["syn-fault"],
              "effective_after_hours": at, "evidence_ref": "synthetic:test event",
              "open_valve_ids": open_valves or [], "close_valve_ids": close_valves or [],
              "restore_edge_ids": restore or []}
    case[0]["payload"]["actions"].append(action)
    case[0]["payload"]["plans"][2]["action_ids"].append(action_id)
    return action


def test_s01_graph_restores_at_six_but_completion_unknown(case):
    before = deepcopy(case)
    out = run(case)
    assert out["status"] == "partial" and out["timeline_status"] == "succeeded"
    assert out["recovery_times"]["plan_completion_hours"]["status"] == "insufficient_data"
    assert out["recovery_times"]["first_target_service_restored_hours"]["value_hours"] == 6
    assert out["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 6
    assert all(v["value_hours"] == 6 for v in out["recovery_times"]["service_first_restored_hours"].values())
    assert out["ignored_topological_action_ids"] == ["syn-backup"]
    assert out["timeline_request"]["time_basis"] == "planned"
    assert case == before


def test_explicit_task_finishes_separate_from_graph_recovery(case):
    result = run(case, action_schedule=[{"action_id": "syn-repair", "finish_offset_hours": 6},
                                       {"action_id": "syn-backup", "start_offset_hours": 1, "duration_hours": 8}])
    assert result["status"] == "succeeded"
    times = result["recovery_times"]
    assert times["plan_completion_hours"]["value_hours"] == 9
    assert times["plan_completion_hours"]["beyond_horizon"] is True
    assert times["all_target_services_restored_hours"]["value_hours"] == 6


def test_backup_does_not_restore_graph_even_with_known_capacity(case):
    result = run(case, "syn-plan-backup")
    assert result["graph_events"] == []
    t = result["recovery_times"]["service_first_restored_hours"]["syn-hospital-001"]
    assert t["status"] == "not_recovered_within_horizon"
    assert t["value_hours"] is None and t["lower_bound_hours"] == 8


def test_preexisting_outage_is_not_misclassified_available(case):
    result = run(case, targets=TARGETS + ["syn-existing-outage", "syn-dual-inlet"])
    ts = result["recovery_times"]
    assert ts["service_first_restored_hours"]["syn-existing-outage"]["status"] == "not_recovered_within_horizon"
    assert ts["service_first_restored_hours"]["syn-dual-inlet"]["status"] == "already_available"
    assert ts["all_target_services_restored_hours"]["status"] == "not_recovered_within_horizon"


def test_repair_without_reopening_isolation_keeps_disconnected(case):
    repair(case)["open_valve_ids"] = []
    assert run(case)["recovery_times"]["all_target_services_restored_hours"]["status"] == "not_recovered_within_horizon"


def test_group_operations_atomic_and_input_order_invariant(case):
    repair(case)["open_valve_ids"] = []
    add_action(case, "syn-open", 6, open_valves=["syn-v1"])
    out = run(case)
    assert len(out["graph_events"]) == 1
    assert out["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 6
    case[0]["payload"]["plans"][2]["action_ids"].reverse()
    assert run(case)["timeline_request"]["availability_events"] == out["timeline_request"]["availability_events"]


def test_reoutage_is_included_in_events_and_first_not_final(case):
    repair(case)["effective_after_hours"] = 2
    add_action(case, "syn-close", 3, close_valves=["syn-v1"])
    add_action(case, "syn-reopen", 6, open_valves=["syn-v1"])
    result = run(case)
    assert [e["at_hours"] for e in result["timeline_request"]["availability_events"]] == [2, 3, 6]
    assert all(v is False for v in result["timeline_request"]["availability_events"][1]["changes"].values())
    assert result["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 2


def test_simultaneous_opposite_operations_rejected(case):
    add_action(case, "syn-close", 6, close_valves=["syn-v1"])
    assert run(case)["status"] == "invalid_input"


def test_same_action_opposite_operations_rejected(case):
    repair(case)["close_valve_ids"] = ["syn-v1"]
    assert run(case)["status"] == "invalid_input"


def test_horizon_inclusive_and_later_action_reported(case):
    repair(case)["effective_after_hours"] = 8
    assert run(case)["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 8
    repair(case)["effective_after_hours"] = 9
    result = run(case)
    assert result["beyond_horizon_action_ids"] == ["syn-repair"]
    assert result["recovery_times"]["all_target_services_restored_hours"]["status"] == "not_recovered_within_horizon"


def test_zero_offset_is_right_continuous_with_pre_state_preserved(case):
    repair(case)["effective_after_hours"] = 0
    out = run(case)
    assert all(v is False for v in out["pre_action_availability"].values())
    assert all(v is True for v in out["timeline_request"]["initial_availability"].values())
    assert out["recovery_times"]["all_target_services_restored_hours"]["status"] == "already_available"


@pytest.mark.parametrize("field,value", [("effective_after_hours", None), ("evidence_ref", " ")])
def test_missing_network_effect_inputs(case, field, value):
    repair(case)[field] = value
    assert run(case)["status"] == "insufficient_data"


@pytest.mark.parametrize("value", [True, "6", float("nan"), float("inf"), -1])
def test_invalid_effect_time(case, value):
    repair(case)["effective_after_hours"] = value
    assert run(case)["status"] == "invalid_input"


def test_unknown_operation_state_not_infeasible(case):
    # Use a different valve from the initial isolation, so the action's operability is tested.
    repair(case)["open_valve_ids"].append("syn-alternative")
    case[0]["payload"]["edges"][-1]["operable"] = None
    assert run(case)["status"] == "insufficient_data"
    case[0]["payload"]["edges"][-1]["operable"] = False
    assert run(case)["status"] == "infeasible"


def test_missing_counts_does_not_prevent_boolean_service_timeline(case):
    case[0]["payload"]["services"][0]["count_value"] = None
    out = run(case)
    assert out["timeline_status"] == "succeeded"
    assert out["recovery_times"]["all_target_services_restored_hours"]["value_hours"] == 6


def test_unknown_targets_duplicates_and_empty_targets(case):
    assert run(case, targets=["syn-missing"])["status"] == "invalid_input"
    assert run(case, targets=TARGETS + TARGETS)["status"] == "invalid_input"
    out = run(case, targets=[])
    assert out["recovery_times"]["all_target_services_restored_hours"]["status"] == "not_applicable"


def test_unknown_plan_wrong_snapshot_clock_and_missing_boundary(case):
    assert run(case, "syn-missing")["status"] == "invalid_input"
    case[1]["snapshot_id"] = "wrong"
    assert run(case)["status"] in {"invalid_input", "insufficient_data"}
    case[1]["snapshot_id"] = case[0]["snapshot_id"]
    case[0]["payload"]["nodes"][0]["boundary_confirmed"] = False
    assert run(case)["status"] == "insufficient_data"


def test_no_hydraulic_or_offline_time_invention(case):
    assert run(case, criterion_id="hydraulic_compliance")["status"] == "unsupported"
    case[0]["payload"]["plans"][2]["offline_effect"] = {
        "as_of": case[1]["as_of"], "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate",
        "horizon_hours": 8, "evidence_ref": "synthetic:offline", "remaining_disconnected_service_ids": []}
    assert run(case)["status"] == "unsupported"


def test_schedule_unknown_id_or_bool_rejected(case):
    assert run(case, action_schedule=[{"action_id": "syn-missing", "finish_offset_hours": 3}])["status"] == "invalid_input"
    assert run(case, action_schedule=[{"action_id": "syn-repair", "finish_offset_hours": True}])["status"] == "invalid_input"


def test_request_decision_candidate_and_target_baseline(case):
    case[1].update(plan_ids=["syn-plan-repair", "syn-plan-backup"], budget_fen=1, resource_capacities={})
    assert run(case)["status"] == "invalid_input"
    out = run(case, "syn-plan-repair")
    other = run(case, "syn-plan-repair", targets=[TARGETS[0]])
    assert out["baseline_key"] != other["baseline_key"]
    # This is conditional effect replay, not a claim that one cent can fund repairs.
    assert out["timeline_status"] == "succeeded"
