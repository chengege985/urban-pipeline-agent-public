import {
  TIMELINE_ALGORITHM_VERSION,
  TIMELINE_MAX_HORIZON_HOURS,
  TIMELINE_SUPPORTED_OUTPUTS,
} from "../domain/constants";
import type {
  IsolationPlan,
  NetworkScenario,
  Pipe,
  PlanTimelineResult,
  ServiceObject,
  TimeMetric,
  TimelineActionSchedule,
  TimelineEvent,
  TimelineGate,
  TimelineRequest,
  TimelineTaskDefinition,
  WaterPulseDataset,
} from "../domain/types";

const LIMITATION_LINES = [
  "按声明的合成动作回放计划图通路；不是水力、水质、真实供水能力或现场执行。",
  "恢复时间以同一 as_of 和观察窗口为基准；null 不能当作 0 或窗口上限。",
  "备用供水行动没有图操作，不计为通路恢复。",
];

interface TimelineInputs {
  scenario?: NetworkScenario;
  isolation?: IsolationPlan;
  targets?: string[];
  tasks?: TimelineTaskDefinition[];
  error?: string;
}

function metric(
  status: TimeMetric["status"],
  valueHours: number | null,
  reason: string,
  horizon: number,
  extra: Partial<TimeMetric> = {},
): TimeMetric {
  return {
    valueHours,
    status,
    reason,
    criterionId: "topological_reachability",
    timeBasis: "planned",
    observationHorizonHours: horizon,
    ...extra,
  };
}

function resolveInputs(dataset: WaterPulseDataset, request: TimelineRequest): TimelineInputs {
  if (!Number.isFinite(request.horizonHours) || request.horizonHours <= 0 || request.horizonHours > TIMELINE_MAX_HORIZON_HOURS) {
    return { error: `观察窗口必须是 0—${TIMELINE_MAX_HORIZON_HOURS} 小时内的有限数。` };
  }
  const scenario = dataset.scenarios.find((item) => item.id === request.scenarioId);
  if (!scenario) return { error: `未知情景：${request.scenarioId}` };
  const isolation = dataset.isolationPlans.find(
    (item) => item.id === request.isolationPlanId && item.scenarioId === scenario.id,
  );
  if (!isolation) return { error: "隔离方案不存在或不属于当前情景。" };
  const tasks = dataset.timeline.tasks.filter(
    (item) => item.scenarioId === scenario.id && item.planId === request.planId,
  );
  if (!dataset.plans.some((plan) => plan.id === request.planId) || tasks.length === 0) {
    return { error: `未知候选方案：${request.planId}` };
  }
  if (!Array.isArray(request.targetServiceIds)) {
    return { error: "必须明确目标服务集合；空集合需显式传入 []。" };
  }
  const known = new Set(dataset.services.map((service) => service.id));
  if (request.targetServiceIds.some((id) => !known.has(id))) {
    return { error: "目标服务集合包含未知ID。" };
  }
  if (new Set(request.targetServiceIds).size !== request.targetServiceIds.length) {
    return { error: "目标服务集合不能重复。" };
  }
  return { scenario, isolation, targets: request.targetServiceIds, tasks };
}

