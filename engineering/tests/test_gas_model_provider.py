"""Model transport doubles for security/contracts; NOT evidence of Qwen inference."""
from __future__ import annotations
import json
import httpx
import pytest
from src.gas_application.model_provider import LocalGasModel, ModelUnavailable, gateway_origin
from src.gas_application.agent import GasAgentService, GasAgentQueryRequest
from src.gas_application.service import GasFixtureService

@pytest.mark.parametrize("url",[
 "https://example.test/v1","http://localhost:30000","http://127.0.0.1:30000@external.test",
 "http://169.254.169.254:30000","http://127.0.0.1:30000/x","http://127.0.0.1:30000?q=x",
 "http://127.0.0.1:30000#x","http://127.0.0.1:80","http://127.0.0.1:999999",
])
def test_only_canonical_loopback_gateway(url):
    with pytest.raises(ModelUnavailable):
        gateway_origin(url)

def provider(handler):
    return LocalGasModel("http://127.0.0.1:18096","synthetic-key-only",transport=httpx.MockTransport(handler))

def completion(content, model="Qwen3.8-27B"):
    return httpx.Response(200,json={"model":model,"choices":[{"message":{"content":json.dumps(content)}}],"usage":{"prompt_tokens":4,"completion_tokens":3,"total_tokens":7}})

def test_plan_and_evidence_only_explanation(monkeypatch):
    calls=[]
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls)==1:return completion({"tool":"event.explain","event_id":"syn-gas-event-isolation","asset_id":None})
        return completion({"sentence_ids":[0,2]})
    model=provider(handler)
    monkeypatch.setattr(LocalGasModel,"from_environment",classmethod(lambda cls:model))
    result=GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question="解释隔离事件"))
    assert result.parser_source=="qwen_intent" and result.model_status=="ready"
    assert result.model_calls==2 and result.model_usage["total_tokens"]==14
    assert len(result.tools[0].data["event"]["asset_ids"])==9
    assert result.explanation_source=="qwen_evidence_selection"
    assert "syn-gas-event-isolation" in result.explanation
    assert all(item["stream"] is False for item in calls)

@pytest.mark.parametrize("bad_plan",[
 {"tool":"valve.close","asset_id":None,"event_id":None},
 {"tool":"asset.query","asset_id":"foreign-asset","event_id":None},
 {"tool":"event.query","asset_id":None,"event_id":"foreign-event"},
 {"tool":"event.query","asset_id":None,"event_id":None,"url":"http://external.test"},
])
def test_model_plan_cannot_expand_tools_or_objects(bad_plan):
    model=provider(lambda req:completion(bad_plan))
    with pytest.raises(ModelUnavailable):model.plan("synthetic question",["known-asset"],["known-event"])

@pytest.mark.parametrize("body",[{"sentence_ids":[100]},{"sentence_ids":[True]},{"sentence_ids":[]},{"sentence_ids":[0],"text":"真实风险99"}])
def test_model_cannot_add_narrative_or_new_facts(body):
    with pytest.raises(ModelUnavailable):
        provider(lambda req:completion(body)).explain(["合成拓扑，不代表真实供气。"])

def test_redirect_not_followed_and_errors_are_sanitized():
    requests=[]
    def handler(req):
        requests.append(str(req.url));return httpx.Response(302,headers={"Location":"https://external.test"},text="secret /workdata/path")
    with pytest.raises(ModelUnavailable,match="model_upstream_unavailable"):
        provider(handler).plan("问题",[],[])
    assert requests==["http://127.0.0.1:18096/v1/chat/completions"]

def test_timeout_explicitly_falls_back_to_real_tools(monkeypatch):
    def handler(req):raise httpx.ReadTimeout("sensitive /private/path")
    monkeypatch.setattr(LocalGasModel,"from_environment",classmethod(lambda cls:provider(handler)))
    result=GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question="查看K-001管段的信息"))
    assert result.model_status=="unavailable" and result.parser_source=="local_rule_not_llm"
    assert result.tools[0].data["asset"]["asset_id"]=="syn-gas-pipe-K-001"
    assert "/private" not in json.dumps(result.model_dump())

def test_control_request_does_not_call_model(monkeypatch):
    def forbidden(cls):raise AssertionError("model must not be called")
    monkeypatch.setattr(LocalGasModel,"from_environment",classmethod(forbidden))
    result=GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question="执行关闭阀门"))
    assert result.tools==[]

def test_key_file_and_production_gate(monkeypatch,tmp_path):
    key=tmp_path/"key";key.write_text("synthetic-test-only-key-value");key.chmod(0o600)
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE","local_qwen")
    monkeypatch.setenv("GAS_AGENT_GATEWAY_URL","http://127.0.0.1:18096")
    monkeypatch.setenv("GAS_AGENT_GATEWAY_KEY_FILE",str(key))
    monkeypatch.setenv("WATER_RUNTIME_MODE","controlled")
    assert LocalGasModel.from_environment().origin=="http://127.0.0.1:18096"
    key.chmod(0o644)
    with pytest.raises(ModelUnavailable):LocalGasModel.from_environment()
    key.chmod(0o600);monkeypatch.setenv("WATER_RUNTIME_MODE","production")
    with pytest.raises(ModelUnavailable):LocalGasModel.from_environment()

def test_unmentioned_event_requires_clarification_without_model(monkeypatch):
    def forbidden(cls):raise AssertionError("missing event must not be guessed")
    monkeypatch.setattr(LocalGasModel,"from_environment",classmethod(forbidden))
    result=GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question="解释这个事件"))
    assert result.parser_source=="local_rule_not_llm" and result.intent=="clarify"
    assert result.tools==[] and result.model_calls==0
    assert result.error_code=="parameter_required"

def test_model_clarification_runs_no_tool(monkeypatch):
    model=provider(lambda req:completion({"tool":"clarify"}))
    monkeypatch.setattr(LocalGasModel,"from_environment",classmethod(lambda cls:model))
    service=GasAgentService(GasFixtureService())
    def forbidden(*args,**kwargs):raise AssertionError("no tool before clarification")
    monkeypatch.setattr(service,"_query_local",forbidden)
    result=service.query(GasAgentQueryRequest(question="查看事件"))
    assert result.tools==[] and result.model_calls==1

def test_explicit_asset_limits_model_selection(monkeypatch):
    model=provider(lambda req:completion({"tool":"asset.query","asset_id":"syn-gas-pipe-K-002"}))
    monkeypatch.setattr(LocalGasModel,"from_environment",classmethod(lambda cls:model))
    result=GasAgentService(GasFixtureService()).query(GasAgentQueryRequest(question="查看管段",asset_id="syn-gas-pipe-K-001"))
    assert result.parser_source=="local_rule_not_llm"
    assert result.tools[0].data["asset"]["asset_id"]=="syn-gas-pipe-K-001"

@pytest.mark.parametrize("raw", [[],None,{"model":"Qwen3.8-27B","usage":[]},
                              {"model":"Qwen3.8-27B","usage":{},"choices":[]}])
def test_malformed_model_envelope_is_explicit_unavailable(raw):
    model=provider(lambda req:httpx.Response(200,json=raw))
    with pytest.raises(ModelUnavailable):model.plan("查询",[],[])
