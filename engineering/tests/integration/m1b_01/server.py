"""M1-B isolated synthetic server. Test session identities, never production auth."""
import asyncio
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sys
import threading
import tempfile
from http.cookies import SimpleCookie


def main():
    workspace = Path(sys.argv[1]).resolve()
    source = workspace / "engine"
    sys.path.insert(0, str(source))
    for key in list(os.environ):
        if key.startswith(("WATER_", "AGENT_", "OPENAI_", "LANGSMITH_", "LANGCHAIN_")):
            os.environ.pop(key)
    os.environ.update(WATER_LLM_MODE="mock", AGENT_ENABLED="false")
    # Private stdin from test controller; no credentials in assets, logs or files.
    sessions = json.loads(sys.stdin.readline())["sessions"]
    assert set(sessions) == {"alice", "bob"}
    from src.platform.app import create_app
    from src.platform.schemas import SnapshotPayload
    from src.platform import services
    from starlette.responses import FileResponse, JSONResponse
    import uvicorn

    runtime_root = Path(tempfile.mkdtemp(prefix="synthetic-runtime-", dir=workspace))
    app = create_app(runtime_root, bootstrap=False, start_worker=True)
    payload = SnapshotPayload.model_validate_json((source / "data/synthetic/m1a_unified.json").read_text())
    saved = app.state.store.save_snapshot(payload, "synthetic:m1b-01")
    sid = saved["snapshot_id"]
    other = payload.model_copy(update={"name": "M1-B denied synthetic fixture"})
    denied_sid = app.state.store.save_snapshot(other, "synthetic:m1b-denied")["snapshot_id"]
    tokens = {actor: secrets.token_urlsafe(32) for actor in sessions}
    os.environ["WATER_PRINCIPALS"] = json.dumps({tokens[actor]: {
        "actor_id": "m1b-" + actor, "scope": "internal", "modes": ["synthetic"],
        "datasets": [sid], "permissions": ["run"], "corpora": [],
    } for actor in sessions})
    counts = {}
    for name in ("calculate_assessment", "simulate_snapshot", "compare_snapshot"):
        original = getattr(services, name)
        def counted(*args, _name=name, _original=original, **kwargs):
            counts[_name] = counts.get(_name, 0) + 1
            return _original(*args, **kwargs)
        setattr(services, name, counted)
    inputs = json.loads((source / "data/synthetic/m1a_unified_inputs.json").read_text())
    recompute = {k: inputs[k] for k in ("as_of", "window_start", "window_end", "static_rows", "dynamic_rows")}
    recompute.update(name="M1-B browser synthetic assessment", data_mode="synthetic", source_refs=["synthetic:m1b-01"])
    audit = []
    outbound_attempts = []
    allowed = {
        ("GET", "/api/v1/capabilities"), ("GET", "/api/v1/assessments"),
        ("POST", "/api/v1/assessment-runs"), ("POST", "/api/v1/simulation-runs"),
        ("POST", "/api/v1/decision-runs"), ("POST", "/api/v1/timeline/check"),
        ("POST", "/api/v1/timeline/evaluate"),
    }

    async def gateway(scope, receive, send):
        if scope["type"] != "http":
            return await app(scope, receive, send)
        headers = {k.lower(): v for k, v in scope["headers"]}
        host = headers.get(b"host", b"").decode()
        origin = headers.get(b"origin", b"").decode()
        if not re.fullmatch(r"127\.0\.0\.1:\d+", host) or (origin and origin != "http://" + host):
            return await JSONResponse({"detail": "isolated test origin denied"}, 403)(scope, receive, send)
        cookies = SimpleCookie()
        cookies.load(headers.get(b"cookie", b"").decode())
        value = cookies["m1b_session"].value if "m1b_session" in cookies else ""
        actor = next((a for a, secret in sessions.items() if secrets.compare_digest(value, secret)), None)
        path, method = scope["path"], scope["method"]
        if path in ("/", "/index.html", "/bundle.js") and method == "GET":
            filename = "bundle.js" if path == "/bundle.js" else "index.html"
            return await FileResponse(workspace / "static" / filename)(scope, receive, send)
        if path == "/m1b/config" and method == "GET":
            data = {"snapshotId": sid, "deniedSnapshotId": denied_sid, "recompute": recompute}
            return await JSONResponse(data if actor else {"detail": "test session required"}, 200 if actor else 401)(scope, receive, send)
        if path == "/m1b/audit" and method == "GET":
            data = {"counts": counts, "requests": audit, "dataMode": "synthetic",
                    "outboundAttempts": outbound_attempts, "realModelCalls": 0}
            return await JSONResponse(data if actor == "alice" else {}, 200 if actor == "alice" else 403)(scope, receive, send)
        permitted = (method, path) in allowed or (method == "GET" and (
            re.fullmatch(r"/api/v1/runs/[\w-]+", path) or
            re.fullmatch(r"/api/v1/snapshots/[\w-]+", path) or
            re.fullmatch(r"/api/v1/datasets/[\w-]+/view", path)))
        if not permitted:
            audit.append({"method": method, "path": path, "actor": actor, "status": 403,
                          "gate": "outside-approved-routes"})
            return await JSONResponse({"detail": "outside M1-B-01 approved routes"}, 403)(scope, receive, send)
        # Client markers never grant identity. Strip spoofed identity and inject
        # a token resolved solely from the controller's HttpOnly test session.
        scope = dict(scope)
        scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() not in
                            (b"x-water-token", b"authorization", b"x-agent-actor", b"x-principal-id")]
        if actor:
            scope["headers"].append((b"x-water-token", tokens[actor].encode()))
        async def logged(message):
            if message["type"] == "http.response.start":
                audit.append({"method": method, "path": path, "actor": actor, "status": message["status"]})
            await send(message)
        return await app(scope, receive, logged)

    # No model or other external socket may be opened by this isolated process.
    def denied_connect(*args, **kwargs):
        outbound_attempts.append("blocked outbound socket")
        raise RuntimeError("M1-B isolated backend forbids outbound sockets")
    socket.socket.connect = denied_connect
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    config = uvicorn.Config(gateway, log_level="error", access_log=False)
    server = uvicorn.Server(config)
    def stop_on_stdin():
        sys.stdin.readline()  # controller sends STOP; EOF also shuts down
        server.should_exit = True
    threading.Thread(target=stop_on_stdin, daemon=True).start()
    print(json.dumps({"ready": True, "port": sock.getsockname()[1]}), flush=True)
    try:
        server.run(sockets=[sock])
    finally:
        (workspace / "server_audit.json").write_text(json.dumps({"counts": counts, "requests": audit,
            "outboundAttempts": outbound_attempts, "realModelCalls": 0, "dataMode": "synthetic"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
