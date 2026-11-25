import type {
  IsolationPlan,
  NetworkNode,
  NetworkScenario,
  Pipe,
  ServiceImpact,
  ServiceObject,
} from "../domain/types";

const LIMITATIONS = [
  "仅表示合成管网中的拓扑通路影响，不代表压力、流量或实际缺供量。",
  "情景为演示假设，不包含失效概率、物理级联或真实阀门操作。",
  "服务对象与管段均为合成数据，不对应任何真实设施。",
];

export function simulateServiceImpact(
  pipes: Pipe[],
  nodes: NetworkNode[],
  services: ServiceObject[],
  scenario: NetworkScenario,
  isolationPlan?: IsolationPlan,
): ServiceImpact {
  const failed = new Set(scenario.failedPipeIds);
  const closed = new Set(isolationPlan?.closedPipeIds ?? []);
  const unavailablePipes = new Set([...failed, ...closed]);
  const adjacency = new Map<string, string[]>();
  nodes.forEach((node) => adjacency.set(node.id, []));

  const unknownFailedPipes = [...unavailablePipes].filter(
    (pipeId) => !pipes.some((pipe) => pipe.id === pipeId),
  );
  const isolationMismatch = Boolean(isolationPlan && isolationPlan.scenarioId !== scenario.id);

  pipes.forEach((pipe) => {
    if (unavailablePipes.has(pipe.id)) return;
    adjacency.get(pipe.fromNodeId)?.push(pipe.toNodeId);
    adjacency.get(pipe.toNodeId)?.push(pipe.fromNodeId);
  });

  const sources = scenario.activeSourceIds.filter((id) => adjacency.has(id));
  const reachable = new Set<string>();
  const queue = [...sources];
  queue.forEach((id) => reachable.add(id));
  while (queue.length > 0) {
    const current = queue.shift() as string;
    for (const next of adjacency.get(current) ?? []) {
      if (!reachable.has(next)) {
        reachable.add(next);
        queue.push(next);
      }
    }
  }

  const affectedServices = services
    .filter((service) => !reachable.has(service.nodeId))
    .map((service) => ({
      serviceId: service.id,
      name: service.name,
      type: service.type,
      critical: service.critical,
      reason: `接入节点 ${service.nodeId} 与当前水源不连通`,
      households: service.households,
      facilities: service.facilities,
    }));

  const unreachableNodeIds = nodes
    .filter((node) => !reachable.has(node.id))
    .map((node) => node.id);

  return {
    scenarioId: scenario.id,
    isolationPlanId: isolationPlan?.id ?? "",
    status:
      sources.length > 0 && unknownFailedPipes.length === 0 && !isolationMismatch
        ? "succeeded"
        : "partial",
    failedPipeIds: [...scenario.failedPipeIds],
    closedPipeIds: [...closed],
    valveIds: [...(isolationPlan?.valveIds ?? [])],
    activeSourceIds: [...sources],
    unreachableNodeIds,
    affectedServices,
    affectedCriticalServiceIds: affectedServices
      .filter((service) => service.critical)
      .map((service) => service.serviceId),
    affectedHouseholds: affectedServices.reduce((sum, service) => sum + service.households, 0),
    affectedFacilities: affectedServices.reduce((sum, service) => sum + service.facilities, 0),
    evidenceIds: [
      `scenario:${scenario.id}`,
      ...scenario.failedPipeIds.map((id) => `pipe:${id}`),
      ...(isolationPlan?.evidenceIds ?? []),
      ...affectedServices.map((service) => `service:${service.serviceId}`),
    ],
    limitations: [
      ...LIMITATIONS,
      ...(isolationPlan
        ? ["给定隔离方案只改变合成图中的管段开闭状态，不表示已实际操作阀门。"]
        : []),
      ...(sources.length === 0 ? ["情景没有可用水源，无法形成正常基准。"] : []),
      ...(isolationMismatch ? ["隔离方案不属于当前情景，结果不可用于业务判断。"] : []),
      ...unknownFailedPipes.map((id) => `未知管段：${id}`),
      ...unreachableNodeIds.slice(0, 4).map((id) => `不可达节点：${id}`),
    ],
  };
}
