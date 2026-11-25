import type {
  PilotAgentRequest, PilotAgentResult, PilotCatalogue, PilotDataset, PilotDatasetEntry, PilotPipeDetail, PilotReplayResult,
} from "../../shared/contracts/platform";
import { GasApiError, gasHttpError, type GasTransport } from "./engineeringApi";

const root = "/api/v1/gas/engineering/pilot/datasets";
export type ReplaySelection = { sensor_id: string; start?: string; end?: string };

export function pilotVersionKey(entry: PilotDatasetEntry) {
  return JSON.stringify([entry.dataset_id, entry.version, entry.data_mode, entry.manifest_sha256]);
}

const record = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const key = (value: unknown) => typeof value === "string" && /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(value);
function entry(value: unknown): value is PilotDatasetEntry {
  return record(value) && key(value.dataset_id) && key(value.version)
    && typeof value.manifest_sha256 === "string" && /^[a-f0-9]{64}$/.test(value.manifest_sha256)
    && ["synthetic", "real_authorized"].includes(String(value.data_mode))
    && ["ready", "partial"].includes(String(value.quality_status));
}
function identity(value: unknown, expected: PilotDatasetEntry): value is Record<string, unknown> {
  return record(value) && value.dataset_id === expected.dataset_id && value.version === expected.version
    && value.data_mode === expected.data_mode && value.manifest_sha256 === expected.manifest_sha256;
}
function path(dataset: PilotDatasetEntry) {
  return root + "/" + encodeURIComponent(dataset.dataset_id) + "/versions/" + encodeURIComponent(dataset.version);
}

export class GasPilotApi {
  constructor(private readonly transport: GasTransport) {}

  private async request<T>(method: "GET" | "POST", url: string, signal: AbortSignal, valid: (value: unknown) => boolean, body?: unknown) {
    const response = await this.transport.send({ method, path: url, signal, body });
    if (response.status === 409) throw new GasApiError("http", "当前试点数据集或阈值版本失效，请重新选择。", 409);
    if (response.status !== 200) throw gasHttpError(response.status, response.body);
    if (!valid(response.body)) throw new GasApiError("invalid_response", "试点响应与当前数据集或版本不匹配。");
    return response.body as T;
  }

  catalogue(signal: AbortSignal) {
    return this.request<PilotCatalogue>("GET", root, signal, (value) =>
      record(value) && typeof value.configured === "boolean" && Array.isArray(value.items) && value.items.every(entry)
      && (value.rejections === undefined || Array.isArray(value.rejections)));
  }

  dataset(dataset: PilotDatasetEntry, signal: AbortSignal) {
    return this.request<PilotDataset>("GET", path(dataset), signal,
      (value) => identity(value, dataset) && Array.isArray(value.sensor_records) && value.engineering_analysis === "unavailable"
        && record(value.quality) && record(value.access) && value.access.status === (dataset.data_mode === "synthetic" ? "synthetic" : "authorized"));
  }

  replay(dataset: PilotDatasetEntry, selected: ReplaySelection, signal: AbortSignal) {
    const params = new URLSearchParams({ sensor_id: selected.sensor_id });
    if (selected.start && selected.end) {
      params.set("start", selected.start);
      params.set("end", selected.end);
    }
    return this.request<PilotReplayResult>("GET", path(dataset) + "/replay?" + params, signal, (value) =>
      identity(value, dataset) && record(value.sensor) && value.sensor.sensor_id === selected.sensor_id
      && value.sensor.pipe_id === value.pipe_id && Array.isArray(value.observations) && Array.isArray(value.segments)
      && Array.isArray(value.related_events) && record(value.validation));
  }

  pipe(dataset: PilotDatasetEntry, pipeId: string, at: string, signal: AbortSignal) {
    const params = new URLSearchParams({ at });
    return this.request<PilotPipeDetail>("GET", path(dataset) + "/pipes/" + encodeURIComponent(pipeId) + "?" + params, signal,
      (value) => identity(value, dataset) && record(value.pipe) && value.pipe.pipe_id === pipeId && Array.isArray(value.geometries_wgs84));
  }

  query(dataset: PilotDatasetEntry, query: PilotAgentRequest, pipeId: string, signal: AbortSignal) {
    return this.request<PilotAgentResult>("POST", path(dataset) + "/agent/query", signal,
      (value) => identity(value, dataset) && value.observation_id === query.observation_id
        && value.asset_id === pipeId && typeof value.answer === "string"
        && record(value.evidence_scope) && value.evidence_scope.scope === "pilot_history"
        && value.evidence_scope.dataset_id === dataset.dataset_id && value.evidence_scope.dataset_version === dataset.version
        && value.evidence_scope.data_mode === dataset.data_mode && value.evidence_scope.source_sha256 === dataset.manifest_sha256
        && (!query.binding || value.threshold_version === query.binding.threshold_version)
        && Array.isArray(value.evidence) && ["qwen_evidence_selection", "local_rule_not_llm"].includes(String(value.parser_source)), query);
  }
}
