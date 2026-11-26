import { useCallback, useState, useRef } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { useGasRead } from "./useGasRead";
import type { NgnFilters, NgnHistoryKind, PublicDatasetOverview } from "./publicDatasets";
import type { GasWindowId } from "./cockpitModel";

const LABELS = { emergency: "应急呼叫", interruption: "非计划停供" } as const;
const FILTERS = { material: "管材", pressure: "压力等级", diameter_mm: "管径（毫米）" };
const MATERIAL: Record<string,string> = { CI: "铸铁", DI: "球墨铸铁", PE: "聚乙烯", SI: "离心铸铁", ST: "钢", UN: "未知" };
type Bounds = [number,number,number,number];
type Point = [number,number];
const formatted = (v: unknown) => typeof v === "number" ? v.toLocaleString("zh-CN") : v == null || v === "" ? "未提供" : String(v);
const objectOrEmpty = (v: unknown) => typeof v === "object" && v !== null ? v as Record<string, unknown> : {};

function assetBounds(assets: NonNullable<PublicDatasetOverview["assets"]>): Bounds | undefined {
  let west = Infinity, south = Infinity, east = -Infinity, north = -Infinity;
  for (const { geometry } of assets) {
    if (!geometry) continue;
    const parts = geometry.type === "MultiLineString" ? geometry.coordinates as number[][][] : [geometry.coordinates as number[][]];
    for (const part of parts) for (const [x,y] of part) {
      west=Math.min(west,x); south=Math.min(south,y); east=Math.max(east,x); north=Math.max(north,y);
    }
  }
  if (!Number.isFinite(west)) return;
  const dx=Math.max(.00015,(east-west)*.12), dy=Math.max(.00015,(north-south)*.12);
  return [west-dx,south-dy,east+dx,north+dy];
}

