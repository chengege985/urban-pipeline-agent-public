"""水脉守护比赛端独立 FastAPI 服务。

只提供合成数据演示契约，不挂载工程目录、不连接工程数据库、不控制真实设施。
启动：
    ../.venv-platform/bin/python -B -m uvicorn server.app:app --host 127.0.0.1 --port 8090
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field

from .contracts import DecisionConstraints, HealthOverride, PipeRecord, StrictInput, TimelineRequest as TimelineInput, frozen_context
from .timeline import ALGORITHM_VERSION, build_timeline, check_timeline

from .demo_engine import (
    DISCLAIMER,
    compare_plans,
    dataset_for_api,
    evaluate_health,
    find_pipe,
    load_dataset,
    is_unsupported_question,
    simulate_impact,
    template_assistant,
)
from .llm_assistant import enabled as llm_enabled
from .llm_assistant import run_llm_assistant

MAX_BODY_BYTES = 1_048_576


class EvaluateRequest(StrictInput):
    pipeId: str | None = None
    pipe: PipeRecord | None = None
    override: HealthOverride = Field(default_factory=HealthOverride)


class SimulateRequest(StrictInput):
    scenarioId: str
    isolationPlanId: str | None = None


class CompareRequest(StrictInput):
    scenarioId: str
    planIds: list[str] | None = None
    isolationPlanId: str | None = None
    constraints: DecisionConstraints | None = None


class AssistantRequest(StrictInput):
    question: str
    context: dict[str, Any] = Field(default_factory=dict)


app = FastAPI(
    title="WaterPulse AI 合成演示 API",
    version="demo-api-0.3.1",
    description="仅使用合成数据；DemoHealth 不是正式健康度、失效概率或水力模型。",
)

_origins = [
    origin.strip()
    for origin in os.getenv(
        "COMPETITION_CORS_ORIGINS",
        "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:4174",
    ).split(",")
    if origin.strip()
]



class BodyLimitMiddleware:
    """Read at most the advertised limit, including headerless/chunked requests.

    Buffer only the bounded body before FastAPI parses it, so an over-limit
    stream always becomes 413 rather than a swallowed receive/JSON error.
    """
    def __init__(self, app, max_body_bytes: int = MAX_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers", [])).get(b"content-length")
        if length is not None:
            try:
                size = int(length)
            except ValueError:
                await JSONResponse(status_code=400, content={"detail": "无效 Content-Length"})(scope, receive, send)
                return
            if size < 0:
                await JSONResponse(status_code=400, content={"detail": "无效 Content-Length"})(scope, receive, send)
                return
            if size > self.max_body_bytes:
                await self._too_large(scope, receive, send)
                return
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > self.max_body_bytes:
                await self._too_large(scope, receive, send)
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        pending = True

        async def replay():
            nonlocal pending
            if pending:
                pending = False
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _too_large(self, scope, receive, send):
        await JSONResponse(status_code=413, content={"detail": "请求体超过 1 MiB 演示上限"})(scope, receive, send)


app.add_middleware(BodyLimitMiddleware)

# CORS wraps the body gate as well, so clients can read its 400/413 details.
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    allow_credentials=False,
)


@app.exception_handler(KeyError)
async def unknown_key(_request: Request, exc: KeyError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ValueError)
async def invalid_input(_request: Request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health():
    return {
        "status": "ok",
        "mode": "synthetic",
        "version": "demo-api-0.3.1",
        "llm_enabled": llm_enabled(),
        "disclaimer": DISCLAIMER,
    }


@app.get("/api/v1/demo/meta")
def meta():
    dataset = load_dataset()
    return {
        "status": "ok",
        "mode": "demo",
        "appVersion": dataset["meta"]["appVersion"],
        "dataVersion": dataset["meta"]["dataVersion"],
        "modelVersion": dataset["meta"]["modelVersion"],
        "disclaimer": DISCLAIMER,
        "features": {
            "isolationPlans": True,
            "constraints": True,
            "timeline": True,
            "timelineVersion": ALGORITHM_VERSION,
            "humanReview": "browser-local",
            "realModel": llm_enabled(),
        },
    }


@app.get("/api/v1/demo/dataset")
def dataset():
    return dataset_for_api()


@app.post("/api/v1/demo/evaluate")
def evaluate(payload: EvaluateRequest):
    current = payload.pipe.model_dump() if payload.pipe is not None else None
    if current is not None and payload.pipeId is not None and current["id"] != payload.pipeId:
        raise HTTPException(status_code=422, detail="pipeId 与 pipe.id 不一致")
    if current is None:
        if not payload.pipeId:
            raise HTTPException(status_code=422, detail="需要 pipeId 或完整 pipe")
        current = find_pipe(load_dataset(), payload.pipeId)
    return evaluate_health(current, payload.override.model_dump(exclude_unset=True))


@app.post("/api/v1/demo/simulate")
def simulate(payload: SimulateRequest):
    return simulate_impact(load_dataset(), payload.scenarioId, payload.isolationPlanId)


def _timeline_dataset(payload: TimelineInput):
    dataset = load_dataset()
    scenario = next((item for item in dataset["scenarios"] if item["id"] == payload.scenarioId), None)
    if scenario is None:
        raise KeyError(f"未知情景：{payload.scenarioId}")
    isolation = next((item for item in dataset["isolationPlans"] if item["id"] == payload.isolationPlanId), None)
    if isolation is None:
        raise KeyError(f"未知隔离方案：{payload.isolationPlanId}")
    if isolation["scenarioId"] != scenario["id"]:
        raise ValueError("隔离方案不属于当前情景")
    if not any(plan["id"] == payload.planId for plan in dataset["plans"]):
        raise KeyError(f"未知候选方案：{payload.planId}")
    return dataset


@app.post("/api/v1/demo/timeline/check")
def timeline_check(payload: TimelineInput):
    return check_timeline(_timeline_dataset(payload), payload.model_dump())


@app.post("/api/v1/demo/timeline")
def timeline(payload: TimelineInput):
    return build_timeline(_timeline_dataset(payload), payload.model_dump())


@app.post("/api/v1/demo/compare")
def compare(payload: CompareRequest):
    return compare_plans(
        load_dataset(),
        payload.scenarioId,
        payload.planIds,
        payload.constraints.model_dump() if payload.constraints is not None else None,
        payload.isolationPlanId,
    )


@app.post("/api/v1/demo/assistant")
def assistant(payload: AssistantRequest):
    dataset = load_dataset()
    context = frozen_context(payload.context)
    if is_unsupported_question(payload.question):
        return template_assistant(dataset, payload.question, context)
    generated = run_llm_assistant(dataset, payload.question, context)
    if generated is not None:
        return generated
    response = template_assistant(dataset, payload.question, context)
    if llm_enabled():
        response["fallbackReason"] = "模型服务未返回可核验的受限工具结果，已回退确定性模板。"
        response["limitations"].append(response["fallbackReason"])
    return response
