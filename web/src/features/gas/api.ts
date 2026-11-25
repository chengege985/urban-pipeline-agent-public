import type { GasCaseResponse } from "./types";
import { GasApiError } from "./errors";
import { parseGasCaseResponse } from "./contract";
import { FetchTransport, safeDetail } from "../../shared/api/platformTransport";
import type { PlatformTransport, TransportResponse } from "../../shared/api/platformTransport";

export const DEFAULT_GAS_CASE_ID = "syn-gas-case-K-001";

export interface FetchGasCaseOptions {
  transport?: PlatformTransport;
  requestId?: string;
  signal?: AbortSignal;
}

export interface GasCaseLoadResult {
  data: GasCaseResponse;
  requestId: string;
  status: number;
  path: string;
}

export function createGasRequestId(): string {
  const cryptoApi = globalThis.crypto;
  if (cryptoApi && typeof cryptoApi.randomUUID === "function") return `gas-${cryptoApi.randomUUID()}`;
  return `gas-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(value: unknown): boolean {
  return isRecord(value) && (value.name === "AbortError" || value.code === 20);
}

function responseDetail(body: unknown): string {
  return isRecord(body) && "detail" in body ? safeDetail(body.detail) : "";
}

function httpFailureMessage(status: number): string {
  if (status === 401) return "燃气候选接口未认证（HTTP 401）。";
  if (status === 403) return "当前主体无权读取燃气候选数据（HTTP 403）。";
  if (status === 404) return "请求的燃气合成案例不存在（HTTP 404）。";
  if (status === 503) return "燃气合成候选服务暂不可用（HTTP 503）。";
  if (status >= 500) return `燃气候选服务暂不可用（HTTP ${status}）。`;
  return `燃气接口返回 HTTP ${status}。`;
}

/**
 * The only gas request entry. Authentication, same-origin policy, timeout and
 * headers remain owned by the shared platform transport. This function owns
 * response mapping and the runtime contract boundary.
 */
export async function fetchGasCase(
  baseUrl: string,
  caseId = DEFAULT_GAS_CASE_ID,
  options: FetchGasCaseOptions = {},
): Promise<GasCaseLoadResult> {
  const requestId = options.requestId ?? createGasRequestId();
  const path = `/api/v1/gas/cases/${encodeURIComponent(caseId)}`;
  const transport = options.transport ?? new FetchTransport(baseUrl);
  if (options.signal?.aborted) {
    throw new GasApiError({
      kind: "unavailable",
      code: "cancelled",
      message: "燃气候选请求已取消。",
      status: null,
      requestId,
      path,
    });
  }

  let response: TransportResponse;
  try {
    response = await transport.send(options.signal
      ? { method: "GET", path, signal: options.signal }
      : { method: "GET", path });
  } catch (caught) {
    const cancelled = Boolean(options.signal?.aborted);
    const timeout = !cancelled && isAbortError(caught);
    throw new GasApiError({
      kind: "unavailable",
      code: cancelled ? "cancelled" : timeout ? "timeout" : "network_error",
      message: cancelled
        ? "燃气候选请求已取消。"
        : timeout
          ? "燃气候选请求超时，未显示任何缓存或演示数据。"
          : "燃气候选网络请求失败，未显示任何缓存或演示数据。",
      status: null,
      requestId,
      path,
    });
  }

  if (response.status < 200 || response.status >= 300) {
    throw new GasApiError({
      kind: "unavailable",
      code: `http_${response.status}`,
      message: httpFailureMessage(response.status),
      status: response.status,
      requestId,
      path,
      detail: responseDetail(response.body),
    });
  }

  return {
    data: parseGasCaseResponse(response.body, { requestId, status: response.status, path }),
    requestId,
    status: response.status,
    path,
  };
}
