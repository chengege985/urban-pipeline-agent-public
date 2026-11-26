import { CityEventAnalysis, DurationDetails, OutageDirectory, PressureComparison } from "./ExtendedDatasetAnalysis";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasTopologyView } from "./GasTopologyView";
import { projectNetwork } from "./GasLibWorkspace";
import { AssetDetails, DatasetMap, DatasetRecords } from "./PublicDatasetViews";
import { useGasRead } from "./useGasRead";
import type { GasWindowId } from "./cockpitModel";
import type { PublicDatasetOverview, ResearchRun } from "./publicDatasets";

const kinds = { main: "主管", service: "服务线", valve: "阀门", zone: "阀门分区" };

export function useCharlottesville() {
  const c = useGasCockpit();
  const active = c.activeDatasetId === "charlottesville";
  const [layer, setLayer] = useState("main");
  const [search, setSearch] = useState("");
  const [offset, setOffset] = useState(0);
  const [kind, setKind] = useState("leaks");
  const [eventOffset, setEventOffset] = useState(0);
  const [filters, setFilters] = useState({ start: "", end: "", status: "", county: "", cause: "" });
  const [eventId, setEventId] = useState("");
  useEffect(() => { if (!active) { setLayer("main"); setSearch(""); setOffset(0); setKind("leaks"); setEventOffset(0);
    setFilters({ start: "", end: "", status: "", county: "", cause: "" }); setEventId(""); } }, [active]);
  const readAssets = useCallback((signal: AbortSignal) => c.publicApi.overview("charlottesville", {
    assetKind: layer, search, offset, bbox: c.publicBbox,
  }, signal), [c.publicApi, layer, search, offset, c.publicBbox]);
  const assets = useGasRead(active && (layer !== "main" || search || offset || c.publicBbox)
    ? JSON.stringify([layer, search, offset, c.publicBbox]) : null, readAssets);
  const readEvents = useCallback((signal: AbortSignal) => c.publicApi.cvilleEvents(kind, eventOffset, filters, signal),
    [c.publicApi, kind, eventOffset, filters]);
  const events = useGasRead(active ? JSON.stringify([kind, eventOffset, filters]) : null, readEvents);
  return { layer, setLayer, search, setSearch, offset, setOffset, kind, setKind, eventOffset, setEventOffset,
    filters, setFilters, eventId, setEventId, events, assets,
    data: assets.data ?? (layer === "main" && !search && !offset && !c.publicBbox ? c.publicOverview.data : null),
    event: events.data?.items.find(({ id }) => id === eventId) };
}

function Fields({ values }: { values: [string, React.ReactNode][] }) {
  return <dl className="gas-fields">{values.map(([label, value]) =>
    <div key={label}><dt>{label}</dt><dd>{value ?? "未提供"}</dd></div>)}</dl>;
}

