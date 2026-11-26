import { useCallback, useEffect, useMemo, useState } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { useGasRead } from "./useGasRead";
import type { CityDuration, ResearchRun } from "./publicDatasets";

const timeLabels: Record<string, string> = { dispatch_arrival: "派遣至到场", report_repair: "上报至修复" };
const linkLabels: Record<string, string> = {
  candidate: "编号候选", ambiguous: "多个候选", unlinked: "未关联", unavailable: "查询暂不可用",
  confirmed: "已证实关联",
};
const labelDuration = ({ status, seconds }: CityDuration) =>
  status === "equal" ? "记录时刻相同" : status === "missing" ? "时间缺失"
    : status === "invalid" || seconds === null ? "时间异常" : (seconds / 60).toFixed(1) + " 分钟";
export function DurationDetails({ values }: { values?: Record<string, CityDuration> }) {
  return Object.keys(values ?? {}).length ? <dl className="gas-fields">{Object.entries(values!).map(([key, entry]) =>
    <div key={key}><dt>{timeLabels[key]}</dt><dd>{labelDuration(entry)}</dd></div>)}</dl> : null;
}
function Counts({ title, rows }: { title: string; rows: { label: string; count: number }[] }) {
  const max = Math.max(1, ...rows.map(({ count }) => count));
  return <section className="gas-analysis-chart"><h4>{title}</h4>
    {rows.length === 0 && <p>当前筛选没有记录。</p>}
    {rows.map(({ label, count }) => <div className="gas-analysis-bar" key={label}>
      <span title={label}>{label}</span><meter min={0} max={max} value={count} aria-label={label} /><b>{count}</b>
    </div>)}</section>;
}

export function CityEventAnalysis({ kind, filters }: { kind: string; filters: Record<string, string> }) {
  const { publicApi } = useGasCockpit();
  const [offset, setOffset] = useState(0);
  const [group, setGroup] = useState<"causes" | "areas">("causes");
  useEffect(() => setOffset(0), [kind, filters]);
  const read = useCallback((signal: AbortSignal) => publicApi.cvilleAnalysis(kind, offset, filters, signal),
    [publicApi, kind, offset, filters]);
  const state = useGasRead(JSON.stringify([kind, offset, filters]), read);
  if (state.error) return <div role="alert">{state.error.message} <button onClick={state.retry}>重试</button></div>;
  const data = state.data;
  if (!data) return <p role="status">读取事件统计与追溯资料…</p>;
  return <div className="gas-analysis-detail">
    <h3>历史事件分析</h3>
    <p>{kind === "leaks" ? "泄漏记录" : "泄漏报告"} · {data.start} 至 {data.end} UTC · {data.total} 条 · 无有效日期 {data.undated} 条</p>
    <div className="gas-analysis-grid">
      <Counts title="月度记录数" rows={data.monthly} />
      <div><label>按类别统计<select value={group} onChange={(event) => setGroup(event.target.value as typeof group)}>
        <option value="causes">登记原因</option><option value="areas">行政区</option></select></label>
        <Counts title={group === "causes" ? "按登记原因分组" : "按原始行政区分组"} rows={data[group]} /></div>
    </div>
    {Object.keys(data.durations).length > 0 && <section><h4>耗时记录质量</h4>{Object.entries(data.durations).map(([metric, counts]) =>
      <p key={metric}>{timeLabels[metric]} · 有效 {(counts.ok ?? 0) + (counts.equal ?? 0)}（同刻 {counts.equal ?? 0}）
        · 缺失 {counts.missing ?? 0} · 异常 {counts.invalid ?? 0}</p>)}</section>}
    <h4>事件与资产追溯</h4>
    <p>按来源工单号核查候选。唯一命中仍需运营方核验。</p>
    <div className="public-table-wrap"><table><thead><tr><th>事件</th><th>UTC 日期</th><th>登记原因</th><th>行政区</th><th>关联依据与耗时</th></tr></thead>
      <tbody>{data.items.map((row) => <tr key={row.id}><td>{row.id}</td><td>{row.time.slice(0, 10)}</td>
        <td>{row.cause || "未记录"}</td><td>{row.area || "未记录"}</td><td><details><summary>{linkLabels[row.link]}</summary>
          {row.matches && <p>工单号匹配 · 主管 {row.matches.main} 条 · 服务线 {row.matches.service} 条</p>}
          <DurationDetails values={row.durations} />
        </details></td></tr>)}</tbody></table></div>
    <div className="public-pager"><button disabled={!offset} onClick={() => setOffset(offset - data.limit)}>上一页</button>
      <span>第 {offset / data.limit + 1} 页</span>
      <button disabled={offset + data.limit >= data.total} onClick={() => setOffset(offset + data.limit)}>下一页</button></div>
    <p><a href={data.source_url} target="_blank" rel="noreferrer">市政府原始图层</a> · 获取于 {data.fetched_at}</p>
  </div>;
}

