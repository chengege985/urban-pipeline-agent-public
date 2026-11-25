import type {
  CuvEventEvidence, GasAgentQueryRequest, GasAgentToolResult, GasCuvCollaborationSnapshot, GasCuvCriteria,
  GasCuvCriteriaComparison, GasCuvReplayResponse,
} from "../../shared/contracts/platform";
import { gasCuvCriteria, gasCuvRankedComparison } from "./cuvCollaborationContract";
import { gasCuvMonitoringAt, gasCuvStepAt } from "./cuvReplayContract";

type CuvAgentContext = {
  case_id: string;
  scenario_id: string;
  event_id: string | null;
  asset_id: string | null;
  at: string;
  scenario_at: string;
};

type ConfirmedComparison = Pick<GasCuvCollaborationSnapshot, "revision" | "input_sha256" | "criteria" | "comparison">;

type ComparisonChange = {
  status: "preview" | "confirmed";
  before: ConfirmedComparison;
  after_criteria: GasCuvCriteria;
  after_comparison: GasCuvCriteriaComparison;
  confirmed_revision: number | null;
};

type CuvAgentPlan = {
  plan_id: string;
  label: string;
  affected_service_hours: number | null;
  topology_restoration_hours: number | null;
  peak_affected_service_count: number | null;
};

type CuvAgentData = {
  context: CuvAgentContext;
  summary: string;
  plans: CuvAgentPlan[];
  event_evidence: CuvEventEvidence[];
  confirmed_comparison: ConfirmedComparison | null;
  comparison_change?: ComparisonChange | null;
};

type Selection = Pick<GasAgentQueryRequest, "event_id" | "asset_id">;

export function cuvAgentRequest(
  report: Pick<GasCuvReplayResponse, "case_id" | "scenario_at" | "event_evidence">, selection: Selection,
): GasAgentQueryRequest {
  const selectedEvent = report.event_evidence.find((entry) => entry.event_id === selection.event_id);
  const relatedAssetTime = selection.asset_id && !selection.event_id
    ? report.event_evidence.filter((entry) => entry.asset_ids.includes(selection.asset_id!))
      .sort((left, right) => Date.parse(left.replay_at) - Date.parse(right.replay_at))[0]?.replay_at
    : undefined;
  return {
    question: "比较当前燃气 CUV 推演的两个方案，解释图影响与恢复时间的取舍并引用证据。",
    case_id: report.case_id,
    ...(selection.event_id ? { event_id: selection.event_id } : {}),
    ...(selection.asset_id ? { asset_id: selection.asset_id } : {}),
    tool_hint: "cuv.query",
    cuv_at: selectedEvent?.replay_at ?? relatedAssetTime ?? report.scenario_at,
  };
}

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const instant = (value: unknown): value is string => typeof value === "string"
  && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
const number = (value: unknown): boolean => typeof value === "number" && Number.isFinite(value) && value >= 0;
const int = (value: unknown): boolean => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const sha = (value: unknown): boolean => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const strings = (value: unknown): boolean => Array.isArray(value) && value.every(text);

function caseEvent(value: unknown, scenarioAt: string): value is CuvEventEvidence {
  return object(value) && text(value.event_id) && text(value.title)
    && ["given_fault", "given_isolation", "synthetic_observation"].includes(String(value.event_kind))
    && strings(value.asset_ids) && strings(value.evidence_refs) && sha(value.source_sha256)
    && instant(value.replay_at) && typeof value.elapsed_hours === "number" && number(value.elapsed_hours)
    && Math.abs(Date.parse(value.replay_at) - Date.parse(scenarioAt) - value.elapsed_hours * 3600000) < 1;
}

function gasCuvConfirmed(value: unknown): value is ConfirmedComparison {
  return object(value) && int(value.revision) && sha(value.input_sha256)
    && gasCuvCriteria(value.criteria) && gasCuvRankedComparison(value.comparison);
}

