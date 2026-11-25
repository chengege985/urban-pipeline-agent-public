import { gasObservationRef } from "../../shared/routing/gasRoutes";
import type { GasMonitoringObservation, GasMonitoringReport, GasObservationRef } from "../../shared/contracts/platform";

const stateValues = ["normal", "observing", "limit_exceeded", "data_missing"] as const;
const eventCauses = {
  limit_exceeded: ["above_upper", "below_lower"],
  data_missing: ["no_usable_observation"],
  recovered: ["within_limits", "observations_resumed"],
} as const;
const allowedUnits = { pressure: ["Pa", "kPa", "MPa"], flow: ["m3/h", "Nm3/h"] } as const;

const record = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const sha256 = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const timestamp = (value: unknown): value is string => typeof value === "string"
  && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)$/.test(value)
  && Number.isFinite(Date.parse(value));
const positive = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value > 0;
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const nullable = (value: unknown, validate: (item: unknown) => boolean) => value == null || validate(value);
const oneOf = (value: unknown, options: readonly string[]) => typeof value === "string" && options.includes(value);

type SourceRecord = Record<string, unknown>;

export function gasMonitoringSource(value: unknown): value is SourceRecord {
  return record(value) && text(value.source_id) && text(value.source_version) && sha256(value.source_sha256);
}

function row(value: unknown): value is SourceRecord {
  return gasMonitoringSource(value) && text(value.source_record_id) && positive(value.source_row);
}

function observation(value: unknown, units: readonly string[], monitoringSource: unknown) {
  return row(value) && timestamp(value.observed_at) && finite(value.value)
    && oneOf(value.unit, units) && oneOf(value.quality, ["valid", "suspect"])
    && gasMonitoringSource(monitoringSource)
    && value.source_id === monitoringSource.source_id
    && value.source_version === monitoringSource.source_version
    && value.source_sha256 === monitoringSource.source_sha256;
}

export function gasMonitoringEvent(value: unknown, metric: keyof typeof allowedUnits, unit: string, monitoringSource: unknown) {
  if (!record(value)) return false;
  const kind = value.kind;
  if (kind !== "limit_exceeded" && kind !== "data_missing" && kind !== "recovered") return false;
  const evidenceUnits = kind === "data_missing" ? allowedUnits[metric] : [unit];
  return oneOf(value.cause, eventCauses[kind]) && oneOf(value.previous_state, stateValues)
    && timestamp(value.at) && Array.isArray(value.evidence)
    && value.evidence.every((item) => observation(item, evidenceUnits, monitoringSource))
    && (kind === "data_missing" || value.evidence.length > 0)
    && Array.isArray(value.missing_slots) && value.missing_slots.every(timestamp)
    && (kind === "data_missing" ? value.missing_slots.length > 0 : value.missing_slots.length === 0);
}

function channel(value: unknown, monitoringSource: unknown) {
  if (!record(value)) return false;
  const { metric, unit } = value;
  return text(value.asset_id) && text(value.sensor_id) && (metric === "pressure" || metric === "flow")
    && typeof unit === "string" && oneOf(unit, allowedUnits[metric])
    && oneOf(value.state, stateValues) && row(value.asset_evidence) && nullable(value.last_usable_at, timestamp)
    && (value.lower === null || finite(value.lower)) && (value.upper === null || finite(value.upper))
    && (value.lower !== null || value.upper !== null)
    && (!finite(value.lower) || !finite(value.upper) || value.lower < value.upper)
    && typeof value.off_schedule_observations === "number"
    && Number.isSafeInteger(value.off_schedule_observations) && value.off_schedule_observations >= 0
    && Array.isArray(value.events) && value.events.every((item) => gasMonitoringEvent(item, metric, unit, monitoringSource));
}

export function sameMonitoringReference(left: GasObservationRef, right: GasObservationRef) {
  return left.observation_id === right.observation_id && left.manifest_sha256 === right.manifest_sha256;
}

export function gasMonitoringObservation(value: unknown, ref?: GasObservationRef): value is GasMonitoringObservation {
  if (!record(value) || !gasObservationRef(value.reference) || (ref && !sameMonitoringReference(value.reference, ref))
    || value.data_mode !== "synthetic" || value.validation_status !== "not_business_validated"
    || value.state !== "limit_exceeded" || !oneOf(value.previous_state, stateValues)
    || !text(value.dataset_id) || !text(value.dataset_version) || !text(value.asset_id) || !row(value.asset_evidence)
    || !text(value.sensor_id) || !text(value.policy_id) || !positive(value.sustained_samples)
    || !timestamp(value.triggered_at) || !row(value.observation)
    || value.observation.source_record_id !== value.reference.observation_id
    || !timestamp(value.observation.observed_at) || Date.parse(value.observation.observed_at) !== Date.parse(value.triggered_at)
    || value.observation.quality !== "valid" || !finite(value.observation.value)
    || (value.metric !== "pressure" && value.metric !== "flow") || !oneOf(value.observation.unit, allowedUnits[value.metric])
    || (value.upper !== null && !finite(value.upper)) || (value.lower !== null && !finite(value.lower))) return false;
  const direction = value.direction === "above_upper" && finite(value.upper) && value.observation.value > value.upper
    || value.direction === "below_lower" && finite(value.lower) && value.observation.value < value.lower;
  const source = value.observation;
  if (!direction || !Array.isArray(value.sustaining_observations)
    || value.sustaining_observations.length !== value.sustained_samples
    || !value.sustaining_observations.every((entry) => observation(entry, [source.unit as string], source))) return false;
  if (value.event === null) return value.scenario_id === null;
  return text(value.scenario_id) && record(value.event) && text(value.event.event_id) && text(value.event.title)
    && Array.isArray(value.event.asset_ids) && value.event.asset_ids.includes(value.asset_id)
    && Array.isArray(value.event.evidence_refs) && value.event.evidence_refs.every(text)
    && sha256(value.event.source_sha256) && timestamp(value.event.replay_at)
    && Date.parse(value.event.replay_at) === Date.parse(value.triggered_at);
}

export function gasMonitoringResponse(value: unknown): value is GasMonitoringReport {
  return record(value) && value.data_mode === "synthetic" && value.validation_status === "not_business_validated"
    && text(value.dataset_id) && text(value.dataset_version) && sha256(value.manifest_sha256)
    && text(value.policy_id) && timestamp(value.window_start) && timestamp(value.evaluated_at)
    && finite(value.cadence_seconds) && value.cadence_seconds > 0 && positive(value.sustained_samples) && positive(value.missing_samples)
    && nullable(value.monitoring_source, gasMonitoringSource) && Array.isArray(value.channels)
    && value.channels.every((item) => channel(item, value.monitoring_source));
}
