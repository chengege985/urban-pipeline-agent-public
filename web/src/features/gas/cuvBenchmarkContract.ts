import type { GasCuvBenchmarkBinding, GasCuvBenchmarkReport } from "../../shared/contracts/platform";

export const WEYMOUTH_SUITE = "syn-gas-public-weymouth-v1";
export const benchmarkQuestion = /weymouth|韦茅斯|韦默斯|公开(?:单管|稳态|求解|验证)?基准|公开算例/i;

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const digest = (value: unknown) => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const https = (value: unknown) => typeof value === "string" && /^https:\/\//.test(value);
const measurement = (value: unknown) => object(value) && finite(value.value) && text(value.unit);
const optionalMeasurement = (value: unknown) => value == null || measurement(value);
const units = new Map([["flow_mmscfd", "MMSCFD"], ["outlet_psia", "psia"], ["internal_diameter_in", "in"]]);

export function matchesBenchmarkBinding(left: GasCuvBenchmarkBinding, right: GasCuvBenchmarkBinding) {
  return left.suite_id === right.suite_id && left.suite_version === right.suite_version
    && left.suite_sha256 === right.suite_sha256 && left.model_version === right.model_version;
}

function inputSource(value: unknown, sources: Set<unknown>) {
  return object(value) && sources.has(value.source_id) && text(value.notes) && text(value.locator);
}

function forwardCheck(value: unknown) {
  if (!object(value) || !finite(value.expected_mmscfd) || !finite(value.tolerance_mmscfd)
    || typeof value.passed !== "boolean") return false;
  if (value.recalculated_mmscfd == null) return !value.passed && value.absolute_error_mmscfd == null;
  return finite(value.recalculated_mmscfd) && finite(value.absolute_error_mmscfd)
    && value.passed === (value.absolute_error_mmscfd <= value.tolerance_mmscfd);
}

function benchmarkCase(item: unknown, sources: Set<unknown>) {
  if (!object(item) || !text(item.case_id) || !text(item.label) || !digest(item.input_sha256)
    || typeof item.passed !== "boolean" || !object(item.input) || !object(item.expected) || !object(item.solve)
    || !object(item.source_input) || !inputSource(item.input_source, sources)) return false;
  const { input, source_input: raw, expected, solve } = item;
  if (!measurement(raw.inlet_pressure) || !optionalMeasurement(raw.outlet_pressure) || !optionalMeasurement(raw.atmosphere)
    || !optionalMeasurement(raw.internal_diameter) || !measurement(raw.length) || !measurement(raw.base_pressure)
    || !measurement(raw.base_temperature) || !measurement(raw.gas_temperature)
    || (raw.flow != null && (!object(raw.flow) || !measurement(raw.flow) || raw.flow.basis !== "standard"))) return false;
  if (!text(expected.metric) || !units.has(expected.metric) || units.get(expected.metric) !== item.unit
    || !["literature_result", "third_party_software", "boundary_target"].includes(String(expected.origin))
    || expected.kind !== (expected.origin === "boundary_target" ? "published_boundary" : "published_result")
    || !finite(expected.value) || !finite(expected.tolerance) || !text(expected.locator) || !text(expected.tolerance_basis)
    || !sources.has(expected.source_id) || !finite(solve.supplied_inputs) || !finite(solve.required_inputs)
    || !Array.isArray(solve.missing_fields) || !solve.missing_fields.every(text)) return false;
  if (solve.status !== "converged") return !item.passed && solve.solution == null && item.forward_check == null;
  const solution = solve.solution;
  const inverse = input.flow_mmscfd != null;
  const forward = item.forward_check;
  if (inverse ? !forwardCheck(forward) : forward != null) return false;
  return object(solution) && finite(solution.outlet_psia) && finite(solution.inlet_psia) && finite(solution.flow_mmscfd)
    && finite(solution.internal_diameter_in) && finite(solution.equation_residual_psi2)
    && finite(solution.standard_flow_balance_residual_mmscfd) && finite(solution.iterations)
    && finite(item.absolute_error) && finite(item.relative_error_percent)
    && item.passed === (item.absolute_error <= expected.tolerance && (!inverse || (object(forward) && forward.passed === true)));
}

