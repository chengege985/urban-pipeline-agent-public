import { GasGisView } from "./GasGisView";
import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { DEFAULT_VIEWPORT, fitGasMapping, zoomGasViewport } from "./cockpitModel";
import { GAS_NODE_LABELS } from "./assetRecords";
import { GasReadError } from "./GasDetails";
import type { GasDrillComparison, GasSyntheticScenario } from "../../shared/contracts/platform";

export type TopologyNode = {
  node_id: string; node_type: keyof typeof GAS_NODE_LABELS | "consumer";
  layout: { x: number; y: number }; name?: string;
};
export type TopologyEdge = {
  edge_type?: string; edge_id: string; pipe_id: string; from_node_id: string; to_node_id: string };
export type BenchmarkMap = {
  nodes: TopologyNode[]; edges: TopologyEdge[]; version: string;
  coordinateLabel: string;
  focusEdgeId?: string;
  focusRevision: number;
  focusNodeIds?: string[];
  alternateEdgeIds?: string[];
  disabledEdges: string[]; unreachableNodes: string[]; possibleUnreachableNodes: string[];
  selectedId: string | null; onSelect(id: string): void;
};
export function GasTopologyView({ benchmark, drill, comparison, highlighted = "after" }: {
  benchmark?: BenchmarkMap; drill?: GasSyntheticScenario; comparison?: GasDrillComparison; highlighted?: "before" | "after";
} = {}) {
  const c = useGasCockpit();
  const value = c.caseView.data;
  const graph = benchmark ?? value;
  const svg = useRef<SVGSVGElement>(null);
  const [viewport, setViewport] = useState(DEFAULT_VIEWPORT);
  const pan = useRef<{ x: number; y: number; startX: number; startY: number } | null>(null);
  const nodes = useMemo(() => new Map(graph?.nodes.map((node) => [node.node_id, node]) ?? []), [graph?.nodes]);
  const active = c.timelineEvents[c.timeline.index];
  const current = comparison ? comparison[highlighted].report?.scenario : drill;
  const pinned = current ?? comparison?.before.report?.scenario ?? comparison?.after.report?.scenario;
  const showDrill = comparison !== undefined || current?.transition.current_status === "analyzed";
  const ready = showDrill && Boolean(value) && pinned?.source.network_content_sha256 === value?.evidence.network_content_sha256;
  const analysis = ready ? current?.analysis : undefined;
  const impact = analysis?.impact;
  const drillGraph = analysis?.graph;
  const excluded = new Set(drillGraph?.excluded_edge_ids ?? (impact ? [impact.fault_edge_id] : []));
  const alternate = new Set(drillGraph?.alternative_path_edge_ids);
  const sourcedParts = drillGraph?.scenario.components.filter(group => group.source_ids.length) ?? [];
  const partitions = new Map(sourcedParts.length > 1 ? sourcedParts.flatMap((group, index) =>
    group.node_ids.map(id => [id, index + 1] as const)) : []);
  const drillEdges = new Set(impact?.affected_edge_ids);
  const unreachable = new Set(benchmark?.unreachableNodes ?? (showDrill ? impact?.newly_unreachable_node_ids : active?.current_unreachable_node_ids) ?? []);
  const selectedEdges = new Set(benchmark ? [benchmark.selectedId] : c.mapping.edgeIds);
  const selectedNodes = new Set(benchmark ? [benchmark.selectedId] : c.mapping.nodeIds);
  const linkedEdges = new Set(benchmark ? [] : c.eventMapping.edgeIds);
  const linkedNodes = new Set(benchmark ? [] : c.eventMapping.nodeIds);
  const isolationIndex = c.timelineEvents.findIndex((item) => item.kind === "given_isolation");
  const replayFaults = value ? isolationIndex >= 0 && c.timeline.index >= isolationIndex ? value.isolation_plan.disabled_edge_ids : value.scenario.fault_edge_ids : [];
  const disabledEdges = benchmark ? new Set(benchmark.disabledEdges) : showDrill ? excluded : new Set(replayFaults);
  const signature = c.mapping.edgeIds.join("|") + ":" + c.mapping.nodeIds.join("|");
  const context = comparison ? comparison.comparison_sha256 + "/" + highlighted : analysis?.analysis_sha256 ?? "";
  const previousContext = useRef("");
  useEffect(() => {
    if (!value) return;
    const changed = context !== previousContext.current;
    previousContext.current = context;
    if (changed && showDrill) {
      setViewport(impact && sourcedParts.length < 2
        ? fitGasMapping(value, { edgeIds: [...excluded, ...alternate], nodeIds: impact.newly_unreachable_node_ids, unmappedIds: [] })
        : DEFAULT_VIEWPORT);
    } else {
      setViewport(signature === ":" ? DEFAULT_VIEWPORT : fitGasMapping(value, c.mapping));
    }
  }, [value, signature, c.focusRevision, context]);
  const benchmarkSelection = benchmark?.selectedId;
  const benchmarkNodes = benchmark?.nodes;
  const benchmarkEdges = benchmark?.edges;
  const focusEdgeId = benchmark?.focusEdgeId;
  const focusRevision = benchmark?.focusRevision ?? 0;
  const focusNodeIds = benchmark?.focusNodeIds;
  const dense = Boolean(benchmark);
  const sourceRadius = dense ? 3.4 : 8;
  useEffect(() => {
    if (focusRevision > 0 && benchmarkSelection && benchmarkNodes && benchmarkEdges) {
      setViewport(fitGasMapping({ nodes: benchmarkNodes, edges: benchmarkEdges },
        { edgeIds: [benchmarkSelection], nodeIds: [benchmarkSelection, ...(focusNodeIds ?? [])], unmappedIds: [] }));
    }
  }, [focusRevision, benchmarkNodes, benchmarkEdges]);
  const possibleUnreachable = new Set(benchmark?.possibleUnreachableNodes);
  const impactEdges = benchmark?.edges.filter((edge) => edge.edge_id !== focusEdgeId
    && possibleUnreachable.has(edge.from_node_id) && possibleUnreachable.has(edge.to_node_id)) ?? [];
  const faultEdge = benchmark?.edges.find((edge) => edge.edge_id === focusEdgeId);
  const alternateEdges = new Set(benchmark?.alternateEdgeIds);
  const renderImpactEdge = (edge: TopologyEdge, kind: "fault" | "branch" | "alternate", highlighted: boolean) => {
    const from = nodes.get(edge.from_node_id), to = nodes.get(edge.to_node_id);
    if (!from || !to) return null;
    return <line key={edge.edge_id} className="gas-stage-line" data-stage-kind={kind} data-active={highlighted}
      x1={from.layout.x} y1={from.layout.y} x2={to.layout.x} y2={to.layout.y} />;
  };
  useEffect(() => {
    const element = svg.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      const matrix = element.getScreenCTM();
      const point = matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : { x: 500, y: 320 };
      setViewport((current) => zoomGasViewport(current, event.deltaY > 0 ? .92 : 1.08, point));
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => element.removeEventListener("wheel", wheel);
  }, [Boolean(graph), c.route.gasMap, Boolean(benchmark)]);
  const point = (event: ReactPointerEvent<SVGSVGElement>) => {
    const matrix = event.currentTarget.getScreenCTM();
    return matrix ? new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse()) : { x: event.clientX, y: event.clientY };
  };
  if (!benchmark && c.route.gasMap === "gis") return <GasGisView />;
  return <div className="gas-topology-shell" data-testid="gas-topology-view"
    data-benchmark-density={benchmark ? "network" : undefined}
    data-case-id={benchmark?.version ?? value?.case_id ?? "unknown"}>
    <div className="gas-map-toolbar"><span className="gas-map-toolbar-title"> {benchmark ? benchmark.nodes.length + " 拓扑节点 / " + benchmark.edges.length + " 条连接" : value ? `${value.counts.nodes} 拓扑节点 / ${value.counts.pipes} 管段` : "拓扑数据尚未就绪"}<span className={`gas-mode-pill is-${c.mode}`}>{benchmark ? "研究基准 · 图可达" : ({ overview: "全网总览", risk: "风险聚焦 · 模型未接入", emergency: "隔离与图影响视角" })[c.mode]}</span></span>
      <span className="gas-map-toolbar-actions"><button type="button" aria-label={benchmark ? "放大基准拓扑" : "放大合成拓扑"} onClick={() => setViewport((current) => zoomGasViewport(current, 1.15))} disabled={!graph}>＋</button>
        <button type="button" aria-label={benchmark ? "缩小基准拓扑" : "缩小合成拓扑"} onClick={() => setViewport((current) => zoomGasViewport(current, .85))} disabled={!graph}>－</button>
        <button type="button" onClick={() => setViewport(DEFAULT_VIEWPORT)} disabled={!graph}>适配视图</button></span></div>
    <div className="gas-map-legend"><span><i className="gas-swatch-line" />{benchmark ? "基准连接" : "候选管段"}</span><span><i className="gas-swatch-line gas-swatch-fault" />{benchmark ? "给定退出" : "给定故障/隔离"}</span>
      <span><i className="gas-swatch-dot source" />气源节点</span>{benchmark ? <span>○ 出口 / 普通节点 · 红圈不可达</span> : <><span><i className="gas-swatch-diamond" />阀门节点</span><span>⊙ 调压 / ○ 普通节点</span></>}<span className="gas-legend-note">{benchmark?.coordinateLabel ?? "schematic / px · 非重庆GIS"}</span></div>
    {showDrill && <div className="gas-drill-map-caption" role="status" data-testid="gas-drill-overlay" data-ready={ready}
      data-comparison-sha={comparison?.comparison_sha256} data-comparison-side={comparison ? highlighted : undefined}
      data-analysis-sha={analysis?.analysis_sha256} data-revision={current?.transition.revision}>
      {ready ? <>DEMO · {comparison ? (highlighted === "before" ? "对比前侧 · " : "对比后侧 · ") : ""}
        {current ? <>演练 r{current.transition.revision} · 虚线为排除图边。
          {alternate.size > 0 ? " 点线为替代路径。" : ""}
          {sourcedParts.length > 1 ? " 分区描边区分独立气源连通组。" : ""}</> : "同源基线"}</>
        : <>当前拓扑与演练证据尚未对齐，未叠加分析结果。</>}
    </div>}
    <div className="gas-map-canvas" data-testid="gas-central-view">
      {c.caseView.status === "loading" ? <div className="gas-map-state" role="status">正在读取同一份GAS-M0案例…</div> : null}
      {c.caseView.error ? <div className="gas-map-state"><GasReadError error={c.caseView.error} retry={c.caseView.retry} label="重试合成拓扑" /></div> : null}
      {graph ? <svg ref={svg} viewBox="0 0 1000 640" role="img" aria-label={benchmark ? "基准网络拓扑图" : "燃气合成管网拓扑图"}
        onPointerDown={(event) => { const target = event.target as Element; if (target !== event.currentTarget && !target.classList.contains("gas-map-background")) return;
          const start = point(event); pan.current = { x: viewport.x, y: viewport.y, startX: start.x, startY: start.y }; event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={(event) => { if (pan.current) { const next = point(event), start = pan.current; setViewport((current) => ({ ...current, x: start.x + next.x - start.startX, y: start.y + next.y - start.startY })); } }}
        onPointerUp={() => { pan.current = null; }} onPointerCancel={() => { pan.current = null; }}>
        <rect className="gas-map-background" width="1000" height="640" fill="var(--gas-surface-0)" />
        <g data-testid="gas-viewport-transform" data-zoom={viewport.zoom} transform={`translate(${viewport.x} ${viewport.y}) scale(${viewport.zoom})`}>
          {c.layers.edges && graph.edges.map((edge) => { const from = nodes.get(edge.from_node_id), to = nodes.get(edge.to_node_id); if (!from || !to) return null;
            const select = () => benchmark ? benchmark.onSelect(edge.edge_id) : c.selectTopologyAsset(edge.pipe_id);
            const selected = selectedEdges.has(edge.edge_id), linked = linkedEdges.has(edge.edge_id) || edge.edge_id === focusEdgeId;
            const drillState = excluded.has(edge.edge_id) ? "excluded" : alternate.has(edge.edge_id) ? "alternate" : drillEdges.has(edge.edge_id) ? "affected" : undefined;
            const part = partitions.get(edge.from_node_id);
            return <g key={edge.edge_id} data-edge-id={edge.edge_id} data-asset-id={edge.pipe_id} data-edge-kind={"edge_type" in edge ? edge.edge_type : undefined} data-drill-state={drillState}
              data-drill-component={part === partitions.get(edge.to_node_id) ? part : undefined}
              data-asset-focus={selected ? "selected" : linked ? "related" : undefined}
              role="button" tabIndex={0} aria-label={`管段资产 ${edge.pipe_id}`} onClick={select}
              onKeyDown={(event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); } }}>
              <line className="gas-topology-edge-hit" x1={from.layout.x} y1={from.layout.y} x2={to.layout.x} y2={to.layout.y} />
              {selected ? <line className="gas-topology-edge-halo" x1={from.layout.x} y1={from.layout.y} x2={to.layout.x} y2={to.layout.y} /> : null}
              <line className={`gas-topology-edge${!benchmark && disabledEdges.has(edge.edge_id) ? " is-disabled" : ""}${selected ? " is-selected" : linked ? " is-associated" : ""}`} x1={from.layout.x} y1={from.layout.y} x2={to.layout.x} y2={to.layout.y} />
              {c.layers.labels && edge.edge_id === focusEdgeId ? <text className="gas-focus-edge-label"
                x={(from.layout.x + to.layout.x) / 2} y={(from.layout.y + to.layout.y) / 2 - 13} textAnchor="middle">{edge.edge_id}</text> : null}
              <title>{edge.pipe_id} / {edge.edge_id} · {benchmark ? "基准连接" : "合成图关系"}</title></g>; })}
          {benchmark ? <g className="gas-stage-overlays" aria-hidden="true">
            {c.layers.edges && c.layers.impact && graph.edges.filter((edge) => alternateEdges.has(edge.edge_id))
              .map((edge) => renderImpactEdge(edge, "alternate", disabledEdges.size > 0))}
            {c.layers.edges && c.layers.impact && impactEdges.map((edge) => renderImpactEdge(edge, "branch",
              disabledEdges.size > 0 && unreachable.has(edge.from_node_id) && unreachable.has(edge.to_node_id)))}
            {c.layers.edges && faultEdge && renderImpactEdge(faultEdge, "fault", disabledEdges.has(faultEdge.edge_id))}
            {c.layers.impact && [...possibleUnreachable].map((id) => {
              const node = nodes.get(id);
              return node ? <circle key={id} className="gas-impact-halo gas-stage-ring"
                data-active={unreachable.has(id)} cx={node.layout.x} cy={node.layout.y} r="10" /> : null;
            })}
          </g> : c.layers.impact && [...unreachable].map((id) => {
            const node = nodes.get(id);
            return node ? <circle key={id} cx={node.layout.x} cy={node.layout.y} r="17" className="gas-impact-halo" /> : null;
          })}
          {c.layers.nodes && graph.nodes.map((node) => { const valve = value?.valves.find((item) => item.node_id === node.node_id);
            const select = () => benchmark ? benchmark.onSelect(node.node_id) : c.selectTopologyAsset(valve?.valve_id ?? node.node_id);
            const selected = selectedNodes.has(node.node_id), linked = linkedNodes.has(node.node_id);
            return <g key={node.node_id} data-node-id={node.node_id} data-asset-id={valve?.valve_id ?? node.node_id}
              data-drill-state={impact && unreachable.has(node.node_id) ? "unreachable" : undefined}
              data-drill-component={partitions.get(node.node_id)}
              data-asset-focus={selected ? "selected" : linked ? "related" : undefined}
              className={`gas-topology-node gas-node-${node.node_type}${unreachable.has(node.node_id) ? " is-unreachable" : ""}${selected ? " is-selected" : linked ? " is-associated" : ""}`}
              role="button" tabIndex={0} aria-label={`${(node.node_type === "consumer" ? "模型建筑接入点" : GAS_NODE_LABELS[node.node_type])} ${node.node_id}`} onClick={select}
              onKeyDown={(event) => { if (["Enter", " "].includes(event.key)) { event.preventDefault(); select(); } }}>
              {node.node_type === "source" ? <rect x={node.layout.x - sourceRadius} y={node.layout.y - sourceRadius} width={sourceRadius * 2} height={sourceRadius * 2} rx={dense ? 1.5 : 4} />
                : node.node_type === "valve" ? <rect x={node.layout.x - 6} y={node.layout.y - 6} width="12" height="12" transform={`rotate(45 ${node.layout.x} ${node.layout.y})`} />
                : node.node_type === "regulator" ? <><circle cx={node.layout.x} cy={node.layout.y} r="6.5" /><circle cx={node.layout.x} cy={node.layout.y} r="2.2" className="gas-node-core" /></>
                : <circle cx={node.layout.x} cy={node.layout.y} r={dense && !selected ? 2 : 4} />}
              {c.layers.labels && (selected || (!benchmark && node.node_type === "source")) ? <text x={node.layout.x} y={node.layout.y - 14} textAnchor="middle">{node.name ?? node.node_id}</text> : null}
              <title>{(node.node_type === "consumer" ? "模型建筑接入点" : GAS_NODE_LABELS[node.node_type])} {node.node_id} · {benchmark ? "基准节点" : "合成坐标，不是地理位置"}</title></g>; })}
        </g>
      </svg> : null}
      <div className="gas-map-gesture-hint">滚轮缩放 · 空白处拖动 · {benchmark ? "点击连接或节点查看来源详情" : "点击管段/阀门读真实详情"}</div>
    </div>
    <div className="gas-map-bottombar"><div className="gas-layer-toggles" aria-label="拓扑图层">{(["edges", "nodes", "impact", "labels"] as const).map((layer) => <label key={layer}><input type="checkbox" checked={c.layers[layer]} onChange={() => c.toggleLayer(layer)} />{({ edges: "管段", nodes: "节点", impact: "图不可达覆盖", labels: "关键标签" })[layer]}</label>)}</div>
      <p className="gas-map-selection" role="status" data-testid="gas-map-selection">
        {benchmark ? (benchmark.disabledEdges.length ? "当前阶段：给定管道退出" : "当前阶段：全网连接") : <>当前高亮 {c.mapping.edgeIds.length} 条管段、{c.mapping.nodeIds.length} 个节点。{c.mapping.unmappedIds.length ? `${c.mapping.unmappedIds.length} 项无拓扑映射。` : "仅为合成拓扑定位。"}
        {c.asset.data && c.event.data ? " 浅色轮廓标记同一事件的其他关联对象。" : null}</>}
      </p></div>
  </div>;
}
