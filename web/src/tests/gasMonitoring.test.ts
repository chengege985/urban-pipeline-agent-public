import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { GasMonitoringPanel } from "../features/gas/GasMonitoringPanel";
import { GasApiError, GasEngineeringApi, type GasTransport } from "../features/gas/engineeringApi";
import type { GasMonitoringReport } from "../shared/contracts/platform";

const start = "2026-11-18T00:00:00Z";
const exceededAt = "2026-11-18T00:05:00Z";
const gap = "2026-11-18T00:10:00Z";
const missingAt = "2026-11-18T00:15:00Z";
const recoveredAt = "2026-11-18T00:20:00Z";
const source = { source_id: "syn-monitor-readings", source_version: "1", source_sha256: "a".repeat(64) };
const observation = {
  ...source, source_record_id: "syn-reading-a", source_row: 2, observed_at: start,
  value: 2.5, unit: "MPa", quality: "valid",
} as const;

const report: GasMonitoringReport = {
  data_mode: "synthetic", validation_status: "not_business_validated", dataset_id: "syn-monitor-demo",
  dataset_version: "1", manifest_sha256: "b".repeat(64), policy_id: "synthetic-demo",
  evaluated_at: recoveredAt, window_start: start, cadence_seconds: 300, sustained_samples: 2,
  missing_samples: 2, monitoring_source: source,
  channels: [
    {
      asset_id: "K-001", sensor_id: "syn-pressure", metric: "pressure", unit: "MPa", state: "normal",
      lower: null, upper: 2, off_schedule_observations: 0, last_usable_at: recoveredAt,
      asset_evidence: {
        source_id: "syn-monitor-assets", source_version: "1", source_sha256: "c".repeat(64),
        source_record_id: "syn-pipe-001", source_row: 1,
      },
      events: [
        {
          kind: "limit_exceeded", at: exceededAt, previous_state: "observing", cause: "above_upper",
          evidence: [observation, { ...observation, source_record_id: "syn-reading-b", source_row: 3, observed_at: exceededAt }],
          missing_slots: [],
        },
        {
          kind: "data_missing", at: missingAt, previous_state: "limit_exceeded", cause: "no_usable_observation",
          evidence: [], missing_slots: [gap, missingAt],
        },
        {
          kind: "recovered", at: recoveredAt, previous_state: "data_missing", cause: "observations_resumed",
          evidence: [{ ...observation, source_record_id: "syn-reading-c", source_row: 4, observed_at: recoveredAt, value: 1 }],
          missing_slots: [],
        },
      ],
    },
  ],
};

function transport(payload: unknown, status = 200, calls?: Array<Parameters<GasTransport["send"]>[0]>): GasTransport {
  return { async send(request) { calls?.push(request); return { status, body: payload }; } };
}

describe("synthetic monitoring cockpit", () => {
  it("fetches the read-only snapshot through the gas transport with caller cancellation", async () => {
    const calls: Array<Parameters<GasTransport["send"]>[0]> = [];
    const signal = new AbortController().signal;
    const api = new GasEngineeringApi(transport(report, 200, calls));
    await expect(api.monitoring(signal)).resolves.toBe(report);
    expect(calls).toEqual([{ method: "GET", path: "/api/v1/gas/engineering/monitoring", signal }]);
  });

  it("rejects unapproved datasets and observations whose source identity differs from the package", async () => {
    const wrongSource = structuredClone(report);
    wrongSource.channels[0].events[0].evidence[0].source_sha256 = "d".repeat(64);
    const unexplainedGap = structuredClone(report);
    unexplainedGap.channels[0].events[1].missing_slots = [];
    for (const body of [{ ...report, data_mode: "real_authorized" }, wrongSource, unexplainedGap]) {
      await expect(new GasEngineeringApi(transport(body)).monitoring()).rejects.toMatchObject({ code: "invalid_response" });
    }
    await expect(new GasEngineeringApi(transport({ detail: "permission denied" }, 403)).monitoring()).rejects.toMatchObject({
      code: "forbidden", status: 403,
    });
  });

  it("labels synthetic playback and exposes separate transitions, readings, gap times and asset links", () => {
    const html = renderToStaticMarkup(createElement(GasMonitoringPanel, {
      resource: { status: "succeeded", key: "gas-monitoring", data: report, error: null, retry() {} },
      onOpenAsset() {},
    }));
    expect(html).toContain("合成数据回放，使用未经业务验证的样例阈值。");
    expect(html).toContain('data-monitoring-event="limit_exceeded"');
    expect(html).toContain('data-monitoring-event="data_missing"');
    expect(html).toContain('data-monitoring-event="recovered"');
    expect(html).toContain('aria-label="打开关联资产 K-001"');
    expect(html).toContain("缺少可用读数的采样时点");
    expect(html).toContain(gap);
    expect(html).toContain("没有可关联的观测行。");
    expect(html).toContain("syn-reading-a");
    expect(html).toContain(source.source_sha256);
    expect(html).toContain("syn-pipe-001");
  });

  it("shows unavailability with retry and no synthetic alert indicators when the read fails", () => {
    const html = renderToStaticMarkup(createElement(GasMonitoringPanel, {
      resource: { status: "failed", key: "gas-monitoring", data: null, error: new GasApiError("unavailable", "燃气服务暂不可用"), retry() {} },
      onOpenAsset() {},
    }));
    expect(html).toContain("重试读取监测预警");
    expect(html).toContain('data-error-code="unavailable"');
    expect(html).not.toContain("data-monitoring-event");
  });
});
