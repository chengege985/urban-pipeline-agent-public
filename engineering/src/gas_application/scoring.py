"""Source-formula arithmetic checks, exclusively for isolated synthetic GAS-M0.

This module does not derive G02--G15 scores from raw measurements, select an
official H definition, classify real data, or register any platform endpoint.
All source coefficients remain candidate demonstration parameters.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from fractions import Fraction
from types import MappingProxyType

LIKELIHOOD_VERSION = "gas-g15-arithmetic-candidate/0.1.0"
RISK_VERSION = "gas-risk-comparison-candidate/0.1.0"
AGE_VERSION = "gas-g01-ratio-candidate/0.1.0"
OLD_C_VERSION = "gas-c-20260803-weighted-candidate/0.1.0"
NEW_C_VERSION = "gas-c-revised-max-candidate/0.1.0"
WEIGHTS = MappingProxyType(dict(zip((f"G{i:02}" for i in range(1, 16)),
                   (.12, .08, .06, .04, .12, .08, .05, .10, .08, .07,
                    .05, .04, .03, .05, .03))))
REQUIRED = frozenset({"G01", "G02", "G04"})
QUALITY = frozenset({.4, .6, .8, 1.0})
MATRIX = ((1, 1, 2, 2, 3), (1, 2, 2, 3, 4), (2, 2, 3, 4, 4),
          (2, 3, 4, 4, 5), (3, 4, 4, 5, 5))
REDLINES = frozenset({"active_leak_alarm", "sustained_combustible_gas_exceedance",
                       "illegal_excavation_approaching", "critical_valve_failure"})


def _number(value, label, *, low=0.0, high=100.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label}: finite numeric value required; bool/string rejected")
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"{label}: finite numeric value required") from exc
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label}: expected finite value in [{low}, {high}]")
    return float(value)


def _score_float(exact):
    """Serialize exact decimal arithmetic without crossing a score boundary.

    Fraction(str(validated_number)) represents the accepted decimal input,
    not the float's binary expansion. If nearest-float serialization would
    turn a genuinely below/above-boundary rational into the exact boundary,
    retain its side using the adjacent float. This is not an epsilon policy
    and never promotes a genuinely lower score to a higher grade.
    """
    value = float(exact)
    if value in (20.0, 40.0, 60.0, 80.0, 100.0) and exact != Fraction(int(value)):
        value = math.nextafter(value, -math.inf if exact < value else math.inf)
    return value


def _gate(data_mode, rule_version, allowed):
    if data_mode != "synthetic":
        raise ValueError("candidate accepts explicitly synthetic input only")
    if not isinstance(rule_version, str) or rule_version not in allowed:
        raise ValueError("unsupported or unspecified candidate rule version")


def _meta(version):
    return {"rule_version": version, "data_mode": "synthetic",
            "model_status": "candidate", "business_validation": "not_business_validated",
            "registered_with_platform": False, "score_unit": "score_0_100"}


def normalize_age_ratio(ratio, *, unit, data_mode, rule_version):
    """G01 only: candidate half-open interpretation of the documented ratios.

    No design lifetime is supplied or inferred here. ratio must be constructed
    from a sourced service age and separately versioned design lifetime.
    """
    _gate(data_mode, rule_version, {AGE_VERSION})
    if unit != "service_age_over_design_life":
        raise ValueError("G01 requires a dimensionless service_age_over_design_life ratio")
    ratio = _number(ratio, "age_ratio", high=math.inf)
    score = next((s for cutoff, s in ((.4, 10), (.6, 30), (.8, 50), (1.0, 70))
                  if ratio < cutoff), 90)
    return {**_meta(rule_version), "indicator": "G01", "score": score,
            "boundary_policy": "candidate_lower_inclusive_upper_exclusive",
            "requires_business_confirmation": True}


def calculate_service_age_ratio(*, commissioned_at, as_of, design_life_years,
                                data_mode, rule_version):
    """Build the G01 candidate ratio from explicit, timezone-aware evidence.

    The design-life value must come from a separately versioned material
    dictionary. No material, date or timezone is inferred by this function.
    """
    _gate(data_mode, rule_version, {AGE_VERSION})
    if not isinstance(commissioned_at, str) or not isinstance(as_of, str):
        raise ValueError("G01 dates must be ISO-8601 strings with timezone")
    try:
        commissioned = datetime.fromisoformat(commissioned_at.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("G01 dates must be valid ISO-8601 timestamps") from exc
    if commissioned.tzinfo is None or observed.tzinfo is None:
        raise ValueError("G01 dates must include an explicit timezone")
    if observed < commissioned:
        raise ValueError("G01 as_of cannot precede commissioned_at")
    if isinstance(design_life_years, bool) or not isinstance(design_life_years, (int, float)):
        raise ValueError("G01 design_life_years must be a positive finite number")
    life = float(design_life_years)
    if not math.isfinite(life) or life <= 0:
        raise ValueError("G01 design_life_years must be a positive finite number")
    age_years = (observed.astimezone(timezone.utc) - commissioned.astimezone(timezone.utc)).total_seconds() / (365.2425 * 86400)
    ratio = age_years / life
    result = normalize_age_ratio(ratio, unit="service_age_over_design_life",
                                 data_mode=data_mode, rule_version=rule_version)
    return {**result, "age_years": age_years, "design_life_years": life,
            "commissioned_at": commissioned_at, "as_of": as_of,
            "source_semantics": "explicit_commissioned_at_and_versioned_design_life"}


def evaluate_likelihood(records, *, data_mode, rule_version):
    """Aggregate explicit, already-normalized G01--G15 evidence.

    records maps an indicator to {available, score, quality, unit, source_ref}.
    An absent indicator or None is missing, never automatically safe. Missing
    mandatory evidence suppresses P_base and P. Optional missing evidence is
    excluded from both numerator and denominator, with partial status.
    """
    _gate(data_mode, rule_version, {LIKELIHOOD_VERSION})
    if not isinstance(records, Mapping):
        raise ValueError("records must be an indicator mapping")
    unknown = set(records) - set(WEIGHTS)
    if unknown:
        raise ValueError(f"unknown indicators: {sorted(map(str, unknown))}")
    used = {}
    effective_weights = {}
    weighted_scores = {}
    missing = []
    for code, weight in WEIGHTS.items():
        record = records.get(code)
        if record is None:
            missing.append(code)
            continue
        if not isinstance(record, Mapping):
            raise ValueError(f"{code}: evidence record must be a mapping or null")
        if set(record) - {"available", "score", "quality", "unit", "source_ref"}:
            raise ValueError(f"{code}: unknown evidence fields")
        if not isinstance(record.get("available"), bool):
            raise ValueError(f"{code}: available must be explicit bool")
        if not record["available"]:
            if record.get("score") is not None or record.get("quality") is not None:
                raise ValueError(f"{code}: unavailable evidence cannot carry a usable score/quality")
            missing.append(code)
            continue
        score = _number(record.get("score"), f"{code}.score")
        quality = _number(record.get("quality"), f"{code}.quality", high=1)
        if quality not in QUALITY:
            raise ValueError(f"{code}: quality must be source-listed 0.4/0.6/0.8/1.0")
        if record.get("unit") != "score_0_100":
            raise ValueError(f"{code}: raw measurements cannot be aggregated as normalized scores")
        if not isinstance(record.get("source_ref"), str) or not record["source_ref"].strip():
            raise ValueError(f"{code}: a source_ref is required for usable evidence")
        # Keep the documented decimal coefficients exact until JSON output.
        effective = Fraction(str(weight)) * Fraction(str(quality))
        effective_weights[code] = effective
        weighted_scores[code] = effective * Fraction(str(score))
        used[code] = {"score": score, "quality": quality, "weight": weight,
                      "effective_weight": float(effective), "source_ref": record["source_ref"]}
    e_exact = sum(effective_weights.values(), Fraction())
    e = float(e_exact)
    required_missing = sorted(REQUIRED.intersection(missing))
    base = {**_meta(rule_version), "P_base": None, "P": None, "E": e,
            "E_unit": "fraction_of_full_model_weight",
            "E_semantics": "effective_weight_coverage_not_prediction_confidence",
            "E_label": "粗评" if e < .35 else "初评" if e < .6 else "较可靠" if e < .8 else "精评",
            "P_low": None, "P_high": None, "missing_indicators": missing,
            "required_missing": required_missing, "used_evidence": used,
            "is_failure_probability": False}
    if required_missing:
        return {**base, "status": "insufficient_data", "reason": "mandatory evidence missing"}
    e_m = sum((effective_weights[c] for c in REQUIRED), Fraction())
    numerator = sum(weighted_scores.values(), Fraction())
    p_base = sum((weighted_scores[c] for c in REQUIRED), Fraction()) / e_m
    p = numerator / e_exact
    return {**base, "status": "partial" if missing else "ok",
            "P_base": _score_float(p_base), "P": _score_float(p),
            "E_M": float(e_m), "P_low": _score_float(numerator),
            "P_high": _score_float(numerator + (1 - e_exact) * 100),
            "interval_semantics": "source_theoretical_envelope_not_statistical_confidence_interval"}


def consequence(values, *, unit, data_mode, rule_version):
    """Version-explicit arithmetic on already-normalized consequence scores."""
    _gate(data_mode, rule_version, {OLD_C_VERSION, NEW_C_VERSION})
    if unit != "score_0_100":
        raise ValueError("consequence dimensions require normalized score_0_100")
    if not isinstance(values, Mapping):
        raise ValueError("consequence values must be a mapping")
    required = ("A", "N_g", "V_g", "T_g", "E_g") if rule_version == OLD_C_VERSION else ("N_g", "V_g", "T_g")
    allowed = set(required) | {"E_g"}
    if set(values) - allowed:
        raise ValueError("unexpected consequence dimensions for selected version")
    clean = {k: _number(v, k) for k, v in values.items() if v is not None}
    missing = [k for k in required if k not in clean]
    meta = {**_meta(rule_version), "C_g": None, "missing_dimensions": missing,
            "M_g": None, "M_direction_business_confirmed": False}
    if missing:
        return {**meta, "status": "insufficient_data"}
    if rule_version == OLD_C_VERSION:
        cg = math.fsum(clean[k] * w for k, w in zip(required, (.4, .25, .15, .1, .1)))
        return {**meta, "status": "ok", "C_g": cg,
                "formula": "0.40*A+0.25*N_g+0.15*V_g+0.10*T_g+0.10*E_g"}
    return {**meta, "status": "ok" if "E_g" in clean else "partial", "C_g": max(clean[k] for k in required),
            "M_g": clean.get("E_g"), "formula": "max(N_g,V_g,T_g)",
            "C_g_status": "calculable", "M_g_status": "calculable" if "E_g" in clean else "insufficient_data",
            "M_g_source_semantics": "E_g_inverse_isolation_score_higher_is_weaker_not_positive_capacity"}


def _level(score):
    return min(int(score // 20) + 1, 5)


def _risk_float(p, c, product):
    """Keep the square-root display on the exact product's boundary side.

    Taking each square root before multiplying avoids prematurely underflowing
    tiny positive P*C to zero. The exact product, not that approximation,
    decides equality and sides at every published score boundary.
    """
    value = math.sqrt(p) * math.sqrt(c)
    for boundary in (20, 40, 60, 80, 100):
        square = boundary * boundary
        if product == square:
            return float(boundary)
        if product < square and value >= boundary:
            value = math.nextafter(float(boundary), -math.inf)
        elif product > square and value <= boundary:
            value = math.nextafter(float(boundary), math.inf)
    return value


def compare_risk_definitions(p, c, *, unit, data_mode, rule_version, redline_events=()):
    """Compare both H interpretations without selecting an official health score.

    P/C boundary semantics adopt an explicitly candidate [0,20),...,[80,100]
    policy consistent with the documented R intervals. Redline exact grade
    is unconfirmed: the source only fixes the high-risk *region*.
    """
    _gate(data_mode, rule_version, {RISK_VERSION})
    if unit != "score_0_100":
        raise ValueError("P/C require normalized score_0_100")
    if not isinstance(redline_events, (list, tuple)) or any(not isinstance(x, str) for x in redline_events):
        raise ValueError("redline_events must be an explicit list/tuple of event codes")
    if len(set(redline_events)) != len(redline_events) or set(redline_events) - REDLINES:
        raise ValueError("unknown or duplicate redline event")
    meta = {**_meta(rule_version), "R": None, "H_selected": None,
            "H_status": "unsupported_pending_business_definition",
            "H_candidates": {}, "grade_before_redline": None, "grade_final": None,
            "region": "high" if redline_events else None,
            "redline_events": list(redline_events), "requires_manual_review": bool(redline_events),
            "boundary_policy": "candidate_lower_inclusive_upper_exclusive",
            "is_failure_probability": False}
    # Invalid supplied values must not be silently hidden by another missing value.
    p = None if p is None else _number(p, "P")
    c = None if c is None else _number(c, "C")
    if p is None or c is None:
        return {**meta, "status": "insufficient_data", "reason": "P and C both required"}
    product = Fraction(str(p)) * Fraction(str(c))
    r = _risk_float(p, c, product)
    # Compare squared thresholds exactly: float multiplication/sqrt can round
    # a genuinely lower product to the boundary and increase the risk grade.
    continuous = 1 + sum(product >= boundary * boundary for boundary in (20, 40, 60, 80))
    matrix = MATRIX[_level(p) - 1][_level(c) - 1]
    grade = max(continuous, matrix)
    # A baseline of grade 5 cannot be reduced by an unknown redline uplift.
    final = grade if not redline_events or grade == 5 else None
    return {**meta, "status": "partial", "R": r,
            "H_candidates": {"100_minus_R": 100 - r, "100_minus_P": 100 - p},
            "grade_continuous": continuous, "grade_matrix": matrix,
            "grade_before_redline": grade, "grade_final": final,
            "possible_final_grades": [final] if final else [max(4, grade), 5],
            "region": "high" if redline_events or grade >= 4 else "medium" if grade == 3 else "low",
            "redline_grade_status": "pending_4_or_5" if redline_events and final is None else "determined",
            "reason": "H definition is not approved; candidates are arithmetic comparisons only"}
