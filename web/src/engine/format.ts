import type { HealthAssessment, HealthGrade } from "../domain/types";

export type Tone = "ok" | "warn" | "bad" | "unknown";

export function formatScore(score: number | null): string {
  return score === null ? "—" : score.toFixed(1);
}

export function formatPercent(value: number): string {
  return `${Math.round(value)}%`;
}

export function formatCurrency(value: number | null): string {
  if (value === null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("zh-CN", {
    style: "currency",
    currency: "CNY",
    maximumFractionDigits: 0,
  }).format(value);
}

export function gradeTone(grade: HealthGrade): Tone {
  switch (grade) {
    case "基本健康":
      return "ok";
    case "关注":
      return "warn";
    case "重点检查":
      return "bad";
    case "数据不足":
      return "unknown";
  }
}

export function assessmentTone(assessment: HealthAssessment | undefined): Tone {
  return assessment ? gradeTone(assessment.grade) : "unknown";
}

export function topFactors(assessment: HealthAssessment, limit = 3) {
  return [...assessment.factors]
    .filter((factor) => factor.score !== null)
    .sort((a, b) => b.contribution - a.contribution)
    .slice(0, limit);
}

export function signed(value: number): string {
  return `${value >= 0 ? "+" : ""}${value.toFixed(1)}`;
}
