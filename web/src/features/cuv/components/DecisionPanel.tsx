import type { DemoConstraints, PlanComparison, PlanComparisonRow } from "../../../shared/contracts/domain";
import { DEPENDENCY_LABELS, RESOURCE_LABELS } from "../../../shared/contracts/constants";
import { formatCurrency } from "../../../shared/utils/format";
import { AlertIcon, ArrowRightIcon, CheckIcon } from "../../../shared/ui/Icons";

interface DecisionPanelProps {
  comparison: PlanComparison;
  constraints: DemoConstraints;
  selectedPlanId: string | null;
  onConstraintsChange: (next: DemoConstraints) => void;
  onSelectPlan: (planId: string | null) => void;
  onGenerateReport: () => void;
  disabled?: boolean;
  reportAvailable?: boolean;
  showReportAction?: boolean;
  reportUnavailableReason?: string;
}

function rowStatus(row: PlanComparisonRow, recommended: boolean) {
  if (row.feasible === null) return <span className="plan-state state-neutral">结果未知</span>;
  if (!row.feasible) return <span className="plan-state state-bad">不可行</span>;
  if (recommended) return <span className="plan-state state-ok">演示推荐</span>;
  return <span className="plan-state state-neutral">可行</span>;
}

export function DecisionPanel({
  comparison,
  constraints,
  selectedPlanId,
  onConstraintsChange,
  onSelectPlan,
  onGenerateReport,
  disabled = false,
  reportAvailable = true,
  showReportAction = true,
  reportUnavailableReason = "当前不支持生成权威报告。",
}: DecisionPanelProps) {
  const selected = comparison.rows.find((row) => row.planId === selectedPlanId && row.feasible === true);
  const recommended = comparison.rows.find((row) => row.planId === comparison.recommendedPlanId);

  function setBudget(value: number) {
    onConstraintsChange({ ...constraints, budgetYuan: Math.max(0, value) });
  }

  function setCapacity(resource: string, value: number) {
    onConstraintsChange({
      ...constraints,
      resourceCapacities: { ...constraints.resourceCapacities, [resource]: Math.max(0, value) },
    });
  }

  function toggleDependency(dependency: string) {
    const next = constraints.confirmedDependencies.includes(dependency)
      ? constraints.confirmedDependencies.filter((item) => item !== dependency)
      : [...constraints.confirmedDependencies, dependency];
    onConstraintsChange({ ...constraints, confirmedDependencies: next });
  }

  return (
    <div className="decision-panel">
      <div className="decision-constraints">
        <label>
          演示预算（元）
          <input
            type="number"
            min="0"
            step="1000"
            value={constraints.budgetYuan}
            onChange={(event) => setBudget(Number(event.target.value))}
          />
        </label>
        {Object.entries(RESOURCE_LABELS).map(([key, label]) => (
          <label key={key}>
            {label}
            <input
              type="number"
              min="0"
              max="9"
              value={constraints.resourceCapacities[key] ?? 0}
              onChange={(event) => setCapacity(key, Number(event.target.value))}
            />
          </label>
        ))}
      </div>

      <div className="dependency-row">
        {Object.entries(DEPENDENCY_LABELS).map(([key, label]) => (
          <label className="check-label" key={key}>
            <input
              type="checkbox"
              checked={constraints.confirmedDependencies.includes(key)}
              onChange={() => toggleDependency(key)}
            />
            {label}
          </label>
        ))}
      </div>

      {comparison.rows.length === 0 ? (
        <p className="inline-warning">没有候选方案。</p>
      ) : (
        <div className="decision-list">
          {comparison.rows.map((row) => {
            const isRecommended = row.planId === comparison.recommendedPlanId;
            const isSelected = row.planId === selectedPlanId;
            return (
              <div className={`decision-row ${isSelected ? "selected" : ""} ${row.feasible === false ? "infeasible" : ""}`} key={row.planId}>
                <div className="decision-row-top">
                  <div>
                    <span className="plan-rank">{isRecommended ? "推荐" : `#${row.rank}`}</span>
                    <strong>{row.name}</strong>
                    {rowStatus(row, isRecommended)}
                  </div>
                  <div className="decision-buttons">
                    {row.feasible === true ? (
                      <button
                        className={`button-ghost ${isSelected ? "button-selected" : ""}`}
                        disabled={disabled}
                        onClick={() => onSelectPlan(isSelected ? null : row.planId)}
                      >
                        {isSelected ? <><CheckIcon size={14} /> 已选择</> : "选择方案"}
                      </button>
                    ) : (
                      <span className="infeasible-note">不可选</span>
                    )}
                  </div>
                </div>
                <div className="decision-metrics">
                  <span><b>{formatCurrency(row.costYuan)}</b><small>演示费用</small></span>
                  <span><b>{row.durationHours}h</b><small>方案完成时间（假设）</small></span>
                  <span><b>{row.riskReduction === null ? "—" : `-${row.riskReduction}`}</b><small>风险下降</small></span>
                  <span><b>{row.remainingAffectedServices ?? "—"}</b><small>剩余影响</small></span>
                  <span><b>{row.utility === null ? "—" : row.utility.toFixed(3)}</b><small>演示效用</small></span>
                </div>
                <p className="decision-note">{row.note}</p>
                {row.violations.length ? (
                  <ul className="violation-list">
                    {row.violations.map((violation) => <li key={violation}><AlertIcon size={12} /> {violation}</li>)}
                  </ul>
                ) : null}
              </div>
            );
          })}
        </div>
      )}

      <div className="decision-footer">
        <div>
          {recommended ? (
            <p className="decision-recommendation">
              演示建议：<strong>{recommended.name}</strong>{selected && selected.planId !== recommended.planId ? "；你已选择其他方案。" : "。"}
            </p>
          ) : (
            <p className="inline-warning">当前没有满足全部硬约束的方案，不能生成决策建议。</p>
          )}
          <small>{comparison.utilityNote}</small>
        </div>
        {showReportAction ? <button className="button-primary" onClick={onGenerateReport} disabled={disabled || !selected || !reportAvailable}>
          <ArrowRightIcon size={15} /> 生成决策简报
        </button> : null}
      </div>
      {showReportAction && !selected ? <p className="muted small">请选择一个可行方案后才能生成简报。</p> : null}
      {showReportAction && !reportAvailable ? <p className="inline-warning">{reportUnavailableReason}</p> : null}
    </div>
  );
}
