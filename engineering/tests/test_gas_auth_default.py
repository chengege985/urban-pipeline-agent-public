from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.schemas import SnapshotPayload

ROOT = Path(__file__).resolve().parents[1]
CASE = "/api/v1/gas/cases/syn-gas-case-K-001"


def clear(monkeypatch):
    for name in (
        "WATER_API_TOKEN", "WATER_PRINCIPALS", "WATER_DEV_MODES",
        "ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS",
        "WATER_RUNTIME_MODE", "WATER_BIND_HOST", "WATER_GAS_FIXTURE_ROOT",
        "WATER_TRUSTED_HOSTS", "WATER_CORS_ORIGINS",
        "WATER_GAS_FIXTURE_ALLOWED_ROOTS",
    ):
        monkeypatch.delenv(name, raising=False)


def app_for(tmp_path, monkeypatch, **env):
    clear(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return create_app(tmp_path, start_worker=False, bootstrap=False)


def test_no_credentials_default_denies_gas_anonymous(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get(CASE)
    assert response.status_code == 401


def test_production_without_server_auth_fails_closed(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="认证配置"):
        app_for(tmp_path, monkeypatch, WATER_RUNTIME_MODE="production")


def test_client_headers_cannot_upgrade_default_access(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    with TestClient(app) as client:
        response = client.get(
            CASE,
            headers={"X-Water-Client": "unified-react", "X-Water-Data-Scope": "authorized"},
        )
    assert response.status_code == 401


def test_invalid_token_is_401(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch, WATER_API_TOKEN="server-token", WATER_DEV_MODES="synthetic")
    with TestClient(app) as client:
        assert client.get(CASE, headers={"X-Water-Token": "wrong"}).status_code == 401


def test_acl_without_synthetic_permission_is_403(tmp_path, monkeypatch):
    principals = {"reader": {"actor_id": "reader", "scope": "internal", "modes": []}}
    app = app_for(tmp_path, monkeypatch, WATER_PRINCIPALS=json.dumps(principals))
    with TestClient(app) as client:
        assert client.get(CASE, headers={"X-Water-Token": "reader"}).status_code == 403


def test_acl_with_synthetic_permission_is_200(tmp_path, monkeypatch):
    principals = {"reader": {"actor_id": "reader", "scope": "internal", "modes": ["synthetic"], "datasets": ["syn-gas-network-html54-0.1.0"]}}
    app = app_for(tmp_path, monkeypatch, WATER_PRINCIPALS=json.dumps(principals))
    with TestClient(app) as client:
        response = client.get(CASE, headers={"X-Water-Token": "reader"})
    assert response.status_code == 200
    assert response.json()["data_mode"] == "synthetic"



def test_configured_origin_allows_authenticated_synthetic_read(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_DEV_DATASETS="syn-gas-network-html54-0.1.0",
        WATER_CORS_ORIGINS="https://site.example",
        WATER_TRUSTED_HOSTS="testserver",
    )
    with TestClient(app) as client:
        response = client.get(
            CASE,
            headers={"X-Water-Token": "server-token", "Origin": "https://site.example"},
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://site.example"


def test_unlisted_origin_remains_forbidden(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_CORS_ORIGINS="https://site.example",
    )
    with TestClient(app) as client:
        response = client.get(
            CASE,
            headers={"X-Water-Token": "server-token", "Origin": "https://evil.example"},
        )
    assert response.status_code == 403


def test_configured_origin_preflight_is_handled_without_application_auth(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_CORS_ORIGINS="https://site.example",
    )
    with TestClient(app) as client:
        response = client.options(
            CASE,
            headers={
                "Origin": "https://site.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Water-Token",
            },
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://site.example"


def test_configured_trusted_host_is_accepted(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_DEV_DATASETS="syn-gas-network-html54-0.1.0",
        WATER_TRUSTED_HOSTS="api.example",
    )
    with TestClient(app, base_url="http://api.example") as client:
        response = client.get(CASE, headers={"X-Water-Token": "server-token"})
    assert response.status_code == 200


def test_explicit_loopback_local_exception_is_read_only_synthetic(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        ALLOW_LOCAL_SYNTHETIC_ANONYMOUS="true",
        WATER_RUNTIME_MODE="local",
        WATER_BIND_HOST="127.0.0.1",
    )
    with TestClient(app) as client:
        assert client.get(CASE).status_code == 200
        assert client.get("/api/v1/gas/cases/does-not-exist").status_code == 404
        write = client.post("/api/v1/data-batches", json={}, headers={"Idempotency-Key": "anonymous"})
    assert write.status_code == 401


def test_local_exception_requires_explicit_binding(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        ALLOW_LOCAL_SYNTHETIC_ANONYMOUS="true",
        WATER_RUNTIME_MODE="local",
    )
    with TestClient(app) as client:
        assert client.get(CASE).status_code == 401


def test_local_exception_rejects_non_loopback_binding(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        ALLOW_LOCAL_SYNTHETIC_ANONYMOUS="true",
        WATER_RUNTIME_MODE="local",
        WATER_BIND_HOST="0.0.0.0",
    )
    with TestClient(app) as client:
        assert client.get(CASE).status_code == 401


def test_local_exception_rejects_real_snapshot(tmp_path, monkeypatch):
    app = app_for(
        tmp_path, monkeypatch,
        ALLOW_LOCAL_SYNTHETIC_ANONYMOUS="true",
        WATER_RUNTIME_MODE="local",
        WATER_BIND_HOST="127.0.0.1",
    )
    payload = json.loads((ROOT / "data/synthetic/s01.json").read_text(encoding="utf-8"))
    payload["data_mode"] = "real"
    payload["source_status"] = "declared_real"
    sid = app.state.store.save_snapshot(SnapshotPayload.model_validate(payload), "security-real-negative")["snapshot_id"]
    with TestClient(app) as client:
        response = client.get(f"/api/v1/snapshots/{sid}")
    assert response.status_code == 403


def test_broken_fixture_is_sanitized_503(tmp_path, monkeypatch):
    missing = tmp_path / "missing-fixture"
    app = app_for(
        tmp_path / "store", monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_DEV_DATASETS="syn-gas-network-html54-0.1.0",
        WATER_GAS_FIXTURE_ROOT=str(missing),
    )
    with TestClient(app) as client:
        response = client.get(CASE, headers={"X-Water-Token": "server-token"})
    assert response.status_code == 503
    assert str(missing) not in response.text
    assert "traceback" not in response.text.lower()


def test_malformed_server_acl_is_sanitized_503(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch, WATER_API_TOKEN="server-token")
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    monkeypatch.setenv("WATER_PRINCIPALS", "{not-json")
    with TestClient(app) as client:
        response = client.get(CASE)
    assert response.status_code == 503
    assert "WATER_PRINCIPALS" not in response.text
    assert "traceback" not in response.text.lower()
