"""Opt-in, real localhost Qwen + FastAPI smoke. No model mocks or real gas data."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import sys
import threading
import time
import httpx
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.gas_application.standalone import create_gas_app
from src.gas_application.service import GasFixtureService

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    if os.getenv("GAS_AGENT_MODEL_MODE")!="local_qwen":
        raise SystemExit("explicit local_qwen configuration required")
    for key in tuple(os.environ):
        if key.startswith(("WATER_","ALLOW_LOCAL_","GAS_BFF_")):del os.environ[key]
    token,limited=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
    os.environ.update(WATER_RUNTIME_MODE="controlled",WATER_PRINCIPALS=json.dumps({
      token:{"actor_id":"qwen-smoke","scope":"internal","modes":["synthetic"],"datasets":["syn-gas-network-html54-0.1.0"]},
      limited:{"actor_id":"qwen-denied","scope":"internal","modes":["synthetic"],"datasets":[]}}))
    service=GasFixtureService()
    sock=socket.socket();sock.bind(("127.0.0.1",0));sock.listen()
    server=uvicorn.Server(uvicorn.Config(create_gas_app(gas_service=service),loop="asyncio",http="h11",proxy_headers=False,log_level="warning"))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    output={"mode":"real_tcp_real_qwen","records":[],"status":"FAIL"}
    root="/api/v1/gas/engineering/agent"
    try:
        deadline=time.monotonic()+15
        while not server.started:
            assert thread.is_alive() and time.monotonic()<deadline
            time.sleep(.02)
        with httpx.Client(base_url=f"http://127.0.0.1:{sock.getsockname()[1]}",trust_env=False,timeout=20) as client:
            for supplied,status in [(None,401),("incorrect",401),(limited,403)]:
                response=client.post(root+"/query",headers={"X-Water-Token":supplied} if supplied else {},json={"question":"查询事件"})
                output["records"].append({"name":"acl","expected":status,"actual":response.status_code})
                assert response.status_code==status
            headers={"X-Water-Token":token}
            health=client.get(root+"/health",headers=headers)
            assert health.status_code==200 and health.json()["model_status"]=="ready"
            output["health"]=health.json()
            cases=[("查看K-001管段的信息","asset.query"),("查看当前有哪些燃气事件","event.query"),
                   ("定位隔离事件关联的管段","map.locate"),("目前支持哪些CUV分析？","capabilities.query"),
                   ("解释这个隔离事件","event.explain")]
            for question,tool in cases:
                start=time.monotonic()
                response=client.post(root+"/query",headers=headers,json={"question":question})
                data=response.json()
                output["records"].append({"question":question,"status":response.status_code,"seconds":round(time.monotonic()-start,3),"response":data})
                assert response.status_code==200
                assert data["model_status"]=="ready" and data["parser_source"]=="qwen_intent"
                assert data["model_id"]=="Qwen3.8-27B" and data["model_calls"]==2
                assert data["model_usage"]["total_tokens"]>0 and data["evidence_refs"]
                assert data["tools"][0]["tool_name"]==tool and data["explanation_source"]=="qwen_evidence_selection"
                if tool=="map.locate":
                    assert len(data["tools"][0]["data"]["asset_ids"])==9
                    assert data["tools"][0]["data"]["coordinate_authorized"] is False
                if tool=="asset.query":assert data["tools"][0]["data"]["asset"]["asset_id"]=="syn-gas-pipe-K-001"
                if tool=="event.query":assert len(data["tools"][0]["data"]["items"])==3
            for body,status in [({"question":"查询","tool_hint":"valve.close"},422),
                                ({"question":"查询","asset_id":"not-found"},404)]:
                response=client.post(root+"/query",headers=headers,json=body)
                output["records"].append({"name":"invalid","expected":status,"actual":response.status_code})
                assert response.status_code==status
            refused=client.post(root+"/query",headers=headers,json={"question":"关闭阀门并执行现场操作"}).json()
            assert refused["tools"]==[] and refused["model_calls"]==0
            output["control_refused"]=refused
            # Real service fault, not an intercepted or fabricated HTTP response.
            service.fixture_root=Path("/nix/wpn-fastdev05-runtime/no-such-fixture")
            response=client.post(root+"/query",headers=headers,json={"question":"查看事件"})
            assert response.status_code==503 and "no-such-fixture" not in response.text
            output["records"].append({"name":"fixture_fault","status":response.status_code})
            service.fixture_root=None
            assert client.get("/api/v1/gas/engineering/assets",headers=headers).status_code==200
        output["status"]="PASS"
    finally:
        server.should_exit=True;thread.join(10);sock.close()
        output["service_stopped"]=not thread.is_alive()
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(output,ensure_ascii=False,indent=2))
        print(json.dumps({"status":output["status"],"records":len(output["records"]),"service_stopped":output["service_stopped"]}))
if __name__=="__main__":main()
