/** 平台 HTTP 传输层：只负责发请求，不解释业务字段。 */
export interface TransportRequest {
  method: "GET" | "POST";
  path: string;
  body?: unknown;
  idempotencyKey?: string;
  /** Optional caller cancellation; the shared transport owns the timeout signal. */
  signal?: AbortSignal;
}

export interface TransportResponse {
  status: number;
  body: unknown;
}

export interface PlatformTransport {
  send(request: TransportRequest): Promise<TransportResponse>;
}

export class PlatformTransportError extends Error {
  constructor(public readonly status: number, public readonly detail: string, message: string) {
    super(message);
    this.name = "PlatformTransportError";
  }
}

const CLIENT_HEADER = "unified-react";
const SAFE_DETAIL = /traceback|stack|api.?key|token|password|secret|bearer|\/workdata|\/root\/|\/home\//i;

/** 不泄露诊断路径、凭据或堆栈；只保留可读的业务拒绝原因。 */
export function safeDetail(value: unknown): string {
  if (typeof value === "string") {
    if (SAFE_DETAIL.test(value)) return "";
    return value.replace(/[\u0000-\u001f]/g, " ").slice(0, 300);
  }
  if (Array.isArray(value)) {
    return value
      .slice(0, 8)
      .map((item) => {
        if (!item || typeof item !== "object") return "";
        const row = item as Record<string, unknown>;
        const field = Array.isArray(row.field) ? row.field.map(String).join(".") : "";
        const reason = typeof row.reason === "string" ? row.reason : "";
        return [field, reason].filter(Boolean).join(": ");
      })
      .filter(Boolean)
      .join("；");
  }
  return "";
}

export class FetchTransport implements PlatformTransport {
  private readonly baseUrl: string;

  constructor(baseUrl: string, private readonly timeoutMs = 15000) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async send(request: TransportRequest): Promise<TransportResponse> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);
    const signal = request.signal ? combinedSignal(request.signal, controller.signal) : controller.signal;
    const headers: Record<string, string> = {
      Accept: "application/json",
      "X-Water-Client": CLIENT_HEADER,
    };
    if (request.body !== undefined) headers["Content-Type"] = "application/json";
    if (request.idempotencyKey) headers["Idempotency-Key"] = request.idempotencyKey;
    try {
      const response = await fetch(`${this.baseUrl}${request.path}`, {
        method: request.method,
        headers,
        signal,
        body: request.body === undefined ? undefined : JSON.stringify(request.body),
      });
      const body = await response.json().catch(() => null);
      return { status: response.status, body };
    } finally {
      clearTimeout(timer);
    }
  }
}

function combinedSignal(external: AbortSignal, timeout: AbortSignal): AbortSignal {
  if (external.aborted) return external;
  const controller = new AbortController();
  const abort = () => controller.abort();
  external.addEventListener("abort", abort, { once: true });
  timeout.addEventListener("abort", abort, { once: true });
  return controller.signal;
}

type MockHandler = (request: TransportRequest) => TransportResponse | Promise<TransportResponse>;

/**
 * 契约测试用传输层：按 `METHOD path` 精确匹配，记录调用顺序。
 * 未登记的路由直接失败，避免测试悄悄落到真实网络。
 */
export class MockTransport implements PlatformTransport {
  readonly calls: TransportRequest[] = [];

  constructor(private readonly routes: Record<string, MockHandler | unknown>) {}

  async send(request: TransportRequest): Promise<TransportResponse> {
    this.calls.push(request);
    const key = `${request.method} ${request.path}`;
    const route = this.routes[key];
    if (route === undefined) throw new Error(`MockTransport 未登记路由：${key}`);
    if (typeof route === "function") return (route as MockHandler)(request);
    return { status: 200, body: route };
  }
}
