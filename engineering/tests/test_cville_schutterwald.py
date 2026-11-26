from datetime import datetime, timezone
import os

import pytest

from src.gas_application import charlottesville as city, schutterwald as model


def test_city_event_whitelist_and_timeline(monkeypatch):
    def query(layer, **params):
        assert layer == 1
        assert "ADDRESS" not in params.get("outFields", "")
        assert "LEAK_REPORTED_DATE < TIMESTAMP '2026-01-03 00:00:00'" in params["where"]
        if params.get("returnCountOnly"):
            return {"count": 1}
        return {"features": [{"attributes": {"OBJECTID": 10, "LEAK_REPORTED_DATE": 1735689600000,
                                             "ADDRESS": "excluded", "DISPATCHED_DATE": None}}]}
    monkeypatch.setattr(city, "query", query)
    record = city.event_page("leaks", "2026-01-01", "2026-01-02", None, 20, 0)["items"][0]
    assert record["timeline"][0]["time"] == "2025-01-01T00:00:00+00:00"
    assert record["timeline"][1]["time"] is None
    assert "excluded" not in str(record)
    assert record["durations"]["dispatch_arrival"]["status"] == "missing"


def test_city_invalid_filter():
    with pytest.raises(ValueError):
        city.asset_page(search="x' OR 1=1")
    with pytest.raises(ValueError):
        city.event_page("reports", None, None, "ACTIVE", 20, 0)


@pytest.mark.skipif(not os.getenv("SCHUTTERWALD_MODEL_FILE"), reason="official model not configured")
def test_model_presets_and_reproducibility():
    data = model.overview()
    assert data["counts"]["distribution_pipes"] == 1053
    base, redundant, fault = [p["id"] for p in data["presets"]]
    baseline = model.execute(base, model.VERSION)
    assert len(baseline["buildings"]) == 1506
    assert all(b["reachable"] for b in baseline["buildings"])
    assert all(b["reachable"] for b in model.execute(redundant, model.VERSION)["buildings"])
    outage = model.execute(fault, model.VERSION)
    repeated = model.execute(fault, model.VERSION)
    assert sum(not b["reachable"] for b in outage["buildings"]) == 1
    assert all(b["path"] is None for b in outage["buildings"] if not b["reachable"])
    assert outage["run_id"] == repeated["run_id"]
    assert outage["stable_result_sha256"] == repeated["stable_result_sha256"]
    assert outage["execution_id"] != repeated["execution_id"]
    with pytest.raises(ValueError):
        model.execute(fault, "wrong")


@pytest.mark.skipif(not os.getenv("SCHUTTERWALD_SOLVER_PYTHON"), reason="separate solver not configured")
def test_baseline_and_fault_hydraulics():
    base = model.execute("baseline", model.VERSION, True)["hydraulics"]
    assert base["status"] == "ok", base
    assert base["mass_balance_error_kg_s"] <= 1e-6
    assert len(base["pressure_bar"]) == 2559
    source = next(iter(model.load_model()["ext_grid"].values()))
    assert base["pressure_bar"][str(source["junction"])] == pytest.approx(source["p_bar"], abs=1e-6)
    fault = model.presets()[2]["id"]
    result = model.execute(fault, model.VERSION, True)
    hydraulic = result["hydraulics"]
    assert hydraulic["status"] == "ok", hydraulic
    assert hydraulic["excluded_model_demand_kg_s"] > 0
    assert hydraulic["mass_balance_error_kg_s"] <= 1e-6
    assert set(result["unreachable_nodes"]).isdisjoint(hydraulic["pressure_bar"])
    assert hydraulic["baseline_pressure_bar"] == base["pressure_bar"]
    assert set(result["unreachable_nodes"]) <= set(hydraulic["baseline_pressure_bar"])



@pytest.mark.skipif(not os.getenv("SCHUTTERWALD_MODEL_FILE"), reason="official model not configured")
def test_all_distribution_outages_match_research_recount():
    data = model.load_model()
    baseloads = [sink["junction"] for sink in data["sink"].values() if sink["in_service"]]
    impacts = []
    for key, pipe in data["pipe"].items():
        if pipe["type"] == "pipe" and pipe["in_service"]:
            reached = model.paths(data, key)
            impacts.append(sum(node not in reached for node in baseloads))
    assert len(impacts) == 1053
    assert impacts.count(0) == 24
    assert max(impacts) == 1488


def test_city_date_grouping_and_candidate_evidence(monkeypatch):
    stamp = lambda value: int(datetime.fromisoformat(value).timestamp() * 1000)
    rows = [
        {"OBJECTID": 1, "LEAK_REPORTED_DATE": stamp("2026-01-01T00:00:00+00:00"), "CITY_COUNTY": "ALBEMARLE",
         "LEAK_CAUSE": "EXCAVATION", "WORK_ORDER_NUM": "privateKey", "DISPATCHED_DATE": 1000, "ARRIVAL_DATE": 2000},
        {"OBJECTID": 2, "LEAK_REPORTED_DATE": stamp("2026-02-28T23:59:59+00:00"), "WORK_ORDER_NUM": "ambiguous",
         "DISPATCHED_DATE": 2000, "ARRIVAL_DATE": 1000},
        {"OBJECTID": 3, "LEAK_REPORTED_DATE": stamp("2026-03-01T00:00:00+00:00"),
         "DISPATCHED_DATE": 1000, "ARRIVAL_DATE": 1000},
        {"OBJECTID": 4, "LEAK_REPORTED_DATE": None},
    ]
    monkeypatch.setattr(city, "event_snapshot", lambda *_: (datetime(2026, 4, 1, tzinfo=timezone.utc), "sha", rows))
    monkeypatch.setattr(city, "candidates", lambda layer, keys: {"privateKey": int(layer == 22), "ambiguous": 1})
    response = city.analysis("leaks", "2026-01-01", "2026-02-28", None, None, None, 0, 20)
    assert [m["count"] for m in response["monthly"]] == [1, 1]
    assert response["total"] == 2 and response["undated"] == 1
    assert [row["link"] for row in response["items"]] == ["ambiguous", "candidate"]
    assert "privateKey" not in str(response)
    assert response["durations"]["dispatch_arrival"] == {"ok": 1, "invalid": 1}
    assert city.durations(rows[2])["dispatch_arrival"] == {"status": "equal", "seconds": 0.0}


def test_city_incomplete_source_page_does_not_publish(monkeypatch):
    def query(layer, **params):
        if params.get("returnIdsOnly"):
            return {"objectIds": [10, 11]}
        return {"features": [{"attributes": {"OBJECTID": 10}}]}
    monkeypatch.setattr(city, "query", query)
    with pytest.raises(OSError):
        city.event_snapshot("leaks", -42)
