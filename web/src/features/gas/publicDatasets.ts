import type { GasTransport } from "./engineeringApi";
import { GasApiError, gasHttpError } from "./engineeringApi";

export const PUBLIC_DATASET_OPTIONS = [
  { id: "charlottesville", label: "Charlottesville", mode: "Official GIS" },
  { id: "schutterwald", label: "Schutterwald", mode: "Research model" },
  { id: "sicily_transmission", label: "西西里 · 高压输气研究样本", mode: "研究基准" },
  { id: "aer_alberta", label: "AER · 阿尔伯塔管线与事件", mode: "官方公开数据" },
  { id: "sgn_eastbourne", label: "SGN · Eastbourne 城市配气", mode: "官方公开数据" },
  { id: "ngn_distribution_example", label: "Pipeline Engineer · NGN 配气样例", mode: "研究基准" },
  { id: "ngn_the_north", label: "NGN · The North 配气主管", mode: "官方公开数据" },
  { id: "gaslib_582", label: "GasLib-582 · 研究基准", mode: "研究基准" },
  { id: "merrimack_2018", label: "Merrimack Valley · 事故复盘", mode: "官方事故资料" },
  { id: "gas_m0_synthetic", label: "GAS-M0 · 合成演示", mode: "合成数据" },
] as const;

export type PublicDatasetId = (typeof PUBLIC_DATASET_OPTIONS)[number]["id"];
export type PublicDatasetFeature = {
  record_id?: string | null;
  pipe_id?: string | null;
  geometry?: { type: "LineString" | "MultiLineString" | "Polygon" | "Point"; coordinates: number[] | number[][] | number[][][] } | null;
  source_properties?: Record<string, unknown>;
  source_ref?: Record<string, unknown>;
  [key: string]: unknown;
};
export type NgnSamplePoint = [number, number];
type ModelPoint = { name: string; geom: NgnSamplePoint[] };
type Pipe = { name: string; geom: NgnSamplePoint[][]; from_junction: string; to_junction: string; diameter_m: number;
  length_km: number; MATERIAL: string; in_service: boolean | number };
export type NgnSampleModel = { pipes: Pipe[]; nodes: ModelPoint[];
  sources: (ModelPoint & { p_bar: number; junction: string })[];
  sinks: (ModelPoint & { mdot_kg_per_s: number; junction: string })[];
};
export type PublicDatasetOverview = {
  dataset_id: PublicDatasetId;
  data_mode: string;
  status: string;
  data_version?: string | null;
  title: string;
  source_crs?: string | null;
  region_bbox?: [number, number, number, number];
  viewport_bbox?: [number, number, number, number] | null;
  overview_grid?: { bbox: [number, number, number, number]; count: number }[];
  facets?: Record<string, { value: string | number | null; count: number }[]>;
  geometry_note?: string | null;
  scope?: string;
  available_scopes?: string[];
  counts?: Record<string, number | null>;
  assets?: PublicDatasetFeature[];
  assets_total_in_scope?: number | null;
  assets_page_offset?: number;
  assets_page_limit?: number;
  assets_page_partial?: boolean;
  nodes?: Array<{ id: string; node_type: string; coordinates?: [number, number] | null; source_properties?: Record<string, string> }>;
  edges?: Array<{ id: string; edge_type: string; from_node_id: string; to_node_id: string; source_properties?: Record<string, string> }>;
  presets?: Array<{ id: string; label: string }>;
  cases?: Array<{ id: string; published: boolean }>;
  default_case?: string;
  model?: NgnSampleModel;
  periods?: string[];
  input_sha256?: Record<string, string>;
  solver_status?: string;
  solver_reason?: string;
  event_date?: string;
  event_time_precision?: string;
  locations?: string[];
  facts?: Array<{ label: string; value: number | string; unit?: string; note?: string }>;
  events?: Array<Record<string, unknown>>;
  events_total?: number;
  events_page_partial?: boolean;
  timeline?: Array<{ time: string; precision: string; title: string; detail: string; source_url?: string; source_label?: string }>;
  evidence?: Array<{ label: string; url: string; kind: string }>;
  source_refs?: string[];
  limitations?: string[];
  event_note?: string;
};
export type NgnFilters = { material: string; pressure: string; diameter_mm: string };
export type NgnHistoryKind = "emergency" | "interruption";
export type NgnHistory = {
  status: "ready" | "unavailable";
  data_version: string;
  kind: NgnHistoryKind;
  total: number | null;
  scope?: string;
  reason?: string;
  coverage?: { first_date: string | null; last_date: string | null; undated?: number };
  monthly: { month: string; count: number }[];
  items: Record<string, unknown>[];
};
export type BenchmarkConnectivity = {
  dataset_id: "gaslib_582"; data_version: string; edge_id: string; source_count: number; exit_count: number;
  method: "undirected_source_reachability_v1";
  reachable_exits_before: number; reachable_exits_after: number; newly_unreachable_node_count: number;
  newly_unreachable_exit_ids: string[];
  unreachable_node_ids_before: string[];
  unreachable_node_ids_after: string[];
};
export type PublicDatasetCatalog = { datasets: Array<{
  dataset_id: PublicDatasetId; label: string; data_mode: string; status: string; default: boolean;
  data_version?: string | null; description: string; source_url?: string; limitations?: string[];
}>; security_note: string };

