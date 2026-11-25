"""Validated synthetic-demo inputs shared by HTTP, templates and model tools.

This contract does not validate real hydraulic or engineering data.
"""
from __future__ import annotations
import math
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

NonNegative = Annotated[float, Field(ge=0, allow_inf_nan=False)]
Positive = Annotated[float, Field(gt=0, allow_inf_nan=False)]
Count = Annotated[int, Field(ge=0)]
Identifier = Annotated[str, Field(min_length=1)]

class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

class HealthOverride(StrictInput):
    material: Literal["PE", "球墨铸铁", "铸铁", "钢", "PVC", "未知"] | None = None
    installYear: Annotated[int, Field(ge=0, le=2026)] | None = None
    diameterMm: Positive | None = None
    depthM: Positive | None = None
    pressure: Literal["低压", "常压", "高压", "未知"] | None = None
    leakCount: Count | None = None
    repairCount: Count | None = None
    monitoring: Literal["正常", "关注", "异常", "未知"] | None = None

class PipeRecord(HealthOverride):
    id: Identifier
    name: str = ""
    fromNodeId: str = ""
    toNodeId: str = ""
    lastInspection: str = ""
    serviceIds: list[Identifier] = Field(default_factory=list)
    lengthM: NonNegative = 0
    note: str = ""

class DecisionConstraints(StrictInput):
    budgetYuan: NonNegative
    resourceCapacities: dict[Identifier, Count]
    confirmedDependencies: list[Identifier]

class TimelineSchedule(StrictInput):
    actionId: Identifier
    finishOffsetHours: NonNegative | None = None
    startOffsetHours: NonNegative | None = None
    durationHours: NonNegative | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.startOffsetHours is not None and self.durationHours is not None and not math.isfinite(self.startOffsetHours + self.durationHours):
            raise ValueError("开始时间与持续时间之和必须为有限数值")
        if self.startOffsetHours is not None and self.durationHours is not None and self.finishOffsetHours is not None:
            if abs(self.finishOffsetHours - (self.startOffsetHours + self.durationHours)) > 1e-9:
                raise ValueError("结束时间必须等于开始时间加持续时间")
        return self


class TimelineRequest(StrictInput):
    scenarioId: Identifier
    isolationPlanId: Identifier
    planId: Identifier
    targetServiceIds: list[Identifier] | None = None
    horizonHours: Annotated[float, Field(gt=0, le=720, allow_inf_nan=False)] = 8
    actionSchedule: list[TimelineSchedule] = Field(default_factory=list)


class AssistantContext(BaseModel):
    actionSchedule: list[TimelineSchedule] = Field(default_factory=list)
    # Older clients may include derived display objects. They are never treated as facts.
    model_config = ConfigDict(extra="ignore", strict=True)
    pipeId: Identifier | None = None
    scenarioId: Identifier | None = None
    isolationPlanId: Identifier | None = None
    override: HealthOverride = Field(default_factory=HealthOverride)
    constraints: DecisionConstraints | None = None
    planIds: list[Identifier] | None = None
    planId: Identifier | None = None
    targetServiceIds: list[Identifier] | None = None
    horizonHours: Annotated[float, Field(gt=0, le=720, allow_inf_nan=False)] | None = None


def validated_override(value: dict[str, Any] | None) -> dict[str, Any]:
    return HealthOverride.model_validate({} if value is None else value).model_dump(exclude_unset=True)


def validated_pipe(value: dict[str, Any]) -> dict[str, Any]:
    return PipeRecord.model_validate(value).model_dump()


def validated_constraints(value: dict[str, Any]) -> dict[str, Any]:
    return DecisionConstraints.model_validate(value).model_dump()


def frozen_context(value: dict[str, Any] | None) -> dict[str, Any]:
    supplied = dict(value or {})
    # Compatibility IDs only: ignore supplied assessments/impacts and recalculate server-side.
    for key, legacy in (("pipeId", "pipe"), ("scenarioId", "scenario")):
        if key not in supplied and isinstance(supplied.get(legacy), dict):
            supplied[key] = supplied[legacy].get("id")
    result = AssistantContext.model_validate(supplied).model_dump(exclude_none=True)
    result["override"] = validated_override(supplied.get("override"))
    return result
