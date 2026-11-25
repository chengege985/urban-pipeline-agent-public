import type {
  GasM2AgentRequest, GasM2AgentResult, GasM2Binding, GasM2Catalog, GasM2Dataset, GasM2Details, GasM2RecordPage,
} from "../../shared/contracts/platform";

export type { GasM2AgentRequest, GasM2AgentResult, GasM2Binding, GasM2Catalog, GasM2Dataset, GasM2Details, GasM2RecordPage };
export type GasM2Kind = GasM2RecordPage["kind"];

const isObject = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const isString = (value: unknown): value is string => typeof value === "string";
const textOrNull = (value: unknown) => value === null || isString(value);
const sha = (value: unknown) => isString(value) && /^[a-f0-9]{64}$/.test(value);
const evidenceStrings = (value: unknown) => Array.isArray(value) && value.every(isString);
const nonnegative = (value: unknown) => typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
const id = (value: unknown) => isString(value) && /^[A-Z][A-Z0-9-]{1,63}$/.test(value);

function capabilities(value: unknown) {
  return isObject(value) && value.health_assessment === "unavailable"
    && value.operating_risk === "unavailable" && value.physical_prediction === "unavailable";
}

export function sameBinding(value: unknown, expected: GasM2Binding): boolean {
  return isObject(value) && value.dataset_id === expected.dataset_id && value.version === expected.version
    && value.registry_sha256 === expected.registry_sha256;
}

function dataset(value: unknown): value is GasM2Dataset {
  return isObject(value) && id(value.dataset_id) && textOrNull(value.version)
    && (value.sha256 === null || sha(value.sha256)) && (value.source_uri === null || (isString(value.source_uri) && value.source_uri.startsWith("https://")))
    && ["title", "source_kind", "data_kind", "geo_scope", "schema_status", "quality_status", "license_status",
      "usage_scope", "ingest_status"].every(key => isString(value[key]))
    && value.data_kind !== "real_authorized" && value.source_kind !== "authorized_operator"
    && (value.synthetic_flag === null || typeof value.synthetic_flag === "boolean")
    && textOrNull(value.coordinate_system) && textOrNull(value.statistics_time)
    && (value.normalized_sha256 === null || sha(value.normalized_sha256)) && (value.quarantine_sha256 === null || sha(value.quarantine_sha256))
    && isObject(value.record_counts) && Object.values(value.record_counts).every(nonnegative)
    && evidenceStrings(value.transformations) && evidenceStrings(value.facts)
    && Array.isArray(value.sources) && value.sources.every(row => isObject(row) && isString(row.name) && sha(row.sha256))
    && Array.isArray(value.field_mappings) && value.field_mappings.every(row => isObject(row)
      && isString(row.raw_field) && isString(row.normalized_field) && textOrNull(row.unit))
    && Array.isArray(value.issues) && value.issues.every(row => isObject(row) && isString(row.code)
      && isString(row.detail) && (row.count === null || nonnegative(row.count)));
}

export function gasM2Catalog(value: unknown): value is GasM2Catalog {
  if (!isObject(value) || value.schema_version !== "gas-data-m2/1" || !sha(value.registry_sha256)
    || !capabilities(value.capabilities) || !Array.isArray(value.datasets) || !value.datasets.every(dataset)) return false;
  return new Set(value.datasets.map(item => item.dataset_id)).size === value.datasets.length;
}

export function gasM2Details(value: unknown, expected: GasM2Binding): value is GasM2Details {
  return isObject(value) && sameBinding(value.binding, expected) && capabilities(value.capabilities)
    && dataset(value.dataset) && value.dataset.dataset_id === expected.dataset_id && value.dataset.version === expected.version;
}

export function gasM2Records(value: unknown, expected: GasM2Binding, kind: GasM2Kind): value is GasM2RecordPage {
  return isObject(value) && expected.dataset_id === "OWN-K001" && sameBinding(value.binding, expected) && value.kind === kind
    && value.data_kind === "internal_synthetic" && value.coordinate_system === "schematic" && capabilities(value.capabilities)
    && Array.isArray(value.items) && value.items.every(row => isObject(row) && isString(row.record_id))
    && typeof value.total === "number" && nonnegative(value.total) && value.total >= value.items.length;
}

export function gasM2Answer(value: unknown, expected: GasM2AgentRequest): value is GasM2AgentResult {
  return isObject(value) && sameBinding(value.binding, expected.binding) && capabilities(value.capabilities)
    && value.tool_name === "data_m2." + expected.section && isString(value.request_id)
    && isString(value.answer) && evidenceStrings(value.facts) && evidenceStrings(value.evidence_refs)
    && (!expected.record_id || value.evidence_refs.includes(`gas-m2-record:${expected.binding.dataset_id}:${expected.record_id}`))
    && ((value.explanation_source === "backend_facts" && value.model_status === "unavailable")
      || (value.explanation_source === "qwen_evidence_selection" && value.model_status === "ready" && value.model_id === "Qwen3.8-27B"
        && typeof value.model_calls === "number" && value.model_calls > 0));
}
