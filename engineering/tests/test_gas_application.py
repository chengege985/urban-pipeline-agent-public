import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from src.gas_application.adapter import FixtureUnavailable, evaluate_fixture, fixture_root, load_case
from src.gas_application.contracts import GasCaseResponse
from src.gas_application.scoring import (
    LIKELIHOOD_VERSION,
    AGE_VERSION,
    NEW_C_VERSION,
    RISK_VERSION,
    compare_risk_definitions,
    consequence,
    calculate_service_age_ratio,
    evaluate_likelihood,
)


def _record(score, quality=1.0):
    return {"available": True, "score": score, "quality": quality,
            "unit": "score_0_100", "source_ref": "synthetic:test-vector"}


class GasApplicationTests(unittest.TestCase):
    def test_fixture_is_computed_by_adapter_and_keeps_physical_values_unknown(self):
        value = evaluate_fixture()
        self.assertEqual(value["counts"], {"nodes": 54, "edges": 73, "pipes": 73,
                         "valves": 31, "sources": 2, "consumers": 46, "services": 46})
        self.assertEqual(value["status"], "partial")
        self.assertEqual(len(value["current"]["newly_affected_service_ids"]), 10)
        self.assertEqual(value["metrics"]["actual_gas_restoration_hours"]["status"], "unsupported")
        self.assertIsNone(value["metrics"]["actual_gas_restoration_hours"]["value"])
        self.assertEqual(value["scoring"]["status"], "unavailable")
        self.assertEqual(value["scoring"]["business_validation"], "not_business_validated")

    def test_adapter_result_hashes_are_for_the_actual_input_objects(self):
        value = evaluate_fixture()
        root = __import__("src.gas_application.adapter", fromlist=["fixture_root"]).fixture_root()
        network = json.loads((root / "gas54_network.json").read_text(encoding="utf-8"))
        case = json.loads((root / "gas54_case.json").read_text(encoding="utf-8"))
        canonical = lambda obj: json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(value["evidence"]["network_content_sha256"], hashlib.sha256(canonical(network).encode()).hexdigest())
        self.assertEqual(value["evidence"]["case_content_sha256"], hashlib.sha256(canonical(case).encode()).hexdigest())

    def test_candidate_score_requires_mandatory_evidence_and_preserves_partial(self):
        result = evaluate_likelihood({"G01": _record(90), "G02": _record(70), "G04": _record(50), "G05": _record(10, .8)},
                                     data_mode="synthetic", rule_version=LIKELIHOOD_VERSION)
        self.assertEqual(result["status"], "partial")
        self.assertIsNotNone(result["P"])
        self.assertFalse(result["is_failure_probability"])
        missing = evaluate_likelihood({"G01": _record(90), "G04": _record(50)},
                                      data_mode="synthetic", rule_version=LIKELIHOOD_VERSION)
        self.assertEqual(missing["status"], "insufficient_data")
        self.assertIsNone(missing["P"])

    def test_candidate_formula_versions_are_explicit(self):
        consequence_result = consequence({"N_g": 80, "V_g": 60, "T_g": 40}, unit="score_0_100",
                                          data_mode="synthetic", rule_version=NEW_C_VERSION)
        self.assertEqual(consequence_result["C_g"], 80)
        risk_result = compare_risk_definitions(71.4, 85, unit="score_0_100", data_mode="synthetic",
                                               rule_version=RISK_VERSION)
        self.assertIsNone(risk_result["H_selected"])
        self.assertEqual(risk_result["H_status"], "unsupported_pending_business_definition")

    def test_g01_can_be_recomputed_from_explicit_versioned_dates(self):
        result = calculate_service_age_ratio(
            commissioned_at="2010-01-01T00:00:00+08:00",
            as_of="2020-01-01T00:00:00+08:00",
            design_life_years=40,
            data_mode="synthetic", rule_version=AGE_VERSION,
        )
        self.assertAlmostEqual(result["age_years"], 9.9988, places=4)
        self.assertEqual(result["score"], 10)
        with self.assertRaises(ValueError):
            calculate_service_age_ratio(
                commissioned_at="2010-01-01T00:00:00",
                as_of="2020-01-01T00:00:00+08:00",
                design_life_years=40,
                data_mode="synthetic", rule_version=AGE_VERSION,
            )

    def test_gas_modules_do_not_import_water_scoring(self):
        self.assertNotIn("src.pipe_health", sys.modules)

    def test_structured_response_contract_rejects_extra_and_invalid_nested_fields(self):
        value = evaluate_fixture()
        validated = GasCaseResponse.model_validate(value)
        self.assertEqual(len(validated.nodes), 54)
        self.assertEqual(len(validated.edges), 73)
        invalid = json.loads(json.dumps(value))
        invalid["nodes"][0]["unexpected"] = True
        with self.assertRaises(ValueError):
            GasCaseResponse.model_validate(invalid)
        invalid = json.loads(json.dumps(value))
        invalid["edges"][0]["direction"] = "physical_flow"
        with self.assertRaises(ValueError):
            GasCaseResponse.model_validate(invalid)

    def test_fixture_boundary_and_corruption_are_not_client_success(self):
        source_root = fixture_root()
        with tempfile.TemporaryDirectory(prefix="gas-fixture-test-") as temp:
            root = Path(temp)
            shutil.copy2(source_root / "gas54_network.json", root / "gas54_network.json")
            (root / "gas54_case.json").write_text("{broken", encoding="utf-8")
            old = os.environ.get("WATER_GAS_FIXTURE_ROOT")
            os.environ["WATER_GAS_FIXTURE_ROOT"] = str(root)
            try:
                with self.assertRaises(FixtureUnavailable):
                    evaluate_fixture()
            finally:
                if old is None:
                    os.environ.pop("WATER_GAS_FIXTURE_ROOT", None)
                else:
                    os.environ["WATER_GAS_FIXTURE_ROOT"] = old

    def test_fixture_root_rejects_missing_directory_and_symlink_escape(self):
        with tempfile.TemporaryDirectory(prefix="gas-fixture-boundary-") as temp:
            parent = Path(temp)
            missing = parent / "missing"
            old = os.environ.get("WATER_GAS_FIXTURE_ROOT")
            try:
                os.environ["WATER_GAS_FIXTURE_ROOT"] = str(missing)
                with self.assertRaises(FixtureUnavailable):
                    fixture_root()
                target = parent / "target"
                target.mkdir()
                link = parent / "link"
                link.symlink_to(target, target_is_directory=True)
                os.environ["WATER_GAS_FIXTURE_ROOT"] = str(link)
                with self.assertRaises(FixtureUnavailable):
                    fixture_root()
            finally:
                if old is None:
                    os.environ.pop("WATER_GAS_FIXTURE_ROOT", None)
                else:
                    os.environ["WATER_GAS_FIXTURE_ROOT"] = old

    def test_unknown_case_is_rejected_before_fixture_access(self):
        with self.assertRaises(KeyError):
            load_case("syn-gas-case-unknown")


if __name__ == "__main__":
    unittest.main()