export function OutageDirectory({ version, select }: { version: string; select: (id: string) => void }) {
  const { publicApi } = useGasCockpit();
  const read = useCallback((signal: AbortSignal) => publicApi.schutterwaldOutages(version, signal), [publicApi, version]);
  const state = useGasRead(version || null, read);
  const [search, setSearch] = useState("");
  const [impact, setImpact] = useState("all");
  const [offset, setOffset] = useState(0);
  if (state.error) return <p role="alert">{state.error.message} <button onClick={state.retry}>重试</button></p>;
  if (!state.data) return <p role="status">读取单管段故障清单…</p>;
  const rows = state.data.items.filter((row) => row.id.includes(search.trim().toLowerCase()) &&
    (impact === "all" || (impact === "positive" ? row.impact > 0 : row.impact === 0)));
  return <section className="gas-analysis-detail"><h3>单管段故障影响清单</h3>
    <p>模型建筑接入点 · {rows.length} / {state.data.items.length} 条配气管线</p>
    <div className="public-toolbar"><label>查找管段<input value={search} onChange={(event) => { setSearch(event.target.value); setOffset(0); }} /></label>
      <label>故障影响<select value={impact} onChange={(event) => { setImpact(event.target.value); setOffset(0); }}>
        <option value="all">全部</option><option value="zero">零影响</option><option value="positive">有影响</option></select></label></div>
    <div className="public-table-wrap"><table><thead><tr><th>管段</th><th>新增不可达接入点</th><th>操作</th></tr></thead>
      <tbody>{rows.slice(offset, offset + 20).map(({ id, impact: count }) => <tr key={id}>
        <td>{id}</td><td>{count}</td><td><button onClick={() => select(id)}>定位并选为情景</button></td></tr>)}</tbody></table></div>
    <div className="public-pager"><button disabled={!offset} onClick={() => setOffset(offset - 20)}>上一页</button>
      <button disabled={offset + 20 >= rows.length} onClick={() => setOffset(offset + 20)}>下一页</button></div>
  </section>;
}

export function PressureComparison({ result, selectedNode, locate }: {
  result?: ResearchRun | null; selectedNode?: string; locate(node: string): void;
}) {
  const data = result?.hydraulics;
  const base = data?.baseline_pressure_bar;
  const after = data?.pressure_bar;
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const nodes = useMemo(() => Object.keys(base ?? {}).sort((a, b) => Number(a) - Number(b)), [base]);
  if (!base || !after || data?.status !== "ok") return <section><h3>故障前后压力</h3>
    <p>{data?.reason ?? "执行时勾选稳态计算，两个工况通过后显示对比。"}</p></section>;
  const comparable = nodes.filter((id) => id in after);
  const missing = nodes.length - comparable.length;
  const delta = comparable.map((id) => after[id] - base[id]);
  const rows = [
    { label: "下降 ≥ 0.01 bar", count: delta.filter((d) => d <= -.01).length },
    { label: "变化小于 0.01 bar", count: delta.filter((d) => Math.abs(d) < .01).length },
    { label: "上升 ≥ 0.01 bar", count: delta.filter((d) => d >= .01).length },
    { label: "故障后无结果", count: missing },
  ];
  const available = nodes.filter((id) => id.includes(query.trim()) &&
    (filter === "all" || (filter === "missing" ? !(id in after) : id in after)));
  const selected = selectedNode && selectedNode in base ? selectedNode : undefined;
  const post = selected ? after[selected] : undefined;
  const localRows = selected ? [{ label: "基准", value: base[selected] }, { label: "故障后", value: post }] : [];
  return <section className="gas-analysis-detail"><h3>故障前后压力</h3>
    <p>{comparable.length} 个节点可比较 · {missing} 个节点故障后无结果 · 原包固定负荷 · bar</p>
    <Counts title="全网压力变化节点数（0.01 bar 为展示分组）" rows={rows} />
    <div className="public-toolbar"><label>搜索节点<input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
      <label>节点状态<select value={filter} onChange={(event) => setFilter(event.target.value)}>
        <option value="all">全部</option><option value="available">两侧有结果</option><option value="missing">故障后无结果</option></select></label>
      <label>节点压力详情<select value={selected && available.includes(selected) ? selected : ""} onChange={(event) => {
        if (event.target.value) locate(event.target.value);
      }}><option value="">选择节点</option>{available.map((id) => <option key={id}>{id}</option>)}</select></label></div>
    {selected && <section className="gas-analysis-chart"><h4>节点 {selected}（bar）</h4>
      {localRows.map(({ label, value }) => <div className="gas-analysis-bar" key={label}><span>{label}</span>
        {value === undefined ? <span>无结果</span> : <><meter aria-label={label} min={0} max={Math.max(1, base[selected], post ?? 0)} value={value} />
          <b>{value.toFixed(5)}</b></>}</div>)}
      {post !== undefined && <p>变化 {(post - base[selected]).toFixed(5)} bar</p>}</section>}
  </section>;
}
