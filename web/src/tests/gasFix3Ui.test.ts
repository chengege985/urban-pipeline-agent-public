import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import fixtureJson from "./fixtures/gasCaseResponse.json";
import { describe, expect, it } from "vitest";
import { parseRoute, isGasRoute } from "../hooks/useHashRoute";
import { gasEngineeringPath, GAS_ASSET_PAGE_SIZE } from "../shared/routing/gasRoutes";
import { GasEngineeringApi, gasAssetPagePath, type GasTransport } from "../features/gas/engineeringApi";
import { GasRecordSummary, GasAssetDirectory } from "../features/gas/GasAssetDirectory";
import { gasRecordIdentity, indexGasRecords } from "../features/gas/assetRecords";
import { GasAssetDetails } from "../features/gas/GasDetails";
import { currentGasRead, type GasReadState } from "../features/gas/useGasRead";
import { TopBar } from "../components/TopBar";
import type { GasAsset, GasCollection } from "../features/gas/contracts";
import type { GasCaseResponse } from "../features/gas/types";

const fixture = fixtureJson as unknown as GasCaseResponse;
const index = indexGasRecords(fixture);
const makeAsset = (id: string, asset_type: GasAsset["asset_type"]): GasAsset => ({ asset_id: id, asset_type, name: id,
  data_mode: "synthetic", source_status: "synthetic_fixture", validation_status: "not_business_validated", status: "synthetic_reference" });
const collection = (items: GasAsset[], total = 204): GasCollection<GasAsset> => ({ status: "partial", data_mode: "synthetic", source_status: "synthetic_fixture", validation_status: "not_business_validated", items, total, limitations: [] });
const resource = (data: GasCollection<GasAsset> | null): GasReadState<GasCollection<GasAsset>> & { retry(): void } => ({ key: "page", status: data ? "succeeded" : "loading", data, error: null, retry() {} });

describe("GAS-M0 asset records are not topology subtypes", () => {
  it("keeps 73 pipes, 31 valve assets, 54 nodes and 46 consumers as 204 distinct records", () => {
    expect(index.size).toBe(204);
    const values = [...index.values()];
    expect(values.filter((item) => item.kind === "pipe_asset")).toHaveLength(73);
    expect(values.filter((item) => item.kind === "valve_asset")).toHaveLength(31);
    expect(values.filter((item) => item.kind === "topology_node")).toHaveLength(54);
    expect(values.filter((item) => item.kind === "topology_node" && item.nodeType === "valve")).toHaveLength(31);
    expect(values.filter((item) => item.kind === "consumer_record")).toHaveLength(46);
  });
  it("uses actual case membership, not matching names, raw valve type or ID prefixes", () => {
    const valve = fixture.valves[0];
    expect(gasRecordIdentity(index, valve.valve_id).kind).toBe("valve_asset");
    expect(gasRecordIdentity(index, valve.node_id).kind).toBe("topology_node");
    expect(gasRecordIdentity(index, "syn-gas-valve-invented").kind).toBe("unknown");
    expect(gasRecordIdentity(null, valve.valve_id).kind).toBe("unknown");
  });
  it("ambiguous cross-collection identity is unknown, never silently merged", () => {
    const value = structuredClone(fixture);
    value.valves[0].valve_id = value.nodes[0].node_id;
    expect(gasRecordIdentity(indexGasRecords(value), value.nodes[0].node_id).kind).toBe("unknown");
  });
  it("KPI reads confirmed counts, not the current 50-row page or raw valve total=62", () => {
    const html = renderToStaticMarkup(createElement(GasRecordSummary, { catalogue: fixture, total: 204 }));
    expect(html).toContain('data-testid="gas-valve-count">31<');
    expect(html).toContain('data-testid="gas-node-count">54<');
    expect(html).toContain('data-testid="gas-record-total">204<');
    expect(html).toContain("31 个阀门节点");
    expect(html).not.toContain(">62<");
  });
  it("unavailable statistics stay unknown instead of becoming zero", () => {
    const html = renderToStaticMarkup(createElement(GasRecordSummary, { catalogue: null, total: null }));
    expect(html.match(/>—</g)).toHaveLength(5);
    expect(html).not.toContain(">0<");
  });
  it("asset details label topology valve and valve asset separately while retaining raw type", () => {
    for (const id of [fixture.valves[0].valve_id, fixture.valves[0].node_id]) {
      const identity = gasRecordIdentity(index, id);
      const html = renderToStaticMarkup(createElement(GasAssetDetails, { asset: makeAsset(id, "valve"), identity }));
      expect(html).toContain(identity.kind === "valve_asset" ? "阀门资产" : "拓扑节点");
      expect(html).toContain("接口原始类型");
      expect(html).toContain("valve");
      if (identity.kind === "topology_node") expect(html).toContain("阀门节点 (valve)");
    }
  });
});

