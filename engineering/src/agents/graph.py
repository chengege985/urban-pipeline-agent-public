"""LangGraph单主智能体：四路任务、受限工具、证据复核、澄清与审批中断。

图只编排已授权的确定性工具；模型负责语义路由和解释，不产生评分。
"""
from __future__ import annotations

import json
from typing import Callable

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from src.agents.evidence import (answer_verification_status, deterministic_answer,
                                 ExecutionLedger, fact_cards, limitations_for,
                                 validate_answer, has_business_facts, verified_facts)
from src.agents.context import validate_context
from src.agents.outbound import classify_request
from src.agents.router import _restrict, deterministic_route
from src.agents.rag.retriever import citations as make_citations
from src.agents.state import AgentSettings, AgentState, TaskName
from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.errors import InputIssues
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.store import fingerprint

TASK_TOOL = {"assessment": {"calculate_health", "query_assessment"},
             "simulation": {"simulate_outage"}, "decision": {"compare_action_plans"}}
ORDER = ("materials", "assessment", "simulation", "decision")


def _interrupt_id(state: AgentState, phase: str) -> str:
    return "int-" + fingerprint({"run": state.get("run_id"), "phase": phase, "n": state.get("clarification", {}).get("round", 0)})[:24]


def request_fingerprint(state) -> str:
    """本次请求的可信输入指纹；平台与内核用同一算法绑定 run 与请求内容。"""
    return fingerprint({
        "question": str(state.get("question") or ""),
        "snapshot_id": state.get("snapshot_id"),
        "pipe_id": state.get("pipe_id"),
        "batch_refs": sorted(state.get("batch_refs") or []),
        "scenario": state.get("scenario"),
        "decision": state.get("decision"),
    })


