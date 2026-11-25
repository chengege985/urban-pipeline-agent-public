"""Versioned navigation and quality evidence for the M2 registry."""

from __future__ import annotations

import json
import re
from collections import Counter
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field, model_validator

from src.platform.api_models import COMMON_ERRORS

from .data_m2 import (
    DEMO_ID, Digest, GasM2Binding, GasM2Details, GasM2Source, StrictModel,
    asset_record_rows, details, load_catalog, require_internal_m2_access,
)
from .monitoring_bindings import GAS_M0_MONITORING_CHANNELS
from .network import _digest
from .service import GasFixtureService
from .synthetic_scenario import GasScenarioAnswer, select_evidence, trace_answer


RULE = "gas-m2-workbench/1"
INDICATOR_FILE = "gas_indicator_contract.json"
INDICATOR_SHA = "c0337963825d695fff5ea7450e961cf9003b440cb4c5ff97688ba47cf424edd8"
CQ_CODES = frozenset("""
500101 500102 500103 500104 500106 500107 500108 500109 500110 500111 500113 500114 500115 500116
500117 500118 500119 500120 500151 500152 500153 500154 500155 500156 500157 500229 500230 500231
500233 500235 500236 500237 500238 500240 500241 500242 500243
""".split())
SCOPE_LIMIT = "正式健康评分与实际供气、压力、泄漏或停气预测均为 unavailable。"
AssetId = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]


class GasTopicSelection(StrictModel):
    scope: Literal["chongqing", "demo"]
    district_id: str | None = Field(default=None, pattern=r"^500[12]\d{2}$")
    subnet_id: AssetId | None = None
    asset_id: AssetId | None = None

    @model_validator(mode="after")
    def scoped_selection(self):
        if (self.scope == "demo" and self.district_id is not None) or (self.asset_id is not None and self.subnet_id is None):
            raise ValueError("topic selection crosses source domains or skips its subnet")
        return self


class GasTopicQuery(GasTopicSelection):
    version: str = Field(min_length=1, max_length=128)
    registry_sha256: Digest

    def binding(self) -> GasM2Binding:
        return GasM2Binding(dataset_id=DEMO_ID, version=self.version, registry_sha256=self.registry_sha256)


class GasQualityQuery(StrictModel):
    version: str = Field(min_length=1, max_length=128)
    registry_sha256: Digest


class GasFieldMetric(StrictModel):
    id: str
    definition: str
    valid: int
    checked: int
    ratio: float | None


class GasIndicatorPreparation(StrictModel):
    code: str
    name: str
    required: bool
    existing_fields: list[str]
    missing_fields: list[str]
    synthetic_possible: list[str]
    authorization_needed: list[str]


class GasQualityWorkbench(StrictModel):
    binding: GasM2Binding
    rule_version: Literal["gas-m2-workbench/1"] = RULE
    data_kind: Literal["internal_synthetic"] = "internal_synthetic"
    metrics: list[GasFieldMetric]
    observation_quality: dict[str, int]
    incomplete_mapping_fields: list[str]
    unsupported_mappings: list[str]
    indicator_version: str
    indicator_source: GasM2Source
    preparation: list[GasIndicatorPreparation]
    sources: list[GasM2Source]
    source_uri: str | None
    health_score: Literal["unavailable"] = "unavailable"
    evidence_sha256: Digest
    evidence_refs: list[str]


class GasTopicAsset(StrictModel):
    asset_id: str
    asset_type: Literal["pipe", "node", "valve", "consumer"]
    fields: dict[str, object]


class GasTopicSubnet(StrictModel):
    subnet_id: str
    name: str
    registered_assets: int


