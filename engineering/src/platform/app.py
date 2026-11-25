"""本地原型REST接口。启动：.venv-platform/bin/python -B -m src.platform。

默认启动一个持久化worker。所有写操作要求Idempotency-Key，并拒绝跨站请求；
仍只适合SSH隧道中的单用户演示，多人和真实试点须接单位认证与权限体系。
"""
from contextlib import asynccontextmanager
import hashlib
import logging
import os
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src.platform.errors import InputIssues
from src.unified.contracts import (
    AssessmentEnvelope,
    CapabilitiesResponse,
    ReportDocument,
    RunListEnvelope,
    RunView,
    SnapshotListEnvelope,
)
from src.unified.report import ReportNotReady, build_report_from_run

from . import VERSION
from .agent_api import agent_router
from .api_models import CLIENT_HEADER, COMMON_ERRORS, OPTIONAL_SECURITY
from .authz import (authorize_run, authorize_snapshot, authorize_assessment, resolve_principal,
                    local_synthetic_anonymous_enabled, require_authenticated, require_permission,
                    run_allowed, snapshot_allowed, validate_security_configuration)
from .dataset_api import dataset_router
from src.gas_application.router import gas_engineering_router, gas_router
from src.gas_application.service import GasFixtureUnavailable
from .timeline_api import timeline_router
from .runtime import Runtime
from .provenance import verify_synthetic_snapshot, with_trusted_assessment_provenance
from .schemas import AssistantRequest, DecisionRequest, FeedbackRequest, RecomputeRequest, ScenarioRequest, SnapshotPayload
from .store import Conflict, Store, fingerprint

ROOT = Path(__file__).resolve().parents[2]
Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]
CLIENT_IDS = frozenset({"local-prototype", "unified-react"})
logger = logging.getLogger(__name__)


