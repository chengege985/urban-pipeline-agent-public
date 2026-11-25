"""Malformed enum inputs must fail as controlled validation errors."""
from pathlib import Path
import copy
import json
import unittest

from waterpipnet_gas.network import evaluate_case, validate_network

ROOT = Path(__file__).resolve().parents[1]
NETWORK = json.loads((ROOT / "data/gas54_network.json").read_text(encoding="utf-8"))
CASE = json.loads((ROOT / "data/gas54_case.json").read_text(encoding="utf-8"))


class NetworkEnumTypeTests(unittest.TestCase):
    def test_network_enum_fields_reject_containers_null_and_nonstrings(self):
        fields = [
            ("nodes", "node_type"), ("edges", "initial_state"),
            ("valves", "initial_state"), ("consumers", "consumer_kind"),
            ("consumers", "count_unit"), ("services", "supply_rule"),
        ]
        for group, field in fields:
            for invalid in ([], {}, None, True, 0, ["open"]):
                with self.subTest(group=group, field=field, invalid=invalid):
                    network = copy.deepcopy(NETWORK)
                    network[group][0][field] = invalid
                    with self.assertRaises(ValueError):
                        validate_network(network)

    def test_event_kind_and_origin_reject_containers_null_and_nonstrings(self):
        for field in ("kind", "origin"):
            for invalid in ([], {}, None, True, 0, ["given_isolation"]):
                with self.subTest(field=field, invalid=invalid):
                    case = copy.deepcopy(CASE)
                    case["events"][1][field] = invalid
                    with self.assertRaises(ValueError):
                        evaluate_case(NETWORK, case)

    def test_valid_case_keeps_partial_null_and_unverified_supply(self):
        expected = json.loads((ROOT / "data/gas54_result.json").read_text(encoding="utf-8"))
        result = evaluate_case(NETWORK, CASE)
        self.assertEqual(result, expected)
        self.assertEqual(result["status"], "partial")
        self.assertIsNone(result["metrics"]["plan_completion_hours"]["value"])
        self.assertIsNone(result["metrics"]["actual_gas_restoration_hours"]["value"])
        self.assertTrue(all(service["physical_supply_status"] == "unknown"
                            for service in result["current"]["services"]))


if __name__ == "__main__":
    unittest.main()
