import type {
  GasCuvCollaborationRequest, GasCuvCollaborationSnapshot, GasCuvCriteria, GasCuvReplayResponse,
} from "../../shared/contracts/platform";

export type CuvCollaborationIdentity = Pick<GasCuvReplayResponse, "case_id" | "scenario_id" | "scenario_at" | "topology_evidence" | "plans">;

export const cuvObjectives = {
  min_affected_service_hours: "优先减少累计图不可达影响",
  min_topology_restoration_hours: "优先缩短图可达恢复时间",
} satisfies Record<GasCuvCriteria["objective"], string>;

export const cuvReasonLabels: Record<string, string> = {
  base_plan_blocked: "基础推演受阻",
  peak_limit_exceeded: "超过新增图不可达服务峰值上限",
  recovery_deadline_missed: "超过图可达恢复时限",
  peak_unknown: "新增图不可达服务峰值未知",
  recovery_unknown: "图可达恢复时间未知",
  objective_unknown: "目标指标未知",
};

export type CuvCriteriaDraft = {
  objective: GasCuvCriteria["objective"];
  peak: string;
  deadline: string;
  feedback: string;
};

const object = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === "string" && value.trim().length > 0;
const list = (value: unknown, check: (item: unknown) => boolean): boolean => Array.isArray(value) && value.every(check);
const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value) && value >= 0;
const count = (value: unknown): value is number => finite(value) && Number.isSafeInteger(value);
const time = (value: unknown): value is string => typeof value === "string" && Number.isFinite(Date.parse(value))
  && /T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value);
const digest = (value: unknown): boolean => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const nullable = (value: unknown, check: (item: unknown) => boolean): boolean => value === null || check(value);

function validFeedback(value: unknown): value is string {
  return text(value) && [...value].length <= 280 && !/\p{C}/u.test(value);
}

export function gasCuvCriteria(value: unknown): value is GasCuvCriteria {
  return object(value) && typeof value.objective === "string" && Object.hasOwn(cuvObjectives, value.objective)
    && nullable(value.max_peak_affected_services, count) && nullable(value.restore_by_hours, finite);
}

export function gasCuvRankedComparison(value: unknown, plans?: CuvCollaborationIdentity["plans"]): boolean {
  if (!object(value) || (value.status !== "ranked" && value.status !== "no_eligible_plan")
    || !Array.isArray(value.plans) || !Array.isArray(value.leading_plan_ids)) return false;
  const planIds = new Set<string>();
  const leadingIds = new Set<string>();
  for (const entry of value.plans) {
    if (!object(entry) || !text(entry.plan_id) || !text(entry.label) || planIds.has(entry.plan_id)
      || !nullable(entry.affected_service_hours, finite) || !nullable(entry.topology_restoration_hours, finite)
      || !nullable(entry.peak_affected_service_count, count)
      || !list(entry.reasons, (reason) => text(reason) && Object.hasOwn(cuvReasonLabels, reason))) return false;
    planIds.add(entry.plan_id);
    if (entry.status === "eligible") {
      if (!count(entry.rank) || entry.rank < 1) return false;
      if (entry.rank === 1) leadingIds.add(entry.plan_id);
    } else if ((entry.status !== "excluded" && entry.status !== "insufficient_data") || entry.rank !== null) {
      return false;
    }
    if (plans) {
      const base = plans.find((plan) => plan.plan_id === entry.plan_id);
      if (!base || base.label !== entry.label
        || base.affected_service_hours !== entry.affected_service_hours
        || base.topology_restoration_hours !== entry.topology_restoration_hours
        || base.peak_affected_service_count !== entry.peak_affected_service_count) return false;
    }
  }
  return (!plans || planIds.size === plans.length)
    && (value.status === "ranked" ? leadingIds.size > 0 : leadingIds.size === 0)
    && value.leading_plan_ids.length === leadingIds.size
    && new Set(value.leading_plan_ids).size === leadingIds.size
    && value.leading_plan_ids.every((id) => text(id) && leadingIds.has(id));
}

export function gasCuvCollaborationSnapshot(value: unknown, report: CuvCollaborationIdentity,
  request?: GasCuvCollaborationRequest): value is GasCuvCollaborationSnapshot {
  if (!object(value) || value.data_mode !== "synthetic" || value.validation_status !== "not_business_validated"
    || (value.persistence !== "process_memory" && value.persistence !== "sqlite_local")
    || value.case_id !== report.case_id || value.scenario_id !== report.scenario_id
    || !time(value.scenario_at) || Date.parse(value.scenario_at) !== Date.parse(report.scenario_at)
    || !object(value.topology_evidence) || !digest(value.input_sha256) || !count(value.revision)
    || !gasCuvCriteria(value.criteria) || !gasCuvRankedComparison(value.comparison, report.plans)
    || !Array.isArray(value.audit)
    || !list(value.unknown_metrics, (item) => object(item) && text(item.code)
      && item.status === "unknown" && item.value === null && text(item.reason))
    || !list(value.missing_items, (item) => object(item) && text(item.code) && text(item.description))) return false;
  const evidence = value.topology_evidence;
  if (!Object.entries(report.topology_evidence).every(([key, expected]) => evidence[key] === expected)) return false;
  if (request && (value.input_sha256 !== request.input_sha256 || value.revision !== request.expected_revision + 1
    || value.criteria.objective !== request.criteria.objective
    || value.criteria.max_peak_affected_services !== request.criteria.max_peak_affected_services
    || value.criteria.restore_by_hours !== request.criteria.restore_by_hours)) return false;
  return value.audit.every((entry) => object(entry) && count(entry.revision) && entry.revision > 0
    && text(entry.actor_id) && time(entry.occurred_at) && digest(entry.input_sha256)
    && gasCuvCriteria(entry.previous_criteria) && gasCuvCriteria(entry.criteria)
    && validFeedback(entry.feedback) && gasCuvRankedComparison(entry.comparison));
}

export function cuvCriteriaDraft(criteria: GasCuvCriteria): CuvCriteriaDraft {
  return {
    objective: criteria.objective,
    peak: criteria.max_peak_affected_services === null ? "" : String(criteria.max_peak_affected_services),
    deadline: criteria.restore_by_hours === null ? "" : String(criteria.restore_by_hours),
    feedback: "",
  };
}

export function prepareCuvConfirmation(
  draft: CuvCriteriaDraft, snapshot: Pick<GasCuvCollaborationSnapshot, "input_sha256" | "revision">,
): Omit<GasCuvCollaborationRequest, "confirmed"> | null {
  const peak = draft.peak.trim(), deadline = draft.deadline.trim();
  const criteria = {
    objective: draft.objective,
    max_peak_affected_services: peak ? Number(peak) : null,
    restore_by_hours: deadline ? Number(deadline) : null,
  };
  const feedback = draft.feedback.trim();
  if (!gasCuvCriteria(criteria) || !validFeedback(feedback)) return null;
  return { input_sha256: snapshot.input_sha256, expected_revision: snapshot.revision, criteria, feedback };
}
