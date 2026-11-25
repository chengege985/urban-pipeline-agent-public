"""Normalize explicit gas benchmark quantities to a single USCS solver basis."""

from __future__ import annotations

from math import isfinite
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

# NIST SP 811 and the international foot/inch definitions.
KPA_PER_PSI = 6.894757293168
MM_PER_INCH = 25.4
KM_PER_MILE = 1.609344
M3_PER_FT3 = 0.3048**3

Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Efficiency = Annotated[float, Field(gt=0, le=1, allow_inf_nan=False)]
Finite = Annotated[float, Field(allow_inf_nan=False)]


class SolverContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class GasPressure(SolverContract):
    value: Finite
    unit: Literal["psia", "psig", "kPa_abs", "kPa_gauge"]

    def to_psia(self, atmosphere: GasPressure | None = None) -> float:
        psi = self.value / KPA_PER_PSI if self.unit.startswith("kPa") else self.value
        if self.unit in ("psig", "kPa_gauge"):
            if atmosphere is None or atmosphere.unit not in ("psia", "kPa_abs"):
                raise ValueError("gauge pressure requires a separate absolute atmospheric pressure")
            psi += atmosphere.to_psia()
        if not isfinite(psi) or psi <= 0:
            raise ValueError("absolute pressure must be finite and positive")
        return psi


class GasTemperature(SolverContract):
    value: Finite
    unit: Literal["degR", "degF", "K", "degC"]

    def to_rankine(self) -> float:
        if self.unit == "degF":
            rankine = self.value + 459.67
        elif self.unit == "degC":
            rankine = (self.value + 273.15) * 1.8
        elif self.unit == "K":
            rankine = self.value * 1.8
        else:
            rankine = self.value
        if not isfinite(rankine) or rankine <= 0:
            raise ValueError("absolute temperature must be finite and positive")
        return rankine


class GasVolumeRate(SolverContract):
    value: Positive
    unit: Literal["million_ft3/day", "m3/day"]
    basis: Literal["standard", "actual"]

    def to_standard_mmscfd(self) -> float:
        if self.basis != "standard":
            raise ValueError("actual volumetric flow requires P/T/Z correction before using a standard-flow boundary")
        return self.value if self.unit == "million_ft3/day" else self.value / (1_000_000 * M3_PER_FT3)


class PipeDiameter(SolverContract):
    value: Positive
    unit: Literal["in", "mm"]

    def to_inches(self) -> float:
        return self.value if self.unit == "in" else self.value / MM_PER_INCH


class PipeLength(SolverContract):
    value: Positive
    unit: Literal["mi", "km"]

    def to_miles(self) -> float:
        return self.value if self.unit == "mi" else self.value / KM_PER_MILE


class GasConditions(SolverContract):
    gas_specific_gravity: Positive
    compressibility: Positive
    base_pressure: GasPressure
    base_temperature: GasTemperature
    gas_temperature: GasTemperature
