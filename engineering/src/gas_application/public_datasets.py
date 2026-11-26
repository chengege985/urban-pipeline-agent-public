"""Read-only adapters for explicitly selected, public gas datasets.

These sources have separate contracts from the synthetic GAS-M0 case.  The
module intentionally does not infer connectivity from geometry or translate
public records into synthetic asset/event DTOs.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from xml.etree import ElementTree

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from src.platform.authz import local_synthetic_anonymous_enabled, resolve_principal
from . import charlottesville, ngn, ngn_sample, schutterwald, sicily, sicily_agent

AER_ID = "aer_alberta"
SGN_ID = "sgn_eastbourne"
GASLIB_ID = "gaslib_582"
MERRIMACK_ID = "merrimack_2018"
SYNTHETIC_ID = "gas_m0_synthetic"

AER_PAGE = "https://www1.aer.ca/ProductCatalogue/557.html"
AER_EVENTS_PAGE = "https://www1.aer.ca/ProductCatalogue/215.html"
SGN_PAGE = "https://www.sgn.co.uk/open-data-sharing-portal"
SGN_LAYER = (
    "https://services-eu1.arcgis.com/UnrqlFBt9cFAdjsP/arcgis/rest/services/"
    "Distribution_Pipe_Network_Eastbourne/FeatureServer/0"
)
GASLIB_PAGE = "https://gaslib.zib.de/data.html"
GASLIB_DOCS = "https://gaslib.zib.de/documentation.html"
NTSB_CASE = "https://www.ntsb.gov/investigations/pages/pld18mr003.aspx"
NTSB_REPORT = "https://www.ntsb.gov/investigations/AccidentReports/Reports/PAR1902.pdf"
NTSB_DOCKET = "https://data.ntsb.gov/Docket/?NTSBNumber=PLD18MR003"
NTSB_RESPONSE_REPORT = (
    "https://data.ntsb.gov/Docket/Document/docBLOB?FileExtension=.PDF&"
    "FileName=Emergency+Response+Group+Factual+Report-Master.PDF&ID=40476254"
)


class PublicDatasetUnavailable(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _aer_root() -> Path | None:
    raw = os.getenv("GAS_AER_DATA_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _gaslib_root() -> Path | None:
    raw = os.getenv("GASLIB_582_DATA_ROOT", "").strip()
    return Path(raw).expanduser().resolve() if raw else None


def _gaslib_network_file(root: Path) -> Path:
    explicit = os.getenv("GASLIB_582_NETWORK_FILE", "").strip()
    if explicit:
        name = Path(explicit)
        if name.name != explicit or name.suffix.casefold() != ".net":
            raise PublicDatasetUnavailable("GASLIB_582_NETWORK_FILE只能填写数据目录内的.net文件名")
        candidate = root / name
        if not candidate.is_file() or "582" not in candidate.name.casefold() or "v2" not in candidate.name.casefold():
            raise PublicDatasetUnavailable("指定文件不是可读取的GasLib-582-v2网络文件")
        return candidate
    candidates = sorted(root.glob("*582*v2*.net"), key=lambda path: path.name.casefold())
    if not candidates:
        raise PublicDatasetUnavailable("GasLib数据目录未找到GasLib-582-v2 .net文件")
    if len(candidates) > 1:
        raise PublicDatasetUnavailable("GasLib数据目录包含多个582-v2网络版本；请通过GASLIB_582_NETWORK_FILE明确指定")
    return candidates[0]


@lru_cache(maxsize=4)
def _verified_aer_snapshot(root_text: str, manifest_mtime_ns: int, database_size: int, database_mtime_ns: int) -> dict[str, Any]:
    del manifest_mtime_ns, database_size, database_mtime_ns
    root = Path(root_text)
    manifest_path = root / "manifest.json"
    database_path = root / "aer_catalog.sqlite3"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PublicDatasetUnavailable("AER数据包manifest缺失或无法读取") from exc
    if manifest.get("database") != database_path.name or not database_path.is_file():
        raise PublicDatasetUnavailable("AER数据包未找到manifest登记的只读数据库")
    expected = manifest.get("database_sha256")
    if not isinstance(expected, str) or len(expected) != 64 or _sha256(database_path) != expected:
        raise PublicDatasetUnavailable("AER数据库SHA-256与manifest不一致")
    if manifest.get("dataset_id") != "AER-ENHANCED-PIPELINE+FIS-RELEASE-REPORT":
        raise PublicDatasetUnavailable("AER数据包dataset_id与当前适配器不匹配")
    return manifest


def aer_manifest() -> dict[str, Any]:
    root = _aer_root()
    if root is None:
        raise PublicDatasetUnavailable("未配置GAS_AER_DATA_ROOT；需指向包含manifest.json和aer_catalog.sqlite3的官方数据快照目录")
    manifest_path, database_path = root / "manifest.json", root / "aer_catalog.sqlite3"
    try:
        stat_m, stat_db = manifest_path.stat(), database_path.stat()
    except OSError as exc:
        raise PublicDatasetUnavailable("AER本地快照文件缺失") from exc
    return _verified_aer_snapshot(str(root), stat_m.st_mtime_ns, stat_db.st_size, stat_db.st_mtime_ns)


def _aer_connection() -> sqlite3.Connection:
    aer_manifest()
    root = _aer_root()
    assert root is not None
    uri = (root / "aer_catalog.sqlite3").as_uri() + "?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as exc:
        raise PublicDatasetUnavailable("无法以只读方式打开AER快照") from exc


def _decode_geometry(blob: bytes | None) -> dict[str, Any] | None:
    if blob is None:
        return None
    try:
        value = json.loads(zlib.decompress(blob))
    except (zlib.error, json.JSONDecodeError, TypeError) as exc:
        raise PublicDatasetUnavailable("AER快照包含无法解析的几何记录") from exc
    if not isinstance(value, dict) or value.get("type") not in {"LineString", "MultiLineString"}:
        return None
    return value


def _aer_pipe(row: sqlite3.Row) -> dict[str, Any]:
    try:
        properties = json.loads(row["properties_json"])
    except (json.JSONDecodeError, TypeError):
        properties = {}
    return {
        "record_id": row["source_object_id"],
        "pipe_id": row["pipe_id"],
        "field_centre": row["field_centre"],
        "segment_status": row["segment_status"],
        "pipe_material": row["pipe_material"],
        "pipe_type": row["pipe_type"],
        "primary_substance": row["primary_substance"],
        "outside_diameter_mm": row["outside_diameter_mm"],
        "licence_number": row["licence_number"],
        "company_name": row["company_name"],
        "duplicate_segment_id_in_source": bool(row["duplicate_segment_id_in_source"]),
        "geometry": _decode_geometry(row["geometry_zlib"]),
        "source_properties": properties,
        "source_ref": {"dataset": "AER Enhanced Pipeline Shapefile", "source_object_id": row["source_object_id"]},
    }


def _aer_filters(*, field_centre: str | None, search: str, bbox: str | None) -> tuple[str, list[Any]]:
    parts = ["1=1"]
    params: list[Any] = []
    if field_centre:
        parts.append("field_centre = ?")
        params.append(field_centre)
    if search.startswith("="):
        parts.append("pipe_id = ?")
        params.append(search[1:])
    elif search:
        parts.append("(pipe_id LIKE ? OR licence_number LIKE ? OR company_name LIKE ?)")
        match = f"%{search[:120]}%"
        params.extend((match, match, match))
    if bbox:
        try:
            west, south, east, north = (float(part.strip()) for part in bbox.split(","))
        except (ValueError, TypeError) as exc:
            raise ValueError("bbox需为minLon,minLat,maxLon,maxLat") from exc
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError("bbox范围无效")
        parts.append("max_lon >= ? AND min_lon <= ? AND max_lat >= ? AND min_lat <= ?")
        params.extend((west, east, south, north))
    return " AND ".join(parts), params


def aer_assets(*, field_centre: str | None = None, search: str = "", bbox: str | None = None,
               offset: int = 0, limit: int = 100) -> dict[str, Any]:
    where, params = _aer_filters(field_centre=field_centre, search=search.strip(), bbox=bbox)
    conn = _aer_connection()
    try:
        total = int(conn.execute(f"SELECT count(*) FROM pipes WHERE {where}", params).fetchone()[0])
        rows = conn.execute(
            f"SELECT * FROM pipes WHERE {where} ORDER BY field_centre, pipe_id, source_object_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    finally:
        conn.close()
    return {"status": "partial" if offset + len(rows) < total else "ready", "total": total,
            "offset": offset, "limit": limit, "items": [_aer_pipe(row) for row in rows],
            "data_version": aer_manifest().get("data_version")}


def aer_events(*, pipe_id: str | None = None, search: str = "", link_status: str | None = None,
               offset: int = 0, limit: int = 100) -> dict[str, Any]:
    parts = ["1=1"]
    params: list[Any] = []
    if pipe_id:
        parts.append("linked_pipe_id = ?")
        params.append(pipe_id)
    if link_status:
        parts.append("link_status = ?")
        params.append(link_status)
    if search:
        like = f"%{search[:120]}%"
        parts.append("(incident_id LIKE ? OR incident_type LIKE ? OR cause_category LIKE ? OR failure_type LIKE ?)")
        params.extend((like, like, like, like))
    where = " AND ".join(parts)
    conn = _aer_connection()
    try:
        total = int(conn.execute(f"SELECT count(*) FROM events WHERE {where}", params).fetchone()[0])
        rows = conn.execute(f"SELECT * FROM events WHERE {where} ORDER BY incident_date DESC, incident_id LIMIT ? OFFSET ?",
                            [*params, limit, offset]).fetchall()
    finally:
        conn.close()
    items = []
    for row in rows:
        items.append({key: row[key] for key in row.keys() if key != "substances_json"} | {
            "substances": json.loads(row["substances_json"] or "[]"),
            "source_ref": {"dataset": "AER Field Surveillance Release Report", "source_line_number": row["source_line_number"]},
            "classification_warning": "Release Report记录不等同于确认泄漏；应按原始incident_type及link_status解释。",
        })
    return {"status": "partial" if offset + len(items) < total else "ready", "total": total,
            "offset": offset, "limit": limit, "items": items,
            "data_version": aer_manifest().get("data_version")}


def _fetch_json(url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "urban-pipeline-agent-public-data/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read(8 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403, 498, 499}:
            raise PublicDatasetUnavailable("SGN 官方图层拒绝当前访问，请核对门户的访问授权。") from exc
        raise PublicDatasetUnavailable(f"SGN 官方图层返回 HTTP {exc.code}，请稍后重试。") from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise PublicDatasetUnavailable("无法连接SGN官方ArcGIS图层；页面未改用其它数据源") from exc
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        if payload["error"].get("code") in {401, 403, 498, 499}:
            raise PublicDatasetUnavailable("SGN 官方图层要求授权，请核对门户的访问权限。")
    if not isinstance(payload, dict) or payload.get("error"):
        raise PublicDatasetUnavailable("SGN官方ArcGIS图层返回错误响应")
    return payload


def sgn_assets(*, offset: int = 0, limit: int = 100, bbox: str | None = None) -> dict[str, Any]:
    params: dict[str, str] = {"where": "1=1", "outFields": "*", "returnGeometry": "true", "outSR": "4326",
                              "resultRecordCount": str(limit), "resultOffset": str(offset), "f": "json"}
    if bbox:
        try:
            west, south, east, north = (float(part.strip()) for part in bbox.split(","))
        except (ValueError, TypeError) as exc:
            raise ValueError("bbox需为minLon,minLat,maxLon,maxLat") from exc
        if not (-180 <= west <= east <= 180 and -90 <= south <= north <= 90):
            raise ValueError("bbox范围无效")
        params["geometry"] = f"{west},{south},{east},{north}"
        params["geometryType"] = "esriGeometryEnvelope"
        params["inSR"] = "4326"
        params["spatialRel"] = "esriSpatialRelIntersects"
    query = urllib.parse.urlencode(params)
    payload = _fetch_json(f"{SGN_LAYER}/query?{query}")
    features = payload.get("features")
    if not isinstance(features, list):
        raise PublicDatasetUnavailable("SGN图层未返回要素列表")
    items = []
    for feature in features:
        if not isinstance(feature, dict) or not isinstance(feature.get("attributes"), dict):
            continue
        attrs, geom = feature["attributes"], feature.get("geometry")
        # Eastbourne is LineString data. Preserve service attributes without
        # inventing endpoint nodes or graph edges.
        coordinates = None
        if isinstance(geom, dict) and isinstance(geom.get("paths"), list) and geom["paths"]:
            coordinates = geom["paths"]
        object_id = attrs.get("OBJECTID", attrs.get("FID", attrs.get("objectid")))
        items.append({"record_id": str(object_id) if object_id is not None else None,
                      "pipe_id": str(object_id) if object_id is not None else None,
                      "source_properties": attrs,
                      "geometry": {"type": "MultiLineString" if coordinates and len(coordinates) > 1 else "LineString",
                                   "coordinates": coordinates[0] if coordinates and len(coordinates) == 1 else coordinates}
                      if coordinates else None,
                      "source_ref": {"layer_url": SGN_LAYER, "object_id": object_id}})
    exceeded = bool(payload.get("exceededTransferLimit"))
    return {"status": "partial" if exceeded or len(items) == limit else "ready", "total": None,
            "offset": offset, "limit": limit, "items": items, "data_version": "SGN-Eastbourne-ArcGIS-live",
            "truncated": exceeded or len(items) == limit,
            "limitation": "公开图层没有逐段拓扑连接、阀门/端口、用户接入或供气工况。"}


def _tag(element: ElementTree.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].casefold()


def gaslib_network() -> dict[str, Any]:
    root_dir = _gaslib_root()
    if root_dir is None:
        raise PublicDatasetUnavailable("未配置 GASLIB_582_DATA_ROOT。请用本地启动脚本设置官方 GasLib-582 v2 .net 所在目录。")
    net_path = _gaslib_network_file(root_dir)
    try:
        tree = ElementTree.parse(net_path)
    except (OSError, ElementTree.ParseError) as exc:
        raise PublicDatasetUnavailable("GasLib .net文件不是合法XML") from exc
    root = tree.getroot()
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    node_kinds = {"source", "sink", "innode", "node"}
    for element in root.iter():
        tag = _tag(element)
        attrs = element.attrib
        entity_id = attrs.get("id")
        if entity_id and tag in node_kinds:
            point = None
            try:
                lon, lat = attrs.get("geoWGS84Long"), attrs.get("geoWGS84Lat")
                if lon is not None and lat is not None:
                    point = [float(lon), float(lat)]
            except ValueError:
                point = None
            nodes.append({"id": entity_id, "node_type": tag, "coordinates": point,
                          "source_properties": dict(attrs)})
        source = attrs.get("from") or attrs.get("fromNode") or attrs.get("startNode")
        target = attrs.get("to") or attrs.get("toNode") or attrs.get("endNode")
        if entity_id and source and target:
            edges.append({"id": entity_id, "edge_type": tag, "from_node_id": source,
                          "to_node_id": target, "source_properties": dict(attrs)})
    # GasLib-582 v2 published counts are a contract guard, not a fabricated
    # completion value. If format changes, fail closed and inspect the parser.
    if len(nodes) != 582 or len(edges) != 609:
        raise PublicDatasetUnavailable(f"解析计数与GasLib-582-v2公开基线不符：nodes={len(nodes)}, connections={len(edges)}")
    missing_endpoints = [edge["id"] for edge in edges
                         if edge["from_node_id"] not in {n["id"] for n in nodes}
                         or edge["to_node_id"] not in {n["id"] for n in nodes}]
    if missing_endpoints:
        raise PublicDatasetUnavailable("GasLib拓扑存在未解析端点，拒绝用于图计算")
    return {"dataset_id": GASLIB_ID, "data_version": net_path.name, "nodes": nodes, "edges": edges,
            "node_count": len(nodes), "connection_count": len(edges),
            "solver_status": "unavailable",
            "solver_reason": "已读取GasLib拓扑，但本项目尚未配置并验证GasLib-582全网稳态方程模型和非线性求解器。不会用图可达性或公开参考解替代本项目求解。",
            "source_ref": GASLIB_PAGE,
            "geometry_note": "节点经纬度仅按GasLib原始坐标呈现；连接直线为拓扑示意，不代表实测管道走向。"}


def gaslib_connectivity(edge_id: str) -> dict[str, Any]:
    """Compare source reachability on the published benchmark's undirected graph."""
    network = gaslib_network()
    nodes = network["nodes"]
    edges = network["edges"]
    if not any(edge["id"] == edge_id and edge["edge_type"] in {"pipe", "shortpipe"} for edge in edges):
        raise ValueError("请选择 GasLib 基准中的管道连接")
    sources = {node["id"] for node in nodes if node["node_type"] == "source"}
    exits = {node["id"] for node in nodes if node["node_type"] == "sink"}

    def reachable(excluded: str | None = None) -> set[str]:
        adjacency: dict[str, list[str]] = {node["id"]: [] for node in nodes}
        for edge in edges:
            if edge["id"] == excluded:
                continue
            a, b = edge["from_node_id"], edge["to_node_id"]
            adjacency[a].append(b)
            adjacency[b].append(a)
        seen = sources.copy()
        pending = list(seen)
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    pending.append(neighbor)
        return seen

    before, after = reachable(), reachable(edge_id)
    return {
        "dataset_id": GASLIB_ID, "data_version": network["data_version"],
        "method": "undirected_source_reachability_v1", "edge_id": edge_id,
        "source_count": len(sources), "exit_count": len(exits),
        "reachable_exits_before": len(exits & before), "reachable_exits_after": len(exits & after),
        "newly_unreachable_exit_ids": sorted(exits & (before - after)),
        "newly_unreachable_node_count": len(before - after),
        "unreachable_node_ids_before": sorted({node["id"] for node in nodes} - before),
        "unreachable_node_ids_after": sorted({node["id"] for node in nodes} - after),
    }


