export type ProviderMode = "demo" | "live" | "unavailable";

export type ServiceType = "医院" | "学校" | "商业" | "居民区" | "未知";

export type PipeMaterial = "PE" | "球墨铸铁" | "铸铁" | "钢" | "PVC" | "未知";

export type PressureLevel = "低压" | "常压" | "高压" | "未知";

export type MonitoringLevel = "正常" | "关注" | "异常" | "未知";

export type HealthGrade = "基本健康" | "关注" | "重点检查" | "数据不足";

export type FactorKey =
  | "material"
  | "age"
  | "diameter"
  | "depth"
  | "pressure"
  | "leaks"
  | "repairs"
  | "monitoring";

export interface FactorDefinition {
  key: FactorKey;
  label: string;
  weight: number;
  description: string;
}

export interface Pipe {
  id: string;
  name: string;
  fromNodeId: string;
  toNodeId: string;
  material: PipeMaterial;
  installYear: number | null;
  diameterMm: number | null;
  depthM: number | null;
  pressure: PressureLevel;
  leakCount: number | null;
  repairCount: number | null;
  monitoring: MonitoringLevel;
  /** null 表示上游未提供巡检记录；不得用空串或当天日期代替。 */
  lastInspection: string | null;
  serviceIds: string[];
  /** null 表示上游未提供管长；不参与评分。 */
  lengthM: number | null;
  note: string;
}

export interface PipeOverride {
  material?: PipeMaterial;
  installYear?: number | null;
  diameterMm?: number | null;
  depthM?: number | null;
  pressure?: PressureLevel;
  leakCount?: number | null;
  repairCount?: number | null;
  monitoring?: MonitoringLevel;
}

export interface ServiceObject {
  id: string;
  name: string;
  type: ServiceType;
  nodeId: string;
  description: string;
  households: number;
  facilities: number;
  critical: boolean;
}

export interface NetworkNode {
  id: string;
  label: string;
  x: number;
  y: number;
  kind: "source" | "junction" | "service";
  serviceId?: string;
}

export interface NetworkScenario {
  id: string;
  name: string;
  summary: string;
  failedPipeIds: string[];
  activeSourceIds: string[];
  defaultIsolationPlanId: string;
  /** “未知”表示平台模式没有给定严重度分级，不等于低风险。 */
  severity: "高" | "中" | "低" | "未知";
  note: string;
}

export interface Valve {
  id: string;
  label: string;
  pipeId: string;
  nodeId: string;
  operable: boolean;
}

export interface IsolationPlan {
  id: string;
  scenarioId: string;
  name: string;
  description: string;
  valveIds: string[];
  closedPipeIds: string[];
  evidenceIds: string[];
  version: string;
}

export interface AffectedService {
  serviceId: string;
  name: string;
  type: ServiceType;
  critical: boolean;
  reason: string;
  households: number;
  facilities: number;
}

export interface ServiceImpact {
  /** 已持久化的平台拓扑 Run，仅用于读取对应报告。 */
  runId?: string;
  scenarioId: string;
  isolationPlanId: string;
  status: "succeeded" | "partial";
  failedPipeIds: string[];
  closedPipeIds: string[];
  valveIds: string[];
  activeSourceIds: string[];
  unreachableNodeIds: string[];
  affectedServices: AffectedService[];
  affectedCriticalServiceIds: string[];
  /** null 表示计数口径缺失或未知；不得用 0 代替。 */
  affectedHouseholds: number | null;
  affectedFacilities: number | null;
  evidenceIds: string[];
  limitations: string[];
}

export type TimeMetricStatus =
  | "succeeded"
  | "already_available"
  | "insufficient_data"
  | "not_recovered_within_horizon"
  | "not_applicable";

export interface TimeMetric {
  valueHours: number | null;
  status: TimeMetricStatus;
  reason: string;
  criterionId: "topological_reachability";
  timeBasis: "planned";
  /** null 表示平台未返返观察窗口；不得用默认 8 小时代替。 */
  observationHorizonHours: number | null;
  beyondHorizon?: boolean;
  lowerBoundHours?: number;
  lowerBoundExclusive?: boolean;
}

export interface TimelineActionSchedule {
  actionId: string;
  finishOffsetHours?: number;
  startOffsetHours?: number;
  durationHours?: number;
}

export interface TimelineRequest {
  scenarioId: string;
  isolationPlanId: string;
  planId: string;
  targetServiceIds?: string[];
  horizonHours: number;
  actionSchedule?: TimelineActionSchedule[];
}

export interface TimelineEventDefinition {
  id: string;
  scenarioId: string;
  planId: string;
  actionId: string;
  label: string;
  edgeIds: string[];
  valveIds: string[];
  effectiveOffsetHours: number;
  evidenceIds: string[];
}

