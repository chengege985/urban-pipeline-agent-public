import { describe, expect, it } from "vitest";
import { groupGasRecordIds, type GasRecordIdentity } from "../features/gas/assetRecords";

describe("event asset grouping", () => {
  it("uses case records, preserves the original order within a group, and counts each ID once", () => {
    const index = new Map<string, GasRecordIdentity>([
      ["pipe-looking-node", { kind: "topology_node", label: "拓扑节点" }],
      ["synthetic-pipe-2", { kind: "pipe_asset", label: "管段资产" }],
      ["synthetic-pipe-1", { kind: "pipe_asset", label: "管段资产" }],
      ["valve-record", { kind: "valve_asset", label: "阀门资产" }],
    ]);
    expect(groupGasRecordIds(index, [
      "valve-record", "synthetic-pipe-2", "unmapped-valve", "pipe-looking-node",
      "synthetic-pipe-1", "valve-record",
    ]).map(({ kind, ids }) => [kind, ids])).toEqual([
      ["pipe_asset", ["synthetic-pipe-2", "synthetic-pipe-1"]],
      ["valve_asset", ["valve-record"]],
      ["topology_node", ["pipe-looking-node"]],
      ["unknown", ["unmapped-valve"]],
    ]);
  });

  it("lists unclassified IDs while the case index is unavailable", () => {
    expect(groupGasRecordIds(null, ["syn-gas-pipe-K-001"])).toEqual([
      { kind: "unknown", label: "类别未确认", ids: ["syn-gas-pipe-K-001"] },
    ]);
    expect(groupGasRecordIds(null, [])).toEqual([]);
  });
});
