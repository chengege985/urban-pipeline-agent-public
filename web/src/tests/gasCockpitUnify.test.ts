import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import fixtureJson from "./fixtures/gasCaseResponse.json";
import type { GasCaseResponse } from "../features/gas/types";
import { DEFAULT_VIEWPORT, fitGasMapping, GAS_WINDOWS, gasTimelineReducer, INITIAL_GAS_TIMELINE, mapGasAssets, PRIMARY_GAS_WINDOWS, UNAVAILABLE_GAS_CAPABILITIES, zoomGasViewport } from "../features/gas/cockpitModel";
import { gasEngineeringPath, parseGasEngineeringRoute, parseGasHash, PROJECT_GAS_MAP } from "../shared/routing/gasRoutes";
import { parseRoute, routeForEdition } from "../hooks/useHashRoute";
import { GasCockpitProvider, useGasCockpit } from "../features/gas/GasCockpitProvider";
import { GasAssistantPanel, GasRiskPanel } from "../features/gas/GasInspector";
import { currentGasRead, type GasReadState } from "../features/gas/useGasRead";
import type { GasAsset } from "../features/gas/contracts";
const value = fixtureJson as unknown as GasCaseResponse;

describe("original cockpit registry and legal readiness", () => {
  it("preserves all eleven windows without spreading them all across the default screen", () => {
    expect(Object.keys(GAS_WINDOWS)).toHaveLength(11);
    expect(PRIMARY_GAS_WINDOWS).toEqual(["scenario", "topology", "selected", "timeline", "tasks"]);
    expect(new Set(PRIMARY_GAS_WINDOWS).size).toBe(5);
  });
  it("does not invent risk, GIS or model readiness", () => {
    expect(UNAVAILABLE_GAS_CAPABILITIES.risk.status).toBe("unavailable");
    expect(UNAVAILABLE_GAS_CAPABILITIES.gis.status).toBe("unavailable");
    expect(UNAVAILABLE_GAS_CAPABILITIES.agent).toMatchObject({ model: "unavailable", interaction: "context_only", validation: "pending_validation" });
    expect(UNAVAILABLE_GAS_CAPABILITIES.risk).not.toHaveProperty("score");
    expect(UNAVAILABLE_GAS_CAPABILITIES.gis).not.toHaveProperty("longitude");
  });
  it("context preview is explicitly not a running model task", () => {
    const html = renderToStaticMarkup(createElement(GasCockpitProvider, { onNavigate() {}, children: createElement(GasAssistantPanel) }));
    expect(html).toContain("尚无本次服务端结果"); expect(html).toContain("local_rule_not_llm");
    expect(html).toContain("未接入模型"); expect(html).toContain("disabled");
    expect(html).not.toContain("正在分析");
  });
  it("risk design has a truthful empty table instead of demo scores or a city ranking", () => {
    const html = renderToStaticMarkup(createElement(GasCockpitProvider, { onNavigate() {}, children: createElement(GasRiskPanel) }));
    expect(html).toContain("unavailable"); expect(html).toContain("暂无经授权结果"); expect(html).not.toContain("风险分：");
  });
});

