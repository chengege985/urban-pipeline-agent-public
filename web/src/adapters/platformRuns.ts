import type {
  AffectedService,
  DemoConstraints,
  PlanComparison,
  PlanComparisonRow,
  PlanRowStatus,
  ServiceImpact,
  ServiceObject,
} from "../domain/types";
import { PlatformContractError } from "./platformDataset";

/**
 * `/simulation-runs` 与 `/decision-runs` 在 OpenAPI 里没有声明响应 schema，
 * 因此这里的手写类型对应 `01_工程项目/src/unified/contracts.py` 的冻结 DTO；
 * Python 侧 `tests/test_unified_contracts.py` 保证真实响应与那份 DTO 同形。
 */
export type RunStatus = "queued" | "running" | "succeeded" | "partial" | "insufficient_data" | "infeasible" | "failed";

export interface RunEnvelope {
  run_id: string;
  kind: "assessment" | "simulation" | "decision" | "assistant";
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  request: Record<string, unknown>;
  result: Record<string, unknown> | null;
  review_status: string | null;
  review_revision: number;
  execution_status: "not_dispatched";
}

export interface DisconnectedCounts {
  household?: number | null;
  person?: number | null;
  facility?: number | null;
}

export interface SimulationRunResult {
  status: "succeeded" | "partial" | "insufficient_data";
  engine_type: "topology";
  algorithm_version: string;
  snapshot_id: string;
  as_of: string;
  data_mode: string;
  source_status: string;
  validation_status: string;
  affected_service_ids: string[];
  affected_critical_facility_ids: string[];
  potentially_disconnected_counts: DisconnectedCounts;
  known_count_subtotals: DisconnectedCounts;
  unknown_count_service_ids: string[];
  newly_unreachable_node_ids: string[];
  isolated_valve_ids: string[];
  evidence_refs: string[];
  limitations: string[];
  missing_items: string[];
  source_node_ids?: string[];
}

export interface DecisionPlanRowResult {
  plan_id: string;
  label: string;
  status: PlanRowStatus;
  utility?: number | null;
  total_cost_fen?: number | null;
  metrics?: Record<string, number | null> | null;
  reasons?: string[];
  selection_reason?: string | null;
  baseline_key?: string | null;
}

export interface DecisionRunResult {
  status: "succeeded" | "infeasible" | "insufficient_data" | "unsupported" | "invalid_input";
  engine_type: "demo_policy";
  decision_algorithm_version: string;
  snapshot_id: string;
  as_of: string;
  data_mode: string;
  source_status: string;
  validation_status: string;
  baseline_key: string;
  plans: DecisionPlanRowResult[];
  recommended_plan_id: string | null;
  recommendation_status: "pending_review" | null;
  tie_plan_ids: string[];
  ranked_plan_ids: string[];
  missing_items: unknown[];
  decision_limitations: string[];
  evidence_refs: string[];
}

const RUN_STATUSES: readonly RunStatus[] = [
  "queued", "running", "succeeded", "partial", "insufficient_data", "infeasible", "failed",
];

export function isTerminal(status: RunStatus): boolean {
  return status !== "queued" && status !== "running";
}

function str(value: unknown, field: string): string {
  if (typeof value !== "string" || value === "") throw new PlatformContractError(`Run 字段 ${field} 缺失或不是字符串`);
  return value;
}

function strList(value: unknown, field: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new PlatformContractError(`Run 字段 ${field} 不是字符串数组`);
  }
  return [...(value as string[])];
}

function counts(value: unknown): DisconnectedCounts {
  if (!value || typeof value !== "object") throw new PlatformContractError("缺少 potentially_disconnected_counts");
  const row = value as Record<string, unknown>;
  const pick = (key: string): number | null => {
    const item = row[key];
    if (item === null || item === undefined) return null;
    if (typeof item !== "number" || !Number.isFinite(item)) throw new PlatformContractError(`计数 ${key} 不是有限数值`);
    return item;
  };
  return { household: pick("household"), person: pick("person"), facility: pick("facility") };
}

export function parseRunEnvelope(value: unknown): RunEnvelope {
  if (!value || typeof value !== "object") throw new PlatformContractError("Run 响应不是对象");
  const row = value as Record<string, unknown>;
  const status = row.status;
  if (typeof status !== "string" || !(RUN_STATUSES as readonly string[]).includes(status)) {
    throw new PlatformContractError(`未知 Run 状态：${String(status)}`);
  }
  const kind = row.kind;
  if (kind !== "assessment" && kind !== "simulation" && kind !== "decision" && kind !== "assistant") {
    throw new PlatformContractError(`未知 Run 类型：${String(kind)}`);
  }
  return {
    run_id: str(row.run_id, "run_id"),
    kind,
    status: status as RunStatus,
    created_at: str(row.created_at, "created_at"),
    finished_at: typeof row.finished_at === "string" ? row.finished_at : null,
    request: (row.request as Record<string, unknown>) ?? {},
    result: (row.result as Record<string, unknown> | null) ?? null,
    review_status: typeof row.review_status === "string" ? row.review_status : null,
    review_revision: typeof row.review_revision === "number" ? row.review_revision : 0,
    execution_status: "not_dispatched",
  };
}

