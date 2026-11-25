"""Read-only gas agent adapter.

The default parser is deterministic. An explicitly configured local model may
suggest a validated read-only plan and select complete evidence sentences. It never accepts a client data
classification and never exposes control or mutation tools.
"""
from __future__ import annotations

import re
import secrets
import time
import logging
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from src.platform.authz import Principal

from .adapter import CASE_ID
from .agent_evidence import (
    ENGINEERING_CLAIM, GAS_M0_REFERENCE, PILOT_HISTORY, PILOT_REFERENCE, PUBLIC_BENCHMARK_REFERENCE,
    GasAgentCaseBinding, GasAgentEvidenceScope,
)
from .capabilities import capability_catalog
from .contracts import GAS_DATASET_ID, GasAsset, GasEvent
from .cuv_benchmark import BENCHMARK_ID, GasCuvBenchmarkBinding, GasCuvBenchmarkReport, benchmark_facts, run_public_benchmark
from .cuv_collaboration import (
    GasCuvComparisonChange, GasCuvConfirmedComparison, GasCuvCriteria, GasCuvCriteriaComparison,
    GasCuvDraftRequest, GasCuvQueryBinding,
)
from .cuv_engineering import (
    GRAPH_CAPABILITY, GasCuvCapabilityId, GasCuvEngineeringAssessment, GasCuvEngineeringCapability,
    capability_status, engineering_assessment, engineering_facts, supports_graph_run, topology_refs,
)
from .cuv_evidence import CuvMonitoringContext, GasCuvReplayResponse
from .cuv_replay import GasCuvPlan
from .data_package import Digest
from .service import GasFixtureService, GasFixtureUnavailable
from .model_provider import LocalGasModel, ModelUnavailable, MODEL_ID
from .monitoring_context import GasMonitoringObservation, GasObservationRef, resolve_observation
from .public_data import GasPublicDataService
from .simulation import GasSimulationRequest, GasSimulationService
from .gaslib import GasLibService

CuvComparisonRead = Callable[
    [GasCuvReplayResponse],
    GasCuvConfirmedComparison
    | tuple[GasCuvQueryBinding, GasCuvConfirmedComparison | None, GasCuvComparisonChange | None]
    | None,
]
GasAgentContext = tuple[list[GasAsset], list[GasEvent], GasAgentCaseBinding, GasAgentEvidenceScope]

GasAgentToolName = Literal[
    "asset.query",
    "event.query",
    "map.locate",
    "capabilities.query",
    "event.explain",
    "cuv.query",
    "cuv.engineering.query",
    "cuv.benchmark.query",
    "monitoring.query",
    "phmsa.incident.query",
    "dryad.experiment.query",
    "public.report.query",
    "simulation.query",
    "gaslib.quality.query",
]
GasAgentIntent = Literal[
    "asset_query",
    "event_query",
    "map_locate",
    "capabilities_query",
    "event_explain",
    "cuv_query",
    "cuv_engineering_query",
    "cuv_benchmark_query",
    "monitoring_query",
    "phmsa_query",
    "dryad_query",
    "public_report_query",
    "simulation_query",
    "gaslib_quality_query",
    "clarify",
]


_OPEN_PIPE_DETAILS = re.compile(
    r"^(?:请(?:帮我)?)?\s*打开\s*(?:(?:管段\s*)?(?:syn-gas-pipe-)?K-\d+\s*(?:管段)?|(?:这个|该|选中的)管段)"
    r"(?:的?(?:详情|信息))?\s*[。！？?!]?\s*$", re.ASCII | re.I,
)
_CONTROL_REQUEST = re.compile(
    r"执行|控制|复气|(?:关闭|打开|关阀|开阀|切断|调节)(?!状态)|"
    r"(?:预测|计算|给出|生成).*(?:泄漏概率|压力|流量|风险分)|(?:压力|流量|泄漏概率|风险分).*(?:多少|计算|预测)|"
    r"(?:确认|开始处理|标记完成|完成处置|更新状态|派单).*(?:事件|处置)|close.*valve|open.*valve",
    re.I,
)
_CUV_READ = re.compile(
    r"推演|两案.{0,8}(?:取舍|比较|对比)|(?:方案|隔离|恢复).{0,12}(?:取舍|比较|对比)|"
    r"(?:比较|对比).{0,12}(?:方案|两案)|(?:确认|重算|修订).{0,10}后.{0,12}(?:变化|差异)"
)
_ENGINEERING_READ = re.compile(r"工程(?:校核|模型|计算)|输入就绪|缺少.{0,5}(?:输入|参数)|校核证据")
CuvToolName = Literal["cuv.query", "cuv.engineering.query"]
_CUV_TOOLS = {"cuv.query", "cuv.engineering.query"}
_CUV_EDIT = re.compile(r"^\s*(?:请(?:帮我)?|帮我)?\s*(?:确认并|调整|修改|保存|提交|应用|重算|重新计算).{0,16}(?:目标|约束|条件|推演|方案|重算)")
_CUV_OBJECT_ID = re.compile(
    r"(?<![a-z0-9_-])(?:syn-gas-(?:pipe|valve|node|event)-[a-z0-9_-]+|K-\d+|N\d+)(?![a-z0-9_-])",
    re.I | re.ASCII,
)
_CUV_REVISION = re.compile(r"第\s*([0-9]{1,8})\s*版|版本\s*([0-9]{1,8})(?![0-9.])")
_BENCHMARK_LIMITATION = "只读复算登记的公开水平单管与串并联工况。真实管网压力、流量和安全性仍待授权数据及独立校核。"
_BENCHMARK_NETWORK_CONTEXT = re.compile(r"(?:当前|选中|现场|真实|试点).{0,8}(?:管段|管网|资产|事件|阀门|监测|观测|复气)")
_CUV_LIMITATION = "合成方案只比较图通路。实际供气、压力、流量、现场复气时长、失效概率和成本保持未知。"
_AFFECTED_PIPES = re.compile(r"(?:受影响|影响|涉及|关联).{0,8}(?:管段|管线)")
_PUBLIC_PHMSA_READ = re.compile(r"PHMSA|美国历史(?:燃气)?事故|燃气事故档案|历史事故", re.I)
_PUBLIC_DRYAD_READ = re.compile(r"Dryad|ESCAPE[- ]?1|甲烷实验|甲烷浓度|methane|释放实验|气象实验", re.I)
_PUBLIC_REPORT_READ = re.compile(r"来源报告|证据报告|数据质量报告|实验报告|事故报告", re.I)
_SIMULATION_READ = re.compile(r"仿真|模拟|回放|能否运行|可不可以跑|可执行.*(?:模型|算例)", re.I)
_GASLIB_READ = re.compile(r"GasLib|Gas Lib|网络基准.*(?:XML|场景|质量)|跨文件引用", re.I)


class GasAgentQueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000, pattern=r"\S")
    case_id: str | None = Field(default=None, max_length=128)
    asset_id: str | None = Field(default=None, max_length=128)
    event_id: str | None = Field(default=None, max_length=128)
    district_id: str | None = Field(default=None, max_length=32)
    tool_hint: GasAgentToolName | None = None
    cuv_at: AwareDatetime | None = None
    monitoring_ref: GasObservationRef | None = None
    cuv_draft: GasCuvDraftRequest | None = None
    case_binding: GasAgentCaseBinding | None = None
    monitoring_policy_id: str | None = Field(default=None, max_length=128)
    cuv_replay_sha256: Digest | None = None
    cuv_comparison_binding: GasCuvQueryBinding | None = None
    cuv_capability: GasCuvCapabilityId | None = None
    benchmark_binding: GasCuvBenchmarkBinding | None = None

    @field_validator("cuv_at", mode="before")
    @classmethod
    def _validate_cuv_clock(cls, value):
        if value is not None and not isinstance(value, (str, datetime)):
            raise ValueError("CUV 时点须为带时区的时间")
        if isinstance(value, str) and "T" not in value:
            raise ValueError("CUV 时点须为带时区的 ISO 时间")
        return value


class GasAgentToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: GasAgentToolName
    tool_version: Literal["gas-tools-0.1.0"] = "gas-tools-0.1.0"
    status: Literal["succeeded", "partial", "unavailable", "not_found"]
    data: dict[str, object] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    limitation: str
    elapsed_ms: float = 0
    parameters: dict[str, str | None] = Field(default_factory=dict)
    evidence_scope: GasAgentEvidenceScope | None = None
    monitoring_scope: GasAgentEvidenceScope | None = None
    cuv_comparison_binding: GasCuvQueryBinding | None = None
    engineering_capability: GasCuvEngineeringCapability | None = None
    engineering_assessment: GasCuvEngineeringAssessment | None = None
    benchmark_report: GasCuvBenchmarkReport | None = None


class GasAgentQueryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    status: Literal["succeeded", "partial", "unavailable", "not_found"]
    data_mode: Literal["synthetic", "public_reference"]
    dataset_id: Literal["syn-gas-network-html54-0.1.0", "syn-gas-public-weymouth-v1", "US-PHMSA-INCIDENT", "US-DRYAD-CH4", "DE-GASLIB"]
    model_status: Literal["unavailable", "ready"]
    parser_source: Literal["local_rule_not_llm", "qwen_intent"]
    intent: GasAgentIntent
    tools: list[GasAgentToolResult]
    model_id: str | None = None
    model_calls: int = 0
    model_elapsed_ms: float = 0
    model_usage: dict[str, int] = Field(default_factory=dict)
    elapsed_ms: float = 0
    error_code: str | None = None
    explanation: str | None = None
    explanation_source: Literal["none", "qwen_evidence_selection"] = "none"
    evidence_refs: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    evidence_scope: GasAgentEvidenceScope | None = None
    reference_mode: Literal["none", "public_reference"] = "none"
    reference_corpus_id: str | None = None


class GasAgentHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: Literal[True] = True
    model_status: Literal["unavailable", "starting", "ready"] = "unavailable"
    parser_source: Literal["local_rule_not_llm", "qwen_intent"] = "local_rule_not_llm"
    provider_configured: bool = False
    tools_status: Literal["ready"] = "ready"
    read_only: Literal[True] = True
    status: Literal["unavailable", "starting", "ready"] = "unavailable"
    reason: str


def _dump(value: object) -> dict[str, object]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")  # type: ignore[no-any-return]
    return dict(value)  # type: ignore[arg-type,return-value]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _utc_text(at: datetime) -> str:
    return at.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _cuv_plan_view(plan: GasCuvPlan, at: datetime, asset_id: str | None) -> dict[str, object]:
    phase = next((step for step in reversed(plan.steps) if step.at <= at), None)
    return {
        "plan_id": plan.plan_id, "label": plan.label, "status": plan.status,
        "phase": _dump(phase) if phase is not None else None,
        "selected_asset_blocked": asset_id in phase.blocked_asset_ids
        if asset_id is not None and phase is not None and phase.status == "evaluated" else None,
        "affected_service_hours": plan.affected_service_hours,
        "peak_affected_service_count": plan.peak_affected_service_count,
        "topology_restoration_hours": plan.topology_restoration_hours,
    }


def _cuv_summary(plans: list[dict[str, object]], at: datetime) -> str:
    phases = []
    schedules = []
    for plan in plans:
        phase = plan["phase"]
        affected = phase["affected_service_ids"] if isinstance(phase, dict) else None
        count = str(len(affected)) if isinstance(affected, list) else "未知"
        impact = plan["affected_service_hours"]
        restoration = plan["topology_restoration_hours"]
        phases.append(f"{plan['label']}新增图不可达服务 {count} 个")
        schedules.append(
            f"{plan['label']}累计图影响 {impact if impact is not None else '未知'} 服务对象·小时，"
            f"图可达恢复至基线为第 {restoration if restoration is not None else '未知'} 小时"
        )
    return f"合成时点 {_utc_text(at)}：{'，'.join(phases)}。完整合成分析窗：{'；'.join(schedules)}。{_CUV_LIMITATION}"


def _cuv_conditions(
    criteria: GasCuvCriteria, comparison: GasCuvCriteriaComparison, report: GasCuvReplayResponse,
) -> str:
    objective = ("最小累计图影响" if criteria.objective == "min_affected_service_hours"
                 else "最早服务图可达恢复")
    constraints = []
    if criteria.max_peak_affected_services is not None:
        constraints.append(f"新增图不可达服务峰值最多 {criteria.max_peak_affected_services} 个")
    if criteria.restore_by_hours is not None:
        constraints.append(f"第 {criteria.restore_by_hours:g} 小时前恢复服务图可达")
    labels = {plan.plan_id: plan.label for plan in report.plans}
    leaders = [labels[plan_id] for plan_id in comparison.leading_plan_ids if plan_id in labels]
    leading = "、".join(leaders) if leaders else "无满足当前约束的可比较方案"
    return f"目标为{objective}，约束为{'、'.join(constraints) if constraints else '未追加'}，领先方案：{leading}"


def _cuv_change_text(change: GasCuvComparisonChange, report: GasCuvReplayResponse) -> str:
    before = _cuv_conditions(change.before.criteria, change.before.comparison, report)
    after = _cuv_conditions(change.after_criteria, change.after_comparison, report)
    label = "待确认条件（只读预览）" if change.status == "preview" else f"已确认第 {change.confirmed_revision} 版"
    return f"原比较第 {change.before.revision} 版，{before}。{label}，{after}。"


