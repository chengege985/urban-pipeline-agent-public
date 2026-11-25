"""Synthetic gas graph candidate; never imports the platform or water algorithms.

Node-cut valves and undirected edges preserve the pinned HTML's graph semantics.
Every result is connectivity evidence, never pressure, supply or safety clearance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, deque
from pathlib import Path

VERSION = "gas-graph-candidate-0.1.0"


def _fail(message):
    raise ValueError(message)


def _finite(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(f"{name} must be a finite number, not bool or text")
    try:
        converted = float(value)
    except (OverflowError, ValueError):
        _fail(f"{name} exceeds finite numeric range")
    if not math.isfinite(converted):
        _fail(f"{name} must be finite")
    return value


def _number(value, name, *, integer=False, positive=False):
    _finite(value, name)
    if value < 0 or (positive and value == 0) or (integer and int(value) != value):
        _fail(f"{name} outside its nonnegative/integer range")
    return value


def _objects(value, name, key, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{name} must be {'nonempty ' if nonempty else ''}list")
    result = {}
    for row in value:
        if not isinstance(row, dict):
            _fail(f"{name} item must be object")
        identifier = row.get(key)
        if not isinstance(identifier, str) or not identifier.startswith("syn-gas-"):
            _fail(f"{name}.{key} must be an explicit syn-gas ID")
        if identifier in result:
            _fail(f"duplicate {key}: {identifier}")
        result[identifier] = row
    return result


def _refs(value, allowed, name, *, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value):
        _fail(f"{name} must be {'nonempty ' if nonempty else ''}list")
    if any(not isinstance(v, str) for v in value) or len(set(value)) != len(value):
        _fail(f"{name} contains a non-string or duplicate ID")
    missing = set(value) - set(allowed)
    if missing:
        _fail(f"{name} has unknown IDs: {sorted(missing)}")
    return set(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _maps(network):
    return {name: {row[key]: row for row in network[name]} for name, key in [
        ("nodes", "node_id"), ("edges", "edge_id"), ("pipes", "pipe_id"),
        ("valves", "valve_id"), ("sources", "source_id"),
        ("consumers", "consumer_id"), ("services", "service_id"),
    ]}


def validate_network(network):
    """Validate structure. Isolates are explicit diagnostics, not invented edges."""
    if not isinstance(network, dict):
        _fail("network must be object")
    if network.get("schema_version") != "gas-network-candidate-0.1.0":
        _fail("unsupported network schema_version")
    if network.get("data_mode") != "synthetic" or network.get("industry") != "gas":
        _fail("candidate accepts synthetic gas fixtures only")
    if network.get("model_scope") != "graph_connectivity_only":
        _fail("model_scope must remain graph_connectivity_only")
    provenance = network.get("source_provenance")
    if not isinstance(provenance, dict):
        _fail("source_provenance required")
    for field in ["sha256", "construction_block_sha256"]:
        digest = provenance.get(field)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            _fail(f"source_provenance.{field} must contain complete SHA-256")
    if not isinstance(network.get("network_id"), str) or not network["network_id"].startswith("syn-gas-"):
        _fail("network_id must be explicit synthetic ID")
    maps = {}
    for name, key in [("nodes", "node_id"), ("edges", "edge_id"), ("pipes", "pipe_id"),
                      ("valves", "valve_id"), ("sources", "source_id"),
                      ("consumers", "consumer_id"), ("services", "service_id")]:
        maps[name] = _objects(network.get(name), name, key, nonempty=name not in {"valves"})
    nodes, edges, pipes = maps["nodes"], maps["edges"], maps["pipes"]
    vals, sources = maps["valves"], maps["sources"]
    consumers, services = maps["consumers"], maps["services"]
    all_ids = [i for group in maps.values() for i in group]
    if len(set(all_ids)) != len(all_ids):
        _fail("entity ID spaces must be distinct")
    incidence = {n: set() for n in nodes}
    for node_id, node in nodes.items():
        if not isinstance(node.get("node_type"), str) or node["node_type"] not in {"junction", "regulator", "valve", "source"}:
            _fail(f"invalid node_type: {node_id}")
        layout = node.get("layout")
        if not isinstance(layout, dict) or layout.get("coordinate_system") != "schematic" or layout.get("unit") != "px":
            _fail(f"schematic px layout required: {node_id}")
        for axis in ["x", "y"]:
            value = layout.get(axis)
            _finite(value, f"layout.{axis}")
    for eid, edge in edges.items():
        a, b = edge.get("from_node_id"), edge.get("to_node_id")
        if not isinstance(a, str) or not isinstance(b, str) or a not in nodes or b not in nodes or a == b:
            _fail(f"edge has unknown/self endpoint: {eid}")
        if edge.get("direction") != "undirected_connectivity_only":
            _fail(f"physical direction is unsupported: {eid}")
        if not isinstance(edge.get("initial_state"), str) or edge["initial_state"] not in {"enabled", "disabled"}:
            _fail(f"invalid edge initial_state: {eid}")
        pid = edge.get("pipe_id")
        if not isinstance(pid, str) or pid not in pipes or pipes[pid].get("edge_id") != eid:
            _fail(f"pipe/edge mapping mismatch: {eid}")
        incidence[a].add(eid); incidence[b].add(eid)
    for pid, pipe in pipes.items():
        eid = pipe.get("edge_id")
        if not isinstance(eid, str) or eid not in edges or edges[eid].get("pipe_id") != pid:
            _fail(f"edge/pipe mapping mismatch: {pid}")
        _number(pipe.get("diameter_mm"), "diameter_mm", positive=True)
    valve_nodes = []
    for vid, valve in vals.items():
        n = valve.get("node_id")
        if not isinstance(n, str) or n not in nodes or nodes[n]["node_type"] != "valve":
            _fail(f"valve node does not identify a valve: {vid}")
        if (valve.get("cut_semantics") != "node_cut" or not isinstance(valve.get("initial_state"), str)
                or valve["initial_state"] not in {"open", "closed"}):
            _fail(f"node_cut and open/closed state required: {vid}")
        if _refs(valve.get("incident_edge_ids"), edges, "incident_edge_ids") != incidence[n]:
            _fail(f"valve incidence differs from explicit graph: {vid}")
        valve_nodes.append(n)
    if len(set(valve_nodes)) != len(valve_nodes) or set(valve_nodes) != {n for n in nodes if nodes[n]["node_type"] == "valve"}:
        _fail("one explicit Valve record per valve Node is required")
    source_nodes = []
    for sid, source in sources.items():
        n = source.get("node_id")
        if not isinstance(n, str) or n not in nodes or nodes[n]["node_type"] != "source":
            _fail(f"source node mismatch: {sid}")
        if not isinstance(source.get("enabled"), bool):
            _fail(f"source enabled must be boolean: {sid}")
        source_nodes.append(n)
    if len(set(source_nodes)) != len(source_nodes) or set(source_nodes) != {n for n in nodes if nodes[n]["node_type"] == "source"}:
        _fail("one explicit Source record per source Node is required")
    for cid, consumer in consumers.items():
        if not isinstance(consumer.get("node_id"), str) or consumer["node_id"] not in nodes:
            _fail(f"consumer references unknown node: {cid}")
        kind = consumer.get("consumer_kind")
        if not isinstance(kind, str) or kind not in {"household", "business", "facility"} or consumer.get("count_unit") != kind:
            _fail(f"consumer kind/count unit mismatch: {cid}")
        _number(consumer.get("count"), "consumer count", integer=True, positive=True)
    linked = []
    for sid, service in services.items():
        cid = service.get("consumer_id")
        if not isinstance(cid, str) or cid not in consumers:
            _fail(f"service references unknown consumer: {sid}")
        _refs(service.get("node_ids"), nodes, "service node_ids", nonempty=True)
        if consumers[cid]["node_id"] not in service["node_ids"]:
            _fail(f"service access conflicts with explicit consumer node: {sid}")
        if not isinstance(service.get("supply_rule"), str) or service["supply_rule"] not in {"any", "all"}:
            _fail(f"supply_rule must be any/all: {sid}")
        if service.get("service_kind") != consumers[cid]["consumer_kind"]:
            _fail(f"service/consumer kind mismatch: {sid}")
        linked.append(cid)
    if len(set(linked)) != len(linked) or set(linked) != set(consumers):
        _fail("exactly one Service per aggregate Consumer is required to avoid double counting")
    baseline = network.get("baseline")
    if not isinstance(baseline, dict):
        _fail("explicit baseline required")
    _refs(baseline.get("disabled_edge_ids"), edges, "baseline disabled edges")
    _refs(baseline.get("closed_valve_ids"), vals, "baseline closed valves")
    _refs(baseline.get("disabled_source_ids"), sources, "baseline disabled sources")
    units = _objects(network.get("isolation_units"), "isolation_units", "unit_id", nonempty=False)
    used_edges = []
    for uid, unit in units.items():
        ue = _refs(unit.get("edge_ids"), edges, "unit edges", nonempty=True)
        uv = _refs(unit.get("valve_ids"), vals, "unit valves")
        expected_valves = {v for v in vals if incidence[vals[v]["node_id"]] & ue}
        if uv != expected_valves:
            _fail(f"unit boundary valves disagree with explicit incidence: {uid}")
        used_edges.extend(ue)
    if units and (len(set(used_edges)) != len(used_edges) or set(used_edges) != set(edges)):
        _fail("prototype isolation units must partition edges")
    isolated = sorted(n for n, es in incidence.items() if not es)
    warnings = []
    if isolated:
        warnings.append("Structurally isolated nodes are retained and cannot be assumed supplied.")
    return {"status": "valid_with_warnings" if warnings else "valid", "algorithm_version": VERSION,
            "counts": {name: len(group) for name, group in maps.items()},
            "isolated_node_ids": isolated, "warnings": warnings}


def _reachable(network, *, disabled_edges=(), closed_valves=(), disabled_sources=()):
    m = _maps(network)
    blocked_nodes = {m["valves"][v]["node_id"] for v in closed_valves}
    blocked_nodes |= {v["node_id"] for v in m["valves"].values() if v["initial_state"] == "closed"}
    cut_edges = set(disabled_edges) | {e for e, v in m["edges"].items() if v["initial_state"] == "disabled"}
    adjacency = {n: [] for n in m["nodes"]}
    for eid, e in m["edges"].items():
        a, b = e["from_node_id"], e["to_node_id"]
        if eid not in cut_edges and a not in blocked_nodes and b not in blocked_nodes:
            adjacency[a].append(b); adjacency[b].append(a)
    seen = {s["node_id"] for sid, s in m["sources"].items()
            if s["enabled"] and sid not in disabled_sources and s["node_id"] not in blocked_nodes}
    queue = deque(sorted(seen))
    while queue:
        for other in adjacency[queue.popleft()]:
            if other not in seen:
                seen.add(other); queue.append(other)
    return seen


def _service_reachable(service, reached):
    checks = [n in reached for n in service["node_ids"]]
    return any(checks) if service["supply_rule"] == "any" else all(checks)


def _state(network, normal, event):
    baseline = network["baseline"]
    reached = _reachable(network,
        disabled_edges=set(baseline["disabled_edge_ids"]) | set(event["fault_edge_ids"]) | set(event["disabled_edge_ids"]),
        closed_valves=set(baseline["closed_valve_ids"]) | set(event["closed_valve_ids"]),
        disabled_sources=set(baseline["disabled_source_ids"]))
    m = _maps(network)
    services, totals = [], Counter()
    for sid, service in m["services"].items():
        was = _service_reachable(service, normal)
        now = _service_reachable(service, reached)
        state = "graph_reachable" if now else "newly_graph_unreachable" if was else "already_graph_unreachable"
        consumer = m["consumers"][service["consumer_id"]]
        if was and not now:
            totals[consumer["count_unit"]] += consumer["count"]
        services.append({"service_id": sid, "consumer_id": service["consumer_id"],
            "node_ids": list(service["node_ids"]), "supply_rule": service["supply_rule"],
            "status": state, "physical_supply_status": "unknown",
            "previously_graph_reachable": was, "currently_graph_reachable": now})
    return {"event_id": event["event_id"], "time_hours": event["time_hours"], "kind": event["kind"],
        "current_reachable_node_ids": sorted(reached),
        "current_unreachable_node_ids": sorted(set(m["nodes"]) - reached),
        "normal_unreachable_node_ids": sorted(set(m["nodes"]) - normal),
        "newly_unreachable_node_ids": sorted(normal - reached),
        "services": services,
        "newly_affected_service_ids": sorted(s["service_id"] for s in services if s["status"] == "newly_graph_unreachable"),
        "potentially_interrupted_consumers": {"household": totals["household"], "business": totals["business"], "facility": totals["facility"]},
        "model_scope": "graph_connectivity_only", "physical_supply_verified": False}


def evaluate_case(network, case):
    """Pure calculation on explicit synthetic full-state slices; no I/O or actions."""
    validation = validate_network(network)
    if not isinstance(case, dict) or case.get("schema_version") != "gas-case-candidate-0.1.0":
        _fail("unsupported case schema_version")
    if case.get("data_mode") != "synthetic" or case.get("network_id") != network["network_id"]:
        _fail("case mode/network identity mismatch")
    if not isinstance(case.get("case_id"), str) or not case["case_id"].startswith("syn-gas-"):
        _fail("case_id required")
    window = _number(case.get("analysis_window_hours"), "analysis_window_hours", positive=True)
    m = _maps(network)
    scenario, plan = case.get("scenario"), case.get("isolation_plan")
    if not isinstance(scenario, dict) or not isinstance(plan, dict):
        _fail("scenario and isolation_plan required")
    if not isinstance(scenario.get("scenario_id"), str) or not scenario["scenario_id"].startswith("syn-gas-"):
        _fail("scenario_id required")
    faults = _refs(scenario.get("fault_edge_ids"), m["edges"], "scenario fault edges", nonempty=True)
    if plan.get("scenario_id") != scenario["scenario_id"] or plan.get("semantics") != "node_cut_plus_explicit_unit_edges":
        _fail("isolation plan scenario/semantics mismatch")
    if not isinstance(plan.get("plan_id"), str) or not plan["plan_id"].startswith("syn-gas-"):
        _fail("plan_id required")
    plan_valves = _refs(plan.get("closed_valve_ids"), m["valves"], "plan valves")
    plan_edges = _refs(plan.get("disabled_edge_ids"), m["edges"], "plan disabled edges")
    events = _objects(case.get("events"), "events", "event_id")
    previous = -1
    for event in events.values():
        time = _number(event.get("time_hours"), "event time_hours")
        if time <= previous or time > window:
            _fail("event times must strictly increase within analysis window")
        previous = time
        if (event.get("origin") != "new_synthetic_case" or not isinstance(event.get("kind"), str)
                or event["kind"] not in {"given_fault", "given_isolation", "synthetic_observation"}):
            _fail("event must be explicit supported new_synthetic_case state")
        if _refs(event.get("fault_edge_ids"), m["edges"], "event faults") != faults:
            _fail("event fault set conflicts with given scenario")
        ev = _refs(event.get("closed_valve_ids"), m["valves"], "event valves")
        ee = _refs(event.get("disabled_edge_ids"), m["edges"], "event disabled edges")
        if event["kind"] == "given_fault":
            if ev or ee or time != 0:
                _fail("given_fault initial slice must be time zero without isolation")
        elif ev != plan_valves or ee != plan_edges:
            _fail("event isolation conflicts with given plan")
    first = next(iter(events.values()))
    if first["kind"] != "given_fault" or not any(e["kind"] == "given_isolation" for e in events.values()):
        _fail("case requires initial fault and explicit isolation event")
    task_end = case.get("task_end_hours")
    if task_end is not None:
        _number(task_end, "task_end_hours")
        last_isolation = max(e["time_hours"] for e in events.values() if e["kind"] == "given_isolation")
        if task_end < last_isolation:
            _fail("task_end_hours precedes last given_isolation event")
    baseline = network["baseline"]
    normal = _reachable(network, disabled_edges=baseline["disabled_edge_ids"],
                        closed_valves=baseline["closed_valve_ids"], disabled_sources=baseline["disabled_source_ids"])
    states = [_state(network, normal, event) for event in events.values()]
    return {"schema_version": "gas-result-candidate-0.1.0", "algorithm_version": VERSION,
        "status": "partial", "data_mode": "synthetic", "model_status": "candidate",
        "validation_status": "not_business_validated", "semantic": "graph_connectivity_only",
        "identity": {"network_id": network["network_id"], "case_id": case["case_id"],
                     "scenario_id": scenario["scenario_id"], "plan_id": plan["plan_id"]},
        "evidence": {"network_content_sha256": _digest(network), "case_content_sha256": _digest(case),
                     "html_source_sha256": network.get("source_provenance", {}).get("sha256")},
        "validation": validation, "analysis_window_hours": window,
        "states": states, "current": states[-1],
        "metrics": {
            "timeline_events": {"status": "available", "value": len(states), "unit": "event"},
            "plan_completion_hours": {"status": "insufficient_data" if task_end is None else "available",
                "value": task_end, "unit": "h", "in_analysis_window": None if task_end is None else task_end <= window,
                "reason": "Explicit task end only; never last event time. An available completion time may be outside the analysis window."},
            "graph_service_restoration_hours": {"status": "insufficient_data", "value": None, "unit": "h",
                "reason": "No restoration event contract in this first fixed-fault candidate."},
            "actual_gas_restoration_hours": {"status": "unsupported", "value": None, "unit": "h",
                "reason": "No physical supply, inspection or safety-release model."},
            "pressure": {"status": "unsupported", "value": None, "unit": None},
            "flow": {"status": "unsupported", "value": None, "unit": None},
            "failure_probability": {"status": "unsupported", "value": None, "unit": None},
        },
        "safety": {"field_operation_instruction": False, "safe_restoration_decision": False,
                   "physical_supply_verified": False, "simulation_only": True},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("network", type=Path)
    parser.add_argument("case", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = evaluate_case(json.loads(args.network.read_text(encoding="utf-8")),
                           json.loads(args.case.read_text(encoding="utf-8")))
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
