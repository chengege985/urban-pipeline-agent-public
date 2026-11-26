import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasTimelineTrack, type GasStage } from "./GasPanels";
import { GasTopologyView, type TopologyEdge, type TopologyNode } from "./GasTopologyView";
import { gasTimelineReducer, INITIAL_GAS_TIMELINE, type GasWindowId } from "./cockpitModel";
import type { PublicDatasetOverview, SicilyAgentContext, SicilyAgentRun, SicilyBaseline, SicilyRun } from "./publicDatasets";
import { useGasRead } from "./useGasRead";

function schematic(data: PublicDatasetOverview | null) {
  const nodes = data?.nodes ?? [];
  const points = nodes.map(({ id }, i) => {
    const angle = i * 2.399963;
    const radius = Math.sqrt(i + 1) * 18;
    return { id, x: Math.cos(angle) * radius, y: Math.sin(angle) * radius, dx: 0, dy: 0 };
  });
  const positions = new Map(points.map((point) => [point.id, point]));
  const sourceEdges = data?.edges ?? [];
  for (let step = 0; step < 90; step++) {
    for (let i = 0; i < points.length; i++) {
      const a = points[i];
      a.dx -= a.x * 0.005;
      a.dy -= a.y * 0.005;
      for (let j = i + 1; j < points.length; j++) {
        const b = points[j], x = a.x - b.x, y = a.y - b.y;
        const scale = 75 / Math.max(9, x * x + y * y);
        a.dx += x * scale; a.dy += y * scale;
        b.dx -= x * scale; b.dy -= y * scale;
      }
    }
    for (const edge of sourceEdges) {
      const a = positions.get(edge.from_node_id), b = positions.get(edge.to_node_id);
      if (!a || !b) continue;
      const x = a.x - b.x, y = a.y - b.y;
      const pull = (1 - 23 / Math.max(1, Math.hypot(x, y))) * 0.15;
      a.dx -= x * pull; a.dy -= y * pull;
      b.dx += x * pull; b.dy += y * pull;
    }
    for (const point of points) {
      point.x += point.dx; point.y += point.dy;
      point.dx *= 0.55; point.dy *= 0.55;
    }
  }
  const scale = Math.min(420 / Math.max(1, ...points.map((p) => Math.abs(p.x))),
    260 / Math.max(1, ...points.map((p) => Math.abs(p.y))));
  const viewNodes: TopologyNode[] = nodes.map((node, index) => ({
    node_id: "node:" + node.id, name: node.id, node_type: node.node_type === "source" ? "source" : "junction", layout: {
      x: 500 + points[index].x * scale, y: 320 + points[index].y * scale,
    },
  }));
  return { nodes: viewNodes, edges: sourceEdges.map((edge): TopologyEdge => ({
    ...edge, edge_id: edge.id, pipe_id: edge.id, from_node_id: "node:" + edge.from_node_id, to_node_id: "node:" + edge.to_node_id,
  })) };
}

const number = (value: number | null | undefined, precision = 3) =>
  value === null || value === undefined ? "未提供" : value.toLocaleString(undefined, { maximumFractionDigits: precision });
const dayLabel = (key: string) => key.replace(/^(\d{4})(\d{2})(\d{2})/, "$1-$2-$3");