def dataset_registry() -> dict[str, Any]:
    try:
        aer = aer_manifest()
        aer_status, aer_version = "ready", aer.get("data_version")
    except PublicDatasetUnavailable as exc:
        aer_status, aer_version = "unavailable", None
        aer_reason = str(exc)
    else:
        aer_reason = "已核验本地manifest与SQLite快照SHA-256。"
    root = _gaslib_root()
    if root is None:
        gaslib_status, gaslib_version, gaslib_reason = "not_installed", None, "未配置GasLib-582-v2网络文件目录。"
    else:
        try:
            network = gaslib_network()
        except PublicDatasetUnavailable as exc:
            gaslib_status, gaslib_version, gaslib_reason = "unavailable", None, str(exc)
        else:
            gaslib_status, gaslib_version = "ready", network["data_version"]
            gaslib_reason = "网络结构已通过节点、连接数量和端点引用检查；物理稳态求解器仍未配置。"
    try:
        _, sicily_meta = sicily.snapshot()
        sicily_status, sicily_version = "configured", sicily_meta["version"]
    except OSError:
        sicily_status, sicily_version = "not_installed", None
    return {"datasets": [
        ngn.registry_item(),
        ngn_sample.registry_item(),
        {"dataset_id": sicily.ID, "label": "西西里 · 高压输气研究样本", "data_mode": "research_benchmark",
         "status": sicily_status, "default": False, "data_version": sicily_version,
         "description": "公开研究拓扑、分时工况及稳态模型", "source_url": sicily.SOURCE},
        {"dataset_id": charlottesville.ID, "label": "Charlottesville", "data_mode": "official_public",
         "status": "remote", "default": False, "data_version": charlottesville.VERSION,
         "description": "Official GIS and historical leak records", "source_url": charlottesville.SOURCE},
        {"dataset_id": schutterwald.ID, "label": "Schutterwald", "data_mode": "research_benchmark",
         "status": "configured" if os.environ.get("SCHUTTERWALD_MODEL_FILE") else "not_installed",
         "default": False, "data_version": schutterwald.VERSION,
         "description": "Research distribution network", "source_url": schutterwald.SOURCE},
        {"dataset_id": SYNTHETIC_ID, "label": "GAS-M0 合成演示", "data_mode": "synthetic", "status": "ready",
         "default": False, "data_version": "synthetic-fixture", "description": "原有合成演示数据；与公开数据完全隔离。"},
        {"dataset_id": AER_ID, "label": "AER 阿尔伯塔管线与事件", "data_mode": "official_public", "status": aer_status,
         "default": True, "data_version": aer_version, "description": aer_reason,
         "source_url": AER_PAGE, "event_source_url": AER_EVENTS_PAGE,
         "limitations": ["不含低压配气", "无阀门设备拓扑、用户接入和水力工况", "Release Report不等同于全部确认泄漏", "需遵守AER当前数据许可"]},
        {"dataset_id": SGN_ID, "label": "SGN Eastbourne 城市配气", "data_mode": "official_public", "status": "remote",
         "default": False, "data_version": "SGN-Eastbourne-ArcGIS-live", "description": "选择后从SGN官方ArcGIS公开图层读取；需服务网络可达。",
         "source_url": SGN_PAGE, "limitations": ["无逐段拓扑连接ID", "无阀门和设备端口", "无用户接入关系和实时工况"]},
        {"dataset_id": GASLIB_ID, "label": "GasLib-582 研究基准", "data_mode": "research_benchmark", "status": gaslib_status,
         "default": False, "data_version": gaslib_version, "description": f"{gaslib_reason} 研究用稳态网络优化基准，不是可定位的在运公共管网。",
         "source_url": GASLIB_PAGE, "limitations": ["完整稳态求解器未接入", "不得把图连通解释为水力能力"]},
        {"dataset_id": MERRIMACK_ID, "label": "Merrimack Valley 事故复盘", "data_mode": "official_case_evidence", "status": "ready",
         "default": False, "data_version": "NTSB-PLD18MR003", "description": "官方事故报告与公开卷宗证据；不含可用于重算的完整城市网络。",
         "source_url": NTSB_CASE, "limitations": ["事故记录展示，不是在线管网", "无可验证的完整网络拓扑和逐户关联", "报告图表不作为未经核验的逐时遥测序列"]},
    ], "security_note": "公开来源只读访问。GAS-M0仍为单独的合成数据模式，不在来源切换时自动回退。"}


