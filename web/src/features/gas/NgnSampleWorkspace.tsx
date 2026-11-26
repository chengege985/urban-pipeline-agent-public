
import { useEffect, useMemo, useReducer, useState } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasTimelineTrack, type GasStage } from "./GasPanels";
import { gasTimelineReducer, INITIAL_GAS_TIMELINE, type GasWindowId } from "./cockpitModel";
import type { NgnSampleModel, NgnSamplePoint, NgnSampleResult } from "./publicDatasets";

const value = (num?: number, digits = 4) => num === undefined ? "—" : num.toFixed(digits);
const periodLabel = (period: string) => period === "base" ? "原始负荷" : `工况 ${period}`;

export function useNgnSample() {
  const c = useGasCockpit();
  const data = c.activeDatasetId === "ngn_distribution_example" && c.publicOverview.data?.dataset_id === c.activeDatasetId
    ? c.publicOverview.data : null;
  const version = data?.data_version;
  const [edge, setEdge] = useState("");
  const [period, setPeriod] = useState("base");
  const [result, setResult] = useState<NgnSampleResult>();
  const [trend, setTrend] = useState<NgnSampleResult>();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const [request, setRequest] = useState<AbortController>();
  const [timeline, dispatch] = useReducer(gasTimelineReducer, INITIAL_GAS_TIMELINE);
  const before = result?.before;
  const after = result?.after;
  const stages: GasStage[] = before && after ? [
    { id: "baseline", time: "阶段 1", label: "基准连通", detail: `${before.reachable_nodes.length} 个可达节点` },
    { id: "withdrawn", time: "阶段 2", label: "给定管段退出", detail: `${after.reachable_nodes.length} 个可达节点 · ${
      after.unavailable_sinks.filter(id => !before.unavailable_sinks.includes(id)).length} 个新增断连负荷点` },
    { id: "restored", time: "阶段 3", label: "恢复基准连接", detail: `回看基准 · ${before.reachable_nodes.length} 个可达节点` },
  ] : [];
  const currentStage = stages[timeline.index];
  const withdrawn = currentStage?.id === "withdrawn";
  const current = withdrawn ? after : before;
  const timelineVisible = !c.hidden.has("timeline");
  useEffect(() => {
    if (!timeline.playing) return;
    if (c.reducedMotion || !timelineVisible || stages.length < 2) {
      dispatch({ type: "pause" });
      return;
    }
    const timer = window.setInterval(() => dispatch({ type: "tick", count: stages.length }), 1600);
    return () => window.clearInterval(timer);
  }, [c.reducedMotion, stages.length, timeline.playing, timelineVisible]);

  useEffect(() => () => request?.abort(), [request]);
  useEffect(() => {
    setEdge(""); setPeriod("base"); setResult(undefined); setTrend(undefined);
    setPending(false); setError(undefined); setRequest(undefined); dispatch({ type: "reset" });
  }, [version]);
  function clearRun() {
    request?.abort();
    setResult(undefined); setError(undefined); setPending(false); setRequest(undefined);
    dispatch({ type: "reset" });
  }
  function selectPipe(id: string) {
    if (id !== edge) { clearRun(); setEdge(id); }
  }
  function selectPeriod(id: string) {
    if (id !== period) { clearRun(); setPeriod(id); }
  }
  async function execute(runTrend: boolean, outage = false) {
    if (!version || pending) return;
    const controller = new AbortController();
    setRequest(controller); setPending(true); setError(undefined);
    if (runTrend) setTrend(undefined);
    else { setResult(undefined); dispatch({ type: "reset" }); }
    try {
      const response = await c.publicApi.ngnSampleRun(version, runTrend ? "base" : period,
        outage ? edge : "", runTrend, controller.signal);
      if (controller.signal.aborted) return;
      if (runTrend) setTrend(response);
      else {
        setResult(response);
        if (response.status === "ok" && response.before && response.after) c.openWindow("timeline");
      }
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (!controller.signal.aborted) setPending(false);
    }
  }
  return { data, edge, period, selectPipe, selectPeriod, result, trend, pending, error, execute,
    current, currentStage, withdrawn, stages, timeline, dispatch };
}

