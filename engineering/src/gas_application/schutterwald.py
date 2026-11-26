"""Versioned Schutterwald research model and reproducible outage calculations."""
from collections import deque
from functools import lru_cache
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import subprocess
from uuid import uuid4

ID = "schutterwald"
SHA = "acf436c5b2a9aef4c5226ef8ea1f947cd10083978a682b10222fdbc017084ca6"
VERSION = "pandapipes-v0.12.0-" + SHA[:12]
SOURCE = "https://github.com/e2nIEE/pandapipes/blob/v0.12.0/src/pandapipes/networks/network_files/gas_net_schutterwald_1bar.json"
METHOD = "schutterwald_reachability_v2"


def model_path():
    value = os.environ.get("SCHUTTERWALD_MODEL_FILE")
    if not value:
        raise OSError("未配置 SCHUTTERWALD_MODEL_FILE")
    return Path(value)


def load_model():
    return _load(model_path().read_bytes())


@lru_cache(maxsize=1)
def _load(raw):
    if sha256(raw).hexdigest() != SHA:
        raise OSError("Schutterwald 原始文件 SHA-256 不匹配")
    root = json.loads(raw)["_object"]
    tables = {}
    for name in ("pipe", "junction", "junction_geodata", "ext_grid", "sink"):
        table = json.loads(root[name]["_object"])
        tables[name] = dict(zip(table["index"], (dict(zip(table["columns"], row)) for row in table["data"])))
    for pipe in tables["pipe"].values():
        if not all(pipe[k] in tables["junction"] for k in ("from_junction", "to_junction")):
            raise OSError("管段端点未能对应模型节点")
    for table in ("ext_grid", "sink"):
        if any(row["junction"] not in tables["junction"] for row in tables[table].values()):
            raise OSError("模型入口或负荷节点缺失")
    return tables


def paths(model, fault=None):
    adjacent = {node: [] for node in model["junction"]}
    for key, pipe in model["pipe"].items():
        if key == fault or not pipe["in_service"]:
            continue
        a, b = pipe["from_junction"], pipe["to_junction"]
        adjacent[a].append((b, key))
        adjacent[b].append((a, key))
    sources = sorted(row["junction"] for row in model["ext_grid"].values() if row["in_service"])
    previous = {node: None for node in sources}
    queue = deque(sources)
    while queue:
        node = queue.popleft()
        for neighbor, edge in adjacent[node]:
            if neighbor not in previous:
                previous[neighbor] = (node, edge)
                queue.append(neighbor)
    return previous


def explain(node, previous):
    nodes, pipes = [node], []
    while previous[node] is not None:
        node, edge = previous[node]
        nodes.append(node)
        pipes.append(edge)
    return {"source_node": str(node), "nodes": list(map(str, reversed(nodes))),
            "pipes": [f"p-{key}" for key in reversed(pipes)]}


def outages():
    load_model()
    return {"dataset_id": ID, "data_version": VERSION,
            "items": [{"id": f"p-{key}", "impact": count} for count, key in outage_scores(VERSION)]}


@lru_cache(maxsize=1)
def outage_scores(version):
    if version != VERSION:
        raise ValueError("Invalid benchmark version")
    model = load_model()
    sinks = [row["junction"] for row in model["sink"].values() if row["in_service"]]
    base = paths(model)
    scores = []
    for key, pipe in model["pipe"].items():
        if pipe["type"] == "pipe" and pipe["in_service"]:
            reached = paths(model, key)
            scores.append((sum(node in base and node not in reached for node in sinks), key))
    return sorted(scores, key=lambda value: (-value[0], value[1]))


def presets():
    scores = outage_scores(VERSION)
    zero = min(key for count, key in scores if not count)
    impact = min((count, key) for count, key in scores if count > 0)[1]
    return [{"id": "baseline", "label": "正常基线"},
            {"id": f"p-{zero}", "label": "管段失效 · 建筑仍可达"},
            {"id": f"p-{impact}", "label": "管段失效 · 部分建筑不可达"}]


