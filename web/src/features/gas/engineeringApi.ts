import type { GasAsset, GasCollection, GasDistrict, GasEvent } from "./contracts";
import type {
  GasCapabilityResponse, GasCuvBenchmarkReport, GasCuvCollaborationRequest, GasCuvCollaborationSnapshot,
  GasCuvEngineeringAssessment, GasCuvReplayResponse,
  GasMonitoringObservation, GasObservationRef,
  GasDrillCompareRequest, GasDrillComparison, GasDrillToolRequest, GasDrillToolResult, GasDrillWorkspace,
  GasSyntheticScenario, GasScenarioExplanation, GasScenarioReport,
  GasDataTopic, GasM2TopicAnswer, GasM2TopicQuestion, GasQualityWorkbench, GasSourceStressAnswer, GasSourceStressQuestion,
  GasSourceStressRequest, GasSourceStressResult, GasTopicSelection,
  GasSimulationCatalogResponse, GasSimulationRequest, GasSimulationResult,
} from "../../shared/contracts/platform";
import { fetchGasCase } from "./api";
import { gasMonitoringObservation, gasMonitoringResponse } from "./monitoringContract";
import { gasCuvReplayResponse, matchesCuvCase, type CuvCaseIdentity } from "./cuvReplayContract";
import { gasCuvCollaborationSnapshot, type CuvCollaborationIdentity } from "./cuvCollaborationContract";
import { gasCuvEngineeringAssessment } from "./cuvEngineeringContract";
import { gasCuvBenchmarkReport } from "./cuvBenchmarkContract";
import { isGasApiError } from "./errors";
import type { GasCaseResponse } from "./types";
import { GAS_ASSET_TYPES } from "../../shared/routing/gasRoutes";
import { FetchTransport, safeDetail } from "../../shared/api/platformTransport";
import { gasM2Answer, gasM2Catalog, gasM2Details, gasM2Records,
  type GasM2AgentRequest, type GasM2Binding, type GasM2Kind,
} from "./dataM2Contract";
import { gasDrillComparison, gasDrillToolResult, gasDrillWorkspace, gasScenario, gasScenarioExplanation, gasScenarioReport } from "./scenarioContract";
import { gasDataTopic, gasQualityWorkbench, gasSourceStress, gasStressAnswer, gasTopicAnswer } from "./topicContract";
import { gasSimulationCatalog, gasSimulationResult } from "./simulationContract";

export interface GasTransport {
  send(request: { method: "GET" | "POST"; path: string; signal?: AbortSignal; body?: unknown }): Promise<{ status: number; body: unknown }>;
}
export interface GasAssetPageQuery { limit?: number; offset?: number; assetType?: GasAsset["asset_type"]; }
export interface GasEventQuery { districtId?: string; status?: string; limit?: number; }
export function gasEventQueryPath(query: GasEventQuery = {}): string {
  const params = new URLSearchParams();
  if (query.districtId !== undefined) {
    const trimmed = query.districtId.trim();
    if (!trimmed) throw new RangeError("Invalid districtId filter");
    params.set("district_id", trimmed);
  }
  if (query.status !== undefined) {
    const trimmed = query.status.trim();
    if (!trimmed) throw new RangeError("Invalid status filter");
    params.set("status", trimmed);
  }
  if (query.limit !== undefined) {
    if (!Number.isInteger(query.limit) || query.limit < 1 || query.limit > 200) throw new RangeError("Invalid bounded gas event limit");
    params.set("limit", String(query.limit));
  }
  const qs = params.toString();
  return qs ? `/api/v1/gas/engineering/events?${qs}` : "/api/v1/gas/engineering/events";
}
export function gasAssetPagePath(query: GasAssetPageQuery = {}): string {
  const limit = query.limit ?? 200;
  const offset = query.offset ?? 0;
  if (!Number.isInteger(limit) || limit < 1 || limit > 200 || !Number.isSafeInteger(offset) || offset < 0
    || (query.assetType !== undefined && !GAS_ASSET_TYPES.includes(query.assetType))) throw new RangeError("Invalid bounded gas asset page query");
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (query.assetType) params.set("asset_type", query.assetType);
  return `/api/v1/gas/engineering/assets?${params}`;
}

