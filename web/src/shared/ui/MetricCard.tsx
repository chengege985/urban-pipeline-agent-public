import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  value: ReactNode;
  hint?: string;
  icon?: ReactNode;
  tone?: "default" | "ok" | "warn" | "bad";
}

export function MetricCard({ label, value, hint, icon, tone = "default" }: MetricCardProps) {
  return (
    <article className={`metric-card tone-${tone}`}>
      <div className="metric-top">
        <span className="metric-label">{label}</span>
        {icon ? <span className="metric-icon">{icon}</span> : null}
      </div>
      <strong className="metric-value">{value}</strong>
      {hint ? <small className="metric-hint">{hint}</small> : null}
    </article>
  );
}
