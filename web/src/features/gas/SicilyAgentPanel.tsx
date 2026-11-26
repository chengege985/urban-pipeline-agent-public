
import { useEffect, useRef, useState } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import type { SicilyAgentAnswer, SicilyAgentRun } from "./publicDatasets";
import type { useSicilyWorkspace } from "./SicilyWorkspace";

const samples = ["当前数据可以做哪些分析？", "254 号管段连接哪两个节点？",
  "按新增断连节点数优先，比较 342 和 254 号管段退出", "为什么这次有节点断连？"];

type Turn = { question: string; reply?: SicilyAgentAnswer; error?: string; confirmationContext?: string };

export function useSicilyAgent(w: ReturnType<typeof useSicilyWorkspace>) {
  const c = useGasCockpit();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [health, setHealth] = useState<"configured" | "unavailable">();
  const [pending, setPending] = useState(false);
  const [actionError, setActionError] = useState("");
  const controller = useRef<AbortController | null>(null);
  const anchor = JSON.stringify(w.agentContext);
  useEffect(() => {
    setTurns([]); setQuestion(""); setPending(false); setActionError(""); setHealth(undefined);
    if (!w.version) return;
    const signal = new AbortController();
    void c.publicApi.sicilyAgentHealth(signal.signal).then(response => setHealth(response.model_status))
      .catch(() => { if (!signal.signal.aborted) setHealth("unavailable"); });
    return () => { signal.abort(); controller.current?.abort(); controller.current = null; };
  }, [c.publicApi, w.version]);
  async function request(mode: "query" | "confirm", fields: Record<string, unknown>, title: string) {
    if (!w.version || controller.current) return;
    const abort = new AbortController();
    controller.current = abort;
    setPending(true); setActionError("");
    const index = turns.length;
    setTurns(prev => [...prev, { question: title }]);
    try {
      const reply = await c.publicApi.sicilyAgentAnswer(mode, w.agentContext, fields, abort.signal);
      if (abort.signal.aborted) return;
      setTurns(prev => prev.map((turn, i) => i === index ? { ...turn, reply, confirmationContext: anchor } : turn));
    } catch (error) {
      if (!abort.signal.aborted) setTurns(prev => prev.map((turn, i) =>
        i === index ? { ...turn, error: error instanceof Error ? error.message : "西西里助手暂不可用" } : turn));
    } finally {
      if (controller.current === abort) { controller.current = null; setPending(false); }
    }
  }
  function ask(text: string) {
    if (!text.trim() || pending || !w.version) return;
    const answers = turns.flatMap(turn => turn.reply ? [turn.reply] : []).reverse();
    const previous = answers.find(answer => answer.runs?.length || answer.object);
    const runAnswer = answers.find(answer => answer.runs?.length);
    const objectAnswer = answers.find(answer => answer.object);
    setQuestion("");
    void request("query", {
      question: text.trim(), current_execution_id: w.result?.execution_id, current_baseline_id: w.baseline?.execution_id,
      previous_execution_ids: runAnswer?.runs?.map(r => r.execution_id) ?? [],
      previous_case_id: /刚才|上一/.test(text) && /这根|管道属性|它的/.test(text)
        ? objectAnswer?.case_id : previous?.case_id,
      previous_object_id: objectAnswer?.object?.id, previous_object_kind: objectAnswer?.object?.kind,
    }, text.trim());
  }
  function confirm(turn: Turn) {
    const plan = turn.reply?.plan;
    if (!plan || turn.confirmationContext !== anchor || Date.now() >= plan.expires_at * 1000) return;
    void request("confirm", { plan_id: plan.id }, "确认运行 " + plan.edges.join("、"));
  }
  function openCondition(caseId: string) {
    if (caseId !== w.caseId) w.chooseCase(caseId);
    c.closeAssistantDialog(); c.openWindow("impact");
  }
  async function openRun(run: SicilyAgentRun) {
    setActionError("");
    try { await w.showAgentRun(run); c.closeAssistantDialog(); }
    catch (error) { setActionError(error instanceof Error ? error.message : "该次执行暂不可读取"); }
  }
  function openObject(object: NonNullable<SicilyAgentAnswer["object"]>) {
    w.showObject(object.id, object.kind, object.case_id);
    c.closeAssistantDialog();
  }
  return { version: w.version, caseId: w.caseId, turns, question, setQuestion, pending, health, anchor,
    actionError, ask, confirm, openCondition, openRun, openObject };
}

