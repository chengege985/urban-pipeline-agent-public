import type { GasMonitoringReport, GasObservationRef, MonitoringChannelResult, MonitoringEvent, MonitoringRowEvidence, MonitoringSourceEvidence } from "../../shared/contracts/platform";
import { GasReadError } from "./GasDetails";
import type { GasReadState } from "./useGasRead";

export const gasMonitoringMetricLabel = { pressure: "压力", flow: "流量" };
export const gasMonitoringStateLabel = { normal: "样例范围内", observing: "待判定", limit_exceeded: "持续越限", data_missing: "数据缺失" };
const eventLabel = { limit_exceeded: "持续越限", data_missing: "数据缺失", recovered: "恢复" };
const causeLabel = {
  above_upper: "连续有效观测高于样例上限",
  below_lower: "连续有效观测低于样例下限",
  no_usable_observation: "连续采样时段缺少可用读数",
  within_limits: "有效观测回到样例范围",
  observations_resumed: "重新收到有效读数",
};
const qualityLabel = { valid: "有效", suspect: "可疑" };

export function GasMonitoringSource({ item }: { item: MonitoringSourceEvidence }) {
  return <span className="gas-monitoring-source">
    <code>{item.source_id}</code> · 版本 <code>{item.source_version}</code> · SHA-256 <code>{item.source_sha256}</code>
  </span>;
}

function SourceRow({ item }: { item: MonitoringRowEvidence }) {
  return <><GasMonitoringSource item={item} /> · 原始记录 <code>{item.source_record_id}</code> · 第 {item.source_row} 行</>;
}

export function monitoringTrigger(event: MonitoringEvent) {
  return event.kind === "limit_exceeded" ? event.evidence.find((item) =>
    item.quality === "valid" && Date.parse(item.observed_at) === Date.parse(event.at)) : undefined;
}

export function GasMonitoringTransition({ event, unit, onOpenObservation }: {
  event: MonitoringEvent; unit: string; onOpenObservation?(id: string): void;
}) {
  const trigger = monitoringTrigger(event);
  return <li data-monitoring-event={event.kind}>
    <details>
      <summary><strong>{eventLabel[event.kind]}</strong> <time dateTime={event.at}>{event.at}</time></summary>
      <p>{causeLabel[event.cause]}。此前状态为{gasMonitoringStateLabel[event.previous_state]}。</p>
      {trigger && onOpenObservation && <button type="button" data-observation-id={trigger.source_record_id}
        onClick={() => onOpenObservation(trigger.source_record_id)}>
        打开异常观测与关联资产 · {trigger.value} {trigger.unit}
      </button>}
      {Boolean(event.missing_slots.length) && <div className="gas-monitoring-readings">
        <strong>缺少可用读数的采样时点</strong>
        <ul>{event.missing_slots.map((slot) => <li key={slot}><time dateTime={slot}>{slot}</time></li>)}</ul>
      </div>}
      {event.evidence.length ? <div className="gas-monitoring-readings">
        <strong>关联观测证据</strong>
        <ul>
          {event.evidence.map((item) => <li key={item.source_row}>
            <time dateTime={item.observed_at}>{item.observed_at}</time> · {item.value} {item.unit} · {qualityLabel[item.quality]}
            {item.unit !== unit && " · 与样例规则单位不一致"}
            <div><SourceRow item={item} /></div>
          </li>)}
        </ul>
      </div> : <p>没有可关联的观测行。</p>}
    </details>
  </li>;
}

function Channel({ item, onOpenAsset, onOpenObservation }: {
  item: MonitoringChannelResult; onOpenAsset(id: string): void; onOpenObservation?(id: string): void;
}) {
  const limits = [
    item.lower === null ? null : "≥ " + item.lower,
    item.upper === null ? null : "≤ " + item.upper,
  ].filter(Boolean).join(" 且 ");
  return <article className="gas-monitoring-channel" data-monitoring-asset={item.asset_id} data-monitoring-metric={item.metric}>
    <div className="gas-monitoring-channel-head">
      <h4>{gasMonitoringMetricLabel[item.metric]} <code>{item.sensor_id}</code></h4>
      <span className="gas-monitoring-state" data-state={item.state}>{gasMonitoringStateLabel[item.state]}</span>
      <button type="button" aria-label={"打开关联资产 " + item.asset_id} onClick={() => onOpenAsset(item.asset_id)}>
        资产 {item.asset_id}
      </button>
    </div>
    <p>样例范围 {limits} {item.unit}</p>
    <p className="gas-muted">最近有效观测 {item.last_usable_at
      ? <time dateTime={item.last_usable_at}>{item.last_usable_at}</time> : "暂无"}</p>
    {item.off_schedule_observations > 0 && <p className="gas-muted">{item.off_schedule_observations} 条非采样时点观测未纳入回放。</p>}
    <details><summary>资产关联证据</summary><p><SourceRow item={item.asset_evidence} /></p></details>
    {item.events.length
      ? <ol className="gas-monitoring-history">{[...item.events].reverse().map((event) => <GasMonitoringTransition key={event.at + ":" + event.kind} event={event} unit={item.unit} onOpenObservation={onOpenObservation} />)}</ol>
      : <p className="gas-muted">本回放窗口内暂无状态变化。</p>}
  </article>;
}

export function GasMonitoringPanel({ resource, onOpenAsset, onOpenObservation }: {
  resource: GasReadState<GasMonitoringReport> & { retry(): void };
  onOpenAsset(id: string): void;
  onOpenObservation?(ref: GasObservationRef): void;
}) {
  const report = resource.data;
  return <section className="gas-monitoring-panel" data-testid="gas-monitoring-panel" aria-labelledby="gas-monitoring-heading">
    <h3 id="gas-monitoring-heading">合成监测预警</h3>
    <p className="gas-muted">合成数据回放，使用未经业务验证的样例阈值。</p>
    {resource.error && <GasReadError error={resource.error} retry={resource.retry} label="重试读取监测预警" />}
    {resource.status === "loading" && <p role="status">正在读取合成监测预警…</p>}
    {report && <>
      <p>回放截至 <time dateTime={report.evaluated_at}>{report.evaluated_at}</time></p>
      <details className="gas-monitoring-package">
        <summary>数据包与规则证据</summary>
        <dl className="gas-fields">
          <div><dt>数据包</dt><dd><code>{report.dataset_id}</code> · 版本 <code>{report.dataset_version}</code></dd></div>
          <div><dt>清单 SHA-256</dt><dd><code>{report.manifest_sha256}</code></dd></div>
          <div><dt>监测来源</dt><dd>{report.monitoring_source ? <GasMonitoringSource item={report.monitoring_source} /> : "未提供"}</dd></div>
          <div><dt>样例规则</dt><dd><code>{report.policy_id}</code> · 每 {report.cadence_seconds} 秒采样 · 连续 {report.sustained_samples} 次越限或 {report.missing_samples} 次缺失</dd></div>
          <div><dt>回放起点</dt><dd><time dateTime={report.window_start}>{report.window_start}</time></dd></div>
        </dl>
      </details>
      <div className="gas-monitoring-channels">
        {report.channels.map((item) => <Channel key={[item.asset_id, item.sensor_id, item.metric].join(":")} item={item} onOpenAsset={onOpenAsset} onOpenObservation={onOpenObservation && ((observation_id) =>
          onOpenObservation({ observation_id, manifest_sha256: report.manifest_sha256 }))} />)}
      </div>
      {report.channels.length === 0 && <p>没有可显示的合成监测通道。</p>}
    </>}
  </section>;
}
