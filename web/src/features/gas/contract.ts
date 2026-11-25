import type {
  GasCaseResponse as OpenApiGasCaseResponse,
  GasCounts as OpenApiGasCounts,
  GasScoringContract as OpenApiGasScoringContract,
} from "../../shared/contracts/platform";
import type { GasCaseResponse } from "./types";
import { GasApiError, type GasContractIssue } from "./errors";

type JsonRecord = Record<string, unknown>;

export interface GasContractContext {
  requestId: string;
  status: number;
  path: string;
}

const MAX_ISSUES = 32;

// Compile-time coupling: every required top-level key must exist in the generated
// 85-schema type. Runtime checks below do not rely on TypeScript existing at runtime.
const TOP_LEVEL_KEYS = [
  "schema_version", "case_id", "network_id", "scenario_id", "plan_id",
  "data_mode", "status", "model_status", "validation_status", "semantic",
  "algorithm_version", "analysis_window_hours", "evidence", "counts", "nodes",
  "edges", "pipes", "valves", "sources", "consumers", "services", "scenario",
  "isolation_plan", "timeline_events", "current", "metrics", "scoring", "safety",
] as const satisfies readonly (keyof OpenApiGasCaseResponse)[];

const COUNT_KEYS = [
  "nodes", "edges", "pipes", "valves", "sources", "consumers", "services",
] as const satisfies readonly (keyof OpenApiGasCounts)[];

const SCORING_KEYS = [
  "status", "rule_version", "business_validation", "required_indicators",
  "available_indicators", "formula_status", "h_status", "p_semantics",
  "coverage_semantics", "note",
] as const satisfies readonly (keyof OpenApiGasScoringContract)[];

