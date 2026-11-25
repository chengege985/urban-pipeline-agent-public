import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import fixtureJson from "./fixtures/gasCaseResponse.json";
import type { GasCaseResponse } from "../features/gas/types";
import type { GasAsset, GasCollection, GasDistrict, GasEvent } from "../features/gas/contracts";
import {
  CHONGQING_DISTRICTS,
  chongqingDistrictSvgViewport,
  findChongqingDistrict,
  matchChongqingDistrictInText,
  projectChongqingCoord,
  summarizeDistrictReadiness,
} from "../features/gas/chongqingDistricts";
import {
  filterAuthorizedAssetFeatures,
  filterAuthorizedEventFeatures,
  normalizeControlledServiceHost,
  readControlledAmapConfig,
  toGcj02Coordinate,
} from "../features/gas/amapLoader";
import { BrowserGasTransport, GasEngineeringApi, gasEventQueryPath } from "../features/gas/engineeringApi";
import { parseGasAssistantQuery, probeGasAgentService } from "../features/gas/assistantModel";
import {
  eventPriorityScore,
  sortEventsByPriority,
  summarizeCuvReadiness,
  summarizeRiskReadiness,
  projectGasCapabilities,
} from "../features/gas/cockpitModel";
import { parseGasEngineeringRoute, gasEngineeringPath } from "../shared/routing/gasRoutes";
import { GasCockpitProvider } from "../features/gas/GasCockpitProvider";
import { GasGisView } from "../features/gas/GasGisView";
import { GasAssistantPanel, GasRiskPanel } from "../features/gas/GasInspector";
import { GasImpactPanel } from "../features/gas/GasPanels";

const fixture = fixtureJson as unknown as GasCaseResponse;

const makeSampleEvent = (overrides: Partial<GasEvent> = {}): GasEvent => ({
  event_id: "syn-gas-event-default",
  event_kind: "synthetic_observation",
  title: "合成观测事件",
  state: "synthetic_given",
  severity: "medium",
  data_mode: "synthetic",
  source_status: "synthetic_fixture",
  validation_status: "not_business_validated",
  evidence_refs: ["case.json#sha256"],
  limitation: "synthetic fixture only",
  ...overrides,
});

