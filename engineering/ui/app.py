"""四页供水工作台：所有业务计算经HTTP调用后端，本页只展示和提交人工操作。

启动：.venv-platform/bin/python -B -m streamlit run ui/app.py --server.address 127.0.0.1
WATER_API_URL默认http://127.0.0.1:8000；令牌从环境变量读取，不写入项目文件。
"""
import json
import os
import uuid

import httpx
import pandas as pd
import streamlit as st

API = os.getenv("WATER_API_URL", "http://127.0.0.1:8000").rstrip("/")
STATUS = {"queued": "排队中", "running": "计算中", "succeeded": "计算完成", "partial": "部分结果",
          "insufficient_data": "条件不足", "infeasible": "没有可行方案", "failed": "运行失败"}
MODE = {"fixture_replay": "提供方评分样例 · 来源未确认", "synthetic": "独立合成情景 · 非真实管网", "real": "用户声明真实 · 业务未验收"}


def api(method, route, data=None, request_headers=None, **params):
    headers = {"X-Water-Client": "local-prototype"}
    headers.update(request_headers or {})
    if os.getenv("WATER_API_TOKEN"):
        headers["X-Water-Token"] = os.environ["WATER_API_TOKEN"]
    if method == "POST":
        headers["Idempotency-Key"] = uuid.uuid4().hex
    try:
        response = httpx.request(method, API + route, json=data, params=params, headers=headers,
                                 timeout=15, follow_redirects=False, trust_env=False)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        st.error(f"接口返回 {exc.response.status_code}")
        try:
            st.json(exc.response.json())
        except ValueError:
            st.write("接口响应不是合法JSON")
    except httpx.RequestError:
        st.error("后端暂不可达，请先启动API服务或确认WATER_API_URL。")
    st.stop()


def show_impact(impact):
    if "newly_unreachable_node_ids" not in impact:
        st.warning("当前不能计算确定性影响")
        st.json(impact.get("missing_items", impact))
        return
    columns = st.columns(3)
    columns[0].metric("新增不可达节点", len(impact["newly_unreachable_node_ids"]))
    columns[1].metric("涉及服务对象", len(impact["affected_service_ids"]))
    columns[2].metric("涉及关键设施", len(impact["affected_critical_facility_ids"]))
    st.caption("以上为拓扑通路影响，不是实际缺供量或事故概率。")
    st.json({"潜在影响计数（单位分开）": impact["potentially_disconnected_counts"],
             "新增不可达节点": impact["newly_unreachable_node_ids"],
             "正常时已不可达": impact["normal_unreachable_node_ids"],
             "服务对象": impact["affected_service_ids"]})
    with st.expander("路径依据与限制"):
        st.json({k: impact.get(k) for k in ("normal_path_examples", "alternative_path_examples", "valve_overrides", "limitations", "missing_items")})


def show_result(result):
    if result.get("answer"):
        st.info(result["answer"])
    if result.get("tool_trace"):
        st.dataframe(result["tool_trace"], use_container_width=True, hide_index=True)
    facts = result.get("facts", result)
    if "plans" in facts:
        if facts.get("recommended_plan_id"):
            st.success("待人工复核的演示推荐：" + facts["recommended_plan_id"])
        else:
            st.warning("未形成完整推荐，检查下方约束和缺项")
        rows = [{"方案": p["label"], "状态": STATUS.get(p["status"], p["status"]),
                 "演示效用": p.get("utility"), "费用（元）": p.get("metrics", {}).get("total_cost_fen", 0) / 100 if p.get("metrics") else None,
                 "通路仍受影响服务": p.get("metrics", {}).get("remaining_disconnected_services"),
                 "原因": p.get("selection_reason", "")} for p in facts["plans"]]
        st.dataframe(rows, use_container_width=True, hide_index=True)
        st.caption("只比较给定方案；demo_policy不是正式CUV。接受建议不代表实际执行。")
        for plan in facts["plans"]:
            with st.expander(plan["label"] + "：效果、资源与价值贡献"):
                st.json(plan)
    elif "newly_unreachable_node_ids" in facts:
        show_impact(facts)
    elif "assessment" in facts:
        st.json(facts["assessment"])
    for citation in result.get("citations", []):
        with st.expander(f"{citation['file']} · {citation['start_line']}—{citation['end_line']}行"):
            st.text(citation["text"])
            st.caption(citation["evidence_id"])
    if facts.get("missing_items"):
        st.warning("缺少的条件或受限原因")
        st.json(facts["missing_items"])
    with st.expander("完整结构化结果与证据"):
        st.json(result)


