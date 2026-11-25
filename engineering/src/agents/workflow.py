"""固定业务状态机：澄清 → 条件检查 → 白名单计算 → 证据 → 待人工复核。

LLM只提供Intent，不能决定任意工具或更改预算、快照、审核状态。
无论mock或在线分类，结论中的数字都由同一确定性工具计算。
"""
from datetime import datetime
import re

from src.decision.plans import compare_plans
from src.platform.schemas import AssistantRequest, DecisionRequest, ScenarioRequest
from src.platform.services import query_assessment as _query_assessment, simulate_snapshot as simulate
from src.simulation.topology import check_inputs

from .gateway import BLOCKED, UNAVAILABLE


class ToolLimit(RuntimeError):
    pass


class ToolRunner:
    ALLOWED = {"query_assessment", "check_inputs", "simulate_outage", "compare_action_plans", "search_materials", "get_evidence"}

    def __init__(self, max_calls=8):
        self.max_calls = min(8, max_calls)
        self.trace = []

    def call(self, name, function, **kwargs):
        if name not in self.ALLOWED:
            raise PermissionError("工具不在只读计算白名单中")
        if len(self.trace) >= self.max_calls:
            raise ToolLimit("已达到最多8次工具调用限制，任务暂停")
        entry = {"step": len(self.trace) + 1, "tool": name, "version": "0.1.0", "status": "running"}
        self.trace.append(entry)
        try:
            result = function(**kwargs)
            entry["status"] = result.get("status", "succeeded") if isinstance(result, dict) else "succeeded"
            return result
        except Exception:
            entry["status"] = "failed"
            raise


def resolve_id(explicit, proposed, candidates, question):
    """只允许快照中存在的完整ID；相互矛盾或多个命中都要求用户澄清。"""
    if explicit and proposed and explicit != proposed:
        return None
    selected = explicit or proposed
    if selected:
        return selected if selected in candidates else None
    matches = [v for v in candidates if re.search(r"(?<![\w-])" + re.escape(v) + r"(?![\w-])", question)]
    return matches[0] if len(matches) == 1 else None


def query_assessment(snapshot, pipe_id):
    """保留旧工作流入口，实际读取逻辑与新Agent工具共用。"""
    return _query_assessment(snapshot, pipe_id)


