import { useMemo } from "react";
import { PRODUCT_EDITION } from "../../shared/config/productEdition";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasReadError, GasEvidence } from "./GasDetails";
import { eventKinds, eventStates } from "./presentation";
import { sortEventsByPriority, summarizeCuvReadiness, summarizeRiskReadiness } from "./cockpitModel";
import { summarizeDistrictReadiness } from "./chongqingDistricts";

export function GasEventPanel() {
  const c = useGasCockpit();
  const filtered = (c.events.data?.items ?? []).filter(
    (item) => (c.eventKind === "all" || item.event_kind === c.eventKind) && (c.eventState === "all" || item.state === c.eventState),
  );
  const items = useMemo(() => sortEventsByPriority(filtered), [filtered]);
  const readiness = useMemo(() => summarizeRiskReadiness(c.caseView.data, c.events.data), [c.caseView.data, c.events.data]);
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
  const topPriority = items[0] ?? null;

  return <div className="gas-cockpit-event-panel">
    <details className="gas-priority-cue" data-testid="gas-priority-cue" aria-label="优先关注提示">
      <summary className="gas-priority-cue-head">
        <strong>优先关注 · 非closed {readiness.nonClosedEventCount} 项</strong>
        <span className="gas-severity-pill">评分就绪 {readiness.availableCount}/{readiness.requiredCount}</span>
      </summary>
      <p>严重(critical/high): {readiness.criticalOrHighEventCount} 项 · 评分状态: {readiness.status}</p>
      {topPriority ? (
        <button type="button" className="gas-priority-jump" onClick={() => c.selectEvent(topPriority.event_id)}>
          定位首要事件：{topPriority.event_id}
        </button>
      ) : null}
    </details>

    {c.mode === "risk" ? <p className="gas-mode-message" data-testid="gas-risk-attention">真实风险排行 unavailable；下面仅为按事件类型与状态整理的合成关注事项，不是高危分数。</p>
      : c.mode === "emergency" ? <div className="gas-mode-message"><b>给定隔离与图通路影响</b><p>{c.caseView.data?.isolation_plan.closed_valve_ids.length ?? "未知"} 项给定关阀记录，不代表现场已执行。</p><button type="button" data-testid={PRODUCT_EDITION === "competition" ? undefined : "gas-cuv-open"} onClick={() => c.openWindow("impact")}>
          {PRODUCT_EDITION === "competition" ? "查看 CUV 与图影响" : "比较隔离与恢复方案"}
        </button></div>
        : <p className="gas-mode-message">{c.events.data ? `${readiness.nonClosedEventCount} 条非closed合成记录待关注` : "关注数量未知"}；不是实时告警或全重庆风险。</p>}

    <div className="gas-event-filters"><label>事件类型<select aria-label="筛选合成事件类型" value={c.eventKind} onChange={(event) => c.setEventKind(event.target.value)}><option value="all">全部类型</option>{Object.entries(eventKinds).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label>
      <label>事件状态<select aria-label="筛选合成事件状态" value={c.eventState} onChange={(event) => c.setEventState(event.target.value)}><option value="all">全部状态</option>{Object.entries(eventStates).map(([key, label]) => <option value={key} key={key}>{label}</option>)}</select></label></div>
    {c.events.status === "loading" ? <p role="status">正在读取合成事件…</p> : null}
    {c.events.error ? <GasReadError error={c.events.error} retry={c.events.retry} label="重试事件列表" /> : null}
    <div className="gas-event-list">{items.map((item) => <button type="button" key={item.event_id} data-event-id={item.event_id} aria-pressed={c.route.gasEventId === item.event_id} onClick={() => c.selectEvent(item.event_id)}>
      <span className="gas-event-meta-top"><span className="gas-event-kind">{eventKinds[item.event_kind]} · {item.severity ?? "unknown"}</span><span className="gas-event-count">{item.asset_ids?.length ?? 0} 项资产</span></span>
      <b>{item.title}</b>
      <small>{item.event_id} · {item.district_id ?? "未映射区县"}</small>
      <span>{item.state ? eventStates[item.state] : "unknown"} · risk:{item.risk_status ?? "unavailable"} · {item.data_mode}</span>
    </button>)}</div>
    {c.events.data && !items.length ? <p className="gas-window-note">当前筛选没有事件，不推断高危对象。</p> : null}
    <details className="gas-scenario-conditions" open={c.mode === "emergency"}><summary>给定情景条件</summary><dl className="gas-fields">
      <div><dt>情景</dt><dd>{c.caseView.data?.scenario_id ?? "unknown"}</dd></div>
      <div><dt>隔离方案</dt><dd>{c.caseView.data?.plan_id ?? "unknown"}</dd></div>
      <div><dt>分析窗口</dt><dd>{c.caseView.data ? `${c.caseView.data.analysis_window_hours} h` : "unknown"}</dd></div>
      <div><dt>给定故障边</dt><dd>{c.caseView.data?.scenario.fault_edge_ids.join("、") ?? "unknown"}</dd></div>
    </dl><p className="gas-window-note">均为同一案例API的给定条件，不在前端推断新的故障或动作。</p></details>

    <details className="gas-district-summary-card" data-testid="gas-district-summary-card">
      <summary>区县资产与告警汇总 · {districtSummary.backendStatus}</summary>
      <dl className="gas-fields">
        <div><dt>区县接口</dt><dd>{districtSummary.backendTotal} / 38 区县</dd></div>
        <div><dt>映射事件</dt><dd>{districtSummary.mappedEventCount} / {(c.events.data?.items ?? []).length}</dd></div>
        <div><dt>已选区县</dt><dd>{districtSummary.selectedDistrict ? districtSummary.selectedDistrict.name : "未选"}</dd></div>
      </dl>
      <button type="button" className="gas-inline-action" onClick={() => c.go({ gasMap: "gis" })}>切换重庆 GIS 底图</button>
    </details>

    <button type="button" className="gas-catalogue-entry" onClick={c.openDirectory}>资产目录 · 每页50条</button>
    <p className="gas-window-note">事件与资产详情均由真实FastAPI读取；缺项和失败不补零。</p>
  </div>;
}

export function GasTimelinePanel() {
  const c = useGasCockpit();
  return <div className="gas-timeline-panel" data-testid="gas-timeline" data-index={c.timeline.index} data-playing={String(c.timeline.playing)}>
    <div className="gas-timeline-controls"><button type="button" onClick={c.resetTimeline}>重置</button>
      <button type="button" className="gas-timeline-play" onClick={c.timeline.playing ? c.pause : c.play} disabled={c.reducedMotion || c.timelineEvents.length < 2}>{c.timeline.playing ? "暂停" : "播放"}</button>
      <span>{c.reducedMotion ? "减少动态" : c.timelineEvents.length ? `阶段 ${c.timeline.index + 1} / ${c.timelineEvents.length}` : "无可回放事件"}</span>
      <span className="gas-cuv-pill" title="CUV边界：仅图连通；概率、PIR和热点模型未就绪">CUV：仅图连通</span>
      <small title="给定场景相对小时，非专业预测；播放不写浏览器历史。">合成给定事件 · 非预测</small></div>
    <div className="gas-timeline-track">{c.timelineEvents.map((item, index) => <button type="button" key={item.event_id} className={`gas-timeline-event${index === c.timeline.index ? " is-active" : ""}`}
      aria-pressed={index === c.timeline.index} onClick={() => c.selectTimeline(index)}><span className="gas-timeline-time">{item.time_hours} h</span><strong>{eventKinds[item.kind]}</strong><small>{item.current_unreachable_node_ids.length} 个图不可达节点 · {item.newly_affected_service_ids.length} 新增受影响</small></button>)}</div>
  </div>;
}

export function GasImpactPanel() {
  const c = useGasCockpit();
  const active = c.timelineEvents[c.timeline.index] ?? null;
  const cuv = useMemo(
    () => summarizeCuvReadiness(c.caseView.data, active, {
      data: c.capabilityResponse.data,
      status: c.capabilityResponse.status,
      error: c.capabilityResponse.error,
    }),
    [c.caseView.data, active, c.capabilityResponse.data, c.capabilityResponse.status, c.capabilityResponse.error],
  );
  return <section className="gas-impact-panel" data-testid="gas-cuv-summary"><h3>CUV 图连通性与模型就绪边界</h3>
    {c.capabilityResponse.error ? <GasReadError error={c.capabilityResponse.error} retry={c.capabilityResponse.retry} label="重试读取能力边界" /> : null}
    <dl className="gas-fields">
    <div><dt>当前推演阶段</dt><dd>{cuv.phaseLabel}{cuv.timeHours !== null ? ` · ${cuv.timeHours} h` : ""}</dd></div>
    <div><dt>累计图不可达</dt><dd>{cuv.unreachableNodes ?? "unknown"} 节点（新增 {cuv.newlyUnreachableNodes ?? "unknown"}）</dd></div>
    <div><dt>图不可达服务</dt><dd>{cuv.affectedServices ?? "unknown"} 项（本阶段新增 {cuv.newlyAffectedServices ?? "unknown"}）</dd></div>
    <div><dt>潜在家庭用户</dt><dd>{cuv.consumers.household ?? "unknown"}（非物理停气确认）</dd></div>
    <div><dt>潜在商业/设施</dt><dd>{cuv.consumers.business ?? "unknown"} 商业 / {cuv.consumers.facility ?? "unknown"} 设施</dd></div>
    <div><dt>图连通 / 隔离能力</dt><dd>{cuv.capabilities.graphConnectivityStatus} / {cuv.capabilities.isolationCandidateStatus}{cuv.capabilities.datasetId ? ` (${cuv.capabilities.datasetId})` : ""}</dd></div>
    <div><dt>物理供气</dt><dd>unknown</dd></div></dl>
    <h4>未就绪模型维度（不编造算法公式）</h4>
    <ul className="gas-cuv-boundaries" data-testid="gas-cuv-boundaries">
      {cuv.boundaries.map((item) => (
        <li key={item.key}><strong>{item.label}</strong> <code>{item.status}</code><span>{item.reason}</span></li>
      ))}
    </ul>
    <p className="gas-window-note">仅为给定合成图通路结果，不等同真实停气范围、泄漏风险或复气许可。</p></section>;
}

export function GasLayersPanel() {
  const c = useGasCockpit();
  return <div className="gas-check-list">
    <strong className="gas-layer-group-title">合成拓扑图层</strong>
    {(["edges", "nodes", "impact", "labels"] as const).map((layer) => <label key={layer}><input type="checkbox" checked={c.layers[layer]} onChange={() => c.toggleLayer(layer)} />{({ edges: "管段基础关系", nodes: "拓扑节点状态", impact: "图不可达覆盖", labels: "关键标签" })[layer]}</label>)}
    <strong className="gas-layer-group-title">重庆 GIS 图层接口</strong>
    {(["adminRegions", "authorizedAssets", "authorizedEvents"] as const).map((layer) => <label key={layer}><input type="checkbox" checked={c.gisLayers[layer]} onChange={() => c.toggleGisLayer(layer)} />{({ adminRegions: "重庆行政区划(参考)", authorizedAssets: "授权设施点位图层", authorizedEvents: "授权事件点位图层" })[layer]}</label>)}
    <p className="gas-window-note">此处与中央拓扑/GIS共用同一图层状态，不改变服务端结果。</p></div>;
}

export function GasServicesPanel() {
  const c = useGasCockpit();
  const services = c.timelineEvents[c.timeline.index]?.services ?? [];
  return <div className="gas-service-table">{services.map((service) => <button type="button" key={service.service_id} onClick={() => { if (service.node_ids[0]) c.selectTopologyAsset(service.node_ids[0]); }}>
    <span className={`gas-service-status${service.status === "newly_graph_unreachable" ? " is-bad" : ""}`} /><span><b>{service.service_id}</b><small>{service.node_ids.join("、")}</small></span><em>{service.status}</em></button>)}
    {!services.length ? <p>当前没有可用服务记录，保留未知。</p> : null}<p className="gas-window-note">服务记录不是独立客户明细，图通路可达不是实际供气。</p></div>;
}

export function GasDataPanel() {
  const c = useGasCockpit();
  return <dl className="gas-fields"><div><dt>案例身份</dt><dd>{c.caseView.data?.case_id ?? "unknown"}</dd></div><div><dt>网络身份</dt><dd>{c.caseView.data?.network_id ?? "unknown"}</dd></div>
    <div><dt>来源模式</dt><dd>{c.caseView.data?.data_mode ?? "unknown"}</dd></div><div><dt>案例状态</dt><dd>{c.caseView.data?.status ?? c.caseView.status}</dd></div>
    <div><dt>业务验证</dt><dd>{c.caseView.data?.validation_status ?? "unknown"}</dd></div><div><dt>资产总记录</dt><dd>{c.summary.data?.total ?? "unknown"}</dd></div>
    <div><dt>区县GIS接口</dt><dd>{c.districts.data ? `${c.districts.data.status} (${c.districts.data.total} 条)` : c.districts.status}</dd></div>
    <div><dt>能力注册表</dt><dd>{c.capabilityResponse.data ? `${c.capabilityResponse.data.items.length} 项 (${c.capabilityResponse.data.data_mode})` : c.capabilityResponse.status}</dd></div>
    <div><dt>气源数量</dt><dd>{c.caseView.data?.counts.sources ?? "unknown"}</dd></div><div><dt>真实监测</dt><dd>unavailable</dd></div><div><dt>唯一案例版本</dt><dd data-testid="gas-case-identity">{c.caseIdentity}</dd></div></dl>;
}

export function GasEvidencePanel() {
  const c = useGasCockpit();
  const value = c.caseView.data;
  const canDownload = PRODUCT_EDITION === "project" && value?.data_mode === "synthetic"
    && Boolean(value.evidence.case_content_sha256 && value.evidence.network_content_sha256);
  const download = () => {
    if (!canDownload || !value) return;
    const event = c.event.data;
    const asset = c.asset.data;
    const observation = c.selectedObservation;
    const phase = c.timelineEvents[c.timeline.index];
    const report = {
      schema_version: "gas-evidence-report-0.1.0",
      generated_at: new Date().toISOString(),
      data_mode: value.data_mode,
      validation_status: value.validation_status,
      case_id: value.case_id,
      network_id: value.network_id,
      scenario_id: value.scenario_id,
      analysis_scope: value.semantic,
      algorithm_version: value.algorithm_version,
      case_evidence: value.evidence,
      phase: phase ? { event_id: phase.event_id, time_hours: phase.time_hours,
        graph_unreachable_node_ids: phase.current_unreachable_node_ids } : null,
      selected_event: event?.data_mode === "synthetic" && value.timeline_events.some(item => item.event_id === event.event_id)
        ? { event_id: event.event_id, asset_ids: event.asset_ids, evidence_refs: event.evidence_refs } : null,
      selected_asset: asset?.data_mode === "synthetic" && c.index?.has(asset.asset_id)
        ? { asset_id: asset.asset_id, asset_type: asset.asset_type, evidence_refs: asset.evidence_refs } : null,
      selected_monitoring_reference: observation?.data_mode === "synthetic" ? {
        dataset_id: observation.dataset_id,
        dataset_version: observation.dataset_version,
        reference: observation.reference,
        asset_id: observation.asset_id,
        policy_id: observation.policy_id,
      } : null,
    };
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `gas-evidence-${value.case_id.replace(/[^\w-]/g, "_")}.json`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  return <div className="gas-evidence-panel">
    {PRODUCT_EDITION === "project" && <section aria-label="燃气证据报告">
      <p className="gas-window-note">合成演练证据，含当前案例、选中对象和来源版本。</p>
      <button type="button" className="gas-inline-action" disabled={!canDownload}
        data-testid="gas-evidence-report-download" onClick={download}>下载 JSON</button>
    </section>}
    <dl className="gas-fields">{Object.entries(value?.evidence ?? {}).map(([key, field]) =>
      <div key={key}><dt>{key}</dt><dd><code>{field ?? "未提供"}</code></dd></div>)}
      <div><dt>算法版本</dt><dd>{value?.algorithm_version ?? "unknown"}</dd></div>
    </dl>
    <GasEvidence event={c.event.data} asset={c.asset.data} showObjects />
  </div>;
}
