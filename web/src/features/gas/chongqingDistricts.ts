import type { GasAsset, GasCollection, GasDistrict, GasEvent } from "./contracts";
import type { GasCaseResponse } from "./types";

export type ChongqingZone = "主城核心区" | "主城新区" | "渝东北" | "渝东南";

export interface ChongqingDistrictRef {
  district_id: string;
  name: string;
  shortName: string;
  zone: ChongqingZone;
  kind: "区" | "县" | "自治县";
  refLon: number;
  refLat: number;
}

export const CHONGQING_CENTER: Readonly<{ lon: number; lat: number; zoom: number }> = {
  lon: 107.35,
  lat: 30.08,
  zoom: 1,
};

// 重庆市民政局 2025-12 区划登记。点位只用于离线示意。
export const CHONGQING_DISTRICTS: readonly ChongqingDistrictRef[] = [
  { district_id: "500103", name: "渝中区", shortName: "渝中", zone: "主城核心区", kind: "区", refLon: 106.569, refLat: 29.553 },
  { district_id: "500157", name: "两江新区", shortName: "两江新区", zone: "主城核心区", kind: "区", refLon: 106.570, refLat: 29.663 },
  { district_id: "500108", name: "南岸区", shortName: "南岸", zone: "主城核心区", kind: "区", refLon: 106.644, refLat: 29.501 },
  { district_id: "500107", name: "九龙坡区", shortName: "九龙坡", zone: "主城核心区", kind: "区", refLon: 106.481, refLat: 29.503 },
  { district_id: "500106", name: "沙坪坝区", shortName: "沙坪坝", zone: "主城核心区", kind: "区", refLon: 106.457, refLat: 29.541 },
  { district_id: "500104", name: "大渡口区", shortName: "大渡口", zone: "主城核心区", kind: "区", refLon: 106.482, refLat: 29.435 },
  { district_id: "500109", name: "北碚区", shortName: "北碚", zone: "主城核心区", kind: "区", refLon: 106.396, refLat: 29.805 },
  { district_id: "500113", name: "巴南区", shortName: "巴南", zone: "主城核心区", kind: "区", refLon: 106.540, refLat: 29.383 },
  { district_id: "500115", name: "长寿区", shortName: "长寿", zone: "主城新区", kind: "区", refLon: 107.081, refLat: 29.858 },
  { district_id: "500116", name: "江津区", shortName: "江津", zone: "主城新区", kind: "区", refLon: 106.259, refLat: 29.290 },
  { district_id: "500117", name: "合川区", shortName: "合川", zone: "主城新区", kind: "区", refLon: 106.276, refLat: 29.972 },
  { district_id: "500118", name: "永川区", shortName: "永川", zone: "主城新区", kind: "区", refLon: 105.927, refLat: 29.356 },
  { district_id: "500119", name: "南川区", shortName: "南川", zone: "主城新区", kind: "区", refLon: 107.099, refLat: 29.158 },
  { district_id: "500110", name: "綦江区", shortName: "綦江", zone: "主城新区", kind: "区", refLon: 106.651, refLat: 29.028 },
  { district_id: "500111", name: "大足区", shortName: "大足", zone: "主城新区", kind: "区", refLon: 105.722, refLat: 29.707 },
  { district_id: "500120", name: "璧山区", shortName: "璧山", zone: "主城新区", kind: "区", refLon: 106.227, refLat: 29.592 },
  { district_id: "500151", name: "铜梁区", shortName: "铜梁", zone: "主城新区", kind: "区", refLon: 106.056, refLat: 29.845 },
  { district_id: "500152", name: "潼南区", shortName: "潼南", zone: "主城新区", kind: "区", refLon: 105.840, refLat: 30.191 },
  { district_id: "500153", name: "荣昌区", shortName: "荣昌", zone: "主城新区", kind: "区", refLon: 105.595, refLat: 29.405 },
  { district_id: "500102", name: "涪陵区", shortName: "涪陵", zone: "主城新区", kind: "区", refLon: 107.389, refLat: 29.703 },
  { district_id: "500101", name: "万州区", shortName: "万州", zone: "渝东北", kind: "区", refLon: 108.409, refLat: 30.808 },
  { district_id: "500154", name: "开州区", shortName: "开州", zone: "渝东北", kind: "区", refLon: 108.393, refLat: 31.161 },
  { district_id: "500155", name: "梁平区", shortName: "梁平", zone: "渝东北", kind: "区", refLon: 107.799, refLat: 30.676 },
  { district_id: "500229", name: "城口县", shortName: "城口", zone: "渝东北", kind: "县", refLon: 108.664, refLat: 31.948 },
  { district_id: "500230", name: "丰都县", shortName: "丰都", zone: "渝东北", kind: "县", refLon: 107.731, refLat: 29.863 },
  { district_id: "500231", name: "垫江县", shortName: "垫江", zone: "渝东北", kind: "县", refLon: 107.333, refLat: 30.328 },
  { district_id: "500233", name: "忠县", shortName: "忠县", zone: "渝东北", kind: "县", refLon: 108.037, refLat: 30.299 },
  { district_id: "500235", name: "云阳县", shortName: "云阳", zone: "渝东北", kind: "县", refLon: 108.697, refLat: 30.931 },
  { district_id: "500236", name: "奉节县", shortName: "奉节", zone: "渝东北", kind: "县", refLon: 109.464, refLat: 31.018 },
  { district_id: "500237", name: "巫山县", shortName: "巫山", zone: "渝东北", kind: "县", refLon: 109.879, refLat: 31.075 },
  { district_id: "500238", name: "巫溪县", shortName: "巫溪", zone: "渝东北", kind: "县", refLon: 109.570, refLat: 31.399 },
  { district_id: "500114", name: "黔江区", shortName: "黔江", zone: "渝东南", kind: "区", refLon: 108.771, refLat: 29.533 },
  { district_id: "500156", name: "武隆区", shortName: "武隆", zone: "渝东南", kind: "区", refLon: 107.760, refLat: 29.326 },
  { district_id: "500240", name: "石柱土家族自治县", shortName: "石柱", zone: "渝东南", kind: "自治县", refLon: 108.114, refLat: 30.000 },
  { district_id: "500241", name: "秀山土家族苗族自治县", shortName: "秀山", zone: "渝东南", kind: "自治县", refLon: 109.007, refLat: 28.448 },
  { district_id: "500242", name: "酉阳土家族苗族自治县", shortName: "酉阳", zone: "渝东南", kind: "自治县", refLon: 108.768, refLat: 28.841 },
  { district_id: "500243", name: "彭水苗族土家族自治县", shortName: "彭水", zone: "渝东南", kind: "自治县", refLon: 108.166, refLat: 29.295 },
];

