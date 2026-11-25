import type { JsonValue, ReportDocument, ReportAssessmentRef, FeedbackRecord } from "../generated/platformOpenapi";
import type { HealthAssessment, PlanTimelineResult } from "../domain/types";
import type { DecisionRunResult, RunEnvelope, SimulationRunResult } from "./platformRuns";
import { isTerminal } from "./platformRuns";
import { PlatformContractError } from "./platformDataset";

/**
 * 旧版前端投影，仅保留历史兼容测试；PlatformProvider 不调用此构建器。
 * 正式平台报告使用下方 BackendReportDocument，原样消费服务端响应。
 */
export interface PlatformReportDocument {
  schemaVersion: "unified-report-1.0";
  source: "existing_run_only";
  recomputation: "none";
  reportId: string;
  runId: string;
  runKind: RunEnvelope["kind"];
  runStatus: RunEnvelope["status"];
  runCreatedAt: string;
  runFinishedAt: string | null;
  businessStatus: string;
  dataset: {
    snapshotId: string;
    dataVersion: string;
    asOf: string;
    dataMode: string;
    sourceStatus: string;
    datasetStatus: string;
  };
  algorithms: Array<{ capabilityId: string; version: string; validationStatus: string }>;
  scenario: Record<string, unknown> | null;
  pipe: {
    pipeId: string;
    modelFamily: string;
    modelVersion: string;
    rs: number | null;
    hs: number | null;
    grade: string;
    completenessPercent: number | null;
    evidenceRefs: string[];
    recomputed: false;
  } | null;
  simulation?: SimulationRunResult;
  decision?: DecisionRunResult;
  timeline?: PlanTimelineResult;
  evidenceIds: string[];
  limitations: string[];
}

export interface ReportInputs {
  run: RunEnvelope;
  dataset: {
    datasetId: string;
    snapshotId: string;
    dataMode: string;
    asOf: string;
    dataVersion: string;
    status: string;
  };
  sourceStatus: string;
  assessment?: HealthAssessment | null;
  simulation?: SimulationRunResult;
  decision?: DecisionRunResult;
  timeline?: PlanTimelineResult;
  extraLimitations?: string[];
}

/** FNV-1a 64 位（十六进制），仅用于稳定报告标识，不作为安全哈希。 */
export function stableId(input: string): string {
  let hash = 0xcbf29ce484222325n;
  const prime = 0x100000001b3n;
  const mask = 0xffffffffffffffffn;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= BigInt(input.charCodeAt(index));
    hash = (hash * prime) & mask;
  }
  return hash.toString(16).padStart(16, "0") + (hash >> 32n).toString(16).padStart(8, "0").slice(0, 8);
}

const BASE_LIMITATIONS = [
  "报告只引用既有 Run 与冻结数据集视图，未重新计算健康度、拓扑影响、方案效用或恢复时间。",
  "legacy_demo_0.1.1 的 Rs/Hs 是简化演示评分，不是正式 P/H、失效概率或 RUL。",
  "拓扑通路结果不代表压力、流量、水质达标或实际供水量。",
  "本报告不是业务验收、预算审批、派单或关阀依据。",
];

