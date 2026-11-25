import asyncio
import secrets
from types import SimpleNamespace

import httpx
import pytest


def chat_body(max_tokens=2, stream=False):
    body = {
        "model": "Qwen3.8-27B",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0,
        "max_tokens": max_tokens,
    }
    if stream:
        body["stream"] = True
    return body


def auth(key):
    return {"Authorization": "Bearer " + key["key"], "Content-Type": "application/json"}


def insert_key(gw, **kwargs):
    key_id = "key_" + secrets.token_hex(6)
    raw = "sk-test-" + secrets.token_urlsafe(20)
    quota = int(kwargs.get("quota_tokens", 0))
    rpm = int(kwargs.get("rpm_limit", 60))
    concurrent = int(kwargs.get("concurrent_limit", 1))
    with gw.DB_LOCK, gw.connect_db() as conn:
        conn.execute(
            """INSERT INTO api_keys(id,name,key_hash,key_prefix,model,quota_tokens,used_tokens,
            reserved_tokens,rpm_limit,concurrent_limit,enabled,deleted,scope,created_at)
            VALUES(?,?,?,?,?,?,0,0,?,?,1,0,'test',?)""",
            (key_id, "direct-" + key_id[:10], gw.sha256_text(raw), raw[:16], gw.MODEL_NAME,
             quota, rpm, concurrent, gw.now_iso()),
        )
    return {"id": key_id, "key": raw, "quota_tokens": quota,
            "rpm_limit": rpm, "concurrent_limit": concurrent}


async def wait_active(client, headers, key_id, expected, timeout=2.0):
    for _ in range(50):
        r = await client.get("/admin/api/concurrency", headers=headers)
        stats = r.json()
        if stats["keys"].get(key_id, {}).get("active") == expected:
            return stats
        await asyncio.sleep(timeout / 50)
    r = await client.get("/admin/api/concurrency", headers=headers)
    return r.json()


def test_lease_release_is_idempotent(gw):
    async def main():
        global_limiter = gw.ConcurrencyLimiter(1)
        key_limiter = gw.ConcurrencyLimiter(1)
        assert await global_limiter.try_acquire()
        assert await key_limiter.try_acquire()
        lease = gw.ConcurrencyLease("k", global_limiter, key_limiter)
        await lease.release()
        await lease.release()
        assert (await global_limiter.stats())["active"] == 0
        assert (await key_limiter.stats())["active"] == 0
    asyncio.run(main())


def test_timeout_504_releases_lease_and_allows_next_request(admin_client, admin_headers, upstream, key_factory):
    key = key_factory(concurrent_limit=1)
    upstream["mode"] = "timeout"
    r1 = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
    assert r1.status_code == 504
    stats = admin_client.get("/admin/api/concurrency", headers=admin_headers).json()
    assert stats["global"]["active"] == 0
    assert stats["keys"][key["id"]]["active"] == 0
    upstream["mode"] = "ok"
    r2 = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
    assert r2.status_code == 200


def test_connect_error_503_releases_lease(admin_client, admin_headers, upstream, key_factory):
    key = key_factory(concurrent_limit=1)
    upstream["mode"] = "connect"
    r1 = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
    assert r1.status_code == 503
    stats = admin_client.get("/admin/api/concurrency", headers=admin_headers).json()
    assert stats["global"]["active"] == 0
    assert stats["keys"][key["id"]]["active"] == 0
    upstream["mode"] = "ok"
    assert admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body()).status_code == 200


def test_quota_exceeded_releases_lease(admin_client, admin_headers, upstream, key_factory):
    key = key_factory(concurrent_limit=1, quota_tokens=3)
    upstream["mode"] = "ok"
    r1 = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
    assert r1.status_code == 429
    assert r1.json()["error"]["message"] == "quota_exceeded"
    stats = admin_client.get("/admin/api/concurrency", headers=admin_headers).json()
    assert stats["keys"][key["id"]]["active"] == 0
    patch = admin_client.patch("/admin/api/keys/" + key["id"], headers=admin_headers,
                               json={"quota_tokens": 0})
    assert patch.status_code == 200
    assert admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body()).status_code == 200


def test_repeated_exception_requests_do_not_increase_concurrency_limit(admin_client, admin_headers, upstream, key_factory):
    key = key_factory(concurrent_limit=1)
    upstream["mode"] = "timeout"
    for _ in range(4):
        r = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
        assert r.status_code == 504
    stats = admin_client.get("/admin/api/concurrency", headers=admin_headers).json()
    assert stats["keys"][key["id"]]["limit"] == 1
    assert stats["keys"][key["id"]]["active"] == 0
    upstream["mode"] = "ok"
    assert admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body()).status_code == 200


