import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import type {
  GasAgentQueryResponse, GasDataTopic, GasDrillComparison, GasDrillToolResult,
  GasM2TopicAnswer, GasSyntheticScenario,
} from "../../shared/contracts/platform";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasReadError } from "./GasDetails";
import { GasApiError, toGasError } from "./engineeringApi";
import { toolAssetIds } from "./gasAgentApi";
import { scenarioReference } from "./scenarioContract";
import { GAS_TOPIC_QUESTION_LIMIT, topicFollowUp } from "./topicContract";
import "./gas-topic.css";

type ToolAnswer =
  | { kind: "topic"; answer: GasM2TopicAnswer }
  | { kind: "agent"; answer: GasAgentQueryResponse }
  | { kind: "drill"; answer: GasDrillToolResult };
type Turn = { question: string; prompt: string; answer: ToolAnswer };
type ToolRoute = "topic" | "asset" | "event" | "lookup" | "simulate" | "compare" | "report";

function routeQuestion(question: string, hasAsset: boolean, hasScenario: boolean, hasComparison: boolean): ToolRoute {
  if (hasComparison && /版本|修订|比较|对比|差异|变化/.test(question)) return "compare";
  if (hasScenario && /报告|审计/.test(question)) return "report";
  if (hasScenario && /拓扑|连通|模拟|演练|故障|隔离|恢复/.test(question)) return "simulate";
  if (hasScenario && /场景|原件|校验/.test(question)) return "lookup";
  if (/syn-gas-event-[A-Za-z0-9_-]+/i.test(question)) return "event";
  if (hasAsset && /事件|告警|关联事件/.test(question)) return "event";
  if (hasAsset && /资产|管段|管线|阀门|节点|材质|管径|字段|详情/.test(question)) return "asset";
  return "topic";
}

function previousPrompt(question: string, previous?: Turn): string {
  if (!previous) return topicFollowUp(question);
  const answer = previous.answer.kind === "agent"
    ? { facts: ["上一轮已返回当前对象的可验证只读证据。"], answer: "上一轮已返回当前对象的可验证只读证据。" }
    : previous.answer.kind === "topic"
    ? previous.answer.answer
    : { facts: [previous.answer.answer.answer], answer: previous.answer.answer.answer };
  return topicFollowUp(question, { question: previous.question, answer });
}

function toolText(answer: ToolAnswer): string {
  if (answer.kind === "topic") return answer.answer.answer;
  if (answer.kind === "agent") {
    const fallback = answer.answer.model_status === "unavailable" ? "Qwen 当前不可用，以下为服务端确定性工具结果。" : "";
    return [fallback, answer.answer.explanation || answer.answer.tools[0]?.limitation || "工具未返回可验证结论。"].filter(Boolean).join("\n");
  }
  return answer.answer.answer;
}

function toolName(answer: ToolAnswer): string {
  return answer.kind === "topic" ? answer.answer.tool_name
    : answer.kind === "agent" ? answer.answer.tools[0]?.tool_name || "agent.query" : answer.answer.tool_name;
}

function evidenceRefs(answer: ToolAnswer): string[] {
  if (answer.kind === "topic") return answer.answer.evidence_refs;
  if (answer.kind === "agent") return answer.answer.evidence_refs || [];
  return answer.answer.evidence_refs;
}

function modelLabel(answer: ToolAnswer): string {
  if (answer.kind === "topic") return answer.answer.explanation_source === "qwen_evidence_selection"
    ? answer.answer.model_id || "Qwen3.8-27B" : "确定性证据 · 规则回答";
  if (answer.kind === "agent") return answer.answer.model_status === "ready"
    ? answer.answer.model_id || "Qwen3.8-27B" : "确定性工具 · 规则降级";
  return answer.answer.explanation_source === "qwen_evidence_selection"
    ? answer.answer.model_id || "Qwen3.8-27B" : "确定性证据 · 规则回答";
}

function scenarioIdentity(scenario?: GasSyntheticScenario, comparison?: GasDrillComparison) {
  const current = scenario?.analysis && scenarioReference(scenario);
  const after = comparison?.selection.after;
  return comparison
    ? [comparison.selection.binding.dataset_id, comparison.selection.binding.version, "comparison",
      comparison.comparison_sha256, after?.scenario_id || "", after?.revision || ""].join(":")
    : current
      ? [scenario?.input.binding.dataset_id, scenario?.input.binding.version, current.scenario_id,
        current.revision, current.analysis_sha256].join(":")
      : "";
}

