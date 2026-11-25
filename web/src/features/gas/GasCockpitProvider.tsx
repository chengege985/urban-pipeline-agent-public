import { createContext, useCallback, useContext, useEffect, useMemo, useReducer, useRef, useState, type ReactNode } from "react";
import { GAS_ASSET_PAGE_SIZE, gasEngineeringPath, PROJECT_GAS_MAP, type GasEngineeringRoute, type GasLayout, type GasMode } from "../../shared/routing/gasRoutes";
import { BrowserGasTransport, GasEngineeringApi } from "./engineeringApi";
import { GasPilotApi } from "./gasPilotApi";
import { indexGasRecords, type GasRecordIndex } from "./assetRecords";
import type { GasAsset, GasCollection, GasDistrict, GasEvent } from "./contracts";
import type { GasCaseResponse } from "./types";
import type {
  GasAgentQueryRequest, GasCapabilityResponse, GasCuvBenchmarkBinding, GasCuvDraftRequest, GasCuvQueryBinding, GasCuvReplayResponse,
  GasMonitoringObservation, GasMonitoringReport, GasObservationRef,
} from "../../shared/contracts/platform";
import { PRODUCT_EDITION } from "../../shared/config/productEdition";
import { useGasRead, type GasReadState } from "./useGasRead";
import { GAS_WINDOWS, gasTimelineReducer, INITIAL_GAS_TIMELINE, mapGasAssets, projectGasCapabilities, type GasCapabilitiesView, type GasWindowId, type TopologyMapping } from "./cockpitModel";
import { findChongqingDistrict } from "./chongqingDistricts";
import { useGasAgent, conversationContext, opensPipeDetails } from "./useGasAgent";
import { toolAssetIds } from "./gasAgentApi";
import { cuvAgentFollowUp, cuvAgentRequest } from "./cuvAgentContext";
import { benchmarkQuestion } from "./cuvBenchmarkContract";
import { relatedAssetIds } from "./presentation";
import { sameMonitoringReference } from "./monitoringContract";

type Resource<T> = GasReadState<T> & { retry(): void };
export type GasLayer = "edges" | "nodes" | "impact" | "labels";
export type GasGisLayer = "adminRegions" | "authorizedAssets" | "authorizedEvents" | "m0Notice";
export interface GasFrame { dx: number; dy: number; }
function initialFrames() { return Object.fromEntries(Object.keys(GAS_WINDOWS).map((id) => [id, { dx: 0, dy: 0 }])) as Record<GasWindowId, GasFrame>; }
export const INITIAL_COCKPIT_ROUTE: GasEngineeringRoute = { assetPage: 1, gasSection: "events", gasMode: "overview", gasLayout: "command", gasMap: "topology" };

const defaultGasMap = PRODUCT_EDITION === "project" ? PROJECT_GAS_MAP : "topology";
const ENGINEERING_QUESTION = /工程(?:校核|模型|计算)|输入就绪|缺少.{0,5}(?:输入|参数)|校核证据/;
type CuvQuestionOptions = { draft?: GasCuvDraftRequest; comparison?: GasCuvQueryBinding; engineering?: boolean };

