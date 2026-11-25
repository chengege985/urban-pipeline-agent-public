import type {
  DecisionSession,
  HealthAssessment,
  IsolationPlan,
  MaintenancePlan,
  NetworkScenario,
  Pipe,
  PlanComparison,
  PlanComparisonRow,
  PlanTimelineResult,
  ServiceImpact,
  TimelineGate,
  WaterPulseDataset,
} from "../domain/types";
import { simulateServiceImpact } from "./network";
import { compareDemoPlans } from "./plans";
import { timelineBasisKey, timelineMatchesRequest } from "./timelineContext";
import { DECISION_ALGORITHM_VERSION, TIMELINE_ALGORITHM_VERSION } from "../domain/constants";

export interface ReportExport {
  resultOrigin: "frozen" | "reconstructed_reference";
  stale: boolean;
  staleReasons: string[];
  actionSchedule?: import("../domain/types").TimelineActionSchedule[];
  timelineDataVersion?: string;
  timelineBasisKey?: string;
  exportedAt: string;
  session: DecisionSession;
  pipe: Pipe;
  assessment: HealthAssessment;
  scenario: NetworkScenario;
  isolationPlan?: IsolationPlan;
  impact: ServiceImpact;
  comparison: PlanComparison;
  selectedPlan?: MaintenancePlan;
  selectedRow?: PlanComparisonRow;
  timeline?: PlanTimelineResult;
  timelineGate?: TimelineGate;
  targetServiceIds?: string[];
  horizonHours?: number;
  evidenceIds: string[];
  limitations: string[];
  dataVersion: string;
  algorithmVersion: string;
}

export interface DecisionReport {
  dataVersion: string;
  algorithmVersion: string;
  resultOrigin: "frozen" | "reconstructed_reference";
  staleReasons: string[];
  session: DecisionSession;
  pipe: Pipe;
  assessment: HealthAssessment;
  scenario: NetworkScenario;
  isolationPlan?: IsolationPlan;
  impact: ServiceImpact;
  comparison: PlanComparison;
  selectedPlan?: MaintenancePlan;
  selectedRow?: PlanComparisonRow;
  timeline?: PlanTimelineResult;
  timelineGate?: TimelineGate;
  targetServiceIds?: string[];
  horizonHours?: number;
  evidenceIds: string[];
  stale: boolean;
}

