"""比赛端可选的 OpenAI 兼容工具调用循环。

只有配置 ``COMPETITION_LLM_BASE_URL`` 时才启用；工具只读写合成数据集，
不接受任意 SQL、文件路径或真实设施操作。
"""
from __future__ import annotations

import ipaddress
import json
import os
import re
from urllib.parse import urlsplit
from typing import Any

import httpx

from .demo_engine import (
    LIMITATIONS,
    SUGGESTIONS,
    compare_plans,
    evaluate_health,
    find_pipe,
    simulate_impact,
    is_unsupported_question,
    template_assistant,
)
from .timeline import build_timeline, check_timeline

from .contracts import frozen_context

MAX_ROUNDS = 4
TIMEOUT_SECONDS = 30

# The model chooses a skill, not the user's budget, scenario or what-if state.
TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    }}
    for name, description in (
        ("evaluate_demo_health", "按已确认的管段和what-if上下文计算合成评分"),
        ("simulate_service_impact", "按已确认的情景和隔离方案计算拓扑影响"),
        ("compare_demo_plans", "按已确认的情景、预算、资源、依赖与候选集比较演示方案"),
        ("analyze_plan_timeline", "按已确认的方案、目标服务和观察窗口计算计划图通路时间线"),
    )
]


def enabled() -> bool:
    return bool(os.getenv("COMPETITION_LLM_BASE_URL", "").strip())


def _endpoint(base_url: str) -> str:
    parsed = urlsplit(base_url)
    host = parsed.hostname
    allowed = {item.strip().lower() for item in os.getenv(
        "COMPETITION_LLM_ALLOWED_HOSTS", "127.0.0.1,localhost"
    ).split(",") if item.strip()}
    if parsed.scheme.lower() not in {"http", "https"} or not host:
        raise ValueError("模型端点必须使用http(s)并包含主机")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("模型端点不得包含用户信息、查询或片段")
    if parsed.path not in {"", "/", "/v1"}:
        raise ValueError("模型端点路径必须是服务根或/v1")
    if host.lower() not in allowed:
        raise ValueError("模型端点不在服务端白名单")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
        if host.lower() not in allowed:
            raise ValueError("模型端点地址不在显式白名单")
    base = base_url.rstrip("/")
    return base if base.endswith("/chat/completions") else f"{base}/chat/completions"


