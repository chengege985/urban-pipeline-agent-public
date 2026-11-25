import asyncio
import httpx
import pytest

@pytest.mark.parametrize("url",["http://evil.test:30000","http://localhost:30000","http://127.0.0.1:30000@evil.test:80","https://127.0.0.1:30000","http://127.0.0.1:30000/private","http://127.0.0.1:30000?url=http://evil","http://127.0.0.1:30000#frag","http://127.0.0.1:80","http://127.0.0.1:99999"])
def test_gateway_rejects_noncanonical_or_nonloopback_upstreams(gw,url):
    with pytest.raises(RuntimeError,match="loopback"):
        gw.validated_upstream(url)

def test_gateway_loopback_and_server_held_upstream_credential(gw,monkeypatch):
    assert gw.validated_upstream("http://127.0.0.1:30000/")=="http://127.0.0.1:30000"
    monkeypatch.setenv("LOCAL_GATEWAY_UPSTREAM_TOKEN","synthetic-model-token")
    assert gw.upstream_auth()=={"Authorization":"Bearer synthetic-model-token"}

@pytest.mark.parametrize("status",[302,401,422,500,504])
def test_gateway_never_relays_raw_upstream_error_or_redirect(gw,status):
    result=asyncio.run(gw.upstream_error_response(httpx.Response(status,json={"error":"secret-password /workdata/private Traceback"},headers={"Location":"https://evil.test"}),"synthetic-request"))
    assert result.status_code==(502 if status==302 else status)
    assert b"secret" not in result.body and b"/workdata" not in result.body and b"Traceback" not in result.body
    assert "location" not in result.headers

def test_health_does_not_expose_internal_diagnostics(gw,monkeypatch):
    async def ready():
        return {"state":"ready","upstream":"http://127.0.0.1:30000","last_warmup_error":"secret /workdata/private"}
    monkeypatch.setattr(gw,"readiness",ready)
    assert asyncio.run(gw.health())=={"status":"ok","state":"ready","model":gw.MODEL_NAME}