@st.fragment(run_every=2)
def task_panel(run_id):
    task = api("GET", f"/api/v1/runs/{run_id}")
    st.caption(f"运行 {run_id} · {STATUS.get(task['status'], task['status'])}")
    if task["status"] in {"queued", "running"}:
        st.info("任务已持久化，正在排队或计算；页面每2秒刷新。")
    elif task.get("result"):
        show_result(task["result"])
        st.download_button("下载本次结果JSON", json.dumps(task, ensure_ascii=False, indent=2),
                           file_name=run_id + ".json", mime="application/json", key="download-" + run_id)


def diagram(snapshot):
    """只绘制后端返回的节点/边，不在前端执行图算法；坐标为示意布局。"""
    nodes, edges = snapshot["payload"]["nodes"], snapshot["payload"]["edges"]
    quote = lambda text: json.dumps(text, ensure_ascii=False)
    lines = ['digraph G { rankdir=LR; bgcolor="transparent"; node [shape=box,style="rounded,filled",fillcolor="#e7f1fa",fontname="sans-serif"];']
    for n in nodes:
        label = n["label"] or n["node_id"]
        color = "#d3e8dc" if n["source"] and n["active"] else "#e7f1fa"
        lines.append(f'{quote(n["node_id"])} [label={quote(label + chr(10) + n["node_id"])},fillcolor="{color}"];')
    for e in edges:
        style = "dashed" if e["state"] != "open" else "solid"
        direction = "both" if e["direction"] == "both" else "forward"
        a, b = (e["to_node"], e["from_node"]) if e["direction"] == "reverse" else (e["from_node"], e["to_node"])
        lines.append(f'{quote(a)} -> {quote(b)} [label={quote(e["edge_id"])},dir={direction},style={style}];')
    lines.append("}")
    st.graphviz_chart("\n".join(lines), use_container_width=True)
    st.caption("拓扑示意图，无真实地理坐标；虚线表示关闭或未知状态。")


def scenario_fields(snapshot, prefix):
    data = snapshot["payload"]
    if not data["edges"] or not data["isolation_plans"]:
        st.warning("这个数据空间只有评分或缺少隔离方案。请在侧栏切换到S01合成网络。")
        return None
    fault = st.selectbox("指定故障边", [e["edge_id"] for e in data["edges"]],
                         index=next((i for i, e in enumerate(data["edges"]) if e["edge_id"] == "syn-fault"), 0), key=prefix + "-fault")
    plans = [p["plan_id"] for p in data["isolation_plans"] if fault in p["fault_edge_ids"]]
    if not plans:
        st.warning("该故障边没有适用的给定隔离方案，不能自动猜测关阀组合。")
        return None
    isolation = st.selectbox("给定隔离方案", plans, key=prefix + "-isolation")
    as_of = st.text_input("分析时点（须与快照一致，带时区）", snapshot["as_of"], key=prefix + "-time")
    horizon = st.number_input("措施对比窗口（小时）", 1.0, 720.0, 8.0, key=prefix + "-horizon")
    return {"snapshot_id": snapshot["snapshot_id"], "as_of": as_of, "fault_edge_id": fault,
            "isolation_plan_id": isolation, "horizon_hours": horizon}


def data_page(snapshot):
    st.subheader("数据与能力")
    st.json(api("GET", "/api/v1/capabilities"))
    st.caption("provided样例和synthetic网络彼此独立；未交付能力不显示为已实现。")
    with st.expander("导入冻结快照（JSON）"):
        st.write("可先下载当前快照作为结构参考。更改后导入会生成新快照，不覆盖原件。")
        st.download_button("下载快照结构", json.dumps(snapshot["payload"], ensure_ascii=False, indent=2),
                           file_name="snapshot.json", mime="application/json")
        uploaded = st.file_uploader("选择平台快照JSON（最大10MiB）", type=["json"], key="snapshot-upload")
        if st.button("校验并导入", disabled=uploaded is None):
            try:
                payload = json.loads(uploaded.getvalue())
            except (ValueError, UnicodeDecodeError):
                st.error("JSON格式不正确")
            else:
                saved = api("POST", "/api/v1/data-batches", payload)
                st.session_state["select_next"] = saved["snapshot_id"]
                st.rerun()
    with st.expander("受控评分重算（JSON记录）"):
        st.caption("上传RecomputeRequest：as_of、window_start/end、static_rows、dynamic_rows、data_mode、source_refs。时间必须带时区，未知值不填0。")
        uploaded = st.file_uploader("重算请求JSON", type=["json"], key="recompute-upload")
        if st.button("提交评分任务", disabled=uploaded is None):
            try:
                payload = json.loads(uploaded.getvalue())
            except (ValueError, UnicodeDecodeError):
                st.error("JSON格式不正确")
            else:
                task = api("POST", "/api/v1/assessment-runs", payload)
                st.session_state["last_recompute"] = task["run_id"]
        if st.session_state.get("last_recompute"):
            task_panel(st.session_state["last_recompute"])
    with st.expander("快照来源、哈希与冻结内容"):
        st.json(snapshot)


