"""Read/write separation for the published Sicilian research benchmark."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import re
import subprocess
import tempfile
import uuid
from collections import deque
from functools import lru_cache
from pathlib import Path

ID = "sicily_transmission"
COMMIT = "4c3bf375338913c6f88749bac89cc534e4a1c0f0"
REPOSITORY = "https://github.com/Filippo-L-A-Munafo/GasNetSim_4Qgis"
SOURCE = f"{REPOSITORY}/tree/{COMMIT}/sample_data/Sicily_transmission_network"
DEFAULT = "20251115-12"
CASE_NAMES = ("sf_nodes.csv", "sf_pipelines.csv", "results_nodes.csv", "results_pipelines.csv")


def long_path(path: Path) -> Path:
    name = str(path.resolve())
    return Path("\\\\?\\" + name) if os.name == "nt" and not name.startswith("\\\\?\\") else Path(name)


def read_csv(data: bytes) -> list[dict]:
    decoded = data.decode("utf-8-sig")
    delimiter = ";" if ";" in decoded.splitlines()[0] else ","
    return list(csv.DictReader(io.StringIO(decoded), delimiter=delimiter))


def finite(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("样本包含非有限数值")
    return number


def indexed(rows: list[dict], field: str) -> dict[str, dict]:
    result = {row[field]: row for row in rows}
    if not result or len(result) != len(rows) or "" in result:
        raise ValueError(f"{field} 缺失或重复")
    return result


@lru_cache(maxsize=4)
def manifest(root: Path, modified_ns: int) -> dict:
    data = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if data["commit"] != COMMIT or data["dataset_id"] != ID or DEFAULT not in data["cases"]:
        raise ValueError("西西里快照版本不兼容")
    return data


def snapshot() -> tuple[Path, dict]:
    configured = os.getenv("GAS_SICILY_DATA_ROOT")
    if not configured:
        raise OSError("西西里样本未导入。请运行 import_sicily.py 并设置 GAS_SICILY_DATA_ROOT。")
    root = long_path(Path(configured))
    try:
        return root, manifest(root, (root / "manifest.json").stat().st_mtime_ns)
    except (OSError, ValueError, KeyError) as exc:
        raise OSError("西西里快照清单缺失或不兼容，请重新导入。") from exc


def case_files(case_id: str) -> tuple[dict, dict[str, bytes]]:
    root, meta = snapshot()
    if not re.fullmatch(r"\d{8}-[0-2]\d", case_id) or case_id not in meta["cases"]:
        raise ValueError("请选择已登记的西西里代表日及小时")
    data = {}
    for name, digest in meta["cases"][case_id].items():
        if name not in CASE_NAMES:
            raise OSError("西西里快照包含未知文件类型")
        payload = (root / case_id / name).read_bytes()
        if hashlib.sha256(payload).hexdigest() != digest:
            raise OSError(f"西西里文件校验失败：{case_id}/{name}")
        data[name] = payload
    return meta, data


def network(data: dict[str, bytes]) -> tuple[dict, dict]:
    nodes = indexed(read_csv(data["sf_nodes.csv"]), "node_index")
    pipes = indexed(read_csv(data["sf_pipelines.csv"]), "pipeline_index")
    if len(nodes) != 340 or len(pipes) != 350:
        raise ValueError("西西里样本的网络规模应为 340 节点、350 管段")
    for pipe in pipes.values():
        if pipe["inlet_index"] not in nodes or pipe["outlet_index"] not in nodes:
            raise ValueError("西西里样本存在未登记的管段端点")
        for field in ("diameter_m", "length_m"):
            if (finite(pipe[field]) or 0) <= 0:
                raise ValueError(f"西西里管段 {field} 必须为正")
    return nodes, pipes


def reachable(nodes: dict, pipes: dict, excluded: str | None = None) -> set[str]:
    graph = adjacency(nodes, pipes, excluded)
    reached = {key for key, node in nodes.items() if (finite(node["pressure_pa"]) or 0) > 0}
    if not reached:
        raise ValueError("该工况缺少供气压力边界")
    pending = deque(sorted(reached))
    while pending:
        for neighbor, _ in graph[pending.popleft()]:
            if neighbor not in reached:
                reached.add(neighbor)
                pending.append(neighbor)
    return reached


def adjacency(nodes: dict, pipes: dict, excluded: str | None) -> dict[str, list[tuple[str, str]]]:
    graph: dict[str, list[tuple[str, str]]] = {key: [] for key in nodes}
    for key, pipe in pipes.items():
        if key != excluded:
            a, b = pipe["inlet_index"], pipe["outlet_index"]
            graph[a].append((b, key))
            graph[b].append((a, key))
    return graph


def alternate_route(nodes: dict, pipes: dict, excluded: str) -> list[str]:
    graph = adjacency(nodes, pipes, excluded)
    pipe = pipes[excluded]
    start, end = pipe["inlet_index"], pipe["outlet_index"]
    pending = deque([(start, [])])
    seen = {start}
    while pending:
        current, path = pending.popleft()
        if current == end:
            return path
        for neighbor, edge in graph[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                pending.append((neighbor, [*path, edge]))
    return []


def result_rows(data: dict, name: str, key: str, fields: tuple[str, ...], allowed: set[str]) -> dict:
    if name not in data:
        return {}
    records = indexed(read_csv(data[name]), key)
    if set(records) != allowed:
        raise ValueError(f"{name} 与工况模型对象不一致")
    return {identifier: {field: finite(row.get(field)) for field in fields}
            for identifier, row in records.items()}


def overview() -> dict:
    meta, files = case_files(DEFAULT)
    nodes, pipes = network(files)
    return {
        "dataset_id": ID, "title": "西西里 · 高压输气研究样本",
        "data_mode": "research_benchmark", "status": "ready", "data_version": meta["version"],
        "counts": {"nodes": len(nodes), "connections": len(pipes), "available_hours": len(meta["cases"])},
        "nodes": [{"id": key, "node_type": "source" if (finite(row["pressure_pa"]) or 0) > 0 else "junction",
                   "source_properties": {"altitude_m": row.get("altitude_m", "")}} for key, row in nodes.items()],
        "edges": [{"id": key, "from_node_id": row["inlet_index"], "to_node_id": row["outlet_index"],
                   "edge_type": "pipe", "source_properties": {field: row[field] for field in ("diameter_m", "length_m")}}
                  for key, row in pipes.items()],
        "cases": [{"id": case, "published": all(name in entry for name in CASE_NAMES[2:])}
                  for case, entry in sorted(meta["cases"].items())],
        "default_case": DEFAULT, "source_refs": [SOURCE],
        "presets": [{"id": "342", "label": "有替代通路 · 342"}, {"id": "254", "label": "支网断连 · 254"}],
        "solver_status": "configured" if os.getenv("SICILY_SOLVER_PYTHON") else "unavailable",
        "geometry_note": "研究样本 · 拓扑示意，线段不表示实际管线走向。",
        "limitations": ["分时输入采用作者公开的重建负荷", "压力为稳态模型结果，单位 bar abs"],
    }


def condition(case_id: str) -> dict:
    meta, files = case_files(case_id)
    nodes, pipes = network(files)
    status = "published" if all(name in files for name in CASE_NAMES[2:]) else "pending_data"
    return {"dataset_id": ID, "data_version": meta["version"], "case_id": case_id, "status": status,
            "nodes": result_rows(files, "results_nodes.csv", "node_id",
                                 ("pressure_bar", "throughput_sm3h", "hhv_mj_sm3", "wobbe_mj_sm3"),
                                 set(nodes)) if status == "published" else {},
            "pipes": result_rows(files, "results_pipelines.csv", "pipe_id",
                                 ("velocity_m_s", "flow_sm3h"), set(pipes)) if status == "published" else {}}


def run(case_id: str, edge_id: str, version: str, *, solve_hydraulics: bool = True) -> dict:
    meta, files = case_files(case_id)
    nodes, pipes = network(files)
    if version != meta["version"] or edge_id not in pipes:
        raise ValueError("管段或快照版本与当前选择不匹配")
    before = reachable(nodes, pipes)
    after = reachable(nodes, pipes, edge_id)
    lost = before - after
    disconnected = sorted(lost)
    branch_edges = sorted(key for key, pipe in pipes.items() if key != edge_id
                          and pipe["inlet_index"] in lost and pipe["outlet_index"] in lost)
    endpoints = pipes[edge_id]
    route = (alternate_route(nodes, pipes, edge_id)
             if {endpoints["inlet_index"], endpoints["outlet_index"]} <= after else [])
    demand = [finite(nodes[key].get("flow_sm3_per_s")) if nodes[key].get("flow_type") == "volumetric"
              else None for key in disconnected]
    disconnected_demand = (sum(max(0.0, value) for value in demand if value is not None)
                           if all(value is not None for value in demand) else None)
    result = {"dataset_id": ID, "data_version": version, "case_id": case_id, "edge_id": edge_id,
              "execution_id": uuid.uuid4().hex, "unreachable_nodes": disconnected,
              "baseline_unreachable_nodes": sorted(nodes.keys() - before),
              "reachable_before": len(before), "reachable_after": len(after),
              "alternate_path_edges": route, "disconnected_branch_edges": branch_edges,
              "input_sha256": hashlib.sha256(json.dumps({"files": meta["cases"][case_id], "edge_id": edge_id},
                                                        sort_keys=True).encode()).hexdigest(),
              "disconnected_model_demand_sm3_s": disconnected_demand}
    if not solve_hydraulics:
        return {**result, "hydraulics": {"status": "not_requested"}}
    return {**result, "hydraulics": _solve(files, before, after, edge_id)}


def baseline(case_id: str, version: str) -> dict:
    meta, files = case_files(case_id)
    if meta["version"] != version:
        raise ValueError("快照版本与当前选择不匹配")
    nodes, pipes = network(files)
    included = reachable(nodes, pipes)
    return {
        "dataset_id": ID, "case_id": case_id, "data_version": version,
        "execution_id": uuid.uuid4().hex,
        "input_sha256": hashlib.sha256(json.dumps(meta["cases"][case_id], sort_keys=True).encode()).hexdigest(),
        "hydraulics": _solve(files, included, included, None),
    }


def _solve(files: dict[str, bytes], before: set[str], after: set[str], edge_id: str | None) -> dict:
    interpreter = os.getenv("SICILY_SOLVER_PYTHON")
    if not interpreter:
        return {"status": "unavailable", "reason": "未配置西西里独立求解器"}
    if "results_nodes.csv" not in files:
        return {"status": "pending_data", "reason": "该工况缺少发布基准压力，无法复核本次求解。"}
    try:
        with tempfile.TemporaryDirectory(prefix="sicily-") as temporary:
            folder = Path(temporary)
            for name in (*CASE_NAMES[:2], "results_nodes.csv"):
                (folder / name).write_bytes(files[name])
            worker = Path(__file__).with_name("sicily_solver.py")
            command = [interpreter, "-X", "utf8", str(worker), str(folder), edge_id or "", json.dumps(sorted(before)),
                       json.dumps(sorted(after))]
            # Small dense systems run faster and more predictably without BLAS fan-out.
            worker_env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
                          "MKL_NUM_THREADS": "1"}
            process = subprocess.run(command, capture_output=True, timeout=90, check=True,
                                     encoding="utf-8", env=worker_env)
            hydraulics = json.loads(process.stdout.splitlines()[-1])
    except subprocess.TimeoutExpired:
        hydraulics = {"status": "not_estimable", "reason": "稳态求解超过 90 秒，保留此次拓扑结果。"}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        hydraulics = {"status": "unavailable", "reason": "稳态求解或校验未通过，请查看求解器配置和参考工况。"}
    return hydraulics