function NgnAssets({ data, records }: { data: PublicDatasetOverview; records: boolean }) {
  const c = useGasCockpit();
  const [search, setSearch] = useState(c.selectedPublicSearch);
  const [filters, setFilters] = useState<NgnFilters>(c.ngnFilters);
  const rows = data.assets ?? [];
  const isRegion = c.publicBbox === "region";
  const view = isRegion ? data.region_bbox : data.viewport_bbox ?? assetBounds(rows);
  const selected = c.selectedPublicAssetId;
  const [moved, setMoved] = useState(false);
  const drag = useRef<Point | null>(null);
  const grid = isRegion ? data.overview_grid ?? [] : [];
  const maxCell = Math.max(1, ...grid.map(({count}) => count));
  const offset = data.assets_page_offset ?? 0;
  const limit = data.assets_page_limit ?? 120;
  const moveTo = (bounds: Bounds) => {
    if (bounds[0] >= -180 && bounds[1] >= -90 && bounds[2] <= 180 && bounds[3] <= 90) {
      c.selectPublicBbox(bounds.map((x) => x.toFixed(7)).join(","));
    }
  };
  const zoom = (factor: number, anchorX = .5, anchorY = .5) => {
    if (!view) return;
    const [w,s,e,n] = view;
    const x = w+(e-w)*anchorX, y = n-(n-s)*anchorY;
    if ((e-w)*factor < .00005 || (e-w)*factor > 30) return;
    moveTo([x+(w-x)*factor, y+(s-y)*factor, x+(e-x)*factor, y+(n-y)*factor]);
  };
  const project = ([x,y]: number[]) => view ? [(x-view[0])/(view[2]-view[0])*1000,(view[3]-y)/(view[3]-view[1])*640] : [0,0];
  return <div className="public-dataset-view public-map-view">
    <form className="public-toolbar public-asset-toolbar" onSubmit={(event) => {
      event.preventDefault(); c.setNgnFilters(filters); c.selectPublicSearch(search);
      c.selectPublicBbox("");
    }}>
      <label>资产编号<input aria-label="NGN 资产编号" value={search} onChange={(e) => setSearch(e.target.value)} /></label>
      {(Object.entries(FILTERS) as [keyof NgnFilters,string][]).map(([key,label]) => <label key={key}>{label}
        <select aria-label={label} value={filters[key]} onChange={(e) => setFilters({ ...filters, [key]: e.target.value })}>
          <option value="">全部</option>{(data.facets?.[key] ?? []).filter(({value}) => value !== null && value !== "").map(({value}) =>
            <option key={String(value)} value={String(value)}>{MATERIAL[String(value)] ? `${MATERIAL[String(value)]} (${value})` : value}</option>)}</select></label>)}
      <button type="submit">筛选</button>
      <button type="button" onClick={() => {
        const empty = { material: "", pressure: "", diameter_mm: "" };
        setSearch(""); setFilters(empty); c.setNgnFilters(empty); c.selectPublicSearch(""); c.selectPublicBbox("");
      }}>重置</button>
    </form>
    <div className="public-pager">
      <span>全区筛选命中 {formatted(data.counts?.filtered_records)} · {data.viewport_bbox ? "视野候选" : "结果"} {formatted(data.assets_total_in_scope)} · 当前 {rows.length} 条</span>
      <button type="button" disabled={!offset} onClick={() => c.selectPublicOffset(offset-limit)}>上一页</button>
      <button type="button" disabled={!data.assets_page_partial} onClick={() => c.selectPublicOffset(offset+limit)}>下一页</button>
    </div>
    {records ? <div className="public-table-wrap"><table><thead><tr><th>资产编号</th><th>管材</th><th>压力等级</th><th>原始管径</th></tr></thead>
      <tbody>{rows.map((item) => <tr key={item.record_id}><td><button type="button" aria-pressed={item.record_id === selected}
        onClick={() => c.selectPublicAsset(item.record_id ?? null)}>{item.pipe_id ?? item.record_id}</button></td>
        <td>{formatted(item.source_properties?.MATERIAL)}</td><td>{formatted(item.source_properties?.PRESSURE)}</td>
        <td>{formatted(item.source_properties?.DIAMETER)} {formatted(item.source_properties?.DIAM_UNIT)}</td></tr>)}</tbody></table></div>
      : view ? <div className="public-network-map-wrap">
        <div className="gas-map-mode"><button type="button" aria-label="放大 NGN 地图" onClick={() => zoom(.55)}>＋</button>
          <button type="button" aria-label="缩小 NGN 地图" onClick={() => zoom(1.8)}>－</button>
          <button type="button" aria-pressed={!isRegion} onClick={() => c.selectPublicBbox("")}>管线近景</button>
          <button type="button" aria-pressed={isRegion} onClick={() => c.selectPublicBbox("region")}>The North 区域概览</button></div>
        <svg className="public-network-map" data-testid="ngn-map" viewBox="0 0 1000 640" preserveAspectRatio="none" aria-label="The North 管线地图"
          onPointerDown={(ev) => { drag.current = [ev.clientX,ev.clientY]; setMoved(false); }}
          onPointerMove={(ev) => { if (drag.current && Math.hypot(ev.clientX-drag.current[0],ev.clientY-drag.current[1]) > 6) setMoved(true); }}
          onPointerCancel={() => { drag.current = null; }}
          onPointerLeave={() => { drag.current = null; }}
          onPointerUp={(ev) => {
            if (!drag.current) return;
            const [sx,sy] = drag.current; drag.current = null;
            if (Math.hypot(ev.clientX-sx,ev.clientY-sy) <= 6) return;
            const rect = ev.currentTarget.getBoundingClientRect();
            const dx = (sx-ev.clientX)/rect.width*(view[2]-view[0]);
            const dy = (ev.clientY-sy)/rect.height*(view[3]-view[1]);
            moveTo([view[0]+dx,view[1]+dy,view[2]+dx,view[3]+dy]);
          }}>
          <rect width="1000" height="640" className="public-map-background" />
          {grid.map(({ bbox, count }) => {
            const [x,y] = project([bbox[0],bbox[3]]);
            const [right,bottom] = project([bbox[2],bbox[1]]);
            return <rect key={bbox.join(",")} x={x} y={y} width={right-x} height={bottom-y}
              fill="var(--gas-brand)" opacity={.12+.66*Math.log1p(count)/Math.log1p(maxCell)}
              role="button" tabIndex={0} aria-label={`查看包含 ${formatted(count)} 条记录的网格`}
              onClick={() => { if (!moved) moveTo(bbox); }}
              onKeyDown={(e) => { if (e.key==="Enter" || e.key===" ") { e.preventDefault(); moveTo(bbox); } }}>
              <title>{formatted(count)} 条管线记录 · 按记录包围框中心归格</title></rect>;
          })}
          {rows.flatMap((item) => {
            const g = item.geometry, id = item.record_id;
            if (!g || !id) return [];
            const parts = g.type === "MultiLineString" ? g.coordinates as number[][][] : [g.coordinates as number[][]];
            return parts.map((part,index) => <polyline key={id+":"+index} points={part.map((p) => project(p).join(",")).join(" ")}
              className={"public-map-line"+(selected===id ? " is-selected" : "")} role="button" tabIndex={0}
              aria-label={`选择 NGN ${item.pipe_id ?? id}`} onClick={() => { if (!moved) c.selectPublicAsset(id); }}
              onKeyDown={(ev) => { if (ev.key==="Enter" || ev.key === " ") { ev.preventDefault(); c.selectPublicAsset(id); } }}>
              <title>{item.pipe_id ?? id}</title></polyline>);
          })}
        </svg>
        <p className="public-map-caption">{isRegion ? "色块表示资产记录密度，点击查看局部管线" : "真实管线几何 · 视野内按页显示"} · {data.geometry_note}</p>
      </div> : null}
    {!rows.length ? <p className="gas-window-note">当前查询没有资产记录。可调整筛选或地图范围。</p> : null}
  </div>;
}