class GasDataTopic(StrictModel):
    binding: GasM2Binding
    rule_version: Literal["gas-m2-workbench/1"] = RULE
    selection: GasTopicSelection
    level: Literal["city", "district", "demo_catalog", "subnet", "asset"]
    source_status: Literal["administrative_reference_only", "synthetic_fixture"]
    authorized_coordinates: Literal[False] = False
    registered_assets: int
    registered_events: int
    field_completeness: GasFieldMetric
    subnets: list[GasTopicSubnet]
    assets: list[GasTopicAsset]
    events: list[str]
    selected_asset: GasTopicAsset | None = None
    administrative_source_uri: str | None = None
    evidence_sha256: Digest
    evidence_refs: list[str]


class GasM2TopicQuestion(StrictModel):
    binding: GasM2Binding
    tool: Literal["navigation", "quality"]
    selection: GasTopicSelection | None
    expected_sha256: Digest
    question: str = Field(min_length=1, max_length=600, pattern=r"\S")

    @model_validator(mode="after")
    def selected_scope(self):
        if (self.tool == "navigation") != (self.selection is not None):
            raise ValueError("only the navigation tool accepts a selected scope")
        return self


class GasM2TopicAnswer(GasScenarioAnswer):
    tool_name: Literal["data_m2.topic", "data_m2.quality_workbench"]
    selection: GasTopicSelection | None
    verified_sha256: Digest


def metric(identity: str, definition: str, values) -> GasFieldMetric:
    tests = list(values)
    valid, checked = sum(tests), len(tests)
    return GasFieldMetric(id=identity, definition=definition, valid=valid, checked=checked,
                          ratio=round(valid / checked, 6) if checked else None)


def mapped_completeness(entry: GasM2Details, network: dict, case: dict, package):
    assets = [{"district_id": row.district_id} for row in package.rows.assets]
    observations = [{"source_record_id": row.provenance.source_record_id, "asset_id": row.asset_id,
                     "sensor_id": row.sensor_id, "observed_at": row.observed_at, "quality": row.quality,
                     "value": row.value, "metric": row.metric} for row in package.rows.monitoring]
    tables = {
        **{f"network.{name}[]": network[name] for name in ("nodes", "edges", "pipes", "valves")},
        "case.events[]": case["events"], "assets.csv": assets, "monitoring.csv": observations,
    }
    complete, incomplete, unsupported = [], [], []
    for mapping in entry.dataset.field_mappings:
        key, sep, suffix = mapping.raw_field.partition(":")
        if not sep:
            key, sep, suffix = mapping.raw_field.partition("[].")
            key += "[]"
        predicate = re.fullmatch(r"value WHERE metric=(pressure|flow)", suffix)
        rows = tables.get(key)
        if not sep or rows is None:
            unsupported.append(mapping.raw_field)
            continue
        if predicate:
            rows = [row for row in rows if row.get("metric") == predicate[1]]
            suffix = "value"
        fields = re.fullmatch(r"(?:(layout)\.)?\{([A-Za-z0-9_,]+)\}", suffix)
        if fields:
            parent, names = fields[1], fields[2].split(",")
        elif re.fullmatch(r"[A-Za-z0-9_,]+", suffix):
            parent, names = None, suffix.split(",")
        else:
            unsupported.append(mapping.raw_field)
            continue
        slots = []
        for row in rows:
            for name in names:
                values = row.get(parent) if parent else row
                value = values.get(name) if isinstance(values, dict) else None
                slots.append(bool(value.strip()) if isinstance(value, str) else value is not None and value != [])
        complete.extend(slots)
        if not slots or not all(slots):
            incomplete.append(mapping.raw_field)
    return metric("completeness", "已映射字段的非空单元格 / 应检查的映射单元格", complete), incomplete, unsupported