describe("actual case topology identity and schematic focus", () => {
  const pipeIds = value.edges.filter((edge) => value.isolation_plan.disabled_edge_ids.includes(edge.edge_id)).map((edge) => edge.pipe_id);
  const valveIds = value.isolation_plan.closed_valve_ids;
  it("resolves seven pipes and two actual valve assets without guessing prefixes", () => {
    expect(pipeIds).toHaveLength(7); expect(valveIds).toHaveLength(2);
    const mapping = mapGasAssets(value, [...pipeIds, ...valveIds]);
    expect(mapping.edgeIds).toHaveLength(7); expect(mapping.nodeIds).toHaveLength(2); expect(mapping.unmappedIds).toEqual([]);
    expect(mapping.nodeIds).toEqual(valveIds.map((id) => value.valves.find((valve) => valve.valve_id === id)!.node_id));
  });
  it("does not collapse the valve asset and its topology record", () => {
    const valve = value.valves[0];
    const asset = mapGasAssets(value, [valve.valve_id]);
    const node = mapGasAssets(value, [valve.node_id]);
    expect(asset.nodeIds).toEqual([valve.node_id]); expect(node.nodeIds).toEqual([valve.node_id]);
    expect(valve.valve_id).not.toBe(valve.node_id);
  });
  it("unmapped, missing and same-prefix fake IDs never produce coordinates", () => {
    const result = mapGasAssets(value, ["syn-gas-pipe-made-up", "syn-gas-valve-made-up", value.edges[0].edge_id]);
    expect(result.edgeIds).toEqual([]); expect(result.nodeIds).toEqual([]); expect(result.unmappedIds).toHaveLength(3);
    expect(mapGasAssets(null, ["id"]).unmappedIds).toEqual(["id"]);
    expect(result).not.toHaveProperty("latitude"); expect(result).not.toHaveProperty("longitude");
  });
  it("consumer selection uses the provided node relation rather than fabricated GIS", () => {
    const item = value.consumers[0];
    expect(mapGasAssets(value, [item.consumer_id]).nodeIds).toEqual([item.node_id]);
  });
  it("fit transform is finite and bounded and empty data restores the original view", () => {
    const mapping = mapGasAssets(value, [...pipeIds, ...valveIds]);
    const view = fitGasMapping(value, mapping);
    expect(view.zoom).toBeGreaterThanOrEqual(.65); expect(view.zoom).toBeLessThanOrEqual(2.4);
    expect(Number.isFinite(view.x) && Number.isFinite(view.y)).toBe(true);
    expect(fitGasMapping(value, { edgeIds: [], nodeIds: [], unmappedIds: [] })).toEqual(DEFAULT_VIEWPORT);
  });
  it("zoom clamps without moving the point under the pointer", () => {
    const anchor = { x: 480, y: 210 }; const old = { zoom: 1, x: 20, y: -10 };
    const next = zoomGasViewport(old, 1.15, anchor);
    expect((anchor.x - old.x) / old.zoom).toBeCloseTo((anchor.x - next.x) / next.zoom);
    expect((anchor.y - old.y) / old.zoom).toBeCloseTo((anchor.y - next.y) / next.zoom);
    expect(zoomGasViewport(old, 100).zoom).toBe(2.4); expect(zoomGasViewport(old, .001).zoom).toBe(.65);
  });
});

describe("timeline pure lifecycle", () => {
  it("runs ten complete reset/play/end cycles without adding events or running past the end", () => {
    let state = INITIAL_GAS_TIMELINE;
    for (let round = 0; round < 10; round++) {
      state = gasTimelineReducer(state, { type: "reset" });
      state = gasTimelineReducer(state, { type: "play", count: 3, reduced: false });
      expect(state).toEqual({ index: 0, playing: true });
      state = gasTimelineReducer(state, { type: "tick", count: 3 }); expect(state.index).toBe(1);
      state = gasTimelineReducer(state, { type: "tick", count: 3 }); expect(state).toEqual({ index: 2, playing: false });
      state = gasTimelineReducer(state, { type: "tick", count: 3 }); expect(state.index).toBe(2);
    }
  });
  it("reduced motion prevents play, while manual navigation stays available", () => {
    expect(gasTimelineReducer(INITIAL_GAS_TIMELINE, { type: "play", count: 3, reduced: true }).playing).toBe(false);
    expect(gasTimelineReducer(INITIAL_GAS_TIMELINE, { type: "select", count: 3, index: 2 })).toEqual({ index: 2, playing: false });
  });
  it("empty/single-event timelines cannot pretend to play", () => {
    for (const count of [0, 1]) expect(gasTimelineReducer(INITIAL_GAS_TIMELINE, { type: "play", count, reduced: false }).playing).toBe(false);
  });
  it("pause/configure/select clamp state without mutating the previous value", () => {
    const state = { index: 2, playing: true };
    expect(gasTimelineReducer(state, { type: "pause" })).toEqual({ index: 2, playing: false });
    expect(gasTimelineReducer(state, { type: "configure", count: 1 })).toEqual({ index: 0, playing: false });
    expect(gasTimelineReducer(state, { type: "select", count: 3, index: -3 })).toEqual({ index: 0, playing: false });
    expect(state).toEqual({ index: 2, playing: true });
  });
});

