"""Read-only Charlottesville GIS adapter. Only allow-listed display fields cross the API."""
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import json
import re
from time import monotonic
from urllib.parse import urlencode
from urllib.request import urlopen

ID = "charlottesville"
VERSION = "cville-arcgis-live-v1"
SOURCE = "https://gisweb.charlottesville.org/arcgis/rest/services/Public_Utilities/public_utilities_data_READ/MapServer"
ASSETS = {
    "main": (22, ("MAIN_ID", "MAIN_SEG_ID", "PIPE_SIZE", "MATERIALS", "PRESSURE")),
    "service": (23, ("SVC_PIPE_ID", "SVC_SEG_ID", "MAIN_ID", "PIPE_SIZE", "MATERIALS", "PRESSURE")),
    "valve": (3, ("AssetID", "ValveType", "Status", "LastInspectionDate")),
    "zone": (28, ("ZONE",)),
}
EVENTS = {
    "leaks": (1, ("LEAK_REPORTED_DATE", "DISPATCHED_DATE", "ARRIVAL_DATE", "LEAK_REPAIR_DATE",
                  "LEAK_CLASS", "LEAK_CAUSE", "STATUS", "FACILITY_TYPE", "CITY_COUNTY")),
    "reports": (2, ("LEAK_DATE", "LEAK_CLASS", "LEAK_CAUSE", "TYPE")),
}
SAFE_ID = re.compile(r"[A-Za-z0-9 _.-]{1,80}")


@lru_cache(maxsize=128)
def _request(layer, params, bucket):
    del bucket
    url = f"{SOURCE}/{layer}/query?{urlencode(dict(params))}"
    for attempt in range(2):
        try:
            with urlopen(url, timeout=12) as response:
                body = json.load(response)
            break
        except (OSError, ValueError) as exc:
            if attempt:
                raise OSError("Charlottesville GIS did not return data; retry later.") from exc
    if not isinstance(body, dict) or "error" in body:
        raise OSError("Charlottesville 官方图层查询失败")
    return body


