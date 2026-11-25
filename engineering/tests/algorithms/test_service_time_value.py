"""Independent hand-calculated oracles and contract boundaries, retained regressions."""
import copy
import unittest
import json
from pathlib import Path

from src.algorithm_validation.run_time_value import cases, request, hydraulic_time_cases
from src.algorithm_validation.service_time_value import compare_backup_gain, evaluate_service_time_value


class TimeValueTests(unittest.TestCase):
    def value(self, req, metric="equivalent_shortfall_hours"):
        result = evaluate_service_time_value(req)
        self.assertEqual(result["status"], "succeeded", result)
        return result["metrics"][metric]["value"]

    def test_early_and_late_gain(self):
        base = request(demand=10)
        for start, end, expected_h, expected_m3 in [(1, 6, 5, 50), (7, 8, 0, 0)]:
            with self.subTest(start=start):
                result = compare_backup_gain(base, request(demand=10, backup_start=start, backup_end=end))
                self.assertEqual(result["status"], "succeeded")
                self.assertEqual(result["gains"]["equivalent_shortfall_hours"]["value"], expected_h)
                self.assertEqual(result["gains"]["unserved_volume_m3"]["value"], expected_m3)

    def test_partial_capacity_is_not_full_outage(self):
        r = request(demand=10, backup_start=1, backup_flow=4)
        r["sources"]["syn-water-truck"]["capacity_m3h"] = 4
        self.assertEqual(self.value(r), 4)
        self.assertEqual(self.value(r, "unserved_volume_m3"), 40)
        self.assertEqual(self.value(r, "requirement_unmet_hours"), 6)
        self.assertEqual(self.value(r, "complete_outage_hours"), 1)

    def test_repeated_interruption_is_integrated(self):
        case = cases()["repeated_outage"]
        result = compare_backup_gain(case["baseline"], case["request"])
        self.assertEqual(result["baseline"]["metrics"]["equivalent_shortfall_hours"]["value"], 5)
        self.assertEqual(result["candidate"]["metrics"]["equivalent_shortfall_hours"]["value"], 1)
        self.assertEqual(result["gains"]["unserved_volume_m3"]["value"], 16)

    def test_shared_resource_conflict(self):
        result = evaluate_service_time_value(cases()["resource_conflict"]["request"])
        self.assertEqual(result["status"], "infeasible")
        self.assertTrue(any("resource_conflict" in s for s in result["violations"]))
        self.assertFalse(result["metrics"])

    def test_shared_source_not_double_counted_and_capacity_not_reused(self):
        r = request(demand=10, backup_start=1, backup_flow=5)
        r["services"]["second"] = copy.deepcopy(r["services"]["syn-hospital-001"])
        for segment in r["segments"]:
            segment["network_m3h"]["second"] = segment["network_m3h"]["syn-hospital-001"]
            if segment["allocations"]:
                segment["allocations"].append({**segment["allocations"][0], "service_id": "second"})
        self.assertEqual(self.value(r), 7)
        self.assertEqual(self.value(r, "unserved_volume_m3"), 70)
        # Same physical source uses one truck, but total allocations cannot exceed its rate.
        r["segments"][1]["allocations"][1]["flow_m3h"] = 10
        result = evaluate_service_time_value(r)
        self.assertEqual(result["status"], "infeasible")
        self.assertTrue(any("capacity_exceeded" in x for x in result["violations"]))

    def test_finite_inventory_and_explicit_stop(self):
        r = request(demand=10, backup_start=1, backup_end=3)
        r["sources"]["syn-water-truck"]["volume_m3"] = 20
        self.assertEqual(self.value(r), 4)
        r["sources"]["syn-water-truck"]["volume_m3"] = 19
        self.assertEqual(evaluate_service_time_value(r)["status"], "infeasible")

    def test_supply_after_repair_has_no_gain_but_consumes_inventory(self):
        r = request(demand=10, backup_start=7, backup_end=8)
        result = evaluate_service_time_value(r)
        self.assertEqual(result["allocated_volume_m3"]["syn-water-truck"], 10)
        self.assertEqual(compare_backup_gain(request(demand=10), r)["gains"]["unserved_volume_m3"]["value"], 0)

    def test_negative_gain_is_preserved(self):
        case = cases()["negative_gain_late_vs_early"]
        self.assertEqual(compare_backup_gain(case["baseline"], case["request"])["gains"]["equivalent_shortfall_hours"]["value"], -5)

    def test_unknown_interval_bounds_are_not_zero_or_probability(self):
        result = evaluate_service_time_value(cases()["unknown_interval"]["request"])
        m = result["metrics"]["equivalent_shortfall_hours"]
        self.assertEqual((m["value"], m["lower"], m["upper"]), (None, 4, 6))
        self.assertEqual(result["status"], "insufficient_data")

    def test_gaps_are_unknown(self):
        r = request()
        r["segments"] = r["segments"][1:]
        result = evaluate_service_time_value(r)
        self.assertEqual(result["metrics"]["equivalent_shortfall_hours"]["upper"], 6)
        self.assertIsNone(result["metrics"]["equivalent_shortfall_hours"]["value"])

    def test_network_and_backup_are_independent_additive(self):
        r = request(demand=10, backup_start=1, backup_flow=5,
                    network_spans=[(0, 1, 0), (1, 6, 5), (6, 8, 10)])
        self.assertEqual(self.value(r), 1)
        self.assertEqual(self.value(r, "requirement_unmet_hours"), 1)

    def test_zero_demand_and_empty_targets(self):
        r = request(demand=0)
        self.assertEqual(self.value(r), 0)
        result = evaluate_service_time_value(r)
        self.assertIsNone(result["per_service"]["syn-hospital-001"]["timeline"][0]["ratio_lower"])
        r["services"] = {}
        for s in r["segments"]:
            s["network_m3h"] = {}
        self.assertEqual(evaluate_service_time_value(r)["status"], "not_applicable")

    def test_service_threshold_is_separate_from_deficit(self):
        r = request(demand=10, backup_start=1, backup_flow=4)
        r["services"]["syn-hospital-001"]["required_ratio"] = .4
        self.assertEqual(self.value(r), 4)
        self.assertEqual(self.value(r, "requirement_unmet_hours"), 1)

    def test_same_network_with_different_event_grid_is_comparable(self):
        a = request()
        b = copy.deepcopy(a)
        s = b["segments"].pop(0)
        b["segments"][0:0] = [{**s, "end_h": 2}, {**s, "start_h": 2}]
        result = compare_backup_gain(a, b)
        self.assertEqual(result["gains"]["equivalent_shortfall_hours"]["value"], 0)
        b["horizon_hours"] = 8.0
        for s in b["segments"]:
            s["start_h"], s["end_h"] = float(s["start_h"]), float(s["end_h"])
            s["network_m3h"] = {k: float(v) for k, v in s["network_m3h"].items()}
        self.assertEqual(compare_backup_gain(b, a)["comparison_basis"], result["comparison_basis"])

    def test_context_or_network_change_rejected_for_marginal_backup(self):
        a = request()
        for change in ("context", "network"):
            b = copy.deepcopy(a)
            if change == "context":
                b["context"]["snapshot_id"] = "other"
            else:
                b["segments"][0]["network_m3h"]["syn-hospital-001"] = 1
            self.assertEqual(compare_backup_gain(a, b)["reason"], "incompatible_counterfactual")

    def test_external_source_availability_is_part_of_common_basis(self):
        a = request()
        b = request(backup_start=1)
        b["sources"]["syn-water-truck"]["available_from_h"] = 1
        self.assertEqual(compare_backup_gain(a, b)["reason"], "incompatible_counterfactual")

    def test_unknown_capacity_and_stock_not_assumed_sufficient(self):
        for field in ("capacity_m3h", "volume_m3"):
            r = request(backup_start=1)
            r["sources"]["syn-water-truck"][field] = None
            result = evaluate_service_time_value(r)
            self.assertEqual(result["constraint_status"], "unknown")
            self.assertIsNone(result["metrics"]["unserved_volume_m3"]["value"])

    def test_missing_connection_or_evidence_not_counted_as_known_delivery(self):
        for field, value in (("connection_ready", None), ("evidence_ref", "")):
            r = request(backup_start=1)
            r["segments"][1]["allocations"][0][field] = value
            result = evaluate_service_time_value(r)
            self.assertIsNone(result["metrics"]["unserved_volume_m3"]["value"])
            if field == "connection_ready":
                self.assertEqual(result["constraint_status"], "unknown")

    def test_known_unavailable_source_or_connection_is_infeasible(self):
        r = request(backup_start=1)
        r["sources"]["syn-water-truck"]["available_from_h"] = 2
        self.assertEqual(evaluate_service_time_value(r)["status"], "infeasible")

    def test_unknown_backup_upper_bound_cannot_exceed_planned_flow(self):
        r = request(demand=10, backup_start=1, backup_flow=4)
        r["segments"][1]["allocations"][0]["connection_ready"] = None
        result = evaluate_service_time_value(r)
        m = result["metrics"]["equivalent_shortfall_hours"]
        self.assertEqual((m["lower"], m["upper"]), (4, 6))
        self.assertEqual(result["constraint_status"], "unknown")
        r = request(backup_start=1)
        r["segments"][1]["allocations"][0]["connection_ready"] = False
        self.assertEqual(evaluate_service_time_value(r)["status"], "infeasible")

    def test_bad_shapes_numbers_overlap_timezone(self):
        mutations = [lambda r: r.update(segments=[None]),
                     lambda r: r.update(resource_capacities=[]),
                     lambda r: r.update(horizon_hours=True),
                     lambda r: r.update(horizon_hours=float("inf")),
                     lambda r: r["segments"][1].update(start_h=5),
                     lambda r: r["context"].update(as_of="2026-11-01"),
                     lambda r: r["segments"][0].update(network_m3h=[])]
        for mutation in mutations:
            r = request()
            mutation(r)
            self.assertEqual(evaluate_service_time_value(r)["status"], "invalid_input")

    def test_real_data_and_hydraulic_claims_not_enabled(self):
        for field, value in (("data_mode", "real"), ("criterion", "qualified_water")):
            r = request()
            r[field] = value
            self.assertEqual(evaluate_service_time_value(r)["status"], "unsupported")

    def test_overflow_is_not_success(self):
        r = request(demand=1e308)
        self.assertEqual(evaluate_service_time_value(r)["status"], "invalid_input")

    def test_error_status_not_hidden_by_other_partial_input(self):
        a = cases()["unknown_interval"]["request"]
        b = cases()["resource_conflict"]["request"]
        self.assertEqual(compare_backup_gain(a, b)["status"], "infeasible")

    def test_supplied_objects_are_not_mutated(self):
        a = request(backup_start=1)
        before = copy.deepcopy(a)
        evaluate_service_time_value(a)
        self.assertEqual(a, before)

    def test_actual_hydraulic_fixture_does_not_become_full_service(self):
        path = Path(__file__).resolve().parents[2] / "experiments/algorithm_validation/time_value_v0_1/hydraulics/results.json"
        outputs = hydraulic_time_cases(json.loads(path.read_text(encoding="utf-8")))
        physical = outputs["hydraulic_backup_at_1h"]["result"]["metrics"]
        optimistic = outputs["topology_full_supply_assumption"]["result"]["metrics"]
        self.assertAlmostEqual(physical["equivalent_shortfall_hours"]["value"], 5.7327278456, places=6)
        self.assertEqual(physical["requirement_unmet_hours"]["value"], 6)
        self.assertEqual(physical["complete_outage_hours"]["value"], 1)
        self.assertEqual(optimistic["equivalent_shortfall_hours"]["value"], 1)


if __name__ == "__main__":
    unittest.main()
