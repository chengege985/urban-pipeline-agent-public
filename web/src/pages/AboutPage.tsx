import { FACTORS } from "../domain/constants";
import type { WaterPulseDataset } from "../domain/types";
import { InfoIcon, LogoIcon } from "../components/Icons";
import { PageHeader } from "../components/PageHeader";

export function AboutPage({ dataset }: { dataset: WaterPulseDataset }) {
  const meta = dataset.meta;
  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="About the demo"
        title="关于水脉守护 AI"
        description="这是一个面向 Hack4Good「技术向善」的合成演示产品：解释风险、展示情景影响，并把维护选择留给人工复核。"
      />

      <section className="about-hero panel">
        <span className="about-mark"><LogoIcon size={28} /></span>
        <div>
          <h2>让地下管网风险变得可见、可解释、可复核</h2>
          <p>
            城市供水管网埋在地下，老化、漏损和爆管往往在发生后才被看见。本演示用合成片区展示
            “选管段 → 看评分 → 问原因 → 改参数 → 看方案”的巡检辅助流程，帮助非专业评委理解
            风险优先级与服务影响。
          </p>
        </div>
        <span className="about-version">{meta.modelVersion}</span>
      </section>

      <div className="about-grid">
        <section className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">DemoHealth</p><h2>演示评分规则</h2></div>
            <InfoIcon size={18} />
          </div>
          <p>
            DemoHealth 是独立演示规则，不是正式 P/C/R/H、失效概率、RUL 或水力模型。
            8 项指标每项取 1—5 分；缺失项不计权重，并降低可信度；因子贡献按加权风险占比展示。
          </p>
          <div className="weight-table">
            {FACTORS.map((factor) => (
              <div className="weight-row" key={factor.key}>
                <span>{factor.label}</span>
                <div className="weight-track"><i style={{ width: `${factor.weight * 100}%` }} /></div>
                <b>{Math.round(factor.weight * 100)}%</b>
                <small>{factor.description}</small>
              </div>
            ))}
          </div>
          <div className="formula-box">
            <code>DemoHealth = 100 × (1 − Σ(w_i × (s_i − 1) / 4) / Σw_valid)</code>
            <code>Confidence = Σw_valid / Σw_all × 100</code>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div><p className="eyebrow">Data & boundaries</p><h2>数据与边界</h2></div>
          </div>
          <dl className="attribute-grid">
            <div><dt>数据版本</dt><dd>{meta.dataVersion}</dd></div>
            <div><dt>演示模型</dt><dd>{meta.modelVersion}</dd></div>
            <div><dt>管段</dt><dd>{dataset.pipes.length} 条合成管段</dd></div>
            <div><dt>服务对象</dt><dd>{dataset.services.length} 个合成对象</dd></div>
            <div><dt>情景</dt><dd>{dataset.scenarios.length} 个故障/运行情景</dd></div>
            <div><dt>运行模式</dt><dd>demo / live / unavailable</dd></div>
          </dl>
          <ul className="boundary-list">
            <li>只使用合成数据，不读取真实 GIS、拓扑、压力、工单或企业接口。</li>
            <li>不控制真实设施，不派单、不关阀；所有建议需人工复核。</li>
            <li>不提供失效概率、剩余寿命、实时水力或物理级联结论。</li>
            <li>当前助手是本地规则/模板，不是 Qwen 或真实 LangGraph Agent。</li>
          </ul>
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div><p className="eyebrow">Provider modes</p><h2>数据访问适配层</h2></div>
        </div>
        <div className="mode-table">
          <div><strong>demo</strong><p>读取本地合成数据与 DemoHealth 规则；本轮默认模式，离线可用。</p></div>
          <div><strong>platform / live</strong><p>统一平台 FastAPI 契约接通、探测成功并通过能力检查后启用；不显示未验证数据。</p></div>
          <div><strong>unavailable</strong><p>live 请求失败时明确提示，并提供重试或切换 demo；不会把演示结果伪装成实时数据。</p></div>
        </div>
        <p className="muted small">
          未来 API 建议统一返回 dataMode、modelVersion、evidenceIds 和 limitations；真实 Agent 的数值仍应由工具计算。
        </p>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div><p className="eyebrow">Roadmap</p><h2>本轮完成与后续</h2></div>
        </div>
        <div className="roadmap">
          <div className="roadmap-done">
            <strong>本轮 C1+C2+C3</strong>
            <p>规范、{dataset.pipes.length} 条合成管段、{dataset.services.length} 个服务、{dataset.scenarios.length} 个情景、3 个候选方案、12+ 测试向量、四个页面与 demo/platform/unavailable 适配层。</p>
          </div>
          <div>
            <strong>下一轮 C4+C5</strong>
            <p>完整故障影响与隔离交互、方案硬约束比较、一页简报、人工审核；真实 Agent/API 接入需另行验收。</p>
          </div>
        </div>
      </section>

      <p className="about-disclaimer">{meta.disclaimer}</p>
    </div>
  );
}
