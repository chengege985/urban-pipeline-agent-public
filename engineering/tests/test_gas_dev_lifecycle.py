"""Actual loopback preview lifecycle, identity, expiry, ports and orphan cleanup."""
import base64,json,os,signal,socket,subprocess,time
from pathlib import Path
import httpx
import pytest

REPO=Path(__file__).resolve().parents[2]
PYTHON=os.sys.executable

def port():
    with socket.socket() as s:s.bind(("127.0.0.1",0));return s.getsockname()[1]

def run(root,command,*extra):
    args=[PYTHON,str(REPO/"scripts/gas_dev.py"),command,"--runtime",str(root),*extra]
    return subprocess.run(args,capture_output=True,text=True,timeout=45)

def wait_closed(port):
    end=time.monotonic()+20
    while time.monotonic()<end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1",port))!=0:return
        time.sleep(.1)
    raise AssertionError("orphan listener remained")

def test_private_preview_amap_configuration_and_proxy_scope(tmp_path):
    key, security_code = "a" * 32, "b" * 32
    settings = tmp_path / "amap-credentials.json"
    settings.write_text(json.dumps({"key": key, "security_js_code": security_code}))
    settings.chmod(0o600)
    ports = [port() for _ in range(4)]
    args = ["--model", "off", "--ttl-minutes", "2", "--python", PYTHON]
    for name, value in zip(("web-port", "api-port", "gateway-port", "model-port"), ports):
        args.extend(["--" + name, str(value)])
    started = run(tmp_path, "start", *args)
    assert started.returncode == 0, started.stderr
    try:
        access = json.loads((tmp_path / "access.json").read_text())
        login = (access["username"] + ":" + access["password"]).encode()
        headers = {"Authorization": "Basic " + base64.b64encode(login).decode()}
        origin = f"http://127.0.0.1:{ports[0]}"
        path = origin + "/_AMapService/v3/config/district"
        query = {"key": key, "keywords": "500157", "subdistrict": "0", "extensions": "all"}
        with httpx.Client(trust_env=False, timeout=5) as client:
            assert client.get(origin + "/").status_code == 401
            assert client.get(path, params=query).status_code == 401
            page = client.get(origin + "/", headers=headers)
            assert page.status_code == 200
            assert key in page.text and "/_AMapService" in page.text
            assert security_code not in page.text and "securityJsCode" not in page.text
            assert client.get(path, params=query, headers={**headers, "Origin": "https://invalid.test"}).status_code == 403
            assert client.get(path, params={**query, "key": "x" * 32}, headers=headers).status_code == 400
            assert client.get(path, params=[*query.items(), ("key", "x" * 32)], headers=headers).status_code == 400
            assert client.get(path, params={**query, "keywords": "110000"}, headers=headers).status_code == 400
            assert client.get(path, params={**query, "jscode": security_code}, headers=headers).status_code == 400
            assert client.get(path, params={**query, "callback": "x); alert(1)"}, headers=headers).status_code == 400
            assert client.get(origin + "/_AMapService/v3/place/text", headers=headers).status_code == 404
            assert client.head(path, params=query, headers=headers).status_code == 405
            assert client.post(path, params=query, headers={**headers, "Origin": origin}).status_code == 405
    finally:
        run(tmp_path, "stop")


