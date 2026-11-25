import type {
  GasDrillCompareRequest, GasDrillComparison, GasDrillGraph, GasDrillGraphState, GasDrillReference,
  GasDrillToolRequest, GasDrillToolResult, GasDrillTrust, GasDrillWorkspace, GasM2Binding, GasSyntheticScenario,
  GasScenarioAudit, GasScenarioExplanation, GasScenarioReport,
} from "../../shared/contracts/platform";
import { sameBinding } from "./dataM2Contract";

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string";
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(string);
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const digest = (value: unknown) => string(value) && /^[a-f0-9]{64}$/.test(value);
const fault = (value: unknown) => value === "synthetic_edge_exclusion" || value === "synthetic_dual_source_split";
const statuses = ["draft", "analyzed", "rolled_back"];

export function gasScenarioId(value: unknown): value is string {
  return string(value) && /^syn-gas-drill-[a-f0-9]{32}$/.test(value);
}

function audit(value: unknown): value is GasScenarioAudit {
  return object(value) && string(value.actor_id) && string(value.occurred_at) && count(value.revision) && value.revision > 0
    && ((value.action === "created" && value.current_status === "draft" && value.previous_status === null && value.analysis_sha256 === null)
      || (value.action === "analyzed" && value.current_status === "analyzed" && string(value.previous_status)
        && statuses.includes(value.previous_status) && digest(value.analysis_sha256))
      || (value.action === "rolled_back" && value.current_status === "rolled_back" && value.previous_status === "analyzed"
        && digest(value.analysis_sha256)));
}

export function graphState(value: unknown): value is GasDrillGraphState {
  return object(value) && strings(value.reachable_node_ids) && Array.isArray(value.components) && value.components.length > 0
    && value.components.every(row => object(row) && strings(row.node_ids) && row.node_ids.length > 0 && strings(row.source_ids));
}

function graphEvidence(value: unknown): value is GasDrillGraph {
  return object(value) && value.rule_version === "gas-drill-graph/1" && digest(value.sha256)
    && object(value.source_nodes) && Object.values(value.source_nodes).every(string)
    && graphState(value.baseline) && graphState(value.scenario)
    && strings(value.excluded_edge_ids) && value.excluded_edge_ids.length > 0 && strings(value.alternative_path_edge_ids);
}

export function gasScenario(value: unknown, binding: GasM2Binding, expected?: {
  id?: string; pipe?: string; revision?: number; faultKind?: GasSyntheticScenario["input"]["fault_kind"];
}): value is GasSyntheticScenario {
  if (!object(value) || !gasScenarioId(value.scenario_id) || (expected?.id && value.scenario_id !== expected.id)
    || value.data_kind !== "internal_synthetic" || value.coordinate_system !== "schematic"
    || !object(value.input) || !sameBinding(value.input.binding, binding) || !fault(value.input.fault_kind)
    || (expected?.faultKind && value.input.fault_kind !== expected.faultKind)
    || !string(value.input.pipe_id) || (expected?.pipe && value.input.pipe_id !== expected.pipe)
    || !object(value.source) || !digest(value.source.network_content_sha256)
    || !(value.source.source_uri === null || string(value.source.source_uri))
    || !Array.isArray(value.source.files) || !value.source.files.every(file => object(file) && string(file.name) && digest(file.sha256))
    || !audit(value.transition) || (expected?.revision && value.transition.revision !== expected.revision)) return false;
  if (value.transition.current_status === "draft") return value.analysis === null;
  const analysis = value.analysis;
  if (!object(analysis) || !string(analysis.algorithm) || analysis.analysis_sha256 !== value.transition.analysis_sha256
    || analysis.model_scope !== "graph_connectivity_only"
    || !["actual_supply", "actual_outage", "pressure", "flow", "leakage"].every(key => analysis[key] === "unknown")
    || !object(analysis.impact)) return false;
  const impact = analysis.impact;
  return string(impact.fault_edge_id) && count(impact.baseline_reachable_node_count) && count(impact.scenario_reachable_node_count)
    && strings(impact.newly_unreachable_node_ids)
    && impact.baseline_reachable_node_count - impact.scenario_reachable_node_count === impact.newly_unreachable_node_ids.length
    && strings(impact.affected_edge_ids) && impact.affected_edge_ids.includes(impact.fault_edge_id)
    && strings(impact.affected_pipe_ids) && impact.affected_pipe_ids.includes(value.input.pipe_id)
    && graphEvidence(analysis.graph) && analysis.graph.baseline.reachable_node_ids.length === impact.baseline_reachable_node_count
    && analysis.graph.scenario.reachable_node_ids.length === impact.scenario_reachable_node_count
    && analysis.graph.excluded_edge_ids.includes(impact.fault_edge_id)
    && analysis.graph.excluded_edge_ids.length === (value.input.fault_kind === "synthetic_dual_source_split" ? 2 : 1);
}

export function groundedAnswer(value: Record<string, unknown>) {
  return string(value.request_id) && strings(value.evidence_refs) && strings(value.facts)
    && string(value.answer) && value.answer.split("\n").every(line => (value.facts as string[]).includes(line))
    && count(value.model_calls) && (value.model_error === null || string(value.model_error))
    && ((value.explanation_source === "backend_facts" && value.model_id === null)
      || (value.explanation_source === "qwen_evidence_selection" && value.model_id === "Qwen3.8-27B" && value.model_calls > 0));
}

