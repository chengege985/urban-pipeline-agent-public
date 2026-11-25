import type { GasCuvReplayResponse } from "../../shared/contracts/platform";
import { gasMonitoringEvent, gasMonitoringSource } from "./monitoringContract";
import type { GasCaseResponse } from "./types";

export type CuvCaseIdentity = Pick<GasCaseResponse, "case_id" | "network_id" | "evidence">;

const states = ["normal", "observing", "limit_exceeded", "data_missing"];
const constraints = ["met", "blocked", "assumed", "unknown", "not_applicable"];
const phases = ["fault", "isolate", "staged_release", "assumed_clearance"];
const eventKinds = ["given_fault", "given_isolation", "synthetic_observation"];
const units = { pressure: ["Pa", "kPa", "MPa"], flow: ["m3/h", "Nm3/h"] } as const;

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const digest = (value: unknown): boolean => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const oneOf = (value: unknown, allowed: readonly string[]): boolean => typeof value === "string" && allowed.includes(value);
const arrayOf = (value: unknown, valid: (entry: unknown) => boolean): boolean => Array.isArray(value) && value.every(valid);
const ids = (value: unknown): value is string[] => Array.isArray(value) && value.every(text);
const count = (value: unknown): boolean => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const nonnegative = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const time = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value)
  && Number.isFinite(Date.parse(value));
const notLater = (value: unknown, asOf: string): boolean => time(value) && Date.parse(value) <= Date.parse(asOf);
const scenarioOffset = (value: unknown, scenarioAt: string, offset: unknown): boolean =>
  time(value) && nonnegative(offset) && Math.abs(Date.parse(value) - Date.parse(scenarioAt) - offset * 3_600_000) < 1;
const nullable = (value: unknown, validate: (entry: unknown) => boolean): boolean => value === null || validate(value);

export function gasCuvStepAt(value: unknown, scenarioAt: string, horizon: number): boolean {
  if (!object(value) || !oneOf(value.kind, phases) || !scenarioOffset(value.at, scenarioAt, value.elapsed_hours)
    || !nonnegative(value.elapsed_hours) || value.elapsed_hours > horizon || !ids(value.blocked_asset_ids)
    || !arrayOf(value.constraints, (entry) => object(entry)
      && text(entry.code) && oneOf(entry.status, constraints) && text(entry.explanation))) return false;
  if (value.status === "blocked") {
    return value.affected_node_ids === null && value.affected_service_ids === null
      && value.restored_service_ids === null && value.affected_consumers === null;
  }
  const consumers = value.affected_consumers;
  return value.status === "evaluated" && ids(value.affected_node_ids) && ids(value.affected_service_ids)
    && ids(value.restored_service_ids) && object(consumers)
    && count(consumers.household) && count(consumers.business) && count(consumers.facility);
}

function plan(value: unknown, scenarioAt: string, horizon: number): boolean {
  if (!object(value) || !text(value.plan_id) || !text(value.label) || !text(value.description)
    || !Array.isArray(value.steps) || value.steps.length === 0 || !value.steps.every((item) => gasCuvStepAt(item, scenarioAt, horizon)))
    return false;
  if (value.status === "blocked") {
    return value.topology_restoration_hours === null && value.peak_affected_service_count === null && value.affected_service_hours === null;
  }
  return value.status === "replayed" && nullable(value.topology_restoration_hours, (hours) => nonnegative(hours) && hours <= horizon)
    && count(value.peak_affected_service_count) && nonnegative(value.affected_service_hours);
}

function monitoringChannel(value: unknown, asOf: string, source: unknown): boolean {
  if (!object(value) || !text(value.asset_id) || !text(value.sensor_id)
    || (value.metric !== "pressure" && value.metric !== "flow") || typeof value.unit !== "string"
    || !oneOf(value.unit, units[value.metric]) || !oneOf(value.state, states)
    || !oneOf(value.relation, ["fault_asset", "isolation_asset"])
    || !nullable(value.last_usable_at, (at) => notLater(at, asOf))) return false;
  return nullable(value.transition, (transition) => object(transition) && notLater(transition.at, asOf)
    && gasMonitoringEvent(transition, value.metric as keyof typeof units, value.unit as string, source)
    && arrayOf(transition.evidence, (item) => object(item) && notLater(item.observed_at, transition.at as string))
    && arrayOf(transition.missing_slots, (at) => notLater(at, transition.at as string)));
}

export function gasCuvMonitoringAt(value: unknown, scenarioAt: string): boolean {
  if (!object(value) || !scenarioOffset(value.as_of, scenarioAt, 0) || !Array.isArray(value.channels)) return false;
  if (value.status === "unavailable") return value.channels.length === 0;
  return value.status === "available" && text(value.dataset_id) && text(value.dataset_version)
    && digest(value.manifest_sha256) && text(value.policy_id) && gasMonitoringSource(value.source)
    && value.channels.every((channel) => monitoringChannel(channel, scenarioAt, value.source));
}

export function gasCuvReplayResponse(value: unknown): value is GasCuvReplayResponse {
  if (!object(value) || value.data_mode !== "synthetic" || value.semantic !== "graph_connectivity_only"
    || value.validation_status !== "not_business_validated" || value.status !== "partial"
    || value.schema_version !== "gas-cuv-replay-0.1.0" || !text(value.case_id) || !text(value.network_id) || !text(value.scenario_id)
    || !time(value.scenario_at) || !nonnegative(value.analysis_window_hours) || !ids(value.fault_asset_ids)
    || !text(value.algorithm_version) || !text(value.comparison_basis)) return false;
  const evidence = value.topology_evidence;
  if (!object(evidence) || !digest(evidence.network_content_sha256) || !digest(evidence.case_content_sha256)
    || !nullable(evidence.html_source_sha256, digest) || !digest(evidence.replay_content_sha256)) return false;
  const scenarioAt = value.scenario_at, horizon = value.analysis_window_hours;
  return Array.isArray(value.plans) && value.plans.length >= 2 && value.plans.every((item) => plan(item, scenarioAt, horizon))
    && arrayOf(value.unknown_metrics, (entry) => object(entry) && text(entry.code)
      && entry.status === "unknown" && entry.value === null && text(entry.reason))
    && arrayOf(value.missing_items, (entry) => object(entry) && text(entry.code) && text(entry.description))
    && arrayOf(value.event_evidence, (event) => object(event) && text(event.event_id) && text(event.title)
      && oneOf(event.event_kind, eventKinds) && ids(event.asset_ids) && ids(event.evidence_refs)
      && scenarioOffset(event.replay_at, scenarioAt, event.elapsed_hours) && event.source_sha256 === evidence.case_content_sha256)
    && gasCuvMonitoringAt(value.monitoring_evidence, scenarioAt);
}

export function matchesCuvCase(value: GasCuvReplayResponse, current: CuvCaseIdentity | null): boolean {
  return !current || value.case_id === current.case_id && value.network_id === current.network_id
    && value.topology_evidence.network_content_sha256 === current.evidence.network_content_sha256
    && value.topology_evidence.case_content_sha256 === current.evidence.case_content_sha256;
}
