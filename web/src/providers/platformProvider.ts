import type { DatasetView } from "../generated/platformOpenapi";
import type {
  AgentContext,
  AgentResponse,
  DemoConstraints,
  HealthAssessment,
  Pipe,
  PipeOverride,
  PlanComparison,
  PlanTimelineResult,
  ProviderMode,
  ServiceImpact,
  TimelineGate,
  TimelineRequest,
  WaterPulseDataset,
} from "../domain/types";
import {
  PlatformContractError,
  datasetViewSummary,
  toWaterPulseDataset,
  type DatasetAdapterResult,
} from "../adapters/platformDataset";
import {
  parseBackendReport,
  stableId,
  type BackendReportDocument,
} from "../adapters/platformReport";
import {
  isTerminal,
  parseDecisionResult,
  parseRunEnvelope,
  parseSimulationResult,
  toPlanComparison,
  toServiceImpact,
  yuanToFen,
  type RunEnvelope,
} from "../adapters/platformRuns";
import { toPlanTimelineResult, parseTimelineCheck, parseTimelineEvaluation, toTimelineGate } from "../adapters/platformTimeline";
import { ProviderUnavailableError, type WaterPulseProvider } from "./types";
import {
  PlatformTransportError,
  safeDetail,
  type PlatformTransport,
  type TransportRequest,
  type TransportResponse,
} from "./platform/transport";

const DEFAULT_HORIZON_HOURS = 8;
const DEFAULT_POLL_INTERVAL_MS = 250;
const DEFAULT_POLL_TIMEOUT_MS = 60_000;

export interface PlatformProviderOptions {
  transport: PlatformTransport;
  /** 平台 `dataset_id`（等于 snapshot_id）。 */
  datasetId: string;
  pollIntervalMs?: number;
  pollTimeoutMs?: number;
  sleep?: (ms: number) => Promise<void>;
  clock?: () => number;
  onUnavailable?: (message: string) => void;
  /** 显式关闭报告读取；默认使用已验收的后端 Report 合同。 */
  reportEnabled?: boolean;
}

function isDatasetView(value: unknown): value is DatasetView {
  if (!value || typeof value !== "object") return false;
  const row = value as Record<string, unknown>;
  const meta = row.meta as Record<string, unknown> | undefined;
  return Boolean(
    meta && typeof meta.snapshot_id === "string" && typeof meta.sha256 === "string" &&
    typeof meta.as_of === "string" && row.assessment_coverage && typeof row.assessment_coverage === "object" &&
    Array.isArray(row.pipes) && Array.isArray(row.nodes) && Array.isArray(row.edges) &&
    Array.isArray(row.services) && Array.isArray(row.isolation_plans) && Array.isArray(row.algorithms),
  );
}

function canonical(value: unknown): string {
  if (value === null || typeof value !== "object") return JSON.stringify(value) ?? "null";
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const entries = Object.entries(value as Record<string, unknown>).sort(([a], [b]) => a.localeCompare(b));
  return `{${entries.map(([key, item]) => `${JSON.stringify(key)}:${canonical(item)}`).join(",")}}`;
}

function hasOverride(override: PipeOverride | undefined): boolean {
  return Boolean(override && Object.keys(override).length > 0);
}

/**
 * 平台 Provider：live 表示已成功探测并读取；unavailable 表示未探测/已失败。
 * 所有业务数值来自平台，前端只做字段映射与状态派生，不重算评分、断连或恢复时间。
 */
export class PlatformProvider implements WaterPulseProvider {
  private ready = false;
  private dataset: WaterPulseDataset | null = null;
  private view: DatasetView | null = null;
  private adapted: DatasetAdapterResult | null = null;
  private readonly sleep: (ms: number) => Promise<void>;
  private readonly clock: () => number;

  constructor(private readonly options: PlatformProviderOptions) {
    this.sleep = options.sleep ?? ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
    this.clock = options.clock ?? (() => Date.now());
  }

  get mode(): ProviderMode {
    return this.ready ? "live" : "unavailable";
  }

  async probe(): Promise<boolean> {
    try {
      const response = await this.send({ method: "GET", path: "/api/v1/capabilities" });
      const body = response.body as Record<string, unknown> | null;
      if (response.status !== 200 || !body || typeof body.platform_version !== "string") {
        this.ready = false;
        return false;
      }
      this.ready = true;
      return true;
    } catch {
      this.ready = false;
      return false;
    }
  }

  private async send(request: TransportRequest): Promise<TransportResponse> {
    try {
      return await this.options.transport.send(request);
    } catch (caught) {
      this.ready = false;
      const message = caught instanceof Error && caught.message.includes("未登记路由")
        ? caught.message
        : "平台接口不可达，请重试或显式切换至合成演示模式。";
      this.options.onUnavailable?.(message);
      throw new ProviderUnavailableError(message);
    }
  }

