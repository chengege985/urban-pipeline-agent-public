import type { GasSimulationCatalogResponse, GasSimulationResult } from "../../shared/contracts/platform";

type RecordValue = Record<string, unknown>;
const record = (value: unknown): value is RecordValue => typeof value === "object" && value !== null && !Array.isArray(value);
const string = (value: unknown): value is string => typeof value === "string" && value.length > 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(string);
const modes = ["synthetic_topology", "experiment_replay", "historical_incident", "solver_benchmark", "physical_chongqing"] as const;
const statuses = ["succeeded", "partial", "unavailable", "not_found"] as const;

export function gasSimulationCatalog(value: unknown): value is GasSimulationCatalogResponse {
  if (!record(value) || value.status !== "ready" || !string(value.workbench_version) || !Array.isArray(value.capabilities)) return false;
  return value.capabilities.every((item) => record(item)
    && typeof item.mode === "string" && modes.includes(item.mode as (typeof modes)[number])
    && string(item.title) && ["ready", "candidate", "unavailable"].includes(String(item.status))
    && typeof item.deterministic === "boolean" && strings(item.dataset_ids)
    && strings(item.required_inputs) && strings(item.supported_outputs) && strings(item.limitations))
    && strings(value.limitations);
}

export function gasSimulationResult(value: unknown): value is GasSimulationResult {
  if (!record(value) || !string(value.run_id) || typeof value.mode !== "string"
    || !modes.includes(value.mode as (typeof modes)[number])
    || typeof value.status !== "string" || !statuses.includes(value.status as (typeof statuses)[number])
    || !string(value.dataset_id) || !string(value.dataset_version)
    || !["synthetic", "public_reference", "real_authorized", "unavailable"].includes(String(value.data_mode))
    || !record(value.result) || !Array.isArray(value.evidence_refs) || !strings(value.limitations)
    || !record(value.report)) return false;
  const report = value.report;
  return string(report.report_id) && string(report.title) && report.mode === value.mode
    && report.status === value.status && report.dataset_id === value.dataset_id
    && report.dataset_version === value.dataset_version && report.data_mode === value.data_mode
    && string(report.report_sha256) && strings(report.findings)
    && strings(report.unknowns) && strings(report.evidence_refs);
}
