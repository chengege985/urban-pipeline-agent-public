import type { ReactNode } from "react";
import type { GasCuvReplayResponse } from "../../shared/contracts/platform";
import { GasReadError } from "./GasDetails";
import {
  GasMonitoringSource, GasMonitoringTransition, gasMonitoringMetricLabel, gasMonitoringStateLabel,
} from "./GasMonitoringPanel";
import { eventKinds } from "./presentation";
import type { GasReadState } from "./useGasRead";

type Plan = GasCuvReplayResponse["plans"][number];
type Step = Plan["steps"][number];
type Monitoring = GasCuvReplayResponse["monitoring_evidence"];

const phaseLabel = {
  fault: "给定故障", isolate: "图上隔离", staged_release: "分段解除隔离", assumed_clearance: "假定故障解除",
};
const constraintLabel = {
  met: "满足图约束", blocked: "受阻", assumed: "方案假设", unknown: "未知", not_applicable: "不适用",
};
const metricLabel: Record<string, string> = {
  actual_gas_supply: "实际供气", pressure: "压力", flow: "流量", physical_restoration_hours: "现场恢复时长",
  failure_probability: "失效概率", operational_cost: "处置成本",
};
const digestLabel = {
  network_content_sha256: "网络内容", case_content_sha256: "案例内容",
  html_source_sha256: "拓扑来源", replay_content_sha256: "推演配置",
};

function AssetLinks({ ids, onOpenAsset }: { ids: string[]; onOpenAsset(id: string): void }) {
  return <div className="gas-cuv-links">
    {ids.map((id) => <button type="button" key={id} aria-label={"定位资产 " + id} onClick={() => onOpenAsset(id)}>{id}</button>)}
  </div>;
}

function Phase({ step, onOpenAsset }: { step: Step; onOpenAsset(id: string): void }) {
  return <li data-cuv-phase={step.kind} data-cuv-phase-status={step.status}>
    <details>
      <summary><strong>{phaseLabel[step.kind]}</strong> <span className="gas-cuv-summary-line">
        +{step.elapsed_hours} h · {step.status === "blocked" ? "受阻" : (step.affected_service_ids?.length ?? "未知") + " 项新增图不可达服务"}
      </span></summary>
      <dl className="gas-fields">
        <div><dt>合成时点</dt><dd><time dateTime={step.at}>{step.at}</time></dd></div>
        <div><dt>新增图不可达节点</dt><dd>{step.affected_node_ids?.length ?? "未知"}</dd></div>
        <div><dt>新增图不可达服务</dt><dd>{step.affected_service_ids?.join("、") || (step.affected_service_ids === null ? "未知" : "无")}</dd></div>
        <div><dt>较上阶段恢复图可达的服务</dt><dd>{step.restored_service_ids?.join("、") || (step.restored_service_ids === null ? "未知" : "无")}</dd></div>
        <div><dt>新增图不可达服务对象记录</dt><dd>{step.affected_consumers
          ? "家庭 " + step.affected_consumers.household + " · 商业 " + step.affected_consumers.business + " · 设施 " + step.affected_consumers.facility
          : "未知"}</dd></div>
      </dl>
      {step.blocked_asset_ids.length > 0 && <><p>{step.status === "blocked" ? "未通过切片拟限制的资产" : "此阶段图中不可通行的资产"}</p><AssetLinks ids={step.blocked_asset_ids} onOpenAsset={onOpenAsset} /></>}
      {step.constraints.length > 0 && <ul className="gas-cuv-constraint-list" aria-label="阶段约束">{step.constraints.map((item) =>
        <li key={item.code}><strong className="gas-cuv-status">{constraintLabel[item.status]}</strong> · {item.explanation}</li>)}</ul>}
    </details>
  </li>;
}

