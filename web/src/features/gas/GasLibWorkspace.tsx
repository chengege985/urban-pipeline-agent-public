import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { gasTimelineReducer, INITIAL_GAS_TIMELINE, type GasWindowId } from "./cockpitModel";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasTimelineTrack, type GasStage } from "./GasPanels";
import { GasTopologyView, type TopologyNode, type TopologyEdge } from "./GasTopologyView";
import type { PublicDatasetOverview } from "./publicDatasets";
import { useGasRead } from "./useGasRead";

export type GasLibMapView = "network" | "published";

export function projectNetwork(data: PublicDatasetOverview | null, view: "schematic" | "published") {
  const nodes = (data?.nodes ?? []).flatMap((node) => {
    const x = node.source_properties?.x, y = node.source_properties?.y;
    const point = view === "schematic" ? (x?.trim() && y?.trim() ? [Number(x), Number(y)] : null) : node.coordinates;
    return point?.every(Number.isFinite) ? [{ node, point }] : [];
  });
  const xs = nodes.map(({ point }) => point[0]), ys = nodes.map(({ point }) => point[1]);
  const minX = Math.min(...xs), minY = Math.min(...ys);
  const width = Math.max(1e-9, Math.max(...xs) - minX), height = Math.max(1e-9, Math.max(...ys) - minY);
  const scale = Math.min(900 / width, 560 / height);
  const viewNodes: TopologyNode[] = nodes.map(({ node, point }) => ({
    node_id: node.id, node_type: node.node_type === "source" ? "source" : node.node_type === "building" ? "consumer" : "junction",
    layout: { x: (point[0] - minX - width / 2) * scale + 500,
      y: 320 - (point[1] - minY - height / 2) * scale },
  }));
  const ids = new Set(viewNodes.map(({ node_id }) => node_id));
  const viewEdges: TopologyEdge[] = (data?.edges ?? []).filter((edge) =>
    ids.has(edge.from_node_id) && ids.has(edge.to_node_id)).map((edge) => ({ ...edge, edge_id: edge.id, pipe_id: edge.id }));
  return { viewNodes, viewEdges };
}

export function useGasLibWorkspace() {
  const c = useGasCockpit();
  const data = c.publicOverview.data?.dataset_id === "gaslib_582" ? c.publicOverview.data : null;
  const version = data?.data_version ?? "";
  const schematic = useMemo(() => projectNetwork(data, "schematic"), [data]);
  const published = useMemo(() => projectNetwork(data, "published"), [data]);
  const preset = data?.edges?.some(({ id, edge_type }) => id === "pipe_2" && edge_type === "pipe") ? "pipe_2" : "";
  const [draft, setDraft] = useState<string | null>(null);
  const [query, setQuery] = useState<{ version: string; edgeId: string } | null>(null);
  const [timeline, dispatch] = useReducer(gasTimelineReducer, INITIAL_GAS_TIMELINE);
  const edgeId = query?.version === version ? query.edgeId : preset;
  const read = useCallback((signal: AbortSignal) => c.publicApi.benchmarkConnectivity(edgeId, version, signal),
    [c.publicApi, edgeId, version]);
  const result = useGasRead(data && version && edgeId ? version + ":" + edgeId : null, read);
  const [focusRevision, requestFocus] = useReducer((value: number) => value + 1, 0);
  useEffect(() => {
    setDraft(null); setQuery(null); dispatch({ type: "reset" });
  }, [c.activeDatasetId]);
  useEffect(() => {
    if (!timeline.playing || !result.data || c.reducedMotion || c.hidden.has("timeline")) {
      if (timeline.playing) dispatch({ type: "pause" });
      return;
    }
    const timer = window.setInterval(() => dispatch({ type: "tick", count: 3 }), 1500);
    return () => window.clearInterval(timer);
  }, [timeline.playing, result.data, c.reducedMotion, c.hidden.has("timeline")]);
  const selectStage = (index: number) => dispatch({ type: "select", index, count: result.data ? 3 : 0 });
  const run = () => {
    const selected = draft ?? preset;
    if (!version || !selected) return;
    dispatch({ type: "reset" });
    if (edgeId === selected) result.retry();
    else setQuery({ version, edgeId: selected });
  };
  return { data, version, schematic, published, focusRevision, requestFocus, draft: draft ?? preset, setDraft, result, timeline, dispatch, selectStage, run };
}

type Workspace = ReturnType<typeof useGasLibWorkspace>;
const stageNames = ["基准连通", "给定管道退出", "恢复基准连接"];

