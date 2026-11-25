import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { BrowserGasTransport, GasApiError, GasEngineeringApi, type GasTransport } from "../features/gas/engineeringApi";
import type { GasAsset, GasEvent } from "../features/gas/contracts";
import { GasAssetDetails, GasEventDetails, GasReadError } from "../features/gas/GasDetails";
import { gasLocationReason, hasAuthorizedGasLocation, relatedAssetIds } from "../features/gas/presentation";
import { currentGasRead, pendingGasRead, type GasReadState } from "../features/gas/useGasRead";

// Explicit synthetic unit fixtures. Final browser E2E uses the real HTTP service.
const asset: GasAsset = { asset_id: "syn-unit-pipe-1", asset_type: "pipe", name: "合成测试管段",
  data_mode: "synthetic", source_status: "synthetic_fixture", validation_status: "not_business_validated", status: "synthetic_reference",
  location: null, location_status: "not_available", evidence_refs: ["unit:synthetic-network"] };
const event: GasEvent = { event_id: "syn-unit-event", title: "合成单元事件", event_kind: "given_isolation",
  data_mode: "synthetic", source_status: "synthetic_fixture", validation_status: "not_business_validated", state: "synthetic_given", severity: "unknown",
  asset_ids: [asset.asset_id, "syn-unit-pipe-2"], location: null, location_status: "not_available",
  risk_status: "not_computed", physical_supply_status: "unknown", evidence_refs: ["unit:synthetic-case"], limitation: "仅用于单元回归。" };
const transport = (body: unknown, status = 200): GasTransport => ({ async send() { return { status, body }; } });
const markup = (value = event) => renderToStaticMarkup(createElement(GasEventDetails, { event: value, selectedAssetId: asset.asset_id, openAsset() {} }));
afterEach(() => vi.unstubAllGlobals());

