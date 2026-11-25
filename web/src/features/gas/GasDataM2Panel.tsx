import { useEffect, useRef, useState, type FormEvent } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import type { GasEngineeringApi } from "./engineeringApi";
import type {
  GasM2AgentRequest, GasM2AgentResult, GasM2Binding, GasM2Catalog, GasM2Dataset, GasM2Details, GasM2Kind, GasM2RecordPage,
} from "./dataM2Contract";
import "./gas-data-m2.css";

const SCOPES: Record<GasM2Dataset["usage_scope"], string> = {
  catalog_only: "目录候选", synthetic_demo: "DEMO 合成数据", research_benchmark: "研究基准",
  research_gis: "欧洲历史 GIS", annual_context: "重庆年度统计", administrative_reference: "行政区划服务",
};
const VIEWS = [
  { label: "管段", kind: "assets", type: "pipe" },
  { label: "节点", kind: "assets", type: "node" },
  { label: "阀门", kind: "assets", type: "valve" },
  { label: "事件", kind: "events", type: undefined },
  { label: "拓扑", kind: "topology", type: undefined },
  { label: "观测", kind: "observations", type: undefined },
] as const;
const show = (value: unknown) => value == null ? "未提供" : typeof value === "object" ? JSON.stringify(value) : String(value);
const errorText = (error: unknown) => error instanceof Error ? error.message : "候选接口暂不可用。";
type M2Api = Pick<GasEngineeringApi, "m2Catalog" | "m2Details" | "m2Records" | "m2Query">;
type Selected = { section: "assets" | "events"; id: string };

function Records({ api, binding, onSelected }: { api: M2Api; binding: GasM2Binding; onSelected(value: Selected | null): void }) {
  const [view, setView] = useState<number | null>(null);
  const [records, setRecords] = useState<GasM2RecordPage>();
  const [recordId, setRecordId] = useState("");
  const [state, setState] = useState("");
  const selection = view === null ? null : VIEWS[view];

  useEffect(() => {
    if (!selection) return;
    const request = new AbortController();
    setState("读取中");
    void api.m2Records(binding, selection.kind, selection.type, request.signal).then(value => {
      if (request.signal.aborted) return;
      setRecords(value);
      setState(value.total ? "" : "该视图没有记录。");
    }).catch(error => {
      if (!request.signal.aborted) setState(errorText(error));
    });
    return () => request.abort();
  }, [api, binding, selection]);

  function choose(index: number) {
    if (index === view) return;
    setView(index);
    setRecords(undefined);
    setRecordId("");
    onSelected(null);
  }

  const current = records?.items.find(row => row.record_id === recordId);
  return <section aria-label="GAS-M0 只读记录">
    <h4>合成记录</h4>
    <div className="gas-cuv-links">
      {VIEWS.map(({ label }, index) => <button type="button" key={label} aria-pressed={view === index}
        onClick={() => choose(index)}>{label}</button>)}
    </div>
    {state && <p role="status">{state}</p>}
    {records && records.items.length > 0 && <>
      <label>记录（{records.total}）
        <select aria-label="选择只读记录" value={recordId} onChange={event => {
          const id = event.target.value;
          setRecordId(id);
          const kind: GasM2Kind = records.kind;
          onSelected(id && (kind === "assets" || kind === "events") ? { section: kind, id } : null);
        }}>
          <option value="">选择记录</option>
          {records.items.map(row => <option key={String(row.record_id)} value={String(row.record_id)}>{String(row.record_id)}</option>)}
        </select>
      </label>
      {current && <dl className="gas-fields" data-testid="gas-m2-record">
        {Object.entries(current).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{show(value)}</dd></div>)}
      </dl>}
    </>}
  </section>;
}