const emptyCollection = <T,>(status: GasCollection<T>["status"] = "unavailable"): GasCollection<T> => ({
  status,
  data_mode: status === "unavailable" ? "unavailable" : "synthetic",
  source_status: status === "unavailable" ? "unavailable" : "synthetic_fixture",
  validation_status: "not_business_validated",
  items: [],
  total: 0,
  limitations: [],
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Chongqing 37 administrative districts & readiness summary", () => {
  it("contains the current 37 Chongqing districts with adcodes that round-trip via URL route", () => {
    expect(CHONGQING_DISTRICTS).toHaveLength(37);
    expect(new Set(CHONGQING_DISTRICTS.map((d) => d.district_id)).size).toBe(37);
    expect(new Set(CHONGQING_DISTRICTS.map((d) => d.name)).size).toBe(37);
    expect(findChongqingDistrict("500157")?.name).toBe("两江新区");
    expect(findChongqingDistrict("500105")).toBeNull();
    expect(findChongqingDistrict("500112")).toBeNull();

    for (const district of CHONGQING_DISTRICTS) {
      const path = gasEngineeringPath({ gasSection: "events", gasMap: "gis", gasDistrictId: district.district_id });
      const [, search = ""] = path.split("?");
      const parsed = parseGasEngineeringRoute([], new URLSearchParams(search));
      expect(parsed.gasMap).toBe("gis");
      expect(parsed.gasDistrictId).toBe(district.district_id);

      const projected = projectChongqingCoord(district.refLon, district.refLat);
      expect(Number.isFinite(projected.x) && Number.isFinite(projected.y)).toBe(true);
      expect(projected.x).toBeGreaterThan(0);
      expect(projected.x).toBeLessThan(1000);
      expect(projected.y).toBeGreaterThan(0);
      expect(projected.y).toBeLessThan(640);
    }
  });

  it("matches districts cleanly by code, full name, or short name and calculates bounded viewport", () => {
    expect(findChongqingDistrict("500103")?.name).toBe("渝中区");
    expect(findChongqingDistrict("渝中区")?.district_id).toBe("500103");
    expect(matchChongqingDistrictInText("定位到万州检查主干管状态")?.district_id).toBe("500101");
    expect(matchChongqingDistrictInText("查看秀山")?.district_id).toBe("500241");
    expect(matchChongqingDistrictInText("查看无关文本")).toBeNull();

    const coreView = chongqingDistrictSvgViewport(findChongqingDistrict("500103"));
    const outerView = chongqingDistrictSvgViewport(findChongqingDistrict("500101"));
    expect(coreView.zoom).toBe(2.8);
    expect(outerView.zoom).toBe(1.9);
    expect(chongqingDistrictSvgViewport(null)).toEqual({ zoom: 1, x: 0, y: 0 });
  });

  it("preserves backend unavailable counts without fabricating district data, and reflects populated district records", () => {
    const unavailable = summarizeDistrictReadiness({
      selectedDistrictId: "500103",
      districts: emptyCollection<GasDistrict>("unavailable"),
      districtsStatus: "succeeded",
      events: { ...emptyCollection<GasEvent>("partial"), items: [makeSampleEvent()], total: 1 },
      sampleAssets: [],
      assetTotal: 204,
      caseView: fixture,
    });
    expect(unavailable.backendStatus).toBe("unavailable");
    expect(unavailable.backendTotal).toBe(0);
    expect(unavailable.mappedEventCount).toBe(0);
    expect(unavailable.unmappedEventCount).toBe(1);
    expect(unavailable.selectedDistrictEventCount).toBeNull();
    expect(unavailable.limitations.length).toBeGreaterThan(0);

    const populated = summarizeDistrictReadiness({
      selectedDistrictId: "500103",
      districts: {
        status: "succeeded",
        data_mode: "real_authorized",
        source_status: "authorized",
        validation_status: "business_validated",
        items: [{
          district_id: "500103",
          name: "渝中区",
          geometry_available: true,
          limitation: "authorized admin geometry",
          data_mode: "real_authorized",
          source_status: "authorized",
          validation_status: "business_validated",
        }],
        total: 1,
        limitations: [],
      },
      districtsStatus: "succeeded",
      events: {
        ...emptyCollection<GasEvent>("partial"),
        items: [makeSampleEvent({ district_id: "500103", state: "open" })],
        total: 1,
      },
      sampleAssets: [],
      assetTotal: 204,
      caseView: fixture,
    });
    expect(populated.backendTotal).toBe(1);
    expect(populated.selectedBackendRecord?.geometry_available).toBe(true);
    expect(populated.selectedDistrictEventCount).toBe(1);
    expect(populated.selectedDistrictNonClosedEventCount).toBe(1);
  });
});