export function CharlottesvilleWindow({ id, w, mapMode }: {
  id: GasWindowId; w: ReturnType<typeof useCharlottesville>; mapMode: "map" | "records";
}) {
  const c = useGasCockpit();
  const event = w.event;
  const serviceOid = c.selectedPublicAssetId?.startsWith("service:") ? c.selectedPublicAssetId.slice(8) : null;
  const readLinks = useCallback((signal: AbortSignal) => c.publicApi.cvilleServiceLinks(serviceOid!, signal), [c.publicApi, serviceOid]);
  const links = useGasRead(id === "selected" || id === "services" ? serviceOid : null, readLinks);
  const linkView = links.data ? <Fields values={[
    ["主管编号", links.data.main_id], ["主管编号匹配", links.data.main_status === "unique" ? "唯一" : links.data.main_status === "ambiguous" ? "多个候选" : "未匹配"],
    ["匹配主管记录数", links.data.main_matches], ["按唯一服务线编号匹配的表计记录", links.data.meter_matches],
  ]} /> : links.error ? <p role="alert">{links.error.message}</p> : serviceOid && <p>正在核对编号关系…</p>;
  const data = w.data;
  const asset = data?.assets?.find(({ record_id }) => record_id === c.selectedPublicAssetId) ?? null;
  const assetError = w.assets.error ?? c.publicOverview.error;
  const changeLayer = (layer: string) => { w.setLayer(layer); w.setOffset(0); w.setSearch(""); c.selectPublicBbox(""); c.selectPublicAsset(null); };
  const gotoPage = (offset: number) => { w.setOffset(offset); c.selectPublicAsset(null); };
  const focusArea = () => {
    const points = (asset?.geometry?.coordinates ?? []).flat(Infinity).filter((value): value is number => typeof value === "number" && Number.isFinite(value));
    const xs = points.filter((_, index) => index % 2 === 0);
    const ys = points.filter((_, index) => index % 2 === 1);
    if (!xs.length || xs.length !== ys.length) return;
    c.selectPublicBbox([Math.min(...xs) - .003, Math.min(...ys) - .003,
      Math.max(...xs) + .003, Math.max(...ys) + .003].join(","));
    gotoPage(0);
  };
  const changeFilter = (key: keyof typeof w.filters, value: string) => {
    w.setFilters({ ...w.filters, [key]: value }); w.setEventOffset(0); w.setEventId("");
  };
  if (id === "topology" || id === "data") return <section className="public-dataset-view">
    <h3>Charlottesville 市政资产</h3>
    <form key={w.layer} className="public-toolbar" onSubmit={(e) => {
      e.preventDefault(); w.setSearch(String(new FormData(e.currentTarget).get("search") ?? "")); gotoPage(0);
    }}>
      <label>图层<select value={w.layer} onChange={(e) => changeLayer(e.target.value)}>
        {Object.entries(kinds).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <label>来源编号<input name="search" defaultValue={w.search} /></label><button>搜索</button>
    </form>
    {asset?.geometry && <button onClick={focusArea}>查询选中要素周边</button>}
    {c.publicBbox && <button onClick={() => { c.selectPublicBbox(""); gotoPage(0); }}>恢复全图层范围</button>}
    {assetError ? <p role="alert">{assetError.message} <button onClick={w.assets.error ? w.assets.retry : c.publicOverview.retry}>重试</button></p> : null}
    {data ? <>
      <div className="public-pager"><button onClick={() => gotoPage(Math.max(0, w.offset - 120))} disabled={!w.offset}>上一页</button>
        <span>本页 {data.assets?.length ?? 0} 条 / 命中 {data.assets_total_in_scope ?? "待核验"} 条</span>
        <button disabled={!data.assets_page_partial} onClick={() => gotoPage(w.offset + 120)}>下一页</button></div>
      {id === "data" || mapMode === "records" ? <DatasetRecords data={data} onSelect={c.selectPublicAsset} /> :
        <DatasetMap data={data} selectedId={c.selectedPublicAssetId} onSelect={c.selectPublicAsset} />}
    </> : !assetError && <p role="status">正在读取资产图层…</p>}
  </section>;
  if (id === "impact") return <section className="public-dataset-view">
    <div className="public-toolbar">
      <label>记录类型<select value={w.kind} onChange={(e) => { w.setKind(e.target.value); w.setEventId(""); w.setEventOffset(0); w.setFilters({ start: "", end: "", status: "", county: "", cause: "" }); }}>
        <option value="leaks">泄漏记录</option><option value="reports">泄漏报告</option></select></label>
      <label>分析开始（UTC）<input type="date" value={w.filters.start} onChange={(e) => changeFilter("start", e.target.value)} /></label>
      <label>分析结束（UTC）<input type="date" value={w.filters.end} onChange={(e) => changeFilter("end", e.target.value)} /></label>
      <label>来源原因<input value={w.filters.cause} onChange={(e) => changeFilter("cause", e.target.value)} /></label>
      {w.kind === "leaks" && <label>行政区<select value={w.filters.county} onChange={(e) => changeFilter("county", e.target.value)}>
        <option value="">全部</option><option>CHARLOTTESVILLE</option><option>ALBEMARLE</option></select></label>}
    </div>
    <CityEventAnalysis key={JSON.stringify([w.kind, w.filters])} kind={w.kind} filters={w.filters} />
    <p>真实关阀推演条件不足，等待核验管线连接、阀位、供气入口和用户接入。</p>
  </section>;
  if (id === "scenario") return <section className="public-dataset-view">
    <div className="public-toolbar public-event-filters">
      <label>记录类型<select value={w.kind} onChange={(e) => { w.setKind(e.target.value); w.setEventOffset(0); w.setEventId(""); w.setFilters({ start: "", end: "", status: "", cause: "", county: "" }); }}>
        <option value="leaks">泄漏记录</option><option value="reports">泄漏报告</option></select></label>
      <label>开始日期<input type="date" value={w.filters.start} onChange={(e) => changeFilter("start", e.target.value)} /></label>
      <label>结束日期<input type="date" value={w.filters.end} onChange={(e) => changeFilter("end", e.target.value)} /></label>
      <label>来源原因<input value={w.filters.cause} onChange={(e) => changeFilter("cause", e.target.value)} /></label>
      {w.kind === "leaks" && <>
        <label>记录状态<select value={w.filters.status} onChange={(e) => changeFilter("status", e.target.value)}>
          <option value="">全部</option>{["ACTIVE", "REPAIRED", "NO ACTION REQUIRED"].map((status) => <option key={status}>{status}</option>)}</select></label>
        <label>行政区<select value={w.filters.county} onChange={(e) => changeFilter("county", e.target.value)}>
          <option value="">全部</option><option>CHARLOTTESVILLE</option><option>ALBEMARLE</option></select></label>
      </>}
    </div>
    {w.events.error && <p role="alert">{w.events.error.message} <button onClick={w.events.retry}>重试</button></p>}
    {w.events.status === "loading" && <p role="status">读取记录…</p>}
    {w.events.data && <><p>筛选命中 {w.events.data.total.toLocaleString()} 条 · 两类来源分开统计</p>
      <div className="gas-event-list">{w.events.data.items.map((item) =>
        <button key={item.id} aria-pressed={item.id === w.eventId} onClick={() => { w.setEventId(item.id); c.openWindow("timeline"); }}>
          <b>{item.grade ?? "等级未提供"} · {item.source_record_id}</b><small>{item.timeline[0]?.time?.slice(0, 10) ?? "日期未记录"} · {item.status ?? "状态未提供"}</small>
        </button>)}</div>
      <div className="public-pager"><button disabled={!w.eventOffset} onClick={() => { w.setEventOffset(w.eventOffset - 20); w.setEventId(""); }}>上一页</button>
        <button disabled={w.eventOffset + 20 >= w.events.data.total} onClick={() => { w.setEventOffset(w.eventOffset + 20); w.setEventId(""); }}>下一页</button></div></>}
  </section>;
  if (id === "timeline") return <section className="public-dataset-view"><h3>历史处置时间线</h3>
    {event ? <><div className="gas-timeline-track">{event.timeline.map((stage) => <article className="gas-timeline-event" key={stage.field}>
      <strong>{stage.label}</strong><small>{stage.time ?? "未记录"}</small><small>{stage.field}</small>
    </article>)}</div><p>{w.events.data?.time_basis}</p><DurationDetails values={event.durations} /></> : <p>从左侧选择一条记录查看。</p>}
  </section>;
  if (id === "selected" || id === "evidence") return <section className="public-dataset-view">
    {event && <><h3>来源事件 {event.source_record_id}</h3><Fields values={[
      ["来源状态", event.status], ["等级", event.grade], ["记录原因", event.cause], ["设施类别", event.facility_type],
      ["行政区", event.area], ["关联管段", "待核实"], ["来源", <a href={event.source_url} target="_blank" rel="noreferrer">官方图层</a>],
    ]} /></>}
    {asset && <><h3>当前资产</h3><AssetDetails feature={asset} />{linkView}</>}
    {!asset && !event && <p>选择地图资产或事件记录。</p>}
  </section>;
  if (id === "services") return <section className="public-dataset-view">
    <p>真实隔离与受影响表计待验证。当前缺少完整的连接、基准阀位、供气入口和表计关系。</p>
    {linkView || <p>先选中一条服务线，核对来源编号及匹配表计记录。</p>}<button onClick={() => c.openWindow("timeline")}>查看历史处置</button></section>;
  if (id === "layers") return <section className="public-dataset-view">{Object.entries(kinds).map(([key, label]) =>
    <button key={key} aria-pressed={key === w.layer} onClick={() => { changeLayer(key); c.openWindow("topology"); }}>{label}</button>)}</section>;
  return <section className="public-dataset-view"><p>官方 GIS · 组会本地查看 · 历史事件与资产查询</p>
    <button onClick={() => c.openWindow("data")}>资产目录</button>{" "}
    <button onClick={() => c.openWindow("evidence")}>查看证据</button>{" "}<button onClick={() => c.openWindow("impact")}>历史事件分析</button></section>;
}

export function useSchutterwald() {
  const c = useGasCockpit();
  const data = c.publicOverview.data?.dataset_id === "schutterwald" ? c.publicOverview.data : null;
  const graph = useMemo(() => projectNetwork(data, "published"), [data]);
  const [draft, setDraft] = useState("baseline");
  const [hydraulic, setHydraulic] = useState(true);
  const [input, setInput] = useState<{ edge: string; hydraulic: boolean; click: number } | null>(null);
  const [history, setHistory] = useState<ResearchRun[]>([]);
  const [stage, setStage] = useState(1);
  const [building, setBuilding] = useState("");
  const version = data?.data_version;
  const read = useCallback((signal: AbortSignal) => c.publicApi.schutterwaldRun(input!.edge, version!, input!.hydraulic, signal),
    [c.publicApi, input, version]);
  const response = useGasRead(data && input && version ? JSON.stringify([version, input]) : null, read);
  useEffect(() => { setDraft("baseline"); setInput(null); setHistory([]); setStage(1); setBuilding(""); }, [version]);
  useEffect(() => {
    const result = response.data;
    if (result) setHistory((old) => old.some((row) => row.execution_id === result.execution_id) ? old : [...old, result].slice(-12));
  }, [response.data]);
  const selectDraft = (id: string) => { setInput(null); setDraft(id); setBuilding(""); };
  return { data, graph, draft, selectDraft, hydraulic, setHydraulic, response, history, stage, setStage, building, setBuilding,
    run: () => { setBuilding(""); setStage(1); setInput({ edge: draft, hydraulic, click: Date.now() }); } };
}

type ResearchWorkspace = ReturnType<typeof useSchutterwald>;
function Compare({ history }: { history: ResearchRun[] }) {
  const [a, setA] = useState("");
  const [b, setB] = useState("");
  const left = history.find(({ execution_id }) => a === execution_id);
  const right = history.find(({ execution_id }) => b === execution_id);
  const before = new Map(left?.buildings.map(({ id, reachable }) => [id, reachable]));
  const lost = right?.buildings.filter(({ id, reachable }) => before.get(id) === true && !reachable) ?? [];
  const regained = right?.buildings.filter(({ id, reachable }) => before.get(id) === false && reachable) ?? [];
  return <details><summary>对比两次执行</summary>
    {([[a, setA, "结果 A"], [b, setB, "结果 B"]] as const).map(([value, select, label]) =>
      <label key={label}>{label}<select value={value} onChange={(e) => select(e.target.value)}><option value="">选择执行</option>
        {history.map((run) => <option value={run.execution_id} key={run.execution_id}>{run.edge_id} · {run.execution_id.slice(0, 8)}</option>)}</select></label>)}
    {left && right && <Fields values={[["B 新增不可达", lost.length], ["B 恢复可达", regained.length],
      ["不可达净变化", lost.length - regained.length], ["新增不可达编号", lost.map(({ id }) => id).join("、") || "无"],
      ["恢复可达编号", regained.map(({ id }) => id).join("、") || "无"]]} />}
  </details>;
}

export function SchutterwaldWindow({ id, w, mapMode }: { id: GasWindowId; w: ResearchWorkspace; mapMode: "map" | "records" }) {
  const c = useGasCockpit();
  const data = w.data;
  if (!data) return <p role={c.publicOverview.error ? "alert" : "status"}>{c.publicOverview.error?.message ?? "正在读取模型…"}</p>;
  const result = w.response.data;
  const current = c.selectedPublicAssetId;
  const edge = data.edges?.find(({ id }) => id === current);
  const node = data.nodes?.find(({ id }) => id === current);
  const building = result?.buildings.find(({ id }) => id === w.building);
  const impacted = result?.buildings.filter(({ reachable, baseline }) => baseline && !reachable) ?? [];
  const params = result?.hydraulics;
  const pipes = data.edges?.filter(({ edge_type }) => edge_type === "pipe") ?? [];
  const select = (objectId: string) => { c.selectPublicAsset(objectId); if (objectId !== w.draft && pipes.some(({ id }) => id === objectId)) w.selectDraft(objectId); };
  if (id === "topology" && mapMode === "map") return <GasTopologyView benchmark={{
    nodes: w.graph.viewNodes, edges: w.graph.viewEdges, version: data.data_version ?? "", selectedId: current,
    focusRevision: 0, onSelect: select, coordinateLabel: "Schutterwald 模型坐标 · 建筑接入为研究设定",
    focusEdgeId: result?.edge_id, disabledEdges: result && w.stage === 1 ? [result.edge_id] : [],
    unreachableNodes: result && w.stage === 1 ? result.unreachable_nodes : [], possibleUnreachableNodes: result?.unreachable_nodes ?? [],
  }} />;
  if (id === "topology" || id === "data") return <AssetPicker data={data} select={select} />;
  if (id === "scenario") return <section className="public-dataset-view">
    <h3>配气管段故障演练</h3>
    <div className="gas-event-list">{data.presets?.map((preset) =>
      <button key={preset.id} aria-pressed={w.draft === preset.id} onClick={() => { w.selectDraft(preset.id); c.selectPublicAsset(preset.id === "baseline" ? null : preset.id); }}>{preset.label}</button>)}</div>
    <label>给定配气管段<select value={w.draft} onChange={(e) => { w.selectDraft(e.target.value); c.selectPublicAsset(e.target.value); }}>
      <option value="baseline">正常基线</option>{pipes.map(({ id }) => <option key={id}>{id}</option>)}</select></label>
    <label><input type="checkbox" checked={w.hydraulic} onChange={(e) => w.setHydraulic(e.target.checked)} />计算稳态压力与流量</label>
    <button disabled={w.response.status === "loading"} onClick={w.run}>确认并执行推演</button>
    {w.response.status === "loading" && <p role="status">正在计算所选情景…</p>}
    {w.response.error && <p role="alert">{w.response.error.message}</p>}
    {result && <p>新增不可达 {impacted.length} 个模型建筑接入点。</p>}
  </section>;
  if (id === "timeline") return <section className="public-dataset-view"><h3>演练阶段</h3>
    {result ? <div className="gas-timeline-track">{["基准", "情景计算", "恢复基准"].map((label, i) =>
      <button className="gas-timeline-event" key={label} aria-pressed={w.stage === i} onClick={() => w.setStage(i)}>
        <strong>{label}</strong><small>新增不可达 {i === 1 ? impacted.length : 0}</small></button>)}</div> :
      <p>执行后可查看阶段。</p>}示意阶段以顺序呈现。</section>;
  if (id === "layers") return <div className="gas-layer-toggles">{(["edges", "nodes", "impact", "labels"] as const).map((layer) =>
    <label key={layer}><input type="checkbox" checked={c.layers[layer]} onChange={() => c.toggleLayer(layer)} />
      {{ edges: "连接", nodes: "节点", impact: "不可达覆盖", labels: "标签" }[layer]}</label>)}</div>;
  if (id === "selected" || id === "services" || id === "impact" || id === "evidence") return <section className="public-dataset-view">
    {(edge || node) && <><h3>模型对象 {current}</h3><Fields values={edge ? [
      ["类型", edge.edge_type === "pipe" ? "配气管线" : "模型接户支线"], ["端点", edge.from_node_id + " ↔ " + edge.to_node_id],
      ...Object.entries(edge.source_properties ?? {}), ["流量 kg/s", params?.mass_flow_kg_s?.[edge.id]?.toPrecision(5)],
    ] : [["类型", node?.node_type === "building" ? "模型建筑接入节点" : node?.node_type],
      ["压力 bar", node && params?.pressure_bar?.[node.id]?.toPrecision(5)]]} /></>}
    {result && <><h3>情景影响</h3><Fields values={[
      ["基准可达", result.buildings.filter(({ baseline }) => baseline).length], ["情景可达", result.buildings.length - result.buildings.filter(({ reachable }) => !reachable).length],
      ["新增不可达", impacted.length], ["稳态", params?.status === "ok" ? "已通过收敛及质量平衡检查" : params?.reason ?? "本次未请求"],
      ["质量平衡误差 kg/s", params?.mass_balance_error_kg_s?.toExponential(3)],
      ["断连需求 kg/s", params?.excluded_model_demand_kg_s?.toPrecision(5)],
    ]} />
      <label>模型建筑<select value={w.building} onChange={(e) => { w.setBuilding(e.target.value); const item = result.buildings.find(({ id }) => id === e.target.value); if (item) c.selectPublicAsset(item.node); }}>
        <option value="">选择建筑接入点</option>{result.buildings.map(({ id, reachable }) => <option key={id} value={id}>{id} · {reachable ? "可达" : "不可达"}</option>)}</select></label>
      {building && <Fields values={[["接入节点", building.node], ["供气入口", building.path?.source_node],
        ["可达节点路径", building.path?.nodes.join(" → ") ?? "无"], ["路径管段", building.path?.pipes.join(" → ") ?? "无"]]} />}
    </>}
    {id === "impact" && <>
      <PressureComparison result={result} selectedNode={node?.id} locate={c.selectPublicAsset} />
      <Compare history={w.history} />
      <OutageDirectory version={data.data_version ?? ""} select={(id) => { select(id); c.openWindow("topology"); c.closeExtra(); }} />
    </>}
    {id === "evidence" && <><Fields values={[["数据版本", data.data_version], ["执行 ID", result?.execution_id], ["计算 ID", result?.run_id],
      ["结果 SHA-256", result?.stable_result_sha256], ["输入 SHA-256", result?.input_sha256]]} />
      {result && <a download="schutterwald-result.json" href={"data:application/json;charset=utf-8," + encodeURIComponent(JSON.stringify(result))}>下载本次计算证据</a>}</>}
    {!result && <p>等待执行。结果单位为模型建筑接入点。</p>}</section>;
  return <section className="public-dataset-view"><p>模型连接可达性与稳态压力分开呈现。原包没有阀门或真实抢修台账。</p>
    <button onClick={() => c.openWindow("data")}>模型资产目录</button>{" "}
    <button onClick={() => c.openWindow("impact")}>故障对比</button>{" "}
    <button onClick={() => c.openWindow("evidence")}>推演证据</button>
    <p><a href={data.source_refs?.[0]} target="_blank" rel="noreferrer">原始研究模型</a></p></section>;
}

function AssetPicker({ data, select }: { data: PublicDatasetOverview; select(id: string): void }) {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const rows = (data.edges ?? []).filter(({ id }) => id.includes(search.trim()));
  return <section className="public-dataset-view"><label>管段编号<input value={search} onChange={(e) => { setSearch(e.target.value); setPage(0); }} /></label>
    <div className="gas-event-list">{rows.slice(page, page + 30).map(({ id }) => <button key={id} onClick={() => select(id)}>{id}</button>)}</div>
    <div className="public-pager"><button disabled={!page} onClick={() => setPage(page - 30)}>上一页</button>
      <button disabled={page + 30 >= rows.length} onClick={() => setPage(page + 30)}>下一页</button></div></section>;
}
