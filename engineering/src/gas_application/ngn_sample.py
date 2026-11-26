from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import sqlite3
import struct
import subprocess
import uuid
from pathlib import Path

ID = "ngn_distribution_example"
TITLE = "Pipeline Engineer · NGN 配气样例"
SOURCE = "https://pipelineengineer.github.io/gas_distribution_network/"
REPOSITORY = "https://github.com/pipelineengineer/examples/tree/"
GPKG = "gas_distribution_network_example.gpkg"
SERIES = "time_series.csv"
SOURCE_REVISION = "27e9de906c35c650a347f1046967a6278409845b"
SOURCE_HASHES = {
    GPKG: "52aef6b70f3a63fb55951708bbd0ac270890cc6aca2895c06b960d6ccabaa2ae",
    SERIES: "62933bcd0cbb36bd8109a002c013695d1d00724d02ac529197fb8411e5e926b5",
}
LIMITS = [
    "公开教学配气模型。拓扑、气源和负荷均采用样例模型定义。",
    "每个 period 单独求稳态，未提供真实时间戳、阀门及住户接入。管段退出结果用于研究。",
]


def _geometry(blob: bytes) -> list:
    envelope = {0: 0, 1: 4, 2: 6, 3: 6, 4: 8}
    if blob[:2] != b"GP" or (blob[3] >> 1 & 7) not in envelope:
        raise ValueError("无效 GeoPackage 几何")

    def read(offset: int) -> tuple[list, int]:
        order = "<" if blob[offset] else ">"
        raw_kind, = struct.unpack_from(order + "I", blob, offset + 1)
        dims = {0: 2, 1: 3, 2: 3, 3: 4}[raw_kind // 1000]
        kind = raw_kind % 1000
        offset += 5
        count = 1
        if kind != 1:
            count, = struct.unpack_from(order + "I", blob, offset)
            offset += 4
        if kind == 5:
            parts = []
            for _ in range(count):
                part, offset = read(offset)
                parts.append(part)
            return parts, offset
        if kind not in {1, 2}:
            raise ValueError("样例只支持点、线及多部件线")
        points = [list(struct.unpack_from(order + "dd", blob, offset + i * 8 * dims)) for i in range(count)]
        return points, offset + 8 * dims * count

    return read(8 + 8 * envelope[blob[3] >> 1 & 7])[0]


def _snapshot() -> dict:
    configured = os.getenv("NGN_SAMPLE_DATA_ROOT")
    if not configured:
        raise OSError("未配置 NGN_SAMPLE_DATA_ROOT")
    root = Path(configured)
    revision = (root / "revision.txt").read_text(encoding="ascii").strip()
    if revision != SOURCE_REVISION:
        raise OSError("NGN 样例缺少有效来源提交")
    contents = {name: (root / name).read_bytes() for name in (GPKG, SERIES)}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()}
    if hashes != SOURCE_HASHES:
        raise OSError("NGN 样例文件 SHA-256 与来源清单不一致")
    version = "pipeline_engineer_" + hashlib.sha256(
        json.dumps([revision, hashes], sort_keys=True).encode()
    ).hexdigest()[:16]
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(contents[GPKG])
        conn.row_factory = sqlite3.Row
        def layer(name: str) -> list[dict]:
            crs = conn.execute("SELECT srs_id FROM gpkg_contents WHERE table_name = ?", (name,)).fetchone()
            if not crs or crs[0] != 27700:
                raise ValueError(f"{name} 缺少声明的 EPSG:27700")
            return [{**dict(row), "geom": _geometry(row["geom"])} for row in conn.execute(f'SELECT * FROM "{name}" ORDER BY fid')]
        nodes, pipes, sources, sinks = (
            layer(name) for name in ("Junction Layer", "Pipe Layer", "External Grid Layer", "Sink Layer")
        )
    finally:
        conn.close()
    for records in nodes, pipes, sources, sinks:
        if len(records) != len({str(row["name"]) for row in records}):
            raise ValueError("模型对象名称重复")
    ids = {row["name"] for row in nodes}
    if (any(row["from_junction"] not in ids or row["to_junction"] not in ids for row in pipes)
            or any(row["junction"] not in ids for row in sources + sinks)
            or not any(row["in_service"] for row in sources)):
        raise ValueError("样例拓扑端点或气源无效")
    loads: dict[str, dict[str, float]] = {}
    for row in csv.DictReader(io.StringIO(contents[SERIES].decode("utf-8-sig"))):
        period, name = str(int(row["period"])), row["name"]
        value = float(row["flow_rate_m_cubed_per_s"])
        current = loads.setdefault(period, {})
        if name in current or not math.isfinite(value) or value < 0:
            raise ValueError("工况负荷重复或无效")
        current[name] = value
    if len(loads) != 10 or any(set(values) != {row["name"] for row in sinks} for values in loads.values()):
        raise ValueError("工况未完整覆盖样例负荷")
    return dict(data_version=version, revision=revision, hashes=hashes, nodes=nodes,
                pipes=pipes, sources=sources, sinks=sinks, loads=loads)


def registry_item() -> dict:
    try:
        data = _snapshot()
        status, version = "configured", data["data_version"]
    except (OSError, ValueError, sqlite3.Error, struct.error):
        status, version = "not_installed", None
    return dict(dataset_id=ID, label=TITLE, data_mode="research_benchmark", status=status,
                data_version=version, default=False, description=LIMITS[0], source_url=SOURCE)


def overview() -> dict:
    data = _snapshot()
    return dict(
        dataset_id=ID, title=TITLE, data_mode="research_benchmark", status="ready",
        data_version=data["data_version"], source_crs="EPSG:27700", limitations=LIMITS,
        source_refs=[SOURCE, REPOSITORY + data["revision"] + "/gas-network-example"],
        counts=dict(pipes=len(data["pipes"]), nodes=len(data["nodes"]),
                    sources=len(data["sources"]), sinks=len(data["sinks"]), periods=len(data["loads"])),
        model={key: data[key] for key in ("nodes", "pipes", "sources", "sinks")},
        periods=sorted(data["loads"], key=int), input_sha256=data["hashes"],
        solver_status="configured" if os.getenv("NGN_SAMPLE_SOLVER_PYTHON") else "not_installed",
    )


def run(version: str, period: str, edge_id: str | None, *, trend: bool = False) -> dict:
    data = _snapshot()
    if version != data["data_version"] or period not in {"base", *data["loads"]}:
        raise ValueError("数据版本或工况无效")
    if edge_id is not None and not any(str(row["name"]) == edge_id and row["in_service"] for row in data["pipes"]):
        raise ValueError("请选择样例中在役的管段")
    if trend and (period != "base" or edge_id is not None):
        raise ValueError("多工况趋势仅支持基准计算")
    task = dict(period=period, edge_id=edge_id, trend=trend)
    worker = Path(__file__).with_name("ngn_sample_solver.py")
    python = os.getenv("NGN_SAMPLE_SOLVER_PYTHON")
    if not python:
        result = dict(status="not_estimable", reason="未配置 NGN_SAMPLE_SOLVER_PYTHON")
    else:
        try:
            proc = subprocess.run([python, str(worker)], input=json.dumps(dict(data=data, task=task)),
                                  encoding="utf-8", capture_output=True, timeout=120, check=True)
            result = json.loads(proc.stdout.splitlines()[-1])
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            result = dict(status="not_estimable", reason="求解进程失败或超过120秒")
    payload = dict(dataset_id=ID, data_version=version, input_sha256=data["hashes"],
                   task=task, **result)
    return dict(**payload, execution_id="exec-" + uuid.uuid4().hex,
                stable_result_sha256=hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest())