describe("Controlled AMap config, coordinate transform & strict authorization gate", () => {
  it("stays unconfigured without valid controlled key and never accepts synthetic coordinates", () => {
    expect(readControlledAmapConfig(null, {})).toMatchObject({ configured: false, key: null, source: "unconfigured" });
    expect(readControlledAmapConfig({ key: "bad-key" }, {})).toMatchObject({ configured: false, key: null });
    const controlled = readControlledAmapConfig({ key: "a1b2c3d4e5f6g7h8i9j0" }, {});
    expect(controlled).toMatchObject({
      configured: true,
      key: "a1b2c3d4e5f6g7h8i9j0",
      source: "runtime",
    });
    expect(controlled).not.toHaveProperty("securityJsCode");

    const wgs = toGcj02Coordinate({ longitude: 106.569, latitude: 29.553, coordinate_system: "wgs84" });
    expect(wgs.lon).not.toBe(106.569);
    expect(wgs.lat).not.toBe(29.553);
    expect(Math.abs(wgs.lon - 106.569)).toBeLessThan(0.02);

    const alreadyGcj = toGcj02Coordinate({ longitude: 106.569, latitude: 29.553, coordinate_system: "gcj02" });
    expect(alreadyGcj).toEqual({ lon: 106.569, lat: 29.553 });

    const assets: GasAsset[] = [
      {
        asset_id: "fake-syn",
        asset_type: "pipe",
        name: "synthetic-should-hide",
        data_mode: "synthetic",
        source_status: "synthetic_fixture",
        validation_status: "not_business_validated",
        location_status: "authorized",
        location: { longitude: 106.55, latitude: 29.56, coordinate_system: "wgs84" },
      },
      {
        asset_id: "unauth-real",
        asset_type: "valve",
        name: "missing-auth",
        data_mode: "real_authorized",
        source_status: "authorized",
        validation_status: "business_validated",
        location_status: "not_available",
        location: { longitude: 106.55, latitude: 29.56, coordinate_system: "wgs84" },
      },
      {
        asset_id: "valid-real",
        asset_type: "valve",
        name: "authorized-valve",
        data_mode: "real_authorized",
        source_status: "authorized",
        validation_status: "business_validated",
        location_status: "authorized",
        location: { longitude: 106.569, latitude: 29.553, coordinate_system: "wgs84" },
      },
    ];
    const filteredAssets = filterAuthorizedAssetFeatures(assets);
    expect(filteredAssets.map((f) => f.asset.asset_id)).toEqual(["valid-real"]);

    const events: GasEvent[] = [
      makeSampleEvent({ event_id: "ev-syn", location_status: "authorized", location: { longitude: 106.5, latitude: 29.5, coordinate_system: "gcj02" } }),
      makeSampleEvent({
        event_id: "ev-auth",
        data_mode: "real_authorized",
        source_status: "authorized",
        location_status: "authorized",
        location: { longitude: 106.57, latitude: 29.55, coordinate_system: "gcj02" },
      }),
    ];
    expect(filterAuthorizedEventFeatures(events).map((f) => f.event.event_id)).toEqual(["ev-auth"]);
  });
});

describe("Backend capabilities projection", () => {
  it("uses the authorized capabilities response instead of a permanent unavailable constant", () => {
    const projected = projectGasCapabilities({
      data_mode: "synthetic",
      dataset_id: "syn-gas-network-html54-0.1.0",
      validation_status: "not_business_validated",
      items: [
        {
          capability_id: "graph_connectivity",
          version: "0.1.0",
          status: "ready_synthetic",
          scope: "synthetic topology",
          validation_status: "synthetic_validated",
          limitations: ["graph only"],
        },
        {
          capability_id: "pir",
          version: "unavailable",
          status: "unsupported",
          scope: "risk",
          validation_status: "unsupported",
          limitations: ["no approved model"],
        },
      ],
    });
    expect(projected.risk.status).toBe("unavailable");
    expect(projected.risk.reason).toContain("no approved model");
    expect(projected.gis.reason).toContain("syn-gas-network-html54-0.1.0");
  });
});

