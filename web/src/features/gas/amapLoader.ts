import type { GasCoordinate } from "../../shared/contracts/platform";
import type { GasAsset, GasEvent } from "./contracts";
import { hasAuthorizedGasLocation } from "./presentation";

export interface ControlledAmapConfig {
  configured: boolean;
  key: string | null;
  version: string;
  serviceHost: string | null;
  source: "runtime" | "env" | "unconfigured";
  reason: string;
}

export interface AmapGlobalConfig {
  key?: string;
  version?: string;
  serviceHost?: string;
}

export interface GasGisAssetFeature {
  kind: "asset";
  asset: GasAsset;
  gcj02: { lon: number; lat: number };
}

export interface GasGisEventFeature {
  kind: "event";
  event: GasEvent;
  gcj02: { lon: number; lat: number };
}

export interface GasGisLayerInterface {
  setDistrictFocus(adcode: string | null, center?: { lon: number; lat: number; zoom?: number }): void;
  setLayersVisible(layers: { adminRegions: boolean; authorizedAssets: boolean; authorizedEvents: boolean }): void;
  renderAuthorizedFeatures(assets: readonly GasGisAssetFeature[], events: readonly GasGisEventFeature[]): void;
  resetViewport(): void;
  destroy(): void;
}

declare global {
  interface Window {
    __WPN_AMAP_CONFIG__?: AmapGlobalConfig;
    _AMapSecurityConfig?: { serviceHost?: string };
    AMap?: {
      Map: new (container: HTMLElement, options: Record<string, unknown>) => {
        setCenter(center: [number, number]): void;
        setZoom(zoom: number): void;
        setZoomAndCenter(zoom: number, center: [number, number]): void;
        setMapStyle(style: string): void;
        setFitView(overlays: unknown[], immediately?: boolean, avoid?: number[], maxZoom?: number): void;
        zoomIn(): void;
        zoomOut(): void;
        add(overlays: unknown | unknown[]): void;
        remove(overlays: unknown | unknown[]): void;
        destroy(): void;
      };
      DistrictSearch?: new (options: { level: "province" | "district"; subdistrict: number; extensions: "all" }) => {
        search(adcode: string, callback: (status: string, result: unknown) => void): void;
      };
      Polygon?: new (options: Record<string, unknown>) => unknown;
      Marker?: new (options: Record<string, unknown>) => unknown;
    };
  }
}

const VALID_KEY_PATTERN = /^[A-Za-z0-9_-]{16,128}$/;

export function normalizeControlledServiceHost(raw?: string | null): string | null {
  const trimmed = raw?.trim();
  if (!trimmed || /\s/.test(trimmed)) return null;
  if (trimmed.startsWith("/")) {
    if (trimmed.startsWith("//") || trimmed.includes("?") || trimmed.includes("#")) return null;
    return trimmed.replace(/\/+$/, "") || "/";
  }
  try {
    const parsed = new URL(trimmed);
    if ((parsed.protocol !== "http:" && parsed.protocol !== "https:") || parsed.username || parsed.password || parsed.search || parsed.hash) {
      return null;
    }
    return `${parsed.origin}${parsed.pathname === "/" ? "" : parsed.pathname.replace(/\/+$/, "")}`;
  } catch {
    return null;
  }
}

export function readControlledAmapConfig(
  runtimeOverride?: AmapGlobalConfig | null,
  envOverride?: Record<string, string | undefined>,
): ControlledAmapConfig {
  const runtime = runtimeOverride ?? (typeof window !== "undefined" ? window.__WPN_AMAP_CONFIG__ : undefined);
  const env = envOverride ?? (import.meta.env as Record<string, string | undefined>);
  const runtimeKey = runtime?.key?.trim() || "";
  const envKey = env.VITE_AMAP_KEY?.trim() || "";
  const rawKey = runtimeKey || envKey;
  const version = runtime?.version?.trim() || env.VITE_AMAP_VERSION?.trim() || "2.0";
  const serviceHost = normalizeControlledServiceHost(runtime?.serviceHost ?? env.VITE_AMAP_SERVICE_HOST);

  if (!rawKey || !VALID_KEY_PATTERN.test(rawKey)) {
    return {
      configured: false,
      key: null,
      version,
      serviceHost: null,
      source: "unconfigured",
      reason: "高德地图未通过受控配置启用；不请求外部瓦片脚本，保持离线重庆行政参考视图。",
    };
  }
  return {
    configured: true,
    key: rawKey,
    version,
    serviceHost,
    source: runtimeKey ? "runtime" : "env",
    reason: serviceHost
      ? "高德 Key 仅用于受控运行时加载，经受控服务端代理（serviceHost）转发安全参数；客户端不含 Security JS Code。"
      : "高德 Key 仅用于受控运行时加载；Security JS Code 不进入浏览器构建，设施坐标仍受接口授权边界门禁。",
  };
}

