import { APP_VERSION, DATA_VERSION, DEMO_MODEL_VERSION, DISCLAIMER } from "../domain/constants";
import type {
  AgentContext,
  AgentResponse,
  DemoConstraints,
  HealthAssessment,
  Pipe,
  PipeOverride,
  PlanComparison,
  PlanTimelineResult,
  ProviderMode,
  ServiceImpact,
  TimelineGate,
  TimelineRequest,
  WaterPulseDataset,
} from "../domain/types";
import {
  DEMO_ISOLATION_PLANS,
  DEMO_NODES,
  DEMO_PIPES,
  DEMO_PLANS,
  DEMO_SCENARIOS,
  DEMO_SERVICES,
  DEMO_VALVES,
  getIsolationPlanById,
  getPipeById,
  getScenarioById,
} from "../data/network";
import { DEMO_TIMELINE } from "../data/timeline";
import { askDemoAssistant } from "../engine/assistant";
import { evaluateDemoHealth } from "../engine/demoHealth";
import { simulateServiceImpact } from "../engine/network";
import { compareDemoPlans } from "../engine/plans";
import { buildPlanTimeline, checkTimelineCapability } from "../engine/timeline";
import type { WaterPulseProvider } from "./types";

export class DemoProvider implements WaterPulseProvider {
  readonly mode: ProviderMode = "demo";

  async loadDataset(): Promise<WaterPulseDataset> {
    const assessments = Object.fromEntries(
      DEMO_PIPES.map((pipe) => [pipe.id, evaluateDemoHealth(pipe)]),
    ) as Record<string, HealthAssessment>;
    const impacts = Object.fromEntries(
      DEMO_SCENARIOS.map((scenario) => {
        const isolation = getIsolationPlanById(scenario.defaultIsolationPlanId);
        return [
          scenario.id,
          simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, scenario, isolation),
        ];
      }),
    ) as Record<string, ServiceImpact>;

    return {
      meta: {
        mode: "demo",
        providerKind: "demo",
        appVersion: APP_VERSION,
        dataVersion: DATA_VERSION,
        modelVersion: DEMO_MODEL_VERSION,
        disclaimer: DISCLAIMER,
      },
      nodes: DEMO_NODES,
      pipes: DEMO_PIPES,
      services: DEMO_SERVICES,
      scenarios: DEMO_SCENARIOS,
      valves: DEMO_VALVES,
      isolationPlans: DEMO_ISOLATION_PLANS,
      plans: DEMO_PLANS,
      timeline: DEMO_TIMELINE,
      assessments,
      impacts,
    };
  }

  async evaluate(pipe: Pipe, override: PipeOverride = {}): Promise<HealthAssessment> {
    return evaluateDemoHealth(pipe, override);
  }

  async simulate(scenarioId: string, isolationPlanId?: string): Promise<ServiceImpact> {
    const scenario = getScenarioById(scenarioId);
    if (!scenario) throw new Error(`未知情景：${scenarioId}`);
    const isolation = getIsolationPlanById(isolationPlanId ?? scenario.defaultIsolationPlanId);
    if (!isolation || isolation.scenarioId !== scenario.id) throw new Error("隔离方案不存在或不属于当前情景。");
    return simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, scenario, isolation);
  }

  async comparePlans(
    scenarioId: string,
    planIds?: string[],
    constraints?: DemoConstraints,
    isolationPlanId?: string,
  ): Promise<PlanComparison> {
    const scenario = getScenarioById(scenarioId);
    if (!scenario) throw new Error(`未知情景：${scenarioId}`);
    const isolation = getIsolationPlanById(isolationPlanId ?? scenario.defaultIsolationPlanId);
    if (!isolation || isolation.scenarioId !== scenario.id) throw new Error("隔离方案不存在或不属于当前情景。");
    const impact = simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, scenario, isolation);
    if (planIds && (new Set(planIds).size !== planIds.length || planIds.some((id) => !DEMO_PLANS.some((plan) => plan.id === id)))) throw new Error("候选方案重复或包含未知ID。");
    const selected = planIds !== undefined
      ? DEMO_PLANS.filter((plan) => planIds.includes(plan.id))
      : DEMO_PLANS;
    return compareDemoPlans(scenarioId, impact, selected, constraints);
  }

  async checkTimeline(request: TimelineRequest): Promise<TimelineGate> {
    const dataset = await this.loadDataset();
    return checkTimelineCapability(dataset, request);
  }

  async timeline(request: TimelineRequest): Promise<PlanTimelineResult> {
    const dataset = await this.loadDataset();
    return buildPlanTimeline(dataset, request);
  }

  async ask(question: string, context: AgentContext): Promise<AgentResponse> {
    const pipe = context.pipe ?? (context.pipeId ? getPipeById(context.pipeId) : undefined);
    const scenario = context.scenarioId ? getScenarioById(context.scenarioId) : undefined;
    const isolation = scenario ? getIsolationPlanById(context.isolationPlanId ?? scenario.defaultIsolationPlanId) : undefined;
    if (scenario && (!isolation || isolation.scenarioId !== scenario.id)) throw new Error("隔离方案不存在或不属于当前情景。");
    const impact =
      (scenario
        ? simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, scenario, isolation)
        : undefined);
    const assessment =
      (pipe ? evaluateDemoHealth(pipe, context.override) : undefined);
    let timeline = undefined;
    let timelineGate = undefined;
    const dataset = await this.loadDataset();
    if (scenario && isolation && context.planId && context.targetServiceIds !== undefined && context.horizonHours !== undefined) {
      const request: TimelineRequest = { scenarioId: scenario.id, isolationPlanId: isolation.id,
        planId: context.planId, targetServiceIds: context.targetServiceIds, horizonHours: context.horizonHours,
        actionSchedule: context.actionSchedule };
      timeline = buildPlanTimeline(dataset, request);
      timelineGate = checkTimelineCapability(dataset, request);
    }
    return { ...askDemoAssistant(
      question,
      {
        ...context,
        pipe,
        pipeId: pipe?.id ?? context.pipeId,
        scenarioId: scenario?.id ?? context.scenarioId,
        assessment,
        impact,
        isolationPlanId: isolation?.id,
        timeline, timelineGate, timelineDataVersion: dataset.timeline.version, timelineAsOf: dataset.timeline.asOf,
      },
      DEMO_PLANS,
    ), engine: "template" };
  }
}
