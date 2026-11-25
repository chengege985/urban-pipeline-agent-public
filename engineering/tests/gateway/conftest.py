from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
ENGINEERING = REPO_ROOT / "engineering"
for path in (str(REPO_ROOT), str(ENGINEERING)):
    if path not in sys.path:
        sys.path.insert(0, path)

ORIGINAL_ASYNC_CLIENT = httpx.AsyncClient

SSE_BODY = (
    'data: {"id":"c1","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"合 "},"finish_reason":null}]}\n\n'
    'data: {"id":"c1","object":"chat.completion.chunk","choices":[],"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}\n\n'
    'data: [DONE]\n\n'
)


class SlowSSEStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'data: {"id":"slow","object":"chat.completion.chunk","choices":[{"index":0,"delta":{"content":"slow"},"finish_reason":null}]}\n\n'
        await asyncio.sleep(30)
        yield b"data: [DONE]\n\n"

    async def aclose(self) -> None:
        return None


@pytest.fixture
def gw(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.startswith("LOCAL_GATEWAY_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    monkeypatch.setenv("LOCAL_GATEWAY_DATA", str(tmp_path / "data"))
    monkeypatch.setenv("LOCAL_GATEWAY_DB", str(tmp_path / "data" / "gateway.db"))
    monkeypatch.setenv("LOCAL_GATEWAY_CONFIG", str(tmp_path / "data" / "config"))
    monkeypatch.setenv("LOCAL_GATEWAY_BACKUP", str(tmp_path / "encrypted_backup"))
    monkeypatch.setenv("LOCAL_GATEWAY_LEGACY_BACKUP", str(tmp_path / "legacy_backup"))
    monkeypatch.setenv("LOCAL_GATEWAY_ADMIN_TOKEN", "adm_fix2_test")
    monkeypatch.setenv("LOCAL_GATEWAY_BACKUP_PASSPHRASE", "fix2-test-passphrase")
    monkeypatch.setenv("LOCAL_GATEWAY_BACKUP_INTERVAL", "3600")
    monkeypatch.setenv("LOCAL_GATEWAY_DISABLE_WARMUP", "1")
    monkeypatch.setenv("LOCAL_LLM_ALLOWED_ORIGINS", "http://127.0.0.1:8081,http://localhost:8081")
    sys.modules.pop("gateway.app", None)
    import gateway.app as module
    module.restore_if_missing()
    module.init_db()
    yield module
    sys.modules.pop("gateway.app", None)


@pytest.fixture
def upstream(gw, monkeypatch):
    state = {"mode": "ok", "sleep": 0.0}

    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/v1/tokenize"):
            return httpx.Response(200, json={"count": 2})
        if path.endswith("/v1/models"):
            return httpx.Response(200, json={"object": "list", "data": [{"id": gw.MODEL_NAME}]})
        if path.endswith("/v1/chat/completions"):
            if state["mode"] == "timeout":
                raise httpx.TimeoutException("mock timeout")
            if state["mode"] == "connect":
                raise httpx.ConnectError("mock connect error")
            if state["mode"] == "redirect":
                return httpx.Response(302, headers={"Location": "http://evil.example"})
            if state["mode"] == "sleep":
                await asyncio.sleep(state["sleep"])
            if state["mode"] == "stream":
                return httpx.Response(200, text=SSE_BODY, headers={"content-type": "text/event-stream"})
            if state["mode"] == "slow_stream":
                return httpx.Response(200, stream=SlowSSEStream(), headers={"content-type": "text/event-stream"})
            if state["mode"] == "no_usage":
                return httpx.Response(200, json={
                    "id": "c1", "model": gw.MODEL_NAME,
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                                 "finish_reason": "stop"}],
                })
            return httpx.Response(200, json={
                "id": "c1", "model": gw.MODEL_NAME,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
            })
        return httpx.Response(404, json={"error": "not_found"})

    def factory(*args, **kwargs):
        return ORIGINAL_ASYNC_CLIENT(
            transport=httpx.MockTransport(handler),
            timeout=httpx.Timeout(30.0),
            follow_redirects=False,
        )

    shim = SimpleNamespace(
        AsyncClient=factory,
        Timeout=httpx.Timeout,
        Response=httpx.Response,
        ConnectError=httpx.ConnectError,
        TimeoutException=httpx.TimeoutException,
        HTTPError=httpx.HTTPError,
        MockTransport=httpx.MockTransport,
    )
    monkeypatch.setattr(gw, "httpx", shim)
    return state


@pytest.fixture
def admin_client(gw, upstream):
    with TestClient(gw.app) as client:
        yield client


@pytest.fixture
def admin_headers(gw):
    return {"X-Admin-Token": gw.ADMIN_TOKEN, "Content-Type": "application/json"}


@pytest.fixture
def key_factory(admin_client, admin_headers):
    def make(**kwargs):
        body = {
            "name": "fix2-key",
            "model": "Qwen3.8-27B",
            "scope": "production",
            "quota_tokens": 0,
            "rpm_limit": 60,
            "concurrent_limit": 4,
        }
        body.update(kwargs)
        response = admin_client.post("/admin/api/keys", headers=admin_headers, json=body)
        assert response.status_code == 200, response.text
        return response.json()
    return make