def linkage(network: dict, events, package):
    pipes = {row["pipe_id"]: row for row in network["pipes"]}
    edges = {row["edge_id"]: row for row in network["edges"]}
    nodes = {row["node_id"] for row in network["nodes"]}
    asset_ids = pipes.keys() | nodes | {row["valve_id"] for row in network["valves"]} | {
        row["consumer_id"] for row in network["consumers"]
    }
    for key, pipe in pipes.items():
        yield edges.get(pipe.get("edge_id"), {}).get("pipe_id") == key
    for key, edge in edges.items():
        yield pipes.get(edge.get("pipe_id"), {}).get("edge_id") == key
        yield edge.get("from_node_id") in nodes
        yield edge.get("to_node_id") in nodes
    for row in network["valves"]:
        yield row.get("node_id") in nodes
        for eid in row["incident_edge_ids"]:
            edge = edges.get(eid, {})
            yield row.get("node_id") in (edge.get("from_node_id"), edge.get("to_node_id"))
    for row in network["consumers"] + network["sources"]:
        yield row.get("node_id") in nodes
    for event in events:
        for asset in event.asset_ids:
            yield asset in asset_ids
    for row in package.rows.monitoring:
        yield row.asset_id in asset_ids and (row.asset_id, row.sensor_id, row.metric, row.unit) in GAS_M0_MONITORING_CHANNELS


def indicator_preparation(service: GasFixtureService, network: dict, package) -> tuple[str, list[GasIndicatorPreparation]]:
    contract = service._load_json(INDICATOR_FILE, expected_sha256=INDICATOR_SHA)
    actual_pipe_ids = bool(network["pipes"])
    fields = {
        "G01": (["material"] if any(row.get("material") for row in network["pipes"]) else []),
        "G02": (["material"] if any(row.get("material") for row in network["pipes"]) else []),
        "G04": (["pressure_class"] if any(row.get("pressure_class") for row in network["pipes"]) else []),
        "G06": (["sensor_id", "observed_at", "pressure[kPa]", "flow[Nm3/h]"] if package.rows.monitoring else []),
        "G07": (["valve_id"] if network["valves"] else []),
        "G15": (["valve_id", "initial_state", "incident_edge_ids"] if network["valves"] else []),
    }
    demo_examples = {
        "G01": ["as_of（演练时点）", "design_life_years（显式假定参数）"],
        "G02": ["material_raw（既有合成材质）"],
        "G04": ["pressure_class（既有合成分类）"],
        "G06": ["pressure / flow（既有回放；正式参考值仍缺）", "window_start/window_end（回放窗口）"],
        "G15": ["incident_edge_ids / initial_state（图连通与初始图状态）"],
    }
    preparation = []
    for row in contract["indicators"]:
        code = row["code"]
        existing = (["pipe_id"] if actual_pipe_ids else []) + fields.get(code, [])
        targets = [line["proposed_target"] for line in row["raw_input_fields"]]
        assembled = {"pipe_id"} if actual_pipe_ids else set()
        if code == "G04" and "pressure_class" in existing:
            assembled.add("pressure_class")
        if code == "G06" and package.rows.monitoring:
            assembled.update(["sensor_id/pipe_id", "observed_at"])
        preparation.append(GasIndicatorPreparation(
            code=code, name=row["chinese_name"], required=row["mandatory"], existing_fields=existing,
            missing_fields=[target for target in targets if target not in assembled],
            synthetic_possible=demo_examples.get(code, []),
            authorization_needed=[target for target in targets if target != "as_of"],
        ))
    return contract["contract_version"], preparation


def _scope_evidence(binding: GasM2Binding, sources: list[GasM2Source]) -> list[str]:
    return [f"gas-m2-registry:sha256:{binding.registry_sha256}:{DEMO_ID}",
            *(f"gas-m2-source:sha256:{row.sha256}:{row.name}" for row in sources)]


