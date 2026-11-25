"""Long-term physics regression for the isolated candidate hydraulic benchmark."""
import unittest
import importlib.util

HAS_WNTR = importlib.util.find_spec("wntr") is not None
if HAS_WNTR:
    from src.algorithm_validation.hydraulic_baseline import CASES, pda_demand_m3s, run_case


@unittest.skipUnless(HAS_WNTR, "isolated hydraulic environment (WNTR) required; not a passed physics check")
class HydraulicBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {case[0]: run_case(*case) for case in CASES}

    def test_normal_is_satisfied_under_fixture_only(self):
        h = self.cases["normal_pda"]["targets"]["H"]
        self.assertTrue(h["graph_reachable"])
        self.assertTrue(h["fixture_hydraulic_satisfied"])
        self.assertIsNone(h["business_service_satisfied"])
        self.assertAlmostEqual(h["raw_delivered_m3h"], 7.2, delta=0.002)
        self.assertGreater(h["raw_pressure_head_m"], 20)

    def test_reachable_backup_is_not_hydraulic_service(self):
        h = self.cases["backup_pda"]["targets"]["H"]
        self.assertTrue(h["graph_reachable"])
        self.assertFalse(h["fixture_hydraulic_satisfied"])
        self.assertAlmostEqual(h["raw_delivered_m3h"], 0.38487, delta=0.002)
        self.assertLess(h["useful_supply_ratio"], 0.06)
        self.assertGreater(h["raw_pressure_head_m"], 0)
        self.assertLess(h["raw_pressure_head_m"], 0.1)

    def test_disconnected_does_not_turn_numerical_artifacts_into_supply(self):
        h = self.cases["disconnected_pda"]["targets"]["H"]
        self.assertFalse(h["graph_reachable"])
        self.assertFalse(h["fixture_hydraulic_satisfied"])
        self.assertEqual(h["useful_supply_m3h"], 0)
        self.assertEqual(h["status"], "no_source_path")
        self.assertIn("raw_pressure_head_m", h)

    def test_dda_is_diagnostic_not_delivered_service(self):
        case = self.cases["backup_dda"]
        h = case["targets"]["H"]
        self.assertEqual(case["solver"]["warning_code"], 6)
        self.assertLess(h["raw_pressure_head_m"], -300)
        self.assertAlmostEqual(h["raw_delivered_m3h"], 7.2)
        self.assertIsNone(h["fixture_hydraulic_satisfied"])
        self.assertIsNone(h["useful_supply_ratio"])

    def test_mass_balance_and_finite_solver_outputs(self):
        for case in self.cases.values():
            with self.subTest(case=case["case_id"]):
                self.assertEqual(case["solver"]["time_s"], 0)
                self.assertEqual(case["solver"]["native_version_code"], 20200)
                self.assertTrue(case["mass_balance"]["within_tolerance"])
                self.assertLessEqual(case["mass_balance"]["max_node_abs_residual_m3s"], 1e-6)

    def test_delivered_demand_matches_independent_pda_relation(self):
        for case_id in ("normal_pda", "backup_pda"):
            for target in self.cases[case_id]["targets"].values():
                self.assertLess(abs(target["pda_relation_residual_m3s"]), 1e-6)

    def test_pda_analytic_boundaries(self):
        self.assertEqual(pda_demand_m3s(0.002, -1), 0)
        self.assertEqual(pda_demand_m3s(0.002, 0), 0)
        self.assertEqual(pda_demand_m3s(0.002, 20), 0.002)
        self.assertEqual(pda_demand_m3s(0.002, 5), 0.001)
        with self.assertRaises(ValueError):
            pda_demand_m3s(0.002, float("nan"))


if __name__ == "__main__":
    unittest.main()
