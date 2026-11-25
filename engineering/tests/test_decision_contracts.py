"""Long-term D01-D04 regression using explicit synthetic fixtures only."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from src.decision.contracts import (build_decision_baseline, check_plan_constraints,
    evaluate_plan_effects, score_fixed_utility, rank_candidate_plans)
from src.decision.plans import compare_plans
from src.platform.schemas import DecisionRequest, SnapshotPayload


@pytest.fixture
def context():
    payload=json.loads((Path(__file__).parents[1]/"data/synthetic/s01.json").read_text())
    snap={"snapshot_id":"snap-contract","payload":SnapshotPayload.model_validate(payload).model_dump(mode="json"),"sha256":"untrusted"}
    req={"snapshot_id":snap["snapshot_id"],"as_of":payload["as_of"],"fault_edge_id":"syn-fault",
         "isolation_plan_id":"syn-isolate","horizon_hours":8,
         "plan_ids":["syn-plan-repair","syn-plan-backup","syn-plan-combined"],
         "budget_fen":400000,"resource_capacities":{"repair_team":1,"water_truck":1}}
    return snap,req


def test_original_scores_and_public_entry(context):
    snap,req=context
    before=deepcopy(snap)
    result=rank_candidate_plans(snap,req)
    assert result["recommended_plan_id"]=="syn-plan-combined"
    assert {r["plan_id"]:r["utility"] for r in result["plans"]}=={
        "syn-plan-repair":0.6375,"syn-plan-backup":0.42,"syn-plan-combined":0.9075}
    assert result["decision_algorithm_version"]=="1.0.0"
    assert compare_plans(snap,DecisionRequest.model_validate(req))==result
    assert snap==before


def test_known_budget_violation_dominates_missing_cost(context):
    snap,req=context
    snap["payload"]["actions"][1]["cost_fen"]=None
    req["budget_fen"]=200000
    result=check_plan_constraints(snap,req,"syn-plan-combined")
    assert result["status"]=="infeasible"
    assert result["total_cost_fen"] is None
    assert result["known_total_cost_fen"]==250000
    assert result["missing_items"] and result["violation_items"]
    assert next(c for c in result["checks"] if c["constraint_id"]=="budget")["state"]=="fail"


@pytest.mark.parametrize("resources,status",[(None,"insufficient_data"),({},"succeeded")])
def test_missing_vs_explicit_empty_resource_requirements(context,resources,status):
    snap,req=context
    snap["payload"]["actions"][1]["resource_requirements"]=resources
    assert check_plan_constraints(snap,req,"syn-plan-backup")["status"]==status


def test_missing_resource_capacity_is_unknown(context):
    snap,req=context;req["resource_capacities"]={}
    result=check_plan_constraints(snap,req,"syn-plan-repair")
    assert result["status"]=="insufficient_data"
    assert next(x for x in result["checks"] if x["constraint_id"]=="resource:repair_team")["right"] is None


@pytest.mark.parametrize("operable,status",[(None,"insufficient_data"),(False,"infeasible"),(True,"succeeded")])
def test_action_valve_operability_three_states(context,operable,status):
    snap,req=context
    snap["payload"]["actions"][0]["open_valve_ids"].append("syn-alternative")
    snap["payload"]["edges"][-1]["operable"]=operable
    assert check_plan_constraints(snap,req,"syn-plan-repair")["status"]==status


@pytest.mark.parametrize("change",["mandatory","dependency","conflict"])
def test_business_set_constraints(context,change):
    snap,req=context
    if change=="mandatory":req["mandatory_action_ids"]=["syn-backup"]
    elif change=="dependency":snap["payload"]["actions"][0]["requires_ids"]=["syn-backup"]
    else:snap["payload"]["actions"][0]["conflicts_with"]=["syn-repair"]
    assert check_plan_constraints(snap,req,"syn-plan-repair")["status"]=="infeasible"


def test_baseline_uses_actual_content_but_not_budget_or_candidate_order(context):
    snap,req=context
    key=build_decision_baseline(snap,req)["baseline_key"]
    req["budget_fen"]=5;req["plan_ids"].reverse();snap["sha256"]="another-untrusted-sha"
    assert build_decision_baseline(snap,req)["baseline_key"]==key
    snap["payload"]["services"][0]["node_ids"]=["syn-S"]
    assert build_decision_baseline(snap,req)["baseline_key"]!=key


def test_target_set_is_in_baseline(context):
    snap,req=context
    full=build_decision_baseline(snap,req)
    hospital=build_decision_baseline(snap,req,target_service_ids=["syn-hospital-001"])
    assert full["baseline_key"]!=hospital["baseline_key"]
    assert build_decision_baseline(snap,req,target_service_ids=["missing"])["status"]=="invalid_input"


def test_effects_do_not_subtract_backup_from_topology(context):
    snap,req=context
    backup=evaluate_plan_effects(snap,req,"syn-plan-backup")
    assert backup["metrics"]["remaining_disconnected_services"]==3
    assert backup["backup_covered_critical_service_ids"]==["syn-hospital-001"]
    assert backup["restored_service_ids"]==[]
    combined=evaluate_plan_effects(snap,req,"syn-plan-combined")
    assert len(combined["restored_service_ids"])==3
    assert combined["metrics"]["restoration_hours"]==6
    assert combined["restoration_hours_semantics"]=="legacy_last_network_effect"
    assert combined["baseline_key"]==backup["baseline_key"]


def test_repairs_do_not_implicitly_reopen_valves(context):
    snap,req=context;snap["payload"]["actions"][0]["open_valve_ids"]=[]
    result=evaluate_plan_effects(snap,req,"syn-plan-repair")
    assert result["metrics"]["remaining_disconnected_services"]==3
    assert result["metrics"]["restoration_hours"] is None


def test_newly_affected_services_are_reported(context):
    snap,req=context
    # A valid valve operation can harm an additional branch, including the dual inlet.
    snap["payload"]["edges"][0].update(is_valve=True,operable=True)
    snap["payload"]["services"][3]["node_ids"]=["syn-A"]
    snap["payload"]["actions"][0]["close_valve_ids"]=["syn-feed"]
    result=evaluate_plan_effects(snap,req,"syn-plan-repair")
    assert "syn-dual-inlet" in result["newly_affected_service_ids"]


def test_opposing_operations_require_explicit_sequence(context):
    snap,req=context;snap["payload"]["actions"][0]["close_valve_ids"]=["syn-v1"]
    result=evaluate_plan_effects(snap,req,"syn-plan-repair")
    assert result["status"]=="insufficient_data" and result["utility"] is None


def test_unknown_counts_preserve_known_record_metric_with_warning(context):
    snap,req=context;snap["payload"]["services"][1]["count_value"]=None
    result=rank_candidate_plans(snap,req)
    assert result["status"]=="succeeded"
    assert result["baseline_impact"]["potentially_disconnected_counts"]["household"] is None
    assert result["plans"][0]["warnings"]
    assert result["plans"][0]["coverage_scope"]=="registered_services_only"


def test_offline_results_need_matching_sidecar(context):
    snap,req=context
    snap["payload"]["plans"][1]["offline_effect"]={
        "as_of":req["as_of"],"fault_edge_id":req["fault_edge_id"],"isolation_plan_id":req["isolation_plan_id"],
        "horizon_hours":8,"evidence_ref":"synthetic:offline", "remaining_disconnected_service_ids":["syn-households-C"],
        "restoration_hours":None,"backup_covered_critical_service_ids":["syn-hospital-001"]}
    missing=evaluate_plan_effects(snap,req,"syn-plan-backup")
    assert missing["status"]=="insufficient_data"
    declaration={"baseline_key":build_decision_baseline(snap,req)["baseline_key"],"effect_model_version":"synthetic-provider-1",
        "metric_definition_version":"registered-service-metrics-1.0", "service_criterion_id":"topological_reachability",
        "evidence_ref":"synthetic:declared-matching-baseline"}
    result=evaluate_plan_effects(snap,req,"syn-plan-backup",offline_provenance=declaration)
    assert result["status"]=="succeeded"
    assert result["impact"]["engine_type"]=="offline_scenario"
    assert result["restoration_hours_semantics"]=="provider_declared_unverified"
    snap["payload"]["services"][0]["count_value"]=2
    assert evaluate_plan_effects(snap,req,"syn-plan-backup",offline_provenance=declaration)["status"]=="insufficient_data"


def test_fixed_utility_units_and_clip(context):
    snap,req=context
    profile={"profile_id":"syn-unit","version":"1", "evidence_ref":"synthetic:fixed-scale",
             "criteria":[{"metric":"total_cost_fen","direction":"min","lower":0,"upper":600000,"weight":1}]}
    result=score_fixed_utility({"total_cost_fen":900000},profile)
    assert result["utility"]==0 and result["warnings"]
    assert result["utility_breakdown"][0]["unit"]=="fen"
    assert result["utility_breakdown"][0]["clipped"] is True


def test_selected_missing_metric_never_reweights(context):
    snap,req=context
    metrics={"remaining_disconnected_services":0,"total_cost_fen":None,"backup_covered_critical_services":1}
    result=score_fixed_utility(metrics,snap["payload"]["value_profile"])
    assert result["status"]=="insufficient_data" and result["utility"] is None
    assert sum(x["contribution"] for x in result["utility_breakdown"])==pytest.approx(.85)


@pytest.mark.parametrize("bad",[True,"123",float("nan"),float("inf"),-1,1.5])
def test_bad_metric_types_and_units(context,bad):
    snap,req=context
    metrics={"remaining_disconnected_services":0,"total_cost_fen":bad,"backup_covered_critical_services":1}
    assert score_fixed_utility(metrics,snap["payload"]["value_profile"])["status"]=="invalid_input"


def test_invalid_scales_and_weights(context):
    snap,req=context;profile=snap["payload"]["value_profile"]
    profile["criteria"][0]["upper"]=profile["criteria"][0]["lower"]
    assert score_fixed_utility({},profile)["status"]=="invalid_input"
    profile["criteria"][0]["upper"]=3;profile["criteria"][0]["weight"]=.9
    assert score_fixed_utility({},profile)["status"]=="invalid_input"


def test_candidate_order_budget_and_tie_are_explicit(context):
    snap,req=context
    snap["payload"]["value_profile"]["criteria"]=[{"metric":"remaining_disconnected_services","direction":"min","lower":0,"upper":3,"weight":1}]
    req["plan_ids"].reverse()
    result=rank_candidate_plans(snap,req)
    assert result["tie_plan_ids"]==["syn-plan-combined","syn-plan-repair"]
    assert result["recommended_plan_id"]=="syn-plan-combined"
    req["budget_fen"]=300000
    result=rank_candidate_plans(snap,req)
    assert result["recommended_plan_id"]=="syn-plan-repair"
    assert next(r for r in result["plans"] if r["plan_id"]=="syn-plan-repair")["utility"]==1


def test_unknown_candidate_blocks_recommendation(context):
    snap,req=context;snap["payload"]["actions"][1]["cost_fen"]=None
    result=rank_candidate_plans(snap,req)
    assert result["status"]=="insufficient_data" and result["recommended_plan_id"] is None
    assert next(r for r in result["plans"] if r["plan_id"]=="syn-plan-repair")["utility"]==.6375


@pytest.mark.parametrize("path,value",[("horizon_hours",True),("budget_fen",1.5),("horizon_hours","8")])
def test_public_request_rejects_coercion(context,path,value):
    snap,req=context;req[path]=value
    assert rank_candidate_plans(snap,req)["status"]=="invalid_input"


def test_nonfinite_resource_sum_is_rejected(context):
    snap,req=context
    for a in snap["payload"]["actions"]:a["resource_requirements"]={"resource":1e308}
    req["resource_capacities"]={"resource":1e308}
    assert check_plan_constraints(snap,req,"syn-plan-combined")["status"]=="invalid_input"


@pytest.mark.parametrize("operable,status",[(False,"infeasible"),(None,"insufficient_data")])
def test_isolation_valve_operability_three_states(context,operable,status):
    snap,req=context;snap["payload"]["edges"][1]["operable"]=operable
    result=check_plan_constraints(snap,req,"syn-plan-backup")
    assert result["status"]==status
    check=next(c for c in result["checks"] if c["constraint_id"]=="isolation:operable:syn-v1")
    assert check["state"]==("fail" if operable is False else "unknown")


def test_plan_must_be_in_request_candidates(context):
    snap,req=context;req["plan_ids"]=["syn-plan-repair","syn-plan-backup"]
    assert check_plan_constraints(snap,req,"syn-plan-combined")["status"]=="invalid_input"
    assert evaluate_plan_effects(snap,req,"syn-plan-combined")["status"]=="invalid_input"


def test_ranking_preserves_invalid_input_from_candidate(context):
    snap,req=context
    for a in snap["payload"]["actions"]:a["resource_requirements"]={"resource":1e308}
    req["resource_capacities"]={"resource":1e308}
    result=rank_candidate_plans(snap,req)
    assert result["status"]=="invalid_input" and result["recommended_plan_id"] is None


def test_ranking_preserves_unsupported_scope(context):
    snap,req=context;snap["payload"]["actions"][0]["restore_edge_ids"].append("syn-feed")
    result=rank_candidate_plans(snap,req)
    assert result["status"]=="unsupported" and result["recommended_plan_id"] is None



def _offline(snap,req,remaining,restoration=None):
    snap["payload"]["plans"][1]["offline_effect"]={
        "as_of":req["as_of"],"fault_edge_id":req["fault_edge_id"],"isolation_plan_id":req["isolation_plan_id"],
        "horizon_hours":8,"evidence_ref":"synthetic:offline", "remaining_disconnected_service_ids":remaining,
        "restoration_hours":restoration,"backup_covered_critical_service_ids":[]}
    return {"baseline_key":build_decision_baseline(snap,req)["baseline_key"],"effect_model_version":"synthetic-provider-1",
            "metric_definition_version":"registered-service-metrics-1.0", "service_criterion_id":"topological_reachability",
            "evidence_ref":"synthetic:declared-baseline"}


def test_offline_existing_outage_is_not_newly_disconnected(context):
    snap,req=context
    declaration=_offline(snap,req,["syn-existing-outage"])
    result=evaluate_plan_effects(snap,req,"syn-plan-backup",offline_provenance=declaration)
    assert result["status"]=="invalid_input"


def test_offline_may_describe_new_damage_to_normally_available_service(context):
    snap,req=context
    declaration=_offline(snap,req,["syn-dual-inlet"])
    result=evaluate_plan_effects(snap,req,"syn-plan-backup",offline_provenance=declaration)
    assert result["status"]=="succeeded"
    assert result["newly_affected_service_ids"]==["syn-dual-inlet"]


def test_offline_time_cannot_mix_with_unverified_definition(context):
    snap,req=context;req["plan_ids"]=["syn-plan-repair","syn-plan-backup"]
    snap["payload"]["value_profile"]["criteria"]=[{"metric":"restoration_hours","direction":"min","lower":0,"upper":8,"weight":1}]
    declaration=_offline(snap,req,[],1)
    result=rank_candidate_plans(snap,req,offline_provenance={"syn-plan-backup":declaration})
    assert result["status"]=="insufficient_data" and result["recommended_plan_id"] is None
    row=next(r for r in result["plans"] if r["plan_id"]=="syn-plan-backup")
    assert row["metrics"]["restoration_hours"]==1 and row["utility"] is None
    declaration["restoration_hours_semantics"]="legacy_last_network_effect"
    accepted=rank_candidate_plans(snap,req,offline_provenance={"syn-plan-backup":declaration})
    assert accepted["status"]=="succeeded" and accepted["recommended_plan_id"]=="syn-plan-backup"
    assert next(r for r in accepted["plans"] if r["plan_id"]=="syn-plan-backup")["restoration_hours_semantics"]=="provider_declared_legacy_last_network_effect"
