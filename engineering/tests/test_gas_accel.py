from __future__ import annotations
import json
import pytest
from fastapi.testclient import TestClient
from src.gas_application.standalone import create_gas_app

DATASET="syn-gas-network-html54-0.1.0"

def _env(monkeypatch, **values):
    names=["WATER_API_TOKEN","WATER_PRINCIPALS","WATER_RUNTIME_MODE","WATER_BIND_HOST","ALLOW_LOCAL_SYNTHETIC_ANONYMOUS","WATER_CORS_ORIGINS","WATER_TRUSTED_HOSTS","GAS_BFF_MODE","GAS_BFF_BOOTSTRAP_TOKEN","GAS_BFF_ACTOR_ID","GAS_BFF_ACTOR_SCOPE","GAS_BFF_SECURE_COOKIE"]
    for n in names: monkeypatch.delenv(n, raising=False)
    for n,v in values.items(): monkeypatch.setenv(n,v)

def _principal():
    return json.dumps({"reader":{"actor_id":"reader","scope":"internal","modes":["synthetic"],"datasets":[DATASET],"permissions":[]}})

def test_standalone_gas_does_not_bootstrap_water_store(tmp_path, monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    app=create_gas_app()
    assert not hasattr(app.state,"store")
    with TestClient(app) as client:
        assert client.get("/health").json()["bootstrap_mode"]=="gas_synthetic_only"
        assert client.get("/api/v1/gas/cases/syn-gas-case-K-001").status_code==401
        assert client.get("/api/v1/gas/cases/syn-gas-case-K-001",headers={"X-Water-Token":"reader"}).status_code==200

def test_capability_registry_is_explicit_about_unavailable_models(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    with TestClient(create_gas_app()) as client:
        response=client.get("/api/v1/gas/engineering/capabilities",headers={"X-Water-Token":"reader"})
    assert response.status_code==200
    body=response.json()
    assert body["data_mode"]=="synthetic"
    assert any(item["capability_id"]=="graph_connectivity" and item["status"]=="ready_synthetic" for item in body["items"])
    assert any(item["capability_id"]=="pir" and item["status"]=="unsupported" for item in body["items"])

def test_development_bff_uses_opaque_cookie_and_csrf(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader",
         WATER_RUNTIME_MODE="local", WATER_BIND_HOST="127.0.0.1")
    with TestClient(create_gas_app(include_bff=True)) as client:
        denied=client.post("/bff/session",headers={"X-Gas-BFF-Bootstrap":"wrong"})
        assert denied.status_code==401
        created=client.post("/bff/session",headers={"X-Gas-BFF-Bootstrap":"bootstrap-only-server-secret"})
        assert created.status_code==200
        assert "gas_bff_session=" in created.headers["set-cookie"]
        assert "bootstrap" not in created.text.lower()
        assert client.get("/bff/gas/assets?limit=1").status_code==200
        assert client.delete("/bff/session").status_code==403
        csrf=created.json()["csrf_token"]
        closed=client.delete("/bff/session",headers={"X-Gas-CSRF":csrf})
        assert closed.status_code==200
        assert client.get("/bff/gas/assets").status_code==401

def test_bff_configuration_fails_closed_without_local_mode(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader")
    with pytest.raises(RuntimeError, match="local/development"):
        create_gas_app(include_bff=True)

def test_bff_is_not_registered_in_production(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader",
         WATER_RUNTIME_MODE="production", WATER_BIND_HOST="127.0.0.1")
    with pytest.raises(RuntimeError, match="local/development"):
        create_gas_app(include_bff=True)

def test_bff_rechecks_current_acl_after_session_creation(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader",
         WATER_RUNTIME_MODE="local", WATER_BIND_HOST="127.0.0.1")
    with TestClient(create_gas_app(include_bff=True)) as client:
        created=client.post("/bff/session",headers={"X-Gas-BFF-Bootstrap":"bootstrap-only-server-secret"})
        assert created.status_code==200
        monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"revoked":{"actor_id":"other","scope":"internal","modes":["synthetic"],"datasets":[DATASET],"permissions":[]}}))
        assert client.get("/bff/gas/assets?limit=1").status_code in {401,403}
        assert client.get("/bff/gas/assets?limit=1").status_code==401

def test_bbox_rejects_out_of_range_and_preflight_rejects_origin(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), WATER_CORS_ORIGINS="https://allowed.example")
    with TestClient(create_gas_app()) as client:
        headers={"X-Water-Token":"reader"}
        assert client.get("/api/v1/gas/engineering/assets?bbox=-999,-999,999,999",headers=headers).status_code==422
        assert client.get("/api/v1/gas/engineering/assets?bbox=180,-90,180,90",headers=headers).status_code==200
        assert client.options("/api/v1/gas/engineering/assets",headers={"Origin":"https://evil.example","Access-Control-Request-Method":"GET"}).status_code==403


