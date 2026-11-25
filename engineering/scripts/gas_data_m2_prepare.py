"""Verify the phase-one inventory and build an isolated M2 candidate data package."""

from __future__ import annotations

import argparse
import json
import re
from decimal import Decimal, InvalidOperation
import shutil
import tempfile
from pathlib import Path, PurePosixPath

from gas_data_m2_profiles import canonical, digest, gaslib, insist, k001, scigrid, yearbook


VERSION = "gas-data-m2/1"
MAPPING = {
    "OWN-K001": [
        ("network.nodes[].node_id", "node_id", None), ("network.nodes[].layout.{x,y}", "layout", "schematic"),
        ("network.edges[].{from_node_id,to_node_id}", "topology_endpoint_ids", None),
        ("network.pipes[].{pipe_id,edge_id,material}", "asset_id,edge_id,material", None),
        ("network.pipes[].diameter_mm", "diameter", "mm"),
        ("network.valves[].{valve_id,node_id,incident_edge_ids}", "asset_id,node_id,incident_edge_ids", None),
        ("case.events[].{event_id,time_hours,kind}", "event_id,scenario_time,event_kind", "h from scenario start"),
        ("assets.csv:district_id", "district_id", None),
        ("monitoring.csv:source_record_id,asset_id,sensor_id,observed_at,quality", "observation_id,asset_id,sensor_id,observed_at,quality", "UTC timestamp"),
        ("monitoring.csv:value WHERE metric=pressure", "observation.value", "kPa"),
        ("monitoring.csv:value WHERE metric=flow", "observation.value", "Nm3/h"),
    ],
    "DE-GASLIB": [
        (".net:nodes/*/@id", "node_id", None), (".net:connections/*/@id,@from,@to", "edge_id,from_node_id,to_node_id", None),
        (".net:pipe/length,diameter", "length,diameter", "km, mm"),
        (".scn:node/@id,@type and flow/@bound", "node_id,direction,nomination_bound", None),
        (".scn:node/flow", "normal_volume_rate", "1000m_cube_per_hour"),
        (".net:pressureMin,pressureMax and .scn:node/pressure", "source_pressure_bounds", "bar and barg preserved separately"),
        (".cs:compressorStation/@id", "compressor_station_id", None),
    ],
    "SCIGRID": [
        ("Nodes CSV:id; PipeSegments CSV:id and node_id", "node_id or edge_id and endpoint_ids", None),
        ("Nodes CSV:long,lat; GeoJSON:geometry.coordinates", "lon,lat or line_positions", "degrees, RFC 7946 axis order"),
        ("GeoJSON properties:param.length_km", "source_length", "km"),
    ],
    "CN-CQ-STATS-2025": [
        ("5-2!A33 and 5-2-1!A555:A556", "sheet,row,raw_label,level,statistics_year", None),
        ("columns B,D:I", "stored_capacity and volume measures", "10^4 m³"),
        ("column C", "pipe_length", "km"),
        ("columns J:K", "customer_counts", "households"),
        ("column L", "population_with_access", "10^4 persons"),
        ("column M", "CNG_station_count", "stations"),
    ],
}

EXPERIMENTS = {
    "CN-SHENZHEN-GAS": ("1.0", "深圳社区", "observational_monitoring"),
    "CN-UPCH-LEAK": ("1.0", "实验场地", "experimental_measurement"),
    "CN-NATIONAL-PIPE-THREAT": ("1.0", "实验场地", "experimental_measurement"),
    "CN-OPENSPACE-GAS": ("1.0", "实验场地", "experiment_and_simulation"),
    "CN-XJTU-CFD": ("1.0", "实验场地", "experiment_and_simulation"),
    "US-DRYAD-CH4": ("4", "美国实验场地", "experimental_measurement"),
    "US-TEXAS-INSENSE": ("1", "美国实验场地", "experimental_measurement"),
    "US-MENDELEY-LEAK": ("1", "数值模拟", "simulation"),
}


