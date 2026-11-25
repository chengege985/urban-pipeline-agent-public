"""Candidate steady-state solver for a single horizontal passive gas pipe."""

from __future__ import annotations

from math import isfinite, sqrt
from typing import Literal, Protocol

from .steady_units import (
    Efficiency, GasConditions, GasPressure, GasVolumeRate, PipeDiameter, PipeLength, Positive, SolverContract,
)

MODEL_ID = "weymouth_horizontal_single_pipe"
MODEL_VERSION = "weymouth-uscs-0.2.0"
MODEL_REFERENCE = "https://www.cedengineering.ca/userfiles/P06-001%20-%20Gas%20Pipeline%20Hydraulics.pdf#page=36"
SCOPE = "水平等径单管、正向等温稳态、定压缩因子和效率。仅核验公开高压算例，低压配气尚待适用性校核。"
# 750 psia is the lowest endpoint in the published single-pipe suite.
# This is a software benchmark gate, not a regulatory pressure classification.
_MIN_BENCHMARK_PSIA = 750.0
_DIAMETER_EXPONENT = 2.667
_RELATIVE_RESIDUAL_LIMIT = 1e-10

Boundary = Literal["fixed_outlet_pressure", "fixed_standard_flow", "fixed_pressures_and_flow"]
SolveStatus = Literal["converged", "missing_inputs", "invalid_inputs", "outside_model_scope", "non_converged"]


class WeymouthInput(SolverContract):
    boundary: Boundary
    application: Literal["public_reference", "unclassified", "low_pressure_distribution"] = "unclassified"
    inlet_psia: Positive | None = None
    outlet_psia: Positive | None = None
    flow_mmscfd: Positive | None = None
    internal_diameter_in: Positive | None = None
    length_mi: Positive | None = None
    gas_specific_gravity: Positive | None = None
    compressibility: Positive | None = None
    efficiency: Efficiency | None = None
    base_pressure_psia: Positive | None = None
    base_temperature_rankine: Positive | None = None
    gas_temperature_rankine: Positive | None = None


class WeymouthSourceInput(GasConditions):
    boundary: Boundary
    inlet_pressure: GasPressure
    outlet_pressure: GasPressure | None = None
    atmosphere: GasPressure | None = None
    flow: GasVolumeRate | None = None
    internal_diameter: PipeDiameter | None = None
    length: PipeLength
    efficiency: Efficiency

    def normalize_public_reference(self) -> WeymouthInput:
        return WeymouthInput(
            boundary=self.boundary, application="public_reference",
            inlet_psia=self.inlet_pressure.to_psia(self.atmosphere),
            outlet_psia=self.outlet_pressure.to_psia(self.atmosphere) if self.outlet_pressure else None,
            flow_mmscfd=self.flow.to_standard_mmscfd() if self.flow else None,
            internal_diameter_in=self.internal_diameter.to_inches() if self.internal_diameter else None,
            length_mi=self.length.to_miles(), gas_specific_gravity=self.gas_specific_gravity,
            compressibility=self.compressibility, efficiency=self.efficiency,
            base_pressure_psia=self.base_pressure.to_psia(),
            base_temperature_rankine=self.base_temperature.to_rankine(),
            gas_temperature_rankine=self.gas_temperature.to_rankine(),
        )


_COMMON_INPUTS = (
    "inlet_psia", "length_mi", "gas_specific_gravity", "compressibility", "efficiency",
    "base_pressure_psia", "base_temperature_rankine", "gas_temperature_rankine",
)
_UNKNOWN_BY_BOUNDARY = {
    "fixed_outlet_pressure": "flow_mmscfd",
    "fixed_standard_flow": "outlet_psia",
    "fixed_pressures_and_flow": "internal_diameter_in",
}
_SOLVED_FIELDS = ("outlet_psia", "flow_mmscfd", "internal_diameter_in")


class WeymouthSolution(SolverContract):
    inlet_psia: float
    outlet_psia: float
    flow_mmscfd: float
    internal_diameter_in: float
    equation_residual_psi2: float
    standard_flow_balance_residual_mmscfd: float
    iterations: int


class WeymouthRun(SolverContract):
    status: SolveStatus
    required_inputs: int
    supplied_inputs: int
    missing_fields: tuple[str, ...] = ()
    reason: str | None = None
    solution: WeymouthSolution | None = None


class GasSteadySolverAdapter(Protocol):
    def solve(self, inputs: WeymouthInput) -> WeymouthRun: ...