def analysis_page(snapshot):
    st.subheader("分析工作台")
    if snapshot["counts"]["assessments"]:
        grades = ["全部", "健康", "基本健康", "关注", "较差", "严重", "数据不足"]
        grade = st.selectbox("等级筛选", grades)
        params = {"snapshot_id": snapshot["snapshot_id"], "limit": 500}
        if grade != "全部":
            params["grade"] = grade
        response = api("GET", "/api/v1/assessments", **params)
        rows = response["items"]
        st.metric("匹配管段", response["total"])
        st.dataframe([{"管线编号": r["pipe_id"], "Hs": r["legacy_result"]["Hs"],
                       "等级": r["legacy_result"]["grade"], "主要风险因子": r["main_risk"]} for r in rows],
                     use_container_width=True, hide_index=True)
        if rows:
            pid = st.selectbox("查看管段详情", [r["pipe_id"] for r in rows])
            record = api("GET", "/api/v1/assessments", snapshot_id=snapshot["snapshot_id"], pipe_id=pid)["items"][0]
            st.json(record)
            if record["indicator_scores"]:
                st.bar_chart(pd.DataFrame.from_dict(record["indicator_scores"], orient="index", columns=["风险档位"]))
        st.info("这里的Rs/Hs及有效权重占比不等于正式P/C/R/H、失效概率或预测准确率。")
    if snapshot["payload"]["nodes"]:
        diagram(snapshot)
        values = scenario_fields(snapshot, "analysis")
        submitted = st.button("计算指定中断的潜在影响", disabled=values is None)
        if submitted:
            task = api("POST", "/api/v1/simulation-runs", values)
            st.session_state["last_simulation"] = task["run_id"]
        if st.session_state.get("last_simulation"):
            task_panel(st.session_state["last_simulation"])


def decision_page(snapshot):
    st.subheader("方案与智能助手")
    health = api("GET", "/health")
    st.info("LLM模式：" + health["llm_mode"] + "。mock为本地模拟路由，数值仍由确定性工具计算。")
    if snapshot["payload"]["nodes"]:
        values = scenario_fields(snapshot, "decision")
        plans = snapshot["payload"]["plans"]
        selected = st.multiselect("候选方案（2至3个）", [p["plan_id"] for p in plans], default=[p["plan_id"] for p in plans],
                                  format_func=lambda x: next(p["label"] for p in plans if p["plan_id"] == x))
        mandatory = st.multiselect("必须包含的措施（明确的硬约束）", [a["action_id"] for a in snapshot["payload"]["actions"]])
        budget = st.number_input("预算（元；合成情景默认4000元）", min_value=0, value=4000, step=100)
        resources = sorted({r for a in snapshot["payload"]["actions"] for r in (a["resource_requirements"] or {})})
        capacities = {r: st.number_input("资源容量：" + r, min_value=0, value=1 if snapshot["data_mode"] == "synthetic" else 0, key="resource-" + r) for r in resources}
        with st.expander("演示目标配置（修改需导入新快照）"):
            st.json(snapshot["payload"]["value_profile"])
        if values:
            values.update(plan_ids=selected, budget_fen=int(budget) * 100, resource_capacities=capacities,
                          mandatory_action_ids=mandatory)
            if st.button("直接比较候选方案", disabled=len(selected) < 2):
                task = api("POST", "/api/v1/decision-runs", values)
                st.session_state["last_decision"] = task["run_id"]
        if st.session_state.get("last_decision"):
            task_panel(st.session_state["last_decision"])
        assistant_values = values
    else:
        ids = [r["pipe_id"] for r in snapshot["payload"]["assessments"]]
        pid = st.selectbox("助手的管段上下文", [None] + ids, format_func=lambda x: x or "未选择（需要时澄清）")
        assistant_values = {"snapshot_id": snapshot["snapshot_id"], "as_of": snapshot["as_of"], "pipe_id": pid}
    st.divider()
    question = st.text_area("输入问题", placeholder="例如：请比较给定方案并解释推荐理由；查询所选管段的健康评分；V5中三方如何分工？")
    if st.button("提交助手任务", disabled=not question.strip()):
        payload = {**(assistant_values or {"snapshot_id": snapshot["snapshot_id"]}), "question": question}
        task = api("POST", "/api/v1/assistant/tasks", payload)
        st.session_state["last_assistant"] = task["run_id"]
    if st.session_state.get("last_assistant"):
        task_panel(st.session_state["last_assistant"])
    agent_panel(snapshot, assistant_values)


