import type { AgentContext, PlanTimelineResult, TimelineRequest, WaterPulseDataset } from "../domain/types";
import { TIMELINE_ALGORITHM_VERSION } from "../domain/constants";
import { inputKey } from "./context";

export function normalizedTimelineRequest(request: TimelineRequest) {
  return {
    scenarioId: request.scenarioId, isolationPlanId: request.isolationPlanId, planId: request.planId,
    targetServiceIds: request.targetServiceIds ? [...request.targetServiceIds].sort() : null,
    horizonHours: request.horizonHours,
    actionSchedule: [...(request.actionSchedule ?? [])].map((item) => Object.fromEntries(
      Object.entries(item).filter(([, value]) => value != null),
    )).sort((a, b) => String(a.actionId).localeCompare(String(b.actionId))),
  };
}

/** Not an authorization token: identifies the data underlying a frozen timeline. */
export function timelineBasisKey(dataset: WaterPulseDataset): string {
  return inputKey([dataset.meta.dataVersion, TIMELINE_ALGORITHM_VERSION, dataset.timeline,
    dataset.nodes, dataset.pipes, dataset.valves, dataset.services, dataset.scenarios,
    dataset.isolationPlans, dataset.plans]);
}

export function timelineMatchesRequest(result: PlanTimelineResult, request: TimelineRequest): boolean {
  return Boolean(result.inputRequest && result.scenarioId === request.scenarioId &&
    result.isolationPlanId === request.isolationPlanId && result.planId === request.planId &&
    result.horizonHours === request.horizonHours &&
    inputKey([...result.targetServiceIds].sort()) === inputKey([...(request.targetServiceIds ?? [])].sort()) &&
    inputKey(normalizedTimelineRequest(result.inputRequest)) === inputKey(normalizedTimelineRequest(request)));
}

export function timelineMatchesContext(context: AgentContext): boolean {
  if (!context.timeline || !context.scenarioId || !context.isolationPlanId || !context.planId ||
      context.targetServiceIds === undefined || context.horizonHours === undefined ||
      !context.timelineDataVersion || !context.timelineAsOf) return false;
  return context.timeline.algorithmVersion === TIMELINE_ALGORITHM_VERSION &&
    context.timeline.timelineDataVersion === context.timelineDataVersion && context.timeline.asOf === context.timelineAsOf &&
    timelineMatchesRequest(context.timeline, {
      scenarioId: context.scenarioId, isolationPlanId: context.isolationPlanId, planId: context.planId,
      targetServiceIds: context.targetServiceIds, horizonHours: context.horizonHours,
      actionSchedule: context.actionSchedule,
    });
}
