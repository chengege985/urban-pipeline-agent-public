/* eslint-disable */
/**
 * 由 scripts/generate-platform-types.mjs 自动生成，请勿手工修改。
 * 来源：engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json
 * 来源 SHA-256：acc296bc5b71476abf035de628a79f65260dbf1d549a7f58e9191b374fba622e
 * 来源版本：0.1.1；schema 数量：250
 * 说明：只有声明了响应 schema 的接口会产出类型；未声明的接口在适配层显式处理。
 */
/** 措施改变明确的网络状态，备用供水单独评价，不从图断连数中直接减去。 */
export interface Action {
  action_id: string;
  label: string;
  fault_edge_ids: Array<string>;
  cost_fen?: number | null;
  effective_after_hours?: number | null;
  evidence_ref?: string | null;
  restore_edge_ids?: Array<string>;
  open_valve_ids?: Array<string>;
  close_valve_ids?: Array<string>;
  resource_requirements?: Record<string, unknown> | null;
  requires_ids?: Array<string>;
  conflicts_with?: Array<string>;
  backup_service_ids?: Array<string>;
  backup_capacity_m3h?: number | null;
  backup_demand_m3h?: number | null;
  backup_duration_hours?: number | null;
}

export interface ActionPlan {
  plan_id: string;
  label: string;
  action_ids: Array<string>;
  offline_effect?: OfflineEffect | null;
}

export interface AgentChatRequest {
  question: string;
  snapshot_id?: string | null;
  pipe_id?: string | null;
  batch_refs?: Array<string>;
  scenario?: ScenarioRequest | null;
  decision?: DecisionRequest | null;
  thread_id?: string | null;
}

export interface AgentResumeRequest {
  interrupt_id: string;
  action: "clarify" | "accept" | "reject" | "revise" | "cancel";
  values?: Record<string, JsonValue>;
  reason?: string;
  expected_revision?: number;
}

export interface AlgorithmAvailability {
  capability_id: string;
  semantics: string;
  version?: string | null;
  available: boolean;
  reason?: string | null;
}

export interface AlgorithmReference {
  capability_id: string;
  semantics: string;
  version: string;
  recovery_summary_version?: string | null;
  criterion_id?: string | null;
  criterion_version?: string | null;
  validation_status: string;
}

export interface AlgorithmSummary {
  capability_id: string;
  semantics: string;
  version?: string | null;
}

export interface Assessment {
  pipe_id: string;
  as_of: string;
  model_family?: "legacy_demo";
  model_version?: string;
  legacy_result: LegacyResult;
  indicator_scores?: Record<string, number>;
  properties?: Record<string, JsonValue>;
  monitoring?: Record<string, JsonValue>;
  main_risk?: string;
  data_issues?: Array<string>;
  evidence_refs: Array<string>;
}

export interface AssessmentCoverage {
  total_pipes: number;
  scored_pipes: number;
  /** 有合法评分记录的管段占比 0—1；不是模型预测置信度 */
  coverage_ratio: number;
  missing_pipe_ids: Array<string>;
  incompatible_pipe_ids: Array<string>;
  status: "complete" | "partial" | "missing";
  coverage_semantics?: "assessment_coverage_not_model_confidence";
  note?: string;
}

/** `GET /api/v1/assessments`：按快照与管段过滤后的评分行。 */
export interface AssessmentEnvelope {
  snapshot_id: string;
  data_mode: "fixture_replay" | "synthetic" | "real";
  as_of: string;
  total: number;
  items: Array<Assessment>;
}

/** Known upstream property names mapped for display only; units are unverified. */
export interface AssetHints {
  material_hint?: string | null;
  /** 上游列“管径”，单位 mm；仅展示提示，未重新核实 */
  diameter_mm_hint?: number | null;
  install_year_hint?: number | null;
  risk_point_count_hint?: number | null;
}

export interface AssetResponse {
  status: "succeeded" | "partial" | "unknown" | "unavailable";
  data_mode: "real_authorized" | "synthetic" | "unknown" | "unavailable";
  source_status: "authorized" | "synthetic_fixture" | "unknown" | "unavailable";
  items?: Array<GasAsset>;
  total: number;
  limitations?: Array<string>;
  validation_status?: "business_validated" | "not_business_validated" | "unknown";
}

export interface AssistantRequest {
  snapshot_id: string;
  question: string;
  as_of?: string | null;
  pipe_id?: string | null;
  fault_edge_id?: string | null;
  isolation_plan_id?: string | null;
  plan_ids?: Array<string>;
  budget_fen?: number | null;
  resource_capacities?: Record<string, unknown>;
  horizon_hours?: number;
  mandatory_action_ids?: Array<string>;
}

export interface AvailabilityEvent {
  at_hours: number;
  changes: Record<string, boolean>;
}

export interface CapabilitiesResponse {
  scoring: string;
  simulation: string;
  decision: string;
  llm_mode: "mock" | "openai";
  formal_scoring: string;
  professional_prediction: string;
  hydraulic: string;
  internal_cuv: string;
  real_dispatch: string;
  note: string;
  platform_version: string;
  datasets: Array<CapabilityDataset>;
  data_modes: Array<"fixture_replay" | "synthetic" | "real">;
  algorithms: Array<AlgorithmSummary>;
}

export interface CapabilityCheckResult {
  capability_id?: string | null;
  status: string;
  /** 原有字段：仅当请求的全部输出完整可得时为 true；false 不代表所有指标都不可计算，请读取 field_availability。 */
  can_compute: boolean;
  calculation_status?: string | null;
  supported_outputs?: Array<string>;
  unsupported_outputs?: Array<string>;
  available_outputs?: Array<string>;
  unavailable_outputs?: Array<string>;
  available_alternative?: string | null;
  missing_items?: Array<unknown>;
  evidence_refs?: Array<string>;
  registry_version: string;
  algorithm_version?: string | null;
  requested_semantics?: string | null;
  validation_status?: string;
  authorization_checked?: boolean;
  validation_mode?: string | null;
}

/** `/api/v1/capabilities` 内的数据集摘要：比 `SnapshotSummary` 少 sha256 与 created_at。 */
export interface CapabilityDataset {
  snapshot_id: string;
  name: string;
  data_mode: "fixture_replay" | "synthetic" | "real";
  source_status: "source_unknown" | "constructed" | "declared_real";
  as_of: string;
  counts: SnapshotCounts;
  validation_status?: "not_business_validated";
}

export interface Criterion {
  metric: "remaining_disconnected_services" | "total_cost_fen" | "restoration_hours" | "backup_covered_critical_services";
  direction: "min" | "max";
  lower: number;
  upper: number;
  weight: number;
}

export interface CuvEventEvidence {
  event_id: string;
  title: string;
  event_kind: "given_fault" | "given_isolation" | "synthetic_observation";
  asset_ids: Array<string>;
  elapsed_hours: number;
  replay_at: string;
  source_sha256: string;
  evidence_refs: Array<string>;
}

export interface CuvMonitoringContext {
  status: "available" | "unavailable";
  as_of: string;
  dataset_id?: string | null;
  dataset_version?: string | null;
  manifest_sha256?: string | null;
  policy_id?: string | null;
  source?: MonitoringSourceEvidence | null;
  channels: Array<CuvMonitoringEvidence>;
}

export interface CuvMonitoringEvidence {
  asset_id: string;
  sensor_id: string;
  metric: "pressure" | "flow";
  unit: string;
  state: "observing" | "normal" | "limit_exceeded" | "data_missing";
  relation: "fault_asset" | "isolation_asset";
  last_usable_at: string | null;
  transition: MonitoringEvent | null;
}

export interface DatasetAction {
  action_id: string;
  label: string;
  fault_edge_ids: Array<string>;
  cost_fen?: number | null;
  effective_after_hours?: number | null;
  evidence_ref?: string | null;
  restore_edge_ids?: Array<string>;
  open_valve_ids?: Array<string>;
  close_valve_ids?: Array<string>;
  resource_requirements?: Record<string, number> | null;
  requires_ids?: Array<string>;
  conflicts_with?: Array<string>;
  backup_service_ids?: Array<string>;
}

export interface DatasetEdge {
  edge_id: string;
  pipe_id: string;
  from_node: string;
  to_node: string;
  direction?: "both" | "forward" | "reverse" | "unknown";
  state?: "open" | "closed" | "unknown";
  is_valve?: boolean;
  operable?: boolean | null;
}

export interface DatasetIsolationPlan {
  plan_id: string;
  fault_edge_ids: Array<string>;
  valve_ids?: Array<string>;
  evidence_ref: string;
}

export interface DatasetMeta {
  dataset_id: string;
  snapshot_id: string;
  name: string;
  schema_version: string;
  data_mode: "fixture_replay" | "synthetic" | "real";
  source_status: string;
  as_of: string;
  sha256: string;
  created_at: string;
  source_refs: Array<string>;
  platform_version: string;
  display_coordinates_available?: boolean;
  coordinate_system?: "schematic" | "wgs84" | "local" | null;
}

export interface DatasetNode {
  node_id: string;
  label?: string;
  source?: boolean;
  active?: boolean | null;
  boundary?: boolean;
  boundary_confirmed?: boolean;
  /** 仅示意布局坐标，不是GIS经度 */
  x?: number | null;
  /** 仅示意布局坐标，不是GIS纬度 */
  y?: number | null;
}

export interface DatasetPipe {
  pipe_id: string;
  name?: string | null;
  from_node_id?: string | null;
  to_node_id?: string | null;
  properties?: Record<string, unknown>;
  asset_hints?: AssetHints;
  score?: LegacyScore | null;
  score_issue?: string | null;
}

export interface DatasetPlan {
  plan_id: string;
  label: string;
  action_ids: Array<string>;
  offline_effect?: Record<string, unknown> | null;
}

export interface DatasetReference {
  dataset_id: string;
  snapshot_id: string;
  data_mode: string;
  source_status: string;
  as_of: string;
  data_version: string;
  schema_version: string;
}