export function SicilyAgentPanel({ agent }: { agent: ReturnType<typeof useSicilyAgent> }) {
  return <div className="gas-sicily-agent">
    <p className="public-note">西西里输气研究样本 · 当前工况 {agent.caseId || "待加载"}</p>
    <p>{agent.health === "configured" ? "Qwen3.8-27B 已配置，调用状态见每条回答" : "规则查询可用 · Qwen3.8-27B 暂不可用"}</p>
    <div className="gas-agent-chips" aria-label="西西里提问示例">{samples.map(sample =>
      <button key={sample} type="button" disabled={agent.pending || !agent.version} onClick={() => agent.ask(sample)}>{sample}</button>)}</div>
    <form className="gas-agent-input-form" onSubmit={event => { event.preventDefault(); agent.ask(agent.question); }}>
      <label htmlFor="sicily-question">向西西里助手提问</label>
      <textarea id="sicily-question" rows={2} maxLength={600} value={agent.question}
        onChange={event => agent.setQuestion(event.target.value)} />
      <button type="submit" disabled={!agent.question.trim() || agent.pending || !agent.version}>发送</button>
    </form>
    {!agent.version && <p role="status">请先加载西西里数据。</p>}
    {agent.pending && <p role="status">正在查询或执行已确认的情景…</p>}
    {agent.actionError && <p role="alert">{agent.actionError}</p>}
    <div aria-label="西西里对话记录">{[...agent.turns].reverse().map((turn, index, turns) => {
      const reply = turn.reply;
      const confirmable = index === 0 && turn.confirmationContext === agent.anchor &&
        !!reply?.plan && Date.now() < reply.plan.expires_at * 1000;
      return <article className="gas-agent-result-card" key={turns.length - index}>
        <p><strong>{turn.question}</strong></p>
        {turn.error && <p role="alert">{turn.error}</p>}
        {reply && <>
          <h3>{reply.answer}</h3>
          <small>{reply.case_id} · {reply.model_status === "ready" ? "Qwen3.8-27B 选择工具" :
            reply.model_status === "unavailable" ? "规则查询" : "后端证据"}</small>
          {reply.rows.length > 0 && <dl className="gas-fields">{reply.rows.map(({label, value}) =>
            <div key={label}><dt>{label}</dt><dd>{value ?? "未提供"}</dd></div>)}</dl>}
          {reply.plan && <>
            {reply.plan.cached_edges.length > 0 && <p>复用同条件结果 {reply.plan.cached_edges.join("、")}</p>}
            <button type="button" disabled={!confirmable || agent.pending} onClick={() => agent.confirm(turn)}>确认运行</button>
            {!confirmable && <small> 如需再次执行，请重新预览。</small>}
          </>}
          <div className="gas-agent-chips">
            {reply.object && <button type="button" onClick={() => agent.openObject(reply.object!)}>在拓扑中查看</button>}
            {reply.open_condition && <button type="button" onClick={() => agent.openCondition(reply.case_id)}>打开工况</button>}
            {reply.runs?.map(run => <button type="button" key={run.execution_id}
              onClick={() => void agent.openRun(run)}>回放管段 {run.edge_id} 结果</button>)}
          </div>
          <p className="public-note">{reply.boundary}</p>
          <details><summary>来源与复核记录</summary><p>{reply.data_version} · {reply.rule_version}</p>
            <a href={reply.source_url} target="_blank" rel="noreferrer">打开作者公开资料</a>
            {Object.values(reply.file_refs ?? {}).map(value => <p key={value}>{value}</p>)}
            {reply.runs?.map(run => <p key={run.execution_id}>管段 {run.edge_id} · 执行 {run.execution_id}<br />输入 SHA-256 {run.input_sha256}</p>)}
          </details>
        </>}
      </article>;
    })}</div>
  </div>;
}
