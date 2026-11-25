import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { GasCuvReplayResponse } from "../shared/contracts/platform";
import { GasCuvReplayPanel } from "../features/gas/GasCuvReplayPanel";
import { GasApiError, GasEngineeringApi, type GasTransport } from "../features/gas/engineeringApi";

const scenarioAt = "2026-11-18T01:12:00Z";
const cutoffReading = "2026-11-18T01:10:00Z";
const isolatedAt = "2026-11-18T01:42:00Z";
const lastGap = "2026-11-18T01:11:00Z";
const asset = "syn-gas-pipe-K-001";
const eventId = "syn-gas-event-fault";
const source = { source_id: "syn-monitor", source_version: "1", source_sha256: "a".repeat(64) };

const report: GasCuvReplayResponse = {
  schema_version: "gas-cuv-replay-0.1.0", data_mode: "synthetic", validation_status: "not_business_validated",
  semantic: "graph_connectivity_only", status: "partial", case_id: "syn-gas-case-K-001", network_id: "syn-gas-network",
  scenario_id: "syn-gas-scenario-K-001", scenario_at: scenarioAt, analysis_window_hours: 12,
  fault_asset_ids: [asset], algorithm_version: "replay-demo", comparison_basis: "两套方案使用相同合成故障起点。",
  topology_evidence: {
    network_content_sha256: "b".repeat(64), case_content_sha256: "c".repeat(64),
    html_source_sha256: null, replay_content_sha256: "d".repeat(64),
  },
  plans: [
    {
      plan_id: "syn-staged", label: "分段图恢复", description: "采用给定隔离及分段图恢复假设。", status: "replayed",
      topology_restoration_hours: 5, peak_affected_service_count: 9, affected_service_hours: 35,
      steps: [
        {
          at: isolatedAt, elapsed_hours: 0.5, kind: "isolate", status: "evaluated",
          affected_service_ids: ["syn-service-1"], affected_node_ids: ["syn-node-1"],
          affected_consumers: { household: 4, business: 1, facility: 0 }, restored_service_ids: [],
          blocked_asset_ids: [asset],
          constraints: [{ code: "graph_isolation", status: "met", explanation: "按停用边和阀门计算。" }],
        },
      ],
    },
    {
      plan_id: "syn-hold", label: "保持单元隔离", description: "关联时点缺失，阶段受阻。", status: "blocked",
      topology_restoration_hours: null, peak_affected_service_count: null, affected_service_hours: null,
      steps: [
        {
          at: isolatedAt, elapsed_hours: 0.5, kind: "isolate", status: "blocked",
          affected_service_ids: null, affected_node_ids: null, affected_consumers: null, restored_service_ids: null,
          blocked_asset_ids: [asset],
          constraints: [{ code: "graph_isolation", status: "blocked", explanation: "故障端仍可达。" }],
        },
      ],
    },
  ],
  unknown_metrics: [{ code: "actual_gas_supply", status: "unknown", value: null, reason: "缺少授权量测。" }],
  missing_items: [{ code: "hydraulic", description: "缺少经过校核的水力模型。" }],
  event_evidence: [
    {
      event_id: eventId, title: "给定故障样例", event_kind: "given_fault", asset_ids: [asset],
      elapsed_hours: 0, replay_at: scenarioAt, source_sha256: "c".repeat(64), evidence_refs: ["gas-m0:case:1"],
    },
  ],
  monitoring_evidence: {
    status: "available", as_of: scenarioAt, dataset_id: "syn-gas-monitoring", dataset_version: "1", manifest_sha256: "e".repeat(64),
    policy_id: "syn-gas-monitoring-rule", source, channels: [
      {
        asset_id: asset, sensor_id: "syn-sensor", metric: "pressure", unit: "MPa", relation: "fault_asset",
        state: "data_missing", last_usable_at: cutoffReading,
        transition: {
          kind: "data_missing", cause: "no_usable_observation", previous_state: "observing", at: scenarioAt,
          missing_slots: [lastGap, scenarioAt],
          evidence: [{
            ...source, source_record_id: "syn-observation", source_row: 1, observed_at: cutoffReading,
            value: 1.5, unit: "MPa", quality: "valid",
          }],
        },
      },
    ],
  },
};

