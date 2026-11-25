import { useMemo, useState } from "react";
import type { HealthAssessment, NetworkNode, Pipe, ServiceObject, ServiceType } from "../../../shared/contracts/domain";
import { formatScore, gradeTone } from "../../../shared/utils/format";

interface NetworkMapProps {
  nodes: NetworkNode[];
  pipes: Pipe[];
  services: ServiceObject[];
  assessments: Record<string, HealthAssessment>;
  filter: ServiceType | "全部";
  selectedPipeId?: string;
  highlightPipeIds?: string[];
  affectedNodeIds?: string[];
  affectedServiceIds?: string[];
  showImpactLegend?: boolean;
  onSelectPipe: (pipeId: string) => void;
}

export function NetworkMap({
  nodes,
  pipes,
  services,
  assessments,
  filter,
  selectedPipeId,
  highlightPipeIds = [],
  affectedNodeIds = [],
  affectedServiceIds = [],
  showImpactLegend = false,
  onSelectPipe,
}: NetworkMapProps) {
  const [hovered, setHovered] = useState<string | null>(null);
  const nodeMap = useMemo(() => new Map(nodes.map((node) => [node.id, node])), [nodes]);
  const serviceTypeIds = useMemo(
    () => new Set(services.filter((service) => filter === "全部" || service.type === filter).map((service) => service.id)),
    [services, filter],
  );
  const highlightedPipes = useMemo(() => new Set(highlightPipeIds), [highlightPipeIds]);
  const affectedNodes = useMemo(() => new Set(affectedNodeIds), [affectedNodeIds]);
  const affectedServices = useMemo(() => new Set(affectedServiceIds), [affectedServiceIds]);
  const labelPipeId = hovered ?? selectedPipeId;
  const labelPipe = labelPipeId ? pipes.find((pipe) => pipe.id === labelPipeId) : undefined;
  const labelNodeA = labelPipe ? nodeMap.get(labelPipe.fromNodeId) : undefined;
  const labelNodeB = labelPipe ? nodeMap.get(labelPipe.toNodeId) : undefined;
  const labelX = labelNodeA && labelNodeB ? Math.max(70, Math.min(930, (labelNodeA.x + labelNodeB.x) / 2)) : 0;
  const labelY = labelNodeA && labelNodeB ? (labelNodeA.y + labelNodeB.y) / 2 : 0;

  function isDimmed(pipe: Pipe) {
    if (filter === "全部") return false;
    return !pipe.serviceIds.some((id) => serviceTypeIds.has(id));
  }

  return (
    <div className="network-map">
      <svg viewBox="0 0 1010 640" role="img" aria-label="虚拟供水管网示意图">
        <defs>
          <pattern id="map-grid" width="28" height="28" patternUnits="userSpaceOnUse">
            <path d="M 28 0 L 0 0 0 28" className="map-grid-line" />
          </pattern>
          <filter id="soft-shadow" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="3" stdDeviation="4" floodColor="#0b2740" floodOpacity=".12" />
          </filter>
        </defs>
        <rect width="1010" height="640" rx="18" className="map-bg" />
        <rect width="1010" height="640" rx="18" fill="url(#map-grid)" />
        <g className="map-zone-labels">
          <text x="55" y="52">城北水源</text>
          <text x="55" y="610">备用 / 南环</text>
          <text x="710" y="52">公共服务</text>
          <text x="710" y="610">滨河居住</text>
        </g>

        {pipes.map((pipe) => {
          const a = nodeMap.get(pipe.fromNodeId);
          const b = nodeMap.get(pipe.toNodeId);
          if (!a || !b) return null;
          const assessment = assessments[pipe.id];
          const tone = assessment ? gradeTone(assessment.grade) : "unknown";
          const selected = selectedPipeId === pipe.id;
          const highlighted = highlightedPipes.has(pipe.id);
          const dimmed = isDimmed(pipe);
          const width = selected ? 7 : Math.max(2.4, Math.min(6.4, (pipe.diameterMm ?? 150) / 100));
          return (
            <g
              key={pipe.id}
              className={`pipe-link tone-${tone} ${selected ? "selected" : ""} ${highlighted ? "failed" : ""} ${dimmed ? "dimmed" : ""}`}
              tabIndex={0}
              role="button"
              aria-label={`${pipe.id} ${pipe.name}，DemoHealth ${formatScore(assessment?.score ?? null)}`}
              onClick={() => onSelectPipe(pipe.id)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  onSelectPipe(pipe.id);
                }
              }}
              onMouseEnter={() => setHovered(pipe.id)}
              onMouseLeave={() => setHovered(null)}
              onFocus={() => setHovered(pipe.id)}
              onBlur={() => setHovered(null)}
            >
              <title>{`${pipe.id} · ${pipe.name} · DemoHealth ${formatScore(assessment?.score ?? null)}`}</title>
              <line className="pipe-hit" x1={a.x} y1={a.y} x2={b.x} y2={b.y} />
              <line className="pipe-visible" x1={a.x} y1={a.y} x2={b.x} y2={b.y} strokeWidth={width} />
            </g>
          );
        })}

        {nodes.map((node) => {
          if (node.kind === "service") {
            const type = services.find((service) => service.id === node.serviceId)?.type;
            const affected = node.serviceId ? affectedServices.has(node.serviceId) : false;
            return (
              <g
                className={`map-node service-node service-type-${type ?? "居民区"} ${affected ? "affected-service" : ""}`}
                key={node.id}
                filter="url(#soft-shadow)"
              >
                <title>{node.label}</title>
                <rect x={node.x - 13} y={node.y - 13} width="26" height="26" rx="8" />
                <circle cx={node.x} cy={node.y} r="3.5" />
                <text x={node.x + 21} y={node.y + 4} textAnchor="start">
                  {node.label}
                </text>
              </g>
            );
          }
          if (node.kind === "source") {
            return (
              <g className="map-node source-node" key={node.id}>
                <title>{node.label}</title>
                <polygon
                  points={`${node.x},${node.y - 15} ${node.x + 15},${node.y} ${node.x},${node.y + 15} ${node.x - 15},${node.y}`}
                />
                <text x={node.x} y={node.y + 32} textAnchor="middle">
                  {node.label}
                </text>
              </g>
            );
          }
          return (
            <g className={`map-node junction-node ${affectedNodes.has(node.id) ? "affected-node" : ""}`} key={node.id}>
              <title>{node.label}</title>
              <circle cx={node.x} cy={node.y} r="7" />
              <text x={node.x} y={node.y + 23} textAnchor="middle">
                {node.label}
              </text>
            </g>
          );
        })}

        {labelPipe && labelNodeA && labelNodeB ? (
          <g className="map-tooltip">
            <rect x={labelX - 66} y={labelY - 32} width="132" height="24" rx="8" />
            <text x={labelX} y={labelY - 16} textAnchor="middle">
              {labelPipe.id} · {formatScore(assessments[labelPipe.id]?.score ?? null)}
            </text>
          </g>
        ) : null}
      </svg>
      <div className="map-legend" aria-label="图例">
        <span><i className="legend-line tone-ok" />基本健康</span>
        <span><i className="legend-line tone-warn" />关注</span>
        <span><i className="legend-line tone-bad" />重点检查</span>
        <span><i className="legend-line tone-unknown" />数据不足</span>
        <span className="legend-source"><i />水源</span>
        <span className="legend-service"><i />服务对象</span>
        {showImpactLegend ? (
          <>
            <span><i className="legend-line legend-failed" />故障 / 隔离</span>
            <span><i className="legend-ring" />受影响区域</span>
          </>
        ) : null}
      </div>
    </div>
  );
}
