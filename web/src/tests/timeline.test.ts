import { beforeAll, describe, expect, it } from "vitest";
import { DEFAULT_DECISION_CONSTRAINTS, TIMELINE_ALGORITHM_VERSION, TIMELINE_AS_OF, TIMELINE_DATA_VERSION } from "../domain/constants";
import type { PlanTimelineResult, TimelineRequest, WaterPulseDataset } from "../domain/types";
import { askDemoAssistant } from "../engine/assistant";
import { buildDecisionReport, buildReportExport } from "../engine/report";
import { buildPlanTimeline, checkTimelineCapability } from "../engine/timeline";
import { DemoProvider } from "../providers/demoProvider";
import { createDecisionSession } from "../storage/decisionStore";

let dataset: WaterPulseDataset;
const provider = new DemoProvider();

beforeAll(async () => {
  dataset = await provider.loadDataset();
});

function repair(overrides: Partial<TimelineRequest> = {}): TimelineRequest {
  return {
    scenarioId: "SC-HOSP",
    isolationPlanId: "ISO-HOSP-MIN",
    planId: "PLAN-REPAIR",
    targetServiceIds: ["SV-HOSP", "SV-SCHOOL"],
    horizonHours: 8,
    ...overrides,
  };
}

function unresolved(): PlanTimelineResult {
  return buildPlanTimeline(dataset, repair());
}

