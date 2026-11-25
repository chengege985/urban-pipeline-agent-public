
"""Read-only GasLib XML quality adapter."""
from __future__ import annotations
import hashlib, json, math, os, zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Literal
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import Principal, resolve_principal

GASLIB_DATASET_ID = "DE-GASLIB"
PUBLIC_CORPUS_ID = "gas-public-20261124"
PUBLIC_DATA_ROOT_ENV = "GAS_PUBLIC_DATA_ROOT"
RECEIPT_NAME = "data_acceptance_20261124.json"

class GasLibContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

class GasLibInstanceSummary(GasLibContract):
    instance_id: str
    archive_path: str
    archive_sha256: str
    archive_bytes: int
    member_count: int
    network_members: int
    compressor_members: int
    scenario_members: int
    cdf_members: int
    svg_members: int

class GasLibQualityResponse(GasLibContract):
    dataset_id: Literal["DE-GASLIB"] = GASLIB_DATASET_ID
    instance_id: str
    version: str
    status: Literal["checked", "partial", "unavailable", "not_found"]
    archive_path: str | None = None
    archive_sha256: str | None = None
    member_hashes: dict[str, str] = Field(default_factory=dict)
    namespace_uris: list[str] = Field(default_factory=list)
    schema_locations: list[str] = Field(default_factory=list)
    node_count: int = 0
    source_count: int = 0
    sink_count: int = 0
    innode_count: int = 0
    exit_count: int = 0
    connection_count: int = 0
    pipe_count: int = 0
    valve_count: int = 0
    compressor_station_count: int = 0
    scenario_file_count: int = 0
    scenarios_checked: int = 0
    scenario_boundary_count: int = 0
    normalized_nodes: list[dict[str, object]] = Field(default_factory=list)
    normalized_connections: list[dict[str, object]] = Field(default_factory=list)
    normalized_scenarios: list[dict[str, object]] = Field(default_factory=list)
    quality_issues: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]

def sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()

def sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

def number(value: str | None) -> float | None:
    if value is None or not value.strip():
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if math.isfinite(result) else None

def schema_locations(root: ET.Element) -> list[str]:
    values: list[str] = []
    for key, value in root.attrib.items():
        if key.endswith("schemaLocation") and value:
            values.extend(value.split())
    return values

