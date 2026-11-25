import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import type {
  GasCuvCollaborationRequest, GasCuvCollaborationSnapshot, GasCuvCriteria, GasCuvDraftRequest,
  GasCuvQueryBinding, GasObservationRef,
} from "../../shared/contracts/platform";
import {
  cuvCriteriaDraft, cuvObjectives, cuvReasonLabels, prepareCuvConfirmation, type CuvCollaborationIdentity,
} from "./cuvCollaborationContract";
import { toGasError, type GasEngineeringApi } from "./engineeringApi";
import { useGasRead } from "./useGasRead";
import { gasObservationRef } from "../../shared/routing/gasRoutes";

type CollaborationApi = Pick<GasEngineeringApi, "cuvCollaboration" | "confirmCuvCollaboration">;
type Comparison = GasCuvCollaborationSnapshot["comparison"];
type Review = Omit<GasCuvCollaborationRequest, "confirmed">;

const eligibility = {
  eligible: "符合当前比较条件", excluded: "未通过约束", insufficient_data: "缺少判断所需数据",
};

function CriteriaDetails({ criteria }: { criteria: GasCuvCriteria }) {
  return <dl className="gas-fields gas-cuv-criteria">
    <div><dt>比较目标</dt><dd>{cuvObjectives[criteria.objective]}</dd></div>
    <div><dt>新增图不可达服务峰值上限</dt><dd>{criteria.max_peak_affected_services ?? "未设置"}</dd></div>
    <div><dt>图可达恢复时限</dt><dd>{criteria.restore_by_hours === null ? "未设置" : criteria.restore_by_hours + " h"}</dd></div>
  </dl>;
}

function ConfirmedComparison({ comparison }: { comparison: Comparison }) {
  return <section data-testid="gas-cuv-ranked-comparison" className="gas-cuv-ranked">
    <h5>已确认条件下的比较</h5>
    {comparison.status === "no_eligible_plan" && <p role="status">没有符合当前条件的方案。</p>}
    <ul>{comparison.plans.map((entry) => <li key={entry.plan_id}
      data-cuv-ranked-plan={entry.plan_id} data-cuv-eligibility={entry.status}>
      <strong>{entry.label}</strong> · {entry.rank === null ? eligibility[entry.status] : "顺位 " + entry.rank}
      <span className="gas-muted"> · 累计影响 {entry.affected_service_hours ?? "未知"} 服务对象·小时 · 图可达恢复 {entry.topology_restoration_hours === null
        ? "未知" : entry.topology_restoration_hours + " h"} · 影响峰值 {entry.peak_affected_service_count ?? "未知"}</span>
      {entry.reasons.length > 0 && <p>{entry.reasons.map((reason) => cuvReasonLabels[reason] ?? reason).join("、")}</p>}
    </li>)}</ul>
  </section>;
}

function CriteriaEditor({ snapshot, report, api, monitoringRef, onAskDraft, onSaved, onUncertain }: {
  snapshot: GasCuvCollaborationSnapshot;
  report: CuvCollaborationIdentity;
  api: CollaborationApi;
  monitoringRef?: GasObservationRef;
  onAskDraft?(draft: GasCuvDraftRequest): void;
  onSaved(value: GasCuvCollaborationSnapshot): void;
  onUncertain(message: string): void;
}) {
  const [draft, setDraft] = useState(() => cuvCriteriaDraft(snapshot.criteria));
  const [review, setReview] = useState<Review | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => () => {
    pending.current?.abort();
    pending.current = null;
  }, []);

  const proposal = prepareCuvConfirmation(draft, snapshot);
  function update<Key extends keyof typeof draft>(key: Key, value: (typeof draft)[Key]) {
    setDraft((current) => ({ ...current, [key]: value }));
    setReview(null);
  }

  function prepare(event: FormEvent) {
    event.preventDefault();
    if (proposal && !pending.current) setReview(proposal);
  }

  async function confirm() {
    if (!review || pending.current) return;
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    const deadline = window.setTimeout(() => controller.abort(), 12000);
    try {
      const updated = await api.confirmCuvCollaboration(report, { ...review, confirmed: true,
        ...(monitoringRef ? { monitoring_ref: monitoringRef } : {}) }, controller.signal);
      if (pending.current !== controller) return;
      if (controller.signal.aborted) {
        onUncertain("本次确认结果待核对。请读取最新版本与审计记录。");
      } else {
        onSaved(updated);
      }
    } catch (cause) {
      if (pending.current !== controller) return;
      const error = toGasError(cause);
      onUncertain(error.status === 409
        ? "推演输入或确认版本已更新。请读取最新对比后重新确认。"
        : "本次确认结果待核对。请读取最新版本与审计记录。");
    } finally {
      window.clearTimeout(deadline);
      if (pending.current === controller) {
        pending.current = null;
        setBusy(false);
      }
    }
  }

  return <div>
    <form onSubmit={prepare} className="gas-cuv-criteria-form" data-testid="gas-cuv-criteria-form">
      <fieldset disabled={busy}>
        <legend>调整比较条件</legend>
        <label>比较目标
          <select value={draft.objective} onChange={(event) => update("objective", event.target.value as GasCuvCriteria["objective"])}>
            {Object.entries(cuvObjectives).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </label>
        <label>新增图不可达服务峰值上限
          <input type="number" min={0} step={1} value={draft.peak} placeholder="未设置"
            onChange={(event) => update("peak", event.target.value)} />
        </label>
        <label>图可达恢复时限（小时）
          <input type="number" min={0} step="any" value={draft.deadline} placeholder="未设置"
            onChange={(event) => update("deadline", event.target.value)} />
        </label>
        <label>调整原因或反馈
          <input type="text" required maxLength={280} value={draft.feedback}
            onChange={(event) => update("feedback", event.target.value)} />
        </label>
        <button type="submit" disabled={!proposal} data-testid="gas-cuv-review">核对待确认条件</button>
      </fieldset>
    </form>
    {review && <section className="gas-cuv-review" data-testid="gas-cuv-confirmation" aria-label="待人工确认">
      <h5>待人工确认 · 基于版本 {review.expected_revision}</h5>
      <CriteriaDetails criteria={review.criteria} />
      <p>{review.feedback}</p>
      <div className="gas-cuv-links">
        <button type="button" disabled={busy} onClick={() => void confirm()} data-testid="gas-cuv-confirm">确认并重算对比</button>
        {onAskDraft && <button type="button" disabled={busy} data-testid="gas-cuv-ask-draft"
          onClick={() => onAskDraft({ expected_revision: review.expected_revision, input_sha256: review.input_sha256,
            criteria: review.criteria })}>让 Qwen 解释待确认差异</button>}
        <button type="button" disabled={busy} onClick={() => setReview(null)}>继续编辑</button>
      </div>
      {busy && <p role="status">正在校验版本并重算合成对比。</p>}
    </section>}
  </div>;
}