def _merrimack_overview() -> dict[str, Any]:
    return {"dataset_id": MERRIMACK_ID, "data_version": "NTSB-PLD18MR003", "status": "ready",
            "data_mode": "official_case_evidence", "title": "Merrimack Valley 天然气系统过压事故",
            "event_date": "2018-09-13", "event_time_precision": "约当地时间16:00（NTSB概述）",
            "locations": ["Lawrence", "Andover", "North Andover", "Massachusetts, USA"],
            "facts": [{"label": "受影响客户", "value": 10894, "unit": "customers",
                       "note": "含预防性关闭的受影响区外客户；其中8,447个为低压系统直接影响服务，不能相加。"},
                      {"label": "受损建筑", "value": 131, "unit": "structures"}],
            "timeline": [
                {"time": "09-13 15:57", "precision": "minute_edt", "title": "监测记录压力突升",
                 "detail": "运营方事后查阅监测记录，在低压系统识别到压力突然上升。",
                 "source_url": NTSB_RESPONSE_REPORT + "#page=16", "source_label": "NTSB 应急响应事实报告，第16页"},
                {"time": "09-13 约16:00", "precision": "approximate_edt", "title": "多地报告火灾和爆炸",
                 "detail": "事故涉及 Lawrence、Andover 和 North Andover。调查认定高压燃气进入了低压配气系统。",
                 "source_url": NTSB_CASE, "source_label": "NTSB 调查概述"},
                {"time": "09-13 19:43", "precision": "minute_edt", "title": "运营方宣布一级应急",
                 "detail": "运营方宣布其应急预案中的一级应急状态。",
                 "source_url": NTSB_RESPONSE_REPORT + "#page=19", "source_label": "NTSB 应急响应事实报告，第19页"},
                {"time": "09-14 06:27", "precision": "minute_edt", "title": "确认涉事低压系统停运",
                 "detail": "报告记录确认系统已停运。受影响客户总数含预防性停供，统计范围见报告。",
                 "source_url": NTSB_RESPONSE_REPORT + "#page=20", "source_label": "NTSB 应急响应事实报告，第20页"},
            ],
            "evidence": [{"label": "NTSB调查页", "url": NTSB_CASE, "kind": "official_investigation"},
                         {"label": "NTSB最终事故报告 PAR-19-02", "url": NTSB_REPORT, "kind": "official_report"},
                         {"label": "NTSB应急响应事实报告（含客户影响口径）", "url": NTSB_RESPONSE_REPORT, "kind": "official_factual_report"},
                         {"label": "NTSB公开卷宗（86项）", "url": NTSB_DOCKET, "kind": "official_docket"}],
            "limitations": ["没有可加载的完整配气网节点/管段、阀门拓扑或逐户关系。",
                            "受影响客户数是报告统计，不是本系统拓扑算法计算结果。",
                            "只展示报告中可追溯事实；不从报告图像数字化伪造SCADA时间序列。"],
            "solver_status": "not_applicable"}