export function parseSimulationResult(run: RunEnvelope): SimulationRunResult {
  const result = run.result;
  if (!result) throw new PlatformContractError("Run 没有 result，不能映射拓扑影响");
  if (result.engine_type !== "topology") throw new PlatformContractError(`Run 不是拓扑引擎：${String(result.engine_type)}`);
  return {
    status: result.status as SimulationRunResult["status"],
    engine_type: "topology",
    algorithm_version: str(result.algorithm_version, "algorithm_version"),
    snapshot_id: str(result.snapshot_id, "snapshot_id"),
    as_of: str(result.as_of, "as_of"),
    data_mode: str(result.data_mode, "data_mode"),
    source_status: str(result.source_status, "source_status"),
    validation_status: str(result.validation_status, "validation_status"),
    affected_service_ids: strList(result.affected_service_ids, "affected_service_ids"),
    affected_critical_facility_ids: strList(result.affected_critical_facility_ids, "affected_critical_facility_ids"),
    potentially_disconnected_counts: counts(result.potentially_disconnected_counts),
    known_count_subtotals: counts(result.known_count_subtotals),
    unknown_count_service_ids: strList(result.unknown_count_service_ids, "unknown_count_service_ids"),
    newly_unreachable_node_ids: strList(result.newly_unreachable_node_ids, "newly_unreachable_node_ids"),
    isolated_valve_ids: strList(result.isolated_valve_ids, "isolated_valve_ids"),
    evidence_refs: strList(result.evidence_refs, "evidence_refs"),
    limitations: strList(result.limitations, "limitations"),
    missing_items: strList(result.missing_items, "missing_items"),
    source_node_ids: Array.isArray(result.source_node_ids) ? (result.source_node_ids as string[]) : undefined,
  };
}

export function parseDecisionResult(run: RunEnvelope): DecisionRunResult {
  const result = run.result;
  if (!result) throw new PlatformContractError("Run 没有 result，不能映射方案比较");
  if (result.engine_type !== "demo_policy") throw new PlatformContractError(`Run 不是给定候选比较：${String(result.engine_type)}`);
  if (!Array.isArray(result.plans)) throw new PlatformContractError("决策结果缺少 plans 数组");
  const plans = (result.plans as unknown[]).map((item) => {
    if (!item || typeof item !== "object") throw new PlatformContractError("候选方案行不是对象");
    const row = item as Record<string, unknown>;
    const status = row.status;
    if (typeof status !== "string") throw new PlatformContractError("候选方案行缺少 status");
    return {
      plan_id: str(row.plan_id, "plan_id"),
      label: str(row.label, "label"),
      status: status as PlanRowStatus,
      utility: typeof row.utility === "number" ? row.utility : null,
      total_cost_fen: typeof row.total_cost_fen === "number" ? row.total_cost_fen : null,
      metrics: (row.metrics as Record<string, number | null> | null) ?? null,
      reasons: Array.isArray(row.reasons) ? (row.reasons as string[]) : [],
      selection_reason: typeof row.selection_reason === "string" ? row.selection_reason : null,
      baseline_key: typeof row.baseline_key === "string" ? row.baseline_key : null,
    };
  });
  return {
    status: result.status as DecisionRunResult["status"],
    engine_type: "demo_policy",
    decision_algorithm_version: str(result.decision_algorithm_version, "decision_algorithm_version"),
    snapshot_id: str(result.snapshot_id, "snapshot_id"),
    as_of: str(result.as_of, "as_of"),
    data_mode: str(result.data_mode, "data_mode"),
    source_status: str(result.source_status, "source_status"),
    validation_status: str(result.validation_status, "validation_status"),
    baseline_key: str(result.baseline_key, "baseline_key"),
    plans,
    recommended_plan_id: typeof result.recommended_plan_id === "string" ? result.recommended_plan_id : null,
    recommendation_status: result.recommendation_status === "pending_review" ? "pending_review" : null,
    tie_plan_ids: Array.isArray(result.tie_plan_ids) ? (result.tie_plan_ids as string[]) : [],
    ranked_plan_ids: Array.isArray(result.ranked_plan_ids) ? (result.ranked_plan_ids as string[]) : [],
    missing_items: Array.isArray(result.missing_items) ? result.missing_items : [],
    decision_limitations: Array.isArray(result.decision_limitations) ? (result.decision_limitations as string[]) : [],
    evidence_refs: Array.isArray(result.evidence_refs) ? (result.evidence_refs as string[]) : [],
  };
}

export interface ImpactMappingInput {
  scenarioId: string;
  isolationPlanId: string;
  faultPipeIds: string[];
  services: ServiceObject[];
}

