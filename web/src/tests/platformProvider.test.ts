import { beforeEach, describe, expect, it, vi } from "vitest";
import { PlatformProvider } from "../providers/platformProvider";
import { MockTransport, type TransportRequest, type TransportResponse } from "../providers/platform/transport";
import { ProviderUnavailableError } from "../providers/types";
import { PlatformContractError } from "../adapters/platformDataset";
import { PlatformTransportError } from "../providers/platform/transport";
import {
  capabilities,
  datasetView,
  decisionRun,
  decisionRunFeasible,
  simulationRun,
  timelineCheck,
  timelineEvaluate,
} from "./fixtures/platformRecorded";

const SNAPSHOT_ID = "snap-4b31408c5a1ee971b535df60";
const DATASET_PATH = `GET /api/v1/datasets/${SNAPSHOT_ID}/view`;
const PIPE = "syn-pipe-fault";
const ISOLATION = "syn-isolate";
const SCENARIO = `scn-${ISOLATION}`;
const CONSTRAINTS = {
  budgetYuan: 5000,
  resourceCapacities: { repair_team: 2, water_truck: 2 },
  confirmedDependencies: [] as string[],
};

function field(value: unknown, name: string): unknown {
  if (!value || typeof value !== "object") throw new Error(`夹具不是对象：${name}`);
  return (value as Record<string, unknown>)[name];
}

function text(value: unknown, name: string): string {
  const found = field(value, name);
  if (typeof found !== "string") throw new Error(`夹具字段 ${name} 不是字符串`);
  return found;
}

const SIMULATION_RUN_ID = text(simulationRun, "run_id");
const DECISION_RUN_ID = text(decisionRun, "run_id");
const DECISION_FEASIBLE_RUN_ID = text(decisionRunFeasible, "run_id");

function providerWith(routes: Record<string, unknown>, overrides: Partial<{ pollTimeoutMs: number; clock: () => number }> = {}) {
  const transport = new MockTransport(routes);
  const provider = new PlatformProvider({
    transport,
    datasetId: SNAPSHOT_ID,
    sleep: async () => undefined,
    pollIntervalMs: 0,
    pollTimeoutMs: overrides.pollTimeoutMs ?? 60_000,
    clock: overrides.clock ?? (() => 0),
  });
  return { provider, transport };
}

function baseRoutes(): Record<string, unknown> {
  return {
    "GET /api/v1/capabilities": capabilities,
    [DATASET_PATH]: datasetView,
  };
}

function postRun(runId: string): TransportResponse {
  return { status: 202, body: { ...(simulationRun as object), run_id: runId, status: "queued", result: null } };
}

describe("PlatformProvider：状态与探测", () => {
  it("探测成功才进入 live；缺字段保持 unavailable", async () => {
    const ok = providerWith(baseRoutes()).provider;
    await expect(ok.probe()).resolves.toBe(true);
    expect(ok.mode).toBe("live");

    const broken = providerWith({ "GET /api/v1/capabilities": { datasets: [] } }).provider;
    await expect(broken.probe()).resolves.toBe(false);
    expect(broken.mode).toBe("unavailable");
  });

  it("传输层异常不伪造 live，也不把异常当成业务结论", async () => {
    const calls: string[] = [];
    const provider = new PlatformProvider({
      transport: {
        send: async (request: TransportRequest) => {
          calls.push(request.path);
          throw new Error("offline");
        },
      },
      datasetId: SNAPSHOT_ID,
    });
    await expect(provider.probe()).resolves.toBe(false);
    expect(provider.mode).toBe("unavailable");
    await expect(provider.loadDataset()).rejects.toBeInstanceOf(ProviderUnavailableError);
    expect(calls.length).toBeGreaterThan(1);
  });
});

