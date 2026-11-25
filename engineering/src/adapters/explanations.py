"""Derived explanation of frozen 0.1.1 scores; never changes the scoring core."""
from __future__ import annotations

import math
from collections.abc import Mapping
from numbers import Real

from src.pipe_health.config import PACKAGE_VERSION, WEIGHTS

EXPLANATION_VERSION = "1.0.0"


def explain_legacy_health(indicator_scores: Mapping, model_version: str = PACKAGE_VERSION) -> dict:
    """H03: decompose unrounded Rs/Hs. Missing indicators are unevaluated, not safe.

    Inputs: standard indicator names -> integer score 1..5 or None.
    Weighted coverage is not a probability or an accuracy estimate.
    This is an additive tool result; do not write it into a legacy snapshot.
    """
    base = {"explanation_version": EXPLANATION_VERSION, "model_version": model_version}
    if model_version != PACKAGE_VERSION:
        return {**base, "status": "unsupported", "reason": "Only the frozen 0.1.1 weights are supported."}
    if not isinstance(indicator_scores, Mapping):
        return {**base, "status": "invalid_input", "reason": "indicator_scores must be a mapping."}
    unknown = [str(key) for key in indicator_scores if key not in WEIGHTS]
    invalid = [
        name for name, value in indicator_scores.items()
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, Real)
            or not 1 <= value <= 5 or not math.isfinite(value) or int(value) != value
        )
    ]
    if unknown or invalid:
        return {**base, "status": "invalid_input", "unknown_indicators": unknown,
                "invalid_indicators": invalid, "reason": "Use standard names and integer scores 1..5."}
    scores = {name: indicator_scores.get(name) for name in WEIGHTS}
    valid_weight = sum(WEIGHTS[name] for name, score in scores.items() if score is not None)
    missing = [name for name, score in scores.items() if score is None]
    result = {**base, "status": "succeeded" if valid_weight else "insufficient_data",
              "valid_weight_sum": valid_weight, "missing_indicators": missing,
              "legacy_completeness_percent": valid_weight / sum(WEIGHTS.values()) * 100,
              "coverage_semantics": "valid_indicator_weight_fraction_not_confidence",
              "indicators": [], "deduction_rank": [], "reconciliation": None}
    if not valid_weight:
        result["indicators"] = [
            {"indicator": name, "raw_weight": weight, "score": None,
             "evaluation_status": "not_evaluated", "effective_weight": 0.0,
             "risk_contribution": None, "health_deduction_points": None}
            for name, weight in WEIGHTS.items()
        ]
        return result
    weighted = sum(WEIGHTS[name] * score for name, score in scores.items() if score is not None)
    rs = weighted / valid_weight
    hs = max(0.0, min(100.0, 100 * (1 - (rs - 1) / 4)))
    for name, score in scores.items():
        alpha = WEIGHTS[name] / valid_weight if score is not None else 0.0
        result["indicators"].append({
            "indicator": name, "raw_weight": WEIGHTS[name], "score": score,
            "evaluation_status": "evaluated" if score is not None else "not_evaluated",
            "effective_weight": alpha,
            "risk_contribution": alpha * score if score is not None else None,
            "health_deduction_points": 25 * alpha * (score - 1) if score is not None else None,
        })
    evaluated = [row for row in result["indicators"] if row["score"] is not None]
    result["deduction_rank"] = [r["indicator"] for r in sorted(
        evaluated, key=lambda r: -r["health_deduction_points"])]
    total = sum(r["health_deduction_points"] for r in evaluated)
    result["reconciliation"] = {
        "Rs_raw": rs, "Hs_raw": hs, "deduction_total": total,
        "deduction_residual": (100 - hs) - total,
        "risk_contribution_total": sum(r["risk_contribution"] for r in evaluated),
    }
    return result