export function gasScenarioExplanation(value: unknown, scenario: GasSyntheticScenario): value is GasScenarioExplanation {
  const hash = scenario.analysis?.analysis_sha256;
  return Boolean(hash) && object(value) && value.tool_name === "synthetic_scenario.read_analysis" && groundedAnswer(value)
    && value.scenario_id === scenario.scenario_id && value.revision === scenario.transition.revision
    && sameBinding(value.binding, scenario.input.binding) && value.analysis_sha256 === hash
    && strings(value.evidence_refs) && value.evidence_refs.includes("gas-scenario-analysis:sha256:" + hash);
}

export function gasScenarioReport(value: unknown, scenario: GasSyntheticScenario): value is GasScenarioReport {
  return object(value) && value.report_version === "gas-scenario-report/1" && string(value.limitation)
    && string(value.audit_boundary) && trust(value.trust)
    && gasScenario(value.scenario, scenario.input.binding, { id: scenario.scenario_id, revision: scenario.transition.revision })
    && value.scenario.analysis?.analysis_sha256 === scenario.analysis?.analysis_sha256
    && value.scenario.analysis?.graph?.sha256 === scenario.analysis?.graph?.sha256
    && strings(value.evidence_refs) && Array.isArray(value.audit) && value.audit.length === scenario.transition.revision
    && value.audit.every((entry, index) => audit(entry) && entry.revision === index + 1 && entry.actor_id === scenario.transition.actor_id);
}

function trust(value: unknown): value is GasDrillTrust {
  return object(value) && value.rule_version === "gas-drill-trust/1"
    && value.identity_scope === "internal_synthetic_owner" && value.revision_policy === "exact_read_compare_and_latest_write"
    && count(value.source_files_verified) && (value.graph_status === "valid" || value.graph_status === "valid_with_warnings")
    && strings(value.graph_warnings) && count(value.observation_count)
    && object(value.observation_quality) && Object.values(value.observation_quality).every(count)
    && object(value.observation_channels) && Object.values(value.observation_channels).every(count);
}

function reference(value: unknown): value is GasDrillReference {
  return object(value) && gasScenarioId(value.scenario_id) && count(value.revision) && value.revision > 0 && digest(value.analysis_sha256);
}

export function sameReference(value: unknown, expected: GasDrillReference | null) {
  return expected === null ? value === null : reference(value) && value.scenario_id === expected.scenario_id
    && value.revision === expected.revision && value.analysis_sha256 === expected.analysis_sha256;
}

function sameSelection(value: unknown, expected: GasDrillCompareRequest) {
  return object(value) && sameBinding(value.binding, expected.binding)
    && sameReference(value.before, expected.before) && sameReference(value.after, expected.after);
}

export function scenarioReference(scenario: GasSyntheticScenario): GasDrillReference | null {
  return scenario.analysis && scenario.transition.current_status === "analyzed" ? {
    scenario_id: scenario.scenario_id, revision: scenario.transition.revision, analysis_sha256: scenario.analysis.analysis_sha256,
  } : null;
}

export function gasDrillWorkspace(value: unknown, binding: GasM2Binding): value is GasDrillWorkspace {
  return object(value) && sameBinding(value.binding, binding) && trust(value.trust)
    && Array.isArray(value.presets) && value.presets.every(row => object(row) && string(row.name) && string(row.label)
      && strings(row.pipe_ids) && row.pipe_ids.length > 0 && fault(row.fault_kind) && string(row.algorithm))
    && Array.isArray(value.versions) && value.versions.every(row => object(row) && reference(row.reference)
      && string(row.pipe_id) && fault(row.fault_kind) && typeof row.latest === "boolean");
}

function comparisonSide(value: unknown, binding: GasM2Binding, expected: GasDrillReference | null) {
  if (!object(value) || !graphState(value.graph)) return false;
  if (expected === null) return value.report === null;
  const report = value.report;
  if (!object(report) || !gasScenario(report.scenario, binding, { id: expected.scenario_id, revision: expected.revision })
    || !gasScenarioReport(report, report.scenario) || report.scenario.analysis?.analysis_sha256 !== expected.analysis_sha256
    || report.scenario.transition.current_status !== "analyzed") return false;
  return JSON.stringify(value.graph) === JSON.stringify(report.scenario.analysis.graph?.scenario);
}

export function gasDrillComparison(value: unknown, expected: GasDrillCompareRequest): value is GasDrillComparison {
  return object(value) && value.rule_version === "gas-drill-comparison/1" && sameSelection(value.selection, expected)
    && comparisonSide(value.before, expected.binding, expected.before) && comparisonSide(value.after, expected.binding, expected.after)
    && digest(value.comparison_sha256) && strings(value.newly_reachable_node_ids) && strings(value.newly_unreachable_node_ids)
    && strings(value.evidence_refs) && value.evidence_refs.includes("gas-scenario-comparison:sha256:" + value.comparison_sha256)
    && string(value.limitation) && string(value.audit_boundary);
}

const toolNames = {
  lookup: "synthetic_scenario.lookup", simulate: "synthetic_scenario.simulate_topology", compare: "synthetic_scenario.compare_versions",
  report: "synthetic_scenario.report_evidence",
} as const;

export function gasDrillToolResult(value: unknown, request: GasDrillToolRequest): value is GasDrillToolResult {
  return object(value) && value.tool_name === toolNames[request.tool] && value.verified_sha256 === request.expected_sha256
    && sameBinding(value.binding, request.selection.binding) && sameSelection(value.selection, request.selection) && groundedAnswer(value);
}