describe("one canonical gas route family", () => {
  it("parses only the explicit overview path and preserves the build-specific landing page", () => {
    expect(parseRoute("#/overview").view).toBe("overview");
    expect(parseRoute("#/overview/invalid").view).toBe("gas");
    expect(routeForEdition("#/overview", "project").view).toBe("gas");
    expect(routeForEdition("#/overview", "competition").view).toBe("overview");
    expect(routeForEdition("", "competition").view).toBe("overview");
  });
  it.each(["", "#/gas", "#/gas-control-room", "#/gas-engineering"])("uses gas for the root/legacy entry %s", (hash) => {
    expect(parseRoute(hash).view).toBe("gas");
  });
  it.each(["#/gas-engineering?tab=assets&page=5", "#/gas-control-room/assets?page=5", "#/gas/assets?page=5"])("canonicalizes the asset directory without another application %s", (hash) => {
    expect(gasEngineeringPath(parseRoute(hash))).toBe("/gas/assets?page=5");
  });
  it("preserves mode, layout, GIS choice and detail identities through URL roundtrip", () => {
    const expected = { gasSection: "events" as const, gasEventId: "事件/1", gasAssetId: "资产?2", assetPage: 5, assetType: "valve" as const,
      gasMode: "emergency" as const, gasLayout: "analysis" as const, gasMap: "gis" as const };
    expect(parseRoute("#" + gasEngineeringPath(expected))).toMatchObject(expected);
  });
  it.each(["analysis", "assistant"] as const)("keeps selected context under the %s subroute", (gasSection) => {
    const route = parseRoute("#" + gasEngineeringPath({ gasSection, gasEventId: "e/1", gasAssetId: "a/2", assetPage: 1 }));
    expect(route).toMatchObject({ view: "gas", gasSection, gasEventId: "e/1", gasAssetId: "a/2" });
  });
  it("malformed identities cannot produce guessed event/asset lookups", () => {
    const route = parseRoute("#/gas/events/%E0%A4%A/assets/asset");
    expect(route.gasRouteError).toBeTruthy(); expect(route.gasEventId).toBeUndefined(); expect(route.gasAssetId).toBeUndefined();
  });
  it("retains the catalogue-open choice when a cross-page asset is selected", () => {
    const route = parseRoute("#" + gasEngineeringPath({ gasSection: "assets", gasAssetId: "id", assetPage: 5, gasCatalogueOpen: true }));
    expect(route).toMatchObject({ assetPage: 5, gasAssetId: "id", gasCatalogueOpen: true });
  });
  it("case identity is part of the async guard so an old case response never appears as new", () => {
    const state: GasReadState<GasAsset> = { key: "old-case:asset:event:id", status: "succeeded", data: {} as GasAsset, error: null };
    expect(currentGasRead(state, "new-case:asset:event:id").data).toBeNull();
  });
});


describe.each(["gis", "topology"] as const)("locate on %s", (view) => {
  const event = "syn-gas-event-isolation";
  const asset = value.isolation_plan.closed_valve_ids[0];
  const independent = value.consumers[0].consumer_id;
  type Locate = ReturnType<typeof useGasCockpit>["locateAssetsOnMap"];

  function locateFrom(segments: string[], target: Parameters<Locate>[0]) {
    let locate!: Locate;
    let path = "";
    function Capture() {
      locate = useGasCockpit().locateAssetsOnMap;
      return null;
    }
    renderToStaticMarkup(createElement(GasCockpitProvider, {
      route: parseGasEngineeringRoute(segments, new URLSearchParams()),
      onNavigate(next) { path = next; },
      children: createElement(Capture),
    }));
    locate({ ...target, view });
    return parseGasHash("#" + path, PROJECT_GAS_MAP);
  }

  it("locates an independent asset after viewing an unrelated event", () => {
    const next = locateFrom(["events", event], { assetId: independent });
    expect(next.gasEventId).toBeUndefined();
    expect(next).toMatchObject({ gasSection: "assets", gasAssetId: independent, gasMap: view });
  });

  it("locates an event without retaining the previously viewed asset", () => {
    const next = locateFrom(["assets", independent], { eventId: event });
    expect(next.gasAssetId).toBeUndefined();
    expect(next).toMatchObject({ gasSection: "events", gasEventId: event, gasMap: view });
  });

  it("retains an explicitly supplied event and its related asset", () => {
    const next = locateFrom(["assets", independent], { eventId: event, assetId: asset });
    expect(next).toMatchObject({ gasEventId: event, gasAssetId: asset, gasSection: "events", gasMap: view });
  });

  it("keeps the current event and asset when only changing map view", () => {
    const next = locateFrom(["events", event, "assets", asset], {});
    expect(next).toMatchObject({ gasEventId: event, gasAssetId: asset, gasMap: view });
  });
});

describe("event filters are part of the same restorable route", () => {
  it("persists actual event kind and state together with selection", () => {
    const input = { gasEventId: "event", assetPage: 1, gasSection: "events" as const, gasEventKind: "given_isolation" as const, gasEventState: "synthetic_given" as const };
    expect(parseRoute("#" + gasEngineeringPath(input))).toMatchObject(input);
  });
  it("invalid filter query values do not invent event types or states", () => {
    const route = parseRoute("#/gas?eventKind=real-critical&eventState=predicted");
    expect(route.gasEventKind).toBeUndefined(); expect(route.gasEventState).toBeUndefined();
  });
});
