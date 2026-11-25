import { describe, expect, it } from "vitest";
import { DEMO_NODES, DEMO_PIPES, DEMO_PLANS, DEMO_SCENARIOS, DEMO_SERVICES } from "../data/network";

describe("合成数据一致性", () => {
  it("包含 24 条管段、5 个服务、5 个情景、3 个方案", () => {
    expect(DEMO_PIPES).toHaveLength(24);
    expect(DEMO_SERVICES).toHaveLength(5);
    expect(DEMO_SCENARIOS).toHaveLength(5);
    expect(DEMO_PLANS).toHaveLength(3);
  });

  it("所有 ID 唯一", () => {
    expect(new Set(DEMO_PIPES.map((pipe) => pipe.id)).size).toBe(DEMO_PIPES.length);
    expect(new Set(DEMO_SERVICES.map((service) => service.id)).size).toBe(DEMO_SERVICES.length);
    expect(new Set(DEMO_SCENARIOS.map((scenario) => scenario.id)).size).toBe(DEMO_SCENARIOS.length);
    expect(new Set(DEMO_PLANS.map((plan) => plan.id)).size).toBe(DEMO_PLANS.length);
  });

  it("管段端点、服务节点和场景引用都存在", () => {
    const nodeIds = new Set(DEMO_NODES.map((node) => node.id));
    const pipeIds = new Set(DEMO_PIPES.map((pipe) => pipe.id));
    const serviceIds = new Set(DEMO_SERVICES.map((service) => service.id));
    for (const pipe of DEMO_PIPES) {
      expect(nodeIds.has(pipe.fromNodeId)).toBe(true);
      expect(nodeIds.has(pipe.toNodeId)).toBe(true);
      expect(pipe.serviceIds.length).toBeGreaterThan(0);
      for (const serviceId of pipe.serviceIds) expect(serviceIds.has(serviceId)).toBe(true);
    }
    for (const service of DEMO_SERVICES) expect(nodeIds.has(service.nodeId)).toBe(true);
    for (const scenario of DEMO_SCENARIOS) {
      for (const pipeId of scenario.failedPipeIds) expect(pipeIds.has(pipeId)).toBe(true);
      for (const sourceId of scenario.activeSourceIds) expect(nodeIds.has(sourceId)).toBe(true);
    }
  });

  it("所有管段为合成 ID，不使用真实设施命名", () => {
    expect(DEMO_PIPES.every((pipe) => pipe.id.startsWith("WP-"))).toBe(true);
    expect(DEMO_NODES.every((node) => node.id.startsWith("N-"))).toBe(true);
    expect(DEMO_SERVICES.every((service) => service.id.startsWith("SV-"))).toBe(true);
  });
});
