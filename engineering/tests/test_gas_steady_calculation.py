"""Software verification for explicit gas units and the inverse solver boundaries."""

import pytest
from pydantic import ValidationError

from src.gas_application import cuv_benchmark
from src.gas_application.steady_series_parallel import solve_series_parallel
from src.gas_application.steady_units import GasPressure, GasTemperature, GasVolumeRate, PipeDiameter, PipeLength
from src.gas_application.weymouth import WeymouthInput, WeymouthSolver


def _case(case_id):
    suite = cuv_benchmark._load_suite()
    return next(row.source_input for row in suite.cases if row.case_id == case_id)


def _solve(inputs, **changes):
    outcome = WeymouthSolver().solve(WeymouthInput(**(inputs.model_dump() | changes)))
    assert outcome.status == "converged" and outcome.solution is not None, outcome
    return outcome.solution


def test_explicit_gauge_and_absolute_pressure_bases():
    gauge = GasPressure(value=1400, unit="psig")
    atmosphere = GasPressure(value=14.696, unit="psia")
    local_atmosphere = GasPressure(value=14.5, unit="psia")
    assert gauge.to_psia(atmosphere) == pytest.approx(1414.696)
    assert gauge.to_psia(local_atmosphere) == pytest.approx(1414.5)
    assert GasPressure(value=750, unit="psia").to_psia(atmosphere) == 750
    assert GasPressure(value=100, unit="kPa_gauge").to_psia(
        GasPressure(value=101.325, unit="kPa_abs")
    ) == pytest.approx(29.199722, rel=1e-6)
    with pytest.raises(ValueError, match="separate absolute atmospheric pressure"):
        gauge.to_psia()
    with pytest.raises(ValueError, match="separate absolute atmospheric pressure"):
        gauge.to_psia(GasPressure(value=0, unit="psig"))
    with pytest.raises(ValueError, match="positive"):
        GasPressure(value=-14.696, unit="psig").to_psia(atmosphere)
    with pytest.raises(ValueError, match="separate absolute atmospheric pressure"):
        _case("case13-min-diameter-menon").model_copy(update={"base_pressure": gauge}).normalize_public_reference()


def test_explicit_standard_flow_and_si_are_equivalent_at_same_base():
    normalized = _case("case13-min-diameter-menon").normalize_public_reference()
    raw = _case("case13-min-diameter-menon")
    si = raw.model_copy(update={
        "inlet_pressure": GasPressure(value=9652.6602104352, unit="kPa_gauge"),
        "outlet_pressure": GasPressure(value=5515.8058345344, unit="kPa_gauge"),
        "atmosphere": GasPressure(value=101.3529322095696, unit="kPa_abs"),
        "base_pressure": GasPressure(value=101.3529322095696, unit="kPa_abs"),
        "base_temperature": GasTemperature(value=288.8888888888889, unit="K"),
        "gas_temperature": GasTemperature(value=15.7388888888889, unit="degC"),
        "length": PipeLength(value=160.9344, unit="km"),
        "flow": GasVolumeRate(value=2831684.6592, unit="m3/day", basis="standard"),
    })
    converted = si.normalize_public_reference()
    assert (converted.inlet_psia, converted.outlet_psia, converted.base_pressure_psia) == pytest.approx(
        (1414.7, 814.7, 14.7)
    )
    assert converted.flow_mmscfd == pytest.approx(100)
    assert converted.length_mi == pytest.approx(100)
    assert converted.gas_temperature_rankine == pytest.approx(520)
    assert converted.base_temperature_rankine == pytest.approx(520)
    assert PipeDiameter(value=311.15, unit="mm").to_inches() == pytest.approx(12.25)
    assert GasTemperature(value=60, unit="degF").to_rankine() == pytest.approx(519.67)
    assert _solve(converted).internal_diameter_in == pytest.approx(_solve(normalized).internal_diameter_in, rel=1e-12)
    actual = si.flow.model_copy(update={"basis": "actual"})
    with pytest.raises(ValueError, match="actual volumetric flow"):
        si.model_copy(update={"flow": actual}).normalize_public_reference()


@pytest.mark.parametrize(("field", "value"), [
    ("gas_temperature_rankine", 0),
    ("base_temperature_rankine", -1),
    ("compressibility", 0),
    ("compressibility", float("inf")),
    ("gas_specific_gravity", -0.5),
    ("efficiency", 0),
    ("efficiency", 1.01),
    ("flow_mmscfd", float("nan")),
    ("outlet_psia", True),
])
def test_invalid_canonical_values_are_rejected(field, value):
    inputs = _case("case13-min-diameter-menon").normalize_public_reference()
    with pytest.raises(ValidationError):
        WeymouthInput(**(inputs.model_dump() | {field: value}))


def test_temperature_and_pressure_outside_absolute_domain_rejected():
    with pytest.raises(ValueError, match="positive"):
        GasTemperature(value=-273.15, unit="degC").to_rankine()
    with pytest.raises(ValueError, match="positive"):
        GasPressure(value=0, unit="kPa_abs").to_psia()


