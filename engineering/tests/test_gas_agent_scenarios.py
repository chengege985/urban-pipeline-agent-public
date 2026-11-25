"""Reusable Chinese cases against real read-only fixture tools (model disabled)."""
import json
from pathlib import Path
import pytest
from src.gas_application.agent import GasAgentService,GasAgentQueryRequest
from src.gas_application.service import GasFixtureService

SCENARIOS=json.loads((Path(__file__).parent/"fixtures/gas_agent_zh_scenarios.json").read_text())["scenarios"]
SUCCESS=[s for s in SCENARIOS if s["execution"]=="both" and s["expected"]["http_status"]==200]

@pytest.mark.parametrize("case",SUCCESS,ids=lambda s:s["id"])
def test_chinese_rule_scenario(case,monkeypatch):
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE","disabled")
    result=GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(**case["input"]))
    expected=case["expected"]
    assert result.parser_source=="local_rule_not_llm" and result.model_calls==0
    assert result.elapsed_ms>=0
    if expected["tool"] is None:
        assert result.intent=="clarify" and result.tools==[]
    else:
        tool=result.tools[0]
        assert tool.tool_name==expected["tool"]
        for name,value in expected["parameters"].items():assert tool.parameters[name]==value
        if expected["evidence_required"]:assert result.evidence_refs
        if expected["ui_action"]=="not_found":assert tool.status=="not_found"
        if expected["ui_action"]=="event_list" and tool.parameters["asset_id"]:
            assert all(tool.parameters["asset_id"] in e["asset_ids"] for e in tool.data["items"])
        if expected["ui_action"]=="topology_9_assets":assert len(tool.data["asset_ids"])==9
        if expected["ui_action"]=="gis_unavailable":
            assert tool.data["coordinate_authorized"] is False
            assert tool.data["districts"]["status"]=="unavailable" and tool.data["districts"]["items"]==[]

def test_scenarios_include_faults_acl_and_no_real_sources():
    assert len(SCENARIOS)>=20
    assert {s.get("fault") for s in SCENARIOS} >= {"tcp_timeout","invalid_model_response","connection_refused"}
    assert {s["expected"]["http_status"] for s in SCENARIOS} >= {200,401,403,404,422}


@pytest.mark.parametrize(("question","reference"), [
    ("查询K-999管段关联事件", "K-999"),
    ("查询WX-999管段关联事件", "WX-999"),
    ("查询编号 syn-gas-event-unknown 的事件", "syn-gas-event-unknown"),
    ("查询阀门N999的关联事件", "N999"),
    ("解释隔离事件 syn-gas-event-unknown", "syn-gas-event-unknown"),
])
def test_unknown_event_query_scope_does_not_return_all_events(question, reference, monkeypatch):
    from src.gas_application.model_provider import LocalGasModel

    def unexpected_model(cls):
        pytest.fail("unknown references must be rejected before inference")

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(unexpected_model))
    result = GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question=question))
    assert result.status == "not_found"
    assert result.tools[0].data == {} and result.evidence_refs == []
    assert reference in result.tools[0].parameters.values()


@pytest.mark.parametrize("question", [
    "查这个管段的关联事件",
    "查询 K-001 和 K-002 管段关联事件",
    "查询 syn-gas-event-fault 和 syn-gas-event-observation 的记录",
])
def test_ambiguous_event_scope_asks_for_a_single_object(question, monkeypatch):
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    result = GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question=question))
    assert result.intent == "clarify" and result.status == "unavailable"
    assert result.tools == [] and result.model_calls == 0


@pytest.mark.parametrize(("question","expected_count"), [
    ("查看当前有哪些燃气事件", 3),
    ("查询 K-003 管段的关联事件", 2),
])
def test_registered_event_query_scope_preserved_when_model_omits_id(question, expected_count, monkeypatch):
    from src.gas_application.model_provider import GasModelPlan, LocalGasModel

    class Planner:
        calls = 0
        elapsed_ms = 0
        usage = {}

        def plan(self, question, asset_ids, event_ids):
            return GasModelPlan(tool="event.query")

        def explain(self, sentences):
            return sentences[0]

    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(lambda cls: Planner()))
    result = GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question=question))
    assert result.tools[0].status == "succeeded"
    records = result.tools[0].data["items"]
    assert len(records) == expected_count
    if expected_count == 2:
        assert all("syn-gas-pipe-K-003" in record["asset_ids"] for record in records)