export type PublicAEREventsResponse = {
  status: string; total: number; offset: number; limit: number;
  items: Array<Record<string, unknown>>; data_version?: string;
};
export type PublicAERAssetsResponse = {
  status: string; total: number; offset: number; limit: number;
  items: PublicDatasetFeature[]; data_version?: string;
};

const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
function validCatalog(value: unknown): value is PublicDatasetCatalog {
  return object(value) && Array.isArray(value.datasets) && value.datasets.every((item) => object(item)
    && PUBLIC_DATASET_OPTIONS.some(({ id }) => item.dataset_id === id)
    && typeof item.label === "string" && typeof item.status === "string" && typeof item.description === "string")
    && typeof value.security_note === "string";
}
function validOverview(value: unknown, id: PublicDatasetId): value is PublicDatasetOverview {
  return object(value) && value.dataset_id === id && typeof value.title === "string"
    && typeof value.status === "string" && typeof value.data_mode === "string"
    && (value.assets === undefined || Array.isArray(value.assets))
    && (value.nodes === undefined || Array.isArray(value.nodes))
    && (value.edges === undefined || Array.isArray(value.edges));
}
function validAEREvents(value: unknown): value is PublicAEREventsResponse {
  return object(value) && typeof value.status === "string" && typeof value.total === "number" && Number.isInteger(value.total)
    && typeof value.offset === "number" && Number.isInteger(value.offset)
    && typeof value.limit === "number" && Number.isInteger(value.limit)
    && Array.isArray(value.items) && value.items.every(object);
}
function validAERAssets(value: unknown): value is PublicAERAssetsResponse {
  return object(value) && typeof value.status === "string" && typeof value.total === "number" && Number.isInteger(value.total)
    && typeof value.offset === "number" && Number.isInteger(value.offset)
    && typeof value.limit === "number" && Number.isInteger(value.limit) && Array.isArray(value.items)
    && value.items.every((item) => object(item) && (item.pipe_id === null || typeof item.pipe_id === "string")
      && (item.record_id === null || typeof item.record_id === "string"));
}