describe("燃气真实详情 GET 合同", () => {
  it("资产和事件详情使用独立GET，并编码整个对象ID", async () => {
    const calls: Parameters<GasTransport["send"]>[0][] = [];
    const id = "管段/ ?#";
    const api = new GasEngineeringApi({ async send(request) { calls.push(request); return { status: 200, body: { ...asset, asset_id: id } }; } });
    await api.asset(id);
    expect(calls[0]).toMatchObject({ method: "GET", path: "/api/v1/gas/engineering/assets/%E7%AE%A1%E6%AE%B5%2F%20%3F%23" });
    const eventCalls: string[] = [];
    await new GasEngineeringApi({ async send(request) { eventCalls.push(request.path); return { status: 200, body: event }; } }).event(event.event_id);
    expect(eventCalls).toEqual([`/api/v1/gas/engineering/events/${event.event_id}`]);
  });
  it("保留默认200条分页及服务端total，不将本页条数当成总数", async () => {
    let path = "";
    const api = new GasEngineeringApi({ async send(request) { path = request.path; return { status: 200, body: { status: "partial", data_mode: "synthetic", source_status: "synthetic_fixture", validation_status: "not_business_validated", items: [asset], total: 204, limitations: [] } }; } });
    const result = await api.assets();
    expect(path).toBe("/api/v1/gas/engineering/assets?limit=200&offset=0");
    expect(result.items).toHaveLength(1);
    expect(result.total).toBe(204);
    expect(result.status).toBe("partial");
  });
  it.each([[401, "unauthenticated"], [403, "forbidden"], [404, "not_found"], [503, "unavailable"], [500, "http"]] as const)("保留HTTP %i的独立失败语义", async (status, code) => {
    const api = new GasEngineeringApi(transport({ detail: "internal secret /private/path" }, status));
    await expect(api.asset(asset.asset_id)).rejects.toMatchObject({ code, status });
    await expect(api.events()).rejects.not.toThrow("internal secret");
  });
  it("网络中断不退回缓存或模拟成功", async () => {
    const api = new GasEngineeringApi({ async send() { throw new TypeError("fetch failed private host"); } });
    await expect(api.asset(asset.asset_id)).rejects.toMatchObject({ code: "network" });
    await expect(api.asset(asset.asset_id)).rejects.not.toThrow("private host");
  });
  it("取消请求传递原AbortError，不包装成网络故障", async () => {
    const controller = new AbortController();
    controller.abort();
    const aborted = new Error("aborted"); aborted.name = "AbortError";
    const api = new GasEngineeringApi({ async send(request) { expect(request.signal).toBe(controller.signal); throw aborted; } });
    await expect(api.asset(asset.asset_id, controller.signal)).rejects.toBe(aborted);
  });
  it.each([null, {}, "<html>upstream failure</html>", { ...asset, asset_id: "another-asset" }, { ...asset, evidence_refs: [false] }, { ...asset, asset_type: "invented" }])("拒绝无效或身份错配的资产响应 %#", async (body) => {
    await expect(new GasEngineeringApi(transport(body)).asset(asset.asset_id)).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("拒绝身份错配事件与非法关联ID", async () => {
    await expect(new GasEngineeringApi(transport({ ...event, event_id: "other" })).event(event.event_id)).rejects.toMatchObject({ code: "invalid_response" });
    await expect(new GasEngineeringApi(transport({ ...event, asset_ids: [42] })).event(event.event_id)).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("响应集合不能让total小于实际返回条数", async () => {
    const body = { status: "partial", data_mode: "synthetic", source_status: "synthetic_fixture", validation_status: "not_business_validated", items: [event], total: 0, limitations: [] };
    await expect(new GasEngineeringApi(transport(body)).events()).rejects.toMatchObject({ code: "invalid_response" });
  });
  it("浏览器运输使用同源认证上下文、GET及取消信号，不硬编码凭据", async () => {
    const controller = new AbortController();
    const fetcher = vi.fn().mockResolvedValue({ status: 200, json: async () => asset });
    vi.stubGlobal("fetch", fetcher);
    await new BrowserGasTransport().send({ method: "GET", path: "/example", signal: controller.signal });
    expect(fetcher).toHaveBeenCalledWith("/example", { method: "GET", signal: controller.signal, credentials: "same-origin", headers: { Accept: "application/json", "X-Water-Client": "unified-react" } });
  });
  it("非JSON的成功响应不能成为资产详情", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 200, json: async () => { throw new SyntaxError("invalid JSON"); } }));
    await expect(new GasEngineeringApi().asset(asset.asset_id)).rejects.toMatchObject({ code: "invalid_response" });
  });
});