def test_agent_reads_network_once_and_reloads_next_request(tmp_path, monkeypatch):
    import shutil
    from src.gas_application.adapter import fixture_root
    from src.gas_application.service import CASE_FILE, NETWORK_FILE, GasFixtureUnavailable

    for filename in (NETWORK_FILE, CASE_FILE):
        shutil.copyfile(fixture_root() / filename, tmp_path / filename)
    fixture = GasFixtureService(tmp_path)
    reads = []
    load = fixture._load_json

    def record_read(name):
        reads.append(name)
        return load(name)

    monkeypatch.setattr(fixture, "_load_json", record_read)
    agent = GasAgentService(fixture)
    assets, events, binding, scope = agent._context()
    assert (len(assets), len(events)) == (204, 3)
    assert scope.source_sha256 == binding.case_content_sha256
    assert reads == [NETWORK_FILE, CASE_FILE]
    (tmp_path / CASE_FILE).unlink()
    with pytest.raises(GasFixtureUnavailable):
        agent._context()

@pytest.mark.parametrize(("question", "reason"), [
    ("用 demo-v3 的试点监测比较 GAS-M0 的两个方案", "cross_dataset_unverified"),
    ("把试点历史回放的事件关联到当前管段", "cross_dataset_unverified"),
    ("根据这个案例推断现场供气恢复时长", "engineering_evidence_unavailable"),
])
def test_agent_refuses_unverified_associations_before_model(monkeypatch, question, reason):
    from src.gas_application.model_provider import LocalGasModel

    def model():
        pytest.fail("rejected evidence query reached the model")
    monkeypatch.setattr(LocalGasModel, "from_environment", model)
    agent = GasAgentService(GasFixtureService())
    result = agent.query(GasAgentQueryRequest(question=question, tool_hint="cuv.query"))
    assert result.status == "unavailable" and result.error_code == reason
    assert not result.tools and not result.evidence_refs and result.model_calls == 0
    assert result.evidence_scope.scope == "gas_m0_case"


def test_scoped_read_only_event_followups(monkeypatch):
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    service = GasAgentService(GasFixtureService())
    event = "syn-gas-event-isolation"
    pipe = "syn-gas-pipe-K-001"

    def query(question, **scope):
        return service.query(GasAgentQueryRequest(question=question, **scope))

    explains = query("解释这个隔离事件。", event_id=event)
    assert explains.tools[0].tool_name == "event.explain"
    assert explains.tools[0].data["event"]["event_id"] == event
    assert explains.evidence_refs
    pipes = query("受影响的管段有哪些？", event_id=event)
    assert pipes.tools[0].tool_name == "event.query"
    by_id = {asset.asset_id: asset for asset in service.fixture_service.asset_event_records()[0]}
    assert pipes.tools[0].data["related_pipe_ids"] == [
        asset_id for asset_id in explains.tools[0].data["event"]["asset_ids"]
        if by_id[asset_id].asset_type == "pipe"
    ]
    assert pipes.evidence_refs
    assert query("受影响的管段有哪些？").tools == []

    opens = query("打开K-001管段。")
    assert opens.tools[0].tool_name == "asset.query" and opens.tools[0].data["asset"]["asset_id"] == pipe
    linked = query("这个管段关联哪些事件？", asset_id=pipe)
    assert linked.tools[0].tool_name == "event.query" and linked.tools[0].data["asset_id"] == pipe
    assert all(pipe in item["asset_ids"] for item in linked.tools[0].data["items"])
    assert query("目前能做哪些CUV分析？").tools[0].tool_name == "capabilities.query"

    for unsafe in ("打开V-001阀门", "打开K-001管段并关闭阀门", "确认这个隔离事件"):
        refused = query(unsafe, event_id=event, asset_id=pipe)
        assert refused.tools == [] and refused.model_calls == 0
