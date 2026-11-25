"""Actual loopback TCP; no external model, database or browser API interception."""
from __future__ import annotations
import json
import socket
import threading
import time
from pathlib import Path
import httpx
import pytest
import uvicorn
from src.gas_application.standalone import create_gas_app
from src.gas_application.service import GasFixtureService
from src.gas_application.agent import GasAgentService, GasAgentQueryRequest

DATASET = "syn-gas-network-html54-0.1.0"
QUESTION_CASES = [
    ("查看K-001管段的信息", "asset.query", "succeeded"),
    ("查看当前有哪些燃气事件", "event.query", "succeeded"),
    ("定位隔离事件关联的管段", "map.locate", "partial"),
    ("目前支持哪些CUV分析？", "capabilities.query", "succeeded"),
    ("解释这个隔离事件", "event.explain", "succeeded"),
]

@pytest.fixture
def tcp(monkeypatch, tmp_path):
    for key in tuple(__import__("os").environ):
        if key.startswith(("WATER_", "ALLOW_LOCAL_", "GAS_BFF_", "GAS_AGENT_")):
            monkeypatch.delenv(key)
    principals = {
        "test-reader": {"actor_id": "reader", "scope": "internal", "modes": ["synthetic"], "datasets": [DATASET]},
        "test-limited": {"actor_id": "limited", "scope": "internal", "modes": ["synthetic"], "datasets": []},
        "test-no-mode": {"actor_id": "no-mode", "scope": "internal", "modes": [], "datasets": [DATASET]},
    }
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps(principals))
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    service = GasFixtureService()
    sock = socket.socket(); sock.bind(("127.0.0.1", 0)); sock.listen()
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_gas_app(gas_service=service), log_level="warning", proxy_headers=False, loop="asyncio", http="h11"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True); thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline
        time.sleep(.02)
    with httpx.Client(base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=10) as client:
        yield client, service, tmp_path
    server.should_exit = True; thread.join(10); sock.close()
    assert not thread.is_alive()

@pytest.mark.parametrize("question,tool,status", QUESTION_CASES)
def test_five_real_tcp_tools(tcp, question, tool, status):
    client, _, _ = tcp
    response = client.post("/api/v1/gas/engineering/agent/query", headers={"X-Water-Token": "test-reader"}, json={"question": question})
    assert response.status_code == 200
    data = response.json()
    assert data["model_status"] == "unavailable"
    assert data["parser_source"] == "local_rule_not_llm"
    assert data["status"] == status
    result = data["tools"][0]
    assert result["tool_name"] == tool
    assert data["evidence_refs"]
    if tool == "asset.query":
        assert result["data"]["asset"]["asset_id"] == "syn-gas-pipe-K-001"
    if tool == "event.query":
        assert len(result["data"]["items"]) == 3
    if tool == "map.locate":
        assert len(result["data"]["asset_ids"]) == 9
        assert result["data"]["event_id"] == "syn-gas-event-isolation"
        assert result["data"]["coordinate_authorized"] is False
    if tool == "capabilities.query":
        assert {v["status"] for v in result["data"]["items"]} == {"ready_synthetic", "unsupported", "unavailable"}
    if tool == "event.explain":
        assert result["data"]["event"]["event_kind"] == "given_isolation"
        assert "不代表真实供气" in result["data"]["explanation"]

@pytest.mark.parametrize("token,status", [(None,401),("wrong",401),("test-limited",403),("test-no-mode",403)])
def test_tcp_acl_precedes_fixture_access(tcp, token, status, monkeypatch):
    from src.gas_application.model_provider import LocalGasModel
    def forbidden(cls):
        raise AssertionError("denied requests must not reach model")
    monkeypatch.setattr(LocalGasModel, "from_environment", classmethod(forbidden))
    client, service, tmp = tcp
    service.fixture_root = tmp / "missing-private-path"
    response = client.post("/api/v1/gas/engineering/agent/query", headers={"X-Water-Token": token} if token else {}, json={"question":"查询事件"})
    assert response.status_code == status
    assert "missing-private-path" not in response.text

def test_tcp_health_tool_readiness_and_fault_recovery(tcp):
    client, service, tmp = tcp
    headers = {"X-Water-Token": "test-reader"}
    health = client.get("/api/v1/gas/engineering/agent/health", headers=headers).json()
    assert health["model_status"] == "unavailable" and health["tools_status"] == "ready"
    service.fixture_root = tmp / "missing-private-path"
    response = client.post("/api/v1/gas/engineering/agent/query", headers=headers, json={"question":"查看事件"})
    assert response.status_code == 503
    assert "missing-private-path" not in response.text and "Traceback" not in response.text
    service.fixture_root = None
    assert client.post("/api/v1/gas/engineering/agent/query", headers=headers, json={"question":"查看事件"}).status_code == 200

@pytest.mark.parametrize("body,status", [
    ({"question":""},422), ({"question":"  "},422), ({"question":"x"*2001},422),
    ({"question":"查询","tool_hint":"valve.close"},422),
    ({"question":"查询","data_mode":"real"},422),
    ({"question":"查询","asset_id":"unknown"},404),
])
def test_tcp_invalid_query(tcp, body, status):
    client, _, _ = tcp
    assert client.post("/api/v1/gas/engineering/agent/query", headers={"X-Water-Token":"test-reader"}, json=body).status_code == status

@pytest.mark.parametrize("hint", [None,"asset.query","map.locate"])
def test_control_requests_never_execute(hint):
    service = GasAgentService(GasFixtureService())
    result = service.query(GasAgentQueryRequest(question="立即关闭阀门并执行现场操作", tool_hint=hint))
    assert result.intent == "clarify" and result.tools == []

def test_short_id_must_exist_and_ambiguous_or_unknown_not_invented():
    service = GasAgentService(GasFixtureService())
    assert service.query(GasAgentQueryRequest(question="查看K-999管段的信息")).status == "not_found"
    ambiguous = service.query(GasAgentQueryRequest(question="查看K-001和K-002管段的信息"))
    assert ambiguous.intent == "clarify" and ambiguous.error_code == "parameter_required"
    assert ambiguous.tools == [] and ambiguous.model_calls == 0


@pytest.mark.parametrize(("question", "expected_status"), [
    ("查询管段 K-999 的关联事件", "not_found"),
    ("解释管段 K-003 的故障事件 syn-gas-event-fault", "not_found"),
    ("解释管段 K-003 的隔离事件 syn-gas-event-isolation", "succeeded"),
])
def test_real_tcp_event_query_honors_all_requested_references(tcp, question, expected_status):
    client, _, _ = tcp
    response = client.post(
        "/api/v1/gas/engineering/agent/query", headers={"X-Water-Token":"test-reader"},
        json={"question": question},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == expected_status
    if expected_status == "not_found":
        assert body["tools"][0]["data"] == {} and body["evidence_refs"] == []
    else:
        event = body["tools"][0]["data"]["event"]
        assert event["event_id"] == "syn-gas-event-isolation" and len(event["asset_ids"]) == 9
