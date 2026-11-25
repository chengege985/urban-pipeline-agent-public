import { useEffect, useMemo, useState } from "react";
import { DEPENDENCY_LABELS, RESOURCE_LABELS, REVIEW_LABELS } from "../domain/constants";
import type { DecisionSession, WaterPulseDataset } from "../domain/types";
import { EmptyState } from "../components/EmptyState";
import { EvidenceList } from "../components/EvidenceList";
import { HealthGauge } from "../components/HealthGauge";
import { ArrowRightIcon, CheckIcon, InfoIcon, ResetIcon, ServiceIcon } from "../components/Icons";
import { PageHeader } from "../components/PageHeader";
import { GradePill, SeverityPill } from "../components/StatusPill";
import { buildDecisionReport, buildReportExport } from "../engine/report";
import { formatCurrency, formatScore } from "../engine/format";
import { metricText, metricTone } from "../components/TimelinePanel";
import { browserDecisionStore, type ReviewAction } from "../storage/decisionStore";

interface ReportPageProps {
  dataset: WaterPulseDataset;
  sessionId?: string;
  onNavigate: (path: string) => void;
}

function formatTime(value: string): string {
  return value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "未记录";
}

function reviewTone(status: DecisionSession["reviewStatus"]): "ok" | "warn" | "bad" | "unknown" {
  if (status === "approved") return "ok";
  if (status === "pending_review") return "warn";
  if (status === "rejected") return "bad";
  return "unknown";
}

