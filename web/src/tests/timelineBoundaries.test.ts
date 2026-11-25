import { beforeAll, describe, expect, it } from "vitest";
import type { TimelineRequest, WaterPulseDataset } from "../domain/types";
import { buildPlanTimeline, checkTimelineCapability } from "../engine/timeline";
import { DemoProvider } from "../providers/demoProvider";

let dataset: WaterPulseDataset;
beforeAll(async () => { dataset = await new DemoProvider().loadDataset(); });
const repair = (overrides: Partial<TimelineRequest> = {}): TimelineRequest => ({
  scenarioId: "SC-HOSP", isolationPlanId: "ISO-HOSP-MIN", planId: "PLAN-REPAIR",
  targetServiceIds: ["SV-HOSP"], horizonHours: 8, ...overrides,
});

describe("timeline numerical and graph boundaries", () => {
  it("counts a declared active source even without an incident pipe", () => {
    const data = structuredClone(dataset);
    data.nodes.push({ ...data.nodes[0], id: "SRC-ISOLATED" });
    data.services.push({ ...data.services[0], id: "SV-ISOLATED", nodeId: "SRC-ISOLATED" });
    const scenario = data.scenarios.find((s) => s.id === "SC-HOSP")!;
    scenario.activeSourceIds.push("SRC-ISOLATED");
    const request = repair({ targetServiceIds: ["SV-ISOLATED"] });
    expect(buildPlanTimeline(data, request).serviceFirstRestored["SV-ISOLATED"]).toMatchObject({
      status: "already_available", valueHours: 0,
    });
    scenario.activeSourceIds = scenario.activeSourceIds.filter((id) => id !== "SRC-ISOLATED");
    expect(buildPlanTimeline(data, request).serviceFirstRestored["SV-ISOLATED"]).toMatchObject({
      status: "not_recovered_within_horizon", valueHours: null,
    });
  });

  it("rejects overflow from finite start/duration before any successful result", () => {
    const request = repair({ actionSchedule: [{
      actionId: "PLAN-REPAIR-action", startOffsetHours: 1e308, durationHours: 1e308,
    }] });
    const result = buildPlanTimeline(dataset, request);
    expect(result.status).toBe("invalid_input");
    expect(result.planCompletion.valueHours).toBeNull();
    expect(result.events).toEqual([]);
    expect(result.issues.join(" ")).toContain("有限");
    expect(checkTimelineCapability(dataset, request).status).toBe("invalid_input");
    expect(JSON.parse(JSON.stringify(result)).status).toBe("invalid_input");
  });

  it("binds normal 9h completion/6h recovery to an independent input copy", () => {
    const request = repair({ actionSchedule: [{
      actionId: "PLAN-REPAIR-action", startOffsetHours: 1, durationHours: 8,
    }] });
    const result = buildPlanTimeline(dataset, request);
    expect(result.planCompletion.valueHours).toBe(9);
    expect(result.serviceFirstRestored["SV-HOSP"].valueHours).toBe(6);
    expect(result.timelineDataVersion).toBe(dataset.timeline.version);
    request.actionSchedule![0].durationHours = 50;
    expect(result.inputRequest?.actionSchedule?.[0].durationHours).toBe(8);
  });
});