  private assertOk(response: TransportResponse, path: string): unknown {
    if (response.status === 200 || response.status === 201 || response.status === 202) return response.body;
    if (response.status >= 500) {
      throw new ProviderUnavailableError(`平台返回 HTTP ${response.status}，不显示任何实时结论。`);
    }
    const body = (response.body ?? {}) as { detail?: unknown };
    throw new PlatformTransportError(response.status, safeDetail(body.detail), `平台拒绝了请求（HTTP ${response.status}）：${path}`);
  }

  private async loadView(force = false): Promise<DatasetView> {
    if (this.view && !force) return this.view;
    const response = await this.send({ method: "GET", path: `/api/v1/datasets/${encodeURIComponent(this.options.datasetId)}/view` });
    const body = this.assertOk(response, "dataset view");
    if (!isDatasetView(body)) throw new PlatformContractError("数据集视图缺少必需字段，拒绝按局部数据渲染");
    this.view = body;
    this.ready = true;
    return body;
  }

  async loadDataset(): Promise<WaterPulseDataset> {
    const view = await this.loadView(true);
    const adapted = toWaterPulseDataset(view);
    this.adapted = adapted;
    this.dataset = adapted.dataset;
    return adapted.dataset;
  }

  /** 数据集视图之外的可用性信息，供页面显示“缺什么”。 */
  async describeDataset(): Promise<{
    summary: ReturnType<typeof datasetViewSummary>;
    coverage: DatasetAdapterResult["coverage"];
    limitations: string[];
    evidenceIds: string[];
  }> {
    const adapted = this.adapted ?? toWaterPulseDataset(await this.loadView());
    return {
      summary: datasetViewSummary(this.view as DatasetView),
      coverage: adapted.coverage,
      limitations: adapted.limitations,
      evidenceIds: adapted.evidenceIds,
    };
  }

  async evaluate(pipe: Pipe, override: PipeOverride = {}): Promise<HealthAssessment> {
    if (hasOverride(override)) {
      throw new ProviderUnavailableError(
        "平台模式不支持在浏览器内改数重算；如需改数请提交 /assessment-runs 并等待 Run 结果。",
      );
    }
    const dataset = this.dataset ?? (await this.loadDataset());
    const cached = dataset.assessments[pipe.id];
    if (!cached) throw new PlatformContractError(`数据集没有管段 ${pipe.id} 的评分记录，未现场补算`);
    return cached;
  }

  private plan(isolationPlanId: string): DatasetView["isolation_plans"][number] {
    const view = this.view;
    if (!view) throw new PlatformContractError("尚未读取数据集视图");
    const plan = view.isolation_plans.find((item) => item.plan_id === isolationPlanId);
    if (!plan) throw new PlatformContractError(`隔离方案 ${isolationPlanId} 不属于当前数据集`);
    if (!plan.fault_edge_ids?.length) throw new PlatformContractError(`隔离方案 ${isolationPlanId} 没有故障边`);
    return plan;
  }

  private async run(path: string, body: Record<string, unknown>): Promise<RunEnvelope> {
    const response = await this.send({
      method: "POST",
      path,
      body,
      idempotencyKey: "unified-" + stableId(canonical(body)),
    });
    const accepted = this.assertOk(response, path);
    const envelope = parseRunEnvelope(accepted);
    return this.poll(envelope);
  }

  private async poll(initial: RunEnvelope): Promise<RunEnvelope> {
    let current = initial;
    const deadline = this.clock() + (this.options.pollTimeoutMs ?? DEFAULT_POLL_TIMEOUT_MS);
    while (!isTerminal(current.status)) {
      if (this.clock() > deadline) {
        throw new ProviderUnavailableError(`Run ${current.run_id} 在超时前未结束，未展示部分结果。`);
      }
      await this.sleep(this.options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS);
      const response = await this.send({ method: "GET", path: `/api/v1/runs/${encodeURIComponent(current.run_id)}` });
      current = parseRunEnvelope(this.assertOk(response, "run poll"));
    }
    return current;
  }

  private scenarioScenarioId(isolationPlanId: string): string {
    return `scn-${isolationPlanId}`;
  }

  async simulate(scenarioId: string, isolationPlanId?: string): Promise<ServiceImpact> {
    const view = await this.loadView();
    const dataset = this.dataset ?? (await this.loadDataset());
    const scenario = dataset.scenarios.find((item) => item.id === scenarioId);
    if (!scenario) throw new PlatformContractError(`未知情景：${scenarioId}`);
    const isolation = isolationPlanId ?? scenario.defaultIsolationPlanId;
    const plan = this.plan(isolation);
    const run = await this.run("/api/v1/simulation-runs", {
      snapshot_id: view.meta.snapshot_id,
      as_of: view.meta.as_of,
      fault_edge_id: plan.fault_edge_ids[0],
      isolation_plan_id: isolation,
      horizon_hours: DEFAULT_HORIZON_HOURS,
    });
    const result = parseSimulationResult(run);
    const impact = toServiceImpact(result, {
      scenarioId,
      isolationPlanId: isolation,
      faultPipeIds: scenario.failedPipeIds,
      services: dataset.services,
    });
    impact.runId = run.run_id;
    return impact;
  }