export interface DatasetService {
  service_id: string;
  label: string;
  node_ids: Array<string>;
  supply_rule: "any" | "all";
  count_value?: number | null;
  count_unit: "household" | "person" | "facility";
  critical?: boolean;
}

export interface DatasetView {
  status: "succeeded" | "partial";
  meta: DatasetMeta;
  assessment_coverage: AssessmentCoverage;
  pipes: Array<DatasetPipe>;
  nodes: Array<DatasetNode>;
  edges: Array<DatasetEdge>;
  services: Array<DatasetService>;
  actions: Array<DatasetAction>;
  plans: Array<DatasetPlan>;
  isolation_plans: Array<DatasetIsolationPlan>;
  algorithms: Array<AlgorithmAvailability>;
  formal_scoring_available?: boolean;
  missing_items: Array<string>;
  limitations: Array<string>;
  evidence_ids: Array<string>;
}

export interface DecisionRequest {
  snapshot_id: string;
  as_of: string;
  fault_edge_id: string;
  isolation_plan_id: string;
  horizon_hours?: number;
  plan_ids: Array<string>;
  budget_fen: number;
  resource_capacities?: Record<string, unknown>;
  mandatory_action_ids?: Array<string>;
}

export interface DistrictResponse {
  status: "succeeded" | "partial" | "unknown" | "unavailable";
  data_mode: "real_authorized" | "synthetic" | "unknown" | "unavailable";
  source_status: "authorized" | "synthetic_fixture" | "unknown" | "unavailable";
  items?: Array<GasDistrict>;
  total: number;
  limitations?: Array<string>;
  validation_status?: "business_validated" | "not_business_validated" | "unknown";
}

export interface DryadExperimentSummary {
  experiment_id: string;
  filename: string;
  member_sha256: string;
  member_md5: string;
  bytes: number;
  record_count: number;
  columns: Array<string>;
  missing_counts?: Record<string, number>;
  sample_rows?: Array<Record<string, string>>;
  evidence_ref: string;
}

export interface DryadQualityResponse {
  dataset_id?: "US-DRYAD-CH4";
  version: string;
  status: "checked" | "partial" | "unavailable";
  file_count: number;
  total_records: number;
  columns_consistent: boolean;
  experiments?: Array<DryadExperimentSummary>;
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
}

export interface Edge {
  edge_id: string;
  pipe_id: string;
  from_node: string;
  to_node: string;
  direction?: "both" | "forward" | "reverse" | "unknown";
  state?: "open" | "closed" | "unknown";
  is_valve?: boolean;
  operable?: boolean | null;
}

export interface ErrorResponse {
  detail: string;
}

export interface EventResponse {
  status: "succeeded" | "partial" | "unknown" | "unavailable";
  data_mode: "real_authorized" | "synthetic" | "unknown" | "unavailable";
  source_status: "authorized" | "synthetic_fixture" | "unknown" | "unavailable";
  items?: Array<GasEvent>;
  total: number;
  limitations?: Array<string>;
  validation_status?: "business_validated" | "not_business_validated" | "unknown";
}

export interface FeedbackRecord {
  run_id: string;
  review_status: "accepted" | "rejected" | "revised";
  reviewer: string;
  reason_type: "fact" | "constraint" | "goal" | "explanation" | "other";
  reason: string;
  expected_revision: number;
  feedback_id: string;
  created_at: string;
  revision: number;
  actor_identity: string;
  execution_status: "not_dispatched";
}

export interface FeedbackRequest {
  run_id: string;
  review_status: "accepted" | "rejected" | "revised";
  reviewer: string;
  reason_type: "fact" | "constraint" | "goal" | "explanation" | "other";
  reason: string;
  expected_revision?: number;
}

/** Per-output availability; do not collapse into a single can_compute flag. */
export interface FieldAvailability {
  field: string;
  status: "available" | "partial" | "missing" | "unsupported" | "not_applicable" | "not_checked";
  in_scope?: boolean;
  missing_items?: Array<unknown>;
  unsupported_reason?: string | null;
}

export interface GasAgentCaseBinding {
  network_content_sha256: string;
  case_content_sha256: string;
}

/** Digest of the source for this scope, never a join key by itself. */
export interface GasAgentEvidenceScope {
  scope: "gas_m0_case" | "gas_m0_monitoring" | "gas_m0_graph_cuv" | "pilot_history" | "public_reference_benchmark";
  dataset_id: string;
  dataset_version: string;
  data_mode: "synthetic" | "real_authorized";
  source_sha256: string;
}

export interface GasAgentHealthResponse {
  enabled?: true;
  model_status?: "unavailable" | "starting" | "ready";
  parser_source?: "local_rule_not_llm" | "qwen_intent";
  provider_configured?: boolean;
  tools_status?: "ready";
  read_only?: true;
  status?: "unavailable" | "starting" | "ready";
  reason: string;
}

export interface GasAgentQueryRequest {
  question: string;
  case_id?: string | null;
  asset_id?: string | null;
  event_id?: string | null;
  district_id?: string | null;
  tool_hint?: "asset.query" | "event.query" | "map.locate" | "capabilities.query" | "event.explain" | "cuv.query" | "cuv.engineering.query" | "cuv.benchmark.query" | "monitoring.query" | "phmsa.incident.query" | "dryad.experiment.query" | "public.report.query" | "simulation.query" | "gaslib.quality.query" | null;
  cuv_at?: string | null;
  monitoring_ref?: GasObservationRef | null;
  cuv_draft?: GasCuvDraftRequest | null;
  case_binding?: GasAgentCaseBinding | null;
  monitoring_policy_id?: string | null;
  cuv_replay_sha256?: string | null;
  cuv_comparison_binding?: GasCuvQueryBinding | null;
  cuv_capability?: "graph_plan_comparison" | "steady_pressure_flow" | "supply_adequacy" | "transient_recovery" | null;
  benchmark_binding?: GasCuvBenchmarkBinding | null;
}

export interface GasAgentQueryResponse {
  request_id: string;
  status: "succeeded" | "partial" | "unavailable" | "not_found";
  data_mode: "synthetic" | "public_reference";
  dataset_id: "syn-gas-network-html54-0.1.0" | "syn-gas-public-weymouth-v1" | "US-PHMSA-INCIDENT" | "US-DRYAD-CH4" | "DE-GASLIB";
  model_status: "unavailable" | "ready";
  parser_source: "local_rule_not_llm" | "qwen_intent";
  intent: "asset_query" | "event_query" | "map_locate" | "capabilities_query" | "event_explain" | "cuv_query" | "cuv_engineering_query" | "cuv_benchmark_query" | "monitoring_query" | "phmsa_query" | "dryad_query" | "public_report_query" | "simulation_query" | "gaslib_quality_query" | "clarify";
  tools: Array<GasAgentToolResult>;
  model_id?: string | null;
  model_calls?: number;
  model_elapsed_ms?: number;
  model_usage?: Record<string, number>;
  elapsed_ms?: number;
  error_code?: string | null;
  explanation?: string | null;
  explanation_source?: "none" | "qwen_evidence_selection";
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
  evidence_scope?: GasAgentEvidenceScope | null;
  reference_mode?: "none" | "public_reference";
  reference_corpus_id?: string | null;
}

export interface GasAgentToolResult {
  tool_name: "asset.query" | "event.query" | "map.locate" | "capabilities.query" | "event.explain" | "cuv.query" | "cuv.engineering.query" | "cuv.benchmark.query" | "monitoring.query" | "phmsa.incident.query" | "dryad.experiment.query" | "public.report.query" | "simulation.query" | "gaslib.quality.query";
  tool_version?: "gas-tools-0.1.0";
  status: "succeeded" | "partial" | "unavailable" | "not_found";
  data?: Record<string, unknown>;
  evidence_refs?: Array<string>;
  limitation: string;
  elapsed_ms?: number;
  parameters?: Record<string, string | null>;
  evidence_scope?: GasAgentEvidenceScope | null;
  monitoring_scope?: GasAgentEvidenceScope | null;
  cuv_comparison_binding?: GasCuvQueryBinding | null;
  engineering_capability?: GasCuvEngineeringCapability | null;
  engineering_assessment?: GasCuvEngineeringAssessment | null;
  benchmark_report?: GasCuvBenchmarkReport | null;
}

export interface GasAsset {
  asset_id: string;
  asset_type: "pipe" | "valve" | "node" | "source" | "consumer" | "regulator" | "junction";
  name: string;
  material?: string | null;
  diameter_mm?: number | null;
  pressure_class?: string | null;
  district_id?: string | null;
  data_mode: "real_authorized" | "synthetic" | "unknown" | "unavailable";
  source_status: "authorized" | "synthetic_fixture" | "unknown" | "unavailable";
  validation_status?: "business_validated" | "not_business_validated" | "unknown";
  location?: GasCoordinate | null;
  location_status?: "authorized" | "not_available" | "unknown";
  status?: "unknown" | "synthetic_reference";
  evidence_refs?: Array<string>;
}

export interface GasCapability {
  capability_id: string;
  version: string;
  status: "ready_synthetic" | "unavailable" | "unsupported";
  input_requirements?: Array<string>;
  evidence_refs?: Array<string>;
  scope: string;
  validation_status?: "synthetic_validated" | "not_business_validated" | "unsupported";
  limitations?: Array<string>;
}

export interface GasCapabilityResponse {
  data_mode: "synthetic";
  dataset_id: string;
  validation_status: "not_business_validated";
  items: Array<GasCapability>;
}

export interface GasCaseList {
  items: Array<GasCaseSummary>;
}

export interface GasCaseResponse {
  schema_version: "gas-case-view-0.1.0";
  case_id: string;
  network_id: string;
  scenario_id: string;
  plan_id: string;
  data_mode: "synthetic";
  status: "partial";
  model_status: "candidate";
  validation_status: "not_business_validated";
  semantic: "graph_connectivity_only";
  algorithm_version: string;
  analysis_window_hours: number;
  evidence: Record<string, string | null>;
  counts: GasCounts;
  nodes: Array<GasNode>;
  edges: Array<GasEdge>;
  pipes: Array<GasPipe>;
  valves: Array<GasValve>;
  sources: Array<GasSource>;
  consumers: Array<GasConsumer>;
  services: Array<GasService>;
  scenario: GasScenario;
  isolation_plan: GasIsolationPlan;
  timeline_events: Array<GasTimelineState>;
  current: GasCurrent;
  metrics: Record<string, GasMetric>;
  scoring: GasScoringContract;
  safety: GasSafety;
}

