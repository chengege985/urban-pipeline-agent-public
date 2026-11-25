import { useCallback, useMemo, useState, type FormEvent } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasReadError } from "./GasDetails";
import { GasM2AnswerView } from "./GasDataM2Panel";
import type { GasM2Binding, GasM2Kind, GasM2RecordPage } from "./dataM2Contract";
import { useGasRead } from "./useGasRead";
import { GasScenarioPanel } from "./GasScenarioPanel";
import { GasTopicPanel, type useGasTopic } from "./GasTopicPanel";
import type { GasScenarioController } from "./useGasScenario";

type RecordRow = GasM2RecordPage["items"][number];
const views = [
  { label: "管段", kind: "assets", type: "pipe" },
  { label: "节点", kind: "assets", type: "node" },
  { label: "阀门", kind: "assets", type: "valve" },
  { label: "事件", kind: "events", type: undefined },
] as const;
const metrics = [
  ["资产总数", "assets"], ["管段", "pipes"], ["节点", "nodes"], ["阀门", "valves"], ["合成事件", "events"],
] as const;
const labels: Record<string, string> = {
  material: "材质", diameter_mm: "管径（mm）", pressure_class: "压力等级", asset_status: "资产状态",
  edge_id: "拓扑边", name: "名称", node_type: "节点类型", node_id: "关联节点", initial_state: "初始状态",
  cut_semantics: "阀门语义", incident_edge_ids: "关联拓扑边", event_kind: "事件类型", state: "处置状态",
  source_status: "来源状态", time_hours: "案例时刻（h）", location_status: "坐标状态", district_id: "区县映射",
  consumer_kind: "用气组类型", count: "规模", count_unit: "计量口径",
};
const displayFields = Object.keys(labels);

