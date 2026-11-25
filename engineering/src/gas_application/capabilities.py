from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

CapabilityStatus = Literal["ready_synthetic","unavailable","unsupported"]
class GasCapability(BaseModel):
    model_config=ConfigDict(extra="forbid")
    capability_id: str
    version: str
    status: CapabilityStatus
    input_requirements: list[str]=Field(default_factory=list)
    evidence_refs: list[str]=Field(default_factory=list)
    scope: str
    validation_status: Literal["synthetic_validated","not_business_validated","unsupported"]="not_business_validated"
    limitations: list[str]=Field(default_factory=list)

class GasCapabilityResponse(BaseModel):
    model_config=ConfigDict(extra="forbid")
    data_mode: Literal["synthetic"]
    dataset_id: str
    validation_status: Literal["not_business_validated"]
    items: list[GasCapability]

def capability_catalog() -> GasCapabilityResponse:
    return GasCapabilityResponse(
        data_mode="synthetic",
        dataset_id="syn-gas-network-html54-0.1.0",
        validation_status="not_business_validated",
        items=[
            GasCapability(
                capability_id="graph_connectivity",
                version="gas-m0-graph/0.1.0",
                status="ready_synthetic",
                input_requirements=["gas54_network.json","gas54_case.json"],
                evidence_refs=["gas-m0:gas54_network.json","gas-m0:gas54_case.json"],
                scope="GAS-M0合成无向连通性",
                validation_status="synthetic_validated",
                limitations=["不代表物理供气、压力、流量或复气"],
            ),
            GasCapability(
                capability_id="isolation_candidate",
                version="gas-m0-node-cut/0.1.0",
                status="ready_synthetic",
                input_requirements=["closed_valve_ids","disabled_edge_ids"],
                evidence_refs=["gas-m0:gas54_case.json"],
                scope="给定隔离方案的候选拓扑影响",
                validation_status="synthetic_validated",
                limitations=["node_cut不是现场阀门控制指令"],
            ),
            GasCapability(
                capability_id="pir",
                version="unavailable",
                status="unsupported",
                scope="燃气风险概率",
                validation_status="unsupported",
                limitations=["没有经业务确认的PIR模型和真实输入"],
            ),
            GasCapability(
                capability_id="accumulation",
                version="unavailable",
                status="unsupported",
                scope="泄漏积聚/扩散",
                validation_status="unsupported",
                limitations=["不计算泄漏扩散或爆炸后果"],
            ),
            GasCapability(
                capability_id="risk_evolution",
                version="unavailable",
                status="unavailable",
                scope="风险演化",
                validation_status="not_business_validated",
                limitations=["真实监测数据和模型尚未授权"],
            ),
        ],
    )
