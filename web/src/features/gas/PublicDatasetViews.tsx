import { useCallback, useEffect, useRef, useState } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { NgnWorkspace } from "./NgnWorkspace";
import type { GasWindowId } from "./cockpitModel";
import type { PublicDatasetFeature, PublicDatasetOverview } from "./publicDatasets";
import { useGasRead } from "./useGasRead";

const DATASET_LABELS: Record<string, string> = {
  ngn_distribution_example: "NGN 配气样例",
  sicily_transmission: "西西里高压输气研究样本",
  charlottesville: "Charlottesville",
  schutterwald: "Schutterwald",
  aer_alberta: "AER 阿尔伯塔公开管线/事件",
  sgn_eastbourne: "SGN Eastbourne 官方配气图层",
  ngn_the_north: "NGN · The North",
  gaslib_582: "GasLib-582 研究基准",
  merrimack_2018: "Merrimack Valley NTSB事故资料",
  gas_m0_synthetic: "GAS-M0 合成演示",
};

function countLabel(value: number | null | undefined) {
  return value === undefined || value === null ? "未提供" : value.toLocaleString("en-US");
}
function featureId(feature: PublicDatasetFeature) {
  return String(feature.record_id ?? feature.pipe_id ?? "");
}

export function PublicDatasetSummary({ data, datasetId, loading, error }: {
  data: PublicDatasetOverview | null; datasetId?: string; loading: boolean; error?: string | null;
}) {
  const facts: [string, string, string][] = data?.dataset_id === "ngn_distribution_example" ? [
    ["模型管段", countLabel(data.counts?.pipes), "Pipe Layer"],
    ["模型节点", countLabel(data.counts?.nodes), "Junction Layer"],
    ["气源边界", countLabel(data.counts?.sources), "External Grid Layer"],
    ["模型负荷点", countLabel(data.counts?.sinks), "Sink Layer"],
    ["独立稳态工况", countLabel(data.counts?.periods), "time_series.csv"],
    ["数据属性", "教学样例", "Pipeline Engineer"],
  ] : data?.dataset_id === "sicily_transmission" ? [
    ["模型节点", countLabel(data.counts?.nodes), "sicily-nodes"],
    ["管线连接", countLabel(data.counts?.connections), "sicily-connections"],
    ["代表日", countLabel(new Set(data.cases?.map((item) => item.id.slice(0, 8))).size), "sicily-days"],
    ["分时工况", countLabel(data.counts?.available_hours), "sicily-hours"],
    ["已发布结果", countLabel(data.cases?.filter((item) => item.published).length), "sicily-published"],
    ["数据模式", "输气研究样本", "sicily-mode"],
  ] : data?.dataset_id === "aer_alberta" ? [
    ["管线记录", countLabel(data.counts?.pipe_records), "aer-pipes"],
    ["Release报告记录", countLabel(data.counts?.release_report_records), "aer-events"],
    ["唯一关联记录", countLabel(data.counts?.uniquely_linked_release_records), "aer-linked"],
    ["当前分区管线", countLabel(data.assets_total_in_scope), "aer-scope"],
    ["阀门拓扑", "未提供", "aer-valves"],
    ["用户接入", "未提供", "aer-customers"],
  ] : data?.dataset_id === "ngn_the_north" ? [
    ["区域资产", countLabel(data.counts?.pipe_records), "ngn-assets"],
    ["可绘制记录", countLabel(data.counts?.drawable_records), "ngn-drawable"],
    ["筛选命中", countLabel(data.counts?.filtered_records), "ngn-filtered"],
    ["当前显示", countLabel(data.counts?.assets_in_current_page), "ngn-page"],
    ["地图覆盖", "The North", "ngn-scope"],
    ["历史范围", "NGN 全网", "ngn-history"],
  ] : data?.dataset_id === "sgn_eastbourne" ? [
    ["当前页要素", countLabel(data.counts?.asset_features_current_page), "sgn-page"],
    ["服务端总数", "未核实", "sgn-total"],
    ["阀门拓扑", "未提供", "sgn-valves"],
    ["用户接入", "未提供", "sgn-customers"],
    ["压力工况", "未提供", "sgn-pressure"],
    ["事件记录", "未提供", "sgn-events"],
  ] : data?.dataset_id === "gaslib_582" ? [
    ["拓扑节点", countLabel(data.counts?.nodes), "gaslib-nodes"],
    ["网络连接", countLabel(data.counts?.connections), "gaslib-edges"],
    ["求解状态", data.solver_status === "available" ? "可用" : "未配置", "gaslib-solver"],
    ["连通性推演", "可用", "gaslib-connectivity"],
    ["在运资产", "研究基准", "gaslib-assets"],
    ["稳态结果", "未计算", "gaslib-steady"],
  ] : data?.dataset_id === "schutterwald" ? [
    ["配气管线", countLabel(data.counts?.distribution_pipes), "sw-pipes"],
    ["模型接户支线", countLabel(data.counts?.service_connections), "sw-laterals"],
    ["模型建筑接入点", countLabel(data.counts?.model_buildings), "sw-buildings"],
    ["模型供气入口", countLabel(data.counts?.supply_nodes), "sw-source"],
    ["阀门数据", "未提供", "sw-valves"],
    ["资料类型", "配气研究模型", "sw-mode"],
  ] : data?.dataset_id === "charlottesville" ? [
    ["主管记录", countLabel(data.assets_total_in_scope), "cville-mains"],
    ["当前展示", countLabel(data.assets?.length), "cville-page"],
    ["资产来源", "市政 GIS", "cville-source"],
    ["事件类型", "历史泄漏", "cville-events"],
    ["隔离推演", "条件不足", "cville-isolation"],
    ["数据状态", "在线读取", "cville-status"],
  ] : data?.dataset_id === "merrimack_2018" ? [
    ["事故日期", data.event_date ?? "未提供", "merrimack-date"],
    ["报告受影响客户", countLabel(data.facts?.find((item) => item.label === "受影响客户")?.value as number | undefined), "merrimack-customers"],
    ["受损建筑", countLabel(data.facts?.find((item) => item.label === "受损建筑")?.value as number | undefined), "merrimack-buildings"],
    ["事故地点", "3个社区", "merrimack-locations"],
    ["可运行拓扑", "无", "merrimack-topology"],
    ["现场状态", "历史案例", "merrimack-status"],
  ] : [
    ["数据集", data?.title ?? DATASET_LABELS[datasetId ?? ""] ?? "公开来源", "public-title"],
    ["数据版本", data?.data_version ?? "待核验", "public-version"],
    ["运行状态", error ? "读取失败" : loading ? "加载中" : data?.status ?? "待数据", "public-status"],
    ["算法结论", "不自动生成", "public-algorithm"],
    ["资产关联", "按来源合同", "public-linkage"],
    ["数据模式", data?.data_mode ?? "—", "public-mode"],
  ];
  return <section className="gas-record-summary public-gas-record-summary" aria-label="当前数据集摘要">
    <dl>{facts.map(([label, value, testId]) => <div key={testId}><dt>{label}</dt><dd data-testid={`public-${testId}`}>{value}</dd></div>)}</dl>
    <details className="gas-count-explanation"><summary>数据来源与口径</summary>
      <p>{data?.title ?? DATASET_LABELS[datasetId ?? ""] ?? "公开来源"} · {data?.data_version ?? "版本待核验"}。数据按所选来源展示。
        {data?.source_refs?.length ? ` 来源：${data.source_refs.join("；")}` : ""}</p>
    </details>
  </section>;
}