function Network({ model, w }: { model: NgnSampleModel; w: ReturnType<typeof useNgnSample> }) {
  const c = useGasCockpit();
  const paths = useMemo(() => {
    const xy = model.pipes.flatMap(p => p.geom.flat());
    const minX = Math.min(...xy.map(p => p[0])), minY = Math.min(...xy.map(p => p[1]));
    const span = Math.max(Math.max(...xy.map(p => p[0])) - minX, Math.max(...xy.map(p => p[1])) - minY);
    const project = ([x, y]: NgnSamplePoint) => [24 + (x - minX) * 552 / span, 576 - (y - minY) * 552 / span];
    return { project, lines: model.pipes.map(p => ({ pipe: p, d: p.geom.map(part =>
      part.map((point, i) => (i ? "L" : "M") + project(point).join(",")).join(" ")).join(" ") })) };
  }, [model]);
  const reachable = w.current ? new Set(w.current.reachable_nodes) : undefined;
  const outage = w.withdrawn ? w.result?.task.edge_id : undefined;
  return <svg className="gas-ngn-model-map" viewBox="0 0 600 600" aria-label="NGN 配气样例管线图">
    {c.layers.edges && paths.lines.map(({ pipe, d }) => {
      const selected = w.edge === pipe.name;
      const disabled = !pipe.in_service || (c.layers.impact && (outage === pipe.name ||
        (reachable && (!reachable.has(pipe.from_junction) || !reachable.has(pipe.to_junction)))));
      return <g key={pipe.name} data-run-pipe={pipe.name} data-stage-status={disabled ? "unavailable" : "active"}>
        <path d={d} fill="none" stroke={disabled ? "#f18165" : selected ? "#f2bc60" : "#55aaf6"} strokeWidth={selected ? 4 : 2}
          strokeDasharray={disabled ? "5 3" : undefined} vectorEffect="non-scaling-stroke" pointerEvents="none" />
        <path d={d} fill="none" stroke="transparent" strokeWidth="10" vectorEffect="non-scaling-stroke"
          role="button" tabIndex={0} aria-label={pipe.name} aria-pressed={selected} onClick={() => w.selectPipe(pipe.name)}
          onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); w.selectPipe(pipe.name); } }}>
          <title>{pipe.name}</title></path>
      </g>;
    })}
    {c.layers.nodes && model.sinks.map(s => { const [x, y] = paths.project(s.geom[0]); return <circle key={s.name} cx={x} cy={y} r="2.6"
      data-load-point={s.name} data-stage-status={w.current?.unavailable_sinks.includes(s.name) ? "unavailable" : w.current ? "reachable" : "unknown"}
      fill={c.layers.impact && w.current?.unavailable_sinks.includes(s.name) ? "#f18165" : "#5ee1be"} pointerEvents="none"><title>{s.name}</title></circle>; })}
    {c.layers.nodes && model.sources.map(s => { const [x, y] = paths.project(s.geom[0]); return <rect key={s.name} x={x - 4} y={y - 4} width="8"
      height="8" fill="#c9b2ff" pointerEvents="none"><title>{s.name}</title></rect>; })}
  </svg>;
}

function Comparison({ model, w }: { model: NgnSampleModel; w: ReturnType<typeof useNgnSample> }) {
  const result = w.result;
  const [sink, setSink] = useState("");
  if (!result?.before || !w.current) return <p>执行当前工况后查看模型压力、管段流量与负荷点。</p>;
  const before = result.before, after = w.withdrawn ? result.after : undefined;
  const current = w.current;
  const impact = after?.unavailable_sinks.filter(id => !before.unavailable_sinks.includes(id));
  const path = current.sink_paths[sink];
  return <div className="gas-ngn-stage-details" data-testid="ngn-stage-details">
    {w.currentStage && <p role="status"><strong>当前阶段 · {w.currentStage.label}</strong></p>}
    <div className="gas-ngn-result-strip"><strong>最大节点质量残差</strong>
      <span>{before.max_nodal_mass_error_kg_s.toExponential(2)} kg/s
        {after ? ` → ${after.max_nodal_mass_error_kg_s.toExponential(2)} kg/s` : ""}</span></div>
    <p>当前不可达负荷点 <strong>{current.unavailable_sinks.length}</strong> · {current.unavailable_sinks.join("、") || "无"}</p>
    {after && <p>新增不可达负荷点 <strong>{impact?.length}</strong> · {impact?.join("、") || "无"}</p>}
    <details open><summary>节点压力（bar 表压）</summary><div className="gas-ngn-table">
      <table><thead><tr><th>节点</th><th>基准</th>{after && <><th>退出后</th><th>差值</th></>}</tr></thead>
        <tbody>{model.nodes.map(n => {
          const a = before.pressure_bar_g[n.name], b = after?.pressure_bar_g[n.name];
          return <tr key={n.name}><th>{n.name}</th><td>{value(a)}</td>{after && <>
            <td>{value(b)}</td><td>{value(a !== undefined && b !== undefined ? b - a : undefined)}</td></>}</tr>;
        })}</tbody></table></div></details>
    <details><summary>管段质量流量（kg/s，沿模型端点方向为正）</summary><div className="gas-ngn-table">
      <table><thead><tr><th>管段</th><th>基准</th>{after && <th>退出后</th>}</tr></thead>
        <tbody>{model.pipes.map(p => <tr key={p.name}><th>{p.name}</th><td>{value(before.flow_kg_s[p.name], 6)}</td>
          {after && <td>{value(after.flow_kg_s[p.name], 6)}</td>}</tr>)}</tbody></table></div></details>
    <label>查看负荷点拓扑证据 <select value={sink} onChange={e => setSink(e.target.value)}>
      <option value="">选择负荷点</option>{model.sinks.map(s => <option key={s.name}>{s.name}</option>)}</select></label>
    {sink && (path ? <p>气源 {path.source} · {path.nodes.join(" → ")}<br />管段 {path.pipes.join(" → ") || "同节点"}</p>
      : <p>该负荷点无源可达，未生成路径。</p>)}
    <p><small>{result.solver} · {result.friction_model} · {result.gas} · {result.execution_id}</small></p>
    <a download="ngn-sample-result.json" href={"data:application/json;charset=utf-8," + encodeURIComponent(JSON.stringify(result))}>下载计算证据</a>
  </div>;
}

