"""Input and validation registry for the registered GAS-M0 replay.

A pilot package does not grant access to a physical model. There is no physical
executor in this registry. Only the existing synthetic graph replay is enabled.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .agent_evidence import GasAgentCaseBinding, GasAgentEvidenceScope
from .cuv_benchmark import GasCuvModelProgress
from .weymouth import MODEL_ID, MODEL_VERSION
from .cuv_replay import GasCuvReplayReport
from .data_package import Digest


GasCuvCapabilityId = Literal[
    "graph_plan_comparison", "steady_pressure_flow", "supply_adequacy", "transient_recovery",
]
GRAPH_CAPABILITY: GasCuvCapabilityId = "graph_plan_comparison"


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GasCuvSourceBinding(GasAgentCaseBinding):
    replay_content_sha256: Digest


class GasCuvEngineeringInput(_Contract):
    input_id: str
    label: str
    fields: tuple[str, ...]
    source: str
    unit_time_basis: str
    status: Literal["verified_synthetic", "missing"]
    evidence_refs: tuple[str, ...] = ()


class GasCuvEngineeringCapability(_Contract):
    capability_id: GasCuvCapabilityId
    label: str
    scope: str
    method: str | None
    model_version: str | None
    status: Literal["ready_synthetic", "blocked"]
    validation_status: Literal["synthetic_validated", "not_business_validated"]
    required_input_ids: tuple[str, ...]
    missing_input_ids: tuple[str, ...]
    calibration_requirements: tuple[str, ...]
    validation_evidence_refs: tuple[str, ...]
    reasons: tuple[str, ...]
    solver_progress: GasCuvModelProgress | None = None


class GasCuvEngineeringAssessment(_Contract):
    contract_version: Literal["gas-cuv-engineering-input/1"] = "gas-cuv-engineering-input/1"
    evidence_scope: GasAgentEvidenceScope
    binding: GasCuvSourceBinding
    case_id: str
    scenario_id: str
    scenario_at: datetime
    plan_ids: tuple[str, ...]
    inputs: tuple[GasCuvEngineeringInput, ...]
    capabilities: tuple[GasCuvEngineeringCapability, ...]


# These inputs are satisfied only by validated synthetic fixtures. The same
# field names in a pilot package do not establish equivalence or a valid grant.
def _input(
    input_id: str, label: str, fields: tuple[str, ...], source: str, unit_time_basis: str,
) -> GasCuvEngineeringInput:
    return GasCuvEngineeringInput(
        input_id=input_id, label=label, fields=fields, source=source,
        unit_time_basis=unit_time_basis, status="missing",
    )


_INPUTS = (
    _input("topology", "节点与管段拓扑",
           ("node_id", "pipe_id", "from_node_id", "to_node_id", "edge_id", "source_node_id", "service_node_id"),
           "核验过版本的台账和连接关系。GAS-M0 使用 gas54_network.json",
           "稳定 ID，起终点需解析到同一有效快照；GIS 几何不作为连通判断"),
    _input("graph_states", "图状态与隔离约束",
           ("valve_id", "valve_node_id", "disabled_edge_ids", "closed_valve_ids", "disabled_source_ids"),
           "给定的设备连接与状态。GAS-M0 使用 gas54_network.json 和 gas54_case.json",
           "状态生效时点与拓扑版本一致；合成关阀节点仅用于图切断"),
    _input("scenario_plan", "场景与方案时序",
           ("case_id", "scenario_id", "scenario_at", "plan_id", "step.elapsed_hours", "step.kind", "fault_edge_ids"),
           "GAS-M0 案例及推演配置",
           "带时区的场景时点；相对小时递增并落在方案窗口内"),
    _input("pipe_physics", "正式管段物理与拓扑",
           ("authorized_network_revision", "pipe_id", "inner_diameter", "surveyed_length", "material",
            "roughness_reference", "elevation_profile", "pressure_class"),
           "运营方资产台账、测绘及专业模型参数。历史试点的 diameter_mm 仍需确认内径口径",
           "长度、高程、内径及粗糙度保留原单位与换算依据；高程附基准"),
    _input("device_characteristics", "设备连接与运行特性",
           ("device_id", "device_type", "from_node_id", "to_node_id", "direction", "state_history",
            "setting_history", "performance_reference", "operating_limits"),
           "阀门、调压设施和适用增压设备的台账、特性及运营记录",
           "状态和设置带有效时点；压力口径、连接方向和适用区间由运营方确认"),
    _input("gas_properties", "介质参数与计量口径",
           ("gas_property_reference", "temperature_basis", "volume_reference_conditions"),
           "气质资料与专业模型合同", "记录气质适用时段、温度口径和体积流量参考状态"),
    _input("boundary_conditions", "供需边界与负荷",
           ("node_id", "boundary_kind", "pressure_or_flow", "demand_profile", "effective_from", "effective_to"),
           "调度、计量及运营确认的气源和用气负荷",
           "保留压力表压或绝压、流量原单位与工况；边界时间使用带时区的时点"),
    _input("qualified_observations", "合格历史监测与时点",
           ("sensor_id", "node_or_asset_id", "observed_at", "metric", "value", "unit", "quality",
            "device_status", "calibration_reference"),
           "获准监测记录、设备校准记录与质量码字典",
           "带时区、采样间隔、质量码及测点位置；缺测值不补零"),
    _input("approval_and_calibration", "模型适用范围与独立校核",
           ("approval_ref", "scope_ref", "manifest_sha256", "method_id", "model_version",
            "calibration_case_reference", "independent_validation_reference", "approved_tolerances"),
           "受控授权登记、运营方、专业模型团队",
           "关联数据集及模型版本、区域、主体、资产、用途与有效期；复核记录可追溯"),
    _input("service_acceptance", "供气达标与服务对象口径",
           ("service_id", "node_id", "required_demand", "service_pressure_min", "approved_coverage_criteria"),
           "运营方服务对象、负荷和批准的达标口径",
           "与边界压力、流量单位和时段一致；核验供气指标分母"),
    _input("transient_state", "初始状态及工况时序",
           ("initial_state_reference", "boundary_history", "demand_history", "device_state_history", "engineering_constraints"),
           "授权的场景初始条件、运行测点和设备操作记录",
           "以同一时钟对齐初始状态、边界及设备变化；明确采样和仿真时间口径"),
    _input("restoration_acceptance", "恢复判据与现场核验",
           ("approved_restoration_criteria", "verified_event_timeline", "field_acceptance_reference"),
           "运营方已核验事件和独立复核记录",
           "明确物理指标的验证条件及现场口径，保留原始时间和证据号"),
)

_GRAPH_INPUT_IDS = ("topology", "graph_states", "scenario_plan")
_STEADY_INPUT_IDS = (
    "pipe_physics", "device_characteristics", "gas_properties", "boundary_conditions",
    "qualified_observations", "approval_and_calibration",
)
_SUPPLY_INPUT_IDS = (*_STEADY_INPUT_IDS, "service_acceptance")

# Each entry carries the minimum prerequisites for a future executor.
# No request or imported field changes the availability of a registered method.
_CAPABILITIES: dict[GasCuvCapabilityId, tuple[str, str, tuple[str, ...], tuple[str, ...]]] = {
    GRAPH_CAPABILITY: (
        "隔离与恢复方案的图连通比较", "固定 GAS-M0 合成拓扑、案例及给定方案时序",
        _GRAPH_INPUT_IDS, ("核对拓扑与时序", "核对每步图约束、基线可达性及方案统计"),
    ),
    "steady_pressure_flow": (
        "稳态压力与流量", "经签核的拓扑、设施与供需边界，限模型已验证工况",
        _STEADY_INPUT_IDS, ("独立压力流量测点与快照", "质量守恒、收敛、残差及误差条件签核"),
    ),
    "supply_adequacy": (
        "供气能力与达标评价", "已验证工况和服务对象口径下的供需与压力评价",
        _SUPPLY_INPUT_IDS, ("已校核的压力和流量结果", "经签核的服务压力、负荷及覆盖判据"),
    ),
    "transient_recovery": (
        "瞬态恢复与现场时长校核", "获准工况内的瞬态模型验证及独立现场核验",
        (*_SUPPLY_INPUT_IDS, "transient_state", "restoration_acceptance"),
        ("压力、流量和设备变化时序的独立对照", "恢复验收和适用工况签核"),
    ),
}


def supports_graph_run(capability_id: GasCuvCapabilityId) -> bool:
    return capability_id == GRAPH_CAPABILITY


def capability_status(
    capability_id: GasCuvCapabilityId, report: GasCuvReplayReport | None = None,
) -> GasCuvEngineeringCapability:
    label, scope, required, validation = _CAPABILITIES[capability_id]
    verified = supports_graph_run(capability_id) and report is not None
    evidence = topology_refs(report) if verified else ()
    solver = None
    if capability_id != GRAPH_CAPABILITY:
        solver = GasCuvModelProgress(implementation_status="unsupported", input_status="missing_inputs")
        if capability_id == "steady_pressure_flow":
            solver = GasCuvModelProgress(
                implementation_status="candidate", input_status="missing_inputs", model_id=MODEL_ID, model_version=MODEL_VERSION,
            )
    return GasCuvEngineeringCapability(
        capability_id=capability_id, label=label, scope=scope,
        method="既有无向图可达性、隔离约束与分阶段比较" if verified else None,
        model_version=report.algorithm_version if verified else None,
        status="ready_synthetic" if verified else "blocked",
        validation_status="synthetic_validated" if verified else "not_business_validated",
        required_input_ids=required, missing_input_ids=() if verified else required,
        calibration_requirements=validation,
        validation_evidence_refs=(*evidence, "repository:engineering/tests/test_gas_cuv_replay.py") if verified else (),
        reasons=(("结果只表示给定合成场景的图连通影响。",) if verified else
                 ("当前管网范围缺少适用的模型、授权工程输入及独立校核证据，工程结果保持未知。",)),
        solver_progress=solver,
    )


def topology_refs(report: GasCuvReplayReport) -> tuple[str, str, str]:
    source = report.topology_evidence
    return (
        f"gas-cuv-replay:sha256:{source.replay_content_sha256}",
        f"gas-network:sha256:{source.network_content_sha256}",
        f"gas-case:sha256:{source.case_content_sha256}",
    )


def engineering_assessment(report: GasCuvReplayReport) -> GasCuvEngineeringAssessment:
    source = report.topology_evidence
    replay_ref, network_ref, case_ref = topology_refs(report)
    verified_inputs = {
        "topology": (network_ref,), "graph_states": (network_ref, case_ref), "scenario_plan": (case_ref, replay_ref),
    }
    return GasCuvEngineeringAssessment(
        evidence_scope=GasAgentEvidenceScope(
            scope="gas_m0_graph_cuv", dataset_id=report.network_id, dataset_version=report.schema_version,
            data_mode=report.data_mode, source_sha256=source.replay_content_sha256,
        ),
        binding=GasCuvSourceBinding(
            network_content_sha256=source.network_content_sha256, case_content_sha256=source.case_content_sha256,
            replay_content_sha256=source.replay_content_sha256,
        ),
        case_id=report.case_id, scenario_id=report.scenario_id, scenario_at=report.scenario_at,
        plan_ids=tuple(plan.plan_id for plan in report.plans),
        inputs=tuple(
            item.model_copy(update={"status": "verified_synthetic", "evidence_refs": verified_inputs[item.input_id]})
            if item.input_id in verified_inputs else item
            for item in _INPUTS
        ),
        capabilities=tuple(capability_status(key, report) for key in _CAPABILITIES),
    )


def engineering_facts(assessment: GasCuvEngineeringAssessment) -> list[str]:
    inputs = {item.input_id: item.label for item in assessment.inputs}
    facts = []
    for item in assessment.capabilities:
        if item.status == "ready_synthetic":
            facts.append(f"{item.label}仅在合成算例可用，方法版本 {item.model_version}，范围为{item.scope}。")
        else:
            missing = "、".join(inputs[key] for key in item.missing_input_ids)
            facts.append(f"{item.label}未就绪，缺少{missing}。{' '.join(item.reasons)}")
    return facts