const TOP_LEVEL_ALLOWED = new Set<string>(TOP_LEVEL_KEYS);
const NODE_ALLOWED = new Set(["node_id", "prototype_id", "node_type", "name", "layout"]);
const LAYOUT_ALLOWED = new Set(["x", "y", "unit", "coordinate_system"]);
const EDGE_ALLOWED = new Set(["edge_id", "pipe_id", "prototype_id", "from_node_id", "to_node_id", "direction", "initial_state"]);
const PIPE_ALLOWED = new Set(["pipe_id", "edge_id", "prototype_id", "material", "diameter_mm", "pressure_class", "asset_status"]);
const VALVE_ALLOWED = new Set(["valve_id", "node_id", "prototype_id", "cut_semantics", "initial_state", "incident_edge_ids"]);
const SOURCE_ALLOWED = new Set(["source_id", "node_id", "prototype_id", "name", "enabled", "pressure", "capacity", "physical_model_status"]);
const CONSUMER_ALLOWED = new Set(["consumer_id", "prototype_node_id", "node_id", "consumer_kind", "count", "count_unit", "facility_type", "origin", "individual_customer_records"]);
const SERVICE_ALLOWED = new Set(["service_id", "consumer_id", "node_ids", "supply_rule", "status", "physical_supply_status", "previously_graph_reachable", "currently_graph_reachable"]);
const SCENARIO_ALLOWED = new Set(["scenario_id", "name", "fault_edge_ids", "origin", "fault_assumption"]);
const ISOLATION_ALLOWED = new Set(["plan_id", "scenario_id", "closed_valve_ids", "disabled_edge_ids", "semantics", "derivation"]);
const STATE_ALLOWED = new Set([
  "event_id", "time_hours", "kind", "current_reachable_node_ids", "current_unreachable_node_ids",
  "normal_unreachable_node_ids", "newly_unreachable_node_ids", "services",
  "newly_affected_service_ids", "potentially_interrupted_consumers", "model_scope",
  "physical_supply_verified",
]);
const CONSUMER_COUNTS_ALLOWED = new Set(["household", "business", "facility"]);
const METRIC_ALLOWED = new Set(["status", "value", "unit", "reason", "in_analysis_window"]);
const SAFETY_ALLOWED = new Set(["field_operation_instruction", "safe_restoration_decision", "physical_supply_verified", "simulation_only"]);

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function has(record: JsonRecord, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function actualType(value: unknown): string {
  if (value === undefined) return "missing";
  if (value === null) return "null";
  if (Array.isArray(value)) return `array(${value.length})`;
  return typeof value;
}

function addIssue(issues: GasContractIssue[], path: string, expected: string, actual: unknown): void {
  if (issues.length < MAX_ISSUES) issues.push({ path, expected, actual: actualType(actual) });
}

function rejectUnknown(record: JsonRecord, allowed: Set<string>, path: string, issues: GasContractIssue[]): void {
  for (const key of Object.keys(record)) {
    if (!allowed.has(key)) addIssue(issues, `${path}.${key}`, "not present in OpenAPI schema", key);
  }
}

function requireRecord(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): JsonRecord | null {
  if (!has(record, key)) {
    addIssue(issues, path, "object", undefined);
    return null;
  }
  const value = record[key];
  if (!isRecord(value)) {
    addIssue(issues, path, "object", value);
    return null;
  }
  return value;
}

function requireArray(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): unknown[] | null {
  if (!has(record, key)) {
    addIssue(issues, path, "array", undefined);
    return null;
  }
  const value = record[key];
  if (!Array.isArray(value)) {
    addIssue(issues, path, "array", value);
    return null;
  }
  return value;
}

function requireString(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): string | null {
  if (!has(record, key)) {
    addIssue(issues, path, "string", undefined);
    return null;
  }
  const value = record[key];
  if (typeof value !== "string") {
    addIssue(issues, path, "string", value);
    return null;
  }
  return value;
}

function optionalStringOrNull(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): void {
  if (!has(record, key)) return;
  const value = record[key];
  if (value !== null && typeof value !== "string") addIssue(issues, path, "string | null", value);
}

function optionalNumberOrNull(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): void {
  if (!has(record, key)) return;
  const value = record[key];
  if (value !== null && (typeof value !== "number" || !Number.isFinite(value))) {
    addIssue(issues, path, "finite number | null", value);
  }
}

function optionalBooleanOrNull(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): void {
  if (!has(record, key)) return;
  const value = record[key];
  if (value !== null && typeof value !== "boolean") addIssue(issues, path, "boolean | null", value);
}

function requireExact(record: JsonRecord, key: string, expected: string, path: string, issues: GasContractIssue[]): void {
  const value = requireString(record, key, path, issues);
  if (value !== null && value !== expected) addIssue(issues, path, `string("${expected}")`, `string("${value}")`);
}

function requireOneOf(record: JsonRecord, key: string, allowed: readonly string[], path: string, issues: GasContractIssue[]): void {
  const value = requireString(record, key, path, issues);
  if (value !== null && !allowed.includes(value)) {
    addIssue(issues, path, allowed.map((item) => `"${item}"`).join(" | "), `string("${value}")`);
  }
}

function requireNumber(record: JsonRecord, key: string, path: string, issues: GasContractIssue[], options: { integer?: boolean; min?: number } = {}): void {
  if (!has(record, key)) {
    addIssue(issues, path, options.integer ? "integer" : "number", undefined);
    return;
  }
  const value = record[key];
  if (typeof value !== "number" || !Number.isFinite(value) || (options.integer && !Number.isInteger(value))
      || (options.min !== undefined && value < options.min)) {
    addIssue(issues, path, `${options.integer ? "integer" : "finite number"}${options.min !== undefined ? ` >= ${options.min}` : ""}`, value);
  }
}

function requireBoolean(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): void {
  if (!has(record, key)) {
    addIssue(issues, path, "boolean", undefined);
    return;
  }
  if (typeof record[key] !== "boolean") addIssue(issues, path, "boolean", record[key]);
}

function requireBooleanExact(record: JsonRecord, key: string, expected: boolean, path: string, issues: GasContractIssue[]): void {
  requireBoolean(record, key, path, issues);
  if (has(record, key) && record[key] !== expected) addIssue(issues, path, `boolean(${expected})`, record[key]);
}

function requireStringArray(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): string[] | null {
  const value = requireArray(record, key, path, issues);
  if (!value) return null;
  value.forEach((item, index) => {
    if (typeof item !== "string") addIssue(issues, `${path}[${index}]`, "string", item);
  });
  return value as string[];
}

function requireRecordArray(record: JsonRecord, key: string, path: string, issues: GasContractIssue[]): JsonRecord[] | null {
  const value = requireArray(record, key, path, issues);
  if (!value) return null;
  const rows: JsonRecord[] = [];
  value.forEach((item, index) => {
    if (!isRecord(item)) addIssue(issues, `${path}[${index}]`, "object", item);
    else rows.push(item);
  });
  return rows;
}

function validateLayout(node: JsonRecord, path: string, issues: GasContractIssue[]): void {
  const layout = requireRecord(node, "layout", `${path}.layout`, issues);
  if (!layout) return;
  rejectUnknown(layout, LAYOUT_ALLOWED, `${path}.layout`, issues);
  requireNumber(layout, "x", `${path}.layout.x`, issues);
  requireNumber(layout, "y", `${path}.layout.y`, issues);
  requireExact(layout, "unit", "px", `${path}.layout.unit`, issues);
  requireExact(layout, "coordinate_system", "schematic", `${path}.layout.coordinate_system`, issues);
}

function validateNode(node: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(node, NODE_ALLOWED, path, issues);
  requireString(node, "node_id", `${path}.node_id`, issues);
  requireString(node, "prototype_id", `${path}.prototype_id`, issues);
  requireOneOf(node, "node_type", ["junction", "regulator", "valve", "source"], `${path}.node_type`, issues);
  optionalStringOrNull(node, "name", `${path}.name`, issues);
  validateLayout(node, path, issues);
}

function validatePipe(pipe: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(pipe, PIPE_ALLOWED, path, issues);
  for (const key of ["pipe_id", "edge_id", "prototype_id", "material", "pressure_class", "asset_status"]) {
    requireString(pipe, key, `${path}.${key}`, issues);
  }
  requireNumber(pipe, "diameter_mm", `${path}.diameter_mm`, issues, { min: 0 });
}

function validateEdge(edge: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(edge, EDGE_ALLOWED, path, issues);
  for (const key of ["edge_id", "pipe_id", "prototype_id", "from_node_id", "to_node_id"]) {
    requireString(edge, key, `${path}.${key}`, issues);
  }
  requireExact(edge, "direction", "undirected_connectivity_only", `${path}.direction`, issues);
  requireOneOf(edge, "initial_state", ["enabled", "disabled"], `${path}.initial_state`, issues);
}

function validateValve(valve: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(valve, VALVE_ALLOWED, path, issues);
  for (const key of ["valve_id", "node_id", "prototype_id"]) requireString(valve, key, `${path}.${key}`, issues);
  requireExact(valve, "cut_semantics", "node_cut", `${path}.cut_semantics`, issues);
  requireOneOf(valve, "initial_state", ["open", "closed"], `${path}.initial_state`, issues);
  requireStringArray(valve, "incident_edge_ids", `${path}.incident_edge_ids`, issues);
}

function validateSource(source: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(source, SOURCE_ALLOWED, path, issues);
  for (const key of ["source_id", "node_id", "prototype_id", "name"]) requireString(source, key, `${path}.${key}`, issues);
  requireBoolean(source, "enabled", `${path}.enabled`, issues);
  optionalNumberOrNull(source, "pressure", `${path}.pressure`, issues);
  optionalNumberOrNull(source, "capacity", `${path}.capacity`, issues);
  requireExact(source, "physical_model_status", "unsupported", `${path}.physical_model_status`, issues);
}

function validateConsumer(consumer: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(consumer, CONSUMER_ALLOWED, path, issues);
  for (const key of ["consumer_id", "prototype_node_id", "node_id", "origin"]) requireString(consumer, key, `${path}.${key}`, issues);
  requireOneOf(consumer, "consumer_kind", ["household", "business", "facility"], `${path}.consumer_kind`, issues);
  requireNumber(consumer, "count", `${path}.count`, issues, { integer: true, min: 0 });
  requireOneOf(consumer, "count_unit", ["household", "business", "facility"], `${path}.count_unit`, issues);
  optionalStringOrNull(consumer, "facility_type", `${path}.facility_type`, issues);
  requireBoolean(consumer, "individual_customer_records", `${path}.individual_customer_records`, issues);
}

function validateService(service: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(service, SERVICE_ALLOWED, path, issues);
  for (const key of ["service_id", "consumer_id"]) requireString(service, key, `${path}.${key}`, issues);
  requireStringArray(service, "node_ids", `${path}.node_ids`, issues);
  requireOneOf(service, "supply_rule", ["any", "all"], `${path}.supply_rule`, issues);
  requireOneOf(service, "status", ["graph_reachable", "newly_graph_unreachable", "already_graph_unreachable"], `${path}.status`, issues);
  requireExact(service, "physical_supply_status", "unknown", `${path}.physical_supply_status`, issues);
  requireBoolean(service, "previously_graph_reachable", `${path}.previously_graph_reachable`, issues);
  requireBoolean(service, "currently_graph_reachable", `${path}.currently_graph_reachable`, issues);
}

function validateMetric(metric: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(metric, METRIC_ALLOWED, path, issues);
  requireOneOf(metric, "status", ["available", "partial", "insufficient_data", "not_applicable", "unsupported"], `${path}.status`, issues);
  optionalNumberOrNull(metric, "value", `${path}.value`, issues);
  optionalStringOrNull(metric, "unit", `${path}.unit`, issues);
  optionalStringOrNull(metric, "reason", `${path}.reason`, issues);
  optionalBooleanOrNull(metric, "in_analysis_window", `${path}.in_analysis_window`, issues);
}

function validateScoring(scoring: JsonRecord, issues: GasContractIssue[]): void {
  rejectUnknown(scoring, new Set<string>(SCORING_KEYS), "scoring", issues);
  for (const key of SCORING_KEYS) if (!has(scoring, key)) addIssue(issues, `scoring.${key}`, "required", undefined);
  if (has(scoring, "status")) requireOneOf(scoring, "status", ["candidate", "unavailable"], "scoring.status", issues);
  if (has(scoring, "rule_version")) requireString(scoring, "rule_version", "scoring.rule_version", issues);
  if (has(scoring, "business_validation")) requireExact(scoring, "business_validation", "not_business_validated", "scoring.business_validation", issues);
  if (has(scoring, "required_indicators")) requireStringArray(scoring, "required_indicators", "scoring.required_indicators", issues);
  if (has(scoring, "available_indicators")) requireStringArray(scoring, "available_indicators", "scoring.available_indicators", issues);
  if (has(scoring, "formula_status")) requireOneOf(scoring, "formula_status", ["candidate_arithmetic_only", "not_available"], "scoring.formula_status", issues);
  if (has(scoring, "h_status")) requireExact(scoring, "h_status", "unsupported_pending_business_definition", "scoring.h_status", issues);
  if (has(scoring, "p_semantics")) requireString(scoring, "p_semantics", "scoring.p_semantics", issues);
  if (has(scoring, "coverage_semantics")) requireString(scoring, "coverage_semantics", "scoring.coverage_semantics", issues);
  if (has(scoring, "note")) requireString(scoring, "note", "scoring.note", issues);
}

function validateIsolationPlan(plan: JsonRecord, issues: GasContractIssue[]): void {
  rejectUnknown(plan, ISOLATION_ALLOWED, "isolation_plan", issues);
  for (const key of ["plan_id", "scenario_id"]) requireString(plan, key, `isolation_plan.${key}`, issues);
  requireStringArray(plan, "closed_valve_ids", "isolation_plan.closed_valve_ids", issues);
  requireStringArray(plan, "disabled_edge_ids", "isolation_plan.disabled_edge_ids", issues);
  requireExact(plan, "semantics", "node_cut_plus_explicit_unit_edges", "isolation_plan.semantics", issues);
  requireString(plan, "derivation", "isolation_plan.derivation", issues);
}

function validateConsumerCounts(value: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(value, CONSUMER_COUNTS_ALLOWED, path, issues);
  for (const key of ["household", "business", "facility"]) requireNumber(value, key, `${path}.${key}`, issues, { integer: true, min: 0 });
}

function validateState(state: JsonRecord, path: string, issues: GasContractIssue[]): void {
  rejectUnknown(state, STATE_ALLOWED, path, issues);
  requireString(state, "event_id", `${path}.event_id`, issues);
  requireNumber(state, "time_hours", `${path}.time_hours`, issues, { min: 0 });
  requireOneOf(state, "kind", ["given_fault", "given_isolation", "synthetic_observation"], `${path}.kind`, issues);
  for (const key of ["current_reachable_node_ids", "current_unreachable_node_ids", "normal_unreachable_node_ids", "newly_unreachable_node_ids", "newly_affected_service_ids"]) {
    requireStringArray(state, key, `${path}.${key}`, issues);
  }
  const services = requireRecordArray(state, "services", `${path}.services`, issues);
  services?.forEach((service, index) => validateService(service, `${path}.services[${index}]`, issues));
  const counts = requireRecord(state, "potentially_interrupted_consumers", `${path}.potentially_interrupted_consumers`, issues);
  if (counts) validateConsumerCounts(counts, `${path}.potentially_interrupted_consumers`, issues);
  requireExact(state, "model_scope", "graph_connectivity_only", `${path}.model_scope`, issues);
  requireBooleanExact(state, "physical_supply_verified", false, `${path}.physical_supply_verified`, issues);
}

function validateSafety(safety: JsonRecord, issues: GasContractIssue[]): void {
  rejectUnknown(safety, SAFETY_ALLOWED, "safety", issues);
  requireBooleanExact(safety, "field_operation_instruction", false, "safety.field_operation_instruction", issues);
  requireBooleanExact(safety, "safe_restoration_decision", false, "safety.safe_restoration_decision", issues);
  requireBooleanExact(safety, "physical_supply_verified", false, "safety.physical_supply_verified", issues);
  requireBooleanExact(safety, "simulation_only", true, "safety.simulation_only", issues);
}

function uniqueAndRefs(raw: JsonRecord, issues: GasContractIssue[], nodes: Set<string>, edges: Set<string>, pipes: Set<string>, valves: Set<string>, consumers: Set<string>, services: Set<string>): void {
  // Explicit ID lists are clearer than a generic singularizer for the generated schema.
  const idList = (key: string, field: string): string[] => {
    const rows = raw[key];
    if (!Array.isArray(rows)) return [];
    const result: string[] = [];
    rows.forEach((row, index) => {
      if (!isRecord(row) || typeof row[field] !== "string") return;
      result.push(row[field]);
      void index;
    });
    return result;
  };
  const checkUnique = (label: string, values: string[], path: string) => {
    if (new Set(values).size !== values.length) addIssue(issues, path, "unique ids", `${values.length - new Set(values).size} duplicate`);
  };
  checkUnique("nodes", idList("nodes", "node_id"), "nodes");
  checkUnique("edges", idList("edges", "edge_id"), "edges");
  checkUnique("pipes", idList("pipes", "pipe_id"), "pipes");
  checkUnique("valves", idList("valves", "valve_id"), "valves");
  checkUnique("consumers", idList("consumers", "consumer_id"), "consumers");
  checkUnique("services", idList("services", "service_id"), "services");

  const requireRef = (value: unknown, set: Set<string>, path: string) => {
    if (typeof value === "string" && !set.has(value)) addIssue(issues, path, "existing id", value);
  };
  const requireRefs = (values: unknown, set: Set<string>, path: string) => {
    if (!Array.isArray(values)) return;
    values.forEach((value, index) => requireRef(value, set, `${path}[${index}]`));
  };
  nodes.clear(); edges.clear(); pipes.clear(); valves.clear(); consumers.clear(); services.clear();
  for (const id of idList("nodes", "node_id")) nodes.add(id);
  for (const id of idList("edges", "edge_id")) edges.add(id);
  for (const id of idList("pipes", "pipe_id")) pipes.add(id);
  for (const id of idList("valves", "valve_id")) valves.add(id);
  for (const id of idList("consumers", "consumer_id")) consumers.add(id);
  for (const id of idList("services", "service_id")) services.add(id);

  const rows = (key: string): JsonRecord[] => Array.isArray(raw[key]) ? raw[key] as JsonRecord[] : [];
  const edgeRows = rows("edges");
  const edgeById = new Map(edgeRows.filter((edge) => typeof edge.edge_id === "string").map((edge) => [edge.edge_id as string, edge]));
  const pipeRows = rows("pipes");
  for (const [index, edge] of edgeRows.entries()) {
    requireRef(edge.from_node_id, nodes, `edges[${index}].from_node_id`);
    requireRef(edge.to_node_id, nodes, `edges[${index}].to_node_id`);
    requireRef(edge.pipe_id, pipes, `edges[${index}].pipe_id`);
    const pipe = pipeRows.find((row) => row.pipe_id === edge.pipe_id);
    if (pipe && pipe.edge_id !== edge.edge_id) addIssue(issues, `edges[${index}].pipe_id`, `pipe ${String(edge.pipe_id)} edge_id=${String(edge.edge_id)}`, pipe.edge_id);
  }
  for (const [index, pipe] of pipeRows.entries()) {
    requireRef(pipe.edge_id, edges, `pipes[${index}].edge_id`);
    if (edges.has(String(pipe.edge_id)) && !edgeRows.some((row) => row.edge_id === pipe.edge_id && row.pipe_id === pipe.pipe_id)) {
      addIssue(issues, `pipes[${index}].pipe_id`, `edge ${String(pipe.edge_id)} pipe_id=${String(pipe.pipe_id)}`, pipe.pipe_id);
    }
  }
  for (const [index, valve] of rows("valves").entries()) {
    requireRef(valve.node_id, nodes, `valves[${index}].node_id`);
    requireRefs(valve.incident_edge_ids, edges, `valves[${index}].incident_edge_ids`);
    if (Array.isArray(valve.incident_edge_ids)) {
      valve.incident_edge_ids.forEach((edgeId, edgeIndex) => {
        const edge = edgeById.get(String(edgeId));
        if (edge && edge.from_node_id !== valve.node_id && edge.to_node_id !== valve.node_id) {
          addIssue(issues, `valves[${index}].incident_edge_ids[${edgeIndex}]`, `edge touching ${String(valve.node_id)}`, edgeId);
        }
      });
    }
  }
  for (const [index, source] of rows("sources").entries()) requireRef(source.node_id, nodes, `sources[${index}].node_id`);
  for (const [index, consumer] of rows("consumers").entries()) requireRef(consumer.node_id, nodes, `consumers[${index}].node_id`);
  for (const [index, service] of rows("services").entries()) {
    requireRef(service.consumer_id, consumers, `services[${index}].consumer_id`);
    requireRefs(service.node_ids, nodes, `services[${index}].node_ids`);
  }
  const scenario = isRecord(raw.scenario) ? raw.scenario : {};
  requireRefs(scenario.fault_edge_ids, edges, "scenario.fault_edge_ids");
  const plan = isRecord(raw.isolation_plan) ? raw.isolation_plan : {};
  requireRefs(plan.closed_valve_ids, valves, "isolation_plan.closed_valve_ids");
  requireRefs(plan.disabled_edge_ids, edges, "isolation_plan.disabled_edge_ids");
  if (plan.plan_id !== raw.plan_id) addIssue(issues, "isolation_plan.plan_id", `same as plan_id ${String(raw.plan_id)}`, plan.plan_id);
  const scenarioIdentity = isRecord(raw.scenario) ? raw.scenario : {};
  if (scenarioIdentity.scenario_id !== raw.scenario_id) addIssue(issues, "scenario.scenario_id", `same as scenario_id ${String(raw.scenario_id)}`, scenario.scenario_id);
  for (const [index, state] of rows("timeline_events").entries()) {
    for (const key of ["current_reachable_node_ids", "current_unreachable_node_ids", "normal_unreachable_node_ids", "newly_unreachable_node_ids"]) {
      requireRefs(state[key], nodes, `timeline_events[${index}].${key}`);
    }
    requireRefs(state.newly_affected_service_ids, services, `timeline_events[${index}].newly_affected_service_ids`);
    if (Array.isArray(state.services)) {
      state.services.forEach((service, serviceIndex) => {
        if (isRecord(service)) requireRef(service.service_id, services, `timeline_events[${index}].services[${serviceIndex}].service_id`);
      });
    }
  }
  const current = isRecord(raw.current) ? raw.current : {};
  for (const key of ["current_reachable_node_ids", "current_unreachable_node_ids", "normal_unreachable_node_ids", "newly_unreachable_node_ids"]) {
    requireRefs(current[key], nodes, `current.${key}`);
  }
  requireRefs(current.newly_affected_service_ids, services, "current.newly_affected_service_ids");
  if (Array.isArray(current.services)) {
    current.services.forEach((service, index) => {
      if (isRecord(service)) requireRef(service.service_id, services, `current.services[${index}].service_id`);
    });
  }
}

function contractFailure(issues: GasContractIssue[], context: GasContractContext): GasApiError {
  return new GasApiError({
    kind: "contract_error",
    code: "contract_error",
    message: `燃气接口返回结构与 OpenAPI 合同不一致（${issues.length} 项）。`,
    status: context.status,
    requestId: context.requestId,
    path: context.path,
    issues,
  });
}

export function parseGasCaseResponse(raw: unknown, context: GasContractContext): GasCaseResponse {
  if (!isRecord(raw)) throw contractFailure([{ path: "$", expected: "object", actual: actualType(raw) }], context);

  const issues: GasContractIssue[] = [];
  rejectUnknown(raw, TOP_LEVEL_ALLOWED, "$", issues);
  for (const key of TOP_LEVEL_KEYS) if (!has(raw, key)) addIssue(issues, key, "required", undefined);

  if (has(raw, "schema_version")) requireExact(raw, "schema_version", "gas-case-view-0.1.0", "schema_version", issues);
  for (const key of ["case_id", "network_id", "scenario_id", "plan_id", "algorithm_version"] as const) {
    if (has(raw, key)) requireString(raw, key, key, issues);
  }
  if (has(raw, "data_mode")) requireExact(raw, "data_mode", "synthetic", "data_mode", issues);
  if (has(raw, "status")) requireExact(raw, "status", "partial", "status", issues);
  if (has(raw, "model_status")) requireExact(raw, "model_status", "candidate", "model_status", issues);
  if (has(raw, "validation_status")) requireExact(raw, "validation_status", "not_business_validated", "validation_status", issues);
  if (has(raw, "semantic")) requireExact(raw, "semantic", "graph_connectivity_only", "semantic", issues);
  if (has(raw, "analysis_window_hours")) requireNumber(raw, "analysis_window_hours", "analysis_window_hours", issues, { min: 0 });

  if (has(raw, "evidence")) {
    const evidence = requireRecord(raw, "evidence", "evidence", issues);
    if (evidence) for (const [key, value] of Object.entries(evidence)) {
      if (value !== null && typeof value !== "string") addIssue(issues, `evidence.${key}`, "string | null", value);
    }
  }

  if (has(raw, "counts")) {
    const counts = requireRecord(raw, "counts", "counts", issues);
    if (counts) {
      rejectUnknown(counts, new Set(COUNT_KEYS), "counts", issues);
      for (const key of COUNT_KEYS) requireNumber(counts, key, `counts.${key}`, issues, { integer: true, min: 0 });
    }
  }

  if (has(raw, "nodes")) {
    const nodes = requireRecordArray(raw, "nodes", "nodes", issues);
    nodes?.forEach((node, index) => validateNode(node, `nodes[${index}]`, issues));
  }
  if (has(raw, "edges")) {
    const edges = requireRecordArray(raw, "edges", "edges", issues);
    edges?.forEach((edge, index) => validateEdge(edge, `edges[${index}]`, issues));
  }
  if (has(raw, "pipes")) {
    const pipes = requireRecordArray(raw, "pipes", "pipes", issues);
    pipes?.forEach((pipe, index) => validatePipe(pipe, `pipes[${index}]`, issues));
  }
  if (has(raw, "valves")) {
    const valves = requireRecordArray(raw, "valves", "valves", issues);
    valves?.forEach((valve, index) => validateValve(valve, `valves[${index}]`, issues));
  }
  if (has(raw, "sources")) {
    const sources = requireRecordArray(raw, "sources", "sources", issues);
    sources?.forEach((source, index) => validateSource(source, `sources[${index}]`, issues));
  }
  if (has(raw, "consumers")) {
    const consumers = requireRecordArray(raw, "consumers", "consumers", issues);
    consumers?.forEach((consumer, index) => validateConsumer(consumer, `consumers[${index}]`, issues));
  }
  if (has(raw, "services")) {
    const services = requireRecordArray(raw, "services", "services", issues);
    services?.forEach((service, index) => validateService(service, `services[${index}]`, issues));
  }
  if (has(raw, "scenario")) {
    const scenario = requireRecord(raw, "scenario", "scenario", issues);
    if (scenario) {
      rejectUnknown(scenario, SCENARIO_ALLOWED, "scenario", issues);
      for (const key of ["scenario_id", "name", "origin", "fault_assumption"]) requireString(scenario, key, `scenario.${key}`, issues);
      requireStringArray(scenario, "fault_edge_ids", "scenario.fault_edge_ids", issues);
    }
  }
  if (has(raw, "isolation_plan")) {
    const plan = requireRecord(raw, "isolation_plan", "isolation_plan", issues);
    if (plan) validateIsolationPlan(plan, issues);
  }
  if (has(raw, "timeline_events")) {
    const states = requireRecordArray(raw, "timeline_events", "timeline_events", issues);
    states?.forEach((state, index) => validateState(state, `timeline_events[${index}]`, issues));
  }
  if (has(raw, "current")) {
    const current = requireRecord(raw, "current", "current", issues);
    if (current) validateState(current, "current", issues);
  }
  if (has(raw, "metrics")) {
    const metrics = requireRecord(raw, "metrics", "metrics", issues);
    if (metrics) for (const [key, value] of Object.entries(metrics)) {
      if (!isRecord(value)) addIssue(issues, `metrics.${key}`, "object", value);
      else validateMetric(value, `metrics.${key}`, issues);
    }
  }
  if (has(raw, "scoring")) {
    const scoring = requireRecord(raw, "scoring", "scoring", issues);
    if (scoring) validateScoring(scoring, issues);
  }
  if (has(raw, "safety")) {
    const safety = requireRecord(raw, "safety", "safety", issues);
    if (safety) validateSafety(safety, issues);
  }

  const nodes = new Set<string>(); const edges = new Set<string>(); const pipes = new Set<string>(); const valves = new Set<string>(); const consumers = new Set<string>(); const services = new Set<string>();
  uniqueAndRefs(raw, issues, nodes, edges, pipes, valves, consumers, services);
  if (isRecord(raw.counts)) {
    const counts = raw.counts;
    for (const [key, field] of [["nodes", "nodes"], ["edges", "edges"], ["pipes", "pipes"], ["valves", "valves"], ["sources", "sources"], ["consumers", "consumers"], ["services", "services"]] as const) {
      if (Array.isArray(raw[field]) && counts[key] !== raw[field].length) addIssue(issues, `counts.${key}`, `number(${raw[field].length})`, counts[key]);
    }
  }

  if (issues.length > 0) throw contractFailure(issues, context);
  return raw as unknown as GasCaseResponse;
}
