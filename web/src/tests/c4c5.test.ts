import { describe, expect, it } from "vitest";
import { DEFAULT_DECISION_CONSTRAINTS } from "../domain/constants";
import type { DemoConstraints } from "../domain/types";
import {
  DEMO_ISOLATION_PLANS,
  DEMO_NODES,
  DEMO_PIPES,
  DEMO_PLANS,
  DEMO_SCENARIOS,
  DEMO_SERVICES,
  DEMO_VALVES,
} from "../data/network";
import { simulateServiceImpact } from "../engine/network";
import { compareDemoPlans, isPlanSelectable } from "../engine/plans";
import { buildDecisionReport } from "../engine/report";
import { askDemoAssistant } from "../engine/assistant";
import { DemoProvider } from "../providers/demoProvider";
import {
  DecisionStore,
  createDecisionSession,
  transitionDecisionReview,
  type StorageLike,
} from "../storage/decisionStore";

function scenario(id: string) {
  const found = DEMO_SCENARIOS.find((item) => item.id === id);
  if (!found) throw new Error(`missing scenario ${id}`);
  return found;
}

function isolation(id: string) {
  const found = DEMO_ISOLATION_PLANS.find((item) => item.id === id);
  if (!found) throw new Error(`missing isolation ${id}`);
  return found;
}

function memoryStorage(): StorageLike {
  const values = new Map<string, string>();
  return {
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => {
      values.set(key, value);
    },
  };
}

function hospitalImpact(planId: string) {
  return simulateServiceImpact(
    DEMO_PIPES,
    DEMO_NODES,
    DEMO_SERVICES,
    scenario("SC-HOSP"),
    isolation(planId),
  );
}

describe("C4 隔离与拓扑影响", () => {
  it("最小隔离只影响医院支线服务，且服务计数不重复", () => {
    const result = hospitalImpact("ISO-HOSP-MIN");
    expect(result.affectedServices.map((service) => service.serviceId)).toEqual(["SV-HOSP"]);
    expect(result.affectedFacilities).toBe(1);
    expect(result.affectedHouseholds).toBe(0);
    expect(new Set(result.affectedServices.map((service) => service.serviceId)).size).toBe(1);
    expect(result.closedPipeIds).toEqual([]);
    expect(result.valveIds).toEqual(["V-016A"]);
  });

  it("扩大隔离增加医院与学校影响，独立推算为 2 个关键设施", () => {
    const result = hospitalImpact("ISO-HOSP-EXT");
    // WP-013 与 WP-015 同时关闭后，J9 失去所有上游路径；医院/学校接入节点不可达。
    expect(result.affectedServices.map((service) => service.serviceId).sort()).toEqual([
      "SV-HOSP",
      "SV-SCHOOL",
    ]);
    expect(result.affectedCriticalServiceIds.sort()).toEqual(["SV-HOSP", "SV-SCHOOL"]);
    expect(result.affectedFacilities).toBe(2);
    expect(result.affectedHouseholds).toBe(0);
    expect(result.closedPipeIds.sort()).toEqual(["WP-013", "WP-015"]);
  });

  it("重置到最小隔离与无额外交闭的基线影响一致", () => {
    const minimal = hospitalImpact("ISO-HOSP-MIN");
    const noExtra = simulateServiceImpact(
      DEMO_PIPES,
      DEMO_NODES,
      DEMO_SERVICES,
      scenario("SC-HOSP"),
    );
    expect(minimal.affectedServices.map((item) => item.serviceId)).toEqual(
      noExtra.affectedServices.map((item) => item.serviceId),
    );
    expect(minimal.affectedHouseholds).toBe(noExtra.affectedHouseholds);
    expect(minimal.affectedFacilities).toBe(noExtra.affectedFacilities);
  });

  it("居民区叠加隔离计数按服务对象唯一汇总：3 个服务、2140 户、1 处设施", () => {
    const result = simulateServiceImpact(
      DEMO_PIPES,
      DEMO_NODES,
      DEMO_SERVICES,
      scenario("SC-RES"),
      isolation("ISO-RES-EXT"),
    );
    // WP-019/020 故障，且 WP-014/015/024 同时关闭后 J10 没有任何上游通路：
    // 商业中心 + 滨河一号院 + 老城二号院受影响；1 处商业设施 + 2140 户居民。
    expect(result.affectedServices.map((item) => item.serviceId).sort()).toEqual([
      "SV-MALL",
      "SV-RES1",
      "SV-RES2",
    ]);
    expect(result.affectedHouseholds).toBe(2140);
    expect(result.affectedFacilities).toBe(1);
    expect(new Set(result.affectedServices.map((item) => item.serviceId)).size).toBe(3);
  });

  it("阀门与隔离方案引用均属于合成数据", () => {
    const valveIds = new Set(DEMO_VALVES.map((valve) => valve.id));
    const pipeIds = new Set(DEMO_PIPES.map((pipe) => pipe.id));
    for (const plan of DEMO_ISOLATION_PLANS) {
      expect(plan.valveIds.every((id) => valveIds.has(id))).toBe(true);
      expect(plan.closedPipeIds.every((id) => pipeIds.has(id))).toBe(true);
      expect(DEMO_SCENARIOS.some((item) => item.id === plan.scenarioId)).toBe(true);
    }
    for (const item of DEMO_SCENARIOS) {
      expect(DEMO_ISOLATION_PLANS.some((plan) => plan.id === item.defaultIsolationPlanId)).toBe(true);
    }
  });
});

