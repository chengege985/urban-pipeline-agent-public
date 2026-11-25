import { useEffect, useMemo, useRef, useState } from "react";
import { DECISION_ALGORITHM_VERSION, DECISION_REFERENCE_COST_YUAN, DEFAULT_DECISION_CONSTRAINTS } from "../domain/constants";
import type {
  AnalysisContext,
  DemoConstraints,
  PlanComparison,
  HealthAssessment,
  PipeMaterial,
  PressureLevel,
  ServiceImpact,
  WaterPulseDataset,
} from "../domain/types";
import { DecisionPanel } from "../components/DecisionPanel";
import { EmptyState } from "../components/EmptyState";
import { EvidenceList } from "../components/EvidenceList";
import { TimelinePanel, type TimelineSnapshot } from "../components/TimelinePanel";
import { FactorBars } from "../components/FactorBars";
import { HealthGauge } from "../components/HealthGauge";
import { ArrowRightIcon, AssistantIcon, ResetIcon, ScenarioIcon, ServiceIcon } from "../components/Icons";
import { NetworkMap } from "../components/NetworkMap";
import { PageHeader } from "../components/PageHeader";
import { SeverityPill } from "../components/StatusPill";
import { timelineBasisKey } from "../engine/timelineContext";
import { currentContext, inputKey } from "../engine/context";
import { formatScore, signed } from "../engine/format";
import type { WaterPulseProvider } from "../providers";
import { browserDecisionStore } from "../storage/decisionStore";

interface ScenarioPageProps {
  dataset: WaterPulseDataset;
  provider: WaterPulseProvider;
  scenarioId?: string;
  pipeId?: string;
  onNavigate: (path: string) => void;
  context?: AnalysisContext | null;
  onContextChange: (context: AnalysisContext) => void;
}

const MATERIALS: PipeMaterial[] = ["PE", "球墨铸铁", "铸铁", "钢", "PVC", "未知"];
const PRESSURES: PressureLevel[] = ["低压", "常压", "高压", "未知"];

