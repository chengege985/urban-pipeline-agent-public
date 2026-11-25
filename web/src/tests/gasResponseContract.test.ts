import { describe, expect, it } from "vitest";
import fixture from "./fixtures/gasCaseResponse.json";
import { parseGasCaseResponse, type GasContractContext } from "../features/gas/contract";
import { GasApiError } from "../features/gas/errors";

const validFixture = fixture as unknown as Record<string, unknown>;
const context: GasContractContext = {
  requestId: "gas-test-request",
  status: 200,
  path: "/api/v1/gas/cases/syn-gas-case-K-001",
};

function clone(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(validFixture)) as Record<string, unknown>;
}

function contractError(value: unknown): GasApiError {
  try {
    parseGasCaseResponse(value, context);
  } catch (caught) {
    expect(caught).toBeInstanceOf(GasApiError);
    expect((caught as GasApiError).kind).toBe("contract_error");
    return caught as GasApiError;
  }
  throw new Error("expected a GasApiError");
}

describe("parseGasCaseResponse: 85-schema runtime boundary", () => {
  it("accepts the real 54-node / 73-edge candidate response", () => {
    const parsed = parseGasCaseResponse(validFixture, context);
    expect(parsed.schema_version).toBe("gas-case-view-0.1.0");
    expect(parsed.nodes).toHaveLength(54);
    expect(parsed.edges).toHaveLength(73);
    expect(parsed.services).toHaveLength(46);
    expect(parsed.timeline_events).toHaveLength(3);
    expect(parsed.counts).toMatchObject({ nodes: 54, edges: 73, services: 46 });
    expect(parsed.isolation_plan.disabled_edge_ids).toHaveLength(7);
    expect(parsed.scoring.business_validation).toBe("not_business_validated");
  });

  it("allows a contractually empty array but never fills a missing one", () => {
    const empty = clone();
    empty.services = [];
    (empty.counts as Record<string, unknown>).services = 0;
    const states = [empty.current as Record<string, unknown>, ...(empty.timeline_events as Array<Record<string, unknown>>)];
    for (const state of states) {
      state.services = [];
      state.newly_affected_service_ids = [];
    }
    expect(parseGasCaseResponse(empty, context).services).toEqual([]);

    const missing = clone();
    delete missing.services;
    const error = contractError(missing);
    expect(error.status).toBe(200);
    expect(error.issues.some((issue) => issue.path === "services" && issue.actual === "missing")).toBe(true);
    expect(missing).not.toHaveProperty("services");
  });

  it("rejects invalid nested service, edge, and isolation fields", () => {
    const broken = clone();
    (broken.services as Array<Record<string, unknown>>)[0].node_ids = "syn-gas-node-N02";
    expect(contractError(broken).issues.some((issue) => issue.path === "services[0].node_ids")).toBe(true);

    const badEdge = clone();
    (badEdge.edges as Array<Record<string, unknown>>)[0].direction = "physical_flow";
    expect(contractError(badEdge).issues.some((issue) => issue.path === "edges[0].direction")).toBe(true);

    const badPlan = clone();
    delete (badPlan.isolation_plan as Record<string, unknown>).disabled_edge_ids;
    expect(contractError(badPlan).issues.some((issue) => issue.path === "isolation_plan.disabled_edge_ids")).toBe(true);
  });

  it("validates counts, optional nullable fields, metric enum and safety literals", () => {
    const badCounts = clone();
    (badCounts.counts as Record<string, unknown>).nodes = "54";
    expect(contractError(badCounts).issues.some((issue) => issue.path === "counts.nodes")).toBe(true);

    const optional = clone();
    const firstNode = (optional.nodes as Array<Record<string, unknown>>)[0];
    delete firstNode.name;
    expect(parseGasCaseResponse(optional, context).nodes).toHaveLength(54);

    const badMetric = clone();
    (badMetric.metrics as Record<string, Record<string, unknown>>).timeline_events.status = "success";
    expect(contractError(badMetric).issues.some((issue) => issue.path === "metrics.timeline_events.status")).toBe(true);

    const badSafety = clone();
    (badSafety.safety as Record<string, unknown>).simulation_only = false;
    expect(contractError(badSafety).issues.some((issue) => issue.path === "safety.simulation_only")).toBe(true);
  });

  it("rejects unknown nested fields and cross-collection dangling references", () => {
    const extra = clone();
    (extra.nodes as Array<Record<string, unknown>>)[0].unexpected = true;
    expect(contractError(extra).issues.some((issue) => issue.path === "nodes[0].unexpected")).toBe(true);

    const dangling = clone();
    (dangling.edges as Array<Record<string, unknown>>)[0].to_node_id = "syn-gas-node-UNKNOWN";
    expect(contractError(dangling).issues.some((issue) => issue.path === "edges[0].to_node_id")).toBe(true);
  });

  it("rejects identity tampering and non-object payloads", () => {
    const broken = clone();
    broken.data_mode = "live";
    expect(contractError(broken).issues.some((issue) => issue.path === "data_mode")).toBe(true);
    expect(contractError([]).issues).toEqual([{ path: "$", expected: "object", actual: "array(0)" }]);
  });
});
