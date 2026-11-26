"""Read-only access to the audited NGN operator snapshot."""
from __future__ import annotations

from contextlib import closing
from datetime import date
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import zlib


NGN_ID = "ngn_the_north"
NGN_HOME = "https://northerngasopendataportal.co.uk/dataset/20"
HISTORY = {"emergency": "应急呼叫", "interruption": "非计划停供"}
LIMITS = ["公开在运低压、中压主管，地图覆盖 The North。", "历史记录覆盖 NGN 全网，原件没有可核验的管段关联键。",
          "公开资料供查询与展示。阀门设备关系、用户接入和运行工况未交付。"]


class NgnUnavailable(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _verify(root: Path, manifest_time: int, db_time: int, size: int):
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        with (root / "ngn_catalog.sqlite3").open("rb") as fp:
            digest = hashlib.file_digest(fp, "sha256").hexdigest()
        if (manifest["dataset_id"] != NGN_ID or manifest["database"] != "ngn_catalog.sqlite3"
                or manifest["database_sha256"] != digest):
            raise NgnUnavailable("NGN 快照身份或 SHA-256 校验失败")
        return manifest
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise NgnUnavailable("NGN 快照清单无法读取") from exc


def snapshot():
    configured = os.getenv("GAS_NGN_DATA_ROOT")
    if not configured:
        raise NgnUnavailable("未配置 GAS_NGN_DATA_ROOT。请先导入 NGN 官方原包。")
    root = Path(configured).resolve()
    db = root / "ngn_catalog.sqlite3"
    try:
        stat = db.stat()
        manifest = _verify(root, (root / "manifest.json").stat().st_mtime_ns, stat.st_mtime_ns, stat.st_size)
        return db, manifest
    except OSError as exc:
        raise NgnUnavailable("NGN 快照文件缺失") from exc


def connect(path):
    conn = sqlite3.connect(path.as_uri()+"?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def registry_item():
    item = dict(dataset_id=NGN_ID, label="NGN · The North 配气管线", data_mode="official_public",
                status="ready", default=False, source_url=NGN_HOME, limitations=LIMITS)
    try:
        _, manifest = snapshot()
        return dict(item, data_version=manifest["data_version"], description="NGN 官方原件的本地只读快照")
    except NgnUnavailable as exc:
        return dict(item, status="unavailable", data_version=None, description=str(exc))


@lru_cache(maxsize=4)
def _facets(path, version):
    with closing(connect(path)) as db:
        return {
            "material": [dict(r) for r in db.execute("SELECT material AS value,count(*) AS count FROM assets GROUP BY material ORDER BY count(*) DESC")],
            "pressure": [dict(r) for r in db.execute("SELECT pressure AS value,count(*) AS count FROM assets GROUP BY pressure ORDER BY pressure")],
            "diameter_mm": [dict(r) for r in db.execute("SELECT diameter_mm AS value,count(*) AS count FROM assets GROUP BY diameter_mm ORDER BY diameter_mm")],
        }


def overview(*, limit, offset, search, bbox, material=None, pressure=None, diameter_mm=None, detail=False):
    path, manifest = snapshot()
    version = manifest["data_version"]
    base = manifest["assets"]
    conditions, params = [], []
    if search.strip():
        conditions.append("instr(lower(a.asset_id), lower(?)) > 0")
        params.append(search.strip())
    for field, value in (("material", material), ("pressure", pressure), ("diameter_mm", diameter_mm)):
        if value is not None:
            if field == "diameter_mm" and (not math.isfinite(value) or value <= 0):
                raise ValueError("管径需为正有限数")
            conditions.append(f"a.{field} = ?")
            params.append(value)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    if detail and not bbox and not search.strip():
        # Pick a reproducible detail window from the same filtered geometry.
        with closing(connect(path)) as db:
            cell = db.execute("""
                SELECT round((r.minx+r.maxx)*100) AS x, round((r.miny+r.maxy)*100) AS y
                FROM asset_rtree r JOIN assets a ON a.fid=r.fid
                """+where+" GROUP BY x,y ORDER BY count(*) DESC,y,x LIMIT 1", params).fetchone()
        if cell:
            x, y = cell[0]/200, cell[1]/200
            bbox = ",".join(str(v) for v in (x-.003, y-.0024, x+.003, y+.0024))
    viewport_bbox = None
    if bbox:
        try:
            west, south, east, north = map(float, bbox.split(","))
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise ValueError()
        except ValueError:
            raise ValueError("bbox 需为有效的西、南、东、北坐标") from None
        from_query = " FROM asset_rtree r JOIN assets a ON r.fid=a.fid"
        spatial_where = " WHERE " + " AND ".join([*conditions, "r.maxx>=?", "r.minx<=?", "r.maxy>=?", "r.miny<=?"])
        spatial_params = [*params, west, east, south, north]
        viewport_bbox = [west, south, east, north]
    else:
        from_query, spatial_where, spatial_params = " FROM assets a", where, params
    with closing(connect(path)) as db:
        filtered = db.execute("SELECT count(*) FROM assets a"+where, params).fetchone()[0] if conditions else base["rows"]
        viewport_count = db.execute("SELECT count(*)"+from_query+spatial_where, spatial_params).fetchone()[0] if bbox else filtered
        order = "a.fid" if bbox or search else "a.map_rank,a.fid"
        records = db.execute("SELECT a.*"+from_query+spatial_where+f" ORDER BY {order} LIMIT ? OFFSET ?",
                             [*spatial_params,limit,offset]).fetchall()
        grid = []
        if not bbox and not detail:
            # Assign each drawable record to exactly one cell using its bounding-box centre.
            cells = db.execute("""
                SELECT round((r.minx + r.maxx)*12.5) AS x, round((r.miny + r.maxy)*12.5) AS y, count(*) AS n
                FROM asset_rtree r JOIN assets a ON a.fid=r.fid
                """+where+" GROUP BY x,y ORDER BY y,x", params)
            grid = [{"bbox": [(x-.5)/25, (y-.5)/25, (x+.5)/25, (y+.5)/25], "count": n} for x,y,n in cells]
    assets = []
    for row in records:
        props = json.loads(row["attrs"])
        assets.append({"record_id": f"ngn:{row['fid']}", "pipe_id": row["asset_id"],
                       "source_properties": props | {"diameter_mm": row["diameter_mm"]},
                       "geometry": json.loads(zlib.decompress(row["geom"])) if row["geom"] else None,
                       "source_ref": {"layer": base["layer"], "OBJECTID": row["fid"], "url": NGN_HOME}})
    return {"dataset_id": NGN_ID, "data_mode": "official_public", "data_version": version, "status": "ready",
            "title": "NGN · The North 在运配气主管", "scope": "The North", "source_crs": base["source_crs"],
            "display_crs": base["display_crs"], "region_bbox": base["bbox"], "viewport_bbox": viewport_bbox,
            "geometry_note": "来源 EPSG:27700，地图使用 WGS84。公开管线仅供参考。",
            "counts": {"pipe_records": base["rows"], "drawable_records": base["drawable"],
                       "assets_in_current_page": len(assets), "filtered_records": filtered},
            "facets": _facets(path, version), "assets": assets, "assets_total_in_scope": viewport_count,
            "overview_grid": grid,
            "assets_page_offset": offset, "assets_page_limit": limit, "assets_page_partial": offset+len(assets)<viewport_count,
            "history_sources": manifest["history"], "source_refs": [s["url"] for s in manifest["sources"].values()],
            "limitations": LIMITS, "solver_status": "topology_not_available"}


def events(kind, *, start, end, offset, limit):
    path, manifest = snapshot()
    if kind not in HISTORY:
        raise ValueError("未知 NGN 历史记录类型")
    base = {"dataset_id": NGN_ID, "data_version": manifest["data_version"], "kind": kind, "offset": offset, "limit": limit}
    if kind not in manifest["history"]:
        return dict(base, status="unavailable", total=None, items=[], monthly=[], coverage=None,
                    reason="这一类 NGN 官方历史原件尚未导入")
    try:
        start = date.fromisoformat(start).isoformat() if start else None
        end = date.fromisoformat(end).isoformat() if end else None
    except ValueError:
        raise ValueError("历史日期应为 YYYY-MM-DD") from None
    if start and end and start > end:
        raise ValueError("历史日期起点晚于终点")
    where, params = "WHERE kind=?", [kind]
    if start:
        where += " AND day>=?"
        params.append(start)
    if end:
        where += " AND day<=?"
        params.append(end)
    with closing(connect(path)) as db:
        rows = db.execute(f"SELECT * FROM events {where} ORDER BY day DESC,source_row DESC LIMIT ? OFFSET ?",
                          [*params,limit,offset]).fetchall()
        total = db.execute(f"SELECT count(*) FROM events {where}", params).fetchone()[0]
        monthly = [dict(r) for r in db.execute(f"""SELECT substr(day,1,7) AS month,count(*) AS count FROM events
            {where} AND day IS NOT NULL GROUP BY month ORDER BY month""", params)]
    source = manifest["sources"][kind]
    items = [{"record_id": f"{kind}:{r['source_row']}", "source_job_id": r["job_id"],
              "date": r["day"], "time": r["clock"], "category": r["category"],
              "properties": json.loads(r["attrs"]), "scope": manifest["history"][kind]["coverage"],
              "source_ref": {"url": source["url"], "file": source["file"], "sheet": "Data", "row": r["source_row"]}}
             for r in rows]
    return dict(base, status="ready", items=items, total=total, monthly=monthly,
                coverage=manifest["history"][kind], source=source, scope="NGN 全网（来源发布范围）")
