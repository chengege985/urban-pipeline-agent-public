from __future__ import annotations

import json

import httpx
import pytest
from pydantic import ValidationError

from src.gas_application.agent import GasAgentQueryRequest, GasAgentService
from src.gas_application.cuv_collaboration import GasCuvCollaborationRequest, GasCuvCollaborationStore
from src.gas_application.model_provider import LocalGasModel
from src.gas_application.service import GasFixtureService, GasFixtureUnavailable


FAULT = "syn-gas-event-fault"
ISOLATION = "syn-gas-event-isolation"
K001 = "syn-gas-pipe-K-001"
K003 = "syn-gas-pipe-K-003"
AT_START = "2026-11-18T01:12:00Z"
AT_ISOLATION = "2026-11-18T01:42:00Z"


@pytest.fixture
def service(monkeypatch):
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    return GasAgentService(GasFixtureService())


def test_selected_event_asset_and_clock_scope_uses_historical_monitoring(service):
    result = service.query(GasAgentQueryRequest(
        question="比较隔离与恢复方案的推演结果", event_id=ISOLATION,
        asset_id=K003, cuv_at="2026-11-18T09:42:00+08:00",
    ))
    assert result.intent == "cuv_query" and result.status == "partial"
    assert result.parser_source == "local_rule_not_llm" and result.model_calls == 0
    assert len(result.tools) == 1
    tool = result.tools[0]
    assert tool.tool_name == "cuv.query"
    assert tool.parameters == {"asset_id": K003, "event_id": ISOLATION, "cuv_at": AT_ISOLATION}
    data = tool.data
    assert data["context"]["at"] == AT_ISOLATION
    assert [item["event_id"] for item in data["event_evidence"]] == [ISOLATION]
    assert data["monitoring_evidence"]["as_of"] == AT_START
    channels = data["monitoring_evidence"]["channels"]
    assert [item["asset_id"] for item in channels] == [K003]
    assert channels[0]["transition"]["missing_slots"] == ["2026-11-18T01:11:00Z", AT_START]
    assert len(data["plans"]) == 2
    staged, hold = data["plans"]
    assert [p["phase"]["kind"] for p in (staged, hold)] == ["isolate", "isolate"]
    assert [len(p["phase"]["affected_service_ids"]) for p in (staged, hold)] == [10, 10]
    assert [p["affected_service_hours"] for p in (staged, hold)] == [35, 37]
    assert [p["topology_restoration_hours"] for p in (staged, hold)] == [5, 4]
    assert all(p["selected_asset_blocked"] is True for p in data["plans"])
    assert all(metric["status"] == "unknown" and metric["value"] is None for metric in data["unknown_metrics"])
    assert any(ref.startswith("gas-event:" + ISOLATION + ":sha256:") for ref in result.evidence_refs)
    assert any(ref.startswith("gas-monitoring-row:") for ref in result.evidence_refs)
    assert all(word in data["summary"] for word in ("35.0", "37.0", "完整合成分析窗", "保持未知"))


def test_explicit_fault_context_is_preserved_despite_generic_isolation_word(service):
    result = service.query(GasAgentQueryRequest(
        question="比较隔离与恢复方案", event_id=FAULT, asset_id=K001, cuv_at=AT_START,
    ))
    data = result.tools[0].data
    assert result.status == "partial"
    assert data["context"]["event_id"] == FAULT
    assert [item["asset_id"] for item in data["monitoring_evidence"]["channels"]] == [K001]
    assert [item["event_id"] for item in data["event_evidence"]] == [FAULT]
    assert all(p["phase"]["kind"] == "fault" for p in data["plans"])


def test_asset_alias_uses_first_associated_replay_time_without_guessing_an_event(service):
    result = service.query(GasAgentQueryRequest(question="K-003 的推演对比"))
    assert result.status == "partial"
    assert result.tools[0].data["context"]["at"] == AT_ISOLATION
    assert result.tools[0].data["context"]["asset_id"] == K003
    assert result.tools[0].data["context"]["event_id"] is None