export interface GasCaseSummary {
  case_id: string;
  network_id: string;
  scenario_id: string;
  plan_id: string;
  data_mode: "synthetic";
  model_status: "candidate";
  validation_status: "not_business_validated";
  semantic: "graph_connectivity_only";
  counts: GasCounts;
}

export interface GasConsumer {
  consumer_id: string;
  prototype_node_id: string;
  node_id: string;
  consumer_kind: "household" | "business" | "facility";
  count: number;
  count_unit: "household" | "business" | "facility";
  facility_type?: string | null;
  origin: string;
  individual_customer_records: boolean;
}

export interface GasConsumerCounts {
  household: number;
  business: number;
  facility: number;
}

export interface GasCoordinate {
  coordinate_system: "wgs84" | "gcj02";
  longitude: number;
  latitude: number;
}

export interface GasCounts {
  nodes: number;
  edges: number;
  pipes: number;
  valves: number;
  sources: number;
  consumers: number;
  services: number;
}

export interface GasCurrent {
  event_id: string;
  time_hours: number;
  kind: "given_fault" | "given_isolation" | "synthetic_observation";
  current_reachable_node_ids: Array<string>;
  current_unreachable_node_ids: Array<string>;
  normal_unreachable_node_ids: Array<string>;
  newly_unreachable_node_ids: Array<string>;
  services: Array<GasService>;
  newly_affected_service_ids: Array<string>;
  potentially_interrupted_consumers: GasConsumerCounts;
  model_scope: "graph_connectivity_only";
  physical_supply_verified: false;
}

export interface GasCuvBenchmarkBinding {
  suite_id: string;
  suite_version: string;
  model_version: string;
  suite_sha256: string;
}

export interface GasCuvBenchmarkCaseResult {
  case_id: string;
  label: string;
  source_input: WeymouthSourceInput;
  input_source: GasCuvBenchmarkInputSource;
  expected: GasCuvBenchmarkExpected;
  input: WeymouthInput;
  input_sha256: string;
  solve: WeymouthRun;
  unit: "MMSCFD" | "psia" | "in";
  absolute_error: number | null;
  relative_error_percent: number | null;
  forward_check: GasCuvForwardCheck | null;
  passed: boolean;
}

export interface GasCuvBenchmarkComparison {
  case_a: string;
  case_b: string;
  outlet_delta_psia: number;
  flow_delta_mmscfd: number;
  basis?: "same_boundary_except_diameter";
}

export interface GasCuvBenchmarkExpected {
  metric: "flow_mmscfd" | "outlet_psia" | "internal_diameter_in";
  kind: "published_result" | "published_boundary";
  origin: "literature_result" | "third_party_software" | "boundary_target";
  value: number;
  tolerance: number;
  source_id: string;
  locator: string;
  tolerance_basis: string;
}

export interface GasCuvBenchmarkInputSource {
  source_id: string;
  locator: string;
  notes: string;
}

export interface GasCuvBenchmarkReference {
  id: string;
  title: string;
  url: string;
}

export interface GasCuvBenchmarkReport {
  data_mode?: "synthetic";
  scope?: "public_reference_benchmark";
  binding: GasCuvBenchmarkBinding;
  progress: GasCuvModelProgress;
  applicability?: string;
  equation_reference_url?: string;
  references: Array<GasCuvBenchmarkReference>;
  cases: Array<GasCuvBenchmarkCaseResult>;
  comparison: GasCuvBenchmarkComparison | null;
  comparison_unavailable_reason?: string | null;
  network_prototype?: GasCuvNetworkPrototype | null;
}

export interface GasCuvCollaborationAuditRecord {
  revision: number;
  input_sha256: string;
  criteria: GasCuvCriteria;
  comparison: GasCuvCriteriaComparison;
  actor_id: string;
  occurred_at: string;
  previous_criteria: GasCuvCriteria;
  feedback: string;
  monitoring_ref?: GasObservationRef | null;
}

export interface GasCuvCollaborationRequest {
  expected_revision: number;
  input_sha256: string;
  criteria: GasCuvCriteria;
  confirmed: true;
  feedback: string;
  monitoring_ref?: GasObservationRef | null;
}

export interface GasCuvCollaborationSnapshot {
  revision: number;
  input_sha256: string;
  criteria: GasCuvCriteria;
  comparison: GasCuvCriteriaComparison;
  case_id: string;
  scenario_id: string;
  scenario_at: string;
  data_mode?: "synthetic";
  validation_status?: "not_business_validated";
  persistence: "process_memory" | "sqlite_local";
  topology_evidence: GasCuvTopologyEvidence;
  audit: Array<GasCuvCollaborationAuditRecord>;
  unknown_metrics: Array<GasCuvUnknownMetric>;
  missing_items: Array<GasCuvMissingItem>;
}

export interface GasCuvConstraint {
  code: string;
  status: "met" | "blocked" | "assumed" | "unknown" | "not_applicable";
  explanation: string;
}

export interface GasCuvCriteria {
  objective: "min_affected_service_hours" | "min_topology_restoration_hours";
  max_peak_affected_services: number | null;
  restore_by_hours: number | null;
}

export interface GasCuvCriteriaComparison {
  status: "ranked" | "no_eligible_plan";
  leading_plan_ids: Array<string>;
  plans: Array<GasCuvRankedPlan>;
}

export interface GasCuvDraftRequest {
  expected_revision: number;
  input_sha256: string;
  criteria: GasCuvCriteria;
}

export interface GasCuvEngineeringAssessment {
  contract_version?: "gas-cuv-engineering-input/1";
  evidence_scope: GasAgentEvidenceScope;
  binding: GasCuvSourceBinding;
  case_id: string;
  scenario_id: string;
  scenario_at: string;
  plan_ids: Array<string>;
  inputs: Array<GasCuvEngineeringInput>;
  capabilities: Array<GasCuvEngineeringCapability>;
}

export interface GasCuvEngineeringCapability {
  capability_id: "graph_plan_comparison" | "steady_pressure_flow" | "supply_adequacy" | "transient_recovery";
  label: string;
  scope: string;
  method: string | null;
  model_version: string | null;
  status: "ready_synthetic" | "blocked";
  validation_status: "synthetic_validated" | "not_business_validated";
  required_input_ids: Array<string>;
  missing_input_ids: Array<string>;
  calibration_requirements: Array<string>;
  validation_evidence_refs: Array<string>;
  reasons: Array<string>;
  solver_progress?: GasCuvModelProgress | null;
}

export interface GasCuvEngineeringInput {
  input_id: string;
  label: string;
  fields: Array<string>;
  source: string;
  unit_time_basis: string;
  status: "verified_synthetic" | "missing";
  evidence_refs?: Array<string>;
}

export interface GasCuvForwardCheck {
  expected_mmscfd: number;
  recalculated_mmscfd: number | null;
  absolute_error_mmscfd: number | null;
  tolerance_mmscfd: number;
  passed: boolean;
}

export interface GasCuvMissingItem {
  code: string;
  description: string;
}

export interface GasCuvModelProgress {
  implementation_status: "unsupported" | "candidate";
  input_status: "missing_inputs" | "complete";
  benchmark_status?: "not_run" | "failed" | "benchmark_passed";
  field_validation_status?: "pending_field_validation" | "business_validated";
  model_id?: string | null;
  model_version?: string | null;
}

export interface GasCuvNetworkCheck {
  id: "split_pressure" | "recombine_pressure" | "outlet_pressure" | "branch_one" | "branch_two";
  label: string;
  unit: "psig" | "MMSCFD";
  literature: number;
  third_party: number;
  tolerance: number;
  solved: number | null;
  absolute_error: number | null;
  passed: boolean;
}

export interface GasCuvNetworkPrototype {
  case_id: string;
  label: string;
  input: SeriesParallelInput;
  input_sha256: string;
  input_source: GasCuvBenchmarkInputSource;
  comparison_source: GasCuvBenchmarkInputSource;
  tolerance_basis: string;
  model_version?: string;
  scope?: string;
  equation_reference_url?: string;
  field_validation_status?: "pending_field_validation";
  status: "benchmark_passed" | "failed";
  checks: Array<GasCuvNetworkCheck>;
  solution: SeriesParallelSolution | null;
  reason: string | null;
}

export interface GasCuvPlan {
  plan_id: string;
  label: string;
  description: string;
  status: "replayed" | "blocked";
  steps: Array<GasCuvStep>;
  topology_restoration_hours: number | null;
  peak_affected_service_count: number | null;
  affected_service_hours: number | null;
}

export interface GasCuvQueryBinding {
  expected_revision: number;
  input_sha256: string;
}

export interface GasCuvRankedPlan {
  plan_id: string;
  label: string;
  status: "eligible" | "excluded" | "insufficient_data";
  rank: number | null;
  affected_service_hours: number | null;
  topology_restoration_hours: number | null;
  peak_affected_service_count: number | null;
  reasons: Array<"base_plan_blocked" | "peak_limit_exceeded" | "recovery_deadline_missed" | "peak_unknown" | "recovery_unknown" | "objective_unknown">;
}

export interface GasCuvReplayResponse {
  schema_version: "gas-cuv-replay-0.1.0";
  data_mode: "synthetic";
  validation_status: "not_business_validated";
  semantic: "graph_connectivity_only";
  status: "partial";
  case_id: string;
  network_id: string;
  scenario_id: string;
  scenario_at: string;
  analysis_window_hours: number;
  fault_asset_ids: Array<string>;
  algorithm_version: string;
  topology_evidence: GasCuvTopologyEvidence;
  plans: Array<GasCuvPlan>;
  unknown_metrics: Array<GasCuvUnknownMetric>;
  missing_items: Array<GasCuvMissingItem>;
  comparison_basis: string;
  event_evidence: Array<CuvEventEvidence>;
  monitoring_evidence: CuvMonitoringContext;
}

