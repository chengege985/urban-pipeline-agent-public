import { describe, expect, it } from "vitest";
import { parseBackendReport } from "../adapters/platformReport";
import { PlatformContractError } from "../adapters/platformDataset";
import { PlatformProvider } from "../providers/platformProvider";
import { MockTransport, PlatformTransportError } from "../providers/platform/transport";
import type { RunEnvelope } from "../adapters/platformRuns";
import type { PlanTimelineResult } from "../domain/types";

const RUN_ID = "run-synthetic-report-test";
const SNAPSHOT_ID = "snap-synthetic-report-test";
const PATH = `/api/v1/runs/${RUN_ID}/report`;

/** Synthetic envelope probes; real backend/browser evidence is recorded separately. */
function response() {
  return {
    report_schema_version: "unified-report-1.0", report_id: "report-synthetic-api-test",
    source: "existing_run_only", recomputation: "none", run_id: RUN_ID,
    run_kind: "decision", run_status: "partial", business_status: "partial",
    dataset: {
      snapshot_id: SNAPSHOT_ID, data_version: "a".repeat(64), as_of: "2026-11-04T00:00:00+00:00",
      data_mode: "synthetic", source_status: "constructed", schema_version: "0.1.0", name: "Synthetic report probe",
    },
    algorithms: [{ capability_id: "candidate_plan_ranking", semantics: "given_candidates_only", version: "1.0.0", validation_status: "not_business_validated" }],
    scenario: { snapshot_id: SNAPSHOT_ID, fault_edge_id: "synthetic-fault", budget_fen: null },
    pipe: {
      pipe_id: "synthetic-pipe", model_family: "legacy_demo", model_version: "0.1.1",
      rs: null, hs: null, grade: "unknown", completeness_percent: null,
      evidence_refs: ["synthetic:missing-score"], recomputed: false,
    },
    simulation: null,
    decision: {
      run_id: RUN_ID, snapshot_id: SNAPSHOT_ID, status: "partial",
      recommended_plan_id: null, recommendation_status: "unknown",
      baseline: { snapshot_id: SNAPSHOT_ID },
      plans: [{
        plan_id: "synthetic-plan", status: "insufficient_data", utility: null,
        constraints: { status: "unknown", total_cost_fen: null, known_total_cost_fen: 250000,
          checks: [{ constraint_id: "budget", state: "unknown", left: null, right: null, unit: "fen" }] },
        metrics: { restoration_hours: null, total_cost_fen: null },
        metric_units: { restoration_hours: "hour", total_cost_fen: "fen" },
      }],
    },
    timeline: {
      status: "partial", plan_completion_hours: { status: "insufficient_data", value: null, unit: "hour" },
      first_target_service_restored_hours: { status: "succeeded", value: 6, unit: "hour" },
    },
    feedback: [], evidence_ids: [`run:${RUN_ID}`, `snapshot:${SNAPSHOT_ID}`],
    limitations: ["Synthetic probe only; missing totals are not zero."],
  };
}

function provider(body: unknown = response()) {
  const transport = new MockTransport({ [`GET ${PATH}`]: body });
  return { transport, client: new PlatformProvider({ transport, datasetId: SNAPSHOT_ID }) };
}