def test_private_preview_lifecycle(tmp_path, monkeypatch):
    # Server-side opt-in cannot be inherited from the calling shell.
    monkeypatch.setenv("GAS_DISPOSITION_DEMO", "1")
    monkeypatch.setenv("GAS_CUV_COLLABORATION_DEMO", "1")
    assert (REPO/"web/dist/index.html").is_file(),"run production build first"
    ports=[port() for _ in range(4)]
    assert len(set(ports))==4
    extra=["--model","off","--ttl-minutes","2","--python",PYTHON]
    for name,value in zip(("web-port","api-port","gateway-port","model-port"),ports):extra.extend(["--"+name,str(value)])
    first=run(tmp_path,"start",*extra)
    assert first.returncode==0,first.stderr
    state=json.loads((tmp_path/"state.json").read_text())
    service_state=json.loads(first.stdout)
    assert service_state["frontend"] == "running"
    assert service_state["fastapi"] == "running"
    assert service_state["agent_status"] == "ready"
    assert service_state["qwen_status"] == "stopped"
    try:
        access=json.loads((tmp_path/"access.json").read_text())
        assert service_state["disposition_demo"] is False
        assert service_state["cuv_collaboration_demo"] is False
        assert "operator" not in access
        headers={"Authorization":"Basic "+base64.b64encode((access["username"]+":"+access["password"]).encode()).decode()}
        origin=f"http://127.0.0.1:{ports[0]}"
        with httpx.Client(trust_env=False,timeout=10) as client:
            deadline=time.monotonic()+15
            while True:
                try:
                    if client.get(origin+"/",headers=headers).status_code==200:break
                except httpx.HTTPError:pass
                assert time.monotonic()<deadline;time.sleep(.1)
            assert client.get(origin+"/").status_code==401
            assert client.head(origin+"/").status_code==401
            assert client.head(origin+"/",headers=headers).status_code==200
            assert client.get(origin+"/assets/missing-chunk.js",headers=headers).status_code==404
            assert client.head(origin+"/assets/missing-chunk.js",headers=headers).status_code==404
            assert client.get(origin+"/gas",headers=headers).status_code==200
            disposition = origin + "/api/v1/gas/engineering/events/syn-gas-event-isolation/disposition"
            assert client.get(disposition, headers=headers).status_code == 404
            assert client.post(disposition, headers={**headers, "Origin": origin}, json={}).status_code == 405
            assert client.head(origin+"/api/v1/gas/engineering/assets",headers=headers).status_code==405
            assert client.get(origin+"/api/v1/gas/engineering/assets",headers=headers).json()["total"]==204
            assert client.get(origin+"/api/v1/gas/engineering/assets",headers={**headers,"Forwarded":"for=127.0.0.1"}).status_code==403
            assert client.get(origin+"/",headers={**headers,"Host":"attacker.test"}).status_code==403
            query=origin+"/api/v1/gas/engineering/agent/query"
            assert client.post(query,headers=headers,json={"question":"查看事件"}).status_code==403
            good=client.post(query,headers={**headers,"Origin":origin},json={"question":"查看事件"})
            assert good.status_code==200 and good.json()["parser_source"]=="local_rule_not_llm"
            assert good.headers["X-Request-ID"] == good.json()["request_id"]
            assert client.delete(origin+"/api/v1/gas/engineering/assets",headers=headers).status_code==405
            assert client.post(origin+"/api/v1/agent/chat",headers={**headers,"Origin":origin},json={}).status_code==405
            accessfile=tmp_path/"access.json";raw=accessfile.read_text()
            accessfile.write_text("{}")
            assert client.get(origin+"/api/v1/gas/engineering/assets",headers=headers).status_code==503
            accessfile.write_text(json.dumps({**access,"expires_at":time.time()-1}))
            assert client.get(origin+"/",headers=headers).status_code==401
            accessfile.write_text(raw)
        second=run(tmp_path,"start",*extra)
        assert second.returncode==0 and json.loads(second.stdout)["already_running"]
        assert json.loads((tmp_path/"state.json").read_text())["supervisor"]==state["supervisor"]
        # SIGKILL is the failure case: independent reaper must close child listeners.
        os.kill(state["supervisor"]["pid"],signal.SIGKILL)
        wait_closed(ports[0]);wait_closed(ports[1])
        cleanup_deadline=time.monotonic()+20
        while (tmp_path/"access.json").exists() and time.monotonic()<cleanup_deadline:time.sleep(.1)
        assert not (tmp_path/"access.json").exists()
        restored=run(tmp_path,"start",*extra)
        assert restored.returncode==0,restored.stderr
        stopped=run(tmp_path,"stop")
        assert stopped.returncode==0 and json.loads(stopped.stdout)["stopped"]
        wait_closed(ports[0]);wait_closed(ports[1])
    finally:
        run(tmp_path,"stop")

