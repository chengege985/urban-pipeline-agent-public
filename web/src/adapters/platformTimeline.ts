import type {
  FieldAvailability,
  RecoveryTimes,
  TimeMetric as PlatformTimeMetric,
  TimelineCheckResponse,
  TimelineEvaluationResponse,
} from "../generated/platformOpenapi";
import type {
  PlanTimelineResult,
  TimeMetric,
  TimeMetricStatus,
  TimelineEvent,
  TimelineGate,
  TimelineRequest,
} from "../domain/types";
import { PlatformContractError } from "./platformDataset";

const METRIC_STATUSES: readonly TimeMetricStatus[] = [
  "succeeded",
  "already_available",
  "insufficient_data",
  "not_recovered_within_horizon",
  "not_applicable",
];

function metricStatus(value: string | undefined): TimeMetricStatus {
  return (METRIC_STATUSES as readonly (string | undefined)[]).includes(value)
    ? (value as TimeMetricStatus)
    : "insufficient_data";
}

/** 平台未返回指标时显式标为 insufficient_data，不用 0 或默认窗口代填。 */
export function toTimeMetric(value: PlatformTimeMetric | null | undefined, fallbackReason: string): TimeMetric {
  if (!value) {
    return {
      valueHours: null,
      status: "insufficient_data",
      reason: fallbackReason,
      criterionId: "topological_reachability",
      timeBasis: "planned",
      observationHorizonHours: null,
    };
  }
  return {
    valueHours: value.value_hours ?? null,
    status: metricStatus(value.status),
    reason: value.reason ?? "",
    criterionId: "topological_reachability",
    timeBasis: "planned",
    observationHorizonHours: value.observation_horizon_hours ?? null,
    beyondHorizon: value.beyond_horizon ?? undefined,
    lowerBoundHours: value.lower_bound_hours ?? undefined,
    lowerBoundExclusive: value.lower_bound_exclusive ?? undefined,
  };
}

function recoveryMetrics(recovery: RecoveryTimes | null | undefined, reason: string) {
  const perService: Record<string, TimeMetric> = {};
  for (const [serviceId, metric] of Object.entries(recovery?.service_first_restored_hours ?? {})) {
    perService[serviceId] = toTimeMetric(metric, reason);
  }
  return {
    planCompletion: toTimeMetric(recovery?.plan_completion_hours, reason),
    firstTarget: toTimeMetric(recovery?.first_target_service_restored_hours, reason),
    allTargets: toTimeMetric(recovery?.all_target_services_restored_hours, reason),
    perService,
  };
}

function toEvents(response: TimelineEvaluationResponse): TimelineEvent[] {
  const result = response.result;
  const evidence = [...(result.timeline_request?.event_evidence_refs ?? [])];
  return (result.graph_events ?? []).map((event) => ({
    atHours: event.at_hours,
    actionIds: [...(event.action_ids ?? [])],
    restoredEdgeIds: [...(event.restored_edge_ids ?? [])],
    serviceAvailability: { ...(event.service_availability ?? {}) },
    evidenceIds: evidence,
  }));
}

function toIssues(response: TimelineEvaluationResponse): string[] {
  const values = [...(response.result.issues ?? []), ...(response.missing_items ?? [])];
  return values.map((value) => (typeof value === "string" ? value : JSON.stringify(value)));
}

export function toPlanTimelineResult(
  request: TimelineRequest,
  response: TimelineEvaluationResponse,
): PlanTimelineResult {
  const result = response.result;
  const reason = "平台未返回该指标；缺少事件覆盖或证据";
  const metrics = recoveryMetrics(result.recovery_times, reason);
  const status = result.status as PlanTimelineResult["status"];
  return {
    inputRequest: request,
    timelineDataVersion: "platform-action-schedule",
    status,
    timelineStatus: (result.timeline_status ?? "insufficient_data") as PlanTimelineResult["timelineStatus"],
    scenarioId: request.scenarioId,
    isolationPlanId: request.isolationPlanId,
    planId: request.planId,
    targetServiceIds: [...(request.targetServiceIds ?? [])],
    horizonHours: request.horizonHours,
    asOf: response.dataset.as_of,
    planCompletion: metrics.planCompletion,
    firstTargetServiceRestored: metrics.firstTarget,
    allTargetServicesRestored: metrics.allTargets,
    serviceFirstRestored: metrics.perService,
    events: toEvents(response),
    evidenceIds: [...(response.evidence_ids ?? [])],
    issues: toIssues(response),
    limitations: [...(response.limitations ?? [])],
    dataMode: response.dataset.data_mode as PlanTimelineResult["dataMode"],
    sourceStatus: response.dataset.source_status as PlanTimelineResult["sourceStatus"],
    // 平台区分“能力版本 1.0.1”与“恢复汇总版本 1.0.0”；UI 展示能力版本。
    algorithmVersion: response.algorithm.version,
    timeBasis: "planned",
    criterionId: "topological_reachability",
  };
}

function missingText(items: FieldAvailability["missing_items"]): string[] {
  return (items ?? []).map((item) => (typeof item === "string" ? item : JSON.stringify(item)));
}

/** 结构守卫：缺少必需字段时显式失败，不用局部数据拼出时间线。 */
export function parseTimelineCheck(value: unknown): TimelineCheckResponse {
  if (!value || typeof value !== "object") throw new PlatformContractError("时间线预检响应不是对象");
  const row = value as Record<string, unknown>;
  const result = row.result as Record<string, unknown> | undefined;
  if (typeof row.status !== "string" || !row.dataset || !row.algorithm || !result ||
      typeof result.registry_version !== "string" || typeof result.can_compute !== "boolean" ||
      !Array.isArray(row.field_availability)) {
    throw new PlatformContractError("时间线预检响应缺少 status/dataset/algorithm/result 或 field_availability");
  }
  return value as TimelineCheckResponse;
}

export function parseTimelineEvaluation(value: unknown): TimelineEvaluationResponse {
  if (!value || typeof value !== "object") throw new PlatformContractError("时间线响应不是对象");
  const row = value as Record<string, unknown>;
  const result = row.result as Record<string, unknown> | undefined;
  const dataset = row.dataset as Record<string, unknown> | undefined;
  const algorithm = row.algorithm as Record<string, unknown> | undefined;
  if (typeof row.status !== "string" || !result || typeof result.status !== "string" ||
      !dataset || typeof dataset.as_of !== "string" || typeof algorithm?.version !== "string") {
    throw new PlatformContractError("时间线响应缺少 status/result/dataset/algorithm.version");
  }
  return value as TimelineEvaluationResponse;
}

export function toTimelineGate(response: TimelineCheckResponse): TimelineGate {
  const check = response.result;
  return {
    status: response.status as TimelineGate["status"],
    canCompute: check.can_compute,
    supportedOutputs: [...(check.supported_outputs ?? [])],
    unsupportedOutputs: [...(check.unsupported_outputs ?? [])],
    availableOutputs: [...(check.available_outputs ?? [])],
    missingItems: missingText(response.missing_items),
    calculationStatus: check.calculation_status as TimelineGate["calculationStatus"],
    authorizationChecked: false,
    registryVersion: check.registry_version,
    requestedSemantics: "planned_topological_reachability",
    evidenceIds: [...(response.evidence_ids ?? [])],
  };
}

/** 逐字段可用性由平台给出；前端不改写状态，只做透传。 */
export function fieldAvailabilityRows(response: TimelineCheckResponse): Array<{ field: string; status: string; inScope: boolean }> {
  return (response.field_availability ?? []).map((item) => ({
    field: item.field,
    status: item.status,
    inScope: item.in_scope === true,
  }));
}