def public_overview(dataset_id: str, *, limit: int = 100, offset: int = 0,
                    field_centre: str | None = None, search: str = "", bbox: str | None = None) -> dict[str, Any]:
    if dataset_id == sicily.ID:
        return sicily.overview()
    if dataset_id == AER_ID:
        manifest = aer_manifest()
        selected_field = field_centre or "Calgary"
        scope_filter = None if selected_field == "__all__" else selected_field
        assets = aer_assets(field_centre=scope_filter, search=search, offset=offset, limit=limit)
        conn = _aer_connection()
        try:
            field_centres = [row[0] for row in conn.execute("SELECT DISTINCT field_centre FROM pipes WHERE field_centre IS NOT NULL ORDER BY field_centre")]
            pipe_count = conn.execute("SELECT count(*) FROM pipes").fetchone()[0]
            event_count = conn.execute("SELECT count(*) FROM events").fetchone()[0]
            linked_count = conn.execute("SELECT count(*) FROM events WHERE link_status='linked_unique_gas_segment'").fetchone()[0]
        finally:
            conn.close()
        events = aer_events(limit=30)
        return {"dataset_id": AER_ID, "data_mode": "official_public", "status": assets["status"],
                "data_version": manifest.get("data_version"), "title": "Alberta Energy Regulator 公开管线与事件记录",
                "source_crs": next((source.get("source_crs") for source in manifest.get("sources", [])
                                     if source.get("product") == "AER Enhanced Pipeline Shapefile"), None),
                "geometry_note": "AER源数据坐标参考系见source_crs；该图仅展示监管发布几何，不对应重庆，也不代表测量/竣工位置或拓扑连接。",
                "scope": "全部分区" if scope_filter is None else selected_field,
                "available_scopes": field_centres,
                "counts": {"pipe_records": pipe_count, "release_report_records": event_count,
                           "uniquely_linked_release_records": linked_count, "assets_in_current_page": len(assets["items"])},
                "assets": assets["items"], "assets_total_in_scope": assets["total"],
                "assets_page_offset": assets["offset"], "assets_page_limit": assets["limit"],
                "assets_page_partial": assets["status"] == "partial", "events": events["items"],
                "events_total": events["total"], "events_page_partial": events["status"] == "partial",
                "event_note": "Release Report按原始事件类别展示；Release记录不自动等同于泄漏事故。",
                "source_refs": [AER_PAGE, AER_EVENTS_PAGE],
                "limitations": ["AER公开管线目录不含低压配气线", "无阀门/端口拓扑、用户连接、压力流量工况", "几何来源及授权需按AER产品说明与许可核验"],
                "solver_status": "topology_not_available"}
    if dataset_id == SGN_ID:
        assets = sgn_assets(limit=limit, offset=offset, bbox=bbox)
        return {"dataset_id": SGN_ID, "data_mode": "official_public", "status": assets["status"],
                "data_version": assets["data_version"], "title": "SGN Eastbourne 配气管线公开图层",
                "source_crs": "EPSG:4326",
                "geometry_note": "ArcGIS响应已转换为EPSG:4326；该公开位置图仅供参考，不可用于开挖许可或现场施工定位。",
                "counts": {"asset_features_current_page": len(assets["items"])}, "assets": assets["items"],
                "assets_total_in_scope": assets["total"], "assets_page_offset": assets["offset"],
                "assets_page_limit": assets["limit"], "assets_page_partial": assets["truncated"],
                "source_refs": [SGN_PAGE, SGN_LAYER],
                "limitations": ["当前图层分页结果只表示已取回记录，不当作总资产数", "无拓扑连接/阀门/用户服务关系", "不支持关阀影响推演或水力计算"],
                "solver_status": "topology_not_available"}
    if dataset_id == GASLIB_ID:
        data = gaslib_network()
        return {"dataset_id": GASLIB_ID, "data_mode": "research_benchmark", "status": "ready",
                "data_version": data["data_version"], "title": "GasLib-582 v2 研究基准网络",
                "counts": {"nodes": data["node_count"], "connections": data["connection_count"]},
                "nodes": data["nodes"], "edges": data["edges"], "solver_status": data["solver_status"],
                "solver_reason": data["solver_reason"], "geometry_note": data["geometry_note"],
                "source_refs": [GASLIB_PAGE, GASLIB_DOCS],
                "limitations": ["研究用稳态基准，不表示当前在运的现实城市管网", "图连通仅为拓扑结果，不是压力/流量解", data["solver_reason"]]}
    if dataset_id == MERRIMACK_ID:
        return _merrimack_overview()
    if dataset_id == SYNTHETIC_ID:
        return {"dataset_id": SYNTHETIC_ID, "data_mode": "synthetic", "status": "available_in_legacy_views",
                "title": "GAS-M0 合成演示", "limitations": ["独立合成演示，不是公开真实网络"]}
    raise KeyError(dataset_id)


