"""Intent draft schema. It represents hypotheses, not dispatch authority."""
from __future__ import annotations

from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from .tool_api import Domain, ID, StrictContract, Text, ToolName


class TargetObject(StrictContract):
    kind: Literal["pipe", "node", "valve", "service", "scenario", "plan", "document", "unknown"]
    object_id: ID | None
    label: Text | None


class TimeConstraint(StrictContract):
    as_of: AwareDatetime | None = None
    start: AwareDatetime | None = None
    end: AwareDatetime | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("time window start must not follow end")
        return self


class IntentDraft(StrictContract):
    request_id: ID
    domain: Domain
    question_type: Literal["explain", "retrieve", "query", "compare", "simulate", "suggest", "unknown"]
    targets: list[TargetObject] = Field(max_length=64)
    time: TimeConstraint
    region_id: ID | None
    region_text: Text | None
    candidate_tools: list[ToolName] = Field(max_length=16)
    requires_clarification: bool
    clarification_questions: list[Text] = Field(max_length=10)
    human_confirmation: Literal["required", "not_required", "undetermined"]

    @model_validator(mode="after")
    def clarification_is_explicit(self):
        if (self.domain == "unknown" or self.question_type == "unknown") and not self.requires_clarification:
            raise ValueError("unknown intent must remain a clarification")
        if self.requires_clarification and not self.clarification_questions:
            raise ValueError("clarification requires a question")
        if len(self.candidate_tools) != len(set(self.candidate_tools)):
            raise ValueError("candidate tool names must be unique")
        return self