def agent_panel(snapshot, values):
    """V2 Agent入口；模型/数据库未启用时只显示未就绪，不影响旧四页。"""
    st.divider()
    st.subheader("V2 行业智能体（实验）")
    health = api("GET", "/api/v1/agent/health")
    if not health.get("enabled"):
        st.info("Agent未启用：需要AGENT_DATABASE_URL、检查点和模型服务。旧评分/情景/方案接口仍可用。")
        return
    headers = {"X-Agent-Actor": os.getenv("WATER_AGENT_ACTOR", "local-demo"),
               "X-Agent-Scope": os.getenv("WATER_AGENT_SCOPE", "internal")}
    question = st.text_area("向Agent提问", key="agent-question",
                            placeholder="例如：为什么这段管段优先？请引用评分与资料依据。")
    if st.button("提交Agent任务", key="agent-submit", disabled=not question.strip()):
        payload = {"question": question, "snapshot_id": snapshot["snapshot_id"],
                   "pipe_id": (values or {}).get("pipe_id")}
        if values and values.get("fault_edge_id"):
            scenario = {k: values[k] for k in ("snapshot_id", "as_of", "fault_edge_id", "isolation_plan_id", "horizon_hours")}
            payload["scenario"] = scenario
            if values.get("plan_ids"):
                payload["decision"] = {**scenario, "plan_ids": values["plan_ids"], "budget_fen": values.get("budget_fen"),
                                       "resource_capacities": values.get("resource_capacities", {}),
                                       "mandatory_action_ids": values.get("mandatory_action_ids", [])}
        run = api("POST", "/api/v1/agent/chat", payload, request_headers=headers)
        st.session_state["agent_thread"] = run["thread_id"]
        st.success("已提交：" + run["run_id"])
    if st.session_state.get("agent_thread"):
        thread = api("GET", f"/api/v1/agent/threads/{st.session_state['agent_thread']}", request_headers=headers)
        st.dataframe([{"运行": r["run_id"], "状态": "已由恢复任务接续" if r["status"] == "superseded" else r["status"], "阶段": r.get("phase"),
                       "审核": r.get("review_status")} for r in thread["runs"]],
                     use_container_width=True, hide_index=True)
        pending = next((r for r in thread["runs"] if r["status"] in {"awaiting_input", "awaiting_approval"}), None)
        if pending:
            st.warning(json.dumps(pending.get("interrupt"), ensure_ascii=False))
            clarifying = pending["status"] == "awaiting_input"
            with st.form("agent-resume"):
                action = st.selectbox("处理", ["clarify", "cancel"] if clarifying else ["accept", "reject", "revise", "cancel"])
                reason = st.text_input("原因", key="agent-reason")
                if clarifying:
                    pipe_id = st.text_input("补充管段ID（必须属于原任务快照；不需要则留空）")
                    context_json = st.text_area("补充情景/方案/输入批次JSON", value="{}",
                        help='允许字段：scenario、decision、batch_refs。情景包含snapshot_id、as_of、fault_edge_id、isolation_plan_id、horizon_hours；方案还需plan_ids、budget_fen、resource_capacities。不能修改身份、快照授权或工具权限。')
                submitted = st.form_submit_button("恢复Agent")
            if submitted:
                resume_values = {}
                if clarifying and action == "clarify":
                    try:
                        resume_values = json.loads(context_json)
                        if not isinstance(resume_values, dict):
                            raise ValueError("补充内容必须为JSON对象")
                        if pipe_id.strip():
                            resume_values["pipe_id"] = pipe_id.strip()
                    except ValueError as exc:
                        st.error("澄清输入无效：" + str(exc))
                        st.stop()
                api("POST", f"/api/v1/agent/threads/{thread['thread_id']}/resume",
                    {"interrupt_id": pending["interrupt"]["interrupt_id"], "action": action,
                     "values": resume_values, "reason": reason,
                     "expected_revision": pending.get("review_revision", 0)},
                    request_headers=headers)
                st.success("已提交恢复请求")
        latest = thread["runs"][0]
        latest_result = latest.get("result") or {}
        if latest_result.get("answer"):
            st.info(latest_result["answer"])
        with st.expander("Agent工具证据"):
            st.json(api("GET", f"/api/v1/agent/runs/{latest['run_id']}/evidence", request_headers=headers))