export interface GasCuvSourceBinding {
  network_content_sha256: string;
  case_content_sha256: string;
  replay_content_sha256: string;
}

export interface GasCuvStep {
  at: string;
  elapsed_hours: number;
  kind: "fault" | "isolate" | "staged_release" | "assumed_clearance";
  status: "evaluated" | "blocked";
  affected_service_ids: Array<string> | null;
  affected_node_ids: Array<string> | null;
  affected_consumers: GasConsumerCounts | null;
  restored_service_ids: Array<string> | null;
  blocked_asset_ids: Array<string>;
  constraints: Array<GasCuvConstraint>;
}

export interface GasCuvTopologyEvidence {
  network_content_sha256: string;
  case_content_sha256: string;
  html_source_sha256: string | null;
  replay_content_sha256: string;
}

export interface GasCuvUnknownMetric {
  code: string;
  status: "unknown";
  value: null;
  reason: string;
}

export interface GasDataTopic {
  binding: GasM2Binding;
  rule_version?: "gas-m2-workbench/1";
  selection: GasTopicSelection;
  level: "city" | "district" | "demo_catalog" | "subnet" | "asset";
  source_status: "administrative_reference_only" | "synthetic_fixture";
  authorized_coordinates?: false;
  registered_assets: number;
  registered_events: number;
  field_completeness: GasFieldMetric;
  subnets: Array<GasTopicSubnet>;
  assets: Array<GasTopicAsset>;
  events: Array<string>;
  selected_asset?: GasTopicAsset | null;
  administrative_source_uri?: string | null;
  evidence_sha256: string;
  evidence_refs: Array<string>;
}

export interface GasDispositionAuditRecord {
  event_id: string;
  actor_id: string;
  occurred_at: string;
  from_status: "pending" | "acknowledged" | "in_progress" | "completed";
  to_status: "pending" | "acknowledged" | "in_progress" | "completed";
  reason: string;
  revision: number;
  monitoring_ref?: GasObservationRef | null;
}

export interface GasDispositionSnapshot {
  event_id: string;
  data_mode?: "synthetic";
  status: "pending" | "acknowledged" | "in_progress" | "completed";
  revision: number;
  next_status: "pending" | "acknowledged" | "in_progress" | "completed" | null;
  audit: Array<GasDispositionAuditRecord>;
}

export interface GasDispositionTransition {
  expected_revision: number;
  target_status: "pending" | "acknowledged" | "in_progress" | "completed";
  reason: string;
  monitoring_ref?: GasObservationRef | null;
}

export interface GasDistrict {
  district_id: string;
  name: string;
  data_mode: "real_authorized" | "synthetic" | "unknown" | "unavailable";
  source_status: "authorized" | "synthetic_fixture" | "unknown" | "unavailable";
  geometry_available: boolean;
  limitation: string;
  validation_status?: "business_validated" | "not_business_validated" | "unknown";
}

export interface GasDrillChoice {
  reference: GasDrillReference;
  pipe_id: string;
  fault_kind: "synthetic_edge_exclusion" | "synthetic_dual_source_split";
  latest: boolean;
}

export interface GasDrillCompareRequest {
  binding: GasM2Binding;
  before: GasDrillReference | null;
  after: GasDrillReference | null;
}

export interface GasDrillComparison {
  rule_version?: "gas-drill-comparison/1";
  selection: GasDrillCompareRequest;
  before: GasDrillSide;
  after: GasDrillSide;
  newly_unreachable_node_ids: Array<string>;
  newly_reachable_node_ids: Array<string>;
  comparison_sha256: string;
  evidence_refs: Array<string>;
  audit_boundary?: string;
  limitation?: string;
}

export interface GasDrillComponent {
  node_ids: Array<string>;
  source_ids: Array<string>;
}

export interface GasDrillGraph {
  rule_version?: "gas-drill-graph/1";
  source_nodes: Record<string, string>;
  baseline: GasDrillGraphState;
  scenario: GasDrillGraphState;
  excluded_edge_ids: Array<string>;
  alternative_path_edge_ids: Array<string>;
  sha256: string;
}

export interface GasDrillGraphState {
  reachable_node_ids: Array<string>;
  components: Array<GasDrillComponent>;
}

export interface GasDrillPreset {
  name: string;
  label: string;
  pipe_ids: Array<string>;
  fault_kind: "synthetic_edge_exclusion" | "synthetic_dual_source_split";
  algorithm: string;
}

export interface GasDrillReference {
  scenario_id: string;
  revision: number;
  analysis_sha256: string;
}

export interface GasDrillSide {
  report: GasScenarioReport | null;
  graph: GasDrillGraphState;
}

export interface GasDrillToolRequest {
  tool: "lookup" | "simulate" | "compare" | "report";
  selection: GasDrillCompareRequest;
  expected_sha256: string;
  question: string;
}

export interface GasDrillToolResult {
  request_id: string;
  binding: GasM2Binding;
  evidence_refs: Array<string>;
  facts: Array<string>;
  answer: string;
  explanation_source?: "backend_facts" | "qwen_evidence_selection";
  model_id?: string | null;
  model_calls?: number;
  model_usage?: Record<string, number>;
  model_error?: string | null;
  tool_name: "synthetic_scenario.lookup" | "synthetic_scenario.simulate_topology" | "synthetic_scenario.compare_versions" | "synthetic_scenario.report_evidence";
  selection: GasDrillCompareRequest;
  verified_sha256: string;
}

export interface GasDrillTrust {
  rule_version?: "gas-drill-trust/1";
  source_files_verified: number;
  graph_status: "valid" | "valid_with_warnings";
  graph_warnings: Array<string>;
  observation_count: number;
  observation_quality: Record<string, number>;
  observation_channels: Record<string, number>;
  identity_scope?: "internal_synthetic_owner";
  revision_policy?: "exact_read_compare_and_latest_write";
}

export interface GasDrillWorkspace {
  binding: GasM2Binding;
  presets: Array<GasDrillPreset>;
  versions: Array<GasDrillChoice>;
  trust: GasDrillTrust;
}

export interface GasEdge {
  edge_id: string;
  pipe_id: string;
  prototype_id: string;
  from_node_id: string;
  to_node_id: string;
  direction: "undirected_connectivity_only";
  initial_state: "enabled" | "disabled";
}

export interface GasEvent {
  event_id: string;
  title: string;
  event_kind: "given_fault" | "given_isolation" | "synthetic_observation";
  severity?: "critical" | "high" | "medium" | "low" | "unknown";
  state?: "synthetic_given" | "open" | "acknowledged" | "closed";
  data_mode: "real_authorized" | "synthetic" | "unknown" | "unavailable";
  source_status: "authorized" | "synthetic_fixture" | "unknown" | "unavailable";
  validation_status?: "business_validated" | "not_business_validated" | "unknown";
  district_id?: string | null;
  asset_ids?: Array<string>;
  closed_valve_ids?: Array<string>;
  location?: GasCoordinate | null;
  location_status?: "authorized" | "not_available" | "unknown";
  risk_status?: "unavailable" | "not_computed" | "confirmed";
  physical_supply_status?: "unknown" | "verified";
  evidence_refs?: Array<string>;
  limitation: string;
}

export interface GasFieldMetric {
  id: string;
  definition: string;
  valid: number;
  checked: number;
  ratio: number | null;
}

export interface GasIndicatorPreparation {
  code: string;
  name: string;
  required: boolean;
  existing_fields: Array<string>;
  missing_fields: Array<string>;
  synthetic_possible: Array<string>;
  authorization_needed: Array<string>;
}

export interface GasIsolationPlan {
  plan_id: string;
  scenario_id: string;
  closed_valve_ids: Array<string>;
  disabled_edge_ids: Array<string>;
  semantics: "node_cut_plus_explicit_unit_edges";
  derivation: string;
}

export interface GasLayout {
  x: number;
  y: number;
  unit: "px";
  coordinate_system: "schematic";
}

export interface GasLibInstanceSummary {
  instance_id: string;
  archive_path: string;
  archive_sha256: string;
  archive_bytes: number;
  member_count: number;
  network_members: number;
  compressor_members: number;
  scenario_members: number;
  cdf_members: number;
  svg_members: number;
}

export interface GasLibQualityResponse {
  dataset_id?: "DE-GASLIB";
  instance_id: string;
  version: string;
  status: "checked" | "partial" | "unavailable" | "not_found";
  archive_path?: string | null;
  archive_sha256?: string | null;
  member_hashes?: Record<string, string>;
  namespace_uris?: Array<string>;
  schema_locations?: Array<string>;
  node_count?: number;
  source_count?: number;
  sink_count?: number;
  innode_count?: number;
  exit_count?: number;
  connection_count?: number;
  pipe_count?: number;
  valve_count?: number;
  compressor_station_count?: number;
  scenario_file_count?: number;
  scenarios_checked?: number;
  scenario_boundary_count?: number;
  normalized_nodes?: Array<Record<string, unknown>>;
  normalized_connections?: Array<Record<string, unknown>>;
  normalized_scenarios?: Array<Record<string, unknown>>;
  quality_issues?: Array<string>;
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
}

export interface GasM2AgentRequest {
  binding: GasM2Binding;
  section: "provenance" | "quality" | "assets" | "events";
  record_id?: string | null;
  question: string;
}

export interface GasM2AgentResult {
  request_id: string;
  binding: GasM2Binding;
  tool_name: "data_m2.provenance" | "data_m2.quality" | "data_m2.assets" | "data_m2.events";
  evidence_refs: Array<string>;
  facts: Array<string>;
  answer: string;
  explanation_source?: "backend_facts" | "qwen_evidence_selection";
  model_status?: "unavailable" | "ready";
  model_id?: string | null;
  model_calls?: number;
  model_usage?: Record<string, number>;
  elapsed_ms?: number;
  model_error?: string | null;
  capabilities?: GasM2Capabilities;
}

export interface GasM2Binding {
  dataset_id: string;
  version: string | null;
  registry_sha256: string;
}