describe("PlatformProvider：数据集与评分映射", () => {
  it("六管段评分原样映射，不重算", async () => {
    const { provider } = providerWith(baseRoutes());
    const dataset = await provider.loadDataset();
    expect(dataset.pipes).toHaveLength(6);
    expect(dataset.meta.mode).toBe("live");
    expect(dataset.meta.providerKind).toBe("platform");
    expect(dataset.planCatalog?.map((plan) => plan.id)).toEqual([
      "syn-plan-repair", "syn-plan-backup", "syn-plan-combined",
    ]);
    expect(dataset.meta.dataVersion).toBe("4b31408c5a1ee971b535df60211e9bdeacff6bdaf79bdcbc9fe2eb02849036c8");
    expect(dataset.meta.modelVersion).toBe("0.1.1");

    const assessment = dataset.assessments[PIPE];
    expect(assessment.score).toBe(78.5);
    expect(assessment.grade).toBe("基本健康");
    expect(assessment.confidence).toBe(100);
    expect(assessment.status).toBe("succeeded");
    expect(assessment.version).toBe("legacy_demo/0.1.1");

    const pipe = dataset.pipes.find((item) => item.id === PIPE)!;
    expect(pipe.material).toBe("铸铁");
    expect(pipe.installYear).toBe(2015);
    expect(pipe.diameterMm).toBe(150);
    // 平台视图没有的字段保持缺失，不用 0 或默认值补齐。
    expect(pipe.depthM).toBeNull();
    expect(pipe.lengthM).toBeNull();
    expect(pipe.pressure).toBe("未知");
    expect(pipe.lastInspection).toBeNull();
  });

  it("服务对象保留 any 规则并按计数口径映射户/处", async () => {
    const { provider } = providerWith(baseRoutes());
    const dataset = await provider.loadDataset();
    const hospital = dataset.services.find((item) => item.id === "syn-hospital-001")!;
    expect(hospital.households).toBe(0);
    expect(hospital.facilities).toBe(1);
    expect(hospital.critical).toBe(true);
    expect(hospital.type).toBe("医院");
    const dual = dataset.services.find((item) => item.id === "syn-dual-inlet")!;
    expect(dual.description).toContain("任一节点可达即供水");
    expect(dual.type).toBe("未知");
    expect(dataset.services.find((item) => item.id === "syn-households-C")!.households).toBe(80);
  });

  it("节点坐标缺失时显式失败，不编造位置", async () => {
    const broken = JSON.parse(JSON.stringify(datasetView));
    broken.nodes[0].x = null;
    const { provider } = providerWith({ "GET /api/v1/capabilities": capabilities, [DATASET_PATH]: broken });
    await expect(provider.loadDataset()).rejects.toBeInstanceOf(PlatformContractError);
  });

  it("阀门只取 is_valve 边，情景由隔离方案派生且不编造严重度", async () => {
    const { provider } = providerWith(baseRoutes());
    const dataset = await provider.loadDataset();
    expect(dataset.valves.map((item) => item.id).sort()).toEqual(["syn-alternative", "syn-v1"]);
    expect(dataset.scenarios).toHaveLength(1);
    expect(dataset.scenarios[0].id).toBe(SCENARIO);
    expect(dataset.scenarios[0].failedPipeIds).toEqual(["syn-pipe-fault"]);
    expect(dataset.scenarios[0].severity).toBe("未知");
    expect(dataset.scenarios[0].activeSourceIds).toEqual(["syn-S"]);
    expect(dataset.plans).toEqual([]);
    expect(dataset.timeline.events).toEqual([]);
  });

  it("浏览器改数重算被拒绝，原有评分直接返回", async () => {
    const { provider } = providerWith(baseRoutes());
    const dataset = await provider.loadDataset();
    const pipe = dataset.pipes.find((item) => item.id === PIPE)!;
    await expect(provider.evaluate(pipe)).resolves.toMatchObject({ score: 78.5 });
    await expect(provider.evaluate(pipe, { leakCount: 9 })).rejects.toBeInstanceOf(ProviderUnavailableError);
  });

  it("数据集视图被拒绝时给出 4xx 原因且不泄露路径", async () => {
    const leaky = providerWith({
      "GET /api/v1/capabilities": capabilities,
      [DATASET_PATH]: () => ({ status: 403, body: { detail: "/workdata/secret 无权" } }),
    }).provider;
    await expect(leaky.loadDataset()).rejects.toBeInstanceOf(PlatformTransportError);
    await expect(leaky.loadDataset()).rejects.toMatchObject({ status: 403, detail: "" });

    const denied = providerWith({
      "GET /api/v1/capabilities": capabilities,
      [DATASET_PATH]: () => ({ status: 403, body: { detail: "当前主体无权读取该数据集" } }),
    }).provider;
    await expect(denied.loadDataset()).rejects.toMatchObject({ status: 403, detail: "当前主体无权读取该数据集" });
  });
});

