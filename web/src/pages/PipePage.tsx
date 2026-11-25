import type { WaterPulseDataset } from "../domain/types";
import { EmptyState } from "../components/EmptyState";
import { EvidenceList } from "../components/EvidenceList";
import { FactorBars } from "../components/FactorBars";
import { HealthGauge } from "../components/HealthGauge";
import { ArrowRightIcon, PipeIcon, ResetIcon, ScenarioIcon } from "../components/Icons";
import { PageHeader } from "../components/PageHeader";
import { GradePill } from "../components/StatusPill";
import { formatScore } from "../engine/format";

interface PipePageProps {
  dataset: WaterPulseDataset;
  pipeId?: string;
  onNavigate: (path: string) => void;
}

export function PipePage({ dataset, pipeId, onNavigate }: PipePageProps) {
  const pipe = dataset.pipes.find((item) => item.id === pipeId) ?? dataset.pipes[0];
  if (!pipe) {
    return <EmptyState title="没有可用管段" message="当前演示数据没有管段。" />;
  }
  const assessment = dataset.assessments[pipe.id];
  const serviceMap = new Map(dataset.services.map((service) => [service.id, service]));
  const age = pipe.installYear === null ? null : 2026 - pipe.installYear;
  const scenario = dataset.scenarios.find((item) => item.failedPipeIds.includes(pipe.id)) ?? dataset.scenarios[0];
  const serviceNames = pipe.serviceIds.map((id) => serviceMap.get(id)?.name).filter(Boolean);

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Pipe health analysis"
        title={`${pipe.id} · ${pipe.name}`}
        description="查看管段资产、演示健康评分、因子贡献与数据完整性；分数用于同一演示版本内的相对比较。"
        actions={
          <>
            <label className="inline-select">
              切换管段
              <select value={pipe.id} onChange={(event) => onNavigate(`/pipe/${event.target.value}`)}>
                {dataset.pipes.map((item) => <option key={item.id} value={item.id}>{item.id} · {item.name}</option>)}
              </select>
            </label>
            <button className="button-primary" onClick={() => onNavigate(`/scenario/${scenario.id}?pipe=${pipe.id}`)}>
              <ScenarioIcon size={16} /> 在情景中测试
            </button>
          </>
        }
      />

      <div className="detail-grid">
        <section className="panel score-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">DemoHealth snapshot</p>
              <h2>演示健康评价</h2>
            </div>
            <GradePill grade={assessment.grade} />
          </div>
          <div className="score-layout">
            <HealthGauge assessment={assessment} />
            <div className="score-copy">
              <p>{assessment.summary}</p>
              <div className="score-stats">
                <span><b>{assessment.missing.length}</b><small>缺失项</small></span>
                <span><b>{Math.round(assessment.confidence)}%</b><small>有效权重</small></span>
                <span><b>{assessment.version}</b><small>规则版本</small></span>
              </div>
              {assessment.missing.length ? (
                <p className="inline-warning">缺失：{assessment.missing.join("、")}。缺失项不计权重，也不会自动按 0 分处理。</p>
              ) : (
                <p className="inline-success">当前 8 项指标均有记录，可信度为 100%。</p>
              )}
            </div>
          </div>
        </section>

        <aside className="panel attributes-panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Asset profile</p>
              <h2>资产信息</h2>
            </div>
            <PipeIcon size={20} />
          </div>
          <dl className="attribute-grid">
            <div><dt>管材</dt><dd>{pipe.material}</dd></div>
            <div><dt>投运年份</dt><dd>{pipe.installYear ?? "缺失"}</dd></div>
            <div><dt>管龄</dt><dd>{age === null ? "缺失" : `${age} 年`}</dd></div>
            <div><dt>管径</dt><dd>{pipe.diameterMm ? `${pipe.diameterMm} mm` : "缺失"}</dd></div>
            <div><dt>埋深</dt><dd>{pipe.depthM === null ? "缺失" : `${pipe.depthM} m`}</dd></div>
            <div><dt>运行压力</dt><dd>{pipe.pressure}</dd></div>
            <div><dt>漏损次数</dt><dd>{pipe.leakCount ?? "缺失"}</dd></div>
            <div><dt>维修次数</dt><dd>{pipe.repairCount ?? "缺失"}</dd></div>
            <div><dt>监测状态</dt><dd>{pipe.monitoring}</dd></div>
            <div><dt>最近巡检</dt><dd>{pipe.lastInspection ?? "缺失"}</dd></div>
            <div><dt>管段长度</dt><dd>{pipe.lengthM === null ? "缺失" : `${pipe.lengthM} m`}</dd></div>
            <div><dt>连接节点</dt><dd>{pipe.fromNodeId} → {pipe.toNodeId}</dd></div>
          </dl>
          <div className="service-tags">
            <span className="muted small">服务对象</span>
            {serviceNames.map((name) => <span className="tag" key={name}>{name}</span>)}
          </div>
          <p className="muted small">{pipe.note}</p>
        </aside>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Factor contribution</p>
            <h2>风险因子贡献</h2>
            <p>贡献占比 = 该因子加权风险 / 全部有效因子加权风险；权重高不等于贡献大。</p>
          </div>
          <span className="panel-kicker">当前 {formatScore(assessment.score)}</span>
        </div>
        <FactorBars assessment={assessment} />
      </section>

      <div className="detail-grid lower">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">How it is calculated</p>
              <h2>计算依据</h2>
            </div>
            <ResetIcon size={18} />
          </div>
          <details className="calculation-details" open>
            <summary>查看演示公式与缺失处理</summary>
            <div className="formula-box">
              <code>r_i = w_i × (s_i − 1) / 4</code>
              <code>R = Σr_i / Σw_valid</code>
              <code>DemoHealth = 100 × (1 − R)</code>
              <code>Confidence = Σw_valid / Σw_all × 100</code>
            </div>
            <p>
              8 项指标每项取 1—5 分，1 最好、5 最差；缺失指标从分母中剔除，并降低可信度。
              等级阈值：≥80 基本健康、≥65 关注、其余重点检查。
            </p>
            <p className="muted">
              这是比赛演示规则，不是正式 P/C/R/H、失效概率、RUL 或水力模型；版本为 {assessment.version}。
            </p>
          </details>
        </section>

        <aside className="panel">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">Evidence & next step</p>
              <h2>证据与下一步</h2>
            </div>
            <ArrowRightIcon size={18} />
          </div>
          <EvidenceList ids={[`rule:demo-health-${assessment.version}`, `data:${pipe.id}`, "rule:missing-policy"]} />
          <div className="next-step-card">
            <strong>下一步：在情景中改数</strong>
            <p>调整管龄、漏损次数或压力，观察 DemoHealth 与因子贡献如何变化。当前改动只作用于模拟输入。</p>
            <button className="button-ghost" onClick={() => onNavigate(`/scenario/${scenario.id}?pipe=${pipe.id}`)}>
              打开情景分析 <ArrowRightIcon size={14} />
            </button>
          </div>
        </aside>
      </div>
    </div>
  );
}