function PlanCard({ plan, onOpenAsset }: { plan: Plan; onOpenAsset(id: string): void }) {
  return <article className="gas-cuv-card" data-cuv-plan={plan.plan_id}>
    <h4>{plan.label}</h4>
    <p>{plan.description}</p>
    {plan.status === "blocked" && <p className="gas-cuv-status">方案受阻</p>}
    <dl className="gas-fields">
      <div><dt>服务图可达恢复至基线时长</dt><dd>{plan.topology_restoration_hours === null ? "未知" : plan.topology_restoration_hours + " h"}</dd></div>
      <div><dt>新增图不可达服务峰值</dt><dd>{plan.peak_affected_service_count ?? "未知"}</dd></div>
      <div><dt>新增图不可达服务·小时</dt><dd>{plan.affected_service_hours ?? "未知"}</dd></div>
    </dl>
    <ol className="gas-cuv-phase-list" aria-label="合成方案时间线">{plan.steps.map((step) =>
      <Phase key={step.at + ":" + step.kind} step={step} onOpenAsset={onOpenAsset} />)}</ol>
  </article>;
}

function MonitoringEvidence({ monitoring, onOpenAsset }: { monitoring: Monitoring; onOpenAsset(id: string): void }) {
  return <section className="gas-cuv-card" aria-label="监测输入证据" data-testid="gas-cuv-monitoring-evidence">
    <h4>场景起点的监测证据</h4>
    <p className="gas-cuv-summary-line">截至 <time dateTime={monitoring.as_of}>{monitoring.as_of}</time> · 对应资产的合成监测上下文</p>
    {monitoring.status === "unavailable" ? <p>该时点的监测证据不可用。</p> : <>
      <details>
        <summary>数据包与策略</summary>
        <dl className="gas-fields">
          <div><dt>数据包</dt><dd><code>{monitoring.dataset_id}</code> · <code>{monitoring.dataset_version}</code></dd></div>
          <div><dt>清单 SHA-256</dt><dd><code>{monitoring.manifest_sha256}</code></dd></div>
          <div><dt>监测策略</dt><dd><code>{monitoring.policy_id}</code></dd></div>
          <div><dt>观测来源</dt><dd>{monitoring.source && <GasMonitoringSource item={monitoring.source} />}</dd></div>
        </dl>
      </details>
      {monitoring.channels.length === 0 ? <p>该时点没有关联资产的监测记录。</p>
        : <ul className="gas-cuv-evidence-list">{monitoring.channels.map((channel) => (
          <li key={[channel.asset_id, channel.sensor_id, channel.metric].join(":")} data-cuv-monitoring-asset={channel.asset_id}>
            <p><strong>{gasMonitoringMetricLabel[channel.metric]}</strong> · <code>{channel.sensor_id}</code> · {gasMonitoringStateLabel[channel.state]}</p>
            <p>{channel.relation === "fault_asset" ? "故障资产" : "隔离关联资产"} · 最近有效观测 {channel.last_usable_at
              ? <time dateTime={channel.last_usable_at}>{channel.last_usable_at}</time> : "未知"}</p>
            <AssetLinks ids={[channel.asset_id]} onOpenAsset={onOpenAsset} />
            {channel.transition && <ul className="gas-monitoring-history">
              <GasMonitoringTransition event={channel.transition} unit={channel.unit} />
            </ul>}
          </li>
        ))}</ul>}
    </>}
  </section>;
}

function EventEvidence({ events, onOpenEvent, onOpenAsset }: {
  events: GasCuvReplayResponse["event_evidence"]; onOpenEvent(id: string): void; onOpenAsset(id: string): void;
}) {
  return <section className="gas-cuv-card" aria-label="合成事件证据" data-testid="gas-cuv-event-evidence">
    <h4>案例事件证据</h4>
    {events.length === 0 ? <p>暂无关联事件。</p> : <ul className="gas-cuv-evidence-list">{events.map((event) => (
      <li key={event.event_id} data-cuv-event={event.event_id}>
        <details>
          <summary><strong>{eventKinds[event.event_kind]}</strong> · <time dateTime={event.replay_at}>{event.replay_at}</time></summary>
          <p>{event.title} · 案例偏移 +{event.elapsed_hours} h</p>
          <p>来源 SHA-256 <code>{event.source_sha256}</code></p>
          {event.evidence_refs.length > 0 && <p>案卷引用 <code>{event.evidence_refs.join(" · ")}</code></p>}
          <div className="gas-cuv-links">
            <button type="button" aria-label={"定位事件 " + event.event_id} onClick={() => onOpenEvent(event.event_id)}>打开事件详情</button>
          </div>
          <AssetLinks ids={event.asset_ids} onOpenAsset={onOpenAsset} />
        </details>
      </li>
    ))}</ul>}
  </section>;
}