def quality_workbench(entry: GasM2Details, service: GasFixtureService) -> GasQualityWorkbench:
    network, case, package, events = service.m2_fixture({row.name: row.sha256 for row in entry.dataset.sources})
    completeness, missing, unsupported = mapped_completeness(entry, network, case, package)
    sources = sorted(entry.dataset.sources, key=lambda row: row.name)
    source_hashes = {row.sha256 for row in sources}
    version, preparation = indicator_preparation(service, network, package)
    metrics = [
        completeness,
        metric("linkage", "可解析的图端点、设备、事件资产与测点关系 / 应检查的关系", linkage(network, events, package)),
        metric("observations", "质量标记为 valid 的观测 / 已登记观测", (row.quality == "valid" for row in package.rows.monitoring)),
        metric("provenance", "有清单来源哈希与原始数据行序号的观测 / 已登记观测", (
            row.provenance.source.sha256 in source_hashes and row.provenance.row >= 1 for row in package.rows.monitoring
        )),
    ]
    payload = {
        "binding": entry.binding, "metrics": metrics, "observation_quality": dict(Counter(row.quality for row in package.rows.monitoring)),
        "incomplete_mapping_fields": missing, "unsupported_mappings": unsupported, "indicator_version": version,
        "indicator_source": GasM2Source(name=INDICATOR_FILE, sha256=INDICATOR_SHA), "preparation": preparation,
        "sources": sources, "source_uri": entry.dataset.source_uri,
        "evidence_refs": [*_scope_evidence(entry.binding, sources), f"gas-indicator-contract:sha256:{INDICATOR_SHA}"],
    }
    workbench = GasQualityWorkbench(**payload, evidence_sha256="0" * 64)
    sha = _digest(workbench.model_dump(mode="json", exclude={"evidence_sha256"}))
    return workbench.model_copy(update={"evidence_sha256": sha, "evidence_refs": [*workbench.evidence_refs, f"gas-quality:sha256:{sha}"]})


def data_topic(entry: GasM2Details, service: GasFixtureService, selection: GasTopicSelection, source_uri: str | None) -> GasDataTopic:
    refs = _scope_evidence(entry.binding, [])
    if selection.scope == "chongqing":
        if selection.district_id is not None and selection.district_id not in CQ_CODES:
            raise HTTPException(404, "gas_topic_district_not_found")
        if selection.subnet_id is not None:
            raise HTTPException(404, "gas_topic_authorized_subnet_not_found")
        # The M2 registry excludes operator datasets. Administrative references have no asset claims.
        values = dict(level="district" if selection.district_id else "city", source_status="administrative_reference_only",
                      registered_assets=0, registered_events=0, subnets=[], assets=[], events=[],
                      field_completeness=metric("completeness", "授权区域没有可检查的登记资产字段", []),
                      administrative_source_uri=source_uri)
        if source_uri:
            refs.append("gas-m2-admin-reference:" + source_uri)
    else:
        network, case, package, events = service.m2_fixture({row.name: row.sha256 for row in entry.dataset.sources})
        subnet_id = network["network_id"]
        if selection.subnet_id is not None and selection.subnet_id != subnet_id:
            raise HTTPException(404, "gas_topic_demo_subnet_not_found")
        assets = [
            GasTopicAsset(asset_id=row["record_id"], asset_type=row["asset_type"],
                          fields={key: value for key, value in row.items() if key not in {"record_id", "asset_type", "district_id"}})
            for row in asset_record_rows(network)
        ]
        selected = next((item for item in assets if item.asset_id == selection.asset_id), None)
        if selection.asset_id is not None and selected is None:
            raise HTTPException(404, "gas_topic_asset_not_found")
        values = dict(level="asset" if selected else "subnet" if selection.subnet_id else "demo_catalog",
                      source_status="synthetic_fixture", registered_assets=len(assets), registered_events=len(events),
                      subnets=[GasTopicSubnet(subnet_id=subnet_id, name="GAS-M0 合成子网", registered_assets=len(assets))],
                      assets=assets if selection.subnet_id is not None else [], events=[row.event_id for row in events],
                      selected_asset=selected, field_completeness=mapped_completeness(entry, network, case, package)[0])
        refs = _scope_evidence(entry.binding, entry.dataset.sources)
        if selected:
            refs.append(f"gas-m2-record:{DEMO_ID}:{selected.asset_id}")
    result = GasDataTopic(binding=entry.binding, selection=selection, evidence_refs=refs, evidence_sha256="0" * 64, **values)
    sha = _digest(result.model_dump(mode="json", exclude={"evidence_sha256"}))
    return result.model_copy(update={"evidence_sha256": sha, "evidence_refs": [*refs, f"gas-topic:sha256:{sha}"]})


