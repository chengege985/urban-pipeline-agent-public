import copy
import json
import hashlib
import math
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from waterpipnet_gas.network import evaluate_case, validate_network


def nid(raw): return "syn-gas-node-" + raw
def eid(raw): return "syn-gas-edge-" + raw
def vid(raw): return "syn-gas-valve-" + raw


class GasNetworkContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original = json.loads((ROOT / "data/gas54_network.json").read_text(encoding="utf-8"))
        cls.original_case = json.loads((ROOT / "data/gas54_case.json").read_text(encoding="utf-8"))
        cls.probes = json.loads((ROOT / "evidence/network/prototype_topology_expectations.json").read_text(encoding="utf-8"))["cases"]

    def setUp(self):
        self.net = copy.deepcopy(self.original)
        self.case = copy.deepcopy(self.original_case)

    def result(self): return evaluate_case(self.net, self.case)

    def test_extracted_counts_and_distinct_id_spaces(self):
        result = validate_network(self.net)
        self.assertEqual(result["counts"], {"nodes":54, "edges":73, "pipes":73,
            "valves":31, "sources":2, "consumers":46, "services":46})
        self.assertEqual(result["isolated_node_ids"], [])
        self.assertEqual(len(self.net["isolation_units"]), 35)
        self.assertEqual(sum(c["count"] for c in self.net["consumers"] if c["count_unit"] == "household"), 5085)
        self.assertEqual(sum(c["count"] for c in self.net["consumers"] if c["count_unit"] == "business"), 406)

    def test_first_case_hand_enumerated_golden_values(self):
        result = self.result()
        current = result["current"]
        consumer_nodes = {s["node_ids"][0] for s in current["services"] if s["status"] == "newly_graph_unreachable"}
        self.assertEqual(consumer_nodes, {nid(x) for x in ["N00","N01","N02","N10","N12"]})
        self.assertEqual(current["potentially_interrupted_consumers"], {"household":182+228+215+317+388,
            "business":34+28+21+7+13, "facility":0})
        self.assertEqual(len(current["newly_affected_service_ids"]), 10)
        # N10 has only K-002 to N00; N00 reaches the remaining graph through K-001.
        # Cutting K-001 therefore disconnects this two-node branch before isolation.
        self.assertEqual(result["states"][0]["potentially_interrupted_consumers"],
                         {"household":182+317,"business":34+7,"facility":0})
        self.assertEqual(result["states"][0]["current_unreachable_node_ids"], [nid("N00"),nid("N10")])

    def test_topology_matches_original_js_for_all_73_fault_choices(self):
        units = {u["unit_id"]:u for u in self.net["isolation_units"]}
        node_type = {n["node_id"]:n["node_type"] for n in self.net["nodes"]}
        for probe in self.probes:
            with self.subTest(fault=probe["fault_edge_id"]):
                unit = units[probe["unit_id"]]
                case = copy.deepcopy(self.original_case)
                case["scenario"]["fault_edge_ids"] = [probe["fault_edge_id"]]
                case["isolation_plan"]["closed_valve_ids"] = unit["valve_ids"]
                case["isolation_plan"]["disabled_edge_ids"] = unit["edge_ids"]
                for event in case["events"]:
                    event["fault_edge_ids"] = [probe["fault_edge_id"]]
                    if event["kind"] != "given_fault":
                        event["closed_valve_ids"] = unit["valve_ids"]
                        event["disabled_edge_ids"] = unit["edge_ids"]
                cur = evaluate_case(self.net, case)["current"]
                # Original JS intentionally excludes valve/source nodes from its usersOf input.
                comparable = sorted(n for n in cur["current_unreachable_node_ids"] if node_type[n] not in {"valve","source"})
                self.assertEqual(comparable, sorted(probe["disconnected_consumer_node_ids"]))
                self.assertEqual(cur["potentially_interrupted_consumers"]["household"], probe["household_count"])
                self.assertEqual(cur["potentially_interrupted_consumers"]["business"], probe["business_count"])

    def test_node_cut_closes_all_explicit_incident_edges(self):
        cur = self.result()["current"]
        self.assertIn(nid("N11"), cur["current_unreachable_node_ids"])
        self.assertIn(nid("N13"), cur["current_unreachable_node_ids"])
        self.assertNotIn(vid("N11"), {e["edge_id"] for e in self.net["edges"]})

    def test_originally_disconnected_consumer_not_counted_as_new(self):
        self.net["baseline"]["disabled_edge_ids"] = [eid("K-001"), eid("K-002")]
        cur = self.result()["current"]
        self.assertIn(nid("N00"), cur["normal_unreachable_node_ids"])
        self.assertNotIn(nid("N00"), cur["newly_unreachable_node_ids"])
        self.assertEqual(cur["potentially_interrupted_consumers"]["household"], 1330-182-317)
        service = next(s for s in cur["services"] if s["service_id"] == "syn-gas-service-household-N00")
        self.assertEqual(service["status"], "already_graph_unreachable")

    def test_any_multi_access_keeps_service_reachable(self):
        self.net["services"][0]["node_ids"] = [nid("N00"), nid("N38")]
        self.net["services"][0]["supply_rule"] = "any"
        cur = self.result()["current"]
        self.assertEqual(cur["services"][0]["status"], "graph_reachable")
        self.assertEqual(cur["potentially_interrupted_consumers"]["household"], 1330-182)

    def test_all_multi_access_requires_all_connections(self):
        self.net["services"][0]["node_ids"] = [nid("N00"), nid("N38")]
        self.net["services"][0]["supply_rule"] = "all"
        self.assertEqual(self.result()["current"]["services"][0]["status"], "newly_graph_unreachable")

    def test_one_source_disabled_uses_other_source(self):
        self.net["baseline"]["disabled_source_ids"] = ["syn-gas-source-N20"]
        self.assertEqual(self.result()["states"][0]["normal_unreachable_node_ids"], [])

    def test_all_sources_disabled_is_existing_outage(self):
        self.net["baseline"]["disabled_source_ids"] = [s["source_id"] for s in self.net["sources"]]
        cur = self.result()["current"]
        self.assertEqual(len(cur["normal_unreachable_node_ids"]), 54)
        self.assertEqual(cur["newly_affected_service_ids"], [])
        self.assertTrue(all(s["status"] == "already_graph_unreachable" for s in cur["services"]))

    def test_facility_explicitly_attached_to_valve_is_retained(self):
        facility = next(s for s in self.net["services"] if s["service_id"] == "syn-gas-service-FAC-01")
        self.assertEqual(facility["node_ids"], [nid("N24")])
        self.net["baseline"]["closed_valve_ids"] = [vid("N24")]
        cur = self.result()["current"]
        found = next(s for s in cur["services"] if s["service_id"] == facility["service_id"])
        self.assertEqual(found["status"], "already_graph_unreachable")
        self.assertEqual(found["physical_supply_status"], "unknown")

    def test_isolated_node_is_diagnostic_not_invented_connection(self):
        self.net["nodes"].append({"node_id":nid("ISOLATED"),"node_type":"junction",
            "layout":{"x":10,"y":10,"unit":"px","coordinate_system":"schematic"}})
        check = validate_network(self.net)
        self.assertEqual(check["status"], "valid_with_warnings")
        self.assertEqual(check["isolated_node_ids"], [nid("ISOLATED")])
        self.assertIn(nid("ISOLATED"), self.result()["current"]["normal_unreachable_node_ids"])

    def test_duplicate_entity_id_rejected(self):
        self.net["nodes"].append(copy.deepcopy(self.net["nodes"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate node_id"): validate_network(self.net)

    def test_unknown_endpoint_rejected(self):
        self.net["edges"][0]["from_node_id"] = nid("MISSING")
        with self.assertRaisesRegex(ValueError, "endpoint"): validate_network(self.net)

    def test_pipe_edge_identity_conflict_rejected(self):
        self.net["pipes"][0]["edge_id"] = self.net["edges"][1]["edge_id"]
        with self.assertRaisesRegex(ValueError, "mapping mismatch"): validate_network(self.net)

    def test_valve_cannot_be_replaced_with_pipe_id(self):
        self.case["isolation_plan"]["closed_valve_ids"] = [self.net["pipes"][0]["pipe_id"]]
        with self.assertRaisesRegex(ValueError, "unknown IDs"): self.result()

    def test_valve_incidence_must_match_edges(self):
        self.net["valves"][0]["incident_edge_ids"] = []
        with self.assertRaisesRegex(ValueError, "incidence"): validate_network(self.net)

    def test_invalid_service_reference_and_empty_access_rejected(self):
        for value in [[nid("MISSING")], []]:
            with self.subTest(value=value):
                self.net = copy.deepcopy(self.original)
                self.net["services"][0]["node_ids"] = value
                with self.assertRaises(ValueError): validate_network(self.net)

    def test_unsupported_supply_rule_rejected(self):
        self.net["services"][0]["supply_rule"] = "nearest"
        with self.assertRaisesRegex(ValueError, "any/all"): validate_network(self.net)

    def test_service_access_cannot_silently_replace_consumer_node(self):
        self.net["services"][0]["node_ids"] = [nid("N38")]
        with self.assertRaisesRegex(ValueError, "explicit consumer node"): validate_network(self.net)

    def test_missing_or_invalid_provenance_digest_rejected(self):
        for value in [None, "", "a" * 63, "z" * 64]:
            with self.subTest(value=value):
                self.net = copy.deepcopy(self.original)
                self.net["source_provenance"]["sha256"] = value
                with self.assertRaisesRegex(ValueError, "SHA-256"): validate_network(self.net)

    def test_bool_nonfinite_and_string_coordinates_rejected(self):
        for value in [True, float("nan"), float("inf"), "12"]:
            with self.subTest(value=value):
                self.net = copy.deepcopy(self.original)
                self.net["nodes"][0]["layout"]["x"] = value
                with self.assertRaisesRegex(ValueError, "finite"): validate_network(self.net)

    def test_bool_negative_fractional_counts_rejected(self):
        for value in [True, -1, 1.5, None]:
            with self.subTest(value=value):
                self.net = copy.deepcopy(self.original)
                self.net["consumers"][0]["count"] = value
                with self.assertRaises(ValueError): validate_network(self.net)

    def test_source_state_cannot_be_truthy_text(self):
        self.net["sources"][0]["enabled"] = "false"
        with self.assertRaisesRegex(ValueError, "boolean"): validate_network(self.net)

    def test_nonsynthetic_or_physical_scope_rejected(self):
        for key,value in [("data_mode","real"),("data_mode","unknown"),("model_scope","physical_supply")]:
            with self.subTest(key=key,value=value):
                self.net = copy.deepcopy(self.original);self.net[key] = value
                with self.assertRaises(ValueError): validate_network(self.net)

    def test_case_network_and_plan_scenario_identity_conflict_rejected(self):
        self.case["network_id"] = "syn-gas-other-network"
        with self.assertRaisesRegex(ValueError, "identity"): self.result()
        self.case = copy.deepcopy(self.original_case)
        self.case["isolation_plan"]["scenario_id"] = "syn-gas-other-scenario"
        with self.assertRaisesRegex(ValueError, "scenario/semantics"): self.result()

    def test_event_conflicting_with_given_plan_rejected(self):
        self.case["events"][1]["closed_valve_ids"] = []
        with self.assertRaisesRegex(ValueError, "conflicts with given plan"): self.result()

    def test_event_times_reject_bool_nan_inf_negative_text(self):
        for value in [True,float("nan"),float("inf"),-0.1,"0.5"]:
            with self.subTest(value=value):
                self.case = copy.deepcopy(self.original_case)
                self.case["events"][1]["time_hours"] = value
                with self.assertRaises(ValueError): self.result()

    def test_unsorted_duplicate_and_outside_window_events_rejected(self):
        for value in [0,9]:
            with self.subTest(value=value):
                self.case = copy.deepcopy(self.original_case)
                self.case["events"][1]["time_hours"] = value
                with self.assertRaisesRegex(ValueError, "strictly increase"): self.result()

    def test_missing_task_end_does_not_use_last_event(self):
        r = self.result()
        self.assertEqual(r["status"], "partial")
        self.assertEqual(r["current"]["time_hours"], 1)
        self.assertEqual(r["metrics"]["plan_completion_hours"]["status"], "insufficient_data")
        self.assertIsNone(r["metrics"]["plan_completion_hours"]["value"])
        self.assertEqual(r["metrics"]["actual_gas_restoration_hours"]["status"], "unsupported")
        self.assertTrue(all(s["physical_supply_status"] == "unknown" for s in r["current"]["services"]))

    def test_task_end_is_only_explicit_input_not_restoration(self):
        self.case["task_end_hours"] = 9
        r = self.result()
        self.assertEqual(r["metrics"]["plan_completion_hours"]["value"], 9)
        self.assertFalse(r["metrics"]["plan_completion_hours"]["in_analysis_window"])
        self.assertIsNone(r["metrics"]["actual_gas_restoration_hours"]["value"])

    def test_task_end_before_isolation_rejected_exact_boundary_accepted(self):
        self.case["task_end_hours"] = 0
        with self.assertRaisesRegex(ValueError, "precedes last given_isolation"): self.result()
        self.case["task_end_hours"] = 0.5
        r = self.result()
        self.assertEqual(r["metrics"]["plan_completion_hours"]["value"], 0.5)
        self.assertTrue(r["metrics"]["plan_completion_hours"]["in_analysis_window"])
        self.assertEqual(r["current"]["time_hours"], 1)

    def test_giant_integer_is_controlled_validation_error(self):
        huge = 10 ** 1000
        self.case["events"][1]["time_hours"] = huge
        with self.assertRaisesRegex(ValueError, "finite numeric range"): self.result()
        self.case = copy.deepcopy(self.original_case)
        self.net["nodes"][0]["layout"]["x"] = huge
        with self.assertRaisesRegex(ValueError, "finite numeric range"): self.result()

    def test_demo_risk_parameters_do_not_change_connectivity(self):
        before = self.result()
        self.net["demo_parameters"]["isolation"]["default_valve_failure_fraction"] = 0.99
        self.net["demo_parameters"]["per_pipe"][0]["generated_P_score"] = 99
        after = self.result()
        self.assertEqual(before["current"], after["current"])
        self.assertNotEqual(before["evidence"]["network_content_sha256"], after["evidence"]["network_content_sha256"])

    def test_input_objects_not_mutated_and_result_does_not_alias_inputs(self):
        original_net, original_case = copy.deepcopy(self.net), copy.deepcopy(self.case)
        result = self.result()
        self.assertEqual(self.net, original_net); self.assertEqual(self.case, original_case)
        result["current"]["services"][0]["node_ids"].append("not-input")
        self.assertEqual(self.net, original_net)
        self.assertFalse(any(result["safety"][k] for k in ["field_operation_instruction","safe_restoration_decision","physical_supply_verified"]))


class GasExtractionTests(unittest.TestCase):
    def run_extract(self, output, source=None):
        command = ["node", str(ROOT / "tools/extract_gas_network.mjs"), "--output-root", str(output)]
        if source is not None:
            command.extend(["--source", str(source)])
        return subprocess.run(command, capture_output=True, text=True, encoding="utf-8", timeout=15)

    def test_pinned_extraction_reproduces_all_committed_fixture_bytes(self):
        with tempfile.TemporaryDirectory(prefix="extract-check-", dir=ROOT / "evidence/network") as directory:
            output = Path(directory).resolve()
            self.assertTrue(output.is_relative_to(ROOT / "evidence/network"))
            result = self.run_extract(output)
            self.assertEqual(result.returncode, 0, result.stderr)
            for relative in ["data/gas54_network.json", "data/gas54_case.json",
                             "evidence/network/prototype_topology_expectations.json"]:
                self.assertEqual((output / relative).read_bytes(), (ROOT / relative).read_bytes())

    def test_extractor_refuses_to_overwrite_existing_outputs(self):
        with tempfile.TemporaryDirectory(prefix="extract-protect-", dir=ROOT / "evidence/network") as directory:
            output = Path(directory).resolve()
            self.assertTrue(output.is_relative_to(ROOT / "evidence/network"))
            self.assertEqual(self.run_extract(output).returncode, 0)
            before = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in output.rglob("*") if p.is_file()}
            result = self.run_extract(output)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Output exists", result.stderr)
            after = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in output.rglob("*") if p.is_file()}
            self.assertEqual(before, after)

    def test_changed_original_hash_rejected_before_execution_or_output(self):
        with tempfile.TemporaryDirectory(prefix="extract-reject-", dir=ROOT / "evidence/network") as directory:
            temporary = Path(directory).resolve()
            self.assertTrue(temporary.is_relative_to(ROOT / "evidence/network"))
            source = ROOT.parents[1] / "燃气动态风险推演_前端示例原型_v1.html"
            altered = temporary / "changed.html"
            altered.write_bytes(source.read_bytes() + b"\n")
            output = temporary / "must-not-be-created"
            result = self.run_extract(output, altered)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Original HTML hash changed", result.stderr)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