@pytest.mark.parametrize("scope", [
    {"event_id": FAULT, "asset_id": K003, "cuv_at": AT_START},
    {"event_id": ISOLATION, "asset_id": K003, "cuv_at": AT_START},
    {"event_id": ISOLATION, "asset_id": K003, "cuv_at": "2026-11-18T01:43:00Z"},
    {"asset_id": K003, "cuv_at": AT_START},
    {"question": "K-999 的推演"},
    {"question": "K-001 和 K-003 的推演比较"},
    {"question": f"比较 {FAULT} 的推演", "event_id": ISOLATION},
])
def test_bad_context_is_rejected_before_model_or_human_projection(service, monkeypatch, scope):
    def forbidden(*args, **kwargs):
        pytest.fail("Out-of-scope CUV query reached Qwen or personal ranking")

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(forbidden))
    payload = GasAgentQueryRequest(**({"question": "比较推演", "tool_hint": "cuv.query"} | scope))
    result = service.query(payload, cuv_comparison=forbidden)
    assert result.status == "not_found" and result.model_calls == 0
    assert result.evidence_refs == [] and result.tools[0].data == {}


@pytest.mark.parametrize("clock", [True, 250000, "250000", "2026-11-18T01:42:00"])
def test_cuv_clock_requires_an_explicit_timezone(clock):
    with pytest.raises(ValidationError):
        GasAgentQueryRequest(question="比较推演", cuv_at=clock)


def test_fixture_failure_has_no_apparent_comparison(service, monkeypatch):
    def unavailable(**kwargs):
        raise GasFixtureUnavailable("sensitive fixture path")

    monkeypatch.setattr(service.fixture_service, "cuv_replay", unavailable)
    result = service.query(GasAgentQueryRequest(question="比较推演"))
    assert result.status == "unavailable"
    assert result.tools[0].data == {}
    assert "sensitive fixture path" not in result.model_dump_json()


@pytest.mark.parametrize("question", ["执行关闭阀门", "调整目标后重算推演", "确认并重算"])
def test_hint_cannot_request_a_cuv_mutation(service, monkeypatch, question):
    def forbidden(*args, **kwargs):
        pytest.fail("Mutation-shaped request reached optional evidence/model")

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(forbidden))
    result = service.query(GasAgentQueryRequest(question=question, tool_hint="cuv.query"), cuv_comparison=forbidden)
    assert result.intent == "clarify" and result.tools == []


def _mock_transport(monkeypatch, plan, selection):
    messages = []

    def handle(request):
        messages.append(json.loads(request.content))
        content = plan if len(messages) == 1 else selection
        return httpx.Response(200, json={
            "model": "Qwen3.8-27B", "choices": [{"message": {"content": json.dumps(content)}}],
        })

    model = LocalGasModel("http://127.0.0.1:18096", "synthetic-key-only", transport=httpx.MockTransport(handle))
    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: model))
    return messages


@pytest.mark.parametrize(("plan", "selection", "source"), [
    ({"tool": "cuv.engineering.query"}, {"sentence_ids": [1]}, "qwen_intent"),
    ({"tool": "cuv.query"}, {"sentence_ids": [1]}, "local_rule_not_llm"),
    ({"tool": "cuv.engineering.query"}, {"sentence_ids": [1], "pressure_kpa": 999}, "local_rule_not_llm"),
])
def test_engineering_questions_use_only_registered_model_evidence(service, monkeypatch, plan, selection, source):
    calls = _mock_transport(monkeypatch, plan | {"asset_id": None, "event_id": None}, selection)
    request = GasAgentQueryRequest(question="工程模型现在能计算实际压力和流量吗？")
    body = service.query(request)
    assert body.intent == "cuv_engineering_query" and body.parser_source == source
    assert body.tools[0].engineering_assessment.capabilities[0].status == "ready_synthetic"
    assert all(item.status == "blocked" for item in body.tools[0].engineering_assessment.capabilities[1:])
    assert body.tools[0].tool_name == "cuv.engineering.query"
    assert "pressure_kpa" not in body.model_dump_json() and "plans" not in body.tools[0].data
    if source == "qwen_intent":
        assert len(calls) == 2 and body.model_calls == 2
        assert "稳态压力与流量未就绪" in body.explanation
        assert "瞬态恢复与现场时长校核未就绪" in body.explanation
    else:
        assert body.error_code and body.explanation is None