@pytest.mark.parametrize("field,value", [
    ("quota_tokens", -1),
    ("quota_tokens", None),
    ("quota_tokens", "100"),
    ("rpm_limit", 0),
    ("rpm_limit", True),
    ("concurrent_limit", -1),
    ("concurrent_limit", None),
    ("enabled", "yes"),
    ("enabled", 1),
    ("scope", "test"),
    ("name", None),
    ("unknown", 1),
])
def test_patch_rejects_illegal_input(admin_client, admin_headers, key_factory, field, value):
    key = key_factory()
    body = {field: value}
    r = admin_client.patch("/admin/api/keys/" + key["id"], headers=admin_headers, json=body)
    assert 400 <= r.status_code < 500, (field, value, r.status_code, r.text)


def test_patch_rejects_non_object_and_null_body(admin_client, admin_headers, key_factory):
    key = key_factory()
    for body in ([], None, "x", 7):
        r = admin_client.patch("/admin/api/keys/" + key["id"], headers=admin_headers, json=body)
        assert 400 <= r.status_code < 500, r.text


def test_api_key_permission_isolation(admin_client, admin_headers, key_factory):
    key = key_factory()
    assert admin_client.get("/v1/models", headers={"X-Admin-Token": "adm_fix2_test"}).status_code == 401
    assert admin_client.get("/admin/api/keys", headers=auth(key)).status_code == 401
    assert admin_client.get("/v1/models", headers=auth(key)).status_code == 200


def test_soft_delete_preserves_stable_consumer_identity(admin_client, admin_headers, upstream, key_factory):
    key = key_factory()
    upstream["mode"] = "ok"
    assert admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body()).status_code == 200
    r = admin_client.delete("/admin/api/keys/" + key["id"], headers=admin_headers)
    assert r.status_code == 200 and r.json()["soft_delete"] is True
    assert admin_client.get("/v1/models", headers=auth(key)).status_code == 401
    reqs = admin_client.get("/admin/api/requests?limit=10", headers=admin_headers).json()["requests"]
    assert any(x["consumer_id"] == key["id"] and x["consumer_name"] for x in reqs)


def test_no_usage_is_flagged_as_estimate_not_upstream(admin_client, admin_headers, upstream, key_factory):
    key = key_factory()
    upstream["mode"] = "no_usage"
    r = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
    assert r.status_code == 200
    reqs = admin_client.get("/admin/api/requests?limit=10", headers=admin_headers).json()["requests"]
    row = next(x for x in reqs if x["consumer_id"] == key["id"])
    assert row["usage_source"] == "estimate"
    assert row["upstream_total_tokens"] == 0
    assert row["billed_tokens"] == row["estimated_tokens"]
    assert row["billed_tokens"] > 0


def test_sse_success_usage_and_release(admin_client, admin_headers, upstream, key_factory):
    key = key_factory(concurrent_limit=1)
    upstream["mode"] = "stream"
    r = admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body(stream=True))
    assert r.status_code == 200
    assert "data: [DONE]" in r.text
    stats = admin_client.get("/admin/api/concurrency", headers=admin_headers).json()
    assert stats["keys"][key["id"]]["active"] == 0
    reqs = admin_client.get("/admin/api/requests?limit=10", headers=admin_headers).json()["requests"]
    row = next(x for x in reqs if x["consumer_id"] == key["id"])
    assert row["usage_source"] in {"upstream", "upstream_capped"}
    assert row["upstream_total_tokens"] == 5
    assert row["billed_tokens"] == 4


def test_token_ledger_consistency_endpoint(admin_client, admin_headers, upstream, key_factory):
    key = key_factory(quota_tokens=100)
    upstream["mode"] = "ok"
    for _ in range(3):
        assert admin_client.post("/v1/chat/completions", headers=auth(key), json=chat_body()).status_code == 200
    data = admin_client.get("/admin/api/stats-consistency", headers=admin_headers).json()
    assert data["ok"] is True
    assert data["requests_billed_tokens"] == data["keys_used_tokens"] == data["overview_billed_tokens"]
    assert data["requests_upstream_total_tokens"] == data["overview_total_tokens"]
    assert data["active_reservations"] == 0


