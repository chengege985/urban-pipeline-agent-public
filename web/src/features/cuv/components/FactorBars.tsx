import type { HealthAssessment } from "../../../shared/contracts/domain";

export function FactorBars({ assessment }: { assessment: HealthAssessment }) {
  return (
    <div className="factor-list">
      {assessment.factors.map((factor) => {
        const contribution = Math.round(factor.contribution * 100);
        const missing = factor.score === null;
        return (
          <div className={`factor-row ${missing ? "factor-missing" : ""}`} key={factor.key} title={factor.detail}>
            <div className="factor-name">
              <strong>{factor.label}</strong>
              <small>权重 {Math.round(factor.weight * 100)}%</small>
            </div>
            <div className="factor-track" aria-hidden="true">
              <span style={{ width: `${missing ? 0 : contribution}%` }} />
            </div>
            <span className="factor-score">{missing ? "缺失" : `${factor.score} 分`}</span>
            <span className="factor-share">{missing ? "未计权重" : `${contribution}%`}</span>
          </div>
        );
      })}
    </div>
  );
}
