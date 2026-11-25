"""Independent synthetic plan-timeline replay for the competition API.

This module reads only the competition dataset. It does not import engineering
G01/T01 code, does not read engineering S01, and does not perform hydraulic,
water-quality or probabilistic calculations.
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Any

ALGORITHM_VERSION = "demo-timeline-0.1.1"
SUPPORTED_OUTPUTS = ["timeline_request", "recovery_times", "plan_completion", "service_first_restored"]
LIMITATIONS = [
    "按声明的合成动作回放计划图通路；不是水力、水质、真实供水能力或现场执行。",
    "恢复时间以同一 as_of 和观察窗口为基准；null 不能当作 0 或窗口上限。",
    "备用供水行动没有图操作，不计为通路恢复。",
]


def _metric(status: str, value: float | None, reason: str, horizon: float, **extra: Any) -> dict[str, Any]:
    metric = {
        "valueHours": value,
        "status": status,
        "reason": reason,
        "criterionId": "topological_reachability",
        "timeBasis": "planned",
        "observationHorizonHours": horizon,
    }
    metric.update(extra)
    return metric


def _find(items: list[dict[str, Any]], key: str, value: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get(key) == value), None)


def _resolve(dataset: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    horizon = request.get("horizonHours")
    if not isinstance(horizon, (int, float)) or isinstance(horizon, bool) or not 0 < horizon <= 720:
        return {"error": "观察窗口必须是 0—720 小时内的有限数。"}
    scenario = _find(dataset["scenarios"], "id", request["scenarioId"])
    if scenario is None:
        return {"error": f"未知情景：{request['scenarioId']}"}
    isolation = _find(dataset["isolationPlans"], "id", request["isolationPlanId"])
    if isolation is None or isolation.get("scenarioId") != scenario["id"]:
        return {"error": "隔离方案不存在或不属于当前情景。"}
    tasks = [
        task for task in dataset.get("timeline", {}).get("tasks", [])
        if task.get("scenarioId") == scenario["id"] and task.get("planId") == request["planId"]
    ]
    if _find(dataset["plans"], "id", request["planId"]) is None or not tasks:
        return {"error": f"未知候选方案：{request['planId']}"}
    if "targetServiceIds" not in request or request["targetServiceIds"] is None:
        return {"error": "必须明确目标服务集合；空集合需显式传入 []。"}
    targets = request["targetServiceIds"]
    known = {service["id"] for service in dataset["services"]}
    if any(service_id not in known for service_id in targets):
        return {"error": "目标服务集合包含未知ID。"}
    if len(targets) != len(set(targets)):
        return {"error": "目标服务集合不能重复。"}
    return {"scenario": scenario, "isolation": isolation, "tasks": tasks, "targets": targets}


def _reachable(dataset: dict[str, Any], scenario: dict[str, Any], isolation: dict[str, Any], restored: set[str]) -> set[str]:
    unavailable = set(scenario["failedPipeIds"]) | set(isolation["closedPipeIds"])
    adjacency: dict[str, list[str]] = {node["id"]: [] for node in dataset["nodes"]}
    for pipe in dataset["pipes"]:
        if pipe["id"] in unavailable and pipe["id"] not in restored:
            continue
        adjacency.setdefault(pipe["fromNodeId"], []).append(pipe["toNodeId"])
        adjacency.setdefault(pipe["toNodeId"], []).append(pipe["fromNodeId"])
    sources = [source for source in scenario["activeSourceIds"] if source in adjacency]
    reachable = set(sources)
    queue = list(sources)
    while queue:
        current = queue.pop(0)
        for nxt in adjacency.get(current, []):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)
    return reachable


def _available(dataset: dict[str, Any], reachable: set[str], service_id: str) -> bool:
    service = _find(dataset["services"], "id", service_id)
    return bool(service and service["nodeId"] in reachable)


def _schedule(tasks: list[dict[str, Any]], action_schedule: Any) -> dict[str, Any]:
    finishes = {task["actionId"]: task.get("finishOffsetHours") for task in tasks}
    violations: list[str] = []
    evidence: list[str] = []
    if action_schedule is None or action_schedule == []:
        return {"finishes": finishes, "violations": violations, "evidence": evidence}
    if not isinstance(action_schedule, list):
        return {"finishes": finishes, "violations": ["任务时间表必须是数组。"], "evidence": evidence}
    known = {task["actionId"]: task for task in tasks}
    seen: set[str] = set()
    for entry in action_schedule:
        if not isinstance(entry, dict):
            violations.append("任务时间表的每一项都必须是对象。")
            continue
        action_id = entry.get("actionId")
        if not isinstance(action_id, str) or not action_id:
            violations.append("任务时间表中的行动ID必须是非空字符串。")
            continue
        if action_id in seen:
            violations.append(f"行动 {action_id} 的任务结束信息重复。")
            continue
        seen.add(action_id)
        if action_id not in known:
            violations.append(f"行动 {action_id} 不属于当前方案。")
            continue
        finish = entry.get("finishOffsetHours")
        start = entry.get("startOffsetHours")
        duration = entry.get("durationHours")
        values = [finish, start, duration]
        if any(value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0) for value in values):
            violations.append(f"行动 {action_id} 的时间必须是非负有限小时数。")
            continue
        if start is not None and duration is not None:
            calculated = start + duration
            if not math.isfinite(calculated):
                violations.append(f"行动 {action_id} 的开始+持续必须为有限小时数。")
                continue
            if finish is not None and abs(finish - calculated) > 1e-9:
                violations.append(f"行动 {action_id} 的结束时间与开始+持续不一致。")
                continue
            finish = calculated
        finishes[action_id] = finish
        evidence.append(f"synthetic:user-assumption:{action_id}")
    return {"finishes": finishes, "violations": violations, "evidence": evidence}


def _completion(tasks: list[dict[str, Any]], finishes: dict[str, float | None], horizon: float) -> dict[str, Any]:
    missing = [task for task in tasks if finishes.get(task["actionId"]) is None]
    if missing:
        return _metric("insufficient_data", None, f"缺少任务结束记录：{'、'.join(task['label'] for task in missing)}。", horizon)
    value = max(finishes[task["actionId"]] for task in tasks)
    return _metric("succeeded", value, "全部任务结束时间已声明；方案完成时间不等于服务恢复时间。", horizon, beyondHorizon=value > horizon)


def check_timeline(dataset: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve(dataset, request)
    if "error" in resolved:
        return {
            "status": "invalid_input", "canCompute": False, "supportedOutputs": SUPPORTED_OUTPUTS,
            "unsupportedOutputs": [], "availableOutputs": [], "missingItems": [resolved["error"]],
            "authorizationChecked": False, "registryVersion": "1.0.0",
            "requestedSemantics": "planned_topological_reachability", "evidenceIds": [],
        }
    schedule = _schedule(resolved["tasks"], request.get("actionSchedule"))
    if schedule["violations"]:
        return {
            "status": "invalid_input", "canCompute": False, "supportedOutputs": SUPPORTED_OUTPUTS,
            "unsupportedOutputs": [], "availableOutputs": [], "missingItems": schedule["violations"],
            "calculationStatus": "invalid_input", "authorizationChecked": False,
            "registryVersion": "1.0.0",
            "requestedSemantics": "planned_topological_reachability", "evidenceIds": [],
        }
    completion = _completion(resolved["tasks"], schedule["finishes"], request["horizonHours"])
    missing = [] if completion["status"] == "succeeded" else [completion["reason"]]
    return {
        "status": "succeeded",
        "canCompute": True,
        "supportedOutputs": SUPPORTED_OUTPUTS,
        "unsupportedOutputs": [],
        "availableOutputs": [
            "timeline_request",
            *(["service_first_restored"] if resolved["targets"] else []),
            *(["recovery_times", "plan_completion"] if completion["status"] == "succeeded" else []),
        ],
        "missingItems": missing,
        "calculationStatus": "partial" if missing else "succeeded",
        "authorizationChecked": False,
        "registryVersion": "1.0.0",
        "requestedSemantics": "planned_topological_reachability",
        "evidenceIds": [
            f"synthetic:timeline:{dataset.get('timeline', {}).get('version', 'unknown')}",
            f"scenario:{resolved['scenario']['id']}",
            f"plan:{request['planId']}",
            *resolved["isolation"]["evidenceIds"],
        ],
    }


def _build_timeline(dataset: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    resolved = _resolve(dataset, request)
    horizon = request["horizonHours"]
    if "error" in resolved:
        return {
            "status": "invalid_input", "timelineStatus": "insufficient_data",
            "scenarioId": request["scenarioId"], "isolationPlanId": request["isolationPlanId"],
            "planId": request["planId"], "targetServiceIds": request.get("targetServiceIds") or [],
            "horizonHours": horizon, "asOf": dataset.get("timeline", {}).get("asOf", ""),
            "planCompletion": _metric("insufficient_data", None, resolved["error"], horizon),
            "firstTargetServiceRestored": _metric("not_applicable", None, "输入不完整", horizon),
            "allTargetServicesRestored": _metric("not_applicable", None, "输入不完整", horizon),
            "serviceFirstRestored": {}, "events": [], "evidenceIds": [], "issues": [resolved["error"]],
            "limitations": ["没有生成时间线；输入错误不能当作恢复时间为0。"],
            "dataMode": "synthetic", "sourceStatus": "constructed",
            "algorithmVersion": ALGORITHM_VERSION, "timeBasis": "planned",
            "criterionId": "topological_reachability",
        }
    scenario, isolation, tasks, targets = resolved["scenario"], resolved["isolation"], resolved["tasks"], resolved["targets"]
    schedule = _schedule(tasks, request.get("actionSchedule"))
    if schedule["violations"]:
        return {
            "status": "invalid_input", "timelineStatus": "insufficient_data",
            "scenarioId": scenario["id"], "isolationPlanId": isolation["id"],
            "planId": request["planId"], "targetServiceIds": list(targets),
            "horizonHours": horizon, "asOf": dataset.get("timeline", {}).get("asOf", ""),
            "planCompletion": _metric("insufficient_data", None, "；".join(schedule["violations"]), horizon),
            "firstTargetServiceRestored": _metric("not_applicable", None, "输入错误，未计算服务恢复时间。", horizon),
            "allTargetServicesRestored": _metric("not_applicable", None, "输入错误，未计算服务恢复时间。", horizon),
            "serviceFirstRestored": {}, "events": [], "evidenceIds": [],
            "issues": schedule["violations"],
            "limitations": ["任务时间表或输入不合法，没有生成时间线；错误输入不能当作恢复时间为0。", *LIMITATIONS],
            "dataMode": "synthetic", "sourceStatus": "constructed",
            "algorithmVersion": ALGORITHM_VERSION, "timeBasis": "planned",
            "criterionId": "topological_reachability",
        }
    completion = _completion(tasks, schedule["finishes"], horizon)
    definitions = [
        event for event in dataset.get("timeline", {}).get("events", [])
        if event.get("scenarioId") == scenario["id"] and event.get("planId") == request["planId"]
        and event.get("effectiveOffsetHours", 1e9) <= horizon
    ]
    definitions.sort(key=lambda event: (event["effectiveOffsetHours"], event["id"]))
    initial = _reachable(dataset, scenario, isolation, set())
    first_restored: dict[str, float] = {}
    for service_id in targets:
        if _available(dataset, initial, service_id):
            first_restored[service_id] = 0
    events = []
    restored: set[str] = set()
    for at_hours in sorted({event["effectiveOffsetHours"] for event in definitions}):
        group = [event for event in definitions if event["effectiveOffsetHours"] == at_hours]
        for event in group:
            restored.update(event["edgeIds"])
        reachable = _reachable(dataset, scenario, isolation, restored)
        for service_id in targets:
            if service_id not in first_restored and _available(dataset, reachable, service_id):
                first_restored[service_id] = at_hours
        events.append({
            "atHours": at_hours,
            "actionIds": list(dict.fromkeys(event["actionId"] for event in group)),
            "restoredEdgeIds": list(dict.fromkeys(edge for event in group for edge in event["edgeIds"])),
            "serviceAvailability": {service_id: _available(dataset, reachable, service_id) for service_id in targets},
            "evidenceIds": list(dict.fromkeys(ref for event in group for ref in event["evidenceIds"])),
        })
    service_metrics = {}
    for service_id in targets:
        if _available(dataset, initial, service_id):
            service_metrics[service_id] = _metric("already_available", 0, "故障隔离后、计划动作前已可达。", horizon)
        elif service_id in first_restored:
            service_metrics[service_id] = _metric("succeeded", first_restored[service_id], "按声明的图操作首次恢复通路。", horizon, beyondHorizon=first_restored[service_id] > horizon)
        else:
            service_metrics[service_id] = _metric("not_recovered_within_horizon", None, "观察窗口内没有声明事件使该服务恢复。", horizon, lowerBoundHours=horizon, lowerBoundExclusive=True)
    not_initial = [service_id for service_id in targets if not _available(dataset, initial, service_id)]
    restored_targets = [service_id for service_id in not_initial if service_id in first_restored]
    if not targets:
        first_target = _metric("not_applicable", None, "用户没有选择目标服务。", horizon)
    elif not not_initial:
        first_target = _metric("already_available", 0, "所有目标在计划动作前已经可达。", horizon)
    elif not restored_targets:
        first_target = _metric("not_recovered_within_horizon", None, "没有目标在观察窗口内首次恢复。", horizon, lowerBoundHours=horizon, lowerBoundExclusive=True)
    else:
        first_target = _metric("succeeded", min(first_restored[service_id] for service_id in restored_targets), "初始不可用目标中最早首次恢复的时刻。", horizon)
    if not targets:
        all_target = _metric("not_applicable", None, "用户没有选择目标服务。", horizon)
    elif all(_available(dataset, initial, service_id) for service_id in targets):
        all_target = _metric("already_available", 0, "所有目标在计划动作前已经同时可达。", horizon)
    else:
        all_at = next((event["atHours"] for event in events if all(event["serviceAvailability"].get(service_id) for service_id in targets)), None)
        all_target = _metric("not_recovered_within_horizon", None, "观察窗口内没有时刻使全部目标同时可达。", horizon, lowerBoundHours=horizon, lowerBoundExclusive=True) if all_at is None else _metric("succeeded", all_at, "首次全部目标同时可达；不代表之后持续可达。", horizon, beyondHorizon=all_at > horizon)
    evidence = list(dict.fromkeys([
        f"synthetic:timeline:{dataset.get('timeline', {}).get('version', 'unknown')}",
        f"scenario:{scenario['id']}", f"plan:{request['planId']}",
        *isolation["evidenceIds"],
        *(ref for event in definitions for ref in event["evidenceIds"]),
        *schedule["evidence"],
    ]))
    issues = list(dict.fromkeys([*schedule["violations"], *([completion["reason"]] if completion["status"] == "insufficient_data" else [])]))
    status = "invalid_input" if schedule["violations"] else ("partial" if completion["status"] == "insufficient_data" else "succeeded")
    return {
        "status": status, "timelineStatus": "succeeded", "scenarioId": scenario["id"],
        "isolationPlanId": isolation["id"], "planId": request["planId"],
        "targetServiceIds": list(targets), "horizonHours": horizon,
        "asOf": dataset.get("timeline", {}).get("asOf", ""),
        "planCompletion": completion, "firstTargetServiceRestored": first_target,
        "allTargetServicesRestored": all_target, "serviceFirstRestored": service_metrics,
        "events": events, "evidenceIds": evidence, "issues": issues,
        "limitations": LIMITATIONS, "dataMode": "synthetic", "sourceStatus": "constructed",
        "algorithmVersion": ALGORITHM_VERSION, "timeBasis": "planned",
        "criterionId": "topological_reachability",
    }


def build_timeline(dataset: dict[str, Any], request: dict[str, Any]) -> dict[str, Any]:
    """Bind every response to the request and timeline data that produced it."""
    return {
        **_build_timeline(dataset, request),
        "inputRequest": deepcopy(request),
        "timelineDataVersion": dataset.get("timeline", {}).get("version", "unknown"),
    }
