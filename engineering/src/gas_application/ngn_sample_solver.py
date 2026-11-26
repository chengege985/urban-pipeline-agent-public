"""Isolated pandapipes research worker. All periods are independent steady states."""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict, deque

import pandapipes as pp

MAX_MASS_ERROR_KG_S = 1e-6
VOLUME_REFERENCE_TEMPERATURE_K = 293.15


def paths(data: dict, edge_id: str | None) -> dict:
    adjacency = defaultdict(list)
    for pipe in data["pipes"]:
        if pipe["in_service"] and str(pipe["name"]) != edge_id:
            a, b = pipe["from_junction"], pipe["to_junction"]
            adjacency[a].append((b, pipe["name"]))
            adjacency[b].append((a, pipe["name"]))
    reached = {src["junction"]: {"source": src["name"], "nodes": [src["junction"]], "pipes": []}
               for src in data["sources"] if src["in_service"]}
    frontier = deque(reached)
    while frontier:
        current = frontier.popleft()
        path = reached[current]
        for neighbor, pipe in adjacency[current]:
            if neighbor not in reached:
                reached[neighbor] = dict(source=path["source"], nodes=[*path["nodes"], neighbor],
                                         pipes=[*path["pipes"], pipe])
                frontier.append(neighbor)
    return reached


def solve(data: dict, period: str, edge_id: str | None) -> dict:
    reached = paths(data, edge_id)
    net = pp.create_empty_network(fluid="methane")
    density = float(pp.get_fluid(net).get_density(VOLUME_REFERENCE_TEMPERATURE_K))
    junctions = {r["name"]: pp.create_junction(net, pn_bar=r["pn_bar"], tfluid_k=r["tfluid_k"], name=r["name"])
                 for r in data["nodes"] if r["name"] in reached}
    for r in data["sources"]:
        if r["in_service"]:
            pp.create_ext_grid(net, junctions[r["junction"]], p_bar=r["p_bar"], t_k=r["t_k"],
                               type="pt", name=r["name"])
    pipes = {str(r["name"]): pp.create_pipe_from_parameters(
        net, junctions[r["from_junction"]], junctions[r["to_junction"]],
        length_km=r["length_km"], diameter_m=r["diameter_m"], k_mm=r["k_mm"], name=r["name"])
        for r in data["pipes"] if r["in_service"] and str(r["name"]) != edge_id
        and r["from_junction"] in reached and r["to_junction"] in reached}
    demands = data["loads"].get(period)
    unavailable = []
    for r in data["sinks"]:
        if r["junction"] not in reached:
            unavailable.append(r["name"])
            continue
        mass = demands[r["name"]] * density if demands is not None else r["mdot_kg_per_s"]
        pp.create_sink(net, junctions[r["junction"]], mdot_kg_per_s=mass, name=r["name"])
    pp.pipeflow(net, mode="hydraulics", friction_model="colebrook")
    p = {key: float(net.res_junction.at[idx, "p_bar"]) for key, idx in junctions.items()}
    q = {key: float(net.res_pipe.at[idx, "mdot_from_kg_per_s"]) for key, idx in pipes.items()}
    if not net.converged or not p or not all(math.isfinite(x) for x in [*p.values(), *q.values()]):
        raise ValueError("压力或流量未收敛或不为有限数")
    nodal = defaultdict(float)
    for r in data["pipes"]:
        if str(r["name"]) in q:
            nodal[r["from_junction"]] -= q[str(r["name"])]
            nodal[r["to_junction"]] += q[str(r["name"])]
    for idx, row in net.sink.iterrows():
        key = net.junction.at[row["junction"], "name"]
        nodal[key] -= float(net.res_sink.at[idx, "mdot_kg_per_s"])
    for idx, row in net.ext_grid.iterrows():
        key = net.junction.at[row["junction"], "name"]
        nodal[key] -= float(net.res_ext_grid.at[idx, "mdot_kg_per_s"])
    residual = max((abs(value) for value in nodal.values()), default=0.0)
    if not math.isfinite(residual) or residual > MAX_MASS_ERROR_KG_S:
        raise ValueError(f"节点质量平衡未通过：{residual:.3g} kg/s")
    return dict(pressure_bar_g=p, flow_kg_s=q, unavailable_sinks=unavailable,
                reachable_nodes=list(reached), max_nodal_mass_error_kg_s=residual,
                sink_paths={r["name"]: reached[r["junction"]] for r in data["sinks"] if r["junction"] in reached})


def execute(data: dict, task: dict) -> dict:
    result = dict(status="ok", solver=f"pandapipes {pp.__version__}",
                  friction_model="colebrook", gas="methane",
                  assumed_volume_reference_temperature_k=VOLUME_REFERENCE_TEMPERATURE_K,
                  volume_to_mass_kg_m3=float(pp.get_fluid(pp.create_empty_network(fluid="methane"))
                                             .get_density(VOLUME_REFERENCE_TEMPERATURE_K)))
    if task["trend"]:
        series = {}
        for period in sorted(data["loads"], key=int):
            try:
                series[period] = dict(status="ok", **solve(data, period, None))
            except (ValueError, RuntimeError) as exc:
                series[period] = dict(status="not_estimable", reason=str(exc))
        return dict(**result, series=series)
    before = solve(data, task["period"], None)
    return dict(**result, before=before, after=solve(data, task["period"], task["edge_id"]) if task["edge_id"] else None)


if __name__ == "__main__":
    sys.stdin.reconfigure(encoding="utf-8")
    try:
        result = execute(**json.load(sys.stdin))
    except (ValueError, RuntimeError) as exc:
        result = dict(status="not_estimable", reason=str(exc))
    print(json.dumps(result, allow_nan=False))