function scheduleViolations(
  tasks: TimelineTaskDefinition[],
  schedule: TimelineActionSchedule[] | undefined,
): { finishes: Map<string, number | null>; violations: string[]; evidence: string[] } {
  const finishes = new Map(tasks.map((task) => [task.actionId, task.finishOffsetHours]));
  const violations: string[] = [];
  const evidence: string[] = [];
  if (schedule === undefined) return { finishes, violations, evidence };
  if (!Array.isArray(schedule)) {
    return { finishes, violations: ["任务时间表必须是数组。"], evidence };
  }
  const byAction = new Map(tasks.map((task) => [task.actionId, task]));
  const seen = new Set<string>();
  for (const entry of schedule) {
    if (!entry || typeof entry !== "object") {
      violations.push("任务时间表的每一项都必须是对象。");
      continue;
    }
    const actionId = entry.actionId;
    if (typeof actionId !== "string" || actionId.length === 0) {
      violations.push("任务时间表中的行动ID必须是非空字符串。");
      continue;
    }
    if (seen.has(actionId)) {
      violations.push(`行动 ${actionId} 的任务结束信息重复。`);
      continue;
    }
    seen.add(actionId);
    if (!byAction.has(actionId)) {
      violations.push(`行动 ${actionId} 不属于当前方案。`);
      continue;
    }
    const finishInput = entry.finishOffsetHours ?? null;
    const startInput = entry.startOffsetHours ?? null;
    const durationInput = entry.durationHours ?? null;
    const values = [finishInput, startInput, durationInput];
    if (values.some((value) => value !== null && (typeof value !== "number" || !Number.isFinite(value) || value < 0))) {
      violations.push(`行动 ${actionId} 的时间必须是非负有限小时数。`);
      continue;
    }
    let finish = finishInput;
    if (startInput !== null && durationInput !== null) {
      const calculated = startInput + durationInput;
      if (!Number.isFinite(calculated)) {
        violations.push(`行动 ${actionId} 的开始+持续必须为有限小时数。`);
        continue;
      }
      if (finish !== null && Math.abs(finish - calculated) > 1e-9) {
        violations.push(`行动 ${actionId} 的结束时间与开始+持续不一致。`);
        continue;
      }
      finish = calculated;
    }
    finishes.set(actionId, finish);
    evidence.push(`synthetic:user-assumption:${actionId}`);
  }
  return { finishes, violations, evidence };
}

function isAvailable(nodeIds: Set<string>, services: ServiceObject[], serviceId: string): boolean {
  const service = services.find((item) => item.id === serviceId);
  return Boolean(service && nodeIds.has(service.nodeId));
}

function reachableNodes(
  pipes: Pipe[],
  nodes: WaterPulseDataset["nodes"],
  scenario: NetworkScenario,
  isolation: IsolationPlan,
  restored: Set<string>,
): Set<string> {
  const unavailable = new Set([...scenario.failedPipeIds, ...isolation.closedPipeIds]);
  const adjacency = new Map<string, string[]>();
  const nodeIds = new Set(nodes.map((node) => node.id));
  for (const pipe of pipes) {
    nodeIds.add(pipe.fromNodeId);
    nodeIds.add(pipe.toNodeId);
  }
  nodeIds.forEach((id) => adjacency.set(id, []));
  for (const pipe of pipes) {
    if (unavailable.has(pipe.id) && !restored.has(pipe.id)) continue;
    adjacency.get(pipe.fromNodeId)?.push(pipe.toNodeId);
    adjacency.get(pipe.toNodeId)?.push(pipe.fromNodeId);
  }
  const sources = scenario.activeSourceIds.filter((id) => adjacency.has(id));
  const reachable = new Set(sources);
  const queue = [...sources];
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const next of adjacency.get(current) ?? []) {
      if (!reachable.has(next)) {
        reachable.add(next);
        queue.push(next);
      }
    }
  }
  return reachable;
}

function serviceAvailability(
  reachable: Set<string>,
  services: ServiceObject[],
  targets: string[],
): Record<string, boolean> {
  return Object.fromEntries(targets.map((id) => [id, isAvailable(reachable, services, id)]));
}

function planCompletionMetric(
  tasks: TimelineTaskDefinition[],
  finishes: Map<string, number | null>,
  horizon: number,
  violations: string[],
): TimeMetric {
  if (violations.length) {
    return metric("insufficient_data", null, violations.join("；"), horizon);
  }
  const missing = tasks.filter((task) => finishes.get(task.actionId) === null || finishes.get(task.actionId) === undefined);
  if (missing.length) {
    return metric(
      "insufficient_data",
      null,
      `缺少任务结束记录：${missing.map((task) => task.label).join("、")}。`,
      horizon,
    );
  }
  const value = Math.max(...tasks.map((task) => finishes.get(task.actionId) as number));
  return metric("succeeded", value, "全部任务结束时间已声明；方案完成时间不等于服务恢复时间。", horizon, {
    beyondHorizon: value > horizon,
  });
}

