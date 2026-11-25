"""Loopback-only frontend acceptance: actual unmodified FastAPI and production UI.

An ephemeral, server-side synthetic test principal is injected by a transparent
GET-only test proxy. No browser API interception, backend patch, fixture edit,
production database, worker or model. Not a production authentication/BFF solution.
"""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def proxy_handler(dist, target, token):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/") or path == "/health":
                headers = {"Accept": "application/json", "X-Water-Client": "unified-react"}
                if token is not None:
                    headers["X-Water-Token"] = token
                try:
                    response = urlopen(Request(f"http://127.0.0.1:{target}" + self.path, headers=headers), timeout=30)
                except HTTPError as error:
                    response = error
                except (URLError, TimeoutError):
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail":"acceptance upstream unavailable"}')
                    return
                with response:
                    body = response.read()
                    self.send_response(response.code)
                    self.send_header("Content-Type", response.headers.get("Content-Type", "application/json"))
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass  # A cancelled frontend GET is expected during navigation.
                return
            if path != "/" and not (dist / path.lstrip("/")).is_file():
                self.path = "/index.html"
            super().do_GET()

        def log_message(self, *_args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--port", type=int, default=22473)
    parser.add_argument("--api-port", type=int, default=22481)
    parser.add_argument("--max-seconds", type=int, default=3600)
    args = parser.parse_args()
    repo = args.repo.resolve()
    dist = repo / "web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("Build the actual UI first")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    if sys.platform.startswith("linux"):
        parent = os.getppid()
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM) != 0:
            raise RuntimeError("cannot install parent-death cleanup")
        if os.getppid() != parent:
            stop.set()
    allowed, denied = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    principals = {
        allowed: {"actor_id": "gas-fix3-browser", "scope": "internal", "modes": ["synthetic"], "datasets": ["syn-gas-network-html54-0.1.0"], "permissions": []},
        denied: {"actor_id": "gas-fix3-denied", "scope": "internal", "modes": ["synthetic"], "datasets": [], "permissions": []},
    }
    env = {key: value for key, value in os.environ.items() if not key.startswith(("WATER_", "AGENT_", "ALLOW_LOCAL_SYNTHETIC"))}
    env.update(WATER_RUNTIME_MODE="controlled", WATER_PRINCIPALS=json.dumps(principals), AGENT_ENABLED="0", PYTHONDONTWRITEBYTECODE="1")
    servers = []
    with tempfile.TemporaryDirectory(prefix="wpn-gas-fix3-ui-") as scratch:
        code = ("import sys; from pathlib import Path; "
            + f"sys.path.insert(0, {str(repo / 'engineering')!r}); "
            + "from src.platform.app import create_app; import uvicorn; "
            + f"app=create_app(root=Path({scratch!r}),bootstrap=False,start_worker=False,agent_start_worker=False); "
            + f"uvicorn.run(app,host='127.0.0.1',port={args.api_port},access_log=False,log_level='warning')")
        with (Path(scratch) / "api.log").open("w+") as log:
            child = subprocess.Popen([sys.executable, "-I", "-B", "-c", code], cwd=repo, env=env, stdout=log, stderr=log)
            try:
                for offset, token in enumerate((allowed, None, denied)):
                    server = ThreadingHTTPServer(("127.0.0.1", args.port + offset), proxy_handler(dist, args.api_port, token))
                    servers.append(server)
                    threading.Thread(target=server.serve_forever, daemon=True).start()
                    print(f"STATIC_READY http://127.0.0.1:{args.port + offset}", flush=True)
                deadline = time.monotonic() + 300
                while not stop.is_set():
                    if child.poll() is not None:
                        log.seek(0)
                        raise RuntimeError("acceptance API exited: " + log.read()[-3000:])
                    try:
                        with urlopen(f"http://127.0.0.1:{args.api_port}/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        pass
                    if time.monotonic() > deadline:
                        log.seek(0)
                        raise RuntimeError("acceptance API readiness timeout: " + log.read()[-3000:])
                    stop.wait(.3)
                print(f"READY actual API; supervisor={os.getpid()} api={child.pid}", flush=True)
                deadline = time.monotonic() + args.max_seconds
                while not stop.wait(1) and time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError("acceptance API stopped unexpectedly")
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()
                if child.poll() is None:
                    child.terminate()
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                print("CLEANED frontend acceptance host and temporary data", flush=True)


if __name__ == "__main__":
    main()
