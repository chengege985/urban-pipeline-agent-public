import type { BackendReportDocument } from "../adapters/platformReport";
import type {
  AgentContext,
  AgentResponse,
  DemoConstraints,
  HealthAssessment,
  Pipe,
  PipeOverride,
  PlanComparison,
  PlanTimelineResult,
  ProviderMode,
  ServiceImpact,
  TimelineGate,
  TimelineRequest,
  WaterPulseDataset,
} from "../domain/types";

export interface WaterPulseProvider {
  readonly mode: ProviderMode;
  loadDataset(): Promise<WaterPulseDataset>;
  evaluate(pipe: Pipe, override?: PipeOverride): Promise<HealthAssessment>;
  simulate(scenarioId: string, isolationPlanId?: string): Promise<ServiceImpact>;
  comparePlans(
    scenarioId: string,
    planIds?: string[],
    constraints?: DemoConstraints,
    isolationPlanId?: string,
  ): Promise<PlanComparison>;
  checkTimeline(request: TimelineRequest): Promise<TimelineGate>;
  timeline(request: TimelineRequest): Promise<PlanTimelineResult>;
  ask(question: string, context: AgentContext): Promise<AgentResponse>;
  /** Read an existing backend report without starting any calculation. */
  report?(runId: string): Promise<BackendReportDocument>;
  probe?(): Promise<boolean>;
}

export class ProviderUnavailableError extends Error {
  constructor(message = "实时接口未接通，当前不能使用 live 模式。") {
    super(message);
    this.name = "ProviderUnavailableError";
  }
}
