"""Agent HTTP接口。身份由服务端认证上下文提供，本地演示需显式开启。"""
from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request

from src.platform.agent_schemas import AgentChatRequest, AgentResumeRequest
from src.platform.agent_store import Conflict
from src.platform.authz import authorize_snapshot, require_authenticated, require_permission
from src.platform.input_batches import BatchPrincipal

Key = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)]


def _principal(request: Request) -> BatchPrincipal:
    principal = require_authenticated(request)
    return BatchPrincipal(actor_id=principal.actor_id, scope=principal.scope)


def _runtime(request: Request):
    runtime = getattr(request.app.state, "agent_runtime", None)
    if runtime is None:
        raise HTTPException(503, "Agent能力未启用或模型/数据库未就绪")
    return runtime


def _authorize_run_snapshot(request: Request, runtime, snapshot_id: str | None) -> None:
    """Agent runs read snapshots; apply the same data gate as platform read paths."""
    if not snapshot_id:
        return
    try:
        snapshot = runtime.snapshots.get_snapshot(snapshot_id)
    except KeyError as exc:
        raise HTTPException(403, "当前主体无权读取该数据集") from exc
    authorize_snapshot(require_authenticated(request), snapshot, store=runtime.snapshots)


def agent_router():
    router = APIRouter(prefix="/api/v1/agent", tags=["agent"])

    @router.get("/health")
    def health(request: Request):
        runtime = getattr(request.app.state, "agent_runtime", None)
        return {"enabled": runtime is not None,
                "llm_mode": getattr(runtime, "llm", None).mode if runtime else None,
                "checkpoint": os.getenv("AGENT_CHECKPOINT", "memory"),
                "database_configured": bool(os.getenv("AGENT_DATABASE_URL")),
                "initialization_error": getattr(request.app.state, "agent_error", None)}

    @router.post("/chat", status_code=202)
    def chat(payload: AgentChatRequest, request: Request, idempotency_key: Key):
        principal = _principal(request)
        require_permission(require_authenticated(request), "run")
        runtime = _runtime(request)
        _authorize_run_snapshot(request, runtime, payload.snapshot_id)
        try:
            return runtime.submit(principal, payload, idempotency_key)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (KeyError, PermissionError) as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/threads/{thread_id}")
    def thread(thread_id: str, request: Request):
        principal = _principal(request)
        runtime = _runtime(request)
        try:
            view = runtime.store.get_thread(thread_id, principal)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        visible = []
        for run in view["runs"]:
            try:
                _authorize_run_snapshot(request, runtime, run.get("snapshot_id"))
                runtime.authorize_history(principal, run)
            except (HTTPException, PermissionError):
                continue
            visible.append(run)
        return {**view, "runs": visible}

    @router.post("/threads/{thread_id}/resume", status_code=202)
    def resume(thread_id: str, payload: AgentResumeRequest, request: Request, idempotency_key: Key):
        principal = _principal(request)
        require_permission(require_authenticated(request), "resume")
        if payload.action in {"accept", "reject", "revise"}:
            require_permission(require_authenticated(request), "review")
        runtime = _runtime(request)
        try:
            pending = runtime.store.find_interrupt(thread_id, payload.interrupt_id, principal)
            _authorize_run_snapshot(request, runtime, pending.get("snapshot_id"))
            return runtime.resume(pending["run_id"], payload, principal, idempotency_key)
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise HTTPException(403 if isinstance(exc, PermissionError) else 422, str(exc)) from exc

    @router.get("/runs/{run_id}")
    def run(run_id: str, request: Request):
        principal = _principal(request)
        runtime = _runtime(request)
        try:
            value = runtime.store.get_run(run_id, principal)
            _authorize_run_snapshot(request, runtime, value.get("snapshot_id"))
            runtime.authorize_history(principal, value)
            return value
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    @router.get("/runs/{run_id}/evidence")
    def evidence(run_id: str, request: Request):
        principal = _principal(request)
        runtime = _runtime(request)
        try:
            value = runtime.store.get_run(run_id, principal)
            _authorize_run_snapshot(request, runtime, value.get("snapshot_id"))
            runtime.authorize_history(principal, value)
            return runtime.store.evidence(run_id, principal)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc

    return router
