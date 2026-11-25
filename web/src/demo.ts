export type Pipe = {
  id: string;
  material: string;
  age: number;
  diameter: number;
  depth: number;
  pressure: "低压" | "常压" | "高压" | "未知";
  leaks: number;
  repairs: number;
  monitoring: "正常" | "关注" | "异常" | "未知";
  service: string;
  serviceType: "居民区" | "医院" | "学校" | "商业";
};

export type Factor = {
  key: string;
  label: string;
  weight: number;
  score: number | null;
  contribution: number;
};

export type Evaluation = {
  score: number;
  risk: number;
  confidence: number;
  factors: Factor[];
  grade: string;
};

const weights: Record<string, number> = {
  管材: 0.15, 管龄: 0.2, 管径: 0.1, 埋深: 0.1,
  运行压力: 0.15, 漏损历史: 0.15, 维修频次: 0.05, 监测质量: 0.1,
};
const materials = ["PE", "球墨铸铁", "铸铁", "钢", "PVC"];
const pressures: Pipe["pressure"][] = ["低压", "常压", "高压", "未知"];
const monitorings: Pipe["monitoring"][] = ["正常", "关注", "异常", "未知"];

export const pipes: Pipe[] = Array.from({ length: 24 }, (_, i) => {
  const serviceType: Pipe["serviceType"] = i === 4 ? "医院" : i === 9 ? "学校" : i === 15 ? "商业" : "居民区";
  return {
    id: `WP-${String(i + 1).padStart(3, "0")}`,
    material: materials[i % materials.length],
    age: 6 + ((i * 7) % 38),
    diameter: [100, 150, 200, 300, 400][i % 5],
    depth: Number((1.2 + (i % 6) * 0.6).toFixed(1)),
    pressure: pressures[i % pressures.length],
    leaks: i % 4 === 3 ? 4 : i % 4,
    repairs: (i * 3) % 8,
    monitoring: monitorings[i % monitorings.length],
    service: serviceType === "居民区" ? `滨河${i + 1}号院` : `澄江${serviceType}`,
    serviceType,
  };
});

function band(value: number, cuts: number[]): number {
  const index = cuts.findIndex((cut) => value <= cut);
  return index < 0 ? cuts.length + 1 : index + 1;
}

export function evaluate(pipe: Pipe, override: Partial<Pipe> = {}): Evaluation {
  const p = { ...pipe, ...override };
  const scores: Record<string, number | null> = {
    管材: ({ PE: 2, 球墨铸铁: 3, 铸铁: 4, 钢: 3, PVC: 2 } as Record<string, number>)[p.material] ?? null,
    管龄: band(p.age, [10, 20, 30, 40]),
    管径: band(p.diameter, [100, 150, 200, 300]),
    埋深: band(p.depth, [1.5, 2.5, 3.5, 4.5]),
    运行压力: p.pressure === "未知" ? null : ({ 低压: 1, 常压: 2, 高压: 4 } as Record<string, number>)[p.pressure],
    漏损历史: p.leaks >= 4 ? 5 : p.leaks + 1,
    维修频次: band(p.repairs, [0, 2, 4, 6]),
    监测质量: p.monitoring === "未知" ? null : ({ 正常: 1, 关注: 3, 异常: 5 } as Record<string, number>)[p.monitoring],
  };
  const valid = Object.entries(scores).filter(([, s]) => s !== null) as [string, number][];
  const totalWeight = valid.reduce((sum, [key]) => sum + weights[key], 0);
  const weighted = valid.reduce((sum, [key, score]) => sum + weights[key] * (score - 1) / 4, 0);
  const risk = weighted / totalWeight;
  const factors = Object.entries(scores).map(([key, score]) => {
    const weight = weights[key];
    const contribution = score === null ? 0 : weight * (score - 1) / 4 / (weighted || 1);
    return { key: `F-${key}`, label: key, weight, score, contribution };
  });
  const score = Math.round(1000 * (1 - risk)) / 10;
  return {
    score, risk, confidence: Math.round(100 * totalWeight),
    factors,
    grade: score >= 80 ? "基本健康" : score >= 65 ? "关注" : "重点检查",
  };
}

export type Action = { id: string; label: string; cost: number; reduction: number; note: string };

export function actions(pipe: Pipe, evaluation: Evaluation): Action[] {
  const urgent = evaluation.score < 65;
  return [
    { id: "A1", label: "立即抢修", cost: 18000, reduction: urgent ? 22 : 12, note: "停水影响大，需现场确认" },
    { id: "A2", label: "计划更换", cost: 76000, reduction: 30, note: "窗口可排期，费用较高" },
    { id: "A3", label: "加密监测", cost: 6000, reduction: 8, note: "不改变管体状态，先补数据" },
  ].map((a) => ({ ...a, note: `${a.note}；对象 ${pipe.id}` }));
}

export function assistant(question: string, pipe: Pipe, evaluation: Evaluation): string {
  const top = [...evaluation.factors].filter((f) => f.score !== null).sort((a, b) => b.contribution - a.contribution).slice(0, 3);
  const missing = evaluation.factors.filter((f) => f.score === null).map((f) => f.label);
  if (/概率|寿命|爆管预测|水力/.test(question)) {
    return "当前演示不提供失效概率、剩余寿命或水力计算；只能解释评分因子和通路影响。";
  }
  if (/方案|比较|维修|抢修/.test(question)) {
    const rows = actions(pipe, evaluation).map((a) => `${a.label}：费用约${a.cost}元，演示风险下降${a.reduction}点`).join("；");
    return `${pipe.id} 的候选措施为：${rows}。这是固定演示权重下的对照，不是正式CUV，也不代表已派单。`;
  }
  return `${pipe.id} 的演示健康分为 ${evaluation.score}（${evaluation.grade}），可信度 ${evaluation.confidence}%。主要贡献因子：${top
    .map((f) => `${f.label}(${Math.round(f.contribution * 100)}%)[${f.key}]`)
    .join("、")}。${missing.length ? `缺失项：${missing.join("、")}，未按0分处理。` : "数据项齐全。"}该分数不是失效概率，也不是正式P/C/R/H。`;
}