describe("Risk priority sorting, CUV readiness boundaries & Gas Agent parser/probe", () => {
  it("sorts open critical/high events before closed observations and flags missing risk/CUV models", () => {
    const events: GasEvent[] = [
      makeSampleEvent({ event_id: "e-closed", severity: "low", state: "closed", event_kind: "synthetic_observation" }),
      makeSampleEvent({ event_id: "e-fault", severity: "high", state: "open", event_kind: "given_fault" }),
      makeSampleEvent({ event_id: "e-crit", severity: "critical", state: "open", event_kind: "given_isolation" }),
    ];
    const sorted = sortEventsByPriority(events);
    expect(sorted.map((e) => e.event_id)).toEqual(["e-crit", "e-fault", "e-closed"]);
    expect(eventPriorityScore(sorted[0])).toBeGreaterThan(eventPriorityScore(sorted[1]));

    const risk = summarizeRiskReadiness(fixture, { ...emptyCollection<GasEvent>("partial"), items: events, total: 3 });
    expect(risk.status).toBe("unavailable");
    expect(risk.availableCount).toBe(0);
    expect(risk.missingIndicators).toEqual(["G01", "G02", "G04"]);
    expect(risk.criticalOrHighEventCount).toBe(2);

    const cuv = summarizeCuvReadiness(fixture, fixture.timeline_events[1] ?? null);
    expect(cuv.unreachableNodes).toBe(fixture.timeline_events[1]?.current_unreachable_node_ids.length);
    expect(cuv.boundaries).toHaveLength(5);
    expect(cuv.boundaries.every((b) => b.status === "unsupported" || b.status === "unsupported_pending_business_definition")).toBe(true);
  });

  it("classifies local gas assistant queries deterministically without calling LLM or fabricating GIS coordinates", () => {
    const sampleEvents: GasCollection<GasEvent> = {
      ...emptyCollection<GasEvent>("partial"),
      items: [makeSampleEvent({ event_id: "syn-fault-1", event_kind: "given_fault", asset_ids: [fixture.pipes[0].pipe_id] })],
      total: 1,
    };

    const districtParse = parseGasAssistantQuery({
      query: "定位到渝中区查看地图状态",
      caseView: fixture,
      events: sampleEvents,
      districts: null,
      selectedEvent: null,
      selectedAsset: null,
    });
    expect(districtParse.parserSource).toBe("local_rule_parser_not_llm");
    expect(districtParse.intent).toBe("locate_district");
    expect(districtParse.locateCommand.targetView).toBe("gis");
    expect(districtParse.locateCommand.districtId).toBe("500103");
    expect(districtParse.locateCommand.coordinateAuthorized).toBe(false);

    const assetParse = parseGasAssistantQuery({
      query: `查看 ${fixture.valves[0].valve_id} 详情`,
      caseView: fixture,
      events: sampleEvents,
      districts: null,
      selectedEvent: null,
      selectedAsset: null,
    });
    expect(assetParse.intent).toBe("inspect_asset");
    expect(assetParse.clickableAssetIds).toEqual([fixture.valves[0].valve_id]);

    const cuvParse = parseGasAssistantQuery({
      query: "查看 CUV 时间线关阀后影响用户和PIR热点",
      caseView: fixture,
      events: sampleEvents,
      districts: null,
      selectedEvent: null,
      selectedAsset: null,
    });
    expect(cuvParse.intent).toBe("query_cuv_timeline");
    expect(cuvParse.params.mode_hint).toBe("emergency");

    const riskParse = parseGasAssistantQuery({
      query: "检查 G15 风险排行和就绪度",
      caseView: fixture,
      events: sampleEvents,
      districts: null,
      selectedEvent: null,
      selectedAsset: null,
    });
    expect(riskParse.intent).toBe("query_risk_readiness");
    expect(riskParse.params.mode_hint).toBe("risk");
  });

  it("maps manual probe responses cleanly across 401, 403, 503, enabled=false, and TimeoutError", async () => {
    vi.stubGlobal("fetch", async () => new Response("{}", { status: 401 }));
    expect((await probeGasAgentService("http://127.0.0.1:1")).status).toBe("unauthenticated");

    vi.stubGlobal("fetch", async () => new Response("{}", { status: 403 }));
    expect((await probeGasAgentService("http://127.0.0.1:1")).status).toBe("forbidden");

    vi.stubGlobal("fetch", async () => new Response("{}", { status: 503 }));
    expect((await probeGasAgentService("http://127.0.0.1:1")).status).toBe("unavailable");

    vi.stubGlobal("fetch", async (url: string | URL) => {
      const target = String(url);
      if (target.endsWith("/health")) {
        return new Response(JSON.stringify({ enabled: false }), { status: 200 });
      }
      return new Response("{}", { status: 503 });
    });
    const disabled = await probeGasAgentService("http://127.0.0.1:1");
    expect(disabled.status).toBe("unavailable");
    expect(disabled.httpStatus).toBeUndefined();
    expect(disabled.detail).toContain("合同");

    vi.stubGlobal("fetch", async () => {
      throw new DOMException("timed out", "TimeoutError");
    });
    expect((await probeGasAgentService("http://127.0.0.1:1")).status).toBe("timeout");
  });
});