export interface GasM2Capabilities {
  health_assessment?: "unavailable";
  operating_risk?: "unavailable";
  physical_prediction?: "unavailable";
}

export interface GasM2Catalog {
  schema_version: "gas-data-m2/1";
  registry_sha256: string;
  datasets: Array<GasM2Dataset>;
  capabilities?: GasM2Capabilities;
}

export interface GasM2Dataset {
  dataset_id: string;
  title: string;
  source_uri: string | null;
  version: string | null;
  sha256: string | null;
  source_kind: "project_fixture" | "government" | "public_portal" | "research_repository" | "experiment_repository" | "map_provider" | "authorized_operator";
  data_kind: "internal_synthetic" | "synthetic_research_benchmark" | "historical_network" | "public_statistics" | "official_incidents" | "experimental_measurement" | "observational_monitoring" | "experiment_and_simulation" | "simulation" | "knowledge_collection" | "administrative_service" | "unverified_catalog" | "real_authorized";
  geo_scope: string;
  coordinate_system: string | null;
  statistics_time: string | null;
  schema_status: "unverified" | "documented" | "verified";
  quality_status: "unavailable" | "partial" | "checked";
  license_status: string;
  synthetic_flag: boolean | null;
  usage_scope: "catalog_only" | "synthetic_demo" | "research_benchmark" | "research_gis" | "annual_context" | "administrative_reference";
  ingest_status: "catalog_only" | "candidate_ready" | "metadata_projection" | "authorization_pending" | "download_blocked" | "service_only";
  sources: Array<GasM2Source>;
  field_mappings: Array<GasM2Field>;
  transformations: Array<string>;
  normalized_sha256: string | null;
  quarantine_sha256: string | null;
  record_counts: Record<string, number>;
  issues: Array<GasM2Issue>;
  facts: Array<string>;
}

export interface GasM2Details {
  binding: GasM2Binding;
  dataset: GasM2Dataset;
  capabilities?: GasM2Capabilities;
}

export interface GasM2Field {
  raw_field: string;
  normalized_field: string;
  unit: string | null;
}

export interface GasM2Issue {
  code: string;
  count: number | null;
  detail: string;
}

export interface GasM2RecordPage {
  binding: GasM2Binding;
  kind: "assets" | "events" | "topology" | "observations";
  source_uri: string | null;
  total: number;
  items: Array<Record<string, unknown>>;
  coordinate_system?: "schematic";
  data_kind?: "internal_synthetic";
  capabilities?: GasM2Capabilities;
}

export interface GasM2Source {
  name: string;
  sha256: string;
}

export interface GasM2TopicAnswer {
  request_id: string;
  binding: GasM2Binding;
  evidence_refs: Array<string>;
  facts: Array<string>;
  answer: string;
  explanation_source?: "backend_facts" | "qwen_evidence_selection";
  model_id?: string | null;
  model_calls?: number;
  model_usage?: Record<string, number>;
  model_error?: string | null;
  tool_name: "data_m2.topic" | "data_m2.quality_workbench";
  selection: GasTopicSelection | null;
  verified_sha256: string;
}

export interface GasM2TopicQuestion {
  binding: GasM2Binding;
  tool: "navigation" | "quality";
  selection: GasTopicSelection | null;
  expected_sha256: string;
  question: string;
}

export interface GasMetric {
  status: "available" | "partial" | "insufficient_data" | "not_applicable" | "unsupported";
  value?: number | null;
  unit?: string | null;
  reason?: string | null;
  in_analysis_window?: boolean | null;
}

export interface GasMonitoringObservation {
  reference: GasObservationRef;
  dataset_id: string;
  dataset_version: string;
  data_mode?: "synthetic";
  validation_status?: "not_business_validated";
  asset_id: string;
  asset_evidence: MonitoringRowEvidence;
  sensor_id: string;
  metric: "pressure" | "flow";
  policy_id: string;
  lower: number | null;
  upper: number | null;
  sustained_samples: number;
  state?: "limit_exceeded";
  previous_state: "observing" | "normal" | "limit_exceeded" | "data_missing";
  direction: "above_upper" | "below_lower";
  triggered_at: string;
  observation: MonitoringObservationEvidence;
  sustaining_observations: Array<MonitoringObservationEvidence>;
  event: CuvEventEvidence | null;
  scenario_id: string | null;
}

export interface GasMonitoringReport {
  data_mode: "synthetic";
  validation_status: "not_business_validated";
  dataset_id: string;
  dataset_version: string;
  manifest_sha256: string;
  policy_id: string;
  window_start: string;
  evaluated_at: string;
  cadence_seconds: number;
  sustained_samples: number;
  missing_samples: number;
  monitoring_source: MonitoringSourceEvidence | null;
  channels: Array<MonitoringChannelResult>;
}

export interface GasNode {
  node_id: string;
  prototype_id: string;
  node_type: "junction" | "regulator" | "valve" | "source";
  name?: string | null;
  layout: GasLayout;
}

export interface GasObservationRef {
  observation_id: string;
  manifest_sha256: string;
}

export interface GasPipe {
  pipe_id: string;
  edge_id: string;
  prototype_id: string;
  material: string;
  diameter_mm: number;
  pressure_class: string;
  asset_status: string;
}

export interface GasPressure {
  value: number;
  unit: "psia" | "psig" | "kPa_abs" | "kPa_gauge";
}

export interface GasQualityWorkbench {
  binding: GasM2Binding;
  rule_version?: "gas-m2-workbench/1";
  data_kind?: "internal_synthetic";
  metrics: Array<GasFieldMetric>;
  observation_quality: Record<string, number>;
  incomplete_mapping_fields: Array<string>;
  unsupported_mappings: Array<string>;
  indicator_version: string;
  indicator_source: GasM2Source;
  preparation: Array<GasIndicatorPreparation>;
  sources: Array<GasM2Source>;
  source_uri: string | null;
  health_score?: "unavailable";
  evidence_sha256: string;
  evidence_refs: Array<string>;
}

export interface GasSafety {
  field_operation_instruction: false;
  safe_restoration_decision: false;
  physical_supply_verified: false;
  simulation_only: true;
}

export interface GasScenario {
  scenario_id: string;
  name: string;
  fault_edge_ids: Array<string>;
  origin: string;
  fault_assumption: string;
}

export interface GasScenarioAnalysis {
  algorithm: string;
  impact: GasScenarioImpact;
  analysis_sha256: string;
  model_scope?: "graph_connectivity_only";
  actual_supply?: "unknown";
  actual_outage?: "unknown";
  pressure?: "unknown";
  flow?: "unknown";
  leakage?: "unknown";
  graph?: GasDrillGraph | null;
}

export interface GasScenarioAudit {
  actor_id: string;
  occurred_at: string;
  revision: number;
  action: "created" | "analyzed" | "rolled_back";
  previous_status: "draft" | "analyzed" | "rolled_back" | null;
  current_status: "draft" | "analyzed" | "rolled_back";
  analysis_sha256: string | null;
}

export interface GasScenarioCreate {
  binding: GasM2Binding;
  pipe_id: string;
  fault_kind: "synthetic_edge_exclusion" | "synthetic_dual_source_split";
}

export interface GasScenarioExplanation {
  request_id: string;
  binding: GasM2Binding;
  evidence_refs: Array<string>;
  facts: Array<string>;
  answer: string;
  explanation_source?: "backend_facts" | "qwen_evidence_selection";
  model_id?: string | null;
  model_calls?: number;
  model_usage?: Record<string, number>;
  model_error?: string | null;
  tool_name?: "synthetic_scenario.read_analysis";
  scenario_id: string;
  revision: number;
  analysis_sha256: string;
}

export interface GasScenarioImpact {
  fault_edge_id: string;
  baseline_reachable_node_count: number;
  scenario_reachable_node_count: number;
  newly_unreachable_node_ids: Array<string>;
  affected_edge_ids: Array<string>;
  affected_pipe_ids: Array<string>;
}

export interface GasScenarioQuestion {
  expected_revision: number;
  analysis_sha256: string;
  question: string;
}

export interface GasScenarioReport {
  report_version?: "gas-scenario-report/1";
  scenario: GasSyntheticScenario;
  audit: Array<GasScenarioAudit>;
  evidence_refs: Array<string>;
  limitation?: string;
  trust: GasDrillTrust;
  audit_boundary?: string;
}

export interface GasScenarioRevision {
  expected_revision: number;
}

export interface GasScenarioSource {
  source_uri: string | null;
  files: Array<GasM2Source>;
  network_content_sha256: string;
}

export interface GasScoringContract {
  status: "candidate" | "unavailable";
  rule_version: string;
  business_validation: "not_business_validated";
  required_indicators: Array<string>;
  available_indicators: Array<string>;
  formula_status: "candidate_arithmetic_only" | "not_available";
  h_status: "unsupported_pending_business_definition";
  p_semantics: string;
  coverage_semantics: string;
  note: string;
}

export interface GasService {
  service_id: string;
  consumer_id: string;
  node_ids: Array<string>;
  supply_rule: "any" | "all";
  status: "graph_reachable" | "newly_graph_unreachable" | "already_graph_unreachable";
  physical_supply_status: "unknown";
  previously_graph_reachable: boolean;
  currently_graph_reachable: boolean;
}

export interface GasSimulationCapability {
  mode: "synthetic_topology" | "experiment_replay" | "historical_incident" | "solver_benchmark" | "physical_chongqing";
  title: string;
  status: "ready" | "candidate" | "unavailable";
  deterministic: boolean;
  dataset_ids?: Array<string>;
  required_inputs?: Array<string>;
  supported_outputs?: Array<string>;
  limitations?: Array<string>;
}

export interface GasSimulationCatalogResponse {
  status: "ready" | "partial" | "unavailable";
  workbench_version: string;
  capabilities: Array<GasSimulationCapability>;
  limitations?: Array<string>;
}

