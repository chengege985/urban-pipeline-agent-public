"""Private child processes for gas_dev.py; never a production BFF/login system."""
from __future__ import annotations
import base64
import ctypes
import hmac
import json
import logging
import os
import re
from pathlib import Path
import runpy
import signal
import sys
import threading
import time
import uuid
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, unquote

from gas_amap_preview import DISTRICT_PATH, DistrictResponse, PreviewAmap


DISPOSITION_PATH = re.compile(r"/api/v1/gas/engineering/events/syn-gas-event-[a-z0-9-]+/disposition")
READ_AGENT_PATHS = {
    "/api/v1/gas/engineering/agent/query",
    "/api/v1/gas/engineering/data-m2/agent/query",
    "/api/v1/gas/engineering/data-m2/topic-query",
}
PILOT_QUERY_PATH = re.compile(
    r"/api/v1/gas/engineering/pilot/datasets/syn-gas-pilot-[A-Za-z0-9_.-]+/versions/"
    r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/agent/query"
)
CUV_COLLABORATION_PATH = "/api/v1/gas/engineering/cuv-replay/collaboration"
SCENARIO_PATH = "/api/v1/gas/engineering/synthetic-scenarios"
SCENARIO_POST_PATHS = {SCENARIO_PATH, SCENARIO_PATH + "/comparison", SCENARIO_PATH + "/tool-query",
                       SCENARIO_PATH + "/source-stress", SCENARIO_PATH + "/source-stress/explain"}
SCENARIO_ACTION_PATH = re.compile(SCENARIO_PATH + r"/syn-gas-drill-[a-f0-9]{32}/(?:analyze|rollback|explain)")


def preview_identities(access: dict, operator_enabled: bool):
    operator = access.get("operator") if operator_enabled else None
    return (access, operator) if isinstance(operator, dict) else (access,)

def parent_guard():
    expected=int(os.environ["GAS_DEV_PARENT"])
    # Linux parent-death notification; supervisor also has an independent reaper.
    if os.getppid()!=expected:raise SystemExit("supervisor unavailable")
    ctypes.CDLL(None).prctl(1,signal.SIGTERM)
    if os.getppid()!=expected:raise SystemExit("supervisor unavailable")

def read_config(root: Path):
    return json.loads((root/"config.json").read_text())

def model(root: Path, cfg: dict):
    os.environ.update(HF_HUB_OFFLINE="1",TRANSFORMERS_OFFLINE="1")
    tensor_parallel = "1" if cfg.get("model_ppu") is not None else "2"
    sys.argv=["sglang.launch_server","--model-path",cfg["model_dir"],"--host","127.0.0.1",
        "--port",str(cfg["model_port"]),"--tp-size",tensor_parallel,"--dtype","bfloat16","--context-length","8192",
        "--mem-fraction-static","0.65","--max-running-requests","2","--served-model-name","Qwen3.8-27B",
        "--tool-call-parser","qwen3_coder","--cuda-graph-backend-prefill","disabled",
        "--cuda-graph-backend-decode","disabled","--api-key",(root/"model.token").read_text().strip()]
    runpy.run_module("sglang.launch_server",run_name="__main__")

def gateway(root: Path,cfg: dict):
    os.environ.update(LOCAL_GATEWAY_DATA=str(root/"gateway-data"),LOCAL_GATEWAY_DB=str(root/"gateway-data/gateway.db"),
        LOCAL_GATEWAY_CONFIG=str(root/"gateway-config"),LOCAL_GATEWAY_BACKUP=str(root/"gateway-backups"),
        LOCAL_GATEWAY_LEGACY_BACKUP=str(root/"no-legacy"),LOCAL_GATEWAY_GLOBAL_CONCURRENCY="2",
        LOCAL_GATEWAY_UPSTREAM=f"http://127.0.0.1:{cfg['model_port']}",
        LOCAL_GATEWAY_UPSTREAM_TOKEN=(root/"model.token").read_text().strip(),
        LOCAL_GATEWAY_ADMIN_TOKEN=(root/"admin.token").read_text().strip())
    sys.path.insert(0,cfg["gateway_repo"])
    import uvicorn
    uvicorn.run("gateway.app:app",host="127.0.0.1",port=cfg["gateway_port"],proxy_headers=False,
                loop="asyncio",http="h11",log_level="warning")