def inspect_inventory(source: Path) -> dict:
    listing = source / "02_核验证据/SHA256SUMS_20261120.txt"
    manifest_bytes = listing.read_bytes()
    checked = {}
    for line in manifest_bytes.decode("utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        sha, relative = line.split("  ", 1)
        posix = PurePosixPath(relative)
        insist(not posix.is_absolute() and ".." not in posix.parts and relative not in checked, "invalid manifest path")
        path = source / relative
        insist(path.resolve().is_relative_to(source) and not path.is_symlink(), "source escape")
        insist(digest(path.read_bytes()) == sha, f"phase-one hash mismatch: {relative}")
        checked[relative] = sha
    disk_raw = {p.relative_to(source).as_posix() for p in (source / "01_原始文件").rglob("*") if p.is_file()}
    raw_entries = {path: sha for path, sha in checked.items() if path.startswith("01_原始文件/")}
    insist(disk_raw == raw_entries.keys(), "phase-one raw inventory is incomplete")
    return {"phase_one_manifest_sha256": digest(manifest_bytes), "all_verified": checked, "raw_verified": raw_entries}


def issue(code: str, count: int | None, detail: str) -> dict:
    return {"code": code, "count": count, "detail": detail}


def base_entry(source: dict) -> dict:
    identity = source["id"]
    result = {
        "dataset_id": identity, "title": source["name"], "source_uri": source["url"].replace("http://", "https://", 1) or None,
        "version": None, "sha256": None, "source_kind": "public_portal", "data_kind": "unverified_catalog",
        "geo_scope": source["region"], "coordinate_system": None, "statistics_time": None,
        "schema_status": "unverified", "quality_status": "unavailable", "license_status": "unconfirmed",
        "synthetic_flag": None, "usage_scope": "catalog_only", "ingest_status": "catalog_only",
        "sources": [], "field_mappings": [], "transformations": [],
        "normalized_sha256": None, "quarantine_sha256": None,
        "record_counts": {}, "issues": [issue("source_bytes_unavailable", None, "原件字段与数据质量尚无文件级证据。")],
        "facts": [],
    }
    if identity in EXPERIMENTS:
        version, region, kind = EXPERIMENTS[identity]
        result.update(version=version, geo_scope=region, source_kind="experiment_repository", data_kind=kind,
                      synthetic_flag={"simulation": True, "experiment_and_simulation": None}.get(kind, False))
        if identity.startswith("CN-"):
            result.update(license_status="authorization_pending", ingest_status="authorization_pending")
            if identity == "CN-SHENZHEN-GAS":
                result["source_uri"] = "https://www.nbsdc.cn/general/dataDetail?id=628357d699f1de7576b0ed0d&type=1"
        elif identity == "US-DRYAD-CH4":
            result.update(license_status="CC0-1.0", ingest_status="download_blocked")
        else:
            result.update(license_status="CC0-1.0" if identity == "US-TEXAS-INSENSE" else "CC-BY-4.0")
    elif identity.startswith("US-PHMSA"):
        result.update(source_kind="government", data_kind="official_incidents" if identity.endswith("INCIDENT") else "public_statistics",
                      synthetic_flag=False, ingest_status="download_blocked", schema_status="documented")
    elif identity in {"US-HIFLD-EIA", "US-DOT-EIA-ARCGIS"}:
        result.update(source_kind="government", data_kind="historical_network", synthetic_flag=False,
                      coordinate_system="EPSG:3857 (service metadata)" if identity == "US-DOT-EIA-ARCGIS" else None)
    elif identity == "CN-KG-PIPE-SAFETY":
        result.update(version="1.0", source_kind="research_repository", data_kind="knowledge_collection",
                      license_status="authorization_pending", ingest_status="authorization_pending")
    elif identity == "GLOBAL-GHSL-POP":
        result.update(version="R2023A", source_kind="government", data_kind="public_statistics",
                      license_status="CC-BY-4.0", schema_status="documented")
    elif identity == "CN-AMAP-DISTRICT":
        result.update(version="2.0", source_kind="map_provider", data_kind="administrative_service", geo_scope="重庆",
                      coordinate_system="GCJ-02", license_status="service_terms", schema_status="documented",
                      ingest_status="service_only", usage_scope="administrative_reference")
    return result


def nomination_balance_status(flow: dict) -> str:
    if flow.get("flow_balance_consistent") is False:
        return "unbalanced"
    try:
        supply_low, supply_high = map(Decimal, flow["supply_bounds"])
        demand_low, demand_high = map(Decimal, flow["demand_bounds"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "unverified"
    if (not all(value.is_finite() for value in (supply_low, supply_high, demand_low, demand_high))
            or not 0 <= supply_low <= supply_high or not 0 <= demand_low <= demand_high):
        return "unverified"
    if max(supply_low, demand_low) > min(supply_high, demand_high):
        return "unbalanced"
    if (flow.get("flow_balance_consistent") is not True or flow.get("flow_outside_network_bounds") != 0
            or flow.get("flow_unit") != "1000m_cube_per_hour"):
        return "unverified"
    if flow.get("fixed_flow") is False:
        return "overlapping_ranges"
    if flow.get("fixed_flow") is not True or supply_low != supply_high or demand_low != demand_high:
        return "unverified"
    try:
        residual = Decimal(flow["fixed_balance_residual"])
    except (KeyError, TypeError, ValueError, InvalidOperation):
        return "unverified"
    if not residual.is_finite():
        return "unverified"
    return "balanced" if residual == 0 else "unbalanced"


def apply_profile(entry: dict, profile: dict, files: list[dict], normalized_sha: str, quarantine_sha: str | None, commit: str) -> dict:
    identity = entry["dataset_id"]
    combined_sha = files[0]["sha256"] if len(files) == 1 else digest(canonical(files))
    mappings = MAPPING["SCIGRID"] if identity.startswith("EU-SCIGRID-") else MAPPING[identity]
    entry.update(
        sha256=combined_sha, sources=files, schema_status="verified", normalized_sha256=normalized_sha,
        quarantine_sha256=quarantine_sha, quality_status="checked", ingest_status="candidate_ready",
        field_mappings=[{"raw_field": raw, "normalized_field": name, "unit": unit} for raw, name, unit in mappings],
        transformations=["原件按 SHA-256 校验；保留源单位。缺项保留 null。"], issues=[],
    )
    if identity == "OWN-K001":
        entry.update(source_kind="project_fixture", data_kind="internal_synthetic", geo_scope="GAS-M0 合成拓扑",
                     synthetic_flag=True, coordinate_system="schematic", version=profile["version"],
                     source_uri=f"https://github.com/catiseyeqaq/urban-pipeline-agent/tree/{commit}/engineering/experiments/gas_m0/data",
                     license_status="internal_fixture", usage_scope="synthetic_demo", quality_status="partial",
                     record_counts=profile["table_counts"],
                     statistics_time=f"{profile['time_min']} / {profile['time_max']}",
                     facts=["合成监测保留源记录 ID、UTC 时间、质量码和原单位。",
                            "观测须匹配已登记的资产、测点、指标与单位。", "示意坐标与真实行政区无关联。"])
        entry["issues"].append(issue("district_id_missing", profile["missing_district_count"], "回放资产缺少区县映射，值为 null。"))
        entry["issues"].append(issue("authorized_coordinates_unavailable", None, "合成资产没有授权 GIS 坐标。"))
        entry["transformations"].append("资产 CSV 的空 district_id 转为 null；保留原始时间文本。")
    elif identity == "DE-GASLIB":
        instances = profile["instances"]
        nominations = [
            (f"GasLib-{instance['node_count']} 工况 {ordinal}", flow)
            for instance in instances for ordinal, flow in enumerate(instance["scenarios"], start=1)
        ]
        facts = []
        unbalanced = []
        unverified = []
        for label, flow in nominations:
            status = nomination_balance_status(flow)
            if status == "balanced":
                facts.append(f"{label} 的固定进出流量总量一致，单位为 {flow['flow_unit']}。")
            elif status == "overlapping_ranges":
                facts.append(f"{label} 的供需允许区间有交集，固定流量配平尚未验证。")
            elif status == "unbalanced":
                unbalanced.append(label)
            else:
                unverified.append(label)
        entry.update(version="v1-20211130", source_kind="research_repository", data_kind="synthetic_research_benchmark",
                     geo_scope="德国 ZIB 研究测试", coordinate_system="benchmark_layout", synthetic_flag=True,
                     license_status="CC-BY-3.0", usage_scope="research_benchmark", quality_status="partial",
                     source_uri="https://gaslib.zib.de/testData.html",
                     record_counts={f"GasLib-{item['node_count']} nodes": item["node_count"] for item in instances}
                     | {"XML": sum(item["xsd_documents_passed"] for item in instances), "nominations": len(nominations)},
                     facts=facts)
        if unbalanced:
            entry["issues"].append(issue("nomination_flow_unbalanced", len(unbalanced), "供需流量未配平的工况：" + "、".join(unbalanced) + "。"))
        if unverified:
            entry["issues"].append(issue("nomination_flow_balance_unverified", len(unverified), "流量配平待核实的工况：" + "、".join(unverified) + "。"))
        missing = sum(flow["nodes_without_explicit_nomination_pressure"] for _, flow in nominations)
        outside = sum(flow["flow_outside_network_bounds"] for _, flow in nominations)
        if missing:
            entry["issues"].append(issue("nomination_pressure_missing", missing, "部分工况节点无单独压力约束。"))
        if outside:
            entry["issues"].append(issue("nomination_outside_flow_bounds", outside, "工况流量超出网络节点声明的范围。"))
        entry["issues"].append(issue("pressure_basis_unmapped", None, "网络 bar 与部分工况 barg 保持各自原单位。物理推演未开放。"))
    elif identity.startswith("EU-SCIGRID-"):
        raw_segments = profile["segments_raw"]
        entry.update(version="1.0.0" if identity.endswith("IGGIN") else "1.2.0",
                     source_kind="research_repository", data_kind="historical_network", geo_scope="欧洲历史输气研究",
                     coordinate_system="RFC7946 lon/lat", synthetic_flag=False, license_status="CC-BY-4.0",
                     usage_scope="research_gis", quality_status="partial" if quarantine_sha else "checked",
                     record_counts={"nodes": profile["nodes"], "segments_raw": raw_segments,
                                    "segments_accepted": profile["segments_accepted"], "quarantine": profile["segments_quarantined"]},
                     facts=["仅提供欧洲历史数据的层和结构检查摘要。"])
        if quarantine_sha:
            entry["issues"] = [issue(code, count, "关联的原记录已保存到隔离区。") for code, count in profile["reason_counts"].items()]
            entry["transformations"].append("异常管段按源记录隔离，接收计数排除该记录。")
    elif identity == "CN-CQ-STATS-2025":
        entry.update(version="2025", source_kind="government", data_kind="public_statistics", geo_scope="重庆城市年度统计",
                     synthetic_flag=False, license_status="public_access_terms_unconfirmed", usage_scope="annual_context",
                     ingest_status="metadata_projection", quality_status="partial", statistics_time="2025 calendar year",
                     record_counts={"retained_source_rows": len(profile["rows"]), "workbook_sheets": profile["book_sheets"]},
                     facts=[f"{row['sheet']} 第 {row['row']} 行为 {row['raw_label'].strip()}，原统计层级 {row['level']}。"
                            for row in profile["rows"]])
        entry["issues"].append(issue("aggregation_unavailable", len(profile["rows"]), "保留出版表的层级和原行号，汇总能力暂未开放。"))
        entry["transformations"].append("保留表号、原始行标签、层级和年度。浏览端只读表级口径及行出处。")
    return entry


def phase_one_comparison(previous: dict, k: dict, g: dict, iggin: dict, emap: dict, xls: dict) -> dict:
    pairs = [
        ("K-001 observations", previous["OWN-K001"]["replay"]["observation_count"], k["table_counts"]["observations"]),
        ("K-001 asset IDs", previous["OWN-K001"]["asset_id_union_nodes_pipes_valves_consumers"], k["table_counts"]["assets"]),
        ("GasLib nodes", [item["node_count"] for item in previous["DE-GASLIB"]["instances"]],
         [item["node_count"] for item in g["instances"]]),
        ("IGGIN segments", previous["EU-SCIGRID-IGGIN"]["pipe_segments"]["csv_rows"], iggin["segments_raw"]),
        ("EMAP segments", previous["EU-SCIGRID-EMAP"]["pipe_segments"]["csv_rows"], emap["segments_raw"]),
    ]
    for table in ("5-2", "5-2-1"):
        pairs.append((table, previous["CN-CQ-STATS-2025"]["checked_tables"][table]["chongqing_row_numbers_1_based"],
                      [row["row"] for row in xls["rows"] if row["sheet"] == table]))
    return {"compared": [{"check": name, "phase_one": old, "recheck": new, "matches": old == new} for name, old, new in pairs]}


def build(source: Path, candidate_file: Path, output: Path) -> None:
    inventory = inspect_inventory(source)
    evidence = source / "02_核验证据"
    git_reference = json.loads((evidence / "K-001与输入指纹.json").read_bytes())
    catalog_payload = candidate_file.read_bytes()
    insist(digest(catalog_payload) == git_reference["inputs"][0]["sha256"], "catalog differs from phase-one attachment")
    candidates = json.loads(catalog_payload.decode("utf-8-sig"))["dataset_candidates"]
    expect_ids = re.findall(r"^\| \x60([A-Z][A-Z0-9-]+)\x60", (source / "数据集验收矩阵_20261120.md").read_text(encoding="utf-8"), re.M)
    insist(len(expect_ids) == len(candidates) == len({item["id"] for item in candidates}) and set(expect_ids) == {item["id"] for item in candidates}, "phase-one catalog coverage")
    previous = json.loads((evidence / "字段解析回执.json").read_bytes())
    raw = source / "01_原始文件"
    k, fixture = k001(raw / "OWN-K001")
    g = gaslib(raw / "DE-GASLIB")
    iggin, iggin_bad = scigrid(raw / "EU-SCIGRID-IGGIN/IGGIN.zip", "IGGIN", "9e7dfa0a41110feacb401110588cee42")
    emap, emap_bad = scigrid(raw / "EU-SCIGRID-EMAP/EMAP_raw.zip", "EMAP", "fdadcb8a69ed510f15090964350c3764")
    xls_path, = (raw / "CN-CQ-STATS-2025").glob("*.xls")
    xls = yearbook(xls_path, evidence / "解析器/xlrd-2.0.2-py2.py3-none-any.whl")
    profiles = {"OWN-K001": k, "DE-GASLIB": g, "EU-SCIGRID-IGGIN": iggin, "EU-SCIGRID-EMAP": emap, "CN-CQ-STATS-2025": xls}
    quarantine = {"EU-SCIGRID-IGGIN": iggin_bad, "EU-SCIGRID-EMAP": emap_bad}
    insists = phase_one_comparison(previous, k, g, iggin, emap, xls)
    insist(all(row["matches"] for row in insists["compared"]), "prior phase count drift")
    insist(not output.exists(), "output already exists; use a new receipt directory")
    insist(all(not (path / ".git").exists() for path in (output, *output.parents)), "source data output must be outside a Git worktree")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix="gas-data-m2-build-") as work:
        stage = Path(work) / "data"
        for sub in ("raw", "normalized", "quarantine", "fixtures", "receipts"):
            (stage / sub).mkdir(parents=True)

        def write(name: str, data: bytes) -> str:
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as sink:
                sink.write(data)
            return digest(data)

        originals = {}
        for relative, source_sha in sorted(inventory["raw_verified"].items()):
            fragment = relative.split("/", 1)[1]
            target = "raw/" + fragment
            actual = write(target, (source / relative).read_bytes())
            insist(actual == source_sha, f"raw changed during copy: {relative}")
            identity = fragment.split("/", 1)[0]
            originals.setdefault(identity, []).append({"name": fragment.split("/", 1)[1], "sha256": actual})
        field_fixture_sha = write("fixtures/k001-replay.json", canonical(fixture))
        entries = []
        for candidate in candidates:
            entry = base_entry(candidate)
            identity = entry["dataset_id"]
            if identity in profiles:
                profile = profiles[identity]
                payload = {"dataset_id": identity, "schema_version": VERSION, "profile": profile}
                if identity == "OWN-K001":
                    payload["fixture_sha256"] = field_fixture_sha
                normal_sha = write(f"normalized/{identity}.json", canonical(payload))
                defects = quarantine.get(identity)
                quarantine_sha = write(f"quarantine/{identity}.json", canonical(defects)) if defects else None
                entry = apply_profile(entry, profile, originals[identity], normal_sha, quarantine_sha, git_reference["commit"])
            entries.append(entry)
        registry_sha = write("normalized/registry.json", canonical({"schema_version": VERSION, "datasets": entries}))
        review = {"source_commit": git_reference["commit"], "source_tree": git_reference["tree"],
                  "phase_one_inventory": inventory, "counts_comparison": insists,
                  "registry_sha256": registry_sha, "profiles": list(profiles)}
        write("receipts/independent-review.json", canonical(review))
        rows = ["# SHA-256. Paths relative to this isolated data package."]
        rows.extend(f"{digest(path.read_bytes())}  {path.relative_to(stage).as_posix()}"
                    for path in sorted(stage.rglob("*")) if path.is_file())
        write("receipts/SHA256SUMS.txt", ("\n".join(rows) + "\n").encode())
        stage.rename(output)
    for path in (output / "raw").rglob("*"):
        if path.is_file():
            path.chmod(0o444)
    print(json.dumps({"input_files_verified": len(inventory["all_verified"]), "raw_files": len(inventory["raw_verified"]),
                      "registry_entries": len(entries), "registry_sha256": registry_sha,
                      "phase_one_comparison_passed": len(insists["compared"])}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase-one", required=True, type=Path)
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    build(args.phase_one.resolve(), args.catalog.resolve(), args.output.resolve())


if __name__ == "__main__":
    main()
