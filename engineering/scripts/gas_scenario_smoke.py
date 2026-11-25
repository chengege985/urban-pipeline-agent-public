"""Real TCP comparison: live local Qwen, rules, and explicit failure-only servers."""
from __future__ import annotations
import argparse,json,os,socket,sys,tempfile,threading,time,secrets
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import httpx,uvicorn
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.gas_application.standalone import create_gas_app

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    cfg=json.loads((args.runtime/"config.json").read_text())
    gateway=f"http://127.0.0.1:{cfg['gateway_port']}"
    keyfile=args.runtime/"gas-client.token"
    assert keyfile.exists(),"Qwen not ready; do not label as live inference"
    for k in tuple(os.environ):
        if k.startswith(("WATER_","GAS_BFF_","GAS_AGENT_","ALLOW_LOCAL_")):del os.environ[k]
    token,limited=secrets.token_urlsafe(24),secrets.token_urlsafe(24)
    os.environ.update(WATER_RUNTIME_MODE="controlled",GAS_AGENT_GATEWAY_URL=gateway,GAS_AGENT_GATEWAY_KEY_FILE=str(keyfile),
      WATER_PRINCIPALS=json.dumps({token:{"actor_id":"scenario","scope":"internal","modes":["synthetic"],"datasets":["syn-gas-network-html54-0.1.0"]},
      limited:{"actor_id":"limited","scope":"internal","modes":["synthetic"],"datasets":[]}}))
    cases=json.loads((Path(__file__).resolve().parents[1]/"tests/fixtures/gas_agent_zh_scenarios.json").read_text())["scenarios"]
    sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen()
    server=uvicorn.Server(uvicorn.Config(create_gas_app(include_bff=False),proxy_headers=False,loop="asyncio",http="h11",log_level="warning"))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    result={"mode":"real_fastapi_tcp","rows":[],"model_evidence":"actual Qwen via pinned existing gateway; no success interception","status":"FAIL"}
    baseline={}
    try:
        deadline=time.monotonic()+15
        while not server.started:
            assert time.monotonic()<deadline and thread.is_alive();time.sleep(.02)
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",trust_env=False,timeout=20) as client:
            for mode in ("disabled","local_qwen"):
                os.environ["GAS_AGENT_MODEL_MODE"]=mode
                for case in [c for c in cases if c["execution"]=="both"]:
                    authorization=case.get("authorization")
                    headers={} if authorization=="none" else {"X-Water-Token":limited if authorization=="limited" else token}
                    start=time.monotonic()
                    response=client.post("/api/v1/gas/engineering/agent/query",headers=headers,json=case["input"])
                    data=response.json();expected=case["expected"]
                    row={"id":case["id"],"mode":mode,"http":response.status_code,"seconds":round(time.monotonic()-start,3),"response":data}
                    checks={"http":response.status_code==expected["http_status"]}
                    if response.status_code==200:
                        tools=data.get("tools",[])
                        checks["intent"]=([t["tool_name"] for t in tools]==[expected["tool"]] if expected["tool"] else not tools and data["intent"]=="clarify")
                        checks["parameters"]=not tools or all(tools[0]["parameters"].get(k)==v for k,v in expected.get("parameters",{}).items())
                        checks["evidence"]=not expected.get("evidence_required") or bool(data.get("evidence_refs"))
                        if mode=="disabled":
                            baseline[case["id"]]=[(t["tool_name"],t["status"],t["data"],t["evidence_refs"]) for t in tools]
                            checks["source"]=data["parser_source"]=="local_rule_not_llm" and data["model_calls"]==0
                        else:
                            checks["facts_equal_rule"]=baseline.get(case["id"])==[(t["tool_name"],t["status"],t["data"],t["evidence_refs"]) for t in tools]
                            expected_model=bool(expected["tool"] and expected["ui_action"]!="not_found")
                            checks["source"]=(data["parser_source"]=="qwen_intent" and data["model_status"]=="ready" and data["model_calls"]==2) if expected_model else data["model_calls"]==0
                    row["checks"]=checks;row["pass"]=all(checks.values());result["rows"].append(row)
                    print(json.dumps({"id":case["id"],"mode":mode,"pass":row["pass"],"checks":checks}),flush=True)
            for case in [c for c in cases if c["execution"]=="fault"]:
                fault=case["fault"]
                class FailureOnly(BaseHTTPRequestHandler):
                    def log_message(self,*args):pass
                    def do_POST(self):
                        if fault=="tcp_timeout":time.sleep(7)
                        body=b'{"invalid_model_reply":true}'
                        self.send_response(200);self.send_header("Content-Length",str(len(body)));self.end_headers()
                        try:self.wfile.write(body)
                        except (BrokenPipeError,ConnectionResetError):pass
                failure=ThreadingHTTPServer(("127.0.0.1",0),FailureOnly);failure.daemon_threads=True
                port=failure.server_port
                if fault=="connection_refused":failure.server_close()
                else:threading.Thread(target=failure.serve_forever,daemon=True).start()
                os.environ["GAS_AGENT_GATEWAY_URL"]=f"http://127.0.0.1:{port}"
                start=time.monotonic()
                response=client.post("/api/v1/gas/engineering/agent/query",headers={"X-Water-Token":token},json=case["input"])
                data=response.json()
                good=response.status_code==200 and data["parser_source"]=="local_rule_not_llm" and data["model_status"]=="unavailable" and bool(data["error_code"]) and data["tools"][0]["data"]["asset"]["asset_id"]=="syn-gas-pipe-K-001"
                result["rows"].append({"id":case["id"],"mode":"controlled_failure_only_endpoint","http":response.status_code,
                    "seconds":round(time.monotonic()-start,3),"response":data,"pass":good})
                if fault!="connection_refused":failure.shutdown();failure.server_close()
        result["status"]="PASS" if all(r["pass"] for r in result["rows"]) else "FAIL"
    finally:
        server.should_exit=True;thread.join(10);sock.close();result["service_stopped"]=not thread.is_alive()
        result["passed"]=sum(r["pass"] for r in result["rows"]);result["total"]=len(result["rows"])
        args.output.parent.mkdir(exist_ok=True,parents=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(json.dumps({k:result[k] for k in ("status","passed","total","service_stopped")}))
    if result["status"]!="PASS":raise SystemExit(1)
if __name__=="__main__":main()