export const CHONGQING_OUTLINE_COORDS: readonly [number, number][] = [
  [105.29, 29.95], [105.62, 30.38], [106.25, 30.25], [106.82, 30.42],
  [107.45, 30.65], [108.15, 31.18], [108.58, 32.16], [109.10, 31.95],
  [109.92, 31.65], [110.19, 31.08], [109.52, 30.78], [108.95, 30.25],
  [109.25, 29.40], [109.30, 28.18], [108.62, 28.42], [108.15, 28.85],
  [107.42, 28.92], [106.85, 28.55], [106.15, 28.95], [105.50, 29.22],
  [105.29, 29.95],
];

export const YANGTZE_RIVER_COORDS: readonly [number, number][] = [
  [105.95, 29.02], [106.26, 29.28], [106.56, 29.55], [107.08, 29.85],
  [107.39, 29.71], [107.73, 29.86], [108.04, 30.30], [108.41, 30.80],
  [108.70, 30.93], [109.46, 31.02], [109.88, 31.07],
];

export const JIALING_RIVER_COORDS: readonly [number, number][] = [
  [106.25, 30.25], [106.28, 29.97], [106.40, 29.81], [106.52, 29.60], [106.57, 29.56],
];

export function findChongqingDistrict(query: string | undefined | null): ChongqingDistrictRef | null {
  if (!query) return null;
  const trimmed = query.trim();
  return CHONGQING_DISTRICTS.find(
    (d) => d.district_id === trimmed || d.name === trimmed || d.shortName === trimmed,
  ) ?? null;
}

