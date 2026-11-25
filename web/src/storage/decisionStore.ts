import type { DecisionSession, DecisionSnapshot, PipeOverride, ReviewStatus } from "../domain/types";

export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

const STORAGE_KEY = "waterpulse.decision.sessions.v1";

export type ReviewAction = "submit" | "approve" | "reject" | "reopen";

export interface CreateDecisionSessionInput {
  pipeId: string;
  scenarioId: string;
  isolationPlanId: string;
  budgetYuan: number;
  resourceCapacities: Record<string, number>;
  confirmedDependencies: string[];
  selectedPlanId: string;
  dataVersion: string;
  algorithmVersion: string;
  evidenceIds: string[];
  override?: PipeOverride;
  decisionAlgorithmVersion?: string;
  snapshot?: DecisionSnapshot;
}

function makeId(): string {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `decision-${random}`;
}

export function createDecisionSession(input: CreateDecisionSessionInput): DecisionSession {
  return {
    id: makeId(),
    createdAt: new Date().toISOString(),
    pipeId: input.pipeId,
    scenarioId: input.scenarioId,
    isolationPlanId: input.isolationPlanId,
    budgetYuan: input.budgetYuan,
    resourceCapacities: { ...input.resourceCapacities },
    confirmedDependencies: [...input.confirmedDependencies],
    selectedPlanId: input.selectedPlanId,
    reviewStatus: "draft",
    reviewComment: "",
    reviewer: "",
    reviewRevision: 0,
    reviewUpdatedAt: "",
    dataVersion: input.dataVersion,
    algorithmVersion: input.algorithmVersion,
    evidenceIds: [...new Set(input.evidenceIds)],
    override: structuredClone(input.override ?? {}),
    decisionAlgorithmVersion: input.decisionAlgorithmVersion,
    snapshot: input.snapshot ? structuredClone(input.snapshot) : undefined,
  };
}

const TRANSITIONS: Record<ReviewAction, { from: ReviewStatus[]; to: ReviewStatus; label: string }> = {
  submit: { from: ["draft"], to: "pending_review", label: "提交审核" },
  approve: { from: ["pending_review"], to: "approved", label: "审核通过" },
  reject: { from: ["pending_review"], to: "rejected", label: "驳回" },
  reopen: { from: ["approved", "rejected"], to: "draft", label: "重新开启" },
};

export function transitionDecisionReview(
  session: DecisionSession,
  action: ReviewAction,
  reviewer: string,
  comment: string,
): DecisionSession {
  const transition = TRANSITIONS[action];
  if (!transition.from.includes(session.reviewStatus)) {
    throw new Error(`当前状态“${session.reviewStatus}”不能执行“${transition.label}”`);
  }
  if (!reviewer.trim()) throw new Error("审核人不能为空");
  if (action === "reject" && !comment.trim()) throw new Error("驳回必须填写审核意见");
  return {
    ...session,
    reviewStatus: transition.to,
    reviewer: reviewer.trim(),
    reviewComment: comment.trim(),
    reviewRevision: session.reviewRevision + 1,
    reviewUpdatedAt: new Date().toISOString(),
  };
}

export class DecisionStore {
  constructor(private readonly storage: StorageLike | null) {}

  private readAll(): Record<string, DecisionSession> {
    if (!this.storage) return {};
    try {
      const raw = this.storage.getItem(STORAGE_KEY);
      if (!raw) return {};
      const parsed = JSON.parse(raw) as unknown;
      return parsed && typeof parsed === "object" ? (parsed as Record<string, DecisionSession>) : {};
    } catch {
      return {};
    }
  }

  loadAll(): DecisionSession[] {
    return Object.values(this.readAll()).sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  }

  load(id: string): DecisionSession | null {
    return this.readAll()[id] ?? null;
  }

  save(session: DecisionSession): void {
    if (!this.storage) throw new Error("当前环境没有可用的本地存储");
    const all = this.readAll();
    all[session.id] = session;
    this.storage.setItem(STORAGE_KEY, JSON.stringify(all));
  }

  create(input: CreateDecisionSessionInput): DecisionSession {
    const session = createDecisionSession(input);
    this.save(session);
    return session;
  }

  review(id: string, action: ReviewAction, reviewer: string, comment: string): DecisionSession {
    const session = this.load(id);
    if (!session) throw new Error("决策简报不存在");
    const updated = transitionDecisionReview(session, action, reviewer, comment);
    this.save(updated);
    return updated;
  }
}

export function browserDecisionStore(): DecisionStore {
  try {
    return new DecisionStore(typeof window === "undefined" ? null : window.localStorage);
  } catch {
    return new DecisionStore(null);
  }
}
