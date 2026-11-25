"""Independent checks over the phase-one source bytes, with no network access."""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import math
import re
import sys
from collections import Counter, defaultdict, deque
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zipfile import ZipFile

# Direct script execution adds the engineering root for the shared replay contract.
_ENGINEERING_ROOT = str(Path(__file__).resolve().parents[1])
if _ENGINEERING_ROOT not in sys.path:
    sys.path.insert(0, _ENGINEERING_ROOT)

from src.gas_application.monitoring_bindings import GAS_M0_MONITORING_CHANNELS


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def insist(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def csv_rows(data: bytes, delimiter: str = ",") -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""), delimiter=delimiter)
    fields = reader.fieldnames or []
    rows = list(reader)
    insist(len(set(fields)) == len(fields), "duplicate CSV header")
    insist(all(None not in row and None not in row.values() for row in rows), "CSV row width mismatch")
    return fields, rows


def unique(rows, key: str) -> dict:
    index = {row[key]: row for row in rows}
    insist(len(index) == len(rows) and all(index), f"missing or duplicate key: {key}")
    return index


def xml_nodes(parent, name: str):
    return parent.findall(f"{{*}}{name}")


def xml_name(element) -> str:
    from lxml import etree

    return etree.QName(element).localname


def xml_payload(payload: bytes, parser=None, base_url: str | None = None):
    from lxml import etree

    parser = parser if parser is not None else etree.XMLParser(resolve_entities=False, no_network=True)
    element = etree.fromstring(payload, parser, base_url=base_url)
    insist(not element.getroottree().docinfo.doctype, "DOCTYPE in GasLib input")
    return element


def gaslib_schemas(archive: ZipFile) -> dict:
    from lxml import etree

    definitions = {PurePosixPath(name).name: archive.read(name) for name in archive.namelist() if name.endswith(".xsd")}

    class LocalSchemaResolver(etree.Resolver):
        def resolve(self, url, _public_id, context):
            name = PurePosixPath(urlsplit(url).path).name
            if name not in definitions:
                raise ValueError("unverified XSD dependency")
            return self.resolve_string(definitions[name], context, base_url=name)

    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    parser.resolvers.add(LocalSchemaResolver())
    schema_names = {".net": "Gas.xsd", ".scn": "Scenario.xsd", ".cs": "CompressorStations.xsd"}
    return {kind: etree.XMLSchema(xml_payload(definitions[name], parser, name)) for kind, name in schema_names.items()}


def component_count(node_ids: set[str], endpoints: list[tuple[str, str]]) -> int:
    neighbors = defaultdict(set)
    for left, right in endpoints:
        neighbors[left].add(right)
        neighbors[right].add(left)
    remaining = set(node_ids)
    count = 0
    while remaining:
        queue = deque([remaining.pop()])
        count += 1
        while queue:
            for nearby in neighbors[queue.popleft()] & remaining:
                remaining.remove(nearby)
                queue.append(nearby)
    return count


def flow_bounds(items) -> tuple[Decimal, Decimal]:
    marks = [(item.attrib["bound"], Decimal(item.attrib["value"])) for item in items]
    values = dict(marks)
    insist(len(values) == len(marks), "GasLib repeated flow bound")
    if set(values) == {"both"}:
        lo = hi = values["both"]
    else:
        insist(set(values) == {"lower", "upper"}, "GasLib flow bounds incomplete")
        lo, hi = values["lower"], values["upper"]
    insist(lo.is_finite() and hi.is_finite() and 0 <= lo <= hi, "GasLib invalid flow bounds")
    return lo, hi


