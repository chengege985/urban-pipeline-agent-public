from __future__ import annotations
import os
from collections.abc import Callable
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from src.platform.authz import LOOPBACK_BIND_HOSTS, LOCAL_RUNTIME_MODES, local_synthetic_anonymous_enabled, resolve_principal, validate_security_configuration
from src.gas_application.router import gas_engineering_router, gas_router
from src.gas_application.service import GasFixtureService, GasFixtureUnavailable

def _csv(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.getenv(name, default).split(",") if x.strip()]

def _origins() -> list[str]:
    out=[]
    for value in _csv("WATER_CORS_ORIGINS"):
        p=urlsplit(value)
        if value == "*" or "*" in value or p.scheme.lower() not in {"http","https"} or not p.netloc or p.username or p.password or p.path or p.query or p.fragment:
            raise RuntimeError("WATER_CORS_ORIGINS must contain explicit origins")
        out.append(f"{p.scheme.lower()}://{p.netloc.lower()}")
    return out

def _host_ok(host: str|None, allowed: list[str]) -> bool:
    if not host: return False
    candidate=host.lower().rstrip(".")
    for item in allowed:
        value=item.lower().strip().rstrip(".")
        if candidate == value: return True
        if value.startswith("*.") and candidate.endswith(value[1:]) and candidate != value[2:]: return True
    return False

def _has_untrusted_proxy_headers(request: Request) -> bool:
    for name in request.headers:
        lowered = name.lower()
        if (lowered == "forwarded" or lowered.startswith("x-forwarded-")
                or lowered.startswith("x-real-") or lowered in {"via", "forwarded-for", "forwarded-host", "forwarded-proto"}):
            return True
    return False

def _validate_bff_configuration(include_bff: bool) -> None:
    if not include_bff:
        return
    if os.getenv("GAS_BFF_MODE", "").strip().lower() != "development":
        raise RuntimeError("开发BFF必须显式使用GAS_BFF_MODE=development")
    runtime_mode = os.getenv("WATER_RUNTIME_MODE", "").strip().lower()
    if runtime_mode not in LOCAL_RUNTIME_MODES:
        raise RuntimeError("开发BFF仅允许local/development运行模式")
    bind_host = os.getenv("WATER_BIND_HOST", "").strip().lower()
    if bind_host not in LOOPBACK_BIND_HOSTS:
        raise RuntimeError("开发BFF仅允许回环监听")

def create_gas_app(
    *, gas_service: GasFixtureService | None = None, include_bff: bool = False,
    model_starting: Callable[[], bool] | None = None,
) -> FastAPI:
    validate_security_configuration()
    _validate_bff_configuration(include_bff)
    hosts=_csv("WATER_TRUSTED_HOSTS","127.0.0.1,localhost,testserver")
    if not hosts or any(x=="*" for x in hosts): raise RuntimeError("WATER_TRUSTED_HOSTS must be explicit")
    origins=_origins()
    @asynccontextmanager
    async def lifespan(_): yield
    app=FastAPI(title="WaterPipNet Gas Synthetic Service",version="gas-adapter-candidate-0.2.0",lifespan=lifespan)
    service=gas_service or GasFixtureService()
    app.state.gas_service=service
    app.state.bootstrap_mode="gas_synthetic_only"
    app.include_router(gas_router())
    app.include_router(gas_engineering_router(service, model_starting=model_starting))
    if include_bff and os.getenv("GAS_BFF_MODE", "").strip().lower() == "development":
        from .bff import bff_router
        app.include_router(bff_router(service))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    if origins:
        app.add_middleware(CORSMiddleware,allow_origins=origins,allow_methods=["GET","POST","DELETE","OPTIONS"],allow_headers=["Accept","Content-Type","X-Water-Token","X-Water-Client","X-Gas-BFF-Bootstrap","X-Gas-CSRF"],allow_credentials=True)
    def guard_error(request: Request, detail: str, status_code: int) -> JSONResponse:
        response = JSONResponse({"detail": detail}, status_code=status_code)
        origin = request.headers.get("Origin")
        if origin and origin in origins:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Vary"] = "Origin"
        return response

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not _host_ok(request.url.hostname, hosts):
            return guard_error(request, "Invalid host header", 400)
        origin=request.headers.get("Origin")
        base=f"{request.url.scheme}://{request.url.netloc}"
        if origin and origin!=base and origin not in set(origins):
            return guard_error(request, "origin denied", 403)
        if request.url.path.startswith("/bff/"):
            peer = request.client.host if request.client else None
            if peer not in LOOPBACK_BIND_HOSTS and peer != "testclient":
                return guard_error(request, "BFF仅允许本地访问", 401)
            if _has_untrusted_proxy_headers(request):
                return guard_error(request, "BFF不接受客户端转发身份", 401)
        if request.method=="OPTIONS": return await call_next(request)
        if not request.url.path.startswith("/bff/") and request.url.path!="/health":
            try: resolve_principal(request)
            except HTTPException as exc: return guard_error(request, str(exc.detail), exc.status_code)
        if local_synthetic_anonymous_enabled():
            peer=request.client.host if request.client else None
            if peer not in {"127.0.0.1","::1","localhost","testclient"}:
                return guard_error(request, "loopback only", 401)
            if _has_untrusted_proxy_headers(request):
                return guard_error(request, "forwarded identity denied", 401)
        return await call_next(request)
    @app.exception_handler(GasFixtureUnavailable)
    async def fixture_error(_, __): return JSONResponse({"detail":"fixture unavailable"},status_code=503)
    @app.exception_handler(RequestValidationError)
    async def validation_error(_, exc): return JSONResponse({"detail":[{"field":list(e["loc"]),"reason":e["msg"],"type":e["type"]} for e in exc.errors()[:100]]},status_code=422)
    @app.exception_handler(KeyError)
    async def missing(_, __): return JSONResponse({"detail":"object not found"},status_code=404)
    @app.get("/health")
    def health():
        return {"status":"ok","service":"gas-synthetic","bootstrap_mode":app.state.bootstrap_mode,"data_mode":"synthetic","dataset_id":"syn-gas-network-html54-0.1.0","agent_enabled":False,"real_data":False,"field_control":False,"access_mode":"token" if (os.getenv("WATER_PRINCIPALS") or os.getenv("WATER_API_TOKEN")) else "local_synthetic_anonymous" if local_synthetic_anonymous_enabled() else "authentication_required"}
    original_openapi = app.openapi
    def _openapi():
        schema = original_openapi()
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["WaterToken"] = {
            "type": "apiKey", "in": "header", "name": "X-Water-Token",
            "description": "服务端认证令牌；客户端标识不是认证凭据，数据集及操作权限单独校验。"}
        return schema
    app.openapi = _openapi
    return app
