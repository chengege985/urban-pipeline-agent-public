"""Local-only real FastAPI gateway for M1-B-03 browser regression.
Uses an isolated source copy, synthetic fixtures, SQLite and output tree.
Test session cookies are not production authentication. No production file is written.
"""
from __future__ import annotations
import contextvars
import copy
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import sys
import threading
from http.cookies import SimpleCookie
from pathlib import Path

WORK = Path(sys.argv[1]).resolve()
DIST = Path(sys.argv[2]).resolve()
SOURCE = Path(sys.argv[3]).resolve()
WORK.mkdir(parents=True, exist_ok=True)
ENGINE = WORK / "engine"
shutil.copytree(SOURCE / "src", ENGINE / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
(ENGINE / "data/synthetic").mkdir(parents=True)
shutil.copy2(SOURCE / "data/synthetic/m1a_unified.json", ENGINE / "data/synthetic/m1a_unified.json")
source_hashes = {str(p.relative_to(ENGINE)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in ENGINE.rglob("*") if p.is_file()}
(WORK / "source_manifest.json").write_text(json.dumps(source_hashes, indent=2))
sys.path.insert(0, str(ENGINE))
sys.dont_write_bytecode = True
for key in list(os.environ):
    if key.startswith(("WATER_", "AGENT_", "OPENAI_", "LANGSMITH_", "LANGCHAIN_")):
        os.environ.pop(key)
os.environ.update(WATER_LLM_MODE="mock", AGENT_ENABLED="false")
RUNTIME = WORK / "runtime"
RUNTIME.mkdir()
phase = contextvars.ContextVar("report_phase", default=None)
report_checks = []
requests = []
outbound = []
counts = {"calculate_assessment": 0, "simulate_snapshot": 0, "compare_snapshot": 0, "runtime_execute": 0}

def file_audit(event, args):
    if event == "sqlite3.connect":
        location = Path(str(args[0])).resolve()
        if not location.is_relative_to(RUNTIME):
            raise RuntimeError("test denied non-isolated SQLite path")
    if event == "open":
        name, mode, flags = args
        if not isinstance(name, (str, bytes, os.PathLike)):
            return
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        if writing:
            location = Path(os.fsdecode(name)).resolve()
            if not location.is_relative_to(WORK):
                raise RuntimeError("test denied write outside workspace: " + str(location))
            if phase.get() is not None:
                phase.get()["file_writes"].append(str(location.relative_to(WORK)))

sys.addaudithook(file_audit)

def denied_connect(*args, **kwargs):
    outbound.append("blocked outbound socket")
    raise RuntimeError("M1-B-03 isolated backend forbids outbound sockets")
socket.socket.connect = denied_connect

from fastapi.responses import FileResponse, JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import event
from src.platform import services
from src.platform.app import create_app
from src.platform.schemas import SnapshotPayload
from src.platform.store import TaskRow
import uvicorn

sessions = json.loads(sys.stdin.readline())["sessions"]
assert set(sessions) == {"alice", "bob"}
tokens = {actor: secrets.token_urlsafe(32) for actor in sessions}
app = create_app(RUNTIME, bootstrap=False, start_worker=True)
store, runtime = app.state.store, app.state.runtime

def set_principals(dataset_ids):
    os.environ["WATER_PRINCIPALS"] = json.dumps({tokens[actor]: {
        "actor_id": "m1b-" + actor, "scope": "internal", "modes": ["synthetic"],
        "datasets": dataset_ids, "permissions": ["run"], "corpora": [],
    } for actor in sessions})

for name in ("calculate_assessment", "simulate_snapshot", "compare_snapshot"):
    original = getattr(services, name)
    def counted(*args, _name=name, _original=original, **kwargs):
        counts[_name] += 1
        if phase.get() is not None:
            phase.get()["compute_calls"].append(_name)
        return _original(*args, **kwargs)
    setattr(services, name, counted)
original_execute = runtime.execute

def counted_execute(*args, **kwargs):
    counts["runtime_execute"] += 1
    if phase.get() is not None:
        phase.get()["compute_calls"].append("runtime_execute")
    return original_execute(*args, **kwargs)
runtime.execute = counted_execute

@event.listens_for(store.engine, "before_cursor_execute")
def sql_audit(conn, cursor, statement, parameters, context, executemany):
    if phase.get() is not None and re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", statement, re.I):
        phase.get()["sql_writes"].append(statement)

original_snapshot = store.get_snapshot

def counted_snapshot(snapshot_id):
    current = phase.get()
    if current is not None:
        current["snapshot_reads"].append(snapshot_id)
        if snapshot_id != current["expected_snapshot_id"]:
            current["foreign_snapshot_reads"].append(snapshot_id)
    return original_snapshot(snapshot_id)
store.get_snapshot = counted_snapshot

base_payload = json.loads((ENGINE / "data/synthetic/m1a_unified.json").read_text())
fixtures = {}
for case in ("complete", "unknown_resource", "partial_population", "selected_time_missing", "unknown_cost"):
    payload = copy.deepcopy(base_payload)
    payload["name"] += " / M1-B-03 " + case
    if case == "partial_population":
        next(row for row in payload["services"] if row["service_id"] == "syn-households-C")["count_value"] = None
    if case == "selected_time_missing":
        payload["value_profile"]["criteria"] = [{"metric": "restoration_hours", "direction": "min", "lower": 0, "upper": 8, "weight": 1}]
    if case == "unknown_cost":
        next(row for row in payload["actions"] if row["action_id"] == "syn-backup")["cost_fen"] = None
    snapshot = store.save_snapshot(SnapshotPayload.model_validate(payload), "synthetic:m1b-03:" + case)
    sid = snapshot["snapshot_id"]
    fixtures[case] = {"snapshot_id": sid}
set_principals([value["snapshot_id"] for value in fixtures.values()])
client = TestClient(app)  # No lifespan: fixtures are executed synchronously before any worker starts.
for case, value in fixtures.items():
    request = {"snapshot_id": value["snapshot_id"], "as_of": base_payload["as_of"],
               "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate", "horizon_hours": 8,
               "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
               "budget_fen": 500000, "resource_capacities": {"repair_team": 2, "water_truck": 2}}
    if case == "unknown_resource":
        request["resource_capacities"].pop("water_truck")
    response = client.post("/api/v1/decision-runs", json=request, headers={"X-Water-Token": tokens["alice"], "X-Water-Client": "unified-react", "Idempotency-Key": "m1b03-seed-" + case})
    assert response.status_code == 202, (case, response.text)
    value["run_id"] = response.json()["run_id"]
    assert runtime.run_one()
    value["run_status"] = store.get_task(value["run_id"])["status"]
    report = client.get("/api/v1/runs/" + value["run_id"] + "/report", headers={"X-Water-Token": tokens["alice"]})
    assert report.status_code == 200, (case, report.text)
    value["report"] = report.json()

# A genuine partial topology Run remains reportable without loading a Dataset view.
partial_sid = fixtures["partial_population"]["snapshot_id"]
partial_request = {"snapshot_id": partial_sid, "as_of": base_payload["as_of"],
                   "fault_edge_id": "syn-fault", "isolation_plan_id": "syn-isolate", "horizon_hours": 8}
partial_response = client.post("/api/v1/simulation-runs", json=partial_request, headers={"X-Water-Token": tokens["alice"], "X-Water-Client": "unified-react", "Idempotency-Key": "m1b03-seed-partial-simulation"})
assert partial_response.status_code == 202, partial_response.text
partial_rid = partial_response.json()["run_id"]
assert runtime.run_one()
partial_report = client.get("/api/v1/runs/" + partial_rid + "/report", headers={"X-Water-Token": tokens["alice"]})
assert partial_report.status_code == 200, partial_report.text
assert partial_report.json()["business_status"] == "partial"
fixtures["partial_simulation"] = {"run_id": partial_rid, "snapshot_id": partial_sid, "report": partial_report.json(), "run_status": "partial"}

# Corruption is confined to this test database, after genuine backend computation.
source_run = fixtures["complete"]["run_id"]
with store.session.begin() as db:
    src = db.get(TaskRow, source_run)
    row = TaskRow(**{column.name: copy.deepcopy(getattr(src, column.name)) for column in TaskRow.__table__.columns})
    row.id = "m1b03-identity-conflict"
    row.key = "m1b03-conflict"
    row.result = {**row.result, "run_id": row.id, "snapshot_id": fixtures["unknown_cost"]["snapshot_id"]}
    db.add(row)
fixtures["identity_conflict"] = {"run_id": row.id, "snapshot_id": fixtures["complete"]["snapshot_id"]}
# Start wrapper inserts the incomplete fixture after recovery, avoiding worker claim races.
original_start = runtime.start

def start_with_pending():
    original_start()
    with store.session.begin() as db:
        src = db.get(TaskRow, source_run)
        row = TaskRow(**{column.name: copy.deepcopy(getattr(src, column.name)) for column in TaskRow.__table__.columns})
        row.id, row.key, row.status, row.result, row.finished_at = "m1b03-pending", "m1b03-pending", "running", None, None
        db.add(row)
runtime.start = start_with_pending
fixtures["pending"] = {"run_id": "m1b03-pending", "snapshot_id": fixtures["complete"]["snapshot_id"]}
(WORK / "fixtures.json").write_text(json.dumps(fixtures, ensure_ascii=False, indent=2), encoding="utf-8")
seed_counts = dict(counts)

def persisted_digest():
    with store.engine.connect() as conn:
        values = {}
        tables = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").scalars().all()
        for table in tables:
            values[table] = sorted([list(row) for row in conn.exec_driver_sql('SELECT * FROM "' + table + '"')], key=repr)
    artifacts = {str(p.relative_to(RUNTIME)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for folder in (RUNTIME / "outputs", RUNTIME / "data") if folder.exists()
                 for p in folder.rglob("*") if p.is_file()}
    return hashlib.sha256(json.dumps({"tables": values, "artifacts": artifacts}, sort_keys=True, default=str).encode()).hexdigest()

ALLOWED = {("GET", r"/api/v1/capabilities"), ("GET", r"/api/v1/datasets/[^/]+/view"),
           ("GET", r"/api/v1/runs"), ("GET", r"/api/v1/runs/[^/]+"),
           ("GET", r"/api/v1/runs/[^/]+/report"), ("POST", r"/api/v1/simulation-runs"),
           ("POST", r"/api/v1/decision-runs"), ("POST", r"/api/v1/timeline/check"),
           ("POST", r"/api/v1/timeline/evaluate")}

def actor_from(scope):
    cookies = SimpleCookie()
    cookies.load(dict(scope["headers"]).get(b"cookie", b"").decode())
    value = cookies["m1b_session"].value if "m1b_session" in cookies else ""
    return next((actor for actor, secret in sessions.items() if secrets.compare_digest(value, secret)), None)

def audit_view():
    return {"counts": counts, "seed_counts": seed_counts, "requests": requests, "report_checks": report_checks,
            "outbound_attempts": outbound, "real_model_calls": 0, "data_mode": "synthetic"}

async def gateway(scope, receive, send):
    if scope["type"] != "http":
        return await app(scope, receive, send)
    path, method = scope["path"], scope["method"]
    if method == "GET" and path in ("/", "/index.html"):
        return await FileResponse(DIST / "index.html")(scope, receive, send)
    if method == "GET" and path.startswith("/assets/"):
        candidate = (DIST / path.lstrip("/")).resolve()
        if candidate.is_relative_to(DIST) and candidate.is_file():
            return await FileResponse(candidate)(scope, receive, send)
    actor = actor_from(scope)
    if path == "/m1b/audit" and method == "GET":
        return await JSONResponse(audit_view() if actor == "alice" else {}, 200 if actor == "alice" else 403)(scope, receive, send)
    if not path.startswith("/api/"):
        return await JSONResponse({"detail": "not found"}, 404)(scope, receive, send)
    if not any(method == m and re.fullmatch(pattern, path) for m, pattern in ALLOWED):
        return await JSONResponse({"detail": "outside M1-B-03 test route scope"}, 403)(scope, receive, send)
    scoped = dict(scope)
    scoped["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() not in
                         (b"x-water-token", b"authorization", b"x-agent-actor", b"x-principal-id", b"x-water-principal")]
    if actor:
        scoped["headers"].append((b"x-water-token", tokens[actor].encode()))
    report = re.fullmatch(r"/api/v1/runs/([^/]+)/report", path) if method == "GET" else None
    item = {"method": method, "path": path, "actor": actor}
    current = None
    if report:
        try:
            expected_sid = store.get_task(report.group(1))["request"].get("snapshot_id")
        except KeyError:
            expected_sid = None
        current = {"path": path, "actor": actor, "expected_snapshot_id": expected_sid,
                   "compute_calls": [], "sql_writes": [], "file_writes": [], "snapshot_reads": [], "foreign_snapshot_reads": [],
                   "before_digest": persisted_digest()}
        marker = phase.set(current)
    async def logged(message):
        if message["type"] == "http.response.start":
            item["status"] = message["status"]
        await send(message)
    try:
        await app(scoped, receive, logged)
    finally:
        requests.append(item)
        if current is not None:
            phase.reset(marker)
            current["status"] = item.get("status")
            current["after_digest"] = persisted_digest()
            current["persisted_unchanged"] = current["before_digest"] == current["after_digest"]
            report_checks.append(current)

sock = socket.socket()
sock.bind(("127.0.0.1", 0))
server = uvicorn.Server(uvicorn.Config(gateway, log_level="info", access_log=False))
def stop_on_stdin():
    sys.stdin.readline()
    server.should_exit = True
threading.Thread(target=stop_on_stdin, daemon=True).start()
print(json.dumps({"ready": True, "port": sock.getsockname()[1], "fixtures": fixtures}), flush=True)
try:
    server.run(sockets=[sock])
finally:
    (WORK / "server_audit.json").write_text(json.dumps(audit_view(), ensure_ascii=False, indent=2), encoding="utf-8")