  async comparePlans(
    scenarioId: string,
    planIds?: string[],
    constraints?: DemoConstraints,
    isolationPlanId?: string,
  ): Promise<PlanComparison> {
    const view = await this.loadView();
    const dataset = this.dataset ?? (await this.loadDataset());
    const scenario = dataset.scenarios.find((item) => item.id === scenarioId);
    if (!scenario) throw new PlatformContractError(`未知情景：${scenarioId}`);
    if (!planIds || planIds.length < 2 || planIds.length > 3) {
      throw new PlatformContractError("平台方案比较要求 2—3 个明确候选；数据集视图不提供方案目录。");
    }
    if (new Set(planIds).size !== planIds.length) throw new PlatformContractError("候选方案不能重复");
    if (!constraints) throw new PlatformContractError("平台方案比较需要预算与资源容量");
    const isolation = isolationPlanId ?? scenario.defaultIsolationPlanId;
    const plan = this.plan(isolation);
    const run = await this.run("/api/v1/decision-runs", {
      snapshot_id: view.meta.snapshot_id,
      as_of: view.meta.as_of,
      fault_edge_id: plan.fault_edge_ids[0],
      isolation_plan_id: isolation,
      horizon_hours: DEFAULT_HORIZON_HOURS,
      plan_ids: [...planIds],
      budget_fen: yuanToFen(constraints.budgetYuan),
      resource_capacities: { ...constraints.resourceCapacities },
      mandatory_action_ids: [],
    });
    const comparison = toPlanComparison(parseDecisionResult(run), constraints, scenarioId);
    if (constraints.confirmedDependencies.length > 0) {
      comparison.limitations = [
        ...comparison.limitations,
        "平台用 mandatory_action_ids 表达强制措施，不建模演示依赖开关；依赖确认不影响本次结果。",
      ];
    }
    comparison.runId = run.run_id;
    return comparison;
  }

  private timelineBody(request: TimelineRequest): Record<string, unknown> {
    const view = this.view;
    if (!view) throw new PlatformContractError("尚未读取数据集视图");
    const plan = this.plan(request.isolationPlanId);
    return {
      snapshot_id: view.meta.snapshot_id,
      fault_edge_id: plan.fault_edge_ids[0],
      isolation_plan_id: request.isolationPlanId,
      plan_id: request.planId,
      target_service_ids: [...(request.targetServiceIds ?? [])],
      horizon_hours: request.horizonHours,
      action_schedule: (request.actionSchedule ?? []).map((item) => ({
        action_id: item.actionId,
        ...(item.finishOffsetHours === undefined ? {} : { finish_offset_hours: item.finishOffsetHours }),
        ...(item.startOffsetHours === undefined ? {} : { start_offset_hours: item.startOffsetHours }),
        ...(item.durationHours === undefined ? {} : { duration_hours: item.durationHours }),
      })),
    };
  }

  async checkTimeline(request: TimelineRequest): Promise<TimelineGate> {
    await this.loadView();
    const response = await this.send({
      method: "POST", path: "/api/v1/timeline/check", body: this.timelineBody(request),
      idempotencyKey: "unified-" + stableId(canonical(this.timelineBody(request))),
    });
    return toTimelineGate(parseTimelineCheck(this.assertOk(response, "timeline check")));
  }

  async timeline(request: TimelineRequest): Promise<PlanTimelineResult> {
    await this.loadView();
    const body = this.timelineBody(request);
    const response = await this.send({
      method: "POST", path: "/api/v1/timeline/evaluate", body,
      idempotencyKey: "unified-" + stableId(canonical(body)),
    });
    return toPlanTimelineResult(request, parseTimelineEvaluation(this.assertOk(response, "timeline evaluate")));
  }

  /** Only read the accepted Report endpoint; no view refresh, polling, or calculation. */
  async report(runId: string): Promise<BackendReportDocument> {
    if (this.options.reportEnabled === false) {
      throw new ProviderUnavailableError("当前配置未启用平台报告读取。");
    }
    if (typeof runId !== "string" || runId.length === 0 || runId.trim() !== runId) {
      throw new PlatformContractError("报告缺少有效的 Run 编号");
    }
    const path = `/api/v1/runs/${encodeURIComponent(runId)}/report`;
    const response = await this.send({ method: "GET", path });
    return parseBackendReport(this.assertOk(response, path), runId);
  }

  /** Compatibility entry: the server owns the report; client timelines cannot be attached. */
  async reportForRun(run: RunEnvelope, timeline?: PlanTimelineResult): Promise<BackendReportDocument> {
    if (timeline !== undefined) throw new PlatformContractError("平台报告只能读取服务端结果，不能附加客户端时间线");
    return this.report(run.run_id);
  }

  async ask(_question: string, _context: AgentContext): Promise<AgentResponse> {
    throw new ProviderUnavailableError(
      "智能体未启用：平台 Agent 尚未通过安全复审，本轮不调用真实模型，也不绕开身份与数据权限。",
    );
  }
}
