"""Actual ASGI receive-byte limits, independent of Content-Length (K-C-05)."""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from server.app import app, BodyLimitMiddleware, MAX_BODY_BYTES


def exercise(chunks, *, headers=None, limit=8):
    sent, seen = [], {"reads": 0, "called": False, "body": None}

    async def receive():
        i = seen["reads"]
        seen["reads"] += 1
        if i >= len(chunks):
            return {"type": "http.disconnect"}
        return {"type": "http.request", "body": chunks[i], "more_body": i < len(chunks) - 1}

    async def send(message):
        sent.append(message)

    async def downstream(scope, receive, send):
        seen["called"] = True
        seen["body"] = (await receive())["body"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    scope = {"type": "http", "method": "POST", "path": "/", "headers": headers or []}
    asyncio.run(BodyLimitMiddleware(downstream, max_body_bytes=limit)(scope, receive, send))
    return sent, seen


def test_headerless_stream_stops_at_first_over_limit_chunk():
    sent, seen = exercise([b"1234", b"5678", b"9", b"unread"])
    assert sent[0]["status"] == 413
    assert seen == {"reads": 3, "called": False, "body": None}


def test_small_content_length_cannot_hide_actual_body_size():
    sent, seen = exercise([b"12345", b"6789"], headers=[(b"content-length", b"1")])
    assert sent[0]["status"] == 413 and not seen["called"]


def test_exact_limit_stream_is_preserved_for_downstream():
    sent, seen = exercise([b"12", b"345", b"678"])
    assert sent[0]["status"] == 200
    assert seen["called"] and seen["body"] == b"12345678"


@pytest.mark.parametrize("length,code", [(b"9", 413), (b"-1", 400), (b"invalid", 400)])
def test_reject_bad_headers_before_reading(length, code):
    sent, seen = exercise([b"body"], headers=[(b"content-length", length)])
    assert sent[0]["status"] == code and seen["reads"] == 0


def test_chunked_valid_json_over_one_mib_returns_413_with_readable_cors_error():
    raw = b" " * MAX_BODY_BYTES + json.dumps({"pipeId": "WP-016"}).encode()
    with TestClient(app) as client:
        response = client.post("/api/v1/demo/evaluate", content=iter([raw[:500000], raw[500000:]]),
                               headers={"content-type": "application/json", "origin": "http://localhost:5173"})
    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert "1 MiB" in response.json()["detail"]
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_chunked_json_below_limit_reaches_normal_endpoint():
    with TestClient(app) as client:
        response = client.post("/api/v1/demo/evaluate", content=iter([b'{"pipeId":', b'"WP-016"}']),
                               headers={"content-type": "application/json"})
    assert response.status_code == 200
