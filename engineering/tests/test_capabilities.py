"""Long-lived G01 boundary tests; all input records are synthetic fixtures."""
from copy import deepcopy
from pathlib import Path
import json

import pytest

from src.capabilities import check_calculation_capability as check, list_calculation_capabilities


@pytest.fixture
def inputs():
    payload = json.loads((Path(__file__).resolve().parents[1] / "data/synthetic/s01.json").read_text())
    snapshot = {"snapshot_id": "snap-capability", "payload": payload}
    request = {"snapshot_id": snapshot["snapshot_id"], "as_of": payload["as_of"],
               "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate", "horizon_hours": 8}
    return {"snapshot": snapshot, "request": request}


def test_probability_cannot_use_health_score_or_invoke_engine(monkeypatch):
    monkeypatch.setattr("src.capabilities._dispatch", lambda *args: pytest.fail("unsupported must not execute"))
    r = check("failure_probability", ["failure_probability_1y"], "annual_failure_probability", {"Hs": 80})
    assert r["status"] == "unsupported" and not r["can_compute"]
    assert r["output_values"] == {"failure_probability_1y": None}


def test_mixed_outputs_do_not_compute_supported_subset_silently(monkeypatch):
    monkeypatch.setattr("src.capabilities._dispatch", lambda *args: pytest.fail("unsupported must not execute"))
    r = check("legacy_health", ["Hs", "H"], "legacy_demo_0.1.1", {})
    assert r["supported_outputs"] == ["Hs"] and r["unsupported_outputs"] == ["H"]
    assert not r["can_compute"]


def test_semantic_mismatch_rejected():
    assert check("legacy_health", ["Hs"], "physical_health", {})["status"] == "unsupported"


@pytest.mark.parametrize("outputs", [[], ["Hs", "Hs"], [1], [""], "Hs"])
def test_bad_output_selection(outputs):
    assert check("legacy_health", outputs, "legacy_demo_0.1.1", {})["status"] == "invalid_input"


def test_manifest_requires_actual_inputs_not_ready_flag():
    assert check("legacy_health", ["Hs"], "legacy_demo_0.1.1", {"ready": True})["status"] == "invalid_input"
    assert check("legacy_health", ["Hs"], "legacy_demo_0.1.1", {})["status"] == "insufficient_data"


def test_explanation_empty_scores_and_version():
    manifest = {"indicator_scores": {}, "model_version": "0.1.1"}
    assert check("legacy_health_explanation", ["reconciliation"], "legacy_demo_0.1.1", manifest)["status"] == "insufficient_data"
    manifest["model_version"] = "official"
    assert check("legacy_health_explanation", ["reconciliation"], "legacy_demo_0.1.1", manifest)["status"] == "unsupported"


def test_node_result_does_not_prove_service_mapping(inputs):
    inputs["snapshot"]["payload"]["services"] = []
    # Remove actions referencing services to keep this a legal node-only snapshot.
    inputs["snapshot"]["payload"]["actions"] = []
    inputs["snapshot"]["payload"]["plans"] = []
    node = check("given_isolation_topology", ["reachable_node_ids"], "topological_reachability", inputs)
    assert node["status"] == "succeeded" and node["calculation_status"] == "partial"
    service = check("given_isolation_topology", ["reachable_node_ids", "affected_service_ids"], "topological_reachability", inputs)
    assert service["status"] == "partial" and not service["can_compute"]
    assert service["available_outputs"] == ["reachable_node_ids"]


def test_wrong_clock_blocks(inputs):
    inputs["request"]["as_of"] = "2026-10-30T08:00:00+08:00"
    assert check("given_isolation_topology", ["affected_service_ids"], "topological_reachability", inputs)["status"] == "insufficient_data"


def test_valid_topology_check_does_not_mutate(inputs):
    before = deepcopy(inputs)
    r = check("given_isolation_topology", ["affected_service_ids"], "topological_reachability", inputs)
    assert r["status"] == "succeeded" and r["can_compute"] and inputs == before
    assert r["authorization_checked"] is False


def timeline_request():
    return {"as_of": "2026-10-29T08:00:00+08:00", "horizon_hours": 8,
            "plan_id": "p", "action_ids": ["a"], "target_service_ids": ["h"],
            "time_basis": "planned", "criterion_id": "topological_reachability", "criterion_version": "1",
            "initial_availability": {"h": False}, "availability_events": [{"at_hours": 6, "changes": {"h": True}}],
            "timeline_complete_until_hours": 8, "event_evidence_refs": ["synthetic:timeline"]}