function useHistory(kind: NgnHistoryKind, version: string, start = "", end = "", offset = 0) {
  const { publicApi } = useGasCockpit();
  const load = useCallback(async (signal: AbortSignal) => {
    const result = await publicApi.ngnEvents({ kind, start, end, offset }, signal);
    if (result.data_version !== version) throw new Error("NGN 历史版本已变化，请刷新数据源");
    return result;
  }, [publicApi, kind, version, start, end, offset]);
  return useGasRead(JSON.stringify([kind,version,start,end,offset]), load);
}

function NgnHistoryList({ version }: { version: string }) {
  const c = useGasCockpit();
  const [page, setPage] = useState({ key: "", offset: 0 });
  const { kind, start, end } = c.ngnHistory;
  const key = JSON.stringify(c.ngnHistory);
  const offset = key === page.key ? page.offset : 0;
  const { data, error, retry } = useHistory(kind, version, start, end, offset);
  const set = (patch: Partial<typeof c.ngnHistory>) => { setPage({ key:"", offset:0 }); c.setNgnHistory({ ...c.ngnHistory, ...patch }); };
  return <div className="gas-cockpit-event-panel">
    <p className="gas-window-note">NGN 全网历史 · 独立于 The North 地图</p>
    <div className="public-toolbar public-event-filters">
      <label>记录类别<select aria-label="NGN 历史记录类别" value={kind} onChange={(e) => set({ kind:e.target.value as NgnHistoryKind })}>
        <option value="emergency">应急呼叫</option><option value="interruption">非计划停供</option></select></label>
      <label>开始日<input type="date" aria-label="NGN 开始日" value={start} onChange={(e) => set({start:e.target.value})}/></label>
      <label>结束日<input type="date" aria-label="NGN 结束日" value={end} onChange={(e) => set({end:e.target.value})}/></label>
    </div>
    {error ? <p role="alert">{error.message} <button type="button" onClick={retry}>重试</button></p>
      : !data ? <p role="status">正在读取历史记录</p>
      : data.status === "unavailable" ? <p>{data.reason}</p>
      : <><p className="gas-window-note">共 {formatted(data.total)} 条 · 原件 {formatted(data.coverage?.first_date)} 至 {formatted(data.coverage?.last_date)}</p>
        <div className="gas-event-list">{data.items.map((item) => <button type="button" key={String(item.record_id)}
          aria-pressed={item.record_id===c.selectedPublicEvent?.record_id} onClick={() => c.selectPublicEvent(item)}>
          <b>{formatted(item.category)}</b><small>{formatted(item.date)} {item.time ? String(item.time) : ""}</small>
          <span>{LABELS[kind]} · {formatted(item.source_job_id ?? item.record_id)}</span></button>)}</div>
        {!data.items.length ? <p>当前日期范围无记录。</p> : null}
        <div className="public-pager"><button type="button" disabled={!offset} onClick={() => setPage({key,offset:offset-30})}>上一页</button>
          <button type="button" disabled={offset+data.items.length >= (data.total ?? 0)} onClick={() => setPage({key,offset:offset+30})}>下一页</button></div></>}
  </div>;
}

