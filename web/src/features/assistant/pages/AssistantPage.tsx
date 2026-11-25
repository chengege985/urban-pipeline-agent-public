import type { AnalysisContext, WaterPulseDataset } from "../../../shared/contracts/domain";
import type { WaterPulseProvider } from "../../../shared/contracts/provider";
import { AssistantChat } from "../components/AssistantChat";
import { PageHeader } from "../../../shared/ui/PageHeader";
import { AssistantIcon } from "../../../shared/ui/Icons";

interface AssistantPageProps {
  dataset: WaterPulseDataset;
  provider: WaterPulseProvider;
  pipeId?: string;
  scenarioId?: string;
  onNavigate: (path: string) => void;
  context?: AnalysisContext | null;
}

export function AssistantPage({ dataset, provider, pipeId, scenarioId, onNavigate, context }: AssistantPageProps) {
  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Demo assistant"
        title="AI 智能助手入口"
        description="继承情景页的管段、隔离、修改参数和预算约束；每轮标明模板、模型或回退来源。"
        actions={<span className="page-tag"><AssistantIcon size={15} /> {provider.mode === "live" ? "API 助手" : "本地模板助手"}</span>}
      />
      <AssistantChat
        dataset={dataset}
        provider={provider}
        meta={dataset.meta}
        initialPipeId={pipeId}
        initialScenarioId={scenarioId}
        onNavigate={onNavigate}
        context={context}
      />
    </div>
  );
}