describe("V1.1 SSR cockpit views preserve unavailable boundaries", () => {
  it("renders Chongqing GIS fallback view with zero AMap iframe/container and clear unavailable bar", () => {
    const html = renderToStaticMarkup(createElement(GasCockpitProvider, {
      route: { assetPage: 1, gasSection: "events", gasMode: "overview", gasLayout: "command", gasMap: "gis", gasDistrictId: "500103" },
      onNavigate() {},
      children: createElement(GasGisView),
    }));
    expect(html).toContain('data-testid="gas-gis-unavailable"');
    expect(html).toContain("重庆全域行政 GIS 视图");
    expect(html).toContain("渝中区");
    expect(html).toContain("返回GAS-M0合成拓扑");
    expect(html).not.toContain("amap-container");
    expect(html).not.toContain("<iframe");
  });

  it("renders V1.1 Risk, CUV impact & Assistant panels with explicit not-ready boundaries", () => {
    const html = renderToStaticMarkup(createElement(GasCockpitProvider, {
      onNavigate() {},
      children: createElement("div", null,
        createElement(GasRiskPanel),
        createElement(GasImpactPanel),
        createElement(GasAssistantPanel),
      ),
    }));
    expect(html).toContain('data-testid="gas-risk-readiness"');
    expect(html).toContain('data-testid="gas-official-ranking-slot"');
    expect(html).toContain("暂无经授权结果");
    expect(html).toContain('data-testid="gas-cuv-boundaries"');
    expect(html).toContain("水力/泄漏 PIR");
    expect(html).toContain("尚无本次服务端结果");
    expect(html).toContain("local_rule_not_llm");
    expect(html).toContain("检测燃气服务");
    expect(html).toContain("发送查询");
  });
});