export function GasCuvCollaborationPanel({ report, api, monitoringRef, onOpenObservation, onAskDraft, onAskConfirmed }: {
  report: CuvCollaborationIdentity; api: CollaborationApi; monitoringRef?: GasObservationRef;
  onOpenObservation?(ref: GasObservationRef): void;
  onAskDraft?(draft: GasCuvDraftRequest): void;
  onAskConfirmed?(binding: GasCuvQueryBinding): void;
}) {
  const read = useCallback((signal: AbortSignal) => api.cuvCollaboration(report, signal), [api, report]);
  const resource = useGasRead(report.topology_evidence.replay_content_sha256, read);
  const [saved, setSaved] = useState<GasCuvCollaborationSnapshot | null>(null);
  const [notice, setNotice] = useState("");
  const [requiresRead, setRequiresRead] = useState(false);
  const snapshot = saved ?? resource.data;
  function reload() {
    setSaved(null);
    setRequiresRead(false);
    resource.retry();
  }
  function markUncertain(message: string) {
    setNotice(message);
    setRequiresRead(true);
    setSaved(null);
  }
  return <details className="gas-cuv-card gas-cuv-collaboration" data-testid="gas-cuv-collaboration"
    data-cuv-collaboration-revision={requiresRead ? undefined : snapshot?.revision}>
    <summary>人工调整目标与约束</summary>
    <p className="gas-window-note">条件只作用于合成图方案比较。实际供气等指标保持未知。</p>
    {resource.status === "loading" && <p role="status">正在读取已确认条件。</p>}
    {notice && <p role={requiresRead ? "alert" : "status"}>{notice}</p>}
    {resource.error && <p role="status">
      {resource.error.status === 404 ? "当前入口未启用人工重算。"
        : resource.error.status === 403 ? "当前身份缺少人工重算权限。" : resource.error.message}
    </p>}
    {resource.status !== "loading" && <button type="button" onClick={reload}>读取最新条件与审计</button>}
    {snapshot && !requiresRead && <>
      <h5>已确认条件 · 版本 {snapshot.revision}</h5>
      <CriteriaDetails criteria={snapshot.criteria} />
      <ConfirmedComparison comparison={snapshot.comparison} />
      {onAskConfirmed && snapshot.audit.at(-1)?.input_sha256 === snapshot.input_sha256 && <button type="button"
        data-testid="gas-cuv-ask-confirmed" onClick={() => onAskConfirmed({
          input_sha256: snapshot.input_sha256, expected_revision: snapshot.revision,
        })}>让 Qwen 解释已确认变化</button>}
      <CriteriaEditor key={[snapshot.input_sha256, snapshot.revision, monitoringRef?.observation_id].join(":")}
        snapshot={snapshot} report={report} api={api} monitoringRef={monitoringRef} onAskDraft={onAskDraft}
        onSaved={(value) => { setSaved(value); setNotice("已确认并重算对比，记录已写入审计。"); }}
        onUncertain={markUncertain} />
      <details data-testid="gas-cuv-collaboration-audit">
        <summary>确认与反馈记录（{snapshot.audit.length}）</summary>
        <p className="gas-window-note">{snapshot.persistence === "sqlite_local" ? "记录存于本机合成演示库。"
          : "记录存于当前合成演示进程。"}</p>
        {snapshot.audit.length === 0 ? <p>尚无人工确认记录。</p> : <ol>{[...snapshot.audit].reverse().map((entry) => (
          <li key={entry.revision} data-cuv-audit-revision={entry.revision}>
            <p>版本 {entry.revision} · {entry.actor_id} · <time dateTime={entry.occurred_at}>{entry.occurred_at}</time></p>
            <p>{entry.feedback}</p>
            {gasObservationRef(entry.monitoring_ref) && onOpenObservation && <button type="button"
              data-testid="gas-cuv-audit-observation" onClick={() => onOpenObservation(entry.monitoring_ref!)}>
              返回原始观测与资产
            </button>}
            <details>
              <summary>展开确认条件与输入摘要</summary>
              <h6>此前条件</h6>
              <CriteriaDetails criteria={entry.previous_criteria} />
              <h6>确认后的条件</h6>
              <CriteriaDetails criteria={entry.criteria} />
              <p>输入 SHA-256 <code>{entry.input_sha256}</code></p>
              <ConfirmedComparison comparison={entry.comparison} />
            </details>
          </li>
        ))}</ol>}
      </details>
    </>}
  </details>;
}