def services(root: Path,cfg: dict):
    import httpx
    import uvicorn
    for name in tuple(os.environ):
        if name.startswith(("WATER_", "GAS_BFF_", "ALLOW_LOCAL_", "AGENT_", "GAS_AGENT_",
                            "GAS_DISPOSITION_", "GAS_CUV_COLLABORATION_")):
            del os.environ[name]
    disposition_demo = cfg.get("disposition_demo") is True
    cuv_collaboration_demo = cfg.get("cuv_collaboration_demo") is True
    pilot_dataset = os.environ.get("GAS_PILOT_PREVIEW_DATASET", "")
    pilot_enabled = bool(os.environ.get("GAS_PILOT_STORE")) and bool(
        re.fullmatch(r"syn-gas-pilot-[A-Za-z0-9_.-]{1,114}", pilot_dataset)
    )
    if not pilot_enabled:
        os.environ.pop("GAS_PILOT_STORE", None)
    datasets = ["syn-gas-network-html54-0.1.0"]
    if pilot_enabled:
        datasets.append(pilot_dataset)
    operator_enabled = disposition_demo or cuv_collaboration_demo
    operator_permissions = []
    if disposition_demo:
        operator_permissions.append("gas:disposition")
    if cuv_collaboration_demo:
        operator_permissions.append("gas:cuv:collaborate")
    identities = preview_identities(json.loads((root / "access.json").read_text()), operator_enabled)
    principals = {
        identity["platform_token"]: {
            "actor_id": identity["actor"], "scope": "internal",
            "modes": ["synthetic"], "datasets": datasets,
            **({"permissions": operator_permissions} if index == 1 else {}),
        }
        for index, identity in enumerate(identities)
    }
    os.environ.update(WATER_RUNTIME_MODE="controlled",WATER_TRUSTED_HOSTS="127.0.0.1,localhost",
        WATER_CORS_ORIGINS=f"http://127.0.0.1:{cfg['web_port']}",GAS_AGENT_MODEL_MODE="local_qwen" if cfg["model"]!="off" else "disabled",
        GAS_AGENT_GATEWAY_URL=f"http://127.0.0.1:{cfg['gateway_port']}",GAS_AGENT_GATEWAY_KEY_FILE=str(root/"gas-client.token"),
        WATER_PRINCIPALS=json.dumps(principals))
    scenario_dir = root / "scenario-audit"
    scenario_dir.mkdir(mode=0o700, exist_ok=True)
    os.environ["GAS_SCENARIO_AUDIT_DB"] = str(scenario_dir / "audit.sqlite")
    if disposition_demo:
        os.environ.update(GAS_DISPOSITION_DEMO="1", WATER_BIND_HOST="127.0.0.1")
    if cuv_collaboration_demo:
        audit_dir = root / "cuv-audit"
        audit_dir.mkdir(mode=0o700, exist_ok=True)
        os.environ.update(
            GAS_CUV_COLLABORATION_DEMO="1", GAS_CUV_COLLABORATION_AUDIT_DB=str(audit_dir / "audit.sqlite"),
            WATER_BIND_HOST="127.0.0.1",
        )
    sys.path.insert(0,str(Path(cfg["repo"])/"engineering"))
    from src.gas_application.standalone import create_gas_app
    from src.gas_application.service import GasFixtureService
    store=GasFixtureService()
    def model_starting() -> bool:
        if cfg["model"] == "off":
            return False
        try:
            return json.loads((root / "state.json").read_text()).get("model_status") == "starting"
        except (OSError, ValueError, AttributeError):
            return False

    app=create_gas_app(gas_service=store,include_bff=False,model_starting=model_starting)
    logging.basicConfig(level=logging.INFO,format="%(message)s")
    build=Path(cfg["repo"])/"web/dist"
    amap = PreviewAmap.from_runtime(root)
    hosts = {f"{host}:{cfg['web_port']}" for host in ("127.0.0.1", "localhost")}
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(build),**kwargs)
        def log_message(self,*args):pass
        def end_headers(self):
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Content-Type-Options","nosniff")
            self.send_header("Referrer-Policy","no-referrer")
            super().end_headers()
        def send_body(self, status, body, content_type, request_id=None, server_timing=None):
            self.send_response(status);self.send_header("Content-Type",content_type)
            self.send_header("Content-Length",str(len(body)))
            if request_id:
                self.send_header("X-Request-ID",request_id)
            if server_timing:
                self.send_header("Server-Timing", server_timing)
            self.end_headers()
            if self.command == "HEAD":
                return
            try:self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
        def reply(self,status,detail,request_id=None):
            self.send_body(status, json.dumps({"detail":detail}).encode(), "application/json", request_id)
        def authorized_token(self):
            host = self.headers.get("Host")
            if (self.client_address[0] != "127.0.0.1" or host not in hosts
                or any(k.lower() == "forwarded" or k.lower().startswith(("x-forwarded-", "x-real-")) for k in self.headers)):
                self.reply(403, "local preview access denied")
                return None
            try:
                access = json.loads((root / "access.json").read_text())
            except (OSError, ValueError):
                self.reply(503, "preview identity unavailable")
                return None
            if (not isinstance(access, dict) or not isinstance(access.get("expires_at"), (int, float))
                or not all(isinstance(access.get(key), str) and access[key] for key in ("username", "password"))):
                self.reply(503, "preview identity unavailable")
                return None
            if time.time() >= access["expires_at"]:
                self.reply(401, "preview identity expired")
                return None
            supplied = self.headers.get("Authorization", "").encode("latin-1")
            token = None
            for identity in preview_identities(access, operator_enabled):
                if not all(isinstance(identity.get(key), str) and identity[key]
                           for key in ("username", "password", "platform_token")):
                    continue
                expected = b"Basic " + base64.b64encode(f"{identity['username']}:{identity['password']}".encode())
                if hmac.compare_digest(supplied, expected) and identity["platform_token"] in principals:
                    token = identity["platform_token"]
            if token is None:
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="gas-local-development"')
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            origin = f"http://{host}"
            origin_header = self.headers.get("Origin")
            if ((origin_header and origin_header != origin) or (self.command == "POST" and origin_header != origin)
                or self.headers.get("Sec-Fetch-Site") == "cross-site"):
                self.reply(403, "preview origin denied")
                return None
            return token

        def proxy(self, token):
            started=time.monotonic();rid=uuid.uuid4().hex;path=urlsplit(self.path).path
            disposition_post = disposition_demo and self.path == path and DISPOSITION_PATH.fullmatch(path)
            collaboration_post = cuv_collaboration_demo and self.path == CUV_COLLABORATION_PATH
            pilot_post = pilot_enabled and self.path == path and PILOT_QUERY_PATH.fullmatch(path)
            scenario_post = self.path == path and (path in SCENARIO_POST_PATHS or SCENARIO_ACTION_PATH.fullmatch(path))
            if self.command == "POST" and not (self.path in READ_AGENT_PATHS or disposition_post or collaboration_post or pilot_post or scenario_post):
                self.reply(405, "unsupported method")
                return
            payload=None
            if self.command=="POST":
                try:size=int(self.headers.get("Content-Length","0"))
                except ValueError:self.reply(400,"invalid length");return
                if size<1 or size>16000:self.reply(413,"bounded JSON required");return
                if self.headers.get("Content-Type","").split(";")[0]!="application/json":
                    self.reply(415,"JSON required");return
                payload=self.rfile.read(size)
            try:
                with httpx.Client(trust_env=False,follow_redirects=False,timeout=20) as client:
                    response=client.request(self.command,f"http://127.0.0.1:{cfg['api_port']}"+self.path,
                        headers={"X-Water-Token":token,"X-Water-Client":"gas-development","Content-Type":"application/json"},content=payload)
                rid = response.headers.get("X-Request-ID", rid)
                self.send_response(response.status_code);self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(response.content)));self.send_header("X-Request-ID",rid);self.end_headers()
                try:self.wfile.write(response.content)
                except (BrokenPipeError,ConnectionResetError):pass
                status=response.status_code
            except httpx.HTTPError:
                status=503;self.reply(status,"preview upstream unavailable",rid)
            print(json.dumps({"kind":"preview_http","request_id":rid,"method":self.command,"path":path,
                 "status":status,"elapsed_ms":round((time.monotonic()-started)*1000,2)}),flush=True)
        def do_GET(self):
            self.serve_read_request()

        def do_HEAD(self):
            self.serve_read_request()

        def proxy_amap_district(self, query):
            if amap is None:
                self.reply(404, "AMap preview is not configured")
                return
            if self.command != "GET":
                self.reply(405, "unsupported method")
                return
            try:
                params = amap.district_parameters(query)
            except ValueError:
                self.reply(400, "invalid district query")
                return
            rid = uuid.uuid4().hex
            started = time.monotonic()
            try:
                result = amap.request_district(params, f"http://{self.headers['Host']}/")
            except httpx.TimeoutException:
                result = DistrictResponse(504)
            except httpx.HTTPError:
                result = DistrictResponse(502)
            timing = f'amap-http;desc="{result.status}"'
            if result.provider_code:
                timing += f', amap-district;desc="{result.provider_code}"'
            self.send_body(result.status, result.body, result.content_type, rid, timing)
            print(json.dumps({"kind": "preview_amap_district", "request_id": rid, "adcode": params["keywords"],
                              "http": result.status, "provider_code": result.provider_code,
                              "elapsed_ms": round((time.monotonic()-started)*1000, 2)}), flush=True)

        def serve_read_request(self):
            token = self.authorized_token()
            if token is None:
                return
            url = urlsplit(self.path)
            path = url.path
            if path == DISTRICT_PATH:
                self.proxy_amap_district(url.query)
                return
            if path.startswith("/_AMapService/"):
                self.reply(404, "unsupported map service")
                return
            if path.startswith("/api/v1/gas/") or path == "/health":
                if self.command == "HEAD":
                    self.reply(405, "unsupported method")
                else:
                    self.proxy(token)
                return
            if path.startswith(("/api/","/bff/","/__dev/")):self.reply(404,"unsupported");return
            target=(build/unquote(path).lstrip("/")).resolve()
            if not target.is_relative_to(build.resolve()) or any(p.startswith(".") for p in Path(unquote(path)).parts):
                self.reply(404,"not found");return
            if not target.is_file():
                if path.startswith("/assets/") or target.suffix:
                    self.reply(404, "not found")
                    return
                self.path = "/index.html"
            if amap is not None and (path == "/index.html" or self.path == "/index.html"):
                self.send_body(200, amap.configure_page((build / "index.html").read_bytes()), "text/html; charset=utf-8")
                return
            if self.command == "HEAD":
                super().do_HEAD()
            else:
                super().do_GET()
        def do_POST(self):
            token = self.authorized_token()
            if token is not None:
                self.proxy(token)

        def do_DELETE(self):
            if self.authorized_token() is not None:
                self.reply(405, "unsupported method")
    web=ThreadingHTTPServer(("127.0.0.1",cfg["web_port"]),Handler)
    web.daemon_threads=True
    threading.Thread(target=web.serve_forever,daemon=True).start()
    try:
        uvicorn.run(app,host="127.0.0.1",port=cfg["api_port"],proxy_headers=False,loop="asyncio",http="h11",log_level="warning")
    finally:web.shutdown();web.server_close()

if __name__=="__main__":
    parent_guard()
    root=Path(sys.argv[2]);cfg=read_config(root)
    {"model":model,"gateway":gateway,"services":services}[sys.argv[1]](root,cfg)
