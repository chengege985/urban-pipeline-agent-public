from __future__ import annotations

import json

from fastapi.testclient import TestClient

from src.gas_application.standalone import create_gas_app

DATASET = "syn-gas-network-html54-0.1.0"


def _principal(*, datasets: list[str] | None = None) -> str:
    return json.dumps({
        "reader": {
            "actor_id": "reader",
            "scope": "internal",
            "modes": ["synthetic"],
            "datasets": datasets if datasets is not None else [DATASET],
            "permissions": [],
        }
    })


def _env(monkeypatch, **values):
    names = [
        "WATER_API_TOKEN", "WATER_PRINCIPALS", "WATER_RUNTIME_MODE",
        "WATER_BIND_HOST", "ALLOW_LOCAL_SYNTHETIC_ANONYMOUS",
        "WATER_CORS_ORIGINS", "WATER_TRUSTED_HOSTS",
    ]
    for name in names:
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_gas_agent_requires_acl_before_reading_tools(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    with TestClient(create_gas_app()) as client:
        assert client.get("/api/v1/gas/engineering/agent/health").status_code == 401
        assert client.post(
            "/api/v1/gas/engineering/agent/query",
            json={"question": "能力状态"},
        ).status_code == 401

    _env(monkeypatch, WATER_PRINCIPALS=_principal(datasets=[]))
    with TestClient(create_gas_app()) as client:
        response = client.post(
            "/api/v1/gas/engineering/agent/query",
            json={"question": "能力状态"},
            headers={"X-Water-Token": "reader"},
        )
    assert response.status_code == 403


def test_gas_agent_local_rule_queries_are_structured_and_read_only(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    with TestClient(create_gas_app()) as client:
        headers = {"X-Water-Token": "reader", "X-Water-Client": "unified-react"}
        health = client.get("/api/v1/gas/engineering/agent/health", headers=headers)
        assert health.status_code == 200
        assert health.json()["model_status"] == "unavailable"

        event = client.post(
            "/api/v1/gas/engineering/agent/query",
            headers=headers,
            json={"question": "查看 syn-gas-event-isolation 关联资产"},
        )
        assert event.status_code == 200
        body = event.json()
        assert body["parser_source"] == "local_rule_not_llm"
        assert body["model_status"] == "unavailable"
        assert body["status"] == "succeeded"
        assert body["tools"][0]["tool_name"] == "event.query"
        event_data = body["tools"][0]["data"]["event"]
        assert len(event_data["asset_ids"]) == 9
        assert event_data["validation_status"] == "not_business_validated"

        asset = client.post(
            "/api/v1/gas/engineering/agent/query",
            headers=headers,
            json={
                "question": "查询管段",
                "tool_hint": "asset.query",
                "asset_id": "syn-gas-pipe-K-001",
            },
        )
        assert asset.status_code == 200
        assert asset.json()["tools"][0]["data"]["asset"]["asset_id"] == "syn-gas-pipe-K-001"

        blocked = client.post(
            "/api/v1/gas/engineering/agent/query",
            headers=headers,
            json={"question": "请关闭阀门并执行现场操作"},
        )
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "unavailable"
        assert blocked.json()["tools"] == []


def test_gas_agent_rejects_unknown_explicit_objects_without_enumeration(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    with TestClient(create_gas_app()) as client:
        response = client.post(
            "/api/v1/gas/engineering/agent/query",
            headers={"X-Water-Token": "reader"},
            json={"question": "查询资产", "asset_id": "not-authorized-or-known"},
        )
    assert response.status_code == 404