function networkPrototype(value: unknown, sources: Set<unknown>) {
  if (!object(value) || !text(value.case_id) || !text(value.label) || !text(value.model_version) || !digest(value.input_sha256)
    || value.field_validation_status !== "pending_field_validation" || !text(value.scope) || !https(value.equation_reference_url)
    || !inputSource(value.input_source, sources) || !inputSource(value.comparison_source, sources) || !text(value.tolerance_basis)
    || !object(value.input) || !measurement(value.input.inlet_pressure) || !measurement(value.input.atmosphere)
    || !object(value.input.terminal_flow) || !finite(value.input.terminal_flow.value)
    || value.input.terminal_flow.basis !== "standard" || !["million_ft3/day", "m3/day"].includes(String(value.input.terminal_flow.unit))
    || !Array.isArray(value.checks) || value.checks.length !== 5) return false;
  if (!value.checks.every((row) => object(row) && text(row.label) && text(row.id)
    && (row.unit === "psig" || row.unit === "MMSCFD") && finite(row.literature) && finite(row.third_party)
    && finite(row.tolerance) && (row.solved == null
      ? row.absolute_error == null && row.passed === false
      : finite(row.solved) && finite(row.absolute_error) && row.passed === (row.absolute_error <= row.tolerance)))) return false;
  const solution = value.solution;
  if (solution != null && (!object(solution) || !finite(solution.max_flow_balance_residual_mmscfd)
    || !finite(solution.max_pressure_equation_residual_psi2))) return false;
  if (value.status === "failed") return text(value.reason) && value.checks.some((row) => !row.passed);
  return value.status === "benchmark_passed" && object(solution) && value.checks.every((row) => row.passed);
}

export function gasCuvBenchmarkReport(value: unknown): value is GasCuvBenchmarkReport {
  if (!object(value) || value.data_mode !== "synthetic" || value.scope !== "public_reference_benchmark"
    || !object(value.binding) || value.binding.suite_id !== WEYMOUTH_SUITE
    || !text(value.binding.suite_version) || !text(value.binding.model_version) || !digest(value.binding.suite_sha256)
    || !object(value.progress) || value.progress.field_validation_status !== "pending_field_validation"
    || value.progress.implementation_status !== "candidate"
    || value.progress.model_version !== value.binding.model_version
    || !["complete", "missing_inputs"].includes(String(value.progress.input_status))
    || !text(value.applicability) || !https(value.equation_reference_url)
    || !Array.isArray(value.references) || !value.references.every((ref) =>
      object(ref) && text(ref.id) && text(ref.title) && https(ref.url))
    || !Array.isArray(value.cases) || value.cases.length < 2) return false;

  const sources = new Set(value.references.map((item) => item.id));
  if (!value.cases.every((item) => benchmarkCase(item, sources))
    || (value.network_prototype != null && !networkPrototype(value.network_prototype, sources))) return false;
  if (value.progress.benchmark_status === "failed") return value.comparison === null && value.cases.some((item) => !item.passed);
  if (value.progress.benchmark_status !== "benchmark_passed" || value.progress.input_status !== "complete"
    || !value.cases.every((item) => item.passed)) return false;
  if (value.comparison === null) return text(value.comparison_unavailable_reason);
  const ids = new Set(value.cases.map((item) => item.case_id));
  return object(value.comparison) && value.comparison.basis === "same_boundary_except_diameter"
    && ids.has(value.comparison.case_a) && ids.has(value.comparison.case_b) && value.comparison.case_a !== value.comparison.case_b
    && finite(value.comparison.outlet_delta_psia) && finite(value.comparison.flow_delta_mmscfd);
}