def history_page(snapshot):
    st.subheader("反馈与历史")
    tasks = api("GET", "/api/v1/runs")["items"]
    if not tasks:
        st.info("还没有任务，请先发起评分查询或S01分析。")
        return
    st.dataframe([{"运行ID": t["run_id"], "类型": t["kind"], "计算状态": STATUS.get(t["status"], t["status"]),
                   "审核状态": t["review_status"], "创建时间": t["created_at"]} for t in tasks], use_container_width=True, hide_index=True)
    rid = st.selectbox("查看运行", [t["run_id"] for t in tasks])
    task = api("GET", f"/api/v1/runs/{rid}")
    task_panel(rid)
    if st.button("以同一快照与参数新建重放任务", disabled=task["status"] in {"queued", "running"}):
        new = api("POST", f"/api/v1/runs/{rid}/replay")
        st.success("已创建：" + new["run_id"])
    if task["review_status"] and task["status"] == "succeeded":
        st.warning("人工审核仅登记意见，不代表关阀、派单或实际执行。姓名为本地演示自报；多人应用需接认证。")
        with st.form("feedback-" + rid):
            reviewer = st.text_input("审核人")
            status = st.selectbox("处理意见", ["accepted", "rejected", "revised"],
                                  format_func=lambda x: {"accepted": "接受建议", "rejected": "拒绝建议", "revised": "提出修改"}[x])
            reason_type = st.selectbox("原因类别", ["fact", "constraint", "goal", "explanation", "other"])
            reason = st.text_area("理由或修改意见（不会自动改数据与权重）")
            confirmed = st.checkbox("确认仅登记审核意见，未执行任何现场操作")
            submit = st.form_submit_button("提交人工反馈")
        if submit:
            if not confirmed or not reviewer.strip() or not reason.strip():
                st.error("请填写审核人和原因，并勾选确认。")
            else:
                api("POST", "/api/v1/feedback", {"run_id": rid, "reviewer": reviewer, "review_status": status,
                    "reason_type": reason_type, "reason": reason, "expected_revision": task["review_revision"]})
                st.success("反馈已登记；未改变模型或实际执行状态。")
    st.json(api("GET", "/api/v1/feedback", run_id=rid)["items"])


def main():
    st.set_page_config(page_title="WaterPipNet | 供水智能分析", layout="wide")
    st.markdown("""<style>.block-container{padding-top:2rem;max-width:1400px}h1{letter-spacing:-.04em}
    [data-testid=stSidebar]{background:#f0f5fa}[data-testid=stMetric]{background:#f6f8fb;border:1px solid #e3eaf3;padding:16px;border-radius:12px}</style>""", unsafe_allow_html=True)
    st.title("WaterPipNet · 供水智能分析工作台")
    st.caption("独立技术原型 / 评分、条件影响、方案解释 / 不连接实际控制系统")
    health = api("GET", "/health")
    snapshots = api("GET", "/api/v1/snapshots")["items"]
    if not snapshots:
        st.warning("尚无快照，请通过API导入或启用内置样例。")
        return
    choices = {s["snapshot_id"]: s for s in snapshots}
    preferred = st.session_state.pop("select_next", None)
    if preferred in choices:
        st.session_state["snapshot_choice"] = preferred
    with st.sidebar:
        st.header("数据空间")
        sid = st.selectbox("冻结快照", list(choices), format_func=lambda x: choices[x]["name"], key="snapshot_choice")
        page = st.radio("工作区", ["数据与能力", "分析工作台", "方案与智能助手", "反馈与历史"])
        st.divider()
        st.caption("API / " + health["version"])
        st.caption("Worker / " + ("运行中" if health["worker_alive"] else "未运行"))
        st.caption("LLM / " + health["llm_mode"])
        st.caption("所有数值来自工具；证据不足时返回缺项。")
    snapshot = api("GET", f"/api/v1/snapshots/{sid}")
    st.warning(MODE[snapshot["data_mode"]] + " ｜ 不是业务验收结果")
    st.caption(f"快照 {sid} · 时点 {snapshot['as_of']} · SHA-256 {snapshot['sha256'][:16]}…")
    {"数据与能力": data_page, "分析工作台": analysis_page, "方案与智能助手": decision_page, "反馈与历史": history_page}[page](snapshot)


if __name__ == "__main__":
    main()