@pytest.mark.parametrize("method,path", [
    ("get", "/bff/session"),
    ("get", "/bff/gas/assets?limit=1"),
    ("get", "/bff/gas/assets/syn-gas-node-N53"),
    ("get", "/bff/gas/events?limit=1"),
    ("get", "/bff/gas/events/syn-gas-event-fault"),
    ("post", "/bff/session"),
    ("delete", "/bff/session"),
])
@pytest.mark.parametrize("header_name", ["Forwarded", "X-Forwarded-Port", "X-Forwarded-Prefix", "X-Real-IP"])
def test_every_bff_method_rejects_untrusted_forwarding_headers(monkeypatch, method, path, header_name):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader",
         WATER_RUNTIME_MODE="local", WATER_BIND_HOST="127.0.0.1")
    with TestClient(create_gas_app(include_bff=True)) as client:
        response = client.request(method, path, headers={header_name: "10.0.0.1"})
    assert response.status_code == 401
    assert "forwarded" not in response.text.lower() or response.json()["detail"] in {"BFF不接受客户端转发身份", "forwarded identity denied"}

def test_bff_malformed_principals_fail_closed_without_http_leak(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS="{", GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader",
         WATER_RUNTIME_MODE="local", WATER_BIND_HOST="127.0.0.1")
    with pytest.raises(RuntimeError, match="合法 JSON"):
        create_gas_app(include_bff=True)

def test_bbox_422_matches_validation_error_contract(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    with TestClient(create_gas_app()) as client:
        response = client.get(
            "/api/v1/gas/engineering/assets?bbox=-999,-999,999,999",
            headers={"X-Water-Token": "reader"},
        )
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], list)
    issue = body["detail"][0]
    assert set(issue) == {"field", "reason", "type"}
    assert issue["field"] == ["query", "bbox"]

def test_cors_allows_required_client_header_and_rejects_unknown_header(monkeypatch):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), WATER_CORS_ORIGINS="https://allowed.example")
    with TestClient(create_gas_app()) as client:
        allowed = client.options(
            "/api/v1/gas/engineering/assets",
            headers={
                "Origin": "https://allowed.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Water-Client, X-Water-Token",
            },
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == "https://allowed.example"
        assert "X-Water-Client" in allowed.headers["access-control-allow-headers"]
        assert "Origin" in allowed.headers.get("vary", "")
        denied = client.options(
            "/api/v1/gas/engineering/assets",
            headers={
                "Origin": "https://allowed.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Not-Allowed",
            },
        )
        assert denied.status_code in {400, 403}
        actual = client.get(
            "/api/v1/gas/cases/syn-gas-case-K-001",
            headers={"Origin": "https://allowed.example", "X-Water-Token": "reader", "X-Water-Client": "unified-react"},
        )
        assert actual.status_code == 200
        assert actual.headers["access-control-allow-origin"] == "https://allowed.example"
        unauthorized = client.get(
            "/api/v1/gas/cases/syn-gas-case-K-001",
            headers={"Origin": "https://allowed.example", "X-Water-Client": "unified-react"},
        )
        assert unauthorized.status_code == 401
        assert unauthorized.headers["access-control-allow-origin"] == "https://allowed.example"


@pytest.mark.parametrize("runtime_mode", ["production", "controlled", "staging"])
def test_bff_is_fail_closed_in_every_formal_runtime_mode(monkeypatch, runtime_mode):
    _env(monkeypatch, WATER_PRINCIPALS=_principal(), GAS_BFF_MODE="development",
         GAS_BFF_BOOTSTRAP_TOKEN="bootstrap-only-server-secret", GAS_BFF_ACTOR_ID="reader",
         WATER_RUNTIME_MODE=runtime_mode, WATER_BIND_HOST="127.0.0.1")
    with pytest.raises(RuntimeError, match="local/development"):
        create_gas_app(include_bff=True)
    app = create_gas_app(include_bff=False)
    with TestClient(app) as client:
        assert client.get("/bff/session").status_code == 404

@pytest.mark.parametrize("bbox", [
    "NaN,0,1,1",
    "0,Infinity,1,1",
    "1,1,0,0",
    "0,0,nope,1",
    "0,0,1,1" + ",1" * 70,
])
def test_bbox_invalid_shapes_keep_validation_error_contract(monkeypatch, bbox):
    _env(monkeypatch, WATER_PRINCIPALS=_principal())
    with TestClient(create_gas_app()) as client:
        response = client.get(
            "/api/v1/gas/engineering/assets",
            params={"bbox": bbox},
            headers={"X-Water-Token": "reader"},
        )
    assert response.status_code == 422
    body = response.json()
    assert isinstance(body["detail"], list)
    assert set(body["detail"][0]) == {"field", "reason", "type"}
    assert body["detail"][0]["field"] == ["query", "bbox"]