function AssetDirectory({ data, onSelect }: { data: PublicDatasetOverview; onSelect(): void }) {
  const c = useGasCockpit();
  const [filter, setFilter] = useState("");
  const [kind, setKind] = useState("all");
  const [offset, setOffset] = useState(0);
  const records = [
    ...(data.edges ?? []).map((edge) => ({ id: edge.id, type: edge.edge_type, group: "edge", relation: edge.from_node_id + " ↔ " + edge.to_node_id })),
    ...(data.nodes ?? []).map((node) => ({ id: node.id, type: node.node_type, group: "node", relation: "—" })),
  ].filter((item) => (kind === "all" || item.group === kind) && item.id.toLowerCase().includes(filter.trim().toLowerCase()));
  return <div className="public-dataset-view">
    <div className="public-toolbar">
      <label>对象编号<input value={filter} onChange={(event) => { setFilter(event.target.value); setOffset(0); }} /></label>
      <label>对象类型<select value={kind} onChange={(event) => { setKind(event.target.value); setOffset(0); }}>
        <option value="all">全部</option><option value="edge">网络连接</option><option value="node">拓扑节点</option>
      </select></label>
    </div>
    <div className="public-table-wrap"><table><thead><tr><th>编号</th><th>类型</th><th>端点</th></tr></thead>
      <tbody>{records.slice(offset, offset + 50).map((item) => <tr key={item.group + ":" + item.id}>
        <td><button type="button" onClick={() => {
          c.selectPublicAsset(item.id); c.openWindow("selected"); c.openWindow("topology"); c.closeExtra(); onSelect();
        }}>{item.id}</button></td>
        <td>{item.type}</td><td>{item.relation}</td>
      </tr>)}</tbody></table></div>
    <div className="public-pager"><span>{records.length} 条 · 第 {offset / 50 + 1} 页</span>
      <button type="button" disabled={!offset} onClick={() => setOffset(offset - 50)}>上一页</button>
      <button type="button" disabled={offset + 50 >= records.length} onClick={() => setOffset(offset + 50)}>下一页</button>
    </div>
  </div>;
}

