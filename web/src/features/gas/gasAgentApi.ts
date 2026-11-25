import type { GasAgentEvidenceScope, GasAgentHealthResponse, GasAgentQueryRequest, GasAgentQueryResponse, GasAgentToolResult } from "../../shared/contracts/platform";
import { asset, event, capabilityResponse, GasApiError, gasHttpError, BrowserGasTransport, type GasTransport, toGasError } from "./engineeringApi";
import { gasCuvAgentContext, gasCuvAgentData, matchesCuvAgentContext } from "./cuvAgentContext";
import { gasCuvEngineeringAssessment, gasCuvEngineeringCapability } from "./cuvEngineeringContract";
import { gasCuvBenchmarkReport, matchesBenchmarkBinding, WEYMOUTH_SUITE } from "./cuvBenchmarkContract";
import { gasMonitoringObservation } from "./monitoringContract";
export type { GasAgentHealthResponse, GasAgentQueryRequest, GasAgentQueryResponse, GasAgentToolResult };
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every((v) => typeof v === "string");
const count = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
function gasEvidenceScope(value: unknown): value is GasAgentEvidenceScope {
  return record(value) && ["gas_m0_case", "gas_m0_monitoring", "gas_m0_graph_cuv", "public_reference_benchmark"].includes(String(value.scope))
    && typeof value.dataset_id === "string" && typeof value.dataset_version === "string"
    && value.data_mode === "synthetic" && sha(value.source_sha256);
}
const cuvTools = ["cuv.query", "cuv.engineering.query"];
const tools = ["asset.query", "event.query", "map.locate", "capabilities.query", "event.explain", "monitoring.query", "cuv.benchmark.query", ...cuvTools];
function scopedEvent(data: Record<string, unknown>) {
  if (!event(data.event)) return false;
  const allowed = data.event.asset_ids ?? [];
  const pipes = data.related_pipe_ids;
  return pipes === undefined || (strings(pipes) && pipes.every((id) => allowed.includes(id)));
}
export function validTool(value: unknown): value is GasAgentToolResult {
  if (!record(value) || !tools.includes(String(value.tool_name)) || value.tool_version !== "gas-tools-0.1.0"
    || !["succeeded", "partial", "unavailable", "not_found"].includes(String(value.status))
    || !record(value.data) || !strings(value.evidence_refs) || typeof value.limitation !== "string"
    || (value.evidence_scope != null && !gasEvidenceScope(value.evidence_scope))
    || (value.monitoring_scope != null && (!gasEvidenceScope(value.monitoring_scope) || value.monitoring_scope.scope !== "gas_m0_monitoring"))
    || (value.engineering_capability != null && (value.tool_name !== "cuv.query" || !gasCuvEngineeringCapability(value.engineering_capability)))
    || (value.engineering_assessment != null && (value.tool_name !== "cuv.engineering.query" || value.status !== "succeeded"))
    || (value.cuv_comparison_binding != null && (!record(value.cuv_comparison_binding)
      || !count(value.cuv_comparison_binding.expected_revision) || !sha(value.cuv_comparison_binding.input_sha256)))) return false;
  if (value.tool_name === "cuv.benchmark.query") {
    const report = value.benchmark_report;
    return gasCuvBenchmarkReport(report) && gasEvidenceScope(value.evidence_scope)
      && value.evidence_scope.scope === "public_reference_benchmark"
      && value.evidence_scope.dataset_id === report.binding.suite_id
      && value.evidence_scope.dataset_version === report.binding.suite_version
      && value.evidence_scope.source_sha256 === report.binding.suite_sha256
      && value.status === (report.progress.benchmark_status === "benchmark_passed" ? "succeeded" : "unavailable")
      && Object.keys(value.data).length === 0;
  }
  if (value.benchmark_report != null) return false;
  if (value.status === "not_found" || value.status === "unavailable") return Object.keys(value.data).length === 0;
  const data = value.data;
  if (data.monitoring_context !== undefined && !gasMonitoringObservation(data.monitoring_context)) return false;
  switch (value.tool_name) {
    case "asset.query": return asset(data.asset);
    case "event.query": return scopedEvent(data)
      || (data.related_pipe_ids === undefined && Array.isArray(data.items) && data.items.every(event));
    case "event.explain": return scopedEvent(data) && typeof data.explanation === "string";
    case "capabilities.query": return capabilityResponse(data);
    case "cuv.query": return gasCuvAgentData(data)
      && (!value.engineering_capability || (gasCuvEngineeringCapability(value.engineering_capability)
        && value.engineering_capability.status === "ready_synthetic"));
    case "cuv.engineering.query":
      return gasCuvEngineeringAssessment(value.engineering_assessment) && gasCuvAgentContext(data.context)
        && value.engineering_assessment.case_id === data.context.case_id
        && value.engineering_assessment.scenario_id === data.context.scenario_id
        && Date.parse(value.engineering_assessment.scenario_at) === Date.parse(data.context.scenario_at)
        && gasEvidenceScope(value.evidence_scope)
        && value.engineering_assessment.evidence_scope.dataset_id === value.evidence_scope.dataset_id
        && value.engineering_assessment.binding.replay_content_sha256 === value.evidence_scope.source_sha256;
    case "monitoring.query": return gasMonitoringObservation(data.monitoring_context);
    case "map.locate": return data.view === "topology" && data.coordinate_authorized === false
      && data.location_status === "not_available" && strings(data.asset_ids)
      && (data.event_id === null || typeof data.event_id === "string");
    default: return false;
  }
}
export function validAgentResponse(value: unknown): value is GasAgentQueryResponse {
  return record(value) && typeof value.request_id === "string" && !!value.request_id
    && value.data_mode === "synthetic" && value.dataset_id === (value.intent === "cuv_benchmark_query" ? WEYMOUTH_SUITE : "syn-gas-network-html54-0.1.0")
    && ((value.model_status === "unavailable" && value.parser_source === "local_rule_not_llm")
      || (value.model_status === "ready" && value.parser_source === "qwen_intent" && value.model_id === "Qwen3.8-27B"))
    && ["succeeded", "partial", "unavailable", "not_found"].includes(String(value.status))
    && ["asset_query", "event_query", "map_locate", "capabilities_query", "event_explain", "cuv_query", "cuv_engineering_query", "cuv_benchmark_query", "monitoring_query", "clarify"].includes(String(value.intent))
    && Array.isArray(value.tools) && value.tools.every(validTool)
    && strings(value.evidence_refs) && strings(value.limitations)
    && (value.evidence_scope == null || gasEvidenceScope(value.evidence_scope))
    && (value.model_calls === undefined || count(value.model_calls))
    && (value.model_usage === undefined || (record(value.model_usage) && Object.values(value.model_usage).every(count)))
    && (value.explanation == null || typeof value.explanation === "string")
    && (value.explanation_source === undefined || ["none", "qwen_evidence_selection"].includes(String(value.explanation_source)));
}
export function validAgentHealth(value: unknown): value is GasAgentHealthResponse {
  return record(value) && value.enabled === true
    && ((value.model_status === "ready" && value.parser_source === "qwen_intent" && value.provider_configured === true && value.status === "ready")
      || (value.model_status === "unavailable" && value.parser_source === "local_rule_not_llm" && value.status === "unavailable")
      || (value.model_status === "starting" && value.parser_source === "local_rule_not_llm" && value.provider_configured === true && value.status === "starting"))
    && typeof value.provider_configured === "boolean"
    && value.tools_status === "ready" && value.read_only === true && ["ready", "starting", "unavailable"].includes(String(value.status)) && typeof value.reason === "string";
}
export class GasAgentApi {
  constructor(private readonly transport: GasTransport = new BrowserGasTransport()) {}
  private async request<T>(method: "GET" | "POST", path: string, valid: (value: unknown) => value is T, signal?: AbortSignal, body?: unknown): Promise<T> {
    let response;
    try { response = await this.transport.send({ method, path, signal, ...(body === undefined ? {} : { body }) }); }
    catch (err) {
      if (signal?.aborted || (err instanceof Error && ["AbortError", "TimeoutError"].includes(err.name))) throw err;
      throw toGasError(err);
    }
    if (response.status !== 200) throw gasHttpError(response.status, response.body);
    if (!valid(response.body)) throw new GasApiError("invalid_response", "燃气 Agent 响应不符合合同；未展示或执行其结果。");
    return response.body;
  }
  health(signal?: AbortSignal) { return this.request("GET", "/api/v1/gas/engineering/agent/health", validAgentHealth, signal); }
  query(body: GasAgentQueryRequest, signal?: AbortSignal) {
    const validate = (value: unknown): value is GasAgentQueryResponse => {
      if (!validAgentResponse(value)) return false;
      if (value.status === "unavailable" && value.tools.length === 0 && value.model_calls === 0
        && ["cross_dataset_unverified", "engineering_evidence_unavailable", "benchmark_scope_conflict"].includes(value.error_code ?? "")) return true;
      if (body.tool_hint === "cuv.benchmark.query" || body.benchmark_binding) {
        const tool = value.tools[0];
        return value.intent === "cuv_benchmark_query" && value.tools.length === 1
          && tool?.tool_name === "cuv.benchmark.query" && !!tool.benchmark_report
          && (!body.benchmark_binding || matchesBenchmarkBinding(tool.benchmark_report.binding, body.benchmark_binding));
      }
      if (body.case_binding && value.evidence_scope?.scope === "gas_m0_case"
        && value.evidence_scope.source_sha256 !== body.case_binding.case_content_sha256) return false;
      if (body.tool_hint === "cuv.query" && (value.intent !== "cuv_query" || value.tools.length !== 1
        || !value.tools.every((tool) => tool.tool_name === "cuv.query"
          && (!gasCuvAgentData(tool.data) || (matchesCuvAgentContext(tool.data.context, body)
            && (!body.cuv_draft || (tool.data.comparison_change?.status === "preview"
              && tool.data.comparison_change.before.input_sha256 === body.cuv_draft.input_sha256))))))) return false;
      if (body.tool_hint === "cuv.engineering.query" && (value.intent !== "cuv_engineering_query" || value.tools.length !== 1
        || !value.tools.every((tool) => tool.tool_name === "cuv.engineering.query"
          && (!gasCuvAgentContext(tool.data?.context) || matchesCuvAgentContext(tool.data.context, body))))) return false;
      return value.tools.every((tool) => {
        if (tool.status !== "succeeded" && tool.status !== "partial") return true;
        if (cuvTools.includes(tool.tool_name)) {
          if (body.cuv_replay_sha256 && (tool.evidence_scope?.scope !== "gas_m0_graph_cuv"
            || tool.evidence_scope.source_sha256 !== body.cuv_replay_sha256)) return false;
          const expected = body.cuv_comparison_binding ?? body.cuv_draft;
          if (expected && (tool.cuv_comparison_binding?.input_sha256 !== expected.input_sha256
            || tool.cuv_comparison_binding?.expected_revision !== expected.expected_revision)) return false;
        }
        if (tool.engineering_assessment && body.case_binding
          && (tool.engineering_assessment.binding.case_content_sha256 !== body.case_binding.case_content_sha256
            || tool.engineering_assessment.binding.network_content_sha256 !== body.case_binding.network_content_sha256)) return false;
        if (!body.monitoring_ref || !["monitoring.query", "event.query", "event.explain", ...cuvTools].includes(tool.tool_name)) return true;
        const monitoring = tool.data?.monitoring_context;
        return gasMonitoringObservation(monitoring, body.monitoring_ref)
          && (!body.monitoring_policy_id || monitoring.policy_id === body.monitoring_policy_id);
      });
    };
    return this.request("POST", "/api/v1/gas/engineering/agent/query", validate, signal, body);
  }
}
export function toolAssetIds(tool: GasAgentToolResult): string[] {
  if (!tool.data) return [];
  if (tool.status !== "succeeded" && tool.status !== "partial") return [];
  if (asset(tool.data.asset)) return [tool.data.asset.asset_id];
  if (tool.tool_name === "monitoring.query" && gasMonitoringObservation(tool.data.monitoring_context))
    return [tool.data.monitoring_context.asset_id];
  if (event(tool.data.event)) return strings(tool.data.related_pipe_ids) ? tool.data.related_pipe_ids : tool.data.event.asset_ids ?? [];
  if (Array.isArray(tool.data.items)) {
    return tool.data.items.filter(event).flatMap((item) => item.asset_ids ?? []);
  }
  if (tool.tool_name === "map.locate" && strings(tool.data.asset_ids)) return tool.data.asset_ids;
  if (tool.tool_name === "cuv.query" && gasCuvAgentData(tool.data) && tool.data.context.asset_id) {
    return [tool.data.context.asset_id];
  }
  return [];
}
export function toolEventId(tool: GasAgentToolResult): string | undefined {
  if (!tool.data) return undefined;
  if (event(tool.data.event)) return tool.data.event.event_id;
  if (tool.tool_name === "cuv.query" && gasCuvAgentData(tool.data)) return tool.data.context.event_id ?? undefined;
  return tool.tool_name === "map.locate" && typeof tool.data.event_id === "string" ? tool.data.event_id : undefined;
}

export function toolEventIds(tool: GasAgentToolResult): string[] {
  if (!tool.data) return [];
  if (event(tool.data.event)) return [tool.data.event.event_id];
  if (tool.tool_name === "monitoring.query" && gasMonitoringObservation(tool.data.monitoring_context) && tool.data.monitoring_context.event)
    return [tool.data.monitoring_context.event.event_id];
  if (Array.isArray(tool.data.items)) return tool.data.items.filter(event).map((item) => item.event_id);
  if (tool.tool_name === "cuv.query" && gasCuvAgentData(tool.data)) return tool.data.event_evidence.map((item) => item.event_id);
  return [];
}
