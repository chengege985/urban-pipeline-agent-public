"""Reproducible time-value fixtures anchored to S01's declared 6h/1h/4m3h values."""
from __future__ import annotations

import copy
import hashlib
import json
import platform
from pathlib import Path

from .service_time_value import VERSION, compare_backup_gain, evaluate_service_time_value

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experiments/algorithm_validation/time_value_v0_1/time_value"


def request(*, demand: float = 4, backup_start: float | None = None,
            backup_end: float = 6, backup_flow: float | None = None,
            network_spans: list[tuple[float, float, float | None]] | None = None,
            backup_spans: list[tuple[float, float]] | None = None) -> dict:
    """All flow delivery and inventory here are declared fixtures, not S01 hydraulic outputs."""
    network_spans = network_spans or [(0, 6, 0), (6, 8, demand)]
    if backup_spans is None:
        backup_spans = [] if backup_start is None else [(backup_start, backup_end)]
    points = sorted({0, 8, *(t for a, b, _ in network_spans for t in (a, b)),
                     *(t for a, b in backup_spans for t in (a, b))})
    segments = []
    for start, end in zip(points, points[1:]):
        flow = next((q for a, b, q in network_spans if a <= start and end <= b), None)
        active = any(a <= start and end <= b for a, b in backup_spans)
        allocation = {"source_id": "syn-water-truck", "service_id": "syn-hospital-001",
                      "flow_m3h": demand if backup_flow is None else backup_flow,
                      "connection_ready": True, "evidence_ref": "fixture:declared-delivery"}
        segments.append({"start_h": start, "end_h": end,
                         "network_m3h": {"syn-hospital-001": flow},
                         "evidence_ref": "fixture:network-quantity-assumption",
                         "allocations": [allocation] if active else []})
    return {"plan_id": "fixture-repair-only" if not backup_spans else "fixture-backup",
            "data_mode": "synthetic", "criterion": "quantity_only_independent_delivery",
            "horizon_hours": 8,
            "context": {"snapshot_id": "fixture:s01-time-value:20261101",
                        "as_of": "2026-11-01T00:00:00+08:00", "fault_id": "syn-fault",
                        "isolation_id": "fixture-given-isolation", "demand_version": "fixture-demand-v1"},
            "services": {"syn-hospital-001": {"demand_m3h": demand, "required_ratio": 1}},
            "sources": {"syn-water-truck": {"capacity_m3h": max(5, demand), "volume_m3": 100,
                                             "independent_of_network": True,
                                             "resources": {"water_truck": 1},
                                             "available_from_h": 0, "available_to_h": 8,
                                             "evidence_ref": "fixture:source-capacity-and-stock"}},
            "resource_capacities": {"water_truck": 1}, "segments": segments}


def cases() -> dict:
    base, early = request(), request(backup_start=1)
    late = request(backup_start=7, backup_end=8)
    partial = request(backup_start=1, backup_flow=2)
    partial_base = copy.deepcopy(base)
    for r in (partial_base, partial):
        r["sources"]["syn-water-truck"]["capacity_m3h"] = 2
    recurring_net = [(0, 2, 0), (2, 3, 4), (3, 6, 0), (6, 8, 4)]
    recurring_base = request(network_spans=recurring_net)
    recurring = request(network_spans=recurring_net, backup_spans=[(1, 2), (3, 6)])
    conflict = copy.deepcopy(early)
    conflict["sources"]["syn-truck-second"] = copy.deepcopy(conflict["sources"]["syn-water-truck"])
    for segment in conflict["segments"]:
        if segment["allocations"]:
            segment["allocations"].append({**segment["allocations"][0], "source_id": "syn-truck-second"})
    unknown = request(network_spans=[(0, 1, 0), (1, 3, None), (3, 6, 0), (6, 8, 4)])
    return {"repair_only": {"request": base},
            "early_backup": {"baseline": base, "request": early},
            "late_backup": {"baseline": base, "request": late},
            "partial_capacity": {"baseline": partial_base, "request": partial},
            "repeated_outage": {"baseline": recurring_base, "request": recurring},
            "resource_conflict": {"request": conflict},
            "unknown_interval": {"request": unknown},
            "negative_gain_late_vs_early": {"baseline": early, "request": late}}


def hydraulic_time_cases(hydraulic: dict) -> dict:
    """Explicitly map this benchmark's H to its own service, never to S01 IDs.

    Event intervals hold independently solved steady states; not EPS dynamics.
    Network changes are evaluated separately, not passed to marginal-backup API.
    """
    runs = {c["case_id"]: c for c in hydraulic["cases"]}
    backup = runs["backup_pda"]["targets"]["H"]
    normal = runs["normal_pda"]["targets"]["H"]
    demand = backup["required_m3h"]
    flows = {"hydraulic_repair_only": 0,
             "hydraulic_backup_at_1h": backup["useful_supply_m3h"],
             "topology_full_supply_assumption": demand}
    answer = {}
    for name, flow in flows.items():
        r = request(demand=demand, network_spans=[(0, 1, 0), (1, 6, flow),
                                                 (6, 8, normal["useful_supply_m3h"])])
        r["plan_id"] = name
        r["context"].update(snapshot_id="fixture:hydraulic-five-nodes", fault_id="fixture:P1-closed",
                            isolation_id="fixture:P4-schedule", demand_version="fixture:H-7.2")
        r["services"] = {"fixture-hydraulic-H": r["services"]["syn-hospital-001"]}
        r["sources"], r["resource_capacities"] = {}, {}
        for s in r["segments"]:
            s["network_m3h"] = {"fixture-hydraulic-H": s["network_m3h"]["syn-hospital-001"]}
            s["evidence_ref"] = "fixture:four-steady-state-hydraulics" if name != "topology_full_supply_assumption" else "fixture:intentionally-optimistic-control"
        answer[name] = {"request": r, "result": evaluate_service_time_value(r)}
    return answer


def main() -> None:
    fixtures = cases()
    results = {}
    for name, case in fixtures.items():
        results[name] = (compare_backup_gain(case["baseline"], case["request"])
                         if "baseline" in case else evaluate_service_time_value(case["request"]))
    paths = [Path(__file__), Path(__file__).with_name("service_time_value.py")]
    evidence = {"contract_version": VERSION, "python": platform.python_version(),
                "data_mode": "synthetic_fixture", "run_date": "2026-11-01",
                "source_parameters": "179/01_工程项目/data/synthetic/s01.json: repair 6h, backup 1h, capacity 5m3/h, demand 4m3/h. Inventory 100m3 and delivered quantities are NEW fixture assumptions, not measured outputs.",
                "s01_source_sha256": "12022d6fd60f15a1b60c35943b2db87eff5e95060b5599588a85125529f9942b",
                "source_sha256": {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                "fixtures": fixtures, "results": results}
    hydro_path = OUT.parent / "hydraulics/results.json"
    if hydro_path.is_file():
        evidence["hydraulic_input_sha256"] = hashlib.sha256(hydro_path.read_bytes()).hexdigest()
        evidence["hydraulic_piecewise_steady_assumption"] = hydraulic_time_cases(json.loads(hydro_path.read_text(encoding="utf-8")))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for name, result in results.items():
        print(name, result["status"])


if __name__ == "__main__":
    main()