def _bisect_flow(gain: float, drop: float, ceiling: float, max_iterations: int) -> tuple[float, int] | None:
    lower, upper = 0.0, ceiling
    for iteration in range(1, max_iterations + 1):
        flow = lower + (upper - lower) / 2
        error = (flow / gain)**2 - drop
        if abs(error) <= _RELATIVE_RESIDUAL_LIMIT * max(1.0, drop):
            return flow, iteration
        if error > 0:
            upper = flow
        else:
            lower = flow
    return None


class WeymouthSolver:
    def __init__(self, *, max_iterations: int = 80):
        self.max_iterations = max_iterations

    def solve(self, inputs: WeymouthInput) -> WeymouthRun:
        unknown = _UNKNOWN_BY_BOUNDARY[inputs.boundary]
        required = (*_COMMON_INPUTS, *(name for name in _SOLVED_FIELDS if name != unknown))
        missing = tuple(name for name in required if getattr(inputs, name) is None)
        counts = {"required_inputs": len(required), "supplied_inputs": len(required) - len(missing)}

        def reject(status: SolveStatus, reason: str) -> WeymouthRun:
            return WeymouthRun(status=status, reason=reason, missing_fields=missing, **counts)

        if missing:
            return reject("missing_inputs", "输入字段缺失。")
        if getattr(inputs, unknown) is not None:
            return reject("invalid_inputs", "待求值已被指定，边界条件过定。")
        if inputs.application != "public_reference":
            return reject("outside_model_scope", "当前应用场景缺少 Weymouth 适用性校核。")

        inlet, outlet, flow, diameter = (
            inputs.inlet_psia, inputs.outlet_psia, inputs.flow_mmscfd, inputs.internal_diameter_in,
        )
        if outlet is not None and outlet >= inlet:
            return reject("outside_model_scope", "仅支持入口绝对压力大于出口的正向工况。")
        if min(inlet, outlet if outlet is not None else inlet) < _MIN_BENCHMARK_PSIA:
            return reject("outside_model_scope", "低于已核验公开高压算例的端点范围。")

        # Menon USCS Weymouth equation, zero elevation difference and Le = L.
        # Q in MMSCFD = factor * D^2.667 * sqrt(P1²-P2²), with P in psia.
        iterations = 0
        try:
            factor = (433.5 * inputs.efficiency * inputs.base_temperature_rankine / inputs.base_pressure_psia
                      / sqrt(inputs.gas_specific_gravity * inputs.gas_temperature_rankine
                             * inputs.length_mi * inputs.compressibility) / 1_000_000)
            if diameter is None:
                diameter = (flow / (factor * sqrt((inlet - outlet) * (inlet + outlet))))**(1 / _DIAMETER_EXPONENT)
            gain = factor * diameter**_DIAMETER_EXPONENT
            ceiling = inlet * gain
            if not isfinite(ceiling) or ceiling <= 0:
                return reject("outside_model_scope", "输入超出可求解数值范围。")

            if outlet is None:
                if flow >= ceiling:
                    return reject("outside_model_scope", "给定流量在本模型下没有正出口绝对压力解。")
                normalized_flow = flow / gain
                outlet = sqrt((inlet - normalized_flow) * (inlet + normalized_flow))
                if not _MIN_BENCHMARK_PSIA <= outlet < inlet:
                    return reject("outside_model_scope", "求解的出口超出已核验的正向压力范围。")

            drop = (inlet - outlet) * (inlet + outlet)
            if flow is None:
                solved_flow = _bisect_flow(gain, drop, ceiling, self.max_iterations)
                if solved_flow is None:
                    return reject("non_converged", "流量迭代未在限定次数内闭合压力方程。")
                flow, iterations = solved_flow
            equation_residual = abs(drop - (flow / gain)**2)
            flow_balance = abs(flow - gain * sqrt(drop))
        except (ZeroDivisionError, OverflowError, ValueError):
            return reject("outside_model_scope", "输入超出可求解数值范围。")

        if (not all(isfinite(value) for value in (flow, outlet, diameter, equation_residual, flow_balance))
            or not 0 < flow < ceiling or diameter <= 0
            or equation_residual > _RELATIVE_RESIDUAL_LIMIT * max(1.0, drop)
            or flow_balance > _RELATIVE_RESIDUAL_LIMIT * max(1.0, flow)):
            return reject("non_converged", "压力方程或两端标况流量闭合检查未通过。")
        return WeymouthRun(status="converged", **counts, solution=WeymouthSolution(
            inlet_psia=inlet, outlet_psia=outlet, flow_mmscfd=flow, internal_diameter_in=diameter,
            equation_residual_psi2=equation_residual, standard_flow_balance_residual_mmscfd=flow_balance,
            iterations=iterations,
        ))
