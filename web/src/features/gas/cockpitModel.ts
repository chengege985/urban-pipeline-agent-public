import type { GasCaseResponse } from "./types";

export const GAS_WINDOWS = {
  topology: "中央管网拓扑", scenario: "事件与情景", timeline: "处置时间线", impact: "故障与隔离影响",
  selected: "事件与资产详情", services: "服务对象清单", layers: "图层管理", data: "数据接入状态",
  evidence: "证据与版本", boundary: "评价边界", tasks: "任务与智能体入口",
} as const;
export type GasWindowId = keyof typeof GAS_WINDOWS;
export const PRIMARY_GAS_WINDOWS: GasWindowId[] = ["scenario", "topology", "selected", "timeline", "tasks"];
export interface TopologyMapping { edgeIds: string[]; nodeIds: string[]; unmappedIds: string[]; }
export function mapGasAssets(value: GasCaseResponse | null, assetIds: readonly string[]): TopologyMapping {
  const edges = new Set<string>(), nodes = new Set<string>(), missing = new Set<string>();
  for (const id of assetIds) {
    if (!value) { missing.add(id); continue; }
    const edge = value.edges.find((item) => item.pipe_id === id);
    const valve = value.valves.find((item) => item.valve_id === id);
    const node = value.nodes.find((item) => item.node_id === id);
    const consumer = value.consumers.find((item) => item.consumer_id === id);
    if (edge) edges.add(edge.edge_id);
    else if (valve && value.nodes.some((item) => item.node_id === valve.node_id)) nodes.add(valve.node_id);
    else if (node) nodes.add(node.node_id);
    else if (consumer && value.nodes.some((item) => item.node_id === consumer.node_id)) nodes.add(consumer.node_id);
    else missing.add(id);
  }
  return { edgeIds: [...edges], nodeIds: [...nodes], unmappedIds: [...missing] };
}
export interface Viewport { zoom: number; x: number; y: number; }
export const DEFAULT_VIEWPORT: Viewport = { zoom: 1, x: 0, y: 0 };
export function zoomGasViewport(current: Viewport, factor: number, anchor = { x: 500, y: 320 }): Viewport {
  const zoom = Math.min(2.4, Math.max(.65, current.zoom * factor));
  const ratio = zoom / current.zoom;
  return { zoom, x: anchor.x - (anchor.x - current.x) * ratio, y: anchor.y - (anchor.y - current.y) * ratio };
}
export function fitGasMapping(value: {
  edges: ReadonlyArray<Pick<GasCaseResponse["edges"][number], "edge_id" | "from_node_id" | "to_node_id">>;
  nodes: ReadonlyArray<{ node_id: string; layout: { x: number; y: number } }>;
}, mapping: TopologyMapping): Viewport {
  const ids = new Set(mapping.nodeIds);
  for (const edge of value.edges) if (mapping.edgeIds.includes(edge.edge_id)) { ids.add(edge.from_node_id); ids.add(edge.to_node_id); }
  const points = value.nodes.filter((node) => ids.has(node.node_id)).map((node) => node.layout).filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (!points.length) return DEFAULT_VIEWPORT;
  const xs = points.map((point) => point.x), ys = points.map((point) => point.y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const zoom = Math.max(.65, Math.min(1.6, 760 / Math.max(180, maxX - minX), 400 / Math.max(180, maxY - minY)));
  return { zoom, x: 500 - (minX + maxX) * .5 * zoom, y: 320 - (minY + maxY) * .5 * zoom };
}
export interface GasTimelineState { index: number; playing: boolean; }
export type GasTimelineAction = { type: "reset" } | { type: "pause" } | { type: "select"; index: number; count: number }
  | { type: "play"; count: number; reduced: boolean } | { type: "tick"; count: number } | { type: "configure"; count: number };
export const INITIAL_GAS_TIMELINE: GasTimelineState = { index: 0, playing: false };
export function gasTimelineReducer(state: GasTimelineState, action: GasTimelineAction): GasTimelineState {
  if (action.type === "reset") return INITIAL_GAS_TIMELINE;
  if (action.type === "pause") return { ...state, playing: false };
  const last = Math.max(0, action.count - 1);
  if (action.type === "configure") return { index: Math.min(state.index, last), playing: false };
  if (action.type === "select") return { index: Math.max(0, Math.min(last, action.index)), playing: false };
  if (action.type === "play") return action.reduced || action.count < 2 ? { ...state, playing: false } : { index: state.index >= last ? 0 : state.index, playing: true };
  if (!state.playing || action.count < 2) return { ...state, playing: false };
  const index = Math.min(last, state.index + 1);
  return { index, playing: index < last };
}
export interface GasCapabilitiesView {
  risk: { status: string; reason: string };
  gis: { status: string; reason: string };
  agent: { model: string; interaction: string; validation: string; reason: string };
}

export const UNAVAILABLE_GAS_CAPABILITIES: GasCapabilitiesView = {
  risk: { status: "unavailable", reason: "尚无经授权、已业务验证的真实燃气风险结果；不生成全重庆风险排行或高危分数。" },
  gis: { status: "unavailable", reason: "高德重庆GIS与授权燃气设施图层尚未接入；只展示GAS-M0合成拓扑，不生成重庆经纬度。" },
  agent: { model: "unavailable", interaction: "context_only", validation: "pending_validation",
    reason: "未接入可验证的燃气模型服务；当前仅展示上下文，未运行AI推理或处置任务。" },
};

export interface GasCapabilityReadState {
  data?: GasCapabilityResponse | null;
  status?: string;
  error?: { message: string; status?: number | null } | null;
}

export function projectGasCapabilities(
  response: GasCapabilityResponse | null,
  state?: { status?: string; error?: { message: string; status?: number | null } | null },
): GasCapabilitiesView {
  if (!response) {
    if (state?.error) {
      return {
        risk: { status: "unavailable", reason: `能力注册接口读取失败（${state.error.message}）；不推断风险模型就绪状态。` },
        gis: { status: "unavailable", reason: `能力注册接口读取失败（${state.error.message}）；保持离线参考视图，不加载未授权坐标。` },
        agent: {
          model: "unavailable",
          interaction: "context_only",
          validation: "pending_validation",
          reason: `能力注册接口读取失败（${state.error.message}）；Agent 保持未接入状态，不执行模型请求。`,
        },
      };
    }
    return UNAVAILABLE_GAS_CAPABILITIES;
  }
  const items = new Map(response.items.map((item) => [item.capability_id, item]));
  const riskItems = [items.get("pir"), items.get("risk_evolution")].filter(Boolean);
  const readyRisk = riskItems.find((item) => item?.status === "ready_synthetic");
  const riskReason = readyRisk
    ? "能力接口声明 " + readyRisk.capability_id + " 为 " + readyRisk.status + "；仍须保持 candidate / not_business_validated 语义。"
    : riskItems.flatMap((item) => item?.limitations ?? []).join("；")
      || "能力接口未声明可用风险模型；不生成正式风险分数。";
  const gisReason = "能力接口数据集 " + response.dataset_id + " 为 " + response.data_mode + "；后端未声明授权 GIS 设施能力，保持离线参考视图。";
  const agentReason = "能力接口数据集 " + response.dataset_id + " 未声明模型推理能力；Agent 仍为未接入状态，不执行模型请求。";
  return {
    risk: { status: readyRisk ? "candidate" : "unavailable", reason: riskReason },
    gis: { status: "unavailable", reason: gisReason },
    agent: { model: "unavailable", interaction: "context_only", validation: "pending_validation", reason: agentReason },
  };
}

import type { GasCollection, GasEvent } from "./contracts";
import type { GasCapabilityResponse } from "../../shared/contracts/platform";

const SEVERITY_RANK: Record<NonNullable<GasEvent["severity"]>, number> = {
  critical: 5,
  high: 4,
  medium: 3,
  low: 2,
  unknown: 1,
};

const KIND_RANK: Record<GasEvent["event_kind"], number> = {
  given_fault: 3,
  given_isolation: 2,
  synthetic_observation: 1,
};

export function eventPriorityScore(item: GasEvent): number {
  const activeBonus = item.state && item.state !== "closed" ? 100 : 0;
  const sev = item.severity ? (SEVERITY_RANK[item.severity] ?? 1) * 10 : 0;
  const kind = KIND_RANK[item.event_kind] ?? 0;
  return activeBonus + sev + kind;
}

export function sortEventsByPriority(events: readonly GasEvent[]): GasEvent[] {
  return [...events].sort((a, b) => {
    const diff = eventPriorityScore(b) - eventPriorityScore(a);
    if (diff !== 0) return diff;
    return (b.asset_ids?.length ?? 0) - (a.asset_ids?.length ?? 0) || a.event_id.localeCompare(b.event_id);
  });
}

export interface RiskReadinessSummary {
  status: "candidate" | "unavailable";
  ruleVersion: string;
  formulaStatus: string;
  hStatus: string;
  requiredCount: number;
  availableCount: number;
  missingIndicators: string[];
  readyRatioLabel: string;
  blockers: string[];
  criticalOrHighEventCount: number;
  nonClosedEventCount: number;
}

export function summarizeRiskReadiness(
  caseView: GasCaseResponse | null,
  events: GasCollection<GasEvent> | null,
): RiskReadinessSummary {
  const scoring = caseView?.scoring;
  const required = scoring?.required_indicators ?? ["G01", "G02", "G04"];
  const available = scoring?.available_indicators ?? [];
  const missing = required.filter((item) => !available.includes(item));
  const items = events?.items ?? [];
  const blockers: string[] = [];
  if (!scoring || scoring.status === "unavailable") blockers.push("无逐管段G01-G15真实监测与评分输入");
  if (scoring?.h_status === "unsupported_pending_business_definition") blockers.push("H健康度定义待业务确认");
  if (caseView?.metrics.failure_probability?.status === "unsupported") blockers.push("失效概率模型未就绪");
  return {
    status: scoring?.status ?? "unavailable",
    ruleVersion: scoring?.rule_version ?? "unknown",
    formulaStatus: scoring?.formula_status ?? "not_available",
    hStatus: scoring?.h_status ?? "unsupported_pending_business_definition",
    requiredCount: required.length,
    availableCount: available.length,
    missingIndicators: missing,
    readyRatioLabel: scoring ? `${available.length} / ${required.length} 核心指标` : "0 / 3（未连接）",
    blockers: blockers.length ? blockers : ["尚未完成业务验证，暂不计算正式高危排行"],
    criticalOrHighEventCount: items.filter((item) => item.severity === "critical" || item.severity === "high").length,
    nonClosedEventCount: items.filter((item) => item.state !== "closed").length,
  };
}

export interface CuvCapabilityMeta {
  state: "loaded" | "error" | "loading" | "idle";
  datasetId: string | null;
  graphConnectivityStatus: string;
  isolationCandidateStatus: string;
  errorMessage: string | null;
}

export interface CuvReadinessSummary {
  phaseLabel: string;
  timeHours: number | null;
  unreachableNodes: number | null;
  newlyUnreachableNodes: number | null;
  reachableNodes: number | null;
  affectedServices: number | null;
  newlyAffectedServices: number | null;
  consumers: { household: number | null; business: number | null; facility: number | null };
  capabilities: CuvCapabilityMeta;
  boundaries: Array<{ key: string; label: string; status: string; reason: string }>;
}

export function summarizeCuvReadiness(
  caseView: GasCaseResponse | null,
  active: GasCaseResponse["timeline_events"][number] | null,
  capState?: GasCapabilityReadState,
): CuvReadinessSummary {
  const services = active?.services ?? [];
  const affectedCount = services.filter((s) => !s.currently_graph_reachable).length;
  const metrics = caseView?.metrics;
  const capItems = new Map((capState?.data?.items ?? []).map((item) => [item.capability_id, item]));
  const capFailed = !capState?.data && Boolean(capState?.error);
  const pirItem = capItems.get("pir");
  const accItem = capItems.get("accumulation");
  const riskEvoItem = capItems.get("risk_evolution");
  const graphCap = capItems.get("graph_connectivity");
  const isoCap = capItems.get("isolation_candidate");

  const capabilities: CuvCapabilityMeta = capState?.data
    ? {
        state: "loaded",
        datasetId: capState.data.dataset_id,
        graphConnectivityStatus: graphCap?.status ?? "unknown",
        isolationCandidateStatus: isoCap?.status ?? "unknown",
        errorMessage: null,
      }
    : capFailed
      ? {
          state: "error",
          datasetId: null,
          graphConnectivityStatus: "unknown",
          isolationCandidateStatus: "unknown",
          errorMessage: capState?.error?.message ?? "能力注册接口读取失败",
        }
      : {
          state: capState?.status === "loading" ? "loading" : "idle",
          datasetId: null,
          graphConnectivityStatus: "unknown",
          isolationCandidateStatus: "unknown",
          errorMessage: null,
        };

  return {
    phaseLabel: active ? `${active.event_id} (${active.kind})` : "未取得阶段状态",
    timeHours: active?.time_hours ?? null,
    unreachableNodes: active ? active.current_unreachable_node_ids.length : null,
    newlyUnreachableNodes: active ? active.newly_unreachable_node_ids.length : null,
    reachableNodes: active ? active.current_reachable_node_ids.length : null,
    affectedServices: active ? affectedCount : null,
    newlyAffectedServices: active ? active.newly_affected_service_ids.length : null,
    consumers: {
      household: active ? active.potentially_interrupted_consumers.household : null,
      business: active ? active.potentially_interrupted_consumers.business : null,
      facility: active ? active.potentially_interrupted_consumers.facility : null,
    },
    capabilities,
    boundaries: [
      {
        key: "failure_probability",
        label: "失效概率",
        status: metrics?.failure_probability?.status ?? "unsupported",
        reason: metrics?.failure_probability?.reason ?? "模型未就绪；P仅作条件风险候选，非失效概率",
      },
      {
        key: "pir",
        label: "水力/泄漏 PIR",
        status: pirItem?.status ?? (capFailed ? "unknown" : "unsupported"),
        reason: pirItem?.limitations?.length
          ? pirItem.limitations.join("；")
          : capFailed
            ? `能力注册接口读取失败（${capState?.error?.message}）；不推断 PIR 模型就绪状态`
            : "模型未就绪；当前仅统计图拓扑受影响用户数，非物理扩散半径/PIR",
      },
      {
        key: "accumulation",
        label: "影响积聚 / 耦合",
        status: accItem?.status ?? (capFailed ? "unknown" : "unsupported"),
        reason: accItem?.limitations?.length
          ? accItem.limitations.join("；")
          : capFailed
            ? `能力注册接口读取失败（${capState?.error?.message}）；不推断影响积聚就绪状态`
            : "模型未就绪；仅三阶段静态图切换，无多事件二次连锁累积预测",
      },
      {
        key: "hotspots",
        label: "预测热点",
        status: riskEvoItem?.status ?? (capFailed ? "unknown" : "unsupported"),
        reason: riskEvoItem?.limitations?.length
          ? riskEvoItem.limitations.join("；")
          : `模型未就绪（${caseView?.scoring.h_status ?? "unsupported_pending_business_definition"}）`,
      },
      {
        key: "restoration",
        label: "物理复气 / 压力流量",
        status: metrics?.actual_gas_restoration_hours?.status ?? "unsupported",
        reason: metrics?.actual_gas_restoration_hours?.reason ?? "无水力/安全放散验证模型",
      },
    ],
  };
}
