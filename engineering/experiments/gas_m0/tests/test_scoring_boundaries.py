"""GAS-M0 P1 regression: decimal weighting, risk chain, and genuine boundary sides.

Golden values use hand-derived 3:5:2 effective weights, not the implementation
helper. Original scoring tests remain unchanged. Inputs are synthetic only.
"""
import itertools
import json
import math
import unittest

from waterpipnet_gas.scoring_candidate import (
    LIKELIHOOD_VERSION, RISK_VERSION, WEIGHTS,
    evaluate_likelihood, compare_risk_definitions,
)


def record(score, quality):
    return {"available": True, "score": score, "quality": quality,
            "unit": "score_0_100", "source_ref": "synthetic:P1-independent-boundary-vector"}


def required(scores, qualities=(.4, 1, .8)):
    return {code: record(score, quality) for code, score, quality in
            zip(("G01", "G02", "G04"), scores, qualities)}


def likelihood(records):
    return evaluate_likelihood(records, data_mode="synthetic", rule_version=LIKELIHOOD_VERSION)


def risk(p, c, **kwargs):
    return compare_risk_definitions(p, c, unit="score_0_100", data_mode="synthetic",
                                    rule_version=RISK_VERSION, **kwargs)


# q=(.4,1,.8) gives effective weights (.048,.08,.032), fractions (3,5,2)/10.
# 3*(b-10)+5*(b+10)+2*(b-10) = 10*b, exactly, for b=20/40/60/80.
# Risk matrix entries and the max with continuous grades are specified by the
# source matrix. No code under test supplies these expected final grades.
CHAIN = ((20, 60, 3, 2), (40, 60, 4, 3), (60, 40, 4, 3), (80, 20, 4, 3))