describe("V1.1 Optimization checks: serviceHost proxy, real /capabilities CUV & 422 parity", () => {
  it("validates controlled serviceHost proxy safely while keeping Security JS Code absent", () => {
    expect(normalizeControlledServiceHost("/_AMapService")).toBe("/_AMapService");
    expect(normalizeControlledServiceHost("https://proxy.example.local/amap/")).toBe("https://proxy.example.local/amap");
    expect(normalizeControlledServiceHost("//evil.example/amap")).toBeNull();
    expect(normalizeControlledServiceHost("javascript:alert(1)")).toBeNull();
    expect(normalizeControlledServiceHost("https://user:pass@proxy.example/amap")).toBeNull();

    const cfg = readControlledAmapConfig(
      { key: "a1b2c3d4e5f6g7h8i9j0", serviceHost: "/_AMapService" },
      { VITE_AMAP_SECURITY_JS_CODE: "must-never-leak" },
    );
    expect(cfg).toMatchObject({
      configured: true,
      key: "a1b2c3d4e5f6g7h8i9j0",
      serviceHost: "/_AMapService",
      source: "runtime",
    });
    expect(cfg).not.toHaveProperty("securityJsCode");
  });

  it("projects capability errors and real CUV boundary metadata without fabricating ready state", () => {
    const failedCap = projectGasCapabilities(null, {
      status: "failed",
      error: { message: "未认证或会话已失效（401）", status: 401 },
    });
    expect(failedCap.risk.status).toBe("unavailable");
    expect(failedCap.risk.reason).toContain("401");
    expect(failedCap.agent.model).toBe("unavailable");

    const loadedCuv = summarizeCuvReadiness(fixture, fixture.timeline_events[1] ?? null, {
      status: "succeeded",
      error: null,
      data: {
        data_mode: "synthetic",
        dataset_id: "syn-gas-network-html54-0.1.0",
        validation_status: "not_business_validated",
        items: [
          {
            capability_id: "graph_connectivity",
            version: "0.1.0",
            status: "ready_synthetic",
            scope: "synthetic topology",
            validation_status: "synthetic_validated",
            limitations: ["graph connectivity only"],
          },
          {
            capability_id: "isolation_candidate",
            version: "0.1.0",
            status: "ready_synthetic",
            scope: "synthetic isolation",
            validation_status: "synthetic_validated",
            limitations: ["given closed valves only"],
          },
          {
            capability_id: "pir",
            version: "unavailable",
            status: "unsupported",
            scope: "hydraulic",
            validation_status: "unsupported",
            limitations: ["no real hydraulic PIR"],
          },
          {
            capability_id: "accumulation",
            version: "unavailable",
            status: "unsupported",
            scope: "cascade",
            validation_status: "unsupported",
            limitations: ["no cascade forecast"],
          },
        ],
      },
    });
    expect(loadedCuv.capabilities).toMatchObject({
      state: "loaded",
      datasetId: "syn-gas-network-html54-0.1.0",
      graphConnectivityStatus: "ready_synthetic",
      isolationCandidateStatus: "ready_synthetic",
    });
    expect(loadedCuv.boundaries.find((b) => b.key === "pir")?.reason).toContain("no real hydraulic PIR");
    expect(loadedCuv.boundaries.find((b) => b.key === "accumulation")?.reason).toContain("no cascade forecast");

    const erroredCuv = summarizeCuvReadiness(fixture, fixture.timeline_events[1] ?? null, {
      status: "failed",
      error: { message: "服务不可用（503）", status: 503 },
      data: null,
    });
    expect(erroredCuv.capabilities.state).toBe("error");
    expect(erroredCuv.boundaries.find((b) => b.key === "pir")?.status).toBe("unknown");
    expect(erroredCuv.boundaries.find((b) => b.key === "pir")?.reason).toContain("503");
  });

  it("extracts safe 422 validation details, validates event query params, and sends same-origin credentials with baseUrl", async () => {
    const api422 = new GasEngineeringApi({
      async send() {
        return {
          status: 422,
          body: { detail: [{ field: ["bbox"], reason: "outside Chongqing bounds", type: "value_error" }] },
        };
      },
    });
    await expect(api422.events()).rejects.toMatchObject({
      code: "http",
      status: 422,
      message: expect.stringContaining("bbox: outside Chongqing bounds"),
    });

    const unsafe422 = new GasEngineeringApi({
      async send() {
        return { status: 422, body: { detail: "bearer secret-token leaked" } };
      },
    });
    await expect(unsafe422.events()).rejects.toMatchObject({
      code: "http",
      status: 422,
      message: "请求参数未通过校验（422），请检查筛选条件或编号。",
    });

    expect(gasEventQueryPath({ districtId: "500103", status: "open", limit: 20 })).toBe(
      "/api/v1/gas/engineering/events?district_id=500103&status=open&limit=20",
    );
    expect(() => gasEventQueryPath({ limit: 500 })).toThrow(RangeError);
    expect(() => gasEventQueryPath({ districtId: "   " })).toThrow(RangeError);

    const fetcher = vi.fn().mockResolvedValue({ status: 200, json: async () => ({ ok: true }) });
    vi.stubGlobal("fetch", fetcher);
    await new BrowserGasTransport("https://gas.local/").send({ method: "GET", path: "/api/v1/gas/engineering/capabilities" });
    expect(fetcher).toHaveBeenCalledWith(
      "https://gas.local/api/v1/gas/engineering/capabilities",
      expect.objectContaining({ credentials: "same-origin", method: "GET" }),
    );
  });

  it("does not leak internal VITE_AMAP_KEY identifier into offline GIS view markup", () => {
    const html = renderToStaticMarkup(createElement(GasCockpitProvider, {
      route: { assetPage: 1, gasSection: "events", gasMode: "overview", gasLayout: "command", gasMap: "gis" },
      onNavigate() {},
      children: createElement(GasGisView),
    }));
    expect(html).toContain("高德运行配置待提供");
    expect(html).not.toContain("VITE_AMAP_KEY");
  });
});