function DatasetDetail({ api, summary, sha }: { api: M2Api; summary: GasM2Dataset; sha: string }) {
  const [detail, setDetail] = useState<GasM2Details>();
  const [status, setStatus] = useState("读取详细合同…");
  const [selected, setSelected] = useState<Selected | null>(null);
  const [section, setSection] = useState<GasM2AgentRequest["section"]>("quality");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<GasM2AgentResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queryInFlight = useRef<AbortController | null>(null);

  useEffect(() => {
    const add = new AbortController();
    const binding = { dataset_id: summary.dataset_id, version: summary.version, registry_sha256: sha };
    void api.m2Details(binding, add.signal).then(value => {
      if (!add.signal.aborted) { setDetail(value); setStatus(""); }
    }).catch(caught => { if (!add.signal.aborted) setStatus(errorText(caught)); });
    return () => add.abort();
  }, [api, sha, summary]);

  useEffect(() => () => queryInFlight.current?.abort(), []);

  function clearAnswer() {
    queryInFlight.current?.abort();
    setBusy(false);
    setAnswer(undefined);
    setError("");
  }

  async function explain(event: FormEvent) {
    event.preventDefault();
    if (!detail || !question.trim() || busy) return;
    const recordId = section === "assets" || section === "events" ? selected?.id ?? null : null;
    const request = new AbortController();
    queryInFlight.current = request;
    setBusy(true);
    setError("");
    setAnswer(undefined);
    try {
      const value = await api.m2Query({ binding: detail.binding, section, record_id: recordId, question: question.trim() }, request.signal);
      if (!request.signal.aborted) setAnswer(value);
    } catch (caught) {
      if (!request.signal.aborted) setError(errorText(caught));
    } finally {
      if (!request.signal.aborted) setBusy(false);
    }
  }

  if (!detail) return <p role="status">{status}</p>;
  const dataset = detail.dataset;
  return <div className="gas-m2-detail" data-testid="gas-m2-detail">
    <div className="gas-m2-heading">
      <div><span className="gas-mode-pill">{SCOPES[dataset.usage_scope]}</span><h3>{dataset.title}</h3></div>
      <code>{dataset.dataset_id} · {dataset.version ?? "版本待核"}</code>
    </div>
    <dl className="gas-fields">
      <div><dt>数据范围</dt><dd>{dataset.geo_scope}</dd></div>
      <div><dt>坐标口径</dt><dd>{dataset.coordinate_system ?? "未提供"}</dd></div>
      <div><dt>统计或观测时间</dt><dd>{dataset.statistics_time ?? "未提供"}</dd></div>
      <div><dt>许可</dt><dd>{dataset.license_status}</dd></div>
      <div><dt>字段 / 质量 / 接入</dt><dd>{dataset.schema_status} / {dataset.quality_status} / {dataset.ingest_status}</dd></div>
      <div><dt>健康评价、运行风险、物理预测</dt><dd>unavailable</dd></div>
    </dl>
    <section aria-label="字段与质量">
      <h4>记录与质量</h4>
      {Object.keys(dataset.record_counts).length > 0 && <dl className="gas-fields">
        {Object.entries(dataset.record_counts).map(([key, count]) => <div key={key}><dt>{key}</dt><dd>{count}</dd></div>)}
      </dl>}
      {dataset.facts.length > 0 && <ul>{dataset.facts.map(fact => <li key={fact}>{fact}</li>)}</ul>}
      {dataset.issues.length > 0 && <ul className="gas-m2-issues" data-testid="gas-m2-issues">
        {dataset.issues.map(issue => <li key={issue.code}><code>{issue.code}</code> {issue.detail}
          {issue.count !== null && <b>{issue.count}</b>}</li>)}
      </ul>}
    </section>
    <details className="gas-m2-provenance">
      <summary>来源、文件指纹与字段映射</summary>
      {dataset.source_uri && <a href={dataset.source_uri} target="_blank" rel="noreferrer">打开数据来源</a>}
      {dataset.sha256 && <p>原件摘要 <code>{dataset.sha256}</code></p>}
      {dataset.normalized_sha256 && <p>派生摘要 <code>{dataset.normalized_sha256}</code></p>}
      {dataset.quarantine_sha256 && <p>隔离摘要 <code>{dataset.quarantine_sha256}</code></p>}
      {dataset.sources.length > 0 && <ul>{dataset.sources.map(source => <li key={source.name}>{source.name} <code>{source.sha256}</code></li>)}</ul>}
      {dataset.field_mappings.length > 0 && <ul>{dataset.field_mappings.map(field => <li key={field.raw_field}>
        {field.raw_field} → {field.normalized_field}{field.unit ? " · " + field.unit : ""}</li>)}</ul>}
      {dataset.transformations.map(change => <p key={change}>{change}</p>)}
    </details>
    {dataset.usage_scope === "synthetic_demo" && <Records api={api} binding={detail.binding} onSelected={(row) => {
      setSelected(row);
      setSection(row?.section ?? "quality");
      clearAnswer();
    }} />}
    <form className="gas-m2-agent" aria-label="Qwen 数据证据" onSubmit={explain}>
      <h4>Qwen 数据证据</h4>
      <label>查询证据<select aria-label="查询证据" value={section} disabled={busy} onChange={event => {
        const value = event.target.value;
        if (value === "quality" || value === "provenance" || value === selected?.section) { setSection(value); clearAnswer(); }
      }}>
        <option value="quality">质量、异常和缺项</option><option value="provenance">来源与许可</option>
        {selected && <option value={selected.section}>{selected.id}</option>}
      </select></label>
      <label>问题<input aria-label="数据证据问题" maxLength={600} value={question} disabled={busy} onChange={event => setQuestion(event.target.value)}
        placeholder="例如：当前有哪些缺项？" /></label>
      <button type="submit" className="gas-inline-action" disabled={!question.trim() || busy}>{busy ? "查询中…" : "查询证据"}</button>
      {error && <p role="alert">{error}</p>}
      {answer && <GasM2AnswerView answer={answer} />}
    </form>
  </div>;
}

