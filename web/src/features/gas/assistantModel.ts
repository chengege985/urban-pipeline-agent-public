import { GasAgentApi } from "./gasAgentApi";
import { BrowserGasTransport, GasApiError } from "./engineeringApi";
import type { GasAsset, GasCollection, GasDistrict, GasEvent } from "./contracts";
import type { GasCaseResponse } from "./types";
import { findChongqingDistrict, matchChongqingDistrictInText, type ChongqingDistrictRef } from "./chongqingDistricts";
import { hasAuthorizedGasLocation, relatedAssetIds } from "./presentation";

export type GasAgentExecutionStatus = "unavailable" | "timeout" | "unauthenticated" | "forbidden";

export type GasAgentIntentKind =
  | "locate_event_assets"
  | "inspect_asset"
  | "locate_district"
  | "query_cuv_timeline"
  | "query_risk_readiness"
  | "general_gas_query";

export interface GasAgentToolStatus {
  tool: string;
  label: string;
  status: "succeeded" | "unavailable" | "not_configured" | "forbidden" | "timeout";
  detail: string;
}

export interface GasAgentLocateCommand {
  targetView: "topology" | "gis" | "none";
  label: string;
  eventId: string | null;
  assetIds: string[];
  districtId: string | null;
  coordinateAuthorized: boolean;
  note: string;
}

export interface GasAssistantParseResult {
  query: string;
  parserSource: "local_rule_parser_not_llm";
  intent: GasAgentIntentKind;
  intentLabel: string;
  params: {
    event_id: string | null;
    asset_ids: string[];
    district_id: string | null;
    district_name: string | null;
    mode_hint: "overview" | "risk" | "emergency" | null;
    map_hint: "topology" | "gis" | null;
  };
  subject: {
    case_id: string;
    event_id: string | null;
    asset_id: string | null;
    district_id: string | null;
  };
  tools: GasAgentToolStatus[];
  evidenceRefs: string[];
  clickableAssetIds: string[];
  locateCommand: GasAgentLocateCommand;
  summaryNote: string;
}

export interface GasAgentServiceState {
  status: GasAgentExecutionStatus;
  httpStatus?: number;
  detail: string;
  checkedAt?: string;
}

export const INITIAL_GAS_AGENT_SERVICE: GasAgentServiceState = {
  status: "unavailable",
  detail: "未接入可验证的燃气大模型/推理服务；前端禁止直连模型长期密钥，当前仅执行本地规则意图解析与上下文对照。",
};

const INTENT_LABELS: Record<GasAgentIntentKind, string> = {
  locate_event_assets: "事件与关联资产定位",
  inspect_asset: "单资产/管段/阀门检索",
  locate_district: "重庆行政区县GIS定位",
  query_cuv_timeline: "CUV推演边界与时间线查询",
  query_risk_readiness: "风险评价就绪度核查",
  general_gas_query: "通用燃气领域问答（待模型）",
};

function findMatchedIdsInQuery(text: string, caseView: GasCaseResponse | null, events: GasCollection<GasEvent> | null): {
  matchedEventId: string | null;
  matchedAssetIds: string[];
} {
  const matchedEvent = (events?.items ?? []).find((item) => text.includes(item.event_id)) ?? null;
  const knownAssets = new Set<string>();
  if (caseView) {
    caseView.pipes.forEach((p) => knownAssets.add(p.pipe_id));
    caseView.valves.forEach((v) => knownAssets.add(v.valve_id));
    caseView.nodes.forEach((n) => knownAssets.add(n.node_id));
    caseView.consumers.forEach((c) => knownAssets.add(c.consumer_id));
  }
  const matchedAssets = [...knownAssets].filter((id) => text.includes(id));
  return { matchedEventId: matchedEvent?.event_id ?? null, matchedAssetIds: matchedAssets };
}