def test_recovery_outputs_available_independently():
    manifest = {"recovery_request": timeline_request()}
    r = check("recovery_times", ["all_target_services_restored_hours"], "declared_event_timeline", manifest)
    assert r["can_compute"] and r["calculation_status"] == "partial"
    both = check("recovery_times", ["all_target_services_restored_hours", "plan_completion_hours"], "declared_event_timeline", manifest)
    assert both["status"] == "partial" and not both["can_compute"]


def test_known_non_recovery_is_a_computed_outcome():
    request = timeline_request()
    request["availability_events"] = []
    r = check("recovery_times", ["all_target_services_restored_hours"], "declared_event_timeline", {"recovery_request": request})
    assert r["status"] == "succeeded" and r["can_compute"]


def test_water_quality_recovery_unsupported():
    request = timeline_request()
    request["criterion_id"] = "hydraulic_compliance"
    r = check("recovery_times", ["all_target_services_restored_hours"], "declared_event_timeline", {"recovery_request": request})
    assert r["status"] == "unsupported" and not r["can_compute"]


def test_fixed_utility_and_no_missing_metric_renormalization(inputs):
    profile = inputs["snapshot"]["payload"]["value_profile"]
    manifest = {"metrics": {"remaining_disconnected_services": 0, "total_cost_fen": 370000,
                             "backup_covered_critical_services": 1}, "value_profile": profile}
    assert check("fixed_utility", ["utility"], "fixed_interval_weighted_utility", manifest)["can_compute"]
    manifest["metrics"]["total_cost_fen"] = None
    assert check("fixed_utility", ["utility"], "fixed_interval_weighted_utility", manifest)["status"] == "insufficient_data"


def test_infeasible_is_not_unknown(inputs):
    inputs["request"].update(plan_ids=["syn-plan-repair", "syn-plan-backup"], budget_fen=0,
                             resource_capacities={"repair_team": 1, "water_truck": 1})
    r = check("plan_constraints", ["feasibility"], "window_aggregate_constraints", {**inputs, "plan_id": "syn-plan-repair"})
    assert r["can_compute"] and r["calculation_status"] == "infeasible"


def test_registry_copy_cannot_change_capability():
    registry = list_calculation_capabilities()
    registry[0]["outputs"].append("H")
    assert "H" not in list_calculation_capabilities()[0]["outputs"]


def test_empty_recovery_request_is_missing_data_not_malformed():
    r = check("recovery_times", ["plan_completion_hours"], "declared_event_timeline", {"recovery_request": {}})
    assert r["status"] == "insufficient_data"


def test_zero_coverage_and_missing_list_are_known_without_scores():
    r = check("legacy_health_explanation", ["legacy_completeness_percent", "missing_indicators"], "legacy_demo_0.1.1",
              {"indicator_scores": {}, "model_version": "0.1.1"})
    assert r["can_compute"] and r["calculation_status"] == "insufficient_data"


@pytest.mark.parametrize("value", [True, "8", float("inf")])
def test_horizon_never_coerces_a_flag_or_text(inputs, value):
    inputs["request"]["horizon_hours"] = value
    assert check("given_isolation_topology", ["affected_service_ids"], "topological_reachability", inputs)["status"] == "invalid_input"


def test_service_id_status_is_not_a_metric_status_key():
    req = timeline_request()
    req.update(target_service_ids=["status"], initial_availability={"status": False},
               availability_events=[{"at_hours": 6, "changes": {"status": True}}])
    r = check("recovery_times", ["service_first_restored_hours"], "declared_event_timeline", {"recovery_request": req})
    assert r["can_compute"]


def test_generated_timeline_does_not_imply_all_task_completion_known(inputs):
    targets = [s["service_id"] for s in inputs["snapshot"]["payload"]["services"]]
    manifest = {**inputs, "plan_id": "syn-plan-combined", "target_service_ids": targets}
    r = check("plan_service_timeline", ["timeline_request"], "planned_topological_reachability", manifest)
    assert r["can_compute"]
    r = check("plan_service_timeline", ["timeline_request", "recovery_times"], "planned_topological_reachability", manifest)
    assert r["status"] == "partial" and r["available_outputs"] == ["timeline_request"]


@pytest.mark.parametrize("field,value", [("request", []), ("snapshot", []), ("payload", []), ("nodes", [1])])
def test_malformed_network_input_returns_status_not_exception(inputs, field, value):
    if field in {"request", "snapshot"}:
        inputs[field] = value
    elif field == "payload":
        inputs["snapshot"]["payload"] = value
    else:
        inputs["snapshot"]["payload"][field] = value
    assert check("given_isolation_topology", ["affected_service_ids"], "topological_reachability", inputs)["status"] == "invalid_input"
