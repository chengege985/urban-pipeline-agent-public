"""Migration-only, loopback synthetic gateway. Does not modify product source.

This is a local restoration helper, not production authentication or deployment.
Use a fresh runtime directory. No original sample, model service or old DB loaded.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
from urllib.parse import urlencode


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path, required=True)
    parser.add_argument("--web-dist", type=Path, required=True)
    parser.add_argument("--runtime-dir", type=Path)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if importlib.util.find_spec("fcntl") is None:
        parser.error("Native Windows backend is currently BLOCKED: src/platform/runtime.py requires POSIX fcntl.flock. "
                     "Use the unchanged backend in Linux/WSL2. Do not install a fake fcntl or disable the worker lock. "
                     "See WINDOWS_LOCAL_SETUP.md. No runtime directory or database has been opened.")
    backend = args.backend_root.resolve(strict=True)
    dist = args.web_dist.resolve(strict=True)
    if not (backend / "src/platform/app.py").is_file() or not (dist / "index.html").is_file():
        parser.error("backend-root must contain src/platform/app.py; web-dist must contain index.html")
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.runtime_dir:
        runtime = args.runtime_dir.resolve()
        if runtime.exists():
            parser.error("runtime-dir must not exist: existing databases are never opened by this helper")
        if runtime.is_relative_to(backend) or runtime.is_relative_to(dist):
            parser.error("runtime-dir must be outside source and dist")
        runtime.mkdir(parents=True)
    else:
        runtime = Path(tempfile.mkdtemp(prefix="waterpulse-local-synthetic-"))
    for key in list(os.environ):
        if key.startswith(("WATER_", "AGENT_", "OPENAI_", "LANGCHAIN_", "LANGSMITH_")):
            os.environ.pop(key)
    os.environ.update(WATER_LLM_MODE="mock", AGENT_ENABLED="0", PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(backend))

    # Deny outbound connections in this helper process. Incoming loopback HTTP
    # uses accept(), not connect(); browsers and the test driver are separate.
    outbound_attempts: list[str] = []
    def blocked_connect(self, address):
        outbound_attempts.append(str(address))
        raise PermissionError("migration helper forbids outbound network")
    socket.socket.connect = blocked_connect
    socket.socket.connect_ex = blocked_connect

    import uvicorn
    from starlette.requests import Request
    from starlette.responses import FileResponse, JSONResponse, RedirectResponse
    from src.platform.app import create_app
    from src.platform.schemas import SnapshotPayload

    token = secrets.token_urlsafe(48)
    session = secrets.token_urlsafe(48)
    login_nonce = secrets.token_urlsafe(32)
    os.environ["WATER_PRINCIPALS"] = json.dumps({token: {
        "actor_id": "local-synthetic-operator", "scope": "internal",
        "modes": ["synthetic"], "corpora": [], "permissions": ["run"],
    }})
    app = create_app(runtime, bootstrap=False, start_worker=True, agent_start_worker=False)
    payload = SnapshotPayload.model_validate_json((backend / "data/synthetic/m1a_unified.json").read_text(encoding="utf-8"))
    if payload.data_mode != "synthetic" or payload.source_status != "constructed":
        raise ValueError("migration fixture must explicitly be synthetic / constructed")
    snapshot = app.state.store.save_snapshot(payload, "migration:synthetic-unified")
    sid = snapshot["snapshot_id"]

    import re
    allowed = [
        ("GET", re.compile(r"/api/v1/capabilities")),
        ("GET", re.compile(r"/api/v1/datasets/[^/]+/view")),
        ("GET", re.compile(r"/api/v1/snapshots(?:/[^/]+)?")),
        ("GET", re.compile(r"/api/v1/assessments")),
        ("GET", re.compile(r"/api/v1/runs(?:/[^/]+(?:/report)?)?")),
        ("POST", re.compile(r"/api/v1/(?:assessment|simulation|decision)-runs")),
        ("POST", re.compile(r"/api/v1/timeline/(?:check|evaluate)")),
    ]
    class Gateway:
        async def __call__(self, scope, receive, send):
            nonlocal login_nonce
            if scope["type"] == "lifespan":
                return await app(scope, receive, send)
            if scope["type"] != "http":
                return
            request = Request(scope, receive)
            path = request.url.path
            if request.url.hostname not in {"127.0.0.1", "localhost"}:
                return await JSONResponse({"detail": "loopback Host required"}, 400)(scope, receive, send)
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return await JSONResponse({"detail": "same origin required"}, 403)(scope, receive, send)
            if path == "/local-login" and request.method == "GET":
                nonce = request.query_params.get("nonce", "")
                if not login_nonce or not secrets.compare_digest(nonce, login_nonce):
                    return await JSONResponse({"detail": "invalid or consumed local login"}, 403)(scope, receive, send)
                login_nonce = ""
                response = RedirectResponse("/?" + urlencode({"mode": "platform", "datasetId": sid}), 303)
                response.set_cookie("waterpulse_local", session, httponly=True, samesite="strict", path="/")
                response.headers["Cache-Control"] = "no-store"
                response.headers["Referrer-Policy"] = "no-referrer"
                return await response(scope, receive, send)
            if path == "/health":
                return await app(scope, receive, send)
            if path.startswith("/api/"):
                if not any(method == request.method and pattern.fullmatch(path) for method, pattern in allowed):
                    return await JSONResponse({"detail": "endpoint excluded from migration synthetic helper"}, 403)(scope, receive, send)
                if not secrets.compare_digest(request.cookies.get("waterpulse_local", ""), session):
                    return await JSONResponse({"detail": "local session required"}, 401)(scope, receive, send)
                # Client ID is not identity. Discard spoofable auth identity and
                # inject the principal token known only inside this gateway.
                headers = [(k, v) for k, v in scope["headers"] if k.lower() not in {
                    b"x-water-token", b"authorization", b"x-principal-id", b"x-actor-id"}]
                headers.append((b"x-water-token", token.encode("ascii")))
                return await app({**scope, "headers": headers}, receive, send)
            if request.method not in {"GET", "HEAD"}:
                return await JSONResponse({"detail": "method not allowed"}, 405)(scope, receive, send)
            target = (dist / path.lstrip("/")).resolve()
            if not target.is_relative_to(dist):
                return await JSONResponse({"detail": "outside web-dist"}, 403)(scope, receive, send)
            if not target.is_file():
                target = dist / "index.html"
            return await FileResponse(target)(scope, receive, send)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(128)
    port = listener.getsockname()[1]
    print(json.dumps({"ready": True, "base_url": f"http://127.0.0.1:{port}",
        "login_url": f"http://127.0.0.1:{port}/local-login?nonce={login_nonce}",
        "snapshot_id": sid, "runtime_dir": str(runtime), "agent_enabled": False,
        "llm_mode": "mock", "data_mode": "synthetic"}, ensure_ascii=False), flush=True)
    try:
        uvicorn.Server(uvicorn.Config(Gateway(), log_level="warning", access_log=False)).run(sockets=[listener])
    finally:
        listener.close()
        print(json.dumps({"stopped": True, "outbound_attempt_count": len(outbound_attempts)}), flush=True)


if __name__ == "__main__":
    main()
