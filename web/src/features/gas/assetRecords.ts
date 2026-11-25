import type { GasCaseResponse, GasNode } from "./types";

export type GasRecordKind = "pipe_asset" | "valve_asset" | "topology_node" | "consumer_record" | "unknown";
export interface GasRecordIdentity {
  kind: GasRecordKind;
  label: string;
  nodeType?: GasNode["node_type"];
}
export type GasRecordIndex = ReadonlyMap<string, GasRecordIdentity>;
export const UNKNOWN_GAS_RECORD: GasRecordIdentity = { kind: "unknown", label: "unknown（未取得集合映射）" };
export const GAS_NODE_LABELS: Record<GasNode["node_type"], string> = {
  junction: "普通节点", regulator: "调压节点", valve: "阀门节点", source: "气源节点",
};
// Membership is read from the confirmed case DTO. Never infer origin from an ID
// prefix or collapse a valve asset and its topology node into one source record.
export function indexGasRecords(value: GasCaseResponse): GasRecordIndex {
  const index = new Map<string, GasRecordIdentity>();
  const put = (id: string, identity: GasRecordIdentity) => index.set(id, index.has(id) ? UNKNOWN_GAS_RECORD : identity);
  value.pipes.forEach((item) => put(item.pipe_id, { kind: "pipe_asset", label: "管段资产" }));
  value.valves.forEach((item) => put(item.valve_id, { kind: "valve_asset", label: "阀门资产" }));
  value.nodes.forEach((item) => put(item.node_id, { kind: "topology_node", label: "拓扑节点", nodeType: item.node_type }));
  value.consumers.forEach((item) => put(item.consumer_id, { kind: "consumer_record", label: "消费对象记录" }));
  return index;
}
export function gasRecordIdentity(index: GasRecordIndex | null, id: string): GasRecordIdentity {
  return index?.get(id) ?? UNKNOWN_GAS_RECORD;
}

const RECORD_GROUPS: readonly { kind: GasRecordKind; label: string }[] = [
  { kind: "pipe_asset", label: "管段" },
  { kind: "valve_asset", label: "阀门" },
  { kind: "topology_node", label: "拓扑节点" },
  { kind: "consumer_record", label: "消费对象" },
  { kind: "unknown", label: "类别未确认" },
];

export function groupGasRecordIds(index: GasRecordIndex | null, ids: readonly string[]) {
  const byKind = new Map<GasRecordKind, string[]>();
  for (const id of new Set(ids)) {
    const { kind } = gasRecordIdentity(index, id);
    const group = byKind.get(kind);
    if (group) group.push(id);
    else byKind.set(kind, [id]);
  }
  return RECORD_GROUPS.flatMap((group) => {
    const members = byKind.get(group.kind);
    return members?.length ? [{ ...group, ids: members }] : [];
  });
}
