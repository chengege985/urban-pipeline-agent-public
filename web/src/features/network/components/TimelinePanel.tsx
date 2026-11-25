import { useEffect, useMemo, useRef, useState } from "react";
import { TIMELINE_ALGORITHM_VERSION, TIMELINE_DEFAULT_HORIZON_HOURS, TIMELINE_MAX_HORIZON_HOURS } from "../../../shared/contracts/constants";
import type {
  PlanCatalogEntry,
  PlanTimelineResult,
  ServiceImpact,
  TimeMetric,
  TimelineGate,
  TimelineRequest,
  WaterPulseDataset,
} from "../../../shared/contracts/domain";
import type { WaterPulseProvider } from "../../../shared/contracts/provider";
import { timelineBasisKey, timelineMatchesRequest } from "../../../shared/utils/timelineContext";
import { inputKey } from "../../../shared/utils/context";
import { EvidenceList } from "../../../shared/ui/EvidenceList";
import { StatusDot } from "../../../shared/ui/StatusPill";

export interface TimelineSnapshot {
  request: TimelineRequest;
  gate: TimelineGate;
  result: PlanTimelineResult;
}

interface TimelinePanelProps {
  dataset: WaterPulseDataset;
  provider: WaterPulseProvider;
  scenarioId: string;
  isolationPlanId: string;
  impact?: ServiceImpact;
  impactError?: string | null;
  planId?: string | null;
  onPlanChange?: (planId: string) => void;
  onResult?: (snapshot: TimelineSnapshot | null) => void;
}

export function metricText(metric: TimeMetric): string {
  if (metric.status === "already_available") return "0 h · 初始可达";
  if (metric.valueHours !== null) {
    return `${metric.valueHours} h${metric.beyondHorizon ? " · 超出窗口" : ""}`;
  }
  if (metric.status === "not_recovered_within_horizon") return "窗口内未恢复";
  if (metric.status === "not_applicable") return "不适用";
  return "待补数据";
}

export function metricTone(metric: TimeMetric): "ok" | "warn" | "bad" | "unknown" {
  if (metric.status === "succeeded" || metric.status === "already_available") return "ok";
  if (metric.status === "not_recovered_within_horizon") return "bad";
  if (metric.status === "not_applicable") return "warn";
  return "unknown";
}

function gateTone(status: TimelineGate["status"]): "ok" | "warn" | "bad" | "unknown" {
  if (status === "succeeded") return "ok";
  if (status === "partial") return "warn";
  if (status === "invalid_input" || status === "unsupported") return "bad";
  return "unknown";
}

function targetDefaults(dataset: WaterPulseDataset, impact?: ServiceImpact): string[] {
  const affected = impact?.affectedServices.map((service) => service.serviceId) ?? [];
  return affected.length ? affected : dataset.services.map((service) => service.id);
}

