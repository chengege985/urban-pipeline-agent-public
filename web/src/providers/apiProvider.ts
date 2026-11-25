import type {
  AgentContext,
  AgentResponse,
  DemoConstraints,
  HealthAssessment,
  Pipe,
  PipeOverride,
  PlanComparison,
  PlanTimelineResult,
  ProviderMode,
  ServiceImpact,
  TimelineGate,
  TimelineRequest,
  WaterPulseDataset,
} from "../domain/types";
import { ProviderUnavailableError, type WaterPulseProvider } from "./types";

export class ApiRequestError extends Error {
  constructor(public readonly status: number, public readonly code: string, public readonly detail: string) {
    super(`${code}（HTTP ${status}）${detail ? `：${detail}` : ""}`);
    this.name = "ApiRequestError";
  }
}

function safeDetail(value: unknown): string {
  if (typeof value === "string") {
    // Do not echo diagnostic paths, secrets or traces from a misconfigured API.
    if (/traceback|stack|api.?key|token|password|secret|bearer|\/workdata|\/root\/|\/home\//i.test(value)) return "";
    return value.replace(/[\u0000-\u001f]/g, " ").slice(0, 300);
  }
  if (Array.isArray(value)) return value.slice(0, 8).map((item) => {
    if (!item || typeof item !== "object") return "";
    const row = item as Record<string, unknown>;
    return [Array.isArray(row.loc) ? safeDetail(row.loc.map(String).join(".")) : "", safeDetail(row.msg)].filter(Boolean).join(": ");
  }).filter(Boolean).join("；");
  return "";
}
const PROBE_TIMEOUT_MS = 4000;
const CALCULATION_TIMEOUT_MS = 15000;
const ASSISTANT_TIMEOUT_MS = 180000;

export class ApiProvider implements WaterPulseProvider {
  private ready = false;
  private readonly baseUrl: string;

  constructor(baseUrl: string, private readonly onUnavailable?: (message: string) => void) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  get mode(): ProviderMode {
    return this.ready ? "live" : "unavailable";
  }

  async probe(): Promise<boolean> {
    if (!this.baseUrl) return false;
    try {
      await this.request("/api/v1/demo/meta", { method: "GET" }, PROBE_TIMEOUT_MS);
      this.ready = true;
      return this.ready;
    } catch {
      this.ready = false;
      return false;
    }
  }

  private async request<T>(path: string, init: RequestInit = {}, timeoutMs = CALCULATION_TIMEOUT_MS): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          ...(init.headers ?? {}),
        },
      });
      if (!response.ok) {
        if (response.status < 500) {
          const labels: Record<number, string> = { 401: "需要登录", 403: "无权访问", 404: "对象不存在", 409: "请求状态冲突", 413: "请求数据过大", 422: "输入校验失败", 429: "请求过于频繁" };
          const body = await response.json().catch(() => null) as { detail?: unknown } | null;
          throw new ApiRequestError(response.status, labels[response.status] ?? "请求被拒绝", safeDetail(body?.detail));
        }
        throw new ProviderUnavailableError(`接口返回 HTTP ${response.status}。`);
      }
      return (await response.json()) as T;
    } catch (caught) {
      // A rejected business input is not a disconnected service.
      if (caught instanceof ApiRequestError) throw caught;
      this.ready = false;
      const message = controller.signal.aborted ? "请求超时，请重试或显式切换至合成演示模式。" : "实时接口不可用，请重试或显式切换至合成演示模式。";
      this.onUnavailable?.(message);
      throw new ProviderUnavailableError(message);
    } finally {
      clearTimeout(timer);
    }
  }

  private async json<T>(path: string, init: RequestInit = {}, timeoutMs = CALCULATION_TIMEOUT_MS): Promise<T> {
    if (!this.ready) throw new ProviderUnavailableError();
    return this.request<T>(path, init, timeoutMs);
  }

  async loadDataset(): Promise<WaterPulseDataset> {
    const dataset = await this.json<WaterPulseDataset>("/api/v1/demo/dataset");
    return { ...dataset, meta: { ...dataset.meta, mode: "live" } };
  }

  async evaluate(pipe: Pipe, override: PipeOverride = {}): Promise<HealthAssessment> {
    return this.json<HealthAssessment>("/api/v1/demo/evaluate", {
      method: "POST",
      body: JSON.stringify({ pipe, override }),
    });
  }

  async simulate(scenarioId: string, isolationPlanId?: string): Promise<ServiceImpact> {
    return this.json<ServiceImpact>("/api/v1/demo/simulate", {
      method: "POST",
      body: JSON.stringify({ scenarioId, isolationPlanId }),
    });
  }

  async comparePlans(
    scenarioId: string,
    planIds?: string[],
    constraints?: DemoConstraints,
    isolationPlanId?: string,
  ): Promise<PlanComparison> {
    return this.json<PlanComparison>("/api/v1/demo/compare", {
      method: "POST",
      body: JSON.stringify({ scenarioId, planIds, constraints, isolationPlanId }),
    });
  }

  async checkTimeline(request: TimelineRequest): Promise<TimelineGate> {
    return this.json<TimelineGate>("/api/v1/demo/timeline/check", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async timeline(request: TimelineRequest): Promise<PlanTimelineResult> {
    return this.json<PlanTimelineResult>("/api/v1/demo/timeline", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async ask(question: string, context: AgentContext): Promise<AgentResponse> {
    return this.json<AgentResponse>("/api/v1/demo/assistant", {
      method: "POST",
      body: JSON.stringify({ question, context }),
    }, ASSISTANT_TIMEOUT_MS);
  }
}