function outOfChina(lon: number, lat: number): boolean {
  return lon < 72.004 || lon > 137.8347 || lat < 0.8293 || lat > 55.8271;
}

function transformLat(x: number, y: number): number {
  let ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x));
  ret += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0;
  ret += ((20.0 * Math.sin(y * Math.PI) + 40.0 * Math.sin((y / 3.0) * Math.PI)) * 2.0) / 3.0;
  ret += ((160.0 * Math.sin((y / 12.0) * Math.PI) + 320 * Math.sin((y * Math.PI) / 30.0)) * 2.0) / 3.0;
  return ret;
}

function transformLon(x: number, y: number): number {
  let ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x));
  ret += ((20.0 * Math.sin(6.0 * x * Math.PI) + 20.0 * Math.sin(2.0 * x * Math.PI)) * 2.0) / 3.0;
  ret += ((20.0 * Math.sin(x * Math.PI) + 40.0 * Math.sin((x / 3.0) * Math.PI)) * 2.0) / 3.0;
  ret += ((150.0 * Math.sin((x / 12.0) * Math.PI) + 300.0 * Math.sin((x / 30.0) * Math.PI)) * 2.0) / 3.0;
  return ret;
}

export function toGcj02Coordinate(coord: GasCoordinate): { lon: number; lat: number } {
  if (coord.coordinate_system === "gcj02" || outOfChina(coord.longitude, coord.latitude)) {
    return { lon: coord.longitude, lat: coord.latitude };
  }
  const a = 6378245.0;
  const ee = 0.00669342162296594323;
  const dLat = transformLat(coord.longitude - 105.0, coord.latitude - 35.0);
  const dLon = transformLon(coord.longitude - 105.0, coord.latitude - 35.0);
  const radLat = (coord.latitude / 180.0) * Math.PI;
  let magic = Math.sin(radLat);
  magic = 1 - ee * magic * magic;
  const sqrtMagic = Math.sqrt(magic);
  const adjustedLat = (dLat * 180.0) / (((a * (1 - ee)) / (magic * sqrtMagic)) * Math.PI);
  const adjustedLon = (dLon * 180.0) / ((a / sqrtMagic) * Math.cos(radLat) * Math.PI);
  return { lon: coord.longitude + adjustedLon, lat: coord.latitude + adjustedLat };
}

export function filterAuthorizedAssetFeatures(assets: readonly GasAsset[]): GasGisAssetFeature[] {
  const result: GasGisAssetFeature[] = [];
  for (const item of assets) {
    if (hasAuthorizedGasLocation(item) && item.location) {
      result.push({ kind: "asset", asset: item, gcj02: toGcj02Coordinate(item.location) });
    }
  }
  return result;
}

export function filterAuthorizedEventFeatures(events: readonly GasEvent[]): GasGisEventFeature[] {
  const result: GasGisEventFeature[] = [];
  for (const item of events) {
    if (hasAuthorizedGasLocation(item) && item.location) {
      result.push({ kind: "event", event: item, gcj02: toGcj02Coordinate(item.location) });
    }
  }
  return result;
}

let sdkPromise: Promise<NonNullable<Window["AMap"]>> | null = null;

