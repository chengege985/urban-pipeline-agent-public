import { FACTORS, GRADE_THRESHOLDS, DEMO_MODEL_VERSION } from "../domain/constants";
import type {
  FactorKey,
  HealthAssessment,
  HealthFactor,
  HealthGrade,
  Pipe,
  PipeOverride,
} from "../domain/types";

export const DEMO_EVALUATION_YEAR = 2026;

const round1 = (value: number): number => Math.round(value * 10) / 10;

export function applyOverride(pipe: Pipe, override: PipeOverride = {}): Pipe {
  return { ...pipe, ...override };
}

export function scoreMaterial(pipe: Pipe): number | null {
  switch (pipe.material) {
    case "PE":
    case "球墨铸铁":
    case "PVC":
      return 2;
    case "钢":
      return 3;
    case "铸铁":
      return 4;
    case "未知":
      return null;
  }
}

export function scoreAge(pipe: Pipe, asOfYear = DEMO_EVALUATION_YEAR): number | null {
  if (pipe.installYear === null) return null;
  const age = asOfYear - pipe.installYear;
  if (age < 0) return null;
  if (age < 10) return 1;
  if (age < 20) return 2;
  if (age < 30) return 3;
  if (age < 40) return 4;
  return 5;
}

export function scoreDiameter(pipe: Pipe): number | null {
  if (pipe.diameterMm === null || pipe.diameterMm <= 0) return null;
  if (pipe.diameterMm < 100) return 5;
  if (pipe.diameterMm < 200) return 4;
  if (pipe.diameterMm < 300) return 3;
  if (pipe.diameterMm < 400) return 2;
  return 1;
}

export function scoreDepth(pipe: Pipe): number | null {
  if (pipe.depthM === null || pipe.depthM <= 0) return null;
  if (pipe.depthM < 1.5) return 1;
  if (pipe.depthM < 2.5) return 2;
  if (pipe.depthM < 3.5) return 3;
  if (pipe.depthM < 4.5) return 4;
  return 5;
}

export function scorePressure(pipe: Pipe): number | null {
  switch (pipe.pressure) {
    case "低压":
      return 1;
    case "常压":
      return 2;
    case "高压":
      return 4;
    case "未知":
      return null;
  }
}

export function scoreLeaks(pipe: Pipe): number | null {
  if (pipe.leakCount === null) return null;
  if (pipe.leakCount <= 0) return 1;
  if (pipe.leakCount === 1) return 2;
  if (pipe.leakCount === 2) return 3;
  if (pipe.leakCount === 3) return 4;
  return 5;
}

export function scoreRepairs(pipe: Pipe): number | null {
  if (pipe.repairCount === null) return null;
  if (pipe.repairCount <= 0) return 1;
  if (pipe.repairCount <= 2) return 2;
  if (pipe.repairCount <= 4) return 3;
  if (pipe.repairCount <= 6) return 4;
  return 5;
}

export function scoreMonitoring(pipe: Pipe): number | null {
  switch (pipe.monitoring) {
    case "正常":
      return 1;
    case "关注":
      return 3;
    case "异常":
      return 5;
    case "未知":
      return null;
  }
}

function factorScore(key: FactorKey, pipe: Pipe, asOfYear: number): number | null {
  switch (key) {
    case "material":
      return scoreMaterial(pipe);
    case "age":
      return scoreAge(pipe, asOfYear);
    case "diameter":
      return scoreDiameter(pipe);
    case "depth":
      return scoreDepth(pipe);
    case "pressure":
      return scorePressure(pipe);
    case "leaks":
      return scoreLeaks(pipe);
    case "repairs":
      return scoreRepairs(pipe);
    case "monitoring":
      return scoreMonitoring(pipe);
  }
}

function factorDetail(key: FactorKey, pipe: Pipe, score: number | null): string {
  if (score === null) return "当前记录缺失或不适用，未计入权重";
  switch (key) {
    case "material":
      return `${pipe.material} → ${score} 分`;
    case "age":
      return pipe.installYear === null
        ? "投运年份缺失"
        : `${pipe.installYear} 年投运 → ${DEMO_EVALUATION_YEAR - pipe.installYear} 年 → ${score} 分`;
    case "diameter":
      return `${pipe.diameterMm} mm → ${score} 分`;
    case "depth":
      return `${pipe.depthM} m → ${score} 分`;
    case "pressure":
      return `${pipe.pressure} → ${score} 分`;
    case "leaks":
      return `${pipe.leakCount} 次漏损 → ${score} 分`;
    case "repairs":
      return `${pipe.repairCount} 次维修 → ${score} 分`;
    case "monitoring":
      return `${pipe.monitoring} → ${score} 分`;
  }
}

export function gradeForScore(score: number | null): HealthGrade {
  if (score === null) return "数据不足";
  const match = GRADE_THRESHOLDS.find((item) => score >= item.min);
  return match?.grade ?? "重点检查";
}

export function evaluateDemoHealth(
  pipe: Pipe,
  override: PipeOverride = {},
  asOfYear = DEMO_EVALUATION_YEAR,
): HealthAssessment {
  const current = applyOverride(pipe, override);
  const scored = FACTORS.map((definition) => ({
    definition,
    score: factorScore(definition.key, current, asOfYear),
  }));
  const valid = scored.filter((item) => item.score !== null) as Array<{
    definition: (typeof FACTORS)[number];
    score: number;
  }>;
  const validWeight = valid.reduce((sum, item) => sum + item.definition.weight, 0);
  const weightedRiskTotal = valid.reduce(
    (sum, item) => sum + item.definition.weight * ((item.score - 1) / 4),
    0,
  );

  const factors: HealthFactor[] = scored.map(({ definition, score }) => {
    const weightedRisk = score === null ? 0 : definition.weight * ((score - 1) / 4);
    return {
      key: definition.key,
      label: definition.label,
      weight: definition.weight,
      score,
      weightedRisk,
      contribution: weightedRiskTotal > 0 ? weightedRisk / weightedRiskTotal : 0,
      detail: factorDetail(definition.key, current, score),
    };
  });

  const missing = factors.filter((factor) => factor.score === null).map((factor) => factor.label);

  if (validWeight === 0) {
    return {
      pipeId: pipe.id,
      status: "insufficient_data",
      score: null,
      grade: "数据不足",
      confidence: 0,
      validWeight: 0,
      factors,
      missing,
      version: DEMO_MODEL_VERSION,
      summary: "所有演示指标均缺失，无法形成可解释的演示评价。",
    };
  }

  const rawScore = Math.max(0, Math.min(100, 100 * (1 - weightedRiskTotal / validWeight)));
  const score = round1(rawScore);
  const confidence = round1(validWeight * 100);
  const top = [...factors]
    .filter((factor) => factor.score !== null)
    .sort((a, b) => b.contribution - a.contribution)
    .slice(0, 2)
    .map((factor) => factor.label)
    .join("、");

  return {
    pipeId: pipe.id,
    status: "succeeded",
    score,
    grade: gradeForScore(score),
    confidence,
    validWeight: round1(validWeight * 100) / 100,
    factors,
    missing,
    version: DEMO_MODEL_VERSION,
    summary: `8 项指标中 ${8 - missing.length} 项有效，可信度 ${confidence}%；当前主要贡献：${top || "无明显风险贡献"}。`,
  };
}
