import { describe, expect, it } from "vitest";
import { DEMO_NODES, DEMO_PIPES, DEMO_SCENARIOS, DEMO_SERVICES } from "../data/network";
import { simulateServiceImpact } from "../engine/network";

function scenario(id: string) {
  const found = DEMO_SCENARIOS.find((item) => item.id === id);
  if (!found) throw new Error(`missing scenario ${id}`);
  return found;
}

function impact(id: string) {
  return simulateServiceImpact(DEMO_PIPES, DEMO_NODES, DEMO_SERVICES, scenario(id));
}

describe("拓扑服务影响", () => {
  it("医院支线故障影响医院，并标记关键设施", () => {
    const result = impact("SC-HOSP");
    expect(result.status).toBe("succeeded");
    expect(result.affectedServices.map((service) => service.serviceId)).toEqual(["SV-HOSP"]);
    expect(result.affectedCriticalServiceIds).toEqual(["SV-HOSP"]);
    expect(result.affectedFacilities).toBe(1);
    expect(result.unreachableNodeIds).toContain("N-H");
    expect(result.limitations.join(" ")).toContain("拓扑");
  });

  it("居民区叠加故障影响两个居民服务", () => {
    const result = impact("SC-RES");
    expect(result.affectedServices.map((service) => service.serviceId).sort()).toEqual(["SV-RES1", "SV-RES2"]);
    expect(result.affectedHouseholds).toBe(2140);
    expect(result.affectedFacilities).toBe(0);
  });

  it("双源切换后服务保持连通", () => {
    const result = impact("SC-DUAL");
    expect(result.affectedServices).toEqual([]);
    expect(result.affectedCriticalServiceIds).toEqual([]);
    expect(result.unreachableNodeIds).toEqual(["N-S1"]);
  });

  it("商业支线故障只影响商业中心", () => {
    const result = impact("SC-MALL");
    expect(result.affectedServices.map((service) => service.serviceId)).toEqual(["SV-MALL"]);
  });

  it("同一情景重复计算结果一致", () => {
    expect(impact("SC-HOSP")).toEqual(impact("SC-HOSP"));
  });
});