export function GasLibWindow({ id, workspace: w, onShowTopology, mapView }: {
  id: GasWindowId; workspace: Workspace; onShowTopology(view: GasLibMapView): void; mapView: GasLibMapView;
}) {
  const c = useGasCockpit();
  const data = w.data;
  if (!data) return null;
  const result = w.result.data;
  const inactive = w.timeline.index !== 1;
  const unreachable = result ? (inactive ? result.unreachable_node_ids_before : result.unreachable_node_ids_after) : [];
  const exits = result ? (inactive ? result.reachable_exits_before : result.reachable_exits_after) : null;
  const stages: GasStage[] = result ? stageNames.map((label, index) => ({
    id: String(index), time: "阶段 " + (index + 1), label,
    detail: (index === 1 ? result.reachable_exits_after : result.reachable_exits_before) + " 个可达出口",
  })) : [];
  const edge = data.edges?.find((item) => item.id === c.selectedPublicAssetId);
  const node = data.nodes?.find((item) => item.id === c.selectedPublicAssetId);
  const pipes = (data.edges ?? []).filter((item) => item.edge_type === "pipe" || item.edge_type === "shortpipe");
  const locateResult = () => {
    if (result) c.selectPublicAsset(result.edge_id);
    c.openWindow("topology");
    w.requestFocus();
  };
  const download = () => {
    if (!result) return;
    const href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = href;
    anchor.download = "GasLib-" + result.edge_id + "-connectivity.json";
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(href), 1000);
  };

  const graph = mapView === "published" || !w.schematic.viewNodes.length ? w.published : w.schematic;
  if (id === "topology") return <GasTopologyView key={mapView} benchmark={{
    nodes: graph.viewNodes, edges: graph.viewEdges, version: w.version, selectedId: c.selectedPublicAssetId,
    coordinateLabel: graph === w.published ? "GasLib 发布经纬度 · 全网" : "GasLib 原始布局 x/y · 全网",
    focusEdgeId: result?.edge_id, focusRevision: w.focusRevision,
    disabledEdges: !inactive && result ? [result.edge_id] : [], unreachableNodes: unreachable,
    possibleUnreachableNodes: result
      ? [...new Set([...result.unreachable_node_ids_before, ...result.unreachable_node_ids_after])] : [],
    onSelect: (objectId) => { c.selectPublicAsset(objectId); if (pipes.some((item) => item.id === objectId)) w.setDraft(objectId); },
  }} />;
  if (id === "timeline") return <GasTimelineTrack stages={stages} index={w.timeline.index} playing={w.timeline.playing}
    reducedMotion={c.reducedMotion} badge="图连通" note="给定阶段 · 非时序预测"
    reset={() => w.dispatch({ type: "reset" })} select={w.selectStage}
    toggle={() => w.dispatch(w.timeline.playing ? { type: "pause" } : { type: "play", count: stages.length, reduced: c.reducedMotion })} />;

  if (id === "scenario") return <div className="gas-cockpit-event-panel">
    <div className="gas-mode-message"><b>给定管道退出与图通路影响</b>
      <p>从基准拓扑选中一条管道，比较退出前后出口可达性。</p>
      <button type="button" onClick={() => c.openWindow("impact")} disabled={!result}>比较退出与恢复</button>
    </div>
    <div className="public-toolbar public-event-filters"><label>给定管道<select aria-label="给定管道" value={w.draft} onChange={(event) => {
      w.setDraft(event.target.value); c.selectPublicAsset(event.target.value || null);
    }}><option value="">选择管道</option>{pipes.map((item) => <option key={item.id}>{item.id}</option>)}</select></label>
      <button type="button" disabled={!w.draft || w.result.status === "loading"} onClick={w.run}>执行推演</button>
    </div>
    {w.result.status === "loading" ? <p role="status">正在比较图可达性…</p> : null}
    {w.result.error ? <p role="alert">{w.result.error.message}</p> : null}
    <div className="gas-event-list">{stages.map((stage, index) => <button type="button" key={stage.id}
      aria-pressed={index === w.timeline.index} onClick={() => w.selectStage(index)}>
      <b>{stage.label}</b><small>{result?.edge_id} · {stage.detail}</small>
    </button>)}</div>
  </div>;

  if (id === "data") return <AssetDirectory data={data} onSelect={() => onShowTopology("network")} />;
  if (id === "tasks") return <div className="gas-task-dock"><div className="gas-task-list">
    <button type="button" onClick={() => c.openWindow("data")}>资产分页目录<span>节点与连接 · 50 条/页</span></button>
    <button type="button" onClick={() => c.openWindow("evidence")}>推演证据报告<span>基准文件 · 结果 JSON</span></button>
    <button type="button" onClick={() => c.openWindow("boundary")}>模型就绪状态<span>图连通 · 稳态未接入</span></button>
  </div></div>;
  if (id === "layers") return <div className="gas-layer-toggles">
    {(["edges", "nodes", "impact", "labels"] as const).map((layer) => <label key={layer}>
      <input type="checkbox" checked={c.layers[layer]} onChange={() => c.toggleLayer(layer)} />
      {{ edges: "连接", nodes: "节点", impact: "图不可达覆盖", labels: "关键标签" }[layer]}
    </label>)}
  </div>;

  const impact = result ? <dl className="gas-fields">
    <div><dt>给定退出连接</dt><dd>{result.edge_id}</dd></div>
    <div><dt>基准可达出口</dt><dd>{result.reachable_exits_before}</dd></div>
    <div><dt>退出后可达出口</dt><dd>{result.reachable_exits_after}</dd></div>
    <div><dt>退出后新增不可达节点</dt><dd>{result.newly_unreachable_node_count}</dd></div>
    <div><dt>退出后新增不可达出口</dt><dd>{result.newly_unreachable_exit_ids.join("、") || "无"}</dd></div>
  </dl> : <p className="gas-window-note">先在左侧选择管道并执行推演。</p>;

  if (id === "selected") return <div className="public-dataset-view">
    <h3>{edge ? "连接详情" : node ? "节点详情" : "选择拓扑对象"}</h3>
    {edge || node ? <dl className="gas-fields">
      <div><dt>对象编号</dt><dd>{edge?.id ?? node?.id}</dd></div>
      <div><dt>来源类型</dt><dd>{edge?.edge_type ?? node?.node_type}</dd></div>
      {edge ? <div><dt>拓扑端点</dt><dd>{edge.from_node_id} ↔ {edge.to_node_id}</dd></div> : null}
      {node?.coordinates ? <div><dt>发布坐标</dt><dd>{node.coordinates.join(", ")}</dd></div> : null}
    </dl> : <p className="gas-window-note">点击图上的连接或节点查看。</p>}
    <h3>图连通与模型就绪</h3>
    <dl className="gas-fields">
      <div><dt>当前阶段</dt><dd>{result ? stageNames[w.timeline.index] : "待执行"}</dd></div>
      <div><dt>当前可达出口</dt><dd>{exits ?? "待执行"}</dd></div>
      <div><dt>当前不可达节点</dt><dd>{result ? unreachable.length : "待执行"}</dd></div>
    </dl>
    {impact}
    {result ? <button type="button" onClick={locateResult}>定位给定管道</button> : null}
    <p className="gas-window-note">GasLib 研究基准 · 暂无稳态压力、流量或实际用户影响结果。</p>
  </div>;
  if (id === "impact" || id === "services" || id === "evidence") return <div className="public-dataset-view">
    <h3>{id === "evidence" ? "推演证据报告" : "基准、退出与恢复对照"}</h3>
    {impact}
    {result ? <div className="public-pager">
      <button type="button" onClick={() => { w.selectStage(1); locateResult(); c.closeExtra(); }}>定位退出阶段</button>
      <button type="button" onClick={download}>下载结果 JSON</button>
    </div> : null}
    <p>基准文件 {w.version} · 算法 {result?.method ?? "无向图多源可达性"}</p>
    <a href="https://gaslib.zib.de/data.html" target="_blank" rel="noreferrer">GasLib 原始研究数据</a>
  </div>;
  return <div className="public-dataset-view"><h3>数据与模型就绪状态</h3>
    <p>网络拓扑与单管道退出连通性已接入。正式风险评分、压力与流量、实际用户服务关系暂无可用结果。</p>
    <p>{data.solver_reason}</p><a href="https://gaslib.zib.de/documentation.html" target="_blank" rel="noreferrer">GasLib 文件说明</a>
  </div>;
}
