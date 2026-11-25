"""Disposable localhost acceptance harness, NOT a production BFF.

Only an explicitly authenticated test browser receives an ephemeral, synthetic
dataset-only principal. The service runs real gas routes over TCP; stdin controls
faults in this isolated process, never a production resource or business response.
"""
from __future__ import annotations
import argparse
import base64
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--edition", choices=("competition", "project"))
    args = parser.parse_args()
    repo = args.repo.resolve()
    dist = repo / "web/dist"
    if args.edition: dist = dist / args.edition
    if not (dist / "index.html").is_file(): raise SystemExit("build the selected frontend first")
    password = os.environ.pop("GAS_ACCEPTANCE_PASSWORD", "")
    if len(password) < 24:
        raise SystemExit("ephemeral test password required")
    auth = "Basic " + base64.b64encode(("tester:" + password).encode()).decode()
    for key in tuple(os.environ):
        if key.startswith(("WATER_", "AGENT_", "GAS_BFF_", "ALLOW_LOCAL_")): del os.environ[key]
    allowed, denied = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    os.environ.update(WATER_RUNTIME_MODE="controlled", WATER_PRINCIPALS=json.dumps({
        allowed: {"actor_id":"agent-acceptance","scope":"internal","modes":["synthetic"],"datasets":["syn-gas-network-html54-0.1.0"]},
        denied: {"actor_id":"agent-denied","scope":"internal","modes":["synthetic"],"datasets":[]},
    }))
    sys.path.insert(0, str(repo / "engineering"))
    import uvicorn
    from src.gas_application.standalone import create_gas_app
    from src.gas_application.service import GasFixtureService
    service = GasFixtureService()
    sock = socket.socket(); sock.bind(("127.0.0.1",0)); sock.listen()
    api_port = sock.getsockname()[1]
    state = {"mode":"normal"}
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw): super().__init__(*a, directory=str(dist), **kw)
        def log_message(self,*_): pass
        def reply(self,status,body):
            payload = json.dumps(body).encode()
            self.send_response(status); self.send_header("Content-Type","application/json"); self.send_header("Cache-Control","no-store")
            self.send_header("Content-Length",str(len(payload))); self.end_headers()
            try: self.wfile.write(payload)
            except (BrokenPipeError,ConnectionResetError): pass
        def check(self):
            if self.client_address[0] != "127.0.0.1" or self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                self.reply(403,{"detail":"test host denied"}); return False
            if any(k.lower() == "forwarded" or k.lower().startswith(("x-forwarded-","x-real-")) for k in self.headers):
                self.reply(403,{"detail":"forwarded identity denied"}); return False
            if not hmac.compare_digest(self.headers.get("Authorization",""),auth):
                self.send_response(401); self.send_header("WWW-Authenticate",'Basic realm="isolated acceptance"'); self.send_header("Content-Length","0"); self.end_headers(); return False
            origin=self.headers.get("Origin")
            if origin and origin != f"http://127.0.0.1:{self.server.server_port}":
                self.reply(403,{"detail":"origin denied"}); return False
            return True
        def proxy(self, method):
            mode=state["mode"]
            if method=="POST" and self.path != "/api/v1/gas/engineering/agent/query":
                self.reply(405,{"detail":"only read-only Agent query permitted"}); return
            if mode=="slow" and method=="POST": time.sleep(17)
            headers={"Accept":"application/json","Content-Type":"application/json","X-Water-Client":"unified-react"}
            if mode != "unauthenticated": headers["X-Water-Token"] = denied if mode=="denied" else allowed
            raw=None
            if method=="POST":
                try: size=int(self.headers.get("Content-Length","0"))
                except ValueError: self.reply(400,{"detail":"invalid length"}); return
                if size<1 or size>16000: self.reply(413,{"detail":"bounded test request required"}); return
                raw=self.rfile.read(size)
            try:
                response=urlopen(Request(f"http://127.0.0.1:{api_port}"+self.path, data=raw,headers=headers,method=method),timeout=25)
            except HTTPError as error: response=error
            except (URLError,TimeoutError): self.reply(503,{"detail":"test upstream unavailable"}); return
            with response:
                data=response.read()
                print(json.dumps({"kind":"http","method":method,"path":self.path,"status":response.status}),flush=True)
                self.send_response(response.status); self.send_header("Content-Type","application/json"); self.send_header("Cache-Control","no-store")
                self.send_header("Content-Length",str(len(data))); self.end_headers()
                try: self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError): pass
        def do_GET(self):
            if not self.check(): return
            if self.path.startswith("/api/v1/gas/") or self.path=="/health": self.proxy("GET"); return
            if self.path.startswith(("/api/","/bff/")): self.reply(404,{"detail":"not available in acceptance"}); return
            if not (dist/urlsplit(self.path).path.lstrip("/")).is_file(): self.path="/index.html"
            super().do_GET()
        def do_POST(self):
            if self.check(): self.proxy("POST")
    web=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    os.environ["WATER_CORS_ORIGINS"]=f"http://127.0.0.1:{web.server_port}"
    server=uvicorn.Server(uvicorn.Config(create_gas_app(gas_service=service),proxy_headers=False,loop="asyncio",http="h11",log_level="warning"))
    worker=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);worker.start()
    with tempfile.TemporaryDirectory(prefix="gas-agent-fault-") as fault:
        try:
            deadline=time.monotonic()+20
            while not server.started:
                if not worker.is_alive() or time.monotonic()>deadline: raise RuntimeError("API startup failed")
                time.sleep(.02)
            threading.Thread(target=web.serve_forever,daemon=True).start()
            print(json.dumps({"kind":"ready","url":f"http://127.0.0.1:{web.server_port}","api_port":api_port}),flush=True)
            for line in sys.stdin:
                mode=line.strip()
                if mode=="stop": break
                if mode not in {"normal","denied","unauthenticated","slow","fixture_unavailable"}: continue
                state["mode"]=mode
                service.fixture_root=Path(fault)/"absent" if mode=="fixture_unavailable" else None
                print(json.dumps({"kind":"mode","mode":mode}),flush=True)
        finally:
            web.shutdown(); web.server_close(); server.should_exit=True;worker.join(10);sock.close()
            print(json.dumps({"kind":"stopped","api_stopped":not worker.is_alive()}),flush=True)

if __name__=="__main__": main()
