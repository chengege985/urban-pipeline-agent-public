import type {
  GasDataTopic, GasFieldMetric, GasM2Binding, GasM2TopicAnswer, GasM2TopicQuestion, GasQualityWorkbench,
  GasSourceStressAnswer, GasSourceStressQuestion, GasSourceStressRequest, GasSourceStressResult, GasTopicSelection,
} from "../../shared/contracts/platform";
import { sameBinding } from "./dataM2Contract";
import { graphState, groundedAnswer, sameReference } from "./scenarioContract";

export const GAS_TOPIC_QUESTION_LIMIT = 600;

export function topicFollowUp(question: string, previous?: {
  question: string; answer: Pick<GasM2TopicAnswer, "answer" | "facts">;
}): string {
  const current = [...question.trim()].slice(0, GAS_TOPIC_QUESTION_LIMIT);
  if (!previous) return current.join("");
  const prefix = "同一专题上一轮问答\n";
  const marker = "\n当前追问\n";
  const room = GAS_TOPIC_QUESTION_LIMIT - prefix.length - marker.length - current.length;
  if (room <= 0) return current.join("");
  const { facts, answer } = previous.answer;
  const selectedFacts = answer.split("\n").filter(line => line !== facts[0] && line !== facts.at(-1));
  const priorQuestion = [...previous.question].slice(0, 160).join("");
  const context = [...[priorQuestion, ...selectedFacts].join("\n")].slice(0, room).join("");
  return prefix + context + marker + current.join("");
}

const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === "object" && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string";
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(string);
const count = (value: unknown): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const sha = (value: unknown) => string(value) && /^[a-f0-9]{64}$/.test(value);
const file = (value: unknown) => object(value) && string(value.name) && sha(value.sha256);

function metric(value: unknown): value is GasFieldMetric {
  return object(value) && string(value.id) && string(value.definition) && count(value.checked) && count(value.valid)
    && value.valid <= value.checked && (value.checked === 0 ? value.ratio === null : typeof value.ratio === "number"
      && Math.abs(value.ratio - value.valid / value.checked) <= 0.000001);
}

function sameTopic(value: unknown, expected: GasTopicSelection) {
  return object(value) && value.scope === expected.scope && (value.district_id ?? null) === (expected.district_id ?? null)
    && (value.subnet_id ?? null) === (expected.subnet_id ?? null) && (value.asset_id ?? null) === (expected.asset_id ?? null);
}

function topicAsset(value: unknown) {
  return object(value) && string(value.asset_id) && string(value.asset_type)
    && ["pipe","node","valve","consumer"].includes(value.asset_type) && object(value.fields);
}

export function gasDataTopic(value: unknown, binding: GasM2Binding, selection: GasTopicSelection): value is GasDataTopic {
  if (!object(value) || !sameBinding(value.binding, binding) || !sameTopic(value.selection, selection)
    || value.rule_version !== "gas-m2-workbench/1" || value.authorized_coordinates !== false
    || !count(value.registered_assets) || !count(value.registered_events) || !metric(value.field_completeness)
    || !Array.isArray(value.subnets) || !value.subnets.every(row => object(row) && string(row.subnet_id) && string(row.name) && count(row.registered_assets))
    || !Array.isArray(value.assets) || !value.assets.every(topicAsset) || !strings(value.events)
    || !sha(value.evidence_sha256) || !strings(value.evidence_refs)
    || !(value.administrative_source_uri === null || (string(value.administrative_source_uri) && value.administrative_source_uri.startsWith("https://")))) return false;
  if (selection.scope === "chongqing") return value.source_status === "administrative_reference_only"
    && value.level === (selection.district_id ? "district" : "city") && value.registered_assets === 0 && value.registered_events === 0
    && value.subnets.length === 0 && value.assets.length === 0 && value.events.length === 0 && value.field_completeness.ratio === null;
  return value.source_status === "synthetic_fixture" && (!selection.asset_id
    ? value.level === (selection.subnet_id ? "subnet" : "demo_catalog")
    : value.level === "asset" && topicAsset(value.selected_asset) && object(value.selected_asset) && value.selected_asset.asset_id === selection.asset_id);
}

