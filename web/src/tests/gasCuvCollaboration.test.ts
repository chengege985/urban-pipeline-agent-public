import { describe, expect, it } from "vitest";
import type {
  GasAgentQueryResponse, GasCuvCollaborationRequest, GasCuvCollaborationSnapshot, GasCuvCriteria, GasCuvReplayResponse,
} from "../shared/contracts/platform";
import { cuvAgentRequest } from "../features/gas/cuvAgentContext";
import { cuvCriteriaDraft, prepareCuvConfirmation, type CuvCollaborationIdentity } from "../features/gas/cuvCollaborationContract";
import { GasEngineeringApi, type GasTransport } from "../features/gas/engineeringApi";
import { GasAgentApi, toolAssetIds, toolEventIds } from "../features/gas/gasAgentApi";

const at = "2026-11-18T01:12:00Z", isolationAt = "2026-11-18T01:42:00Z";
const asset = "syn-gas-pipe-K-003", event = "syn-gas-event-isolation";
const baseCriteria: GasCuvCriteria = {
  objective: "min_affected_service_hours", max_peak_affected_services: null, restore_by_hours: null,
};
const report: CuvCollaborationIdentity & Pick<GasCuvReplayResponse, "event_evidence"> = {
  case_id: "syn-gas-case-K-001", scenario_id: "syn-gas-scenario-K-001", scenario_at: at,
  topology_evidence: {
    network_content_sha256: "a".repeat(64), case_content_sha256: "b".repeat(64),
    html_source_sha256: null, replay_content_sha256: "c".repeat(64),
  },
  plans: [
    { plan_id: "staged", label: "分阶段图恢复", status: "replayed", description: "合成图方案", steps: [],
      affected_service_hours: 35, topology_restoration_hours: 5, peak_affected_service_count: 10 },
    { plan_id: "hold", label: "维持隔离", status: "replayed", description: "合成图方案", steps: [],
      affected_service_hours: 37, topology_restoration_hours: 4, peak_affected_service_count: 10 },
  ],
  event_evidence: [
    { event_id: "syn-gas-event-fault", title: "给定故障", event_kind: "given_fault", elapsed_hours: 0, replay_at: at,
      asset_ids: ["syn-gas-pipe-K-001"], source_sha256: "b".repeat(64), evidence_refs: ["case:fault"] },
    { event_id: event, title: "给定隔离", event_kind: "given_isolation", elapsed_hours: 0.5, replay_at: isolationAt,
      asset_ids: [asset], source_sha256: "b".repeat(64), evidence_refs: ["case:isolation"] },
  ],
};
const snapshot: GasCuvCollaborationSnapshot = {
  case_id: report.case_id, scenario_id: report.scenario_id, scenario_at: at,
  topology_evidence: report.topology_evidence, input_sha256: "d".repeat(64), revision: 0,
  data_mode: "synthetic", validation_status: "not_business_validated", persistence: "sqlite_local",
  criteria: baseCriteria,
  comparison: {
    status: "ranked", leading_plan_ids: ["staged"],
    plans: report.plans.map((entry, index) => ({
      plan_id: entry.plan_id, label: entry.label, status: "eligible" as const, rank: index + 1,
      affected_service_hours: entry.affected_service_hours, topology_restoration_hours: entry.topology_restoration_hours,
      peak_affected_service_count: entry.peak_affected_service_count, reasons: [],
    })),
  },
  audit: [], unknown_metrics: [{ code: "actual_gas_supply", status: "unknown", value: null, reason: "缺少正式量测。" }],
  missing_items: [{ code: "physical_model", description: "缺少正式模型。" }],
};
const path = "/api/v1/gas/engineering/cuv-replay/collaboration";

function transport(status: number, body: unknown, calls: Array<Parameters<GasTransport["send"]>[0]> = []): GasTransport {
  return { async send(request) { calls.push(request); return { status, body }; } };
}

