import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { ASSISTANT_SUGGESTIONS, DEFAULT_DECISION_CONSTRAINTS } from "../../../shared/contracts/constants";
import type { AgentContext, AnalysisContext, ChatMessage, HealthAssessment, ServiceImpact, ProviderMeta, WaterPulseDataset } from "../../../shared/contracts/domain";
import type { WaterPulseProvider } from "../../../shared/contracts/provider";
import { currentContext, inputKey } from "../../../shared/utils/context";
import { formatScore } from "../../../shared/utils/format";
import { EvidenceList } from "../../../shared/ui/EvidenceList";
import { GradePill } from "../../../shared/ui/StatusPill";
import { ToolCallCard } from "./ToolCallCard";
import { AssistantIcon, ArrowRightIcon, ResetIcon, ScenarioIcon } from "../../../shared/ui/Icons";

interface AssistantChatProps {
  dataset: WaterPulseDataset;
  provider: WaterPulseProvider;
  meta: ProviderMeta;
  initialPipeId?: string;
  initialScenarioId?: string;
  onNavigate: (path: string) => void;
  context?: AnalysisContext | null;
}

function makeId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `${prefix}-${random}`;
}

function now(): string {
  return new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

export function AssistantChat({
  dataset,
  provider,
  meta,
  initialPipeId,
  initialScenarioId,
  onNavigate,
  context: sharedContext,
}: AssistantChatProps) {
  const sortedPipes = useMemo(
    () =>
      [...dataset.pipes].sort(
        (a, b) => (dataset.assessments[a.id]?.score ?? 101) - (dataset.assessments[b.id]?.score ?? 101),
      ),
    [dataset.assessments, dataset.pipes],
  );
  const shared = currentContext(sharedContext, dataset);
  const [pipeId, setPipeId] = useState(initialPipeId ?? shared?.pipeId ?? sortedPipes[0]?.id ?? "");
  const [scenarioId, setScenarioId] = useState(initialScenarioId ?? shared?.scenarioId ?? dataset.scenarios[0]?.id ?? "");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pipe = dataset.pipes.find((item) => item.id === pipeId);
  const scenario = dataset.scenarios.find((item) => item.id === scenarioId);
  const isolationPlanId = shared?.scenarioId === scenarioId ? shared.isolationPlanId : scenario?.defaultIsolationPlanId;
  const override = useMemo(() => shared?.pipeId === pipeId ? shared.override : {}, [shared, pipeId]);
  const timelineContext = shared?.scenarioId === scenarioId && shared.isolationPlanId === isolationPlanId ? shared : null;
  const constraints = shared?.constraints ?? DEFAULT_DECISION_CONSTRAINTS;
  const key = inputKey([pipeId, scenarioId, isolationPlanId, override, constraints, timelineContext?.planId, timelineContext?.targetServiceIds, timelineContext?.horizonHours, timelineContext?.actionSchedule, timelineContext?.timelineDataVersion, timelineContext?.timelineAsOf, dataset.meta.dataVersion, dataset.meta.modelVersion]);
  const latestKey = useRef(key); latestKey.current = key;
  const requestSequence = useRef(0);
  useEffect(() => { requestSequence.current += 1; setLoading(false); }, [key]);
  const [calculated, setCalculated] = useState<{ key: string; assessment?: HealthAssessment; impact?: ServiceImpact } | null>(null);
  const [contextError, setContextError] = useState<string | null>(null);
  const assessment = calculated?.key === key ? calculated.assessment : undefined;
  const impact = calculated?.key === key ? calculated.impact : undefined;
  useEffect(() => {
    let cancelled = false; setContextError(null); setCalculated(null);
    void Promise.all([pipe ? provider.evaluate(pipe, override) : undefined,
      scenario && isolationPlanId ? provider.simulate(scenario.id, isolationPlanId) : undefined]).then(([assessment, impact]) => {
      if (!cancelled) setCalculated({ key, assessment, impact });
    }).catch((error: unknown) => { if (!cancelled) setContextError(error instanceof Error ? error.message : "上下文计算失败。"); });
    return () => { cancelled = true; };
  }, [key, pipe, scenario, isolationPlanId, override, provider]);

  useEffect(() => {
    if (initialPipeId) setPipeId(initialPipeId);
  }, [initialPipeId]);

  useEffect(() => {
    if (initialScenarioId) setScenarioId(initialScenarioId);
  }, [initialScenarioId]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, loading]);

  async function send(question: string) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;
    const userMessage: ChatMessage = {
      id: makeId("msg"),
      role: "user",
      text: trimmed,
      createdAt: now(),
    };
    setMessages((current) => [...current, userMessage]);
    setInput("");
    setLoading(true);
    const sentKey = key;
    const sequence = ++requestSequence.current;
    const context: AgentContext = {
      pipe,
      pipeId: pipe?.id,
      assessment,
      scenarioId: scenario?.id,
      isolationPlanId,
      override,
      constraints,
      impact,
      planId: timelineContext?.planId,
      targetServiceIds: timelineContext?.targetServiceIds,
      horizonHours: timelineContext?.horizonHours,
      timeline: timelineContext?.timeline,
      timelineGate: timelineContext?.timelineGate,
      actionSchedule: timelineContext?.actionSchedule,
      timelineDataVersion: timelineContext?.timelineDataVersion,
      timelineAsOf: timelineContext?.timelineAsOf,
    };
    try {
      const response = await provider.ask(trimmed, context);
      if (latestKey.current !== sentKey || sequence !== requestSequence.current) return;
      setMessages((current) => [
        ...current,
        { id: makeId("msg"), role: "assistant", response, createdAt: now() },
      ]);
    } catch (caught) {
      if (latestKey.current !== sentKey || sequence !== requestSequence.current) return;
      const message = caught instanceof Error ? caught.message : "演示助手暂时不可用。";
      setMessages((current) => [
        ...current,
        {
          id: makeId("msg"),
          role: "assistant",
          text: `演示助手不可用：${message}。这不会影响评分、情景和方案页面。`,
          createdAt: now(),
        },
      ]);
    } finally {
      if (sequence === requestSequence.current) setLoading(false);
    }
  }

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void send(input);
  }

  return (
    <div className="assistant-layout">
      <section className="panel chat-panel">
        <div className="chat-head">
          <div className="chat-identity">
            <span className="assistant-mark"><AssistantIcon size={20} /></span>
            <div>
              <strong>水脉演示助手</strong>
              <small>{provider.mode === "live" ? "API 已接通 · 每轮标记实际生成来源" : "本地规则与模板"}</small>
            </div>
          </div>
          <div className="chat-actions">
            <span className="demo-assistant-badge">演示助手</span>
            <button className="icon-button" onClick={() => setMessages([])} disabled={messages.length === 0} aria-label="清空对话">
              <ResetIcon size={15} />
            </button>
          </div>
        </div>

        <div className="chat-scroll" ref={scrollRef} aria-live="polite">
          {messages.length === 0 ? (
            <div className="chat-welcome">
              <span className="welcome-icon"><AssistantIcon size={26} /></span>
              <h3>我可以解释风险，但不能替你做现场决定</h3>
              <p>
                当前是比赛演示助手：只引用 DemoHealth、合成情景和固定候选方案。
                数值由确定性工具计算；API 模式可能使用模型组织解释，实际来源在每轮回答旁标记。
              </p>
              <div className="suggestion-grid">
                {ASSISTANT_SUGGESTIONS.map((question) => (
                  <button className="suggestion-chip" key={question} onClick={() => void send(question)}>
                    {question}
                  </button>
                ))}
              </div>
            </div>
          ) : null}

          {messages.map((message) => (
            <div className={`chat-message ${message.role}`} key={message.id}>
              <div className="chat-avatar">{message.role === "assistant" ? <AssistantIcon size={16} /> : "你"}</div>
              <div className="chat-bubble">
                <div className="chat-meta-line">
                  <strong>{message.role === "assistant" ? "演示助手" : "你"}</strong>
                  <small>{message.createdAt}</small>
                </div>
                {message.text ? <p className="chat-text">{message.text}</p> : null}
                {message.response ? (
                  <>
                    <p className="muted small">来源：{message.response.engine === "llm" ? "模型选择工具 · 确定性结果解释" : message.response.engine === "template" || message.response.mode === "demo" ? "确定性模板" : "API 未声明来源"}{message.response.fallbackReason ? ` · 回退原因：${message.response.fallbackReason}` : ""}</p>
                    <p className="chat-text">{message.response.answer}</p>
                    {message.response.toolCalls.length ? (
                      <div className="tool-grid">
                        {message.response.toolCalls.map((call) => <ToolCallCard call={call} key={call.id} />)}
                      </div>
                    ) : null}
                    <div className="chat-evidence">
                      <span className="muted small">引用证据</span>
                      <EvidenceList ids={message.response.evidenceIds} />
                    </div>
                    <details className="chat-limits">
                      <summary>查看限制与边界</summary>
                      <ul>
                        {message.response.limitations.map((limit) => <li key={limit}>{limit}</li>)}
                      </ul>
                    </details>
                    <div className="suggestion-grid compact">
                      {message.response.suggestedQuestions.slice(0, 3).map((question) => (
                        <button className="suggestion-chip" key={question} onClick={() => void send(question)}>
                          {question}
                        </button>
                      ))}
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          ))}
          {loading ? (
            <div className="chat-message assistant">
              <div className="chat-avatar"><AssistantIcon size={16} /></div>
              <div className="chat-bubble typing"><span /><span /><span /><small>助手处理中</small></div>
            </div>
          ) : null}
        </div>

        <form className="chat-input-row" onSubmit={submit}>
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="问评分、影响或给定措施；使用当前上下文"
            aria-label="向演示助手提问"
          />
          <button className="button-primary" type="submit" disabled={!input.trim() || loading}>发送</button>
        </form>
        <p className="chat-footnote">
          API 接通与语言模型执行分别标记；模板回退不表示模型已经完成推理。
        </p>
      </section>

      <aside className="panel context-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Conversation context</p>
            <h2>当前上下文</h2>
            <p>预算 {constraints.budgetYuan} 元；隔离 {isolationPlanId ?? "未选择"}；{shared ? "继承情景页约束与输入" : "使用明示演示默认约束"}</p>
            {contextError ? <p className="inline-warning">{contextError}</p> : null}
          </div>
        </div>
        <label className="stacked-label">
          管段
          <select value={pipeId} onChange={(event) => setPipeId(event.target.value)}>
            {sortedPipes.map((item) => (
              <option key={item.id} value={item.id}>
                {item.id} · {item.name} {dataset.assessments[item.id]?.score ?? "—"}
              </option>
            ))}
          </select>
        </label>
        <label className="stacked-label">
          情景
          <select value={scenarioId} onChange={(event) => setScenarioId(event.target.value)}>
            {dataset.scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>

        {pipe && assessment ? (
          <div className="context-card">
            <div className="context-card-head">
              <div><strong>{pipe.id}</strong><small>{pipe.name}</small></div>
              <GradePill grade={assessment.grade} />
            </div>
            <div className="context-score"><b>{formatScore(assessment.score)}</b><span>DemoHealth</span></div>
            <p>{assessment.summary}</p>
            <button className="text-button" onClick={() => onNavigate(`/pipe/${pipe.id}`)}>
              打开管段详情 <ArrowRightIcon size={14} />
            </button>
          </div>
        ) : null}

        {scenario && impact ? (
          <div className="context-card">
            <div className="context-card-head">
              <div><strong>{scenario.name}</strong><small>{scenario.id}</small></div>
              <ScenarioIcon size={17} />
            </div>
            <p>{scenario.summary}</p>
            <div className="context-kpis">
              <span><b>{impact.affectedServices.length}</b> 服务</span>
              <span><b>{impact.affectedCriticalServiceIds.length}</b> 关键</span>
            </div>
            <button className="text-button" onClick={() => onNavigate(`/scenario/${scenario.id}?pipe=${pipe?.id ?? ""}`)}>
              打开情景分析 <ArrowRightIcon size={14} />
            </button>
          </div>
        ) : null}

        <div className="context-note">
          <strong>本轮边界</strong>
          <p>助手只解释已计算的因子、情景和方案，不生成新的数值结论；不接受关阀、派单、概率或水力请求。</p>
          <span>{meta.modelVersion} · {meta.dataVersion}</span>
        </div>
      </aside>
    </div>
  );
}
