import { useMemo } from "react";
import { PRODUCT_EDITION } from "../../shared/config/productEdition";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasAssetDetails, GasEventAssetGroups, GasEventDetails, GasReadError } from "./GasDetails";
import { GasSelectedObservation } from "./GasSelectedObservation";
import { gasRecordIdentity } from "./assetRecords";
import { GasImpactPanel } from "./GasPanels";
import { GasAssetDirectory } from "./GasAssetDirectory";
import { sortEventsByPriority, summarizeRiskReadiness } from "./cockpitModel";
import { summarizeDistrictReadiness } from "./chongqingDistricts";
import { eventKinds, eventStates } from "./presentation";

export function GasRiskPanel() {
  const c = useGasCockpit();
  const readiness = useMemo(
    () => summarizeRiskReadiness(c.caseView.data, c.events.data),
    [c.caseView.data, c.events.data],
  );
  const priorityEvents = useMemo(
    () => sortEventsByPriority(c.events.data?.items ?? []),
    [c.events.data],
  );
  const districtSummary = useMemo(
    () => summarizeDistrictReadiness({
      selectedDistrictId: c.selectedDistrictId,
      districts: c.districts.data,
      districtsStatus: c.districts.status,
      events: c.events.data,
      assetTotal: c.summary.data?.total ?? null,
      caseView: c.caseView.data,
    }),
    [c.selectedDistrictId, c.districts.data, c.districts.status, c.events.data, c.summary.data, c.caseView.data],
  );

  return <section className="gas-capability-empty gas-risk-panel" data-testid="gas-risk-unavailable">
    <h3>风险排行与领导驾驶舱就绪度</h3>
    <strong>unavailable · 正式风险结果未就绪</strong>
    <p>{c.capabilities.risk.reason}</p>

    <div className="gas-risk-readiness" data-testid="gas-risk-readiness">
      <h4>风险数据就绪度（{readiness.readyRatioLabel}）</h4>
      <dl className="gas-fields">
        <div><dt>评分合同状态</dt><dd>{readiness.status} / {readiness.formulaStatus}</dd></div>
        <div><dt>规则版本</dt><dd>{readiness.ruleVersion}</dd></div>
        <div><dt>缺失核心指标</dt><dd>{readiness.missingIndicators.length ? readiness.missingIndicators.join("、") : "无"}</dd></div>
        <div><dt>H 健康度口径</dt><dd>{readiness.hStatus}</dd></div>
        <div><dt>待关注事件</dt><dd>非closed {readiness.nonClosedEventCount} 项（严重 {readiness.criticalOrHighEventCount} 项）</dd></div>
      </dl>
      <p className="gas-window-note">阻断原因：{readiness.blockers.join("；")}</p>
    </div>

    <div className="gas-official-ranking-slot" data-testid="gas-official-ranking-slot">
      <h4>正式风险排行展示槽位（已预留）</h4>
      <table className="gas-risk-table">
        <thead><tr><th>排名</th><th>资产/管段</th><th>正式风险结果</th><th>依据</th></tr></thead>
        <tbody><tr><td colSpan={4}>暂无经授权结果，不生成示例排行或演示分数。</td></tr></tbody>
      </table>
    </div>

    <div className="gas-priority-evidence-slot" data-testid="gas-evidence-priority-list">
      <h4>有依据的合成事件优先关注（非风险评分）</h4>
      {priorityEvents.length ? (
        <ul className="gas-priority-event-list">
          {priorityEvents.map((item) => (
            <li key={item.event_id}>
              <button type="button" onClick={() => c.selectEvent(item.event_id)}>
                <span><strong>{item.event_id}</strong> · {item.title}</span>
                <small>{eventKinds[item.event_kind]} · {item.severity ?? "unknown"} · {item.state ? eventStates[item.state] : "unknown"} · 关联 {item.asset_ids?.length ?? 0} 项 · 未计算评分</small>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="gas-window-note">尚未取得事件集合。</p>}
    </div>

    <div className="gas-district-summary-slot" data-testid="gas-district-summary-slot">
      <h4>区县资产与告警汇总</h4>
      <dl className="gas-fields">
        <div><dt>区县 GIS 接口</dt><dd>{districtSummary.backendStatus}（已返回 {districtSummary.backendTotal} / 38 区县）</dd></div>
        <div><dt>已映射区县事件</dt><dd>{districtSummary.mappedEventCount} / {(c.events.data?.items ?? []).length}</dd></div>
        <div><dt>已映射区县资产</dt><dd>{districtSummary.mappedAssetCount} / {districtSummary.totalAssetsKnown ?? "—"}</dd></div>
      </dl>
      <p className="gas-window-note">{districtSummary.limitations[0]}</p>
    </div>

    <p>左侧仅按真实返回的合成事件类型与状态组织关注，不表示全重庆风险分布。</p>
  </section>;
}

import { GasAgentPanel as GasAssistantPanel } from "./GasAgentPanel";
export { GasAssistantPanel };

function GasSchematicContext() {
  const c = useGasCockpit();
  const value = c.caseView.data;
  const nodes = value?.nodes.filter((node) => c.mapping.nodeIds.includes(node.node_id)) ?? [];
  const edges = value?.edges.filter((edge) => c.mapping.edgeIds.includes(edge.edge_id)) ?? [];
  if (!value) return <p className="gas-window-note">合成拓扑上下文未就绪。</p>;
  return <details className="gas-schematic-context" data-testid="gas-schematic-context" data-source-case-id={value.case_id}>
    <summary>合成拓扑关系（非GIS）</summary><p className="gas-window-note">以下来自同一案例API，不冒充资产详情DTO；px布局不是重庆经纬度。</p>
    {nodes.map((node) => <dl className="gas-fields" key={node.node_id}><div><dt>拓扑节点</dt><dd>{node.node_id}</dd></div>
      <div><dt>合成布局x/y</dt><dd>{node.layout.x}, {node.layout.y} px</dd></div>
      <div><dt>关联服务</dt><dd>{value.services.filter((item) => item.node_ids.includes(node.node_id)).length} 项</dd></div></dl>)}
    {edges.map((edge) => <dl className="gas-fields" key={edge.edge_id}><div><dt>拓扑边</dt><dd>{edge.edge_id}</dd></div>
      <div><dt>起点</dt><dd>{edge.from_node_id}</dd></div><div><dt>终点</dt><dd>{edge.to_node_id}</dd></div><div><dt>方向语义</dt><dd>{edge.direction}</dd></div></dl>)}
    {!nodes.length && !edges.length ? <p>没有可用的确认拓扑关系。</p> : null}
  </details>;
}

function GasPipeCaseContext() {
  const c = useGasCockpit();
  const assetId = c.asset.data?.asset_id;
  const pipe = assetId ? c.caseView.data?.pipes.find((item) => item.pipe_id === assetId) : undefined;
  const valve = assetId ? c.caseView.data?.valves.find((item) => item.valve_id === assetId) : undefined;
  if (!pipe && !valve) return null;
  return <details className="gas-schematic-context" data-testid="gas-pipe-case-context">
    <summary>案例合成属性参考（GAS-M0，非详情DTO实时监测）</summary>
    {pipe ? <dl className="gas-fields">
      <div><dt>材质</dt><dd>{pipe.material}</dd></div>
      <div><dt>公称管径</dt><dd>{pipe.diameter_mm} mm</dd></div>
      <div><dt>设计压力等级</dt><dd>{pipe.pressure_class}</dd></div>
      <div><dt>合成资产状态</dt><dd>{pipe.asset_status}</dd></div>
    </dl> : null}
    {valve ? <dl className="gas-fields">
      <div><dt>切分语义</dt><dd>{valve.cut_semantics}</dd></div>
      <div><dt>初始阀门状态</dt><dd>{valve.initial_state}</dd></div>
      <div><dt>绑定节点</dt><dd>{valve.node_id}</dd></div>
      <div><dt>关联拓扑边</dt><dd>{valve.incident_edge_ids.length} 条</dd></div>
    </dl> : null}
  </details>;
}

export function GasInspector({ inDirectory = false }: { inDirectory?: boolean }) {
  const c = useGasCockpit();
  const mapped = c.mapping.edgeIds.length + c.mapping.nodeIds.length > 0;
  const locate = () => { c.focusTopology(); document.querySelector('[data-window-id="topology"]')?.scrollIntoView({ behavior: c.reducedMotion ? "auto" : "smooth", block: "center" }); };
  const locateGis = () => c.locateAssetsOnMap({
    view: "gis",
    eventId: c.route.gasEventId,
    assetId: c.route.gasAssetId,
    districtId: c.asset.data?.district_id ?? c.event.data?.district_id ?? c.selectedDistrictId ?? undefined,
  });

  if (c.directoryOpen && !inDirectory) return <p className="gas-window-note">资产目录已展开。目录与本详情窗口使用同一Provider、同一份案例和同一资产响应。</p>;
  if (c.route.gasSection === "assistant" && !inDirectory) return <GasAssistantPanel />;
  if (c.route.gasSection === "analysis" && !c.route.gasAssetId && !c.route.gasEventId) return <GasRiskPanel />;
  return <div className="gas-inspector" data-testid="gas-inspector" data-case-identity={c.caseIdentity}>
    {c.route.gasRouteError ? <div className="gas-read-error" role="alert">{c.route.gasRouteError}</div> : null}
    {PRODUCT_EDITION !== "competition" && c.route.gasObservation && <GasSelectedObservation />}
    {c.route.gasAssetId ? <>
      <div className="gas-inspector-actions">
        <div className="gas-selection-breadcrumb">
          {c.event.data ? <span>当前事件：{c.event.data.event_id}</span> : null}
          <span>当前资产：{c.route.gasAssetId}</span>
        </div>
        <button type="button" onClick={c.closeAsset}>{c.route.gasEventId ? "返回事件详情" : "关闭详情"}</button>
      </div>
      {c.route.gasEventId && c.event.error ? <GasReadError error={c.event.error} retry={c.event.retry} label="重试读取所属事件" /> : null}
      {c.route.gasEventId && !c.event.data && !c.event.error ? <p className="gas-window-note">先核对事件关联依据，再读取该事件下的资产。</p> : null}
      {c.event.data && !c.related ? <div className="gas-read-error" role="alert">该资产不在此事件返回的关联ID中，未伪装为事件关联结果。
        <button type="button" onClick={() => c.selectAsset(c.route.gasAssetId!, false)}>独立读取此资产</button></div> : null}
      {c.asset.status === "loading" ? <p role="status">正在从FastAPI读取资产详情…</p> : null}
      {c.asset.error ? <GasReadError error={c.asset.error} retry={c.asset.retry} label="重试读取资产" /> : null}
      {c.asset.data ? <><div className="gas-locate-actions"><button type="button" className="gas-locate-topology" disabled={!mapped} onClick={locate}>定位到合成拓扑</button>
        <button type="button" className="gas-locate-gis-status" onClick={locateGis}>查看 GIS 位置状态</button></div>
        {!mapped ? <p className="gas-window-note">该资产暂无已确认的合成拓扑映射，不构造位置。</p> : null}
        <GasAssetDetails asset={c.asset.data} identity={gasRecordIdentity(c.index, c.asset.data.asset_id)} /><GasPipeCaseContext /><GasSchematicContext /></> : null}
      {c.event.data ? <div className="gas-compact-associated">
        <GasEventAssetGroups
          event={c.event.data}
          index={c.index}
          selectedAssetId={c.route.gasAssetId}
          openAsset={(id) => c.selectAsset(id, true)}
        />
      </div> : null}
    </> : c.route.gasEventId ? <><p className="gas-request-identity">当前事件：{c.route.gasEventId}</p>
      {c.event.status === "loading" ? <p role="status">正在读取真实事件详情…</p> : null}
      {c.event.error ? <GasReadError error={c.event.error} retry={c.event.retry} label="重试读取事件" /> : null}
      {c.event.data ? <><div className="gas-locate-actions"><button type="button" className="gas-locate-topology" disabled={!mapped} onClick={locate}>定位关联资产（合成拓扑）</button>
        <button type="button" className="gas-locate-gis-status" onClick={locateGis}>查看 GIS 位置状态</button></div>
        <GasEventDetails event={c.event.data} index={c.index} selectedAssetId={null} openAsset={(id) => c.selectAsset(id, true)}
          monitoringRef={c.selectedObservation?.event?.event_id === c.event.data.event_id ? c.selectedObservation.reference : undefined}
          onOpenObservation={c.openObservation} /></> : null}
    </> : <div className="gas-empty-selection"><h3>选择事件或拓扑资产</h3><p>事件关联、分页目录和右侧详情共用一份案例，资产详情通过真实HTTP读取。</p>
      <button type="button" onClick={c.openDirectory}>打开204记录资产目录</button>
      {c.mode === "risk" ? <GasRiskPanel /> : c.mode === "emergency" ? <GasImpactPanel /> : <p className="gas-window-note">当前为GAS-M0合成候选；暂无经授权的重庆风险结果与设施GIS坐标；智能体状态以服务端响应为准。</p>}</div>}
  </div>;
}
export function GasDirectoryPanel() {
  const c = useGasCockpit();
  return <div className="gas-directory-modal-grid"><GasAssetDirectory route={c.route} resource={c.assets} index={c.index} allTotal={c.summary.data?.total ?? null} selectedAssetId={c.route.gasAssetId}
    onPage={(assetPage) => c.go({ assetPage, gasAssetId: undefined, gasEventId: undefined, gasSection: "assets", gasCatalogueOpen: true })}
    onType={(assetType) => c.go({ assetType, assetPage: 1, gasAssetId: undefined, gasEventId: undefined, gasSection: "assets", gasCatalogueOpen: true })}
    onOpen={(id) => c.selectAsset(id, false, true)} onLookup={(id) => c.selectAsset(id, false, true)} />
    <section className="gas-directory-detail" aria-label="目录资产详情"><GasInspector inDirectory /></section></div>;
}
