from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app

CASE = "/api/v1/gas/cases/syn-gas-case-K-001"
ROOT = Path(__file__).resolve().parents[1]


def clear(monkeypatch):
    for name in (
        "WATER_API_TOKEN", "WATER_PRINCIPALS", "WATER_DEV_MODES", "WATER_DEV_DATASETS",
        "WATER_CORS_ORIGINS", "WATER_TRUSTED_HOSTS", "WATER_RUNTIME_MODE", "WATER_BIND_HOST",
        "ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS",
        "WATER_GAS_FIXTURE_ROOT", "WATER_GAS_FIXTURE_ALLOWED_ROOTS",
    ):
        monkeypatch.delenv(name, raising=False)


def app_for(tmp_path, monkeypatch, **env):
    clear(monkeypatch)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return create_app(tmp_path, start_worker=False, bootstrap=False)


def test_default_read_contract_requires_token_for_all_read_endpoints(tmp_path, monkeypatch):
    app = app_for(tmp_path, monkeypatch)
    schema = app.openapi()
    expected = {
        "/api/v1/datasets/{dataset_id}/view",
        "/api/v1/gas/cases",
        "/api/v1/gas/cases/{case_id}",
        "/api/v1/capabilities",
        "/api/v1/snapshots",
        "/api/v1/snapshots/{snapshot_id}",
        "/api/v1/assessments",
    }
    for path in expected:
        operation = schema["paths"][path]["get"]
        assert operation["security"] == [{"WaterToken": []}], path


def test_origin_trailing_slash_is_not_accepted(tmp_path, monkeypatch):
    app = app_for(
        tmp_path,
        monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_CORS_ORIGINS="https://site.example",
    )
    with TestClient(app) as client:
        response = client.get(
            CASE,
            headers={"X-Water-Token": "server-token", "Origin": "https://site.example/"},
        )
    assert response.status_code == 403
    assert "access-control-allow-origin" not in response.headers


def test_cors_wildcard_configuration_fails_closed(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="通配符"):
        app_for(
            tmp_path,
            monkeypatch,
            WATER_API_TOKEN="server-token",
            WATER_DEV_MODES="synthetic",
            WATER_CORS_ORIGINS="*",
        )


def test_invalid_host_preflight_is_rejected_before_cors(tmp_path, monkeypatch):
    app = app_for(
        tmp_path,
        monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_CORS_ORIGINS="https://site.example",
    )
    with TestClient(app, base_url="http://evil.example") as client:
        response = client.options(
            CASE,
            headers={
                "Origin": "https://site.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-Water-Token",
            },
        )
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_local_anonymous_exception_rejects_forwarded_identity_headers(tmp_path, monkeypatch):
    app = app_for(
        tmp_path,
        monkeypatch,
        ALLOW_LOCAL_SYNTHETIC_ANONYMOUS="true",
        WATER_RUNTIME_MODE="local",
        WATER_BIND_HOST="127.0.0.1",
    )
    with TestClient(app) as client:
        response = client.get(CASE, headers={"X-Forwarded-For": "203.0.113.10"})
    assert response.status_code == 401


def test_write_preflight_is_public_cors_only_but_write_requires_auth_and_client(tmp_path, monkeypatch):
    app = app_for(
        tmp_path,
        monkeypatch,
        WATER_API_TOKEN="server-token",
        WATER_DEV_MODES="synthetic",
        WATER_CORS_ORIGINS="https://site.example",
    )
    with TestClient(app) as client:
        preflight = client.options(
            "/api/v1/data-batches",
            headers={
                "Origin": "https://site.example",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-Water-Token, X-Water-Client, Idempotency-Key, Content-Type",
            },
        )
        unauthenticated = client.post("/api/v1/data-batches", json={}, headers={"Origin": "https://site.example"})
        missing_client = client.post(
            "/api/v1/data-batches",
            json={},
            headers={"Origin": "https://site.example", "X-Water-Token": "server-token", "Idempotency-Key": "s1fix2"},
        )
        invalid_payload = client.post(
            "/api/v1/data-batches",
            json={},
            headers={
                "Origin": "https://site.example",
                "X-Water-Token": "server-token",
                "X-Water-Client": "unified-react",
                "Idempotency-Key": "s1fix2-valid-client",
            },
        )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "https://site.example"
    assert "Origin" in preflight.headers.get("vary", "")
    assert unauthenticated.status_code == 401
    assert missing_client.status_code == 403
    assert invalid_payload.status_code == 422