export function ScenarioPage({ dataset, provider, scenarioId, pipeId, onNavigate, context, onContextChange }: ScenarioPageProps) {
  const platformMode = dataset.meta.providerKind === "platform";
  const seed = useRef(currentContext(context, dataset)).current;
  const initialScenario = dataset.scenarios.find((item) => item.id === (scenarioId ?? seed?.scenarioId)) ?? dataset.scenarios[0];
  const [activeScenarioId, setActiveScenarioId] = useState(initialScenario?.id ?? "");
  const scenario = dataset.scenarios.find((item) => item.id === activeScenarioId);
  const [focusPipeId, setFocusPipeId] = useState(pipeId ?? seed?.pipeId ?? scenario?.failedPipeIds[0] ?? "");
  const activePipe = dataset.pipes.find((item) => item.id === focusPipeId);
  const baselineAssessment = activePipe ? dataset.assessments[activePipe.id] : undefined;
  const isolationOptions = dataset.isolationPlans.filter((plan) => plan.scenarioId === scenario?.id);
  const [isolationPlanId, setIsolationPlanId] = useState(seed && seed.scenarioId === scenario?.id ? seed.isolationPlanId : scenario?.defaultIsolationPlanId ?? "");
  const activeIsolation = isolationOptions.find((plan) => plan.id === isolationPlanId);
  const [constraints, setConstraints] = useState<DemoConstraints>(seed?.constraints ?? DEFAULT_DECISION_CONSTRAINTS);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);
  const [timelineSnapshot, setTimelineSnapshot] = useState<TimelineSnapshot | null>(null);
  const baselineAge = activePipe?.installYear == null ? null : Math.max(0, 2026 - activePipe.installYear);
  const initialOverride = seed?.pipeId === focusPipeId ? seed.override : {};
  const [age, setAge] = useState<number | null>(initialOverride.installYear === undefined ? baselineAge : initialOverride.installYear === null ? null : 2026 - initialOverride.installYear);
  const [leaks, setLeaks] = useState<number | null>(initialOverride.leakCount === undefined ? activePipe?.leakCount ?? null : initialOverride.leakCount);
  const [pressure, setPressure] = useState<PressureLevel>(initialOverride.pressure ?? activePipe?.pressure ?? "未知");
  const [material, setMaterial] = useState<PipeMaterial>(initialOverride.material ?? activePipe?.material ?? "未知");
  const override = useMemo(() => ({ installYear: age === null ? null : 2026 - age, leakCount: leaks, pressure, material }), [age, leaks, pressure, material]);
  // 平台模式不接受浏览器内改数；正式评分只能来自服务端结果。
  const effectiveOverride = useMemo(() => (platformMode ? {} : override), [platformMode, override]);
  const candidatePlanIds = useMemo(
    () => (platformMode ? dataset.planCatalog?.map((plan) => plan.id) : undefined),
    [platformMode, dataset.planCatalog],
  );
  const [impactResult, setImpactResult] = useState<{ key: string; value: ServiceImpact } | null>(null);
  const [assessmentResult, setAssessmentResult] = useState<{ key: string; value: HealthAssessment } | null>(null);
  const [comparisonResult, setComparisonResult] = useState<{ key: string; value: PlanComparison } | null>(null);
  const [impactError, setImpactError] = useState<string | null>(null);
  const [evaluationError, setEvaluationError] = useState<string | null>(null);
  const [comparisonError, setComparisonError] = useState<string | null>(null);
  const networkKey = inputKey([dataset.meta.dataVersion, scenario?.id, activeIsolation?.id]);
  const assessmentKey = inputKey([dataset.meta.dataVersion, dataset.meta.modelVersion, activePipe, effectiveOverride]);
  const comparisonKey = inputKey([networkKey, constraints, platformMode ? "platform-decision" : DECISION_ALGORITHM_VERSION]);
  // A response belongs to its original inputs, including during the render before effects run.
  const impact = impactResult?.key === networkKey ? impactResult.value : undefined;
  const current = assessmentResult?.key === assessmentKey ? assessmentResult.value : undefined;
  const comparison = comparisonResult?.key === comparisonKey ? comparisonResult.value : null;
  const reportRunId = comparison?.runId ?? (impact?.status === "partial" ? impact.runId : undefined);
  const impactLoading = !impact && !impactError;
  const evaluating = !current && !evaluationError;
  const previousScenario = useRef(scenario?.id);
  const previousPipe = useRef(activePipe?.id);
  const currentTimeline = timelineSnapshot &&
    timelineSnapshot.request.scenarioId === scenario?.id &&
    timelineSnapshot.request.isolationPlanId === activeIsolation?.id &&
    (selectedPlanId === null || timelineSnapshot.request.planId === selectedPlanId)
    ? timelineSnapshot
    : null;

  useEffect(() => { if (scenarioId) setActiveScenarioId(scenarioId); }, [scenarioId]);
  useEffect(() => { if (pipeId) setFocusPipeId(pipeId); }, [pipeId]);
  useEffect(() => {
    if (!scenario || previousScenario.current === scenario.id) return;
    previousScenario.current = scenario.id;
    setIsolationPlanId(scenario.defaultIsolationPlanId);
    setFocusPipeId(scenario.failedPipeIds[0] ?? "");
  }, [scenario]);
  useEffect(() => {
    if (!activePipe || previousPipe.current === activePipe.id) return;
    previousPipe.current = activePipe.id;
    setAge(baselineAge); setLeaks(activePipe.leakCount); setPressure(activePipe.pressure); setMaterial(activePipe.material);
  }, [activePipe, baselineAge]);
  useEffect(() => {
    if (activePipe && scenario && activeIsolation) onContextChange({
      pipeId: activePipe.id,
      scenarioId: scenario.id,
      isolationPlanId: activeIsolation.id,
      override,
      constraints,
      dataVersion: dataset.meta.dataVersion,
      modelVersion: dataset.meta.modelVersion,
      planId: currentTimeline?.request.planId,
      targetServiceIds: currentTimeline?.request.targetServiceIds,
      horizonHours: currentTimeline?.request.horizonHours,
      actionSchedule: currentTimeline?.request.actionSchedule,
      timelineDataVersion: dataset.timeline.version,
      timelineAsOf: dataset.timeline.asOf,
      timeline: currentTimeline?.result,
      timelineGate: currentTimeline?.gate,
    });
  }, [activePipe, scenario, activeIsolation, override, constraints, dataset.meta.dataVersion, dataset.meta.modelVersion, currentTimeline, onContextChange]);
  useEffect(() => {
    let cancelled = false;
    setImpactResult(null); setImpactError(null);
    if (!scenario || !activeIsolation) return;
    void provider.simulate(scenario.id, activeIsolation.id).then((result) => {
      if (result.scenarioId !== scenario.id || result.isolationPlanId !== activeIsolation.id) throw new Error("影响结果与当前情景/隔离不一致。");
      if (!cancelled) setImpactResult({ key: networkKey, value: result });
    }).catch((error: unknown) => { if (!cancelled) setImpactError(error instanceof Error ? error.message : "情景重算失败。"); });
    return () => { cancelled = true; };
  }, [networkKey, provider, scenario, activeIsolation]);
  useEffect(() => {
    let cancelled = false;
    setAssessmentResult(null); setEvaluationError(null);
    if (!activePipe) return;
    void provider.evaluate(activePipe, effectiveOverride).then((result) => {
      if (result.pipeId !== activePipe.id) throw new Error("评分结果与当前管段不一致。");
      if (!cancelled) setAssessmentResult({ key: assessmentKey, value: result });
    }).catch((error: unknown) => { if (!cancelled) setEvaluationError(error instanceof Error ? error.message : "评分重算失败。"); });
    return () => { cancelled = true; };
  }, [assessmentKey, activePipe, effectiveOverride, provider]);
  useEffect(() => {
    let cancelled = false;
    setComparisonResult(null); setComparisonError(null); setSelectedPlanId(null); setReportError(null);
    if (!scenario || !activeIsolation || !impact || impact.status !== "succeeded") return;
    void provider.comparePlans(scenario.id, candidatePlanIds, constraints, activeIsolation.id).then((result) => {
      const versionMatches = platformMode ? Boolean(result.decisionAlgorithmVersion) : result.decisionAlgorithmVersion === DECISION_ALGORITHM_VERSION;
      if (result.scenarioId !== scenario.id || inputKey(result.constraints) !== inputKey(constraints) ||
          !versionMatches) throw new Error("方案结果与当前约束或算法版本不一致。");
      if (!cancelled) setComparisonResult({ key: comparisonKey, value: result });
    }).catch((error: unknown) => { if (!cancelled) setComparisonError(error instanceof Error ? error.message : "方案比较失败。"); });
    return () => { cancelled = true; };
  }, [comparisonKey, scenario, activeIsolation, impact, constraints, provider, candidatePlanIds]);

  if (!scenario || !activePipe || !baselineAssessment || !activeIsolation) {
    return <EmptyState title="没有可用的情景、隔离或管段" message="请返回总览选择有效输入。" />;
  }
  const dirty = !platformMode && (age !== baselineAge || leaks !== activePipe.leakCount || pressure !== activePipe.pressure || material !== activePipe.material);
  const delta = current?.score != null && baselineAssessment.score !== null ? current.score - baselineAssessment.score : null;
  const affectedServiceIds = impact?.affectedServices.map((service) => service.serviceId) ?? [];
  const highlightPipeIds = [...scenario.failedPipeIds, ...activeIsolation.closedPipeIds];
  const analysisReady = Boolean(current && impact?.status === "succeeded" && comparison?.status === "succeeded" && !impactError && !evaluationError && !comparisonError);

  function changeScenario(id: string) {
    const next = dataset.scenarios.find((item) => item.id === id);
    if (!next) return;
    setActiveScenarioId(id); setSelectedPlanId(null); setReportError(null);
    setIsolationPlanId(next.defaultIsolationPlanId);
    if (next.failedPipeIds[0]) setFocusPipeId(next.failedPipeIds[0]);
    onNavigate(`/scenario/${id}?pipe=${next.failedPipeIds[0] ?? ""}`);
  }
  function changePipe(id: string) { setFocusPipeId(id); onNavigate(`/scenario/${scenario!.id}?pipe=${id}`); }
  function resetWhatIf() { setAge(baselineAge); setLeaks(activePipe!.leakCount); setPressure(activePipe!.pressure); setMaterial(activePipe!.material); }
  function generateReport() {
    if (platformMode) {
      if (reportRunId && provider.report) onNavigate(`/report/${encodeURIComponent(reportRunId)}`);
      else setReportError("当前输入尚无可读取的运行报告，请等待方案比较完成。");
      return;
    }
    if (!analysisReady || !comparison || !selectedPlanId || !current || !impact || !activeIsolation || !activePipe || !scenario) return;
    const selectedPlan = dataset.plans.find((plan) => plan.id === selectedPlanId);
    if (!selectedPlan || !comparison.rows.some((row) => row.planId === selectedPlanId && row.feasible)) {
      setReportError("当前方案不可行，请重新选择。"); return;
    }
    const evidenceIds = [
      `data:${activePipe.id}`,
      `rule:demo-health-${dataset.meta.modelVersion}`,
      ...impact.evidenceIds,
      ...activeIsolation.evidenceIds,
      ...selectedPlan.evidenceIds,
      ...(currentTimeline?.result.evidenceIds ?? []),
    ];
    try {
      const session = browserDecisionStore().create({ pipeId: activePipe.id, scenarioId: scenario.id,
        isolationPlanId: activeIsolation.id, budgetYuan: constraints.budgetYuan, resourceCapacities: constraints.resourceCapacities,
        confirmedDependencies: constraints.confirmedDependencies, selectedPlanId, dataVersion: dataset.meta.dataVersion,
        algorithmVersion: dataset.meta.modelVersion, decisionAlgorithmVersion: DECISION_ALGORITHM_VERSION, override, evidenceIds,
        snapshot: { pipe: { ...activePipe, ...override }, assessment: current, scenario, isolationPlan: activeIsolation,
          impact, comparison, selectedPlan,
          timeline: currentTimeline?.result,
          timelineGate: currentTimeline?.gate,
          targetServiceIds: currentTimeline?.request.targetServiceIds,
          horizonHours: currentTimeline?.request.horizonHours,
          actionSchedule: currentTimeline?.request.actionSchedule,
          timelineDataVersion: currentTimeline ? dataset.timeline.version : undefined,
          timelineBasisKey: currentTimeline ? timelineBasisKey(dataset) : undefined } });
      onNavigate(`/report/${session.id}`);
    } catch (error) { setReportError(error instanceof Error ? error.message : "简报保存失败。"); }
  }

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Scenario analysis · C4"
        title="交互式情景分析"
        description={platformMode
          ? "选择故障情景与预设隔离方案，查看统一 FastAPI 返回的拓扑影响、候选方案与恢复时间线；平台模式不在浏览器内改数。"
          : "选择故障情景与预设隔离方案，查看合成管网的拓扑影响；再调整演示参数，观察 DemoHealth 如何变化。"}
        actions={
          <>
            <button className="button-ghost" onClick={() => onNavigate(`/pipe/${activePipe.id}`)}>
              查看管段详情 <ArrowRightIcon size={14} />
            </button>
            <button className="button-primary" onClick={() => onNavigate(`/assistant?pipe=${activePipe.id}&scenario=${scenario.id}`)}>
              <AssistantIcon size={16} /> 问演示助手
            </button>
          </>
        }
      />

      <section className="scenario-toolbar panel">
        <label>
          故障 / 运行情景
          <select value={scenario.id} onChange={(event) => changeScenario(event.target.value)}>
            {dataset.scenarios.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
          </select>
        </label>
        <label>
          关注管段
          <select value={activePipe.id} onChange={(event) => changePipe(event.target.value)}>
            {dataset.pipes.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.name}</option>)}
          </select>
        </label>
        <label>
          预设隔离方案
          <select
            value={activeIsolation?.id ?? ""}
            onChange={(event) => {
              setIsolationPlanId(event.target.value);
              setSelectedPlanId(null);
            }}
          >
            {isolationOptions.map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}
          </select>
        </label>
        <div className="scenario-toolbar-note">
          <SeverityPill severity={scenario.severity} />
          <span>{activeIsolation?.description ?? scenario.note}</span>
        </div>
      </section>

      <section className="panel map-panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Isolation map</p>
            <h2>故障与受影响区域</h2>
            <p>红色虚线为故障或隔离管段；受影响服务与节点以高亮标出。示意布局，无真实地理坐标。</p>
          </div>
          <span className="panel-kicker">{highlightPipeIds.length} 段故障/隔离</span>
        </div>
        <NetworkMap
          nodes={dataset.nodes}
          pipes={dataset.pipes}
          services={dataset.services}
          assessments={dataset.assessments}
          filter="全部"
          selectedPipeId={activePipe.id}
          highlightPipeIds={highlightPipeIds}
          affectedNodeIds={impact?.unreachableNodeIds ?? []}
          affectedServiceIds={affectedServiceIds}
          showImpactLegend
          onSelectPipe={(id) => setFocusPipeId(id)}
        />
        {impactLoading ? <p className="muted small">正在按当前隔离方案重算拓扑影响…</p> : null}
        {impactError ? <p className="inline-warning">{impactError}</p> : null}
      </section>

      <div className="detail-grid">
        <section className="panel impact-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Service impact · C4</p>
              <h2>可能影响谁？</h2>
              <p>{scenario.summary}</p>
            </div>
            <ScenarioIcon size={20} />
          </div>
          <div className="impact-metrics">
            <span><b>{impact?.affectedServices.length ?? "—"}</b><small>受影响服务</small></span>
            <span><b>{impact?.affectedCriticalServiceIds.length ?? "—"}</b><small>关键设施</small></span>
            <span><b>{impact?.affectedHouseholds ?? "—"}</b><small>居民户数</small></span>
            <span><b>{impact?.affectedFacilities ?? "—"}</b><small>设施处数</small></span>
          </div>
          <div className="affected-services">
            {(impact?.affectedServices ?? []).map((service) => (
              <div className="affected-service" key={service.serviceId}>
                <span className={`service-icon service-${service.type}`}><ServiceIcon type={service.type} size={17} /></span>
                <div>
                  <strong>{service.name}</strong>
                  <small>{service.type}{service.critical ? " · 关键设施" : ""}</small>
                </div>
                <span className="affected-count">
                  {service.households > 0 ? `${service.households} 户` : `${service.facilities} 处`}
                </span>
              </div>
            ))}
            {impact?.affectedServices.length === 0 ? (
              <p className="inline-success">该情景与隔离方案下所有服务对象仍保持拓扑连通；备用源切换演示正常。</p>
            ) : null}
          </div>
          <p className="topology-note">
            仅表示合成管网中的拓扑通路影响，不代表压力、流量、实际缺供量或事故概率。
          </p>
        </section>

        <aside className="panel scenario-summary">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Scenario facts</p>
              <h2>情景事实</h2>
            </div>
          </div>
          <dl className="attribute-grid compact">
            <div><dt>故障管段</dt><dd>{scenario.failedPipeIds.join("、")}</dd></div>
            <div><dt>隔离方案</dt><dd>{activeIsolation?.name ?? "未选择"}</dd></div>
            <div><dt>关闭管段</dt><dd>{(activeIsolation?.closedPipeIds ?? []).join("、") || "无额外关闭"}</dd></div>
            <div><dt>预设阀门</dt><dd>{(activeIsolation?.valveIds ?? []).join("、") || "无"}</dd></div>
            <div><dt>启用节点</dt><dd>{scenario.activeSourceIds.join("、")}</dd></div>
            <div><dt>不可达节点</dt><dd>{impact?.unreachableNodeIds.length ?? "—"} 个</dd></div>
          </dl>
          <div className="evidence-block">
            <span className="muted small">隔离与情景证据</span>
            <EvidenceList ids={impact?.evidenceIds ?? []} />
          </div>
        </aside>
      </div>

      <div className="detail-grid lower">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">What-if sandbox</p>
              <h2>改参数并重算</h2>
              <p>可调整管龄、漏损次数、运行压力和管材；仅作用于当前模拟输入，不修改资产台账。</p>
            </div>
            {dirty ? <span className="dirty-badge">已修改</span> : <span className="clean-badge">原始输入</span>}
          </div>

          <div className="scenario-controls">
            {platformMode ? (
              <p className="inline-warning">平台模式只读取服务端评分；不支持在浏览器内改数重算。</p>
            ) : null}
            <label>
              管龄 <b>{age === null ? "未知" : `${age} 年`}</b>
              <input type="range" min="0" max="60" value={age ?? 0} disabled={platformMode} onChange={(event) => setAge(Number(event.target.value))} />
            </label>
            <label>
              漏损次数 <b>{leaks === null ? "未知" : `${leaks} 次`}</b>
              <input type="range" min="0" max="8" value={leaks ?? 0} disabled={platformMode} onChange={(event) => setLeaks(Number(event.target.value))} />
            </label>
            <label>
              运行压力
              <select value={pressure} disabled={platformMode} onChange={(event) => setPressure(event.target.value as PressureLevel)}>
                {PRESSURES.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
            <label>
              管材
              <select value={material} disabled={platformMode} onChange={(event) => setMaterial(event.target.value as PipeMaterial)}>
                {MATERIALS.map((value) => <option key={value}>{value}</option>)}
              </select>
            </label>
          </div>

          <div className="before-after">
            <div>
              <span>原始 DemoHealth</span>
              <b>{formatScore(baselineAssessment.score)}</b>
              <small>{baselineAssessment.grade}</small>
            </div>
            <span className={`delta ${delta === null ? "muted" : delta >= 0 ? "delta-up" : "delta-down"}`}>
              {evaluating ? "计算中…" : delta === null ? "无法比较" : `${signed(delta)} 分`}
            </span>
            <div>
              <span>当前模拟 DemoHealth</span>
              <b>{formatScore(current?.score ?? null)}</b>
              <small>{current?.grade ?? "数据不足"}</small>
            </div>
          </div>

          {current ? <div className="mini-gauge"><HealthGauge assessment={current} /></div> : null}
          {evaluationError ? <p className="inline-warning">{evaluationError}</p> : null}

          <div className="control-actions">
            <button className="button-ghost" onClick={resetWhatIf} disabled={!dirty}>
              <ResetIcon size={15} /> 恢复原始值
            </button>
            <span className="muted small">修改后自动重算；结果只代表当前模拟输入。</span>
          </div>

          {current ? (
            <details className="factor-details" open>
              <summary>查看当前因子贡献</summary>
              <FactorBars assessment={current} />
            </details>
          ) : null}
        </section>

        <aside className="panel decision-shell">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Plan constraints · C5-A</p>
              <h2>方案约束与比较</h2>
              <p>{platformMode ? "展示平台的预算、资源与约束检查；报告保留未知或不可行的结果。" : "预算、资源与依赖只是演示硬约束；不可行方案不能生成简报。"}</p>
            </div>
          </div>
            <DecisionPanel
              comparison={comparison ?? { status: "insufficient_data", scenarioId: scenario.id, planIds: [], rows: [], recommendedPlanId: null,
                decisionAlgorithmVersion: platformMode ? "platform" : DECISION_ALGORITHM_VERSION, referenceCostYuan: platformMode ? null : DECISION_REFERENCE_COST_YUAN, effectBasis: "synthetic_preset",
                constraints, utilityNote: comparisonError ?? impactError ?? "等待当前输入的完整计算结果。", limitations: [] }}
              constraints={constraints}
              selectedPlanId={selectedPlanId}
              onConstraintsChange={setConstraints}
              onSelectPlan={setSelectedPlanId}
              onGenerateReport={generateReport}
              disabled={!analysisReady}
              showReportAction={!platformMode}
            />
          {platformMode ? <div className="decision-footer">
            <p className="muted small">查看本次运行的已有报告；数据不足的结果也会保留。</p>
            <button data-testid="open-platform-report" className="button-primary"
              disabled={!reportRunId || !provider.report} onClick={generateReport}>{impact?.status === "partial" ? "查看拓扑报告" : "查看报告"}</button>
          </div> : null}
          {comparisonError ? <p className="inline-warning">{comparisonError}</p> : null}
          {impact?.status === "partial" ? <p className="inline-warning">{platformMode ? "拓扑结果不完整；可读取本次拓扑报告，当前没有方案比较或推荐。" : "拓扑条件不完整，不能推荐或生成简报。"}</p> : null}
          {reportError ? <p className="inline-warning">{reportError}</p> : null}
        </aside>
      </div>

      <TimelinePanel
        dataset={dataset}
        provider={provider}
        scenarioId={scenario.id}
        isolationPlanId={activeIsolation.id}
        impact={impact}
        impactError={impactError}
        planId={selectedPlanId}
        onPlanChange={setSelectedPlanId}
        onResult={setTimelineSnapshot}
      />
    </div>
  );
}