def build_graph(*, llm, retriever, snapshot_lookup: Callable, batches: InputBatchRepository,
                decision_provider=None, checkpointer=None, settings: AgentSettings | None = None):
    settings = settings or AgentSettings()
    # 执行账本随图实例存在；只有 execute 节点可以签发回执。checkpoint 里的回执只是
    # 审计副本，不能作为执行证明。
    execution_ledger = ExecutionLedger()
    def _run_context(state):
        """Separate a new execution from a persisted thread/checkpoint.

        A matching run_context is the only source of a per-run question class.
        Matching requires run_id, principal, snapshot **and the request fingerprint**:
        reusing a run_id for another question is a contract violation, not a new run.
        """
        run_id = str(state.get("run_id") or "")
        principal = state.get("principal") or {}
        principal_id = str(principal.get("actor_id") or "")
        snapshot_id = state.get("snapshot_id")
        current_fingerprint = request_fingerprint(state)
        ctx = state.get("run_context")
        same_context = (
            isinstance(ctx, dict) and ctx.get("run_id") == run_id
            and ctx.get("principal_id") == principal_id
            and ctx.get("snapshot_id") == snapshot_id
            and ctx.get("request_fingerprint") == current_fingerprint
        )
        reuses_run_id = isinstance(ctx, dict) and ctx.get("run_id") == run_id
        if same_context:
            sensitivity = str(ctx.get("question_sensitivity") or "unknown")
            authorization = ctx.get("authorization_context") or {}
        elif not ctx:
            sensitivity = str(state.get("question_sensitivity") or "unknown") if run_id else "unknown"
            authorization = {}
        else:
            sensitivity = "unknown"
            authorization = {}
        return {
            "run_id": run_id, "thread_id": state.get("thread_id"),
            "principal_id": principal_id, "snapshot_id": snapshot_id,
            "question_sensitivity": sensitivity,
            "request_fingerprint": current_fingerprint,
            "authorization_context": authorization or {"scope": principal.get("scope"), "snapshot_id": snapshot_id},
        }, (reuses_run_id and not same_context)

    def route_context(state, snapshot):
        pipe_ids = [a["pipe_id"] for a in (snapshot or {}).get("payload", {}).get("assessments", [])]
        allowed = []
        if retriever is not None:
            allowed.append("materials")
        if state.get("snapshot_id") or state.get("batch_refs"):
            allowed.append("assessment")
        if state.get("scenario"):
            allowed.append("simulation")
        if state.get("decision"):
            allowed.append("decision")
        blocked = deterministic_route(state["question"])
        classification = classify_request(
            state.get("principal"), state["question"], "route", snapshot=snapshot,
            model_service=getattr(llm, "service_id", "unknown"),
            endpoint=getattr(llm, "endpoint", ""),
            question_sensitivity=state.get("question_sensitivity"))
        route = blocked if blocked.intent == "unsupported" else _restrict(
            llm.route(state["question"], allowed_tasks=tuple(allowed), pipe_ids=pipe_ids,
                      data_mode=(snapshot or {}).get("payload", {}).get("data_mode", "unknown"),
                      classification=classification),
            allowed, pipe_ids, question=None if state.get("entity_resolution", {}).get("pipe_id") == state.get("pipe_id") and state.get("pipe_id") else state["question"], confirmed_pipe_id=state.get("pipe_id"))
        if state.get("pipe_id") and route.intent != "unsupported" and not route.reason.startswith("对象校验未通过"):
            tasks = route.tasks or (["assessment"] if "assessment" in allowed else [])
            route = route.model_copy(update={"pipe_id": state["pipe_id"], "tasks": tasks,
                                            "intent": tasks[0] if tasks else "clarify"})
        updates = {"route": route.model_dump(), "allowed_tools": sorted(
            set().union(*(TASK_TOOL.get(t, set()) for t in route.tasks)) if route.tasks else set()),
            "tool_results": [], "evidence_refs": [], "citations": [], "missing": [], "errors": []}
        if route.pipe_id:
            updates["route"]["pipe_id"] = route.pipe_id
        if route.intent == "unsupported":
            updates.update(status="insufficient_data", capability_status="unsupported",
                           phase="unsupported", answer=route.reason, verification_status="unavailable")
        elif not route.tasks:
            updates.update(status="running", phase="clarification_required", missing=[
                route.reason + "；当前授权上下文没有该任务所需的数据、情景或方案，请补充或选择可用任务。"],
                verification_status="unavailable")
        else:
            updates["status"] = "running"
            updates["verification_status"] = ""
        return updates

    def guard(state: AgentState):
        # A new run uses the thread's checkpointer but never inherits previous outputs.
        run_context, reuses_run_id = _run_context(state)
        fresh = {"answer": "", "answer_mode": "", "clarification": {}, "approval": {},
                 "approval_result": {}, "capability_status": "supported", "error_kind": None,
                 "entity_resolution": {}, "question_sensitivity": run_context["question_sensitivity"],
                 "run_context": run_context, "tool_results": [], "execution_receipts": [],
                 "citations": [], "verified_facts": [], "verification_status": "",
                 "evidence_refs": [], "allowed_tools": [], "missing": [], "errors": []}
        if reuses_run_id:
            # 同一 run_id 不得用于另一条新问题：既不能继承旧分类，也不能调用模型。
            return {**fresh, "route": {}, "allowed_tools": [], "tool_results": [],
                    "execution_receipts": [], "citations": [], "verified_facts": [],
                    "verification_status": "unavailable", "evidence_refs": [],
                    "missing": ["同一 run_id 不能用于另一条新问题或另一主体/快照；请以新的 run_id 重新提交。"],
                    "errors": [], "status": "insufficient_data",
                    "capability_status": "insufficient_data", "error_kind": "run_contract_violation",
                    "phase": "run_contract_rejected",
                    "answer": "同一 run_id 已绑定其他请求，本次未执行任何计算或模型调用。"}
        try:
            context, snapshot = validate_context(state, snapshot_lookup, batches)
        except (PermissionError, InputIssues, ValueError, KeyError) as exc:
            return {**fresh, "route": {}, "allowed_tools": [], "tool_results": [], "citations": [],
                    "verified_facts": [], "verification_status": "unavailable",
                    "evidence_refs": [], "missing": [str(exc)], "errors": [],
                    "status": "insufficient_data", "capability_status": "insufficient_data",
                    "error_kind": "permission_denied" if isinstance(exc, PermissionError) else "invalid_input",
                    "phase": "context_rejected", "answer": "当前任务输入未通过校验：" + str(exc)}
        return {**fresh, **context, **route_context({**state, **fresh, **context}, snapshot)}

    def check(state: AgentState):
        route = state.get("route", {})
        if state.get("status") == "insufficient_data":
            return {}
        tasks = list(route.get("tasks", []))
        missing = list(state.get("missing", []))
        pipe_id = route.get("pipe_id")
        if "assessment" in tasks and not pipe_id and not state.get("batch_refs"):
            missing.append("请提供当前快照中的管段ID，或已登记的完整输入批次引用。")
        if "simulation" in tasks and not state.get("scenario"):
            missing.append("请选择已确认的故障边和给定隔离方案。")
        if "decision" in tasks and not state.get("decision"):
            missing.append("请提供2至3个候选方案、预算、资源容量和目标配置。")
        if "materials" in tasks and retriever is None:
            missing.append("知识检索服务未配置，不能回答资料依据。")
        return {"missing": missing, "phase": "input_check" if missing else "ready", "status": "running" if not missing else state.get("status", "running")}

    def clarify(state: AgentState):
        round_no = int(state.get("clarification", {}).get("round", 0)) + 1
        if round_no > 2:
            return {"status": "insufficient_data", "phase": "clarification_exhausted",
                    "answer": "两次澄清后仍缺少必要信息，请重新发起并明确对象、时点、情景或方案。"}
        interrupt_id = _interrupt_id(state, "clarification")
        payload = interrupt({"type": "clarification", "interrupt_id": interrupt_id, "revision": 0,
                             "missing": state.get("missing", []), "question": state.get("question")})
        if not isinstance(payload, dict):
            payload = {}
        if payload.get("action") == "cancel":
            return {"status": "cancelled", "phase": "cancelled", "answer": "任务已由用户取消。"}
        progress = {"clarification": {"round": round_no, "interrupt_id": interrupt_id},
                    "status": "running", "phase": "clarification_resumed"}
        if payload.get("action") != "clarify":
            return {**progress, "missing": ["请提交clarify及补充字段，或取消任务。"]}
        try:
            context, snapshot = validate_context(state, snapshot_lookup, batches, payload.get("values", {}))
        except (PermissionError, InputIssues, ValueError, KeyError) as exc:
            return {**progress, "missing": [str(exc)], "allowed_tools": [],
                    "error_kind": "permission_denied" if isinstance(exc, PermissionError) else "invalid_input"}
        resolution = state.get("entity_resolution", {})
        if payload.get("values", {}).get("pipe_id"):
            resolution = {"pipe_id": context["pipe_id"], "source": "user_clarification"}
        return {**context, "entity_resolution": resolution,
                **route_context({**state, **context, "entity_resolution": resolution}, snapshot), **progress,
                "answer": "", "error_kind": None}

    def execute(state: AgentState):
        route = state.get("route", {})
        tasks: list[TaskName] = route.get("tasks", [])
        pipe_id = route.get("pipe_id")
        allowed = set(state.get("allowed_tools", []))
        access = ToolAccess(principal=BatchPrincipal.model_validate(state["principal"]), snapshot_id=state.get("snapshot_id"),
                            allowed_tools=frozenset(allowed), batch_refs=frozenset(state.get("batch_refs", [])),
                            scenario=state.get("scenario"), decision=state.get("decision"))
        tools = DomainTools(access, snapshot_lookup, batches, decision_provider,
                            ledger=execution_ledger, run_id=state["run_id"])
        results, citations = [], []
        for task in tasks:
            if task == "materials":
                snapshot = None
                if state.get("snapshot_id"):
                    try:
                        snapshot = snapshot_lookup(state["snapshot_id"])
                    except Exception:
                        snapshot = None
                classification = classify_request(
                    state.get("principal"), state["question"], "query_embedding", snapshot=snapshot,
                    model_service=getattr(retriever, "embedding_service_id", "unknown"),
                    endpoint=getattr(retriever, "embedding_endpoint", getattr(retriever, "endpoint", ""))
                    or getattr(retriever, "endpoint", ""),
                    question_sensitivity=state.get("question_sensitivity"))
                hits, mode = retriever.search(
                    state["question"], scope=state["principal"]["scope"], limit=5,
                    classification=classification)
                vector_status = getattr(retriever, "vector_status", {"available": False, "reason": "未知", "mode": "bm25"})
                if not hits:
                    missing_retrieval = (["语料版本或权限已变化，旧缓存不可用；请重建检索索引"]
                                         if mode == "stale" else ["没有检索到可引用资料"])
                    results.append({"tool_name": "knowledge_retrieval", "tool_version": "hybrid-1",
                                    "status": "insufficient_data", "result": {"status": "insufficient_data",
                                    "missing_items": missing_retrieval,
                                    "vector_status": vector_status},
                                    "evidence_id": "tool:" + fingerprint({"q": state["question"], "scope": state["principal"]["scope"]}),
                                    "evidence_refs": [], "execution_status": "not_dispatched"})
                else:
                    citations = make_citations(hits)
                    results.append({"tool_name": "knowledge_retrieval", "tool_version": "hybrid-1",
                                    "status": "succeeded", "result": {"status": "succeeded", "retrieval_mode": mode,
                                    "vector_status": vector_status, "citations": citations},
                                    "evidence_id": citations[0]["evidence_id"],
                                    "evidence_refs": [c["evidence_id"] for c in citations],
                                    "execution_status": "not_dispatched"})
                continue
            calls = []
            if task == "assessment":
                if state.get("batch_refs"):
                    for ref in state.get("batch_refs", [])[:2]:
                        calls.append(("calculate_health", {"batch_ref": ref}))
                if pipe_id:
                    calls.append(("query_assessment", {"pipe_id": pipe_id}))
            elif task == "simulation":
                calls.append(("simulate_outage", {}))
            elif task == "decision":
                calls.append(("compare_action_plans", {}))
            for name, args in calls:
                if len(results) >= settings.max_tool_calls:
                    results.append({"tool_name": "limit", "tool_version": "1", "status": "partial",
                                    "result": {"status": "partial", "missing_items": ["达到工具调用上限"]},
                                    "evidence_id": "tool:" + fingerprint({"limit": state["run_id"]}),
                                    "evidence_refs": [], "execution_status": "not_dispatched"})
                    break
                try:
                    executed = tools.execute(name, args)
                except (PermissionError, InputIssues, ValueError) as exc:
                    results.append({"tool_name": name, "tool_version": "domain-tools-1", "status": "insufficient_data",
                                    "result": {"status": "insufficient_data", "error_kind": "permission_denied" if isinstance(exc, PermissionError) else "invalid_input", "missing_items": [str(exc)[:500]]},
                                    "evidence_id": "tool:" + fingerprint({"run": state["run_id"], "tool": name, "error": type(exc).__name__}),
                                    "evidence_refs": [], "execution_status": "not_dispatched"})
                    continue
                # 回执由受控执行器在真实返回后登记；此处只收集结果。
                results.append(executed)
        return {"tool_results": results, "citations": citations,
                # 审计副本仅供展示/落库，永不参与事实认证。
                "execution_receipts": [item.as_dict() for item in execution_ledger.records(state["run_id"])],
                "evidence_refs": list(dict.fromkeys([r["evidence_id"] for r in results] + [c["evidence_id"] for c in citations]))}

    def evidence(state: AgentState):
        facts = fact_cards(state.get("tool_results", []), state.get("citations", []))
        statuses = [f["status"] for f in facts]
        if not facts:
            status = "insufficient_data"
        elif all(s == "insufficient_data" for s in statuses):
            status = "insufficient_data"
        elif any(s in {"insufficient_data", "partial"} for s in statuses):
            status = "partial"
        else:
            status = "succeeded"
        return {"status": status, "phase": "evidence_checked",
                "verified_facts": [item.as_dict() for item in verified_facts(
                    facts, run_id=str(state.get("run_id") or ""),
                    snapshot_id=str(state.get("snapshot_id") or ""),
                    principal_id=str((state.get("principal") or {}).get("actor_id") or ""),
                    ledger=execution_ledger)]}

    def respond(state: AgentState):
        citations = state.get("citations", [])
        facts = fact_cards(state.get("tool_results", []), citations)
        run_id = str(state.get("run_id") or "")
        snapshot_id = str(state.get("snapshot_id") or "")
        principal_id = str((state.get("principal") or {}).get("actor_id") or "")
        limits = limitations_for(state.get("tool_results", []))
        # R02/R04: never send a RAG citation without its body, version, hash or integrity.
        if citations and any(
                not (item.get("content") or item.get("snippet"))
                or not item.get("document_version") or not item.get("content_hash")
                or item.get("integrity_status") not in {"verified", None}
                for item in citations):
            return {"answer": deterministic_answer(
                        state["question"], facts, citations,
                        limits + ["检索上下文缺少正文、版本、哈希或完整性校验失败，未发送至在线模型"]),
                    "answer_mode": "template_fallback", "verification_status": "invalid_evidence",
                    "verified_facts": [item.as_dict() for item in verified_facts(
                        facts, run_id=run_id, snapshot_id=snapshot_id, principal_id=principal_id,
                        ledger=execution_ledger)]}
        if state.get("status") == "insufficient_data" or has_business_facts(facts):
            # Business numbers and recommendations are rendered from bound tool facts.
            # LLM prose cannot relabel another entity's score/cost or invent a capability.
            return {"answer": deterministic_answer(state["question"], facts, citations, limits),
                    "answer_mode": "deterministic_facts",
                    "verification_status": "deterministic_bound",
                    "verified_facts": [item.as_dict() for item in verified_facts(
                        facts, run_id=run_id, snapshot_id=snapshot_id, principal_id=principal_id,
                        ledger=execution_ledger)],
                    "capability_status": "insufficient_data" if state.get("status") == "insufficient_data" else "supported"}
        try:
            snapshot = snapshot_lookup(state["snapshot_id"]) if state.get("snapshot_id") else None
            mode = (snapshot or {}).get("payload", {}).get("data_mode", "unknown")
            if snapshot is None and state["principal"]["scope"] == "competition":
                mode = "synthetic"
            classification = classify_request(
                state.get("principal"), state["question"], "answer", snapshot=snapshot,
                facts=facts, citations=citations,
                model_service=getattr(llm, "service_id", "unknown"),
                endpoint=getattr(llm, "endpoint", ""),
                question_sensitivity=state.get("question_sensitivity"))
            answer = llm.answer(state["question"], facts, citations, limits,
                                data_mode=mode, classification=classification)
            principal_id = str((state.get("principal") or {}).get("actor_id") or "")
            ok, problems = validate_answer(answer, facts, citations, run_id=run_id,
                                           snapshot_id=snapshot_id, principal_id=principal_id,
                                           ledger=execution_ledger)
        except PermissionError:
            answer, ok, problems = "", False, ["当前资料范围未获准发送至在线模型，使用本地证据摘要"]
        except Exception:
            answer, ok, problems = "", False, ["模型调用失败"]
        if not ok:
            answer = deterministic_answer(state["question"], facts, citations, limits + problems)
            mode = "template_fallback"
            verification_status = "unverified_prose"
        else:
            mode = "llm"
            verification_status = "verified_excerpt"
            if citations and not has_business_facts(facts):
                verification_status = answer_verification_status(answer, facts, citations)
                if verification_status != "verified_excerpt":
                    answer = ("模型解释（未经语义蕴含验证，以下附来源摘录，不作为业务数值）：\n"
                              + answer + "\n\n" + deterministic_answer(state["question"], facts, citations, limits))
                    mode = "llm_with_excerpt"
        return {"answer": answer, "answer_mode": mode, "verification_status": verification_status,
                "verified_facts": [item.as_dict() for item in verified_facts(
                    facts, run_id=run_id, snapshot_id=snapshot_id, principal_id=principal_id,
                    ledger=execution_ledger)]}

    def review(state: AgentState):
        decision = next((r["result"] for r in state.get("tool_results", [])
                         if r["tool_name"] == "compare_action_plans" and r["status"] in {"succeeded", "partial"}), None)
        if not decision or not decision.get("recommended_plan_id"):
            return {"status": state.get("status", "succeeded"), "phase": "completed"}
        interrupt_id = _interrupt_id(state, "approval")
        payload = interrupt({"type": "approval", "interrupt_id": interrupt_id, "revision": 0,
                             "recommended_plan_id": decision["recommended_plan_id"],
                             "recommendation_status": "pending_review",
                             "question": "是否接受该演示建议？接受仅表示审阅，不代表派单或关阀。"})
        if not isinstance(payload, dict):
            payload = {}
        action = payload.get("action")
        if action == "cancel":
            return {"status": "cancelled", "phase": "cancelled", "approval_result": payload}
        if action == "revise":
            # 修改不能沿用旧结果：暂停并明确要求新任务重算，不生成虚假的新版本。
            return {"status": "paused", "phase": "revision_required", "approval_result": payload,
                    "answer": "已记录修改意见；旧建议未批准，请以新预算/输入重新提交任务后重算。"}
        return {"status": "succeeded", "phase": "reviewed", "approval_result": payload,
                "answer": state.get("answer", "")}

    def finalize(state: AgentState):
        status = state.get("status", "failed")
        if status == "running":
            status = "succeeded"
        return {"status": status, "phase": state.get("phase", "completed")}

    def after_check(state):
        if state.get("status") in {"insufficient_data", "cancelled"}:
            return "finalize"
        return "clarify" if state.get("missing") else "execute"

    def after_guard(state):
        return "finalize" if state.get("status") == "insufficient_data" else "check"

    def after_respond(state):
        decision = any(r["tool_name"] == "compare_action_plans" and r["status"] in {"succeeded", "partial"}
                       and r["result"].get("recommended_plan_id") for r in state.get("tool_results", []))
        return "review" if decision else "finalize"

    builder = StateGraph(AgentState)
    for name, node in (("guard", guard), ("check", check), ("clarify", clarify), ("execute", execute),
                       ("evidence", evidence), ("respond", respond), ("review", review), ("finalize", finalize)):
        builder.add_node(name, node)
    builder.set_entry_point("guard")
    builder.add_conditional_edges("guard", after_guard, {"check": "check", "finalize": "finalize"})
    builder.add_conditional_edges("check", after_check, {"clarify": "clarify", "execute": "execute", "finalize": "finalize"})
    builder.add_edge("clarify", "check")
    builder.add_edge("execute", "evidence")
    builder.add_edge("evidence", "respond")
    builder.add_conditional_edges("respond", after_respond, {"review": "review", "finalize": "finalize"})
    builder.add_edge("review", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)