export interface GasSimulationReport {
  report_id: string;
  generated_at: string;
  title: string;
  mode: "synthetic_topology" | "experiment_replay" | "historical_incident" | "solver_benchmark" | "physical_chongqing";
  status: "succeeded" | "partial" | "unavailable" | "not_found";
  dataset_id: string;
  dataset_version: string;
  data_mode: "synthetic" | "public_reference" | "real_authorized" | "unavailable";
  findings?: Array<string>;
  unknowns?: Array<string>;
  evidence_refs?: Array<string>;
  report_sha256: string;
}

export interface GasSimulationRequest {
  mode: "synthetic_topology" | "experiment_replay" | "historical_incident" | "solver_benchmark" | "physical_chongqing";
  dataset_id?: string | null;
  scenario_id?: string | null;
  experiment_id?: string | null;
  category?: "gas_distribution" | "gas_transmission_gathering" | "lng" | "type_r_gas_gathering" | null;
  year_from?: number | null;
  year_to?: number | null;
  state?: string | null;
  limit?: number;
}

export interface GasSimulationResult {
  run_id: string;
  mode: "synthetic_topology" | "experiment_replay" | "historical_incident" | "solver_benchmark" | "physical_chongqing";
  status: "succeeded" | "partial" | "unavailable" | "not_found";
  dataset_id: string;
  dataset_version: string;
  data_mode: "synthetic" | "public_reference" | "real_authorized" | "unavailable";
  result?: Record<string, unknown>;
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
  report: GasSimulationReport;
}

export interface GasSource {
  source_id: string;
  node_id: string;
  prototype_id: string;
  name: string;
  enabled: boolean;
  pressure?: number | null;
  capacity?: number | null;
  physical_model_status: "unsupported";
}

export interface GasSourceStressAnswer {
  request_id: string;
  binding: GasM2Binding;
  evidence_refs: Array<string>;
  facts: Array<string>;
  answer: string;
  explanation_source?: "backend_facts" | "qwen_evidence_selection";
  model_id?: string | null;
  model_calls?: number;
  model_usage?: Record<string, number>;
  model_error?: string | null;
  tool_name?: "synthetic_scenario.source_stress";
  selection: GasSourceStressRequest;
  verified_sha256: string;
}

export interface GasSourceStressQuestion {
  selection: GasSourceStressRequest;
  expected_sha256: string;
  question: string;
}

export interface GasSourceStressRequest {
  binding: GasM2Binding;
  scenario: GasDrillReference | null;
  source_id: string;
}

export interface GasSourceStressResult {
  selection: GasSourceStressRequest;
  rule_version?: "gas-drill-source-withdrawal/1";
  source: GasScenarioSource;
  enabled_source_nodes: Record<string, string>;
  remaining_source_ids: Array<string>;
  excluded_edge_ids: Array<string>;
  before: GasDrillGraphState;
  after: GasDrillGraphState;
  newly_unreachable_node_ids: Array<string>;
  single_source_dependencies: Record<string, Array<string>>;
  alternate_source_id: string | null;
  alternate_source_path_edge_ids: Array<string>;
  scenario_alternative_path_edge_ids: Array<string>;
  audit: Array<GasScenarioAudit>;
  stress_sha256: string;
  evidence_refs: Array<string>;
  limitation?: string;
  audit_boundary?: string;
}

export interface GasSyntheticScenario {
  scenario_id: string;
  data_kind?: "internal_synthetic";
  coordinate_system?: "schematic";
  input: GasScenarioCreate;
  source: GasScenarioSource;
  analysis: GasScenarioAnalysis | null;
  transition: GasScenarioAudit;
}

export interface GasTemperature {
  value: number;
  unit: "degR" | "degF" | "K" | "degC";
}

export interface GasTimelineState {
  event_id: string;
  time_hours: number;
  kind: "given_fault" | "given_isolation" | "synthetic_observation";
  current_reachable_node_ids: Array<string>;
  current_unreachable_node_ids: Array<string>;
  normal_unreachable_node_ids: Array<string>;
  newly_unreachable_node_ids: Array<string>;
  services: Array<GasService>;
  newly_affected_service_ids: Array<string>;
  potentially_interrupted_consumers: GasConsumerCounts;
  model_scope: "graph_connectivity_only";
  physical_supply_verified: false;
}

export interface GasTopicAsset {
  asset_id: string;
  asset_type: "pipe" | "node" | "valve" | "consumer";
  fields: Record<string, unknown>;
}

export interface GasTopicSelection {
  scope: "chongqing" | "demo";
  district_id?: string | null;
  subnet_id?: string | null;
  asset_id?: string | null;
}

export interface GasTopicSubnet {
  subnet_id: string;
  name: string;
  registered_assets: number;
}

export interface GasValve {
  valve_id: string;
  node_id: string;
  prototype_id: string;
  cut_semantics: "node_cut";
  initial_state: "open" | "closed";
  incident_edge_ids: Array<string>;
}

export interface GasVolumeRate {
  value: number;
  unit: "million_ft3/day" | "m3/day";
  basis: "standard" | "actual";
}

export interface GeneralFlowPipe {
  length: PipeLength;
  internal_diameter: PipeDiameter;
  darcy_friction_factor: number;
}

