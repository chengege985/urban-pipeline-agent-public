import { describe, expect, it } from "vitest";
import { HEALTH_VECTORS } from "../data/vectors";
import { evaluateDemoHealth } from "../engine/demoHealth";

describe("DemoHealth 12 条测试向量", () => {
  it("12 条向量全部存在且 ID 唯一", () => {
    expect(HEALTH_VECTORS).toHaveLength(12);
    expect(new Set(HEALTH_VECTORS.map((vector) => vector.id)).size).toBe(12);
  });

  for (const vector of HEALTH_VECTORS) {
    it(`${vector.id} ${vector.name}`, () => {
      const actual = evaluateDemoHealth(vector.pipe, vector.override);
      const expected = vector.expected;
      expect(actual.status).toBe(expected.status);

      if (expected.score !== undefined) {
        if (expected.score === null) {
          expect(actual.score).toBeNull();
        } else {
          expect(actual.score).toBeCloseTo(expected.score, 1);
        }
      }
      if (expected.confidence !== undefined) {
        expect(actual.confidence).toBeCloseTo(expected.confidence, 1);
      }
      if (expected.grade !== undefined) {
        expect(actual.grade).toBe(expected.grade);
      }
      if (expected.missing !== undefined) {
        expect(actual.missing).toEqual(expected.missing);
      }
      if (expected.baselineScore !== undefined) {
        const baseline = evaluateDemoHealth(vector.pipe);
        expect(baseline.score).toBeCloseTo(expected.baselineScore, 1);
      }
      if (expected.overrideScore !== undefined) {
        expect(actual.score).toBeCloseTo(expected.overrideScore, 1);
      }
      if (expected.deterministic) {
        expect(evaluateDemoHealth(vector.pipe, vector.override)).toEqual(actual);
      }
    });
  }

  it("因子贡献在有风险时归一化，在无风险时为零", () => {
    const base = HEALTH_VECTORS[0].pipe;
    const result = evaluateDemoHealth(base);
    const total = result.factors.reduce((sum, factor) => sum + factor.contribution, 0);
    expect(total).toBeCloseTo(1, 5);
  });

  it("缺失项不会被当作低风险 0 分", () => {
    const result = evaluateDemoHealth({ ...HEALTH_VECTORS[0].pipe, pressure: "未知" });
    const pressure = result.factors.find((factor) => factor.key === "pressure");
    expect(pressure?.score).toBeNull();
    expect(pressure?.weightedRisk).toBe(0);
    expect(result.missing).toContain("运行压力");
    expect(result.confidence).toBeLessThan(100);
  });
});