function text(value: unknown) {
  return typeof value === "string" || typeof value === "number" ? String(value) : "";
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function recordTitle(row: RecordRow) {
  return text(row.title) || text(row.name) || text(row.record_id).replace(/^syn-gas-(?:pipe|node|valve|event)-/, "");
}

export function useGasM2Workbench(enabled: boolean) {
  const { dataM2Api: api } = useGasCockpit();
  const read = useCallback((signal: AbortSignal) => api.m2Catalog(signal), [api]);
  const catalog = useGasRead(enabled ? "gas-m2-workspace" : null, read);
  const dataset = catalog.data?.datasets.find(entry =>
    entry.dataset_id === "OWN-K001" && entry.usage_scope === "synthetic_demo" && entry.synthetic_flag === true);
  const digest = catalog.data?.registry_sha256;
  const binding = useMemo<GasM2Binding | null>(() => dataset && digest && dataset.version
    ? { dataset_id: dataset.dataset_id, registry_sha256: digest, version: dataset.version }
    : null, [dataset, digest]);
  const key = binding ? JSON.stringify([binding.dataset_id, binding.version, binding.registry_sha256]) : null;
  return { api, catalog, dataset, binding, key };
}

type Workspace = ReturnType<typeof useGasM2Workbench>;

function useRecords(workspace: Workspace, kind: GasM2Kind, type?: "pipe" | "node" | "valve", id?: string) {
  const { api, binding, key } = workspace;
  const read = useCallback((signal: AbortSignal) => api.m2Records(binding!, kind, type, signal, id),
    [api, binding, kind, type, id]);
  return useGasRead(key ? JSON.stringify([key, kind, type, id]) : null, read);
}

function ReadState({ workspace, resource }: { workspace: Workspace; resource?: ReturnType<typeof useRecords> }) {
  const error = workspace.catalog.error ?? resource?.error;
  if (error) {
    const refresh = !resource || error.status === 409 || workspace.catalog.error;
    return <GasReadError error={error} retry={refresh ? workspace.catalog.retry : resource.retry}
      label={refresh ? "刷新数据登记" : "重试记录查询"} />;
  }
  if (workspace.catalog.status === "loading" || resource?.status === "loading") return <p role="status">正在读取 M2 数据…</p>;
  if (!workspace.binding) return <p className="gas-sprint-empty">GAS-M0 合成登记暂不可用</p>;
  return null;
}

export function GasM2Indicators({ workspace }: { workspace: Workspace }) {
  return <section className="gas-sprint-metrics" aria-label="GAS-M0 数据指标" data-testid="gas-m2-indicators">
    <dl>
      {metrics.map(([label, key]) => <div key={key}><dt>{label}</dt>
        <dd>{workspace.dataset?.record_counts[key] ?? "—"}</dd></div>)}
      <div className="gas-sprint-health"><dt>正式健康评分</dt><dd>unavailable</dd></div>
    </dl>
  </section>;
}

export function GasM2RecordsPane({ workspace }: { workspace: Workspace }) {
  const c = useGasCockpit();
  const [view, setView] = useState(0);
  const [filter, setFilter] = useState("");
  const selected = views[view];
  const records = useRecords(workspace, selected.kind, selected.type);
  const query = filter.trim().toLowerCase();
  const matches = records.data?.items.filter(row =>
    (recordTitle(row) + " " + text(row.record_id)).toLowerCase().includes(query));
  return <div className="gas-sprint-records" data-testid="gas-m2-directory">
    <div role="group" aria-label="查询资产与事件" className="gas-sprint-tabs">
      {views.map(({ label }, index) => <button type="button" key={label} aria-pressed={index === view}
        onClick={() => { setView(index); setFilter(""); }}>{label}</button>)}
    </div>
    <label className="gas-sprint-search">
      <span className="gas-sr-only">搜索记录</span><input type="search" value={filter}
        onChange={event => setFilter(event.target.value)} placeholder="按名称或编号搜索" />
    </label>
    <ReadState workspace={workspace} resource={records} />
    {records.data && <p className="gas-sprint-result-count" role="status">
      <strong>{matches?.length ?? 0}</strong> / {records.data.total} 条 · 合成登记
    </p>}
    {matches?.length === 0 && <p className="gas-sprint-empty">没有匹配的记录</p>}
    {!!matches?.length && <ul className="gas-sprint-record-list">
      {matches.map(row => {
        const id = String(row.record_id);
        const isEvent = selected.kind === "events";
        const state = text(row.state ?? row.asset_status ?? row.initial_state ?? row.node_type);
        const active = isEvent ? c.route.gasEventId === id && !c.route.gasAssetId : c.route.gasAssetId === id;
        return <li key={id}><button type="button" aria-pressed={active} data-m2-record-id={id}
          onClick={() => {
            if (isEvent) c.selectEvent(id);
            else if (c.route.gasMap === "gis") c.locateAssetsOnMap({ view: "topology", assetId: id });
            else c.selectAsset(id, false);
          }}>
          <span><strong>{recordTitle(row)}</strong><small>{id}</small></span>
          {state && <span className="gas-sprint-record-state">{state}</span>}
        </button></li>;
      })}
    </ul>}
  </div>;
}

function RelatedRecords({ row, workspace }: { row: RecordRow; workspace: Workspace }) {
  const c = useGasCockpit();
  const isNode = row.asset_type === "node";
  const topology = useRecords(isNode ? workspace : { ...workspace, key: null }, "topology");
  const connected = topology.data?.items.filter(item =>
    item.record_type === "edge" && (item.from_node_id === row.record_id || item.to_node_id === row.record_id));
  const related = strings(row.asset_ids);
  if (isNode) return <section className="gas-sprint-related" aria-label="节点连接关系">
    <h4>节点连接关系</h4>
    <ReadState workspace={workspace} resource={topology} />
    {connected?.map(edge => <div key={String(edge.record_id)} className="gas-sprint-connection">
      <span>{text(edge.from_node_id)} → {text(edge.to_node_id)}</span>
      <button type="button" onClick={() => c.selectAsset(String(edge.pipe_id), false)}>{text(edge.pipe_id)}</button>
    </div>)}
    {connected?.length === 0 && <p className="gas-sprint-empty">该节点没有关联管段</p>}
  </section>;
  if (row.event_id) return <section className="gas-sprint-related" aria-label="关联资产">
    <h4>关联资产 <small>{related.length}</small></h4>
    {related.length ? <ul>{related.map(id => <li key={id}><button type="button" onClick={() => c.selectAsset(id, true)}>{id}</button></li>)}</ul>
      : <p className="gas-sprint-empty">无关联资产</p>}
  </section>;
  return null;
}

function RecordAssistant({ workspace, kind, id }: { workspace: Workspace; kind: "assets" | "events"; id: string }) {
  const [question, setQuestion] = useState("");
  const [submitted, setSubmitted] = useState<{ question: string; attempt: number }>();
  const { api, binding } = workspace;
  const read = useCallback((signal: AbortSignal) => api.m2Query({
    binding: binding!, section: kind, record_id: id, question: submitted!.question,
  }, signal), [api, binding, kind, id, submitted]);
  const response = useGasRead(submitted && binding ? String(submitted.attempt) : null, read);
  const busy = response.status === "loading";
  function submit(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || busy) return;
    setSubmitted(prev => ({ question: question.trim(), attempt: (prev?.attempt ?? 0) + 1 }));
  }
  return <form className="gas-m2-agent gas-sprint-assistant" aria-label="当前记录的 Qwen 只读查询" onSubmit={submit}>
    <h4>Qwen 证据查询</h4>
    <p className="gas-sprint-context"><span>当前对象</span><code>{id}</code></p>
    <label className="gas-sr-only" htmlFor="gas-sprint-question">向 Qwen 提问</label>
    <input id="gas-sprint-question" maxLength={600} value={question} onChange={event => setQuestion(event.target.value)}
      placeholder="询问当前记录的属性或证据" />
    <button type="submit" disabled={busy || !question.trim()}>{busy ? "查询中…" : "查询当前记录"}</button>
    {response.error && <GasReadError error={response.error}
      retry={response.error.status === 409 ? workspace.catalog.retry : response.retry}
      label={response.error.status === 409 ? "刷新数据登记" : "重试证据查询"} />}
    {response.data && <GasM2AnswerView answer={response.data} />}
  </form>;
}