export interface GraphEvent {
  at_hours: number;
  action_ids?: Array<string>;
  restored_edge_ids?: Array<string>;
  valve_overrides?: Record<string, string>;
  service_availability?: Record<string, boolean>;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface IsolationPlan {
  plan_id: string;
  fault_edge_ids: Array<string>;
  valve_ids?: Array<string>;
  evidence_ref: string;
}

export type JsonValue = unknown;

export interface LegacyResult {
  Rs?: number | null;
  Hs?: number | null;
  completeness_percent?: number | null;
  grade: string;
}

/** Legacy 0.1.1 score with explicit semantics; never reported as formal H. */
export interface LegacyScore {
  semantics?: "legacy_demo_0.1.1";
  rs?: number | null;
  hs?: number | null;
  grade: string;
  completeness_percent?: number | null;
  model_family?: string;
  model_version?: string;
  evidence_refs?: Array<string>;
}

export interface MonitoringChannelResult {
  asset_id: string;
  sensor_id: string;
  metric: "pressure" | "flow";
  unit: string;
  lower: number | null;
  upper: number | null;
  state: "observing" | "normal" | "limit_exceeded" | "data_missing";
  last_usable_at: string | null;
  off_schedule_observations: number;
  asset_evidence: MonitoringRowEvidence;
  events: Array<MonitoringEvent>;
}

export interface MonitoringEvent {
  kind: "limit_exceeded" | "data_missing" | "recovered";
  at: string;
  previous_state: "observing" | "normal" | "limit_exceeded" | "data_missing";
  cause: "above_upper" | "below_lower" | "no_usable_observation" | "within_limits" | "observations_resumed";
  evidence: Array<MonitoringObservationEvidence>;
  missing_slots: Array<string>;
}

export interface MonitoringObservationEvidence {
  source_id: string;
  source_version: string;
  source_sha256: string;
  source_record_id: string;
  source_row: number;
  observed_at: string;
  value: number;
  unit: string;
  quality: "valid" | "suspect";
}

export interface MonitoringRowEvidence {
  source_id: string;
  source_version: string;
  source_sha256: string;
  source_record_id: string;
  source_row: number;
}

export interface MonitoringSourceEvidence {
  source_id: string;
  source_version: string;
  source_sha256: string;
}

export interface Node {
  node_id: string;
  label?: string;
  source?: boolean;
  active: boolean | null;
  boundary?: boolean;
  boundary_confirmed?: boolean;
  x?: number | null;
  y?: number | null;
}

/** 离线专业情景随所属快照冻结；事件、隔离和窗口必须与当前任务一致。 */
export interface OfflineEffect {
  as_of: string;
  fault_edge_id: string;
  isolation_plan_id: string;
  horizon_hours: number;
  evidence_ref: string;
  remaining_disconnected_service_ids: Array<string>;
  restoration_hours?: number | null;
  backup_covered_critical_service_ids?: Array<string>;
}

export interface PhmsaFileQuality {
  category: "gas_distribution" | "gas_transmission_gathering" | "lng" | "type_r_gas_gathering";
  path: string;
  archive_sha256: string;
  record_count: number;
  column_count: number;
  missing_record_id: number;
  missing_year: number;
  parse_errors: number;
  evidence_ref: string;
}

export interface PhmsaIncidentPage {
  dataset_id?: "US-PHMSA-INCIDENT";
  version: string;
  status: "succeeded" | "not_found" | "unavailable";
  category?: "gas_distribution" | "gas_transmission_gathering" | "lng" | "type_r_gas_gathering" | null;
  total: number;
  items?: Array<Record<string, unknown>>;
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
}

export interface PhmsaQualityResponse {
  dataset_id?: "US-PHMSA-INCIDENT";
  version: string;
  status: "checked" | "partial" | "unavailable";
  files?: Array<PhmsaFileQuality>;
  total_records: number;
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
}

export interface PilotAccess {
  status: "synthetic" | "authorized";
  valid_from?: string | null;
  valid_until?: string | null;
  asset_geometry: boolean;
  local_qwen: boolean;
}

export interface PilotAgentRequest {
  observation_id: string;
  question: string;
  binding?: PilotQueryBinding | null;
}

export interface PilotAgentResult {
  dataset_id: string;
  version: string;
  data_mode: "synthetic" | "real_authorized";
  manifest_sha256: string;
  evidence_scope: GasAgentEvidenceScope;
  blocked_reason?: "cross_dataset_unverified" | "reference_outside_scope" | "engineering_evidence_unavailable" | null;
  observation_id: string;
  status: "succeeded" | "unavailable";
  parser_source: "local_rule_not_llm" | "qwen_evidence_selection";
  asset_id: string;
  focus?: "observation" | "alarm" | "threshold" | "quality" | "version_change" | "asset_events";
  model_state?: "online" | "fallback" | "unavailable";
  fallback_reason?: "timeout" | "model_response_unavailable" | null;
  model_id?: string | null;
  model_calls?: number;
  answer: string;
  evidence: Array<PilotSource>;
  event_ids?: Array<string>;
  threshold_version?: string | null;
  citations?: Array<PilotCitation>;
}

export interface PilotAlarm {
  threshold_id: string;
  threshold_version: string;
  at: string;
  direction: "above_upper" | "below_lower";
}

export interface PilotCatalogue {
  configured: boolean;
  items: Array<PilotDatasetEntry>;
  rejections?: Array<PilotRejectedQuality>;
}

export interface PilotCitation {
  kind: "observation" | "sensor" | "asset" | "threshold" | "event";
  record_id: string;
  source: PilotSource;
  threshold_version?: string | null;
  detail?: string | null;
}

export interface PilotDataset {
  dataset_id: string;
  version: string;
  data_mode: "synthetic" | "real_authorized";
  manifest_sha256: string;
  imported_at: string;
  quality_status: "ready" | "partial";
  pipes: number;
  sensors: number;
  observations: number;
  imported_by?: string | null;
  admission_state?: "admitted" | "quarantined";
  grant_scope_sha256?: string | null;
  preferred?: boolean;
  sensor_records: Array<PilotSensor>;
  access: PilotAccess;
  quality: PilotQualityReport;
  engineering_analysis?: "unavailable";
}

export interface PilotDatasetEntry {
  dataset_id: string;
  version: string;
  data_mode: "synthetic" | "real_authorized";
  manifest_sha256: string;
  imported_at: string;
  quality_status: "ready" | "partial";
  pipes: number;
  sensors: number;
  observations: number;
  imported_by?: string | null;
  admission_state?: "admitted" | "quarantined";
  grant_scope_sha256?: string | null;
  preferred?: boolean;
}

export interface PilotDuplicateAlarm {
  alarm: PilotAlarm;
  original: PilotAlarm;
}

export interface PilotEvent {
  source: PilotSource;
  event_id: string;
  recorded_at: string;
  observation_ids: Array<string>;
  pipe_ids: Array<string>;
  status: "open" | "acknowledged" | "resolved" | "unknown";
  evidence_ref: string;
}

export interface PilotFieldGap {
  table: "nodes" | "pipes" | "gis" | "sensors" | "observations" | "events" | "thresholds" | "threshold_rules" | "truth_windows" | "truth_events";
  fields: Array<string>;
}

export interface PilotLine {
  type: "LineString";
  coordinates: Array<Array<unknown>>;
}

export interface PilotMappedLine {
  geometry_id: string;
  source_crs: string;
  scope_ref: string;
  geometry: PilotLine;
  source: PilotSource;
}

export interface PilotObservation {
  source: PilotSource;
  observation_id: string;
  sensor_id: string;
  observed_at: string;
  value: number | null;
  unit: string;
  quality: "valid" | "suspect" | "invalid" | "missing";
  quality_reason?: string | null;
}

export interface PilotPipe {
  source: PilotSource;
  pipe_id: string;
  from_node_id: string;
  to_node_id: string;
  material: "steel" | "pe" | "ductile_iron" | "other" | "unknown";
  diameter_mm: number;
  pressure_class: string;
  region_ref?: string | null;
}

export interface PilotPipeDetail {
  dataset_id: string;
  version: string;
  data_mode: "synthetic" | "real_authorized";
  manifest_sha256: string;
  pipe: PilotPipe;
  geometry_status: "not_requested" | "outside_scope" | "not_available" | "available";
  geometries_wgs84: Array<PilotMappedLine>;
}

export interface PilotQualityCounts {
  error: number;
  warning: number;
}

export interface PilotQualityIssue {
  table: "nodes" | "pipes" | "gis" | "sensors" | "observations" | "events" | "thresholds" | "threshold_rules" | "truth_windows" | "truth_events";
  code: string;
  row?: number | null;
  field?: string | null;
  severity: "error" | "warning";
}

export interface PilotQualityReport {
  issue_counts: PilotQualityCounts;
  issues: Array<PilotQualityIssue>;
  missing_fields?: Array<PilotFieldGap>;
  findings_omitted?: number;
}

export interface PilotQueryBinding {
  manifest_sha256: string;
  threshold_version: string | null;
  window_start?: string | null;
  window_end?: string | null;
}

export interface PilotRejectedQuality {
  dataset_id: string;
  version: string;
  data_mode: "synthetic" | "real_authorized";
  manifest_sha256: string;
  receipt: number;
  rejected_at: string;
  quality: PilotQualityReport;
}

export interface PilotReplayPoint {
  observation: PilotObservation;
  classification: "normal" | "anomaly" | "excluded" | "off_schedule" | "no_policy";
  threshold_id: string | null;
  threshold_version: string | null;
  event_ids: Array<string>;
}

export interface PilotReplayResult {
  dataset_id: string;
  version: string;
  data_mode: "synthetic" | "real_authorized";
  manifest_sha256: string;
  pipe_id: string;
  sensor: PilotSensor;
  rule_version?: "gas-scheduled-replay-1";
  window_start: string | null;
  window_end: string | null;
  observations: Array<PilotReplayPoint>;
  segments: Array<PilotReplaySegment>;
  related_events: Array<PilotEvent>;
  validation: PilotReplayValidation;
  applied_thresholds?: Array<PilotThreshold>;
}

export interface PilotReplaySegment {
  threshold: PilotThreshold;
  start: string;
  end: string;
  channel: MonitoringChannelResult;
}

export interface PilotReplayValidation {
  status: "rule_only" | "truth_comparison";
  reason: "complete_review" | "no_eligible_review_window" | "review_use_outside_scope";
  duplicate_alarms: Array<PilotDuplicateAlarm>;
  metrics: PilotTruthMetrics | null;
}

export interface PilotSensor {
  source: PilotSource;
  sensor_id: string;
  pipe_id: string;
  metric: "pressure" | "flow";
  unit: string | "m3/h" | "Nm3/h";
  device_status: "active" | "inactive" | "maintenance" | "unknown";
}

export interface PilotSource {
  source_id: string;
  source_version: string;
  source_sha256: string;
  source_record_id: string;
  source_row: number;
  table: "nodes" | "pipes" | "gis" | "sensors" | "observations" | "events" | "thresholds" | "threshold_rules" | "truth_windows" | "truth_events";
  sheet?: string | null;
}

export interface PilotThreshold {
  source: PilotSource;
  threshold_id: string;
  sensor_id: string;
  version: string;
  unit: string;
  lower: number | null;
  upper: number | null;
  cadence_seconds: number;
  sustained_samples: number;
  missing_samples: number;
  operator_id: string;
  approval_ref: string;
  approved_at: string;
  effective_from: string;
  effective_until: string;
  rule_inputs?: Array<PilotThresholdRule>;
}

export interface PilotThresholdRule {
  source: PilotSource;
  rule_id: string;
  version: string;
  metric_id: "pressure" | "flow";
  asset_id: string;
  sensor_id: string;
  unit: string;
  threshold_type: "lower" | "upper";
  threshold_value: number;
  effective_from: string;
  effective_to: string;
  approval_status: "draft" | "pending" | "approved" | "rejected" | "revoked";
  approved_by: string | null;
  approved_at: string | null;
  evidence_reference: string | null;
  cadence_seconds: number;
  sustained_samples: number;
  missing_samples: number;
}

export interface PilotTruthComparison {
  event_id: string;
  truth_event_id: string;
  truth_source: PilotSource;
  verified_start: string;
  verified_recovery: string | null;
  detected_at: string | null;
  detection_delay_seconds: number | null;
  recovery_detected_at: string | null;
  recovery_delay_seconds: number | null;
}

export interface PilotTruthMetrics {
  mode: "synthetic_truth_check" | "operator_reviewed_history";
  windows: Array<PilotTruthWindow>;
  matches: Array<PilotTruthComparison>;
  false_alarms: Array<PilotAlarm>;
  missed_events: number;
  recoveries_reviewed: number;
  recoveries_detected: number;
  recoveries_missed: number;
}

export interface PilotTruthWindow {
  source: PilotSource;
  truth_window_id: string;
  sensor_id: string;
  start: string;
  end: string;
  coverage_status: "complete" | "partial";
  reviewed_by: string;
  reviewed_at: string;
  evidence_reference: string;
}

export interface PipeDiameter {
  value: number;
  unit: "in" | "mm";
}

export interface PipeLength {
  value: number;
  unit: "mi" | "km";
}

export interface PublicCatalogResponse {
  status: "ready" | "partial" | "unavailable";
  corpus_id: string;
  datasets?: Array<PublicDatasetSummary>;
  limitations?: Array<string>;
}

export interface PublicDatasetSummary {
  dataset_id: string;
  version: string;
  title: string;
  source_uri: string;
  source_kind: string;
  data_kind: string;
  geo_scope: string;
  license_status: string;
  raw_archive_sha256: string;
  quality_status: "checked" | "partial" | "unavailable";
  ingest_status: "raw_only" | "candidate_ready" | "unavailable";
  record_count?: number | null;
  file_count?: number | null;
  evidence_refs?: Array<string>;
  limitations?: Array<string>;
}

export interface PublicEvidenceReport {
  report_id: string;
  dataset_id: string;
  version: string;
  generated_at: string;
  status: "checked" | "partial" | "unavailable";
  source_kind: string;
  findings?: Array<string>;
  quality_findings?: Array<string>;
  unknowns?: Array<string>;
  evidence_refs?: Array<string>;
  report_sha256: string;
}

/** 受控重算仅接收明确列名的内存记录，不接受文件路径或执行代码。 */
export interface RecomputeRequest {
  name?: string;
  as_of: string;
  window_start: string;
  window_end: string;
  static_rows: Array<Record<string, JsonValue>>;
  dynamic_rows: Array<Record<string, JsonValue>>;
  data_mode: "fixture_replay" | "synthetic" | "real";
  source_refs: Array<string>;
}

export interface RecoveryTimes {
  status: string;
  algorithm_version?: string | null;
  plan_completion_hours?: TimeMetric | null;
  service_first_restored_hours?: Record<string, TimeMetric>;
  first_target_service_restored_hours?: TimeMetric | null;
  all_target_services_restored_hours?: TimeMetric | null;
}

export interface ReportAlgorithmRef {
  capability_id: string;
  semantics: string;
  version: string;
  validation_status: string;
}

/** 评分原样引用；`recomputed=false` 表示报告未重跑评分。 */
export interface ReportAssessmentRef {
  pipe_id: string;
  model_family: string;
  model_version: string;
  rs?: number | null;
  hs?: number | null;
  grade: string;
  completeness_percent?: number | null;
  evidence_refs?: Array<string>;
  recomputed?: false;
}

/**
 * `GET /api/v1/runs/{run_id}/report` 的响应体。
 * 报告只引用既有 Run 与已有快照字段，不重新生成业务分数。
 */
export interface ReportDocument {
  report_schema_version?: "unified-report-1.0";
  report_id: string;
  source?: "existing_run_only";
  business_status: string;
  run_id: string;
  run_kind: "assessment" | "simulation" | "decision" | "assistant";
  run_status: "queued" | "running" | "succeeded" | "partial" | "insufficient_data" | "infeasible" | "failed";
  dataset: Record<string, JsonValue>;
  algorithms: Array<ReportAlgorithmRef>;
  scenario?: Record<string, JsonValue> | null;
  pipe?: ReportAssessmentRef | null;
  simulation?: Record<string, JsonValue> | null;
  decision?: Record<string, JsonValue> | null;
  timeline?: Record<string, JsonValue> | null;
  feedback?: Array<FeedbackRecord>;
  evidence_ids?: Array<string>;
  limitations?: Array<string>;
  recomputation?: "none";
}

export interface RunListEnvelope {
  items: Array<RunView>;
}

/** `GET /api/v1/runs/{run_id}` 与 enqueue 响应共用的任务视图。 */
export interface RunView {
  run_id: string;
  kind: "assessment" | "simulation" | "decision" | "assistant";
  status: "queued" | "running" | "succeeded" | "partial" | "insufficient_data" | "infeasible" | "failed";
  created_at: string;
  finished_at?: string | null;
  request: Record<string, JsonValue>;
  result?: Record<string, JsonValue> | null;
  review_status?: "accepted" | "rejected" | "revised" | "pending_review" | null;
  review_revision: number;
  execution_status?: "not_dispatched";
}

export interface ScenarioRequest {
  snapshot_id: string;
  as_of: string;
  fault_edge_id: string;
  isolation_plan_id: string;
  horizon_hours?: number;
}

export interface SeriesParallelInput {
  gas_specific_gravity: number;
  compressibility: number;
  base_pressure: GasPressure;
  base_temperature: GasTemperature;
  gas_temperature: GasTemperature;
  inlet_pressure: GasPressure;
  atmosphere: GasPressure;
  terminal_flow: GasVolumeRate;
  upstream: GeneralFlowPipe;
  parallel: Array<unknown>;
  downstream: GeneralFlowPipe;
}

export interface SeriesParallelSolution {
  inlet_psia: number;
  split_psia: number;
  recombine_psia: number;
  outlet_psia: number;
  atmosphere_psia: number;
  terminal_flow_mmscfd: number;
  branch_flows_mmscfd: Array<unknown>;
  max_flow_balance_residual_mmscfd: number;
  max_pressure_equation_residual_psi2: number;
}

export interface Service {
  service_id: string;
  label: string;
  node_ids: Array<string>;
  supply_rule: "any" | "all";
  count_value?: number | null;
  count_unit: "household" | "person" | "facility";
  critical?: boolean;
}

export interface SnapshotCounts {
  assessments: number;
  nodes: number;
  edges: number;
  services: number;
  plans: number;
}

export interface SnapshotListEnvelope {
  items: Array<SnapshotSummary>;
}

/** 一个数据空间的一次冻结快照；禁止把提供方500条样例与虚构网络拼接。 */
export interface SnapshotPayload {
  schema_version?: "0.1.0";
  name: string;
  data_mode: "fixture_replay" | "synthetic" | "real";
  source_status: "source_unknown" | "constructed" | "declared_real";
  as_of: string;
  source_refs: Array<string>;
  coordinate_system?: "schematic" | "wgs84" | "local" | null;
  assessments?: Array<Assessment>;
  nodes?: Array<Node>;
  edges?: Array<Edge>;
  services?: Array<Service>;
  isolation_plans?: Array<IsolationPlan>;
  actions?: Array<Action>;
  plans?: Array<ActionPlan>;
  value_profile?: ValueProfile | null;
}

/** `GET /api/v1/snapshots` 的元素。不含 payload。 */
export interface SnapshotSummary {
  snapshot_id: string;
  sha256: string;
  created_at: string;
  name: string;
  data_mode: "fixture_replay" | "synthetic" | "real";
  source_status: "source_unknown" | "constructed" | "declared_real";
  as_of: string;
  validation_status?: "not_business_validated";
  counts: SnapshotCounts;
}

export interface TimeMetric {
  /** 小时，相对请求的 as_of；null 表示缺数或未在窗口内恢复 */
  value_hours?: number | null;
  status: string;
  reason?: string;
  criterion_id?: string | null;
  time_basis?: string | null;
  observation_horizon_hours?: number | null;
  beyond_horizon?: boolean | null;
  lower_bound_hours?: number | null;
  lower_bound_exclusive?: boolean | null;
}

export interface TimelineCheckResponse {
  status: string;
  dataset: DatasetReference;
  algorithm: AlgorithmReference;
  result: CapabilityCheckResult;
  field_availability: Array<FieldAvailability>;
  /** 至少一个本次范围内的指标有确定结果（含显式不适用）。 */
  has_any_calculable_output: boolean;
  /** 本次范围内的全部指标都有完整结果；与 can_compute 的完整可得语义对齐。 */
  all_required_outputs_calculable: boolean;
  evidence_ids: Array<string>;
  missing_items: Array<unknown>;
  limitations: Array<string>;
}

export interface TimelineEvaluationResponse {
  status: string;
  dataset: DatasetReference;
  algorithm: AlgorithmReference;
  result: TimelineResult;
  evidence_ids: Array<string>;
  missing_items: Array<unknown>;
  limitations: Array<string>;
}

export interface TimelineRequest {
  snapshot_id: string;
  as_of?: string | null;
  fault_edge_id: string;
  isolation_plan_id: string;
  plan_id: string;
  target_service_ids: Array<string>;
  /** 观察窗口，单位小时；拒绝布尔值、NaN 与 Infinity */
  horizon_hours?: number;
  action_schedule?: Array<TimelineSchedule>;
}

export interface TimelineResult {
  status: string;
  algorithm_version: string;
  snapshot_id?: string | null;
  plan_id?: string | null;
  data_mode?: string | null;
  source_status?: string | null;
  time_basis?: string | null;
  scenario?: Record<string, unknown> | null;
  timeline_scope?: string | null;
  criterion_id?: string | null;
  criterion_version?: string | null;
  timeline_status?: string | null;
  timeline_request?: TimelineSnapshotRequest | null;
  recovery_times?: RecoveryTimes | null;
  pre_action_availability?: Record<string, boolean | null> | null;
  graph_events?: Array<GraphEvent>;
  ignored_topological_action_ids?: Array<string>;
  beyond_horizon_action_ids?: Array<string>;
  deferred_validation_issues?: Array<string>;
  plan_feasibility_checked?: boolean | null;
  issues?: Array<unknown>;
  limitations?: Array<string>;
  validation_status?: string | null;
}

export interface TimelineSchedule {
  action_id: string;
  finish_offset_hours?: number | null;
  start_offset_hours?: number | null;
  duration_hours?: number | null;
}

export interface TimelineSnapshotRequest {
  as_of: string;
  horizon_hours: number;
  plan_id: string;
  action_ids?: Array<string>;
  action_schedule?: Array<TimelineSchedule>;
  target_service_ids?: Array<string>;
  time_basis: string;
  criterion_id: string;
  criterion_version: string;
  initial_availability?: Record<string, boolean | null>;
  availability_events?: Array<AvailabilityEvent>;
  timeline_complete_until_hours?: number | null;
  event_evidence_refs?: Array<string>;
}

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

export interface ValidationErrorResponse {
  detail: Array<ValidationIssue>;
}

export interface ValidationIssue {
  field: Array<string | number>;
  reason: string;
  type: string;
}

export interface ValueProfile {
  profile_id: string;
  version: string;
  policy_type?: "demo_policy";
  approval_status?: "demo_only";
  criteria: Array<Criterion>;
  evidence_ref: string;
}

export interface WeymouthInput {
  boundary: "fixed_outlet_pressure" | "fixed_standard_flow" | "fixed_pressures_and_flow";
  application?: "public_reference" | "unclassified" | "low_pressure_distribution";
  inlet_psia?: number | null;
  outlet_psia?: number | null;
  flow_mmscfd?: number | null;
  internal_diameter_in?: number | null;
  length_mi?: number | null;
  gas_specific_gravity?: number | null;
  compressibility?: number | null;
  efficiency?: number | null;
  base_pressure_psia?: number | null;
  base_temperature_rankine?: number | null;
  gas_temperature_rankine?: number | null;
}

export interface WeymouthRun {
  status: "converged" | "missing_inputs" | "invalid_inputs" | "outside_model_scope" | "non_converged";
  required_inputs: number;
  supplied_inputs: number;
  missing_fields?: Array<string>;
  reason?: string | null;
  solution?: WeymouthSolution | null;
}

export interface WeymouthSolution {
  inlet_psia: number;
  outlet_psia: number;
  flow_mmscfd: number;
  internal_diameter_in: number;
  equation_residual_psi2: number;
  standard_flow_balance_residual_mmscfd: number;
  iterations: number;
}

export interface WeymouthSourceInput {
  gas_specific_gravity: number;
  compressibility: number;
  base_pressure: GasPressure;
  base_temperature: GasTemperature;
  gas_temperature: GasTemperature;
  boundary: "fixed_outlet_pressure" | "fixed_standard_flow" | "fixed_pressures_and_flow";
  inlet_pressure: GasPressure;
  outlet_pressure?: GasPressure | null;
  atmosphere?: GasPressure | null;
  flow?: GasVolumeRate | null;
  internal_diameter?: PipeDiameter | null;
  length: PipeLength;
  efficiency: number;
}
