"""Read-only gas simulation workbench over audited datasets.

The workbench deliberately separates deterministic graph replay, public research
replay and professional solver benchmarks. It does not infer physical gas
behaviour from topology and it never performs a control or mutation action.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import Principal, authorize_dataset, resolve_principal

from .contracts import GAS_DATASET_ID
from .cuv_benchmark import BENCHMARK_ID, run_public_benchmark
from .public_data import (
    DRYAD_DATASET_ID,
    PHMSA_DATASET_ID,
    GasPublicDataService,
    PhmsaCategory,
)
from .service import GasFixtureService, GasFixtureUnavailable


SimulationMode = Literal[
    "synthetic_topology",
    "experiment_replay",
    "historical_incident",
    "solver_benchmark",
    "physical_chongqing",
]
SimulationStatus = Literal["succeeded", "partial", "unavailable", "not_found"]
CapabilityStatus = Literal["ready", "candidate", "unavailable"]
SimulationDataMode = Literal["synthetic", "public_reference", "real_authorized", "unavailable"]


class SimulationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class GasSimulationCapability(SimulationContract):
    mode: SimulationMode
    title: str
    status: CapabilityStatus
    deterministic: bool
    dataset_ids: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    supported_outputs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


class GasSimulationCatalogResponse(SimulationContract):
    status: Literal["ready", "partial", "unavailable"]
    workbench_version: str
    capabilities: tuple[GasSimulationCapability, ...]
    limitations: tuple[str, ...] = ()


class GasSimulationRequest(SimulationContract):
    mode: SimulationMode
    dataset_id: str | None = Field(default=None, max_length=128)
    scenario_id: str | None = Field(default=None, max_length=128)
    experiment_id: str | None = Field(default=None, max_length=128)
    category: PhmsaCategory | None = None
    year_from: int | None = Field(default=None, ge=1900, le=2100)
    year_to: int | None = Field(default=None, ge=1900, le=2100)
    state: str | None = Field(default=None, min_length=2, max_length=2)
    limit: int = Field(default=20, ge=1, le=100)


class GasSimulationReport(SimulationContract):
    report_id: str
    generated_at: datetime
    title: str
    mode: SimulationMode
    status: SimulationStatus
    dataset_id: str
    dataset_version: str
    data_mode: SimulationDataMode
    findings: tuple[str, ...] = ()
    unknowns: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    report_sha256: str


class GasSimulationResult(SimulationContract):
    run_id: str
    mode: SimulationMode
    status: SimulationStatus
    dataset_id: str
    dataset_version: str
    data_mode: SimulationDataMode
    result: dict[str, object] = Field(default_factory=dict)
    evidence_refs: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    report: GasSimulationReport


_WORKBENCH_VERSION = "gas-simulation-workbench-0.1.0"

_CAPABILITIES: tuple[GasSimulationCapability, ...] = (
    GasSimulationCapability(
        mode="synthetic_topology", title="GAS-M0 合成拓扑回放", status="ready", deterministic=True,
        dataset_ids=(GAS_DATASET_ID,), required_inputs=("gas54_network.json", "gas54_case.json", "gas_cuv_replay.json"),
        supported_outputs=("图连通性", "事件证据", "方案步骤", "合成监测关联"),
        limitations=("只表示给定合成图的可达性和方案统计", "不代表实际供气、压力、流量、泄漏或现场处置"),
    ),
    GasSimulationCapability(
        mode="experiment_replay", title="Dryad 甲烷实验回放", status="ready", deterministic=True,
        dataset_ids=(DRYAD_DATASET_ID,), required_inputs=("授权公共研究数据回执", "experiment_id"),
        supported_outputs=("实验文件摘要", "记录数", "字段与缺测统计", "原件证据"),
        limitations=("实验数据不含重庆资产关联", "不输出管网压力、流量或事故概率"),
    ),
    GasSimulationCapability(
        mode="historical_incident", title="PHMSA 历史事故查询", status="ready", deterministic=True,
        dataset_ids=(PHMSA_DATASET_ID,), required_inputs=("授权公共研究数据回执", "可选类别、年份和州过滤"),
        supported_outputs=("历史记录分页", "质量状态", "来源文件与源行证据"),
        limitations=("美国历史档案不能映射为重庆当前事件", "不生成实时告警或风险判断"),
    ),
    GasSimulationCapability(
        mode="solver_benchmark", title="公开稳态求解基准", status="candidate", deterministic=True,
        dataset_ids=(BENCHMARK_ID,), required_inputs=("固定公开算例", "模型适用范围"),
        supported_outputs=("求解状态", "参考误差", "正向复算", "多管段原型检查"),
        limitations=("仅为公开基准软件验证", "尚未完成重庆现场数据校核"),
    ),
    GasSimulationCapability(
        mode="physical_chongqing", title="重庆授权管网物理仿真", status="unavailable", deterministic=False,
        dataset_ids=(), required_inputs=("授权拓扑与坐标", "管径、长度、材质与粗糙度", "气源与负荷边界", "校准监测", "专业签核"),
        supported_outputs=(),
        limitations=("缺少授权工程输入和独立校核，禁止输出物理压力、流量、供气率或恢复时长" ,),
    ),
)


def _canonical_sha(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))


class GasSimulationService:
    """Execute only registered deterministic read paths."""

    def __init__(self, fixture_service: GasFixtureService, public_data_service: GasPublicDataService):
        self.fixture_service = fixture_service
        self.public_data_service = public_data_service

    @staticmethod
    def _authorize(principal: Principal, mode: SimulationMode) -> None:
        if mode in {"synthetic_topology", "solver_benchmark", "physical_chongqing"}:
            dataset_id = GAS_DATASET_ID if mode != "solver_benchmark" else BENCHMARK_ID
            authorize_dataset(principal, dataset_id, "synthetic")
            return
        # Public adapters enforce corpus ACLs and internal scope themselves.

    def catalog(self, principal: Principal) -> GasSimulationCatalogResponse:
        self._authorize(principal, "synthetic_topology")
        limitations = (
            "仿真工作台只读；原始数据、GAS-M0 合成数据和公开研究数据保持独立身份。",
            "未经授权的重庆工程数据不进入本工作台。",
        )
        return GasSimulationCatalogResponse(status="ready", workbench_version=_WORKBENCH_VERSION,
                                            capabilities=_CAPABILITIES, limitations=limitations)

    def run(self, principal: Principal, request: GasSimulationRequest) -> GasSimulationResult:
        self._authorize(principal, request.mode)
        if request.mode == "synthetic_topology":
            return self._synthetic_topology(request)
        if request.mode == "experiment_replay":
            self.public_data_service._authorize(principal)
            return self._experiment_replay(principal, request)
        if request.mode == "historical_incident":
            self.public_data_service._authorize(principal)
            return self._historical_incident(principal, request)
        if request.mode == "solver_benchmark":
            return self._solver_benchmark(request)
        return self._physical_chongqing(request)

    def _result(
        self, *, mode: SimulationMode, status: SimulationStatus, dataset_id: str,
        dataset_version: str, data_mode: SimulationDataMode, result: dict[str, object],
        evidence_refs: list[str], limitations: list[str], findings: list[str], unknowns: list[str],
    ) -> GasSimulationResult:
        report_base = {
            "title": f"燃气仿真工作台 · {mode}", "mode": mode, "status": status,
            "dataset_id": dataset_id, "dataset_version": dataset_version, "data_mode": data_mode,
            "findings": findings, "unknowns": unknowns, "evidence_refs": evidence_refs,
        }
        digest = _canonical_sha(report_base)
        report_id = f"gas-simulation-{mode}-{digest[:16]}"
        generated_at = datetime.now(UTC)
        report = GasSimulationReport(
            report_id=report_id, generated_at=generated_at, report_sha256=digest, **report_base,
        )
        run_id = f"{report_id}-run"
        return GasSimulationResult(
            run_id=run_id, mode=mode, status=status, dataset_id=dataset_id,
            dataset_version=dataset_version, data_mode=data_mode, result=result,
            evidence_refs=_unique(evidence_refs), limitations=_unique(limitations), report=report,
        )

    def _synthetic_topology(self, request: GasSimulationRequest) -> GasSimulationResult:
        if request.dataset_id not in (None, GAS_DATASET_ID):
            raise HTTPException(409, "gas_simulation_dataset_conflict")
        try:
            replay = self.fixture_service.cuv_replay()
        except GasFixtureUnavailable:
            return self._result(
                mode="synthetic_topology", status="unavailable", dataset_id=GAS_DATASET_ID,
                dataset_version="unknown", data_mode="synthetic", result={}, evidence_refs=[],
                limitations=["GAS-M0 合成推演原件或哈希校验不可用。"], findings=[],
                unknowns=["无法读取合成网络、案例或回放版本。"],
            )
        if request.scenario_id and request.scenario_id != replay.scenario_id:
            return self._result(
                mode="synthetic_topology", status="not_found", dataset_id=replay.network_id,
                dataset_version=replay.schema_version, data_mode="synthetic", result={}, evidence_refs=[],
                limitations=["指定合成场景不在当前登记回放中。"], findings=[],
                unknowns=["未执行跨场景推断。"],
            )
        refs = list(_unique([
            f"gas-cuv-replay:sha256:{replay.topology_evidence.replay_content_sha256}",
            f"gas-network:sha256:{replay.topology_evidence.network_content_sha256}",
            f"gas-case:sha256:{replay.topology_evidence.case_content_sha256}",
            *(ref for event in replay.event_evidence for ref in event.evidence_refs),
        ]))
        payload = replay.model_dump(mode="json")
        findings = [f"GAS-M0 合成场景 {replay.scenario_id} 已完成确定性图连通回放。",
                    f"回放包含 {len(replay.plans)} 个方案，算法版本 {replay.algorithm_version}。"]
        unknowns = [item.reason for item in replay.unknown_metrics]
        return self._result(
            mode="synthetic_topology", status="partial", dataset_id=replay.network_id,
            dataset_version=replay.schema_version, data_mode="synthetic", result=payload,
            evidence_refs=refs, limitations=[replay.comparison_basis], findings=findings,
            unknowns=unknowns,
        )

    def _experiment_replay(self, principal: Principal, request: GasSimulationRequest) -> GasSimulationResult:
        if request.dataset_id not in (None, DRYAD_DATASET_ID):
            raise HTTPException(409, "gas_simulation_dataset_conflict")
        if not request.experiment_id:
            quality = self.public_data_service.dryad_quality(principal)
            result = quality.model_dump(mode="json")
            refs = list(quality.evidence_refs)
            return self._result(
                mode="experiment_replay", status="succeeded" if quality.status == "checked" else "partial",
                dataset_id=DRYAD_DATASET_ID, dataset_version=quality.version, data_mode="public_reference",
                result=result, evidence_refs=refs,
                limitations=list(quality.limitations), findings=[f"Dryad实验目录包含 {quality.file_count - 1} 个CSV，共 {quality.total_records} 条记录。"],
                unknowns=["实验观测未绑定重庆管段、测点或运行事件。"],
            )
        try:
            experiment = self.public_data_service.dryad_experiment(principal, request.experiment_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                return self._result(
                    mode="experiment_replay", status="not_found", dataset_id=DRYAD_DATASET_ID,
                    dataset_version="unknown", data_mode="public_reference", result={}, evidence_refs=[],
                    limitations=["指定 Dryad 实验文件不存在。"], findings=[], unknowns=["未读取其他实验文件。"],
                )
            raise
        version = self.public_data_service.dryad_quality(principal).version
        return self._result(
            mode="experiment_replay", status="succeeded", dataset_id=DRYAD_DATASET_ID,
            dataset_version=version, data_mode="public_reference", result=experiment.model_dump(mode="json"),
            evidence_refs=[experiment.evidence_ref],
            limitations=["这是确定性实验文件摘要回放，不是燃气管网物理仿真。"],
            findings=[f"{experiment.experiment_id} 包含 {experiment.record_count} 条观测，字段数为 {len(experiment.columns)}。"],
            unknowns=["缺少重庆资产关联、授权压力边界和专业泄漏模型。"],
        )

    def _historical_incident(self, principal: Principal, request: GasSimulationRequest) -> GasSimulationResult:
        if request.dataset_id not in (None, PHMSA_DATASET_ID):
            raise HTTPException(409, "gas_simulation_dataset_conflict")
        page = self.public_data_service.phmsa_incidents(
            principal, category=request.category, year_from=request.year_from,
            year_to=request.year_to, state=request.state, limit=request.limit,
        )
        return self._result(
            mode="historical_incident", status=page.status, dataset_id=PHMSA_DATASET_ID,
            dataset_version=page.version, data_mode="public_reference", result=page.model_dump(mode="json"),
            evidence_refs=list(page.evidence_refs), limitations=list(page.limitations),
            findings=[f"PHMSA查询返回 {page.total} 条符合条件的美国历史记录。"],
            unknowns=["历史事件不映射为重庆当前资产、实时告警或事故概率。"],
        )

    def _solver_benchmark(self, request: GasSimulationRequest) -> GasSimulationResult:
        if request.dataset_id not in (None, BENCHMARK_ID):
            raise HTTPException(409, "gas_simulation_dataset_conflict")
        report = run_public_benchmark()
        payload = report.model_dump(mode="json")
        binding = report.binding
        refs = [f"gas-cuv-benchmark:sha256:{binding.suite_sha256}"]
        refs.extend(f"gas-cuv-benchmark-input:{case.case_id}:sha256:{case.input_sha256}" for case in report.cases)
        if report.network_prototype is not None:
            refs.append(f"gas-cuv-benchmark-input:{report.network_prototype.case_id}:sha256:{report.network_prototype.input_sha256}")
        passed = report.progress.benchmark_status == "benchmark_passed"
        return self._result(
            mode="solver_benchmark", status="succeeded" if passed else "partial", dataset_id=BENCHMARK_ID,
            dataset_version=binding.suite_version, data_mode="synthetic", result=payload,
            evidence_refs=refs, limitations=[report.applicability, "benchmark_passed 不等于重庆真实管网校核通过。"],
            findings=[f"公开稳态基准状态为 {report.progress.benchmark_status}。"],
            unknowns=["缺少授权重庆管网参数、边界和现场对照。"],
        )

    def _physical_chongqing(self, request: GasSimulationRequest) -> GasSimulationResult:
        return self._result(
            mode="physical_chongqing", status="unavailable", dataset_id="gas-chongqing-authorized-pilot",
            dataset_version="unavailable", data_mode="unavailable", result={
                "validation_status": "pending_field_validation", "calculation": "unavailable",
            }, evidence_refs=[], limitations=list(_CAPABILITIES[-1].limitations), findings=[],
            unknowns=list(_CAPABILITIES[-1].required_inputs),
        )


def gas_simulation_router(service: GasSimulationService) -> APIRouter:
    router = APIRouter(prefix="/simulation", tags=["gas-simulation"])
    security = {"openapi_extra": {"security": [{"WaterToken": []}]}, "responses": COMMON_ERRORS}

    @router.get("/catalog", response_model=GasSimulationCatalogResponse, **security)
    def catalog(request: Request):
        principal = resolve_principal(request)
        return service.catalog(principal)

    @router.post("/run", response_model=GasSimulationResult, **security)
    def run(payload: GasSimulationRequest, request: Request):
        principal = resolve_principal(request)
        return service.run(principal, payload)

    return router


__all__ = [
    "GasSimulationCapability", "GasSimulationCatalogResponse", "GasSimulationRequest",
    "GasSimulationReport", "GasSimulationResult", "GasSimulationService", "gas_simulation_router",
]
