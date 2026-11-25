import type { GasAsset, GasEvent } from "./contracts";

export const assetTypes: Record<GasAsset["asset_type"], string> = {
  pipe: "管段", valve: "阀门", node: "节点", source: "气源", consumer: "消费对象",
  regulator: "调压节点", junction: "普通节点",
};
export const eventKinds: Record<GasEvent["event_kind"], string> = {
  given_fault: "给定故障", given_isolation: "给定隔离", synthetic_observation: "合成观察",
};
export const eventStates: Record<NonNullable<GasEvent["state"]>, string> = {
  synthetic_given: "合成给定", open: "待处理", acknowledged: "已确认", closed: "已关闭",
};
type LocatedObject = Pick<GasAsset, "data_mode" | "source_status" | "location_status" | "location">;
export function hasAuthorizedGasLocation(item: LocatedObject): boolean {
  const point = item.location;
  return item.data_mode === "real_authorized" && item.source_status === "authorized"
    && item.location_status === "authorized" && point != null
    && ["wgs84", "gcj02"].includes(point.coordinate_system)
    && Number.isFinite(point.longitude) && Math.abs(point.longitude) <= 180
    && Number.isFinite(point.latitude) && Math.abs(point.latitude) <= 90;
}
export function gasLocationReason(item: LocatedObject): string {
  if (item.data_mode !== "real_authorized" || item.source_status !== "authorized") return "没有经授权的真实 GIS 点位；合成示意坐标不能用于地图定位。";
  if (!hasAuthorizedGasLocation(item)) return "坐标缺失、无效或未获授权，地图定位已禁用。";
  return "接口提供了合法授权坐标，但当前未接入 GIS 定位能力，定位仍不可用。";
}
export function relatedAssetIds(event: GasEvent): string[] {
  return [...new Set(event.asset_ids ?? [])];
}
