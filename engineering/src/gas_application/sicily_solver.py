"""Isolated steady-state worker using the author's GasNetSim interface."""
from __future__ import annotations

import csv
import json
import math
import os
import sys
from importlib.metadata import version
from pathlib import Path

import GasNetSim as gns

# Reproduction thresholds for this research model only.
REFERENCE_MAX_ERROR_BAR = 0.25
MAX_NODAL_STANDARD_FLOW_ERROR = 0.03  # Sm³/s; engine PressureProblem residual basis
STANDARD_ATMOSPHERE_PA = 101325


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream, delimiter=";"))


def write(path: Path, records: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]), delimiter=";")
        writer.writeheader()
        writer.writerows(records)


def solve(folder: Path, source: Path, included: set[str], excluded: str | None) -> dict:
    folder.mkdir()
    nodes = [row for row in rows(source / "sf_nodes.csv") if row["node_index"] in included]
    pipes = [row for row in rows(source / "sf_pipelines.csv") if row["pipeline_index"] != excluded
             and row["inlet_index"] in included and row["outlet_index"] in included]
    if not nodes or not pipes:
        raise ValueError("可求解部分缺少有效管段")
    write(folder / "sf_nodes.csv", nodes)
    write(folder / "sf_pipelines.csv", pipes)
    network = gns.create_network_from_folder(folder)
    network.simulation(max_iter=80, tol=0.01, underrelaxation_factor=1.0,
                       tracking_method="simple_mixing", coupling_strategy="staggered",
                       coupling_max_iter=80, coupling_tol=1e-4, sparse_matrix=False)
    pressure = {str(key): float(node.pressure) for key, node in network.nodes.items()}
    flow = {str(key): float(pipe.flow_rate) for key, pipe in network.pipelines.items()}
    if any(not math.isfinite(value) or value <= 0 for value in pressure.values()):
        raise ValueError("压力计算出现非有限值或非正绝对压力")
    if any(not math.isfinite(value) for value in flow.values()):
        raise ValueError("流量计算出现非有限值")
    net_flow = dict.fromkeys(pressure, 0.0)
    for pipe in pipes:
        quantity = flow[pipe["pipeline_index"]]
        net_flow[pipe["inlet_index"]] -= quantity
        net_flow[pipe["outlet_index"]] += quantity
    errors = []
    for row in nodes:
        node_id, fixed = row["node_index"], row["pressure_pa"]
        if fixed:
            # The pinned author's CSV and loader use gauge input, absolute node pressure.
            if abs(float(fixed) + STANDARD_ATMOSPHERE_PA - pressure[node_id]) > 1:
                raise ValueError("压力边界偏离输入")
        elif row["flow_type"] == "volumetric" and row["flow_sm3_per_s"]:
            errors.append(abs(net_flow[node_id] - float(row["flow_sm3_per_s"])))
        else:
            raise ValueError("节点负荷类型不支持独立标准流量复核")
    max_error = max(errors, default=0.0)
    return {"pressure_bar": {key: value / 100000 for key, value in pressure.items()},
            "flow_sm3_s": flow, "max_nodal_flow_error_sm3_s": max_error}


def main() -> None:
    folder_text, edge, before_json, after_json = sys.argv[1:]
    folder = Path(folder_text)
    try:
        baseline = solve(folder / "before", folder, set(json.loads(before_json)), None)
        reference = {row["node_id"]: float(row["pressure_bar"]) for row in rows(folder / "results_nodes.csv")}
        if set(reference) != set(baseline["pressure_bar"]) or not all(map(math.isfinite, reference.values())):
            raise ValueError("发布基准与可求解节点集合不一致")
        max_difference = max(abs(actual - reference[node]) for node, actual in baseline["pressure_bar"].items())
        checks = {"solver_version": version("GasNetSim"), "solver_revision": os.getenv("SICILY_SOLVER_REVISION"),
                  "reference_max_pressure_error_bar": max_difference,
                  "before_max_nodal_flow_error_sm3_s": baseline["max_nodal_flow_error_sm3_s"]}
        failures = []
        if max_difference > REFERENCE_MAX_ERROR_BAR:
            failures.append(f"与发布基准的最大压力差 {max_difference:.3f} bar")
        if baseline["max_nodal_flow_error_sm3_s"] > MAX_NODAL_STANDARD_FLOW_ERROR:
            failures.append(f"基准节点标准流量最大残差 {baseline['max_nodal_flow_error_sm3_s']:.4g} Sm³/s")
        if failures:
            result = {"status": "not_estimable", "reason": "基准复核未通过。 " + "；".join(failures), **checks}
        elif not edge:
            result = {"status": "ok", **checks, "before_pressure_bar": baseline["pressure_bar"],
                      "before_flow_sm3_s": baseline["flow_sm3_s"]}
        else:
            fault = solve(folder / "after", folder, set(json.loads(after_json)), edge)
            if fault["max_nodal_flow_error_sm3_s"] > MAX_NODAL_STANDARD_FLOW_ERROR:
                result = {"status": "not_estimable", **checks, "reason":
                          f"退出工况节点标准流量最大残差 {fault['max_nodal_flow_error_sm3_s']:.4g} Sm³/s，复核未通过。"}
            else:
                result = {"status": "ok", **checks,
                          "before_pressure_bar": baseline["pressure_bar"], "after_pressure_bar": fault["pressure_bar"],
                          "before_flow_sm3_s": baseline["flow_sm3_s"], "after_flow_sm3_s": fault["flow_sm3_s"],
                          "after_max_nodal_flow_error_sm3_s": fault["max_nodal_flow_error_sm3_s"]}
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        result = {"status": "not_estimable", "reason": str(exc)}
    print(json.dumps(result, allow_nan=False, ensure_ascii=True))


if __name__ == "__main__":
    main()