export function GasCuvReplayPanel({ resource, onOpenAsset, onOpenEvent, onAskQwen, children }: {
  resource: GasReadState<GasCuvReplayResponse> & { retry(): void };
  onOpenAsset(id: string): void;
  onOpenEvent(id: string): void;
  onAskQwen?(report: GasCuvReplayResponse): void;
  children?: ReactNode;
}) {
  const report = resource.data;
  return <section className="gas-cuv-replay" data-testid="gas-cuv-comparison" aria-labelledby="gas-cuv-replay-heading">
    <h3 id="gas-cuv-replay-heading">合成隔离与恢复方案对比</h3>
    <p className="gas-window-note">影响按气源图通路计算。实际供气、压力、流量和现场复气保持未知。</p>
    {resource.error && <GasReadError error={resource.error} retry={resource.retry} label="重试读取 CUV 推演" />}
    {resource.status === "loading" && <p role="status">正在读取合成推演…</p>}
    {report && <>
      <div className="gas-cuv-overview">
        <p>合成故障 <time dateTime={report.scenario_at}>{report.scenario_at}</time> · 回放窗口 {report.analysis_window_hours} h · 未经业务验证</p>
        <p>{report.comparison_basis}</p>
        <AssetLinks ids={report.fault_asset_ids} onOpenAsset={onOpenAsset} />
        {onAskQwen && <button type="button" data-testid="gas-cuv-ask-qwen" onClick={() => onAskQwen(report)}>让 Qwen 比较</button>}
      </div>
      {children}
      <div className="gas-cuv-plan-grid">{report.plans.map((plan) =>
        <PlanCard key={plan.plan_id} plan={plan} onOpenAsset={onOpenAsset} />)}</div>
      <details>
        <summary>未知指标与输入缺项</summary>
        <dl className="gas-fields">{report.unknown_metrics.map((item) => (
          <div key={item.code} data-cuv-unknown={item.code}><dt>{metricLabel[item.code] ?? item.code}</dt><dd>未知 · {item.reason}</dd></div>
        ))}</dl>
        <ul>{report.missing_items.map((item) => <li key={item.code}>{item.description}</li>)}</ul>
      </details>
      <div className="gas-cuv-evidence">
        <MonitoringEvidence monitoring={report.monitoring_evidence} onOpenAsset={onOpenAsset} />
        <EventEvidence events={report.event_evidence} onOpenEvent={onOpenEvent} onOpenAsset={onOpenAsset} />
      </div>
      <details>
        <summary>拓扑、案例与算法版本</summary>
        <dl className="gas-fields">
          <div><dt>网络</dt><dd><code>{report.network_id}</code></dd></div>
          <div><dt>案例</dt><dd><code>{report.case_id}</code></dd></div>
          <div><dt>情景</dt><dd><code>{report.scenario_id}</code></dd></div>
          <div><dt>算法</dt><dd><code>{report.algorithm_version}</code></dd></div>
          {(Object.keys(digestLabel) as Array<keyof typeof digestLabel>).map((key) => (
            <div key={key}><dt>{digestLabel[key]} SHA-256</dt><dd><code>{report.topology_evidence[key] ?? "未知"}</code></dd></div>
          ))}
        </dl>
      </details>
    </>}
  </section>;
}