describe("human-only CUV confirmation client contract", () => {
  it("keeps review local, sends explicit confirmation, and accepts the next audited result only", async () => {
    const calls: Array<Parameters<GasTransport["send"]>[0]> = [];
    const read = new GasEngineeringApi(transport(200, snapshot, calls));
    await expect(read.cuvCollaboration(report)).resolves.toBe(snapshot);
    const review = prepareCuvConfirmation({
      ...cuvCriteriaDraft(snapshot.criteria), objective: "min_topology_restoration_hours", peak: "10", deadline: "4.5",
      feedback: " 改为按图可达恢复时间比较。 ",
    }, snapshot);
    expect(calls.map(({ method, path }) => [method, path])).toEqual([["GET", path]]);
    expect(review).toEqual({ expected_revision: 0, input_sha256: snapshot.input_sha256, criteria: {
      objective: "min_topology_restoration_hours", max_peak_affected_services: 10, restore_by_hours: 4.5,
    }, feedback: "改为按图可达恢复时间比较。" });
    const comparison: GasCuvCollaborationSnapshot["comparison"] = {
      status: "ranked", leading_plan_ids: ["hold"],
      plans: [
        { ...snapshot.comparison.plans[0], status: "excluded", rank: null, reasons: ["recovery_deadline_missed"] },
        { ...snapshot.comparison.plans[1], rank: 1 },
      ],
    };
    const updated: GasCuvCollaborationSnapshot = { ...snapshot, revision: 1, criteria: review!.criteria, comparison,
      audit: [{ revision: 1, actor_id: "operator", occurred_at: at, input_sha256: snapshot.input_sha256,
        previous_criteria: snapshot.criteria, criteria: review!.criteria, comparison, feedback: review!.feedback }] };
    const send = new GasEngineeringApi(transport(200, updated, calls));
    const request = { ...review!, confirmed: true } satisfies GasCuvCollaborationRequest;
    await expect(send.confirmCuvCollaboration(report, request)).resolves.toBe(updated);
    expect(calls[1]).toMatchObject({ method: "POST", path, body: request });
    expect(() => send.confirmCuvCollaboration(report, { ...request, confirmed: false } as unknown as GasCuvCollaborationRequest)).toThrow();
    expect(calls).toHaveLength(2);
    await expect(new GasEngineeringApi(transport(200, { ...updated, revision: 3 })).confirmCuvCollaboration(report, request))
      .rejects.toMatchObject({ code: "invalid_response" });
  });

  it("refuses unsafe drafts and expected-input mismatches, and exposes stale/denied requests", async () => {
    const draft = { ...cuvCriteriaDraft(snapshot.criteria), feedback: "人工核对" };
    expect(prepareCuvConfirmation({ ...draft, peak: "3.2" }, snapshot)).toBeNull();
    expect(prepareCuvConfirmation({ ...draft, deadline: "-1" }, snapshot)).toBeNull();
    expect(prepareCuvConfirmation({ ...draft, feedback: "line\nbreak" }, snapshot)).toBeNull();
    const request = { ...prepareCuvConfirmation(draft, snapshot)!, confirmed: true } as const;
    await expect(new GasEngineeringApi(transport(409, null)).confirmCuvCollaboration(report, request))
      .rejects.toMatchObject({ status: 409 });
    await expect(new GasEngineeringApi(transport(403, null)).cuvCollaboration(report)).rejects.toMatchObject({ status: 403 });
    for (const bad of [
      { ...snapshot, unknown_metrics: [{ ...snapshot.unknown_metrics[0], value: 0 }] },
      { ...snapshot, topology_evidence: { ...snapshot.topology_evidence, replay_content_sha256: "f".repeat(64) } },
      { ...snapshot, comparison: { ...snapshot.comparison, leading_plan_ids: ["foreign"] } },
    ]) {
      await expect(new GasEngineeringApi(transport(200, bad)).cuvCollaboration(report)).rejects.toMatchObject({ code: "invalid_response" });
    }
  });
});

describe("read-only CUV Agent context", () => {
  it("uses the active event and asset, or the earliest matching asset time", () => {
    expect(cuvAgentRequest(report, { event_id: event, asset_id: asset })).toMatchObject({
      event_id: event, asset_id: asset, case_id: report.case_id, cuv_at: isolationAt, tool_hint: "cuv.query",
    });
    expect(cuvAgentRequest(report, { asset_id: asset }).cuv_at).toBe(isolationAt);
    expect(cuvAgentRequest(report, {}).cuv_at).toBe(at);
  });

  it("binds the reply to the requested context and retains source drill-down references", async () => {
    const calls: Array<Parameters<GasTransport["send"]>[0]> = [];
    const data = {
      summary: "当前为图比较，实际供气保留未知。",
      context: { case_id: report.case_id, scenario_id: report.scenario_id, event_id: event, asset_id: asset, at: isolationAt, scenario_at: at },
      plans: report.plans.map((entry) => ({ ...entry, phase: null, selected_asset_blocked: null })),
      unknown_metrics: snapshot.unknown_metrics,
      monitoring_evidence: { as_of: at, status: "unavailable", channels: [] },
      event_evidence: [report.event_evidence[1]], confirmed_comparison: null,
    };
    const result: GasAgentQueryResponse = {
      request_id: "request", status: "partial", data_mode: "synthetic", dataset_id: "syn-gas-network-html54-0.1.0",
      model_status: "unavailable", parser_source: "local_rule_not_llm", intent: "cuv_query", evidence_refs: ["case:isolation"],
      limitations: [], tools: [{ tool_name: "cuv.query", tool_version: "gas-tools-0.1.0", status: "partial",
        data, evidence_refs: ["case:isolation"], limitation: "实际供气未知。" }],
    };
    const request = cuvAgentRequest(report, { event_id: event, asset_id: asset });
    await expect(new GasAgentApi(transport(200, result, calls)).query(request)).resolves.toBe(result);
    expect(calls).toEqual([{ method: "POST", path: "/api/v1/gas/engineering/agent/query", signal: undefined, body: request }]);
    expect(calls[0].body).not.toHaveProperty("confirmed");
    expect(toolAssetIds(result.tools[0])).toEqual([asset]);
    expect(toolEventIds(result.tools[0])).toEqual([event]);
    await expect(new GasAgentApi(transport(200, result)).query({ ...request, asset_id: "other" }))
      .rejects.toMatchObject({ code: "invalid_response" });
  });
});
