"""Candidate, synthetic steady-state hydraulic benchmark; not an API capability.

Reads a portable standard INP fixture exported from the cuv_reference network.
Run with the existing .venv-cuv (or an isolated environment with WNTR 1.4.0):
    python -m src.algorithm_validation.hydraulic_baseline --output <directory>
The native EPANET step API avoids Windows ENsolveH scratch-file Error 305.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import math
import platform
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import networkx as nx
import wntr
from wntr.epanet.toolkit import ENepanet
from wntr.epanet.util import EN

VERSION = "hydraulic-fixture-0.1.0"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "experiments" / "algorithm_validation" / "time_value_v0_1" / "hydraulics"
# Historical provenance captured when exporting the base INP fixture. These
# old local-only files are not runtime dependencies and are not re-hashed here.
LEGACY_INPUT_HASHES = {
    "04_实施方案/对接样例_v0.2/nodes.csv": "a41d7b03b3578348cd00744e82d6344ee4972037c0923841a09f0c954fbe4735",
    "04_实施方案/对接样例_v0.2/pipes.csv": "bbc29655a94e340c4a0fd4232337dcebe24405ba1cc844c117e9797ff13994ff",
}
LEGACY_REFERENCE_SHA256 = "5fe48b793eb62835ef2474301e110362cc388489ed630314974f8c331fdf4ea8"
FLOW_BALANCE_TOLERANCE_M3S = 1e-6
PRESSURE_TOLERANCE_M = 1e-5
SERVICE_RATIO_TOLERANCE = 1e-4
CASES = (("normal_pda", "normal", "PDD"),
         ("backup_pda", "backup", "PDD"),
         ("disconnected_pda", "fault", "PDD"),
         ("backup_dda", "backup", "DD"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pda_demand_m3s(demand: float, pressure_m: float,
                   minimum_m: float = 0.0, required_m: float = 20.0,
                   exponent: float = 0.5) -> float:
    """Wagner demand relation, same exponent convention as EPANET/WNTR."""
    if not all(math.isfinite(v) for v in (demand, pressure_m, minimum_m, required_m, exponent)):
        raise ValueError("non-finite PDA input")
    if demand < 0 or required_m <= minimum_m or exponent <= 0:
        raise ValueError("invalid PDA parameters")
    if pressure_m <= minimum_m:
        return 0.0
    if pressure_m >= required_m:
        return demand
    return demand * ((pressure_m - minimum_m) / (required_m - minimum_m)) ** exponent


def _reachable(wn) -> dict[str, bool]:
    # This fixture has ordinary pipes only: undirected connectivity is intentional.
    graph = nx.Graph()
    graph.add_nodes_from(wn.node_name_list)
    for _, link in wn.pipes():
        if str(link.initial_status).lower() != "closed":
            graph.add_edge(link.start_node_name, link.end_node_name)
    sources = wn.reservoir_name_list
    return {n: any(nx.has_path(graph, s, n) for s in sources) for n in wn.node_name_list}


def _solve_step(wn, prefix: Path) -> dict:
    """Read native toolkit values in INP LPS units, explicitly convert to SI.

    ENinitH(0) disables hydraulic history saving. Only time=0 is solved; no
    ENsolveQ / water-quality result is claimed. Every opened handle is closed.
    """
    inp = prefix.with_suffix(".inp")
    wntr.network.write_inpfile(wn, str(inp), units="LPS", version=2.2)
    ep = ENepanet(version=2.2)
    opened = hydraulic_opened = False
    try:
        ep.ENopen(str(inp), str(prefix.with_suffix(".rpt")), str(prefix.with_suffix(".bin")))
        opened = True
        version = ctypes.c_int()
        if ep.ENlib.EN_getversion(ctypes.byref(version)) != 0:
            raise RuntimeError("EPANET version lookup failed")
        ep.ENopenH()
        hydraulic_opened = True
        ep.ENinitH(0)
        time_s = ep.ENrunH()
        # Save before subsequent getter calls replace the toolkit error code.
        warning_code = int(ep.errcode)
        nodes = {}
        for name in wn.node_name_list:
            index = ep.ENgetnodeindex(name)
            nodes[name] = {
                "raw_pressure_head_m": ep.ENgetnodevalue(index, EN.PRESSURE),
                "raw_head_m": ep.ENgetnodevalue(index, EN.HEAD),
                "raw_demand_m3s": ep.ENgetnodevalue(index, EN.DEMAND) / 1000.0,
            }
        flows = {name: ep.ENgetlinkvalue(ep.ENgetlinkindex(name), EN.FLOW) / 1000.0
                 for name in wn.link_name_list}
        if not all(math.isfinite(v) for n in nodes.values() for v in n.values()):
            raise RuntimeError("non-finite EPANET node output")
        if not all(math.isfinite(v) for v in flows.values()):
            raise RuntimeError("non-finite EPANET link output")
    finally:
        try:
            if hydraulic_opened:
                ep.ENcloseH()
        finally:
            if opened:
                ep.ENclose()
    report = prefix.with_suffix(".rpt").read_text(encoding="utf-8", errors="replace")
    warning_lines = [line.strip() for line in report.splitlines() if "WARNING" in line.upper()]
    return {"nodes": nodes, "link_flows_m3s": flows,
            "solver": {"native_version_code": version.value,
                       "time_s": time_s, "warning_code": warning_code,
                       "warnings": warning_lines,
                       "status": "converged" if warning_code == 0 else "returned_with_warning",
                       "api": "ENopenH -> ENinitH(0) -> ENrunH -> ENcloseH"}}


def run_case(case_id: str, mode: str, demand_model: str,
             input_dir: Path = DEFAULT, inp_output: Path | None = None) -> dict:
    if (case_id, mode, demand_model) not in CASES:
        raise ValueError("this benchmark supports only its four declared fixtures")
    fixture_path = input_dir / "normal_pda.inp"
    wn = wntr.network.WaterNetworkModel(str(fixture_path))
    wn.get_link("P1").initial_status = "OPEN" if mode == "normal" else "CLOSED"
    wn.get_link("P4").initial_status = "OPEN" if mode == "backup" else "CLOSED"
    wn.options.hydraulic.demand_model = demand_model
    # EPANET's supported lower accuracy bound is 1e-5; do not label it 1e-8.
    wn.options.hydraulic.accuracy = 1e-5
    wn.options.hydraulic.trials = 200
    wn.options.hydraulic.headerror = 1e-7
    wn.options.hydraulic.flowchange = 1e-9
    reachable = _reachable(wn)
    with tempfile.TemporaryDirectory(prefix="waterpipenet_hyd_step_") as td:
        prefix = Path(td) / case_id
        solved = _solve_step(wn, prefix)
        if inp_output is not None:
            inp_output.mkdir(parents=True, exist_ok=True)
            target_path = inp_output / (case_id + ".inp")
            if target_path.resolve() != fixture_path.resolve():
                target_path.write_bytes(prefix.with_suffix(".inp").read_bytes())

    flows = solved["link_flows_m3s"]
    balances = {}
    nodes = solved["nodes"]
    for name, node in wn.nodes():
        incoming = sum(flows[k] for k, l in wn.links() if l.end_node_name == name)
        outgoing = sum(flows[k] for k, l in wn.links() if l.start_node_name == name)
        balances[name] = incoming - outgoing - nodes[name]["raw_demand_m3s"]
        nodes[name]["graph_reachable"] = reachable[name]
        nodes[name]["node_type"] = node.node_type

    targets = {}
    for name in ("H", "R"):
        required = wn.get_node(name).base_demand
        raw = nodes[name]
        ratio = raw["raw_demand_m3s"] / required
        # Disconnected components have neither sources nor storage in this
        # fixture. Their physical supply is structurally zero; raw tiny flows
        # and heads remain available above as numerical diagnostics.
        if not reachable[name]:
            status, useful, satisfied = "no_source_path", 0.0, False
        elif demand_model == "DD":
            status, useful, satisfied = "unsupported_for_service_verdict", None, None
        elif solved["solver"]["warning_code"] != 0:
            status, useful, satisfied = "insufficient_data_solver_warning", None, None
        else:
            useful = min(required, max(0.0, raw["raw_demand_m3s"]))
            satisfied = (ratio >= 1.0 - SERVICE_RATIO_TOLERANCE
                         and raw["raw_pressure_head_m"] >= 20.0 - PRESSURE_TOLERANCE_M)
            status = "hydraulic_fixture_satisfied" if satisfied else "hydraulic_fixture_unsatisfied"
        targets[name] = {
            "required_m3h": required * 3600,
            "graph_reachable": reachable[name],
            "raw_pressure_head_m": raw["raw_pressure_head_m"],
            "raw_delivered_m3h": raw["raw_demand_m3s"] * 3600,
            "raw_supply_ratio": ratio,
            "useful_supply_m3h": None if useful is None else useful * 3600,
            "useful_supply_ratio": None if useful is None else useful / required,
            "fixture_hydraulic_satisfied": satisfied,
            "business_service_satisfied": None,
            "business_status": "insufficient_data_quality_and_business_thresholds",
            "status": status,
            "pda_relation_residual_m3s": (raw["raw_demand_m3s"] - pda_demand_m3s(required, raw["raw_pressure_head_m"]))
                if demand_model == "PDD" and reachable[name] else None,
        }
    source_flow = -sum(nodes[n]["raw_demand_m3s"] for n in wn.reservoir_name_list)
    delivered = sum(nodes[n]["raw_demand_m3s"] for n in wn.junction_name_list)
    max_residual = max(abs(v) for v in balances.values())
    residual = source_flow - delivered
    balance_ok = max_residual <= FLOW_BALANCE_TOLERANCE_M3S and abs(residual) <= FLOW_BALANCE_TOLERANCE_M3S
    return {
        "case_id": case_id, "mode": mode, "demand_model": demand_model,
        "data_class": "synthetic_fixture", "model_class": "candidate",
        **solved, "targets": targets,
        "mass_balance": {
            "junction_and_source_residuals_m3s": balances,
            "source_net_outflow_m3s": source_flow,
            "junction_demand_sum_m3s": delivered,
            "global_residual_m3s": residual,
            "max_node_abs_residual_m3s": max_residual,
            "absolute_tolerance_m3s": FLOW_BALANCE_TOLERANCE_M3S,
            "within_tolerance": balance_ok,
        },
        "numeric_validation_status": "passed" if balance_ok else "failed_mass_balance",
    }


def run_benchmark(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    cases = [run_case(*case, inp_output=output_dir) for case in CASES]
    result = {
        "schema_version": VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "synthetic steady-state benchmark, not a calibrated business model or API",
        "versions": {"python": platform.python_version(), "wntr": wntr.__version__,
                     "networkx": nx.__version__, "epanet": "2.2.0 (native code 20200)"},
        "input_hashes": {str((DEFAULT / "normal_pda.inp").relative_to(ROOT)):
                         sha256(DEFAULT / "normal_pda.inp")},
        "fixture_provenance": {"exported_from": "src/cuv_reference/run_example.py:build_network",
                               "legacy_local_input_hashes_at_export": LEGACY_INPUT_HASHES,
                               "legacy_source_sha256_at_export": LEGACY_REFERENCE_SHA256,
                               "base_fixture": "normal_pda.inp",
                               "case_overrides": "normal: P1 open/P4 closed; fault: both closed; backup: P1 closed/P4 open; demand model from CASES",
                               "runtime_requires_legacy_source_or_csv": False},
        "source_code": {"reference": "portable standard INP fixtures exported from cuv_reference",
                        "reference_sha256": LEGACY_REFERENCE_SHA256,
                        "benchmark_sha256": sha256(Path(__file__))},
        "assumptions": {"source_heads_m": {"S": 60, "B": 42},
                        "demand_m3h": {"H": 7.2, "R": 10.8},
                        "minimum_pressure_m": 0, "required_pressure_m": 20,
                        "pressure_exponent": 0.5, "threshold_authority": "fixture_only",
                        "duration_s": 0, "headloss": "Hazen-Williams",
                        "storage_pumps_quality": "none; no water quality acceptance claim",
                        "hydraulic_accuracy": 1e-5, "trials": 200,
                        "headerror_m": 1e-7, "flowchange_m3s": 1e-9,
                        "service_ratio_tolerance": SERVICE_RATIO_TOLERANCE,
                        "pressure_tolerance_m": PRESSURE_TOLERANCE_M},
        "units": {"inp_and_native_toolkit_flow": "L/s", "result_flow": "m3/s",
                  "result_display_flow": "m3/h", "pressure": "m water head (not MPa)",
                  "native_conversion": "L/s divided by 1000; m3/s multiplied by 3600"},
        "cases": cases,
    }
    (output_dir / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=Path("experiments/algorithm_validation/time_value_v0_1/hydraulics"))
    args = parser.parse_args()
    result = run_benchmark(args.output)
    for case in result["cases"]:
        hospital = case["targets"]["H"]
        print(f"{case['case_id']}: graph={hospital['graph_reachable']}, "
              f"p={hospital['raw_pressure_head_m']:.6f} m, "
              f"q={hospital['raw_delivered_m3h']:.6f} m3/h, "
              f"status={hospital['status']}, warning={case['solver']['warning_code']}, "
              f"balance={case['mass_balance']['within_tolerance']}")
    if not all(c["numeric_validation_status"] == "passed" for c in result["cases"]):
        raise SystemExit("hydraulic mass balance validation failed")


if __name__ == "__main__":
    main()