@pytest.mark.parametrize(("plan", "selection", "expected"), [
    ("cuv.benchmark.query", {"sentence_ids": [1, 2]}, "qwen_intent"),
    ("cuv.query", {"sentence_ids": [1]}, "local_rule_not_llm"),
    ("cuv.benchmark.query", {"sentence_ids": [1], "fabricated_flow": 999}, "local_rule_not_llm"),
])
def test_qwen_benchmark_stays_on_solver_results(service, monkeypatch, plan, selection, expected):
    calls = _mock_transport(monkeypatch, {"tool": plan, "asset_id": None, "event_id": None}, selection)
    result = service.query(GasAgentQueryRequest(question="复算公开 Weymouth 基准并比较两案。"))
    assert result.status == "succeeded" and result.parser_source == expected
    assert result.tools[0].tool_name == "cuv.benchmark.query"
    assert result.tools[0].benchmark_report.progress.benchmark_status == "benchmark_passed"
    assert "fabricated_flow" not in result.model_dump_json()
    if expected == "qwen_intent":
        assert len(calls) == result.model_calls == 2
        assert "163.255858 MMSCFD" in result.explanation and "1.647420 psia" in result.explanation
        assert result.evidence_scope.source_sha256 in result.explanation
    else:
        assert result.explanation is None and result.error_code


def test_qwen_selection_keeps_grounded_comparison_and_excludes_feedback(service, monkeypatch):
    store = GasCuvCollaborationStore()
    report = service.fixture_service.cuv_replay()
    initial = store.read("test-operator", report)
    feedback = "synthetic-only note excluded from Qwen"
    store.confirm("test-operator", report, GasCuvCollaborationRequest(
        expected_revision=initial.revision, input_sha256=initial.input_sha256,
        confirmed=True, feedback=feedback, criteria={
            "objective": "min_topology_restoration_hours",
            "max_peak_affected_services": 10, "restore_by_hours": 4,
        },
    ))
    calls = _mock_transport(monkeypatch, {"tool": "cuv.query", "asset_id": K003, "event_id": ISOLATION},
                            {"sentence_ids": [0]})
    req = GasAgentQueryRequest(question="解释这两案取舍", event_id=ISOLATION, asset_id=K003, cuv_at=AT_ISOLATION)
    result = service.query(req, cuv_comparison=lambda replay: store.confirmed_comparison("test-operator", replay))
    assert result.model_calls == 2 and result.parser_source == "qwen_intent"
    assert result.explanation_source == "qwen_evidence_selection"
    assert "35.0" in result.explanation and "37.0" in result.explanation
    assert "推演配置 SHA-256" in result.explanation and "保持未知" in result.explanation
    confirmed = result.tools[0].data["confirmed_comparison"]
    assert set(confirmed) == {"revision", "input_sha256", "criteria", "comparison"}
    assert confirmed["revision"] == 1
    assert confirmed["comparison"]["leading_plan_ids"] == ["syn-gas-cuv-plan-hold-isolation"]
    assert feedback not in result.model_dump_json() and feedback not in json.dumps(calls)
    plan_prompt = json.loads(calls[0]["messages"][1]["content"])
    assert plan_prompt["allowed_asset_ids"] == [K003] and plan_prompt["allowed_event_ids"] == [ISOLATION]

    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    other = service.query(req, cuv_comparison=lambda replay: store.confirmed_comparison("other-operator", replay))
    assert other.tools[0].data["confirmed_comparison"] is None


def test_invalid_model_plan_downgrades_to_same_cuv_scope(service, monkeypatch):
    _mock_transport(monkeypatch, {"tool": "cuv.query", "event_id": FAULT, "asset_id": K003}, {})
    result = service.query(GasAgentQueryRequest(
        question="比较推演", event_id=ISOLATION, asset_id=K003, cuv_at=AT_ISOLATION,
    ))
    assert result.status == "partial" and result.parser_source == "local_rule_not_llm"
    assert result.error_code == "model_event_not_authorized"
    assert result.tools[0].data["context"]["event_id"] == ISOLATION