interface CockpitContext {
  route: GasEngineeringRoute; mode: GasMode; layout: GasLayout;
  caseView: Resource<GasCaseResponse>; summary: Resource<GasCollection<GasAsset>>; events: Resource<GasCollection<GasEvent>>;
  districts: Resource<GasCollection<GasDistrict>>; capabilityResponse: Resource<GasCapabilityResponse>;
  monitoring: Resource<GasMonitoringReport>; cuvReplay: Resource<GasCuvReplayResponse>; pilotApi: GasPilotApi;
  observation: Resource<GasMonitoringObservation>; selectedObservation: GasMonitoringObservation | null;
  openObservation(ref: GasObservationRef): void; openObservationAsset(): void;
  askObservationAgent(): Promise<void>;
  assets: Resource<GasCollection<GasAsset>>; event: Resource<GasEvent>; asset: Resource<GasAsset>;
  index: GasRecordIndex | null; caseIdentity: string; related: boolean;
  caseBinding: { network_content_sha256: string; case_content_sha256: string } | undefined;
  mapping: TopologyMapping; eventMapping: TopologyMapping; focusRevision: number;
  timeline: { index: number; playing: boolean }; timelineEvents: GasCaseResponse["timeline_events"];
  reducedMotion: boolean; play(): void; pause(): void; resetTimeline(): void; selectTimeline(index: number): void;
  layers: Record<GasLayer, boolean>; toggleLayer(layer: GasLayer): void;
  gisLayers: Record<GasGisLayer, boolean>; toggleGisLayer(layer: GasGisLayer): void;
  selectedDistrictId: string | null; selectDistrict(districtId: string | null, ensureGis?: boolean): void;
  gisViewportRevision: number; resetGisViewport(): void;
  lastLocateNotice: string | null;
  locateAssetsOnMap(target: { view?: "topology" | "gis"; eventId?: string; assetId?: string; districtId?: string }): void;
  hidden: ReadonlySet<GasWindowId>; activeWindow: GasWindowId | null; frames: Record<GasWindowId, GasFrame>; windowRevision: number;
  hideWindow(id: GasWindowId): void; openWindow(id: GasWindowId): void; restoreWindows(): void; closeExtra(): void;
  moveWindow(id: GasWindowId, frame: GasFrame): void;
  directoryOpen: boolean; openDirectory(): void; closeDirectory(): void;
  assistantDialog: "current" | "tools" | null;
  openAssistantDialog(): void; openAssistantTools(): void; closeAssistantDialog(): void;
  assistantInput: string; setAssistantInput(value: string): void;
  agent: ReturnType<typeof useGasAgent>; submitAgent(queryOverride?: string): Promise<void>;
  askCuvAgent(report: GasCuvReplayResponse, options?: CuvQuestionOptions): Promise<void>;
  askCuvBenchmark(binding: GasCuvBenchmarkBinding): Promise<void>;
  cuvApi: Pick<GasEngineeringApi, "cuvCollaboration" | "confirmCuvCollaboration" | "cuvEngineering" | "cuvBenchmark">;
  dataM2Api: Pick<GasEngineeringApi, "m2Catalog" | "m2Details" | "m2Records" | "m2Query" | "dataTopic" | "qualityWorkbench" | "topicQuery">;
  scenarioApi: Pick<GasEngineeringApi, "scenario" | "createScenario" | "advanceScenario" | "explainScenario" | "scenarioReport"
    | "drillWorkspace" | "compareScenarios" | "queryDrillTool" | "sourceStress" | "explainSourceStress">;
  go(patch: Partial<GasEngineeringRoute>): void; selectEvent(id: string): void; selectAsset(id: string, keepEvent?: boolean, catalogue?: boolean): void;
  selectTopologyAsset(id: string): void; focusTopology(): void; closeAsset(): void; setMode(mode: GasMode): void; setLayout(layout: GasLayout): void;
  refresh(): void; capabilities: GasCapabilitiesView;
  eventKind: string; eventState: string; setEventKind(value: string): void; setEventState(value: string): void;
}
function observationConversation(
  question: string, selected: GasMonitoringObservation, recentCuv: boolean,
): Partial<GasAgentQueryRequest> | undefined {
  const event = selected.event;
  const ids = question.match(/(?<![A-Za-z0-9_-])(?:syn-gas-(?:pipe|valve|node|event)-[A-Za-z0-9_-]+|K-\d+|N\d+)(?![A-Za-z0-9_-])/gi);
  const scope = [selected.asset_id, selected.asset_id.replace(/^syn-gas-pipe-/, ""), event?.event_id];
  if (ids?.some((id) => !scope.some((allowed) => id.toLowerCase() === allowed?.toLowerCase()))) return undefined;
  const comparison = recentCuv || /推演|两案|(?:比较|对比).{0,12}方案/.test(question);
  if ((!comparison && !/异常|观测|监测|读数|越限|阈值|关联管段|受影响|(?:当前|这个|该|选中).{0,6}事件/.test(question))
    || (comparison && !event)) return undefined;
  return {
    monitoring_ref: selected.reference, monitoring_policy_id: selected.policy_id,
    asset_id: selected.asset_id, event_id: event?.event_id,
    ...(comparison ? { tool_hint: "cuv.query", cuv_at: event?.replay_at } : {}),
  };
}