export function useSicilyWorkspace() {
  const c = useGasCockpit();
  const data = c.activeDatasetId === "sicily_transmission" && c.publicOverview.data?.dataset_id === "sicily_transmission"
    ? c.publicOverview.data : null;
  const version = data?.data_version ?? "";
  const graph = useMemo(() => schematic(data), [data]);
  const [session] = useState(() => Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, "0")).join(""));
  const [picked, setPicked] = useState("");
  const [edge, setEdge] = useState("");
  const [result, setResult] = useState<SicilyRun>();
  const [baseline, setBaseline] = useState<SicilyBaseline>();
  const [working, setWorking] = useState<"topology" | "run" | "baseline" | null>(null);
  const [error, setError] = useState("");
  const [timeline, dispatch] = useReducer(gasTimelineReducer, INITIAL_GAS_TIMELINE);
  const [focusRevision, focus] = useReducer((value: number) => value + 1, 0);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    setPicked(""); setEdge(""); setBaseline(undefined); setResult(undefined); setError(""); setWorking(null); dispatch({ type: "reset" });
    return () => controller.current?.abort();
  }, [version]);
  const timelineVisible = !c.hidden.has("timeline");
  useEffect(() => {
    if (!timeline.playing) return;
    if (c.reducedMotion || !timelineVisible) { dispatch({ type: "pause" }); return; }
    const timer = window.setInterval(() => dispatch({ type: "tick", count: 3 }), 1500);
    return () => window.clearInterval(timer);
  }, [timeline.playing, c.reducedMotion, timelineVisible]);
  const caseId = picked || data?.default_case || "";
  const read = useCallback((signal: AbortSignal) => c.publicApi.sicilyCondition(caseId, version, signal),
    [c.publicApi, caseId, version]);
  const condition = useGasRead(version && caseId ? version + ":" + caseId : null, read);
  const clearRun = () => {
    controller.current?.abort(); setResult(undefined); setError(""); setWorking(null); dispatch({ type: "reset" });
  };
  const chooseEdge = (value: string) => {
    if (value !== edge) { clearRun(); setEdge(value); }
    c.selectPublicAsset(value || null);
  };
  const execute = async (mode: "topology" | "run") => {
    if (!edge || !caseId || !version || working || (mode === "run" && !result)) return;
    if (mode === "topology") clearRun();
    else setError("");
    const abort = new AbortController();
    controller.current = abort;
    setWorking(mode);
    const timer = window.setTimeout(() => {
      if (abort.signal.aborted) return;
      abort.abort();
      setWorking(null);
      setError(mode === "topology" ? "拓扑推演请求超时，请检查服务后重试。" : "稳态计算等待超时，仍可回放拓扑结果。");
    }, mode === "topology" ? 8000 : 95000);
    try {
      const output = await c.publicApi.sicilyRun(caseId, edge, version, abort.signal, mode, session);
      if (abort.signal.aborted) return;
      setResult(output);
      if (mode === "topology") {
        dispatch({ type: "select", index: 1, count: 3 });
        focus();
        c.openWindow("timeline");
      }
    } catch (failure) {
      if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : "执行失败");
    } finally {
      window.clearTimeout(timer);
      if (!abort.signal.aborted) setWorking(null);
    }
  };
  const executeBaseline = async () => {
    if (!caseId || !version || working) return;
    const abort = new AbortController();
    controller.current = abort;
    setBaseline(undefined); setWorking("baseline"); setError("");
    const timer = window.setTimeout(() => {
      if (abort.signal.aborted) return;
      abort.abort(); setWorking(null); setError("基准求解请求超时。");
    }, 95000);
    try {
      const output = await c.publicApi.sicilyBaseline(caseId, version, abort.signal, session);
      if (!abort.signal.aborted) setBaseline(output);
    } catch (failure) {
      if (!abort.signal.aborted) setError(failure instanceof Error ? failure.message : "基准求解失败");
    } finally {
      window.clearTimeout(timer);
      if (!abort.signal.aborted) setWorking(null);
    }
  };
  const selected = c.selectedPublicAssetId;
  const agentContext: SicilyAgentContext = { session_id: session, data_version: version, case_id: caseId,
    ...(selected ? { selected_id: selected.replace(/^node:/, ""), selected_kind: selected.startsWith("node:") ? "node" : "pipe" } : {}) };
  const chooseCase = (value: string) => { clearRun(); setBaseline(undefined); setPicked(value); };
  const showObject = (target: string, kind: "node" | "pipe", targetCase: string) => {
    if (targetCase !== caseId) chooseCase(targetCase);
    c.selectPublicAsset(kind === "node" ? "node:" + target : target);
    focus();
  };
  const showAgentRun = async (ref: SicilyAgentRun) => {
    const output = await c.publicApi.sicilyAgentExecution(agentContext, ref);
    clearRun();
    if (ref.case_id !== caseId) setBaseline(undefined);
    setPicked(ref.case_id); setEdge(ref.edge_id); setResult(output);
    c.selectPublicAsset(ref.edge_id); c.openWindow("timeline"); focus();
    dispatch({ type: "select", index: 1, count: 3 });
  };
  return { data, graph, version, caseId, chooseCase, agentContext, showObject, showAgentRun,
    edge, chooseEdge, result, baseline, working, error, condition, execute, executeBaseline, timeline, dispatch, focusRevision };
}