describe("G01 合成时间线：显式事件与缺项", () => {
  it("6h 图通路恢复可算，缺任务结束记录时完成时间保持 null", () => {
    const gate = checkTimelineCapability(dataset, repair());
    expect(gate.status).toBe("succeeded");
    expect(gate.canCompute).toBe(true);
    expect(gate.calculationStatus).toBe("partial");
    expect(gate.availableOutputs).toContain("timeline_request");
    expect(gate.availableOutputs).toContain("service_first_restored");
    expect(gate.availableOutputs).not.toContain("plan_completion");
    expect(gate.missingItems.join("")).toContain("抢修任务");
    expect(gate.authorizationChecked).toBe(false);

    const result = unresolved();
    expect(result.status).toBe("partial");
    expect(result.timelineStatus).toBe("succeeded");
    expect(result.algorithmVersion).toBe(TIMELINE_ALGORITHM_VERSION);
    expect(result.dataMode).toBe("synthetic");
    expect(result.timeBasis).toBe("planned");
    expect(result.criterionId).toBe("topological_reachability");
    expect(result.asOf).toBe(TIMELINE_AS_OF);

    expect(result.planCompletion.status).toBe("insufficient_data");
    expect(result.planCompletion.valueHours).toBeNull();
    expect(result.firstTargetServiceRestored.status).toBe("succeeded");
    expect(result.firstTargetServiceRestored.valueHours).toBe(6);
    expect(result.allTargetServicesRestored.status).toBe("succeeded");
    expect(result.allTargetServicesRestored.valueHours).toBe(6);
    expect(result.serviceFirstRestored["SV-HOSP"]).toMatchObject({
      status: "succeeded",
      valueHours: 6,
      observationHorizonHours: 8,
    });
    expect(result.serviceFirstRestored["SV-SCHOOL"]).toMatchObject({
      status: "already_available",
      valueHours: 0,
    });
    expect(result.events).toHaveLength(1);
    expect(result.events[0]).toMatchObject({
      atHours: 6,
      actionIds: ["PLAN-REPAIR-action"],
      restoredEdgeIds: ["WP-016"],
      serviceAvailability: { "SV-HOSP": true, "SV-SCHOOL": true },
    });
    expect(result.evidenceIds).toContain("synthetic:assumption:repair-restores-at-6h");
  });

  it("补录 9h 任务结束：完成时间 9h 不裁为 8h 窗口，图通路仍为 6h", () => {
    const request = repair({
      actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: 9 }],
    });
    const gate = checkTimelineCapability(dataset, request);
    expect(gate.calculationStatus).toBe("succeeded");
    expect(gate.availableOutputs).toContain("plan_completion");
    expect(gate.availableOutputs).toContain("recovery_times");
    expect(gate.evidenceIds).toEqual(expect.arrayContaining(["synthetic:timeline:" + TIMELINE_DATA_VERSION]));

    const result = buildPlanTimeline(dataset, request);
    expect(result.status).toBe("succeeded");
    expect(result.planCompletion).toMatchObject({
      status: "succeeded",
      valueHours: 9,
      beyondHorizon: true,
      observationHorizonHours: 8,
    });
    expect(result.serviceFirstRestored["SV-HOSP"].valueHours).toBe(6);
    expect(result.issues).toEqual([]);
  });

  it("开始 1h + 持续 8h 推导 9h；finish 与计算值矛盾时整体输入非法", () => {
    const derived = buildPlanTimeline(dataset, repair({
      actionSchedule: [{ actionId: "PLAN-REPAIR-action", startOffsetHours: 1, durationHours: 8 }],
    }));
    expect(derived.status).toBe("succeeded");
    expect(derived.planCompletion.valueHours).toBe(9);

    const contradictory = buildPlanTimeline(dataset, repair({
      actionSchedule: [{
        actionId: "PLAN-REPAIR-action",
        finishOffsetHours: 7,
        startOffsetHours: 1,
        durationHours: 8,
      }],
    }));
    expect(contradictory.status).toBe("invalid_input");
    expect(contradictory.timelineStatus).toBe("insufficient_data");
    expect(contradictory.planCompletion.valueHours).toBeNull();
    expect(contradictory.firstTargetServiceRestored.valueHours).toBeNull();
    expect(contradictory.allTargetServicesRestored.valueHours).toBeNull();
    expect(contradictory.serviceFirstRestored).toEqual({});
    expect(contradictory.events).toEqual([]);
    expect(contradictory.evidenceIds).toEqual([]);
    expect(contradictory.issues.join("")).toContain("不一致");
  });

  it("24h 事件在 8h 窗口内不裁剪、不外推；服务明确窗口内未恢复", () => {
    const horizon8 = buildPlanTimeline(dataset, repair({
      planId: "PLAN-REPLACE",
      targetServiceIds: ["SV-HOSP"],
    }));
    expect(horizon8.status).toBe("succeeded");
    expect(horizon8.events).toEqual([]);
    expect(horizon8.planCompletion).toMatchObject({ valueHours: 24, beyondHorizon: true });
    expect(horizon8.serviceFirstRestored["SV-HOSP"]).toMatchObject({
      status: "not_recovered_within_horizon",
      valueHours: null,
      lowerBoundHours: 8,
      lowerBoundExclusive: true,
    });
    expect(horizon8.firstTargetServiceRestored.status).toBe("not_recovered_within_horizon");
    expect(horizon8.allTargetServicesRestored.status).toBe("not_recovered_within_horizon");
    expect(horizon8.allTargetServicesRestored.valueHours).toBeNull();

    const horizon30 = buildPlanTimeline(dataset, repair({
      planId: "PLAN-REPLACE",
      targetServiceIds: ["SV-HOSP"],
      horizonHours: 30,
    }));
    expect(horizon30.events).toHaveLength(1);
    expect(horizon30.serviceFirstRestored["SV-HOSP"].valueHours).toBe(24);
    expect(horizon30.allTargetServicesRestored.valueHours).toBe(24);
    expect(horizon30.planCompletion.valueHours).toBe(24);
    expect(horizon30.planCompletion.beyondHorizon).toBe(false);
  });

  it("监测任务完成 2h 不代表任何服务通路恢复", () => {
    const result = buildPlanTimeline(dataset, repair({
      planId: "PLAN-MONITOR",
      targetServiceIds: ["SV-HOSP"],
    }));
    expect(result.status).toBe("succeeded");
    expect(result.planCompletion).toMatchObject({ status: "succeeded", valueHours: 2 });
    expect(result.events).toEqual([]);
    expect(result.serviceFirstRestored["SV-HOSP"].status).toBe("not_recovered_within_horizon");
    expect(result.allTargetServicesRestored.status).toBe("not_recovered_within_horizon");
  });

  it("空目标明确返回不适用，而不是 0 小时完成", () => {
    const gate = checkTimelineCapability(dataset, repair({ targetServiceIds: [] }));
    expect(gate.status).toBe("succeeded");
    expect(gate.canCompute).toBe(true);
    expect(gate.availableOutputs).toEqual(["timeline_request"]);

    const result = buildPlanTimeline(dataset, repair({ targetServiceIds: [] }));
    expect(result.targetServiceIds).toEqual([]);
    expect(result.serviceFirstRestored).toEqual({});
    expect(result.firstTargetServiceRestored.status).toBe("not_applicable");
    expect(result.firstTargetServiceRestored.valueHours).toBeNull();
    expect(result.allTargetServicesRestored.status).toBe("not_applicable");
    expect(result.allTargetServicesRestored.valueHours).toBeNull();
    expect(result.planCompletion.valueHours).toBeNull();
  });

  it("遗漏目标集合与空集合可区分，遗漏直接判为非法输入", () => {
    const request = repair();
    delete request.targetServiceIds;
    const gate = checkTimelineCapability(dataset, request);
    expect(gate.status).toBe("invalid_input");
    expect(gate.canCompute).toBe(false);
    expect(gate.missingItems.join("")).toContain("空集合");

    const result = buildPlanTimeline(dataset, request);
    expect(result.status).toBe("invalid_input");
    expect(result.events).toEqual([]);
    expect(result.planCompletion.valueHours).toBeNull();
    expect(result.firstTargetServiceRestored.valueHours).toBeNull();
  });

  it("已可达目标返回 0h 初始可达，而不是计划恢复", () => {
    const result = buildPlanTimeline(dataset, repair({ targetServiceIds: ["SV-SCHOOL"] }));
    expect(result.serviceFirstRestored["SV-SCHOOL"]).toMatchObject({
      status: "already_available",
      valueHours: 0,
    });
    expect(result.firstTargetServiceRestored.status).toBe("already_available");
    expect(result.allTargetServicesRestored.status).toBe("already_available");
    expect(result.firstTargetServiceRestored.valueHours).toBe(0);
  });
});