def test_concurrent_limit_blocks_second_request(gw, upstream, admin_headers):
    key = insert_key(gw, concurrent_limit=1, rpm_limit=60)
    upstream["mode"] = "sleep"
    upstream["sleep"] = 0.4

    async def main():
        await gw.reset_limiters()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", headers=auth(key), json=chat_body()))
            stats = await wait_active(client, admin_headers, key["id"], 1)
            assert stats["keys"][key["id"]]["limit"] == 1
            second = await client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
            assert second.status_code == 429
            assert second.json()["error"]["message"] == "concurrency_limit_exceeded"
            first_result = await first
            assert first_result.status_code == 200
            stats = await client.get("/admin/api/concurrency", headers=admin_headers)
            assert stats.json()["keys"][key["id"]]["active"] == 0
    asyncio.run(asyncio.wait_for(main(), 10))


def test_dynamic_concurrency_limit_does_not_replace_inflight_state(gw, upstream, admin_headers):
    key = insert_key(gw, concurrent_limit=1, rpm_limit=60)
    upstream["mode"] = "sleep"
    upstream["sleep"] = 0.5

    async def main():
        await gw.reset_limiters()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://test") as client:
            first = asyncio.create_task(client.post("/v1/chat/completions", headers=auth(key), json=chat_body()))
            await wait_active(client, admin_headers, key["id"], 1)
            patch = await client.patch("/admin/api/keys/" + key["id"], headers=admin_headers,
                                       json={"concurrent_limit": 2})
            assert patch.status_code == 200
            second = await client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
            assert second.status_code == 200
            stats = await client.get("/admin/api/concurrency", headers=admin_headers)
            assert stats.json()["keys"][key["id"]]["limit"] == 2
            assert (await first).status_code == 200
            stats = await client.get("/admin/api/concurrency", headers=admin_headers)
            assert stats.json()["keys"][key["id"]]["active"] == 0
    asyncio.run(asyncio.wait_for(main(), 10))


def test_quota_reservation_is_atomic_under_race(gw, upstream, admin_headers):
    key = insert_key(gw, quota_tokens=8, concurrent_limit=4, rpm_limit=60)
    upstream["mode"] = "sleep"
    upstream["sleep"] = 0.15

    async def main():
        await gw.reset_limiters()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url="http://test") as client:
            results = await asyncio.gather(*[
                client.post("/v1/chat/completions", headers=auth(key), json=chat_body())
                for _ in range(3)
            ])
            codes = sorted(r.status_code for r in results)
            assert codes == [200, 200, 429]
            stats = await client.get("/admin/api/concurrency", headers=admin_headers)
            assert stats.json()["keys"][key["id"]]["active"] == 0
            ledger = await client.get("/admin/api/stats-consistency", headers=admin_headers)
            assert ledger.json()["ok"] is True
    asyncio.run(asyncio.wait_for(main(), 10))


class FakeRequest:
    def __init__(self, raw_key, body):
        self.headers = {"Authorization": "Bearer " + raw_key, "Content-Type": "application/json"}
        self.state = SimpleNamespace(request_id="req_sse_cancel")
        self._body = body

    async def json(self):
        return self._body


def test_sse_cancel_and_client_disconnect_release_lease(gw, upstream):
    key = insert_key(gw, concurrent_limit=1, rpm_limit=60)
    upstream["mode"] = "slow_stream"

    async def main():
        await gw.reset_limiters()
        response = await gw.chat_completions(FakeRequest(key["key"], chat_body(stream=True)))
        assert hasattr(response, "body_iterator")

        async def consume():
            async for _ in response.body_iterator:
                return True
            return False

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.08)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        if hasattr(response.body_iterator, "aclose"):
            await response.body_iterator.aclose()
        await asyncio.sleep(0.05)
        if response.background is not None:
            await response.background()

        stats = await gw.concurrency_stats()
        assert stats["keys"][key["id"]]["active"] == 0
        with gw.DB_LOCK, gw.connect_db() as conn:
            statuses = [r[0] for r in conn.execute(
                "SELECT status FROM requests WHERE consumer_id=?", (key["id"],)).fetchall()]
            billed = conn.execute("SELECT COALESCE(SUM(billed_tokens),0) v FROM requests").fetchone()["v"]
            used = conn.execute("SELECT COALESCE(SUM(used_tokens),0) v FROM api_keys").fetchone()["v"]
        assert 499 in statuses
        assert billed == used == gw.overview()["billed_tokens"]
    asyncio.run(asyncio.wait_for(main(), 5))
