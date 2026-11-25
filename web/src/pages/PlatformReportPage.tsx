import { useEffect, useState } from "react";
import type { BackendReportDocument } from "../adapters/platformReport";
import type { WaterPulseProvider } from "../providers";
import { PlatformTransportError } from "../providers/platform/transport";
import { PageHeader } from "../components/PageHeader";

// Display helpers deliberately do not convert strings/bools into business numbers.
const object = (value: unknown): Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
const text = (value: unknown) => typeof value === "string" && value.length > 0 ? value : "未知（未提供）";
const number = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? String(value) : "未知（未提供）";
const ids = (value: unknown) => Array.isArray(value) ? (value.filter(x => typeof x === "string").join("、") || "无") : "未知（未提供）";
const rows = (value: unknown) => Array.isArray(value) ? value.map(object) : [];
const labels: Record<string, string> = {
  succeeded: "已完成", partial: "部分结果", insufficient_data: "数据不足", unknown: "未知",
  infeasible: "不满足约束", unsupported: "不支持", not_applicable: "不适用", failed: "失败",
  queued: "排队中", running: "计算中", pass: "通过", fail: "未通过", pending_review: "待审核",
  accepted: "已接受", rejected: "已驳回", revised: "已修订", invalid_input: "输入无效",
};
function status(value: unknown) { return typeof value === "string" ? `${labels[value] ?? "未知状态"}（${value}）` : "未知（未提供）"; }
function Status({ value }: { value: unknown }) {
  return <span className={`pill tone-${value === "succeeded" || value === "pass" ? "ok" : "warn"}`}>{status(value)}</span>;
}
function Topology({ value, title }: { value: unknown; title: string }) {
  const v = object(value); const c = object(v.potentially_disconnected_counts);
  return <section className="panel report-section">
    <div className="panel-heading"><h2>{title}</h2><Status value={v.status} /></div>
    <dl className="attribute-grid compact">
      <div><dt>受影响服务</dt><dd>{ids(v.affected_service_ids)}</dd></div>
      <div><dt>受影响关键设施</dt><dd>{ids(v.affected_critical_facility_ids)}</dd></div>
      <div><dt>居民户数（户）</dt><dd>{number(c.household)}</dd></div>
      <div><dt>人数（人）</dt><dd>{number(c.person)}</dd></div>
      <div><dt>设施数量（处）</dt><dd>{number(c.facility)}</dd></div>
      <div><dt>数量未知的服务</dt><dd>{ids(v.unknown_count_service_ids)}</dd></div>
    </dl>
    <p className="topology-note">图通路结果，不代表压力、流量、水质或实际供水量。</p>
  </section>;
}
interface Props { provider: WaterPulseProvider; runId?: string; onNavigate: (path: string) => void }
interface LoadState { runId: string; attempt: number; provider: WaterPulseProvider; report?: BackendReportDocument; error?: string }

