import type { HealthAssessment } from "../../../shared/contracts/domain";
import { formatScore, gradeTone } from "../../../shared/utils/format";

const RADIUS = 52;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function HealthGauge({ assessment }: { assessment: HealthAssessment }) {
  const tone = gradeTone(assessment.grade);
  const score = assessment.score ?? 0;
  const offset = CIRCUMFERENCE * (1 - score / 100);
  return (
    <div className={`health-gauge tone-${tone}`}>
      <svg viewBox="0 0 140 140" role="img" aria-label={`DemoHealth ${formatScore(assessment.score)} 分，${assessment.grade}`}>
        <circle className="gauge-track" cx="70" cy="70" r={RADIUS} />
        <circle
          className="gauge-progress"
          cx="70"
          cy="70"
          r={RADIUS}
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={assessment.score === null ? CIRCUMFERENCE : offset}
          transform="rotate(-90 70 70)"
        />
        <text className="gauge-score" x="70" y="68" textAnchor="middle">
          {formatScore(assessment.score)}
        </text>
        <text className="gauge-caption" x="70" y="87" textAnchor="middle">
          DemoHealth
        </text>
      </svg>
      <div className="gauge-meta">
        <span className={`pill tone-${tone}`}>{assessment.grade}</span>
        <small>可信度 {Math.round(assessment.confidence)}%</small>
      </div>
    </div>
  );
}