class GasAgentService:
    """Authorized read-only tools with explicit rule/model provenance."""

    VERSION = "gas-tools-0.1.0"

    def __init__(self, fixture_service: GasFixtureService, *, model_starting: Callable[[], bool] | None = None,
                 public_data_service: GasPublicDataService | None = None,
                 simulation_service: GasSimulationService | None = None,
                 gaslib_service: GasLibService | None = None):
        self.fixture_service = fixture_service
        self._model_starting = model_starting
        self.public_data_service = public_data_service
        self.simulation_service = simulation_service or GasSimulationService(fixture_service, public_data_service or GasPublicDataService())
        self.gaslib_service = gaslib_service or GasLibService()

    def health(self) -> GasAgentHealthResponse:
        if self._model_starting is not None and self._model_starting():
            return GasAgentHealthResponse(
                model_status="starting", status="starting", provider_configured=True,
                reason="Qwen 正在后台预检或预热。只读规则查询已可用。",
            )
        try:
            provider = LocalGasModel.from_environment()
            if provider and provider.ready():
                return GasAgentHealthResponse(model_status="ready", parser_source="qwen_intent",
                    provider_configured=True, status="ready",
                    reason="本地Qwen网关就绪；意图经校验后才执行只读工具，解释只能选取真实证据。")
            configured = provider is not None
        except ModelUnavailable:
            configured = False
        return GasAgentHealthResponse(provider_configured=configured,
            reason="本地Qwen网关未就绪；仍可使用 local_rule_not_llm 只读工具。")

    def _context(self) -> GasAgentContext:
        context = self.fixture_service.asset_event_records()
        if len(context[1]) > 1000:
            raise HTTPException(503, "燃气事件索引暂不可用")
        return context

    @staticmethod
    def _find_ids(
        question: str, assets: list[GasAsset], events: list[GasEvent], *,
        infer_event: bool = True, aliases: bool = False,
    ) -> tuple[list[str], list[str]]:
        def mentions(identifier: str) -> bool:
            return re.search(r"(?<![A-Za-z0-9_-])" + re.escape(identifier) + r"(?![A-Za-z0-9_-])", question, re.I) is not None
        asset_ids = [item.asset_id for item in assets if mentions(item.asset_id)]
        # Keep every mentioned registered ID, including aliases mixed with full IDs.
        if aliases or any(word in question for word in ("管段", "管线", "事件", "告警")):
            asset_ids.extend(item.asset_id for item in assets if item.asset_type == "pipe"
                             and mentions(item.asset_id.removeprefix("syn-gas-pipe-")))
        if aliases or "阀门" in question:
            asset_ids.extend(item.asset_id for item in assets if item.asset_id.startswith("syn-gas-valve-")
                             and mentions(item.asset_id.removeprefix("syn-gas-valve-")))
        event_ids = [item.event_id for item in events if mentions(item.event_id)]
        if infer_event and not event_ids:
            kind = "given_isolation" if "隔离" in question else "given_fault" if "故障" in question else None
            matches = [item.event_id for item in events if item.event_kind == kind]
            if len(matches) == 1:
                event_ids = matches
        return _unique(asset_ids), event_ids

    @staticmethod
    def _event_scope_error(
        payload: GasAgentQueryRequest,
        tool: GasAgentToolName | None,
        asset_ids: list[str],
        event_ids: list[str],
    ) -> GasAgentQueryResponse | None:
        if tool not in {"event.query", "event.explain"}:
            return None
        # Preserve explicit identifiers which did not resolve. Dropping them
        # would turn a scoped query into an unfiltered event list.
        recognized = {value.casefold() for value in asset_ids + event_ids}
        for asset_id in asset_ids:
            for prefix in ("syn-gas-pipe-", "syn-gas-valve-"):
                if asset_id.startswith(prefix):
                    recognized.add(asset_id.removeprefix(prefix).casefold())
        asset_nouns = ("管段", "管线", "阀门", "节点", "资产")
        names_asset = any(noun in payload.question for noun in asset_nouns)
        short_id = r"[a-z]+-?\d+" if names_asset else r"K-\d+|N\d+"
        identifiers = re.findall(
            rf"(?<![a-z0-9_-])(?:syn-gas-[a-z0-9_-]+|{short_id})(?![a-z0-9_-])",
            payload.question, re.ASCII | re.I,
        )
        for value in identifiers:
            if value.casefold() not in recognized:
                parameter = "event_id" if value.casefold().startswith("syn-gas-event-") else "asset_id"
                result = GasAgentToolResult(tool_name=tool, status="not_found", parameters={parameter: value},
                                            limitation="指定编号在当前合成案例中无法解析。")
                return GasAgentQueryResponse(
                    request_id="gas-agent-"+secrets.token_hex(8), status="not_found", data_mode="synthetic",
                    dataset_id=GAS_DATASET_ID, model_status="unavailable", parser_source="local_rule_not_llm",
                    intent="event_query" if tool == "event.query" else "event_explain", tools=[result],
                )
        asset_scope = set(asset_ids)
        event_scope = set(event_ids)
        if payload.asset_id:
            asset_scope.add(payload.asset_id)
        if payload.event_id:
            event_scope.add(payload.event_id)
        if len(asset_scope) > 1 or len(event_scope) > 1:
            clarification = "识别到多个对象，请选择本次要查询的资产或事件。"
        elif not asset_scope and not event_scope and names_asset and any(
            term in payload.question for term in ("关联", "相关", "对应")
        ):
            clarification = "请提供要查询关联事件的资产编号。"
        else:
            return None
        return GasAgentQueryResponse(
            request_id="gas-agent-" + secrets.token_hex(8), status="unavailable", data_mode="synthetic",
            dataset_id=GAS_DATASET_ID, model_status="unavailable", parser_source="local_rule_not_llm",
            intent="clarify", tools=[], error_code="parameter_required", limitations=[clarification],
        )

    @staticmethod
    def _intent(payload: GasAgentQueryRequest, asset_ids: list[str], event_ids: list[str]) -> tuple[GasAgentIntent, GasAgentToolName | None]:
        text = payload.question
        # Opening a specific pipe's details is navigation. Other open commands
        # remain outside the read tool boundary, including valve operations.
        if _OPEN_PIPE_DETAILS.fullmatch(text):
            return "asset_query", "asset.query"
        if payload.tool_hint == "cuv.engineering.query" or (payload.tool_hint is None and _ENGINEERING_READ.search(text)):
            return "cuv_engineering_query", "cuv.engineering.query"
        if _CONTROL_REQUEST.search(text) or _CUV_EDIT.search(text):
            return "clarify", None
        intent_map: dict[str, GasAgentIntent] = {
            "asset.query": "asset_query", "event.query": "event_query",
            "map.locate": "map_locate", "capabilities.query": "capabilities_query",
            "event.explain": "event_explain", "cuv.query": "cuv_query", "monitoring.query": "monitoring_query",
            "phmsa.incident.query": "phmsa_query", "dryad.experiment.query": "dryad_query",
            "public.report.query": "public_report_query", "simulation.query": "simulation_query",
            "gaslib.quality.query": "gaslib_quality_query",
        }
        if payload.tool_hint is not None:
            return intent_map[payload.tool_hint], payload.tool_hint
        if payload.tool_hint == "simulation.query" or _SIMULATION_READ.search(text):
            return "simulation_query", "simulation.query"
        if _PUBLIC_PHMSA_READ.search(text):
            return "phmsa_query", "phmsa.incident.query"
        if _PUBLIC_DRYAD_READ.search(text):
            return "dryad_query", "dryad.experiment.query"
        if _PUBLIC_REPORT_READ.search(text):
            return "public_report_query", "public.report.query"
        if any(word in text.upper() for word in ("定位", "区县", "GIS", "地图", "坐标", "位置")):
            return "map_locate", "map.locate"
        if _CUV_READ.search(text) or payload.cuv_at is not None or payload.cuv_capability is not None:
            return "cuv_query", "cuv.query"
        if any(word in text.upper() for word in ("能力", "CUV", "就绪", "风险", "未接入", "不可用")):
            return "capabilities_query", "capabilities.query"
        if payload.monitoring_ref and re.search(r"异常|观测|读数|越限|阈值|监测", text) and not _AFFECTED_PIPES.search(text):
            return "monitoring_query", "monitoring.query"
        if payload.event_id or event_ids or _AFFECTED_PIPES.search(text) or any(word in text for word in ("事件", "告警")):
            return ("event_explain", "event.explain") if any(word in text for word in ("解释", "原因", "依据")) else ("event_query", "event.query")
        if payload.asset_id or asset_ids or any(word in text for word in ("管段", "阀门", "资产", "节点")):
            return "asset_query", "asset.query"
        return "clarify", None

    def query(
        self, payload: GasAgentQueryRequest, *, principal: Principal | None = None,
        cuv_comparison: CuvComparisonRead | None = None,
    ) -> GasAgentQueryResponse:
        started = time.monotonic()
        request_id = "gas-agent-"+secrets.token_hex(8)
        try:
            if payload.tool_hint in {"phmsa.incident.query", "dryad.experiment.query", "public.report.query"} or (
                payload.tool_hint is None and (_PUBLIC_PHMSA_READ.search(payload.question)
                                                or _PUBLIC_DRYAD_READ.search(payload.question)
                                                or _PUBLIC_REPORT_READ.search(payload.question))
            ):
                result = self._query_public(payload, principal)
            elif payload.tool_hint == "simulation.query" or (
                payload.tool_hint is None and _SIMULATION_READ.search(payload.question)
            ):
                result = self._query_simulation(payload, principal)
            elif payload.tool_hint == "gaslib.quality.query" or (
                payload.tool_hint is None and _GASLIB_READ.search(payload.question)
            ):
                result = self._query_gaslib(payload, principal)
            elif payload.tool_hint == "cuv.benchmark.query" or (
                payload.tool_hint is None and PUBLIC_BENCHMARK_REFERENCE.search(payload.question)
            ):
                result = self._query_benchmark(payload)
            else:
                context = self._context()
                result = self._query(payload, context, cuv_comparison=cuv_comparison)
                case_scope = context[3]
                for tool in result.tools:
                    if tool.evidence_scope is None:
                        tool.evidence_scope = case_scope
                result.evidence_scope = result.tools[0].evidence_scope if result.tools else case_scope
        except HTTPException as exc:
            logging.getLogger("gas.agent").info(json.dumps({"kind":"gas_agent_error","request_id":request_id,
                "error_code":f"http_{exc.status_code}","elapsed_ms":round((time.monotonic()-started)*1000,2)}))
            exc.headers = {**(exc.headers or {}),"X-Request-ID":request_id}
            raise
        result.request_id=request_id
        result.elapsed_ms = round((time.monotonic()-started)*1000,2)
        logging.getLogger("gas.agent").info(json.dumps({"kind":"gas_agent_result","request_id":result.request_id,
            "parser_source":result.parser_source,"model_status":result.model_status,"model_id":result.model_id,
            "model_calls":result.model_calls,"model_elapsed_ms":result.model_elapsed_ms,"elapsed_ms":result.elapsed_ms,"error_code":result.error_code,
            "tools":[{"tool":t.tool_name,"status":t.status,"parameters":t.parameters,"elapsed_ms":t.elapsed_ms} for t in result.tools]}))
        return result

    def _query_simulation(self, payload: GasAgentQueryRequest, principal: Principal | None) -> GasAgentQueryResponse:
        if self.simulation_service is None or principal is None:
            tool = GasAgentToolResult(
                tool_name="simulation.query", status="unavailable",
                limitation="仿真工作台未配置服务端读取身份。",
            )
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="synthetic", dataset_id=GAS_DATASET_ID,
                model_status="unavailable", parser_source="local_rule_not_llm", intent="simulation_query",
                tools=[tool], error_code="simulation_unavailable",
                limitations=["仿真必须通过服务端登记的只读数据集执行。"],
            )
        text = payload.question
        if _PUBLIC_DRYAD_READ.search(text) or "实验" in text:
            mode = "experiment_replay"
        elif _PUBLIC_PHMSA_READ.search(text) or "历史事故" in text:
            mode = "historical_incident"
        elif re.search(r"Weymouth|稳态|求解|基准|GasLib", text, re.I):
            mode = "solver_benchmark"
        elif re.search(r"重庆.*(?:真实|现场|物理)|实际管网|物理仿真", text):
            mode = "physical_chongqing"
        else:
            mode = "synthetic_topology"
        request = GasSimulationRequest(mode=mode)
        result = self.simulation_service.run(principal, request)
        payload_data = result.model_dump(mode="json")
        tool = GasAgentToolResult(
            tool_name="simulation.query", status=result.status, data=payload_data,
            evidence_refs=list(result.evidence_refs),
            limitation="；".join(result.limitations) or "结果仅来自已登记的只读仿真路径。",
            parameters={"mode": mode, "dataset_id": result.dataset_id},
        )
        response_dataset = result.dataset_id if result.dataset_id in {GAS_DATASET_ID, "syn-gas-public-weymouth-v1", "US-PHMSA-INCIDENT", "US-DRYAD-CH4"} else GAS_DATASET_ID
        return GasAgentQueryResponse(
            request_id="", status=result.status, data_mode=result.data_mode if result.data_mode in {"synthetic", "public_reference"} else "synthetic",
            dataset_id=response_dataset, model_status="unavailable", parser_source="local_rule_not_llm",
            intent="simulation_query", tools=[tool], evidence_refs=list(result.evidence_refs),
            reference_mode="public_reference" if result.data_mode == "public_reference" else "none",
            reference_corpus_id="gas-public-20261124" if result.data_mode == "public_reference" else None,
            limitations=list(result.limitations),
        )

    def _query_gaslib(self, payload: GasAgentQueryRequest, principal: Principal | None) -> GasAgentQueryResponse:
        if self.gaslib_service is None or principal is None:
            tool = GasAgentToolResult(
                tool_name="gaslib.quality.query", status="unavailable",
                limitation="GasLib 适配器未配置服务端读取身份。",
            )
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="public_reference",
                dataset_id="DE-GASLIB", model_status="unavailable",
                parser_source="local_rule_not_llm", intent="gaslib_quality_query",
                tools=[tool], error_code="gaslib_unavailable",
                reference_mode="public_reference", reference_corpus_id="gas-public-20261124",
                limitations=["GasLib 只读质量查询需要公共研究数据 ACL。"],
            )
        match = re.search(r"GasLib[- ]?(\d+)(?:[- ]v?\d+[- ]\d+)?", payload.question, re.I)
        instance_id = f"GasLib-{match.group(1)}" if match else "GasLib-11-v1-20211130"
        if match and not instance_id.endswith(tuple(("20211130", "20211129", "20240708", "20220119"))):
            candidates = self.gaslib_service.catalog(principal)
            selected = next((item.instance_id for item in candidates if item.instance_id.startswith(instance_id + "-")), None)
            instance_id = selected or instance_id
        result = self.gaslib_service.quality(principal, instance_id)
        status = "succeeded" if result.status == "checked" else result.status
        tool = GasAgentToolResult(
            tool_name="gaslib.quality.query", status=status, data=result.model_dump(mode="json"),
            evidence_refs=result.evidence_refs,
            limitation="；".join(result.limitations) or "结果仅来自 GasLib 原件、成员哈希和 XML 引用校验。",
            parameters={"instance_id": instance_id},
        )
        return GasAgentQueryResponse(
            request_id="", status=status, data_mode="public_reference", dataset_id="DE-GASLIB",
            model_status="unavailable", parser_source="local_rule_not_llm",
            intent="gaslib_quality_query", tools=[tool], evidence_refs=result.evidence_refs,
            reference_mode="public_reference", reference_corpus_id="gas-public-20261124",
            limitations=result.limitations,
        )

    def _query_public(self, payload: GasAgentQueryRequest, principal: Principal | None) -> GasAgentQueryResponse:
        if self.public_data_service is None or principal is None:
            tool = GasAgentToolResult(
                tool_name="public.report.query", status="unavailable",
                limitation="公共燃气数据适配器未配置服务端读取身份。",
            )
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="public_reference", dataset_id="US-PHMSA-INCIDENT",
                model_status="unavailable", parser_source="local_rule_not_llm", intent="public_report_query",
                tools=[tool], error_code="public_data_unavailable",
                limitations=["公共历史和实验数据必须通过服务端ACL读取。"], reference_mode="public_reference",
            )
        tool_name = payload.tool_hint
        if tool_name not in {"phmsa.incident.query", "dryad.experiment.query", "public.report.query"}:
            if _PUBLIC_PHMSA_READ.search(payload.question):
                tool_name = "phmsa.incident.query"
            elif _PUBLIC_DRYAD_READ.search(payload.question):
                tool_name = "dryad.experiment.query"
            else:
                tool_name = "public.report.query"
        result = self.public_data_service.agent_query(principal, payload.question, tool_name)
        tool = GasAgentToolResult(
            tool_name=tool_name, status=result.status, data=result.data,
            evidence_refs=result.evidence_refs, limitation="；".join(result.limitations) or "结果仅来自公共历史或实验原件。",
        )
        intent = {"phmsa.incident.query": "phmsa_query", "dryad.experiment.query": "dryad_query",
                  "public.report.query": "public_report_query"}[tool_name]
        return GasAgentQueryResponse(
            request_id="", status=result.status, data_mode="public_reference", dataset_id=result.dataset_id,
            model_status="unavailable", parser_source="local_rule_not_llm", intent=intent,
            tools=[tool], evidence_refs=result.evidence_refs, reference_mode="public_reference",
            reference_corpus_id="gas-public-20261124", limitations=result.limitations,
        )

    def _query_benchmark(self, payload: GasAgentQueryRequest) -> GasAgentQueryResponse:
        # The reference suite is resolved independently of the engineering/pilot datasets.
        attached = (
            payload.case_id, payload.case_binding, payload.asset_id, payload.event_id, payload.district_id,
            payload.monitoring_ref, payload.monitoring_policy_id, payload.cuv_at, payload.cuv_draft,
            payload.cuv_replay_sha256, payload.cuv_comparison_binding, payload.cuv_capability,
        )
        result = GasAgentQueryResponse(
            request_id="", status="unavailable", data_mode="synthetic", dataset_id=BENCHMARK_ID,
            model_status="unavailable", parser_source="local_rule_not_llm", intent="cuv_benchmark_query",
            tools=[], limitations=[_BENCHMARK_LIMITATION],
        )
        if (any(value is not None for value in attached)
            or PILOT_REFERENCE.search(payload.question) or PILOT_HISTORY.search(payload.question)
            or GAS_M0_REFERENCE.search(payload.question) or _CUV_OBJECT_ID.search(payload.question)
            or _BENCHMARK_NETWORK_CONTEXT.search(payload.question) or ENGINEERING_CLAIM.search(payload.question)):
            result.error_code = "benchmark_scope_conflict"
            return result

        report = run_public_benchmark(payload.benchmark_binding)
        identity = report.binding
        scope = GasAgentEvidenceScope(
            scope="public_reference_benchmark", dataset_id=identity.suite_id,
            dataset_version=identity.suite_version, data_mode="synthetic", source_sha256=identity.suite_sha256,
        )
        evidence = [f"gas-cuv-benchmark:sha256:{identity.suite_sha256}",
                    *(f"gas-cuv-benchmark-input:{item.case_id}:sha256:{item.input_sha256}" for item in report.cases)]
        if report.network_prototype is not None:
            network = report.network_prototype
            evidence.append(f"gas-cuv-benchmark-input:{network.case_id}:sha256:{network.input_sha256}")
        passed = report.progress.benchmark_status == "benchmark_passed"
        result.evidence_scope = scope
        result.status = "succeeded" if passed else "unavailable"
        result.tools = [GasAgentToolResult(
            tool_name="cuv.benchmark.query", status=result.status, evidence_scope=scope,
            evidence_refs=evidence, limitation=_BENCHMARK_LIMITATION, benchmark_report=report,
        )]
        result.evidence_refs = evidence
        if not passed:
            result.error_code = "benchmark_validation_failed"
            return result
        provider = None
        try:
            provider = LocalGasModel.from_environment()
            if provider is None:
                return result
            question = f"已校验的查询类型：cuv.benchmark.query。问题：{payload.question}"
            if provider.plan(question, [], []).tool != "cuv.benchmark.query":
                raise ModelUnavailable("model_tool_scope_mismatch")
            facts = benchmark_facts(report)
            selected = provider.explain(facts, question=payload.question)
            return result.model_copy(update={
                "model_status": "ready", "parser_source": "qwen_intent", "model_id": MODEL_ID,
                "model_calls": provider.calls, "model_usage": provider.usage, "model_elapsed_ms": provider.elapsed_ms,
                "explanation": "\n".join(_unique([facts[0], *selected.splitlines(), facts[-1]])),
                "explanation_source": "qwen_evidence_selection",
            })
        except ModelUnavailable as exc:
            result.error_code = str(exc)
            if provider is not None:
                result.model_calls = provider.calls
                result.model_usage = provider.usage
                result.model_elapsed_ms = provider.elapsed_ms
            return result

    def _query(
        self, payload: GasAgentQueryRequest, context: GasAgentContext, *, cuv_comparison: CuvComparisonRead | None,
    ) -> GasAgentQueryResponse:
        assets, events, binding, case_scope = context
        if payload.case_binding is not None and payload.case_binding != binding:
            raise HTTPException(409, "gas_agent_case_version_conflict")
        if payload.case_id is not None and payload.case_id != CASE_ID:
            raise HTTPException(404, "燃气合成案例不存在")

        if PILOT_REFERENCE.search(payload.question) or PILOT_HISTORY.search(payload.question):
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="synthetic", dataset_id=GAS_DATASET_ID,
                model_status="unavailable", parser_source="local_rule_not_llm", intent="clarify", tools=[],
                error_code="cross_dataset_unverified",
                limitations=["试点历史回放与 GAS-M0 之间没有已核验的资产、事件或时点关联。请在试点观测中查询其证据。"],
            )
        if payload.benchmark_binding is not None or PUBLIC_BENCHMARK_REFERENCE.search(payload.question):
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="synthetic", dataset_id=GAS_DATASET_ID,
                model_status="unavailable", parser_source="local_rule_not_llm", intent="clarify", tools=[],
                error_code="benchmark_scope_conflict", limitations=[_BENCHMARK_LIMITATION],
            )
        if payload.cuv_capability is not None and not supports_graph_run(payload.cuv_capability):
            if payload.tool_hint not in (None, "cuv.query"):
                raise HTTPException(422, "gas_cuv_capability_requires_cuv_query")
            capability = capability_status(payload.cuv_capability)
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="synthetic", dataset_id=GAS_DATASET_ID,
                model_status="unavailable", parser_source="local_rule_not_llm", intent="cuv_query",
                error_code="engineering_capability_blocked", limitations=list(capability.reasons),
                tools=[GasAgentToolResult(
                    tool_name="cuv.query", status="unavailable", limitation=capability.reasons[0],
                    engineering_capability=capability,
                )],
            )

        self._validate_context(payload, assets, events)
        matches, event_matches = self._find_ids(payload.question, assets, events)
        intent, rule_tool = self._intent(payload, matches, event_matches)
        if rule_tool != "cuv.engineering.query" and ENGINEERING_CLAIM.search(payload.question):
            return GasAgentQueryResponse(
                request_id="", status="unavailable", data_mode="synthetic", dataset_id=GAS_DATASET_ID,
                model_status="unavailable", parser_source="local_rule_not_llm", intent="clarify", tools=[],
                error_code="engineering_evidence_unavailable",
                limitations=["缺少经校核的专业模型与授权工程输入。实际供气、压力、流量、泄漏和现场恢复时间保持未知。"],
            )
        scope_error = self._event_scope_error(payload, rule_tool, matches, event_matches)
        if scope_error is not None:
            return scope_error
        # Reject control/unsupported requests before contacting the model.
        no_asset = not matches and not payload.asset_id
        no_event = not event_matches and not payload.event_id
        asset_scope = set(matches)
        if payload.asset_id:
            asset_scope.add(payload.asset_id)
        missing_parameter = (
            intent == "asset_query" and (len(asset_scope) > 1 or (no_asset and not re.search(r"[A-Za-z]+-?\d+", payload.question)))
            or no_event and (intent == "event_explain" or (intent == "event_query" and bool(_AFFECTED_PIPES.search(payload.question))))
        )
        if missing_parameter:
            return GasAgentQueryResponse(request_id="gas-agent-"+secrets.token_hex(8),status="unavailable",
                data_mode="synthetic",dataset_id=GAS_DATASET_ID,model_status="unavailable",
                parser_source="local_rule_not_llm",intent="clarify",tools=[],evidence_refs=[],
                error_code="parameter_required",limitations=["请提供或选择资产/事件编号；没有猜测对象或执行工具。"])
        if intent == "asset_query" and no_asset and re.search(r"[A-Za-z]+-?\d+", payload.question):
            return self._query_local(payload, context=context)
        if intent == "clarify":
            return self._query_local(payload, context=context)
        if payload.cuv_draft is not None and rule_tool != "cuv.query":
            raise HTTPException(422, "gas_cuv_draft_requires_cuv_query")
        if rule_tool not in _CUV_TOOLS and (payload.cuv_replay_sha256 or payload.cuv_comparison_binding):
            raise HTTPException(422, "gas_cuv_binding_requires_cuv_query")
        if payload.cuv_capability is not None and rule_tool != "cuv.query":
            raise HTTPException(422, "gas_cuv_capability_requires_cuv_query")
        if payload.monitoring_policy_id is not None and payload.monitoring_ref is None:
            raise HTTPException(422, "gas_monitoring_binding_requires_observation")

        monitoring = None
        replay = None
        if payload.monitoring_ref and rule_tool in {"monitoring.query", "event.query", "event.explain", *_CUV_TOOLS}:
            if rule_tool in _CUV_TOOLS:
                replay = self.fixture_service.cuv_replay(case_binding=binding, replay_sha256=payload.cuv_replay_sha256)
            monitoring = resolve_observation(self.fixture_service, payload.monitoring_ref, replay=replay)
            if ((payload.monitoring_policy_id and payload.monitoring_policy_id != monitoring.policy_id)
                or (monitoring.event and monitoring.event.source_sha256 != binding.case_content_sha256)):
                raise HTTPException(409, "gas_monitoring_evidence_version_conflict")
            event_id = monitoring.event.event_id if monitoring.event else None
            if ((payload.asset_id and payload.asset_id != monitoring.asset_id)
                or (payload.event_id and payload.event_id != event_id)
                or (matches and matches != [monitoring.asset_id])
                or (event_matches and event_matches != [event_id] and rule_tool not in _CUV_TOOLS)
                or (rule_tool in _CUV_TOOLS and (event_id is None or (
                    payload.cuv_at is not None and payload.cuv_at != monitoring.triggered_at
                )))):
                raise HTTPException(409, "gas_monitoring_context_conflict")
            payload = payload.model_copy(update={"asset_id": monitoring.asset_id, "event_id": event_id})

        def local(query: GasAgentQueryRequest = payload) -> GasAgentQueryResponse:
            return self._query_local(query, context=context, cuv_comparison=cuv_comparison, monitoring=monitoring, replay=replay)

        scoped_result = None
        if rule_tool in {"monitoring.query", *_CUV_TOOLS}:
            scoped_result = local()
            if scoped_result.status not in {"succeeded", "partial"}:
                return scoped_result
        provider = None
        try:
            provider = LocalGasModel.from_environment()
            if provider is None:
                return scoped_result or local()
            if scoped_result is None:
                candidates = [payload.asset_id] if payload.asset_id else matches
                event_candidates = event_matches or ([payload.event_id] if payload.event_id else [])
            else:
                scope = scoped_result.tools[0].parameters
                candidates = [scope["asset_id"]] if scope["asset_id"] else []
                event_candidates = [scope["event_id"]] if scope["event_id"] else []
            # No unidentified object may be selected just because it is in the dataset.
            question = payload.question
            if monitoring is not None or rule_tool in _CUV_TOOLS:
                question = f"已校验的查询类型：{rule_tool}。问题：{question}"
            plan = provider.plan(question, candidates, event_candidates)
            if plan.tool == "clarify" and scoped_result is None:
                return GasAgentQueryResponse(
                    request_id="gas-agent-" + secrets.token_hex(8), status="unavailable",
                    data_mode="synthetic", dataset_id=GAS_DATASET_ID, intent="clarify",
                    tools=[], evidence_refs=[], model_status="ready", parser_source="qwen_intent",
                    model_id=MODEL_ID, model_calls=provider.calls, model_usage=provider.usage, model_elapsed_ms=provider.elapsed_ms,
                    limitations=["模型请求澄清；未执行工具。"])
            if plan.tool != rule_tool:
                raise ModelUnavailable("model_tool_scope_mismatch")
            # A plan may only use registered IDs and the fixed read-only allowlist.
            result = scoped_result or local(payload.model_copy(update={
                "tool_hint": plan.tool, "asset_id": plan.asset_id or payload.asset_id,
                "event_id": plan.event_id or payload.event_id,
            }))
            evidence_scope = result.tools[0].evidence_scope if result.tools else None
            scope = evidence_scope or case_scope
            scope_sentence = (
                f"数据集 {scope.dataset_id}，版本 {scope.dataset_version}，"
                f"适用范围 {scope.scope}，来源 SHA-256 {scope.source_sha256}。"
            )
            sentences = [scope_sentence]
            required_sentences = [scope_sentence]
            for tool in result.tools:
                sentences.append(f"工具 {tool.tool_name} 状态 {tool.status}。{tool.limitation}")
                if tool.tool_name == "asset.query" and "asset" in tool.data:
                    asset = tool.data["asset"]
                    sentences.append(f"资产编号 {asset['asset_id']}；资产类型 {asset['asset_type']}。")
                    for field, label in (("material", "材质"), ("diameter_mm", "管径毫米"), ("pressure_class", "压力等级")):
                        if asset.get(field) is not None:
                            sentences.append(f"资产 {asset['asset_id']} 的{label}为 {asset[field]}。")
                if "event" in tool.data:
                    event = tool.data["event"]
                    sentences.append(f"事件 {event['event_id']}；类型 {event['event_kind']}；关联资产 {', '.join(event['asset_ids'])}。")
                if "related_pipe_ids" in tool.data:
                    ids = "、".join(tool.data["related_pipe_ids"])
                    sentences.append(f"合成事件关联管段：{ids or '无'}。")
                if tool.tool_name == "event.query" and tool.data.get("asset_id"):
                    ids = "、".join(event["event_id"] for event in tool.data["items"])
                    sentences.append(f"资产 {tool.data['asset_id']} 的合成关联事件：{ids or '无'}。")
                if tool.tool_name == "event.explain" and "explanation" in tool.data:
                    sentences.append(tool.data["explanation"])
                if tool.tool_name == "cuv.query":
                    sentences.extend(tool.data["explanation_sentences"])
                if tool.engineering_assessment is not None:
                    facts = engineering_facts(tool.engineering_assessment)
                    sentences.extend(facts)
                    required_sentences.extend(facts)
                    if tool.cuv_comparison_binding:
                        revision = tool.cuv_comparison_binding.expected_revision
                        version_fact = f"本次工程就绪度检查绑定人工比较修订 {revision}。"
                        sentences.append(version_fact)
                        required_sentences.append(version_fact)
            if monitoring is not None:
                sentences.append(monitoring.explanation())
            # Model only selects complete server-produced evidence sentences.
            # Its free text can never replace structured numeric/business facts.
            explanation = provider.explain(sentences)
            if rule_tool == "cuv.query" and scoped_result is not None:
                data = scoped_result.tools[0].data
                required_sentences.extend((data["summary"], data["explanation_sentences"][1]))
            if monitoring is not None:
                required_sentences.append(monitoring.explanation())
            explanation = "\n".join(_unique(required_sentences + explanation.splitlines()))
            return result.model_copy(update={"model_status":"ready","parser_source":"qwen_intent",
                "model_id":MODEL_ID,"model_calls":provider.calls,"model_usage":provider.usage,"model_elapsed_ms":provider.elapsed_ms,
                "explanation":explanation,"explanation_source":"qwen_evidence_selection",
                "limitations":["本地模型仅规划只读工具及选取证据句；无真实GIS、物理风险或设备控制结论。"]})
        except ModelUnavailable as exc:
            fallback = scoped_result or local()
            fallback.limitations[0] = "模型规划或解释未通过验证；结果使用 local_rule_not_llm。模型请求数单独记录。"
            fallback.error_code = str(exc)
            fallback.limitations.append(str(exc) + "；已明确降级为服务端本地规则。")
            if provider is not None:
                fallback.model_elapsed_ms = provider.elapsed_ms
                fallback.model_calls = provider.calls
                fallback.model_usage = provider.usage
            return fallback

    @staticmethod
    def _validate_context(payload: GasAgentQueryRequest, assets: list[GasAsset], events: list[GasEvent]) -> None:
        if payload.case_id is not None and payload.case_id != "syn-gas-case-K-001":
            raise HTTPException(status_code=404, detail="燃气合成案例不存在")
        if payload.asset_id is not None and payload.asset_id not in {item.asset_id for item in assets}:
            raise HTTPException(status_code=404, detail="燃气资产不存在")
        if payload.event_id is not None and payload.event_id not in {item.event_id for item in events}:
            raise HTTPException(status_code=404, detail="燃气事件不存在")

    def _cuv_tool(
        self, payload: GasAgentQueryRequest, assets: list[GasAsset],
        events: list[GasEvent], case_binding: GasAgentCaseBinding,
        cuv_comparison: CuvComparisonRead | None, tool_name: CuvToolName, replay: GasCuvReplayResponse | None,
    ) -> GasAgentToolResult:
        matched_assets, matched_events = self._find_ids(
            payload.question, assets, events, infer_event=False, aliases=True,
        )
        if not matched_events and payload.event_id is None:
            named_kinds = {
                kind for term, kind in (("隔离事件", "given_isolation"), ("故障事件", "given_fault"))
                if term in payload.question
            }
            matched_events = [item.event_id for item in events if item.event_kind in named_kinds]

        asset_scope = _unique(matched_assets + ([payload.asset_id] if payload.asset_id else []))
        event_scope = _unique(matched_events + ([payload.event_id] if payload.event_id else []))
        asset_id = asset_scope[0] if len(asset_scope) == 1 else None
        event_id = event_scope[0] if len(event_scope) == 1 else None
        requested_at = payload.cuv_at
        parameters = {
            "asset_id": asset_id, "event_id": event_id,
            "cuv_at": _utc_text(requested_at) if requested_at else None,
        }

        def rejected(message: str, status: Literal["not_found", "unavailable"] = "not_found") -> GasAgentToolResult:
            return GasAgentToolResult(
                tool_name=tool_name, status=status, limitation=message, parameters=parameters,
            )

        recognized = {value.casefold() for value in asset_scope + event_scope}
        recognized.update(value.removeprefix("syn-gas-pipe-").removeprefix("syn-gas-valve-").casefold()
                          for value in asset_scope)
        if any(value.casefold() not in recognized for value in _CUV_OBJECT_ID.findall(payload.question)):
            return rejected("问题中的资产或事件编号未在当前合成案例中解析。")
        if len(asset_scope) > 1 or len(event_scope) > 1:
            return rejected("存在多个资产或事件，请选择同一组推演上下文。")

        try:
            report = replay if replay is not None else self.fixture_service.cuv_replay(
                case_binding=case_binding, replay_sha256=payload.cuv_replay_sha256,
            )
        except GasFixtureUnavailable:
            return rejected("合成推演所需资料暂不可用。", "unavailable")

        topology = report.topology_evidence

        by_event = {event.event_id: event for event in report.event_evidence}
        selected_event = by_event.get(event_id) if event_id is not None else None
        if event_id is not None and selected_event is None:
            return rejected("当前事件没有关联的 CUV 合成推演。")
        if selected_event is not None and asset_id is not None and asset_id not in selected_event.asset_ids:
            return rejected("所选资产与事件在当前合成推演中没有关联。")

        asset_events = [event for event in report.event_evidence if asset_id in event.asset_ids]
        if asset_id is not None and not asset_events:
            return rejected("当前资产没有关联的 CUV 合成推演。")
        allowed_times = {report.scenario_at, *(event.replay_at for event in report.event_evidence)}
        at = requested_at
        if at is None:
            at = selected_event.replay_at if selected_event is not None else min(
                (event.replay_at for event in asset_events), default=report.scenario_at,
            )
        if at not in allowed_times or (selected_event is not None and at != selected_event.replay_at):
            return rejected("请选择合成起点或与当前事件一致的已记录回放时点。")
        if asset_id is not None and not any(event.replay_at <= at for event in asset_events):
            return rejected("在所选合成时点没有该资产的事件关联证据。")
        parameters["cuv_at"] = _utc_text(at)

        projection = cuv_comparison(report) if cuv_comparison is not None else None
        comparison_binding, confirmed, change = projection if isinstance(projection, tuple) else (None, projection, None)
        requested_revisions = {
            int(number) for match in _CUV_REVISION.findall(payload.question) for number in match if number
        }
        represented_revisions = {confirmed.revision} if confirmed is not None else {0}
        if change is not None:
            represented_revisions.add(change.before.revision)
        if not requested_revisions <= represented_revisions:
            return rejected("问题引用的方案版本不在本次可追溯比较范围。请打开对应审计，或查询当前版本。", "unavailable")

        cuv_context = {
            "case_id": report.case_id, "scenario_id": report.scenario_id, "event_id": event_id,
            "asset_id": asset_id, "at": _utc_text(at), "scenario_at": _utc_text(report.scenario_at),
        }
        evidence_refs = list(topology_refs(report))
        if confirmed is not None:
            evidence_refs.append(f"gas-cuv-confirmed:{confirmed.revision}:sha256:{confirmed.input_sha256}")
        if tool_name == "cuv.engineering.query":
            assessment = engineering_assessment(report)
            return GasAgentToolResult(
                tool_name=tool_name, status="succeeded", parameters=parameters, data={"context": cuv_context},
                limitation="仅查询模型输入、校核和就绪度。正式压力、流量、供气及现场复气时长保持未知。",
                evidence_scope=assessment.evidence_scope, evidence_refs=evidence_refs,
                cuv_comparison_binding=comparison_binding, engineering_assessment=assessment,
            )

        event_evidence = [selected_event] if selected_event else [
            event for event in report.event_evidence
            if event.replay_at <= at and (asset_id is None or asset_id in event.asset_ids)
        ]
        visible_assets = {asset_id} if asset_id is not None else {
            identity for event in event_evidence for identity in event.asset_ids
        }
        monitoring = report.monitoring_evidence
        if monitoring.as_of > at:
            monitoring = CuvMonitoringContext(status="unavailable", as_of=at, channels=[])
        else:
            monitoring = monitoring.model_copy(update={
                "channels": [item for item in monitoring.channels if item.asset_id in visible_assets],
            })
        plans = [_cuv_plan_view(plan, at, asset_id) for plan in report.plans]
        summary = _cuv_summary(plans, at)
        source_sentence = f"推演配置 SHA-256 {topology.replay_content_sha256}。"
        evidence_sentences = [summary, source_sentence]
        for event in event_evidence:
            evidence_refs.extend(event.evidence_refs)
            evidence_refs.append(f"gas-event:{event.event_id}:sha256:{event.source_sha256}")
            evidence_sentences.append(f"案例事件 {event.event_id} 的合成时点为 {_utc_text(event.replay_at)}，案例 SHA-256 {event.source_sha256}。")
        monitoring_scope = None
        if monitoring.channels and monitoring.manifest_sha256 and monitoring.dataset_id and monitoring.dataset_version:
            monitoring_scope = GasAgentEvidenceScope(
                scope="gas_m0_monitoring", dataset_id=monitoring.dataset_id, dataset_version=monitoring.dataset_version,
                data_mode="synthetic", source_sha256=monitoring.manifest_sha256,
            )
            evidence_refs.append(f"gas-monitoring:sha256:{monitoring.manifest_sha256}")
            evidence_sentences.append(
                f"关联监测取自 {_utc_text(monitoring.as_of)} 的合成回放，清单 SHA-256 {monitoring.manifest_sha256}。"
            )
            evidence_refs.extend(
                f"gas-monitoring-row:{row.source_id}:{row.source_row}:sha256:{row.source_sha256}"
                for channel in monitoring.channels if channel.transition is not None
                for row in channel.transition.evidence
            )

        confirmed_data = None
        if confirmed is not None:
            confirmed_data = confirmed.model_dump(
                mode="json", include={"revision", "input_sha256", "criteria", "comparison"},
            )
            conditions = _cuv_conditions(confirmed.criteria, confirmed.comparison, report)
            decision_text = f"已确认的人工比较第 {confirmed.revision} 版，{conditions}。"
            summary += " " + decision_text
            evidence_sentences.append(decision_text)

        if change is not None:
            change_text = _cuv_change_text(change, report)
            summary += " " + change_text
            evidence_sentences.append(change_text)

        return GasAgentToolResult(
            tool_name="cuv.query", status="partial", limitation=_CUV_LIMITATION, parameters=parameters,
            evidence_refs=_unique(evidence_refs),
            evidence_scope=GasAgentEvidenceScope(
                scope="gas_m0_graph_cuv", dataset_id=report.network_id, dataset_version=report.schema_version,
                data_mode=report.data_mode, source_sha256=topology.replay_content_sha256,
            ),
            monitoring_scope=monitoring_scope, cuv_comparison_binding=comparison_binding,
            engineering_capability=capability_status(GRAPH_CAPABILITY, report),
            data={
                "context": cuv_context,
                "plans": plans,
                "unknown_metrics": [_dump(item) for item in report.unknown_metrics],
                "event_evidence": [_dump(event) for event in event_evidence],
                "monitoring_evidence": _dump(monitoring),
                "confirmed_comparison": confirmed_data,
                "comparison_change": _dump(change) if change is not None else None,
                "summary": summary,
                "explanation_sentences": evidence_sentences,
            },
        )

    def _query_local(
        self, payload: GasAgentQueryRequest, *,
        context: GasAgentContext | None = None,
        cuv_comparison: CuvComparisonRead | None = None,
        monitoring: GasMonitoringObservation | None = None,
        replay: GasCuvReplayResponse | None = None,
    ) -> GasAgentQueryResponse:
        started = time.monotonic()
        assets, events, case_binding, _ = context if context is not None else self._context()
        self._validate_context(payload, assets, events)
        asset_by_id = {item.asset_id: item for item in assets}
        event_by_id = {item.event_id: item for item in events}
        matched_asset_ids, matched_event_ids = self._find_ids(payload.question, assets, events)
        requested_asset_id = payload.asset_id or (matched_asset_ids[0] if len(matched_asset_ids) == 1 else None)
        requested_event_id = (matched_event_ids[0] if len(matched_event_ids) == 1 else payload.event_id)

        intent, tool_name = self._intent(payload, matched_asset_ids, matched_event_ids)
        request_id = "gas-agent-" + secrets.token_hex(8)
        evidence: list[str] = []
        tools: list[GasAgentToolResult] = []
        limitations = [
            "model_status=unavailable；本次未调用 Qwen 或其他外部模型。",
            "仅执行已授权的 synthetic GAS-M0 只读工具；不输出真实坐标、压力、流量、风险概率或控制指令。",
        ]

        if tool_name == "asset.query":
            item = asset_by_id.get(requested_asset_id or "")
            if item is None:
                tools.append(GasAgentToolResult(
                    tool_name="asset.query", status="not_found",
                    limitation="问题中没有识别到当前 synthetic 数据集内的资产编号。",
                ))
            else:
                data = _dump(item)
                evidence = list(data.get("evidence_refs", []))
                tools.append(GasAgentToolResult(
                    tool_name="asset.query", status="succeeded", data={"asset": data},
                    evidence_refs=evidence,
                    limitation="资产属性来自 GAS-M0 合成 Fixture，validation_status 保持 not_business_validated。",
                ))
        elif tool_name in {"event.query", "event.explain"}:
            item = event_by_id.get(requested_event_id or "")
            if item is not None and requested_asset_id and requested_asset_id not in item.asset_ids:
                tools.append(GasAgentToolResult(
                    tool_name=tool_name, status="not_found",
                    limitation="在当前合成案例中未找到此事件与指定资产的关联。",
                ))
            elif item is None and tool_name == "event.explain":
                tools.append(GasAgentToolResult(
                    tool_name=tool_name, status="not_found",
                    limitation="请指定或选择要解释的事件；没有生成无证据解释。",
                ))
            elif item is None:
                filtered = [value for value in events if not requested_asset_id or requested_asset_id in value.asset_ids]
                data = {"items": [_dump(value) for value in filtered], "asset_id":requested_asset_id}
                evidence = _unique([ref for value in filtered for ref in value.evidence_refs]
                    + (list(asset_by_id[requested_asset_id].evidence_refs) if requested_asset_id in asset_by_id else []))
                tools.append(GasAgentToolResult(
                    tool_name=tool_name, status="succeeded", data=data,
                    evidence_refs=evidence,
                    limitation="事件来自给定合成案例，不是企业实时告警。",
                ))
            else:
                raw_event = _dump(item)
                evidence = list(raw_event.get("evidence_refs", []))
                data = {"event": raw_event}
                if _AFFECTED_PIPES.search(payload.question):
                    data["related_pipe_ids"] = [
                        asset_id for asset_id in item.asset_ids
                        if asset_id in asset_by_id and asset_by_id[asset_id].asset_type == "pipe"
                    ]
                if tool_name == "event.explain":
                    data["explanation"] = (
                        "该事件仅表示 GAS-M0 给定的图连通性情景；asset_ids 和 "
                        "closed_valve_ids 是可追溯输入，不代表真实供气中断或现场操作建议。"
                    )
                tools.append(GasAgentToolResult(
                    tool_name=tool_name, status="succeeded", data=data,
                    evidence_refs=evidence,
                    limitation="事件说明取自结构化字段，不包含专业物理推演或业务风险验证。",
                ))
        elif tool_name == "map.locate":
            target_assets = [requested_asset_id] if requested_asset_id in asset_by_id else matched_asset_ids
            target_event = requested_event_id if requested_event_id in event_by_id else None
            if target_event is not None:
                target_assets = _unique(target_assets + event_by_id[target_event].asset_ids)
            target_assets = [item for item in target_assets if item in asset_by_id]
            tools.append(GasAgentToolResult(
                tool_name="map.locate", status="partial",
                data={
                    "view": "topology",
                    "event_id": target_event,
                    "asset_ids": _unique(target_assets),
                    "district_id": payload.district_id,
                    "coordinate_authorized": False,
                    "location_status": "not_available",
                    "districts": _dump(self.fixture_service.districts()),
                },
                evidence_refs=_unique(
                    [ref for item in assets if item.asset_id in target_assets for ref in item.evidence_refs]
                    + [ref for item in events if item.event_id == target_event for ref in item.evidence_refs]
                    + ["gas-api:/api/v1/gas/engineering/districts"]
                ),
                limitation="没有经授权的重庆燃气经纬度；定位仅作用于合成拓扑，不能冒充 GIS 实景位置。",
            ))
        elif tool_name in ("cuv.query", "cuv.engineering.query"):
            tools.append(self._cuv_tool(payload, assets, events, case_binding, cuv_comparison, tool_name, replay))
        elif tool_name == "monitoring.query":
            tools.append(GasAgentToolResult(
                tool_name="monitoring.query", status="succeeded" if monitoring else "unavailable",
                limitation="仅引用已选合成越限观测与样例策略。" if monitoring else "请先从合成监测回放选择一条越限观测。",
            ))
        elif tool_name == "capabilities.query":
            data = _dump(capability_catalog())
            evidence = _unique([ref for item in data["items"] for ref in item.get("evidence_refs", [])])
            tools.append(GasAgentToolResult(
                tool_name="capabilities.query", status="succeeded", data=data,
                evidence_refs=evidence,
                limitation="能力状态保留 ready_synthetic、unsupported 和 not_business_validated 语义。",
            ))
        else:
            limitations.append("请补充事件、资产、区县或 CUV 能力查询目标；未执行任何工具。")

        for tool in tools:
            tool.elapsed_ms = round((time.monotonic()-started)*1000,2)
            if tool.tool_name not in _CUV_TOOLS:
                tool.parameters = {"asset_id":requested_asset_id,"event_id":requested_event_id,"district_id":payload.district_id}
            if monitoring is not None and tool.status in {"succeeded", "partial"}:
                tool.parameters["observation_id"] = monitoring.reference.observation_id
                tool.data["monitoring_context"] = _dump(monitoring)
                tool.monitoring_scope = GasAgentEvidenceScope(
                    scope="gas_m0_monitoring", dataset_id=monitoring.dataset_id, dataset_version=monitoring.dataset_version,
                    data_mode="synthetic", source_sha256=monitoring.reference.manifest_sha256,
                )
                if tool.tool_name == "monitoring.query":
                    tool.evidence_scope = tool.monitoring_scope
                tool.evidence_refs = _unique(tool.evidence_refs + monitoring.evidence_refs())
        response_status = tools[0].status if tools else "unavailable"
        return GasAgentQueryResponse(
            request_id=request_id,
            status=response_status,
            data_mode="synthetic",
            dataset_id=GAS_DATASET_ID,
            model_status="unavailable",
            parser_source="local_rule_not_llm",
            intent=intent,
            tools=tools,
            evidence_refs=_unique(evidence + [ref for tool in tools for ref in tool.evidence_refs]),
            limitations=limitations,
        )


__all__ = [
    "GasAgentHealthResponse",
    "GasAgentQueryRequest",
    "GasAgentQueryResponse",
    "GasAgentService",
]
