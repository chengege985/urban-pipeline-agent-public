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
} from "../data/network";
import { simulateServiceImpact } from "../engine/network";
import { compareDemoPlans } from "../engine/plans";
import { buildDecisionReport } from "../engine/report";
import { DemoProvider } from "../providers/demoProvider";
import {
  DecisionStore,
  createDecisionSession,
  transitionDecisionReview,
  type StorageLike,
} from "../storage/decisionStore";

function hospitalImpact(isolationPlanId?: string) {
  const scenario = DEMO_SCENARIOS.find((item) => item.id === "SC-HOSP");
  if (!scenario) throw new Error("SC-HOSP missing");
  const isolation = DEMO_ISOLATION_PLANS.find((item) => item.id === isolationPlanId);
  return simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, scenario, isolation);
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

describe("演示硬约束", () => {
  const impact = hospitalImpact("ISO-HOSP-MIN");

  it("默认约束下立即抢修与加密监测可行，推荐立即抢修", () => {
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, DEFAULT_DECISION_CONSTRAINTS);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPAIR")?.feasible).toBe(true);
    expect(comparison.rows.find((row) => row.planId === "PLAN-MONITOR")?.feasible).toBe(true);
    expect(comparison.rows.find((row) => row.planId === "PLAN-REPLACE")?.feasible).toBe(false);
    expect(comparison.recommendedPlanId).toBe("PLAN-REPAIR");
  });

  it("预算不足会淘汰方案，且没有可行方案时不推荐", () => {
    const tight: DemoConstraints = { ...DEFAULT_DECISION_CONSTRAINTS, budgetYuan: 5000 };
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, tight);
    expect(comparison.rows.every((row) => !row.feasible)).toBe(true);
    expect(comparison.recommendedPlanId).toBeNull();
    expect(comparison.rows.some((row) => row.violations.some((item) => item.includes("预算")))).toBe(true);
  });

  it("资源不足只淘汰需要该资源的方案", () => {
    const limited: DemoConstraints = {
      budgetYuan: 40000,
      resourceCapacities: { repair_team: 0, water_truck: 1, monitoring_kit: 1 },
      confirmedDependencies: ["shutdown_window"],
    };
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, limited);
    const repair = comparison.rows.find((row) => row.planId === "PLAN-REPAIR");
    const monitor = comparison.rows.find((row) => row.planId === "PLAN-MONITOR");
    expect(repair?.feasible).toBe(false);
    expect(monitor?.feasible).toBe(true);
    expect(comparison.recommendedPlanId).toBe("PLAN-MONITOR");
  });

  it("未确认施工许可使计划更换不可行", () => {
    const comparison = compareDemoPlans("SC-HOSP", impact, DEMO_PLANS, DEFAULT_DECISION_CONSTRAINTS);
    const replace = comparison.rows.find((row) => row.planId === "PLAN-REPLACE");
    expect(replace?.feasible).toBe(false);
    expect(replace?.violations.join(" ")).toContain("施工许可");
  });

  it("扩大隔离会增加受影响服务", () => {
    const minimal = hospitalImpact("ISO-HOSP-MIN");
    const extended = hospitalImpact("ISO-HOSP-EXT");
    expect(extended.affectedServices.length).toBeGreaterThan(minimal.affectedServices.length);
    expect(extended.closedPipeIds).toContain("WP-013");
  });
});

describe("决策简报与人工审核", () => {
  it("简报只引用可行方案并保留证据", async () => {
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
    });
    const report = buildDecisionReport(dataset, session);
    expect(report).not.toBeNull();
    expect(report?.stale).toBe(true); // Legacy sessions have no frozen computation and cannot be re-approved.
    expect(report?.selectedRow?.feasible).toBe(true);
    expect(report?.evidenceIds.length).toBeGreaterThan(1);
    expect(report?.impact.isolationPlanId).toBe("ISO-HOSP-MIN");
  });

  it("审核状态按提交、通过、驳回、重开约束流转", () => {
    const session = createDecisionSession({
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
    const pending = transitionDecisionReview(session, "submit", "演示审核员", "");
    expect(pending.reviewStatus).toBe("pending_review");
    const approved = transitionDecisionReview(pending, "approve", "演示审核员", "同意演示方案");
    expect(approved.reviewStatus).toBe("approved");
    expect(approved.reviewRevision).toBe(2);
    expect(() => transitionDecisionReview(approved, "reject", "演示审核员", "不行")).toThrow();
    const reopened = transitionDecisionReview(approved, "reopen", "演示审核员", "");
    expect(reopened.reviewStatus).toBe("draft");
  });

  it("本地存储保存并读取决策会话", () => {
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
    expect(store.load(session.id)?.selectedPlanId).toBe("PLAN-REPAIR");
    expect(store.loadAll()).toHaveLength(1);
  });
});