function NgnTimeline({ version }: { version: string }) {
  const c = useGasCockpit();
  const calls = useHistory("emergency",version), interruptions = useHistory("interruption",version);
  return <div className="gas-timeline-panel"><p className="gas-window-note">NGN 全网历史 · 按原件有记录的月份汇总</p>
    {([["emergency",calls],["interruption",interruptions]] as const).map(([kind,source]) => <section key={kind}>
      <strong>{LABELS[kind]}</strong>
      {source.error ? <p role="alert">{source.error.message} <button type="button" onClick={source.retry}>重试</button></p>
        : !source.data ? <p role="status">正在读取月度统计</p>
        : source.data.status === "unavailable" ? <p>{source.data.reason}</p>
        : <div className="gas-timeline-track" aria-label={LABELS[kind]+"月度记录数"}>{source.data.monthly.map(({month,count}) =>
          <button className="gas-timeline-event" key={month} type="button" onClick={() => {
            const [y,m] = month.split("-").map(Number);
            const day = new Date(Date.UTC(y,m,0)).getUTCDate();
            c.setNgnHistory({kind,start:month+"-01",end:`${month}-${day}`});
            c.openWindow("scenario");
          }}><span className="gas-timeline-time">{month}</span><strong>{formatted(count)} 条</strong></button>)}</div>}
    </section>)}</div>;
}

export function NgnWorkspace({ id, data, mapMode }: { id: GasWindowId; data: PublicDatasetOverview; mapMode: "map" | "records" }) {
  const c = useGasCockpit();
  const version = data.data_version ?? "";
  if (id === "topology") return <NgnAssets data={data} records={mapMode === "records"} />;
  if (id === "scenario") return <NgnHistoryList version={version} />;
  if (id === "timeline") return <NgnTimeline version={version} />;
  if (id === "selected") {
    const event = c.selectedPublicEvent;
    const asset = data.assets?.find((item) => item.record_id === c.selectedPublicAssetId);
    if (!asset && !event) return <div className="gas-empty-selection"><h3>选择管线或历史记录</h3><p>资产来自 The North，历史来自 NGN 全网。</p></div>;
    const source = objectOrEmpty(event?.source_ref ?? asset?.source_ref);
    const fields = objectOrEmpty(event?.properties ?? asset?.source_properties);
    return <><h3 data-testid="ngn-selection">{event ? formatted(event.category) : formatted(asset?.pipe_id ?? asset?.record_id)}</h3>
      <p className="gas-window-note">{event ? "NGN 全网 · 未核验管段关联" : "The North · 在运配气主管"}</p>
      <dl className="gas-fields">{Object.entries(fields).map(([label,value]) => <div key={label}><dt>{label}</dt><dd>{formatted(value)}</dd></div>)}</dl>
      <p className="gas-window-note">{event ? `${formatted(source.file)} · ${formatted(source.sheet)} 第 ${formatted(source.row)} 行` : `OBJECTID ${formatted(source.OBJECTID)} · ${formatted(source.layer)}`}</p>
      {typeof source.url === "string" ? <a href={source.url} target="_blank" rel="noreferrer">打开 NGN 官方来源</a> : null}</>;
  }
  if (id === "tasks") return <div className="gas-task-list">
    <button type="button" onClick={() => c.openWindow("data")}>数据覆盖与来源</button>
    <button type="button" onClick={() => c.openWindow("boundary")}>推演所需输入</button>
  </div>;
  return <div className="public-dataset-view"><h3>NGN 数据覆盖与能力</h3>
    <p>地图来源 The North。运行历史覆盖 NGN 全网。两类历史各按自己的原表计数。</p>
    {c.mode === "risk" || id === "data" ? (["material","pressure"] as const).map((key) =>
      <section key={key}><h3>{FILTERS[key]}分布 · The North 区域</h3><dl className="gas-fields">
        {data.facets?.[key]?.map(({value,count}) => <div key={String(value)}><dt>{formatted(value)}</dt><dd>{formatted(count)} 条</dd></div>)}
      </dl></section>) : null}
    <ul className="public-limitations">{data.limitations?.map((line) => <li key={line}>{line}</li>)}</ul>
    <div className="public-source-links">{data.source_refs?.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">官方资料 {url.split("/").at(-1)}</a>)}</div>
  </div>;
}
