"""Versioned tool envelopes and a transport-independent controlled API port.

All fields are descriptive claims until validated against trusted platform
context. Constructing ActorContext or passing validate_call_contract grants no
permissions. This module performs no I/O and does not contain business formulas.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

Text = Annotated[str, Field(min_length=1, max_length=512)]
ID = Annotated[str, Field(min_length=1, max_length=128, pattern=r"^[^\s/\\]+$")]
Domain = Literal["water", "gas", "unknown"]
DataMode = Literal["synthetic", "fixture_replay", "real", "unknown"]
ToolName = Literal[
    "get_dataset_view", "get_health_assessment", "get_cuv_capabilities",
    "get_run", "get_run_report", "get_agent_run_evidence",
    "submit_scenario_analysis", "submit_plan_comparison", "submit_health_assessment",
    "check_timeline", "evaluate_timeline",
    "get_asset", "get_data_completeness", "get_gas_assessment",
]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True)


class SchemaReference(StrictContract):
    document: Literal["platform_openapi", "module_contracts"]
    pointer: Annotated[str, Field(pattern=r"^#/(components/schemas|\$defs)/[A-Za-z_][A-Za-z0-9_]*$")]


class ActorContext(StrictContract):
    """Server-associated metadata, never an authorization token or client ACL."""
    actor_id: ID
    scope: Literal["internal", "competition"]
    policy_version: Text


class EvidenceReference(StrictContract):
    evidence_id: Text
    source_kind: Literal["snapshot", "run", "tool", "document"]
    source_id: Text
    source_version: Text | None
    content_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")] | None


class ToolDefinition(StrictContract):
    tool_name: ToolName
    tool_version: Text
    domains: list[Literal["water", "gas"]]
    input_schema: SchemaReference
    result_schema: SchemaReference
    effect: Literal["read_only", "creates_run", "requires_review"]
    requires_confirmation: bool
    # A route can exist without the new collaboration ToolApi adapter existing.
    binding_status: Literal["existing_api_unbound", "adapter_required", "unavailable"]
    http_method: Literal["GET", "POST"] | None
    http_path: Text | None
    limitations: list[Text]

    @model_validator(mode="after")
    def coherent(self):
        if len(self.domains) != len(set(self.domains)):
            raise ValueError("domains must be unique")
        if self.effect != "read_only" and not self.requires_confirmation:
            raise ValueError("side-effecting tools require explicit action or confirmation")
        if (self.http_method is None) != (self.http_path is None):
            raise ValueError("HTTP method and path must be supplied together")
        if self.binding_status == "existing_api_unbound" and self.http_path is None:
            raise ValueError("existing API descriptions require an actual path")
        return self


def _finite_json(value: JsonValue) -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON business values must be finite; use null for unknown")
    if isinstance(value, dict):
        for item in value.values():
            _finite_json(item)
    elif isinstance(value, list):
        for item in value:
            _finite_json(item)
    return value


class ToolCall(StrictContract):
    request_id: ID
    actor_context: ActorContext
    domain: Domain
    data_mode: DataMode
    tool_name: ToolName
    tool_version: Text
    snapshot_id: ID | None
    run_id: ID | None
    input_schema: SchemaReference
    result_schema: SchemaReference
    parameters: dict[str, JsonValue] = Field(max_length=64)
    # This reference is also untrusted until resolved by platform policy.
    confirmation_ref: ID | None = None

    @field_validator("parameters")
    @classmethod
    def finite_parameters(cls, value):
        return _finite_json(value)


class ToolResult(StrictContract):
    request_id: ID
    actor_context: ActorContext
    domain: Domain
    data_mode: DataMode
    tool_name: ToolName
    tool_version: Text
    snapshot_id: ID | None
    run_id: ID | None
    result_schema: SchemaReference
    model_version: Text | None
    evidence_refs: list[EvidenceReference]
    limitations: list[Text]
    status: Literal["queued", "running", "succeeded", "partial", "insufficient_data",
                    "not_applicable", "unsupported", "failed"]
    error_code: Text | None
    result: JsonValue

    @field_validator("result")
    @classmethod
    def finite_result(cls, value):
        return _finite_json(value)

    @model_validator(mode="after")
    def explicit_failure(self):
        if self.status in {"failed", "unsupported"} and self.error_code is None:
            raise ValueError("failed or unsupported results require error_code")
        if self.status in {"partial", "insufficient_data", "unsupported"} and not self.limitations:
            raise ValueError("limited results require explicit limitations")
        return self


def validate_call_contract(call: ToolCall, definition: ToolDefinition) -> None:
    """Check shape and capability compatibility only; never authorize or execute."""
    if (call.tool_name, call.tool_version) != (definition.tool_name, definition.tool_version):
        raise ValueError("tool identity/version mismatch")
    if call.input_schema != definition.input_schema or call.result_schema != definition.result_schema:
        raise ValueError("tool schema identity mismatch")
    if call.domain not in definition.domains or call.data_mode == "unknown":
        raise ValueError("domain or dataset classification requires clarification")
    if definition.binding_status == "unavailable":
        raise ValueError("tool is unavailable")
    if definition.requires_confirmation and call.confirmation_ref is None:
        raise ValueError("explicit user action or human confirmation reference required")


class ControlledToolApi(Protocol):
    """Future platform-bound adapter, injected by the integration layer.

    Each call must resolve current authenticated identity and ACLs, bind the
    requested snapshot/run/data mode, validate the referenced business schema,
    and verify any confirmation reference. Client-supplied ActorContext never
    grants access. A platform error must propagate, with no Demo fallback.
    No implementation is registered by this refactor.
    """
    def call(self, request: ToolCall) -> ToolResult: ...


class NoInput(StrictContract):
    """No parameters. Identity remains in the trusted transport boundary."""


class DatasetQuery(StrictContract):
    dataset_id: ID


class AssessmentQuery(StrictContract):
    snapshot_id: ID
    pipe_id: ID | None = None
    grade: Text | None = None
    limit: Annotated[int, Field(ge=1, le=500)] = 100
    offset: Annotated[int, Field(ge=0)] = 0


class RunQuery(StrictContract):
    run_id: ID


class UnspecifiedApiResult(StrictContract):
    """Placeholder only: the underlying API lacks a stable typed response.

    Not a substitute schema for authorization or UI consumption. A controlled
    integration PR must define the actual response before binding this tool.
    """
    body: JsonValue