export function loadAmapSdk(config: ControlledAmapConfig, timeoutMs = 8000): Promise<NonNullable<Window["AMap"]>> {
  if (!config.configured || !config.key) {
    return Promise.reject(new Error(config.reason));
  }
  if (typeof window === "undefined" || typeof document === "undefined") {
    return Promise.reject(new Error("当前环境不是浏览器，无法加载高德 JS API。"));
  }
  if (config.serviceHost) {
    window._AMapSecurityConfig = { serviceHost: new URL(config.serviceHost, window.location.origin).href };
  }
  if (window.AMap) {
    return Promise.resolve(window.AMap);
  }
  if (sdkPromise) return sdkPromise;

  sdkPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.async = true;
    script.dataset.wpnAmapSdk = "true";
    const params = new URLSearchParams({
      v: config.version,
      key: config.key!,
      plugin: "AMap.DistrictSearch",
    });
    script.src = `https://webapi.amap.com/maps?${params.toString()}`;
    const timer = window.setTimeout(() => {
      script.remove();
      sdkPromise = null;
      reject(new Error("高德 JS API 加载超时，已降级为离线重庆行政参考视图。"));
    }, timeoutMs);
    script.onload = () => {
      window.clearTimeout(timer);
      if (window.AMap) resolve(window.AMap);
      else {
        sdkPromise = null;
        reject(new Error("高德脚本已返回但未导出 AMap 对象，已降级为离线参考视图。"));
      }
    };
    script.onerror = () => {
      window.clearTimeout(timer);
      script.remove();
      sdkPromise = null;
      reject(new Error("高德 JS API 脚本加载失败（网络受限或密钥无效），已降级为离线行政参考视图。"));
    };
    document.head.appendChild(script);
  });
  return sdkPromise;
}

export type AmapSdk = NonNullable<Window["AMap"]>;
export type AmapMap = InstanceType<AmapSdk["Map"]>;

type BoundaryRings = unknown[][];

export class AmapBoundaryError extends Error {
  constructor(message: string, readonly reason: "service" | "rate_limit" | "timeout" | "invalid_boundary",
              readonly sdkStatus?: string, readonly providerCode?: string) {
    super(message);
  }
}

// The SDK object lives for this page session. Share in-flight lookups and keep
// only validated geometry in memory. A caller aborts its subscription, not the SDK request.
const sessionBoundaries = new WeakMap<AmapSdk, Map<string, Promise<BoundaryRings>>>();
const requestChains = new WeakMap<AmapSdk, Promise<unknown>>();
const rateLimits = new WeakMap<AmapSdk, { until: number; error: AmapBoundaryError }>();
const rateLimitCodes = new Set(["10014", "10015", "10019", "10020", "10021",
  "QPS_HAS_EXCEEDED_THE_LIMIT", "GATEWAY_TIMEOUT", "CQPS_HAS_EXCEEDED_THE_LIMIT",
  "CKQPS_HAS_EXCEEDED_THE_LIMIT", "CUQPS_HAS_EXCEEDED_THE_LIMIT"]);
const RATE_LIMIT_COOLDOWN_MS = 5000;

function rateLimitError(providerCode?: string, sdkStatus?: string) {
  return new AmapBoundaryError("高德区划请求暂时受限，显示离线参考。请稍后重试。", "rate_limit", sdkStatus, providerCode);
}

function observeProxyRateLimit(adcode: string, onLimit: (error: AmapBoundaryError) => void): PerformanceObserver | undefined {
  if (typeof window === "undefined" || typeof PerformanceObserver === "undefined") return;
  if (!PerformanceObserver.supportedEntryTypes.includes("resource")) return;
  const host = window._AMapSecurityConfig?.serviceHost;
  if (host !== `${window.location.origin}/_AMapService`) return;
  const started = performance.now();
  const observer = new PerformanceObserver(list => {
    for (const entry of list.getEntries() as PerformanceResourceTiming[]) {
      if (entry.startTime < started) continue;
      const url = new URL(entry.name);
      if (url.origin !== window.location.origin || url.pathname !== "/_AMapService/v3/config/district"
        || url.searchParams.get("keywords") !== adcode) continue;
      const code = entry.serverTiming?.find(metric => metric.name === "amap-district")?.description;
      const http = entry.serverTiming?.find(metric => metric.name === "amap-http")?.description;
      // An SDK error may arrive without a callback. Use the matched proxy response as evidence.
      if (http === "429" || (code && rateLimitCodes.has(code))) onLimit(rateLimitError(code));
    }
  });
  observer.observe({ entryTypes: ["resource"] });
  return observer;
}

