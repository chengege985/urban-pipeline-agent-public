"""比赛端独立合成演示引擎。

约束：
- 只读取 ``public_demo_data/dataset.json``，不导入工程 ``01_工程项目/src``。
- 所有管段、节点、服务、方案均为合成数据；不接真实设施或工程数据库。
- DemoHealth 是独立演示规则，不是正式 P/C/R/H、失效概率或水力模型。
"""
from __future__ import annotations

import json
import math
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from .contracts import frozen_context, validated_constraints, validated_override, validated_pipe
from .timeline import build_timeline, check_timeline

DATA_PATH = Path(__file__).resolve().parents[1] / "public_demo_data" / "dataset.json"

DISCLAIMER = (
    "合成数据演示，不控制真实设施；DemoHealth 不是正式健康度、失效概率、RUL 或水力计算结果。"
)
LIMITATIONS = [
    "所有数据均为合成演示数据，不对应任何真实设施。",
    "DemoHealth 是固定演示规则，不是正式 P/C/R/H、失效概率或剩余寿命。",
    "拓扑影响只表示通路是否可达，不代表压力、流量或实际缺供量。",
    "方案建议与人工审核均为本地演示记录，不派单、不关阀、不执行现场操作。",
]
SUGGESTIONS = [
    "为什么这段管段优先检查？",
    "如果这段爆管会影响哪些服务？",
    "有哪些维护方案可以比较？",
    "这个方案多久恢复？",
    "能预测爆管概率或剩余寿命吗？",
]
REFUSAL = (
    "当前演示不提供失效概率、剩余寿命、爆管预测或水力计算；"
    "也不能直接关阀、派单或下发工单。可以改为解释 DemoHealth 因子、"
    "查看合成情景的拓扑影响，或比较已给出的候选维护方案。"
)

DECISION_ALGORITHM_VERSION = "demo-decision-0.2.0"
REFERENCE_COST_YUAN = 40000.0

def is_unsupported_question(question: str) -> bool:
    return any(word in question for word in ("概率", "寿命", "RUL", "爆管预测", "水力", "关阀", "派单", "工单", "预测未来"))


FACTOR_DEFINITIONS = [
    ("material", "管材", 0.15),
    ("age", "管龄", 0.20),
    ("diameter", "管径", 0.10),
    ("depth", "埋深", 0.10),
    ("pressure", "运行压力", 0.15),
    ("leaks", "漏损历史", 0.15),
    ("repairs", "维修频次", 0.05),
    ("monitoring", "监测质量", 0.10),
]
MATERIAL_SCORES = {"PE": 2, "球墨铸铁": 2, "铸铁": 4, "钢": 3, "PVC": 2}
PRESSURE_SCORES = {"低压": 1, "常压": 2, "高压": 4}
MONITORING_SCORES = {"正常": 1, "关注": 3, "异常": 5}
RESOURCE_LABELS = {"repair_team": "抢修班组", "water_truck": "应急送水车", "monitoring_kit": "监测设备包"}
DEPENDENCY_LABELS = {"shutdown_window": "停水窗口已确认", "construction_permit": "施工许可已确认"}
DEFAULT_CONSTRAINTS = {
    "budgetYuan": 40000,
    "resourceCapacities": {"repair_team": 1, "water_truck": 1, "monitoring_kit": 1},
    "confirmedDependencies": ["shutdown_window"],
}


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _score_age(install_year: Any) -> int | None:
    year = _number(install_year)
    if year is None:
        return None
    age = 2026 - year
    if age < 0:
        return None
    if age < 10:
        return 1
    if age < 20:
        return 2
    if age < 30:
        return 3
    if age < 40:
        return 4
    return 5


def _score_diameter(value: Any) -> int | None:
    diameter = _number(value)
    if diameter is None or diameter <= 0:
        return None
    if diameter < 100:
        return 5
    if diameter < 200:
        return 4
    if diameter < 300:
        return 3
    if diameter < 400:
        return 2
    return 1


def _score_depth(value: Any) -> int | None:
    depth = _number(value)
    if depth is None or depth <= 0:
        return None
    if depth < 1.5:
        return 1
    if depth < 2.5:
        return 2
    if depth < 3.5:
        return 3
    if depth < 4.5:
        return 4
    return 5


