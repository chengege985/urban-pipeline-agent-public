"""Versioned, read-only reference results for steady gas CUV software."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from .data_package import Digest
from .network import _digest
from .steady_series_parallel import (
    NETWORK_EQUATION_URL, NETWORK_MODEL_VERSION, NETWORK_SCOPE, SeriesParallelInput, SeriesParallelSolution,
    solve_series_parallel,
)
from .steady_units import Positive, SolverContract
from .weymouth import (
    MODEL_ID, MODEL_REFERENCE, MODEL_VERSION, SCOPE, GasSteadySolverAdapter,
    WeymouthInput, WeymouthRun, WeymouthSolution, WeymouthSolver, WeymouthSourceInput,
)

BENCHMARK_ID = "syn-gas-public-weymouth-v1"
_SUITE_SHA256 = "05fcb95eac6981608a50bb513796c49d3f4f671fc00f2c176808c8f901367dec"
_SUITE_PATH = Path(__file__).with_name("fixtures") / "weymouth-public-v2.json"
Metric = Literal["flow_mmscfd", "outlet_psia", "internal_diameter_in"]
ResultUnit = Literal["MMSCFD", "psia", "in"]
_OUTPUT_UNITS: dict[Metric, ResultUnit] = {
    "flow_mmscfd": "MMSCFD", "outlet_psia": "psia", "internal_diameter_in": "in",
}
_REFERENCE_ORIGIN = {
    "literature_result": "文献结果",
    "third_party_software": "第三方软件值",
    "boundary_target": "公开边界目标",
}


class GasCuvModelProgress(SolverContract):
    implementation_status: Literal["unsupported", "candidate"]
    input_status: Literal["missing_inputs", "complete"]
    benchmark_status: Literal["not_run", "failed", "benchmark_passed"] = "not_run"
    field_validation_status: Literal["pending_field_validation", "business_validated"] = "pending_field_validation"
    model_id: str | None = None
    model_version: str | None = None


class GasCuvBenchmarkBinding(SolverContract):
    suite_id: str
    suite_version: str
    model_version: str
    suite_sha256: Digest


class GasCuvBenchmarkReference(SolverContract):
    id: str
    title: str
    url: str


class GasCuvBenchmarkInputSource(SolverContract):
    source_id: str
    locator: str
    notes: str


class GasCuvBenchmarkExpected(SolverContract):
    metric: Metric
    kind: Literal["published_result", "published_boundary"]
    origin: Literal["literature_result", "third_party_software", "boundary_target"]
    value: Positive
    tolerance: Positive
    source_id: str
    locator: str
    tolerance_basis: str


class _BenchmarkCase(SolverContract):
    case_id: str
    label: str
    source_input: WeymouthSourceInput
    input_source: GasCuvBenchmarkInputSource
    expected: GasCuvBenchmarkExpected


class _NetworkReference(SolverContract):
    id: Literal["split_pressure", "recombine_pressure", "outlet_pressure", "branch_one", "branch_two"]
    label: str
    unit: Literal["psig", "MMSCFD"]
    literature: Positive
    third_party: Positive
    tolerance: Positive


class _NetworkCase(SolverContract):
    case_id: str
    label: str
    input: SeriesParallelInput
    input_source: GasCuvBenchmarkInputSource
    comparison_source: GasCuvBenchmarkInputSource
    reference_checks: tuple[_NetworkReference, ...]
    tolerance_basis: str


class _BenchmarkSuite(SolverContract):
    suite_id: Literal["syn-gas-public-weymouth-v1"]
    suite_version: str
    references: tuple[GasCuvBenchmarkReference, ...]
    cases: tuple[_BenchmarkCase, ...]
    comparison_case_ids: tuple[str, str]
    network_case: _NetworkCase


class GasCuvForwardCheck(SolverContract):
    expected_mmscfd: float
    recalculated_mmscfd: float | None
    absolute_error_mmscfd: float | None
    tolerance_mmscfd: float
    passed: bool


class GasCuvBenchmarkCaseResult(_BenchmarkCase):
    input: WeymouthInput
    input_sha256: Digest
    solve: WeymouthRun
    unit: ResultUnit
    absolute_error: float | None
    relative_error_percent: float | None
    forward_check: GasCuvForwardCheck | None
    passed: bool


class GasCuvBenchmarkComparison(SolverContract):
    case_a: str
    case_b: str
    outlet_delta_psia: float
    flow_delta_mmscfd: float
    basis: Literal["same_boundary_except_diameter"] = "same_boundary_except_diameter"


class GasCuvNetworkCheck(_NetworkReference):
    solved: float | None
    absolute_error: float | None
    passed: bool


class GasCuvNetworkPrototype(SolverContract):
    case_id: str
    label: str
    input: SeriesParallelInput
    input_sha256: Digest
    input_source: GasCuvBenchmarkInputSource
    comparison_source: GasCuvBenchmarkInputSource
    tolerance_basis: str
    model_version: str = NETWORK_MODEL_VERSION
    scope: str = NETWORK_SCOPE
    equation_reference_url: str = NETWORK_EQUATION_URL
    field_validation_status: Literal["pending_field_validation"] = "pending_field_validation"
    status: Literal["benchmark_passed", "failed"]
    checks: tuple[GasCuvNetworkCheck, ...]
    solution: SeriesParallelSolution | None
    reason: str | None


class GasCuvBenchmarkReport(SolverContract):
    data_mode: Literal["synthetic"] = "synthetic"
    scope: Literal["public_reference_benchmark"] = "public_reference_benchmark"
    binding: GasCuvBenchmarkBinding
    progress: GasCuvModelProgress
    applicability: str = SCOPE
    equation_reference_url: str = MODEL_REFERENCE
    references: tuple[GasCuvBenchmarkReference, ...]
    cases: tuple[GasCuvBenchmarkCaseResult, ...]
    comparison: GasCuvBenchmarkComparison | None
    comparison_unavailable_reason: str | None = None
    network_prototype: GasCuvNetworkPrototype | None = None


def _load_suite() -> _BenchmarkSuite:
    try:
        # Normalize checkout line endings before verifying the pinned published suite.
        raw = _SUITE_PATH.read_bytes().replace(b"\r\n", b"\n")
        if sha256(raw).hexdigest() != _SUITE_SHA256:
            raise ValueError("benchmark digest mismatch")
        return _BenchmarkSuite.model_validate_json(raw)
    except (OSError, ValueError):
        raise HTTPException(503, "gas_cuv_benchmark_unavailable") from None


def _check_forward(
    inputs: WeymouthInput, solution: WeymouthSolution, solver: GasSteadySolverAdapter,
) -> GasCuvForwardCheck:
    known_flow = inputs.flow_mmscfd
    forward = inputs.model_copy(update={
        "boundary": "fixed_outlet_pressure", "flow_mmscfd": None,
        "outlet_psia": solution.outlet_psia, "internal_diameter_in": solution.internal_diameter_in,
    })
    recomputed = solver.solve(forward)
    flow = recomputed.solution.flow_mmscfd if recomputed.status == "converged" and recomputed.solution else None
    error = abs(known_flow - flow) if flow is not None else None
    tolerance = 1e-9 * max(1.0, known_flow)
    return GasCuvForwardCheck(
        expected_mmscfd=known_flow, recalculated_mmscfd=flow, absolute_error_mmscfd=error,
        tolerance_mmscfd=tolerance, passed=error is not None and error <= tolerance,
    )


def _run_case(case: _BenchmarkCase, solver: GasSteadySolverAdapter) -> GasCuvBenchmarkCaseResult:
    inputs = case.source_input.normalize_public_reference()
    outcome = solver.solve(inputs)
    solution = outcome.solution
    actual = getattr(solution, case.expected.metric) if solution else None
    error = abs(actual - case.expected.value) if actual is not None else None
    forward = _check_forward(inputs, solution, solver) if solution and inputs.flow_mmscfd is not None else None
    return GasCuvBenchmarkCaseResult(
        **case.model_dump(), input=inputs,
        input_sha256=_digest({"source": case.source_input.model_dump(mode="json"), "normalized": inputs.model_dump(mode="json")}),
        solve=outcome, unit=_OUTPUT_UNITS[case.expected.metric],
        absolute_error=error,
        relative_error_percent=100 * error / case.expected.value if error is not None else None,
        forward_check=forward,
        passed=(outcome.status == "converged" and error is not None and error <= case.expected.tolerance
                and (forward is None or forward.passed)),
    )


def _compare_diameters(
    left: GasCuvBenchmarkCaseResult, right: GasCuvBenchmarkCaseResult,
) -> GasCuvBenchmarkComparison | None:
    if left.case_id == right.case_id or left.input.boundary != "fixed_standard_flow":
        return None
    if left.input.model_dump(exclude={"internal_diameter_in"}) != right.input.model_dump(exclude={"internal_diameter_in"}):
        return None
    a, b = left.solve.solution, right.solve.solution
    if not left.passed or not right.passed or a is None or b is None:
        return None
    return GasCuvBenchmarkComparison(
        case_a=left.case_id, case_b=right.case_id,
        outlet_delta_psia=b.outlet_psia - a.outlet_psia, flow_delta_mmscfd=b.flow_mmscfd - a.flow_mmscfd,
    )


def _run_network_prototype(case: _NetworkCase) -> GasCuvNetworkPrototype:
    metrics = {}
    try:
        solution = solve_series_parallel(case.input)
    except (ValueError, ArithmeticError):
        solution = None
    if solution is not None:
        atmosphere = solution.atmosphere_psia
        metrics = {
            "split_pressure": solution.split_psia - atmosphere,
            "recombine_pressure": solution.recombine_psia - atmosphere,
            "outlet_pressure": solution.outlet_psia - atmosphere,
            "branch_one": solution.branch_flows_mmscfd[0],
            "branch_two": solution.branch_flows_mmscfd[1],
        }
    checks = []
    for row in case.reference_checks:
        solved = metrics.get(row.id)
        error = abs(solved - row.literature) if solved is not None else None
        checks.append(GasCuvNetworkCheck(
            **row.model_dump(), solved=solved, absolute_error=error, passed=error is not None and error <= row.tolerance,
        ))
    passed = solution is not None and all(row.passed for row in checks)
    return GasCuvNetworkPrototype(
        **case.model_dump(exclude={"reference_checks"}),
        input_sha256=_digest(case.input.model_dump(mode="json")),
        status="benchmark_passed" if passed else "failed",
        checks=tuple(checks), solution=solution,
        reason=None if passed else "串并联工况未通过求解或文献误差核对。",
    )


def run_public_benchmark(
    expected_binding: GasCuvBenchmarkBinding | None = None, *, solver: GasSteadySolverAdapter | None = None,
) -> GasCuvBenchmarkReport:
    suite = _load_suite()
    binding = GasCuvBenchmarkBinding(
        suite_id=suite.suite_id, suite_version=suite.suite_version, model_version=MODEL_VERSION,
        suite_sha256=_SUITE_SHA256,
    )
    if expected_binding is not None and expected_binding != binding:
        raise HTTPException(409, "gas_cuv_benchmark_version_conflict")

    executor = solver if solver is not None else WeymouthSolver()
    try:
        rows = tuple(_run_case(case, executor) for case in suite.cases)
    except ValueError:
        raise HTTPException(503, "gas_cuv_benchmark_unavailable") from None
    passed = all(row.passed for row in rows)
    comparison = None
    comparison_reason = None
    if passed:
        by_id = {row.case_id: row for row in rows}
        case_a, case_b = suite.comparison_case_ids
        comparison = _compare_diameters(by_id[case_a], by_id[case_b])
        if comparison is None:
            comparison_reason = "固定流量比较要求相同入口压力、流量、管长、标况及介质条件，仅内径可变。"
    return GasCuvBenchmarkReport(
        binding=binding, progress=GasCuvModelProgress(
            implementation_status="candidate", model_id=MODEL_ID, model_version=MODEL_VERSION,
            input_status="missing_inputs" if any(row.solve.missing_fields for row in rows) else "complete",
            benchmark_status="benchmark_passed" if passed else "failed",
        ),
        references=suite.references, cases=rows, comparison=comparison,
        comparison_unavailable_reason=comparison_reason,
        network_prototype=_run_network_prototype(suite.network_case),
    )


def benchmark_facts(report: GasCuvBenchmarkReport) -> list[str]:
    if report.progress.benchmark_status != "benchmark_passed":
        return []
    binding = report.binding
    facts = [f"公开稳态基准 {binding.suite_id} 版本 {binding.suite_version}，模型 {binding.model_version}，"
             f"算例清单 SHA-256 {binding.suite_sha256}。公开基准通过，现场工程能力待校核。"]
    for row in report.cases:
        value = getattr(row.solve.solution, row.expected.metric)
        unit = row.unit
        text = (f"{row.case_id} 求解 {value:.6f} {unit}，{_REFERENCE_ORIGIN[row.expected.origin]} {row.expected.value:g} {unit}，"
                f"绝对差 {row.absolute_error:.6f} {unit}，输入 SHA-256 {row.input_sha256}。")
        if row.forward_check:
            text += (f"代回正向模型得到 {row.forward_check.recalculated_mmscfd:.9f} MMSCFD（标况），"
                     f"目标 {row.forward_check.expected_mmscfd:g} MMSCFD，闭合差 {row.forward_check.absolute_error_mmscfd:.3e} MMSCFD。")
        facts.append(text)
    network = report.network_prototype
    if network and network.status == "benchmark_passed":
        values = "、".join(f"{row.label} {row.solved:.6f} {row.unit}" for row in network.checks)
        facts.append(f"Case 12 串并联原型 {network.model_version} 的公开对照通过。"
                     f"{values}。输入 SHA-256 {network.input_sha256}。")
    change = report.comparison
    if change is not None:
        inputs = {case.case_id: case.input_sha256 for case in report.cases}
        facts.append(f"相同入口压力、标况流量、管长及介质条件下，取径 B 相对 A 的出口绝对压力变化 {change.outlet_delta_psia:.6f} psia，"
                     f"指定标况流量变化 {change.flow_delta_mmscfd:.6f} MMSCFD。"
                     f"A 输入 SHA-256 {inputs[change.case_a]}，B 输入 SHA-256 {inputs[change.case_b]}。"
                     "此差值仅描述公开工况的内径敏感性，现场安全性待工程校核。")
    elif report.comparison_unavailable_reason:
        facts.append(f"取径比较不可用。{report.comparison_unavailable_reason}")
    return facts
