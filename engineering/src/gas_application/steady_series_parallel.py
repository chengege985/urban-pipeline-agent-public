"""Fixed-friction General Flow prototype for one split and one recombination."""

from __future__ import annotations

from math import fsum, isfinite, sqrt

from .steady_units import GasConditions, GasPressure, GasVolumeRate, PipeDiameter, PipeLength, Positive, SolverContract

NETWORK_MODEL_VERSION = "general-flow-fixed-darcy-uscs-0.1.0"
NETWORK_EQUATION_URL = "https://www.cedengineering.ca/userfiles/P06-001%20-%20Gas%20Pipeline%20Hydraulics.pdf#page=20"
NETWORK_SCOPE = "公开算例的水平被动管段，单一入口压力和末端固定标况流量，定 Darcy 摩阻与气体性质。"
_RESIDUAL_LIMIT = 1e-10


class GeneralFlowPipe(SolverContract):
    length: PipeLength
    internal_diameter: PipeDiameter
    darcy_friction_factor: Positive


class SeriesParallelInput(GasConditions):
    inlet_pressure: GasPressure
    atmosphere: GasPressure
    terminal_flow: GasVolumeRate
    upstream: GeneralFlowPipe
    parallel: tuple[GeneralFlowPipe, GeneralFlowPipe]
    downstream: GeneralFlowPipe


class SeriesParallelSolution(SolverContract):
    inlet_psia: float
    split_psia: float
    recombine_psia: float
    outlet_psia: float
    atmosphere_psia: float
    terminal_flow_mmscfd: float
    branch_flows_mmscfd: tuple[float, float]
    max_flow_balance_residual_mmscfd: float
    max_pressure_equation_residual_psi2: float


def solve_series_parallel(inputs: SeriesParallelInput) -> SeriesParallelSolution:
    """Solve a horizontal series-parallel network using Menon General Flow eq. 2.7."""
    atmosphere_psia = inputs.atmosphere.to_psia()
    inlet_psia = inputs.inlet_pressure.to_psia(inputs.atmosphere)
    demand = inputs.terminal_flow.to_standard_mmscfd()
    base_factor = 77.54 * inputs.base_temperature.to_rankine() / inputs.base_pressure.to_psia() / 1_000_000
    gas_term = inputs.gas_specific_gravity * inputs.gas_temperature.to_rankine() * inputs.compressibility

    def conductance(pipe: GeneralFlowPipe) -> float:
        return (base_factor * pipe.internal_diameter.to_inches()**2.5
                / sqrt(gas_term * pipe.length.to_miles() * pipe.darcy_friction_factor))

    first, left, right, last = map(conductance, (inputs.upstream, *inputs.parallel, inputs.downstream))
    parallel_gain = left + right
    left_flow, right_flow = demand * (left / parallel_gain), demand * (right / parallel_gain)
    inlet_squared = inlet_psia**2
    split_squared = inlet_squared - (demand / first)**2
    merge_squared = split_squared - (demand / parallel_gain)**2
    outlet_squared = merge_squared - (demand / last)**2
    if not 0 < outlet_squared < merge_squared < split_squared < inlet_squared or not isfinite(inlet_squared):
        raise ValueError("network pressure boundaries have no finite positive forward solution")

    split, merge, outlet = map(sqrt, (split_squared, merge_squared, outlet_squared))
    balance = abs(fsum((left_flow, right_flow, -demand)))
    residuals = []
    for pressure_in, pressure_out, flow, gain in (
        (inlet_psia, split, demand, first), (split, merge, left_flow, left),
        (split, merge, right_flow, right), (merge, outlet, demand, last),
    ):
        drop = (pressure_in - pressure_out) * (pressure_in + pressure_out)
        residual = abs(drop - (flow / gain)**2)
        if not isfinite(residual) or residual > _RESIDUAL_LIMIT * max(1, drop):
            raise ValueError("pipe pressure equation failed to close")
        residuals.append(residual)
    if not isfinite(balance) or balance > _RESIDUAL_LIMIT * max(1, demand):
        raise ValueError("split or recombination balance failed to close")
    return SeriesParallelSolution(
        inlet_psia=inlet_psia, split_psia=split, recombine_psia=merge, outlet_psia=outlet,
        atmosphere_psia=atmosphere_psia, terminal_flow_mmscfd=demand, branch_flows_mmscfd=(left_flow, right_flow),
        max_flow_balance_residual_mmscfd=balance, max_pressure_equation_residual_psi2=max(residuals),
    )