export function PlatformReportPage({ provider, runId, onNavigate }: Props) {
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState | null>(null);
  const [enteredId, setEnteredId] = useState("");
  useEffect(() => {
    let active = true;
    if (!runId) return;
    const load = async () => {
      try {
        if (!provider.report) throw new Error("当前数据源不支持读取运行报告。");
        const report = await provider.report(runId);
        if (active) setState({ runId, attempt, provider, report });
      } catch (error) {
        let message = error instanceof Error ? error.message : "报告读取失败，请重试。";
        if (error instanceof PlatformTransportError) {
          const reason = error.status === 409 ? "报告暂不可用" : error.status === 401 ? "尚未认证" : error.status === 403 ? "没有该报告的访问权限" : error.status === 404 ? "报告对应的运行不存在" : "报告请求被拒绝";
          message = `${reason}（HTTP ${error.status}）${error.detail ? `：${error.detail}` : ""}`;
        }
        if (active) setState({ runId, attempt, provider, error: message });
      }
    };
    void load();
    return () => { active = false; };
  }, [provider, runId, attempt]);
  const current = state !== null && state.runId === runId && state.attempt === attempt && state.provider === provider ? state : null;
  const report = current?.report;
  if (!runId) return <div className="page-stack platform-report-page" data-testid="platform-report-page">
    <PageHeader eyebrow="Platform report" title="运行报告" description="输入已有运行编号，读取已保存的分析结果。" />
    <form className="panel report-section" onSubmit={e => { e.preventDefault(); if (/^[A-Za-z0-9_-]+$/.test(enteredId.trim())) onNavigate(`/report/${enteredId.trim()}`); }}>
      <label>运行编号<input aria-label="运行编号" required pattern="[A-Za-z0-9_-]+" value={enteredId} onChange={e => setEnteredId(e.target.value)} /></label>
      <button className="button-primary" type="submit">查看报告</button>
      <p>也可在情景分析完成方案比较后选择“查看报告”。</p>
    </form>
  </div>;
  if (!report) return <div className="page-stack platform-report-page" data-testid="platform-report-page">
    <PageHeader eyebrow="Platform report" title="运行报告" description={`运行编号：${runId}`} />
    <section className="panel report-section" aria-live="polite">
      {current?.error ? <><p role="alert" data-testid="report-error">{current.error}</p><p>没有展示该运行的报告。可确认运行状态或权限后重试读取。</p>
        <button className="button-primary" data-testid="report-refresh" onClick={() => setAttempt(x => x + 1)}>重新读取</button></> : <p role="status">正在读取已有报告…</p>}
      <button className="button-ghost" onClick={() => onNavigate("/overview")}>返回总览</button>
    </section>
  </div>;
  const decision = object(report.decision); const pipe = object(report.pipe); const scenario = object(report.scenario);
  const download = () => {
    const url = URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json;charset=utf-8" }));
    const a = document.createElement("a"); a.href = url; a.download = `waterpulse-${report.run_id.replace(/[^\w-]/g, "_")}.json`; a.click(); URL.revokeObjectURL(url);
  };
  return <div className="page-stack report-page platform-report-page" data-testid="platform-report-page">
    <PageHeader eyebrow="Platform report" title="运行报告" description="本报告读取平台已保存的运行结果，保留原始数据状态和证据。"
      actions={<><button className="button-ghost" data-testid="report-refresh" onClick={() => setAttempt(x => x + 1)}>重新读取</button>
        <button className="button-ghost" onClick={() => window.print()}>打印 / 另存 PDF</button><button className="button-primary" onClick={download}>下载 JSON</button></>} />
    <section className="panel report-head">
      <div className="report-title-row"><h1>{report.run_id}</h1><span data-testid="report-status"><Status value={report.business_status} /></span></div>
      <dl className="report-meta-grid">
        <div><dt>报告编号</dt><dd>{report.report_id}</dd></div><div><dt>运行类型 / 状态</dt><dd>{report.run_kind} / {status(report.run_status)}</dd></div>
        <div><dt>快照编号</dt><dd>{report.dataset.snapshot_id}</dd></div><div><dt>数据版本</dt><dd>{report.dataset.data_version}</dd></div>
        <div><dt>评价时点</dt><dd>{report.dataset.as_of}</dd></div><div><dt>数据来源 / 模式</dt><dd>{report.dataset.source_status} / {report.dataset.data_mode}</dd></div>
        <div><dt>故障边 / 隔离方案</dt><dd>{text(scenario.fault_edge_id)} / {text(scenario.isolation_plan_id)}</dd></div>
        <div><dt>分析窗口（小时）</dt><dd>{number(scenario.horizon_hours)}</dd></div>
      </dl>
      <p>算法版本：{report.algorithms.map(x => `${x.capability_id} / ${x.version} / ${x.validation_status}`).join("；")}</p>
    </section>
    <section className="panel report-section"><h2>管段与评分</h2>
      {report.pipe ? <dl className="attribute-grid compact">
        <div><dt>管段编号</dt><dd>{text(pipe.pipe_id)}</dd></div><div><dt>评分模型</dt><dd>{text(pipe.model_family)} / {text(pipe.model_version)}</dd></div>
        <div><dt>简化风险指标 Rs</dt><dd>{number(pipe.rs)}</dd></div><div><dt>简化健康评分 Hs（分）</dt><dd>{number(pipe.hs)}</dd></div>
        <div><dt>等级</dt><dd>{text(pipe.grade)}</dd></div><div><dt>数据完整率（%）</dt><dd>{number(pipe.completeness_percent)}</dd></div>
      </dl> : <p>未知（未提供管段评分）</p>}
      <p className="muted">简化评分不等于正式健康评价、失效概率或剩余寿命。完整率表示数据覆盖情况。</p>
    </section>
    {report.simulation ? <Topology value={report.simulation} title="拓扑影响" /> : null}
    {decision.baseline_impact ? <Topology value={decision.baseline_impact} title="基准拓扑影响" /> : null}
    {report.decision ? <section className="panel report-section"><h2>候选方案与约束</h2>
      <p>后端推荐方案：{decision.recommended_plan_id == null ? "未知（未提供推荐）" : text(decision.recommended_plan_id)}</p>
      {rows(decision.plans).map((plan, i) => {
        const metrics = object(plan.metrics); const constraints = object(plan.constraints ?? plan);
        return <article className="panel report-section" key={text(plan.plan_id) + i} data-testid={`report-plan-${text(plan.plan_id)}`}>
          <div className="panel-heading"><h3>{text(plan.label)} · {text(plan.plan_id)}</h3><Status value={plan.status} /></div>
          <dl className="attribute-grid compact">
            <div><dt>方案效用</dt><dd>{number(plan.utility)}</dd></div>
            <div><dt>费用（分）</dt><dd>{number("total_cost_fen" in metrics ? metrics.total_cost_fen : plan.total_cost_fen)}</dd></div>
            <div><dt>剩余断连服务（条服务记录）</dt><dd>{number(metrics.remaining_disconnected_services)}</dd></div>
            <div><dt>最后网络动作生效时间（小时）</dt><dd>{number(metrics.restoration_hours)}</dd></div>
            <div><dt>备用覆盖关键服务（条服务记录）</dt><dd>{number(metrics.backup_covered_critical_services)}</dd></div>
          </dl>
          <p className="muted small">最后网络动作时间不等于服务恢复时间或方案完成时间。</p>
          <h4>约束检查</h4>
          {rows(constraints.checks).map((check, j) => <p key={j}><Status value={check.state} /> {text(check.reason)}</p>)}
          {Array.isArray(plan.reasons) && plan.reasons.length > 0 ? <p>{ids(plan.reasons)}</p> : null}
          {plan.impact ? <Topology value={plan.impact} title="方案拓扑影响" /> : null}
        </article>;
      })}
    </section> : null}
    <section className="panel report-section"><h2>服务时间线</h2>
      {report.timeline === null ? <p>未提供。该报告未附服务时间线，服务恢复时间和方案完成时间未知。</p> : <pre>{JSON.stringify(report.timeline, null, 2)}</pre>}
    </section>
    <section className="panel report-section"><h2>证据与适用范围</h2>
      <ul>{report.evidence_ids.map(id => <li key={id}>{id}</li>)}</ul>
      <ul>{report.limitations.map((item, i) => <li key={i}>{item}</li>)}</ul>
      <p>报告合同：{report.report_schema_version}；结果来源：{report.source}；重算：{report.recomputation}</p>
    </section>
    <section className="panel report-section"><h2>已记录的人工反馈</h2>
      {report.feedback.length ? report.feedback.map((item, i) => <p key={i}>{status(item.review_status)} · {text(item.reason)}</p>) : <p>暂无反馈记录。报告不代表已审批、派单或关阀。</p>}
    </section>
  </div>;
}