def nomination_check(scenario, network_nodes: dict) -> dict:
    boundary_nodes = xml_nodes(scenario, "node")
    expected_types = {"source": "entry", "sink": "exit"}
    expected = {identity: expected_types[xml_name(row)] for identity, row in network_nodes.items()
                if xml_name(row) in expected_types}
    actual = {row.attrib["id"]: row.attrib["type"] for row in boundary_nodes}
    insist(actual == expected and len(actual) == len(boundary_nodes), "GasLib nomination nodes or directions differ")
    groups = {"entry": [], "exit": []}
    pressure_units = set()
    explicit_pressure = 0
    outside = 0
    unit = "1000m_cube_per_hour"
    for row in boundary_nodes:
        network = network_nodes[row.attrib["id"]]
        bounds = xml_nodes(row, "flow")
        insist({item.get("unit") for item in bounds} == {unit}, "GasLib nomination flow unit mismatch")
        lo, hi = flow_bounds(bounds)
        groups[row.attrib["type"]].append((lo, hi))
        minimum, = xml_nodes(network, "flowMin")
        maximum, = xml_nodes(network, "flowMax")
        insist(minimum.get("unit") == maximum.get("unit") == unit, "GasLib network flow unit mismatch")
        outside += int(lo < Decimal(minimum.attrib["value"]) or hi > Decimal(maximum.attrib["value"]))
        pressures = xml_nodes(row, "pressure")
        if pressures:
            explicit_pressure += 1
            pressure_units.update(item.get("unit") for item in pressures)
            insist(all(Decimal(item.attrib["value"]).is_finite() for item in pressures), "GasLib invalid pressure")
            lower = next((Decimal(item.attrib["value"]) for item in pressures if item.get("bound") == "lower"), None)
            upper = next((Decimal(item.attrib["value"]) for item in pressures if item.get("bound") == "upper"), None)
            insist(lower is None or upper is None or lower <= upper, "GasLib reversed pressure bounds")
    supply, demand = [tuple(sum(item[i] for item in groups[key]) for i in (0, 1)) for key in ("entry", "exit")]
    fixed = all(lo == hi for lo, hi in groups["entry"] + groups["exit"])
    consistent = max(supply[0], demand[0]) <= min(supply[1], demand[1])
    return {
        "entry_count": len(groups["entry"]), "exit_count": len(groups["exit"]),
        "flow_unit": unit, "supply_bounds": list(map(str, supply)), "demand_bounds": list(map(str, demand)),
        "fixed_flow": fixed, "flow_balance_consistent": consistent,
        "fixed_balance_residual": str(supply[0] - demand[0]) if fixed else None,
        "flow_outside_network_bounds": outside, "nomination_pressure_units": sorted(pressure_units),
        "nodes_without_explicit_nomination_pressure": len(boundary_nodes) - explicit_pressure,
        "pressure_basis_conversion": "unavailable",
    }


def gaslib(raw: Path) -> dict:
    with ZipFile(raw / "schemaDefinition.zip") as contract:
        insist(contract.testzip() is None, "GasLib XSD checksum")
        schemas = gaslib_schemas(contract)
        schema_count = sum(name.endswith(".xsd") for name in contract.namelist())
    profiles = []
    for path in sorted(raw.glob("GasLib-*.zip")):
        with ZipFile(path) as archive:
            insist(archive.testzip() is None, "GasLib ZIP checksum")
            members = {kind: next(name for name in archive.namelist() if name.endswith(kind)) for kind in schemas}
            docs = {}
            member_shas = {}
            for kind, name in members.items():
                payload = archive.read(name)
                docs[kind] = xml_payload(payload)
                schemas[kind].assertValid(docs[kind])
                member_shas[name] = digest(payload)
            network = docs[".net"]
            nodes, = xml_nodes(network, "nodes")
            connections, = xml_nodes(network, "connections")
            indexed = {n.attrib["id"]: n for n in nodes}
            ids = {edge.attrib["id"] for edge in connections}
            insist(len(indexed) == len(nodes) and len(ids) == len(connections), "GasLib duplicate node or edge")
            endpoints = [(edge.attrib["from"], edge.attrib["to"]) for edge in connections]
            insist(all(left in indexed and right in indexed and left != right for left, right in endpoints), "GasLib endpoint mismatch")
            in_net = {edge.attrib["id"] for edge in xml_nodes(connections, "compressorStation")}
            cs_rows = xml_nodes(docs[".cs"], "compressorStation")
            in_cs = {row.attrib["id"] for row in cs_rows}
            insist(in_net == in_cs and len(in_cs) == len(cs_rows), "GasLib compressor identity mismatch")
            scenarios = [nomination_check(s, indexed) for s in xml_nodes(docs[".scn"], "scenario")]
            insist(bool(scenarios), "GasLib nomination missing")
            network_units = Counter(item.get("unit") for item in network.iter() if item.get("unit"))
            profiles.append({
                "archive": path.name, "members_sha256": member_shas,
                "xsd_documents_passed": len(docs), "node_count": len(nodes), "connection_count": len(connections),
                "component_count_undirected": component_count(set(indexed), endpoints),
                "connection_kinds": dict(Counter(xml_name(edge) for edge in connections)),
                "node_kinds": dict(Counter(xml_name(node) for node in nodes)),
                "compressor_station_count": len(in_cs), "network_unit_counts": dict(network_units), "scenarios": scenarios,
            })
    insist(len(profiles) == 2, "expected two phase-one GasLib archives")
    return {"xsd_member_count": schema_count, "instances": profiles}