def query(layer, **params):
    return _request(layer, tuple(sorted(params.items())), int(monotonic() // 30))


def rows(layer, fields, where, limit, offset, *, geo=False, bbox=None, order="OBJECTID ASC"):
    options = {"where": where}
    if bbox:
        try:
            a, b, c, d = map(float, bbox.split(","))
        except ValueError as exc:
            raise ValueError("地图范围无效") from exc
        if not (-180 <= a < c <= 180 and -90 <= b < d <= 90):
            raise ValueError("地图范围无效")
        options.update(geometry=f"{a},{b},{c},{d}", geometryType="esriGeometryEnvelope",
                       spatialRel="esriSpatialRelIntersects", inSR=4326)
    total = query(layer, **options, f="json", returnCountOnly="true").get("count")
    payload = query(layer, **options, outFields=",".join(("OBJECTID", *fields)),
                    resultOffset=offset, resultRecordCount=limit, orderByFields=order,
                    f="geojson" if geo else "json", outSR=4326, returnGeometry=str(geo).lower())
    features = payload.get("features")
    if not isinstance(total, int) or not isinstance(features, list):
        raise OSError("Charlottesville 返回的分页数据不完整")
    return total, features


def safe_id(value):
    return value if isinstance(value, str) and SAFE_ID.fullmatch(value) else None


def asset_page(kind="main", search="", limit=100, offset=0, bbox=None):
    if kind not in ASSETS:
        raise ValueError("未知资产图层")
    layer, fields = ASSETS[kind]
    term = safe_id(search.strip().upper())
    if search.strip() and not term:
        raise ValueError("请输入合法的资产编号")
    where = f"{fields[0]} LIKE '%{term}%'" if term else "1=1"
    total, records = rows(layer, fields, where, limit, offset, geo=True, bbox=bbox)
    result = []
    for item in records:
        props = item["properties"]
        oid = props["OBJECTID"]
        result.append({"record_id": f"{kind}:{oid}", "pipe_id": safe_id(props.get(fields[0])),
                       "geometry": item.get("geometry"),
                       "source_properties": {k: props.get(k) for k in fields},
                       "source_ref": {"layer": layer, "object_id": oid}})
    return {"dataset_id": ID, "title": "Charlottesville 城市资产与事件", "data_version": VERSION,
            "data_mode": "official_public", "status": "ready", "source_crs": "EPSG:2284",
            "assets": result, "assets_total_in_scope": total, "assets_page_offset": offset,
            "assets_page_limit": limit, "assets_page_partial": offset + len(result) < total,
            "counts": {"当前图层记录": total, "当前页": len(result)}, "source_refs": [f"{SOURCE}/{layer}"]}


def source_date(value):
    if type(value) not in (int, float):
        return None
    try:
        return datetime.fromtimestamp(value / 1000, timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def event_page(kind, start, end, status, limit, offset, cause=None, county=None):
    if kind not in EVENTS:
        raise ValueError("未知事件图层")
    layer, fields = EVENTS[kind]
    date_field = fields[0]
    where = ["1=1"]
    if status:
        if kind != "leaks" or status not in {"ACTIVE", "REPAIRED", "NO ACTION REQUIRED"}:
            raise ValueError("无效的记录状态")
        where.append(f"STATUS = '{status}'")
    if cause:
        cause = cause.upper()
        if not re.fullmatch(r"[A-Z0-9 ,._&()/+-]{1,80}", cause):
            raise ValueError("无效的原因筛选")
        where.append(f"LEAK_CAUSE = '{cause}'")
    if county:
        if kind != "leaks" or county not in {"CHARLOTTESVILLE", "ALBEMARLE"}:
            raise ValueError("无效的行政区")
        where.append(f"CITY_COUNTY = '{county}'")
    lo, hi = date.fromisoformat(start) if start else None, date.fromisoformat(end) if end else None
    if (lo and hi and hi < lo) or hi == date.max:
        raise ValueError("日期范围无效")
    if lo:
        where.append(f"{date_field} >= TIMESTAMP '{lo.isoformat()} 00:00:00'")
    if hi:
        where.append(f"{date_field} < TIMESTAMP '{(hi + timedelta(days=1)).isoformat()} 00:00:00'")
    total, records = rows(layer, fields, " AND ".join(where), limit, offset, order=f"{date_field} DESC, OBJECTID ASC")
    items = []
    for item in records:
        p = item["attributes"]
        oid = p["OBJECTID"]
        timeline = [{"label": label, "field": field, "time": source_date(p.get(field))}
                    for label, field in (("上报", date_field), ("派遣", "DISPATCHED_DATE"),
                                         ("到场", "ARRIVAL_DATE"), ("修复", "LEAK_REPAIR_DATE"))
                    if kind == "leaks" or field == date_field]
        items.append({"id": f"{kind}:{oid}", "kind": kind, "timeline": timeline,
                      "status": p.get("STATUS"), "grade": p.get("LEAK_CLASS"), "cause": p.get("LEAK_CAUSE"),
                      "area": p.get("CITY_COUNTY"), "facility_type": p.get("FACILITY_TYPE"),
                      "source_url": f"{SOURCE}/{layer}", "source_record_id": oid,
                      "asset_link_status": "unverified",
                      "durations": durations(p) if kind == "leaks" else {}})
    return {"dataset_id": ID, "data_version": VERSION, "kind": kind, "total": total, "items": items,
            "source_url": f"{SOURCE}/{layer}", "time_basis": "ArcGIS UTC"}



def service_links(oid):
    total, records = rows(23, ("SVC_PIPE_ID", "MAIN_ID"), f"OBJECTID = {oid}", 1, 0)
    if total != 1 or len(records) != 1:
        return {"dataset_id": ID, "status": "missing", "main_matches": None, "meter_matches": None}
    props = records[0]["attributes"]
    main_id = safe_id(props.get("MAIN_ID"))
    service_id = safe_id(props.get("SVC_PIPE_ID"))
    main_matches = query(22, where=f"MAIN_ID = '{main_id}'", f="json", returnCountOnly="true").get("count") if main_id else None
    meter_matches = query(4, where=f"SVC_PIPE_ID = '{service_id}'", f="json", returnCountOnly="true").get("count") if service_id else None
    duplicate_service = query(23, where=f"SVC_PIPE_ID = '{service_id}'", f="json", returnCountOnly="true").get("count") if service_id else None
    return {"dataset_id": ID, "data_version": VERSION, "status": "source_id_lookup", "object_id": oid,
            "main_id": main_id, "main_matches": main_matches,
            "main_status": "unmatched" if not main_matches else "unique" if main_matches == 1 else "ambiguous",
            "meter_matches": meter_matches if duplicate_service == 1 else None,
            "meter_link_status": "unique_service_id" if duplicate_service == 1 else "ambiguous_or_missing"}

# Keep relation keys server-side. A shared work-order number is evidence for a
# candidate only because the published layers do not define a foreign key.
DURATION_PAIRS = {
    "dispatch_arrival": ("DISPATCHED_DATE", "ARRIVAL_DATE"),
    "report_repair": ("LEAK_REPORTED_DATE", "LEAK_REPAIR_DATE"),
}


def durations(record):
    findings = {}
    for name, (start, end) in DURATION_PAIRS.items():
        original = record.get(start), record.get(end)
        times = [source_date(value) for value in original]
        if None in times:
            findings[name] = {"status": "missing" if None in original else "invalid", "seconds": None}
        else:
            seconds = (datetime.fromisoformat(times[1]) - datetime.fromisoformat(times[0])).total_seconds()
            findings[name] = {"status": "invalid" if seconds < 0 else "equal" if seconds == 0 else "ok",
                              "seconds": seconds if seconds >= 0 else None}
    return findings


def month_after(value):
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


@lru_cache(maxsize=4)
def event_snapshot(kind, bucket):
    del bucket
    layer, fields = EVENTS[kind]
    work = "WORK_ORDER_NUM" if kind == "leaks" else "WORK_ORDER"
    ids = query(layer, where="1=1", f="json", returnIdsOnly="true").get("objectIds")
    if not isinstance(ids, list) or len(set(ids)) != len(ids) or any(type(i) is not int for i in ids):
        raise OSError("Charlottesville 事件索引未通过完整性检查")
    ids.sort()
    groups = [ids[offset:offset + 500] for offset in range(0, len(ids), 500)]

    def read_page(group):
        where = f"OBJECTID >= {group[0]} AND OBJECTID <= {group[-1]}"
        payload = query(layer, where=where, f="json", returnGeometry="false",
                        outFields=",".join(("OBJECTID", *fields, work)), orderByFields="OBJECTID ASC",
                        resultRecordCount=500)
        records = payload.get("features")
        if not isinstance(records, list) or payload.get("exceededTransferLimit"):
            raise OSError("Charlottesville 事件页不完整")
        page = {entry["attributes"]["OBJECTID"]: entry["attributes"] for entry in records}
        if len(records) != len(page) or set(page) != set(group):
            raise OSError("Charlottesville 事件页与索引不一致")
        return page

    normalized = {}
    with ThreadPoolExecutor(max_workers=4) as executor:
        for page in executor.map(read_page, groups):
            normalized.update(page)
    taken = datetime.now(timezone.utc)
    stable = json.dumps(sorted(normalized.items()), ensure_ascii=False, sort_keys=True)
    return taken, sha256(stable.encode()).hexdigest(), list(normalized.values())


def candidates(layer, keys):
    if not keys:
        return {}
    where = "WORK_ORDER IN (" + ",".join("'" + key + "'" for key in sorted(keys)) + ")"
    data = query(layer, where=where, f="json", returnGeometry="false",
                 groupByFieldsForStatistics="WORK_ORDER",
                 outStatistics=json.dumps([{"statisticType": "count", "onStatisticField": "OBJECTID",
                                            "outStatisticFieldName": "match_count"}]))
    if data.get("exceededTransferLimit") or not isinstance(data.get("features"), list):
        raise OSError("Charlottesville 编号候选查询不完整")
    return {entry["attributes"]["WORK_ORDER"]: entry["attributes"]["match_count"] for entry in data["features"]}


def analysis(kind, start, end, status, cause, county, offset, limit):
    if kind not in EVENTS:
        raise ValueError("未知事件类型")
    layer, columns = EVENTS[kind]
    fetched, fingerprint, records = event_snapshot(kind, int(monotonic() // 300))
    upper = fetched.date().replace(day=1)
    lower = upper.replace(year=upper.year - 1)
    lo = date.fromisoformat(start) if start else lower if not end else date.fromisoformat(end).replace(day=1)
    hi = date.fromisoformat(end) if end else upper - timedelta(days=1) if not start else fetched.date()
    if lo > hi or hi.year == 9999 or (hi - lo).days > 3660:
        raise ValueError("请使用不超过十年的有效日期范围")
    selected, undated = [], 0
    for row in records:
        if any(value and row.get(key) != value for key, value in
               [("STATUS", status), ("LEAK_CAUSE", cause.upper() if cause else None), ("CITY_COUNTY", county)]):
            continue
        timestamp = source_date(row.get(columns[0]))
        if timestamp is None:
            undated += 1
        elif lo.isoformat() <= timestamp[:10] <= hi.isoformat():
            selected.append((timestamp, row))
    selected.sort(key=lambda item: (item[0], item[1]["OBJECTID"]), reverse=True)
    month = lo.replace(day=1)
    monthly = dict.fromkeys((timestamp[:7] for timestamp, _ in selected), 0)
    for stamp, _ in selected:
        monthly[stamp[:7]] += 1
    months = []
    while month <= hi:
        key = month.isoformat()[:7]
        months.append({"label": key, "count": monthly.get(key, 0)})
        month = month_after(month)
    work = "WORK_ORDER_NUM" if kind == "leaks" else "WORK_ORDER"
    page = selected[offset:offset + limit]
    keys = {key for _, row in page if (key := safe_id(row.get(work)))}
    try:
        mains, services = candidates(22, keys), candidates(23, keys)
        candidate_status = "ok"
    except OSError:
        mains, services = {}, {}
        candidate_status = "unavailable"
    items = []
    summary = {name: Counter() for name in DURATION_PAIRS} if kind == "leaks" else {}
    for _, row in selected:
        if kind == "leaks":
            for name, entry in durations(row).items():
                summary[name][entry["status"]] += 1
    for stamp, row in page:
        key = safe_id(row.get(work))
        matches = {"main": mains.get(key, 0), "service": services.get(key, 0)} if candidate_status == "ok" else None
        total = sum(matches.values()) if matches is not None else None
        link = "unavailable" if total is None else "ambiguous" if total > 1 else "candidate" if total else "unlinked"
        items.append({"id": f"{kind}:{row['OBJECTID']}", "time": stamp, "cause": row.get("LEAK_CAUSE"),
                      "area": row.get("CITY_COUNTY"), "link": link, "matches": matches,
                      "source_url": f"{SOURCE}/{layer}", "durations": durations(row) if kind == "leaks" else {}})
    def grouped(key):
        return [{"label": label, "count": count} for label, count in sorted(
            Counter(row.get(key) or "未记录" for _, row in selected).items(), key=lambda v: (-v[1], v[0]))]
    return {"dataset_id": ID, "kind": kind, "fetched_at": fetched.isoformat(), "snapshot_sha256": fingerprint,
            "start": lo.isoformat(), "end": hi.isoformat(), "total": len(selected), "undated": undated,
            "monthly": months, "causes": grouped("LEAK_CAUSE"), "areas": grouped("CITY_COUNTY"),
            "durations": summary, "items": items, "offset": offset, "limit": limit,
            "source_url": f"{SOURCE}/{layer}"}