@pytest.mark.parametrize(("changes", "status"), [
    ({"flow_mmscfd": None}, "missing_inputs"),
    ({"internal_diameter_in": 13.3}, "invalid_inputs"),
    ({"application": "unclassified"}, "outside_model_scope"),
    ({"application": "low_pressure_distribution"}, "outside_model_scope"),
    ({"inlet_psia": 15.1, "outlet_psia": 14.9}, "outside_model_scope"),
    ({"inlet_psia": 814.7}, "outside_model_scope"),
    ({"inlet_psia": 750}, "outside_model_scope"),
    ({"inlet_psia": 1e250}, "outside_model_scope"),
])
def test_unknown_or_incompatible_boundaries_do_not_produce_results(changes, status):
    inputs = _case("case13-min-diameter-menon").normalize_public_reference()
    outcome = WeymouthSolver().solve(WeymouthInput(**(inputs.model_dump() | changes)))
    assert (outcome.status, outcome.solution) == (status, None)


def test_inverse_outlet_scope_is_checked_on_the_computed_endpoint():
    inputs = _case("menon-course-ex11-outlet").normalize_public_reference()
    result = WeymouthSolver().solve(inputs.model_copy(update={"flow_mmscfd": 360.0}))
    assert result.status == "outside_model_scope" and result.solution is None


@pytest.mark.parametrize(("field", "increase_raises_flow"), [
    ("inlet_psia", True),
    ("outlet_psia", False),
    ("internal_diameter_in", True),
    ("length_mi", False),
    ("gas_specific_gravity", False),
    ("compressibility", False),
    ("efficiency", True),
    ("gas_temperature_rankine", False),
])
def test_forward_flow_parameter_perturbations_are_monotone(field, increase_raises_flow):
    inputs = _case("pipeflow-case13-flow").normalize_public_reference()
    baseline = _solve(inputs).flow_mmscfd
    perturbed = _solve(inputs, **{field: getattr(inputs, field) * 1.02}).flow_mmscfd
    assert perturbed != pytest.approx(baseline)
    assert (perturbed > baseline) == increase_raises_flow


def test_diameter_is_the_minimum_boundary_solution_and_closes_when_substituted():
    inputs = _case("case13-min-diameter-menon").normalize_public_reference()
    diameter = _solve(inputs).internal_diameter_in
    larger_demand = _solve(inputs, flow_mmscfd=110).internal_diameter_in
    assert larger_demand > diameter
    forward = inputs.model_copy(update={"boundary": "fixed_outlet_pressure", "flow_mmscfd": None})
    assert _solve(forward, internal_diameter_in=diameter).flow_mmscfd == pytest.approx(100, abs=1e-7)
    assert _solve(forward, internal_diameter_in=diameter * 0.999).flow_mmscfd < 100
    assert _solve(forward, internal_diameter_in=diameter * 1.001).flow_mmscfd > 100


def test_network_fixed_friction_branches_conserve_and_have_same_endpoint_pressure():
    network = cuv_benchmark.run_public_benchmark().network_prototype
    assert network.status == "benchmark_passed"
    assert network.field_validation_status == "pending_field_validation"
    assert all(row.passed for row in network.checks)
    assert [row.literature for row in network.checks] == [1166.6, 1130.9, 1071.12, 51.0, 49.0]
    assert [row.third_party for row in network.checks] == [1167.6, 1131.5, 1073.6, 51.2, 48.8]
    solution = network.solution
    assert solution.outlet_psia < solution.recombine_psia < solution.split_psia < solution.inlet_psia
    assert sum(solution.branch_flows_mmscfd) == pytest.approx(solution.terminal_flow_mmscfd, abs=1e-10)
    assert solution.max_flow_balance_residual_mmscfd < 1e-10
    assert solution.max_pressure_equation_residual_psi2 < 1e-7
    swapped = solve_series_parallel(network.input.model_copy(update={"parallel": network.input.parallel[::-1]}))
    assert swapped.branch_flows_mmscfd == pytest.approx(solution.branch_flows_mmscfd[::-1])
    assert swapped.outlet_psia == pytest.approx(solution.outlet_psia)
    impossible = network.input.model_copy(update={"terminal_flow": GasVolumeRate(
        value=1000, unit="million_ft3/day", basis="standard",
    )})
    with pytest.raises(ValueError, match="no finite positive forward solution"):
        solve_series_parallel(impossible)


def test_different_normalized_boundaries_cannot_claim_a_comparison(monkeypatch):
    suite = cuv_benchmark._load_suite()
    scenarios = []
    for row in suite.cases:
        if row.case_id == "case13-diameter-b":
            new_rate = row.source_input.flow.model_copy(update={"value": 100.01})
            row = row.model_copy(update={"source_input": row.source_input.model_copy(update={"flow": new_rate})})
        scenarios.append(row)
    monkeypatch.setattr(cuv_benchmark, "_load_suite", lambda: suite.model_copy(update={"cases": tuple(scenarios)}))
    report = cuv_benchmark.run_public_benchmark()
    assert report.progress.benchmark_status == "benchmark_passed"
    assert report.comparison is None and report.comparison_unavailable_reason
    facts = cuv_benchmark.benchmark_facts(report)
    assert facts[-1].startswith("取径比较不可用")
    assert not any("取径 B 相对 A" in fact for fact in facts)