def public_data_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/gas/public-data", tags=["gas-public-read-only"])

    def authorize_read(request: Request, dataset_id: str | None = None) -> None:
        principal = resolve_principal(request)
        if principal.authenticated:
            if dataset_id:
                from src.platform.authz import authorize_dataset
                authorize_dataset(principal, dataset_id, "public")
            elif "public" not in principal.modes:
                raise HTTPException(403, "当前主体未获准读取公开燃气数据")
            return
        peer = request.client.host if request.client else None
        enabled = os.getenv("GAS_PUBLIC_DATA_READONLY", "").strip().lower() in {"1", "true", "yes", "on"}
        if (principal.scope != "local" or not local_synthetic_anonymous_enabled() or not enabled
                or peer not in {"127.0.0.1", "::1", "localhost", "testclient"}):
            raise HTTPException(403, "本地只读公开数据模式未启用；请启用GAS_PUBLIC_DATA_READONLY并仅绑定loopback")

    @router.get("/catalog")
    def catalog(request: Request):
        authorize_read(request)
        return dataset_registry()

    @router.get("/{dataset_id}/overview")
    def overview(request: Request, dataset_id: str, limit: int = Query(default=100, ge=1, le=300),
                 offset: int = Query(default=0, ge=0), field_centre: str | None = Query(default=None, max_length=80),
                 search: str = Query(default="", max_length=120), bbox: str | None = Query(default=None, max_length=100),
                 material: str | None = Query(default=None, max_length=20),
                 pressure: str | None = Query(default=None, max_length=20), diameter_mm: float | None = Query(default=None),
                 ngn_view: Literal["detail", "region"] = "region", asset_kind: str = "main"):
        authorize_read(request, dataset_id)
        try:
            if dataset_id == charlottesville.ID:
                return charlottesville.asset_page(asset_kind, search, limit, offset, bbox)
            if dataset_id == schutterwald.ID:
                return schutterwald.overview()
            if dataset_id == ngn_sample.ID:
                return ngn_sample.overview()
            if dataset_id == ngn.NGN_ID:
                return ngn.overview(limit=limit, offset=offset, search=search, bbox=bbox,
                                    material=material, pressure=pressure, diameter_mm=diameter_mm, detail=ngn_view == "detail")
            return public_overview(dataset_id, limit=limit, offset=offset, field_centre=field_centre, search=search, bbox=bbox)
        except KeyError:
            raise HTTPException(404, "公开燃气数据集不存在") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except (PublicDatasetUnavailable, ngn.NgnUnavailable, OSError) as exc:
            raise HTTPException(503, str(exc)) from None

    @router.post("/ngn_distribution_example/run")
    def ngn_sample_run(request: Request, version: str, period: str = "base", edge_id: str | None = None,
                       trend: bool = False):
        authorize_read(request, ngn_sample.ID)
        try:
            result = ngn_sample.run(version, period, edge_id, trend=trend)
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    def sicily_agent_call(request: Request, action, *args):
        authorize_read(request, sicily.ID)
        try:
            return JSONResponse(action(resolve_principal(request).actor_id, *args), headers={"Cache-Control": "no-store"})
        except sicily_agent.Conflict as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "西西里数据或执行记录暂不可用") from None

    @router.get("/sicily_transmission/agent/health")
    def sicily_agent_health(request: Request):
        authorize_read(request, sicily.ID)
        return sicily_agent.health()

    @router.post("/sicily_transmission/agent/query")
    def sicily_agent_query(request: Request, body: sicily_agent.Query):
        return sicily_agent_call(request, sicily_agent.answer, body)

    @router.post("/sicily_transmission/agent/confirm")
    def sicily_agent_confirm(request: Request, body: sicily_agent.Confirmation):
        return sicily_agent_call(request, sicily_agent.confirm, body)

    @router.post("/sicily_transmission/agent/execution/{execution_id}")
    def sicily_agent_execution(request: Request, execution_id: sicily_agent.Token, body: sicily_agent.Context):
        return sicily_agent_call(request, sicily_agent.read_run, body, execution_id)

    @router.get("/sicily_transmission/condition")
    def sicily_condition(request: Request, case_id: str):
        authorize_read(request, sicily.ID)
        try:
            return sicily.condition(case_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.post("/sicily_transmission/baseline")
    def sicily_baseline(request: Request, case_id: str, version: str, session_id: sicily_agent.Token | None = None):
        authorize_read(request, sicily.ID)
        try:
            return sicily_agent.record(resolve_principal(request).actor_id, session_id, sicily.baseline(case_id, version))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None
        except sqlite3.Error:
            raise HTTPException(503, "西西里执行记录暂不可用") from None

    @router.post("/sicily_transmission/topology")
    @router.post("/sicily_transmission/run")
    def sicily_run(request: Request, case_id: str, edge_id: str, version: str, session_id: sicily_agent.Token | None = None):
        authorize_read(request, sicily.ID)
        try:
            return sicily_agent.record(resolve_principal(request).actor_id, session_id,
                                       sicily.run(case_id, edge_id, version, solve_hydraulics=request.url.path.endswith("/run")))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None
        except sqlite3.Error:
            raise HTTPException(503, "西西里执行记录暂不可用") from None

    @router.get("/charlottesville/service-links/{oid}")
    def cville_service_links(request: Request, oid: int):
        authorize_read(request, charlottesville.ID)
        if oid < 1:
            raise HTTPException(422, "Invalid source object ID")
        try:
            return charlottesville.service_links(oid)
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/charlottesville/events")
    def cville_events(request: Request, kind: str = "leaks", start: str | None = None, end: str | None = None,
                     status: str | None = None, cause: str | None = None, county: str | None = None,
                     limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
        authorize_read(request, charlottesville.ID)
        try:
            return charlottesville.event_page(kind, start, end, status, limit, offset, cause, county)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/charlottesville/analysis")
    def cville_analysis(request: Request, kind: str = "leaks", start: str | None = None, end: str | None = None,
                       status: str | None = None, cause: str | None = None, county: str | None = None,
                       limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0)):
        authorize_read(request, charlottesville.ID)
        try:
            return charlottesville.analysis(kind, start, end, status, cause, county, offset, limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/schutterwald/outages")
    def schutterwald_outages(request: Request, version: str):
        authorize_read(request, schutterwald.ID)
        if version != schutterwald.VERSION:
            raise HTTPException(422, "Invalid model version")
        try:
            return schutterwald.outages()
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/schutterwald/run")
    def replay_schutterwald(request: Request, edge_id: str, version: str, hydraulic: bool = False):
        authorize_read(request, schutterwald.ID)
        try:
            return JSONResponse(schutterwald.execute(edge_id, version, hydraulic), headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except OSError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/ngn/events")
    def ngn_events(request: Request, kind: str = Query(default="emergency", max_length=20),
                   start: str | None = Query(default=None, max_length=10), end: str | None = Query(default=None, max_length=10),
                   offset: int = Query(default=0, ge=0), limit: int = Query(default=30, ge=1, le=100)):
        authorize_read(request, ngn.NGN_ID)
        try:
            return ngn.events(kind, start=start, end=end, offset=offset, limit=limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except ngn.NgnUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/gaslib_582/connectivity")
    def benchmark_connectivity(request: Request, edge_id: str = Query(min_length=1, max_length=120)):
        authorize_read(request, GASLIB_ID)
        try:
            return gaslib_connectivity(edge_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except PublicDatasetUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/aer/assets")
    def aer_asset_list(request: Request, field_centre: str | None = Query(default=None, max_length=80),
                       search: str = Query(default="", max_length=120), bbox: str | None = Query(default=None, max_length=100),
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=300)):
        authorize_read(request, AER_ID)
        try:
            return aer_assets(field_centre=field_centre, search=search, bbox=bbox, offset=offset, limit=limit)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except PublicDatasetUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/aer/events")
    def aer_event_list(request: Request, pipe_id: str | None = Query(default=None, max_length=128),
                       search: str = Query(default="", max_length=120), link_status: str | None = Query(default=None, max_length=80),
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=300)):
        authorize_read(request, AER_ID)
        try:
            return aer_events(pipe_id=pipe_id, search=search, link_status=link_status, offset=offset, limit=limit)
        except PublicDatasetUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    @router.get("/sgn/assets")
    def sgn_asset_list(request: Request, bbox: str | None = Query(default=None, max_length=100),
                       offset: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=300)):
        authorize_read(request, SGN_ID)
        try:
            return sgn_assets(offset=offset, limit=limit, bbox=bbox)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        except PublicDatasetUnavailable as exc:
            raise HTTPException(503, str(exc)) from None

    return router


__all__ = ["public_data_router", "dataset_registry", "public_overview", "aer_assets", "aer_events",
           "sgn_assets", "gaslib_network", "PublicDatasetUnavailable"]