export function TimelinePanel({
  dataset,
  provider,
  scenarioId,
  isolationPlanId,
  impact,
  impactError,
  planId,
  onPlanChange,
  onResult,
}: TimelinePanelProps) {
  const planOptions = useMemo<PlanCatalogEntry[]>(
    () => (dataset.plans.length > 0
      ? dataset.plans.map((plan) => ({ id: plan.id, name: plan.name }))
      : (dataset.planCatalog ?? [])),
    [dataset.plans, dataset.planCatalog],
  );
  const fallbackPlanId = planId && planOptions.some((plan) => plan.id === planId)
    ? planId
    : planOptions[0]?.id ?? "PLAN-REPAIR";
  const [internalPlanId, setInternalPlanId] = useState(fallbackPlanId);
  const selectedPlanId = planId && planOptions.some((plan) => plan.id === planId)
    ? planId
    : internalPlanId;
  const [targets, setTargets] = useState<string[]>([]);
  const [horizon, setHorizon] = useState(TIMELINE_DEFAULT_HORIZON_HOURS);
  const [scheduleDraft, setScheduleDraft] = useState<string>("");
  const [state, setState] = useState<{ key: string; snapshot: TimelineSnapshot } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const previousContext = useRef<string | null>(null);
  const previousImpactReady = useRef(false);
  const task = useMemo(
    () => dataset.timeline.tasks.find((item) => item.scenarioId === scenarioId && item.planId === selectedPlanId),
    [dataset.timeline.tasks, scenarioId, selectedPlanId],
  );
  const schedule = useMemo(
    () => scheduleDraft.trim() === ""
      ? undefined
      : [{ actionId: task?.actionId ?? "", finishOffsetHours: Number(scheduleDraft) }],
    [scheduleDraft, task?.actionId],
  );
  const key = inputKey([scenarioId, isolationPlanId, selectedPlanId, targets, horizon, schedule, timelineBasisKey(dataset)]);
  const snapshot = state?.key === key ? state.snapshot : null;

  useEffect(() => {
    if (planId && planOptions.some((plan) => plan.id === planId)) setInternalPlanId(planId);
  }, [planOptions, planId]);

  useEffect(() => {
    const contextKey = `${scenarioId}:${isolationPlanId}`;
    if (previousContext.current !== contextKey) {
      previousContext.current = contextKey;
      previousImpactReady.current = false;
      setTargets([]);
      setScheduleDraft("");
    }
    if (impact && !previousImpactReady.current) {
      previousImpactReady.current = true;
      setTargets(targetDefaults(dataset, impact));
    }
  }, [dataset, impact, isolationPlanId, scenarioId]);

  useEffect(() => {
    setScheduleDraft("");
  }, [selectedPlanId]);

  // Only expose a snapshot whose key still matches the current inputs. Changing a
  // target, plan, horizon or schedule therefore removes the old result immediately.
  useEffect(() => {
    onResult?.(snapshot);
  }, [onResult, snapshot]);

  useEffect(() => {
    if (!impact || impact.scenarioId !== scenarioId || impact.isolationPlanId !== isolationPlanId) {
      setLoading(false);
      setError(impactError ?? null);
      setState(null);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    setState(null);
    const request: TimelineRequest = {
      scenarioId,
      isolationPlanId,
      planId: selectedPlanId,
      targetServiceIds: targets,
      horizonHours: horizon,
      ...(schedule ? { actionSchedule: schedule } : {}),
    };
    void provider.checkTimeline(request)
      .then((gate) => provider.timeline(request).then((result) => ({ gate, result })))
      .then((value) => {
        const expectedAlgorithm = dataset.meta.providerKind === "platform" ? null : TIMELINE_ALGORITHM_VERSION;
        if (!timelineMatchesRequest(value.result, request) || value.result.asOf !== dataset.timeline.asOf ||
            value.result.timelineDataVersion !== dataset.timeline.version ||
            (expectedAlgorithm !== null && value.result.algorithmVersion !== expectedAlgorithm)) throw new Error("时间线结果与请求不一致，请重新计算。");
        if (!cancelled) setState({ key, snapshot: { request, ...value } });
      })
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : "时间线计算失败。");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [horizon, impact, impactError, isolationPlanId, key, provider, scenarioId, schedule, selectedPlanId, targets]);

  function toggleTarget(serviceId: string) {
    setTargets((current) => current.includes(serviceId)
      ? current.filter((id) => id !== serviceId)
      : [...current, serviceId]);
  }

  return (
    <section className="panel timeline-panel">
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Service recovery · G01</p>
          <h2>服务恢复时间线</h2>
          <p>按合成动作事件回放计划图通路；任务完成时间与服务恢复时间分开显示。不是水力、水质或现场执行结论。</p>
        </div>
        <span className="panel-kicker">{dataset.timeline.version}</span>
      </div>

      <div className="timeline-controls">
        <label>
          方案
          <select value={selectedPlanId} onChange={(event) => {
            const next = event.target.value;
            setInternalPlanId(next);
            onPlanChange?.(next);
          }}>
            {planOptions.map((plan) => <option key={plan.id} value={plan.id}>{plan.name}</option>)}
          </select>
        </label>
        <label>
          观察窗口（小时）
          <input
            type="number"
            min={1}
            max={TIMELINE_MAX_HORIZON_HOURS}
            value={horizon}
            onChange={(event) => setHorizon(Math.max(1, Math.min(TIMELINE_MAX_HORIZON_HOURS, Number(event.target.value) || 1)))}
          />
        </label>
        <div className="timeline-targets">
          <span>目标服务</span>
          <div className="target-grid">
            {dataset.services.map((service) => {
              const affected = impact?.affectedServices.some((item) => item.serviceId === service.id);
              return (
                <label className={`target-chip ${targets.includes(service.id) ? "selected" : ""}`} key={service.id}>
                  <input type="checkbox" checked={targets.includes(service.id)} onChange={() => toggleTarget(service.id)} />
                  {service.name}
                  {affected ? <em>受影响</em> : null}
                </label>
              );
            })}
          </div>
        </div>
      </div>

      {targets.length === 0 ? <p className="inline-warning">请至少选择一个目标服务；明确空集合只适用于“不统计服务恢复”的任务。</p> : null}
      {!impact && !impactError && !loading ? <p className="muted small">等待故障影响结果后计算时间线。</p> : null}
      {loading ? <p className="muted small">正在校验能力并计算时间线…</p> : null}
      {error ? <p className="inline-warning">时间线不可用：{error}</p> : null}

      {snapshot ? (
        <>
          <div className="timeline-gate">
            <StatusDot tone={gateTone(snapshot.gate.status)} />
            <strong>能力预检：{snapshot.gate.status}</strong>
            <span>可计算：{snapshot.gate.canCompute ? "是" : "否"} · 计算状态：{snapshot.gate.calculationStatus ?? "—"}</span>
          </div>
          <p className="muted small">
            基准 {snapshot.result.asOf} · 计划时间 · 观察窗口 {snapshot.result.horizonHours} 小时 · 图通路判据
          </p>
          {snapshot.gate.missingItems.length ? (
            <ul className="violation-list">
              {snapshot.gate.missingItems.map((item) => <li key={item}>{item}</li>)}
            </ul>
          ) : null}

          <div className="timeline-metrics">
            <div className={`timeline-metric tone-${metricTone(snapshot.result.planCompletion)}`}>
              <span>方案全部任务完成</span>
              <b>{metricText(snapshot.result.planCompletion)}</b>
              <small>{snapshot.result.planCompletion.reason}</small>
            </div>
            <div className={`timeline-metric tone-${metricTone(snapshot.result.firstTargetServiceRestored)}`}>
              <span>首个目标恢复</span>
              <b>{metricText(snapshot.result.firstTargetServiceRestored)}</b>
              <small>{snapshot.result.firstTargetServiceRestored.reason}</small>
            </div>
            <div className={`timeline-metric tone-${metricTone(snapshot.result.allTargetServicesRestored)}`}>
              <span>全部目标首次同时可达</span>
              <b>{metricText(snapshot.result.allTargetServicesRestored)}</b>
              <small>{snapshot.result.allTargetServicesRestored.reason}</small>
            </div>
          </div>

          {snapshot.result.planCompletion.status === "insufficient_data" || scheduleDraft !== "" ? (
            <div className="schedule-fix">
              <label>
                补录任务结束（小时，仅本次假设）
                <input type="number" min={0} step={0.5} value={scheduleDraft} onChange={(event) => setScheduleDraft(event.target.value)} placeholder="例如 6" />
              </label>
              <span className="muted small">补录只影响本次计算，不修改合成资产或数据文件。</span>
              {scheduleDraft ? <button className="button-ghost" onClick={() => setScheduleDraft("")}>撤销补录</button> : null}
            </div>
          ) : null}

          <div className="timeline-table-wrap">
            <table className="timeline-table">
              <thead><tr><th>目标服务</th><th>首次图通路恢复</th><th>状态</th><th>依据</th></tr></thead>
              <tbody>
                {snapshot.result.targetServiceIds.map((serviceId) => {
                  const service = dataset.services.find((item) => item.id === serviceId);
                  const metric = snapshot.result.serviceFirstRestored[serviceId];
                  return (
                    <tr key={serviceId}>
                      <td>{service?.name ?? serviceId}<small>{serviceId}</small></td>
                      <td>{metric ? metricText(metric) : "—"}</td>
                      <td>{metric?.status ?? "insufficient_data"}</td>
                      <td>{metric?.reason ?? "未返回"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="event-axis">
            <h3>图事件轴</h3>
            {snapshot.result.events.length ? snapshot.result.events.map((event) => (
              <div className="event-row" key={`${event.atHours}-${event.actionIds.join("-")}`}>
                <span className="event-time">{event.atHours}h</span>
                <span>{event.actionIds.join("、")}</span>
                <small>恢复边：{event.restoredEdgeIds.join("、") || "无"}</small>
              </div>
            )) : <p className="muted small">该方案没有图操作事件；任务完成不等于服务恢复。</p>}
          </div>

          <div className="timeline-evidence">
            <span className="muted small">证据与限制</span>
            <EvidenceList ids={snapshot.result.evidenceIds} />
            <ul className="report-limits">
              {snapshot.result.limitations.map((limit) => <li key={limit}>{limit}</li>)}
            </ul>
          </div>
        </>
      ) : null}
    </section>
  );
}
