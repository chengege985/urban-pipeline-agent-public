import { afterEach, expect, it, vi } from "vitest";
import { DemoProvider } from "../providers/demoProvider";
import { ApiProvider, ApiRequestError } from "../providers/apiProvider";
import { askDemoAssistant } from "../engine/assistant";
import { timelineBasisKey, timelineMatchesContext } from "../engine/timelineContext";
import { buildDecisionReport, buildReportExport } from "../engine/report";
import { createDecisionSession } from "../storage/decisionStore";
import { DECISION_ALGORITHM_VERSION, DEFAULT_DECISION_CONSTRAINTS as defaults } from "../domain/constants";
import type { TimelineRequest } from "../domain/types";

const provider = new DemoProvider();
const request: TimelineRequest = { scenarioId: "SC-HOSP", isolationPlanId: "ISO-HOSP-MIN", planId: "PLAN-REPAIR",
  targetServiceIds: ["SV-HOSP"], horizonHours: 8, actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: 9 }] };
afterEach(() => vi.unstubAllGlobals());

it("demo recomputes the full input, retaining 9h completion instead of trusting a supplied timeline", async () => {
  const dataset = await provider.loadDataset();
  const timeline = await provider.timeline(request);
  const context = { ...request, timeline, timelineAsOf: dataset.timeline.asOf, timelineDataVersion: dataset.timeline.version };
  expect(timelineMatchesContext(context)).toBe(true);
  expect(askDemoAssistant("多久恢复", context).answer).toContain("9 小时");
  const corrupted = structuredClone(timeline); corrupted.planCompletion.valueHours = 777;
  const answer = await provider.ask("多久恢复", { ...context, timeline: corrupted });
  expect(answer.answer).toContain("9 小时"); expect(answer.answer).not.toContain("777");
});

it.each([
  { scenarioId: "SC-SCHOOL", isolationPlanId: "ISO-SCHOOL-MIN" },
  { planId: "PLAN-REPLACE" }, { horizonHours: 30 }, { targetServiceIds: [] },
  { actionSchedule: [{ actionId: "PLAN-REPAIR-action", finishOffsetHours: 10 }] },
  { timelineDataVersion: "changed" },
])("stale timeline cannot be explained after a context change: %j", async (patch) => {
  const dataset = await provider.loadDataset(); const timeline = await provider.timeline(request);
  const context = { ...request, timeline, timelineAsOf: dataset.timeline.asOf, timelineDataVersion: dataset.timeline.version, ...patch };
  expect(timelineMatchesContext(context)).toBe(false);
  const response = askDemoAssistant("恢复时间", context);
  expect(response.status).toBe("insufficient_data"); expect(response.answer).not.toContain("9 小时");
});

async function reportFixture() {
  const dataset = await provider.loadDataset();
  const scenario = dataset.scenarios.find(x => x.id === request.scenarioId)!;
  const isolationPlan = dataset.isolationPlans.find(x => x.id === request.isolationPlanId)!;
  const pipe = dataset.pipes.find(x => x.id === "WP-016")!;
  const selectedPlan = dataset.plans.find(x => x.id === request.planId)!;
  const impact = await provider.simulate(scenario.id, isolationPlan.id);
  const comparison = await provider.comparePlans(scenario.id, undefined, defaults, isolationPlan.id);
  const session = createDecisionSession({ pipeId: pipe.id, scenarioId: scenario.id, isolationPlanId: isolationPlan.id,
    budgetYuan: defaults.budgetYuan, resourceCapacities: defaults.resourceCapacities, confirmedDependencies: defaults.confirmedDependencies,
    selectedPlanId: selectedPlan.id, dataVersion: dataset.meta.dataVersion, algorithmVersion: dataset.meta.modelVersion,
    decisionAlgorithmVersion: DECISION_ALGORITHM_VERSION, evidenceIds: [], snapshot: {
      pipe, scenario, isolationPlan, assessment: dataset.assessments[pipe.id], impact, comparison, selectedPlan,
      timeline: await provider.timeline(request), actionSchedule: request.actionSchedule,
      targetServiceIds: request.targetServiceIds, horizonHours: request.horizonHours,
      timelineDataVersion: dataset.timeline.version, timelineBasisKey: timelineBasisKey(dataset),
    } });
  return { dataset, session };
}

it.each(["data", "algorithm", "event", "schedule"])("report becomes stale after timeline-only change: %s", async (kind) => {
  const { dataset, session } = await reportFixture();
  expect(buildDecisionReport(dataset, session)?.stale).toBe(false);
  if (kind === "data") dataset.timeline.version += "-new";
  if (kind === "algorithm") session.snapshot!.timeline!.algorithmVersion = "old";
  if (kind === "event") dataset.timeline.events[0].effectiveOffsetHours += 1;
  if (kind === "schedule") session.snapshot!.actionSchedule![0].finishOffsetHours = 10;
  const report = buildDecisionReport(dataset, session)!;
  expect(report.stale).toBe(true); expect(report.staleReasons.length).toBeGreaterThan(0);
});

it("export retains frozen versions, schedule and times; legacy reconstruction is explicitly labeled", async () => {
  const { dataset, session } = await reportFixture(); dataset.meta.dataVersion += "-new";
  const report = buildDecisionReport(dataset, session)!;
  const out = buildReportExport(report, dataset);
  expect(out.dataVersion).toBe(session.dataVersion); expect(out.resultOrigin).toBe("frozen");
  expect(out.actionSchedule).toEqual(request.actionSchedule); expect(out.timeline?.planCompletion.valueHours).toBe(9);
  delete session.snapshot;
  const legacy = buildDecisionReport(dataset, session)!;
  expect(legacy.resultOrigin).toBe("reconstructed_reference"); expect(legacy.stale).toBe(true);
  expect(buildReportExport(legacy, dataset).dataVersion).toBe(dataset.meta.dataVersion);
});

it.each([401, 403, 404, 413, 422, 429])("4xx %i keeps status and safe detail without disconnecting", async (status) => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response("{}")); vi.stubGlobal("fetch", fetch);
  const api = new ApiProvider("https://fixture.invalid"); await api.probe();
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "fixture validation reason" }), { status }));
  try { await api.ask("恢复", {}); throw new Error("expected rejection"); }
  catch (error) {
    expect(error).toBeInstanceOf(ApiRequestError);
    expect(error).toMatchObject({ status, detail: "fixture validation reason" });
  }
  expect(api.mode).toBe("live");
});

it("4xx diagnostics do not leak secrets or stack paths", async () => {
  const fetch = vi.fn().mockResolvedValueOnce(new Response("{}")); vi.stubGlobal("fetch", fetch);
  const api = new ApiProvider("https://fixture.invalid"); await api.probe();
  fetch.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "token=SECRET /root/private" }), { status: 403 }));
  await expect(api.ask("恢复", {})).rejects.toMatchObject({ status: 403, detail: "" });
});