export function matchChongqingDistrictInText(text: string): ChongqingDistrictRef | null {
  const cleaned = text.trim();
  if (!cleaned) return null;
  for (const district of CHONGQING_DISTRICTS) {
    if (cleaned.includes(district.district_id) || cleaned.includes(district.name) || cleaned.includes(district.shortName)) {
      return district;
    }
  }
  return null;
}

export function projectChongqingCoord(lon: number, lat: number): { x: number; y: number } {
  const x = ((lon - 105.2) / (110.3 - 105.2)) * 880 + 60;
  const y = ((32.25 - lat) / (32.25 - 28.1)) * 520 + 60;
  return { x: Number(x.toFixed(1)), y: Number(y.toFixed(1)) };
}

export function chongqingDistrictSvgViewport(district: ChongqingDistrictRef | null): { zoom: number; x: number; y: number } {
  if (!district) return { zoom: 1, x: 0, y: 0 };
  const zoom = district.zone === "主城核心区" ? 2.8 : 1.9;
  const point = projectChongqingCoord(district.refLon, district.refLat);
  return {
    zoom,
    x: Number((500 - point.x * zoom).toFixed(1)),
    y: Number((320 - point.y * zoom).toFixed(1)),
  };
}

export interface DistrictReadinessSummary {
  backendStatus: GasCollection<GasDistrict>["status"] | "loading" | "error" | "unknown";
  backendDataMode: GasCollection<GasDistrict>["data_mode"] | "unknown";
  backendTotal: number;
  mappedEventCount: number;
  unmappedEventCount: number;
  mappedAssetCount: number;
  totalAssetsKnown: number | null;
  selectedDistrict: ChongqingDistrictRef | null;
  selectedBackendRecord: GasDistrict | null;
  selectedDistrictEventCount: number | null;
  selectedDistrictNonClosedEventCount: number | null;
  limitations: string[];
}

export function summarizeDistrictReadiness(options: {
  selectedDistrictId?: string | null;
  districts: GasCollection<GasDistrict> | null;
  districtsStatus: "idle" | "loading" | "succeeded" | "failed";
  events: GasCollection<GasEvent> | null;
  sampleAssets?: GasAsset[];
  assetTotal: number | null;
  caseView: GasCaseResponse | null;
}): DistrictReadinessSummary {
  const { selectedDistrictId, districts, districtsStatus, events, sampleAssets, assetTotal, caseView } = options;
  const selectedDistrict = findChongqingDistrict(selectedDistrictId);
  const items = districts?.items ?? [];
  const selectedBackendRecord = selectedDistrict
    ? items.find((item) => item.district_id === selectedDistrict.district_id || item.name === selectedDistrict.name) ?? null
    : null;
  const eventList = events?.items ?? [];
  const mappedEvents = eventList.filter((item) => Boolean(item.district_id));
  const mappedSampleAssets = (sampleAssets ?? []).filter((item) => Boolean(item.district_id));
  const matchingEvents = selectedDistrict ? eventList.filter((item) => item.district_id === selectedDistrict.district_id) : [];
  const hasDistrictApiItems = items.length > 0;
  const limitations = [...(districts?.limitations ?? [])];
  if (!hasDistrictApiItems && !limitations.length) {
    limitations.push("未发现经授权的重庆区县GIS边界或设施点位；不生成虚构区县数据。");
  }
  return {
    backendStatus: districts ? districts.status : districtsStatus === "loading" ? "loading" : districtsStatus === "failed" ? "error" : "unknown",
    backendDataMode: districts?.data_mode ?? "unknown",
    backendTotal: districts?.total ?? 0,
    mappedEventCount: mappedEvents.length,
    unmappedEventCount: Math.max(0, eventList.length - mappedEvents.length),
    mappedAssetCount: mappedSampleAssets.length,
    totalAssetsKnown: assetTotal ?? (caseView ? caseView.counts.pipes + caseView.counts.valves + caseView.counts.nodes + caseView.counts.consumers : null),
    selectedDistrict,
    selectedBackendRecord,
    selectedDistrictEventCount: selectedDistrict && (hasDistrictApiItems || mappedEvents.length > 0) ? matchingEvents.length : null,
    selectedDistrictNonClosedEventCount: selectedDistrict && (hasDistrictApiItems || mappedEvents.length > 0)
      ? matchingEvents.filter((item) => item.state !== "closed").length
      : null,
    limitations,
  };
}