export function gasQualityWorkbench(value: unknown, binding: GasM2Binding): value is GasQualityWorkbench {
  return object(value) && sameBinding(value.binding, binding) && value.rule_version === "gas-m2-workbench/1"
    && value.data_kind === "internal_synthetic" && value.health_score === "unavailable"
    && Array.isArray(value.metrics) && value.metrics.length === 4 && value.metrics.every(metric)
    && object(value.observation_quality) && Object.values(value.observation_quality).every(count)
    && strings(value.incomplete_mapping_fields) && strings(value.unsupported_mappings)
    && string(value.indicator_version) && file(value.indicator_source)
    && Array.isArray(value.preparation) && value.preparation.length === 15
    && value.preparation.every((row, index) => object(row) && row.code === "G" + String(index + 1).padStart(2, "0")
      && string(row.name) && typeof row.required === "boolean" && strings(row.existing_fields)
      && strings(row.missing_fields) && strings(row.synthetic_possible) && strings(row.authorization_needed))
    && Array.isArray(value.sources) && value.sources.every(file)
    && sha(value.evidence_sha256) && strings(value.evidence_refs);
}

export function gasTopicAnswer(value: unknown, request: GasM2TopicQuestion): value is GasM2TopicAnswer {
  return object(value) && value.tool_name === (request.tool === "navigation" ? "data_m2.topic" : "data_m2.quality_workbench")
    && sameBinding(value.binding, request.binding)
    && (request.selection ? sameTopic(value.selection, request.selection) : value.selection === null)
    && value.verified_sha256 === request.expected_sha256 && groundedAnswer(value);
}

function sameStress(value: unknown, expected: GasSourceStressRequest) {
  return object(value) && sameBinding(value.binding, expected.binding) && value.source_id === expected.source_id
    && sameReference(value.scenario, expected.scenario);
}

export function gasSourceStress(value: unknown, expected: GasSourceStressRequest): value is GasSourceStressResult {
  if (!object(value) || !sameStress(value.selection, expected) || value.rule_version !== "gas-drill-source-withdrawal/1"
    || !graphState(value.before) || !graphState(value.after)
    || !object(value.source) || !sha(value.source.network_content_sha256) || !Array.isArray(value.source.files)
    || !value.source.files.every(file) || !object(value.enabled_source_nodes) || !Object.values(value.enabled_source_nodes).every(string)
    || !strings(value.remaining_source_ids) || value.remaining_source_ids.includes(expected.source_id)
    || !strings(value.newly_unreachable_node_ids) || !strings(value.excluded_edge_ids) || !object(value.single_source_dependencies)
    || !Object.values(value.single_source_dependencies).every(strings)
    || !strings(value.alternate_source_path_edge_ids) || !strings(value.scenario_alternative_path_edge_ids)
    || !(value.alternate_source_id === null || (string(value.alternate_source_id) && value.remaining_source_ids.includes(value.alternate_source_id)))
    || !Array.isArray(value.audit) || !value.audit.every(row => object(row) && count(row.revision)
      && ["actor_id","occurred_at","action","current_status"].every(key => string(row[key])))
    || !string(value.limitation) || !string(value.audit_boundary) || !sha(value.stress_sha256) || !strings(value.evidence_refs)) return false;
  const remainder = new Set(value.after.reachable_node_ids);
  const lost = value.before.reachable_node_ids.filter(id => !remainder.has(id));
  return lost.length === value.newly_unreachable_node_ids.length && lost.every(id => (value.newly_unreachable_node_ids as string[]).includes(id));
}

export function gasStressAnswer(value: unknown, request: GasSourceStressQuestion): value is GasSourceStressAnswer {
  return object(value) && value.tool_name === "synthetic_scenario.source_stress"
    && sameBinding(value.binding, request.selection.binding) && sameStress(value.selection, request.selection)
    && value.verified_sha256 === request.expected_sha256 && groundedAnswer(value);
}
