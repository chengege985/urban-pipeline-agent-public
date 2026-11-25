import type { DemoConstraints, FactorDefinition, HealthGrade, ReviewStatus } from "./types";

export const APP_VERSION = "0.3.0-rc.2";
export const DEMO_MODEL_VERSION = "demo-score-0.1.0";
export const DECISION_ALGORITHM_VERSION = "demo-decision-0.2.0";
export const DECISION_REFERENCE_COST_YUAN = 40000;
export const DATA_VERSION = "synthetic-2026-10-30";
export const TIMELINE_ALGORITHM_VERSION = "demo-timeline-0.1.1";
export const TIMELINE_DATA_VERSION = "synthetic-timeline-2026-11-01";
export const TIMELINE_AS_OF = "2026-11-01T08:00:00+08:00";
export const TIMELINE_DEFAULT_HORIZON_HOURS = 8;
export const TIMELINE_MAX_HORIZON_HOURS = 720;
export const TIMELINE_SUPPORTED_OUTPUTS = [
  "timeline_request",
  "recovery_times",
  "plan_completion",
  "service_first_restored",
] as const;
export const DISCLAIMER =
  "合成数据演示，不控制真实设施；DemoHealth 不是正式健康度、失效概率、RUL 或水力计算结果。";

export const FACTORS: FactorDefinition[] = [
  { key: "material", label: "管材", weight: 0.15, description: "材质本身的腐蚀与老化倾向" },
  { key: "age", label: "管龄", weight: 0.2, description: "投运年份距演示评价时点的年限" },
  { key: "diameter", label: "管径", weight: 0.1, description: "小管径更易受沉积与负荷影响" },
  { key: "depth", label: "埋深", weight: 0.1, description: "埋深越大，巡检与抢修难度越高" },
  { key: "pressure", label: "运行压力", weight: 0.15, description: "压力等级偏离常压的程度" },
  { key: "leaks", label: "漏损历史", weight: 0.15, description: "近期记录的漏损次数" },
  { key: "repairs", label: "维修频次", weight: 0.05, description: "近期维修工单次数" },
  { key: "monitoring", label: "监测质量", weight: 0.1, description: "监测状态与数据异常程度" },
];

export const GRADE_THRESHOLDS: Array<{ min: number; grade: Exclude<HealthGrade, "数据不足"> }> = [
  { min: 80, grade: "基本健康" },
  { min: 65, grade: "关注" },
  { min: 0, grade: "重点检查" },
];

export const SCORE_TONE = {
  ok: "基本健康",
  warn: "关注",
  bad: "重点检查",
  unknown: "数据不足",
} as const;

export const ASSISTANT_SUGGESTIONS = [
  "为什么这段管段优先检查？",
  "如果这段爆管会影响哪些服务？",
  "有哪些维护方案可以比较？",
  "这个方案多久恢复？",
  "能预测爆管概率或剩余寿命吗？",
];

export const RESOURCE_LABELS: Record<string, string> = {
  repair_team: "抢修班组",
  water_truck: "应急送水车",
  monitoring_kit: "监测设备包",
};

export const DEPENDENCY_LABELS: Record<string, string> = {
  shutdown_window: "停水窗口已确认",
  construction_permit: "施工许可已确认",
};

export const DEPENDENCY_SHORT_LABELS: Record<string, string> = {
  shutdown_window: "停水窗口",
  construction_permit: "施工许可",
};

export const DEFAULT_DECISION_CONSTRAINTS: DemoConstraints = {
  budgetYuan: 40000,
  resourceCapacities: { repair_team: 1, water_truck: 1, monitoring_kit: 1 },
  confirmedDependencies: ["shutdown_window"],
};

export const UNLIMITED_DECISION_CONSTRAINTS: DemoConstraints = {
  budgetYuan: 1000000,
  resourceCapacities: { repair_team: 99, water_truck: 99, monitoring_kit: 99 },
  confirmedDependencies: ["shutdown_window", "construction_permit"],
};

export const REVIEW_LABELS: Record<ReviewStatus, string> = {
  draft: "草稿",
  pending_review: "待审核",
  approved: "审核通过",
  rejected: "已驳回",
};