describe("G01 合成时间线：非法输入不产生时间数字", () => {
  const cases: Array<[string, Partial<TimelineRequest>]> = [
    ["重复行动", { actionSchedule: [
      { actionId: "PLAN-REPAIR-action", finishOffsetHours: 6 },
      { actionId: "PLAN-REPAIR-action", finishOffsetHours: 9 },
    ] }],
    ["未知行动", { actionSchedule: [{ actionId: "ghost-action", finishOffsetHours: 6 }] }],
    ["布尔小时", { actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: true as unknown as number }] }],
    ["字符串小时", { actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: "6" as unknown as number }] }],
    ["负小时", { actionSchedule: [{ actionId: "PLAN-REPAIR-action", startOffsetHours: -1 }] }],
    ["无限小时", { actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: Infinity }] }],
    ["未知服务", { targetServiceIds: ["SV-GHOST"] }],
    ["重复服务", { targetServiceIds: ["SV-HOSP", "SV-HOSP"] }],
    ["未知情景", { scenarioId: "SC-GHOST" }],
    ["未知方案", { planId: "PLAN-GHOST" }],
    ["隔离不属于情景", { isolationPlanId: "ISO-SCHOOL-MIN" }],
    ["零窗口", { horizonHours: 0 }],
    ["超过最大窗口", { horizonHours: 721 }],
    ["非有限窗口", { horizonHours: Number.NaN }],
    ["布尔窗口", { horizonHours: true as unknown as number }],
  ];

  it.each(cases)("%s：gate 与结果都不返回时间", (_label, patch) => {
    const request = repair(patch);
    const gate = checkTimelineCapability(dataset, request);
    expect(gate.status).toBe("invalid_input");
    expect(gate.canCompute).toBe(false);
    expect(gate.availableOutputs).toEqual([]);
    expect(gate.evidenceIds).toEqual([]);

    const result = buildPlanTimeline(dataset, request);
    expect(result.status).toBe("invalid_input");
    expect(result.timelineStatus).toBe("insufficient_data");
    expect(result.planCompletion.valueHours).toBeNull();
    expect(result.firstTargetServiceRestored.valueHours).toBeNull();
    expect(result.allTargetServicesRestored.valueHours).toBeNull();
    expect(result.serviceFirstRestored).toEqual({});
    expect(result.events).toEqual([]);
    expect(result.evidenceIds).toEqual([]);
  });

  it("非数组时间表被拒绝", () => {
    const request = repair({ actionSchedule: { actionId: "PLAN-REPAIR-action" } as unknown as TimelineRequest["actionSchedule"] });
    const result = buildPlanTimeline(dataset, request);
    expect(result.status).toBe("invalid_input");
    expect(result.events).toEqual([]);
    expect(result.planCompletion.valueHours).toBeNull();
  });

  it("显式 null 视为未知，不转成 0，也不伪造恢复时间", () => {
    const request = repair({
      actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: null as unknown as number }],
    });
    const result = buildPlanTimeline(dataset, request);
    expect(result.status).toBe("partial");
    expect(result.planCompletion.status).toBe("insufficient_data");
    expect(result.planCompletion.valueHours).toBeNull();
    expect(result.serviceFirstRestored["SV-HOSP"].valueHours).toBe(6);
  });
});

