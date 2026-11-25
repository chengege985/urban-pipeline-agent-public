import type { HealthGrade, ProviderMode } from "../contracts/domain";
import { gradeTone } from "../utils/format";

export function GradePill({ grade }: { grade: HealthGrade }) {
  return <span className={`pill tone-${gradeTone(grade)}`}>{grade}</span>;
}

export function ModePill({ mode }: { mode: ProviderMode }) {
  const label = mode === "demo" ? "演示模式" : mode === "live" ? "实时接口" : "接口不可用";
  return <span className={`mode-pill mode-${mode}`}>{label}</span>;
}

export function SeverityPill({ severity }: { severity: "高" | "中" | "低" | "未知" }) {
  const tone = severity === "高" ? "bad" : severity === "中" ? "warn" : severity === "低" ? "ok" : "unknown";
  return <span className={`pill tone-${tone}`}>{severity === "未知" ? "未分级" : `${severity}影响`}</span>;
}

export function StatusDot({ tone = "ok" }: { tone?: "ok" | "warn" | "bad" | "unknown" }) {
  return <span className={`status-dot tone-${tone}`} aria-hidden="true" />;
}
