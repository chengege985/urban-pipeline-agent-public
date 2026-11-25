"""Read-only data registry candidate. Runtime views expose audited projections only."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import stat
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path as ApiPath, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import Principal, authorize_dataset, resolve_principal

from .contracts import GAS_DATASET_ID
from .model_provider import MODEL_ID, LocalGasModel, ModelUnavailable
from .service import GasFixtureService


M2_VERSION = "gas-data-m2/1"
DEMO_ID = "OWN-K001"
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
DataId = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9-]{1,63}$")]
RecordKind = Literal["assets", "events", "topology", "observations"]
ToolSection = Literal["provenance", "quality", "assets", "events"]
AssetKind = Literal["pipe", "node", "valve", "consumer"]
LIMITATION = "正式健康评价、运行风险和物理预测均为 unavailable。"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GasM2Source(StrictModel):
    name: str
    sha256: Digest


class GasM2Field(StrictModel):
    raw_field: str
    normalized_field: str
    unit: str | None


class GasM2Issue(StrictModel):
    code: str
    count: int | None = Field(ge=0)
    detail: str


class GasM2Dataset(StrictModel):
    dataset_id: DataId
    title: str
    source_uri: str | None
    version: str | None
    sha256: Digest | None
    source_kind: Literal["project_fixture", "government", "public_portal", "research_repository",
                         "experiment_repository", "map_provider", "authorized_operator"]
    data_kind: Literal["internal_synthetic", "synthetic_research_benchmark", "historical_network",
                       "public_statistics", "official_incidents", "experimental_measurement", "observational_monitoring",
                       "experiment_and_simulation", "simulation", "knowledge_collection", "administrative_service",
                       "unverified_catalog", "real_authorized"]
    geo_scope: str
    coordinate_system: str | None
    statistics_time: str | None
    schema_status: Literal["unverified", "documented", "verified"]
    quality_status: Literal["unavailable", "partial", "checked"]
    license_status: str
    synthetic_flag: bool | None
    usage_scope: Literal["catalog_only", "synthetic_demo", "research_benchmark", "research_gis", "annual_context",
                         "administrative_reference"]
    ingest_status: Literal["catalog_only", "candidate_ready", "metadata_projection", "authorization_pending",
                           "download_blocked", "service_only"]
    sources: list[GasM2Source]
    field_mappings: list[GasM2Field]
    transformations: list[str]
    normalized_sha256: Digest | None
    quarantine_sha256: Digest | None
    record_counts: dict[str, Annotated[int, Field(ge=0)]]
    issues: list[GasM2Issue]
    facts: list[str]


class GasM2Registry(StrictModel):
    schema_version: Literal["gas-data-m2/1"]
    datasets: list[GasM2Dataset] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def check_candidate_scope(self):
        ids = {entry.dataset_id for entry in self.datasets}
        if len(ids) != len(self.datasets) or DEMO_ID not in ids:
            raise ValueError("invalid candidate catalog identities")
        for item in self.datasets:
            if item.data_kind == "real_authorized" or item.source_kind == "authorized_operator":
                raise ValueError("candidate catalog excludes operator data")
            if (item.usage_scope == "synthetic_demo") != (item.dataset_id == DEMO_ID):
                raise ValueError("invalid candidate asset scope")
            if item.dataset_id == DEMO_ID and (
                item.synthetic_flag is not True or item.data_kind != "internal_synthetic"
                or item.coordinate_system != "schematic" or item.ingest_status != "candidate_ready"
                or item.version is None or item.sha256 is None
            ):
                raise ValueError("invalid K-001 fixture identity")
            if item.source_uri and not item.source_uri.startswith("https://"):
                raise ValueError("candidate source URI must use HTTPS")
        return self


class GasM2Binding(StrictModel):
    dataset_id: DataId
    version: str | None = Field(max_length=128)
    registry_sha256: Digest


class GasM2Capabilities(StrictModel):
    health_assessment: Literal["unavailable"] = "unavailable"
    operating_risk: Literal["unavailable"] = "unavailable"
    physical_prediction: Literal["unavailable"] = "unavailable"


class GasM2Catalog(StrictModel):
    schema_version: Literal["gas-data-m2/1"]
    registry_sha256: Digest
    datasets: list[GasM2Dataset]
    capabilities: GasM2Capabilities = Field(default_factory=GasM2Capabilities)


class GasM2Details(StrictModel):
    binding: GasM2Binding
    dataset: GasM2Dataset
    capabilities: GasM2Capabilities = Field(default_factory=GasM2Capabilities)


class GasM2RecordPage(StrictModel):
    binding: GasM2Binding
    kind: RecordKind
    source_uri: str | None
    total: int
    items: list[dict[str, object]]
    coordinate_system: Literal["schematic"] = "schematic"
    data_kind: Literal["internal_synthetic"] = "internal_synthetic"
    capabilities: GasM2Capabilities = Field(default_factory=GasM2Capabilities)


class GasM2AgentRequest(StrictModel):
    binding: GasM2Binding
    section: ToolSection
    record_id: str | None = Field(default=None, min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=600, pattern=r"\S")

    @model_validator(mode="after")
    def require_selected_record(self):
        if (self.section in {"assets", "events"}) != (self.record_id is not None):
            raise ValueError("asset and event queries require a record ID")
        return self


class GasM2AgentResult(StrictModel):
    request_id: str
    binding: GasM2Binding
    tool_name: Literal["data_m2.provenance", "data_m2.quality", "data_m2.assets", "data_m2.events"]
    evidence_refs: list[str]
    facts: list[str]
    answer: str
    explanation_source: Literal["backend_facts", "qwen_evidence_selection"] = "backend_facts"
    model_status: Literal["unavailable", "ready"] = "unavailable"
    model_id: str | None = None
    model_calls: int = 0
    model_usage: dict[str, int] = Field(default_factory=dict)
    elapsed_ms: float = 0
    model_error: str | None = None
    capabilities: GasM2Capabilities = Field(default_factory=GasM2Capabilities)


def load_catalog() -> GasM2Catalog:
    path_value = os.getenv("GAS_DATA_M2_REGISTRY_FILE")
    expected = os.getenv("GAS_DATA_M2_REGISTRY_SHA256")
    local_modes = {"local", "development", "controlled"}
    if not path_value or not expected or os.getenv("WATER_RUNTIME_MODE") not in local_modes:
        raise HTTPException(503, "gas_m2_candidate_unavailable")
    try:
        path = Path(path_value)
        info = path.lstat()
        if not path.is_absolute() or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 512_000:
            raise ValueError("invalid registry path")
        with path.open("rb") as stream:
            contents = stream.read(512_001)
        actual = hashlib.sha256(contents).hexdigest()
        if len(contents) > 512_000 or actual != expected:
            raise ValueError("registry digest mismatch")
        registry = GasM2Registry.model_validate_json(contents)
    except (OSError, ValueError, ValidationError):
        raise HTTPException(503, "gas_m2_registry_invalid") from None
    return GasM2Catalog(schema_version=registry.schema_version, registry_sha256=actual, datasets=registry.datasets)


def details(catalog: GasM2Catalog, dataset_id: str) -> GasM2Details:
    item = next((item for item in catalog.datasets if item.dataset_id == dataset_id), None)
    if item is None:
        raise HTTPException(404, "gas_m2_dataset_not_found")
    binding = GasM2Binding(dataset_id=item.dataset_id, version=item.version, registry_sha256=catalog.registry_sha256)
    return GasM2Details(binding=binding, dataset=item)


_ASSET_FIELDS = {
    "pipe": ("pipes", "pipe_id", ("material", "diameter_mm", "pressure_class", "asset_status", "edge_id")),
    "node": ("nodes", "node_id", ("name", "node_type", "layout")),
    "valve": ("valves", "valve_id", ("node_id", "incident_edge_ids", "initial_state", "cut_semantics")),
    "consumer": ("consumers", "consumer_id", ("node_id", "consumer_kind", "count", "count_unit")),
}


def asset_record_rows(network: dict, asset_type: AssetKind | None = None) -> list[dict[str, object]]:
    return [
        {"record_id": row[key], "asset_type": role, "district_id": None,
         **{field: row.get(field) for field in selected}}
        for role, (table, key, selected) in _ASSET_FIELDS.items() if asset_type is None or role == asset_type
        for row in network[table]
    ]


def records(
    entry: GasM2Details, service: GasFixtureService, kind: RecordKind, *, asset_type: AssetKind | None = None,
) -> list[dict[str, object]]:
    if entry.dataset.dataset_id != DEMO_ID:
        raise HTTPException(409, "gas_m2_projection_only")
    source_hashes = {item.name: item.sha256 for item in entry.dataset.sources}
    network, case, package, events = service.m2_fixture(source_hashes)
    if kind == "assets":
        return asset_record_rows(network, asset_type)
    if kind == "events":
        elapsed = {event["event_id"]: event["time_hours"] for event in case["events"]}
        return [{"record_id": event.event_id, "time_hours": elapsed[event.event_id],
                 **event.model_dump(mode="json")} for event in events]
    if kind == "topology":
        nodes = [{"record_id": node["node_id"], "record_type": "node", "node_type": node["node_type"],
                  "layout": node["layout"]} for node in network["nodes"]]
        edges = [{"record_id": edge["edge_id"], "record_type": "edge", "pipe_id": edge["pipe_id"],
                  "from_node_id": edge["from_node_id"], "to_node_id": edge["to_node_id"]} for edge in network["edges"]]
        return nodes + edges
    return [{"record_id": row.provenance.source_record_id, "asset_id": row.asset_id, "sensor_id": row.sensor_id,
             "observed_at": row.observed_at.isoformat(), "metric": row.metric, "value": row.value,
             "unit": row.unit, "quality": row.quality, "source_row": row.provenance.row,
             "source_sha256": row.provenance.source.sha256} for row in package.rows.monitoring]


def scoped_facts(entry: GasM2Details, payload: GasM2AgentRequest, service: GasFixtureService) -> tuple[list[str], list[str]]:
    dataset = entry.dataset
    facts = [f"{dataset.title}（{dataset.dataset_id}），版本 {dataset.version or '未锁定'}，适用范围 {dataset.geo_scope}。"]
    refs = [f"gas-m2-registry:sha256:{entry.binding.registry_sha256}:{dataset.dataset_id}"]
    if dataset.normalized_sha256:
        refs.append(f"gas-m2-normalized:sha256:{dataset.normalized_sha256}")
    if payload.section == "provenance":
        facts += [f"来源 {dataset.source_uri or '未提供'}，数据身份 {dataset.data_kind}，许可状态 {dataset.license_status}。",
                  f"原件摘要 {dataset.sha256 or '未取得'}。"]
        facts.extend(dataset.facts)
    elif payload.section == "quality":
        facts.append(f"字段状态 {dataset.schema_status}，质量状态 {dataset.quality_status}。")
        facts.extend(f"{row.code}。{row.detail}" + (f" 记录数 {row.count}。" if row.count is not None else "")
                     for row in dataset.issues)
        if not dataset.issues:
            facts.append("当前已检查范围未登记异常。")
        if dataset.quarantine_sha256:
            refs.append(f"gas-m2-quarantine:sha256:{dataset.quarantine_sha256}")
    else:
        kind: RecordKind = "assets" if payload.section == "assets" else "events"
        row = next((row for row in records(entry, service, kind) if row["record_id"] == payload.record_id), None)
        if row is None:
            raise HTTPException(404, "gas_m2_record_not_found")
        refs.append(f"gas-m2-record:{dataset.dataset_id}:{payload.record_id}")
        if kind == "assets":
            facts.append(f"已校验的合成资产字段 {json.dumps(row, ensure_ascii=False, sort_keys=True)}。")
        else:
            facts.append(f"事件 {row['event_id']}，类型 {row['event_kind']}，案例相对时间 {row['time_hours']} 小时。")
            facts.append(f"快照关联资产 {json.dumps(row['asset_ids'], ensure_ascii=False)}。")
    facts.append(LIMITATION)
    return facts, refs


def agent_query(entry: GasM2Details, payload: GasM2AgentRequest, service: GasFixtureService) -> GasM2AgentResult:
    start = monotonic()
    facts, refs = scoped_facts(entry, payload, service)
    result = GasM2AgentResult(request_id="gas-m2-" + secrets.token_hex(8), binding=entry.binding,
                              tool_name=f"data_m2.{payload.section}", evidence_refs=refs, facts=facts,
                              answer="\n".join(facts))
    provider = None
    try:
        provider = LocalGasModel.from_environment()
        if provider is not None:
            allowed_facts = set(facts)
            selected = provider.explain(facts.copy(), question=payload.question)
            selected_lines = selected.splitlines() if isinstance(selected, str) else []
            if not selected_lines or any(line not in allowed_facts for line in selected_lines):
                raise ModelUnavailable("model_evidence_whitelist_violation")
            result.answer = "\n".join(dict.fromkeys((facts[0], *selected_lines, LIMITATION)))
            result.explanation_source = "qwen_evidence_selection"
            result.model_status = "ready"
            result.model_id = MODEL_ID
    except ModelUnavailable as exc:
        result.model_error = str(exc)
    if provider is not None:
        result.model_calls = provider.calls
        result.model_usage = provider.usage
    result.elapsed_ms = round((monotonic() - start) * 1000, 2)
    logging.getLogger("gas.agent").info(json.dumps({
        "kind": "gas_m2_read_tool", "request_id": result.request_id, "tool": result.tool_name,
        "binding": result.binding.model_dump(), "evidence_refs": result.evidence_refs,
        "model_status": result.model_status, "model_id": result.model_id, "model_calls": result.model_calls,
        "explanation_source": result.explanation_source, "model_usage": result.model_usage,
        "model_error": result.model_error, "elapsed_ms": result.elapsed_ms,
    }, ensure_ascii=False))
    return result


def require_internal_m2_access(request: Request, response: Response) -> Principal:
    principal = resolve_principal(request)
    if not principal.authenticated:
        raise HTTPException(401, "未完成服务端身份认证")
    if principal.scope != "internal":
        raise HTTPException(403, "gas_m2_access_denied")
    authorize_dataset(principal, GAS_DATASET_ID, "synthetic")
    response.headers["Cache-Control"] = "no-store"
    return principal


def gas_m2_router(service: GasFixtureService) -> APIRouter:
    router = APIRouter(prefix="/data-m2", tags=["gas-data-m2"])
    secured = {"openapi_extra": {"security": [{"WaterToken": []}]}, "responses": COMMON_ERRORS}

    def access(request: Request, response: Response) -> GasM2Catalog:
        require_internal_m2_access(request, response)
        return load_catalog()

    @router.get("/datasets", response_model=GasM2Catalog, **secured)
    def catalog(request: Request, response: Response):
        return access(request, response)

    @router.get("/datasets/{dataset_id}", response_model=GasM2Details, **secured)
    def dataset(dataset_id: Annotated[str, ApiPath(pattern=r"^[A-Z][A-Z0-9-]{1,63}$")], request: Request, response: Response):
        return details(access(request, response), dataset_id)

    @router.get("/datasets/{dataset_id}/records/{kind}", response_model=GasM2RecordPage, **secured)
    def list_records(
        dataset_id: Annotated[str, ApiPath(pattern=r"^[A-Z][A-Z0-9-]{1,63}$")], kind: RecordKind, request: Request, response: Response,
        registry_sha256: Digest, version: str = Query(max_length=128), record_id: str | None = Query(default=None, max_length=128),
        asset_type: AssetKind | None = None, limit: int = Query(default=200, ge=1, le=250),
        offset: int = Query(default=0, ge=0),
    ):
        current = details(access(request, response), dataset_id)
        if (registry_sha256, version) != (current.binding.registry_sha256, current.binding.version):
            raise HTTPException(409, "gas_m2_version_conflict")
        if asset_type is not None and kind != "assets":
            raise HTTPException(422, "gas_m2_asset_filter_scope")
        filtered = records(current, service, kind, asset_type=asset_type)
        if record_id is not None:
            filtered = [row for row in filtered if row["record_id"] == record_id]
            if not filtered:
                raise HTTPException(404, "gas_m2_record_not_found")
        return GasM2RecordPage(binding=current.binding, kind=kind, source_uri=current.dataset.source_uri,
                               total=len(filtered), items=filtered[offset:offset + limit])

    @router.post("/agent/query", response_model=GasM2AgentResult, **secured)
    def query(payload: GasM2AgentRequest, request: Request, response: Response):
        current = details(access(request, response), payload.binding.dataset_id)
        if current.binding != payload.binding:
            raise HTTPException(409, "gas_m2_version_conflict")
        result = agent_query(current, payload, service)
        response.headers["X-Request-ID"] = result.request_id
        return result

    return router