const Context = createContext<CockpitContext | null>(null);
export function useGasCockpit(): CockpitContext {
  const value = useContext(Context);
  if (!value) throw new Error("Gas cockpit must use its single Provider");
  return value;
}
export function GasCockpitProvider({ route = INITIAL_COCKPIT_ROUTE, baseUrl = "", onNavigate, children }: {
  route?: GasEngineeringRoute; baseUrl?: string; onNavigate(path: string, replace?: boolean): void; children: ReactNode;
}) {
  const api = useMemo(() => new GasEngineeringApi(new BrowserGasTransport(baseUrl)), [baseUrl]);
  const pilotApi = useMemo(() => new GasPilotApi(new BrowserGasTransport(baseUrl)), [baseUrl]);
  const [activeWindow, setActiveWindow] = useState<GasWindowId | null>(null);
  const readMonitoring = useCallback((signal: AbortSignal) => api.monitoring(signal), [api]);
  const monitoring = useGasRead(PRODUCT_EDITION !== "competition" && activeWindow === "data" ? "gas-monitoring" : null, readMonitoring);
  const readCase = useCallback((signal: AbortSignal) => api.catalogue(signal), [api]);
  const readSummary = useCallback((signal: AbortSignal) => api.assets(signal, { limit: 1, offset: 0 }), [api]);
  const readEvents = useCallback((signal: AbortSignal) => api.events(signal), [api]);
  const readDistricts = useCallback((signal: AbortSignal) => api.districts(signal), [api]);
  const readCapabilities = useCallback((signal: AbortSignal) => api.capabilities(signal), [api]);
  const readPage = useCallback((signal: AbortSignal) => api.assets(signal, { limit: GAS_ASSET_PAGE_SIZE, offset: (route.assetPage - 1) * GAS_ASSET_PAGE_SIZE, assetType: route.assetType }), [api, route.assetPage, route.assetType]);
  const readEvent = useCallback((signal: AbortSignal) => api.event(route.gasEventId!, signal), [api, route.gasEventId]);
  const readAsset = useCallback((signal: AbortSignal) => api.asset(route.gasAssetId!, signal), [api, route.gasAssetId]);
  // The case is loaded in exactly one place. Every window and detail maps against this object.
  const caseView = useGasRead("gas-case", readCase);
  const summary = useGasRead("gas-asset-total", readSummary);
  const events = useGasRead("gas-events", readEvents);
  const districts = useGasRead("gas-districts", readDistricts);
  const capabilityResponse = useGasRead("gas-capabilities", readCapabilities);
  const caseIdentity = caseView.data ? [caseView.data.case_id, caseView.data.network_id, caseView.data.algorithm_version,
    caseView.data.evidence.network_content_sha256, caseView.data.evidence.case_content_sha256].join("|") : "case-not-ready";
  const caseEvidence = caseView.data?.evidence;
  const caseBinding = caseEvidence?.network_content_sha256 && caseEvidence.case_content_sha256
    ? { network_content_sha256: caseEvidence.network_content_sha256, case_content_sha256: caseEvidence.case_content_sha256 }
    : undefined;
  const ref = PRODUCT_EDITION === "competition" || route.gasRouteError ? undefined : route.gasObservation;
  const readObservation = useCallback((signal: AbortSignal) => api.monitoringObservation(ref!, signal),
    [api, ref?.observation_id, ref?.manifest_sha256]);
  const observation = useGasRead(ref && caseView.data ? caseIdentity + ":" + ref.manifest_sha256 + ":" + ref.observation_id : null, readObservation);
  const selectedObservation = ref && observation.data && sameMonitoringReference(ref, observation.data.reference)
    && (!route.gasEventId || route.gasEventId === observation.data.event?.event_id)
    && (!route.gasAssetId || route.gasAssetId === observation.data.asset_id)
    ? observation.data : null;
  const readCuvReplay = useCallback((signal: AbortSignal) => api.cuvReplay(signal, caseView.data), [api, caseView.data]);
  const cuvReplay = useGasRead(
    PRODUCT_EDITION !== "competition" && activeWindow === "impact" ? "gas-cuv-replay:" + caseIdentity : null,
    readCuvReplay,
  );
  const event = useGasRead(route.gasEventId && !route.gasRouteError ? `${caseIdentity}:event:${route.gasEventId}` : null, readEvent);
  const eventAssetIds = useMemo(() => event.data ? relatedAssetIds(event.data) : [], [event.data]);
  const related = !route.gasEventId || Boolean(route.gasAssetId && eventAssetIds.includes(route.gasAssetId));
  const asset = useGasRead(route.gasAssetId && related && !route.gasRouteError ? `${caseIdentity}:asset:${route.gasEventId ?? "independent"}:${route.gasAssetId}` : null, readAsset);
  const directoryOpen = route.gasSection === "assets" && (!route.gasAssetId || route.gasCatalogueOpen === true);
  const assets = useGasRead(directoryOpen && !route.gasRouteError ? `${caseIdentity}:page:${route.assetType ?? "all"}:${route.assetPage}` : null, readPage);
  const index = useMemo(() => caseView.data ? indexGasRecords(caseView.data) : null, [caseView.data]);
  const eventMapping = useMemo(() => mapGasAssets(caseView.data, eventAssetIds), [caseView.data, eventAssetIds]);
  const mapping = useMemo(
    () => asset.data ? mapGasAssets(caseView.data, [asset.data.asset_id]) : eventMapping,
    [caseView.data, asset.data, eventMapping],
  );
  const [focusRevision, setFocusRevision] = useState(0);
  const [gisViewportRevision, setGisViewportRevision] = useState(0);
  const [locateNotice, setLocateNotice] = useState<{ path: string; text: string } | null>(null);
  const lastLocateNotice = locateNotice?.path === gasEngineeringPath(route, defaultGasMap) ? locateNotice.text : null;
  const eventKind = route.gasEventKind ?? "all", eventState = route.gasEventState ?? "all";
  const [layers, setLayers] = useState<Record<GasLayer, boolean>>({ edges: true, nodes: true, impact: true, labels: true });
  const [gisLayers, setGisLayers] = useState<Record<GasGisLayer, boolean>>({ adminRegions: true, authorizedAssets: true, authorizedEvents: true, m0Notice: true });
  const [hidden, setHidden] = useState<Set<GasWindowId>>(new Set());
  const [frames, setFrames] = useState(initialFrames);
  const [windowRevision, setWindowRevision] = useState(0);
  const [reducedMotion, setReducedMotion] = useState(() => typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  const [timeline, dispatchTimeline] = useReducer(gasTimelineReducer, INITIAL_GAS_TIMELINE);
  const timelineEvents = useMemo(() => caseView.data ? [...caseView.data.timeline_events].sort((a, b) => a.time_hours - b.time_hours) : [], [caseView.data]);
  const [assistantDialog, setAssistantDialog] = useState<CockpitContext["assistantDialog"]>(null);
  const [assistantInput, setAssistantInput] = useState("");
  const agentScope = ref ? ref.manifest_sha256 + ":" + ref.observation_id : route.gasEventId ?? "";
  const agent = useGasAgent(baseUrl, caseIdentity, agentScope);
  const activeRoute = gasEngineeringPath(route, defaultGasMap);
  const latestRoute = useRef(activeRoute);
  useEffect(() => { latestRoute.current = activeRoute; }, [activeRoute]);
  const selectedDistrictId = route.gasDistrictId ?? null;

  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update(); query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => { dispatchTimeline({ type: "configure", count: timelineEvents.length }); }, [caseIdentity, timelineEvents.length]);
  useEffect(() => { if (reducedMotion) dispatchTimeline({ type: "pause" }); }, [reducedMotion]);
  useEffect(() => {
    if (!timeline.playing || reducedMotion || timelineEvents.length < 2) return;
    const timer = window.setInterval(() => dispatchTimeline({ type: "tick", count: timelineEvents.length }), 1200);
    return () => window.clearInterval(timer);
  }, [timeline.playing, reducedMotion, timelineEvents.length]);
  useEffect(() => {
    const position = timelineEvents.findIndex((item) => item.event_id === route.gasEventId);
    if (position >= 0) dispatchTimeline({ type: "select", index: position, count: timelineEvents.length });
    setFocusRevision((current) => current + 1);
  }, [route.gasEventId, route.gasAssetId, caseIdentity]);

  useEffect(() => {
    if (!selectedObservation || route.gasEventId || route.gasAssetId || route.gasCatalogueOpen) return;
    const eventId = selectedObservation.event?.event_id;
    onNavigate(gasEngineeringPath({
      ...route, gasEventId: eventId, gasAssetId: selectedObservation.asset_id,
      gasSection: eventId ? "events" : "assets", gasMap: "topology",
    }), true);
    setFocusRevision((current) => current + 1);
  }, [selectedObservation, route, onNavigate]);

  const go = (patch: Partial<GasEngineeringRoute>, notice: string | null = null) => {
    const next = { ...route, ...patch, gasRouteError: undefined };
    if (!Object.hasOwn(patch, "gasObservation") && route.gasObservation &&
      (next.gasEventId !== route.gasEventId || next.gasAssetId !== route.gasAssetId || next.gasCatalogueOpen)) {
      if (!selectedObservation || (next.gasEventId && next.gasEventId !== selectedObservation.event?.event_id)
        || (next.gasAssetId && next.gasAssetId !== selectedObservation.asset_id)
        || (!next.gasEventId && !next.gasAssetId) || next.gasCatalogueOpen) next.gasObservation = undefined;
    }
    if (next.gasMap !== route.gasMap || next.gasDistrictId !== route.gasDistrictId || next.gasSubnetId !== route.gasSubnetId
      || (next.gasEventId !== route.gasEventId && next.gasEventId !== selectedObservation?.event?.event_id)) {
      agent.clearHistory();
    }
    const path = gasEngineeringPath(next, defaultGasMap);
    setLocateNotice(notice ? { path, text: notice } : null);
    onNavigate(path);
  };
  const restorePrimary = () => setHidden((current) => { const next = new Set(current); next.delete("topology"); next.delete("selected"); return next; });
  const openObservation = (reference: GasObservationRef) => {
    restorePrimary();
    setActiveWindow(null);
    setAssistantDialog(null);
    go({ gasObservation: reference, gasEventId: undefined, gasAssetId: undefined,
      gasSection: "events", gasMap: "topology", gasCatalogueOpen: false });
  };
  const openObservationAsset = () => {
    if (!selectedObservation) return;
    restorePrimary();
    setFocusRevision((current) => current + 1);
    const eventId = selectedObservation.event?.event_id;
    go({ gasEventId: eventId, gasAssetId: selectedObservation.asset_id, gasSection: eventId ? "events" : "assets",
      gasMap: "topology", gasCatalogueOpen: false });
  };
  const selectEvent = (id: string) => {
    restorePrimary(); setActiveWindow(null); setFocusRevision((current) => current + 1);
    go({ gasEventId: id, gasAssetId: undefined, gasSection: "events", gasCatalogueOpen: false, gasMap: route.gasMap === "gis" ? "gis" : "topology" });
  };
  const selectAsset = (id: string, keepEvent = true, catalogue = false) => {
    restorePrimary(); setFocusRevision((current) => current + 1);
    const eventId = keepEvent ? route.gasEventId : undefined;
    go({
      gasEventId: eventId,
      gasAssetId: id,
      gasSection: eventId ? "events" : "assets",
      gasCatalogueOpen: catalogue,
      gasMap: route.gasMap,
    });
  };
  const selectDistrict = (districtId: string | null, ensureGis = true) => {
    restorePrimary();
    const ref = findChongqingDistrict(districtId);
    setGisViewportRevision((current) => current + 1);
    go(
      { gasDistrictId: districtId ?? undefined, gasMap: ensureGis ? "gis" : route.gasMap,
        ...(ensureGis && PRODUCT_EDITION === "project" ? {
          gasSubnetId: undefined, gasAssetId: undefined, gasEventId: undefined, gasObservation: undefined, gasSection: "events" as const,
        } : {}) },
      ref ? `已定位重庆行政参考区县：${ref.name}（${ref.district_id}）。设施位置以授权坐标为准。` : null,
    );
  };
  const locateAssetsOnMap = (target: { view?: "topology" | "gis"; eventId?: string; assetId?: string; districtId?: string }) => {
    restorePrimary();
    setAssistantDialog(null);
    setFocusRevision((current) => current + 1);
    const gasMap = target.view ?? (target.districtId ? "gis" : "topology");
    const ref = gasMap === "gis" ? findChongqingDistrict(target.districtId) : null;
    if (gasMap === "gis") setGisViewportRevision((current) => current + 1);
    const notice = ref
      ? `已打开 ${ref.name} 行政参考视图。设施位置以授权坐标为准。`
      : null;
    const hasObjectTarget = target.eventId !== undefined || target.assetId !== undefined;
    const gasEventId = hasObjectTarget ? target.eventId : route.gasEventId;
    const gasAssetId = hasObjectTarget ? target.assetId : route.gasAssetId;
    go({
      gasMap,
      gasDistrictId: target.districtId ?? route.gasDistrictId,
      gasEventId,
      gasAssetId,
      gasSection: gasEventId ? "events" : "assets",
    }, notice);
  };
  const focusTopology = () => { restorePrimary(); setFocusRevision((current) => current + 1); if (route.gasMap === "gis") go({ gasMap: "topology" }); };
  const hideWindow = (id: GasWindowId) => { setHidden((current) => new Set(current).add(id)); if (activeWindow === id) setActiveWindow(null); if (id === "timeline") dispatchTimeline({ type: "pause" }); };
  const openWindow = (id: GasWindowId) => { setHidden((current) => { const next = new Set(current); next.delete(id); return next; });
    if (!["topology", "scenario", "selected", "timeline", "tasks"].includes(id)) setActiveWindow(id); };
  const restoreWindows = () => { setHidden(new Set()); setFrames(initialFrames()); setActiveWindow(null); setWindowRevision((current) => current + 1); };
  const selectTimeline = (position: number) => { dispatchTimeline({ type: "select", index: position, count: timelineEvents.length });
    const id = timelineEvents[position]?.event_id;
    if (id && events.data?.items.some((item) => item.event_id === id)) selectEvent(id); };

  const submitAgent = async (queryOverride?: string) => {
    const question = (queryOverride ?? assistantInput).trim();
    if (!question) return;
    if (queryOverride !== undefined) setAssistantInput(queryOverride);
    const lastTool = agent.history.at(-1)?.result?.tools[0];
    const priorBenchmark = lastTool?.benchmark_report?.binding;
    if (PRODUCT_EDITION !== "competition" && (benchmarkQuestion.test(question)
      || (priorBenchmark && /刚才|上一轮|两案|取径|参考误差/.test(question)))) {
      await agent.submit({ question, tool_hint: "cuv.benchmark.query", benchmark_binding: priorBenchmark });
      return;
    }
    const followUp = cuvAgentFollowUp(question, lastTool);
    const engineering = ENGINEERING_QUESTION.test(question) || followUp?.tool_hint === "cuv.engineering.query";
    const scope = selectedObservation && observationConversation(question, selectedObservation, Boolean(followUp) || engineering);
    const result = await agent.submit({
      question,
      case_id: caseView.data?.case_id, case_binding: caseBinding,
      ...conversationContext(question, agent.history, {
        asset_id: route.gasAssetId, event_id: route.gasEventId, district_id: route.gasDistrictId,
      }),
      ...followUp,
      ...(scope || {}),
      ...(engineering && { tool_hint: "cuv.engineering.query" }),
    });
    if (latestRoute.current !== activeRoute || !opensPipeDetails(question) || result?.status !== "succeeded") return;
    const assetTool = result.tools.find((tool) => tool.tool_name === "asset.query");
    const id = assetTool && toolAssetIds(assetTool)[0];
    if (id) selectAsset(id, event.data?.asset_ids?.includes(id) === true);
  };

  const askObservationAgent = async () => {
    if (!selectedObservation) return;
    const question = "查询当前选中异常并解释观测证据。";
    const scope = observationConversation(question, selectedObservation, false);
    setAssistantDialog("tools");
    setAssistantInput(question);
    await agent.submit({ question, case_id: caseView.data?.case_id, case_binding: caseBinding,
      ...scope, tool_hint: "monitoring.query" });
  };

  const askCuvAgent = async (report: GasCuvReplayResponse, { draft, comparison, engineering }: CuvQuestionOptions = {}) => {
    const request = cuvAgentRequest(report, { event_id: route.gasEventId, asset_id: route.gasAssetId });
    request.case_binding = caseBinding;
    request.cuv_replay_sha256 = report.topology_evidence.replay_content_sha256;
    request.cuv_comparison_binding = comparison;
    if (selectedObservation?.event) {
      request.event_id = selectedObservation.event.event_id;
      request.asset_id = selectedObservation.asset_id;
      request.cuv_at = selectedObservation.event.replay_at;
      request.monitoring_ref = selectedObservation.reference;
      request.monitoring_policy_id = selectedObservation.policy_id;
    }
    if (engineering) {
      request.tool_hint = "cuv.engineering.query";
      request.question = "说明当前 CUV 工程模型的输入就绪度、缺失参数和校核证据。";
    }
    if (draft) {
      request.cuv_draft = draft;
      request.question = "比较当前 CUV 方案和待确认条件下的方案差异，并引用证据。";
    }
    setAssistantDialog("tools");
    setAssistantInput(request.question);
    await agent.submit(request);
  };

  const askCuvBenchmark = async (binding: GasCuvBenchmarkBinding) => {
    const question = "复算公开 Weymouth 基准，引用最小内径反求的文献误差与代回流量，并说明取径两案的可比条件。";
    setAssistantDialog("tools");
    setAssistantInput(question);
    await agent.submit({ question, tool_hint: "cuv.benchmark.query", benchmark_binding: binding });
  };

  const value: CockpitContext = {
    route, mode: route.gasMode ?? "overview", layout: route.gasLayout ?? "command", caseView, summary, events, districts, capabilityResponse, monitoring, cuvReplay, pilotApi, assets, event, asset,
    observation, selectedObservation, openObservation, openObservationAsset, askObservationAgent,
    index, caseIdentity, caseBinding, related, mapping, eventMapping, focusRevision, timeline, timelineEvents, reducedMotion,
    play: () => dispatchTimeline({ type: "play", count: timelineEvents.length, reduced: reducedMotion }),
    pause: () => dispatchTimeline({ type: "pause" }), resetTimeline: () => dispatchTimeline({ type: "reset" }), selectTimeline,
    layers, toggleLayer: (layer) => setLayers((current) => ({ ...current, [layer]: !current[layer] })),
    gisLayers, toggleGisLayer: (layer) => setGisLayers((current) => ({ ...current, [layer]: !current[layer] })),
    selectedDistrictId, selectDistrict, gisViewportRevision, resetGisViewport: () => setGisViewportRevision((cur) => cur + 1),
    lastLocateNotice, locateAssetsOnMap,
    hidden, activeWindow, frames, windowRevision, hideWindow, openWindow, restoreWindows, closeExtra: () => setActiveWindow(null),
    moveWindow: (id, frame) => setFrames((current) => ({ ...current, [id]: frame })),
    directoryOpen, openDirectory: () => go({ gasSection: "assets", gasEventId: undefined, gasAssetId: undefined, gasCatalogueOpen: true }),
    closeDirectory: () => go({ gasSection: route.gasAssetId ? "assets" : "events", gasCatalogueOpen: false }),
    assistantDialog,
    openAssistantDialog: () => setAssistantDialog("current"),
    openAssistantTools: () => setAssistantDialog("tools"),
    closeAssistantDialog: () => setAssistantDialog(null),
    assistantInput, setAssistantInput, agent, submitAgent, askCuvAgent, askCuvBenchmark,
    cuvApi: api,
    dataM2Api: api,
    scenarioApi: api,
    go, selectEvent, selectAsset,
    selectTopologyAsset: (id) => selectAsset(id, eventAssetIds.includes(id)),
    focusTopology,
    closeAsset: () => go({ gasAssetId: undefined, gasSection: route.gasEventId ? "events" : route.gasCatalogueOpen ? "assets" : "events" }),
    setMode: (mode) => {
      dispatchTimeline({ type: "pause" });
      go({ gasMode: mode, ...(mode !== "risk" && route.gasSection === "analysis" ? { gasSection: "events" } : {}) });
    },
    setLayout: (layout) => { setFrames(initialFrames()); go({ gasLayout: layout }); },
    refresh: () => { caseView.retry(); events.retry(); summary.retry(); districts.retry(); capabilityResponse.retry(); monitoring.retry(); observation.retry(); cuvReplay.retry(); event.retry(); asset.retry(); assets.retry(); },
    capabilities: projectGasCapabilities(capabilityResponse.data, { status: capabilityResponse.status, error: capabilityResponse.error }), eventKind, eventState,
    setEventKind: (kind) => go({ gasEventKind: kind === "all" ? undefined : kind as GasEvent["event_kind"] }),
    setEventState: (state) => go({ gasEventState: state === "all" ? undefined : state as NonNullable<GasEvent["state"]> }),
  };
  return <Context.Provider value={value}>{children}</Context.Provider>;
}
