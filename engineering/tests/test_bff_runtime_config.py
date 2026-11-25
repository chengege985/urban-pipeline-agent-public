from __future__ import annotations

import json

import pytest

from fastapi.testclient import TestClient

from src.gas_application.bff import SESSION_COOKIE
from src.gas_application.standalone import create_gas_app
from src.platform.openapi_export import build_openapi

DATASET = "syn-gas-network-html54-0.1.0"


def _principal():
    return json.dumps(
        {
            "reader": {
                "actor_id": "reader",
                "scope": "internal",
                "modes": ["synthetic"],
                "datasets": [DATASET],
                "permissions": [],
            }
        }
    )


def test_bff_runtime_acl_corruption_returns_sanitized_503_and_revokes_cookie(monkeypatch):
    monkeypatch.setenv("WATER_PRINCIPALS", _principal())
    monkeypatch.setenv("GAS_BFF_MODE", "development")
    monkeypatch.setenv("GAS_BFF_BOOTSTRAP_TOKEN", "bootstrap-only-server-secret")
    monkeypatch.setenv("GAS_BFF_ACTOR_ID", "reader")
    monkeypatch.setenv("WATER_RUNTIME_MODE", "local")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")

    with TestClient(create_gas_app(include_bff=True)) as client:
        created = client.post(
            "/bff/session",
            headers={"X-Gas-BFF-Bootstrap": "bootstrap-only-server-secret"},
        )
        assert created.status_code == 200
        stale_cookie = created.cookies[SESSION_COOKIE]
        assert client.get("/bff/gas/assets?limit=1").status_code == 200

        monkeypatch.setenv("WATER_PRINCIPALS", "{not-json")
        broken = client.get("/bff/gas/assets?limit=1")
        assert broken.status_code == 503
        assert "WATER_PRINCIPALS" not in broken.text
        assert "traceback" not in broken.text.lower()
        assert broken.headers["set-cookie"].startswith(f"{SESSION_COOKIE}=")
        assert "Max-Age=0" in broken.headers["set-cookie"]
        assert SESSION_COOKIE not in client.cookies

        monkeypatch.setenv("WATER_PRINCIPALS", _principal())
        assert client.get("/bff/gas/assets?limit=1",
                          headers={"Cookie": f"{SESSION_COOKIE}={stale_cookie}"}).status_code == 401
        assert client.get("/bff/gas/assets?limit=1").status_code == 401


def test_development_bff_routes_stay_out_of_formal_platform_contract(monkeypatch):
    monkeypatch.setenv("WATER_PRINCIPALS", _principal())
    monkeypatch.setenv("GAS_BFF_MODE", "development")
    monkeypatch.setenv("GAS_BFF_BOOTSTRAP_TOKEN", "test-only")
    monkeypatch.setenv("GAS_BFF_ACTOR_ID", "reader")
    monkeypatch.setenv("WATER_RUNTIME_MODE", "local")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")
    app = create_gas_app(include_bff=True)
    operations = {
        (path, method)
        for path, methods in app.openapi()["paths"].items() if path.startswith("/bff/")
        for method in methods
    }
    assert operations == {
        ("/bff/session", "post"),
        ("/bff/session", "get"),
        ("/bff/session", "delete"),
        ("/bff/gas/assets", "get"),
        ("/bff/gas/assets/{asset_id}", "get"),
        ("/bff/gas/events", "get"),
        ("/bff/gas/events/{event_id}", "get"),
    }
    assert not any(path.startswith("/bff/") for path in build_openapi()["paths"])


@pytest.mark.parametrize("mode", ["controlled", "staging", "production"])
def test_development_bff_cannot_be_enabled_in_formal_modes(monkeypatch, mode):
    monkeypatch.setenv("WATER_RUNTIME_MODE", mode)
    monkeypatch.setenv("WATER_PRINCIPALS", _principal())
    monkeypatch.setenv("GAS_BFF_MODE", "development")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")
    with pytest.raises(RuntimeError, match="local/development"):
        create_gas_app(include_bff=True)
    with TestClient(create_gas_app(include_bff=False)) as client:
        assert client.get("/bff/session").status_code == 404