export function SicilyWindow({ id, workspace: w, mapMode, onShowMap }: {
  id: GasWindowId; workspace: ReturnType<typeof useSicilyWorkspace>; mapMode: "map" | "records"; onShowMap(): void;
}) {
  const c = useGasCockpit();
  const [query, setQuery] = useState("");
  if (!w.data) return null;
  const selected = c.selectedPublicAssetId ?? "";
  const nodeId = selected.startsWith("node:") ? selected.slice(5) : "";
  const node = w.data.nodes?.find(({ id }) => id === nodeId);
  const edge = w.data.edges?.find(({ id }) => id === selected);
  const record = w.condition.data?.nodes[nodeId];
  const flow = w.condition.data?.pipes[selected];
  const days = [...new Set(w.data.cases?.map((item) => item.id.slice(0, 8)))];
  const day = w.caseId.slice(0, 8);
  const hours = w.data.cases?.filter((item) => item.id.startsWith(day + "-")) ?? [];
  const controls = <div className="public-toolbar">
    <label>代表日<select value={day} onChange={(e) => {
      const available = w.data?.cases?.filter((item) => item.id.startsWith(e.target.value + "-")) ?? [];
      const next = available.find((item) => item.id.endsWith(w.caseId.slice(-3))) ?? available[0];
      if (next) w.chooseCase(next.id);
    }}>{days.map((key) => <option key={key} value={key}>{dayLabel(key)}</option>)}</select></label>
    <label>小时<select value={w.caseId} onChange={(e) => w.chooseCase(e.target.value)}>
      {hours.map((item) => <option key={item.id} value={item.id}>{item.id.slice(-2)}:00{item.published ? "" : " · 缺发布结果"}</option>)}
    </select></label>
  </div>;
  const heading = <p className="public-note">西西里高压输气研究样本 · 作者重建负荷 · 稳态工况</p>;
  const withdrawn = w.timeline.index === 1;
  const baselineUnreachable = w.result?.baseline_unreachable_nodes ?? [];
  const outageUnreachable = [...baselineUnreachable, ...(w.result?.unreachable_nodes ?? [])];
  const route = w.result?.alternate_path_edges ?? [];
  const branches = w.result?.disconnected_branch_edges ?? [];
  const stages: GasStage[] = w.result ? [
    { id: "baseline", time: "阶段 1", label: "基准连通", detail: `${w.result.reachable_before} 个节点可达` },
    { id: "withdrawn", time: "阶段 2", label: "给定管段退出",
      detail: `${w.result.reachable_after} 个可达 · ${w.result.unreachable_nodes.length} 个新增断连` },
    { id: "restored", time: "阶段 3", label: "恢复基准连接", detail: `${w.result.reachable_before} 个节点可达` },
  ] : [];
  if (id === "topology" && mapMode === "map") return <GasTopologyView benchmark={{
    ...w.graph, version: w.version, selectedId: selected, coordinateLabel: "西西里 · 研究拓扑示意",
    focusRevision: w.focusRevision,
    focusNodeIds: w.result?.unreachable_nodes.map((key) => "node:" + key),
    focusEdgeId: w.result?.edge_id, disabledEdges: w.result && withdrawn ? [w.result.edge_id] : [],
    alternateEdgeIds: route,
    unreachableNodes: (withdrawn ? outageUnreachable : baselineUnreachable).map((key) => "node:" + key),
    possibleUnreachableNodes: outageUnreachable.map((key) => "node:" + key),
    onSelect: (key) => {
      c.selectPublicAsset(key); c.openWindow("selected");
      if (w.data?.edges?.some(({ id }) => id === key)) w.chooseEdge(key);
    },
  }} />;
  if (id === "data" || id === "topology") return <div className="public-dataset-view">
    {heading}<label>管段编号<input value={query} onChange={(e) => setQuery(e.target.value)} /></label>
    <div className="public-table-wrap"><table><thead><tr><th>管段</th><th>端点</th><th>长度 m</th></tr></thead><tbody>
      {(w.data.edges ?? []).filter((item) => item.id.includes(query.trim())).map((item) => <tr key={item.id}>
        <td><button type="button" onClick={() => { w.chooseEdge(item.id); onShowMap(); c.closeExtra(); c.openWindow("selected"); }}>{item.id}</button></td>
        <td>{item.from_node_id} ↔ {item.to_node_id}</td><td>{item.source_properties?.length_m}</td>
      </tr>)}
    </tbody></table></div>
  </div>;
  if (id === "scenario") return <div className="public-dataset-view">{heading}{controls}
    <div className="public-toolbar" aria-label="示例场景">
      {w.data.presets?.map((preset) => <button type="button" key={preset.id}
        onClick={() => { if (w.data?.default_case) w.chooseCase(w.data.default_case); w.chooseEdge(preset.id); }}>
        {preset.label}
      </button>)}
    </div>
    <label>给定退出管段<select value={w.edge} onChange={(e) => w.chooseEdge(e.target.value)}>
      <option value="">请选择</option>{w.data.edges?.map(({ id }) => <option key={id}>{id}</option>)}
    </select></label>
    <button type="button" disabled={!w.edge || !!w.working} onClick={() => void w.execute("topology")}>执行拓扑推演</button>
    {w.working === "topology" && <p role="status">正在计算拓扑连通性…</p>}
    {w.error && <p role="alert">{w.error}</p>}
    {w.result && <>
      {route.length > 0 && <p className="public-note">青色虚线 · 两端之间存在替代通路（图连通）</p>}
      {w.result.unreachable_nodes.length > 0 && <p className="public-note">红色覆盖 · {w.result.unreachable_nodes.length} 个新增断连节点、{branches.length} 条断连支网管段</p>}
      <button type="button" onClick={() => c.openWindow("impact")}>查看退出影响</button>
      <button type="button" disabled={!!w.working} onClick={() => void w.execute("run")}>另算稳态压力</button>
      {w.working === "run" && <p role="status">正在独立求解稳态压力，仍可播放拓扑（最多等待约 90 秒）。</p>}
      {w.result.hydraulics.reason && <p className="public-note">{w.result.hydraulics.reason}</p>}
    </>}
  </div>;
  if (id === "selected") return <div className="public-dataset-view">{heading}
    {edge && <dl className="gas-fields"><div><dt>管段编号</dt><dd>{edge.id}</dd></div>
      <div><dt>连接节点</dt><dd>{edge.from_node_id} ↔ {edge.to_node_id}</dd></div>
      <div><dt>长度（m）</dt><dd>{edge.source_properties?.length_m}</dd></div>
      <div><dt>内径（m）</dt><dd>{edge.source_properties?.diameter_m}</dd></div>
      <div><dt>发布流量（Sm³/h）</dt><dd>{number(flow?.flow_sm3h)}</dd></div>
      <div><dt>发布流速（m/s）</dt><dd>{number(flow?.velocity_m_s)}</dd></div></dl>}
    {node && !edge && <dl className="gas-fields"><div><dt>节点</dt><dd>{node.id}</dd></div>
      <div><dt>发布压力（bar abs）</dt><dd>{number(record?.pressure_bar)}</dd></div></dl>}
    {!node && !edge && <p>点击拓扑或目录中的对象查看属性。</p>}
  </div>;
  if (id === "timeline") return <GasTimelineTrack stages={stages} index={w.timeline.index} playing={w.timeline.playing}
    reducedMotion={c.reducedMotion} badge="图连通" note={stages.length ? "给定阶段 · 拓扑演练" : "选择管段并执行拓扑推演"}
    reset={() => w.dispatch({ type: "reset" })}
    select={(index) => w.dispatch({ type: "select", index, count: stages.length })}
    toggle={() => w.dispatch(w.timeline.playing ? { type: "pause" }
      : { type: "play", count: stages.length, reduced: c.reducedMotion })} />;
  if (id === "impact") return <div className="public-dataset-view">{heading}{controls}
    <button type="button" disabled={!!w.working || w.condition.data?.status !== "published" || w.data.solver_status !== "configured"}
      onClick={() => void w.executeBaseline()}>复核当前基准稳态</button>
    {w.working === "baseline" && <p role="status">正在复核基准压力及节点标准流量平衡…</p>}
    {w.error && <p role="alert">{w.error}</p>}
    {w.baseline && <details open><summary>本站独立复核 · {w.baseline.hydraulics.status === "ok" ? "通过" : "未通过"}</summary>
      {w.baseline.hydraulics.reason && <p>{w.baseline.hydraulics.reason}</p>}
      <p>压力最大差 {number(w.baseline.hydraulics.reference_max_pressure_error_bar, 6)} bar / 0.25 bar · 节点最大残差 {
        number(w.baseline.hydraulics.before_max_nodal_flow_error_sm3_s, 6)} Sm³/s / 0.03 Sm³/s</p>
      {w.baseline.hydraulics.before_pressure_bar && <PressureTable before={w.baseline.hydraulics.before_pressure_bar} selected={nodeId} />}
      <small>执行 {w.baseline.execution_id}</small></details>}
    {w.result && <><p>退出管段 {w.result.edge_id} · 新增断连节点 {w.result.unreachable_nodes.length} 个 · 断连模型需求 {number(w.result.disconnected_model_demand_sm3_s, 6)} Sm³/s</p>
      <p>执行 {w.result.execution_id} · {w.result.hydraulics.status === "not_requested" ? "拓扑计算已完成" : w.result.hydraulics.reason ?? "本站稳态重算"}</p>
      {route.length > 0 && <p>退出管段两端的替代通路（管段编号）{route.join(" → ")}。该路径用于说明图连通性。</p>}
      {branches.length > 0 && <p>断连支网管段（共 {branches.length} 条）{branches.join("、")}。</p>}
      {w.result.hydraulics.status === "ok" && <>
        <p>GasNetSim {w.result.hydraulics.solver_version} · 基准压力最大差 {number(w.result.hydraulics.reference_max_pressure_error_bar, 6)} bar</p>
        {edge && <p>选中管段 {edge.id} 的标准流量 · 基准 {number(w.result.hydraulics.before_flow_sm3_s?.[edge.id])}
          {" · 退出后 "}{number(w.result.hydraulics.after_flow_sm3_s?.[edge.id])} Sm³/s</p>}
        <PressureTable before={w.result.hydraulics.before_pressure_bar ?? {}}
          after={w.result.hydraulics.after_pressure_bar} selected={edge?.from_node_id ?? nodeId} /></>}</>}
    {w.condition.error && <p role="alert">{w.condition.error.message}</p>}
    {w.condition.status === "loading" && <p role="status">正在读取发布工况…</p>}
    {w.condition.data?.status === "pending_data" && <p>该小时未发布完整结果。</p>}
    {w.condition.data?.status === "published" && <details open={!w.result}><summary>作者发布结果 · 节点压力（bar abs）</summary>
      <p>选中管段可在右侧查看发布流量和流速，发布结果属于稳态模型。</p>
      <PressureTable before={Object.fromEntries(Object.entries(w.condition.data.nodes).flatMap(([key, row]) =>
        row.pressure_bar === null ? [] : [[key, row.pressure_bar]]))} selected={edge?.from_node_id ?? nodeId} /></details>}
    {!w.result && <p>比较管段退出影响时，从情景窗口执行拓扑推演。</p>}
  </div>;
  if (id === "tasks") return <div className="gas-task-list">
    <button type="button" onClick={c.openAssistantDialog}>打开西西里助手</button>
    <button type="button" onClick={() => c.openWindow("data")}>查找管段</button>
    <button type="button" onClick={() => c.openWindow("impact")}>查看退出影响</button>
    <button type="button" onClick={() => c.openWindow("evidence")}>查看样本来源</button>
  </div>;
  if (id === "layers") return <div className="gas-layer-toggles">
    {(["edges", "nodes", "impact", "labels"] as const).map((layer) => <label key={layer}>
      <input type="checkbox" checked={c.layers[layer]} onChange={() => c.toggleLayer(layer)} />
      {{ edges: "连接", nodes: "节点", impact: "断连覆盖", labels: "标签" }[layer]}
    </label>)}</div>;
  return <div className="public-dataset-view">{heading}<p>{w.version}</p><p>{w.data.geometry_note}</p>
    <p>失效概率、实际关阀、现场监测和居民停气影响需要独立数据。</p>
    {w.data.source_refs?.map((url) => <a key={url} href={url} target="_blank" rel="noreferrer">作者公开样本</a>)}</div>;
}