def test_private_preview_disposition_roles_and_routes(tmp_path):
    ports = [port() for _ in range(4)]
    assert len(set(ports)) == 4
    args = ["--model", "off", "--ttl-minutes", "2", "--python", PYTHON]
    for name, value in zip(("web-port", "api-port", "gateway-port", "model-port"), ports):
        args.extend(["--" + name, str(value)])
    started = run(tmp_path, "start", *args, "--disposition-demo")
    assert started.returncode == 0, started.stderr
    try:
        assert json.loads(started.stdout)["disposition_demo"] is True
        access = json.loads((tmp_path / "access.json").read_text())
        operator = access["operator"]
        assert access["actor"] != operator["actor"]
        assert access["platform_token"] != operator["platform_token"]
        assert operator["password"] not in started.stdout
        assert not run(tmp_path, "credentials", "--credential-role", "operator").stdout

        origin = f"http://127.0.0.1:{ports[0]}"
        prefix = origin + "/api/v1/gas/engineering"
        endpoint = prefix + "/events/syn-gas-event-isolation/disposition"

        def headers(identity):
            login = f"{identity['username']}:{identity['password']}".encode()
            return {"Origin": origin, "Authorization": "Basic " + base64.b64encode(login).decode()}

        reader = headers(access)
        writer = headers(operator)
        update = {"expected_revision": 0, "target_status": "acknowledged", "reason": "测试：已确认合成事件"}
        with httpx.Client(trust_env=False, timeout=10) as client:
            assert client.get(prefix + "/agent/health", headers=reader).status_code == 200
            assert client.get(endpoint, headers=reader).status_code == 403
            assert client.post(endpoint, headers={**reader, "X-Water-Token": operator["platform_token"]},
                               json=update).status_code == 403
            assert client.post(endpoint, headers={**writer, "Origin": "https://invalid.test"},
                               json=update).status_code == 403
            assert client.post(endpoint, headers={**writer, "Forwarded": "for=127.0.0.1"},
                               json=update).status_code == 403
            assert client.get(endpoint, headers=writer).json()["revision"] == 0
            assert client.post(prefix + "/assets", headers=writer, json=update).status_code == 405
            assert client.post(endpoint + "/unexpected", headers=writer, json=update).status_code == 405
            applied = client.post(endpoint, headers=writer, json=update)
            assert applied.status_code == 200, applied.text
            assert applied.json()["revision"] == 1
            assert client.post(endpoint, headers=writer, json=update).status_code == 409
            assert client.get(endpoint, headers=writer).json()["revision"] == 1
        assert run(tmp_path, "start", *args).returncode != 0
        again = run(tmp_path, "start", *args, "--disposition-demo")
        assert again.returncode == 0 and json.loads(again.stdout)["already_running"]
    finally:
        stopped = run(tmp_path, "stop")
        assert stopped.returncode == 0 and json.loads(stopped.stdout)["stopped"]
        assert not (tmp_path / "access.json").exists()


def test_old_preview_ports_and_unbounded_budget_rejected(tmp_path):
    for extra in (["--web-port","24073"],["--api-port","24081"],["--ttl-minutes","99999"]):
        result=run(tmp_path,"start",*extra)
        assert result.returncode!=0

def test_public_runtime_permissions_rejected(tmp_path):
    tmp_path.chmod(0o755)
    assert run(tmp_path,"start","--model","off").returncode!=0


