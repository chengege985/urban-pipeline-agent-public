import type {
  GasCuvEngineeringAssessment, GasCuvEngineeringCapability, GasCuvReplayResponse,
} from "../../shared/contracts/platform";

const record = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const nonempty = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const strings = (values: unknown): values is string[] => Array.isArray(values) && values.every(nonempty);
const sha = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const capabilityIds = ["graph_plan_comparison", "steady_pressure_flow", "supply_adequacy", "transient_recovery"];

export function gasCuvEngineeringCapability(value: unknown): value is GasCuvEngineeringCapability {
  if (!record(value) || !capabilityIds.includes(String(value.capability_id))
    || !nonempty(value.label) || !nonempty(value.scope)
    || !strings(value.required_input_ids) || !strings(value.missing_input_ids)
    || !strings(value.calibration_requirements) || !strings(value.validation_evidence_refs)
    || !strings(value.reasons)) return false;
  if (value.status === "ready_synthetic") {
    return value.capability_id === "graph_plan_comparison" && value.validation_status === "synthetic_validated"
      && nonempty(value.method) && nonempty(value.model_version)
      && value.missing_input_ids.length === 0 && value.validation_evidence_refs.length > 0;
  }
  return value.status === "blocked" && value.validation_status === "not_business_validated"
    && value.method === null && value.model_version === null && value.missing_input_ids.length > 0
    && value.validation_evidence_refs.length === 0;
}

export function gasCuvEngineeringAssessment(
  value: unknown, report?: GasCuvReplayResponse,
): value is GasCuvEngineeringAssessment {
  if (!record(value) || value.contract_version !== "gas-cuv-engineering-input/1"
    || !nonempty(value.case_id) || !nonempty(value.scenario_id)
    || !nonempty(value.scenario_at) || !Number.isFinite(Date.parse(value.scenario_at))
    || !strings(value.plan_ids) || value.plan_ids.length < 2
    || !record(value.binding) || !sha(value.binding.case_content_sha256) || !sha(value.binding.network_content_sha256)
    || !sha(value.binding.replay_content_sha256) || !record(value.evidence_scope)
    || value.evidence_scope.scope !== "gas_m0_graph_cuv" || value.evidence_scope.data_mode !== "synthetic"
    || !nonempty(value.evidence_scope.dataset_id) || value.evidence_scope.dataset_version !== "gas-cuv-replay-0.1.0"
    || value.evidence_scope.source_sha256 !== value.binding.replay_content_sha256
    || !Array.isArray(value.inputs) || !value.inputs.every((input) => record(input) && nonempty(input.input_id)
      && nonempty(input.label) && strings(input.fields) && nonempty(input.source) && nonempty(input.unit_time_basis)
      && (input.status === "missing" || input.status === "verified_synthetic") && strings(input.evidence_refs))
    || !Array.isArray(value.capabilities) || !value.capabilities.every(gasCuvEngineeringCapability)) return false;
  const inputStatus = new Map(value.inputs.map((input) => [input.input_id, input.status]));
  const ids = new Set(value.capabilities.map((item) => item.capability_id));
  if (ids.size !== capabilityIds.length || ids.size !== value.capabilities.length || inputStatus.size !== value.inputs.length
    || !value.capabilities.every((item) => item.required_input_ids.every((id) =>
      inputStatus.get(id) === (item.missing_input_ids.includes(id) ? "missing" : "verified_synthetic")))) return false;
  if (!report) return true;
  const topology = report.topology_evidence;
  const plans = value.plan_ids;
  return value.case_id === report.case_id && value.scenario_id === report.scenario_id
    && Date.parse(value.scenario_at) === Date.parse(report.scenario_at)
    && value.evidence_scope.dataset_id === report.network_id
    && value.binding.network_content_sha256 === topology.network_content_sha256
    && value.binding.case_content_sha256 === topology.case_content_sha256
    && value.binding.replay_content_sha256 === topology.replay_content_sha256
    && plans.length === report.plans.length && report.plans.every((plan, index) => plans[index] === plan.plan_id)
    && value.capabilities.find((item) => item.capability_id === "graph_plan_comparison")?.model_version === report.algorithm_version;
}