export interface TimelineTaskDefinition {
  scenarioId: string;
  planId: string;
  actionId: string;
  label: string;
  finishOffsetHours: number | null;
  evidenceIds: string[];
}

export interface TimelineDataset {
  version: string;
  asOf: string;
  events: TimelineEventDefinition[];
  tasks: TimelineTaskDefinition[];
}

export interface TimelineEvent {
  atHours: number;
  actionIds: string[];
  restoredEdgeIds: string[];
  serviceAvailability: Record<string, boolean>;
  evidenceIds: string[];
}

export interface TimelineGate {
  status: "succeeded" | "partial" | "insufficient_data" | "unsupported" | "invalid_input";
  canCompute: boolean;
  supportedOutputs: string[];
  unsupportedOutputs: string[];
  availableOutputs: string[];
  missingItems: string[];
  calculationStatus?: "succeeded" | "partial" | "insufficient_data" | "unsupported" | "invalid_input";
  authorizationChecked: false;
  registryVersion: string;
  requestedSemantics: "planned_topological_reachability";
  evidenceIds: string[];
}

export interface PlanTimelineResult {
  inputRequest?: TimelineRequest;
  timelineDataVersion?: string;
  status: "succeeded" | "partial" | "insufficient_data" | "invalid_input" | "unsupported";
  timelineStatus: "succeeded" | "insufficient_data" | "unsupported" | "invalid_input";
  scenarioId: string;
  isolationPlanId: string;
  planId: string;
  targetServiceIds: string[];
  horizonHours: number;
  asOf: string;
  planCompletion: TimeMetric;
  firstTargetServiceRestored: TimeMetric;
  allTargetServicesRestored: TimeMetric;
  serviceFirstRestored: Record<string, TimeMetric>;
  events: TimelineEvent[];
  evidenceIds: string[];
  issues: string[];
  limitations: string[];
  dataMode: "synthetic" | "fixture_replay" | "real";
  sourceStatus: "constructed" | "source_unknown" | "declared_real";
  algorithmVersion: string;
  timeBasis: "planned";
  criterionId: "topological_reachability";
}

export interface HealthFactor {
  key: FactorKey;
  label: string;
  weight: number;
  score: number | null;
  weightedRisk: number;
  contribution: number;
  detail: string;
}

export interface HealthAssessment {
  pipeId: string;
  status: "succeeded" | "insufficient_data";
  score: number | null;
  grade: HealthGrade;
  confidence: number;
  validWeight: number;
  factors: HealthFactor[];
  missing: string[];
  version: string;
  summary: string;
}

export type MaintenancePlanType = "抢修" | "更换" | "监测";

export interface MaintenancePlan {
  id: string;
  name: string;
  description: string;
  type: MaintenancePlanType;
  costYuan: number;
  durationHours: number;
  riskReduction: number;
  restoresAffected: boolean;
  resourceRequirements: Record<string, number>;
  requiredDependencies: string[];
  constraints: string;
  evidenceIds: string[];
}

export interface DemoConstraints {
  budgetYuan: number;
  resourceCapacities: Record<string, number>;
  confirmedDependencies: string[];
}

/** 平台候选状态；unknown 类状态不等于不可行。 */
export type PlanRowStatus =
  | "succeeded"
  | "infeasible"
  | "insufficient_data"
  | "unsupported"
  | "invalid_input";

export interface PlanComparisonRow {
  planId: string;
  name: string;
  /** null 表示平台未提供方案类型；不使用演示分类代替。 */
  type: MaintenancePlanType | null;
  /** null 表示费用未知；不得用 0 代替。 */
  costYuan: number | null;
  /** null 表示未提供工期；不等同于恢复时间。 */
  durationHours: number | null;
  /** null 表示该口径不适用于当前数据源（如平台模式无风险下降指标）。 */
  riskReduction: number | null;
  restoredServiceCount: number | null;
  remainingAffectedServices: number | null;
  resourceRequirements: Record<string, number>;
  /** null 表示结果未知，既不是可行也不是不可行。 */
  feasible: boolean | null;
  violations: string[];
  utility: number | null;
  rank: number;
  note: string;
  /** 平台返回的原始候选状态；演示模式不填。 */
  sourceStatus?: PlanRowStatus;
  /** 平台费用原值（分），避免元换算后再回推。 */
  costFen?: number | null;
}