def lon_lat(position) -> bool:
    return (isinstance(position, list) and len(position) == 2
            and all(type(n) in (int, float) and math.isfinite(n) for n in position)
            and abs(position[0]) <= 180 and abs(position[1]) <= 90)


def scigrid(path: Path, prefix: str, expected_md5: str) -> tuple[dict, list]:
    md5 = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
    insist(md5 == expected_md5, "SciGRID published MD5 mismatch")
    quarantine = []
    with ZipFile(path) as archive:
        insist(archive.testzip() is None, "SciGRID ZIP checksum")
        license_text = archive.read("data/LICENSE").decode("utf-8")
        insist("Attribution 4.0 International" in license_text, "SciGRID packaged license mismatch")
        tables = {}
        for name in sorted(n for n in archive.namelist() if n.endswith(".csv")):
            layer = PurePosixPath(name).stem.removeprefix(prefix + "_")
            geo_name = name.removesuffix(".csv") + ".geojson"
            data = json.loads(archive.read(geo_name))
            insist(data["type"] == "FeatureCollection", f"SciGRID wrong collection in {layer}")
            fields, table = csv_rows(archive.read(name), ";")
            tables[layer] = (name, geo_name, fields, table, data)
        if not tables:
            raise ValueError("SciGRID layers absent")
        summaries = {}
        for layer, (_, _, fields, table, data) in tables.items():
            feature_types = Counter((row.get("geometry") or {}).get("type", "missing") for row in data["features"])
            summaries[layer] = {
                "csv_header": fields, "csv_rows": len(table), "geojson_features": len(data["features"]),
                "geometry_types": dict(feature_types), "crs_explicit": "crs" in data,
            }

        _, _, _, nodes, node_geo = tables["Nodes"]
        csv_nodes = unique(nodes, "id")
        geo_nodes = unique([dict(f["properties"], geometry=f["geometry"]) for f in node_geo["features"]], "id")
        insist(csv_nodes.keys() == geo_nodes.keys(), "SciGRID node CSV–GeoJSON identity mismatch")
        for identity, row in csv_nodes.items():
            geometry = geo_nodes[identity]["geometry"]
            coordinates = geometry["coordinates"]
            insist(geometry["type"] == "Point" and lon_lat(coordinates), "SciGRID node geometry")
            csv_position = [float(row["long"]), float(row["lat"])]
            insist(lon_lat(csv_position) and all(abs(a-b) <= 1e-7 for a, b in zip(coordinates, csv_position)), "SciGRID axis mismatch")

        csv_member, geo_member, _, lines, line_geo = tables["PipeSegments"]
        csv_lines = unique(lines, "id")
        features = {f["properties"]["id"]: (i, f) for i, f in enumerate(line_geo["features"])}
        insist(len(features) == len(line_geo["features"]) and csv_lines.keys() == features.keys(), "SciGRID segment CSV–GeoJSON identity mismatch")
        flags = Counter()
        for row_number, row in enumerate(lines, start=2):
            feature_index, feature = features[row["id"]]
            left, right = ast.literal_eval(row["node_id"])
            reasons = []
            geometry = feature["geometry"]
            coords = geometry["coordinates"]
            if left not in csv_nodes or right not in csv_nodes:
                reasons.append("orphan_endpoint")
            if left == right:
                reasons.append("self_loop")
            if geometry["type"] != "LineString" or any(not lon_lat(p) for p in coords):
                reasons.append("invalid_geometry")
            elif len({tuple(p) for p in coords}) < 2:
                reasons.append("degenerate_geometry")
            length = feature["properties"].get("param", {}).get("length_km")
            if type(length) in (int, float) and (not math.isfinite(length) or length <= 0):
                reasons.append("nonpositive_or_invalid_length")
            if reasons:
                flags.update(reasons)
                quarantine.append({
                    "row_key": row["id"], "csv_member": csv_member, "csv_row": row_number, "csv_fields": row,
                    "geojson_member": geo_member, "feature_index_0_based": feature_index, "feature": feature,
                    "reasons": reasons, "archive_sha256": digest(path.read_bytes()),
                })
        return {
            "publisher_md5": expected_md5, "computed_md5": md5, "layers": summaries,
            "nodes": len(csv_nodes), "segments_raw": len(lines), "segments_accepted": len(lines) - len(quarantine),
            "segments_quarantined": len(quarantine), "reason_counts": dict(flags),
            "coordinate_check": "CSV long/lat versus GeoJSON positions, 1e-7 degrees",
        }, quarantine