def test_dead_group_leader_does_not_orphan_workers(tmp_path):
    import importlib.util
    spec=importlib.util.spec_from_file_location("gas_dev_lifecycle_module",REPO/"scripts/gas_dev.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    env={**os.environ,"GAS_DEV_PARENT":str(os.getpid())}
    child_code="import time;time.sleep(120)"
    leader_code="import subprocess,sys,time,pathlib;p=subprocess.Popen([sys.executable,'-c',sys.argv[2]]);pathlib.Path(sys.argv[1]).write_text(str(p.pid));time.sleep(120)"
    output=tmp_path/"worker.pid"
    leader=subprocess.Popen([PYTHON,"-c",leader_code,str(output),child_code],env=env,start_new_session=True)
    row={**module.identity(leader.pid),"group_owner":os.getpid()}
    try:
        deadline=time.monotonic()+5
        while not output.exists() and time.monotonic()<deadline:time.sleep(.05)
        worker=int(output.read_text())
        worker_row=module.identity(worker)
        leader.kill();leader.wait(timeout=5)
        assert not module.alive(row) and module.alive(worker_row)
        assert module.group_alive(row)
        module.kill_children({"children":[row]})
        assert not module.group_alive(row)
        assert not module.alive(worker_row)
    finally:
        module.kill_children({"children":[row]})
        if leader.poll() is None:leader.kill();leader.wait()


def test_actual_ttl_stops_local_preview(tmp_path):
    ports=[port() for _ in range(4)]
    extra=["--model","off","--ttl-minutes","1","--python",PYTHON]
    for name,value in zip(("web-port","api-port","gateway-port","model-port"),ports):extra.extend(["--"+name,str(value)])
    start=time.monotonic()
    assert run(tmp_path,"start",*extra).returncode==0
    try:
        deadline=start+85
        while time.monotonic()<deadline:
            state=json.loads((tmp_path/"state.json").read_text())
            if state.get("status")=="stopped" and not (tmp_path/"access.json").exists():break
            time.sleep(.5)
        else:raise AssertionError("TTL did not stop preview and revoke credentials")
        assert time.monotonic()-start>=59
        wait_closed(ports[0]);wait_closed(ports[1])
    finally:run(tmp_path,"stop")


def test_busy_model_port_does_not_block_rule_preview(tmp_path):
    web_port, api_port, gateway_port = [port() for _ in range(3)]
    with socket.socket() as other_model:
        other_model.bind(("127.0.0.1", 0))
        model_port = other_model.getsockname()[1]
        other_model.listen()
        args = [
            "--model", "auto", "--ttl-minutes", "2", "--python", PYTHON,
            "--web-port", str(web_port), "--api-port", str(api_port),
            "--gateway-port", str(gateway_port), "--model-port", str(model_port),
        ]
        try:
            started = run(tmp_path, "start", *args)
            assert started.returncode == 0, started.stderr
            status = json.loads(started.stdout)
            assert status["frontend"] == "running"
            assert status["fastapi"] == "running"
            assert status["agent_status"] == "ready"
            deadline = time.monotonic() + 5
            while status["qwen_status"] == "starting" and time.monotonic() < deadline:
                time.sleep(.1)
                status = json.loads(run(tmp_path, "status", *args).stdout)
            assert status["qwen_status"] == "failed"
            assert str(model_port) in status["startup_error"]
            assert status["agent"]["parser_source"] == "local_rule_not_llm"
            children = json.loads((tmp_path/"state.json").read_text())["children"]
            assert [child["component"] for child in children] == ["services"]
        finally:
            assert run(tmp_path, "stop", *args).returncode == 0
        with socket.create_connection(("127.0.0.1", model_port), timeout=2):
            pass


def test_committed_gateway_source_matches_pin_and_rejects_drift(tmp_path):
    import importlib.util
    import shutil

    spec = importlib.util.spec_from_file_location("gas_dev_gateway_pin", REPO / "scripts/gas_dev.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.gateway_source_matches(REPO)
    shutil.copytree(REPO / "gateway", tmp_path / "gateway")
    assert module.gateway_source_matches(tmp_path)
    with (tmp_path / "gateway/app.py").open("a", encoding="utf-8") as handle:
        handle.write("\n# changed\n")
    assert not module.gateway_source_matches(tmp_path)


def test_private_preview_cuv_audit_survives_restart_and_only_projects_own_comparison(tmp_path):
    ports = [port() for _ in range(4)]
    args = ["--model", "off", "--ttl-minutes", "2", "--python", PYTHON, "--cuv-collaboration-demo"]
    for name, value in zip(("web-port", "api-port", "gateway-port", "model-port"), ports, strict=True):
        args.extend(["--" + name, str(value)])
    origin = f"http://127.0.0.1:{ports[0]}"
    prefix = origin + "/api/v1/gas/engineering"
    collaboration = prefix + "/cuv-replay/collaboration"
    question = {
        "question": "请比较当前合成隔离和恢复方案的取舍",
        "tool_hint": "cuv.query",
        "event_id": "syn-gas-event-isolation",
        "asset_id": "syn-gas-pipe-K-003",
        "cuv_at": "2026-11-18T01:42:00Z",
    }

    def start_preview():
        started = run(tmp_path, "start", *args)
        assert started.returncode == 0, started.stderr
        state = json.loads(started.stdout)
        assert state["cuv_collaboration_demo"] and not state["disposition_demo"]
        return json.loads((tmp_path / "access.json").read_text())

    def headers(identity):
        login = f"{identity['username']}:{identity['password']}".encode()
        return {"Origin": origin, "Authorization": "Basic " + base64.b64encode(login).decode()}

    try:
        first = start_preview()
        reader, operator = headers(first), headers(first["operator"])
        feedback = "合成样例反馈，验证审计与读投影隔离"
        with httpx.Client(trust_env=False, timeout=12) as client:
            assert client.get(collaboration, headers=reader).status_code == 403
            assert client.post(collaboration + "?source=other", headers=operator, json={}).status_code == 405
            assert client.get(
                prefix + "/events/syn-gas-event-isolation/disposition", headers=operator,
            ).status_code == 404
            base = client.get(collaboration, headers=operator).json()
            assert base["revision"] == 0 and base["persistence"] == "sqlite_local"
            update = {
                "input_sha256": base["input_sha256"], "expected_revision": 0, "confirmed": True,
                "criteria": {
                    "objective": "min_topology_restoration_hours",
                    "max_peak_affected_services": 10, "restore_by_hours": 4,
                },
                "feedback": feedback,
            }
            assert client.post(collaboration, headers=reader, json=update).status_code == 403
            assert client.post(collaboration, headers=operator, json=update).status_code == 200
            assert client.post(collaboration, headers=operator, json=update).status_code == 409
            result = client.post(prefix + "/agent/query", headers=operator, json=question)
            assert result.status_code == 200
            projection = result.json()["tools"][0]["data"]["confirmed_comparison"]
            assert projection["revision"] == 1
            assert projection["comparison"]["leading_plan_ids"] == ["syn-gas-cuv-plan-hold-isolation"]
            assert set(projection) == {"revision", "input_sha256", "criteria", "comparison"}
            assert feedback not in result.text
            public = client.post(prefix + "/agent/query", headers=reader, json=question)
            assert public.status_code == 200
            assert not public.json()["tools"][0]["data"].get("confirmed_comparison")
        assert run(tmp_path, "stop").returncode == 0
        second = start_preview()
        assert first["operator"]["platform_token"] != second["operator"]["platform_token"]
        assert first["operator"]["actor"] == second["operator"]["actor"]
        with httpx.Client(trust_env=False, timeout=12) as client:
            saved = client.get(collaboration, headers=headers(second["operator"])).json()
            assert saved["revision"] == 1
            assert saved["audit"][0]["actor_id"] == second["operator"]["actor"]
            assert saved["audit"][0]["feedback"] == feedback
    finally:
        run(tmp_path, "stop")