describe("事件、关联资产与证据展示", () => {
  it("展示真实DTO的ID、类型、状态与全部可点击关联ID", () => {
    const html = markup();
    for (const text of [event.event_id, event.event_kind, "synthetic_given", asset.asset_id, "syn-unit-pipe-2", "unit:synthetic-case", "not_computed"]) expect(html).toContain(text);
    expect(html.match(/aria-label="打开资产详情/g)).toHaveLength(2);
    expect(html).toContain('aria-pressed="true"');
  });
  it("时间字段未确认时不补当前时刻或私自引入time_hours", () => {
    const html = markup();
    expect(html).toContain("接口未提供时间字段");
    expect(html).not.toContain("time_hours");
    expect(html).not.toContain("2026-");
  });
  it("缺失关联不推断，重复ID只生成一个入口", () => {
    expect(markup({ ...event, asset_ids: [] })).toContain("不推测资产映射");
    expect(relatedAssetIds({ ...event, asset_ids: ["a", "a", "b"] })).toEqual(["a", "b"]);
    expect(relatedAssetIds({ ...event, asset_ids: undefined })).toEqual([]);
  });
  it("详情展示资产既有属性和来源，不伪造专业属性值", () => {
    const html = renderToStaticMarkup(createElement(GasAssetDetails, { asset }));
    for (const text of [asset.asset_id, asset.name, "管段", "pipe", "synthetic_reference", "synthetic_fixture", "unit:synthetic-network", "未提供"]) expect(html).toContain(text);
    expect(html).toContain("不补零或推导");
    expect(html).toContain("disabled");
    expect(html).not.toMatch(/longitude|latitude|重庆.*[0-9]+\.[0-9]+/);
  });
  it("省略的可选状态显示unknown而不是成功", () => {
    const html = markup({ ...event, state: undefined, severity: undefined, risk_status: undefined, physical_supply_status: undefined, evidence_refs: [] });
    expect(html).toContain("未知");
    expect(html).toContain("unknown");
    expect(html).toContain("接口未提供证据引用");
  });
  it.each([401, 403, 404, 503])("HTTP %i在界面中提供可辨识提示和重试", async (status) => {
    let error: GasApiError | undefined;
    try { await new GasEngineeringApi(transport(null, status)).asset(asset.asset_id); } catch (caught) { error = caught as GasApiError; }
    const html = renderToStaticMarkup(createElement(GasReadError, { error: error!, retry() {}, label: "重试读取资产" }));
    expect(html).toContain('role="alert"');
    expect(html).toContain(String(status));
    expect(html).toContain("重试读取资产");
  });
});

describe("GIS授权与合法坐标门禁", () => {
  const authorized: GasAsset = { ...asset, data_mode: "real_authorized", source_status: "authorized", location_status: "authorized", location: { coordinate_system: "wgs84", longitude: 0, latitude: 0 } };
  it("允许合法零坐标通过数据校验，但未接地图时仍不伪造定位", () => {
    expect(hasAuthorizedGasLocation(authorized)).toBe(true);
    expect(gasLocationReason(authorized)).toContain("未接入 GIS");
    expect(renderToStaticMarkup(createElement(GasAssetDetails, { asset: authorized }))).toContain("disabled");
  });
  it.each([null, { coordinate_system: "wgs84", longitude: 181, latitude: 0 }, { coordinate_system: "wgs84", longitude: 0, latitude: 91 }, { coordinate_system: "wgs84", longitude: NaN, latitude: 0 }])("拒绝缺失或非法坐标 %#", (location) => {
    expect(hasAuthorizedGasLocation({ ...authorized, location: location as GasAsset["location"] })).toBe(false);
  });
  it("合成/unknown/未授权数据即使坐标数值合法也不可定位", () => {
    for (const data_mode of ["synthetic", "unknown", "unavailable"] as const) expect(hasAuthorizedGasLocation({ ...authorized, data_mode })).toBe(false);
    expect(hasAuthorizedGasLocation({ ...authorized, source_status: "unknown" })).toBe(false);
    expect(hasAuthorizedGasLocation({ ...authorized, location_status: "unknown" })).toBe(false);
    expect(hasAuthorizedGasLocation({ ...authorized, location: { ...authorized.location!, coordinate_system: "schematic" as "wgs84" } })).toBe(false);
  });
});

describe("对象切换与错误状态隔离", () => {
  const loaded: GasReadState<GasAsset> = { key: asset.asset_id, status: "succeeded", data: asset, error: null };
  it("切换ID的第一帧不能展示上一资产", () => {
    expect(currentGasRead(loaded, "next")).toEqual({ key: "next", status: "loading", data: null, error: null });
  });
  it("关闭详情立即清空成功数据，取消后旧响应仍被ID门禁遮蔽", () => {
    expect(currentGasRead(loaded, null)).toEqual({ key: null, status: "idle", data: null, error: null });
    expect(currentGasRead(loaded, "next").data).toBeNull();
    expect(currentGasRead(loaded, asset.asset_id)).toBe(loaded);
  });
  it("新请求或重试不继承上一结果与错误", () => {
    const failed: GasReadState<GasAsset> = { key: "old", status: "failed", data: null, error: new GasApiError("forbidden", "拒绝", 403) };
    expect(currentGasRead(failed, "new").error).toBeNull();
    expect(pendingGasRead<GasAsset>("new")).toEqual({ key: "new", status: "loading", data: null, error: null });
  });
});