def yearbook(path: Path, wheel: Path) -> dict:
    expected = "ea762c3d29f4cca48d82df517b6d89fbce4db3107f9d78713e48cd321d5c9aa9"
    insist(digest(wheel.read_bytes()) == expected, "unverified XLS parser")
    sys.path.insert(0, str(wheel.resolve()))
    try:
        import xlrd
    finally:
        sys.path.remove(str(wheel.resolve()))
    insist(xlrd.__version__ == "2.0.2", "unexpected XLS parser version")
    book = xlrd.open_workbook(file_contents=path.read_bytes(), on_demand=True, formatting_info=True)
    tables = {}
    rows = []
    try:
        for table in ("5-2", "5-2-1"):
            sheet = book.sheet_by_name(table)
            footnotes = [
                {"row": i + 1, "column": col + 1, "text": value}
                for i in range(sheet.nrows) for col, value in enumerate(sheet.row_values(i))
                if isinstance(value, str) and re.match(r"^\s*(?:注|说明|备注|Note)\s*[:：]", value, re.I)
            ]
            tables[table] = {
                "title": sheet.cell_value(0, 0), "row_extent_including_formatting": sheet.nrows,
                "populated_rows": sum(any(isinstance(v, (int, float)) or isinstance(v, str) and v.strip() for v in sheet.row_values(i)) for i in range(sheet.nrows)),
                "columns": sheet.ncols, "footnotes": footnotes, "cell_comments": len(sheet.cell_note_map),
            }
            for i in range(5, sheet.nrows):
                label = sheet.cell_value(i, 0)
                if not isinstance(label, str) or label.strip() not in {"重庆", "重庆市"}:
                    continue
                layer = "provincial_table_row" if table == "5-2" else (
                    "city_table_child_row" if label != label.lstrip() else "city_table_group_header"
                )
                values = [sheet.cell_value(i, col) if sheet.cell_type(i, col) not in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK}
                          else None for col in range(1, 13)]
                rows.append({"sheet": table, "row": i + 1, "raw_label": label, "level": layer,
                             "source_style": sheet.cell_xf_index(i, 0), "year": 2025, "values_b_to_m": values})
        return {"book_sheets": book.nsheets, "statistics_year": 2025, "tables": tables, "rows": rows,
                "matched_numeric_b_to_m": len({tuple(row["values_b_to_m"]) for row in rows}) == 1,
                "grouping_basis": "workbook table titles, raw label leading spaces and adjacent province groups",
                "aggregation": "unavailable"}
    finally:
        book.release_resources()


