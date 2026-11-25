"""Synthetic module contracts only; no app, DB, HTTP or model client is started."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.ai_application.contracts import IntentDraft as ExportedIntent
from src.ai_application.orchestration.ports import WORKFLOW_STAGES, WorkflowPorts
from src.ai_application.tool_adapters.ports import ControlledToolApi
from src.cuv_application.api_catalog import list_tool_definitions
from src.shared_contracts.intent import IntentDraft
from src.shared_contracts.knowledge import DocumentReference, RetrievedChunk, RetrievalRequest
from src.shared_contracts import tool_api
from src.shared_contracts.tool_api import ActorContext, ToolCall, ToolResult, validate_call_contract

ROOT = Path(__file__).resolve().parents[1]
OPENAPI = ROOT / "04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json"


def definition(name="get_run_report"):
    return next(d for d in list_tool_definitions() if d.tool_name == name)


def actor():
    return {"actor_id": "synthetic-reviewer", "scope": "internal", "policy_version": "test-policy-v1"}


def call_data(name="get_run_report"):
    d = definition(name)
    return {"request_id": "req-test", "actor_context": actor(), "domain": "water",
            "data_mode": "synthetic", "tool_name": name, "tool_version": d.tool_version,
            "snapshot_id": "snap-test", "run_id": "run-test",
            "input_schema": d.input_schema.model_dump(), "result_schema": d.result_schema.model_dump(),
            "parameters": {"run_id": "run-test"}, "confirmation_ref": None}


def intent_data():
    return {"request_id": "req-test", "domain": "water", "question_type": "query",
            "targets": [{"kind": "pipe", "object_id": "pipe-test", "label": None}],
            "time": {"as_of": None, "start": None, "end": None}, "region_id": None,
            "region_text": None, "candidate_tools": ["get_health_assessment"],
            "requires_clarification": False, "clarification_questions": [],
            "human_confirmation": "undetermined"}


def document_data():
    return {"document_id": "doc-test", "document_version": "v1", "corpus_id": "synthetic-corpus",
            "source_uri": "synthetic:test", "source_category": "synthetic", "data_mode": "synthetic",
            "sensitivity": "synthetic", "content_sha256": "a" * 64}


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_data():
    return {"chunk_id": "chunk-test-v1", "citation_id": "citation-test-v1", "evidence_id": "evidence:test",
            "document": document_data(), "original_text": "合成来源：服务尚未恢复。",
            "original_sha256": digest("合成来源：服务尚未恢复。"), "sent_text": "合成来源：",
            "sent_sha256": digest("合成来源："), "truncated": True}


def test_actor_context_cannot_accept_client_auth_or_acl_fields():
    for field, value in {"authenticated": True, "permissions": ["*"], "datasets": ["*"],
                         "token": "not-a-credential"}.items():
        with pytest.raises(ValidationError):
            ActorContext.model_validate({**actor(), field: value})


@pytest.mark.parametrize("bad", [True, 42, [], {}])
def test_strict_identity_and_classification_do_not_coerce(bad):
    payload = call_data(); payload["actor_context"]["actor_id"] = bad
    with pytest.raises(ValidationError):
        ToolCall.model_validate(payload)


@pytest.mark.parametrize("field,value", [("domain", "gas"), ("domain", "unknown"), ("data_mode", "unknown")])
def test_water_definition_does_not_adopt_gas_or_unknown(field, value):
    payload = call_data(); payload[field] = value
    with pytest.raises(ValueError):
        validate_call_contract(ToolCall.model_validate(payload), definition())


@pytest.mark.parametrize("field,value", [("tool_version", "wrong"), ("tool_name", "get_run")])
def test_wrong_tool_identity_rejected(field, value):
    payload = call_data(); payload[field] = value
    with pytest.raises(ValueError):
        validate_call_contract(ToolCall.model_validate(payload), definition())


def test_schema_identity_is_not_an_arbitrary_override():
    payload = call_data(); payload["result_schema"]["pointer"] = "#/components/schemas/RunView"
    with pytest.raises(ValueError):
        validate_call_contract(ToolCall.model_validate(payload), definition())


@pytest.mark.parametrize("name", ["submit_scenario_analysis", "submit_plan_comparison", "submit_health_assessment"])
def test_persistent_run_creation_requires_confirmation_reference(name):
    d = definition(name); call = ToolCall.model_validate(call_data(name))
    assert d.effect == "creates_run" and d.requires_confirmation
    with pytest.raises(ValueError):
        validate_call_contract(call, d)
    call.confirmation_ref = "server-confirmation-test"
    # Structural success does not invoke any port or prove this reference valid.
    assert validate_call_contract(call, d) is None
    assert d.binding_status == "existing_api_unbound"


def test_definition_cannot_downgrade_side_effect_confirmation():
    data = definition("submit_scenario_analysis").model_dump(); data["requires_confirmation"] = False
    with pytest.raises(ValidationError):
        tool_api.ToolDefinition.model_validate(data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_nested_parameters_rejected(value):
    data = call_data(); data["parameters"] = {"nested": [{"value": value}]}
    with pytest.raises(ValidationError):
        ToolCall.model_validate(data)


def result_data():
    call = call_data()
    return {k: call[k] for k in ["request_id", "actor_context", "domain", "data_mode", "tool_name",
                                 "tool_version", "snapshot_id", "run_id", "result_schema"]} | {
        "model_version": None, "evidence_refs": [], "limitations": ["Synthetic data are incomplete."],
        "status": "partial", "error_code": None,
        "result": {"service_recovery_hours": None, "state": "unknown", "reachable": None}}


def test_partial_unknown_and_null_remain_exact_values():
    result = ToolResult.model_validate(result_data())
    assert result.model_dump()["result"] == {"service_recovery_hours": None, "state": "unknown", "reachable": None}
    assert result.model_version is None and result.status == "partial"


@pytest.mark.parametrize("status", ["partial", "insufficient_data", "unsupported"])
def test_incomplete_result_requires_explicit_limitations(status):
    data = result_data(); data.update(status=status, error_code="unsupported" if status == "unsupported" else None,
                                     limitations=[])
    with pytest.raises(ValidationError):
        ToolResult.model_validate(data)


@pytest.mark.parametrize("status", ["failed", "unsupported"])
def test_failed_result_requires_explicit_code(status):
    data = result_data(); data["status"] = status
    with pytest.raises(ValidationError):
        ToolResult.model_validate(data)


def test_unknown_intent_stays_clarification_not_dispatch_authority():
    data = intent_data(); data["domain"] = "unknown"
    with pytest.raises(ValidationError):
        IntentDraft.model_validate(data)
    data.update(requires_clarification=True, clarification_questions=["请确认燃气还是供水。"])
    draft = IntentDraft.model_validate(data)
    assert draft.domain == "unknown" and draft.human_confirmation == "undetermined"
    assert ExportedIntent is IntentDraft


@pytest.mark.parametrize("candidate", ["open_live_valve", "write_database", "read_any_dataset"])
def test_intent_cannot_name_undeclared_high_privilege_tool(candidate):
    data = intent_data(); data["candidate_tools"] = [candidate]
    with pytest.raises(ValidationError):
        IntentDraft.model_validate(data)


def test_intent_time_requires_timezone_and_consistent_window():
    data = intent_data(); data["time"]["as_of"] = "2026-11-04T08:00:00"
    with pytest.raises(ValidationError):
        IntentDraft.model_validate_json(json.dumps(data))
    data["time"] = {"start": "2026-11-04T09:00:00+08:00", "end": "2026-11-04T08:00:00+08:00"}
    with pytest.raises(ValidationError):
        IntentDraft.model_validate_json(json.dumps(data))
    data["time"] = {"as_of": "2026-11-04T08:00:00+08:00"}
    assert IntentDraft.model_validate_json(json.dumps(data)).time.as_of.utcoffset().total_seconds() == 28800


@pytest.mark.parametrize("sensitivity", ["synthetic", "public", "unknown"])
def test_internal_document_cannot_be_downgraded(sensitivity):
    data = document_data(); data.update(source_category="authorized_internal", sensitivity=sensitivity)
    with pytest.raises(ValidationError):
        DocumentReference.model_validate(data)


def test_query_classification_is_required_and_independent_of_corpus():
    data = {"request_id": "req-test", "actor_context": actor(), "question": "自由文本",
            "allowed_corpus_ids": ["synthetic-corpus"], "limit": 5}
    with pytest.raises(ValidationError):
        RetrievalRequest.model_validate(data)
    request = RetrievalRequest.model_validate({**data, "question_sensitivity": "unknown"})
    assert request.question_sensitivity == "unknown"
    assert RetrievalRequest.model_validate({**data, "question_sensitivity": "unknown",
                                           "allowed_corpus_ids": []}).allowed_corpus_ids == []


def test_chunk_hashes_validate_actual_source_and_sent_text():
    chunk = RetrievedChunk.model_validate(chunk_data())
    assert chunk.original_sha256 == digest(chunk.original_text)
    assert chunk.sent_sha256 == digest(chunk.sent_text)
    for key in ["original_sha256", "sent_sha256"]:
        data = chunk_data(); data[key] = "f" * 64
        with pytest.raises(ValidationError):
            RetrievedChunk.model_validate(data)


def test_excerpt_must_be_prefix_and_truncation_exact():
    data = chunk_data(); data["truncated"] = False
    with pytest.raises(ValidationError):
        RetrievedChunk.model_validate(data)
    data = chunk_data(); data.update(sent_text="服务已经恢复。", sent_sha256=digest("服务已经恢复。"))
    with pytest.raises(ValidationError):
        RetrievedChunk.model_validate(data)


def test_source_version_is_required_not_silently_invented():
    data = document_data(); data.pop("document_version")
    with pytest.raises(ValidationError):
        DocumentReference.model_validate(data)


def test_catalogue_existing_paths_and_schema_references_match_frozen_openapi():
    api = json.loads(OPENAPI.read_text(encoding="utf-8"))
    for item in list_tool_definitions():
        if item.http_path is not None:
            operation = api["paths"][item.http_path][item.http_method.lower()]
            if item.input_schema.document == "platform_openapi":
                assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"] == item.input_schema.pointer
            if item.result_schema.document == "platform_openapi":
                expected = operation["responses"]["202" if item.effect == "creates_run" else "200"]["content"]["application/json"]["schema"]["$ref"]
                assert expected == item.result_schema.pointer
        for ref in (item.input_schema, item.result_schema):
            name = ref.pointer.rsplit("/", 1)[1]
            if ref.document == "platform_openapi":
                assert name in api["components"]["schemas"]
            else:
                assert hasattr(tool_api, name)


def test_catalogue_does_not_publish_unimplemented_gas_or_asset_as_available():
    assert definition("get_gas_assessment").binding_status == "unavailable"
    assert definition("get_asset").binding_status == "adapter_required"
    assert definition("get_data_completeness").binding_status == "adapter_required"
    assert definition("get_agent_run_evidence").binding_status == "adapter_required"
    assert all(d.binding_status != "registered" for d in list_tool_definitions())
    gas = call_data("get_gas_assessment"); gas["domain"] = "gas"
    with pytest.raises(ValueError):
        validate_call_contract(ToolCall.model_validate(gas), definition("get_gas_assessment"))


def test_catalogue_instances_cannot_mutate_global_contract():
    first = list_tool_definitions(); first[0].domains.append("gas")
    assert definition("get_dataset_view").domains == ["water"]


def test_ports_are_unbound_and_workflow_gates_precede_calls():
    assert getattr(ControlledToolApi, "_is_protocol", False)
    assert WORKFLOW_STAGES.index("authorization_and_parameters") < WORKFLOW_STAGES.index("retrieval_or_controlled_tool")
    assert set(WorkflowPorts.__dataclass_fields__) == {"intent", "retrieval", "tools"}
