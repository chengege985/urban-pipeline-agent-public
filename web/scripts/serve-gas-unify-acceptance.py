"""Loopback-only real-API acceptance host, NOT a production BFF.

Runs an immutable, Git-blob-verified copy of the supplied baseline backend on
local temporary storage to avoid object-storage Python import stalls. No backend
source, ACL, fixture, worker, model or production database is modified. Tokens
are generated in RAM, never persisted or exposed to the browser. Business GETs
are transparently forwarded; upstream connection failure is 502, not fake 503.
"""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

BASELINE = "0940740db1743c49f640f6cdfc1ea5516559edb7"


def backend_mirror(repo, scratch):
    prefixes = ["engineering/src", "engineering/configs", "engineering/experiments/gas_m0"]
    entries = subprocess.check_output(["git", "ls-tree", "-rz", BASELINE, "--", *prefixes], cwd=repo).split(bytes([0]))
    records = []
    for entry in entries:
        if not entry:
            continue
        head, name = entry.split(bytes([9]), 1)
        mode, kind, sha = head.decode().split()
        if kind == "blob":
            records.append((mode, sha, name.decode()))
    assert records, "missing pinned backend source"
    mirror = scratch / "backend"
    mirror.mkdir()
    archive = subprocess.check_output(["git", "archive", BASELINE, *prefixes], cwd=repo)
    with tarfile.open(fileobj=io.BytesIO(archive)) as bundle:
        bundle.extractall(mirror, filter="data")
    for mode, sha, name in records:
        path = mirror / name
        data = path.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(data)).encode() + bytes([0]) + data).hexdigest()
        if actual != sha:
            raise RuntimeError("backend mirror blob mismatch: " + name)
    tree = subprocess.check_output(["git", "rev-parse", BASELINE + ":engineering"], cwd=repo, text=True).strip()
    return mirror, {"baseline": BASELINE, "backendTree": tree, "verifiedBackendFiles": len(records), "runtime": "immutable-baseline-backend-mirror", "productionBff": False}


def handler(dist, api_port, token, metadata):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(dist), **kwargs)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/__acceptance/meta":
                body = json.dumps(metadata).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith("/api/") or path == "/health":
                headers = {"Accept": "application/json", "X-Water-Client": "unified-react"}
                if token is not None:
                    headers["X-Water-Token"] = token
                try:
                    response = urlopen(Request(f"http://127.0.0.1:{api_port}" + self.path, headers=headers), timeout=25)
                except HTTPError as error:
                    response = error
                except (URLError, TimeoutError):
                    self.send_response(502)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"detail":"acceptance upstream not reachable; not a fixture 503"}')
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
                        pass
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
    parser.add_argument("--port", default=23473, type=int)
    parser.add_argument("--api-port", default=23481, type=int)
    parser.add_argument("--max-seconds", default=7200, type=int)
    args = parser.parse_args()
    repo = args.repo.resolve()
    dist = repo / "web/dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("Build the production frontend first")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: stop.set())
    if sys.platform.startswith("linux"):
        parent = os.getppid()
        if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM) != 0:
            raise RuntimeError("cannot configure parent-death cleanup")
        if os.getppid() != parent:
            stop.set()
    servers, children, logs = [], [], []
    with tempfile.TemporaryDirectory(prefix="wpn-gas-unify-acceptance-") as directory:
        scratch = Path(directory)
        mirror, metadata = backend_mirror(repo, scratch)
        print("VERIFIED_BACKEND_MIRROR " + json.dumps(metadata), flush=True)
        allowed, denied = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        principals = {
            allowed: {"actor_id": "gas-unify-browser", "scope": "internal", "modes": ["synthetic"], "datasets": ["syn-gas-network-html54-0.1.0"], "permissions": []},
            denied: {"actor_id": "gas-unify-denied", "scope": "internal", "modes": ["synthetic"], "datasets": [], "permissions": []},
        }
        env = {key: value for key, value in os.environ.items() if not key.startswith(("WATER_", "AGENT_", "ALLOW_LOCAL_SYNTHETIC"))}
        env.update(WATER_RUNTIME_MODE="controlled", WATER_PRINCIPALS=json.dumps(principals), AGENT_ENABLED="0", PYTHONDONTWRITEBYTECODE="1")
        try:
            for offset in range(2):
                child_env = dict(env)
                if offset:
                    child_env["WATER_GAS_FIXTURE_ROOT"] = str(mirror / "engineering/experiments/gas_m0/data/missing-unify-test")
                code = ("import sys; from pathlib import Path; "
                    + f"sys.path.insert(0, {str(mirror / 'engineering')!r}); "
                    + "from src.platform.app import create_app; import uvicorn; "
                    + f"app=create_app(root=Path({str(scratch / ('database-' + str(offset)))!r}),bootstrap=False,start_worker=False,agent_start_worker=False); "
                    + f"uvicorn.run(app,host='127.0.0.1',port={args.api_port + offset},access_log=False,log_level='warning')")
                log = (scratch / f"api-{offset}.log").open("w+")
                logs.append(log)
                children.append(subprocess.Popen([sys.executable, "-I", "-B", "-c", code], cwd=scratch, env=child_env, stdout=log, stderr=log))
            for offset, (api_offset, token) in enumerate(((0, allowed), (0, None), (0, denied), (1, allowed))):
                server = ThreadingHTTPServer(("127.0.0.1", args.port + offset), handler(dist, args.api_port + api_offset, token, metadata))
                servers.append(server)
                threading.Thread(target=server.serve_forever, daemon=True).start()
                print(f"STATIC_READY http://127.0.0.1:{args.port + offset}", flush=True)
            waiting = set(range(2))
            deadline = time.monotonic() + 240
            while waiting and not stop.is_set():
                for index in list(waiting):
                    if children[index].poll() is not None:
                        logs[index].seek(0)
                        raise RuntimeError("BLOCKED_BY_ENV: backend exited: " + logs[index].read()[-3000:])
                    try:
                        with urlopen(f"http://127.0.0.1:{args.api_port + index}/health", timeout=1) as response:
                            if response.status == 200:
                                waiting.remove(index)
                    except (URLError, TimeoutError):
                        pass
                if time.monotonic() > deadline:
                    raise RuntimeError("BLOCKED_BY_ENV: actual TCP readiness timeout")
                stop.wait(.25)
            print(f"READY actual FastAPI supervisor={os.getpid()} children=" + ",".join(str(child.pid) for child in children), flush=True)
            deadline = time.monotonic() + args.max_seconds
            while not stop.wait(1) and time.monotonic() < deadline:
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("BLOCKED_BY_ENV: actual API stopped unexpectedly")
        finally:
            for server in servers:
                server.shutdown()
                server.server_close()
            for child in children:
                if child.poll() is None:
                    child.terminate()
            for child in children:
                try:
                    child.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            for log in logs:
                log.close()
            print("CLEANED acceptance processes, RAM credentials and temporary databases", flush=True)


if __name__ == "__main__":
    main()