def _score_leaks(value: Any) -> int | None:
    leaks = _number(value)
    if leaks is None:
        return None
    if leaks <= 0:
        return 1
    if leaks == 1:
        return 2
    if leaks == 2:
        return 3
    if leaks == 3:
        return 4
    return 5


def _score_repairs(value: Any) -> int | None:
    repairs = _number(value)
    if repairs is None:
        return None
    if repairs <= 0:
        return 1
    if repairs <= 2:
        return 2
    if repairs <= 4:
        return 3
    if repairs <= 6:
        return 4
    return 5


def _round1(value: float) -> float:
    """对齐 JS Math.round：正数四舍五入，不使用 Python 的银行家舍入。"""
    return math.floor(value * 10 + 0.5) / 10


def _naive_sum(values) -> float:
    """JS reduce 使用朴素浮点累加；Python 3.12 的 sum 会补偿求和，需显式对齐。"""
    total = 0.0
    for value in values:
        total += value
    return total


def _round3(value: float) -> float:
    return math.floor(value * 1000 + 0.5) / 1000


@lru_cache(maxsize=1)
def load_dataset() -> dict[str, Any]:
    if not DATA_PATH.exists():
        raise RuntimeError(f"缺少合成数据集：{DATA_PATH}")
    dataset = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    if not str(dataset.get("meta", {}).get("dataVersion", "")).startswith("synthetic-"):
        raise RuntimeError("比赛服务只允许 synthetic 数据版本")
    return dataset


def dataset_for_api() -> dict[str, Any]:
    dataset = json.loads(json.dumps(load_dataset(), ensure_ascii=False))
    dataset["meta"]["mode"] = "live"
    return dataset


def _factor_detail(key: str, pipe: dict[str, Any], score: int | None) -> str:
    if score is None:
        return "当前记录缺失或不适用，未计入权重"
    if key == "material":
        return f"{pipe.get('material')} → {score} 分"
    if key == "age":
        if pipe.get("installYear") is None:
            return "投运年份缺失"
        return f"{pipe['installYear']} 年投运 → {2026 - int(pipe['installYear'])} 年 → {score} 分"
    if key == "diameter":
        return f"{pipe['diameterMm']:g} mm → {score} 分"
    if key == "depth":
        return f"{pipe['depthM']:g} m → {score} 分"
    if key == "pressure":
        return f"{pipe.get('pressure')} → {score} 分"
    if key == "leaks":
        return f"{pipe.get('leakCount')} 次漏损 → {score} 分"
    if key == "repairs":
        return f"{pipe.get('repairCount')} 次维修 → {score} 分"
    return f"{pipe.get('monitoring')} → {score} 分"


