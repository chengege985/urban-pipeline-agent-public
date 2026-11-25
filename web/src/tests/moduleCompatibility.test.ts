import { describe, expect, it } from "vitest";
import * as oldEvidenceList from "../components/EvidenceList";
import * as newEvidenceList from "../shared/ui/EvidenceList";
import * as oldIcons from "../components/Icons";
import * as newIcons from "../shared/ui/Icons";
import * as oldStatusPill from "../components/StatusPill";
import * as newStatusPill from "../shared/ui/StatusPill";
import * as oldPageHeader from "../components/PageHeader";
import * as newPageHeader from "../shared/ui/PageHeader";
import * as oldEmptyState from "../components/EmptyState";
import * as newEmptyState from "../shared/ui/EmptyState";
import * as oldMetricCard from "../components/MetricCard";
import * as newMetricCard from "../shared/ui/MetricCard";
import * as oldAssistantPage from "../pages/AssistantPage";
import * as newAssistantPage from "../features/assistant/pages/AssistantPage";
import * as oldAssistantChat from "../components/AssistantChat";
import * as newAssistantChat from "../features/assistant/components/AssistantChat";
import * as oldToolCallCard from "../components/ToolCallCard";
import * as newToolCallCard from "../features/assistant/components/ToolCallCard";
import * as oldDecisionPanel from "../components/DecisionPanel";
import * as newDecisionPanel from "../features/cuv/components/DecisionPanel";
import * as oldFactorBars from "../components/FactorBars";
import * as newFactorBars from "../features/cuv/components/FactorBars";
import * as oldHealthGauge from "../components/HealthGauge";
import * as newHealthGauge from "../features/cuv/components/HealthGauge";
import * as oldPlanCards from "../components/PlanCards";
import * as newPlanCards from "../features/cuv/components/PlanCards";
import * as oldScenarioCards from "../components/ScenarioCards";
import * as newScenarioCards from "../features/cuv/components/ScenarioCards";
import * as oldNetworkMap from "../components/NetworkMap";
import * as newNetworkMap from "../features/network/components/NetworkMap";
import * as oldPipeRiskList from "../components/PipeRiskList";
import * as newPipeRiskList from "../features/network/components/PipeRiskList";
import * as oldServiceCards from "../components/ServiceCards";
import * as newServiceCards from "../features/network/components/ServiceCards";
import * as oldTimelinePanel from "../components/TimelinePanel";
import * as newTimelinePanel from "../features/network/components/TimelinePanel";

const compatibilityCases = [
  ["EvidenceList", oldEvidenceList, newEvidenceList],
  ["Icons", oldIcons, newIcons],
  ["StatusPill", oldStatusPill, newStatusPill],
  ["PageHeader", oldPageHeader, newPageHeader],
  ["EmptyState", oldEmptyState, newEmptyState],
  ["MetricCard", oldMetricCard, newMetricCard],
  ["AssistantPage", oldAssistantPage, newAssistantPage],
  ["AssistantChat", oldAssistantChat, newAssistantChat],
  ["ToolCallCard", oldToolCallCard, newToolCallCard],
  ["DecisionPanel", oldDecisionPanel, newDecisionPanel],
  ["FactorBars", oldFactorBars, newFactorBars],
  ["HealthGauge", oldHealthGauge, newHealthGauge],
  ["PlanCards", oldPlanCards, newPlanCards],
  ["ScenarioCards", oldScenarioCards, newScenarioCards],
  ["NetworkMap", oldNetworkMap, newNetworkMap],
  ["PipeRiskList", oldPipeRiskList, newPipeRiskList],
  ["ServiceCards", oldServiceCards, newServiceCards],
  ["TimelinePanel", oldTimelinePanel, newTimelinePanel],
] as const;

describe("module compatibility paths", () => {
  it.each(compatibilityCases)("%s resolves to the same implementation", (_name, oldModule, newModule) => {
    expect(Object.keys(oldModule).sort()).toEqual(Object.keys(newModule).sort());
    for (const name of Object.keys(oldModule)) {
      expect((oldModule as Record<string, unknown>)[name]).toBe((newModule as Record<string, unknown>)[name]);
    }
  });
});
