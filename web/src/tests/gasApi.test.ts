import { describe, expect, it } from "vitest";
import fixture from "./fixtures/gasCaseResponse.json";
import {
  createGasRequestId,
  DEFAULT_GAS_CASE_ID,
  fetchGasCase,
} from "../features/gas/api";
import type { PlatformTransport, TransportRequest, TransportResponse } from "../shared/api/platformTransport";
import { GasApiError } from "../features/gas/errors";

const validFixture = fixture as unknown as Record<string, unknown>;
const PATH = `/api/v1/gas/cases/${DEFAULT_GAS_CASE_ID}`;

function clone(): Record<string, unknown> {
  return JSON.parse(JSON.stringify(validFixture)) as Record<string, unknown>;
}

function transportFor(response: TransportResponse, calls?: TransportRequest[]): PlatformTransport {
  return {
    send: async (request) => {
      calls?.push(request);
      return response;
    },
  };
}

async function failureOf(promise: Promise<unknown>): Promise<GasApiError> {
  try {
    await promise;
  } catch (caught) {
    expect(caught).toBeInstanceOf(GasApiError);
    return caught as GasApiError;
  }
  throw new Error("expected a GasApiError");
}

describe("fetchGasCase: shared transport + controlled failures", () => {
  it("accepts the real 54-node 85-schema response and forwards the shared request", async () => {
    const calls: TransportRequest[] = [];
    const transport = transportFor({ status: 200, body: validFixture }, calls);
    const result = await fetchGasCase("", DEFAULT_GAS_CASE_ID, { transport, requestId: "gas-request-ok" });
    expect(result.requestId).toBe("gas-request-ok");
    expect(result.status).toBe(200);
    expect(result.path).toBe(PATH);
    expect(result.data.nodes).toHaveLength(54);
    expect(result.data.services).toHaveLength(46);
    expect(calls).toEqual([{ method: "GET", path: PATH }]);
  });

  it("maps 401/403/404/503 to unavailable with safe details and request identity", async () => {
    for (const status of [401, 403, 404, 503]) {
      const transport = transportFor({ status, body: { detail: `服务端拒绝 ${status}` } });
      const error = await failureOf(fetchGasCase("", DEFAULT_GAS_CASE_ID, {
        transport,
        requestId: `gas-request-${status}`,
      }));
      expect(error.kind).toBe("unavailable");
      expect(error.code).toBe(`http_${status}`);
      expect(error.status).toBe(status);
      expect(error.path).toBe(PATH);
      expect(error.requestId).toBe(`gas-request-${status}`);
      expect(error.message).toContain(String(status));
      expect(error.detail).toBe(`服务端拒绝 ${status}`);
    }
  });

  it("does not leak absolute paths or credentials from error details", async () => {
    const transport = transportFor({ status: 403, body: { detail: "/workdata/secret token=abc" } });
    const error = await failureOf(fetchGasCase("", DEFAULT_GAS_CASE_ID, { transport }));
    expect(error.kind).toBe("unavailable");
    expect(error.detail).toBe("");
  });

  it("keeps HTTP 200 with malformed structure as contract_error", async () => {
    const broken = clone();
    delete broken.services;
    const transport = transportFor({ status: 200, body: broken });
    const error = await failureOf(fetchGasCase("", DEFAULT_GAS_CASE_ID, {
      transport,
      requestId: "gas-request-contract",
    }));
    expect(error.kind).toBe("contract_error");
    expect(error.status).toBe(200);
    expect(error.code).toBe("contract_error");
    expect(error.requestId).toBe("gas-request-contract");
    expect(error.issues.some((issue) => issue.path === "services" && issue.actual === "missing")).toBe(true);
  });

  it("maps network failures and timeout without inventing a status", async () => {
    const network = await failureOf(fetchGasCase("", DEFAULT_GAS_CASE_ID, {
      transport: { send: async () => { throw new Error("offline"); } },
      requestId: "gas-request-network",
    }));
    expect(network.kind).toBe("unavailable");
    expect(network.code).toBe("network_error");
    expect(network.status).toBeNull();

    const timeoutTransport: PlatformTransport = {
      send: async () => {
        const error = new Error("aborted");
        error.name = "AbortError";
        throw error;
      },
    };
    const timeout = await failureOf(fetchGasCase("", DEFAULT_GAS_CASE_ID, { transport: timeoutTransport }));
    expect(timeout.code).toBe("timeout");
    expect(timeout.message).toContain("超时");
  });

  it("forwards caller cancellation to the shared transport and reports cancellation", async () => {
    const controller = new AbortController();
    const calls: TransportRequest[] = [];
    const transport = transportFor({ status: 200, body: validFixture }, calls);
    controller.abort();
    const error = await failureOf(fetchGasCase("", DEFAULT_GAS_CASE_ID, { transport, signal: controller.signal }));
    expect(error.code).toBe("cancelled");
    expect(calls).toEqual([]);
  });

  it("forwards a live cancellation signal to the shared transport", async () => {
    const controller = new AbortController();
    const calls: TransportRequest[] = [];
    const transport = transportFor({ status: 200, body: validFixture }, calls);
    await fetchGasCase("", DEFAULT_GAS_CASE_ID, { transport, signal: controller.signal });
    expect(calls[0]?.signal).toBe(controller.signal);
  });

  it("encodes case ids and generates a frontend-only request id", async () => {
    const calls: TransportRequest[] = [];
    const transport = transportFor({ status: 200, body: validFixture }, calls);
    const result = await fetchGasCase("", "case/with space", { transport });
    expect(result.path).toBe("/api/v1/gas/cases/case%2Fwith%20space");
    expect(calls[0].path).toBe("/api/v1/gas/cases/case%2Fwith%20space");
    expect(createGasRequestId()).toMatch(/^gas-/);
  });
});