export function checkTimelineCapability(dataset: WaterPulseDataset, request: TimelineRequest): TimelineGate {
  const resolved = resolveInputs(dataset, request);
  if (resolved.error || !resolved.scenario || !resolved.isolation || !resolved.targets || !resolved.tasks) {
    return {
      status: "invalid_input",
      canCompute: false,
      supportedOutputs: [...TIMELINE_SUPPORTED_OUTPUTS],
      unsupportedOutputs: [],
      availableOutputs: [],
      missingItems: [resolved.error ?? "输入不完整"],
      authorizationChecked: false,
      registryVersion: "1.0.0",
      requestedSemantics: "planned_topological_reachability",
      evidenceIds: [],
    };
  }
  const schedule = scheduleViolations(resolved.tasks, request.actionSchedule);
  if (schedule.violations.length) {
    return {
      status: "invalid_input",
      canCompute: false,
      supportedOutputs: [...TIMELINE_SUPPORTED_OUTPUTS],
      unsupportedOutputs: [],
      availableOutputs: [],
      missingItems: schedule.violations,
      calculationStatus: "invalid_input",
      authorizationChecked: false,
      registryVersion: "1.0.0",
      requestedSemantics: "planned_topological_reachability",
      evidenceIds: [],
    };
  }
  const completion = planCompletionMetric(resolved.tasks, schedule.finishes, request.horizonHours, schedule.violations);
  const missingItems = completion.status === "insufficient_data" ? [completion.reason] : [];
  const availableOutputs = [
    "timeline_request",
    ...(resolved.targets.length ? ["service_first_restored"] : []),
    ...(completion.status === "succeeded" ? ["recovery_times", "plan_completion"] : []),
  ];
  // The page requests timeline_request first, so the graph timeline remains computable
  // while plan completion is still missing. recovery_times is only fully available
  // once every task finish record exists.
  return {
    status: "succeeded",
    canCompute: true,
    supportedOutputs: [...TIMELINE_SUPPORTED_OUTPUTS],
    unsupportedOutputs: [],
    availableOutputs,
    missingItems,
    calculationStatus: missingItems.length ? "partial" : "succeeded",
    authorizationChecked: false,
    registryVersion: "1.0.0",
    requestedSemantics: "planned_topological_reachability",
    evidenceIds: [
      `synthetic:timeline:${dataset.timeline.version}`,
      `scenario:${resolved.scenario.id}`,
      `plan:${request.planId}`,
      ...resolved.isolation.evidenceIds,
    ],
  };
}

