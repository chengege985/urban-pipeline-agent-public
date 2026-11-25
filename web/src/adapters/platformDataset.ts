import type {
  Assessment,
  DatasetAction,
  DatasetEdge,
  DatasetIsolationPlan,
  DatasetNode,
  DatasetPipe,
  DatasetPlan,
  DatasetService,
  DatasetView,
  LegacyScore,
} from "../generated/platformOpenapi";
import type {
  HealthAssessment,
  HealthGrade,
  IsolationPlan,
  NetworkNode,
  NetworkScenario,
  Pipe,
  PipeMaterial,
  ServiceObject,
  ServiceType,
  Valve,
  WaterPulseDataset,
} from "../domain/types";

/** 平台视图与 UI 领域模型不兼容时显式失败，不做强制转换。 */
export class PlatformContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "PlatformContractError";
  }
}

const DISCLAIMERS = [
  "合成数据合成演示：不控制真实设施。",
  "legacy_demo_0.1.1 的 Hs/Rs 是简化演示评分，不是正式 P/H、失效概率或 RUL。",
  "示意坐标不是 GIS 经纬度。",
];

const MATERIALS: readonly PipeMaterial[] = ["PE", "球墨铸铁", "铸铁", "钢", "PVC"];

function material(value: string | null | undefined): PipeMaterial {
  return value && (MATERIALS as readonly string[]).includes(value) ? (value as PipeMaterial) : "未知";
}

/**
 * 服务类型只用于图标与筛选，不参与任何计算。
 * 平台快照没有类型字段，因此按上游 label 的显式关键词映射；无法识别即“未知”。
 */
function serviceType(label: string): ServiceType {
  if (label.includes("医院")) return "医院";
  if (label.includes("学校")) return "学校";
  if (label.includes("商业")) return "商业";
  if (label.includes("居民")) return "居民区";
  return "未知";
}

/** 平台只有单一计数口径；人口口径无法换算为户数，故显式拒绝。 */
function counts(service: DatasetService): { households: number; facilities: number } {
  if (service.count_unit === "person") {
    throw new PlatformContractError(
      `服务 ${service.service_id} 的计数口径为 person，当前 UI 模型只有户/处，未做单位换算。`,
    );
  }
  if (service.count_value === null || service.count_value === undefined) {
    throw new PlatformContractError(`服务 ${service.service_id} 缺少计数，不能用 0 代替。`);
  }
  return service.count_unit === "household"
    ? { households: service.count_value, facilities: 0 }
    : { households: 0, facilities: service.count_value };
}

function gradeOf(score: LegacyScore | null | undefined): HealthGrade {
  const value = score?.grade;
  if (value === "基本健康" || value === "关注" || value === "重点检查" || value === "数据不足") return value;
  return "数据不足";
}

function assessmentOf(pipe: DatasetPipe): HealthAssessment {
  const score = pipe.score;
  return {
    pipeId: pipe.pipe_id,
    status: score && score.hs !== null ? "succeeded" : "insufficient_data",
    score: score?.hs ?? null,
    grade: gradeOf(score),
    confidence: score?.completeness_percent ?? 0,
    validWeight: (score?.completeness_percent ?? 0) / 100,
    factors: [],
    missing: score ? [] : [pipe.score_issue ?? "该数据集没有评分记录"],
    version: score ? `${score.model_family}/${score.model_version}` : "unavailable",
    summary: score
      ? `平台快照 ${score.semantics} 评分；指标分解未在该视图中返回。`
      : "平台快照没有该管段的合法评分；未现场补算。",
  };
}

function pipeOf(pipe: DatasetPipe): Pipe {
  return {
    id: pipe.pipe_id,
    name: pipe.name ?? pipe.pipe_id,
    fromNodeId: pipe.from_node_id ?? "",
    toNodeId: pipe.to_node_id ?? "",
    material: material(pipe.asset_hints?.material_hint),
    installYear: pipe.asset_hints?.install_year_hint ?? null,
    diameterMm: pipe.asset_hints?.diameter_mm_hint ?? null,
    depthM: null,
    pressure: "未知",
    leakCount: null,
    repairCount: null,
    monitoring: "未知",
    lastInspection: null,
    serviceIds: [],
    lengthM: null,
    note: "字段来自平台数据集视图；未返回的字段保持缺失，不补零。",
  };
}

function nodeKind(node: DatasetNode, serviceNodeIds: Set<string>): NetworkNode["kind"] {
  if (node.source) return "source";
  if (serviceNodeIds.has(node.node_id)) return "service";
  return "junction";
}

function nodesOf(nodes: DatasetNode[], services: DatasetService[]): NetworkNode[] {
  const missing = nodes.filter((node) => node.x === null || node.x === undefined || node.y === null || node.y === undefined);
  if (missing.length > 0) {
    throw new PlatformContractError(
      `节点缺少示意坐标，不能绘制：${missing.map((node) => node.node_id).join("、")}`,
    );
  }
  const serviceNodeIds = new Set(services.flatMap((service) => service.node_ids));
  return nodes.map((node) => ({
    id: node.node_id,
    label: node.label || node.node_id,
    x: node.x as number,
    y: node.y as number,
    kind: nodeKind(node, serviceNodeIds),
  }));
}

function valvesOf(edges: DatasetEdge[]): Valve[] {
  return edges
    .filter((edge) => edge.is_valve)
    .map((edge) => ({
      id: edge.edge_id,
      label: edge.edge_id,
      pipeId: edge.pipe_id,
      nodeId: edge.from_node,
      operable: edge.operable === true,
    }));
}