def k001(raw: Path) -> tuple[dict, dict]:
    net = json.loads((raw / "gas54_network.json").read_bytes())
    case = json.loads((raw / "gas54_case.json").read_bytes())
    replay = raw / "gas_monitoring_replay"
    manifest = json.loads((replay / "manifest.json").read_bytes())
    insist(net["data_mode"] == case["data_mode"] == manifest["data_mode"] == "synthetic", "K-001 data identity")
    insist(net["network_id"] == case["network_id"] and net["coordinate_system"] == "schematic", "K-001 network binding")
    ids = {name: unique(net[name], key) for name, key in (
        ("nodes", "node_id"), ("edges", "edge_id"), ("pipes", "pipe_id"), ("valves", "valve_id"), ("consumers", "consumer_id")
    )}
    unique(case["events"], "event_id")
    for edge in ids["edges"].values():
        insist(edge["from_node_id"] in ids["nodes"] and edge["to_node_id"] in ids["nodes"], "K-001 topology endpoint")
        insist(ids["pipes"][edge["pipe_id"]]["edge_id"] == edge["edge_id"], "K-001 pipe edge backlink")
    for valve in ids["valves"].values():
        insist(valve["node_id"] in ids["nodes"] and set(valve["incident_edge_ids"]) <= ids["edges"].keys(), "K-001 valve relation")
    for event in case["events"]:
        insist(set(event["fault_edge_ids"] + event["disabled_edge_ids"]) <= ids["edges"].keys()
               and set(event["closed_valve_ids"]) <= ids["valves"].keys(), "K-001 event relation")
    artifacts = {name: (replay / f"{name}.csv").read_bytes() for name in ("assets", "monitoring")}
    for name, value in artifacts.items():
        insist(digest(value) == manifest["sources"][name]["sha256"], "K-001 replay manifest mismatch")
    _, assets = csv_rows(artifacts["assets"])
    keys, observations = csv_rows(artifacts["monitoring"])
    terminals = unique(assets, "asset_id")
    unique(assets, "source_record_id")
    unique(observations, "source_record_id")
    insist(set(terminals) <= ids["pipes"].keys(), "K-001 replay assets")
    latest = {}
    units = {metric: unit for _, _, metric, unit in GAS_M0_MONITORING_CHANNELS}
    rows = []
    for row_num, row in enumerate(observations, start=2):
        observed = datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00"))
        insist(observed.utcoffset() == timedelta(0), "K-001 observation clock is not UTC")
        sensor = row["sensor_id"]
        insist(sensor not in latest or latest[sensor] < observed, "K-001 duplicated or out-of-order reading")
        latest[sensor] = observed
        channel = (row["asset_id"], sensor, row["metric"], row["unit"])
        insist(row["asset_id"] in terminals and channel in GAS_M0_MONITORING_CHANNELS,
               f"K-001 monitoring sensor binding mismatch at row {row_num}")
        value = Decimal(row["value"]) if row["value"] else None
        insist(value is None or value.is_finite(), "K-001 invalid number")
        insist(row["quality"] in {"valid", "suspect", "missing", "invalid"}, "K-001 unrecognized quality")
        insist(value is not None or row["quality"] in {"missing", "invalid"}, "K-001 missing reading marked usable")
        rows.append({**row, "value": float(value) if value is not None else None, "source_row": row_num})
    source_assets = [dict(row, district_id=row["district_id"] or None) for row in assets]
    asset_union = set().union(*(ids[name] for name in ("nodes", "pipes", "valves", "consumers")))
    return ({
        "case_id": case["case_id"], "network_id": net["network_id"], "replay_id": manifest["dataset_id"],
        "version": manifest["version"], "coordinate_system": net["coordinate_system"],
        "table_counts": {**{name: len(values) for name, values in ids.items()}, "events": len(case["events"]),
                         "assets": len(asset_union), "replay_assets": len(assets), "observations": len(rows), "sensors": len(latest)},
        "observation_fields": keys, "metric_units": units, "quality_counts": dict(Counter(row["quality"] for row in rows)),
        "time_min": min(row["observed_at"] for row in rows), "time_max": max(row["observed_at"] for row in rows),
        "missing_district_count": sum(row["district_id"] is None for row in source_assets),
        "network_schema": net["schema_version"], "case_schema": case["schema_version"],
    }, {"assets": source_assets, "observations": rows, "data_mode": "synthetic",
        "observation_sha256": manifest["sources"]["monitoring"]["sha256"]})
