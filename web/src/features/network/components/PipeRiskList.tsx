import type { HealthAssessment, Pipe, ServiceObject, ServiceType } from "../../../shared/contracts/domain";
import { formatScore, gradeTone } from "../../../shared/utils/format";
import { EmptyState } from "../../../shared/ui/EmptyState";
import { GradePill } from "../../../shared/ui/StatusPill";

interface PipeRiskListProps {
  pipes: Pipe[];
  assessments: Record<string, HealthAssessment>;
  services: ServiceObject[];
  onSelect: (pipeId: string) => void;
  filterLabel?: string;
}

export function PipeRiskList({ pipes, assessments, services, onSelect, filterLabel }: PipeRiskListProps) {
  const sorted = [...pipes].sort((a, b) => {
    const scoreA = assessments[a.id]?.score;
    const scoreB = assessments[b.id]?.score;
    if (scoreA === null || scoreA === undefined) return 1;
    if (scoreB === null || scoreB === undefined) return -1;
    return scoreA - scoreB;
  });
  const serviceMap = new Map(services.map((service) => [service.id, service]));

  if (sorted.length === 0) {
    return <EmptyState title="没有匹配的管段" message="请调整服务类型或等级筛选。" />;
  }

  return (
    <div className="risk-list">
      {filterLabel ? <p className="list-caption">{filterLabel} · 按评分升序</p> : null}
      {sorted.map((pipe, index) => {
        const assessment = assessments[pipe.id];
        const tone = assessment ? gradeTone(assessment.grade) : "unknown";
        const serviceTypes = pipe.serviceIds
          .map((id) => serviceMap.get(id)?.type)
          .filter((value, idx, arr): value is ServiceType => Boolean(value) && arr.indexOf(value) === idx);
        return (
          <button className="risk-row" key={pipe.id} onClick={() => onSelect(pipe.id)}>
            <span className="risk-rank">{String(index + 1).padStart(2, "0")}</span>
            <span className="risk-main">
              <strong>{pipe.id}</strong>
              <small>
                {pipe.name} · {serviceTypes.join(" / ") || "未关联服务"}
              </small>
            </span>
            <span className={`risk-score tone-${tone}`}>{formatScore(assessment?.score ?? null)}</span>
            <GradePill grade={assessment?.grade ?? "数据不足"} />
          </button>
        );
      })}
    </div>
  );
}
