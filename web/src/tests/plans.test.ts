import { describe, expect, it } from "vitest";
import { DEMO_NODES, DEMO_PIPES, DEMO_PLANS, DEMO_SCENARIOS, DEMO_SERVICES } from "../data/network";
import { simulateServiceImpact } from "../engine/network";
import { compareDemoPlans } from "../engine/plans";

describe("演示方案比较", () => {
  const hospital = DEMO_SCENARIOS.find((item) => item.id === "SC-HOSP");
  if (!hospital) throw new Error("SC-HOSP missing");
  const impact = simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, hospital);
  const comparison = compareDemoPlans(hospital.id, impact, DEMO_PLANS);

  it("比较 3 个方案并推荐立即抢修", () => {
    expect(comparison.rows).toHaveLength(3);
    expect(comparison.recommendedPlanId).toBe("PLAN-REPAIR");
    expect(comparison.rows[0].rank).toBe(1);
  });

  it("监测方案不能恢复服务，不因预算有限而改写其效果", () => {
    const monitor = comparison.rows.find((row) => row.planId === "PLAN-MONITOR");
    expect(monitor?.restoredServiceCount).toBe(0);
    expect(monitor?.remainingAffectedServices).toBe(impact.affectedServices.length);
  });

  it("推荐理由是演示权重，不声称正式 CUV", () => {
    expect(comparison.utilityNote).toContain("不是正式 CUV");
    expect(comparison.limitations.join(" ")).toContain("合成演示值");
  });

  it("比较结果可重复", () => {
    expect(compareDemoPlans(hospital.id, impact, DEMO_PLANS)).toEqual(comparison);
  });
});