export type CityServiceLinks = {
  dataset_id: "charlottesville"; status: string; main_matches: number | null; main_id?: string;
  main_status?: string; meter_matches: number | null; meter_link_status?: string;
};
export type CityDuration = { status: "ok" | "equal" | "missing" | "invalid"; seconds: number | null };
type CountGroup = { label: string; count: number };
export type CityAnalysis = {
  dataset_id: "charlottesville"; kind: string; source_url: string; fetched_at: string;
  start: string; end: string; total: number; undated: number; offset: number; limit: number;
  monthly: CountGroup[]; causes: CountGroup[]; areas: CountGroup[];
  durations: Record<string, Partial<Record<CityDuration["status"], number>>>;
  items: Array<{ id: string; time: string; link: string; cause: string | null; area: string | null;
    matches: { main: number; service: number } | null; durations: Record<string, CityDuration> }>;
};
export type ResearchOutages = {
  dataset_id: "schutterwald"; data_version: string; items: Array<{ id: string; impact: number }>;
};
export type CityEventPage = {
  dataset_id: "charlottesville"; total: number; time_basis: string;
  items: Array<{ id: string; grade?: string; status?: string; cause?: string; area?: string;
    facility_type?: string; source_record_id: number; source_url: string; durations?: Record<string, CityDuration>;
    timeline: { label: string; field: string; time: string | null }[] }>;
};
export type ResearchRun = {
  dataset_id: "schutterwald"; data_version: string; edge_id: string; execution_id: string; run_id: string;
  stable_result_sha256: string; input_sha256: string; unreachable_nodes: string[];
  buildings: Array<{ id: string; node: string; baseline: boolean; reachable: boolean;
    path: { source_node: string; nodes: string[]; pipes: string[] } | null }>;
  hydraulics: { status: string; reason?: string; pressure_bar?: Record<string, number>; baseline_pressure_bar?: Record<string, number>;
    mass_flow_kg_s?: Record<string, number>; mass_balance_error_kg_s?: number; excluded_model_demand_kg_s?: number };
};
export type SicilyCondition = {
  status: "published" | "pending_data";
  nodes: Record<string, { pressure_bar: number | null; throughput_sm3h: number | null;
    hhv_mj_sm3: number | null; wobbe_mj_sm3: number | null }>;
  pipes: Record<string, { velocity_m_s: number | null; flow_sm3h: number | null }>;
};
export type SicilyRun = {
  execution_id: string; edge_id: string; unreachable_nodes: string[];
  baseline_unreachable_nodes: string[]; reachable_before: number; reachable_after: number;
  alternate_path_edges: string[]; disconnected_branch_edges: string[];
  disconnected_model_demand_sm3_s: number | null;
  hydraulics: { status: string; reason?: string; solver_version?: string; reference_max_pressure_error_bar?: number;
    before_max_nodal_flow_error_sm3_s?: number; after_max_nodal_flow_error_sm3_s?: number;
    before_pressure_bar?: Record<string, number>; after_pressure_bar?: Record<string, number>;
    before_flow_sm3_s?: Record<string, number>; after_flow_sm3_s?: Record<string, number> };
};
export type SicilyAgentContext = {
  session_id: string; case_id: string; data_version: string; selected_id?: string; selected_kind?: "node" | "pipe";
};
export type SicilyAgentRun = Pick<SicilyAgentContext, "case_id" | "data_version"> & {
  execution_id: string; edge_id: string; input_sha256: string;
};
export type SicilyAgentAnswer = {
  dataset_id: "sicily_transmission"; data_version: string; case_id: string; rule_version: string;
  answer: string; rows: { label: string; value: string | number | null }[];
  source_url: string; boundary: string; model_status: "ready" | "unavailable" | "not_used";
  object?: { kind: "node" | "pipe"; id: string; case_id: string } | null;
  open_condition?: boolean; file_refs?: Record<string, string>; runs?: SicilyAgentRun[];
  plan?: { id: string; edges: string[]; cached_edges: string[]; expires_at: number };
};
export type SicilyBaseline = {
  execution_id: string; input_sha256: string; hydraulics: SicilyRun["hydraulics"];
};
type NgnSampleState = {
  pressure_bar_g: Record<string, number>; flow_kg_s: Record<string, number>;
  unavailable_sinks: string[]; reachable_nodes: string[]; max_nodal_mass_error_kg_s: number;
  sink_paths: Record<string, { source: string; nodes: string[]; pipes: string[] }>;
};
export type NgnSampleResult = {
  dataset_id: "ngn_distribution_example"; data_version: string;
  status: "ok" | "not_estimable"; reason?: string; solver?: string; gas?: string; friction_model?: string;
  execution_id: string; stable_result_sha256: string; input_sha256: Record<string, string>;
  task: { period: string; edge_id: string | null; trend: boolean };
  before?: NgnSampleState; after?: NgnSampleState | null;
  series?: Record<string, (NgnSampleState & { status: "ok"; reason?: never }) | { status: "not_estimable"; reason: string; pressure_bar_g?: never }>;
};
export class PublicGasDatasetApi {
  constructor(private readonly transport: GasTransport) {}
  private async get<T>(path: string, validate: (value: unknown) => value is T, signal?: AbortSignal, method: "GET" | "POST" = "GET", body?: unknown): Promise<T> {
    let response: { status: number; body: unknown };
    try { response = await this.transport.send({ method, path, signal, ...(body === undefined ? {} : { body }) }); }
    catch (error) {
      if (signal?.aborted || (error instanceof Error && error.name === "AbortError")) throw error;
      throw new GasApiError("network", "公开数据服务暂不可用；当前未切换或回退到其它数据源。");
    }
    if (response.status >= 200 && response.status < 300) {
      if (validate(response.body)) return response.body;
      throw new GasApiError("invalid_response", "公开数据接口响应不符合数据集合同，未展示不完整记录。");
    }
    if ([409, 422, 503].includes(response.status) && object(response.body) && typeof response.body.detail === "string") {
      throw new GasApiError(response.status === 503 ? "unavailable" : "http", response.body.detail, response.status);
    }
    throw gasHttpError(response.status, response.body);
  }
  ngnSampleRun(version: string, period: string, edgeId: string, trend: boolean, signal?: AbortSignal) {
    const params = new URLSearchParams({ version, period, trend: String(trend) });
    if (edgeId) params.set("edge_id", edgeId);
    return this.get("/api/v1/gas/public-data/ngn_distribution_example/run?" + params,
      (v): v is NgnSampleResult => object(v) && v.dataset_id === "ngn_distribution_example" &&
        v.data_version === version && object(v.task) && v.task.period === period &&
        v.task.edge_id === (edgeId || null) && v.task.trend === trend && typeof v.execution_id === "string" &&
        typeof v.stable_result_sha256 === "string" && object(v.input_sha256) &&
        (v.status === "not_estimable" && typeof v.reason === "string" ||
         v.status === "ok" && (trend ? object(v.series) : object(v.before) && (!edgeId || object(v.after)))),
      signal, "POST");
  }
  sicilyCondition(caseId: string, version: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ case_id: caseId });
    return this.get("/api/v1/gas/public-data/sicily_transmission/condition?" + params,
      (v): v is SicilyCondition => object(v) && v.dataset_id === "sicily_transmission" &&
        v.case_id === caseId && v.data_version === version && object(v.nodes) && object(v.pipes) &&
        (v.status === "published" || v.status === "pending_data"), signal);
  }
  sicilyBaseline(caseId: string, version: string, signal?: AbortSignal, session?: string) {
    const params = new URLSearchParams({ case_id: caseId, version });
    if (session) params.set("session_id", session);
    return this.get("/api/v1/gas/public-data/sicily_transmission/baseline?" + params,
      (v): v is SicilyBaseline => object(v) && v.dataset_id === "sicily_transmission" &&
        v.case_id === caseId && v.data_version === version && typeof v.execution_id === "string" &&
        typeof v.input_sha256 === "string" && object(v.hydraulics) && typeof v.hydraulics.status === "string",
      signal, "POST");
  }
  sicilyRun(caseId: string, edgeId: string, version: string, signal?: AbortSignal, mode: "topology" | "run" = "run", session?: string) {
    const params = new URLSearchParams({ case_id: caseId, edge_id: edgeId, version });
    if (session) params.set("session_id", session);
    return this.get(`/api/v1/gas/public-data/sicily_transmission/${mode}?` + params,
      (v): v is SicilyRun => object(v) && v.dataset_id === "sicily_transmission" &&
        v.case_id === caseId && v.data_version === version && v.edge_id === edgeId &&
        typeof v.execution_id === "string" && Array.isArray(v.unreachable_nodes) && object(v.hydraulics)
        && Array.isArray(v.baseline_unreachable_nodes) && Number.isInteger(v.reachable_before)
        && Number.isInteger(v.reachable_after) && Array.isArray(v.alternate_path_edges)
        && Array.isArray(v.disconnected_branch_edges),
      signal, "POST");
  }
  sicilyAgentHealth(signal?: AbortSignal) {
    return this.get("/api/v1/gas/public-data/sicily_transmission/agent/health",
      (v): v is { model_status: "configured" | "unavailable" } => object(v) &&
        v.dataset_id === "sicily_transmission" && (v.model_status === "configured" || v.model_status === "unavailable"), signal);
  }
  sicilyAgentAnswer(mode: "query" | "confirm", context: SicilyAgentContext, fields: Record<string, unknown>, signal?: AbortSignal) {
    return this.get("/api/v1/gas/public-data/sicily_transmission/agent/" + mode,
      (v): v is SicilyAgentAnswer => object(v) && v.dataset_id === "sicily_transmission" &&
        v.data_version === context.data_version && typeof v.case_id === "string" && typeof v.answer === "string" &&
        typeof v.boundary === "string" && Array.isArray(v.rows) &&
        (v.plan === undefined || object(v.plan) && typeof v.plan.id === "string" && Array.isArray(v.plan.edges)) &&
        (v.runs === undefined || Array.isArray(v.runs) && v.runs.every(run => object(run) &&
          run.data_version === context.data_version && run.case_id === v.case_id &&
          typeof run.execution_id === "string" && typeof run.edge_id === "string")),
      signal, "POST", { ...context, ...fields });
  }
  sicilyAgentExecution(context: SicilyAgentContext, run: SicilyAgentRun, signal?: AbortSignal) {
    return this.get("/api/v1/gas/public-data/sicily_transmission/agent/execution/" + encodeURIComponent(run.execution_id),
      (v): v is SicilyRun => object(v) && v.dataset_id === "sicily_transmission" &&
        v.data_version === context.data_version && v.case_id === run.case_id && v.edge_id === run.edge_id &&
        v.execution_id === run.execution_id && v.input_sha256 === run.input_sha256 &&
        Array.isArray(v.unreachable_nodes) && Array.isArray(v.baseline_unreachable_nodes) &&
        Array.isArray(v.alternate_path_edges) && Array.isArray(v.disconnected_branch_edges) && object(v.hydraulics),
      signal, "POST", context);
  }
  catalog(signal?: AbortSignal) {
    return this.get("/api/v1/gas/public-data/catalog", validCatalog, signal);
  }
  overview(datasetId: PublicDatasetId, options: {
    fieldCentre?: string; offset?: number; search?: string; bbox?: string; ngnFilters?: NgnFilters; assetKind?: string;
  } = {}, signal?: AbortSignal) {
    const params = new URLSearchParams({ limit: "120", offset: String(options.offset ?? 0) });
    if (datasetId === "aer_alberta" && options.fieldCentre) params.set("field_centre", options.fieldCentre);
    if (datasetId === "aer_alberta" && options.search?.trim()) params.set("search", options.search.trim());
    if (datasetId === "sgn_eastbourne" && options.bbox) params.set("bbox", options.bbox);
    if (datasetId === "charlottesville") {
      if (options.assetKind) params.set("asset_kind", options.assetKind);
      if (options.search) params.set("search", options.search);
      if (options.bbox) params.set("bbox", options.bbox);
    }
    if (datasetId === "ngn_the_north") {
      params.set("ngn_view", options.bbox === "region" ? "region" : "detail");
      if (options.bbox && options.bbox !== "region") params.set("bbox", options.bbox);
      if (options.search?.trim()) params.set("search", options.search.trim());
      for (const [key, value] of Object.entries(options.ngnFilters ?? {})) {
        if (value) params.set(key, value);
      }
    }
    return this.get(`/api/v1/gas/public-data/${encodeURIComponent(datasetId)}/overview?${params}`,
      (value): value is PublicDatasetOverview => validOverview(value, datasetId), signal);
  }
  cvilleServiceLinks(oid: string, signal?: AbortSignal) {
    return this.get("/api/v1/gas/public-data/charlottesville/service-links/" + encodeURIComponent(oid),
      (v): v is CityServiceLinks => object(v) && v.dataset_id === "charlottesville" && typeof v.status === "string"
        && (v.main_matches === null || typeof v.main_matches === "number")
        && (v.meter_matches === null || typeof v.meter_matches === "number"), signal);
  }
  cvilleEvents(kind: string, offset: number, filters: Record<string, string>, signal?: AbortSignal) {
    const params = new URLSearchParams({ kind, offset: String(offset) });
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    return this.get("/api/v1/gas/public-data/charlottesville/events?" + params,
      (v): v is CityEventPage => object(v) && v.dataset_id === "charlottesville" && v.kind === kind
        && typeof v.total === "number" && typeof v.time_basis === "string" && Array.isArray(v.items)
        && v.items.every((i) => object(i) && typeof i.id === "string" && Array.isArray(i.timeline)), signal);
  }
  cvilleAnalysis(kind: string, offset: number, filters: Record<string, string>, signal?: AbortSignal) {
    const params = new URLSearchParams({ kind, offset: String(offset) });
    for (const [key, value] of Object.entries(filters)) if (value) params.set(key, value);
    return this.get("/api/v1/gas/public-data/charlottesville/analysis?" + params,
      (v): v is CityAnalysis => object(v) && v.dataset_id === "charlottesville" && v.kind === kind
        && typeof v.total === "number" && object(v.durations)
        && [v.monthly, v.causes, v.areas, v.items].every(Array.isArray), signal);
  }
  schutterwaldOutages(version: string, signal?: AbortSignal) {
    return this.get("/api/v1/gas/public-data/schutterwald/outages?version=" + encodeURIComponent(version),
      (v): v is ResearchOutages => object(v) && v.dataset_id === "schutterwald" && v.data_version === version
        && Array.isArray(v.items) && v.items.every((row) => object(row) && typeof row.id === "string"
          && Number.isInteger(row.impact) && Number(row.impact) >= 0), signal);
  }
  schutterwaldRun(edge: string, version: string, hydraulic: boolean, signal?: AbortSignal) {
    const params = new URLSearchParams({ edge_id: edge, version, hydraulic: String(hydraulic) });
    return this.get("/api/v1/gas/public-data/schutterwald/run?" + params,
      (v): v is ResearchRun => object(v) && v.dataset_id === "schutterwald" && v.data_version === version
        && v.edge_id === edge && typeof v.execution_id === "string" && typeof v.run_id === "string"
        && typeof v.stable_result_sha256 === "string" && object(v.hydraulics)
        && Array.isArray(v.buildings) && Array.isArray(v.unreachable_nodes), signal);
  }
  ngnEvents(options: { kind: NgnHistoryKind; start?: string; end?: string; offset?: number }, signal?: AbortSignal) {
    const params = new URLSearchParams({ kind: options.kind, limit: "30", offset: String(options.offset ?? 0) });
    if (options.start) params.set("start", options.start);
    if (options.end) params.set("end", options.end);
    return this.get("/api/v1/gas/public-data/ngn/events?" + params, (value): value is NgnHistory =>
      object(value) && value.dataset_id === "ngn_the_north" && value.kind === options.kind
      && typeof value.data_version === "string" && (value.status === "ready" || value.status === "unavailable")
      && (value.total === null || typeof value.total === "number")
      && Array.isArray(value.items) && value.items.every(object) && Array.isArray(value.monthly), signal);
  }
  benchmarkConnectivity(edgeId: string, version: string, signal?: AbortSignal) {
    const params = new URLSearchParams({ edge_id: edgeId });
    return this.get("/api/v1/gas/public-data/gaslib_582/connectivity?" + params,
      (value): value is BenchmarkConnectivity => object(value) && value.dataset_id === "gaslib_582"
        && value.edge_id === edgeId && value.data_version === version
        && value.method === "undirected_source_reachability_v1"
        && ["source_count", "exit_count", "reachable_exits_before", "reachable_exits_after",
          "newly_unreachable_node_count"].every((key) => typeof value[key] === "number" && Number.isInteger(value[key]))
        && ["newly_unreachable_exit_ids", "unreachable_node_ids_before", "unreachable_node_ids_after"]
          .every((key) => Array.isArray(value[key]) && value[key].every((id) => typeof id === "string")), signal);
  }
  aerEvents(options: { pipeId?: string; search?: string; linkStatus?: string; offset?: number; limit?: number } = {}, signal?: AbortSignal) {
    const params = new URLSearchParams({ offset: String(options.offset ?? 0), limit: String(options.limit ?? 30) });
    if (options.pipeId) params.set("pipe_id", options.pipeId);
    if (options.search?.trim()) params.set("search", options.search.trim());
    if (options.linkStatus && options.linkStatus !== "all") params.set("link_status", options.linkStatus);
    return this.get(`/api/v1/gas/public-data/aer/events?${params}`,
      (value): value is PublicAEREventsResponse => validAEREvents(value), signal);
  }
  aerAssets(options: { fieldCentre?: string; search?: string; offset?: number; limit?: number } = {}, signal?: AbortSignal) {
    const params = new URLSearchParams({ offset: String(options.offset ?? 0), limit: String(options.limit ?? 30) });
    if (options.fieldCentre && options.fieldCentre !== "__all__") params.set("field_centre", options.fieldCentre);
    if (options.search?.trim()) params.set("search", options.search.trim());
    return this.get(`/api/v1/gas/public-data/aer/assets?${params}`,
      (value): value is PublicAERAssetsResponse => validAERAssets(value), signal);
  }
}