function faultPipeIds(plan: DatasetIsolationPlan, edges: DatasetEdge[]): string[] {
  const byEdge = new Map(edges.map((edge) => [edge.edge_id, edge.pipe_id]));
  return plan.fault_edge_ids.map((edgeId) => byEdge.get(edgeId) ?? edgeId);
}

/**
 * 平台没有“情景”对象：按给定隔离方案派生 UI 情景。
 * 派生只做结构分组，不含严重度分级，因此 severity 恒为“未知”。
 */
function scenariosOf(
  plans: DatasetIsolationPlan[],
  edges: DatasetEdge[],
  nodes: DatasetNode[],
): NetworkScenario[] {
  const sources = nodes.filter((node) => node.source && node.active === true).map((node) => node.node_id);
  return plans.map((plan) => {
    const pipes = faultPipeIds(plan, edges);
    return {
      id: `scn-${plan.plan_id}`,
      name: `故障 ${pipes.join("、")}`,
      summary: `由平台隔离方案 ${plan.plan_id} 派生的展示情景；来源：${plan.evidence_ref}`,
      failedPipeIds: pipes,
      activeSourceIds: sources,
      defaultIsolationPlanId: plan.plan_id,
      severity: "未知",
      note: "情景为 UI 结构分组，不是平台业务对象；严重度未分级。",
    };
  });
}

function isolationPlansOf(plans: DatasetIsolationPlan[], edges: DatasetEdge[]): IsolationPlan[] {
  return plans.map((plan) => ({
    id: plan.plan_id,
    scenarioId: `scn-${plan.plan_id}`,
    name: plan.plan_id,
    description: `给定隔离方案；来源依据 ${plan.evidence_ref}`,
    valveIds: [...(plan.valve_ids ?? [])],
    closedPipeIds: [],
    evidenceIds: [plan.evidence_ref],
    version: "platform-given",
  }));
}

function servicesOf(services: DatasetService[]): ServiceObject[] {
  return services.map((service) => {
    const count = counts(service);
    const type = serviceType(service.label);
    return {
      id: service.service_id,
      name: service.label,
      type,
      nodeId: service.node_ids[0],
      description:
        `${service.supply_rule === "any" ? "任一节点可达即供水" : "全部节点可达才供水"}` +
        `；节点 ${service.node_ids.join("、")}；类型${type === "未知" ? "未识别" : "按上游名称映射"}`,
      households: count.households,
      facilities: count.facilities,
      critical: service.critical === true,
    };
  });
}

export interface DatasetAdapterResult {
  dataset: WaterPulseDataset;
  coverage: { total: number; scored: number; status: string; missingPipeIds: string[]; incompatiblePipeIds: string[] };
  limitations: string[];
  evidenceIds: string[];
}

/** 只做结构映射：所有业务数值直接来自数据集视图，前端不重算。 */
export function toWaterPulseDataset(view: DatasetView): DatasetAdapterResult {
  const assessments: Record<string, HealthAssessment> = {};
  for (const pipe of view.pipes) assessments[pipe.pipe_id] = assessmentOf(pipe);
  const modelVersions = new Set(
    view.pipes.map((pipe) => pipe.score?.model_version).filter((value): value is string => Boolean(value)),
  );
  const evidenceIds = [...new Set([...view.evidence_ids, ...view.pipes.flatMap((pipe) => pipe.score?.evidence_refs ?? [])])];
  const dataset: WaterPulseDataset = {
    meta: {
      mode: "live",
      providerKind: "platform",
      appVersion: view.meta.platform_version,
      dataVersion: view.meta.sha256,
      modelVersion: modelVersions.size === 1 ? [...modelVersions][0] : "unavailable",
      disclaimer: DISCLAIMERS.join(" "),
    },
    nodes: nodesOf(view.nodes, view.services),
    pipes: view.pipes.map(pipeOf),
    services: servicesOf(view.services),
    scenarios: scenariosOf(view.isolation_plans, view.edges, view.nodes),
    valves: valvesOf(view.edges),
    isolationPlans: isolationPlansOf(view.isolation_plans, view.edges),
    // 平台没有演示维护方案目录；候选方案由 /decision-runs 按给定 ID 计算。
    plans: [],
    planCatalog: view.plans.map((plan) => ({ id: plan.plan_id, name: plan.label })),
    // 平台时间线来自措施排期，不是预置事件表；不补造事件。
    timeline: { version: "platform-action-schedule", asOf: view.meta.as_of, events: [], tasks: [] },
    assessments,
    impacts: {},
  };
  return {
    dataset,
    coverage: {
      total: view.assessment_coverage.total_pipes,
      scored: view.assessment_coverage.scored_pipes,
      status: view.assessment_coverage.status,
      missingPipeIds: [...view.assessment_coverage.missing_pipe_ids],
      incompatiblePipeIds: [...view.assessment_coverage.incompatible_pipe_ids],
    },
    limitations: [...view.limitations, ...view.missing_items],
    evidenceIds,
  };
}

export function datasetViewSummary(view: DatasetView): {
  datasetId: string;
  snapshotId: string;
  dataMode: string;
  asOf: string;
  dataVersion: string;
  status: string;
} {
  return {
    datasetId: view.meta.dataset_id,
    snapshotId: view.meta.snapshot_id,
    dataMode: view.meta.data_mode,
    asOf: view.meta.as_of,
    dataVersion: view.meta.sha256,
    status: view.status,
  };
}

/** 平台 action/plan 的类型仅用于引用，不在前端换算金额。 */
export type PlatformAction = DatasetAction;
export type PlatformPlan = DatasetPlan;
export type PlatformAssessmentRow = Assessment;