function lookupBoundary(sdk: AmapSdk, adcode: string, timeoutMs: number): Promise<BoundaryRings> {
  return new Promise((resolve, reject) => {
    const { DistrictSearch, Polygon } = sdk;
    if (!DistrictSearch || !Polygon) {
      reject(new AmapBoundaryError("高德行政区划插件未就绪，显示离线参考。", "service"));
      return;
    }
    let sdkErrorTimer: ReturnType<typeof setTimeout> | undefined;
    let settled = false;
    function finish() {
      settled = true;
      clearTimeout(timer);
      clearTimeout(sdkErrorTimer);
      observer?.disconnect();
    }
    function fail(error: AmapBoundaryError) {
      finish();
      reject(error);
    }
    const timer = setTimeout(() => fail(new AmapBoundaryError("高德区划请求超时，显示离线参考。", "timeout")), timeoutMs);
    const observer = observeProxyRateLimit(adcode, fail);
    try {
      const query = new DistrictSearch({ level: adcode === "500000" ? "province" : "district", subdistrict: 0, extensions: "all" });
      query.search(adcode, (status, result) => {
        if (settled) return;
        const response = result as { info?: string; infocode?: string | number;
          districtList?: { adcode?: string | number; boundaries?: BoundaryRings }[] } | null;
        if (status !== "complete") {
          const reportedCode = String(response?.infocode ?? response?.info ?? (typeof result === "string" ? result : ""));
          const code = /^(?:\d{5}|[A-Z_]{3,40})$/.test(reportedCode) ? reportedCode : undefined;
          const limited = code !== undefined && rateLimitCodes.has(code);
          if (limited) {
            fail(rateLimitError(code, status));
          } else {
            const error = new AmapBoundaryError("高德区划服务暂不可用，显示离线参考。", "service", status, code);
            // Resource timing delivers the same response independently of the SDK's error callback.
            if (observer) sdkErrorTimer = setTimeout(() => fail(error), 150);
            else fail(error);
          }
          return;
        }
        const district = Array.isArray(response?.districtList)
          ? response.districtList.find(item => item && String(item.adcode) === adcode) : undefined;
        const rings = district?.boundaries;
        if (!Array.isArray(rings) || !rings.length || rings.some(ring => !Array.isArray(ring) || ring.length < 3)) {
          fail(new AmapBoundaryError("高德暂未返回当前区划代码对应的完整边界。", "invalid_boundary", status));
          return;
        }
        finish();
        resolve(rings);
      });
    } catch {
      fail(new AmapBoundaryError("高德区划服务暂不可用，显示离线参考。", "service"));
    }
  });
}

export function readAmapBoundary(sdk: AmapSdk, adcode: string, signal: AbortSignal, timeoutMs = 10000): Promise<BoundaryRings> {
  if (signal.aborted) return Promise.reject(new DOMException("Cancelled", "AbortError"));
  const cache = sessionBoundaries.get(sdk) ?? new Map<string, Promise<BoundaryRings>>();
  sessionBoundaries.set(sdk, cache);
  let shared = cache.get(adcode);
  if (!shared) {
    // Serialize requests for distinct regions so rapid selections cannot hit the provider in parallel.
    shared = (requestChains.get(sdk) ?? Promise.resolve()).then(async () => {
      const limit = rateLimits.get(sdk);
      if (limit && limit.until > Date.now()) throw limit.error;
      try {
        return await lookupBoundary(sdk, adcode, timeoutMs);
      } catch (error) {
        if (error instanceof AmapBoundaryError && error.reason === "rate_limit") {
          rateLimits.set(sdk, { until: Date.now() + RATE_LIMIT_COOLDOWN_MS, error });
        }
        throw error;
      }
    });
    cache.set(adcode, shared);
    requestChains.set(sdk, shared.catch(() => undefined));
    void shared.catch(() => { if (cache.get(adcode) === shared) cache.delete(adcode); });
  }
  return new Promise((resolve, reject) => {
    const cancel = () => reject(new DOMException("Cancelled", "AbortError"));
    signal.addEventListener("abort", cancel, { once: true });
    void shared.then(resolve, reject).finally(() => signal.removeEventListener("abort", cancel));
  });
}