describe("C5-A 硬约束、可行性与选择", () => {
  const impact = hospitalImpact("ISO-HOSP-MIN");

  it("预算不足时只有监测方案可行，推荐监测", () => {
    const constraints: DemoConstraints = {
      ...DEFAULT_DECISION_CONSTRAINTS,
      budgetYuan: 10000,
    };
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, constraints);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPAIR")?.feasible).toBe(false);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPLACE")?.feasible).toBe(false);
    expect(comparison.rows.find((row) => row.planId === "PLAN-MONITOR")?.feasible).toBe(true);
    expect(comparison.recommendedPlanId).toBe("PLAN-MONITOR");
    expect(isPlanSelectable(comparison, "PLAN-MONITOR")).toBe(true);
    expect(isPlanSelectable(comparison, "PLAN-REPAIR")).toBe(false);
  });

  it("资源不足只淘汰依赖该资源的方案", () => {
    const constraints: DemoConstraints = {
      ...DEFAULT_DECISION_CONSTRAINTS,
      resourceCapacities: { repair_team: 0, water_truck: 1, monitoring_kit: 1 },
    };
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, constraints);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPAIR")?.feasible).toBe(false);
    expect(comparison.rows.find((row) => row.planId === "PLAN-MONITOR")?.feasible).toBe(true);
    expect(comparison.recommendedPlanId).toBe("PLAN-MONITOR");
  });

  it("未确认停水窗口时抢修和更换均不可行", () => {
    const constraints: DemoConstraints = {
      ...DEFAULT_DECISION_CONSTRAINTS,
      confirmedDependencies: [],
    };
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, constraints);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPAIR")?.feasible).toBe(false);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPLACE")?.feasible).toBe(false);
    expect(comparison.rows.find((row) => row.planId === "PLAN-MONITOR")?.feasible).toBe(true);
  });

  it("不可行方案不会成为推荐方案", () => {
    const constraints: DemoConstraints = {
      ...DEFAULT_DECISION_CONSTRAINTS,
      budgetYuan: 0,
    };
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, constraints);
    expect(comparison.recommendedPlanId).toBeNull();
    expect(comparison.rows.every((row) => !row.feasible)).toBe(true);
    expect(comparison.rows.every((row) => row.planId !== comparison.recommendedPlanId)).toBe(true);
  });
});