export function GasM2AnswerView({ answer }: { answer: GasM2AgentResult }) {
  return <div className="gas-m2-answer" data-testid="gas-m2-answer" role="status">
        <b>{answer.explanation_source === "qwen_evidence_selection" ? "Qwen 已选择证据" : "后端只读证据 · Qwen 暂不可用"}</b>
        <p>{answer.answer}</p>
        <details><summary>调用证据</summary><code>{answer.request_id} · {answer.tool_name}</code>
          <ul>{answer.evidence_refs.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul>
        </details>
      </div>;
}

export function GasDataM2Panel() {
  const { dataM2Api: api } = useGasCockpit();
  const [catalog, setCatalog] = useState<GasM2Catalog>();
  const [selected, setSelected] = useState("OWN-K001");
  const [status, setStatus] = useState("读取数据登记…");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const request = new AbortController();
    void api.m2Catalog(request.signal).then(value => {
      if (!request.signal.aborted) { setCatalog(value); setStatus(""); }
    }).catch(error => { if (!request.signal.aborted) setStatus(errorText(error)); });
    return () => request.abort();
  }, [api, reload]);

  const summary = catalog?.datasets.find(dataset => dataset.dataset_id === selected);
  return <section className="gas-m2-panel" aria-label="数据登记与质量" data-testid="gas-m2-panel">
    <div className="gas-m2-picker">
      <label>数据集<select aria-label="选择数据登记数据集" value={selected} disabled={!catalog} onChange={event => setSelected(event.target.value)}>
        {catalog?.datasets.map(entry => <option key={entry.dataset_id} value={entry.dataset_id}>{SCOPES[entry.usage_scope]} · {entry.title}</option>)}
      </select></label>
      <button type="button" className="gas-inline-action" onClick={() => { setStatus("读取数据登记…"); setCatalog(undefined); setReload(x => x + 1); }}>刷新登记</button>
    </div>
    {status && <p role="status">{status}</p>}
    {catalog && summary && <DatasetDetail key={catalog.registry_sha256 + summary.dataset_id}
      summary={summary} sha={catalog.registry_sha256} api={api} />}
  </section>;
}