def _score_pipe(pipe: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {**pipe, **(override or {})}
    scores: dict[str, int | None] = {
        "material": MATERIAL_SCORES.get(str(merged.get("material"))),
        "age": _score_age(merged.get("installYear")),
        "diameter": _score_diameter(merged.get("diameterMm")),
        "depth": _score_depth(merged.get("depthM")),
        "pressure": PRESSURE_SCORES.get(str(merged.get("pressure"))),
        "leaks": _score_leaks(merged.get("leakCount")),
        "repairs": _score_repairs(merged.get("repairCount")),
        "monitoring": MONITORING_SCORES.get(str(merged.get("monitoring"))),
    }
    valid = [(key, score) for key, score in scores.items() if score is not None]
    total_weight = _naive_sum(
        weight for key, _, weight in FACTOR_DEFINITIONS if scores[key] is not None
    )
    weighted = _naive_sum(
        weight * (scores[key] - 1) / 4
        for key, _, weight in FACTOR_DEFINITIONS
        if scores[key] is not None
    )
    risk = weighted / total_weight if total_weight else None
    denominator = weighted or 1.0
    factors = []
    for key, label, weight in FACTOR_DEFINITIONS:
        score = scores[key]
        weighted_risk = 0.0 if score is None else weight * (score - 1) / 4
        factors.append(
            {
                "key": key,
                "label": label,
                "weight": weight,
                "score": score,
                "weightedRisk": weighted_risk,
                "contribution": 0.0 if score is None else weighted_risk / denominator,
                "detail": _factor_detail(key, merged, score),
            }
        )
    missing = [label for key, label, _ in FACTOR_DEFINITIONS if scores[key] is None]
    if risk is None:
        return {
            "pipeId": pipe["id"],
            "status": "insufficient_data",
            "score": None,
            "grade": "数据不足",
            "confidence": 0,
            "validWeight": 0,
            "factors": factors,
            "missing": missing,
            "version": "demo-score-0.1.0",
            "summary": "所有演示指标均缺失，无法形成可解释的演示评价。",
        }
    score = _round1(100 * (1 - risk))
    grade = "基本健康" if score >= 80 else "关注" if score >= 65 else "重点检查"
    top = "、".join(
        factor["label"]
        for factor in sorted(
            [item for item in factors if item["score"] is not None],
            key=lambda item: item["contribution"],
            reverse=True,
        )[:2]
    )
    confidence = _round1(total_weight * 100)
    return {
        "pipeId": pipe["id"],
        "status": "succeeded",
        "score": score,
        "grade": grade,
        "confidence": confidence,
        "validWeight": _round1(total_weight * 100) / 100,
        "factors": factors,
        "missing": missing,
        "version": "demo-score-0.1.0",
        "summary": f"8 项指标中 {8 - len(missing)} 项有效，可信度 {confidence}%；当前主要贡献：{top or '无明显风险贡献'}。",
    }


def evaluate_health(pipe: dict[str, Any], override: dict[str, Any] | None = None) -> dict[str, Any]:
    return _score_pipe(validated_pipe(pipe), validated_override(override))


def find_pipe(dataset: dict[str, Any], pipe_id: str) -> dict[str, Any]:
    for pipe in dataset["pipes"]:
        if pipe["id"] == pipe_id:
            return pipe
    raise KeyError(f"未知管段：{pipe_id}")


def find_scenario(dataset: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    for scenario in dataset["scenarios"]:
        if scenario["id"] == scenario_id:
            return scenario
    raise KeyError(f"未知情景：{scenario_id}")


def find_isolation(dataset: dict[str, Any], isolation_id: str | None, scenario: dict[str, Any]) -> dict[str, Any] | None:
    target = isolation_id or scenario.get("defaultIsolationPlanId")
    for plan in dataset["isolationPlans"]:
        if plan["id"] == target:
            if plan["scenarioId"] != scenario["id"]:
                raise ValueError("隔离方案不属于当前情景")
            return plan
    if target is not None:
        raise KeyError(f"未知隔离方案：{target}")
    return None


def simulate_impact(
    dataset: dict[str, Any],
    scenario_id: str,
    isolation_plan_id: str | None = None,
) -> dict[str, Any]:
    scenario = find_scenario(dataset, scenario_id)
    node_ids = [node["id"] for node in dataset["nodes"]]
    pipe_ids_list = [pipe["id"] for pipe in dataset["pipes"]]
    service_ids = [service["id"] for service in dataset["services"]]
    if any(len(ids) != len(set(ids)) for ids in (node_ids, pipe_ids_list, service_ids)):
        raise ValueError("节点、管段和服务对象标识必须各自唯一")
    node_set = set(node_ids)
    if any(pipe[endpoint] not in node_set for pipe in dataset["pipes"] for endpoint in ("fromNodeId", "toNodeId")):
        raise ValueError("管段端点不在节点集合中")
    if any(service["nodeId"] not in node_set for service in dataset["services"]):
        raise ValueError("服务对象接入节点不存在")
    source_ids = {node["id"] for node in dataset["nodes"] if node.get("kind") == "source"}
    if not set(scenario["activeSourceIds"]).issubset(source_ids):
        raise ValueError("可用水源必须引用已定义的水源节点")
    isolation = find_isolation(dataset, isolation_plan_id, scenario)
    failed = set(scenario["failedPipeIds"])
    closed = set(isolation["closedPipeIds"] if isolation else [])
    unavailable = failed | closed
    pipe_ids = {pipe["id"] for pipe in dataset["pipes"]}
    unknown_failed = sorted(pipe_id for pipe_id in unavailable if pipe_id not in pipe_ids)
    if unknown_failed:
        raise ValueError(f"故障或隔离引用未知管段：{unknown_failed}")
    mismatch = bool(isolation and isolation["scenarioId"] != scenario["id"])

    adjacency: dict[str, list[str]] = {node["id"]: [] for node in dataset["nodes"]}
    for pipe in dataset["pipes"]:
        if pipe["id"] in unavailable:
            continue
        adjacency.setdefault(pipe["fromNodeId"], []).append(pipe["toNodeId"])
        adjacency.setdefault(pipe["toNodeId"], []).append(pipe["fromNodeId"])

    sources = [source for source in scenario["activeSourceIds"] if source in adjacency]
    reachable: set[str] = set(sources)
    queue = list(sources)
    while queue:
        current = queue.pop(0)
        for nxt in adjacency.get(current, []):
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)

    affected = []
    for service in dataset["services"]:
        if service["nodeId"] in reachable:
            continue
        affected.append(
            {
                "serviceId": service["id"],
                "name": service["name"],
                "type": service["type"],
                "critical": service["critical"],
                "reason": f"接入节点 {service['nodeId']} 与当前水源不连通",
                "households": service["households"],
                "facilities": service["facilities"],
            }
        )
    unreachable = [node["id"] for node in dataset["nodes"] if node["id"] not in reachable]
    status = "succeeded" if sources and not unknown_failed and not mismatch else "partial"
    limitations = [
        "仅表示合成管网中的拓扑通路影响，不代表压力、流量或实际缺供量。",
        "情景为演示假设，不包含失效概率、物理级联或真实阀门操作。",
    ]
    if isolation:
        limitations.append("给定隔离方案只改变合成图中的管段开闭状态，不表示已实际操作阀门。")
    if not sources:
        limitations.append("情景没有可用水源，无法形成正常基准。")
    if mismatch:
        limitations.append("隔离方案不属于当前情景，结果不可用于业务判断。")
    return {
        "scenarioId": scenario["id"],
        "isolationPlanId": isolation["id"] if isolation else "",
        "status": status,
        "failedPipeIds": list(scenario["failedPipeIds"]),
        "closedPipeIds": list(isolation["closedPipeIds"] if isolation else []),
        "valveIds": list(isolation["valveIds"] if isolation else []),
        "activeSourceIds": sources,
        "unreachableNodeIds": unreachable,
        "affectedServices": affected,
        "affectedCriticalServiceIds": [item["serviceId"] for item in affected if item["critical"]],
        "affectedHouseholds": sum(item["households"] for item in affected),
        "affectedFacilities": sum(item["facilities"] for item in affected),
        "evidenceIds": list(
            dict.fromkeys(
                [
                    f"scenario:{scenario['id']}",
                    *[f"pipe:{pipe_id}" for pipe_id in scenario["failedPipeIds"]],
                    *(isolation["evidenceIds"] if isolation else []),
                    *[f"service:{item['serviceId']}" for item in affected],
                ]
            )
        ),
        "limitations": limitations,
    }


def _constraints_or_default(constraints: dict[str, Any] | None) -> dict[str, Any]:
    # Only the standalone compare endpoint retains the documented demo default.
    return validated_constraints(DEFAULT_CONSTRAINTS if constraints is None else constraints)


def _violations(plan: dict[str, Any], constraints: dict[str, Any]) -> list[str]:
    violations = []
    if plan["costYuan"] > constraints["budgetYuan"]:
        violations.append(f"费用 {plan['costYuan']} 元超过演示预算 {constraints['budgetYuan']} 元")
    for resource, amount in plan.get("resourceRequirements", {}).items():
        capacity = constraints["resourceCapacities"].get(resource, 0)
        if amount > capacity:
            violations.append(
                f"{RESOURCE_LABELS.get(resource, resource)}不足（需要 {amount}，可用 {capacity}）"
            )
    for dependency in plan.get("requiredDependencies", []):
        if dependency not in constraints["confirmedDependencies"]:
            violations.append(f"{DEPENDENCY_LABELS.get(dependency, dependency)}未确认")
    return violations


def compare_plans(
    dataset: dict[str, Any],
    scenario_id: str,
    plan_ids: list[str] | None = None,
    constraints: dict[str, Any] | None = None,
    isolation_plan_id: str | None = None,
) -> dict[str, Any]:
    impact = simulate_impact(dataset, scenario_id, isolation_plan_id)
    if plan_ids is not None:
        if not isinstance(plan_ids, list) or any(not isinstance(pid, str) or not pid for pid in plan_ids):
            raise ValueError("候选方案标识必须是非空字符串列表")
        if len(plan_ids) != len(set(plan_ids)):
            raise ValueError("候选方案标识不能重复")
        unknown = set(plan_ids) - {plan["id"] for plan in dataset["plans"]}
        if unknown:
            raise KeyError(f"未知候选方案：{sorted(unknown)}")
    selected_plans = [
        plan for plan in dataset["plans"] if plan_ids is None or plan["id"] in plan_ids
    ]
    effective = _constraints_or_default(constraints)
    base = {
        "scenarioId": scenario_id,
        "status": "succeeded",
        "decisionAlgorithmVersion": DECISION_ALGORITHM_VERSION,
        "referenceCostYuan": REFERENCE_COST_YUAN,
        "effectBasis": "synthetic_preset",
        "constraintsSource": "demo_default" if constraints is None else "provided_context",
        "constraints": effective,
    }
    if impact["status"] != "succeeded" or not selected_plans:
        return {
            **base, "status": "insufficient_data", "planIds": [], "rows": [],
            "recommendedPlanId": None,
            "utilityNote": "缺少有效运行基准或候选方案，不进行排序。",
            "limitations": [*impact["limitations"], "缺少可比较的有效情景或候选方案。"],
        }
    max_cost = REFERENCE_COST_YUAN
    affected_count = len(impact["affectedServices"])
    rows = []
    for plan in selected_plans:
        restored = affected_count if plan["restoresAffected"] else 0
        risk_part = min(plan["riskReduction"] / 30, 1) * 0.55
        cost_part = max(0.0, min(1.0, 1 - plan["costYuan"] / max_cost)) * 0.25
        restore_part = 0.2 if restored else 0
        violations = _violations(plan, effective)
        rows.append(
            {
                "planId": plan["id"],
                "name": plan["name"],
                "type": plan["type"],
                "costYuan": plan["costYuan"],
                "durationHours": plan["durationHours"],
                "riskReduction": plan["riskReduction"],
                "restoredServiceCount": restored,
                "remainingAffectedServices": max(0, affected_count - restored),
                "resourceRequirements": dict(plan.get("resourceRequirements", {})),
                "feasible": not violations,
                "violations": violations,
                "utility": _round3(risk_part + cost_part + restore_part),
                "rank": 0,
                "note": plan["constraints"],
            }
        )
    rows.sort(key=lambda row: (not row["feasible"], -row["utility"], row["costYuan"], row["planId"]))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    recommended = next((row for row in rows if row["feasible"]), None)
    return {
        **base,
        "planIds": [row["planId"] for row in rows],
        "rows": rows,
        "recommendedPlanId": recommended["planId"] if recommended else None,
        "constraints": effective,
        "utilityNote": (
            "先按预算、资源和前置条件淘汰不可行方案，再比较效用：风险下降 55% + 成本 25% + "
            "预设服务恢复 20%；成本采用固定40000元参考尺度。这是演示规则，不是正式 CUV 或业务验收。"
        ),
        "limitations": [
            "只比较已给出的候选方案，不自动生成方案，也不做全局最优搜索。",
            "费用、工期和风险下降均为合成演示值；硬约束只作用于本次演示会话。",
            "恢复数量是方案预设标记，不是措施后网络或水力重算；工期不等于服务恢复时间。",
            "人工选择只登记演示意见，不代表派单、关阀或实际执行。",
        ],
    }


def _tool(tool_id: str, tool: str, label: str, status: str, summary: str, detail: str = "") -> dict[str, Any]:
    return {
        "id": f"tool-{tool_id}-{uuid.uuid4().hex[:8]}",
        "tool": tool,
        "label": label,
        "status": status,
        "summary": summary,
        "detail": detail,
    }


def _response(
    answer: str,
    status: str = "succeeded",
    tool_calls: list[dict[str, Any]] | None = None,
    evidence_ids: list[str] | None = None,
    extra_limitations: list[str] | None = None,
    refused: bool = False,
) -> dict[str, Any]:
    return {
        "id": f"agent-{uuid.uuid4().hex[:8]}",
        "status": status,
        "mode": "live",
        "engine": "template",
        "answerOrigin": "deterministic_tool_render",
        "answer": answer,
        "toolCalls": tool_calls or [],
        "evidenceIds": list(dict.fromkeys(evidence_ids or [])),
        "limitations": [*LIMITATIONS, *(extra_limitations or [])],
        "suggestedQuestions": SUGGESTIONS,
        "refused": refused,
    }


def template_assistant(
    dataset: dict[str, Any],
    question: str,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = question.strip()
    context = frozen_context(context)
    if not normalized:
        return _response("请输入一个问题，例如“为什么这段管段优先检查？”。", status="insufficient_data")
    if is_unsupported_question(normalized):
        return _response(
            REFUSAL,
            status="refused",
            tool_calls=[_tool("refuse", "scope_guard", "范围检查", "refused", "超出演示能力")],
            evidence_ids=["rule:demo-scope"],
            refused=True,
        )
    pipe_id = context.get("pipeId") or context.get("pipe", {}).get("id")
    scenario_id = context.get("scenarioId") or context.get("scenario", {}).get("id")
    isolation_id = context.get("isolationPlanId") or context.get("scenario", {}).get("isolationPlanId")

    if any(word in normalized for word in ("恢复", "多久", "完成时间", "时间线", "任务结束")):
        plan_id = context.get("planId")
        targets = context.get("targetServiceIds")
        horizon = context.get("horizonHours", 8)
        if not scenario_id or not plan_id or targets is None:
            return _response(
                "请先在情景分析页选择方案、目标服务和观察窗口，再计算服务恢复时间线。",
                status="insufficient_data",
                tool_calls=[_tool("timeline-context", "get_context", "读取时间线上下文", "insufficient_data", "缺少方案、目标或窗口")],
                evidence_ids=["rule:demo-timeline-context"],
            )
        request = {
            "scenarioId": scenario_id,
            "isolationPlanId": isolation_id,
            "planId": plan_id,
            "targetServiceIds": targets,
            "horizonHours": horizon,
            "actionSchedule": context.get("actionSchedule", []),
        }
        gate = check_timeline(dataset, request)
        result = build_timeline(dataset, request)
        def metric_text(metric):
            if metric["status"] == "already_available":
                return "0小时（初始可达）"
            if metric.get("valueHours") is not None:
                return f"{metric['valueHours']}小时" + ("（超出窗口）" if metric.get("beyondHorizon") else "")
            if metric["status"] == "not_recovered_within_horizon":
                return "窗口内未恢复"
            if metric["status"] == "not_applicable":
                return "不适用"
            return "待补数据"
        targets_text = "；".join(
            f"{service_id}：{metric_text(result['serviceFirstRestored'][service_id])}"
            for service_id in result["targetServiceIds"]
        )
        if result["status"] == "invalid_input":
            answer = "时间线输入未通过校验，未生成任何恢复时间：" + ("；".join(result["issues"]) or "输入不完整") + "。"
        else:
            answer = (
                f"方案「{plan_id}」的时间线：方案完成 {metric_text(result['planCompletion'])}；"
                f"首个目标恢复 {metric_text(result['firstTargetServiceRestored'])}；"
                f"全部目标首次同时可达 {metric_text(result['allTargetServicesRestored'])}。"
                + (f"逐目标：{targets_text}。" if targets_text else "")
                + f"基准 {result['asOf']}，观察窗口 {result['horizonHours']} 小时。"
                + "这是计划图通路和任务结束记录，不是水力、水质或现场执行。"
            )
        return _response(
            answer,
            status="insufficient_data" if result["status"] == "invalid_input" else result["status"],
            tool_calls=[_tool(
                "timeline", "analyze_plan_timeline", "计划图通路时间线",
                "insufficient_data" if result["status"] == "invalid_input" else result["status"],
                f"方案 {plan_id} · {len(result['targetServiceIds'])} 个目标",
                f"缺项：{'；'.join(gate['missingItems']) or '无'}",
            )],
            evidence_ids=result["evidenceIds"],
            extra_limitations=result["limitations"],
        )
    if any(word in normalized for word in ("方案", "维修", "抢修", "更换", "监测", "措施")):
        if not scenario_id:
            return _response("方案比较需要先选择一个故障情景。", status="insufficient_data")
        if "constraints" not in context:
            return _response("请先确认本次预算、资源和依赖条件，再比较方案。", status="insufficient_data")
        comparison = compare_plans(dataset, scenario_id, context.get("planIds"), context["constraints"], isolation_id)
        if comparison["status"] != "succeeded":
            return _response("当前情景或候选数据不足，暂不推荐方案。", status="insufficient_data", extra_limitations=comparison["limitations"])
        rows = "；".join(
            f"{row['rank']}. {row['name']}：{row['costYuan']} 元，风险下降 {row['riskReduction']} 点"
            for row in comparison["rows"]
        )
        recommended = next(
            (row["name"] for row in comparison["rows"] if row["planId"] == comparison["recommendedPlanId"]),
            "无可行方案",
        )
        return _response(
            f"当前合成情景的候选措施为：{rows}。演示建议是「{recommended}」，"
            "但这是固定演示权重和硬约束下的对照，不是正式 CUV、预算审批或派单结论。",
            tool_calls=[
                _tool("plans", "get_maintenance_plans", "读取候选方案", "succeeded", f"{len(comparison['rows'])} 个候选方案"),
                _tool("compare", "compare_demo_plans", "演示方案比较", "succeeded", f"推荐 {comparison['recommendedPlanId'] or '无'}"),
            ],
            evidence_ids=[f"scenario:{scenario_id}", *[f"plan:{row['planId']}" for row in comparison["rows"]]],
            extra_limitations=comparison["limitations"],
        )
    if any(word in normalized for word in ("影响", "服务", "医院", "学校", "居民", "商业", "故障", "中断", "爆管")):
        if not scenario_id:
            return _response("请先选择一个故障情景，我再解释它可能影响哪些服务对象。", status="insufficient_data")
        impact = simulate_impact(dataset, scenario_id, isolation_id)
        names = "、".join(item["name"] for item in impact["affectedServices"]) or "暂无服务对象"
        return _response(
            f"情景「{scenario_id}」下，受影响服务对象为：{names}。"
            f"涉及居民约 {impact['affectedHouseholds']} 户、设施 {impact['affectedFacilities']} 处。"
            "这只是合成管网的拓扑通路影响，不代表实际缺供量、压力或事故概率。",
            tool_calls=[
                _tool(
                    "impact",
                    "simulate_service_impact",
                    "拓扑影响试算",
                    impact["status"],
                    f"受影响服务 {len(impact['affectedServices'])} 个",
                    f"不可达节点：{'、'.join(impact['unreachableNodeIds']) or '无'}",
                )
            ],
            status=impact["status"],
            evidence_ids=impact["evidenceIds"],
            extra_limitations=impact["limitations"],
        )
    if any(word in normalized for word in ("为什么", "解释", "优先", "因子", "评分", "健康", "可信度", "数据")):
        if not pipe_id:
            return _response("请先选择一段管段，我再解释 DemoHealth 因子。", status="insufficient_data")
        pipe = find_pipe(dataset, pipe_id)
        assessment = evaluate_health(pipe, context.get("override"))
        top = sorted(
            [factor for factor in assessment["factors"] if factor["score"] is not None],
            key=lambda factor: factor["contribution"],
            reverse=True,
        )[:3]
        factors = "、".join(f"{factor['label']}({round(factor['contribution'] * 100)}%)" for factor in top)
        missing = "、".join(assessment["missing"]) or "无"
        return _response(
            f"{pipe['id']}「{pipe['name']}」的 DemoHealth 为 {assessment['score']} 分，"
            f"等级「{assessment['grade']}」，可信度 {assessment['confidence']}%。"
            f"主要贡献因子：{factors or '暂无明显贡献'}；缺失项：{missing}，未按 0 分处理。"
            "该分数不是失效概率，也不是正式 P/C/R/H。",
            status=assessment["status"],
            tool_calls=[
                _tool("pipe", "get_pipe", "读取管段", "succeeded", f"{pipe['id']} · {pipe['name']}"),
                _tool(
                    "health",
                    "evaluate_demo_health",
                    "DemoHealth 评价",
                    assessment["status"],
                    f"得分 {assessment['score']}，可信度 {assessment['confidence']}%",
                ),
            ],
            evidence_ids=[f"data:{pipe['id']}", f"rule:demo-health-{assessment['version']}"],
        )
    return _response(
        "我可以解释 DemoHealth 的评分因子、查看合成情景下的服务影响，或比较已给出的维护方案。"
        "请选择一段管段或一个情景，也可以直接点击下方建议问题。",
        tool_calls=[_tool("context", "get_context", "读取上下文", "succeeded", "演示助手已就绪")],
        evidence_ids=["rule:demo-assistant"],
    )