def _csv_env(name: str, default: str = "") -> list[str]:
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def _cors_origins() -> list[str]:
    values = _csv_env("WATER_CORS_ORIGINS")
    normalized: list[str] = []
    for value in values:
        if value == "*" or "*" in value:
            raise RuntimeError("WATER_CORS_ORIGINS 不允许通配符")
        parsed = urlsplit(value)
        if (parsed.scheme.lower() not in {"http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment):
            raise RuntimeError("WATER_CORS_ORIGINS 必须是无路径的合法 Origin")
        normalized.append(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
    return normalized


def _host_allowed(host: str | None, allowed_hosts: list[str]) -> bool:
    if not host:
        return False
    candidate = host.lower().rstrip(".")
    for pattern in allowed_hosts:
        value = pattern.lower().strip().rstrip(".")
        if value == candidate:
            return True
        if value.startswith("*.") and candidate.endswith(value[1:]) and candidate != value[2:]:
            return True
    return False


def create_app(root: Path | None = None, *, start_worker=True, bootstrap=True,
               agent_runtime=None, agent_start_worker=True, gas_service=None) -> FastAPI:
    validate_security_configuration()
    root = Path(root or os.getenv("WATER_PLATFORM_ROOT", str(ROOT))).resolve()
    store = Store(root)
    runtime = Runtime(store, ROOT)

    @asynccontextmanager
    async def lifespan(app):
        if bootstrap:
            from src.adapters.scoring import provided_sample, RESULT

            sample_key = "builtin:legacy-0.1.1:" + hashlib.sha256(RESULT.read_bytes()).hexdigest()
            if store.imported(sample_key) is None:
                store.save_snapshot(provided_sample(), sample_key)
            s01_path = ROOT / "data/synthetic/s01.json"
            s01_key = "builtin:s01-0.1.0:" + hashlib.sha256(s01_path.read_bytes()).hexdigest()
            if store.imported(s01_key) is None:
                store.save_snapshot(SnapshotPayload.model_validate_json(s01_path.read_text(encoding="utf-8")), s01_key)
        if start_worker:
            runtime.start()
        if app.state.agent_runtime is None and os.getenv("AGENT_ENABLED") == "1":
            try:
                from .agent_runtime import build_agent_runtime
                app.state.agent_runtime = build_agent_runtime(root)
            except Exception as exc:
                # 旧接口仍启动；Agent明确未就绪，不伪装启用。
                app.state.agent_error = type(exc).__name__
                logger.exception("Agent初始化失败，旧接口继续运行")
        if app.state.agent_runtime is not None and agent_start_worker:
            app.state.agent_runtime.start()
        try:
            yield
        finally:
            if app.state.agent_runtime is not None:
                app.state.agent_runtime.close()
            runtime.close()
            store.engine.dispose()

    app = FastAPI(title="WaterPipNet供水原型", version=VERSION, lifespan=lifespan,
                  description="技术演示：不控制实际设施，不代表正式CUV或业务验收")
    app.state.store, app.state.runtime = store, runtime
    app.state.agent_runtime = agent_runtime
    app.state.agent_error = None
    app.include_router(agent_router())
    app.include_router(dataset_router(store))
    app.include_router(gas_router())
    app.include_router(gas_engineering_router(gas_service))
    app.include_router(timeline_router(store))
    trusted_hosts = _csv_env("WATER_TRUSTED_HOSTS", "127.0.0.1,localhost,testserver")
    if not trusted_hosts or any(value == "*" for value in trusted_hosts):
        raise RuntimeError("WATER_TRUSTED_HOSTS 必须限制到明确主机")
    cors_origins = _cors_origins()
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Accept", "Content-Type", "X-Water-Token", "X-Water-Client", "Idempotency-Key"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def local_guard(request: Request, call_next):
        # Validate Host before the CORS preflight short circuit. Otherwise an
        # invalid Host could receive a successful OPTIONS response from the
        # outer CORS middleware.
        if not _host_allowed(request.url.hostname, trusted_hosts):
            return JSONResponse({"detail": "Invalid host header"}, status_code=400)
        # CORS preflight carries no application credentials. CORSMiddleware
        # validates the configured origin and requested headers before the
        # actual request is sent.
        if request.method == "OPTIONS":
            return await call_next(request)
        # A marker or an actor name in a header is never authentication. Resolve
        # all credentials through the same server policy (including precedence).
        protected = request.method in {"POST", "PUT", "PATCH", "DELETE"} or request.url.path.startswith((
            "/api/v1/runs", "/api/v1/feedback"))
        try:
            if protected:
                principal = require_authenticated(request)
                if request.method == "POST" and request.url.path.startswith("/api/v1/timeline/"):
                    require_permission(principal, "run")
            elif request.url.path != "/health":
                resolve_principal(request)
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        origin = request.headers.get("Origin")
        allowed_origins = set(cors_origins)
        base_origin = f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin != base_origin and origin not in allowed_origins:
            return JSONResponse({"detail": "原型拒绝跨站调用"}, status_code=403)
        if local_synthetic_anonymous_enabled():
            peer = request.client.host if request.client else None
            if peer not in {"127.0.0.1", "::1", "localhost", "testclient"}:
                return JSONResponse({"detail": "本地匿名例外仅限直接回环连接"}, status_code=401)
            if any(request.headers.get(name) for name in
                   ("X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto", "Forwarded")):
                return JSONResponse({"detail": "本地匿名例外不接受转发身份头"}, status_code=401)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            # This is a protocol marker, not identity: token/principal and Origin
            # checks above remain authoritative. Both controlled clients share it.
            if request.headers.get("X-Water-Client") not in CLIENT_IDS:
                return JSONResponse({"detail": "缺少受控客户端标识"}, status_code=403)
            try:
                length = int(request.headers.get("Content-Length", "0"))
            except ValueError:
                return JSONResponse({"detail": "非法Content-Length"}, status_code=400)
            if length > 10 * 1024 * 1024:
                return JSONResponse({"detail": "原型单批输入不得超过10MiB"}, status_code=413)
            if len(await request.body()) > 10 * 1024 * 1024:
                return JSONResponse({"detail": "原型单批输入不得超过10MiB"}, status_code=413)
        return await call_next(request)

    @app.exception_handler(GasFixtureUnavailable)
    async def gas_fixture_unavailable(_, exc):
        return JSONResponse({"detail": "合成燃气 Fixture 当前不可用"}, status_code=503)

    @app.exception_handler(RequestValidationError)
    async def invalid(_, exc):
        return JSONResponse({"detail": [{"field": list(e["loc"]), "reason": e["msg"], "type": e["type"]}
                                        for e in exc.errors()[:100]]}, status_code=422)

    @app.exception_handler(InputIssues)
    async def input_issues(_, exc):
        return JSONResponse({"detail": exc.issues}, status_code=422)

    @app.exception_handler(Conflict)
    async def conflict(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(ReportNotReady)
    async def report_not_ready(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(KeyError)
    async def missing(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(ValueError)
    async def value_error(_, exc):
        return JSONResponse({"detail": str(exc)}, status_code=422)

    @app.get("/health")
    def health():
        return {"status": "ok", "version": VERSION, "worker_alive": bool(runtime.thread and runtime.thread.is_alive()),
                "llm_mode": runtime.gateway.mode, "agent_enabled": app.state.agent_runtime is not None,
                "access_mode": (
                    "token" if os.getenv("WATER_PRINCIPALS") or os.getenv("WATER_API_TOKEN")
                    else "local_synthetic_anonymous" if local_synthetic_anonymous_enabled()
                    else "authentication_required"
                )}

    @app.get("/api/v1/capabilities", response_model=CapabilitiesResponse)
    def capabilities(request: Request):
        from src.capabilities import list_calculation_capabilities

        principal = resolve_principal(request)
        datasets = [item for item in store.snapshots() if snapshot_allowed(principal, item, store=store)]
        return {"scoring": "legacy_demo_0.1.1", "simulation": "conditional_topology",
                "decision": "demo_policy_given_candidates", "llm_mode": runtime.gateway.mode,
                "formal_scoring": "unavailable", "professional_prediction": "unavailable",
                "hydraulic": "unavailable", "internal_cuv": "unavailable", "real_dispatch": "disabled",
                "note": "没有交付的能力返回受限状态，不以演示替代正式模型",
                "platform_version": VERSION,
                "datasets": [{"snapshot_id": item["snapshot_id"], "name": item["name"],
                              "data_mode": item["data_mode"], "source_status": item["source_status"],
                              "as_of": item["as_of"], "counts": item["counts"],
                              "validation_status": item["validation_status"]} for item in datasets],
                "data_modes": sorted({item["data_mode"] for item in datasets}),
                "algorithms": [{"capability_id": item["capability_id"], "semantics": item["semantics"],
                                "version": item["version"]}
                               for item in list_calculation_capabilities()]}

    @app.post("/api/v1/data-batches", status_code=201)
    def import_batch(payload: SnapshotPayload, request: Request, idempotency_key: Key):
        principal = require_authenticated(request)
        require_permission(principal, "import")
        if payload.data_mode == "synthetic":
            payload = verify_synthetic_snapshot(payload)
        snapshot_id = "snap-" + fingerprint(payload.model_dump(mode="json"))[:24]
        authorize_snapshot(principal, {"snapshot_id": snapshot_id, "data_mode": payload.data_mode})
        scoped_key = fingerprint([principal.actor_id, principal.scope, idempotency_key])
        return store.save_snapshot(payload, "http-import:" + scoped_key)

    @app.post("/api/v1/data-batches/sample", status_code=201)
    def import_sample(request: Request, idempotency_key: Key):
        principal = require_authenticated(request)
        require_permission(principal, "import")
        if "fixture_replay" not in principal.modes:
            raise HTTPException(403, "当前主体无权导入提供方样例")
        from src.adapters.scoring import provided_sample
        return import_batch(provided_sample(), request, idempotency_key)

    @app.get("/api/v1/snapshots", response_model=SnapshotListEnvelope, responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def snapshots(request: Request):
        principal = resolve_principal(request)
        return {"items": [item for item in store.snapshots() if snapshot_allowed(principal, item, store=store)]}

    @app.get("/api/v1/snapshots/{snapshot_id}", responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def snapshot(snapshot_id: str, request: Request):
        principal = resolve_principal(request)
        value = store.get_snapshot(snapshot_id)
        authorize_snapshot(principal, value, store=store)
        return value

    @app.get("/api/v1/assessments", response_model=AssessmentEnvelope, responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def assessments(request: Request, snapshot_id: str, pipe_id: str | None = None, grade: str | None = None,
                    limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
        principal = resolve_principal(request)
        snap = store.get_snapshot(snapshot_id)
        authorize_snapshot(principal, snap, store=store)
        rows = snap["payload"]["assessments"]
        if pipe_id:
            rows = [r for r in rows if r["pipe_id"] == pipe_id]
        if grade:
            rows = [r for r in rows if r["legacy_result"]["grade"] == grade]
        return {"snapshot_id": snapshot_id, "data_mode": snap["data_mode"], "as_of": snap["as_of"],
                "total": len(rows), "items": rows[offset:offset + limit]}

    def enqueue(kind, payload, key, request: Request):
        principal = require_authenticated(request)
        require_permission(principal, "run")
        if kind == "assessment":
            trusted = authorize_assessment(principal, payload.model_dump(mode="json"))
            payload = with_trusted_assessment_provenance(payload, trusted)
        if hasattr(payload, "snapshot_id"):
            snapshot = store.get_snapshot(payload.snapshot_id)
            authorize_snapshot(principal, snapshot, store=store)
        return store.enqueue(kind, payload.model_dump(mode="json"), key, principal)

    @app.post("/api/v1/assessment-runs", status_code=202, response_model=RunView)
    def assessment_run(payload: RecomputeRequest, request: Request, idempotency_key: Key):
        return enqueue("assessment", payload, idempotency_key, request)

    @app.post("/api/v1/simulation-runs", status_code=202, response_model=RunView)
    def simulation_run(payload: ScenarioRequest, request: Request, idempotency_key: Key):
        return enqueue("simulation", payload, idempotency_key, request)

    @app.post("/api/v1/decision-runs", status_code=202, response_model=RunView)
    def decision_run(payload: DecisionRequest, request: Request, idempotency_key: Key):
        return enqueue("decision", payload, idempotency_key, request)

    @app.post("/api/v1/assistant/tasks", status_code=202, response_model=RunView)
    def assistant_task(payload: AssistantRequest, request: Request, idempotency_key: Key):
        return enqueue("assistant", payload, idempotency_key, request)

    @app.get("/api/v1/runs", response_model=RunListEnvelope, responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def runs(request: Request, limit: int = Query(50, ge=1, le=100)):
        principal = resolve_principal(request)
        return {"items": [item for item in store.tasks(limit, principal=principal)
                           if run_allowed(principal, item, store)]}

    @app.get("/api/v1/runs/{run_id}", response_model=RunView, responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def task(run_id: str, request: Request):
        principal = resolve_principal(request)
        value = store.get_task(run_id)
        authorize_run(principal, value, store)
        return value

    @app.post("/api/v1/runs/{run_id}/replay", status_code=202, response_model=RunView, responses=COMMON_ERRORS,
              openapi_extra={"security": OPTIONAL_SECURITY, "parameters": [CLIENT_HEADER]})
    def replay(run_id: str, request: Request, idempotency_key: Key):
        principal = require_authenticated(request)
        require_permission(principal, "run")
        previous = store.get_task(run_id)
        authorize_run(principal, previous, store)
        if previous["status"] in {"queued", "running"}:
            raise Conflict("原任务尚未结束，不创建重复运行")
        if previous["kind"] == "assessment":
            trusted = authorize_assessment(principal, previous["request"])
            previous["request"] = with_trusted_assessment_provenance(
                RecomputeRequest.model_validate(previous["request"]), trusted
            ).model_dump(mode="json")
        return store.enqueue(previous["kind"], previous["request"], idempotency_key, principal)

    @app.get("/api/v1/runs/{run_id}/export")
    def export(run_id: str, request: Request):
        principal = resolve_principal(request)
        value = store.get_task(run_id)
        authorize_run(principal, value, store)
        return JSONResponse(value, headers={"Content-Disposition": f'attachment; filename="{value["run_id"]}.json"'})

    @app.get("/api/v1/runs/{run_id}/report", response_model=ReportDocument, responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def report(run_id: str, request: Request):
        principal = resolve_principal(request)
        value = store.get_task(run_id)
        authorize_run(principal, value, store)
        snapshot_id = ((value.get("request") or {}).get("snapshot_id")
                       or ((value.get("result") or {}).get("snapshot_id") if value["kind"] == "assessment" else None))
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise ReportNotReady("Run 没有关联快照，不能生成报告")
        try:
            snapshot = store.get_snapshot(snapshot_id)
        except KeyError:
            # Do not distinguish a missing snapshot from an inaccessible one.
            raise ReportNotReady("Run 关联的快照不可用，不能生成报告") from None
        authorize_snapshot(principal, snapshot, store=store)
        return build_report_from_run(value, snapshot, feedback=store.feedbacks(run_id, principal=principal))

    @app.post("/api/v1/feedback", status_code=201)
    def feedback(payload: FeedbackRequest, request: Request, idempotency_key: Key):
        principal = require_authenticated(request)
        require_permission(principal, "review")
        run = store.get_task(payload.run_id)
        authorize_run(principal, run, store)
        return store.feedback(payload, idempotency_key, principal)

    @app.get("/api/v1/feedback", responses=COMMON_ERRORS,
             openapi_extra={"security": OPTIONAL_SECURITY})
    def feedbacks(request: Request, run_id: str | None = None,
                  limit: int = Query(100, ge=1, le=500),
                  offset: int = Query(0, ge=0)):
        principal = resolve_principal(request)
        items = []
        for item in store.feedbacks(run_id, principal=principal, limit=limit, offset=offset):
            try:
                task_row = store.get_task(item["run_id"])
            except KeyError:
                continue
            if run_allowed(principal, task_row, store):
                items.append(item)
        return {"items": items}

    # Security is optional for local demo but explicit in the contract; de-duplicate
    # the scheme list produced by combining Security(...) with openapi_extra.
    original_openapi = app.openapi

    def _openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["WaterToken"] = {
            "type": "apiKey", "in": "header", "name": "X-Water-Token",
            "description": "服务端认证令牌；客户端标识不是认证凭据，数据集及操作权限单独校验。"}
        for path, methods in schema.get("paths", {}).items():
            for method, operation in methods.items():
                if not isinstance(operation, dict) or method not in {"get", "post", "put", "patch", "delete"}:
                    continue
                protected = method != "get" or path.startswith(("/api/v1/runs", "/api/v1/feedback")) or (
                    path.startswith("/api/v1/agent/"))
                if protected:
                    operation["security"] = [{"WaterToken": []}]
                elif path != "/health":
                    # Runtime read paths resolve a server principal as well;
                    # the explicit local synthetic exception is documented
                    # separately and is not an anonymous OpenAPI contract.
                    operation["security"] = [{"WaterToken": []}]
                if method != "get":
                    parameters = operation.setdefault("parameters", [])
                    parameters[:] = [p for p in parameters if p.get("name") != "X-Water-Client"]
                    parameters.append(CLIENT_HEADER)
        return schema

    app.openapi = _openapi
    return app
