import { useEffect, useRef, useState } from "react";
import { toGasError } from "./engineeringApi";

interface EvidenceAnswer {
  request_id: string;
  tool_name?: string;
  answer: string;
  model_id?: string | null;
  explanation_source?: "backend_facts" | "qwen_evidence_selection";
  evidence_refs: string[];
}

export function GasEvidenceAsk({ label, prompt, ask }: {
  label: string; prompt: string; ask(question: string, signal: AbortSignal): Promise<EvidenceAnswer>;
}) {
  const [question, setQuestion] = useState(prompt);
  const [answer, setAnswer] = useState<EvidenceAnswer>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const operation = useRef<AbortController | undefined>(undefined);
  useEffect(() => () => operation.current?.abort(), []);

  async function submit() {
    if (operation.current || !question.trim()) return;
    const controller = new AbortController();
    operation.current = controller;
    setBusy(true);
    setAnswer(undefined);
    setError("");
    try {
      const response = await ask(question.trim(), controller.signal);
      if (!controller.signal.aborted) setAnswer(response);
    } catch (caught) {
      if (!controller.signal.aborted) setError(toGasError(caught).message);
    } finally {
      if (!controller.signal.aborted) setBusy(false);
      operation.current = undefined;
    }
  }
  return <details className="gas-topic-ask">
    <summary>{label}</summary>
    <form onSubmit={event => { event.preventDefault(); void submit(); }}>
      <label>提问<input value={question} onChange={event => setQuestion(event.target.value)} maxLength={600} /></label>
      <button type="submit" disabled={busy || !question.trim()}>读取并解释证据</button>
    </form>
    {busy && <p role="status">正在读取同版本证据…</p>}
    {error && <p role="alert">{error}</p>}
    {answer && <div className="gas-topic-answer" data-testid="gas-topic-answer" data-tool-name={answer.tool_name}
      data-explanation-source={answer.explanation_source}>
      <strong>{answer.explanation_source === "qwen_evidence_selection" ? answer.model_id : "确定性证据 · 模型未参与回答"}</strong>
      <p>{answer.answer}</p>
      <details><summary>工具回执与来源</summary><code>{answer.request_id} · {answer.tool_name}</code>
        <ul>{answer.evidence_refs.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul>
      </details>
    </div>}
  </details>;
}
