"""Structured read-only DTOs for the synthetic gas candidate.

The models are deliberately limited to the extracted graph and candidate
status. They do not introduce pressure, flow, leakage, restoration, or
field-operation semantics.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class GasLayout(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float
    unit: Literal["px"]
    coordinate_system: Literal["schematic"]


class GasNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str
    prototype_id: str
    node_type: Literal["junction", "regulator", "valve", "source"]
    name: str | None = None
    layout: GasLayout


class GasEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str
    pipe_id: str
    prototype_id: str
    from_node_id: str
    to_node_id: str
    direction: Literal["undirected_connectivity_only"]
    initial_state: Literal["enabled", "disabled"]


class GasPipe(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pipe_id: str
    edge_id: str
    prototype_id: str
    material: str
    diameter_mm: float = Field(ge=0)
    pressure_class: str
    asset_status: str


class GasValve(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valve_id: str
    node_id: str
    prototype_id: str
    cut_semantics: Literal["node_cut"]
    initial_state: Literal["open", "closed"]
    incident_edge_ids: list[str]


class GasSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    node_id: str
    prototype_id: str
    name: str
    enabled: bool
    pressure: float | None = None
    capacity: float | None = None
    physical_model_status: Literal["unsupported"]


class GasConsumer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumer_id: str
    prototype_node_id: str
    node_id: str
    consumer_kind: Literal["household", "business", "facility"]
    count: int = Field(ge=0)
    count_unit: Literal["household", "business", "facility"]
    facility_type: str | None = None
    origin: str
    individual_customer_records: bool


class GasService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str
    consumer_id: str
    node_ids: list[str]
    supply_rule: Literal["any", "all"]
    status: Literal["graph_reachable", "newly_graph_unreachable", "already_graph_unreachable"]
    physical_supply_status: Literal["unknown"]
    previously_graph_reachable: bool
    currently_graph_reachable: bool


class GasScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    name: str
    fault_edge_ids: list[str]
    origin: str
    fault_assumption: str


class GasIsolationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str
    scenario_id: str
    closed_valve_ids: list[str]
    disabled_edge_ids: list[str]
    semantics: Literal["node_cut_plus_explicit_unit_edges"]
    derivation: str


class GasConsumerCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    household: int = Field(ge=0)
    business: int = Field(ge=0)
    facility: int = Field(ge=0)


class GasTimelineState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    time_hours: float = Field(ge=0)
    kind: Literal["given_fault", "given_isolation", "synthetic_observation"]
    current_reachable_node_ids: list[str]
    current_unreachable_node_ids: list[str]
    normal_unreachable_node_ids: list[str]
    newly_unreachable_node_ids: list[str]
    services: list[GasService]
    newly_affected_service_ids: list[str]
    potentially_interrupted_consumers: GasConsumerCounts
    model_scope: Literal["graph_connectivity_only"]
    physical_supply_verified: Literal[False]


class GasCurrent(GasTimelineState):
    pass


class GasMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["available", "partial", "insufficient_data", "not_applicable", "unsupported"]
    value: float | int | None = None
    unit: str | None = None
    reason: str | None = None
    in_analysis_window: bool | None = None


class GasCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: int = Field(ge=0)
    edges: int = Field(ge=0)
    pipes: int = Field(ge=0)
    valves: int = Field(ge=0)
    sources: int = Field(ge=0)
    consumers: int = Field(ge=0)
    services: int = Field(ge=0)


class GasScoringContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["candidate", "unavailable"]
    rule_version: str
    business_validation: Literal["not_business_validated"]
    required_indicators: list[str]
    available_indicators: list[str]
    formula_status: Literal["candidate_arithmetic_only", "not_available"]
    h_status: Literal["unsupported_pending_business_definition"]
    p_semantics: str
    coverage_semantics: str
    note: str


class GasSafety(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_operation_instruction: Literal[False]
    safe_restoration_decision: Literal[False]
    physical_supply_verified: Literal[False]
    simulation_only: Literal[True]


class GasCaseResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["gas-case-view-0.1.0"]
    case_id: str
    network_id: str
    scenario_id: str
    plan_id: str
    data_mode: Literal["synthetic"]
    status: Literal["partial"]
    model_status: Literal["candidate"]
    validation_status: Literal["not_business_validated"]
    semantic: Literal["graph_connectivity_only"]
    algorithm_version: str
    analysis_window_hours: float = Field(gt=0)
    evidence: dict[str, str | None]
    counts: GasCounts
    nodes: list[GasNode]
    edges: list[GasEdge]
    pipes: list[GasPipe]
    valves: list[GasValve]
    sources: list[GasSource]
    consumers: list[GasConsumer]
    services: list[GasService]
    scenario: GasScenario
    isolation_plan: GasIsolationPlan
    timeline_events: list[GasTimelineState]
    current: GasCurrent
    metrics: dict[str, GasMetric]
    scoring: GasScoringContract
    safety: GasSafety


class GasCaseSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    network_id: str
    scenario_id: str
    plan_id: str
    data_mode: Literal["synthetic"]
    model_status: Literal["candidate"]
    validation_status: Literal["not_business_validated"]
    semantic: Literal["graph_connectivity_only"]
    counts: GasCounts


class GasCaseList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[GasCaseSummary]


DataMode = Literal["real_authorized", "synthetic", "unknown", "unavailable"]
SourceStatus = Literal["authorized", "synthetic_fixture", "unknown", "unavailable"]
ValidationStatus = Literal["business_validated", "not_business_validated", "unknown"]
GAS_DATASET_ID = "syn-gas-network-html54-0.1.0"
CollectionStatus = Literal["succeeded", "partial", "unknown", "unavailable"]
AssetType = Literal["pipe", "valve", "node", "source", "consumer", "regulator", "junction"]


class GasCoordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    coordinate_system: Literal["wgs84", "gcj02"]
    longitude: float
    latitude: float


class GasDistrict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    district_id: str
    name: str
    data_mode: DataMode
    source_status: SourceStatus
    geometry_available: bool
    limitation: str
    validation_status: ValidationStatus = "unknown"


class GasAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: str
    asset_type: AssetType
    name: str
    material: str | None = None
    diameter_mm: float | None = Field(default=None, ge=0)
    pressure_class: str | None = None
    district_id: str | None = None
    data_mode: DataMode
    source_status: SourceStatus
    validation_status: ValidationStatus = "unknown"
    location: GasCoordinate | None = None
    location_status: Literal["authorized", "not_available", "unknown"] = "not_available"
    status: Literal["unknown", "synthetic_reference"] = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)


class GasEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    title: str
    event_kind: Literal["given_fault", "given_isolation", "synthetic_observation"]
    severity: Literal["critical", "high", "medium", "low", "unknown"] = "unknown"
    state: Literal["synthetic_given", "open", "acknowledged", "closed"] = "synthetic_given"
    data_mode: DataMode
    source_status: SourceStatus
    validation_status: ValidationStatus = "unknown"
    district_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)
    closed_valve_ids: list[str] = Field(default_factory=list)
    location: GasCoordinate | None = None
    location_status: Literal["authorized", "not_available", "unknown"] = "not_available"
    risk_status: Literal["unavailable", "not_computed", "confirmed"] = "unavailable"
    physical_supply_status: Literal["unknown", "verified"] = "unknown"
    evidence_refs: list[str] = Field(default_factory=list)
    limitation: str


class DistrictResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CollectionStatus
    data_mode: DataMode
    source_status: SourceStatus
    items: list[GasDistrict] = Field(default_factory=list)
    total: int
    limitations: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = "unknown"


class AssetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CollectionStatus
    data_mode: DataMode
    source_status: SourceStatus
    items: list[GasAsset] = Field(default_factory=list)
    total: int
    limitations: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = "unknown"


class EventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: CollectionStatus
    data_mode: DataMode
    source_status: SourceStatus
    items: list[GasEvent] = Field(default_factory=list)
    total: int
    limitations: list[str] = Field(default_factory=list)
    validation_status: ValidationStatus = "unknown"


class GasErrorResponse(BaseModel):
    detail: str
