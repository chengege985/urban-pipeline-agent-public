import type { GasAsset, GasEvent, GasObservationRef } from "../contracts/platform";

export const GAS_ASSET_PAGE_SIZE = 50;
export const GAS_ASSET_TYPES = ["pipe", "valve", "node", "source", "consumer", "regulator", "junction"] as const satisfies readonly GasAsset["asset_type"][];
export type GasMode = "overview" | "risk" | "emergency";
export type GasLayout = "command" | "analysis" | "compact";
export type GasMap = "topology" | "gis";
export const PROJECT_GAS_MAP: GasMap = "gis";
export interface GasEngineeringRoute {
  gasEventId?: string;
  gasAssetId?: string;
  gasObservation?: GasObservationRef;
  assetPage: number;
  assetType?: GasAsset["asset_type"];
  gasSection: "events" | "assets" | "analysis" | "assistant";
  gasMode?: GasMode;
  gasLayout?: GasLayout;
  gasMap?: GasMap;
  gasCatalogueOpen?: boolean;
  gasEventKind?: GasEvent["event_kind"];
  gasEventState?: NonNullable<GasEvent["state"]>;
  gasDistrictId?: string;
  gasSubnetId?: string;
  gasRouteError?: string;
}
export function gasObservationRef(value: unknown): value is GasObservationRef {
  if (typeof value !== "object" || !value) return false;
  const { observation_id, manifest_sha256 } = value as Partial<GasObservationRef>;
  return typeof observation_id === "string" && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(observation_id)
    && typeof manifest_sha256 === "string" && /^[a-f0-9]{64}$/.test(manifest_sha256);
}

export function parseGasEngineeringRoute(segments: string[], params: URLSearchParams, defaultMap: GasMap = "topology"): GasEngineeringRoute {
  const map = params.get("map");
  const rawPage = params.get("page") ?? "1";
  const page = Number(rawPage);
  const route: GasEngineeringRoute = {
    assetPage: /^[1-9]\d*$/.test(rawPage) && Number.isSafeInteger(page) && Number.isSafeInteger((page - 1) * GAS_ASSET_PAGE_SIZE) ? page : 1,
    assetType: GAS_ASSET_TYPES.find((type) => type === params.get("type")),
    gasSection: params.get("tab") === "assets" ? "assets" : "events",
    gasMode: ["overview", "risk", "emergency"].includes(params.get("mode") ?? "") ? params.get("mode") as GasMode : "overview",
    gasLayout: ["command", "analysis", "compact"].includes(params.get("layout") ?? "") ? params.get("layout") as GasLayout : "command",
    gasMap: map === "gis" || map === "topology" ? map : defaultMap,
    gasCatalogueOpen: params.get("catalogue") === "1",
    gasEventKind: (["given_fault", "given_isolation", "synthetic_observation"] as const).find((kind) => kind === params.get("eventKind")),
    gasEventState: (["synthetic_given", "open", "acknowledged", "closed"] as const).find((state) => state === params.get("eventState")),
    gasDistrictId: /^500[12]\d{2}$/.test(params.get("district") ?? "") ? params.get("district")! : undefined,
    gasSubnetId: (map === "gis" || map === "topology" ? map : defaultMap) === "topology"
      && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(params.get("subnet") ?? "")
      ? params.get("subnet")! : undefined,
  };
  try {
    if (params.has("observation") || params.has("monitoringDigest")) {
      const observation = { observation_id: params.get("observation"), manifest_sha256: params.get("monitoringDigest") };
      if (!gasObservationRef(observation)) throw new Error("invalid monitoring identity");
      route.gasObservation = observation;
    }
    if (segments[0] === "events" && (segments.length === 2 || (segments[2] === "assets" && segments.length === 4))) {
      route.gasEventId = decodeURIComponent(segments[1]);
      if (segments.length === 4) route.gasAssetId = decodeURIComponent(segments[3]);
      route.gasSection = "events";
    } else if (segments[0] === "assets" && segments.length <= 2) {
      route.gasSection = "assets";
      if (segments[1]) route.gasAssetId = decodeURIComponent(segments[1]);
      else route.gasCatalogueOpen = true;
    } else if (["analysis", "assistant"].includes(segments[0]) && segments.length === 1) {
      route.gasSection = segments[0] as "analysis" | "assistant";
      route.gasEventId = params.get("event") || undefined;
      route.gasAssetId = params.get("asset") || undefined;
      if (route.gasSection === "analysis" && !params.has("mode")) route.gasMode = "risk";
    } else if (segments.length) {
      throw new Error("unrecognized gas route");
    }
    if (route.gasEventId === "" || route.gasAssetId === "") throw new Error("empty gas identity");
    return route;
  } catch {
    return { ...route, gasEventId: undefined, gasAssetId: undefined, gasObservation: undefined, gasSection: "events", gasCatalogueOpen: false,
      gasRouteError: "无法识别燃气详情地址。请在统一中控重新选择事件或资产。" };
  }
}
export function parseGasHash(hash: string, defaultMap: GasMap = "topology"): GasEngineeringRoute {
  const [path, query] = hash.replace(/^#\/?/, "").split("?", 2);
  const [prefix, ...segments] = path.split("/").filter(Boolean);
  const isGas = prefix === "gas" || prefix === "gas-control-room" || prefix === "gas-engineering";
  return parseGasEngineeringRoute(isGas ? segments : [], new URLSearchParams(isGas ? query : undefined), defaultMap);
}

export function gasEngineeringPath(route: Partial<GasEngineeringRoute> = {}, defaultMap: GasMap = "topology"): string {
  const params = new URLSearchParams();
  const section = route.gasSection ?? (route.gasEventId ? "events" : route.gasAssetId ? "assets" : "events");
  let path = "/gas";
  if (section === "analysis" || section === "assistant") {
    path += `/${section}`;
    if (route.gasEventId) params.set("event", route.gasEventId);
    if (route.gasAssetId) params.set("asset", route.gasAssetId);
  } else if (route.gasEventId) {
    path += `/events/${encodeURIComponent(route.gasEventId)}`;
    if (route.gasAssetId) path += `/assets/${encodeURIComponent(route.gasAssetId)}`;
  } else if (section === "assets" || route.gasAssetId) {
    path += "/assets";
    if (route.gasAssetId) path += `/${encodeURIComponent(route.gasAssetId)}`;
  }
  if (route.assetPage && route.assetPage > 1) params.set("page", String(route.assetPage));
  if (route.assetType) params.set("type", route.assetType);
  const defaultMode = section === "analysis" ? "risk" : "overview";
  if (route.gasMode && route.gasMode !== defaultMode) params.set("mode", route.gasMode);
  if (route.gasLayout && route.gasLayout !== "command") params.set("layout", route.gasLayout);
  if (route.gasMap && route.gasMap !== defaultMap) params.set("map", route.gasMap);
  if (route.gasCatalogueOpen && route.gasAssetId) params.set("catalogue", "1");
  if (route.gasEventKind) params.set("eventKind", route.gasEventKind);
  if (route.gasEventState) params.set("eventState", route.gasEventState);
  if (route.gasDistrictId) params.set("district", route.gasDistrictId);
  if ((route.gasMap ?? defaultMap) === "topology" && route.gasSubnetId) params.set("subnet", route.gasSubnetId);
  if (gasObservationRef(route.gasObservation)) {
    params.set("observation", route.gasObservation.observation_id);
    params.set("monitoringDigest", route.gasObservation.manifest_sha256);
  }
  return path + (params.size ? `?${params}` : "");
}
