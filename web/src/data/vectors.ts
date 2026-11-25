import type { HealthGrade, Pipe, PipeOverride } from "../domain/types";

export const TEST_BASE_PIPE: Pipe = {
  id: "WP-TEST-001",
  name: "测试管段",
  fromNodeId: "N-J1",
  toNodeId: "N-J2",
  material: "PE",
  installYear: 2018,
  diameterMm: 300,
  depthM: 1.2,
  pressure: "常压",
  leakCount: 0,
  repairCount: 0,
  monitoring: "正常",
  lastInspection: "2026-09-01",
  serviceIds: ["SV-RES1"],
  lengthM: 300,
  note: "仅用于前端演示规则测试的合成管段。",
};

const OLD_PIPE: Pipe = {
  ...TEST_BASE_PIPE,
  id: "WP-TEST-OLD",
  name: "老旧测试管段",
  material: "铸铁",
  installYear: 1986,
  diameterMm: 100,
  depthM: 2.4,
  pressure: "高压",
  leakCount: 6,
  repairCount: 8,
  monitoring: "异常",
  note: "仅用于改数前后对比测试。",
};

export interface HealthVector {
  id: string;
  name: string;
  pipe: Pipe;
  override?: PipeOverride;
  expected: {
    status: "succeeded" | "insufficient_data";
    score?: number | null;
    confidence?: number;
    grade?: HealthGrade;
    missing?: string[];
    baselineScore?: number;
    overrideScore?: number;
    deterministic?: boolean;
  };
}

export const HEALTH_VECTORS: HealthVector[] = [
  {
    id: "V01",
    name: "基础健康管段",
    pipe: TEST_BASE_PIPE,
    expected: { status: "succeeded", score: 90, confidence: 100, grade: "基本健康", missing: [] },
  },
  {
    id: "V02",
    name: "全部指标最差",
    pipe: {
      ...TEST_BASE_PIPE,
      material: "铸铁",
      installYear: 1980,
      diameterMm: 80,
      depthM: 5,
      pressure: "高压",
      leakCount: 8,
      repairCount: 8,
      monitoring: "异常",
    },
    expected: { status: "succeeded", score: 7.5, confidence: 100, grade: "重点检查", missing: [] },
  },
  {
    id: "V03",
    name: "压力缺失不计权重",
    pipe: { ...TEST_BASE_PIPE, pressure: "未知" },
    expected: {
      status: "succeeded",
      score: 92.6,
      confidence: 85,
      grade: "基本健康",
      missing: ["运行压力"],
    },
  },
  {
    id: "V04",
    name: "监测质量缺失",
    pipe: { ...TEST_BASE_PIPE, monitoring: "未知" },
    expected: { status: "succeeded", score: 88.9, confidence: 90, grade: "基本健康", missing: ["监测质量"] },
  },
  {
    id: "V05",
    name: "管材未知",
    pipe: { ...TEST_BASE_PIPE, material: "未知" },
    expected: { status: "succeeded", score: 92.6, confidence: 85, grade: "基本健康", missing: ["管材"] },
  },
  {
    id: "V06",
    name: "管龄 10 年边界",
    pipe: { ...TEST_BASE_PIPE, installYear: 2016 },
    expected: { status: "succeeded", score: 85, confidence: 100, grade: "基本健康", missing: [] },
  },
  {
    id: "V07",
    name: "管径 299 mm 边界",
    pipe: { ...TEST_BASE_PIPE, diameterMm: 299 },
    expected: { status: "succeeded", score: 87.5, confidence: 100, grade: "基本健康", missing: [] },
  },
  {
    id: "V08",
    name: "漏损 4 次进入最高档",
    pipe: { ...TEST_BASE_PIPE, leakCount: 4 },
    expected: { status: "succeeded", score: 75, confidence: 100, grade: "关注", missing: [] },
  },
  {
    id: "V09",
    name: "维修 7 次进入最高档",
    pipe: { ...TEST_BASE_PIPE, repairCount: 7 },
    expected: { status: "succeeded", score: 85, confidence: 100, grade: "基本健康", missing: [] },
  },
  {
    id: "V10",
    name: "全部指标缺失",
    pipe: {
      ...TEST_BASE_PIPE,
      material: "未知",
      installYear: null,
      diameterMm: null,
      depthM: null,
      pressure: "未知",
      leakCount: null,
      repairCount: null,
      monitoring: "未知",
    },
    expected: {
      status: "insufficient_data",
      score: null,
      confidence: 0,
      grade: "数据不足",
      missing: ["管材", "管龄", "管径", "埋深", "运行压力", "漏损历史", "维修频次", "监测质量"],
    },
  },
  {
    id: "V11",
    name: "改数前后对比",
    pipe: OLD_PIPE,
    override: {
      material: "PE",
      installYear: 2016,
      diameterMm: 300,
      depthM: 1.2,
      pressure: "常压",
      leakCount: 0,
      repairCount: 0,
      monitoring: "正常",
    },
    expected: {
      status: "succeeded",
      baselineScore: 17.5,
      overrideScore: 85,
      confidence: 100,
      grade: "基本健康",
      missing: [],
    },
  },
  {
    id: "V12",
    name: "重复计算一致性",
    pipe: TEST_BASE_PIPE,
    expected: { status: "succeeded", score: 90, confidence: 100, grade: "基本健康", deterministic: true },
  },
];