export interface PlanComparison {
  /** 已完成的平台Run，报告只读取服务端已持久化结果。 */
  runId?: string;
  status: "succeeded" | "insufficient_data";
  decisionAlgorithmVersion: string;
  referenceCostYuan: number | null;
  effectBasis: "synthetic_preset" | "platform_given_candidates";
  scenarioId: string;
  planIds: string[];
  rows: PlanComparisonRow[];
  recommendedPlanId: string | null;
  constraints: DemoConstraints;
  utilityNote: string;
  limitations: string[];
}

export type ReviewStatus = "draft" | "pending_review" | "approved" | "rejected";

export interface DecisionSession {
  id: string;
  createdAt: string;
  pipeId: string;
  scenarioId: string;
  isolationPlanId: string;
  budgetYuan: number;
  resourceCapacities: Record<string, number>;
  confirmedDependencies: string[];
  selectedPlanId: string;
  reviewStatus: ReviewStatus;
  reviewComment: string;
  reviewer: string;
  reviewRevision: number;
  reviewUpdatedAt: string;
  dataVersion: string;
  algorithmVersion: string;
  evidenceIds: string[];
  override?: PipeOverride;
  decisionAlgorithmVersion?: string;
  snapshot?: DecisionSnapshot;
}

/** Immutable inputs and computed outputs used when the user selected a plan. */
export interface DecisionSnapshot {
  timelineDataVersion?: string;
  timelineBasisKey?: string;
  pipe: Pipe;
  assessment: HealthAssessment;
  scenario: NetworkScenario;
  isolationPlan: IsolationPlan;
  impact: ServiceImpact;
  comparison: PlanComparison;
  selectedPlan: MaintenancePlan;
  timeline?: PlanTimelineResult;
  timelineGate?: TimelineGate;
  targetServiceIds?: string[];
  horizonHours?: number;
  actionSchedule?: TimelineActionSchedule[];
}

export interface AnalysisContext {
  timelineAsOf?: string;
  actionSchedule?: TimelineActionSchedule[];
  timelineDataVersion?: string;
  pipeId: string;
  scenarioId: string;
  isolationPlanId: string;
  override: PipeOverride;
  constraints: DemoConstraints;
  dataVersion: string;
  modelVersion: string;
  planId?: string;
  targetServiceIds?: string[];
  horizonHours?: number;
  timeline?: PlanTimelineResult;
  timelineGate?: TimelineGate;
}

export type EvidenceKind = "data" | "rule" | "scenario" | "plan";

export interface EvidenceReference {
  id: string;
  kind: EvidenceKind;
  label: string;
  detail: string;
  version: string;
}

export type ToolStatus = "succeeded" | "partial" | "refused" | "insufficient_data";

export interface AgentToolCall {
  id: string;
  tool: string;
  label: string;
  status: ToolStatus;
  summary: string;
  detail?: string;
}

export interface AgentContext {
  timelineAsOf?: string;
  actionSchedule?: TimelineActionSchedule[];
  timelineDataVersion?: string;
  pipeId?: string;
  scenarioId?: string;
  pipe?: Pipe;
  assessment?: HealthAssessment;
  impact?: ServiceImpact;
  isolationPlanId?: string;
  override?: PipeOverride;
  constraints?: DemoConstraints;
  planId?: string;
  targetServiceIds?: string[];
  horizonHours?: number;
  timeline?: PlanTimelineResult;
  timelineGate?: TimelineGate;
}

export interface AgentResponse {
  id: string;
  status: "succeeded" | "partial" | "refused" | "insufficient_data";
  mode: "demo" | "live";
  answer: string;
  toolCalls: AgentToolCall[];
  evidenceIds: string[];
  limitations: string[];
  suggestedQuestions: string[];
  refused: boolean;
  engine?: "template" | "llm";
  fallbackReason?: string;
  answerOrigin?: string;
}

export interface ProviderMeta {
  mode: ProviderMode;
  /** 数据来源：demo 为离线演示，platform 为统一 FastAPI；旧接口不填。 */
  providerKind?: "demo" | "platform" | "legacy-live";
  appVersion: string;
  dataVersion: string;
  modelVersion: string;
  disclaimer: string;
}

export interface PlanCatalogEntry {
  id: string;
  name: string;
}

export interface WaterPulseDataset {
  meta: ProviderMeta;
  nodes: NetworkNode[];
  pipes: Pipe[];
  services: ServiceObject[];
  scenarios: NetworkScenario[];
  valves: Valve[];
  isolationPlans: IsolationPlan[];
  plans: MaintenancePlan[];
  /** 平台候选方案目录；plans 为空时页面用它选择服务端候选，不补造演示字段。 */
  planCatalog?: PlanCatalogEntry[];
  timeline: TimelineDataset;
  assessments: Record<string, HealthAssessment>;
  impacts: Record<string, ServiceImpact>;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text?: string;
  response?: AgentResponse;
  createdAt: string;
}