describe("bounded server pagination", () => {
  it("retains default 200 and supports bounded limit/offset/type without changing total", async () => {
    const calls: string[] = [];
    const api = new GasEngineeringApi({ async send(request) { calls.push(request.path); return { status: 200, body: collection([makeAsset("a", "valve")], 62) }; } });
    expect((await api.assets()).total).toBe(62);
    expect((await api.assets(undefined, { limit: 50, offset: 50, assetType: "valve" })).items).toHaveLength(1);
    expect(calls).toEqual(["/api/v1/gas/engineering/assets?limit=200&offset=0", "/api/v1/gas/engineering/assets?limit=50&offset=50&asset_type=valve"]);
    expect(GAS_ASSET_PAGE_SIZE).toBe(50);
  });
  it.each([{ limit: 0 }, { limit: 201 }, { limit: Infinity }, { limit: 2.5 }, { offset: -1 }, { offset: NaN }, { offset: 1.5 }])("rejects unbounded or malformed page query %#", (query) => {
    expect(() => gasAssetPagePath(query)).toThrow(RangeError);
  });
  it("last page exposes all four tail consumers with original total=204", () => {
    const items = fixture.consumers.slice(-4).map((item) => makeAsset(item.consumer_id, "consumer"));
    const html = renderToStaticMarkup(createElement(GasAssetDirectory, { route: { assetPage: 5, gasSection: "assets" }, resource: resource(collection(items)), index, allTotal: 204, onPage() {}, onType() {}, onOpen() {}, onLookup() {} }));
    expect(html).toContain("本页已加载 4 / 总记录 204");
    expect(html).toContain("201–204");
    expect(html.match(/aria-label="查看资产 /g)).toHaveLength(4);
    for (const item of items) expect(html).toContain(item.asset_id);
  });
  it("a raw valve filter preserves 62 records while separately identifying both origins", () => {
    const ids = fixture.valves[0];
    const html = renderToStaticMarkup(createElement(GasAssetDirectory, { route: { assetPage: 1, assetType: "valve", gasSection: "assets" }, resource: resource(collection([makeAsset(ids.valve_id,"valve"), makeAsset(ids.node_id,"valve")],62)), index, allTotal: 204, onPage() {}, onType() {}, onOpen() {}, onLookup() {} }));
    expect(html).toContain("总记录 62");
    expect(html).toContain('data-record-kind="valve_asset"');
    expect(html).toContain('data-record-kind="topology_node"');
    expect(html).toContain("不能把筛选总记录数当作阀门资产数");
  });
  it("query switches mask the previous page or filter immediately", () => {
    const old = { ...resource(collection([makeAsset("old", "pipe")])), key: "assets:pipe:1" };
    for (const key of ["assets:pipe:2", "assets:valve:1", "asset:standalone:new"]) {
      expect(currentGasRead(old, key).data).toBeNull();
      expect(currentGasRead(old, key).status).toBe("loading");
    }
  });
});

describe("gas routes and navigation state", () => {
  it.each([
    ["#/gas-engineering/events/event-1", "event-1", undefined],
    ["#/gas-engineering/assets/asset-1", undefined, "asset-1"],
    ["#/gas-engineering/events/event-1/assets/asset-1", "event-1", "asset-1"],
    ["#/gas/events/event-1/assets/asset-1", "event-1", "asset-1"],
  ])("parses detail and compatibility routes %s", (hash, gasEventId, gasAssetId) => {
    expect(parseRoute(hash)).toMatchObject({ view: "gas", assetPage: 1 });
    expect(parseRoute(hash).gasEventId).toBe(gasEventId);
    expect(parseRoute(hash).gasAssetId).toBe(gasAssetId);
  });
  it("round-trips Unicode and reserved characters exactly once while preserving page and filter", () => {
    const expected = { gasEventId: "事件/ ?#%", gasAssetId: "资产/ ?#%", assetPage: 5, assetType: "valve" as const, gasSection: "events" as const };
    const path = gasEngineeringPath(expected);
    const actual = parseRoute("#" + path);
    expect(actual).toMatchObject(expected);
    expect(path).toContain("%2F");
  });
  it("asset tab, page and filter survive refresh as URL state", () => {
    const route = parseRoute("#/gas-engineering?tab=assets&page=5&type=consumer");
    expect(route).toMatchObject({ gasSection: "assets", assetPage: 5, assetType: "consumer" });
    expect(gasEngineeringPath(route)).toBe("/gas/assets?page=5&type=consumer");
  });
  it.each(["0", "-1", "NaN", "1.5", "Infinity", "9999999999999999999999999999999"])("invalid page %s cannot create a bad request", (page) => {
    expect(parseRoute(`#/gas-engineering?tab=assets&page=${page}&type=not-valid`).assetPage).toBe(1);
    expect(parseRoute(`#/gas-engineering?type=not-valid`).assetType).toBeUndefined();
  });
  it.each(["#/gas-engineering/events/%E0%A4%A", "#/gas-engineering/unknown/id", "#/gas-engineering/events/id/extra"])("malformed route is explicit and carries no guessed identities %s", (hash) => {
    const route = parseRoute(hash);
    expect(route.gasRouteError).toContain("无法识别");
    expect(route.gasEventId).toBeUndefined();
    expect(route.gasAssetId).toBeUndefined();
  });
  it("both gas entries skip water bootstrap while legacy routes retain it", () => {
    for (const view of ["gas", "gas-engineering"] as const) expect(isGasRoute({ view })).toBe(true);
    for (const view of ["overview", "report", "pipe", "scenario", "assistant", "about"] as const) expect(isGasRoute({ view })).toBe(false);
    expect(parseRoute("#/report/run-1")).toEqual({ view: "report", sessionId: "run-1" });
    expect(parseRoute("#/scenario/sc-1?pipe=pipe-1")).toEqual({ view: "scenario", scenarioId: "sc-1", pipeId: "pipe-1" });
  });
  it("TopBar has the sole active gas entry for nested routes", () => {
    const html = renderToStaticMarkup(createElement(TopBar, { route: parseRoute("#/gas-engineering/events/e/assets/a"), mode: "demo", onNavigate() {} }));
    expect(html).toMatch(/aria-current="page"[^>]*>[\s\S]*?燃气中控/);
    expect(html).toContain("燃气中控");
    expect(html).toContain("燃气合成候选");
  });
});

describe("catalogue uses the confirmed case client and shared transport", () => {
  it("loads counts and membership through actual case path, never an internal fixture URL", async () => {
    const calls: string[] = [];
    const transport: GasTransport = { async send(request) { calls.push(request.path); return { status: 200, body: fixture }; } };
    const value = await new GasEngineeringApi(transport).catalogue();
    expect(value.counts.valves).toBe(31);
    expect(calls).toEqual(["/api/v1/gas/cases/syn-gas-case-K-001"]);
  });
  it.each([[401,"unauthenticated"],[403,"forbidden"],[503,"unavailable"]] as const)("preserves HTTP %i rejection", async (status, code) => {
    const api = new GasEngineeringApi({ async send() { return { status, body: { detail: "denied" } }; } });
    await expect(api.catalogue()).rejects.toMatchObject({ code, status });
  });
  it("invalid catalogue cannot generate invented statistics", async () => {
    const api = new GasEngineeringApi({ async send() { return { status: 200, body: { counts: { valves: 31 } } }; } });
    await expect(api.catalogue()).rejects.toMatchObject({ code: "invalid_response" });
  });
});