describe("Platform Report API: authoritative read and semantic preservation", () => {
  it("defaults to one GET report without capabilities, dataset, Run polling or computation requests", async () => {
    const raw = response();
    const before = JSON.stringify(raw);
    const { client, transport } = provider(raw);
    const actual = await client.report(RUN_ID);
    expect(actual).toBe(raw);
    expect(JSON.stringify(actual)).toBe(before);
    expect(transport.calls).toEqual([{ method: "GET", path: PATH }]);
  });

  it("preserves null, unknown, partial, units, model identity and evidence without filling values", async () => {
    const raw = response();
    const actual = parseBackendReport(raw, RUN_ID);
    expect(actual.business_status).toBe("partial");
    expect(actual.pipe).toMatchObject({ hs: null, rs: null, completeness_percent: null, model_family: "legacy_demo", model_version: "0.1.1", grade: "unknown" });
    expect(actual.decision).toEqual(raw.decision);
    expect(actual.timeline).toEqual(raw.timeline);
    expect(actual.evidence_ids).toEqual(raw.evidence_ids);
    expect(actual.simulation).toBeNull();
    expect(actual).not.toHaveProperty("runId");
    expect(actual).not.toHaveProperty("generatedAt");
  });

  it("accepts legal empty grade and dataset name in a 200 response without inventing labels", async () => {
    const raw = response();
    raw.pipe.grade = "";
    raw.dataset.name = "";
    const { client, transport } = provider(raw);
    const actual = await client.report(RUN_ID);
    expect(actual.pipe?.grade).toBe("");
    expect(actual.dataset.name).toBe("");
    expect(actual).toBe(raw);
    expect(transport.calls).toEqual([{ method: "GET", path: PATH }]);
  });

  it("does not replace a returned zero with null, unknown or a default value", () => {
    const raw = response();
    const withZeros = { ...raw, pipe: { ...raw.pipe, hs: 0, rs: 0, completeness_percent: 0 } };
    expect(parseBackendReport(withZeros, RUN_ID).pipe).toMatchObject({ hs: 0, rs: 0, completeness_percent: 0 });
  });

  it("compatibility entry only reads the server report and ignores caller result objects", async () => {
    const { client, transport } = provider();
    // The local envelope is deliberately incomplete: only the server decides report readiness.
    const local = { run_id: RUN_ID, status: "running", result: { hs: 999 } } as unknown as RunEnvelope;
    const result = await client.reportForRun(local);
    expect(result.pipe?.hs).toBeNull();
    expect(transport.calls).toEqual([{ method: "GET", path: PATH }]);
  });

  it("rejects client timeline attachment before any request", async () => {
    const { client, transport } = provider();
    await expect(client.reportForRun({ run_id: RUN_ID } as RunEnvelope, {} as PlanTimelineResult)).rejects.toBeInstanceOf(PlatformContractError);
    expect(transport.calls).toEqual([]);
  });

  it.each([401, 403, 404, 409, 422])("keeps HTTP %i as an error without another endpoint or fallback", async (status) => {
    const transport = new MockTransport({ [`GET ${PATH}`]: () => ({ status, body: { detail: "synthetic test rejection" } }) });
    const client = new PlatformProvider({ transport, datasetId: SNAPSHOT_ID });
    const error = await client.report(RUN_ID).catch((value: unknown) => value);
    expect(error).toBeInstanceOf(PlatformTransportError);
    expect(error).toMatchObject({ status, detail: "synthetic test rejection" });
    expect(transport.calls).toEqual([{ method: "GET", path: PATH }]);
  });

  it.each(["", " run-with-leading-space", "run-with-trailing-space "])("rejects invalid Run ID %j before transport", async (id) => {
    const { client, transport } = provider();
    await expect(client.report(id)).rejects.toBeInstanceOf(PlatformContractError);
    expect(transport.calls).toEqual([]);
  });

  it("rejects a response for another Run instead of displaying it", async () => {
    const { client, transport } = provider({ ...response(), run_id: "run-other" });
    await expect(client.report(RUN_ID)).rejects.toBeInstanceOf(PlatformContractError);
    expect(transport.calls).toHaveLength(1);
  });

  it.each([
    ["schema", (x: ReturnType<typeof response>) => ({ ...x, report_schema_version: "unrecognized" })],
    ["source", (x: ReturnType<typeof response>) => ({ ...x, source: "frontend_projection" })],
    ["recomputation", (x: ReturnType<typeof response>) => ({ ...x, recomputation: "client" })],
    ["snapshot", (x: ReturnType<typeof response>) => ({ ...x, dataset: { ...x.dataset, snapshot_id: null } })],
    ["content hash", (x: ReturnType<typeof response>) => ({ ...x, dataset: { ...x.dataset, data_version: "prefix-only" } })],
    ["decision run", (x: ReturnType<typeof response>) => ({ ...x, decision: { ...x.decision, run_id: "run-other" } })],
    ["decision snapshot", (x: ReturnType<typeof response>) => ({ ...x, decision: { ...x.decision, snapshot_id: "snap-other" } })],
    ["absent result", (x: ReturnType<typeof response>) => ({ ...x, decision: null })],
    ["missing evidence anchor", (x: ReturnType<typeof response>) => ({ ...x, evidence_ids: [`run:${RUN_ID}`] })],
    ["running state", (x: ReturnType<typeof response>) => ({ ...x, run_status: "running" })],
    ["score numeric string", (x: ReturnType<typeof response>) => ({ ...x, pipe: { ...x.pipe, hs: "80" } })],
    ["score bool", (x: ReturnType<typeof response>) => ({ ...x, pipe: { ...x.pipe, hs: false } })],
    ["score NaN", (x: ReturnType<typeof response>) => ({ ...x, pipe: { ...x.pipe, hs: NaN } })],
    ["score Infinity", (x: ReturnType<typeof response>) => ({ ...x, pipe: { ...x.pipe, hs: Infinity } })],
  ] as const)("rejects invalid %s envelope without constructing missing data", (_name, mutate) => {
    expect(() => parseBackendReport(mutate(response()), RUN_ID)).toThrow(PlatformContractError);
  });
});