function computePlanTimeline(dataset: WaterPulseDataset, request: TimelineRequest): PlanTimelineResult {
  const resolved = resolveInputs(dataset, request);
  if (resolved.error || !resolved.scenario || !resolved.isolation || !resolved.targets || !resolved.tasks) {
    return {
      status: "invalid_input",
      timelineStatus: "insufficient_data",
      scenarioId: request.scenarioId,
      isolationPlanId: request.isolationPlanId,
      planId: request.planId,
      targetServiceIds: request.targetServiceIds ?? [],
      horizonHours: request.horizonHours,
      asOf: dataset.timeline.asOf,
      planCompletion: metric("insufficient_data", null, resolved.error ?? "输入不完整", request.horizonHours),
      firstTargetServiceRestored: metric("not_applicable", null, "输入不完整", request.horizonHours),
      allTargetServicesRestored: metric("not_applicable", null, "输入不完整", request.horizonHours),
      serviceFirstRestored: {},
      events: [],
      evidenceIds: [],
      issues: [resolved.error ?? "输入不完整"],
      limitations: ["没有生成时间线；输入错误不能当作恢复时间为0。"],
      dataMode: "synthetic",
      sourceStatus: "constructed",
      algorithmVersion: TIMELINE_ALGORITHM_VERSION,
      timeBasis: "planned",
      criterionId: "topological_reachability",
    };
  }

  const schedule = scheduleViolations(resolved.tasks, request.actionSchedule);
  if (schedule.violations.length) {
    return {
      status: "invalid_input",
      timelineStatus: "insufficient_data",
      scenarioId: resolved.scenario.id,
      isolationPlanId: resolved.isolation.id,
      planId: request.planId,
      targetServiceIds: [...resolved.targets],
      horizonHours: request.horizonHours,
      asOf: dataset.timeline.asOf,
      planCompletion: metric("insufficient_data", null, schedule.violations.join("；"), request.horizonHours),
      firstTargetServiceRestored: metric("not_applicable", null, "输入错误，未计算服务恢复时间。", request.horizonHours),
      allTargetServicesRestored: metric("not_applicable", null, "输入错误，未计算服务恢复时间。", request.horizonHours),
      serviceFirstRestored: {},
      events: [],
      evidenceIds: [],
      issues: schedule.violations,
      limitations: ["任务时间表或输入不合法，没有生成时间线；错误输入不能当作恢复时间为0。", ...LIMITATION_LINES],
      dataMode: "synthetic",
      sourceStatus: "constructed",
      algorithmVersion: TIMELINE_ALGORITHM_VERSION,
      timeBasis: "planned",
      criterionId: "topological_reachability",
    };
  }

  const eventDefinitions = dataset.timeline.events
    .filter((event) => event.scenarioId === resolved.scenario!.id && event.planId === request.planId)
    .filter((event) => event.effectiveOffsetHours <= request.horizonHours)
    .sort((a, b) => a.effectiveOffsetHours - b.effectiveOffsetHours || a.id.localeCompare(b.id));
  const events: TimelineEvent[] = [];
  const restored = new Set<string>();
  const initialReachable = reachableNodes(dataset.pipes, dataset.nodes, resolved.scenario, resolved.isolation, restored);
  const firstRestored = new Map<string, number>();
  for (const serviceId of resolved.targets) {
    if (isAvailable(initialReachable, dataset.services, serviceId)) firstRestored.set(serviceId, 0);
  }
  const grouped = new Map<number, typeof eventDefinitions>();
  for (const event of eventDefinitions) {
    grouped.set(event.effectiveOffsetHours, [...(grouped.get(event.effectiveOffsetHours) ?? []), event]);
  }
  for (const [atHours, definitions] of [...grouped.entries()].sort(([a], [b]) => a - b)) {
    for (const definition of definitions) {
      for (const edgeId of definition.edgeIds) restored.add(edgeId);
    }
    const reachable = reachableNodes(dataset.pipes, dataset.nodes, resolved.scenario, resolved.isolation, restored);
    for (const serviceId of resolved.targets) {
      if (!firstRestored.has(serviceId) && isAvailable(reachable, dataset.services, serviceId)) {
        firstRestored.set(serviceId, atHours);
      }
    }
    events.push({
      atHours,
      actionIds: [...new Set(definitions.map((definition) => definition.actionId))],
      restoredEdgeIds: [...new Set(definitions.flatMap((definition) => definition.edgeIds))],
      serviceAvailability: serviceAvailability(reachable, dataset.services, resolved.targets),
      evidenceIds: [...new Set(definitions.flatMap((definition) => definition.evidenceIds))],
    });
  }

  const serviceFirstRestored: Record<string, TimeMetric> = {};
  for (const serviceId of resolved.targets) {
    const service = dataset.services.find((item) => item.id === serviceId);
    if (!service) continue;
    const first = firstRestored.get(serviceId);
    if (isAvailable(initialReachable, dataset.services, serviceId)) {
      serviceFirstRestored[serviceId] = metric("already_available", 0, "故障隔离后、计划动作前已可达。", request.horizonHours);
    } else if (first !== undefined) {
      serviceFirstRestored[serviceId] = metric("succeeded", first, "按声明的图操作首次恢复通路。", request.horizonHours, {
        beyondHorizon: first > request.horizonHours,
      });
    } else {
      serviceFirstRestored[serviceId] = metric(
        "not_recovered_within_horizon",
        null,
        "观察窗口内没有声明事件使该服务恢复。",
        request.horizonHours,
        { lowerBoundHours: request.horizonHours, lowerBoundExclusive: true },
      );
    }
  }

  const notInitiallyAvailable = resolved.targets.filter(
    (id) => !isAvailable(initialReachable, dataset.services, id),
  );
  const restoredTargets = notInitiallyAvailable.filter((id) => firstRestored.has(id));
  const firstTarget = resolved.targets.length === 0
    ? metric("not_applicable", null, "用户没有选择目标服务。", request.horizonHours)
    : notInitiallyAvailable.length === 0
      ? metric("already_available", 0, "所有目标在计划动作前已经可达。", request.horizonHours)
      : restoredTargets.length === 0
        ? metric("not_recovered_within_horizon", null, "没有目标在观察窗口内首次恢复。", request.horizonHours, { lowerBoundHours: request.horizonHours, lowerBoundExclusive: true })
        : metric("succeeded", Math.min(...restoredTargets.map((id) => firstRestored.get(id) as number)), "初始不可用目标中最早首次恢复的时刻。", request.horizonHours);

  let allTarget = metric("not_applicable", null, "用户没有选择目标服务。", request.horizonHours);
  if (resolved.targets.length > 0) {
    if (resolved.targets.every((id) => isAvailable(initialReachable, dataset.services, id))) {
      allTarget = metric("already_available", 0, "所有目标在计划动作前已经同时可达。", request.horizonHours);
    } else {
      let allAt: number | null = null;
      const currentRestored = new Set<string>();
      for (const event of events) {
        for (const edgeId of event.restoredEdgeIds) currentRestored.add(edgeId);
        const reachable = reachableNodes(dataset.pipes, dataset.nodes, resolved.scenario, resolved.isolation, currentRestored);
        if (resolved.targets.every((id) => isAvailable(reachable, dataset.services, id))) {
          allAt = event.atHours;
          break;
        }
      }
      allTarget = allAt === null
        ? metric("not_recovered_within_horizon", null, "观察窗口内没有时刻使全部目标同时可达。", request.horizonHours, { lowerBoundHours: request.horizonHours, lowerBoundExclusive: true })
        : metric("succeeded", allAt, "首次全部目标同时可达；不代表之后持续可达。", request.horizonHours, { beyondHorizon: allAt > request.horizonHours });
    }
  }

  const planCompletion = planCompletionMetric(resolved.tasks, schedule.finishes, request.horizonHours, schedule.violations);
  const issues = [
    ...schedule.violations,
    ...(planCompletion.status === "insufficient_data" ? [planCompletion.reason] : []),
  ];
  const evidenceIds = [
    ...new Set([
      `synthetic:timeline:${dataset.timeline.version}`,
      `scenario:${resolved.scenario.id}`,
      `plan:${request.planId}`,
      ...resolved.isolation.evidenceIds,
      ...eventDefinitions.flatMap((definition) => definition.evidenceIds),
      ...schedule.evidence,
    ]),
  ];
  const status = schedule.violations.length
    ? "invalid_input"
    : planCompletion.status === "insufficient_data"
      ? "partial"
      : "succeeded";

  return {
    status,
    timelineStatus: "succeeded",
    scenarioId: resolved.scenario.id,
    isolationPlanId: resolved.isolation.id,
    planId: request.planId,
    targetServiceIds: [...resolved.targets],
    horizonHours: request.horizonHours,
    asOf: dataset.timeline.asOf,
    planCompletion,
    firstTargetServiceRestored: firstTarget,
    allTargetServicesRestored: allTarget,
    serviceFirstRestored,
    events,
    evidenceIds,
    issues,
    limitations: LIMITATION_LINES,
    dataMode: "synthetic",
    sourceStatus: "constructed",
    algorithmVersion: TIMELINE_ALGORITHM_VERSION,
    timeBasis: "planned",
    criterionId: "topological_reachability",
  };
}


export function buildPlanTimeline(dataset: WaterPulseDataset, request: TimelineRequest): PlanTimelineResult {
  return {
    ...computePlanTimeline(dataset, request),
    inputRequest: structuredClone(request),
    timelineDataVersion: dataset.timeline.version,
  };
}