describe("G01 数据来源与版本", () => {
  it("恢复时刻来自声明事件，不从 durationHours 推导", () => {
    const changed = structuredClone(dataset);
    const plan = changed.plans.find((item) => item.id === "PLAN-REPAIR");
    expect(plan).toBeDefined();
    plan!.durationHours = 99;
    expect(buildPlanTimeline(changed, repair()).serviceFirstRestored["SV-HOSP"].valueHours).toBe(6);

    const eventChanged = structuredClone(dataset);
    const event = eventChanged.timeline.events.find(
      (item) => item.scenarioId === "SC-HOSP" && item.planId === "PLAN-REPAIR",
    );
    expect(event).toBeDefined();
    event!.effectiveOffsetHours = 5.5;
    expect(buildPlanTimeline(eventChanged, repair()).serviceFirstRestored["SV-HOSP"].valueHours).toBe(5.5);
  });

  it("数据集只在合成时间线版本下提供服务", async () => {
    expect(dataset.timeline.version).toBe(TIMELINE_DATA_VERSION);
    expect(dataset.timeline.events).toHaveLength(10);
    expect(dataset.timeline.tasks).toHaveLength(15);
    const viaProvider = await provider.timeline(repair());
    expect(viaProvider).toEqual(unresolved());
    const gate = await provider.checkTimeline(repair());
    expect(gate.status).toBe("succeeded");
  });
});