export function parseGasAssistantQuery(options: {
  query: string;
  caseView: GasCaseResponse | null;
  events: GasCollection<GasEvent> | null;
  districts: GasCollection<GasDistrict> | null;
  selectedEvent: GasEvent | null;
  selectedAsset: GasAsset | null;
  selectedEventId?: string;
  selectedAssetId?: string;
  selectedDistrictId?: string | null;
}): GasAssistantParseResult {
  const query = options.query.trim();
  const { caseView, events, districts, selectedEvent, selectedAsset, selectedEventId, selectedAssetId, selectedDistrictId } = options;
  const { matchedEventId, matchedAssetIds } = findMatchedIdsInQuery(query, caseView, events);
  const matchedDistrict: ChongqingDistrictRef | null = matchChongqingDistrictInText(query) ?? findChongqingDistrict(selectedDistrictId);

  const eventPool = events?.items ?? [];
  const defaultFaultEvent = eventPool.find((item) => item.event_kind === "given_fault") ?? eventPool[0] ?? null;
  const targetEvent = matchedEventId
    ? eventPool.find((item) => item.event_id === matchedEventId) ?? selectedEvent
    : selectedEvent ?? (/故障|隔离|事件/.test(query) ? defaultFaultEvent : null);

  let intent: GasAgentIntentKind = "general_gas_query";
  let modeHint: "overview" | "risk" | "emergency" | null = null;
  let mapHint: "topology" | "gis" | null = null;

  if (matchedAssetIds.length > 0 || (/管段|阀门|节点|资产|详情/.test(query) && !/区|县|GIS|高德/.test(query) && !/风险|排行|分数/.test(query))) {
    intent = "inspect_asset";
    mapHint = "topology";
  } else if (matchChongqingDistrictInText(query) || /区县|GIS|高德|底图|重庆全/.test(query)) {
    intent = "locate_district";
    mapHint = "gis";
  } else if (/时间线|推演|CUV|关阀|停气|影响用户|PIR|热点|积聚|概率/i.test(query)) {
    intent = "query_cuv_timeline";
    modeHint = "emergency";
    mapHint = "topology";
  } else if (/风险|高危|排行|G01|G15|评分|就绪/.test(query)) {
    intent = "query_risk_readiness";
    modeHint = "risk";
  } else if (/定位|故障|事件|关联/.test(query) || matchedEventId) {
    intent = "locate_event_assets";
    mapHint = "topology";
  }

  const eventRelated = targetEvent ? relatedAssetIds(targetEvent) : [];
  const clickableAssetIds = matchedAssetIds.length > 0
    ? matchedAssetIds
    : eventRelated.length > 0
      ? eventRelated
      : selectedAssetId
        ? [selectedAssetId]
        : intent === "inspect_asset" && caseView
          ? [...caseView.valves.slice(0, 2).map((v) => v.valve_id), ...caseView.pipes.slice(0, 3).map((p) => p.pipe_id)]
          : [];

  const targetAssetIds = clickableAssetIds;
  const targetEventId = targetEvent?.event_id ?? selectedEventId ?? null;
  const targetDistrictId = matchedDistrict?.district_id ?? null;

  const coordinateAuthorized = Boolean(
    (selectedAsset && hasAuthorizedGasLocation(selectedAsset))
    || (targetEvent && hasAuthorizedGasLocation(targetEvent)),
  );

  const evidenceSet = new Set<string>();
  targetEvent?.evidence_refs?.forEach((ref) => evidenceSet.add(ref));
  selectedAsset?.evidence_refs?.forEach((ref) => evidenceSet.add(ref));
  if (caseView) {
    Object.entries(caseView.evidence).forEach(([k, v]) => {
      if (v) evidenceSet.add(`${k}:${v.slice(0, 12)}…`);
    });
  }

  const tools: GasAgentToolStatus[] = [
    {
      tool: "gas_case_and_events_read",
      label: "GAS-M0 案例与事件只读查询",
      status: caseView || events ? "succeeded" : "unavailable",
      detail: caseView ? `已加载 ${caseView.case_id}（${caseView.counts.pipes} 管段 / ${caseView.counts.valves} 阀门）` : "案例尚未就绪",
    },
    {
      tool: "gas_district_gis_read",
      label: "重庆区县与设施坐标查询",
      status: districts && districts.items.length > 0 && coordinateAuthorized ? "succeeded" : "unavailable",
      detail: coordinateAuthorized
        ? "存在经授权的设施坐标"
        : "后端 /districts 返回 unavailable，且资产 location_status=not_available；仅可用区县行政参考位置",
    },
    {
      tool: "gas_risk_assessment",
      label: "正式风险评估工具 (get_gas_assessment)",
      status: "unavailable",
      detail: `规则 ${caseView?.scoring.rule_version ?? "gas-g15"} 缺少 ${caseView?.scoring.required_indicators.join("/") ?? "G01/G02/G04"} 输入且未获适配器注册`,
    },
    {
      tool: "llm_gas_agent_runtime",
      label: "后端大模型 Agent 推理执行",
      status: "not_configured",
      detail: "未启用受控大模型推理通道，本栏仅为前端规则解析，未生成 AI 回复。",
    },
  ];

  const locateCommand: GasAgentLocateCommand = intent === "locate_district" && matchedDistrict
    ? {
      targetView: "gis",
      label: `GIS定位至 ${matchedDistrict.name}`,
      eventId: targetEventId,
      assetIds: [],
      districtId: matchedDistrict.district_id,
      coordinateAuthorized: false,
      note: `行政参考中心 ${matchedDistrict.refLon}, ${matchedDistrict.refLat}；无真实燃气管线坐标。`,
    }
    : targetEventId || targetAssetIds.length > 0
      ? {
        targetView: "topology",
        label: targetAssetIds.length ? `合成拓扑定位 (${targetAssetIds.length} 项)` : `聚焦事件 ${targetEventId}`,
        eventId: targetEventId,
        assetIds: targetAssetIds,
        districtId: targetDistrictId,
        coordinateAuthorized,
        note: coordinateAuthorized
          ? "已携带授权坐标，可在 GIS 定位。"
          : "当前资产/事件无真实经纬度，定位指令作用于 GAS-M0 合成拓扑高亮。",
      }
      : {
        targetView: "none",
        label: "暂无可执行定位对象",
        eventId: null,
        assetIds: [],
        districtId: null,
        coordinateAuthorized: false,
        note: "可输入区县名称（如「渝中区」）或事件/资产编号以解析定位指令。",
      };

  const summaryNote = query
    ? `已通过前端确定性规则将输入归类为「${INTENT_LABELS[intent]}」（非大模型推理结果）。${locateCommand.note}`
    : "输入自然语言检索事件、管段/阀门或重庆区县（本地规则解析，不会向第三方泄露私钥或伪造模型回复）。";

  return {
    query,
    parserSource: "local_rule_parser_not_llm",
    intent,
    intentLabel: INTENT_LABELS[intent],
    params: {
      event_id: targetEventId,
      asset_ids: targetAssetIds,
      district_id: matchedDistrict?.district_id ?? null,
      district_name: matchedDistrict?.name ?? null,
      mode_hint: modeHint,
      map_hint: mapHint,
    },
    subject: {
      case_id: caseView?.case_id ?? "unknown",
      event_id: selectedEventId ?? targetEventId,
      asset_id: selectedAssetId ?? (targetAssetIds[0] ?? null),
      district_id: matchedDistrict?.district_id ?? null,
    },
    tools,
    evidenceRefs: [...evidenceSet],
    clickableAssetIds,
    locateCommand,
    summaryNote,
  };
}

export async function probeGasAgentService(baseUrl: string, signal?: AbortSignal): Promise<GasAgentServiceState> {
  const checkedAt = new Date().toLocaleTimeString("zh-CN", { hour12: false });
  try {
    const health = await new GasAgentApi(new BrowserGasTransport(baseUrl)).health(signal);
    return { status: "unavailable", httpStatus: 200, detail: health.reason, checkedAt };
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    if (error instanceof DOMException && error.name === "TimeoutError")
      return { status: "timeout", detail: "燃气服务探测超时。", checkedAt };
    if (error instanceof GasApiError)
      return { status: error.status === 401 ? "unauthenticated" : error.status === 403 ? "forbidden" : "unavailable",
        httpStatus: error.status, detail: error.message, checkedAt };
    return { status: "unavailable", detail: "无法连接燃气服务。", checkedAt };
  }
}