class GasLibService:
    def __init__(self, root: str | Path | None = None):
        configured = root if root is not None else os.getenv(PUBLIC_DATA_ROOT_ENV, "")
        self.root = Path(configured).expanduser().resolve() if configured else None

    def _authorize(self, principal: Principal) -> None:
        if not principal.authenticated:
            raise HTTPException(401, "未完成服务端身份认证")
        if principal.scope != "internal" or PUBLIC_CORPUS_ID not in principal.corpora:
            raise HTTPException(403, "当前主体无权读取燃气公共研究数据")

    def _required_root(self) -> Path:
        if self.root is None:
            raise RuntimeError("GAS_PUBLIC_DATA_ROOT未配置")
        return self.root

    def _receipt(self) -> dict[str, object]:
        return json.loads((self._required_root() / "receipts" / RECEIPT_NAME).read_text(encoding="utf-8"))

    def _instances(self) -> dict[str, GasLibInstanceSummary]:
        root = self._required_root()
        rows = self._receipt().get("files", [])
        out: dict[str, GasLibInstanceSummary] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            relative = str(row.get("path", ""))
            if not relative.startswith("raw/DE-GASLIB/") or not relative.endswith(".zip"):
                continue
            if relative.endswith("/schemaDefinition.zip"):
                continue
            path = root / relative
            expected = str(row.get("sha256", ""))
            if not path.is_file() or sha_file(path) != expected:
                continue
            with zipfile.ZipFile(path) as archive:
                names = [name for name in archive.namelist() if not name.endswith("/")]
            instance_id = Path(relative).stem
            out[instance_id] = GasLibInstanceSummary(
                instance_id=instance_id, archive_path=relative, archive_sha256=expected,
                archive_bytes=path.stat().st_size, member_count=len(names),
                network_members=sum(name.lower().endswith(".net") for name in names),
                compressor_members=sum(name.lower().endswith(".cs") for name in names),
                scenario_members=sum(name.lower().endswith(".scn") for name in names),
                cdf_members=sum(name.lower().endswith(".cdf") for name in names),
                svg_members=sum(name.lower().endswith(".svg") for name in names),
            )
        return out

    def catalog(self, principal: Principal) -> list[GasLibInstanceSummary]:
        self._authorize(principal)
        return list(self._instances().values())

    def quality(self, principal: Principal, instance_id: str, *, scenario_limit: int = 2000) -> GasLibQualityResponse:
        self._authorize(principal)
        summary = self._instances().get(instance_id)
        if summary is None:
            return GasLibQualityResponse(instance_id=instance_id, version="unknown", status="not_found",
                limitations=["指定 GasLib 原件不存在、未登记或哈希校验失败。"])
        root = self._required_root()
        issues: list[str] = []
        evidence = [f"gaslib:archive:{summary.archive_path}:sha256:{summary.archive_sha256}"]
        member_hashes: dict[str, str] = {}
        namespaces: set[str] = set()
        locations: list[str] = []
        nodes: list[dict[str, object]] = []
        connections: list[dict[str, object]] = []
        scenarios: list[dict[str, object]] = []
        node_types: dict[str, str] = {}
        node_ids: set[str] = set()
        scenario_files: list[str] = []
        scenario_boundary_count = 0
        checked = 0
        try:
            with zipfile.ZipFile(root / summary.archive_path) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("ZIP CRC校验失败")
                names = [name for name in archive.namelist() if not name.endswith("/")]
                net_names = [name for name in names if name.lower().endswith(".net")]
                cs_names = [name for name in names if name.lower().endswith(".cs")]
                scenario_files = [name for name in names if name.lower().endswith(".scn")]
                if len(net_names) != 1:
                    issues.append(f"network_file_count={len(net_names)}")
                if len(cs_names) > 1:
                    issues.append(f"compressor_file_count={len(cs_names)}")
                if not net_names:
                    raise RuntimeError("缺少唯一 .net 网络文件")
                net_name = net_names[0]
                net_bytes = archive.read(net_name)
                net_root = ET.fromstring(net_bytes)
                member_hashes[net_name] = sha_bytes(net_bytes)
                evidence.append(f"gaslib:member:{net_name}:sha256:{member_hashes[net_name]}")
                for element in net_root.iter():
                    tag = str(element.tag)
                    if tag.startswith("{"):
                        namespaces.add(tag.split("}", 1)[0][1:])
                locations.extend(schema_locations(net_root))
                kind_map = {"source": "source", "sink": "sink", "innode": "innode", "exit": "exit"}
                duplicate: Counter[str] = Counter()
                for element in net_root.iter():
                    kind = local(element.tag)
                    if kind in kind_map and element.attrib.get("id"):
                        identifier = element.attrib["id"]
                        duplicate[identifier] += 1
                        node_ids.add(identifier)
                        node_types[identifier] = kind_map[kind]
                        item: dict[str, object] = {"id": identifier, "type": kind_map[kind]}
                        for key in ("x", "y", "geoWGS84Lat", "geoWGS84Long"):
                            if key in element.attrib:
                                value = number(element.attrib[key])
                                if value is None:
                                    issues.append(f"node:{identifier}:invalid_{key}")
                                else:
                                    item[key] = value
                        nodes.append(item)
                issues.extend(f"duplicate_node_id:{key}" for key, count in duplicate.items() if count > 1)
                for element in net_root.iter():
                    kind = local(element.tag)
                    if kind not in {"pipe", "valve", "compressorStation"}:
                        continue
                    identifier, from_id, to_id = element.attrib.get("id"), element.attrib.get("from"), element.attrib.get("to")
                    if not identifier or not from_id or not to_id:
                        issues.append(f"{kind}:missing_identity_or_endpoint")
                        continue
                    if from_id not in node_ids:
                        issues.append(f"{kind}:{identifier}:missing_from:{from_id}")
                    if to_id not in node_ids:
                        issues.append(f"{kind}:{identifier}:missing_to:{to_id}")
                    item: dict[str, object] = {"id": identifier, "type": kind, "from": from_id, "to": to_id}
                    for key in ("length", "diameter", "roughness", "pressure", "flow"):
                        child = next((child for child in element if local(child.tag) == key), None)
                        if child is None:
                            continue
                        value = number(child.attrib.get("value") or child.text)
                        if value is None:
                            issues.append(f"{kind}:{identifier}:invalid_{key}")
                        else:
                            item[key] = value
                        if child.attrib.get("unit"):
                            item[f"{key}_unit"] = child.attrib["unit"]
                    connections.append(item)
                for cs_name in cs_names:
                    cs_bytes = archive.read(cs_name)
                    cs_root = ET.fromstring(cs_bytes)
                    member_hashes[cs_name] = sha_bytes(cs_bytes)
                    evidence.append(f"gaslib:member:{cs_name}:sha256:{member_hashes[cs_name]}")
                    locations.extend(schema_locations(cs_root))
                    cs_ids = {element.attrib.get("id") for element in cs_root.iter() if local(element.tag) == "compressorStation"}
                    net_ids = {str(item["id"]) for item in connections if item["type"] == "compressorStation"}
                    issues.extend(f"compressor_station_missing_in_cs:{key}" for key in sorted(net_ids - cs_ids))
                    issues.extend(f"compressor_station_not_in_net:{key}" for key in sorted(cs_ids - net_ids))
                for scenario_name in scenario_files[:scenario_limit]:
                    checked += 1
                    raw = archive.read(scenario_name)
                    member_hashes[scenario_name] = sha_bytes(raw)
                    evidence.append(f"gaslib:member:{scenario_name}:sha256:{member_hashes[scenario_name]}")
                    try:
                        scenario_root = ET.fromstring(raw)
                    except ET.ParseError:
                        issues.append(f"scenario:{scenario_name}:xml_parse_error")
                        continue
                    scenario_id = next((element.attrib.get("id") for element in scenario_root.iter() if local(element.tag) == "scenario"), Path(scenario_name).stem)
                    scenario_nodes: list[dict[str, object]] = []
                    for element in scenario_root.iter():
                        if local(element.tag) != "node":
                            continue
                        node_id, node_type = element.attrib.get("id"), element.attrib.get("type")
                        if not node_id or node_id not in node_ids:
                            issues.append(f"scenario:{scenario_name}:unknown_node:{node_id or 'missing'}")
                            continue
                        expected = node_types[node_id]
                        compatible = node_type == expected or (node_type == "entry" and expected == "source") or (node_type == "exit" and expected == "sink")
                        if not compatible:
                            issues.append(f"scenario:{scenario_name}:node_type_mismatch:{node_id}:{node_type}:{expected}")
                        boundaries: list[dict[str, object]] = []
                        for boundary in element:
                            boundary_type = local(boundary.tag)
                            if boundary_type not in {"pressure", "flow"}:
                                continue
                            value = number(boundary.attrib.get("value") or boundary.text)
                            unit = boundary.attrib.get("unit")
                            if value is None:
                                issues.append(f"scenario:{scenario_name}:{node_id}:invalid_{boundary_type}")
                            if not unit:
                                issues.append(f"scenario:{scenario_name}:{node_id}:missing_{boundary_type}_unit")
                            boundaries.append({"kind": boundary_type, "value": value, "unit": unit, "bound": boundary.attrib.get("bound")})
                            scenario_boundary_count += 1
                        scenario_nodes.append({"node_id": node_id, "type": node_type, "boundaries": boundaries})
                    scenarios.append({"scenario_id": scenario_id, "file": scenario_name, "nodes": scenario_nodes})
        except (OSError, ValueError, ET.ParseError, zipfile.BadZipFile, RuntimeError, KeyError) as exc:
            return GasLibQualityResponse(instance_id=instance_id, version="unknown", status="unavailable",
                archive_path=summary.archive_path, archive_sha256=summary.archive_sha256,
                quality_issues=[type(exc).__name__], evidence_refs=evidence,
                limitations=["原始 ZIP 或 XML 无法通过接收回执、CRC 或解析校验。"])
        if len(scenario_files) > scenario_limit:
            issues.append(f"scenario_check_limited:{scenario_limit}/{len(scenario_files)}")
        status: Literal["checked", "partial", "unavailable"] = "checked" if not issues else "partial"
        return GasLibQualityResponse(
            instance_id=instance_id, version=instance_id.split("-")[-1] if "-" in instance_id else "unknown",
            status=status, archive_path=summary.archive_path, archive_sha256=summary.archive_sha256,
            member_hashes=member_hashes, namespace_uris=sorted(namespaces), schema_locations=list(dict.fromkeys(locations)),
            node_count=len(node_ids), source_count=sum(value == "source" for value in node_types.values()),
            sink_count=sum(value == "sink" for value in node_types.values()), innode_count=sum(value == "innode" for value in node_types.values()),
            exit_count=sum(value == "exit" for value in node_types.values()), connection_count=len(connections),
            pipe_count=sum(item["type"] == "pipe" for item in connections), valve_count=sum(item["type"] == "valve" for item in connections),
            compressor_station_count=sum(item["type"] == "compressorStation" for item in connections), scenario_file_count=len(scenario_files),
            scenarios_checked=checked, scenario_boundary_count=scenario_boundary_count, normalized_nodes=nodes,
            normalized_connections=connections, normalized_scenarios=scenarios, quality_issues=issues,
            evidence_refs=list(dict.fromkeys(evidence)),
            limitations=[
                "GasLib 是公开研究基准，不代表重庆实际管网。",
                "规范化结果只用于拓扑、边界和求解器研究。",
                "大型实例默认最多检查前 2000 个场景文件。",
            ],
        )

def gaslib_router(service: GasLibService | None = None) -> APIRouter:
    gaslib = service or GasLibService()
    router = APIRouter(prefix="/gaslib", tags=["gas-public-gaslib"])
    security = {"openapi_extra": {"security": [{"WaterToken": []}]}, "responses": COMMON_ERRORS}
    @router.get("/instances", response_model=list[GasLibInstanceSummary], **security)
    def instances(request: Request):
        return gaslib.catalog(resolve_principal(request))
    @router.get("/instances/{instance_id}/quality", response_model=GasLibQualityResponse, **security)
    def quality(instance_id: str, request: Request, scenario_limit: int = Query(default=2000, ge=1, le=5000)):
        return gaslib.quality(resolve_principal(request), instance_id, scenario_limit=scenario_limit)
    return router

__all__ = ["GASLIB_DATASET_ID", "GasLibInstanceSummary", "GasLibQualityResponse", "GasLibService", "gaslib_router"]