def gas_data_workbench_router(service: GasFixtureService) -> APIRouter:
    router = APIRouter(prefix="/data-m2", tags=["gas-data-workbench"])
    settings = {"openapi_extra": {"security": [{"WaterToken": []}]}, "responses": COMMON_ERRORS}

    def read(request: Request, response: Response, binding: GasM2Binding):
        require_internal_m2_access(request, response)
        catalog = load_catalog()
        current = details(catalog, DEMO_ID)
        if current.binding != binding:
            raise HTTPException(409, "gas_m2_version_conflict")
        admin_uri = next((row.source_uri for row in catalog.datasets if row.dataset_id == "CN-AMAP-DISTRICT"), None)
        return current, admin_uri

    @router.get("/navigation", response_model=GasDataTopic, **settings)
    def navigation(request: Request, response: Response, query: Annotated[GasTopicQuery, Query()]):
        entry, uri = read(request, response, query.binding())
        return data_topic(entry, service, GasTopicSelection.model_validate(query.model_dump(exclude={"version", "registry_sha256"})), uri)

    @router.get("/quality-workbench", response_model=GasQualityWorkbench, **settings)
    def quality(request: Request, response: Response, query: Annotated[GasQualityQuery, Query()]):
        entry, _ = read(request, response, GasM2Binding(dataset_id=DEMO_ID, **query.model_dump()))
        return quality_workbench(entry, service)

    @router.post("/topic-query", response_model=GasM2TopicAnswer, **settings)
    def explain(payload: GasM2TopicQuestion, request: Request, response: Response):
        entry, uri = read(request, response, payload.binding)
        first = f"M2 {DEMO_ID} {entry.binding.version}，登记 SHA-256 {entry.binding.registry_sha256}。"
        if payload.selection is not None:
            data = data_topic(entry, service, payload.selection, uri)
            tool = "data_m2.topic"
            if data.source_status == "administrative_reference_only":
                facts = [f"重庆范围 {data.selection.district_id or '全市'} 当前仅登记行政参考，授权燃气覆盖和字段完整率未知。",
                         "当前 M2 范围登记的授权子网、资产和事件均为 0 项。"]
            else:
                facts = [f"GAS-M0 合成拓扑登记 {data.registered_assets} 项资产、{data.registered_events} 项演练事件。",
                         "合成网络坐标为 schematic，授权地理坐标不可用。"]
                if data.selected_asset:
                    asset = data.selected_asset
                    facts.append(f"合成资产 {asset.asset_id} 为 {asset.asset_type}。")
                    facts.extend(f"合成登记字段 {key} 为 {json.dumps(value, ensure_ascii=False, sort_keys=True)}。"
                                 for key, value in asset.fields.items() if value is not None)
        else:
            data = quality_workbench(entry, service)
            tool = "data_m2.quality_workbench"
            facts = [f"{row.definition} 为 {row.valid}/{row.checked}。" if row.checked else f"{row.definition} 为 unknown。"
                     for row in data.metrics]
            facts.append(f"{data.indicator_version} 包含 {len(data.preparation)} 项数据准备合同，正式健康评分 unavailable。")
        if data.evidence_sha256 != payload.expected_sha256:
            raise HTTPException(409, "gas_topic_evidence_conflict")
        facts = [first, *facts, SCOPE_LIMIT]
        result = GasM2TopicAnswer(
            request_id="gas-topic-" + uuid4().hex, binding=entry.binding, tool_name=tool, selection=payload.selection,
            verified_sha256=data.evidence_sha256, evidence_refs=data.evidence_refs, facts=facts, answer="\n".join(facts),
        )
        select_evidence(result, payload.question)
        read(request, response, payload.binding)
        trace_answer(result, response, kind="gas_m2_topic_read_tool")
        return result

    return router
