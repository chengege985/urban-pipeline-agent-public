"""Worker entry point for the separately pinned Schutterwald solver."""
import json
import math
import sys


def solve_case(path, request):
    import pandapipes as pp

    if pp.__version__ != "0.12.0":
        raise ValueError("Unverified pandapipes version")
    net = pp.from_json(path)
    reached = request.get("reached", net.junction.index.tolist())
    if request["fault"] is not None:
        net.pipe.loc[request["fault"], "in_service"] = False
    net.pipe["in_service"] &= net.pipe.from_junction.isin(reached) & net.pipe.to_junction.isin(reached)
    net.junction["in_service"] &= net.junction.index.isin(reached)
    net.sink["in_service"] &= net.sink.junction.isin(reached)
    pp.pipeflow(net, mode="hydraulics", max_iter_hyd=30, tol_p=1e-6, tol_m=1e-6, tol_res=1e-7)
    pressure = net.res_junction.loc[reached, "p_bar"]
    flow = net.res_pipe.loc[net.pipe.in_service, "mdot_from_kg_per_s"]
    supplied = -float(net.res_ext_grid.mdot_kg_per_s.sum())
    demand = float((net.sink.loc[net.sink.in_service, "mdot_kg_per_s"] *
                    net.sink.loc[net.sink.in_service, "scaling"]).sum())
    residual = abs(supplied - demand)
    if (not net.converged or residual > 1e-6 or not math.isfinite(residual)
            or not all(math.isfinite(value) for value in [*pressure, *flow])):
        raise ValueError("Steady state validation failed")
    return {"status": "ok", "solver": f"pandapipes-{pp.__version__}",
            "pressure_bar": {str(key): float(value) for key, value in pressure.items()},
            "mass_flow_kg_s": {f"p-{key}": float(value) for key, value in flow.items()},
            "supplied_kg_s": supplied, "connected_demand_kg_s": demand, "mass_balance_error_kg_s": residual}


def solve(path, request):
    if request["fault"] is None:
        base = solve_case(path, request)
        return {**base, "baseline_pressure_bar": base["pressure_bar"]}
    base = solve_case(path, {"fault": None})
    try:
        fault = solve_case(path, request)
    except (ValueError, RuntimeError):
        return {"status": "failed", "reason": "Steady-state outage solve failed",
                "baseline_pressure_bar": base["pressure_bar"]}
    return {**fault, "baseline_pressure_bar": base["pressure_bar"]}


if __name__ == "__main__":
    print(json.dumps(solve(sys.argv[1], json.load(sys.stdin)), allow_nan=False))