export function buildPlatformReport(inputs: ReportInputs): PlatformReportDocument {
  const { run, dataset } = inputs;
  if (!isTerminal(run.status)) {
    throw new PlatformContractError(`Run 尚未结束（${run.status}），不能生成报告`);
  }
  if (!run.result) throw new PlatformContractError("Run 没有可引用的结果");
  const requestSnapshot = run.request.snapshot_id;
  if (typeof requestSnapshot !== "string" || requestSnapshot !== dataset.snapshotId) {
    throw new PlatformContractError("Run 请求与数据集快照不一致，不能拼接报告");
  }

  const algorithms: PlatformReportDocument["algorithms"] = [];
  if (inputs.simulation) {
    algorithms.push({
      capabilityId: "given_isolation_topology",
      version: inputs.simulation.algorithm_version,
      validationStatus: inputs.simulation.validation_status,
    });
  }
  if (inputs.decision) {
    algorithms.push({
      capabilityId: "candidate_plan_ranking",
      version: inputs.decision.decision_algorithm_version,
      validationStatus: inputs.decision.validation_status,
    });
  }
  if (inputs.timeline) {
    algorithms.push({
      capabilityId: "plan_service_timeline",
      version: inputs.timeline.algorithmVersion,
      validationStatus: "not_business_validated",
    });
  }
  if (algorithms.length === 0) throw new PlatformContractError("Run 类型没有可引用的算法结果，不能生成报告");

  const pipe = inputs.assessment && inputs.assessment.score !== null
    ? {
        pipeId: inputs.assessment.pipeId,
        modelFamily: inputs.assessment.version.split("/")[0] ?? "unknown",
        modelVersion: inputs.assessment.version.split("/")[1] ?? "unknown",
        rs: null,
        hs: inputs.assessment.score,
        grade: inputs.assessment.grade,
        completenessPercent: inputs.assessment.confidence,
        evidenceRefs: [] as string[],
        recomputed: false as const,
      }
    : null;

  const evidenceIds = [...new Set([
    `snapshot:${dataset.snapshotId}`,
    `run:${run.run_id}`,
    ...(inputs.simulation?.evidence_refs ?? []),
    ...(inputs.decision?.evidence_refs ?? []),
    ...(inputs.timeline?.evidenceIds ?? []),
  ])];

  const limitations = [...BASE_LIMITATIONS, ...(inputs.extraLimitations ?? [])];
  if (!pipe) limitations.push("该 Run 没有对应管段的合法评分；报告不补造评分。");
  if (!inputs.timeline) limitations.push("本次报告未附服务时间线结果。");
  if (inputs.decision && inputs.decision.recommended_plan_id === null) {
    limitations.push("平台未给出推荐方案：候选存在未知结果或全部不可行。");
  }

  const identity = [
    run.run_id, dataset.snapshotId, dataset.dataVersion, run.status,
    ...algorithms.map((item) => `${item.capabilityId}@${item.version}`),
  ].join("|");

  return {
    schemaVersion: "unified-report-1.0",
    source: "existing_run_only",
    recomputation: "none",
    reportId: "report-" + stableId(identity),
    runId: run.run_id,
    runKind: run.kind,
    runStatus: run.status,
    runCreatedAt: run.created_at,
    runFinishedAt: run.finished_at,
    businessStatus: run.status,
    dataset: {
      snapshotId: dataset.snapshotId,
      dataVersion: dataset.dataVersion,
      asOf: dataset.asOf,
      dataMode: dataset.dataMode,
      sourceStatus: inputs.sourceStatus,
      datasetStatus: dataset.status,
    },
    algorithms,
    scenario: (run.request as Record<string, unknown>) ?? null,
    pipe,
    ...(inputs.simulation ? { simulation: inputs.simulation } : {}),
    ...(inputs.decision ? { decision: inputs.decision } : {}),
    ...(inputs.timeline ? { timeline: inputs.timeline } : {}),
    evidenceIds,
    limitations,
  };
}


/** The accepted backend Report contract. No client-built fields or calculations. */
export interface BackendReportDocument extends ReportDocument {
  report_schema_version: "unified-report-1.0";
  source: "existing_run_only";
  recomputation: "none";
  dataset: Record<string, JsonValue> & {
    snapshot_id: string;
    data_version: string;
    as_of: string;
    data_mode: string;
    source_status: string;
    schema_version: string;
    name: string;
  };
  scenario: Record<string, JsonValue> | null;
  pipe: ReportAssessmentRef | null;
  simulation: Record<string, JsonValue> | null;
  decision: Record<string, JsonValue> | null;
  timeline: Record<string, JsonValue> | null;
  feedback: FeedbackRecord[];
  evidence_ids: string[];
  limitations: string[];
}

function reportObject(value: unknown, path: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PlatformContractError(`报告字段 ${path} 不是对象`);
  }
  return value as Record<string, unknown>;
}

function reportString(value: unknown, path: string): asserts value is string {
  if (typeof value !== "string") {
    throw new PlatformContractError(`报告字段 ${path} 不是文本`);
  }
}

function reportText(value: unknown, path: string): asserts value is string {
  if (typeof value !== "string" || value.length === 0) {
    throw new PlatformContractError(`报告字段 ${path} 缺失或不是文本`);
  }
}