/** 只搬运平台拓扑结果；前端不重算断连、不把未知计数写成 0。 */
export function toServiceImpact(result: SimulationRunResult, input: ImpactMappingInput): ServiceImpact {
  const byId = new Map(input.services.map((service) => [service.id, service]));
  const affected: AffectedService[] = result.affected_service_ids.map((serviceId) => {
    const service = byId.get(serviceId);
    if (!service) throw new PlatformContractError(`受影响服务 ${serviceId} 不在数据集中`);
    return {
      serviceId,
      name: service.name,
      type: service.type,
      critical: service.critical,
      reason: "平台拓扑计算：正常状态可达，当前情景不可达",
      households: service.households,
      facilities: service.facilities,
    };
  });
  const limitations = [...result.limitations];
  if (result.unknown_count_service_ids.length > 0) {
    limitations.push(`以下服务计数口径缺失，合计不可用：${result.unknown_count_service_ids.join("、")}`);
  }
  return {
    scenarioId: input.scenarioId,
    isolationPlanId: input.isolationPlanId,
    status: result.status === "succeeded" ? "succeeded" : "partial",
    failedPipeIds: [...input.faultPipeIds],
    closedPipeIds: [],
    valveIds: [...result.isolated_valve_ids],
    activeSourceIds: [...(result.source_node_ids ?? [])],
    unreachableNodeIds: [...result.newly_unreachable_node_ids],
    affectedServices: affected,
    affectedCriticalServiceIds: [...result.affected_critical_facility_ids],
    affectedHouseholds: result.potentially_disconnected_counts.household ?? null,
    affectedFacilities: result.potentially_disconnected_counts.facility ?? null,
    evidenceIds: [...result.evidence_refs],
    limitations,
  };
}

function fenToYuan(fen: number | null | undefined): number | null {
  if (fen === null || fen === undefined) return null;
  if (!Number.isFinite(fen)) throw new PlatformContractError("费用不是有限数值");
  return fen / 100;
}

/** 元→分必须精确；非整数元直接拒绝，不做静默四舍五入。 */
export function yuanToFen(yuan: number): number {
  if (!Number.isFinite(yuan) || yuan < 0) throw new PlatformContractError(`预算必须是非负有限数值：${yuan}`);
  const fen = yuan * 100;
  if (!Number.isInteger(fen)) throw new PlatformContractError(`预算 ${yuan} 元无法精确换算为分`);
  return fen;
}

export function toPlanComparison(result: DecisionRunResult, constraints: DemoConstraints, scenarioId: string): PlanComparison {
  const ranked = new Map(result.ranked_plan_ids.map((id, index) => [id, index + 1]));
  const rows: PlanComparisonRow[] = result.plans.map((row, index) => {
    const feasible = row.status === "succeeded" ? true : row.status === "infeasible" ? false : null;
    const reasons = [...(row.reasons ?? [])];
    if (feasible === null && reasons.length === 0) reasons.push("平台未给出确定结论，结果未知");
    // 平台只在得出完整指标的候选上提供 metrics；未知候选的费用在行顶层。
    const costFen = row.metrics?.total_cost_fen ?? row.total_cost_fen ?? null;
    return {
      planId: row.plan_id,
      name: row.label,
      type: null,
      costYuan: fenToYuan(costFen),
      durationHours: null,
      riskReduction: null,
      restoredServiceCount: null,
      remainingAffectedServices: row.metrics?.remaining_disconnected_services ?? null,
      resourceRequirements: {},
      feasible,
      violations: reasons,
      utility: row.utility ?? null,
      rank: ranked.get(row.plan_id) ?? result.plans.length + index + 1,
      note: row.selection_reason ?? "",
      sourceStatus: row.status,
      costFen: costFen ?? null,
    };
  });
  const limitations = [...result.decision_limitations];
  if (result.missing_items.length > 0) limitations.push("部分候选的结论未知；未知不等于不可行，也不构成推荐。");
  if (rows.some((row) => row.feasible === null)) limitations.push("存在结果未知的候选，平台已取消推荐。");
  return {
    status: result.status === "succeeded" ? "succeeded" : "insufficient_data",
    decisionAlgorithmVersion: result.decision_algorithm_version,
    referenceCostYuan: null,
    effectBasis: "platform_given_candidates",
    scenarioId,
    planIds: result.plans.map((row) => row.plan_id),
    rows,
    recommendedPlanId: result.recommended_plan_id,
    constraints: {
      budgetYuan: constraints.budgetYuan,
      resourceCapacities: { ...constraints.resourceCapacities },
      confirmedDependencies: [...constraints.confirmedDependencies],
    },
    utilityNote:
      "平台按快照内固定价值配置计算效用；前端不重算效用，也不提供风险下降或工期口径。" +
      "平台模式不使用演示依赖开关。",
    limitations,
  };
}
