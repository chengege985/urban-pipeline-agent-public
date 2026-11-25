"""T01 synthetic timeline cases; no inference of physical service from topology."""
from copy import deepcopy

import pytest

from src.decision.recovery import compute_recovery_times


def sample():
    return dict(
        as_of="2026-10-29T08:00:00+08:00", horizon_hours=8,
        plan_id="syn-timing", action_ids=["repair", "backup"], time_basis="planned",
        action_schedule=[{"action_id": "repair", "finish_offset_hours": 6},
                         {"action_id": "backup", "start_offset_hours": 1, "duration_hours": 8}],
        target_service_ids=["hospital", "residents"], criterion_id="declared_service_availability",
        criterion_version="synthetic-v1", initial_availability={"hospital": False, "residents": False},
        availability_events=[{"at_hours": 1, "changes": {"hospital": True}},
                             {"at_hours": 6, "changes": {"residents": True}}],
        timeline_complete_until_hours=8, event_evidence_refs=["synthetic:explicit-service-assumptions"],
    )


def value(result, name):
    return result[name]["value_hours"]


def test_three_times_are_distinct_and_input_unchanged():
    data = sample()
    original = deepcopy(data)
    result = compute_recovery_times(data)
    assert result["status"] == "succeeded"
    assert value(result, "plan_completion_hours") == 9
    assert result["plan_completion_hours"]["beyond_horizon"] is True
    assert value(result, "first_target_service_restored_hours") == 1
    assert value(result, "all_target_services_restored_hours") == 6
    assert data == original


def test_all_restored_requires_simultaneous_availability():
    data = sample()
    data["availability_events"] = [
        {"at_hours": 2, "changes": {"hospital": True}},
        {"at_hours": 3, "changes": {"hospital": False}},
        {"at_hours": 4, "changes": {"residents": True}},
        {"at_hours": 6, "changes": {"hospital": True}},
    ]
    result = compute_recovery_times(data)
    assert result["service_first_restored_hours"]["hospital"]["value_hours"] == 2
    assert result["service_first_restored_hours"]["residents"]["value_hours"] == 4
    assert value(result, "all_target_services_restored_hours") == 6


def test_same_time_changes_are_atomic_not_order_dependent():
    data = sample()
    data["availability_events"] = [
        {"at_hours": 1, "changes": {"hospital": True}},
        {"at_hours": 2, "changes": {"residents": True}},
        {"at_hours": 2, "changes": {"hospital": False}},
    ]
    result = compute_recovery_times(data)
    assert result["all_target_services_restored_hours"]["status"] == "not_recovered_within_horizon"
    data["availability_events"].reverse()
    assert compute_recovery_times(data) == result


def test_partial_coverage_preserves_known_first_time():
    data = sample()
    data["timeline_complete_until_hours"] = 4
    result = compute_recovery_times(data)
    assert result["status"] == "partial"
    assert result["service_first_restored_hours"]["hospital"]["value_hours"] == 1
    assert result["all_target_services_restored_hours"]["status"] == "insufficient_data"


def test_complete_window_without_recovery_is_censored_not_missing():
    data = sample()
    data["availability_events"] = []
    result = compute_recovery_times(data)
    all_time = result["all_target_services_restored_hours"]
    assert all_time["value_hours"] is None
    assert all_time["status"] == "not_recovered_within_horizon"
    assert all_time["lower_bound_hours"] == 8
    assert all_time["lower_bound_exclusive"] is True


def test_horizon_inclusive_and_later_event_excluded():
    data = sample()
    data["availability_events"] = [{"at_hours": 8, "changes": {"hospital": True, "residents": True}}]
    assert value(compute_recovery_times(data), "all_target_services_restored_hours") == 8
    data["availability_events"][0]["at_hours"] = 9
    assert compute_recovery_times(data)["all_target_services_restored_hours"]["status"] == "not_recovered_within_horizon"


def test_missing_action_finish_does_not_invent_completion():
    data = sample()
    data["action_schedule"] = []
    result = compute_recovery_times(data)
    assert result["plan_completion_hours"]["status"] == "insufficient_data"
    assert value(result, "all_target_services_restored_hours") == 6


def test_initial_available_not_a_post_fault_restoration():
    data = sample()
    data["initial_availability"] = {"hospital": True, "residents": False}
    result = compute_recovery_times(data)
    assert result["service_first_restored_hours"]["hospital"]["status"] == "already_available"
    assert value(result, "first_target_service_restored_hours") == 6


def test_empty_targets_are_not_zero_hour_restoration():
    data = sample()
    data.update(target_service_ids=[], initial_availability={}, availability_events=[])
    result = compute_recovery_times(data)
    assert result["all_target_services_restored_hours"]["status"] == "not_applicable"
    assert value(result, "all_target_services_restored_hours") is None


@pytest.mark.parametrize("change", [
    {"target_service_ids": ["hospital", "hospital"]},
    {"availability_events": [{"at_hours": 2, "changes": {"other": True}}]},
    {"availability_events": [{"at_hours": 2, "changes": {"hospital": True}},
                             {"at_hours": 2, "changes": {"hospital": False}}]},
    {"availability_events": [{"at_hours": 0, "changes": {"hospital": True}}]},
    {"action_schedule": [{"action_id": "repair", "start_offset_hours": 1,
                          "duration_hours": 2, "finish_offset_hours": 8}]},
    {"horizon_hours": float("nan")},
    {"horizon_hours": True},
    {"action_schedule": [{"action_id": "repair", "start_offset_hours": 1e308, "duration_hours": 1e308}]},
])
def test_invalid_references_or_time_data_rejected(change):
    assert compute_recovery_times({**sample(), **change})["status"] == "invalid_input"


def test_unknown_initial_cannot_become_an_invented_first_recovery():
    data = sample()
    data["initial_availability"]["hospital"] = None
    result = compute_recovery_times(data)
    assert result["service_first_restored_hours"]["hospital"]["status"] == "insufficient_data"
    # Residents were definitely unavailable before 6; at 6 both are certainly available.
    assert value(result, "all_target_services_restored_hours") == 6


def test_no_evidence_or_coverage_cannot_assert_recovery():
    for change in [{"event_evidence_refs": []}, {"timeline_complete_until_hours": None}]:
        result = compute_recovery_times({**sample(), **change})
        assert result["all_target_services_restored_hours"]["status"] == "insufficient_data"


def test_hydraulics_is_not_implied_by_timeline():
    result = compute_recovery_times({**sample(), "criterion_id": "hydraulic_compliance"})
    assert result["status"] == "unsupported"
    assert result["all_target_services_restored_hours"] is None
