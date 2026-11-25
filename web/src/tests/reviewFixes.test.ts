import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiProvider } from "../providers/apiProvider";
import { DemoProvider } from "../providers/demoProvider";
import { DECISION_ALGORITHM_VERSION, DEFAULT_DECISION_CONSTRAINTS as defaults } from "../domain/constants";
import { compareDemoPlans } from "../engine/plans";
import { buildDecisionReport } from "../engine/report";
import { createDecisionSession } from "../storage/decisionStore";

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("API timeouts and availability", () => {
  it("a valid assistant response after 5s is not aborted by the 4s probe deadline", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValueOnce(new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const provider = new ApiProvider("https://fixture.invalid");
    await provider.probe();
    fetch.mockImplementationOnce((_url, init) => new Promise((resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(new Error("aborted")));
      setTimeout(() => resolve(new Response('{"answer":"ready"}')), 5000);
    }));
    const pending = provider.ask("explain", {});
    await vi.advanceTimersByTimeAsync(5000);
    await expect(pending).resolves.toMatchObject({ answer: "ready" });
    expect(provider.mode).toBe("live");
  });
  it("a disconnected request marks the provider unavailable and notifies its UI", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(new Response("{}"));
    vi.stubGlobal("fetch", fetch);
    const notify = vi.fn(); const provider = new ApiProvider("https://fixture.invalid", notify);
    await provider.probe(); fetch.mockRejectedValueOnce(new Error("offline"));
    await expect(provider.ask("explain", {})).rejects.toThrow("实时接口不可用");
    expect(provider.mode).toBe("unavailable"); expect(notify).toHaveBeenCalledOnce();
  });
  it("body reading stays inside the assistant deadline", async () => {
    vi.useFakeTimers();
    const fetch = vi.fn().mockResolvedValueOnce(new Response("{}")); vi.stubGlobal("fetch", fetch);
    const provider = new ApiProvider("https://fixture.invalid"); await provider.probe();
    fetch.mockImplementationOnce((_url, init) => Promise.resolve({ ok: true, json: () => new Promise((_resolve, reject) => {
      init.signal.addEventListener("abort", () => reject(new Error("aborted")));
    }) }));
    const pending = expect(provider.ask("explain", {})).rejects.toThrow("请求超时");
    await vi.advanceTimersByTimeAsync(180000); await pending;
    expect(provider.mode).toBe("unavailable");
  });
});

async function frozenSession() {
  const provider = new DemoProvider(); const dataset = await provider.loadDataset();
  const pipe = dataset.pipes.find((p) => p.id === "WP-016")!;
  const scenario = dataset.scenarios.find((s) => s.id === "SC-HOSP")!;
  const isolationPlan = dataset.isolationPlans.find((s) => s.id === "ISO-HOSP-MIN")!;
  const override = { installYear: 2026, leakCount: 0, material: "PE" as const, pressure: "低压" as const };
  const assessment = await provider.evaluate(pipe, override);
  const impact = await provider.simulate(scenario.id, isolationPlan.id);
  const comparison = await provider.comparePlans(scenario.id, undefined, defaults, isolationPlan.id);
  const selectedPlan = dataset.plans.find((p) => p.id === comparison.recommendedPlanId)!;
  const session = createDecisionSession({ pipeId: pipe.id, scenarioId: scenario.id, isolationPlanId: isolationPlan.id,
    budgetYuan: defaults.budgetYuan, resourceCapacities: defaults.resourceCapacities, confirmedDependencies: defaults.confirmedDependencies,
    selectedPlanId: selectedPlan.id, dataVersion: dataset.meta.dataVersion, algorithmVersion: dataset.meta.modelVersion,
    decisionAlgorithmVersion: DECISION_ALGORITHM_VERSION, override, evidenceIds: ["data:WP-016"],
    snapshot: { pipe: { ...pipe, ...override }, assessment, scenario, isolationPlan, impact, comparison, selectedPlan } });
  return { dataset, session, provider, comparison, impact };
}

describe("the selected analysis is the saved report", () => {
  it("WP-016 what-if 71.3 is retained instead of reverting to original 17.5", async () => {
    const { dataset, session } = await frozenSession();
    expect(dataset.assessments["WP-016"].score).toBe(17.5);
    const report = buildDecisionReport(dataset, session)!;
    expect(report.assessment.score).toBe(71.3); expect(report.pipe.installYear).toBe(2026);
    expect(report.stale).toBe(false);
    dataset.assessments["WP-016"].score = 0;
    expect(buildDecisionReport(dataset, session)?.assessment.score).toBe(71.3);
  });
  it("does not replace a missing or cross-scenario isolation with a default", async () => {
    const { dataset, session } = await frozenSession();
    expect(buildDecisionReport(dataset, { ...session, isolationPlanId: "missing" })).toBeNull();
    const other = dataset.isolationPlans.find((p) => p.scenarioId !== session.scenarioId)!;
    expect(buildDecisionReport(dataset, { ...session, isolationPlanId: other.id })).toBeNull();
  });
  it("cannot silently approve an old decision algorithm using a new ranking", async () => {
    const { dataset, session } = await frozenSession();
    expect(buildDecisionReport(dataset, { ...session, decisionAlgorithmVersion: "old" })?.stale).toBe(true);
  });
});

describe("same decision baseline across views", () => {
  it("does not change existing utilities when adding an unaffordable candidate", async () => {
    const { dataset, impact } = await frozenSession();
    const base = compareDemoPlans("SC-HOSP", impact, dataset.plans, defaults);
    const extra = { ...dataset.plans[0], id: "EXPENSIVE", costYuan: 900000 };
    const added = compareDemoPlans("SC-HOSP", impact, [...dataset.plans, extra], defaults);
    for (const row of base.rows) expect(added.rows.find((r) => r.planId === row.planId)?.utility).toBe(row.utility);
    expect(added.recommendedPlanId).toBe(base.recommendedPlanId);
  });
  it("partial or mismatched scenario results cannot yield recommendations", async () => {
    const { dataset, impact } = await frozenSession();
    for (const bad of [{ ...impact, status: "partial" as const }, { ...impact, scenarioId: "wrong" }]) {
      const result = compareDemoPlans("SC-HOSP", bad, dataset.plans, defaults);
      expect(result.status).toBe("insufficient_data"); expect(result.rows).toEqual([]); expect(result.recommendedPlanId).toBeNull();
    }
  });
  it("assistant keeps the 5000 yuan budget instead of defaulting to an affordable repair", async () => {
    const provider = new DemoProvider();
    const response = await provider.ask("比较方案", { scenarioId: "SC-HOSP", isolationPlanId: "ISO-HOSP-MIN", constraints: { ...defaults, budgetYuan: 5000 } });
    expect(response.answer).toContain("5000"); expect(response.answer).toContain("没有可行方案");
    expect(response.toolCalls.find((call) => call.tool === "compare_demo_plans")?.summary).toBe("推荐 无");
  });
  it("assistant does not invent a budget and explicit empty candidates remain empty", async () => {
    const provider = new DemoProvider();
    expect((await provider.ask("比较方案", { scenarioId: "SC-HOSP" })).status).toBe("insufficient_data");
    expect((await provider.comparePlans("SC-HOSP", [], defaults)).recommendedPlanId).toBeNull();
    await expect(provider.comparePlans("SC-HOSP", ["unknown"], defaults)).rejects.toThrow();
    await expect(provider.comparePlans("SC-HOSP", ["PLAN-REPAIR", "PLAN-REPAIR"], defaults)).rejects.toThrow();
  });
});