export type GasErrorCode = "unauthenticated" | "forbidden" | "not_found" | "unavailable" | "network" | "invalid_response" | "http";
export class GasApiError extends Error {
  constructor(public readonly code: GasErrorCode, message: string, public readonly status?: number) {
    super(message);
    this.name = "GasApiError";
  }
}

export class BrowserGasTransport implements GasTransport {
  private readonly baseUrl: string;

  constructor(baseUrl = "") {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async send(request: { method: "GET" | "POST"; path: string; signal?: AbortSignal; body?: unknown }) {
    const response = await fetch(`${this.baseUrl}${request.path}`, {
      method: request.method, signal: request.signal, credentials: "same-origin",
      headers: { Accept: "application/json", "X-Water-Client": "unified-react",
        ...(request.body === undefined ? {} : { "Content-Type": "application/json" }) },
      body: request.body === undefined ? undefined : JSON.stringify(request.body),
    });
    return { status: response.status, body: await response.json().catch(() => null) };
  }
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((item) => typeof item === "string");
const optionalStrings = (value: unknown) => value === undefined || strings(value);
const optionalEnum = (value: unknown, allowed: readonly string[]) => value === undefined || (typeof value === "string" && allowed.includes(value));
const modes = ["real_authorized", "synthetic", "unknown", "unavailable"];
const sources = ["authorized", "synthetic_fixture", "unknown", "unavailable"];
function provenance(value: Record<string, unknown>) {
  return typeof value.data_mode === "string" && modes.includes(value.data_mode)
    && typeof value.source_status === "string" && sources.includes(value.source_status)
    && typeof value.validation_status === "string"
    && ["business_validated", "not_business_validated", "unknown"].includes(value.validation_status);
}
function location(value: Record<string, unknown>) {
  const point = value.location;
  return (point === undefined || point === null || (record(point)
    && typeof point.coordinate_system === "string" && ["wgs84", "gcj02"].includes(point.coordinate_system)
    && typeof point.longitude === "number" && Number.isFinite(point.longitude)
    && typeof point.latitude === "number" && Number.isFinite(point.latitude)))
    && optionalEnum(value.location_status, ["authorized", "not_available", "unknown"]);
}
export function asset(value: unknown): value is GasAsset {
  return record(value) && typeof value.asset_id === "string" && !!value.asset_id.trim()
    && typeof value.name === "string" && typeof value.asset_type === "string" && ["pipe", "valve", "node", "source", "consumer", "regulator", "junction"].includes(value.asset_type)
    && provenance(value) && location(value) && optionalStrings(value.evidence_refs)
    && (value.material == null || typeof value.material === "string")
    && (value.diameter_mm == null || (typeof value.diameter_mm === "number" && Number.isFinite(value.diameter_mm) && value.diameter_mm >= 0))
    && (value.pressure_class == null || typeof value.pressure_class === "string")
    && (value.district_id == null || typeof value.district_id === "string")
    && optionalEnum(value.status, ["unknown", "synthetic_reference"]);
}
export function event(value: unknown): value is GasEvent {
  return record(value) && typeof value.event_id === "string" && !!value.event_id.trim()
    && typeof value.title === "string" && typeof value.limitation === "string"
    && typeof value.event_kind === "string" && ["given_fault", "given_isolation", "synthetic_observation"].includes(value.event_kind)
    && provenance(value) && location(value) && optionalStrings(value.asset_ids) && optionalStrings(value.closed_valve_ids) && optionalStrings(value.evidence_refs)
    && (value.district_id == null || typeof value.district_id === "string")
    && optionalEnum(value.state, ["synthetic_given", "open", "acknowledged", "closed"])
    && optionalEnum(value.severity, ["critical", "high", "medium", "low", "unknown"])
    && optionalEnum(value.risk_status, ["unavailable", "not_computed", "confirmed"])
    && optionalEnum(value.physical_supply_status, ["unknown", "verified"]);
}
function district(value: unknown): value is GasDistrict {
  return record(value) && typeof value.district_id === "string" && typeof value.name === "string"
    && provenance(value) && typeof value.geometry_available === "boolean" && typeof value.limitation === "string";
}
export function capabilityResponse(value: unknown): value is GasCapabilityResponse {
  return record(value)
    && value.data_mode === "synthetic"
    && typeof value.dataset_id === "string"
    && value.validation_status === "not_business_validated"
    && Array.isArray(value.items)
    && value.items.every((item) => record(item)
      && typeof item.capability_id === "string"
      && typeof item.version === "string"
      && typeof item.status === "string"
      && ["ready_synthetic", "unavailable", "unsupported"].includes(item.status)
      && typeof item.scope === "string"
      && typeof item.validation_status === "string"
      && ["synthetic_validated", "not_business_validated", "unsupported"].includes(item.validation_status)
      && optionalStrings(item.input_requirements)
      && optionalStrings(item.evidence_refs)
      && optionalStrings(item.limitations));
}
function collection<T>(item: (value: unknown) => value is T) {
  return (value: unknown): value is GasCollection<T> => record(value) && provenance(value)
    && typeof value.validation_status === "string"
    && typeof value.status === "string" && ["succeeded", "partial", "unknown", "unavailable"].includes(value.status)
    && Array.isArray(value.items) && value.items.every(item) && strings(value.limitations)
    && typeof value.total === "number" && Number.isInteger(value.total) && value.total >= value.items.length;
}

export function toGasError(caught: unknown): GasApiError {
  return caught instanceof GasApiError ? caught : new GasApiError("network", "无法连接燃气服务。请检查网络或服务状态后重试；未切换为模拟结果。");
}

function extractGasErrorDetail(body: unknown): string {
  if (!record(body)) return "";
  return safeDetail(body.detail);
}

export function gasHttpError(status: number, body?: unknown): GasApiError {
  if (status === 401) return new GasApiError("unauthenticated", "未认证或会话已失效（401）。请通过受控入口重新认证，不会自动降级数据权限。", 401);
  if (status === 403) return new GasApiError("forbidden", "无权访问当前燃气数据（403）。请联系管理员核实授权，不会切换到其他数据源。", 403);
  if (status === 404) {
    const detail = extractGasErrorDetail(body);
    if (detail === "gas_disposition_disabled")
      return new GasApiError("unavailable", "人工处置演示当前未启用，当前仅提供只读状态和证据。", 404);
    return new GasApiError("not_found", "请求的燃气对象不存在或已移除（404）。请返回列表重新选择。", 404);
  }
  if (status === 409) {
    const detail = extractGasErrorDetail(body);
    if (detail?.includes("gas_monitoring_"))
      return new GasApiError("http", "当前监测输入或关联已变更。请从回放重新选择观测。", 409);
    if (detail?.includes("gas_cuv_collaboration_") || detail === "gas_cuv_evidence_version_conflict")
      return new GasApiError("http", "推演输入或比较版本已变更。请读取最新条件再试。", 409);
    if (detail === "gas_cuv_benchmark_version_conflict")
      return new GasApiError("http", "公开基准版本已变更。请重新打开 CUV 基准证据。", 409);
    if (detail === "gas_m2_version_conflict")
      return new GasApiError("http", "当前数据登记的版本有更新，请刷新后重新选择。", 409);
    if (detail === "gas_m2_projection_only")
      return new GasApiError("http", "当前数据集只提供研究或统计摘要。", 409);
    if (detail === "gas_scenario_pipe_already_excluded")
      return new GasApiError("http", "所选管段已被合成基线排除，请选择基线中的可用管段。", 409);
    if (detail.startsWith("gas_scenario_"))
      return new GasApiError("http", "演练条件或版本已变化。请重新读取演练后再试。", 409);
    if (detail === "gas_agent_case_version_conflict")
      return new GasApiError("http", "合成案例证据已变更。请刷新大屏并重新选择对象。", 409);
  }
  if (status === 422) {
    const detail = extractGasErrorDetail(body);
    return new GasApiError("http", detail ? `请求参数未通过校验（422）：${detail}` : "请求参数未通过校验（422），请检查筛选条件或编号。", 422);
  }
  if (status === 503) return new GasApiError("unavailable", "燃气服务或合成燃气 Fixture 当前不可用（503）。请稍后重试。", 503);
  const detail = extractGasErrorDetail(body);
  return new GasApiError("http", detail ? `燃气接口返回 HTTP ${status}：${detail}` : `燃气接口返回 HTTP ${status}，未取得可用结果。`, status);
}

const cuvCollaborationPath = "/api/v1/gas/engineering/cuv-replay/collaboration";
const m2Path = "/api/v1/gas/engineering/data-m2";
const scenarioPath = "/api/v1/gas/engineering/synthetic-scenarios";
const simulationPath = "/api/v1/gas/engineering/simulation";

export class GasEngineeringApi {
  constructor(private readonly transport: GasTransport = new FetchTransport("")) {}
  private async request<T>(
    method: "GET" | "POST", path: string, validate: (value: unknown) => value is T, signal?: AbortSignal, body?: unknown,
  ): Promise<T> {
    let response: { status: number; body: unknown };
    try { response = await this.transport.send({ method, path, signal, ...(body === undefined ? {} : { body }) }); }
    catch (caught) {
      if (signal?.aborted || (caught instanceof Error && caught.name === "AbortError")) throw caught;
      throw toGasError(caught);
    }
    if (response.status >= 200 && response.status < 300) {
      if (validate(response.body)) return response.body;
      throw new GasApiError("invalid_response", "燃气接口响应与已确认合同不一致，未展示不完整或错配的详情。");
    }
    throw gasHttpError(response.status, response.body);
  }
  private get<T>(path: string, validate: (value: unknown) => value is T, signal?: AbortSignal): Promise<T> {
    return this.request("GET", path, validate, signal);
  }
  m2Catalog(signal?: AbortSignal) { return this.get(m2Path + "/datasets", gasM2Catalog, signal); }
  m2Details(binding: GasM2Binding, signal?: AbortSignal) {
    return this.get(m2Path + "/datasets/" + encodeURIComponent(binding.dataset_id),
      (value): value is import("./dataM2Contract").GasM2Details => gasM2Details(value, binding), signal);
  }
  m2Records(binding: GasM2Binding, kind: GasM2Kind, assetType?: "pipe" | "node" | "valve", signal?: AbortSignal, recordId?: string) {
    const params = new URLSearchParams({ registry_sha256: binding.registry_sha256, version: binding.version ?? "", limit: "250" });
    if (assetType) params.set("asset_type", assetType);
    if (recordId) params.set("record_id", recordId);
    return this.get(m2Path + "/datasets/" + encodeURIComponent(binding.dataset_id) + "/records/" + kind + "?" + params,
      (value): value is import("./dataM2Contract").GasM2RecordPage => gasM2Records(value, binding, kind)
        && (!recordId || (value.total === 1 && value.items.length === 1 && value.items[0].record_id === recordId)), signal);
  }
  m2Query(payload: GasM2AgentRequest, signal?: AbortSignal) {
    return this.request("POST", m2Path + "/agent/query",
      (value): value is import("./dataM2Contract").GasM2AgentResult => gasM2Answer(value, payload), signal, payload);
  }
  dataTopic(binding: GasM2Binding, selection: GasTopicSelection, signal?: AbortSignal) {
    const params = new URLSearchParams({ version: binding.version ?? "", registry_sha256: binding.registry_sha256, scope: selection.scope });
    if (selection.district_id) params.set("district_id", selection.district_id);
    if (selection.subnet_id) params.set("subnet_id", selection.subnet_id);
    if (selection.asset_id) params.set("asset_id", selection.asset_id);
    return this.get(m2Path + "/navigation?" + params,
      (value): value is GasDataTopic => gasDataTopic(value, binding, selection), signal);
  }
  qualityWorkbench(binding: GasM2Binding, signal?: AbortSignal) {
    const params = new URLSearchParams({ version: binding.version ?? "", registry_sha256: binding.registry_sha256 });
    return this.get(m2Path + "/quality-workbench?" + params,
      (value): value is GasQualityWorkbench => gasQualityWorkbench(value, binding), signal);
  }
  topicQuery(payload: GasM2TopicQuestion, signal?: AbortSignal) {
    return this.request("POST", m2Path + "/topic-query",
      (value): value is GasM2TopicAnswer => gasTopicAnswer(value, payload), signal, payload);
  }
  sourceStress(payload: GasSourceStressRequest, signal?: AbortSignal) {
    return this.request("POST", scenarioPath + "/source-stress",
      (value): value is GasSourceStressResult => gasSourceStress(value, payload), signal, payload);
  }
  explainSourceStress(payload: GasSourceStressQuestion, signal?: AbortSignal) {
    return this.request("POST", scenarioPath + "/source-stress/explain",
      (value): value is GasSourceStressAnswer => gasStressAnswer(value, payload), signal, payload);
  }
  scenario(id: string, binding: GasM2Binding, signal?: AbortSignal) {
    return this.get(scenarioPath + "/" + encodeURIComponent(id),
      (value): value is GasSyntheticScenario => gasScenario(value, binding, { id }), signal);
  }
  createScenario(binding: GasM2Binding, pipeId: string, faultKind: GasSyntheticScenario["input"]["fault_kind"], signal?: AbortSignal) {
    return this.request("POST", scenarioPath,
      (value): value is GasSyntheticScenario => gasScenario(value, binding, { pipe: pipeId, faultKind, revision: 1 }), signal,
      { binding, pipe_id: pipeId, fault_kind: faultKind });
  }
  drillWorkspace(binding: GasM2Binding, signal?: AbortSignal) {
    const params = new URLSearchParams({ registry_sha256: binding.registry_sha256, version: binding.version ?? "" });
    return this.get(scenarioPath + "/workspace?" + params,
      (value): value is GasDrillWorkspace => gasDrillWorkspace(value, binding), signal);
  }
  compareScenarios(payload: GasDrillCompareRequest, signal?: AbortSignal) {
    return this.request("POST", scenarioPath + "/comparison",
      (value): value is GasDrillComparison => gasDrillComparison(value, payload), signal, payload);
  }
  queryDrillTool(payload: GasDrillToolRequest, signal?: AbortSignal) {
    return this.request("POST", scenarioPath + "/tool-query",
      (value): value is GasDrillToolResult => gasDrillToolResult(value, payload), signal, payload);
  }
  advanceScenario(scenario: GasSyntheticScenario, action: "analyze" | "rollback", signal?: AbortSignal) {
    const revision = scenario.transition.revision;
    return this.request("POST", scenarioPath + "/" + encodeURIComponent(scenario.scenario_id) + "/" + action,
      (value): value is GasSyntheticScenario => gasScenario(value, scenario.input.binding,
        { id: scenario.scenario_id, pipe: scenario.input.pipe_id, faultKind: scenario.input.fault_kind, revision: revision + 1 })
        && value.transition.action === (action === "analyze" ? "analyzed" : "rolled_back"),
      signal, { expected_revision: revision });
  }
  explainScenario(scenario: GasSyntheticScenario, question: string, signal?: AbortSignal) {
    return this.request("POST", scenarioPath + "/" + encodeURIComponent(scenario.scenario_id) + "/explain",
      (value): value is GasScenarioExplanation => gasScenarioExplanation(value, scenario), signal,
      { expected_revision: scenario.transition.revision, analysis_sha256: scenario.analysis?.analysis_sha256, question });
  }
  scenarioReport(scenario: GasSyntheticScenario, signal?: AbortSignal) {
    return this.get(scenarioPath + "/" + encodeURIComponent(scenario.scenario_id) + "/report?revision=" + scenario.transition.revision,
      (value): value is GasScenarioReport => gasScenarioReport(value, scenario), signal);
  }
  cuvBenchmark(signal?: AbortSignal) {
    return this.get<GasCuvBenchmarkReport>("/api/v1/gas/engineering/cuv-replay/benchmark", gasCuvBenchmarkReport, signal);
  }
  cuvEngineering(report: GasCuvReplayResponse, signal?: AbortSignal) {
    const source = report.topology_evidence;
    const params = new URLSearchParams({
      network_content_sha256: source.network_content_sha256,
      case_content_sha256: source.case_content_sha256,
      replay_content_sha256: source.replay_content_sha256,
    });
    return this.get("/api/v1/gas/engineering/cuv-replay/engineering?" + params,
      (value): value is GasCuvEngineeringAssessment => gasCuvEngineeringAssessment(value, report), signal);
  }
  cuvCollaboration(report: CuvCollaborationIdentity, signal?: AbortSignal) {
    return this.get(cuvCollaborationPath,
      (value): value is GasCuvCollaborationSnapshot => gasCuvCollaborationSnapshot(value, report), signal);
  }
  confirmCuvCollaboration(report: CuvCollaborationIdentity, body: GasCuvCollaborationRequest, signal?: AbortSignal) {
    if (body.confirmed !== true) throw new GasApiError("http", "请先明确确认目标与约束。");
    return this.request("POST", cuvCollaborationPath,
      (value): value is GasCuvCollaborationSnapshot => gasCuvCollaborationSnapshot(value, report, body), signal, body);
  }
  monitoring(signal?: AbortSignal) { return this.get("/api/v1/gas/engineering/monitoring", gasMonitoringResponse, signal); }
  monitoringObservation(ref: GasObservationRef, signal?: AbortSignal) {
    const params = new URLSearchParams({ observation_id: ref.observation_id, manifest_sha256: ref.manifest_sha256 });
    return this.get("/api/v1/gas/engineering/monitoring/observation?" + params,
      (value): value is GasMonitoringObservation => gasMonitoringObservation(value, ref), signal);
  }
  cuvReplay(signal?: AbortSignal, currentCase: CuvCaseIdentity | null = null) {
    return this.get("/api/v1/gas/engineering/cuv-replay",
      (value): value is GasCuvReplayResponse => gasCuvReplayResponse(value) && matchesCuvCase(value, currentCase), signal);
  }
  districts(signal?: AbortSignal) { return this.get("/api/v1/gas/engineering/districts", collection(district), signal); }
  capabilities(signal?: AbortSignal) { return this.get("/api/v1/gas/engineering/capabilities", capabilityResponse, signal); }
  simulationCatalog(signal?: AbortSignal) {
    return this.get<GasSimulationCatalogResponse>(simulationPath + "/catalog", gasSimulationCatalog, signal);
  }
  runSimulation(payload: GasSimulationRequest, signal?: AbortSignal) {
    return this.request<GasSimulationResult>("POST", simulationPath + "/run", gasSimulationResult, signal, payload);
  }
  assets(signal?: AbortSignal, query: GasAssetPageQuery = {}) { return this.get(gasAssetPagePath(query), collection(asset), signal); }
  async catalogue(signal?: AbortSignal): Promise<GasCaseResponse> {
    try { return (await fetchGasCase("", undefined, { transport: this.transport, signal })).data; }
    catch (caught) {
      if (signal?.aborted) throw caught;
      if (isGasApiError(caught) && caught.status !== null && caught.status >= 400) throw gasHttpError(caught.status);
      if (isGasApiError(caught) && caught.kind === "contract_error") throw new GasApiError("invalid_response", "燃气案例集合映射与已确认合同不一致，未推断统计或记录类别。");
      throw toGasError(caught);
    }
  }
  events(signal?: AbortSignal, query?: GasEventQuery) { return this.get(gasEventQueryPath(query), collection(event), signal); }
  asset(assetId: string, signal?: AbortSignal) {
    return this.get(`/api/v1/gas/engineering/assets/${encodeURIComponent(assetId)}`,
      (value): value is GasAsset => asset(value) && value.asset_id === assetId, signal);
  }
  event(eventId: string, signal?: AbortSignal) {
    return this.get(`/api/v1/gas/engineering/events/${encodeURIComponent(eventId)}`,
      (value): value is GasEvent => event(value) && value.event_id === eventId, signal);
  }
}