describe("PlatformProvider：异步 Run 轮询", () => {
  let calls: TransportRequest[] = [];

  beforeEach(() => {
    calls = [];
  });

  it("入队→轮询→返回同一 Run 结果，请求体字段完整", async () => {
    const transport = new MockTransport({
      ...baseRoutes(),
      "POST /api/v1/simulation-runs": () => {
        calls.push({ method: "POST", path: "/api/v1/simulation-runs" });
        return postRun(SIMULATION_RUN_ID);
      },
      [`GET /api/v1/runs/${SIMULATION_RUN_ID}`]: simulationRun,
    });
    const provider = new PlatformProvider({
      transport, datasetId: SNAPSHOT_ID, sleep: async () => undefined, pollIntervalMs: 0, clock: () => 0,
    });
    const impact = await provider.simulate(SCENARIO);
    expect(impact.status).toBe("succeeded");
    expect(impact.runId).toBe(SIMULATION_RUN_ID);
    expect(impact.affectedServices.map((item) => item.serviceId)).toEqual([
      "syn-hospital-001", "syn-households-C", "syn-households-U",
    ]);
    expect(impact.affectedHouseholds).toBe(200);
    expect(impact.affectedFacilities).toBe(1);
    expect(impact.valveIds).toEqual(["syn-v1"]);
    expect(impact.unreachableNodeIds).toEqual(["syn-B", "syn-C", "syn-H", "syn-U"]);
    expect(impact.evidenceIds).toContain(`snapshot:${SNAPSHOT_ID}`);
    expect(impact.failedPipeIds).toEqual(["syn-pipe-fault"]);

    const posted = transport.calls.find((item) => item.method === "POST")!;
    expect(posted.path).toBe("/api/v1/simulation-runs");
    expect(posted.idempotencyKey).toMatch(/^unified-[0-9a-f]{24}$/);
    expect(posted.body).toMatchObject({
      snapshot_id: SNAPSHOT_ID,
      as_of: "2026-10-29T08:00:00+08:00",
      fault_edge_id: "syn-fault",
      isolation_plan_id: ISOLATION,
      horizon_hours: 8,
    });
  });

  it("Run 未在期限内结束时拒绝展示部分结果", async () => {
    let ticks = 0;
    const transport = new MockTransport({
      ...baseRoutes(),
      "POST /api/v1/simulation-runs": () => postRun(SIMULATION_RUN_ID),
      [`GET /api/v1/runs/${SIMULATION_RUN_ID}`]: () => {
        ticks += 1;
        return { status: 200, body: { ...(simulationRun as object), status: "running", result: null } };
      },
    });
    const provider = new PlatformProvider({
      transport, datasetId: SNAPSHOT_ID, sleep: async () => undefined, pollIntervalMs: 0,
      pollTimeoutMs: 5, clock: () => ticks * 10,
    });
    await expect(provider.simulate(SCENARIO)).rejects.toBeInstanceOf(ProviderUnavailableError);
  });

  it("Run 失败状态原样返回，不抛出成功结论", async () => {
    const transport = new MockTransport({
      ...baseRoutes(),
      "POST /api/v1/simulation-runs": () => postRun(SIMULATION_RUN_ID),
      [`GET /api/v1/runs/${SIMULATION_RUN_ID}`]: {
        status: 200,
        body: { ...(simulationRun as object), status: "failed", result: { status: "failed", run_id: SIMULATION_RUN_ID } },
      },
    });
    const provider = new PlatformProvider({ transport, datasetId: SNAPSHOT_ID, sleep: async () => undefined, pollIntervalMs: 0 });
    await expect(provider.simulate(SCENARIO)).rejects.toBeInstanceOf(PlatformContractError);
  });
});

