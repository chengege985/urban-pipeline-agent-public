import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { GasEvidencePanel } from "../features/gas/GasPanels";
import type { GasAsset, GasEvent } from "../features/gas/contracts";

type EventEvidence = Pick<GasEvent, "event_id" | "evidence_refs">;
type AssetEvidence = Pick<GasAsset, "asset_id" | "evidence_refs">;
const reads = vi.hoisted(() => ({
  caseView: { data: null },
  event: { data: null as EventEvidence | null },
  asset: { data: null as AssetEvidence | null },
}));
vi.mock("../features/gas/GasCockpitProvider", () => ({ useGasCockpit: () => reads }));

const event = { event_id: "syn-gas-event-isolation", evidence_refs: ["case.json", "network.json"] };
const asset = { asset_id: "syn-gas-pipe-K-001", evidence_refs: ["network.json", "network.json"] };

function render(eventData: EventEvidence | null, assetData: AssetEvidence | null) {
  reads.event.data = eventData;
  reads.asset.data = assetData;
  return renderToStaticMarkup(createElement(GasEvidencePanel));
}

describe("燃气中控证据窗口", () => {
  it("进入关联资产后分别展示事件与资产来源，同一来源可同时属于两个对象", () => {
    const html = render(event, asset);
    expect(html).toContain(event.event_id);
    expect(html).toContain(asset.asset_id);
    const [eventPart, assetPart] = html.split(asset.asset_id);
    expect(eventPart).toContain("case.json");
    expect(eventPart).toContain("network.json");
    expect(assetPart).toContain("network.json");
    expect(assetPart).not.toContain("case.json");
    expect(html.match(/network\.json/g)).toHaveLength(2);
  });

  it("关联资产未提供证据时保留事件证据及资产空态", () => {
    const html = render(event, { ...asset, evidence_refs: [] });
    expect(html).toContain("case.json");
    expect(html.split(asset.asset_id)[1]).toContain("接口未提供证据引用");
  });

  it("切换独立资产或返回事件时，只显示当前已加载对象", () => {
    render(event, asset);
    const independent = render(null, asset);
    expect(independent).toContain(asset.asset_id);
    expect(independent).not.toContain(event.event_id);
    expect(independent).not.toContain("case.json");
    const eventOnly = render(event, null);
    expect(eventOnly).toContain(event.event_id);
    expect(eventOnly).not.toContain(asset.asset_id);
  });

  it("未读取到任何对象时不显示先前对象或其引用", () => {
    render(event, asset);
    const html = render(null, null);
    expect(html).not.toContain(event.event_id);
    expect(html).not.toContain(asset.asset_id);
    expect(html).not.toContain("network.json");
    expect(html).toContain("当前没有已加载的事件或资产");
  });
});