export function buildDecisionReport(
  dataset: WaterPulseDataset,
  session: DecisionSession,
): DecisionReport | null {
  const pipe = dataset.pipes.find((item) => item.id === session.pipeId);
  const scenario = dataset.scenarios.find((item) => item.id === session.scenarioId);
  if (!pipe || !scenario) return null;

  const isolationPlan = dataset.isolationPlans.find((item) => item.id === session.isolationPlanId && item.scenarioId === scenario.id);
  if (!isolationPlan) return null;
  const frozen = session.snapshot;
  if (frozen) {
    if (frozen.pipe.id !== session.pipeId || frozen.assessment.pipeId !== session.pipeId ||
        frozen.scenario.id !== session.scenarioId || frozen.impact.scenarioId !== session.scenarioId ||
        frozen.isolationPlan.id !== session.isolationPlanId || frozen.impact.isolationPlanId !== session.isolationPlanId ||
        frozen.comparison.scenarioId !== session.scenarioId || frozen.selectedPlan.id !== session.selectedPlanId ||
        Boolean(frozen.timeline && frozen.timeline.planId !== frozen.selectedPlan.id)) return null;
    const staleReasons: string[] = [];
    if (session.dataVersion !== dataset.meta.dataVersion) staleReasons.push("主数据版本已变化");
    if (session.algorithmVersion !== dataset.meta.modelVersion) staleReasons.push("健康评分版本已变化");
    if (session.decisionAlgorithmVersion !== DECISION_ALGORITHM_VERSION) staleReasons.push("决策算法版本已变化");
    if (frozen.timeline) {
      if (!frozen.timelineDataVersion || frozen.timelineDataVersion !== dataset.timeline.version ||
          frozen.timeline.timelineDataVersion !== frozen.timelineDataVersion) staleReasons.push("时间线数据版本已变化或未冻结");
      if (frozen.timeline.algorithmVersion !== TIMELINE_ALGORITHM_VERSION) staleReasons.push("时间线算法版本已变化");
      if (frozen.timeline.asOf !== dataset.timeline.asOf || frozen.timelineBasisKey !== timelineBasisKey(dataset)) staleReasons.push("时间线计算基准已变化或未冻结");
      if (frozen.horizonHours === undefined || !timelineMatchesRequest(frozen.timeline, {
        scenarioId: session.scenarioId, isolationPlanId: session.isolationPlanId, planId: session.selectedPlanId,
        targetServiceIds: frozen.targetServiceIds, horizonHours: frozen.horizonHours ?? 8,
        actionSchedule: frozen.actionSchedule,
      })) staleReasons.push("时间线与冻结请求不一致");
    }
    return {
      session,
      ...structuredClone(frozen),
      dataVersion: session.dataVersion, algorithmVersion: session.algorithmVersion,
      resultOrigin: "frozen", staleReasons,
      selectedRow: structuredClone(frozen.comparison.rows.find((row) => row.planId === session.selectedPlanId)),
      evidenceIds: [...session.evidenceIds],
      stale: staleReasons.length > 0,
    };
  }
  const impact = simulateServiceImpact(
    dataset.pipes,
    dataset.nodes,
    dataset.services,
    scenario,
    isolationPlan,
  );
  const comparison = compareDemoPlans(scenario.id, impact, dataset.plans, {
    budgetYuan: session.budgetYuan,
    resourceCapacities: session.resourceCapacities,
    confirmedDependencies: session.confirmedDependencies,
  });
  const selectedPlan = dataset.plans.find((item) => item.id === session.selectedPlanId);
  const selectedRow = comparison.rows.find((row) => row.planId === session.selectedPlanId);
  const assessment = dataset.assessments[pipe.id];
  const evidenceIds = [
    ...new Set([
      `data:${pipe.id}`,
      `rule:demo-health-${dataset.meta.modelVersion}`,
      ...impact.evidenceIds,
      ...(isolationPlan?.evidenceIds ?? []),
      ...(selectedPlan?.evidenceIds ?? []),
    ]),
  ];
  // Older sessions lack a frozen result and must be regenerated before review.
  const stale = true;

  return {
    session,
    dataVersion: dataset.meta.dataVersion, algorithmVersion: dataset.meta.modelVersion,
    resultOrigin: "reconstructed_reference", staleReasons: ["缺少原始冻结结果；以下仅为按当前演示模型重建的参考结果"],
    pipe,
    assessment,
    scenario,
    isolationPlan,
    impact,
    comparison,
    selectedPlan,
    selectedRow,
    evidenceIds,
    stale,
  };
}

export function buildReportExport(
  report: DecisionReport,
  _dataset: WaterPulseDataset,
  exportedAt = new Date().toISOString(),
): ReportExport {
  return {
    exportedAt,
    resultOrigin: report.resultOrigin, stale: report.stale, staleReasons: [...report.staleReasons],
    actionSchedule: report.session.snapshot?.actionSchedule,
    timelineDataVersion: report.session.snapshot?.timelineDataVersion,
    timelineBasisKey: report.session.snapshot?.timelineBasisKey,
    session: report.session,
    pipe: report.pipe,
    assessment: report.assessment,
    scenario: report.scenario,
    ...(report.isolationPlan ? { isolationPlan: report.isolationPlan } : {}),
    impact: report.impact,
    comparison: report.comparison,
    ...(report.selectedPlan ? { selectedPlan: report.selectedPlan } : {}),
    ...(report.selectedRow ? { selectedRow: report.selectedRow } : {}),
    ...(report.timeline ? { timeline: report.timeline } : {}),
    ...(report.timelineGate ? { timelineGate: report.timelineGate } : {}),
    ...(report.targetServiceIds ? { targetServiceIds: report.targetServiceIds } : {}),
    ...(report.horizonHours !== undefined ? { horizonHours: report.horizonHours } : {}),
    evidenceIds: report.evidenceIds,
    limitations: [
      "合成数据演示，不控制真实设施。",
      "DemoHealth 不是正式健康度、失效概率、RUL 或水力计算结果。",
      "服务恢复时间只是计划图通路回放，不是水力、水质、实际供水量或现场执行结论。",
      "审核通过仅代表本地演示记录获得确认，不代表真实维修工单已批准、下发或执行。",
    ],
    dataVersion: report.dataVersion,
    algorithmVersion: report.algorithmVersion,
  };
}
