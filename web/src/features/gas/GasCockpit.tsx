import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent, type ReactNode } from "react";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasTopologyView } from "./GasTopologyView";
import { GAS_WINDOWS, PRIMARY_GAS_WINDOWS, type GasWindowId } from "./cockpitModel";
import { GasRecordSummary } from "./GasAssetDirectory";
import { GasAssistantPanel, GasDirectoryPanel, GasInspector, GasRiskPanel } from "./GasInspector";
import { GasDataPanel, GasEventPanel, GasEvidencePanel, GasImpactPanel, GasLayersPanel, GasServicesPanel, GasTimelinePanel } from "./GasPanels";
import "./gas-engineering.css";
import "./gas-cockpit-unify.css";
import "./gas-workbench.css";
import { PRODUCT_EDITION, PRODUCT } from "../../shared/config/productEdition";
import { gasAgentPresentation, useGasTheme } from "./gasTheme";
import { GasMonitoringPanel, monitoringTrigger } from "./GasMonitoringPanel";
import { GasCuvReplayPanel } from "./GasCuvReplayPanel";
import { GasCuvCollaborationPanel } from "./GasCuvCollaborationPanel";
import { GasCuvEngineeringPanel } from "./GasCuvEngineeringPanel";
import { GasCuvBenchmarkPanel } from "./GasCuvBenchmarkPanel";
import { GasPilotPanel } from "./GasPilotPanel";
import { GasDataM2Panel } from "./GasDataM2Panel";
import { GasM2Indicators, GasM2Inspector, GasM2RecordsPane, useGasM2Workbench } from "./GasM2Workbench";
import { useGasScenario } from "./useGasScenario";
import { GasQualityWorkbench } from "./GasQualityWorkbench";
import { useGasTopic } from "./GasTopicPanel";
import { GasTopicConversation } from "./GasTopicConversation";
import { GasReadError } from "./GasDetails";
import type { GasM2Binding } from "../../shared/contracts/platform";
import "./gas-sprint01.css";

export function GasWindow({ id, children, actions, title = GAS_WINDOWS[id] }: {
  id: GasWindowId; children: ReactNode; actions?: ReactNode; title?: string;
}) {
  const c = useGasCockpit();
  const drag = useRef<{ x: number; y: number; dx: number; dy: number } | null>(null);
  const frame = c.frames[id];
  const start = (event: ReactPointerEvent<HTMLDivElement>) => { if ((event.target as Element).closest("button")) return;
    drag.current = { x: event.clientX, y: event.clientY, dx: frame.dx, dy: frame.dy }; event.currentTarget.setPointerCapture(event.pointerId); };
  return <section className={`gas-window gas-window-${id}`} data-window-id={id} aria-labelledby={`gas-window-title-${id}`} style={{ transform: `translate(${frame.dx}px, ${frame.dy}px)` }}>
    <div className="gas-window-head" onPointerDown={start} onPointerMove={(event) => { const current = drag.current; if (current) c.moveWindow(id, {
      dx: Math.max(-260, Math.min(260, current.dx + event.clientX - current.x)), dy: Math.max(-160, Math.min(160, current.dy + event.clientY - current.y)),
    }); }} onPointerUp={() => { drag.current = null; }} onPointerCancel={() => { drag.current = null; }}>
      <h2 id={`gas-window-title-${id}`} tabIndex={-1}>{title}</h2>{actions}<button type="button" className="gas-window-close" aria-label={`隐藏${GAS_WINDOWS[id]}`} onClick={() => c.hideWindow(id)}>×</button></div>
    <div className="gas-window-body">{children}</div><span className="gas-window-resize" aria-hidden="true" /></section>;
}
function GasDialog({ title, onClose, testId, children }: { title: string; onClose(): void; testId?: string; children: ReactNode }) {
  const element = useRef<HTMLDialogElement>(null);
  useEffect(() => { const dialog = element.current; if (dialog && !dialog.open) dialog.showModal(); return () => dialog?.close(); }, []);
  return <dialog className={`gas-cockpit-dialog${testId === "gas-agent-dialog" ? " gas-assistant-drawer" : ""}`} ref={element} aria-label={title} data-testid={testId} onCancel={(event) => { event.preventDefault(); onClose(); }}>
    <div className="gas-dialog-bar"><strong>{title}</strong><button type="button" onClick={onClose}>关闭窗口</button></div>{children}</dialog>;
}
type ProjectDataView = "demo" | "pilot" | "m2" | "quality";
function GasProjectDataWindow({ initialView, binding }: { initialView: ProjectDataView; binding: GasM2Binding | null }) {
  const c = useGasCockpit();
  const [dataView, setDataView] = useState<ProjectDataView>(initialView);
  const monitoring = c.monitoring.data;
  const demoTransition = monitoring?.channels.find(({ asset_id, metric }) => asset_id === "syn-gas-pipe-K-001" && metric === "pressure")
    ?.events.filter((event) => event.kind === "limit_exceeded").at(-1);
  const demoTrigger = demoTransition && monitoringTrigger(demoTransition);
  const openCuvDemo = monitoring?.data_mode === "synthetic" && demoTrigger ? () =>
    c.openObservation({ observation_id: demoTrigger.source_record_id, manifest_sha256: monitoring.manifest_sha256 }) : undefined;
  return <>
    <div role="group" aria-label="燃气数据范围" className="gas-cuv-links">
      <button type="button" aria-pressed={dataView === "demo"} onClick={() => setDataView("demo")}>GAS-M0 合成场景</button>
      <button type="button" aria-pressed={dataView === "pilot"} onClick={() => setDataView("pilot")}>试点历史回放</button>
      <button type="button" aria-pressed={dataView === "m2"} onClick={() => setDataView("m2")}>数据登记与质量</button>
      <button type="button" aria-pressed={dataView === "quality"} onClick={() => setDataView("quality")}>质量指标与准备矩阵</button>
    </div>
    {dataView === "m2" && <GasDataM2Panel />}
    {dataView === "quality" && (binding ? <GasQualityWorkbench binding={binding} /> : <p>当前 M2 登记不可用。</p>)}
    {dataView === "pilot" && <GasPilotPanel api={c.pilotApi} onOpenCuvDemo={openCuvDemo} />}
    {dataView === "demo" && <>
      <GasMonitoringPanel resource={c.monitoring} onOpenObservation={c.openObservation} onOpenAsset={(id) => {
        c.closeExtra();
        c.locateAssetsOnMap({ view: "topology", assetId: id });
      }} />
      <GasDataPanel />
    </>}
  </>;
}

