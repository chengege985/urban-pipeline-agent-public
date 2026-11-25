import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type { GasDispositionSnapshot, GasDispositionTransition, GasObservationRef } from "../../shared/contracts/platform";
import { BrowserGasTransport, GasApiError, gasHttpError, toGasError } from "./engineeringApi";
import { useGasRead } from "./useGasRead";
import { gasObservationRef } from "../../shared/routing/gasRoutes";

type Status = GasDispositionSnapshot["status"];

const statusLabels: Record<Status, string> = {
  pending: "待确认",
  acknowledged: "已确认",
  in_progress: "处理中",
  completed: "已完成",
};
const actions: Record<Status, string> = {
  pending: "提交待确认",
  acknowledged: "确认事件",
  in_progress: "开始处理",
  completed: "完成处置",
};
const transport = new BrowserGasTransport();

function status(value: unknown): value is Status {
  return typeof value === "string" && Object.hasOwn(statusLabels, value);
}

function snapshot(value: unknown, eventId: string): value is GasDispositionSnapshot {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const data = value as Record<string, unknown>;
  return data.event_id === eventId && data.data_mode === "synthetic" && status(data.status)
    && typeof data.revision === "number" && Number.isSafeInteger(data.revision) && data.revision >= 0
    && (data.next_status === null || status(data.next_status))
    && Array.isArray(data.audit) && data.audit.every((item) =>
      !!item && item.event_id === eventId && typeof item.actor_id === "string"
      && typeof item.occurred_at === "string" && status(item.from_status) && status(item.to_status)
      && typeof item.reason === "string" && Number.isSafeInteger(item.revision)
      && (item.monitoring_ref == null || gasObservationRef(item.monitoring_ref)));
}

async function request(eventId: string, signal: AbortSignal, body?: GasDispositionTransition): Promise<GasDispositionSnapshot | null> {
  const res = await transport.send({
    method: body ? "POST" : "GET",
    path: "/api/v1/gas/engineering/events/" + encodeURIComponent(eventId) + "/disposition",
    signal,
    body,
  });
  if (!body && res.status === 404) {
    if (typeof res.body === "object" && res.body !== null && !Array.isArray(res.body)
      && (res.body as Record<string, unknown>).detail === "gas_disposition_disabled") {
      throw gasHttpError(res.status, res.body);
    }
    return null;
  }
  if (res.status === 409) throw new GasApiError("http", "事件状态已变化，请按最新状态重新核对。", 409);
  if (res.status !== 200) throw gasHttpError(res.status, res.body);
  if (!snapshot(res.body, eventId)) throw new GasApiError("invalid_response", "处置响应校验失败。");
  return res.body;
}

export function GasDispositionPanel({ eventId, monitoringRef, onOpenObservation }: {
  eventId: string; monitoringRef?: GasObservationRef; onOpenObservation?(ref: GasObservationRef): void;
}) {
  const fetchState = useCallback((signal: AbortSignal) => request(eventId, signal), [eventId]);
  const resource = useGasRead(eventId, fetchState);
  const [posted, setPosted] = useState<GasDispositionSnapshot | null>(null);
  const [reason, setReason] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => {
    pending.current?.abort();
    pending.current = null;
  }, [eventId]);
  const state = posted?.event_id === eventId ? posted : resource.data;
  const next = state?.next_status;
  const canSubmit = reason.trim().length > 0 && !/\p{C}/u.test(reason);

  async function transition(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!state || !next || !canSubmit || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    setNotice("");
    const timer = setTimeout(() => controller.abort(), 10000);
    try {
      const updated = await request(eventId, controller.signal, {
        expected_revision: state.revision, target_status: next, reason: reason.trim(),
        ...(monitoringRef ? { monitoring_ref: monitoringRef } : {}),
      });
      if (!controller.signal.aborted) {
        setPosted(updated);
        setReason("");
      }
    } catch (cause) {
      if (pending.current !== controller) return;
      const error = toGasError(cause);
      setPosted(null);
      setNotice(error.status === 409 ? error.message : "提交未确认。请核对最新状态再操作。");
      resource.retry();
    } finally {
      clearTimeout(timer);
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  if (!state && !resource.error && !notice) return null;
  return <section className="gas-event-disposition" data-testid="gas-disposition-panel" data-disposition-state={state?.status}>
    <h3>人工处置演示</h3>
    <p className="gas-window-note">GAS-M0 合成流程。演示进程重启后清空，只记录人工确认的状态。</p>
    {notice ? <p role="alert">{notice}</p> : null}
    {resource.error ? <div>
      <p role="status">{[401, 403].includes(resource.error.status ?? 0)
        ? "当前身份没有处置权限。"
        : resource.error.code === "unavailable"
          ? resource.error.message
          : "暂时无法读取处置状态。"}</p>
      <button type="button" onClick={resource.retry}>重新读取处置状态</button>
    </div> : null}
    {state ? <>
      {posted && <p role="status">已记录{statusLabels[posted.status]}，处置版本 {posted.revision}。</p>}
      <dl className="gas-fields">
        <div><dt>处置状态</dt><dd data-testid="gas-disposition-status">{statusLabels[state.status]}</dd></div>
        <div><dt>版本</dt><dd>{state.revision}</dd></div>
      </dl>
      {next ? <form className="gas-agent-input-form" onSubmit={(event) => void transition(event)}>
        <label>操作说明
          <input type="text" value={reason} onChange={(event) => setReason(event.target.value)}
            maxLength={280} required disabled={busy} />
        </label>
        <button type="submit" disabled={busy || !canSubmit} data-testid="gas-disposition-transition">{actions[next]}</button>
        {busy ? <p role="status">正在校验并提交处置状态。</p> : null}
      </form> : null}
      {state.audit.length > 0 ? <details data-testid="gas-disposition-audit">
        <summary>处置记录（{state.audit.length}）</summary>
        <ol>{state.audit.map((entry) => <li key={entry.revision}>
          <p>{statusLabels[entry.from_status]} → {statusLabels[entry.to_status]} · 版本 {entry.revision} · {entry.actor_id}</p>
          <time dateTime={entry.occurred_at}>{entry.occurred_at}</time>
          <p>{entry.reason}</p>
          {gasObservationRef(entry.monitoring_ref) && onOpenObservation && <button type="button"
            data-testid="gas-disposition-audit-observation" onClick={() => onOpenObservation(entry.monitoring_ref!)}>
            返回原始观测与资产
          </button>}
        </li>)}</ol>
      </details> : null}
    </> : null}
  </section>;
}