class ScoringBoundaryTests(unittest.TestCase):
    def assert_representative_chain(self, boundary, consequence, expected):
        out = likelihood(required((boundary-10, boundary+10, boundary-10)))
        self.assertEqual(out["P"], boundary)
        self.assertEqual(out["P_base"], boundary)
        self.assertEqual(out["E"], .16)
        self.assertEqual(out["status"], "partial")
        result = risk(out["P"], consequence)
        self.assertEqual(result["grade_final"], expected)
        self.assertIsNone(result["H_selected"])
        self.assertEqual(result["status"], "partial")

    def test_original_20_reproducer_has_grade_three(self):
        self.assert_representative_chain(20, 60, 3)

    def test_exact_40_mixed_quality_chain(self):
        self.assert_representative_chain(40, 60, 4)

    def test_exact_60_mixed_quality_chain(self):
        self.assert_representative_chain(60, 40, 4)

    def test_exact_80_mixed_quality_chain(self):
        self.assert_representative_chain(80, 20, 4)

    def test_original_100_reproducer_is_valid_for_downstream_risk(self):
        out = likelihood(required((100, 100, 100), (.4, .4, .6)))
        self.assertEqual(out["P"], 100)
        self.assertEqual(out["P_base"], 100)
        self.assertEqual(out["P_high"], 100)
        self.assertEqual(risk(out["P"], 100)["grade_final"], 5)

    def test_all_64_quality_combinations_keep_each_exact_boundary(self):
        for qualities in itertools.product((.4, .6, .8, 1), repeat=3):
            for boundary, c, grade in ((20,60,3),(40,60,4),(60,40,4),(80,20,4),(100,100,5)):
                with self.subTest(qualities=qualities, boundary=boundary):
                    out = likelihood(required((boundary,)*3, qualities))
                    self.assertEqual(out["P"], boundary)
                    self.assertEqual(out["P_base"], boundary)
                    self.assertLessEqual(out["P_high"], 100)
                    self.assertEqual(risk(out["P"], c)["grade_final"], grade)

    def test_adjacent_below_scores_are_not_promoted(self):
        for boundary, c, _, lower_grade in CHAIN:
            with self.subTest(boundary=boundary):
                below = math.nextafter(boundary, -math.inf)
                out = likelihood(required((below,)*3))
                self.assertEqual(out["P"], below)
                self.assertEqual(out["P_base"], below)
                self.assertEqual(risk(out["P"], c)["grade_final"], lower_grade)

    def test_adjacent_above_scores_are_not_rounded_down(self):
        for boundary, c, grade, _ in CHAIN:
            with self.subTest(boundary=boundary):
                above = math.nextafter(boundary, math.inf)
                out = likelihood(required((above,)*3))
                self.assertEqual(out["P"], above)
                self.assertEqual(out["P_base"], above)
                self.assertEqual(risk(out["P"], c)["grade_final"], grade)

    def test_single_indicator_downward_ulp_keeps_lower_grade(self):
        # Perturbing only the 0.3-share indicator can put the exact rational
        # less than half a float ULP below the boundary. Nearest-float alone
        # would erase that direction; a broad epsilon would also be wrong.
        for boundary, c, _, lower_grade in CHAIN:
            with self.subTest(boundary=boundary):
                scores = (math.nextafter(boundary-10, -math.inf), boundary+10, boundary-10)
                out = likelihood(required(scores))
                self.assertLess(out["P"], boundary)
                self.assertLess(out["P_base"], boundary)
                self.assertEqual(risk(out["P"], c)["grade_final"], lower_grade)

    def test_single_indicator_upward_ulp_keeps_upper_side(self):
        for boundary, c, grade, _ in CHAIN:
            with self.subTest(boundary=boundary):
                scores = (math.nextafter(boundary-10, math.inf), boundary+10, boundary-10)
                out = likelihood(required(scores))
                self.assertGreater(out["P"], boundary)
                self.assertGreater(out["P_base"], boundary)
                self.assertEqual(risk(out["P"], c)["grade_final"], grade)

    def test_single_indicator_below_100_is_not_serialized_as_full_score(self):
        scores = (math.nextafter(100, -math.inf), 100, 100)
        out = likelihood(required(scores, (.4,.4,.6)))
        self.assertLess(out["P"], 100)
        self.assertLess(out["P_base"], 100)
        self.assertLess(out["P_high"], 100)
        self.assertLessEqual(out["P"], out["P_high"])
        self.assertEqual(risk(out["P"],100)["grade_final"],5)

    def test_true_value_above_100_is_still_rejected_not_clamped(self):
        above = math.nextafter(100, math.inf)
        with self.assertRaises(ValueError):
            likelihood(required((above, 100, 100)))
        with self.assertRaises(ValueError):
            risk(above, 100)

    def test_optional_null_retains_boundary_partial_result(self):
        records = required((10,30,10))
        records["G05"] = None
        out = likelihood(records)
        self.assertEqual(out["P"], 20)
        self.assertEqual(out["status"], "partial")
        self.assertIn("G05", out["missing_indicators"])
        self.assertNotIn("G05", out["used_evidence"])

    def test_each_mandatory_null_retains_insufficient_result(self):
        for code in ("G01", "G02", "G04"):
            with self.subTest(code=code):
                records = required((10,30,10))
                records[code] = None
                out = likelihood(records)
                self.assertEqual(out["status"], "insufficient_data")
                for field in ("P", "P_base", "P_low", "P_high"):
                    self.assertIsNone(out[field])
                self.assertEqual(risk(out["P"],60)["status"], "insufficient_data")

    def test_full_mixed_quality_envelope_contains_serialized_score(self):
        qualities = (.4,.6,.8,1)
        for boundary in (20,40,60,80,100):
            with self.subTest(boundary=boundary):
                records = {code:record(boundary, qualities[i%4]) for i,code in enumerate(WEIGHTS)}
                out = likelihood(records)
                self.assertEqual(out["status"], "ok")
                self.assertEqual(out["P"], boundary)
                self.assertLessEqual(out["P_low"], out["P"])
                self.assertLessEqual(out["P"], out["P_high"])
                self.assertLessEqual(out["P_high"], 100)
                self.assertFalse(out["is_failure_probability"])
                self.assertIn("not_prediction_confidence",out["E_semantics"])
                json.dumps(out, allow_nan=False)

    def test_zero_mixed_quality_is_a_score_not_missing(self):
        out = likelihood(required((0,0,0),(.4,.4,.6)))
        self.assertEqual((out["P"], out["P_base"], out["P_low"]),(0,0,0))
        self.assertEqual(out["status"],"partial")
        self.assertEqual(out["required_missing"],[])
        self.assertEqual(risk(out["P"],0)["grade_final"],1)

    def test_candidate_and_unconfirmed_redline_semantics_unchanged(self):
        out = likelihood(required((10,30,10)))
        self.assertEqual(out["model_status"],"candidate")
        self.assertEqual(out["business_validation"],"not_business_validated")
        self.assertFalse(out["registered_with_platform"])
        result = risk(out["P"],60,redline_events=["active_leak_alarm"])
        self.assertEqual(result["grade_before_redline"],3)
        self.assertIsNone(result["grade_final"])
        self.assertIsNone(result["H_selected"])
        self.assertEqual(result["possible_final_grades"],[4,5])
        self.assertEqual(result["status"],"partial")

    def test_full_valid_inputs_keep_ok_with_decimal_weighting(self):
        records={code:record(40,(.4,.6,.8,1)[i%4]) for i,code in enumerate(WEIGHTS)}
        out=likelihood(records)
        self.assertEqual(out["P"],40)
        self.assertEqual(out["status"],"ok")
        self.assertEqual(out["missing_indicators"],[])
        self.assertEqual(risk(out["P"],60)["grade_final"],4)


    def test_symmetric_ulp_product_does_not_raise_risk_grade(self):
        # Decimal (b+d)*(b-d)=b*b-d*d is strictly below the square,
        # even when binary multiplication rounds back to b*b.
        for boundary, continuous, final in ((20,1,1),(40,2,2),(60,3,4),(80,4,5)):
            above=math.nextafter(boundary,math.inf)
            below=math.nextafter(boundary,-math.inf)
            self.assertEqual(above*below,boundary*boundary)
            for p,c in ((above,below),(below,above)):
                with self.subTest(boundary=boundary,p=p,c=c):
                    out=likelihood(required((p,p,p)))
                    result=risk(out["P"],c)
                    self.assertLess(result["R"],boundary)
                    self.assertEqual(result["grade_continuous"],continuous)
                    self.assertEqual(result["grade_final"],final)

    def test_risk_one_sided_product_retains_each_square_boundary_side(self):
        for boundary, lower_grade in ((20,1),(40,2),(60,3),(80,4)):
            out=likelihood(required((boundary,)*3))
            for direction in (-math.inf,math.inf):
                with self.subTest(boundary=boundary,direction=direction):
                    result=risk(out["P"],math.nextafter(boundary,direction))
                    if direction<0:
                        self.assertLess(result["R"],boundary)
                        self.assertEqual(result["grade_continuous"],lower_grade)
                    else:
                        self.assertGreater(result["R"],boundary)
                        self.assertEqual(result["grade_continuous"],lower_grade+1)

    def test_exact_decimal_square_products_keep_exact_risk_boundaries(self):
        # Non-identical P/C inputs exercise exact multiplication independently
        # of sqrt(P)*sqrt(C)'s intermediate binary approximations.
        for p,c,boundary,continuous,final in (
                (12.5,32,20,2,2),(32,50,40,3,3),(45,80,60,4,4),
                (80,80,80,5,5),(100,100,100,5,5)):
            with self.subTest(p=p,c=c):
                out=likelihood(required((p,p,p)))
                result=risk(out["P"],c)
                self.assertEqual(result["R"],boundary)
                self.assertEqual(result["grade_continuous"],continuous)
                self.assertEqual(result["grade_final"],final)

    def test_tiny_positive_product_does_not_prematurely_underflow(self):
        tiny=math.nextafter(0,math.inf)
        self.assertEqual(tiny*tiny,0)
        out=likelihood(required((tiny,)*3))
        result=risk(out["P"],tiny)
        self.assertEqual(result["R"],tiny)
        self.assertEqual(result["grade_continuous"],1)
        self.assertEqual(result["grade_final"],1)

    def test_zero_product_stays_zero(self):
        tiny=math.nextafter(0,math.inf)
        for p,c in ((0,tiny),(tiny,0),(0,0)):
            with self.subTest(p=p,c=c):
                result=risk(p,c)
                self.assertEqual(result["R"],0)
                self.assertEqual(result["grade_final"],1)
                self.assertIsNone(result["H_selected"])

    def test_risk_below_100_is_not_serialized_as_full_score(self):
        below=math.nextafter(100,-math.inf)
        out=likelihood(required((below,)*3))
        result=risk(out["P"],100)
        self.assertLess(result["R"],100)
        self.assertEqual(result["grade_continuous"],5)
        self.assertEqual(result["grade_final"],5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