function reportTexts(value: unknown, path: string): asserts value is string[] {
  if (!Array.isArray(value) || value.some((x) => typeof x !== "string")) {
    throw new PlatformContractError(`报告字段 ${path} 不是文本数组`);
  }
}

function reportNumber(value: unknown, path: string): void {
  if (value !== null && (typeof value !== "number" || !Number.isFinite(value))) {
    throw new PlatformContractError(`报告字段 ${path} 不是有限数值或 null`);
  }
}

/** Validate the response envelope; return its original object without filling defaults. */
export function parseBackendReport(value: unknown, requestedRunId: string): BackendReportDocument {
  const row = reportObject(value, "ReportDocument");
  if (row.report_schema_version !== "unified-report-1.0" ||
      row.source !== "existing_run_only" || row.recomputation !== "none") {
    throw new PlatformContractError("报告版本、来源或重算声明不符合已验收合同");
  }
  reportString(row.report_id, "report_id");
  reportText(row.run_id, "run_id");
  if (row.run_id !== requestedRunId) throw new PlatformContractError("报告 Run 与请求不一致");
  if (!["assessment", "simulation", "decision", "assistant"].includes(String(row.run_kind))) {
    throw new PlatformContractError("报告 Run 类型不支持");
  }
  if (!["succeeded", "partial", "insufficient_data", "infeasible", "failed"].includes(String(row.run_status))) {
    throw new PlatformContractError("报告 Run 未完成或状态不支持");
  }
  reportString(row.business_status, "business_status");
  const dataset = reportObject(row.dataset, "dataset");
  for (const key of ["snapshot_id", "data_version", "as_of", "data_mode", "source_status", "schema_version"]) {
    reportText(dataset[key], `dataset.${key}`);
  }
  reportString(dataset.name, "dataset.name");
  if (!/^[a-f0-9]{64}$/.test(dataset.data_version as string)) {
    throw new PlatformContractError("报告数据版本不是完整内容哈希");
  }
  if (!Array.isArray(row.algorithms)) throw new PlatformContractError("报告缺少算法版本列表");
  for (const item of row.algorithms) {
    const algorithm = reportObject(item, "algorithms");
    for (const key of ["capability_id", "semantics", "version", "validation_status"]) reportString(algorithm[key], `algorithms.${key}`);
  }
  for (const key of ["scenario", "simulation", "decision", "timeline"]) {
    if (row[key] !== null) reportObject(row[key], key);
  }
  for (const key of ["simulation", "decision"]) {
    if (row[key] !== null) {
      const result = row[key] as Record<string, unknown>;
      if (result.snapshot_id !== dataset.snapshot_id || result.run_id !== row.run_id) {
        throw new PlatformContractError("报告结果与冻结快照或 Run 不一致");
      }
    }
  }
  if ((row.run_kind === "simulation" && row.simulation === null) ||
      (row.run_kind === "decision" && row.decision === null)) {
    throw new PlatformContractError("报告没有对应的服务端结果");
  }
  if (row.pipe !== null) {
    const pipe = reportObject(row.pipe, "pipe");
    reportText(pipe.pipe_id, "pipe.pipe_id");
    for (const key of ["model_family", "model_version", "grade"]) reportString(pipe[key], `pipe.${key}`);
    for (const key of ["rs", "hs", "completeness_percent"]) reportNumber(pipe[key], `pipe.${key}`);
    reportTexts(pipe.evidence_refs, "pipe.evidence_refs");
    if (pipe.recomputed !== false) throw new PlatformContractError("报告评分不是既有结果引用");
  }
  if (!Array.isArray(row.feedback)) throw new PlatformContractError("报告缺少反馈记录列表");
  reportTexts(row.evidence_ids, "evidence_ids");
  reportTexts(row.limitations, "limitations");
  if (!row.evidence_ids.includes(`run:${requestedRunId}`) || !row.evidence_ids.includes(`snapshot:${dataset.snapshot_id}`)) {
    throw new PlatformContractError("报告缺少当前 Run 或 Snapshot 的证据锚点");
  }
  return value as BackendReportDocument;
}
