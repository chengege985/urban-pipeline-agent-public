import type { NetworkScenario, ServiceImpact } from "../../../shared/contracts/domain";
import { ArrowRightIcon } from "../../../shared/ui/Icons";
import { SeverityPill } from "../../../shared/ui/StatusPill";

interface ScenarioCardsProps {
  scenarios: NetworkScenario[];
  impacts: Record<string, ServiceImpact>;
  onSelect: (scenarioId: string) => void;
}

export function ScenarioCards({ scenarios, impacts, onSelect }: ScenarioCardsProps) {
  return (
    <div className="scenario-grid">
      {scenarios.map((scenario) => {
        const impact = impacts[scenario.id];
        const affected = impact?.affectedServices.length ?? 0;
        const critical = impact?.affectedCriticalServiceIds.length ?? 0;
        return (
          <button className="scenario-card" key={scenario.id} onClick={() => onSelect(scenario.id)}>
            <div className="scenario-top">
              <span className="scenario-id">{scenario.id}</span>
              <SeverityPill severity={scenario.severity} />
            </div>
            <strong>{scenario.name}</strong>
            <p>{scenario.summary}</p>
            <div className="scenario-stats">
              <span>
                <b>{affected}</b> 个服务受影响
              </span>
              <span>
                <b>{critical}</b> 个关键设施
              </span>
            </div>
            <span className="card-link">
              查看情景 <ArrowRightIcon size={14} />
            </span>
          </button>
        );
      })}
    </div>
  );
}