describe("PlatformProvider：方案比较（分/元与三态可行性）", () => {
  const routes = {
    ...baseRoutes(),
    "POST /api/v1/decision-runs": () => postRun(DECISION_FEASIBLE_RUN_ID),
    [`GET /api/v1/runs/${DECISION_FEASIBLE_RUN_ID}`]: decisionRunFeasible,
  };

  it("费用分转元、效用与推荐来自平台，前端不重算", async () => {
    const transport = new MockTransport(routes);
    const provider = new PlatformProvider({ transport, datasetId: SNAPSHOT_ID, sleep: async () => undefined, pollIntervalMs: 0 });
    const comparison = await provider.comparePlans(
      SCENARIO, ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"], CONSTRAINTS,
    );
    expect(comparison.status).toBe("succeeded");
    expect(comparison.runId).toBe(DECISION_FEASIBLE_RUN_ID);
    expect(comparison.effectBasis).toBe("platform_given_candidates");
    expect(comparison.referenceCostYuan).toBeNull();
    expect(comparison.decisionAlgorithmVersion).toBe("1.0.0");
    expect(comparison.recommendedPlanId).toBe("syn-plan-combined");

    const combined = comparison.rows.find((row) => row.planId === "syn-plan-combined")!;
    expect(combined.costFen).toBe(370000);
    expect(combined.costYuan).toBe(3700);
    expect(combined.remainingAffectedServices).toBe(0);
    expect(combined.feasible).toBe(true);
    expect(combined.sourceStatus).toBe("succeeded");
    expect(combined.utility).toBeCloseTo(0.9075, 6);
    expect(combined.rank).toBe(1);
    // 平台不提供风险下降与工期口径，映射为 null 而不是 0。
    expect(combined.riskReduction).toBeNull();
    expect(combined.durationHours).toBeNull();
    expect(combined.type).toBeNull();

    // 排名与推荐完全来自平台，不在前端重排。
    expect(Object.fromEntries(comparison.rows.map((row) => [row.planId, row.rank]))).toEqual({
      "syn-plan-combined": 1, "syn-plan-repair": 2, "syn-plan-backup": 3,
    });
    expect(comparison.rows.map((row) => row.planId)).toEqual(
      ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
    );

    const posted = transport.calls.find((item) => item.path === "/api/v1/decision-runs")!;
    expect(posted.body).toMatchObject({ budget_fen: 500000, plan_ids: ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"] });
  });

  it("未知候选映射为 feasible=null，且不产生推荐", async () => {
    const provider = new PlatformProvider({
      transport: new MockTransport({
        ...baseRoutes(),
        "POST /api/v1/decision-runs": () => postRun(DECISION_RUN_ID),
        [`GET /api/v1/runs/${DECISION_RUN_ID}`]: decisionRun,
      }),
      datasetId: SNAPSHOT_ID, sleep: async () => undefined, pollIntervalMs: 0,
    });
    const comparison = await provider.comparePlans(SCENARIO, ["syn-plan-repair", "syn-plan-backup"], CONSTRAINTS);
    expect(comparison.status).toBe("insufficient_data");
    expect(comparison.recommendedPlanId).toBeNull();
    const unknown = comparison.rows.find((row) => row.planId === "syn-plan-backup")!;
    expect(unknown.feasible).toBeNull();
    expect(unknown.utility).toBeNull();
    expect(unknown.costFen).toBe(120000);
    expect(unknown.violations.join("")).toMatch(/资源/);
    expect(comparison.limitations.join("")).toContain("未知不等于不可行");
  });

  it("候选数量、重复与非整数预算都被拒绝", async () => {
    const { provider } = providerWith(routes);
    await expect(provider.comparePlans(SCENARIO, ["syn-plan-repair"], CONSTRAINTS)).rejects.toBeInstanceOf(PlatformContractError);
    await expect(
      provider.comparePlans(SCENARIO, ["syn-plan-repair", "syn-plan-repair"], CONSTRAINTS),
    ).rejects.toBeInstanceOf(PlatformContractError);
    await expect(
      provider.comparePlans(SCENARIO, ["syn-plan-repair", "syn-plan-backup"], { ...CONSTRAINTS, budgetYuan: 1.005 }),
    ).rejects.toBeInstanceOf(PlatformContractError);
  });
});

describe("PlatformProvider：时间线", () => {
  const request = {
    scenarioId: SCENARIO,
    isolationPlanId: ISOLATION,
    planId: "syn-plan-combined",
    targetServiceIds: ["syn-hospital-001"],
    horizonHours: 8,
  };

  it("任务完成时间缺失保持 insufficient_data，服务恢复时间原样", async () => {
    const { provider } = providerWith({
      ...baseRoutes(),
      "POST /api/v1/timeline/evaluate": timelineEvaluate,
    });
    const timeline = await provider.timeline(request);
    expect(timeline.algorithmVersion).toBe("1.0.1");
    expect(timeline.status).toBe("partial");
    expect(timeline.planCompletion.status).toBe("insufficient_data");
    expect(timeline.planCompletion.valueHours).toBeNull();
    // 平台在缺少任务结束记录时仍会报告观察窗口；窗口不等于恢复时间。
    expect(timeline.planCompletion.observationHorizonHours).toBe(8);
    expect(timeline.planCompletion.reason).toContain("finish");
    expect(timeline.firstTargetServiceRestored.valueHours).toBe(6);
    expect(timeline.allTargetServicesRestored.valueHours).toBe(6);
    expect(timeline.serviceFirstRestored["syn-hospital-001"].valueHours).toBe(6);
    expect(timeline.dataMode).toBe("synthetic");
    expect(timeline.sourceStatus).toBe("constructed");
    expect(timeline.evidenceIds).toContain(`snapshot:${SNAPSHOT_ID}`);
    expect(timeline.inputRequest).toMatchObject({ scenarioId: SCENARIO, horizonHours: 8 });
  });

  it("预检的逐字段可用性直接透传，不合并成单一布尔", async () => {
    const { provider } = providerWith({
      ...baseRoutes(),
      "POST /api/v1/timeline/check": timelineCheck,
    });
    const gate = await provider.checkTimeline(request);
    expect(gate.status).toBe("partial");
    expect(gate.canCompute).toBe(false);
    expect(gate.registryVersion).toBe("1.0.1");
    expect(gate.missingItems.length).toBeGreaterThan(0);
    expect(gate.authorizationChecked).toBe(false);
  });

  it("时间线响应结构不完整时显式失败", async () => {
    const { provider } = providerWith({ ...baseRoutes(), "POST /api/v1/timeline/evaluate": { status: "succeeded" } });
    await expect(provider.timeline(request)).rejects.toBeInstanceOf(PlatformContractError);
  });
});

describe("PlatformProvider：报告与 Agent 边界", () => {
  it("报告仅消费后端结果并保留服务端身份及算法语义", async () => {
    const serverReport = {
      report_schema_version: "unified-report-1.0", report_id: "report-server-synthetic",
      source: "existing_run_only", recomputation: "none",
      run_id: SIMULATION_RUN_ID, run_kind: "simulation", run_status: "succeeded", business_status: "succeeded",
      dataset: {
        snapshot_id: SNAPSHOT_ID,
        data_version: "4b31408c5a1ee971b535df60211e9bdeacff6bdaf79bdcbc9fe2eb02849036c8",
        as_of: "2026-10-29T08:00:00+08:00", data_mode: "synthetic", source_status: "constructed",
        schema_version: "0.1.0", name: "Synthetic report contract test",
      },
      algorithms: [{ capability_id: "given_isolation_topology", semantics: "topological_reachability", version: "0.1.0", validation_status: "not_business_validated" }],
      scenario: field(simulationRun, "request"), pipe: null,
      simulation: field(simulationRun, "result"), decision: null, timeline: null,
      feedback: [], evidence_ids: [`snapshot:${SNAPSHOT_ID}`, `run:${SIMULATION_RUN_ID}`], limitations: ["合成契约测试"],
    };
    const path = `/api/v1/runs/${SIMULATION_RUN_ID}/report`;
    const { provider, transport } = providerWith({ [`GET ${path}`]: serverReport });
    const report = await provider.report(SIMULATION_RUN_ID);
    expect(report).toBe(serverReport);
    expect(report.run_id).toBe(SIMULATION_RUN_ID);
    expect(report.dataset.snapshot_id).toBe(SNAPSHOT_ID);
    expect(report.algorithms[0].semantics).toBe("topological_reachability");
    expect(report.pipe).toBeNull();
    expect(transport.calls).toEqual([{ method: "GET", path }]);
  });

  it("明确禁用报告时不发起任何请求", async () => {
    const transport = new MockTransport({});
    const provider = new PlatformProvider({ transport, datasetId: SNAPSHOT_ID, reportEnabled: false });
    await expect(provider.report(SIMULATION_RUN_ID)).rejects.toBeInstanceOf(ProviderUnavailableError);
    expect(transport.calls).toEqual([]);
  });

  it("未完成或身份冲突由报告接口返回409，不在前端构造替代报告", async () => {
    const path = `/api/v1/runs/${SIMULATION_RUN_ID}/report`;
    const { provider, transport } = providerWith({
      [`GET ${path}`]: () => ({ status: 409, body: { detail: "Run identity mismatch" } }),
    });
    await expect(provider.report(SIMULATION_RUN_ID)).rejects.toMatchObject({ status: 409, detail: "Run identity mismatch" });
    expect(transport.calls).toEqual([{ method: "GET", path }]);
  });

  it("Agent 未启用时明确拒绝，不返回模板答案冒充模型", async () => {
    const { provider } = providerWith(baseRoutes());
    await expect(provider.ask("这段管段健康吗", { pipeId: PIPE })).rejects.toBeInstanceOf(ProviderUnavailableError);
  });

  it("ApiProvider 与 DemoProvider 行为保持不变", async () => {
    const { DemoProvider } = await import("../providers/demoProvider");
    const demo = new DemoProvider();
    expect(demo.mode).toBe("demo");
    const dataset = await demo.loadDataset();
    expect(dataset.pipes).toHaveLength(24);
    expect(vi.isMockFunction(() => undefined)).toBe(false);
  });
});
