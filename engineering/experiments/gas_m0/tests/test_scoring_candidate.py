"""GAS-M0 arithmetic vectors; no platform, database, model or network imports."""
import json
import math
from fractions import Fraction
from pathlib import Path
import unittest

from waterpipnet_gas.scoring_candidate import (
    AGE_VERSION, LIKELIHOOD_VERSION, NEW_C_VERSION, OLD_C_VERSION, RISK_VERSION,
    WEIGHTS, consequence, compare_risk_definitions, evaluate_likelihood, normalize_age_ratio,
)


def record(score, quality=1):
    return {"available": True, "score": score, "quality": quality,
            "unit": "score_0_100", "source_ref": "synthetic:independently-specified-test-vector"}


def baseline():
    return {"G01": record(90), "G02": record(70), "G04": record(50)}


def likelihood(records):
    return evaluate_likelihood(records, data_mode="synthetic", rule_version=LIKELIHOOD_VERSION)


def risk(p, c, **kwargs):
    return compare_risk_definitions(p, c, unit="score_0_100", data_mode="synthetic",
                                    rule_version=RISK_VERSION, **kwargs)


class ScoringCandidateTests(unittest.TestCase):
    def test_contract_has_fifteen_distinct_indicator_rows(self):
        contract = json.loads((Path(__file__).resolve().parents[1] / "data/gas_indicator_contract.json").read_text(encoding="utf-8"))
        self.assertEqual([x["code"] for x in contract["indicators"]], [f"G{i:02}" for i in range(1, 16)])
        self.assertEqual({x["code"] for x in contract["indicators"] if x["mandatory"]}, {"G01", "G02", "G04"})
        self.assertAlmostEqual(sum(x["initial_weight"] for x in contract["indicators"]), 1)
        self.assertFalse(contract["business_approved"])

    def test_original_required_vector_has_independent_fraction_expectation(self):
        result = likelihood(baseline())
        # Source example can be formed by 90*12 + 70*8 + 50*4 over 24.
        self.assertAlmostEqual(result["P_base"], float(Fraction(230, 3)))
        self.assertAlmostEqual(result["P"], float(Fraction(230, 3)))
        self.assertEqual(result["E"], .24)
        self.assertAlmostEqual(result["P_low"], 18.4)
        self.assertAlmostEqual(result["P_high"], 94.4)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["E_label"], "粗评")

    def test_required_quality_affects_both_numerator_and_denominator(self):
        data = baseline(); data["G01"]["quality"] = .8
        result = likelihood(data)
        self.assertAlmostEqual(result["P_base"], float(Fraction(2030, 27)))
        self.assertEqual(result["E"], .216)

    def test_low_optional_evidence_can_lower_p(self):
        data = baseline(); data["G05"] = record(10)
        result = likelihood(data)
        self.assertAlmostEqual(result["P"], float(Fraction(490, 9)))
        self.assertGreater(result["E"], .24)
        self.assertLess(result["P"], result["P_base"])

    def test_high_optional_evidence_can_raise_p(self):
        data = baseline(); data["G05"] = record(100)
        result = likelihood(data)
        self.assertAlmostEqual(result["P"], float(Fraction(760, 9)))
        self.assertGreater(result["P"], result["P_base"])

    def test_optional_null_is_excluded_not_zero_or_fifty(self):
        data = baseline(); data["G05"] = None
        self.assertEqual(likelihood(data)["P"], likelihood(baseline())["P"])

    def test_each_missing_mandatory_prevents_p(self):
        for code in ("G01", "G02", "G04"):
            with self.subTest(code=code):
                data = baseline(); del data[code]; data["G05"] = record(99)
                result = likelihood(data)
                self.assertEqual(result["status"], "insufficient_data")
                self.assertIsNone(result["P"]); self.assertIsNone(result["P_base"])
                self.assertEqual(result["required_missing"], [code])

    def test_empty_evidence_is_insufficient(self):
        result = likelihood({})
        self.assertEqual(result["E"], 0)
        self.assertIsNone(result["P_low"])
        self.assertEqual(result["status"], "insufficient_data")

    def test_explicit_invalid_evidence_is_missing(self):
        data = baseline(); data["G01"] = {"available": False, "score": None, "quality": None}
        self.assertIsNone(likelihood(data)["P"])

    def test_unavailable_cannot_hide_usable_value(self):
        data = baseline(); data["G05"] = {"available": False, "score": 0}
        with self.assertRaises(ValueError): likelihood(data)

    def test_full_zero_vector_is_not_missing(self):
        result = likelihood({c: record(0) for c in WEIGHTS})
        self.assertEqual((result["P"], result["P_low"], result["P_high"]), (0, 0, 0))
        self.assertEqual(result["status"], "ok")

    def test_full_ninety_vector_preserves_source_scale(self):
        result = likelihood({c: record(90) for c in WEIGHTS})
        self.assertEqual(result["E"], 1)
        self.assertAlmostEqual(result["P"], 90)
        self.assertAlmostEqual(result["P_high"], 90)
        self.assertEqual(result["E_label"], "精评")
        self.assertFalse(result["is_failure_probability"])

    def test_full_maximum_stays_one_hundred(self):
        result = likelihood({c: record(100) for c in WEIGHTS})
        self.assertAlmostEqual(result["P_high"], 100)

    def test_quality_coverage_is_not_confidence(self):
        result = likelihood({c: record(50, .4) for c in WEIGHTS})
        self.assertEqual(result["E"], .4)
        self.assertEqual(result["E_label"], "初评")
        self.assertAlmostEqual(result["P"], 50)
        self.assertEqual(result["E_semantics"], "effective_weight_coverage_not_prediction_confidence")

    def test_invalid_numeric_scores_rejected(self):
        for value in (True, "80", float("nan"), float("inf"), -1, 101, 10 ** 1000):
            with self.subTest(value=str(value)[:20]):
                data = baseline(); data["G01"]["score"] = value
                with self.assertRaises(ValueError): likelihood(data)

    def test_unsupported_quality_values_rejected(self):
        for value in (True, "1", 0, .5, 1.1, float("nan")):
            with self.subTest(value=value):
                data = baseline(); data["G01"]["quality"] = value
                with self.assertRaises(ValueError): likelihood(data)

    def test_raw_units_rejected(self):
        data = baseline(); data["G04"]["unit"] = "MPa"
        with self.assertRaises(ValueError): likelihood(data)

    def test_missing_source_and_implicit_availability_rejected(self):
        for field in ("source_ref", "available"):
            data = baseline(); del data["G01"][field]
            with self.subTest(field=field):
                with self.assertRaises(ValueError): likelihood(data)

    def test_unknown_indicator_rejected(self):
        data = baseline(); data["W01"] = record(80)
        with self.assertRaises(ValueError): likelihood(data)

    def test_candidate_does_not_accept_real_unknown_or_internal_mode(self):
        for mode in ("real", "unknown", "internal", None, "provided_sample"):
            with self.subTest(mode=mode):
                with self.assertRaises(ValueError):
                    evaluate_likelihood(baseline(), data_mode=mode, rule_version=LIKELIHOOD_VERSION)

    def test_version_cannot_be_omitted_or_impersonate_official(self):
        for version in (None, "official", "legacy_demo/0.1.1", "gas/1.0"):
            with self.subTest(version=version):
                with self.assertRaises(ValueError):
                    evaluate_likelihood(baseline(), data_mode="synthetic", rule_version=version)

    def test_g01_documented_ratio_boundaries(self):
        for ratio, expected in ((0,10),(.3999,10),(.4,30),(.6,50),(.8,70),(1,90),(2,90)):
            with self.subTest(ratio=ratio):
                out = normalize_age_ratio(ratio, unit="service_age_over_design_life", data_mode="synthetic", rule_version=AGE_VERSION)
                self.assertEqual(out["score"], expected)
                self.assertTrue(out["requires_business_confirmation"])

    def test_g01_raw_years_and_negative_or_nonfinite_ratio_rejected(self):
        for ratio in (-1, True, "0.4", float("inf"), float("nan")):
            with self.subTest(ratio=ratio):
                with self.assertRaises(ValueError): normalize_age_ratio(ratio, unit="service_age_over_design_life", data_mode="synthetic", rule_version=AGE_VERSION)
        with self.assertRaises(ValueError): normalize_age_ratio(20, unit="year", data_mode="synthetic", rule_version=AGE_VERSION)

    def test_old_and_new_consequence_are_distinct(self):
        old = consequence({"A":80,"N_g":90,"V_g":70,"T_g":50,"E_g":30}, unit="score_0_100", data_mode="synthetic", rule_version=OLD_C_VERSION)
        new = consequence({"N_g":90,"V_g":70,"T_g":50,"E_g":30}, unit="score_0_100", data_mode="synthetic", rule_version=NEW_C_VERSION)
        self.assertEqual(old["C_g"], 73)
        self.assertEqual(new["C_g"], 90)
        self.assertEqual(new["M_g"], 30)
        self.assertFalse(new["M_direction_business_confirmed"])

    def test_revised_consequence_does_not_require_mitigation_score(self):
        new = consequence({"N_g":90,"V_g":70,"T_g":50}, unit="score_0_100", data_mode="synthetic", rule_version=NEW_C_VERSION)
        self.assertEqual(new["C_g"],90)
        self.assertIsNone(new["M_g"])
        self.assertEqual(new["M_g_status"], "insufficient_data")

    def test_missing_consequence_is_not_filled(self):
        new = consequence({"N_g":90,"V_g":None}, unit="score_0_100", data_mode="synthetic", rule_version=NEW_C_VERSION)
        self.assertIsNone(new["C_g"])
        self.assertEqual(new["status"],"insufficient_data")

    def test_consequence_mixed_version_dimensions_rejected(self):
        with self.assertRaises(ValueError): consequence({"A":80,"N_g":90,"V_g":70,"T_g":50}, unit="score_0_100", data_mode="synthetic", rule_version=NEW_C_VERSION)

    def test_document_example_risk_but_two_different_h_values(self):
        out = risk(71.4, 85)
        self.assertAlmostEqual(out["R"], math.sqrt(6069))
        self.assertAlmostEqual(out["H_candidates"]["100_minus_P"], 28.6)
        self.assertAlmostEqual(out["H_candidates"]["100_minus_R"], 100-math.sqrt(6069))
        self.assertEqual((out["grade_continuous"],out["grade_matrix"],out["grade_final"]),(4,5,5))
        self.assertIsNone(out["H_selected"])

    def test_matrix_catches_low_score_high_consequence(self):
        for p,c in ((10,90),(90,10)):
            out=risk(p,c)
            self.assertEqual((out["grade_continuous"],out["grade_matrix"],out["grade_final"]),(2,3,3))

    def test_risk_half_open_boundary_policy_explicit(self):
        for score, grade in ((0,1),(19.999,1),(20,2),(40,3),(60,4),(80,5),(100,5)):
            with self.subTest(score=score):
                self.assertEqual(risk(score,score)["grade_continuous"],grade)

    def test_redline_changes_region_without_inventing_exact_grade(self):
        out=risk(10,10,redline_events=["active_leak_alarm"])
        self.assertEqual(out["region"],"high")
        self.assertEqual(out["grade_before_redline"],1)
        self.assertIsNone(out["grade_final"])
        self.assertEqual(out["possible_final_grades"],[4,5])
        self.assertTrue(out["requires_manual_review"])

    def test_redline_never_reduces_existing_grade_five(self):
        self.assertEqual(risk(90,90,redline_events=["critical_valve_failure"])["grade_final"],5)

    def test_redline_can_be_visible_when_p_is_missing(self):
        out=risk(None,50,redline_events=["active_leak_alarm"])
        self.assertEqual(out["region"],"high")
        self.assertIsNone(out["R"])
        self.assertEqual(out["status"],"insufficient_data")

    def test_invalid_risk_value_not_hidden_by_other_missing_input(self):
        with self.assertRaises(ValueError): risk(None,float("nan"))

    def test_unknown_and_duplicate_redlines_rejected(self):
        for events in (["alarm"],["active_leak_alarm","active_leak_alarm"],"active_leak_alarm"):
            with self.subTest(events=events):
                with self.assertRaises(ValueError): risk(10,10,redline_events=events)

    def test_risk_does_not_accept_pressure_as_score(self):
        with self.assertRaises(ValueError): compare_risk_definitions(.4,.8,unit="MPa",data_mode="synthetic",rule_version=RISK_VERSION)

    def test_weight_contract_cannot_be_mutated(self):
        with self.assertRaises(TypeError): WEIGHTS["G01"] = 1


if __name__ == "__main__":
    unittest.main(verbosity=2)