def _execute_tool(
    dataset: dict[str, Any], name: str, arguments: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    state = frozen_context(context)
    if not isinstance(arguments, dict):
        raise ValueError("工具参数必须是对象")
    # Legacy model arguments may identify the same entity, but cannot change it.
    for key in ("pipeId", "scenarioId", "isolationPlanId", "planId", "targetServiceIds", "horizonHours", "actionSchedule"):
        if key in arguments and arguments[key] != state.get(key):
            raise ValueError(f"模型不得改变已确认的 {key}")
    if name == "evaluate_demo_health":
        if not state.get("pipeId"):
            return {"ok": False, "error": "请先确认管段"}
        pipe = find_pipe(dataset, state["pipeId"])
        assessment = evaluate_health(pipe, state.get("override"))
        return {
            "ok": assessment["status"] == "succeeded", "pipeId": pipe["id"],
            "assessment": assessment,
            "error": "评分数据不足" if assessment["status"] != "succeeded" else "",
            "evidenceIds": [f"data:{pipe['id']}", f"rule:demo-health-{assessment['version']}"],
        }
    if name in ("simulate_service_impact", "compare_demo_plans") and not state.get("scenarioId"):
        return {"ok": False, "error": "请先确认故障情景"}
    if name == "simulate_service_impact":
        impact = simulate_impact(dataset, state["scenarioId"], state.get("isolationPlanId"))
        return {"ok": impact["status"] == "succeeded", "impact": impact,
                "error": "情景数据不足" if impact["status"] != "succeeded" else "",
                "evidenceIds": impact["evidenceIds"]}
    if name == "compare_demo_plans":
        if "constraints" not in state:
            return {"ok": False, "error": "请先确认预算、资源和依赖条件"}
        comparison = compare_plans(dataset, state["scenarioId"], state.get("planIds"),
                                   state["constraints"], state.get("isolationPlanId"))
        return {
            "ok": comparison["status"] == "succeeded", "comparison": comparison,
            "error": "比较数据不足" if comparison["status"] != "succeeded" else "",
            "evidenceIds": [f"scenario:{comparison['scenarioId']}",
                            *[f"plan:{row['planId']}" for row in comparison["rows"]]],
        }
    if name == "analyze_plan_timeline":
        if not state.get("scenarioId") or not state.get("planId") or state.get("targetServiceIds") is None:
            return {"ok": False, "error": "请先确认情景、方案、目标服务和观察窗口"}
        request = {
            "scenarioId": state["scenarioId"],
            "isolationPlanId": state.get("isolationPlanId"),
            "planId": state["planId"],
            "targetServiceIds": state["targetServiceIds"],
            "horizonHours": state.get("horizonHours") or 8,
            "actionSchedule": state.get("actionSchedule", []),
        }
        gate = check_timeline(dataset, request)
        result = build_timeline(dataset, request)
        return {"ok": result["status"] == "succeeded", "gate": gate, "timeline": result,
                "error": "时间线部分可算" if result["status"] != "succeeded" else "",
                "evidenceIds": result["evidenceIds"]}
    return {"ok": False, "error": f"不允许的工具：{name}"}


def _tool_record(call: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function", {})
    name = function.get("name", "unknown")
    return {
        "id": f"tool-{call.get('id', name)}",
        "tool": name,
        "label": {
            "evaluate_demo_health": "DemoHealth 评价",
            "simulate_service_impact": "拓扑影响试算",
            "compare_demo_plans": "演示方案比较",
            "analyze_plan_timeline": "计划图通路时间线",
        }.get(name, name),
        "status": "succeeded" if result.get("ok") else "insufficient_data",
        "summary": "工具已执行" if result.get("ok") else str(result.get("error", "工具失败")),
        "detail": json.dumps(result, ensure_ascii=False)[:800],
    }


def run_llm_assistant(
    dataset: dict[str, Any],
    question: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """返回 AgentResponse；模型不可用时返回 None，由调用方回退模板。"""
    context = frozen_context(context)
    if is_unsupported_question(question):
        return template_assistant(dataset, question, context)
    base_url = os.getenv("COMPETITION_LLM_BASE_URL", "").strip()
    if not base_url:
        return None
    try:
        endpoint = _endpoint(base_url)
    except ValueError:
        return None
    api_key = os.getenv("COMPETITION_LLM_API_KEY", "").strip()
    if host := urlsplit(base_url).hostname:
        if host.lower() not in {"127.0.0.1", "localhost"} and not api_key:
            return None
    if os.getenv("COMPETITION_LLM_REQUIRE_API_KEY", "").strip().lower() in {"1", "true", "yes"} and not api_key:
        return None
    model = os.getenv("COMPETITION_LLM_MODEL", "Qwen3.6-35B-A3B")
    context = context or {}
    system = (
        "你是水脉守护比赛的合成演示助手。所有数据均为合成数据，不控制真实设施。"
        "你只能使用提供的工具读取合成管段、情景和方案；不得编造数值，不得提供失效概率、"
        "剩余寿命、水力计算或直接关阀/派单。回答使用中文，明确说明这是演示结果。"
        "需要事实时先调用工具，引用工具返回的 evidenceIds。"
    )
    user = f"问题：{question}\n已确认、不可修改的上下文：{json.dumps(context, ensure_ascii=False)}"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    tool_calls_out: list[dict[str, Any]] = []
    evidence_ids: list[str] = []
    tool_results: list[dict[str, Any]] = []
    try:
        with httpx.Client(timeout=TIMEOUT_SECONDS, trust_env=False, follow_redirects=False) as client:
            for _ in range(MAX_ROUNDS):
                response = client.post(
                    endpoint,
                    headers=headers,
                    json={
                        "model": model,
                        "messages": messages,
                        "tools": TOOL_SCHEMAS,
                        "tool_choice": "auto",
                        "temperature": 0.2,
                        "max_tokens": 1200,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
                response.raise_for_status()
                payload = response.json()
                message = payload["choices"][0]["message"]
                calls = message.get("tool_calls") or []
                if not calls:
                    # Output gate: free-form model prose is not authoritative business evidence.
                    # The model selects tools; numeric claims are rendered from recomputed facts.
                    if not tool_results or not all(result.get("ok") for result in tool_results):
                        return None
                    render_questions = {
                        "evaluate_demo_health": "解释评分",
                        "simulate_service_impact": "解释故障影响",
                        "compare_demo_plans": "比较方案",
                        "analyze_plan_timeline": "恢复时间",
                    }
                    # Preserve the first successful call order; repeated skills share one section.
                    ordered_tools = list(dict.fromkeys(call["tool"] for call in tool_calls_out))
                    if any(name not in render_questions for name in ordered_tools):
                        return None
                    sections = [template_assistant(dataset, render_questions[name], context)
                                for name in ordered_tools]
                    status_priority = {"succeeded": 0, "partial": 1, "insufficient_data": 2, "refused": 3}
                    response_out = dict(sections[0])
                    response_out.update({
                        "engine": "llm", "answerOrigin": "deterministic_tool_render",
                        "toolCalls": tool_calls_out,
                        "answer": "\n\n".join(section["answer"] for section in sections),
                        "status": max((section["status"] for section in sections), key=lambda status: status_priority.get(status, 2)),
                        "refused": any(section.get("refused", False) for section in sections),
                        "evidenceIds": list(dict.fromkeys([*evidence_ids, *[item for section in sections for item in section["evidenceIds"]]])),
                        "limitations": list(dict.fromkeys(item for section in sections for item in section["limitations"])),
                    })
                    response_out["limitations"].append("模型负责受限工具选择；数值和业务结论由确定性工具模板生成。")
                    return response_out
                messages.append(
                    {
                        "role": "assistant",
                        "content": message.get("content") or "",
                        "tool_calls": calls,
                    }
                )
                for call in calls:
                    function = call.get("function", {})
                    name = function.get("name", "")
                    try:
                        arguments = json.loads(function.get("arguments") or "{}")
                    except json.JSONDecodeError:
                        arguments = {}
                    try:
                        result = _execute_tool(dataset, name, arguments, context)
                    except (KeyError, ValueError, TypeError) as exc:
                        result = {"ok": False, "error": str(exc)}
                    tool_results.append(result)
                    tool_calls_out.append(_tool_record(call, result))
                    evidence_ids.extend(result.get("evidenceIds", []))
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call.get("id", ""),
                            "content": json.dumps(result, ensure_ascii=False),
                        }
                    )
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return None
    return None
