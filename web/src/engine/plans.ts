import { DECISION_ALGORITHM_VERSION, DECISION_REFERENCE_COST_YUAN, DEFAULT_DECISION_CONSTRAINTS, DEPENDENCY_SHORT_LABELS, RESOURCE_LABELS } from "../domain/constants";
import type {
  DemoConstraints,
  MaintenancePlan,
  PlanComparison,
  PlanComparisonRow,
  ServiceImpact,
} from "../domain/types";

const round3 = (value: number): number => Math.round(value * 1000) / 1000;

export function isPlanSelectable(comparison: PlanComparison, planId: string | null): boolean {
  if (!planId) return false;
  return comparison.rows.some((row) => row.planId === planId && row.feasible === true);
}

/** null（未知）排在已知值之后，绝不当作最大值参与比较。 */
const descending = (a: number | null, b: number | null): number =>
  a === null && b === null ? 0 : a === null ? 1 : b === null ? -1 : b - a;
const ascending = (a: number | null, b: number | null): number =>
  a === null && b === null ? 0 : a === null ? 1 : b === null ? -1 : a - b;

function constraintViolations(plan: MaintenancePlan, constraints: DemoConstraints): string[] {
  const violations: string[] = [];
  if (plan.costYuan > constraints.budgetYuan) {
    violations.push(`费用 ${plan.costYuan} 元超过演示预算 ${constraints.budgetYuan} 元`);
  }
  for (const [resource, amount] of Object.entries(plan.resourceRequirements)) {
    const capacity = constraints.resourceCapacities[resource] ?? 0;
    if (amount > capacity) {
      violations.push(
        `${RESOURCE_LABELS[resource] ?? resource}不足（需要 ${amount}，可用 ${capacity}）`,
      );
    }
  }
  for (const dependency of plan.requiredDependencies) {
    if (!constraints.confirmedDependencies.includes(dependency)) {
      violations.push(`${DEPENDENCY_SHORT_LABELS[dependency] ?? dependency}未确认`);
    }
  }
  return violations;
}

export function compareDemoPlans(
  scenarioId: string,
  impact: ServiceImpact,
  plans: MaintenancePlan[],
  constraints: DemoConstraints = DEFAULT_DECISION_CONSTRAINTS,
): PlanComparison {
  const supported = impact.status === "succeeded" && impact.scenarioId === scenarioId && plans.length > 0;
  const affectedCount = impact.affectedServices.length;

  const rows: PlanComparisonRow[] = (supported ? plans : []).map((plan) => {
    const restoredServiceCount = plan.restoresAffected ? affectedCount : 0;
    const riskPart = Math.min(plan.riskReduction / 30, 1) * 0.55;
    const costPart = Math.max(0, Math.min(1, 1 - plan.costYuan / DECISION_REFERENCE_COST_YUAN)) * 0.25;
    const restorePart = restoredServiceCount > 0 ? 0.2 : 0;
    const violations = constraintViolations(plan, constraints);
    return {
      planId: plan.id,
      name: plan.name,
      type: plan.type,
      costYuan: plan.costYuan,
      durationHours: plan.durationHours,
      riskReduction: plan.riskReduction,
      restoredServiceCount,
      remainingAffectedServices: plan.restoresAffected ? 0 : affectedCount,
      resourceRequirements: { ...plan.resourceRequirements },
      feasible: violations.length === 0,
      violations,
      utility: round3(riskPart + costPart + restorePart),
      rank: 0,
      note: plan.constraints,
    };
  });

  rows.sort(
    (a, b) =>
      Number(b.feasible === true) - Number(a.feasible === true) ||
      descending(a.utility, b.utility) ||
      ascending(a.costYuan, b.costYuan) ||
      a.planId.localeCompare(b.planId),
  );
  rows.forEach((row, index) => {
    row.rank = index + 1;
  });

  const recommended = rows.find((row) => row.feasible === true) ?? null;

  return {
    status: supported ? "succeeded" : "insufficient_data",
    decisionAlgorithmVersion: DECISION_ALGORITHM_VERSION,
    referenceCostYuan: DECISION_REFERENCE_COST_YUAN,
    effectBasis: "synthetic_preset",
    scenarioId,
    planIds: rows.map((row) => row.planId),
    rows,
    recommendedPlanId: recommended?.planId ?? null,
    constraints: {
      budgetYuan: constraints.budgetYuan,
      resourceCapacities: { ...constraints.resourceCapacities },
      confirmedDependencies: [...constraints.confirmedDependencies],
    },
    utilityNote:
      "演示效用 = 风险下降 55% + 成本 25% + 受影响服务恢复 20%；成本采用固定40000元参考尺度；恢复效果为合成预设，不是正式 CUV 或业务验收。",
    limitations: [
      ...(!supported ? ["运行基准不一致或拓扑结果不完整，不能推荐方案。", ...impact.limitations] : []),
      "只比较已给出的候选方案，不自动生成方案，也不做全局最优搜索。",
      "费用、工期、资源需求和风险下降均为合成演示值；预算、资源与依赖只是演示硬约束。",
      "人工选择只登记演示意见，不代表派单、关阀或实际执行。",
    ],
  };
}