export function gasCuvAgentContext(value: unknown): value is CuvAgentContext {
  return object(value) && text(value.case_id) && text(value.scenario_id)
    && instant(value.scenario_at) && instant(value.at) && Date.parse(value.at) >= Date.parse(value.scenario_at)
    && (value.event_id === null || text(value.event_id)) && (value.asset_id === null || text(value.asset_id));
}

export function gasCuvAgentData(value: unknown): value is CuvAgentData {
  if (!object(value) || !gasCuvAgentContext(value.context) || !text(value.summary)) return false;
  const context = value.context;
  const scenarioAt = context.scenario_at;
  const selectedAt = Date.parse(context.at);
  const horizon = (selectedAt - Date.parse(scenarioAt)) / 3600000;
  if (horizon < 0 || !Array.isArray(value.plans) || value.plans.length < 2 || !value.plans.every((entry) =>
    object(entry) && text(entry.plan_id) && text(entry.label) && ["replayed", "blocked"].includes(String(entry.status))
    && (entry.phase === null || gasCuvStepAt(entry.phase, scenarioAt, horizon))
    && (entry.selected_asset_blocked === null || typeof entry.selected_asset_blocked === "boolean")
    && (entry.affected_service_hours === null || number(entry.affected_service_hours))
    && (entry.topology_restoration_hours === null || number(entry.topology_restoration_hours))
    && (entry.peak_affected_service_count === null || int(entry.peak_affected_service_count)))) return false;
  if (!Array.isArray(value.event_evidence) || !value.event_evidence.every((event) => caseEvent(event, scenarioAt))
    || (context.event_id !== null && !value.event_evidence.some((event) =>
      event.event_id === context.event_id && Date.parse(event.replay_at) === selectedAt))) return false;
  if (!Array.isArray(value.unknown_metrics) || !value.unknown_metrics.every((item) =>
    object(item) && text(item.code) && text(item.reason) && item.status === "unknown" && item.value === null)
    || !gasCuvMonitoringAt(value.monitoring_evidence, scenarioAt)) return false;
  const confirmed = value.confirmed_comparison;
  if (confirmed !== null && !gasCuvConfirmed(confirmed)) return false;
  const change = value.comparison_change;
  return change == null || (object(change) && gasCuvConfirmed(change.before)
    && gasCuvCriteria(change.after_criteria) && gasCuvRankedComparison(change.after_comparison)
    && ((change.status === "preview" && change.confirmed_revision === null)
      || (change.status === "confirmed" && confirmed !== null && change.confirmed_revision === confirmed.revision)));
}

export function cuvAgentFollowUp(question: string, tool: GasAgentToolResult | undefined): Partial<GasAgentQueryRequest> | undefined {
  if ((tool?.tool_name !== "cuv.query" && tool?.tool_name !== "cuv.engineering.query")
    || tool.evidence_scope?.scope !== "gas_m0_graph_cuv" || !gasCuvAgentContext(tool.data?.context)
    || !/刚才|上一轮|前面|这两|两案|方案|确认后|差异|变化|取舍|版本|还有哪些缺项/.test(question)
    || /阈值|监测|读数|观测/.test(question)) return undefined;
  const { context } = tool.data;
  return {
    tool_hint: tool.tool_name, cuv_at: context.at,
    asset_id: context.asset_id, event_id: context.event_id,
    cuv_replay_sha256: tool.evidence_scope.source_sha256,
    cuv_comparison_binding: tool.cuv_comparison_binding,
  };
}

export function matchesCuvAgentContext(context: CuvAgentContext, request: GasAgentQueryRequest): boolean {
  return (!request.case_id || request.case_id === context.case_id)
    && (request.event_id ?? null) === context.event_id
    && (request.asset_id ?? null) === context.asset_id
    && (!request.cuv_at || Date.parse(request.cuv_at) === Date.parse(context.at));
}