function transport(payload: unknown, status = 200, calls?: Array<Parameters<GasTransport["send"]>[0]>): GasTransport {
  return { async send(request) { calls?.push(request); return { status, body: payload }; } };
}
const caseIdentity = {
  case_id: report.case_id, network_id: report.network_id,
  evidence: {
    network_content_sha256: report.topology_evidence.network_content_sha256,
    case_content_sha256: report.topology_evidence.case_content_sha256,
  },
};

describe("synthetic gas CUV comparison", () => {
  it("reads the scoped endpoint and refuses a result from an earlier active network revision", async () => {
    const calls: Array<Parameters<GasTransport["send"]>[0]> = [];
    const signal = new AbortController().signal;
    const api = new GasEngineeringApi(transport(report, 200, calls));
    await expect(api.cuvReplay(signal, caseIdentity)).resolves.toBe(report);
    expect(calls).toEqual([{ method: "GET", path: "/api/v1/gas/engineering/cuv-replay", signal }]);
    await expect(api.cuvReplay(signal, { ...caseIdentity, evidence: { ...caseIdentity.evidence, network_content_sha256: "f".repeat(64) } }))
      .rejects.toMatchObject({ code: "invalid_response" });
    await expect(new GasEngineeringApi(transport({ detail: "permission denied" }, 403)).cuvReplay())
      .rejects.toMatchObject({ code: "forbidden", status: 403 });
  });

  it("rejects unapproved modes, asserted physical outcomes, future observations, and open-ended event timing", async () => {
    const futureObservation = structuredClone(report);
    futureObservation.monitoring_evidence.channels[0].transition!.evidence[0].observed_at = isolatedAt;
    const wrongOrigin = structuredClone(report);
    wrongOrigin.monitoring_evidence.channels[0].transition!.evidence[0].source_sha256 = "f".repeat(64);
    const wrongEvent = structuredClone(report);
    wrongEvent.event_evidence[0].replay_at = isolatedAt;
    const assertedPhysicalSupply = { ...report, unknown_metrics: [{ ...report.unknown_metrics[0], value: 0 }] };
    for (const payload of [{ ...report, data_mode: "real_authorized" }, assertedPhysicalSupply, futureObservation, wrongOrigin, wrongEvent]) {
      await expect(new GasEngineeringApi(transport(payload)).cuvReplay()).rejects.toMatchObject({ code: "invalid_response" });
    }
  });

  it("renders both schedules, unknowns, constraints, timestamps, and asset and event provenance", () => {
    const html = renderToStaticMarkup(createElement(GasCuvReplayPanel, {
      resource: { status: "succeeded", key: "gas-cuv-replay", data: report, error: null, retry() {} },
      onOpenAsset() {}, onOpenEvent() {},
    }));
    expect(html).toContain("实际供气、压力、流量和现场复气保持未知");
    expect(html).toContain('data-cuv-plan="syn-staged"');
    expect(html).toContain('data-cuv-plan="syn-hold"');
    expect(html).toContain('data-cuv-phase-status="blocked"');
    expect(html).toContain("故障端仍可达");
    expect(html).toContain("未通过切片拟限制的资产");
    expect(html).toContain("5 h");
    expect(html).toContain('data-cuv-unknown="actual_gas_supply"');
    expect(html).toContain("缺少经过校核的水力模型");
    expect(html).toContain("syn-observation");
    expect(html).toContain(source.source_sha256);
    expect(html).toContain(lastGap);
    expect(html).toContain('aria-label="定位资产 ' + asset + '"');
    expect(html).toContain('aria-label="定位事件 ' + eventId + '"');
  });

  it("presents unavailability without synthesizing plan results", () => {
    const html = renderToStaticMarkup(createElement(GasCuvReplayPanel, {
      resource: {
        status: "failed", key: "gas-cuv-replay", data: null, error: new GasApiError("unavailable", "燃气服务暂不可用"), retry() {},
      },
      onOpenAsset() {}, onOpenEvent() {},
    }));
    expect(html).toContain("重试读取 CUV 推演");
    expect(html).toContain('data-error-code="unavailable"');
    expect(html).not.toContain("data-cuv-plan");
  });
});