describe("C5-B/C 简报与审核", () => {
  async function makeSession(overrides: Partial<Parameters<typeof createDecisionSession>[0]> = {}) {
    const dataset = await new DemoProvider().loadDataset();
    const session = createDecisionSession({
      pipeId: "WP-016",
      scenarioId: "SC-HOSP",
      isolationPlanId: "ISO-HOSP-MIN",
      budgetYuan: DEFAULT_DECISION_CONSTRAINTS.budgetYuan,
      resourceCapacities: DEFAULT_DECISION_CONSTRAINTS.resourceCapacities,
      confirmedDependencies: DEFAULT_DECISION_CONSTRAINTS.confirmedDependencies,
      selectedPlanId: "PLAN-REPAIR",
      dataVersion: dataset.meta.dataVersion,
      algorithmVersion: dataset.meta.modelVersion,
      evidenceIds: ["data:WP-016"],
      ...overrides,
    });
    return { dataset, session };
  }

  it("简报数据与当前管段、情景、隔离和方案一致", async () => {
    const { dataset, session } = await makeSession();
    const report = buildDecisionReport(dataset, session);
    expect(report).not.toBeNull();
    expect(report?.assessment).toEqual(dataset.assessments["WP-016"]);
    expect(report?.impact.isolationPlanId).toBe("ISO-HOSP-MIN");
    expect(report?.impact.affectedServices.map((item) => item.serviceId)).toEqual(["SV-HOSP"]);
    expect(report?.selectedRow?.feasible).toBe(true);
    expect(report?.evidenceIds).toContain("isolation:ISO-HOSP-MIN");
    expect(report?.evidenceIds).toContain("plan:PLAN-REPAIR");
  });

  it("旧版本会标记 stale", async () => {
    const { dataset, session } = await makeSession({ dataVersion: "synthetic-old" });
    expect(buildDecisionReport(dataset, session)?.stale).toBe(true);
  });

  it("审核意见保存、状态流转与刷新读取", () => {
    const store = new DecisionStore(memoryStorage());
    const session = store.create({
      pipeId: "WP-016",
      scenarioId: "SC-HOSP",
      isolationPlanId: "ISO-HOSP-MIN",
      budgetYuan: 40000,
      resourceCapacities: { repair_team: 1, water_truck: 1, monitoring_kit: 1 },
      confirmedDependencies: ["shutdown_window"],
      selectedPlanId: "PLAN-REPAIR",
      dataVersion: "synthetic-2026-10-30",
      algorithmVersion: "demo-score-0.1.0",
      evidenceIds: ["data:WP-016"],
    });
    const pending = store.review(session.id, "submit", "演示审核员", "请复核费用与影响");
    expect(pending.reviewStatus).toBe("pending_review");
    expect(store.load(session.id)?.reviewComment).toBe("请复核费用与影响");
    const approved = store.review(session.id, "approve", "演示审核员", "同意演示建议");
    expect(approved.reviewStatus).toBe("approved");
    expect(approved.reviewRevision).toBe(2);
    expect(approved.reviewComment).toBe("同意演示建议");
    const loaded = store.load(session.id);
    expect(loaded?.reviewStatus).toBe("approved");
    expect(loaded?.reviewComment).toBe("同意演示建议");
    expect(() =>
      transitionDecisionReview(approved, "approve", "演示审核员", "重复通过"),
    ).toThrow();
  });

  it("助手不能执行真实关阀、派单或无依据概率预测", () => {
    const context = {
      pipe: DEMO_PIPES.find((item) => item.id === "WP-016"),
      pipeId: "WP-016",
      assessment: undefined,
      scenarioId: "SC-HOSP",
      impact: hospitalImpact("ISO-HOSP-MIN"),
    };
    for (const question of ["直接关阀并下发工单", "给出爆管概率", "预测剩余寿命"]) {
      const response = askDemoAssistant(question, context, DEMO_PLANS);
      expect(response.refused).toBe(true);
      expect(response.status).toBe("refused");
    }
  });
});