export function ReportPage({ dataset, sessionId, onNavigate }: ReportPageProps) {
  const store = useMemo(() => browserDecisionStore(), []);
  const [session, setSession] = useState<DecisionSession | null>(() =>
    sessionId ? store.load(sessionId) : null,
  );
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [exportMessage, setExportMessage] = useState<string | null>(null);

  useEffect(() => {
    setSession(sessionId ? store.load(sessionId) : null);
  }, [sessionId, store]);

  useEffect(() => {
    if (!session) return;
    setReviewer(session.reviewer);
    setComment(session.reviewComment);
    setReviewError(null);
  }, [session?.id, session?.reviewRevision]);

  const report = useMemo(
    () => (session ? buildDecisionReport(dataset, session) : null),
    [dataset, session],
  );

  if (!report || !session) {
    return (
      <div className="page-stack">
        <PageHeader
          eyebrow="Decision report"
          title="决策简报"
          description="当前链接没有对应的本地简报记录，可能是浏览器本地存储被清理或链接已失效。"
        />
        <EmptyState
          title="找不到这份简报"
          message="请回到情景分析，重新选择可行方案并生成简报。"
          action={<button className="button-primary" onClick={() => onNavigate("/overview")}>返回总览</button>}
        />
      </div>
    );
  }

  const selected = report.selectedRow;
  const feasibleSelected = selected?.feasible === true;
  const canReview = !report.stale && feasibleSelected && report.impact.status === "succeeded" && report.comparison.status === "succeeded";

  function review(action: ReviewAction) {
    if (!session) return;
    if (!canReview) { setReviewError("当前报告已失效或缺少可审核结果，请重新生成。"); return; }
    setReviewError(null);
    try {
      const updated = store.review(session.id, action, reviewer, comment);
      setSession(updated);
      setReviewer(updated.reviewer);
      setComment(updated.reviewComment);
    } catch (caught) {
      setReviewError(caught instanceof Error ? caught.message : "审核状态更新失败。");
    }
  }

  function downloadJson() {
    if (!report) return;
    const payload = buildReportExport(report, dataset);
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `waterpulse-report-${report.session.id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
    setExportMessage("已导出当前简报 JSON（浏览器下载）。");
  }

  return (
    <div className="page-stack report-page">
      <PageHeader
        eyebrow="Decision report · C5"
        title="决策简报"
        description="管段、情景、隔离、候选方案、证据与人工审核的本地演示记录；可打印为 PDF 或导出 JSON。"
        actions={
          <>
            <button className="button-ghost" onClick={() => window.print()}>
              <InfoIcon size={15} /> 打印 / 另存 PDF
            </button>
            <button className="button-primary" onClick={downloadJson}>
              <ArrowRightIcon size={15} /> 下载 JSON
            </button>
          </>
        }
      />

      <section className="panel report-head">
        <div className="report-title-row">
          <div>
            <span className="eyebrow">WaterPulse · synthetic decision brief</span>
            <h1>{report.pipe.id} · {report.pipe.name}</h1>
            <p>{report.scenario.name} · {report.isolationPlan?.name ?? "未记录隔离方案"}</p>
          </div>
          <div className="report-head-status">
            <span className={`pill tone-${reviewTone(session.reviewStatus)}`}>{REVIEW_LABELS[session.reviewStatus]}</span>
            {report.stale ? <span className="pill tone-bad">版本已变化</span> : null}
          </div>
        </div>
        <div className="report-meta-grid">
          <div><span>管段编号</span><b>{report.pipe.id}</b></div>
          <div><span>分析时间</span><b>{formatTime(session.createdAt)}</b></div>
          <div><span>数据版本</span><b>{report.dataVersion}</b></div>
          <div><span>模型版本</span><b>{report.algorithmVersion}</b></div>
          <div><span>情景</span><b>{report.scenario.id}</b></div>
          <div><span>隔离方案</span><b>{report.isolationPlan?.id ?? "—"}</b></div>
          <div><span>选中方案</span><b>{report.selectedPlan?.name ?? "—"}</b></div>
          <div><span>审核版本</span><b>r{session.reviewRevision}</b></div>
        </div>
        {report.resultOrigin === "reconstructed_reference" ? <p className="inline-warning">按当前演示模型重建的参考结果，不是原会话的计算结果。</p> : null}
        {report.stale ? (
          <p className="inline-warning">
            {report.staleReasons.join("；")}。请回到情景分析重新生成后再审核。
          </p>
        ) : null}
      </section>

      <div className="report-grid">
        <section className="panel report-section">
          <div className="panel-heading">
            <div><p className="eyebrow">Asset & DemoHealth</p><h2>管段与演示健康评价</h2></div>
            <GradePill grade={report.assessment.grade} />
          </div>
          <div className="report-health">
            <HealthGauge assessment={report.assessment} />
            <div>
              <p>{report.assessment.summary}</p>
              <div className="report-kpis">
                <span><b>{report.pipe.material}</b><small>管材</small></span>
                <span><b>{report.pipe.installYear ?? "—"}</b><small>投运年份</small></span>
                <span><b>{report.pipe.diameterMm ? `${report.pipe.diameterMm}mm` : "—"}</b><small>管径</small></span>
                <span><b>{report.assessment.confidence}%</b><small>可信度</small></span>
              </div>
              <p className="muted small">缺失项：{report.assessment.missing.join("、") || "无"}；分数不是失效概率。</p>
            </div>
          </div>
        </section>

        <section className="panel report-section">
          <div className="panel-heading">
            <div><p className="eyebrow">Fault & isolation</p><h2>故障、隔离与服务影响</h2></div>
            <SeverityPill severity={report.scenario.severity} />
          </div>
          <dl className="attribute-grid compact">
            <div><dt>故障管段</dt><dd>{report.scenario.failedPipeIds.join("、")}</dd></div>
            <div><dt>预设阀门</dt><dd>{report.isolationPlan?.valveIds.join("、") || "无"}</dd></div>
            <div><dt>额外关闭</dt><dd>{report.isolationPlan?.closedPipeIds.join("、") || "无"}</dd></div>
            <div><dt>受影响关键设施</dt><dd>{report.impact.affectedCriticalServiceIds.length} 个</dd></div>
          </dl>
          <div className="impact-metrics report-impact">
            <span><b>{report.impact.affectedServices.length}</b><small>受影响服务</small></span>
            <span><b>{report.impact.affectedHouseholds ?? "—"}</b><small>居民户数</small></span>
            <span><b>{report.impact.affectedFacilities ?? "—"}</b><small>设施处数</small></span>
          </div>
          <div className="affected-services">
            {report.impact.affectedServices.map((service) => (
              <div className="affected-service" key={service.serviceId}>
                <span className={`service-icon service-${service.type}`}><ServiceIcon type={service.type} size={17} /></span>
                <div><strong>{service.name}</strong><small>{service.type}{service.critical ? " · 关键" : ""}</small></div>
                <span className="affected-count">{service.households > 0 ? `${service.households} 户` : `${service.facilities} 处`}</span>
              </div>
            ))}
            {report.impact.affectedServices.length === 0 ? <p className="inline-success">该情景下服务保持拓扑连通。</p> : null}
          </div>
          <p className="topology-note">拓扑通路影响，不代表水压、流量、实际缺供量或事故概率。</p>
        </section>
      </div>

      <section className="panel report-section">
        <div className="panel-heading">
          <div><p className="eyebrow">Plan comparison · constraints</p><h2>候选方案与约束结果</h2></div>
          <span className="panel-kicker">预算 {formatCurrency(report.comparison.constraints.budgetYuan)}</span>
        </div>
        <div className="constraint-summary">
          <div>
            <strong>资源容量</strong>
            <p>{Object.entries(report.comparison.constraints.resourceCapacities).map(([key, value]) => `${RESOURCE_LABELS[key] ?? key} ${value}`).join(" · ")}</p>
          </div>
          <div>
            <strong>依赖确认</strong>
            <p>{report.comparison.constraints.confirmedDependencies.map((key) => DEPENDENCY_LABELS[key] ?? key).join("、") || "未确认任何依赖"}</p>
          </div>
          <div>
            <strong>演示推荐</strong>
            <p>{report.comparison.recommendedPlanId ?? "无（没有完全可行方案）"}</p>
          </div>
        </div>
        <div className="report-plan-list">
          {report.comparison.rows.map((row) => (
            <div className={`report-plan-row ${row.planId === session.selectedPlanId ? "selected" : ""} ${row.feasible === false ? "infeasible" : ""}`} key={row.planId}>
              <span className="plan-rank">{row.planId === report.comparison.recommendedPlanId ? "推荐" : `#${row.rank}`}</span>
              <div>
                <strong>{row.name}</strong>
                <small>{row.feasible === true ? "满足当前演示硬约束" : row.feasible === null ? "结果未知，未判定" : row.violations.join("；")}</small>
              </div>
              <span>{formatCurrency(row.costYuan)}</span>
              <span>{row.durationHours}h</span>
              <span>{row.riskReduction === null ? "—" : `-${row.riskReduction}`} 风险</span>
              <span>{row.remainingAffectedServices ?? "—"} 剩余影响</span>
              <span className={`pill tone-${row.feasible === true ? "ok" : row.feasible === null ? "warn" : "bad"}`}>{row.feasible === true ? "可行" : row.feasible === null ? "未知" : "不可行"}</span>
            </div>
          ))}
        </div>
        <p className="muted small">{report.comparison.utilityNote}</p>
      </section>

      <section className="panel report-section">
        <div className="panel-heading">
          <div><p className="eyebrow">Selected plan</p><h2>选中方案与解释</h2></div>
          {feasibleSelected ? <span className="pill tone-ok">演示建议</span> : <span className="pill tone-bad">方案不可行</span>}
        </div>
        {selected ? (
          <>
            <div className="selected-plan-summary">
              <div><span>方案</span><strong>{selected.name}</strong></div>
              <div><span>演示费用</span><strong>{formatCurrency(selected.costYuan)}</strong></div>
              <div><span>方案完成时间（假设）</span><strong>{selected.durationHours} 小时</strong></div>
              <div><span>风险下降</span><strong>{selected.riskReduction === null ? "—" : `-${selected.riskReduction} 点`}</strong></div>
              <div><span>剩余影响服务</span><strong>{selected.remainingAffectedServices ?? "—"} 个</strong></div>
              <div><span>演示效用</span><strong>{selected.utility === null ? "—" : selected.utility.toFixed(3)}</strong></div>
            </div>
            <p>
              在本简报冻结的输入和演示约束下，{selected.name}{selected.feasible === true ? "满足可行性条件" : selected.feasible === null ? "的结果未知，未判定可行性" : "不满足可行性条件"}，
              按固定演示权重排名第 {selected.rank}。方案完成时间不等于服务首次或全部恢复时间；这是演示建议，不是业务最优解、正式 CUV 或维修审批结论。
            </p>
            <p className="muted small">{selected.note}</p>
          </>
        ) : (
          <p className="inline-warning">未找到选中的方案；不能把这份简报作为有效建议。</p>
        )}
      </section>

      <section className="panel report-section">
        <div className="panel-heading">
          <div><p className="eyebrow">Service recovery · timeline</p><h2>服务恢复时间线</h2></div>
          {report.timeline ? <span className={`pill tone-${metricTone(report.timeline.allTargetServicesRestored)}`}>{report.timeline.algorithmVersion}</span> : null}
        </div>
        {report.timeline ? (
          <>
            <p className="muted small">
              基准 {report.timeline.asOf} · 计划时间 · 观察窗口 {report.timeline.horizonHours} 小时 · {report.timeline.criterionId}
            </p>
            {report.timelineGate ? (
              <p className="muted small">
                能力预检 {report.timelineGate.status} · 计算状态 {report.timelineGate.calculationStatus ?? "—"}
                {report.timelineGate.missingItems.length ? ` · 缺项：${report.timelineGate.missingItems.join("；")}` : ""}
              </p>
            ) : null}
            {report.timeline.status === "invalid_input" ? (
              <p className="inline-warning">时间线输入未通过校验，未生成恢复时间：{report.timeline.issues.join("；") || "输入不完整"}。</p>
            ) : null}
            <div className="timeline-metrics">
              <div className={`timeline-metric tone-${metricTone(report.timeline.planCompletion)}`}>
                <span>方案全部任务完成</span><b>{metricText(report.timeline.planCompletion)}</b><small>{report.timeline.planCompletion.reason}</small>
              </div>
              <div className={`timeline-metric tone-${metricTone(report.timeline.firstTargetServiceRestored)}`}>
                <span>首个目标恢复</span><b>{metricText(report.timeline.firstTargetServiceRestored)}</b><small>{report.timeline.firstTargetServiceRestored.reason}</small>
              </div>
              <div className={`timeline-metric tone-${metricTone(report.timeline.allTargetServicesRestored)}`}>
                <span>全部目标首次同时可达</span><b>{metricText(report.timeline.allTargetServicesRestored)}</b><small>{report.timeline.allTargetServicesRestored.reason}</small>
              </div>
            </div>
            <div className="timeline-table-wrap">
              <table className="timeline-table">
                <thead><tr><th>目标服务</th><th>首次图通路恢复</th><th>状态</th><th>依据</th></tr></thead>
                <tbody>
                  {report.timeline.targetServiceIds.map((serviceId) => {
                    const service = dataset.services.find((item) => item.id === serviceId);
                    const metric = report.timeline?.serviceFirstRestored[serviceId];
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
              {report.timeline.events.length ? report.timeline.events.map((event) => (
                <div className="event-row" key={`${event.atHours}-${event.actionIds.join("-")}`}>
                  <span className="event-time">{event.atHours}h</span>
                  <span>{event.actionIds.join("、")}</span>
                  <small>恢复边：{event.restoredEdgeIds.join("、") || "无"}</small>
                </div>
              )) : <p className="muted small">该方案没有图操作事件；任务完成不等于服务恢复。</p>}
            </div>
            <ul className="report-limits">
              {report.timeline.limitations.map((limit) => <li key={limit}>{limit}</li>)}
            </ul>
          </>
        ) : (
          <p className="muted">本次简报未包含服务恢复时间线（旧会话或生成简报时尚未计算）。</p>
        )}
      </section>

      <section className="panel report-section">
        <div className="panel-heading">
          <div><p className="eyebrow">Evidence & limitations</p><h2>证据与模型限制</h2></div>
        </div>
        <EvidenceList ids={report.evidenceIds} />
        <ul className="report-limits">
          <li>合成数据与合成服务对象，不连接真实管网、企业 API 或生产数据库。</li>
          <li>DemoHealth 只用于同一演示版本内相对比较，不是正式健康度、失效概率、RUL 或水力结果。</li>
          <li>拓扑影响只表示连通性，不代表实际压力、流量或缺供量。</li>
          <li>费用、工期、资源、依赖和风险下降均为演示假设；不声称业务最优。</li>
          <li>审核通过只代表本地演示记录得到确认，不代表真实工单批准、下发或执行。</li>
        </ul>
        <div className="report-version-line">
          当前环境：数据版本 {dataset.meta.dataVersion} · 模型版本 {dataset.meta.modelVersion}
          <br />
          本次简报：隔离版本 {report.isolationPlan?.version ?? "—"} · 编号 {session.id}
        </div>
      </section>

      <section className="panel report-section review-section">
        <div className="panel-heading">
          <div><p className="eyebrow">Human review · local demo</p><h2>人工审核状态</h2></div>
          <span className={`pill tone-${reviewTone(session.reviewStatus)}`}>{REVIEW_LABELS[session.reviewStatus]}</span>
        </div>
        <div className="review-summary">
          <div><span>当前状态</span><b>{REVIEW_LABELS[session.reviewStatus]}</b></div>
          <div><span>审核人</span><b>{session.reviewer || "未填写"}</b></div>
          <div><span>更新时间</span><b>{formatTime(session.reviewUpdatedAt)}</b></div>
          <div><span>版本</span><b>r{session.reviewRevision}</b></div>
        </div>
        {session.reviewComment ? (
          <p className="review-saved"><strong>已保存审核意见：</strong>{session.reviewComment}</p>
        ) : null}
        <p className="muted small">
          静态演示使用浏览器 localStorage 保存本简报与审核状态；刷新页面后仍可恢复。清理浏览器存储会丢失记录。
          审核通过仅代表本地演示记录获得确认，不代表真实维修工单已批准、下发或执行。
        </p>
        {reviewError ? <p className="inline-warning">{reviewError}</p> : null}
        {!canReview ? <p className="inline-warning">方案不可行或数据版本已变化，当前不能提交审核。</p> : null}
        <div className="review-controls">
          <label>
            审核人
            <input value={reviewer} onChange={(event) => setReviewer(event.target.value)} placeholder="例如：演示审核人" />
          </label>
          <label>
            审核意见
            <textarea value={comment} onChange={(event) => setComment(event.target.value)} rows={3} placeholder="记录事实、约束、目标或解释方面的意见" />
          </label>
          <div className="review-actions">
            {session.reviewStatus === "draft" ? (
              <button className="button-primary" disabled={!canReview} onClick={() => review("submit")}>
                <CheckIcon size={15} /> 提交审核
              </button>
            ) : null}
            {session.reviewStatus === "pending_review" ? (
              <>
                <button className="button-primary" disabled={!canReview} onClick={() => review("approve")}>
                  <CheckIcon size={15} /> 审核通过
                </button>
                <button className="button-ghost" disabled={!canReview} onClick={() => review("reject")}>
                  驳回
                </button>
              </>
            ) : null}
            {session.reviewStatus === "approved" || session.reviewStatus === "rejected" ? (
              <button className="button-ghost" onClick={() => review("reopen")}>
                <ResetIcon size={15} /> 重新开启
              </button>
            ) : null}
          </div>
        </div>
        {exportMessage ? <p className="inline-success">{exportMessage}</p> : null}
        <p className="report-footer-note">水脉守护 AI · 合成演示简报 · 不控制真实设施 · 不派单、不关阀</p>
      </section>
    </div>
  );
}
