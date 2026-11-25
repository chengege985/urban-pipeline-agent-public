import { describe, expect, it } from "vitest";
import { DEMO_NODES, DEMO_PIPES, DEMO_PLANS, DEMO_SCENARIOS, DEMO_SERVICES } from "../data/network";
import { evaluateDemoHealth } from "../engine/demoHealth";
import { simulateServiceImpact } from "../engine/network";
import { askDemoAssistant } from "../engine/assistant";
import { TEST_BASE_PIPE } from "../data/vectors";
import type { AgentContext } from "../domain/types";
import { DEFAULT_DECISION_CONSTRAINTS } from "../domain/constants";

const pipe = DEMO_PIPES.find((item) => item.id === "WP-016") ?? TEST_BASE_PIPE;
const assessment = evaluateDemoHealth(pipe);
const hospital = DEMO_SCENARIOS.find((item) => item.id === "SC-HOSP");
if (!hospital) throw new Error("SC-HOSP missing");
const impact = simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, hospital);

const context: AgentContext = { pipe, pipeId: pipe.id, assessment, scenarioId: hospital.id, impact, constraints: DEFAULT_DECISION_CONSTRAINTS };

describe("演示助手", () => {
  it("解释评分时引用真实管段和 DemoHealth 证据", () => {
    const response = askDemoAssistant("为什么这段管段优先检查？", context);
    expect(response.status).toBe("succeeded");
    expect(response.answer).toContain(pipe.id);
    expect(response.answer).toContain("DemoHealth");
    expect(response.evidenceIds).toContain(`data:${pipe.id}`);
    expect(response.toolCalls.some((call) => call.tool === "evaluate_demo_health")).toBe(true);
  });

  it("解释故障影响时返回服务对象与关键设施", () => {
    const response = askDemoAssistant("如果这段爆管会影响哪些服务？", context);
    expect(response.status).toBe("succeeded");
    expect(response.answer).toContain("市第二医院");
    expect(response.evidenceIds).toContain("scenario:SC-HOSP");
  });

  it("比较方案时使用本地固定方案，不伪称正式 CUV", () => {
    const response = askDemoAssistant("有哪些维护方案可以比较？", context, DEMO_PLANS);
    expect(response.status).toBe("succeeded");
    expect(response.answer).toContain("立即抢修");
    expect(response.answer).toContain("不是正式 CUV");
    expect(response.toolCalls.some((call) => call.tool === "compare_demo_plans")).toBe(true);
  });

  it("概率/寿命/关阀请求被明确拒绝", () => {
    for (const question of ["能预测爆管概率吗？", "给出剩余寿命", "直接关阀并派单"]) {
      const response = askDemoAssistant(question, context, DEMO_PLANS);
      expect(response.status).toBe("refused");
      expect(response.refused).toBe(true);
      expect(response.toolCalls[0].tool).toBe("refuse_out_of_scope");
    }
  });

  it("始终声明演示助手未连接 Qwen 或真实 Agent", () => {
    const response = askDemoAssistant("为什么这段优先？", context);
    expect(response.mode).toBe("demo");
    expect(response.limitations.join(" ")).toContain("未连接 Qwen");
  });

  it("缺少上下文时不编造结论", () => {
    const response = askDemoAssistant("为什么这段优先？", {});
    expect(response.status).toBe("insufficient_data");
    expect(response.toolCalls[0].status).toBe("insufficient_data");
  });
});