function ExtraContent({ id, dataView, binding }: { id: GasWindowId; dataView: ProjectDataView; binding: GasM2Binding | null }) {
  const c = useGasCockpit();
  if (id === "impact") return <>
    {PRODUCT_EDITION !== "competition" && <GasCuvReplayPanel resource={c.cuvReplay}
      onOpenAsset={(assetId) => {
        c.closeExtra();
        c.locateAssetsOnMap({ view: "topology", assetId });
      }}
      onOpenEvent={(eventId) => {
        c.closeExtra();
        c.locateAssetsOnMap({ view: "topology", eventId });
      }} onAskQwen={(report) => void c.askCuvAgent(report)}>
      {c.cuvReplay.data && c.caseView.data && <GasCuvCollaborationPanel
        key={[c.caseIdentity, c.cuvReplay.data.topology_evidence.replay_content_sha256,
          c.cuvReplay.data.monitoring_evidence.manifest_sha256].join(":")}
        report={c.cuvReplay.data} api={c.cuvApi}
        monitoringRef={c.selectedObservation?.event ? c.selectedObservation.reference : undefined}
        onOpenObservation={c.openObservation}
        onAskDraft={(draft) => void c.askCuvAgent(c.cuvReplay.data!, { draft })}
        onAskConfirmed={(comparison) => void c.askCuvAgent(c.cuvReplay.data!, { comparison })} />}
      {c.cuvReplay.data && <GasCuvEngineeringPanel report={c.cuvReplay.data} api={c.cuvApi}
        onAsk={() => void c.askCuvAgent(c.cuvReplay.data!, { engineering: true })} />}
      <GasCuvBenchmarkPanel api={c.cuvApi} onAsk={(binding) => void c.askCuvBenchmark(binding)} />
    </GasCuvReplayPanel>}
    <GasImpactPanel />
  </>;
  if (id === "services") return <GasServicesPanel />;
  if (id === "layers") return <GasLayersPanel />;
  if (id === "data") return PRODUCT_EDITION === "competition" ? <GasDataPanel /> : <GasProjectDataWindow initialView={dataView} binding={binding} />;
  if (id === "evidence") return <GasEvidencePanel />;
  return <><GasRiskPanel /><h3>候选评价口径</h3><dl className="gas-fields">
    <div><dt>P语义</dt><dd>{c.caseView.data?.scoring.p_semantics ?? "unknown"}</dd></div>
    <div><dt>E语义</dt><dd>{c.caseView.data?.scoring.coverage_semantics ?? "unknown"}</dd></div>
    <div><dt>H口径</dt><dd>{c.caseView.data?.scoring.h_status ?? "unknown"}</dd></div>
    <div><dt>压力能力</dt><dd>{c.caseView.data?.metrics.pressure?.status ?? "unknown"} / {c.caseView.data?.metrics.pressure?.reason ?? "未提供"}</dd></div>
    <div><dt>流量能力</dt><dd>{c.caseView.data?.metrics.flow?.status ?? "unknown"} / {c.caseView.data?.metrics.flow?.reason ?? "未提供"}</dd></div>
  </dl><h3>GIS与模型边界</h3><p>{c.capabilities.gis.reason}</p><p>智能体：{c.agent.health?.reason ?? gasAgentPresentation(c.agent).detail}</p>
    <p>物理供气 unknown；不输出实际关阀、复气、正式风险分或全重庆排行。</p><p>{c.caseView.data?.scoring.note ?? "评分专业口径未就绪。"}</p></>;
}
export function GasCockpit({ onNavigate }: { onNavigate(path: string): void }) {
  const c = useGasCockpit();
  const value = c.caseView.data;
  const project = PRODUCT_EDITION === "project";
  const m2 = useGasM2Workbench(project);
  const topic = useGasTopic(m2.binding);
  const currentTopic = topic.resource.data;
  const showTopic = project && topic.focused && c.assistantDialog === "current";
  const renderAssistant = (content: ReactNode) => <GasDialog title="燃气智能体与受控能力边界"
    testId="gas-agent-dialog" onClose={c.closeAssistantDialog}>{content}</GasDialog>;
  const drill = useGasScenario(m2.binding);
  const recordsWorkspace = project && c.mode !== "emergency";
  const [dataView, chooseDataView] = useState<ProjectDataView>("demo");
  const openData = (view: ProjectDataView) => { chooseDataView(view); c.openWindow("data"); };
  const { theme, toggleTheme } = useGasTheme();
  const settings = useRef<HTMLDetailsElement>(null);
  const agentDisplay = gasAgentPresentation(c.agent);
  useEffect(() => { void c.agent.probe(); }, [c.agent.probe, c.caseIdentity]);
  useEffect(() => {
    const close = (event: MouseEvent) => { if (settings.current && !settings.current.contains(event.target as Node)) settings.current.open = false; };
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape" && settings.current?.open) { settings.current.open = false; settings.current.querySelector("summary")?.focus(); } };
    document.addEventListener("click", close); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("click", close); document.removeEventListener("keydown", escape); };
  }, []);
  const mapActions = <div className="gas-map-mode" role="group" aria-label="中央视图">
    <button type="button" aria-pressed={c.route.gasMap !== "gis"} onClick={() => c.go({ gasMap: "topology" })}>合成拓扑</button>
    <button type="button" aria-pressed={c.route.gasMap === "gis"} onClick={() => c.go({ gasMap: "gis" })}>重庆GIS</button>
  </div>;
  const monitoringAction = PRODUCT_EDITION !== "competition" ? <button type="button" data-testid="gas-monitoring-open" onClick={() => openData("demo")}>
    合成监测预警
  </button> : undefined;
  const bodies: Record<(typeof PRIMARY_GAS_WINDOWS)[number], ReactNode> = {
    topology: <GasTopologyView drill={recordsWorkspace && !drill.workspace.error ? drill.scenario : undefined}
      comparison={recordsWorkspace && !drill.workspace.error ? drill.comparison : undefined} highlighted={drill.highlighted} />,
    scenario: recordsWorkspace ? <GasM2RecordsPane workspace={m2} /> : <GasEventPanel />,
    selected: recordsWorkspace && !c.route.gasObservation && c.route.gasSection !== "analysis" && c.route.gasSection !== "assistant"
      ? <GasM2Inspector workspace={m2} topic={topic} drill={drill} openQuality={() => openData("m2")}
        openQualityWorkbench={() => openData("quality")} /> : <GasInspector />,
    timeline: <GasTimelinePanel />,
    tasks: <div className="gas-task-dock">
      <form
        className="gas-agent-quick-entry"
        data-testid="gas-agent-quick-entry"
        onSubmit={(event) => {
          event.preventDefault();
          void c.submitAgent();
          c.openAssistantTools();
        }}
      >
        <input
          type="text"
          aria-label="智能体快捷查询"
          value={c.assistantInput}
          onChange={(event) => c.setAssistantInput(event.target.value)}
          placeholder="快捷输入：渝中区 / 定位故障 / CUV"
        />
        <button type="submit" disabled={c.agent.status === "loading"}>发送查询</button>
      </form>
      <div className="gas-task-list">
        <button type="button" onClick={() => c.go({ gasSection: "assistant" })}>燃气智能体入口<span>{c.agent.health?.tools_status ?? "unknown"} / 只读工具</span></button>
        <button type="button" onClick={c.openAssistantDialog}>独立展开面板<span>意图 · 工具 · 定位</span></button>
        <button type="button" onClick={c.openDirectory}>资产分页目录<span>50条/页 · {c.summary.data?.total ?? "unknown"}记录</span></button>
        {PRODUCT_EDITION === "competition"
          ? <button type="button" onClick={() => onNavigate("/report")}>既有平台报告<span>兼容只读入口</span></button>
          : <button type="button" onClick={() => c.openWindow("evidence")}>燃气证据报告<span>案例与版本 · JSON</span></button>}
      </div>
    </div>,
    impact: null, services: null, layers: null, data: null, evidence: null, boundary: null,
  };
  return <main className={`gas-cockpit-page gas-cockpit-unified gas-workbench gas-mode-${c.mode}${project ? " gas-sprint-cockpit" : ""}`} data-testid="gas-page" data-theme={theme} data-mode={c.mode} data-layout={c.layout} data-case-id={value?.case_id ?? "unknown"} data-case-identity={c.caseIdentity}>
    <header className="gas-app-bar">
      <div className="gas-app-brand"><span aria-hidden="true" className="gas-brand-mark">G</span><div><h1>{project ? "重庆燃气中控" : "重庆燃气智能监管"}</h1><span data-testid="product-edition-label">{PRODUCT.label}</span></div></div>
      {PRODUCT_EDITION === "competition" && <nav className="gas-domain-switch" aria-label="赛题3业务切换">
        <button type="button" onClick={() => onNavigate("/overview")}>供水中控</button>
        <button type="button" aria-current="page" onClick={() => onNavigate("/gas")}>燃气中控</button>
      </nav>}
      <nav className="gas-mode-switch" aria-label="中控模式">{(["overview", "risk", "emergency"] as const).map((mode) =>
        <button type="button" aria-pressed={c.mode === mode} key={mode} onClick={() => c.setMode(mode)}>{({ overview: "综合态势", risk: "风险分析", emergency: "应急推演" })[mode]}</button>)}</nav>
      <button type="button" className="gas-provenance-badge" onClick={() => openData(project ? "m2" : "demo")} title="查看数据来源、完整性和业务边界">
        {project ? (m2.binding ? `GAS-M0 合成 · v${m2.binding.version}` : "GAS-M0 · 登记待就绪")
          : "GAS-M0 合成数据 · 未经业务验证"}
      </button>
      <div className="gas-app-actions">
        {import.meta.env.VITE_WPN_GAS_DEMO === "1" && <span className="gas-status-pill" role="status" data-testid="gas-demo-model-status" data-status={c.agent.health?.model_status ?? "unknown"}>
          Qwen {c.agent.health?.model_status === "ready" ? "ready" : c.agent.health?.model_status === "starting" ? "starting · 规则工具可用" : c.agent.health?.tools_status === "ready" ? "unavailable · 规则工具可用" : "状态待确认"}
        </span>}
        <button type="button" data-testid="gas-theme-toggle" aria-label={theme === "dark" ? "切换浅色主题" : "切换深色主题"} onClick={toggleTheme}>{theme === "dark" ? "浅色工作台" : "深海蓝大屏"}</button>
        <details className="gas-view-settings" ref={settings}><summary>视图设置（11窗口）</summary>
          <div className="gas-settings-content">
            <h2>布局预设</h2><div className="gas-layout-options">{(["command", "analysis", "compact"] as const).map((layout) =>
              <button type="button" key={layout} aria-pressed={c.layout === layout} onClick={() => { c.setLayout(layout); if (settings.current) settings.current.open = false; }}>{({ command: "指挥布局", analysis: "分析布局", compact: "紧凑布局" })[layout]}</button>)}</div>
            <h2>11窗口管理</h2><div className="gas-window-options">{Object.entries(GAS_WINDOWS).map(([id, title]) => {
              const winId = id as GasWindowId;
              const status = c.hidden.has(winId) ? "已隐藏 · 恢复" : PRIMARY_GAS_WINDOWS.includes(winId) ? "主区已开" : c.activeWindow === winId ? "弹窗已开" : "打开面板";
              return <button type="button" key={id} data-window-control={id} onClick={() => { c.openWindow(winId); if (settings.current) settings.current.open = false; }}><span>{title}</span><small>{status}</small></button>;
            })}</div>
            <div className="gas-settings-actions"><button type="button" onClick={() => { c.restoreWindows(); if (settings.current) settings.current.open = false; }}>恢复全部窗口</button><button type="button" onClick={() => { c.refresh(); if (project) m2.catalog.retry(); }}>刷新受控数据</button>
              <button type="button" onClick={() => { c.go({ gasSection: "analysis", gasEventId: undefined, gasAssetId: undefined }); if (settings.current) settings.current.open = false; }}>风险就绪状态</button></div>
          </div>
        </details>
        <button type="button" className="gas-assistant-entry" onClick={c.openAssistantDialog} title={agentDisplay.detail} data-agent-tone={agentDisplay.tone}>
          智能助手 <span data-testid="gas-agent-live-status">{agentDisplay.label}</span>
        </button>
      </div>
    </header>
    {project ? <GasM2Indicators workspace={m2} />
      : <GasRecordSummary catalogue={value} total={c.summary.data?.total ?? null} eventsTotal={c.events.data?.total ?? null} />}
    {c.route.gasRouteError ? <div className="gas-inline-route-error" role="alert">{c.route.gasRouteError}</div> : null}
    <section className={`gas-cockpit-workspace gas-unified-workspace gas-layout-${c.layout}`} data-testid="gas-workspace">
      {PRIMARY_GAS_WINDOWS.map((id) => !c.hidden.has(id) ? <GasWindow
        title={recordsWorkspace && id === "scenario" ? "资产与事件" : project && id === "topology" ? "空间视图" : undefined}
        key={`${id}:${c.windowRevision}`} id={id} actions={id === "topology" ? mapActions : id === "scenario" ? monitoringAction : undefined}>{bodies[id]}</GasWindow> : <section className={`gas-hidden-slot gas-window-${id}`} key={id}><button type="button" onClick={() => c.openWindow(id)}>恢复{GAS_WINDOWS[id]}</button></section>)}
    </section>
    <footer className="gas-cockpit-footer"><span>合成网络 · 图连通不等于真实供气 · 不提供现场控制指令</span><span data-testid="gas-case-status">{value?.data_mode ?? "unknown"} / {value?.status ?? (c.caseView.error ? "unavailable" : "loading")} / {value?.validation_status ?? "unknown"}</span></footer>
    {c.activeWindow && !c.hidden.has(c.activeWindow) ? <GasDialog title={GAS_WINDOWS[c.activeWindow]} onClose={c.closeExtra}><GasWindow id={c.activeWindow}><ExtraContent id={c.activeWindow} dataView={dataView} binding={m2.binding} /></GasWindow></GasDialog> : null}
    {c.directoryOpen ? <GasDialog title="资产目录与真实详情" onClose={c.closeDirectory}><GasDirectoryPanel /></GasDialog> : null}
    {project && topic.focused && currentTopic && <GasTopicConversation key={currentTopic.evidence_sha256}
      topic={currentTopic} title={topic.title} refresh={m2.catalog.retry} render={showTopic ? renderAssistant : undefined}
      scenario={drill.scenario} comparison={drill.comparison} />}
    {c.assistantDialog && !(showTopic && currentTopic) && renderAssistant(showTopic ? <>
      <button type="button" onClick={c.openAssistantTools}>通用工具</button>
      {topic.resource.error ? <GasReadError error={topic.resource.error} retry={topic.resource.retry} label="重试专题" />
        : m2.catalog.error ? <GasReadError error={m2.catalog.error} retry={m2.catalog.retry} label="刷新数据登记" />
          : <p role="status">正在读取当前专题证据…</p>}
    </> : <>
      {project && topic.focused && <button type="button" onClick={c.openAssistantDialog}>询问当前专题</button>}
      <GasAssistantPanel />
    </>)}
  </main>;
}