function Trend({ model, w }: { model: NgnSampleModel; w: ReturnType<typeof useNgnSample> }) {
  const [node, setNode] = useState("");
  const id = node || model.sinks[0]?.junction;
  const points = Object.entries(w.trend?.series ?? {}).map(([period, state]) => ({
    period, pressure: state.pressure_bar_g?.[id], reason: state.reason,
  }));
  const max = Math.max(1, ...points.map(p => p.pressure ?? 0));
  return <>
    <h3>各工况基准压力</h3><p>10 个独立计算工况，横轴为样例编号。节点压力以 bar 表压显示。</p>
    <button type="button" disabled={w.pending || w.data?.solver_status !== "configured"} onClick={() => void w.execute(true)}>计算十个基准工况</button>
    {w.pending && <p role="status">正在求解…</p>}{(w.error || w.trend?.reason) && <p role="alert">{w.error || w.trend?.reason}</p>}
    {points.length > 0 && <>
      <label>节点 <select value={id} onChange={e => setNode(e.target.value)}>
        {model.nodes.map(n => <option key={n.name}>{n.name}</option>)}</select></label>
      <div className="gas-ngn-periods" aria-label="各工况基准压力">{points.map(p =>
        <div key={p.period} title={p.reason ?? `工况 ${p.period} · ${value(p.pressure)} bar 表压`}>
          <span>{value(p.pressure, 3)}</span><i style={{ height: `${(p.pressure ?? 0) / max * 84}px` }} /><small>{p.period}</small>
        </div>)}</div>
      {points.filter(p => p.reason).map(p => <p key={p.period}>工况 {p.period} · {p.reason}</p>)}
    </>}
  </>;
}