describe("G01 助手与冻结报告", () => {
  it("助手回答保留 6h 通路、说明任务完成待补，不产生审批", () => {
    const timeline = unresolved();
    const gate = checkTimelineCapability(dataset, repair());
    const response = askDemoAssistant("这个方案多久恢复？", {
      pipeId: "WP-016",
      scenarioId: "SC-HOSP",
      isolationPlanId: "ISO-HOSP-MIN",
      planId: "PLAN-REPAIR",
      targetServiceIds: ["SV-HOSP", "SV-SCHOOL"],
      horizonHours: 8,
      timeline,
      timelineDataVersion: dataset.timeline.version,
      timelineAsOf: dataset.timeline.asOf,
      timelineGate: gate,
    }, dataset.plans);
    expect(response.status).toBe("partial");
    expect(response.answer).toContain("6 小时");
    expect(response.answer).toContain("待补数据");
    expect(response.answer).toContain("不补造完成时间");
    expect(response.toolCalls[0].tool).toBe("analyze_plan_timeline");
    expect(response.toolCalls[0].status).toBe("partial");
    expect(response.refused).toBe(false);
  });

  it("非法时间线输入不显示任何恢复数字", () => {
    const timeline = buildPlanTimeline(dataset, repair({
      actionSchedule: [{ actionId: "ghost-action", finishOffsetHours: 6 }],
    }));
    const response = askDemoAssistant("这个方案多久恢复？", {
      pipeId: "WP-016",
      scenarioId: "SC-HOSP",
      isolationPlanId: "ISO-HOSP-MIN",
      planId: "PLAN-REPAIR",
      targetServiceIds: ["SV-HOSP", "SV-SCHOOL"],
      horizonHours: 8,
      actionSchedule: [{ actionId: "ghost-action", finishOffsetHours: 6 }],
      timelineDataVersion: dataset.timeline.version,
      timelineAsOf: dataset.timeline.asOf,
      timeline,
    }, dataset.plans);
    expect(response.status).toBe("insufficient_data");
    expect(response.answer).toContain("未生成任何恢复时间");
    expect(response.answer).not.toMatch(/\d+(\.\d+)?\s*小时/);
    expect(response.evidenceIds).toEqual([]);
  });

  it("报告冻结当次时间线、输入与预检状态；旧报告不补造时间", async () => {
    const scenario = dataset.scenarios.find((item) => item.id === "SC-HOSP")!;
    const isolationPlan = dataset.isolationPlans.find((item) => item.id === "ISO-HOSP-MIN")!;
    const selectedPlan = dataset.plans.find((item) => item.id === "PLAN-REPAIR")!;
    const impact = await provider.simulate("SC-HOSP", "ISO-HOSP-MIN");
    const comparison = await provider.comparePlans("SC-HOSP", undefined, DEFAULT_DECISION_CONSTRAINTS, "ISO-HOSP-MIN");
    const timeline = unresolved();
    const gate = checkTimelineCapability(dataset, repair());
    const base = {
      pipeId: "WP-016",
      scenarioId: "SC-HOSP",
      isolationPlanId: "ISO-HOSP-MIN",
      budgetYuan: DEFAULT_DECISION_CONSTRAINTS.budgetYuan,
      resourceCapacities: DEFAULT_DECISION_CONSTRAINTS.resourceCapacities,
      confirmedDependencies: DEFAULT_DECISION_CONSTRAINTS.confirmedDependencies,
      selectedPlanId: "PLAN-REPAIR",
      dataVersion: dataset.meta.dataVersion,
      algorithmVersion: dataset.meta.modelVersion,
      evidenceIds: ["data:WP-016", ...timeline.evidenceIds],
    };
    const session = createDecisionSession({
      ...base,
      snapshot: {
        pipe: dataset.pipes.find((item) => item.id === "WP-016")!,
        assessment: dataset.assessments["WP-016"],
        scenario,
        isolationPlan,
        impact,
        comparison,
        selectedPlan,
        timeline,
        timelineGate: gate,
        targetServiceIds: ["SV-HOSP", "SV-SCHOOL"],
        horizonHours: 8,
      },
    });
    const report = buildDecisionReport(dataset, session);
    expect(report?.timeline?.planId).toBe("PLAN-REPAIR");
    expect(report?.timeline?.serviceFirstRestored["SV-HOSP"].valueHours).toBe(6);
    expect(report?.timeline?.planCompletion.valueHours).toBeNull();
    expect(report?.timelineGate?.calculationStatus).toBe("partial");
    expect(report?.targetServiceIds).toEqual(["SV-HOSP", "SV-SCHOOL"]);
    expect(report?.evidenceIds).toContain("synthetic:assumption:repair-restores-at-6h");

    const exported = buildReportExport(report!, dataset, "2026-11-01T00:00:00.000Z");
    expect(exported.timeline).toEqual(report!.timeline);
    expect(exported.timelineGate).toEqual(report!.timelineGate);
    expect(exported.targetServiceIds).toEqual(["SV-HOSP", "SV-SCHOOL"]);
    expect(exported.horizonHours).toBe(8);
    expect(exported.timeline?.algorithmVersion).toBe(TIMELINE_ALGORITHM_VERSION);
    expect(exported.limitations.join("")).toContain("服务恢复时间只是计划图通路回放");

    const legacy = createDecisionSession(base);
    const legacyReport = buildDecisionReport(dataset, legacy);
    expect(legacyReport?.timeline).toBeUndefined();
    expect(legacyReport?.stale).toBe(true);
  });
});