export function GasM2Inspector({ workspace, topic, drill, openQuality, openQualityWorkbench }: {
  workspace: Workspace; topic: ReturnType<typeof useGasTopic>; drill: GasScenarioController;
  openQuality(): void; openQualityWorkbench(): void;
}) {
  const c = useGasCockpit();
  const id = c.route.gasAssetId ?? c.route.gasEventId;
  return <section className="gas-sprint-inspector" data-testid="gas-m2-inspector">
    {workspace.binding && <GasTopicPanel workspace={topic} openQuality={openQualityWorkbench} />}
    {id && workspace.binding ? <RecordDetail key={JSON.stringify([workspace.key, id, c.route.gasEventId])}
      workspace={workspace} drill={drill} id={id} kind={c.route.gasAssetId ? "assets" : "events"} /> : <>
      <ReadState workspace={workspace} />
      {workspace.binding && <GasScenarioPanel controller={drill} />}
      {workspace.binding && <div className="gas-sprint-selection-empty">
        <span className="gas-sprint-empty-icon" aria-hidden="true"><i /></span>
        <strong>等待选择</strong><p>从左侧打开资产或事件，也可以直接点击合成拓扑中的对象。</p>
      </div>}
      {workspace.dataset && <section className="gas-sprint-quality" aria-label="数据源与质量">
        <h4>数据源与质量</h4>
        <dl className="gas-fields">
          <div><dt>登记</dt><dd>{workspace.dataset.dataset_id} · {workspace.dataset.version}</dd></div>
          <div><dt>字段核验</dt><dd>{workspace.dataset.schema_status}</dd></div>
          <div><dt>质量状态</dt><dd>{workspace.dataset.quality_status}</dd></div>
        </dl>
        <button type="button" onClick={openQuality}>查看来源与 {workspace.dataset.issues.length} 项异常</button>
      </section>}
    </>}
  </section>;
}

function RecordDetail({ workspace, drill, id, kind }: {
  workspace: Workspace; drill: GasScenarioController; id: string; kind: "assets" | "events";
}) {
  const c = useGasCockpit();
  const record = useRecords(workspace, kind, undefined, id);
  const row = record.data?.items.find(item => item.record_id === id);
  const evidence = strings(row?.evidence_refs);
  if (!row) return <ReadState workspace={workspace} resource={record} />;
  const title = kind === "events" ? "事件详情" : "资产详情";
  function close() {
    if (kind === "assets") c.closeAsset();
    else c.go({ gasEventId: undefined, gasSection: "events" });
  }
  return <article className="gas-sprint-record-detail" aria-label={title} data-testid="gas-m2-detail-drawer" data-record-id={id}>
    <div className="gas-sprint-detail-head">
      <div><span className="gas-sprint-kind">{title}</span><h3>{recordTitle(row)}</h3><code>{id}</code></div>
      <button type="button" aria-label={c.route.gasAssetId && c.route.gasEventId ? "返回事件详情" : "清除当前选择"} onClick={close}>
        {c.route.gasAssetId && c.route.gasEventId ? "返回事件" : "×"}
      </button>
    </div>
    <div className="gas-sprint-detail-meta"><span>合成 · {workspace.binding?.dataset_id} {workspace.binding?.version}</span>
      <button type="button" onClick={c.focusTopology}>在合成拓扑定位</button></div>
    <GasScenarioPanel controller={drill} selectedPipe={kind === "assets" && row.asset_type === "pipe" ? id : undefined} />
    <dl className="gas-fields">
      {displayFields.filter(key => Object.hasOwn(row, key)).map(key => <div key={key}><dt>{labels[key]}</dt>
        <dd>{Array.isArray(row[key]) ? strings(row[key]).join("、") : text(row[key]) || "未提供"}</dd></div>)}
    </dl>
    <RelatedRecords row={row} workspace={workspace} />
    {evidence.length > 0 && <details className="gas-m2-provenance"><summary>事件证据（{evidence.length}）</summary>
      <ul>{evidence.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul></details>}
    <RecordAssistant workspace={workspace} kind={kind} id={id} />
  </article>;
}