export function NgnSampleWindow({ id, workspace: w, mapMode }: {
  id: GasWindowId; workspace: ReturnType<typeof useNgnSample>; mapMode: "map" | "records";
}) {
  const c = useGasCockpit();
  const data = w.data, model = data?.model;
  if (!data || !model) return <p>正在读取 NGN 配气样例…</p>;
  const pipe = model.pipes.find(p => p.name === w.edge);
  if (id === "topology") return <div className="gas-ngn-network">
    {mapMode === "map" ? <Network model={model} w={w} /> : <div className="gas-ngn-table">
      <table><thead><tr><th>管段</th><th>起点</th><th>终点</th><th>管材</th><th>内径（mm）</th></tr></thead>
        <tbody>{model.pipes.map(p => <tr key={p.name}><th><button type="button" aria-pressed={w.edge === p.name} onClick={() => w.selectPipe(p.name)}>{p.name}</button></th>
          <td>{p.from_junction}</td><td>{p.to_junction}</td><td>{p.MATERIAL || "—"}</td><td>{value(p.diameter_m * 1000, 1)}</td></tr>)}</tbody></table></div>}
    <div className="gas-ngn-legend" aria-label="配气样例图例">
      <span><i style={{ background: "#55aaf6" }} />在役</span>
      <span><i style={{ background: "#f2bc60" }} />选中</span>
      <span><i className="gas-ngn-dashed" />退出或不可达</span>
      <span><i className="gas-ngn-square" />气源</span>
      <span><i className="gas-ngn-dot" />模型负荷</span>
      <small>EPSG:27700 · 来源几何</small>
    </div>
  </div>;
  if (id === "scenario") return <div className="gas-ngn-controls">
    <h3>样例工况</h3>
    <label>模型负荷 <select value={w.period} onChange={e => w.selectPeriod(e.target.value)}>
      {["base", ...(data.periods ?? [])].map(p => <option key={p} value={p}>{periodLabel(p)}</option>)}</select></label>
    <label>退出管段 <select value={w.edge} onChange={e => w.selectPipe(e.target.value)}>
      <option value="">选择管段</option>{model.pipes.map(p => <option key={p.name}>{p.name}</option>)}</select></label>
    <button type="button" disabled={w.pending || data.solver_status !== "configured"} onClick={() => void w.execute(false)}>计算基准稳态</button>
    <button type="button" disabled={w.pending || !pipe?.in_service || data.solver_status !== "configured"}
      onClick={() => void w.execute(false, true)}>计算管段退出</button>
    {data.solver_status !== "configured" && <p>本机未配置样例求解器。</p>}
    {w.pending && <p role="status">正在求解…</p>}
    {(w.error || w.result?.reason) && <p role="alert">{w.error || w.result?.reason}</p>}
  </div>;
  if (id === "selected") return <>
    <h3>样例管段属性</h3>
    {w.currentStage && <p className="public-note">当前阶段 · {w.currentStage.label}</p>}{pipe ? <dl className="gas-fields">
      <div><dt>管段</dt><dd>{pipe.name}</dd></div>
      <div><dt>模型端点</dt><dd>{pipe.from_junction} → {pipe.to_junction}</dd></div>
      <div><dt>管材</dt><dd>{pipe.MATERIAL || "—"}</dd></div>
      <div><dt>模型长度</dt><dd>{value(pipe.length_km, 3)} km</dd></div>
      <div><dt>模型内径</dt><dd>{value(pipe.diameter_m * 1000, 1)} mm</dd></div>
      <div><dt>当前流量</dt><dd>{value(w.current?.flow_kg_s[pipe.name], 6)} kg/s</dd></div>
      {w.current && <div><dt>当前端点压力</dt><dd>{value(w.current.pressure_bar_g[pipe.from_junction])}
        {" / "}{value(w.current.pressure_bar_g[pipe.to_junction])} bar 表压</dd></div>}
    </dl> : <p>点击地图管线或从工况窗口选择管段。</p>}
    <h3>样例气源边界</h3>{model.sources.map(s => <p key={s.name}>{s.name} · {s.junction} · {s.p_bar} bar 表压</p>)}
  </>;
  if (id === "timeline") return <div className="gas-ngn-network">
    <GasTimelineTrack stages={w.stages} index={w.timeline.index} playing={w.timeline.playing}
      reducedMotion={c.reducedMotion} badge="稳态工况" note={w.stages.length
        ? "恢复阶段回看基准工况" : "选择工况与管段并计算管段退出"}
      reset={() => w.dispatch({ type: "reset" })}
      select={index => w.dispatch({ type: "select", index, count: w.stages.length })}
      toggle={() => w.dispatch(w.timeline.playing ? { type: "pause" }
        : { type: "play", count: w.stages.length, reduced: c.reducedMotion })} />
    <h3>{periodLabel(w.result?.task.period ?? w.period)} · 压力与流量</h3>
    <Comparison key={w.result?.execution_id} model={model} w={w}/>
  </div>;
  if (id === "impact") return <Trend model={model} w={w}/>;
  if (id === "layers") return <div className="gas-layer-toggles">
    {(["edges", "nodes", "impact"] as const).map(layer => <label key={layer}>
      <input type="checkbox" checked={c.layers[layer]} onChange={() => c.toggleLayer(layer)} />
      {{ edges: "管段", nodes: "气源与负荷点", impact: "退出与断连覆盖" }[layer]}</label>)}</div>;
  if (id === "tasks") return <div className="gas-ngn-controls"><button type="button" onClick={() => c.openWindow("impact")}>查看工况压力趋势</button>
    <button type="button" onClick={() => c.openWindow("evidence")}>查看数据与计算口径</button></div>;
  return <><h3>样例来源与计算口径</h3>{data.limitations?.map(line => <p key={line}>{line}</p>)}
    <p>直接读取模型端点与原始在役状态。10 个工况的体积流量以甲烷、293.15 K 为换算假设。
      压力为表压，流量为质量流量。退出后无源可达区域不参与求解。质量守恒校验上限 10⁻⁶ kg/s。</p>
    {data.source_refs?.map(url => <p key={url}><a href={url} target="_blank" rel="noreferrer">{url.includes("github.com/") ? "固定版本样例文件" : "作者教程"}</a></p>)}
    <dl className="gas-fields">{Object.entries(data.input_sha256 ?? {}).map(([file, hash]) => <div key={file}><dt>{file}</dt><dd>{hash}</dd></div>)}</dl></>;
}