def overview():
    model = load_model()
    sources = {p["junction"] for p in model["ext_grid"].values() if p["in_service"]}
    sinks = {p["junction"] for p in model["sink"].values() if p["in_service"]}
    return {"dataset_id": ID, "title": "Schutterwald 配气研究模型", "data_version": VERSION,
            "data_mode": "research_benchmark", "status": "ready", "source_refs": [SOURCE],
            "presets": presets(),
            "counts": {"distribution_pipes": sum(p["type"] == "pipe" for p in model["pipe"].values()),
                       "service_connections": sum(p["type"] == "house_connection" for p in model["pipe"].values()),
                       "model_buildings": len(model["sink"]), "supply_nodes": len(sources)},
            "nodes": [{"id": str(key), "node_type": "source" if key in sources else "building" if key in sinks else "junction",
                       "coordinates": [coords["x"], coords["y"]]}
                      for key, coords in model["junction_geodata"].items()],
            "edges": [{"id": f"p-{key}", "edge_type": p["type"], "from_node_id": str(p["from_junction"]),
                       "to_node_id": str(p["to_junction"]),
                       "source_properties": {"长度 km": str(p["length_km"]), "管径 mm": str(p["diameter_m"] * 1000)}}
                      for key, p in model["pipe"].items()]}


def hydraulic_result(fault, reached, excluded_demand):
    interpreter = os.environ.get("SCHUTTERWALD_SOLVER_PYTHON")
    if not interpreter:
        return {"status": "unavailable", "reason": "未配置独立 pandapipes 计算环境"}
    worker = Path(__file__).with_name("schutterwald_solver.py")
    try:
        run = subprocess.run([interpreter, "-X", "utf8", str(worker), str(model_path().resolve())],
                             input=json.dumps({"fault": fault, "reached": sorted(reached)}),
                             capture_output=True, text=True, encoding="utf-8", timeout=55, check=True)
        result = json.loads(run.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return {"status": "failed", "reason": "稳态求解未通过收敛、质量平衡或输出验证"}
    return {**result, "excluded_model_demand_kg_s": excluded_demand}


def execute(edge_id, version, solve=False):
    if version != VERSION:
        raise ValueError("数据版本已变化，请重新选择模型")
    model = load_model()
    fault = None
    if edge_id != "baseline":
        prefix, _, key = edge_id.partition("-")
        if prefix != "p" or not key.isdecimal():
            raise ValueError("请选择有效的配气管段")
        fault = int(key)
        edge = model["pipe"].get(fault)
        if not edge or edge["type"] != "pipe" or not edge["in_service"]:
            raise ValueError("仅支持有效的配气管段")
    before, after = paths(model), paths(model, fault)
    buildings = []
    for key, sink in sorted(model["sink"].items()):
        if sink["in_service"]:
            node = sink["junction"]
            buildings.append({"id": f"b-{key}", "node": str(node), "baseline": node in before,
                              "reachable": node in after, "path": explain(node, after) if node in after else None})
    excluded = math.fsum(s["mdot_kg_per_s"] * s["scaling"] for s in model["sink"].values()
                         if s["in_service"] and s["junction"] not in after)
    hydraulics = hydraulic_result(fault, after, excluded) if solve else {"status": "not_requested"}
    stable = {"dataset_id": ID, "data_version": VERSION, "method": METHOD, "edge_id": edge_id,
              "buildings": buildings, "unreachable_nodes": sorted(map(str, set(before) - set(after))),
              "hydraulics": hydraulics, "source": SOURCE, "input_sha256": SHA}
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result_hash = sha256(canonical.encode()).hexdigest()
    run_id = sha256(f"{VERSION}|{METHOD}|{edge_id}|{solve}|pandapipes-0.12.0".encode()).hexdigest()
    return {**stable, "stable_result_sha256": result_hash, "run_id": run_id, "execution_id": uuid4().hex}