type Point = [number, number];
function lineParts(feature: PublicDatasetFeature): Point[][] {
  const geom = feature.geometry;
  if (!geom?.coordinates) return [];
  const coords = geom.coordinates as unknown;
  if (geom.type === "LineString" && Array.isArray(coords)) return [coords as Point[]];
  if (geom.type === "Point" && Array.isArray(coords)) return [[coords as Point]];
  if ((geom.type === "MultiLineString" || geom.type === "Polygon") && Array.isArray(coords)) return coords as Point[][];
  return [];
}

export function DatasetMap({ data, selectedId, onSelect }: {
  data: PublicDatasetOverview; selectedId: string | null; onSelect(id: string): void;
}) {
  const [focus, setFocus] = useState(true);
  const segments = (data.assets ?? []).flatMap((feature) =>
    lineParts(feature).map((points) => ({ id: featureId(feature) || "unknown", points })));
  const focusPoints = focus && selectedId ? segments.filter((segment) => segment.id === selectedId).flatMap((segment) => segment.points) : [];
  const allPoints = (focusPoints.length ? focusPoints : segments.flatMap((segment) => segment.points))
    .filter((point) => Number.isFinite(point[0]) && Number.isFinite(point[1]));
  if (!allPoints.length) return <div className="public-map-empty" role="status">
    <strong>{data.dataset_id === "merrimack_2018" ? "该资料不是可绘制的管网图层" : "当前数据没有可展示的线几何"}</strong>
    <p>{data.dataset_id === "merrimack_2018" ? "NTSB资料提供事故与响应证据，不包含可以核验的完整网络坐标。请在事故资料面板查看原始报告。" : "未生成示意坐标；请检查数据源文件与几何字段。"}</p>
  </div>;
  const xs = allPoints.map(([x]) => x), ys = allPoints.map(([, y]) => y);
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
  const width = 1000, height = 640, pad = 30;
  const spanX = Math.max(maxX - minX, 1e-9), spanY = Math.max(maxY - minY, 1e-9);
  const scale = Math.min((width - 2 * pad) / spanX, (height - 2 * pad) / spanY);
  const offsetX = (width - spanX * scale) / 2, offsetY = (height - spanY * scale) / 2;
  const project = ([x, y]: Point): Point => [offsetX + (x - minX) * scale, height - offsetY - (y - minY) * scale];
  return <div className="public-network-map-wrap">
    {selectedId ? <div className="gas-map-mode"><button type="button" aria-pressed={focus} onClick={() => setFocus(true)}>聚焦选中管段</button>
      <button type="button" aria-pressed={!focus} onClick={() => setFocus(false)}>适配当前图层</button></div> : null}
    <svg className="public-network-map" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${data.title}几何预览`}>
      <rect width={width} height={height} rx="14" className="public-map-background" />
      {segments.map(({ id, points }, index) => points.length === 1
        ? <circle key={`${id}-${index}`} cx={project(points[0])[0]} cy={project(points[0])[1]} r={5} role="button" tabIndex={0}
            aria-label={id} className={`public-map-line${id === selectedId ? " is-selected" : ""}`}
            onClick={() => onSelect(id)} onKeyDown={(event) => { if (event.key === "Enter") onSelect(id); }} />
        : <polyline key={`${id}-${index}`} points={points.map((point) => project(point).join(",")).join(" ")}
        className={`public-map-line${id === selectedId ? " is-selected" : ""}`} tabIndex={0} role="button"
        aria-label={`选择记录 ${id}`} onClick={() => { setFocus(true); onSelect(id); }} onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(id); }
        }}><title>{id} · 点击查看当前返回页中的来源属性</title></polyline>)}

    </svg>
    <p className="public-map-caption">坐标参考系：{data.source_crs ?? "来源未说明"}。 {data.geometry_note ?? "来源坐标展示；不表示管线已具备端点拓扑或可进行关阀推演。"}</p>
  </div>;
}

export function AssetDetails({ feature }: { feature: PublicDatasetFeature | null }) {
  if (!feature) return <div className="public-empty-state"><strong>请选择一条资产记录</strong><p>地图和列表选择会同步到此处；未加载到的记录不会以邻近关系代替关联。</p></div>;
  const props = feature.source_properties ?? {};
  const preferred = ["pipe_id", "record_id", "field_centre", "segment_status", "pipe_material", "pipe_type", "primary_substance", "outside_diameter_mm", "licence_number", "company_name", "OBJECTID", "MATERIAL", "DIAMETER", "PRESSURE"];
  const entries = [...new Set([...preferred, ...Object.keys(props)])].flatMap((key) => {
    const value = key === "pipe_id" ? feature.pipe_id : key === "record_id" ? feature.record_id : props[key];
    return value === undefined || value === null || value === "" ? [] : [[key, String(value)] as [string, string]];
  });
  return <><div className="public-selected-id">{feature.pipe_id ?? feature.record_id ?? "来源记录"}</div>
    <dl className="public-detail-grid">{entries.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <p className="public-source-ref">来源记录：{JSON.stringify(feature.source_ref ?? {})}</p></>;
}

function AERRecords({ data, onSelect, compact = false }: { data: PublicDatasetOverview; onSelect(id: string, keepEvent?: boolean): void; compact?: boolean }) {
  const c = useGasCockpit();
  const [query, setQuery] = useState("");
  const [linkStatus, setLinkStatus] = useState("linked_unique_gas_segment");
  const [page, setPage] = useState(0);
  const [locatingId, setLocatingId] = useState<string | null>(null);
  const [locateNotice, setLocateNotice] = useState<string | null>(null);
  const selectedPipeId = data.assets?.find((asset) => featureId(asset) === c.selectedPublicAssetId)?.pipe_id ?? undefined;
  const readEvents = useCallback((signal: AbortSignal) => c.publicApi.aerEvents({
    pipeId: selectedPipeId ?? undefined, search: query, linkStatus, offset: page * 30, limit: 30,
  }, signal), [c.publicApi, selectedPipeId, query, linkStatus, page]);
  const eventKey = `aer-events:${data.data_version ?? "unknown"}:${selectedPipeId ?? "all"}:${linkStatus}:${query.trim()}:${page}`;
  const eventRead = useGasRead(eventKey, readEvents);
  const events = eventRead.data?.items ?? [];
  const total = eventRead.data?.total;
  const lookup = useRef<AbortController | null>(null);
  useEffect(() => () => lookup.current?.abort(), []);
  const locateAsset = async (row: Record<string, unknown>) => {
    const pipeId = String(row.linked_pipe_id ?? "");
    if (!pipeId || row.link_status !== "linked_unique_gas_segment") return;
    lookup.current?.abort();
    const controller = new AbortController();
    lookup.current = controller;
    setLocateNotice(null);
    setLocatingId(pipeId);
    try {
      const response = await c.publicApi.aerAssets({ search: "=" + pipeId, limit: 2 }, controller.signal);
      if (controller.signal.aborted) return;
      if (response.total !== 1 || response.items[0]?.pipe_id !== pipeId) {
        setLocateNotice("未找到唯一的来源资产记录。");
        return;
      }
      c.selectPublicEvent(row);
      c.selectPublicScope("__all__");
      c.selectPublicSearch("=" + pipeId);
      onSelect(featureId(response.items[0]), true);
    } catch (error) {
      if (!controller.signal.aborted) setLocateNotice(error instanceof Error ? error.message : "资产定位请求失败。");
    } finally {
      if (!controller.signal.aborted) setLocatingId(null);
    }
  };
  return <>
    <div className={compact ? "public-toolbar public-event-filters" : "public-toolbar"}><label>查询监管记录<input value={query} onChange={(event) => { setQuery(event.target.value); setPage(0); }} placeholder="事件编号、事件类型或管段编号" /></label>
      <label>资产关联<select value={linkStatus} onChange={(event) => { setLinkStatus(event.target.value); setPage(0); }}>
        <option value="all">全部关联状态</option><option value="linked_unique_gas_segment">已唯一关联燃气管段</option>
        <option value="linked_non_gas_segment">关联到非燃气管段</option><option value="unmatched_segment_id">编号未匹配</option>
        <option value="no_segment_id">没有管段编号</option><option value="ambiguous_segment_id">关联有歧义</option>
      </select></label><span>{selectedPipeId ? `管段 ${selectedPipeId} · ` : "全域记录 · "}{total === undefined ? "统计中" : `${countLabel(total)}条`}</span></div>
    {eventRead.status === "failed" ? <div className="public-empty-state is-error" role="alert"><strong>监管记录读取失败</strong><p>{eventRead.error?.message}</p><button type="button" onClick={eventRead.retry}>重试</button></div> : null}
    {compact ? <div className="gas-event-list">
      {events.map((row) => {
        const id = String(row.incident_id ?? "");
        const pipeId = String(row.linked_pipe_id ?? "");
        return <article className="public-event-card" key={id}>
          <button type="button" aria-pressed={c.selectedPublicEvent?.incident_id === row.incident_id}
            onClick={() => c.selectPublicEvent(row)}>
            <b>{String(row.incident_type ?? "监管记录")}</b>
            <small>{id} · {String(row.incident_date ?? "日期未提供")}</small>
            <span>{pipeId ? "来源管段 " + pipeId : "无关联管段"}</span>
          </button>
          {pipeId && row.link_status === "linked_unique_gas_segment" ? <button type="button" disabled={locatingId === pipeId}
            onClick={() => void locateAsset(row)}>
            {locatingId === pipeId ? "查找中…" : "定位资产"}
          </button> : null}
        </article>;
      })}
    </div> : <div className="public-table-wrap"><table><thead><tr><th>记录编号</th><th>事件类型</th><th>事件日期</th><th>来源管段编号</th><th>关联状态</th><th>来源</th><th>操作</th></tr></thead>
      <tbody>{events.map((row) => { const id = String(row.incident_id ?? ""); const linkedPipe = String(row.linked_pipe_id ?? ""); return <tr key={id}>
        <td>{id || "未提供"}</td><td>{String(row.incident_type ?? "未分类")}</td><td>{String(row.incident_date ?? row.incident_date_raw ?? "未提供")}</td>
        <td>{String(row.linked_pipe_id ?? row.pipe_id_source ?? "未关联")}</td><td>{String(row.link_status ?? "unknown")}</td>
        <td>{row.source_ref ? `原始行 ${String((row.source_ref as Record<string, unknown>).source_line_number ?? "未知")}` : "AER Release Report"}</td>
        <td><button type="button" disabled={!linkedPipe || row.link_status !== "linked_unique_gas_segment" || locatingId === linkedPipe}
          title="使用已经核验的唯一管段编号定位"
          onClick={() => void locateAsset(row)}>{locatingId === linkedPipe ? "查找中…" : "定位资产"}</button></td>
      </tr>; })}</tbody></table></div>}
    {eventRead.status === "loading" ? <p className="public-note" role="status">正在读取监管记录…</p> : null}
    {locateNotice ? <div className="public-empty-state" role="status">{locateNotice}</div> : null}
    <div className="public-pager"><span>第 {page + 1} 页{total === undefined ? " · 总数未核实" : ` · 共 ${countLabel(total)} 条`}</span>
      <button type="button" disabled={page === 0} onClick={() => setPage((value) => Math.max(0, value - 1))}>上一页</button>
      <button type="button" disabled={eventRead.status === "loading" || events.length < 30 || (total !== undefined && (page + 1) * 30 >= total)} onClick={() => setPage((value) => value + 1)}>下一页</button></div>
    <p className="public-note">Release Report是监管发布记录，记录类别按来源字段保留；不得把所有记录都称作确认泄漏。</p>
  </>;
}

export function DatasetRecords({ data, onSelect }: { data: PublicDatasetOverview; onSelect(id: string): void }) {
  if (data.dataset_id === "aer_alberta") return <AERRecords data={data} onSelect={onSelect} />;
  if (data.dataset_id === "merrimack_2018") return <MerrimackEvidence data={data} />;
  const rows: Array<PublicDatasetFeature | NonNullable<PublicDatasetOverview["edges"]>[number]> = data.assets?.length ? data.assets : data.edges ?? [];
  if (!rows.length) return <div className="public-empty-state"><strong>来源没有提供可列出的记录</strong><p>此处不会用其它数据源补齐。</p></div>;
  return <div className="public-table-wrap"><table><thead><tr><th>来源ID</th><th>类型</th><th>来源字段摘要</th></tr></thead><tbody>
    {rows.map((row, index) => {
      const item = row as Record<string, unknown>;
      const id = String(item.record_id ?? item.pipe_id ?? item.id ?? `row-${index + 1}`);
      const props = (item.source_properties && typeof item.source_properties === "object" ? item.source_properties : {}) as Record<string, unknown>;
      const summary = Object.entries(props).slice(0, 5).map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
      return <tr key={`${id}-${index}`}><td><button type="button" onClick={() => onSelect(id)}>{id}</button></td>
        <td>{String(item.edge_type ?? (item.geometry ? "管线要素" : "网络连接"))}</td><td>{summary || "来源未提供属性"}</td></tr>;
    })}
  </tbody></table></div>;
}

function MerrimackEvidence({ data }: { data: PublicDatasetOverview }) {
  return <div className="public-evidence-view">
    <div className="public-event-title"><span className="public-state-dot" />{data.title}</div>
    <p>{data.event_date} · {data.event_time_precision}</p>
    <p>涉及地点：{data.locations?.join("、") ?? "未提供"}</p>
    <div className="public-fact-grid">{data.facts?.map((fact) => <article key={fact.label}><span>{fact.label}</span><strong>{countLabel(typeof fact.value === "number" ? fact.value : undefined)} {fact.unit}</strong><small>{fact.note}</small></article>)}</div>
    <h3>可追溯资料</h3><ul>{data.evidence?.map((item) => <li key={item.url}><a href={item.url} target="_blank" rel="noreferrer">{item.label}</a></li>)}</ul>
    <h3>时间证据</h3>{data.timeline?.map((item) => <article className="public-timeline-row" key={item.time}><b>{item.time}</b><span>{item.title}</span><p>{item.detail}</p></article>)}
    <p className="public-note">报告客户数是事故报告统计，不是本项目算法计算结果；本数据源没有可用于复算的完整管网拓扑。</p>
  </div>;
}

function CapabilityList({ data }: { data: PublicDatasetOverview }) {
  const capabilities = data.dataset_id === "aer_alberta"
    ? [["管线资产与来源属性", "可展示", "AER公开管线记录；当前地图只渲染所选管理分区的有限页"], ["事件与资产关联", "部分可用", "仅唯一精确匹配的link_status可关联；其余保留未关联/歧义"], ["城市低压配气", "不支持", "AER产品范围不包含低压配气"], ["关阀影响推演", "不支持", "无阀门设备拓扑、用户接入及供气工况"]]
    : data.dataset_id === "sgn_eastbourne"
      ? [["城市配气管线地图", "可展示", "直接来自SGN Eastbourne官方图层；当前页数据"], ["管径/材质/压力级别", "按来源字段展示", "保留原字段和值"], ["关阀影响推演", "不支持", "无逐段拓扑、阀门和用户关系"], ["资产事件/维修分析", "不支持", "未提供关联事件和维修记录"]]
      : data.dataset_id === "gaslib_582"
        ? [["网络拓扑", "可读取", "GasLib-582-v2研究基准"], ["图结构连通", "仅拓扑", "不解释压力/流量能力"], ["全网稳态计算", data.solver_status === "available" ? "已配置" : "未配置", data.solver_reason ?? "需完整模型和求解器"], ["真实城市维修/事件", "不支持", "研究基准不包含相应业务记录"]]
        : [["事故资料复盘", "可展示", "NTSB事故报告、事故时间地点和已发布统计"], ["报告客户影响", "可引用", "报告口径的客户数，不是拓扑重算"], ["实时资产地图", "不支持", "没有可以核验的完整事故网络GIS"], ["现场关阀/供气推演", "不支持", "报告资料不能替代运行网络和模型输入"]];
  return <div className="public-capability-list">{capabilities.map(([name, state, detail]) => <article key={name}><div><strong>{name}</strong><span className={state === "不支持" || state === "未配置" ? "is-unavailable" : "is-available"}>{state}</span></div><p>{detail}</p></article>)}</div>;
}

function AccidentReplay({ data }: { data: PublicDatasetOverview }) {
  const c = useGasCockpit();
  const items = data.timeline ?? [];
  const index = c.publicReplayIndex;
  const stage = items[index];
  if (!stage) return null;
  return <section className="public-dataset-view">
    <div className="gas-timeline-controls">
      <button type="button" disabled={index === 0} onClick={() => c.setPublicReplayIndex(index - 1)}>上一步</button>
      <button type="button" disabled={index >= items.length - 1} onClick={() => c.setPublicReplayIndex(index + 1)}>下一步</button>
      <span>阶段 {index + 1} / {items.length} · 2018 年当地时间 EDT</span>
    </div>
    <article className="public-timeline-row">
      <b>{stage.time} · {stage.title}</b><p>{stage.detail}</p>
      {stage.source_url ? <a href={stage.source_url} target="_blank" rel="noreferrer">{stage.source_label}</a> : null}
    </article>
  </section>;
}

export function PublicDatasetWindow({ id, mapMode = "map" }: { id: GasWindowId; mapMode?: "map" | "records" }) {
  const c = useGasCockpit();
  const resource = c.publicOverview;
  const data = resource.data;
  const [assetSearchDraft, setAssetSearchDraft] = useState(c.selectedPublicSearch);
  useEffect(() => setAssetSearchDraft(c.selectedPublicSearch), [c.activeDatasetId, c.selectedPublicScope, c.selectedPublicSearch]);
  const selectedEdge = data?.edges?.find((item) => item.id === c.selectedPublicAssetId);
  const feature = data?.assets?.find((item) => featureId(item) === c.selectedPublicAssetId)
    ?? (selectedEdge ? { record_id: selectedEdge.id, pipe_id: selectedEdge.id, source_properties: selectedEdge.source_properties,
      source_ref: { from_node_id: selectedEdge.from_node_id, to_node_id: selectedEdge.to_node_id } } as PublicDatasetFeature : null);
  if (resource.status === "loading" || resource.status === "idle") return <div className="public-empty-state" role="status"><strong>正在读取所选数据源</strong><p>{DATASET_LABELS[c.activeDatasetId]} · 不会回退到合成数据。</p></div>;
  if (resource.error) {
    if (id !== "topology") return <p className="public-note">数据源读取失败，处理方式见中央窗口。</p>;
    const configurationError = /未配置|未导入|目录|快照|清单/.test(resource.error.message);
    return <div className="public-empty-state is-error" role="alert"><strong>当前数据源暂不可用</strong>
      <p>{resource.error.message}</p>{!configurationError && <button type="button" onClick={resource.retry}>重试</button>}</div>;
  }
  if (!data) return <div className="public-empty-state" role="status"><strong>当前数据源没有返回数据</strong><p>请检查来源文件、网络或访问配置。</p></div>;

  if (id === "topology" && data.dataset_id === "merrimack_2018") return <AccidentReplay data={data} />;
  if (data.dataset_id === "ngn_the_north") return <NgnWorkspace id={id} data={data} mapMode={mapMode} />;
  if (id === "topology") return <div className="public-dataset-view public-map-view">
    <div className="public-data-banner"><strong>{DATASET_LABELS[c.activeDatasetId]}</strong><span>{data.data_version ?? "版本待核验"} · {data.status}</span></div>
    {(data.dataset_id === "aer_alberta" || data.dataset_id === "sgn_eastbourne") ? <form className="public-toolbar public-asset-toolbar" onSubmit={(event) => { event.preventDefault(); c.selectPublicSearch(assetSearchDraft); }}>
      {data.dataset_id === "aer_alberta" && data.available_scopes?.length ? <label>管理分区<select value={c.selectedPublicScope} onChange={(event) => c.selectPublicScope(event.target.value)}>
        <option value="__all__">全部分区</option>
        {data.available_scopes.map((scope) => <option key={scope} value={scope}>{scope}</option>)}
      </select></label> : null}
      {data.dataset_id === "aer_alberta" ? <><label>搜索来源资产<input value={assetSearchDraft} onChange={(event) => setAssetSearchDraft(event.target.value)} placeholder="管段编号、许可证或运营方" /></label>
        <button type="submit" disabled={assetSearchDraft.trim() === c.selectedPublicSearch.trim()}>搜索</button></> : null}
      {data.dataset_id === "sgn_eastbourne" ? <>
        <button type="button" disabled={!feature?.geometry} onClick={() => {
          if (!feature) return;
          const points = lineParts(feature).flat();
          if (!points.length) return;
          const xs = points.map(([x]) => x), ys = points.map(([, y]) => y);
          c.selectPublicBbox([Math.min(...xs) - .002, Math.min(...ys) - .002,
            Math.max(...xs) + .002, Math.max(...ys) + .002].map((value) => value.toFixed(6)).join(","));
        }}>加载选中管段周边</button>
        {c.publicBbox ? <button type="button" onClick={() => c.selectPublicBbox("")}>返回全域分页</button> : null}
      </> : null}
      <span>{c.publicBbox ? "当前地理范围 · " : ""}{data.dataset_id === "aer_alberta" ? `${data.scope ?? c.selectedPublicScope} · ` : "SGN官方图层 · "}第 {Math.floor((data.assets_page_offset ?? c.selectedPublicOffset) / (data.assets_page_limit ?? 120)) + 1} 页</span>
      <div className="public-pager"><button type="button" disabled={(data.assets_page_offset ?? c.selectedPublicOffset) <= 0}
        onClick={() => c.selectPublicOffset(Math.max(0, (data.assets_page_offset ?? c.selectedPublicOffset) - (data.assets_page_limit ?? 120)))}>上一页</button>
        <span>{(data.assets_page_offset ?? c.selectedPublicOffset) + 1}–{(data.assets_page_offset ?? c.selectedPublicOffset) + (data.assets?.length ?? 0)}{data.assets_total_in_scope === null || data.assets_total_in_scope === undefined ? " 条 · 总量未核实" : ` / ${countLabel(data.assets_total_in_scope)} 条`}</span>
        <button type="button" disabled={!data.assets_page_partial || !data.assets?.length}
          onClick={() => c.selectPublicOffset((data.assets_page_offset ?? c.selectedPublicOffset) + (data.assets_page_limit ?? 120))}>下一页</button></div>
    </form> : null}
    {mapMode === "records" ? <DatasetRecords data={data} onSelect={c.selectPublicAsset} />
      : <div className="public-map-card"><DatasetMap data={data} selectedId={c.selectedPublicAssetId} onSelect={c.selectPublicAsset} />
      {data.assets?.length ? <div className="public-asset-picker"><label>已加载资产记录<select value={c.selectedPublicAssetId ?? ""} onChange={(event) => c.selectPublicAsset(event.target.value || null)}>
        <option value="">选择管段/要素</option>{data.assets.map((asset, index) => { const key = featureId(asset); return <option key={`${key}-${index}`} value={key}>{asset.pipe_id ?? key}{asset.record_id && asset.record_id !== asset.pipe_id ? ` · 来源 ${asset.record_id}` : ""}</option>; })}</select></label>
        <span>当前显示 {data.assets.length} 条{data.assets_page_partial ? ` / 分区约 ${countLabel(data.assets_total_in_scope)} 条（已分页）` : ""}</span></div> : null}
    </div>}
  </div>;

  if (id === "scenario" || id === "impact" || id === "evidence") {
    if (data.dataset_id === "aer_alberta") return <div className="public-dataset-view"><AERRecords data={data} onSelect={c.selectPublicAsset} compact={id === "scenario"} />
      <p className="public-note">事件只按精确管段编号关联；未关联或有歧义记录不会推断到资产。</p></div>;
    if (data.dataset_id === "merrimack_2018") return id === "scenario"
      ? <div className="gas-event-list"><button type="button" onClick={() => c.openWindow("evidence")}><b>{data.title}</b><small>{data.event_date} · 查看调查资料</small></button></div>
      : <MerrimackEvidence data={data} />;
    return <div className="public-empty-state"><strong>{data.dataset_id === "gaslib_582" ? "该研究网络未提供事故事件或六任务输入" : "该官方图层未提供关联事件"}</strong>
      <p>本数据源仅展示其实际公开内容，不从图形相交或相邻关系制造故障场景。</p><CapabilityList data={data} /></div>;
  }

  if (id === "selected" || id === "services") return <div className="public-dataset-view"><h3>{c.selectedPublicEvent ? "监管事件详情" : "当前选中记录"}</h3>
    {c.selectedPublicEvent ? <dl className="gas-fields">
      {([["记录编号", "incident_id"], ["事件类型", "incident_type"], ["事件日期", "incident_date"], ["关联管段", "linked_pipe_id"], ["关联状态", "link_status"]] as const)
        .map(([label, key]) => <div key={key}><dt>{label}</dt><dd>{String(c.selectedPublicEvent?.[key] ?? "未提供")}</dd></div>)}
    </dl> : data.dataset_id === "merrimack_2018" ? <MerrimackEvidence data={data} /> : <AssetDetails feature={feature} />}
    {feature && c.selectedPublicEvent ? <AssetDetails feature={feature} /> : null}
    <p className="public-note">公开资产没有经过授权的用户接入服务关系。没有记录时不显示户数或推演路径。</p></div>;

  if (id === "timeline") {
    const items = data.dataset_id === "merrimack_2018" ? (data.timeline ?? []).map((item) =>
      ({ id: item.time, date: item.time, title: item.title, detail: "", open: () => c.setPublicReplayIndex((data.timeline ?? []).indexOf(item)) }))
      : (data.events ?? []).slice(0, 4).map((row) => ({
        id: String(row.incident_id), date: String(row.incident_date ?? "日期未提供"),
        title: String(row.incident_type ?? "监管记录"), detail: String(row.incident_id),
        open: () => c.selectPublicEvent(row),
      }));
    return items.length ? <div className="gas-timeline-panel">
      <div className="gas-timeline-controls"><span>{data.dataset_id === "aer_alberta" ? "近期监管记录" : "事故时间证据"}</span></div>
      <div className="gas-timeline-track">{items.map((item) => <button className="gas-timeline-event" key={item.id} type="button"
        aria-pressed={data.dataset_id === "merrimack_2018" ? items[c.publicReplayIndex]?.id === item.id : undefined} onClick={item.open}>
        <span className="gas-timeline-time">{item.date}</span><strong>{item.title}</strong><small>{item.detail}</small>
      </button>)}</div>
    </div> : <p className="gas-window-note">当前来源未提供事件时间线。</p>;
  }

  if (id === "tasks") return <div className="gas-task-dock"><div className="gas-task-list">
    <button type="button" onClick={() => c.openWindow("boundary")}>数据能力与边界</button>
    <button type="button" onClick={() => c.openWindow(data.dataset_id === "merrimack_2018" ? "evidence" : "data")}>{data.dataset_id === "merrimack_2018" ? "查看调查资料" : "查看数据来源"}</button>
  </div></div>;

  return <div className="public-dataset-view"><div className="public-data-banner"><strong>数据能力与边界</strong><span>{data.data_version ?? "版本待核验"}</span></div>
    <CapabilityList data={data} />
    <h3>来源与限制</h3><ul className="public-limitations">{data.limitations?.map((item) => <li key={item}>{item}</li>)}</ul>
    <div className="public-source-links">{data.source_refs?.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">查看来源</a>)}</div>
  </div>;
}
