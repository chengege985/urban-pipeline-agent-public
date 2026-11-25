import { useMemo, useState } from "react";
import type { HealthGrade, ServiceType, WaterPulseDataset } from "../domain/types";
import { NetworkMap } from "../components/NetworkMap";
import { PageHeader } from "../components/PageHeader";
import { MetricCard } from "../components/MetricCard";
import { PipeRiskList } from "../components/PipeRiskList";
import { ScenarioCards } from "../components/ScenarioCards";
import { ServiceCards } from "../components/ServiceCards";
import { OverviewIcon, ScenarioIcon, AssistantIcon, AlertIcon, HospitalIcon } from "../components/Icons";
import { formatScore } from "../engine/format";

interface OverviewPageProps {
  dataset: WaterPulseDataset;
  onNavigate: (path: string) => void;
}

const GRADES: Array<HealthGrade | "全部"> = ["全部", "基本健康", "关注", "重点检查", "数据不足"];
const SERVICE_TYPES: Array<ServiceType | "全部"> = ["全部", "医院", "学校", "商业", "居民区"];

export function OverviewPage({ dataset, onNavigate }: OverviewPageProps) {
  const [serviceFilter, setServiceFilter] = useState<ServiceType | "全部">("全部");
  const [gradeFilter, setGradeFilter] = useState<HealthGrade | "全部">("全部");
  const platformMode = dataset.meta.providerKind === "platform";
  const firstScenario = dataset.scenarios[0];
  const serviceMap = useMemo(
    () => new Map(dataset.services.map((service) => [service.id, service])),
    [dataset.services],
  );

  const filteredPipes = useMemo(() => {
    return dataset.pipes.filter((pipe) => {
      const assessment = dataset.assessments[pipe.id];
      const serviceMatch =
        serviceFilter === "全部" ||
        pipe.serviceIds.some((id) => serviceMap.get(id)?.type === serviceFilter);
      const gradeMatch = gradeFilter === "全部" || assessment?.grade === gradeFilter;
      return serviceMatch && gradeMatch;
    });
  }, [dataset.assessments, dataset.pipes, gradeFilter, serviceFilter, serviceMap]);

  const criticalCount = dataset.services.filter((service) => service.critical).length;
  const highRiskCount = dataset.pipes.filter(
    (pipe) => dataset.assessments[pipe.id]?.grade === "重点检查",
  ).length;
  const averageScore = useMemo(() => {
    const scores = dataset.pipes
      .map((pipe) => dataset.assessments[pipe.id]?.score)
      .filter((score): score is number => typeof score === "number");
    return scores.length ? formatScore(scores.reduce((sum, score) => sum + score, 0) / scores.length) : "—";
  }, [dataset.assessments, dataset.pipes]);

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="虚拟片区 · 澄江市滨河片区"
        title="城市管网总览"
        description={platformMode
            ? `从 ${dataset.pipes.length} 条统一平台合成管段中识别优先巡检对象；所有评分均来自 FastAPI 快照。`
            : "从 24 条合成管段中识别优先巡检对象，查看服务对象与情景影响；所有数字均来自 DemoHealth 演示规则。"}
        actions={
          <>
            <button className="button-ghost" onClick={() => onNavigate("/assistant")}>
              <AssistantIcon size={16} /> 打开演示助手
            </button>
            <button className="button-primary" onClick={() => onNavigate(`/scenario/${firstScenario?.id ?? ""}?pipe=${firstScenario?.failedPipeIds[0] ?? ""}`)}>
              <ScenarioIcon size={16} /> 查看情景
            </button>
          </>
        }
      />

      <section className="metrics-grid" aria-label="片区概览">
        <MetricCard label="合成管段" value={dataset.pipes.length} hint={platformMode ? `${dataset.pipes.length} 条平台合成资产` : "24 条程序化合成资产"} icon={<OverviewIcon size={18} />} />
        <MetricCard label={platformMode ? "平均平台评分" : "平均 DemoHealth"} value={averageScore} hint={platformMode ? "平台快照 legacy_demo 0.1.1；仅用于相对比较" : "0—100，仅用于相对比较"} icon={<AlertIcon size={18} />} />
        <MetricCard label="重点检查" value={highRiskCount} hint="得分低于 65 的管段" tone={highRiskCount > 0 ? "bad" : "ok"} icon={<AlertIcon size={18} />} />
        <MetricCard label="关键服务" value={criticalCount} hint="医院、学校等关键设施" icon={<HospitalIcon size={18} />} />
      </section>

      <section className="overview-grid">
        <article className="panel map-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Network sketch</p>
              <h2>虚拟管网示意</h2>
              <p>示意布局，无真实地理坐标。线条颜色表示{platformMode ? "平台评分" : "DemoHealth"}，点击管段查看详情。</p>
            </div>
            <span className="panel-kicker">{dataset.pipes.length} 段 · {dataset.nodes.length} 节点</span>
          </div>
          <NetworkMap
            nodes={dataset.nodes}
            pipes={dataset.pipes}
            services={dataset.services}
            assessments={dataset.assessments}
            filter={serviceFilter}
            onSelectPipe={(pipeId) => onNavigate(`/pipe/${pipeId}`)}
          />
        </article>

        <article className="panel risk-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Priority list</p>
              <h2>巡检优先序</h2>
              <p>按{platformMode ? "平台评分" : "DemoHealth"}从低到高排列；缺失数据不会自动按 0 分处理。</p>
            </div>
            <span className="panel-kicker">{filteredPipes.length} 段</span>
          </div>
          <div className="filter-row">
            <label>
              服务类型
              <select value={serviceFilter} onChange={(event) => setServiceFilter(event.target.value as ServiceType | "全部")}>
                {SERVICE_TYPES.map((type) => <option key={type} value={type}>{type}</option>)}
              </select>
            </label>
            <label>
              健康等级
              <select value={gradeFilter} onChange={(event) => setGradeFilter(event.target.value as HealthGrade | "全部")}>
                {GRADES.map((grade) => <option key={grade} value={grade}>{grade}</option>)}
              </select>
            </label>
          </div>
          <PipeRiskList
            pipes={filteredPipes}
            assessments={dataset.assessments}
            services={dataset.services}
            onSelect={(pipeId) => onNavigate(`/pipe/${pipeId}`)}
            filterLabel={`${serviceFilter === "全部" ? "全部服务" : serviceFilter} · ${gradeFilter === "全部" ? "全部等级" : gradeFilter}`}
          />
        </article>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Service objects</p>
            <h2>服务对象</h2>
            <p>医院、学校、商业与居民区共用同一张合成管网；点击卡片筛选管段。</p>
          </div>
          {serviceFilter !== "全部" ? (
            <button className="text-button" onClick={() => setServiceFilter("全部")}>清除筛选</button>
          ) : null}
        </div>
        <ServiceCards services={dataset.services} activeType={serviceFilter} onSelect={setServiceFilter} />
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Scenario library</p>
            <h2>情景库</h2>
            <p>{dataset.scenarios.length} 个合成故障/运行情景；点击进入情景分析，查看拓扑通路影响。</p>
          </div>
          <button className="text-button" onClick={() => onNavigate("/assistant")}>问助手怎么读 <AssistantIcon size={14} /></button>
        </div>
        <ScenarioCards
          scenarios={dataset.scenarios}
          impacts={dataset.impacts}
          onSelect={(scenarioId) => onNavigate(`/scenario/${scenarioId}?pipe=${dataset.scenarios.find((item) => item.id === scenarioId)?.failedPipeIds[0] ?? ""}`)}
        />
      </section>

      <section className="info-strip">
        <div>
          <strong>数据口径</strong>
          <p>{dataset.pipes.length} 条管段、{dataset.services.length} 个服务对象、{dataset.scenarios.length} 个情景全部为合成数据；{platformMode ? "评分为平台快照的 legacy_demo 0.1.1，仅用于同一演示版本内的相对比较。" : "DemoHealth 只用于同一演示版本内相对比较。"}</p>
        </div>
        <div className="info-strip-meta">
          <span>{dataset.meta.modelVersion}</span>
          <span>{dataset.meta.dataVersion}</span>
          <span>不控制真实设施</span>
        </div>
      </section>
    </div>
  );
}
