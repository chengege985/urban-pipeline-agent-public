"""Candidate quantity-only time-value contract; never imported by the product.

Constant demand per service, explicit half-open schedules, independent additive
delivery. No hydraulic, water-quality, dispatch optimization or utility claims.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime
from typing import Any

VERSION = "service-time-value-0.1"
EPS = 1e-9


def _number(value: Any, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: finite numeric value required")
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value <= 0):
        raise ValueError(f"{name}: invalid nonnegative/positive number")
    return value


def _id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: nonempty identifier required")
    return value


def _evidence(obj: dict) -> None:
    if obj.get("evidence_ref") is not None and not isinstance(obj["evidence_ref"], str):
        raise ValueError("evidence_ref must be a string or null")


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()).hexdigest()


def _metric(low: float, high: float, unit: str) -> dict:
    if not all(math.isfinite(v) for v in (low, high)):
        raise ValueError("derived integral is not finite")
    exact = abs(high - low) <= EPS
    return {"status": "succeeded" if exact else "insufficient_data",
            "value": low if exact else None, "lower": low, "upper": high,
            "unit": unit}


def _prepare(request: dict) -> tuple[dict, list[dict]]:
    if not isinstance(request, dict):
        raise ValueError("request must be an object")
    r = copy.deepcopy(request)
    h = _number(r["horizon_hours"], "horizon_hours", positive=True)
    r["horizon_hours"] = h
    if r.get("data_mode") != "synthetic":
        raise NotImplementedError("candidate validation accepts synthetic data only")
    if r.get("criterion") != "quantity_only_independent_delivery":
        raise NotImplementedError("only declared independent quantity delivery is supported")
    _id(r["plan_id"], "plan_id")
    for name in ("context", "services", "sources", "resource_capacities"):
        if not isinstance(r[name], dict):
            raise ValueError(f"{name}: object required")
    for name in ("snapshot_id", "as_of", "fault_id", "isolation_id", "demand_version"):
        _id(r["context"][name], f"context.{name}")
    as_of = datetime.fromisoformat(r["context"]["as_of"])
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of requires an explicit UTC offset")
    for sid, service in r["services"].items():
        _id(sid, "service_id")
        if not isinstance(service, dict):
            raise ValueError("service: object required")
        service["demand_m3h"] = _number(service["demand_m3h"], sid + ".demand")
        service["required_ratio"] = _number(service["required_ratio"], sid + ".ratio", positive=True)
        if service["required_ratio"] > 1:
            raise ValueError("required_ratio must be at most 1")
    for rid, cap in r["resource_capacities"].items():
        _id(rid, "resource_id")
        if cap is not None:
            r["resource_capacities"][rid] = _number(cap, "resource capacity")
    for sid, source in r["sources"].items():
        _id(sid, "source_id")
        if not isinstance(source, dict) or not isinstance(source.get("resources"), dict):
            raise ValueError("source and source.resources: objects required")
        _evidence(source)
        for key in ("capacity_m3h", "volume_m3"):
            if source.get(key) is not None:
                source[key] = _number(source[key], sid + "." + key)
        if source.get("independent_of_network") is not True:
            raise NotImplementedError("source independence requires an explicit declaration")
        for rid, qty in source["resources"].items():
            _id(rid, "resource_id")
            source["resources"][rid] = _number(qty, sid + ".resource quantity", positive=True)
        source["available_from_h"] = _number(source["available_from_h"], "available_from")
        source["available_to_h"] = _number(source["available_to_h"], "available_to")
        if source["available_to_h"] <= source["available_from_h"]:
            raise ValueError("invalid source availability interval")
    if not isinstance(r["segments"], list) or any(not isinstance(s, dict) for s in r["segments"]):
        raise ValueError("segments: list of objects required")
    segments = sorted(r["segments"], key=lambda s: _number(s["start_h"], "start"))
    covered: list[dict] = []
    last = 0.0
    for s in segments:
        start = _number(s["start_h"], "start")
        end = _number(s["end_h"], "end")
        s["start_h"], s["end_h"] = start, end
        if start < last or end <= start or end > h:
            raise ValueError("overlapping, reversed or out-of-window interval")
        if start > last:
            covered.append({"start_h": last, "end_h": start, "network_m3h": {},
                            "allocations": [], "gap": True})
        if not isinstance(s["network_m3h"], dict) or not isinstance(s["allocations"], list):
            raise ValueError("segment network_m3h and allocations have invalid structure")
        _evidence(s)
        if not set(s["network_m3h"]).issubset(r["services"]):
            raise ValueError("unknown service in network delivery")
        for sid, q in s["network_m3h"].items():
            if q is not None:
                s["network_m3h"][sid] = _number(q, sid + ".network flow")
        for a in s["allocations"]:
            if not isinstance(a, dict):
                raise ValueError("allocation: object required")
            _evidence(a)
            _id(a["service_id"], "allocation.service_id")
            _id(a["source_id"], "allocation.source_id")
            if a["service_id"] not in r["services"] or a["source_id"] not in r["sources"]:
                raise ValueError("unknown service or source in allocation")
            a["flow_m3h"] = _number(a["flow_m3h"], "allocated flow")
            if a.get("connection_ready") is not None and not isinstance(a["connection_ready"], bool):
                raise ValueError("connection_ready must be boolean or null")
        covered.append(s)
        last = end
    if last < h:
        covered.append({"start_h": last, "end_h": h, "network_m3h": {},
                        "allocations": [], "gap": True})
    return r, covered


def _evaluate(request: dict) -> dict:
    r, segments = _prepare(request)
    result = {"contract_version": VERSION, "data_mode": "synthetic", "model_status": "candidate",
              "plan_id": r["plan_id"], "criterion": r["criterion"],
              "request_fingerprint": _fingerprint(r),
              "qualified_water_service": "unsupported",
              "limitations": ["Declared quantity only; no pressure, quality or delivery-route verification.",
                              "Explicit allocations are checked, not optimized.",
                              "All allocated volume consumes inventory, including oversupply."],
              "issues": [], "per_service": {}}
    if not r["services"]:
        return {**result, "status": "not_applicable", "constraint_status": "not_applicable", "metrics": {}}
    issues = result["issues"]
    violations: list[str] = []
    unknown_sources: set[str] = set()
    unknown_connections: list[str] = []
    volumes = {sid: 0.0 for sid in r["sources"]}
    for n, segment in enumerate(segments):
        totals = {sid: 0.0 for sid in r["sources"]}
        for a in segment["allocations"]:
            q = float(a["flow_m3h"])
            source = r["sources"][a["source_id"]]
            totals[a["source_id"]] += q
            volumes[a["source_id"]] += q * (segment["end_h"] - segment["start_h"])
            if q > 0 and (segment["start_h"] < source["available_from_h"]
                          or segment["end_h"] > source["available_to_h"]):
                violations.append(f"source_unavailable:{n}:{a['source_id']}")
            if q > 0 and a.get("connection_ready") is False:
                violations.append(f"connection_not_ready:{n}:{a['service_id']}")
            elif q > 0 and a.get("connection_ready") is None:
                unknown_connections.append(f"connection_unverified:{n}:{a['service_id']}")
        usage: dict[str, float] = {}
        for sid, q in totals.items():
            if q <= 0:
                continue
            source = r["sources"][sid]
            if source.get("capacity_m3h") is None:
                unknown_sources.add(sid)
                issues.append(f"missing_source_capacity:{sid}")
            elif q > source["capacity_m3h"] + EPS:
                violations.append(f"capacity_exceeded:{n}:{sid}")
            for resource, qty in source["resources"].items():
                usage[resource] = usage.get(resource, 0.0) + qty
        for resource, qty in usage.items():
            cap = r["resource_capacities"].get(resource)
            if cap is None:
                issues.append(f"unknown_resource_capacity:{resource}")
                unknown_sources.update(sid for sid, q in totals.items()
                                       if q > 0 and resource in r["sources"][sid]["resources"])
            elif qty > cap + EPS:
                violations.append(f"resource_conflict:{n}:{resource}")
    for sid, volume in volumes.items():
        if not math.isfinite(volume):
            raise ValueError("derived allocated volume is not finite")
        if volume <= 0:
            continue
        source = r["sources"][sid]
        if source.get("volume_m3") is None:
            unknown_sources.add(sid)
            issues.append(f"missing_source_volume:{sid}")
        elif volume > source["volume_m3"] + EPS:
            violations.append(f"inventory_exceeded:{sid}")
        if not source.get("evidence_ref"):
            unknown_sources.add(sid)
            issues.append(f"missing_source_evidence:{sid}")
    if violations:
        return {**result, "status": "infeasible", "constraint_status": "fail",
                "violations": sorted(set(violations)), "metrics": {}, "allocated_volume_m3": volumes}
    keys = {"equivalent_shortfall_hours": "service*h", "unserved_volume_m3": "m3",
            "requirement_unmet_hours": "service*h", "complete_outage_hours": "service*h"}
    aggregate = {k: [0.0, 0.0] for k in keys}
    for sid, service in r["services"].items():
        demand, threshold = service["demand_m3h"], service["required_ratio"]
        sums = {k: [0.0, 0.0] for k in keys}
        timeline = []
        for n, segment in enumerate(segments):
            dt = segment["end_h"] - segment["start_h"]
            q_network = segment["network_m3h"].get(sid)
            known = q_network is not None and bool(segment.get("evidence_ref"))
            lower_flow = float(q_network) if known else 0.0
            upper_flow = float(q_network) if known else demand
            for a in segment["allocations"]:
                if a["service_id"] != sid or a["flow_m3h"] == 0:
                    continue
                if (a["source_id"] in unknown_sources or a.get("connection_ready") is not True
                        or not a.get("evidence_ref")):
                    # Unknown fulfilment cannot exceed the declared allocation.
                    upper_flow += a["flow_m3h"]
                else:
                    lower_flow += a["flow_m3h"]
                    upper_flow += a["flow_m3h"]
            if not math.isfinite(lower_flow) or not math.isfinite(upper_flow):
                raise ValueError("derived flow is not finite")
            if demand == 0:
                lo = hi = None
                shortfall = unmet = outage = (0.0, 0.0)
                state = "no_demand"
            else:
                lo = min(1.0, lower_flow / demand)
                hi = min(1.0, upper_flow / demand)
                shortfall = ((1 - hi) * dt, (1 - lo) * dt)
                unmet = (float(hi < threshold - EPS) * dt, float(lo < threshold - EPS) * dt)
                outage = (float(hi <= EPS) * dt, float(lo <= EPS) * dt)
                state = ("insufficient_data" if hi - lo > EPS else
                         "meets_quantity" if lo >= threshold - EPS else "quantity_shortfall")
                if hi - lo > EPS:
                    issues.append(f"unknown_delivery:{n}:{sid}")
            pairs = {"equivalent_shortfall_hours": shortfall,
                     "unserved_volume_m3": tuple(v * demand for v in shortfall),
                     "requirement_unmet_hours": unmet, "complete_outage_hours": outage}
            for k, pair in pairs.items():
                sums[k] = [sums[k][j] + pair[j] for j in (0, 1)]
                aggregate[k] = [aggregate[k][j] + pair[j] for j in (0, 1)]
            timeline.append({"start_h": segment["start_h"], "end_h": segment["end_h"],
                             "ratio_lower": lo, "ratio_upper": hi, "state": state})
        result["per_service"][sid] = {"timeline": timeline,
                                     "metrics": {k: _metric(*sums[k], unit) for k, unit in keys.items()}}
    result["metrics"] = {k: _metric(*aggregate[k], unit) for k, unit in keys.items()}
    result["allocated_volume_m3"] = volumes
    result["issues"] = sorted(set(issues + unknown_connections))
    constraints_unknown = bool(unknown_sources or unknown_connections)
    result["constraint_status"] = "unknown" if constraints_unknown else "pass"
    statuses = [m["status"] for m in result["metrics"].values()]
    result["status"] = ("succeeded" if all(s == "succeeded" for s in statuses) and not constraints_unknown
                        else "partial" if any(s == "succeeded" for s in statuses) else "insufficient_data")
    return result


def evaluate_service_time_value(request: dict) -> dict:
    """Compute quantity shortfall integrals and bounds, never a governance score."""
    try:
        return _evaluate(request)
    except NotImplementedError as exc:
        return {"status": "unsupported", "contract_version": VERSION, "reason": str(exc), "metrics": {}}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"status": "invalid_input", "contract_version": VERSION, "reason": str(exc), "metrics": {}}


def _comparison_basis(r: dict) -> dict:
    # A shared time grid is not required: merge adjacent equal network states.
    request, segments = _prepare(r)
    network = []
    for s in segments:
        state = {sid: s["network_m3h"].get(sid) if s.get("evidence_ref") else None
                 for sid in sorted(request["services"])}
        if network and network[-1][2] == state and network[-1][1] == s["start_h"]:
            network[-1][1] = s["end_h"]
        else:
            network.append([s["start_h"], s["end_h"], state])
    # Source availability is an external boundary, not a planned activation.
    # Early/late choices belong in allocations and must not change that boundary.
    sources = request["sources"]
    return {"context": request["context"], "horizon_hours": request["horizon_hours"],
            "criterion": request["criterion"], "services": request["services"],
            "sources": sources, "resource_capacities": request["resource_capacities"],
            "network": network}


def compare_backup_gain(baseline: dict, candidate: dict) -> dict:
    """Marginal backup value with identical network/demand/environment, signed."""
    a, b = evaluate_service_time_value(baseline), evaluate_service_time_value(candidate)
    errors = ("invalid_input", "unsupported", "infeasible", "not_applicable")
    if any(r["status"] in errors for r in (a, b)):
        return {"status": next(r["status"] for r in (a, b) if r["status"] in errors),
                "baseline": a, "candidate": b, "gains": {}}
    try:
        basis_a, basis_b = _comparison_basis(baseline), _comparison_basis(candidate)
        if basis_a != basis_b:
            return {"status": "invalid_input", "reason": "incompatible_counterfactual",
                    "gains": {}}
        gains = {}
        for k, ma in a["metrics"].items():
            mb = b["metrics"][k]
            gains[k] = _metric(ma["lower"] - mb["upper"], ma["upper"] - mb["lower"], ma["unit"])
        return {"status": "succeeded" if a["status"] == b["status"] == "succeeded" else "insufficient_data",
                "contract_version": VERSION, "comparison_basis": _fingerprint(basis_a),
                "baseline_fingerprint": a["request_fingerprint"],
                "candidate_fingerprint": b["request_fingerprint"],
                "baseline": a, "candidate": b, "gains": gains}
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        return {"status": "invalid_input", "reason": str(exc), "gains": {}}
