"""Synthetic long-term regressions; no writes to source samples or model outputs."""
from copy import deepcopy

import pytest

from src.adapters.scoring import recompute, validate_recompute
from src.adapters.explanations import explain_legacy_health
from src.pipe_health.config import WEIGHTS
from src.platform.errors import InputIssues
from src.platform.schemas import RecomputeRequest


def request_data():
    return dict(
        as_of="2026-01-02T00:00:00+08:00",
        window_start="2026-01-01T00:00:00+08:00",
        window_end="2026-01-02T00:00:00+08:00",
        static_rows=[{"管线编号": "syn-contract", "材质": "PE", "投运年份": 2005,
                      "管径": 300, "起点埋深": 1, "终点埋深": 1, "压力": "低压"}],
        dynamic_rows=[{"管线编号": "syn-contract", "时间": f"2026-01-01T0{i}:00:00+08:00",
                       "动态压力": .3, "余氯": .4, "浊度": .2, "瞬时流量": 1,
                       "风险点数量": 0} for i in range(2)],
        data_mode="synthetic", source_refs=["synthetic:scoring-contract"],
    )


@pytest.mark.parametrize("field,value", [
    ("材质", " "), ("材质", ["PE", "钢"]), ("材质", 23),
    ("压力", 100), ("压力", "100"), ("压力", " "), ("压力", {"value": "低压"}),
])
def test_reject_invalid_categories_before_scoring(field, value):
    data = request_data()
    data["static_rows"][0][field] = value
    with pytest.raises(InputIssues):
        validate_recompute(RecomputeRequest(**data))


def test_same_instant_same_year_gate_and_score():
    data = request_data()
    data.update(window_start="2025-12-31T00:00:00+08:00",
                window_end="2025-12-31T20:00:00+08:00")
    data["static_rows"][0]["投运年份"] = 2026
    for row in data["dynamic_rows"]:
        row["时间"] = row["时间"].replace("2026-01-01", "2025-12-31")
    scores = []
    for clock in ["2025-12-31T16:00:00+00:00", "2026-01-01T00:00:00+08:00"]:
        scores.append(recompute(RecomputeRequest(**{**data, "as_of": clock})).assessments[0].legacy_result.Hs)
    assert scores == [95.75, 95.75]


def test_legacy_semantics_and_additive_explanation_preserved():
    request = RecomputeRequest(**request_data())
    before = request.model_dump()
    item = recompute(request).assessments[0]
    explanation = explain_legacy_health(item.indicator_scores)
    assert item.model_version == "0.1.1"
    assert item.legacy_result.Rs == 1.3
    assert item.legacy_result.Hs == 92.5
    assert explanation["reconciliation"]["deduction_total"] == pytest.approx(7.5)
    assert request.model_dump() == before


def test_contribution_rank_differs_from_raw_score_rank():
    scores = {name: 1 for name in WEIGHTS}
    scores.update({"压力等级风险": 4, "风险点风险": 3})
    result = explain_legacy_health(scores)
    rows = {r["indicator"]: r for r in result["indicators"]}
    assert result["deduction_rank"][0] == "风险点风险"
    assert rows["风险点风险"]["health_deduction_points"] == pytest.approx(7.5)
    assert rows["压力等级风险"]["health_deduction_points"] == pytest.approx(3)
    assert result["reconciliation"]["Hs_raw"] == pytest.approx(89.5)


def test_missing_is_unevaluated_and_may_lower_health():
    scores = {"材质风险": 2, "管龄风险": 3}
    before = deepcopy(scores)
    result = explain_legacy_health(scores)
    assert scores == before
    assert result["legacy_completeness_percent"] == pytest.approx(26)
    assert result["reconciliation"]["Hs_raw"] == pytest.approx(62.5)
    assert explain_legacy_health({"管龄风险": 3})["reconciliation"]["Hs_raw"] == 50
    assert explain_legacy_health({"材质风险": 2})["reconciliation"]["Hs_raw"] == 75
    row = next(r for r in result["indicators"] if r["indicator"] == "管径风险")
    assert row["health_deduction_points"] is None
    assert row["effective_weight"] == 0


@pytest.mark.parametrize("value", [True, 0, 6, 1.5, float("nan"), "2", 10 ** 400])
def test_explanation_rejects_non_scores(value):
    assert explain_legacy_health({"材质风险": value})["status"] == "invalid_input"


def test_explanation_empty_and_unknown_model():
    assert explain_legacy_health({})["status"] == "insufficient_data"
    assert explain_legacy_health({"unknown": 1})["status"] == "invalid_input"
    assert explain_legacy_health({}, model_version="official")["status"] == "unsupported"
