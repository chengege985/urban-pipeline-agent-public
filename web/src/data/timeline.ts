import { TIMELINE_AS_OF, TIMELINE_DATA_VERSION } from "../domain/constants";
import type {
  TimelineDataset,
  TimelineEventDefinition,
  TimelineTaskDefinition,
} from "../domain/types";
import { DEMO_PLANS, DEMO_SCENARIOS } from "./network";

function actionId(planId: string): string {
  return `${planId}-action`;
}

/**
 * Independent competition-only timeline data.
 * A repair restores the declared failed edges at hour 6; replacement at hour 24;
 * monitoring has no graph action. The repair task has no finish record until the
 * user supplies one, so plan completion remains insufficient while service
 * restoration can still be calculated.
 */
function buildTimeline(): TimelineDataset {
  const events: TimelineEventDefinition[] = [];
  const tasks: TimelineTaskDefinition[] = [];

  for (const scenario of DEMO_SCENARIOS) {
    for (const plan of DEMO_PLANS) {
      const action = actionId(plan.id);
      const commonEvidence = [
        `synthetic:timeline:${scenario.id}:${plan.id}`,
        `scenario:${scenario.id}`,
        `plan:${plan.id}`,
      ];
      if (plan.id === "PLAN-REPAIR") {
        events.push({
          id: `tl-${scenario.id}-repair`,
          scenarioId: scenario.id,
          planId: plan.id,
          actionId: action,
          label: "按抢修方案声明恢复故障管段",
          edgeIds: [...scenario.failedPipeIds],
          valveIds: [],
          effectiveOffsetHours: 6,
          evidenceIds: [...commonEvidence, "synthetic:assumption:repair-restores-at-6h"],
        });
      }
      if (plan.id === "PLAN-REPLACE") {
        events.push({
          id: `tl-${scenario.id}-replace`,
          scenarioId: scenario.id,
          planId: plan.id,
          actionId: action,
          label: "按计划更换方案声明恢复故障管段",
          edgeIds: [...scenario.failedPipeIds],
          valveIds: [],
          effectiveOffsetHours: 24,
          evidenceIds: [...commonEvidence, "synthetic:assumption:replace-restores-at-24h"],
        });
      }
      tasks.push({
        scenarioId: scenario.id,
        planId: plan.id,
        actionId: action,
        label: plan.id === "PLAN-REPAIR" ? "抢修任务" : plan.id === "PLAN-REPLACE" ? "更换任务" : "监测任务",
        finishOffsetHours:
          plan.id === "PLAN-REPAIR" ? null : plan.id === "PLAN-REPLACE" ? 24 : 2,
        evidenceIds: [...commonEvidence, "synthetic:assumption:task-schedule"],
      });
    }
  }

  return { version: TIMELINE_DATA_VERSION, asOf: TIMELINE_AS_OF, events, tasks };
}

export const DEMO_TIMELINE: TimelineDataset = buildTimeline();

export function getTimelineEvents(scenarioId: string, planId: string): TimelineEventDefinition[] {
  return DEMO_TIMELINE.events.filter((event) => event.scenarioId === scenarioId && event.planId === planId);
}

export function getTimelineTasks(scenarioId: string, planId: string): TimelineTaskDefinition[] {
  return DEMO_TIMELINE.tasks.filter((task) => task.scenarioId === scenarioId && task.planId === planId);
}