export function GasTopicConversation({ topic, title, refresh, render, scenario, comparison }: {
  topic: GasDataTopic; title: string; refresh(): void;
  scenario?: GasSyntheticScenario; comparison?: GasDrillComparison;
  render?: (content: ReactNode) => ReactNode;
}) {
  const c = useGasCockpit();
  const input = useId();
  const [draft, setDraft] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<GasApiError>();
  const [retryQuestion, setRetryQuestion] = useState<string>();
  const request = useRef<AbortController>(null);
  const requestContext = useRef("");
  const history = useRef<HTMLOListElement>(null);
  const open = Boolean(render);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (history.current) history.current.scrollTop = history.current.scrollHeight;
  }, [turns, open]);

  const asset = topic.selected_asset;
  const subject = asset?.asset_id.replace(/^syn-gas-(pipe|node|valve|consumer)-/, "") ?? title;
  const reference = asset
    ? `gas-m2-record:${topic.binding.dataset_id}:${asset.asset_id}`
    : "gas-topic:sha256:" + topic.evidence_sha256;
  const contextKey = [
    topic.binding.dataset_id, topic.binding.version, topic.binding.registry_sha256,
    topic.selection.scope, topic.selection.district_id ?? "", topic.selection.subnet_id ?? "",
    topic.selection.asset_id ?? "", topic.evidence_sha256, scenarioIdentity(scenario, comparison),
    c.caseBinding?.network_content_sha256 ?? "", c.caseBinding?.case_content_sha256 ?? "",
  ].join("|");
  requestContext.current = contextKey;
  useEffect(() => {
    request.current?.abort();
    request.current = null;
    setTurns([]);
    setDraft("");
    setBusy(false);
    setError(undefined);
    setRetryQuestion(undefined);
  }, [contextKey]);

  function jumpToEvidence(answer: ToolAnswer) {
    const ids = answer.kind === "agent" ? toolAssetIds(answer.answer.tools[0]) : [];
    if (asset) c.locateAssetsOnMap({ view: "topology", assetId: asset.asset_id });
    else if (ids[0]) c.locateAssetsOnMap({ view: "topology", assetId: ids[0] });
    else if (answer.kind === "drill") c.focusTopology();
    else {
      c.closeAssistantDialog();
      c.selectDistrict(topic.selection.district_id ?? null);
    }
  }

  async function submit(questionOverride?: string) {
    const question = (questionOverride ?? draft).trim();
    if (!question || request.current) return;
    const controller = new AbortController();
    request.current = controller;
    const submittedContext = contextKey;
    setError(undefined);
    setBusy(true);
    try {
      const prompt = previousPrompt(question, turns.at(-1));
      const route = routeQuestion(question, Boolean(asset), Boolean(scenario), Boolean(comparison));
      let answer: ToolAnswer;
      if (route === "asset" || route === "event") {
        const value = await c.agent.submit({
          question: prompt,
          case_id: c.caseView.data?.case_id,
          asset_id: asset?.asset_id,
          case_binding: c.caseBinding,
          tool_hint: route === "event" ? "event.query" : "asset.query",
        });
        if (!value) throw new GasApiError("unavailable", "Qwen 当前不可用，当前对象查询未生成回答，请重试当前查询。");
        answer = { kind: "agent", answer: value };
      } else if (route !== "topic") {
        const ref = scenario && scenarioReference(scenario);
        const selection = route === "compare" && comparison ? comparison.selection
          : ref ? { binding: topic.binding, before: null, after: ref } : null;
        if (!selection) throw new GasApiError("unavailable", "当前没有绑定的已分析演练修订，无法调用该工具。");
        const tool = route === "compare" ? "compare" : route;
        const expected_sha256 = route === "compare" && comparison ? comparison.comparison_sha256 : ref!.analysis_sha256;
        answer = { kind: "drill", answer: await c.scenarioApi.queryDrillTool({
          tool, selection, expected_sha256, question: prompt,
        }, controller.signal) };
      } else {
        answer = { kind: "topic", answer: await c.dataM2Api.topicQuery({
          tool: "navigation", binding: topic.binding, selection: topic.selection,
          expected_sha256: topic.evidence_sha256, question: prompt,
        }, controller.signal) };
      }
      if (!controller.signal.aborted && requestContext.current === submittedContext) {
        setTurns(previous => [...previous, { question, prompt, answer }].slice(-20));
        setDraft("");
        setRetryQuestion(undefined);
      }
    } catch (caught) {
      if (!controller.signal.aborted && requestContext.current === submittedContext) {
        setError(toGasError(caught));
        setRetryQuestion(question);
      }
    } finally {
      if (request.current === controller) {
        request.current = null;
        if (!controller.signal.aborted) setBusy(false);
      }
    }
  }

  function clear() {
    request.current?.abort();
    request.current = null;
    setTurns([]);
    setDraft("");
    setBusy(false);
    setError(undefined);
    setRetryQuestion(undefined);
  }

  if (!render) return null;
  return render(<section className="gas-topic gas-topic-conversation" data-testid="gas-topic-conversation"
    data-topic-scope={topic.selection.scope} data-topic-sha256={topic.evidence_sha256}
    data-context-binding={contextKey} data-scenario-identity={scenarioIdentity(scenario, comparison)} aria-label="当前专题对话">
    <header>
      <div data-testid="gas-topic-chat-context"><h3>{subject}</h3>
        <span>{asset ? "GAS-M0 合成资产" : "重庆行政参考"} · {topic.selection.district_id ?? asset?.asset_id}</span>
      </div>
      <button type="button" onClick={c.openAssistantTools}>通用工具</button>
    </header>
    {turns.length > 0 && <ol className="gas-topic-turns" ref={history} aria-label="当前专题问答" aria-live="polite">
      {turns.map(({ question, answer }) => {
        const refs = evidenceRefs(answer);
        const canJump = answer.kind === "topic"
          ? topic.evidence_refs.includes(reference) && refs.includes(reference) : refs.length > 0;
        const label = answer.kind === "drill" ? "打开当前演练拓扑" : asset ? "打开 " + subject + " 资产详情" : "查看" + subject + "专题";
        return <li key={answer.kind + ":" + (answer.answer.request_id || question)} className="gas-agent-turn gas-topic-answer"
          data-testid="gas-topic-answer" data-tool-name={toolName(answer)} data-context-binding={contextKey}>
          <h4>{question}</h4>
          <strong>{modelLabel(answer)}</strong>
          <p>{toolText(answer)}</p>
          <div className="gas-topic-actions">
            {canJump && <button type="button" data-testid="gas-topic-evidence-jump" onClick={() => jumpToEvidence(answer)}>{label}</button>}
            {topic.administrative_source_uri && refs.includes("gas-m2-admin-reference:" + topic.administrative_source_uri) &&
              <a href={topic.administrative_source_uri} target="_blank" rel="noreferrer">行政区划来源</a>}
          </div>
          <details><summary>工具回执与来源</summary>
            <p><code>{answer.answer.request_id} · {toolName(answer)}</code></p>
            {answer.kind === "drill" && <p>绑定修订 <code>{"r" + (answer.answer.selection.after?.revision || "compare")}</code> · 结果指纹 <code>{answer.answer.verified_sha256}</code></p>}
            <ul>{refs.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul>
          </details>
        </li>;
      })}
    </ol>}
    <form className="gas-agent-input-form" onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label htmlFor={input}>{turns.length ? "追问当前专题" : "询问当前专题"}</label>
      <textarea id={input} data-testid="gas-topic-chat-input" maxLength={GAS_TOPIC_QUESTION_LIMIT} rows={2}
        autoFocus value={draft} readOnly={busy} onChange={event => setDraft(event.target.value)}
        placeholder={turns.length ? "接着问这个对象" : asset ? "这个管段的材质和管径是什么？" : "当前区县有哪些已登记燃气资料？"} />
      <div className="gas-agent-form-actions">
        <button type="submit" disabled={busy || !draft.trim()}>发送查询</button>
        {(turns.length > 0 || busy) && <button type="button" onClick={clear}>清空本次对话</button>}
      </div>
    </form>
    {busy && <p role="status">正在查询当前专题…</p>}
    {error && (error.status === 409
      ? <GasReadError error={error} retry={refresh} label="刷新数据登记" /> : <div role="alert"><p>{error.message}</p>{retryQuestion && <button type="button" onClick={() => void submit(retryQuestion)}>重试当前查询</button>}</div>)}
  </section>);
}
