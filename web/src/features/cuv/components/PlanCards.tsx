import type { PlanComparison } from "../../../shared/contracts/domain";
import { formatCurrency } from "../../../shared/utils/format";

export function PlanCards({ comparison }: { comparison: PlanComparison }) {
  return (
    <div className="plan-list">
      <p className="plan-note">{comparison.utilityNote}</p>
      {comparison.rows.map((row) => (
        <div className={`plan-row ${row.rank === 1 ? "plan-best" : ""}`} key={row.planId}>
          <span className="plan-rank">{row.rank === 1 ? "推荐" : `#${row.rank}`}</span>
          <div className="plan-copy">
            <strong>{row.name}</strong>
            <small>{row.note}</small>
          </div>
          <div className="plan-metrics">
            <span>
              <b>{formatCurrency(row.costYuan)}</b>
              <small>演示费用</small>
            </span>
            <span>
              <b>{row.durationHours}h</b>
              <small>工期</small>
            </span>
            <span>
              <b>{row.riskReduction === null ? "—" : `-${row.riskReduction}`}</b>
              <small>风险下降</small>
            </span>
            <span>
              <b>{row.restoredServiceCount ?? "—"}</b>
              <small>恢复服务</small>
            </span>
          </div>
        </div>
      ))}
      <p className="muted small">本轮只读展示候选方案；硬约束、预算与人工审核在 C4/C5 阶段实现。</p>
    </div>
  );
}