function PressureTable({ before, after, selected }: {
  before: Record<string, number>; after?: Record<string, number>; selected: string;
}) {
  const [search, setSearch] = useState("");
  const ids = Object.keys(before).filter((id) => id.includes(search.trim()));
  ids.sort((a, b) => a === selected ? -1 : b === selected ? 1 : Number(a) - Number(b));
  const featured = ids.slice(0, 12);
  const range = featured.flatMap((id) => after?.[id] === undefined ? [before[id]] : [before[id], after[id]]);
  const low = range.length ? Math.floor(Math.min(...range)) : 0;
  const high = range.length ? Math.max(low + 1, Math.ceil(Math.max(...range))) : 1;
  const scale = (value: number) => 102 + (value - low) * 452 / (high - low);
  return <><label>筛选节点<input value={search} onChange={(e) => setSearch(e.target.value)} /></label>
    {!!featured.length && <figure style={{ margin: "0.5rem 0" }}>
      <figcaption>前 {featured.length} 个结果的压力对比（bar abs） · 圆点为基准{after && "，菱形为退出后"}</figcaption>
      <svg viewBox={`0 0 600 ${featured.length * 18 + 28}`} width="100%" role="img" aria-label="节点绝对压力点图">
        {featured.map((id, index) => {
          const y = 14 + index * 18, x = scale(before[id]), other = after?.[id];
          return <g key={id}><title>{id} · {number(before[id])}{other === undefined ? "" : " → " + number(other)} bar abs</title>
            <text x="4" y={y + 4} fill="currentColor" fontSize="11">{id}</text>
            <line x1="102" x2="554" y1={y} y2={y} stroke="currentColor" opacity=".18" />
            {other !== undefined && <line x1={x} x2={scale(other)} y1={y} y2={y} stroke="#db9f43" />}
            <circle cx={x} cy={y} r="3" fill="#6eb4fd" />
            {other !== undefined && <rect width="6" height="6" x={scale(other) - 3} y={y - 3}
              fill="#db9f43" transform={`rotate(45 ${scale(other)} ${y})`} />}
          </g>;
        })}
        <text x="102" y={featured.length * 18 + 16} fill="currentColor" fontSize="11">{low}</text>
        <text x="554" y={featured.length * 18 + 16} fill="currentColor" fontSize="11" textAnchor="end">{high}</text>
      </svg>
    </figure>}
    <div className="public-table-wrap"><table><thead><tr><th>节点</th><th>基准 bar abs</th>
      {after && <><th>退出后 bar abs</th><th>变化 bar</th></>}</tr></thead>
      <tbody>{ids.map((id) => <tr key={id}><td>{id}{id === selected ? " · 已选" : ""}</td><td>{number(before[id])}</td>
        {after && <><td>{number(after[id])}</td><td>{after[id] === undefined ? "—" : number(after[id] - before[id])}</td></>}</tr>)}</tbody>
    </table></div></>;
}