def run_assistant(snapshot, request: AssistantRequest, gateway, search, max_calls=8, decision_function=None):
    tools = ToolRunner(max_calls)
    common = {"snapshot_id": snapshot["snapshot_id"], "as_of": snapshot["payload"]["as_of"],
              "data_mode": snapshot["data_mode"], "source_status": snapshot["source_status"],
              "llm_mode": gateway.mode, "validation_status": "not_business_validated",
              "evidence_refs": ["snapshot:" + snapshot["snapshot_id"]], "tool_trace": tools.trace,
              "workflow_version": "0.1.0", "recommendation_status": None}

    def stop(message, phase="clarification_required"):
        return {**common, "status": "insufficient_data", "phase": phase,
                "answer": message, "missing_items": [message]}

    try:
        if request.snapshot_id != snapshot["snapshot_id"]:
            return stop("请求快照与当前上下文不一致。")
        # 执行边界在网关外再校验一次，不能被在线模型的分类绕过。
        if BLOCKED.search(request.question) or UNAVAILABLE.search(request.question):
            return stop("首期不提供实际设施控制、工单下发、任意代码执行或专业概率/寿命推断。可查询评分或做指定故障情景。", "unsupported")
        intent = gateway.classify(request.question, snapshot["data_mode"])
        common["intent"] = intent.model_dump()
        if intent.task_type in {"unsupported", "clarify"}:
            return stop("请明确要查询评分、分析指定故障、比较给定方案，还是查找项目资料。")
        if intent.task_type == "materials":
            citations = tools.call("search_materials", search.search, query=request.question)
            return {**common, "status": "succeeded" if citations else "insufficient_data", "phase": "evidence_returned",
                    "answer": "以下为白名单资料的原文引用；课程与方案描述不代表已取得可调用实现。",
                    "citations": citations, "evidence_refs": [c["evidence_id"] for c in citations]}
        if request.as_of is None or request.as_of != datetime.fromisoformat(snapshot["as_of"]):
            return stop("请显式选择与快照一致的评价时点；不默认用当前时间替代。")
        if intent.task_type == "assessment":
            pid = resolve_id(request.pipe_id, intent.pipe_id,
                             [a["pipe_id"] for a in snapshot["payload"]["assessments"]], request.question)
            if pid is None:
                return stop("请提供当前快照中唯一、完整的管段ID，不能按模糊名称猜测。")
            result = tools.call("query_assessment", query_assessment, snapshot=snapshot, pipe_id=pid)
            value = result["assessment"]["legacy_result"]
            answer = f"管段{pid}的简化健康度Hs={value['Hs']}，等级为{value['grade']}。这些值来自评分结果，不是失效概率。"
        else:
            fid = resolve_id(request.fault_edge_id, intent.fault_edge_id,
                             [e["edge_id"] for e in snapshot["payload"]["edges"]], request.question)
            if fid is None or request.isolation_plan_id is None:
                return stop("请指定该快照的故障边ID和已给定隔离方案；评分样例不具备真实运行网络。")
            values = {"snapshot_id": request.snapshot_id, "as_of": request.as_of, "fault_edge_id": fid,
                      "isolation_plan_id": request.isolation_plan_id, "horizon_hours": request.horizon_hours}
            scenario = ScenarioRequest(**values)
            missing = tools.call("check_inputs", check_inputs, snapshot=snapshot, request=scenario)
            if missing:
                return stop("；".join(missing), "input_check")
            if intent.task_type == "simulation":
                result = tools.call("simulate_outage", simulate, snapshot=snapshot, request=scenario)
                answer = f"指定情景新增{len(result['newly_unreachable_node_ids'])}个不可达节点，涉及{len(result['affected_service_ids'])}个已映射服务对象。仅表示通路影响，不表示实际缺供量。"
            else:
                if len(request.plan_ids) < 2 or request.budget_fen is None:
                    return stop("比较方案需显式提供2至3个候选方案、预算（分）和资源容量。")
                decision = DecisionRequest(**values, plan_ids=request.plan_ids, budget_fen=request.budget_fen,
                                           resource_capacities=request.resource_capacities,
                                           mandatory_action_ids=request.mandatory_action_ids)
                result = tools.call("compare_action_plans", decision_function or compare_plans, snapshot=snapshot, request=decision)
                rec = result.get("recommended_plan_id")
                answer = (f"当前演示配置下，给定可行候选中推荐{rec}，请人工复核约束、效果和依据。未下发任何工单。"
                          if rec else "目前不能形成完整推荐，请先查看不可行原因或补齐措施、效果与资源条件。")
        evidence = tools.call("get_evidence", lambda: {"snapshot_id": snapshot["snapshot_id"],
                              "sha256": snapshot["sha256"], "source_refs": snapshot["payload"]["source_refs"]})
        notice = "本地模拟意图路由（未连接LLM）。" if gateway.mode == "mock" else "LLM仅参与意图识别，事实与结论来自工具。"
        return {**common, "status": result["status"], "phase": "pending_review" if result.get("recommended_plan_id") else "completed",
                "answer": notice + answer, "facts": result, "evidence": evidence,
                "evidence_refs": list(dict.fromkeys(common["evidence_refs"] + result.get("evidence_refs", []))),
                "recommendation_status": result.get("recommendation_status")}
    except ToolLimit as exc:
        return {**common, "status": "partial", "phase": "paused", "answer": str(exc)}
    except Exception as exc:
        return {**common, "status": "failed", "phase": "tool_or_gateway_failed",
                "answer": "工具或模型网关失败，没有生成确定性建议。", "error_type": type(exc).__name__}
