import type { GasAgentEvidenceScope } from "../../shared/contracts/platform";
import { gasAgentPresentation } from "./gasTheme";
import { useGasCockpit } from "./GasCockpitProvider";
import { toolAssetIds, toolEventId, toolEventIds } from "./gasAgentApi";
import { gasCuvAgentData } from "./cuvAgentContext";
import { CuvEngineeringCapabilities } from "./GasCuvEngineeringPanel";
import { GasCuvBenchmarkEvidence } from "./GasCuvBenchmarkPanel";
import { gasMonitoringObservation } from "./monitoringContract";

const evidenceLabel: Record<GasAgentEvidenceScope["scope"], string> = {
  gas_m0_case: "GAS-M0 合成案例",
  gas_m0_monitoring: "GAS-M0 合成监测",
  gas_m0_graph_cuv: "GAS-M0 合成 CUV · 图连通",
  pilot_history: "试点历史回放",
  public_reference_benchmark: "公开单管基准",
};

const samples = ["查看K-001管段的信息", "查看当前有哪些燃气事件", "定位隔离事件关联的管段", "目前支持哪些CUV分析？", "解释这个隔离事件"];
export function GasAgentPanel() {
  const c = useGasCockpit(), agent = c.agent, result = agent.result;
  const display = gasAgentPresentation(agent);
  const blocked = result?.error_code === "cross_dataset_unverified" || result?.error_code === "engineering_evidence_unavailable"
    || result?.error_code === "benchmark_scope_conflict";
  return <section className="gas-assistant-panel" data-testid="gas-assistant-state" data-agent-status={agent.status}>
    <div className="gas-agent-health-card" data-agent-tone={display.tone}><h3>{display.label}</h3><p>{display.detail}</p><span>燃气智能体 · 授权只读工具查询</span></div>
    <dl className="gas-fields" data-testid="gas-agent-execution-banner">
      <div><dt>模型状态</dt><dd>{agent.health?.model_status ?? "unknown"} · {agent.health?.provider_configured ? "已配置" : "未接入模型"}</dd></div>
      <div><dt>只读工具查询</dt><dd data-testid="gas-agent-tools-status">{agent.health?.tools_status === "ready" ? "可用 · ready" : agent.probing ? "检测中" : "未确认"}</dd></div>
      <div><dt>解析来源</dt><dd>{result?.parser_source ?? agent.health?.parser_source ?? "unknown"}</dd></div>
    </dl>
    <p className="gas-window-note">{agent.health?.reason ?? "发送后由服务端校验身份并查询，不在前端拼接回答。模型与工具可用性分别显示。"}</p>
    {agent.healthError ? <p role="alert">{agent.healthError.message}</p> : null}
    {c.route.gasEventId || c.route.gasAssetId ? <dl className="gas-fields" data-testid="gas-agent-current-context">
      {c.route.gasEventId ? <div><dt>当前事件</dt><dd>{c.route.gasEventId}</dd></div> : null}
      {c.route.gasAssetId ? <div><dt>当前资产</dt><dd>{c.route.gasAssetId}</dd></div> : null}
    </dl> : null}
    {agent.history.length ? <details className="gas-agent-history" data-testid="gas-agent-history">
      <summary>本次对话 · {agent.history.length} 轮（最多20轮，不写浏览器存储）</summary>
      {agent.history.map((turn,index) => <article className="gas-agent-turn" key={turn.result?.request_id ?? index}>
        <strong>{index+1}. {turn.input}</strong><p>{turn.result ? `${turn.result.parser_source} · ${turn.result.intent} · ${turn.result.status}` : turn.error?.message}</p>
        {turn.result?.tools.map((tool,n) => <span key={n}>{toolAssetIds(tool).map((id) =>
          <button type="button" key={id} onClick={() => {c.closeAssistantDialog();c.selectAsset(id,false);}}>{id}</button>)}{toolEventIds(tool).map((id) => <button type="button" key={id} onClick={() => {c.closeAssistantDialog();c.selectEvent(id);}}>{id}</button>)}</span>)}
      </article>)}
      <button type="button" onClick={agent.clearHistory}>清空本次对话</button>
    </details> : null}
    <form className="gas-agent-input-form" onSubmit={(e) => { e.preventDefault(); void c.submitAgent(); }}>
      <label htmlFor="gas-agent-nl-input">燃气自然语言输入</label>
      <textarea id="gas-agent-nl-input" aria-label="燃气自然语言输入" rows={2} maxLength={2000}
        value={c.assistantInput} onChange={(e) => c.setAssistantInput(e.target.value)} placeholder={samples[0]} />
      <div className="gas-agent-form-actions">
        <button type="submit" disabled={!c.assistantInput.trim() || agent.status === "loading"}>发送查询</button>
        <button type="button" onClick={() => void agent.probe()} disabled={agent.probing}>检测燃气服务</button>
        {agent.status === "loading" ? <button type="button" onClick={agent.cancel}>取消查询</button> : null}
        {agent.error?.status === 409 ? <button type="button" onClick={c.refresh}>刷新案例与证据</button>
          : (agent.status === "cancelled" || (agent.status === "error" && agent.error?.status !== 401 && agent.error?.status !== 403))
            && <button type="button" onClick={() => void agent.retry()}>重试查询</button>}
      </div>
    </form>
    <div className="gas-agent-chips" aria-label="示例指令">{samples.map((sample) =>
      <button key={sample} type="button" onClick={() => void c.submitAgent(sample)}>{sample}</button>)}</div>
    <p role="status" data-testid="gas-agent-request-state">查询状态：{agent.status}</p>
    {agent.status==="loading" ? <p role="status">已发送真实请求，等待服务端授权、意图校验与只读查询结果；尚无完成结果。</p> : null}
    {agent.input ? <p data-testid="gas-agent-submitted">输入：{agent.input}</p> : null}
    {agent.error ? <p role="alert" data-testid="gas-agent-error">{agent.error.message}</p> : null}
    {result ? <div data-testid="gas-agent-result" data-request-id={result.request_id}
      data-evidence-scope={result.evidence_scope?.scope}>
      <dl className="gas-fields">
        <div><dt>parser_source</dt><dd>{result.parser_source}</dd></div>
        <div><dt>model_status</dt><dd>{result.model_status} · {result.model_calls ? `${result.model_calls}次调用 / ${result.model_elapsed_ms ?? "unknown"} ms` : "本次未调用模型"}</dd></div>
        <div><dt>服务端意图 / 状态</dt><dd>{result.intent} / {result.status}</dd></div>
        <div><dt>请求 / 服务端耗时</dt><dd>{result.request_id} · {result.elapsed_ms ?? "unknown"} ms</dd></div>
        {result.error_code ? <div><dt>{blocked ? "证据范围" : "状态原因"}</dt><dd>{
          result.error_code === "model_timeout" ? "模型超时 · 规则降级"
            : result.error_code === "cross_dataset_unverified" ? "跨数据集关联未核验"
              : result.error_code === "engineering_evidence_unavailable" ? "缺少工程证据" : result.error_code
        }</dd></div> : null}
        <div><dt>数据来源</dt><dd>{result.evidence_scope
          ? [evidenceLabel[result.evidence_scope.scope], result.evidence_scope.dataset_id, result.evidence_scope.dataset_version].join(" · ")
          : result.data_mode + " / " + result.dataset_id}</dd></div>
      </dl>
      {result.explanation ? <section data-testid="gas-agent-model-explanation">
        <h4>模型证据选取 · {result.explanation_source}</h4>
        <p>{result.explanation}</p>
        <p className="gas-window-note">{result.model_id} · {result.model_calls} 次模型请求；证据句由服务端生成，模型不能改写业务事实。</p>
      </section> : null}
      {result.tools.map((tool, index) => <section className="gas-agent-result-card" key={index} data-testid="gas-agent-tool">
        <h4>{tool.tool_name} · {tool.status}</h4>
        <p>{tool.limitation}</p>
        {tool.benchmark_report && <GasCuvBenchmarkEvidence report={tool.benchmark_report} />}
        {tool.engineering_assessment && <section data-testid="gas-agent-engineering">
          <h5>工程能力校核</h5>
          {tool.cuv_comparison_binding && <p>人工比较修订 {tool.cuv_comparison_binding.expected_revision}</p>}
          <CuvEngineeringCapabilities assessment={tool.engineering_assessment} />
          <button type="button" onClick={() => { c.closeAssistantDialog(); c.openWindow("impact"); }}>打开当前 CUV 工程校核</button>
        </section>}
        {tool.tool_name === "cuv.query" && gasCuvAgentData(tool.data) && <section data-testid="gas-agent-cuv-summary">
          <h5>合成方案取舍与时点证据</h5>
          <time dateTime={tool.data.context.at}>{tool.data.context.at}</time>
          {!result.explanation?.split("\n").includes(tool.data.summary) && <p>{tool.data.summary}</p>}
          {tool.data.confirmed_comparison && <details data-testid="gas-agent-cuv-version">
            <summary>引用人工确认版本 {tool.data.confirmed_comparison.revision}</summary>
            <p>输入 SHA-256 <code>{tool.data.confirmed_comparison.input_sha256}</code></p>
            <p>符合条件的领先方案 {tool.data.confirmed_comparison.comparison.leading_plan_ids.join("、") || "无"}</p>
          </details>}
          {tool.data.comparison_change?.status === "preview" && <p data-testid="gas-cuv-preview-status">
            草案预览 · 基于版本 {tool.data.comparison_change.before.revision} · 尚待确认
          </p>}
          <div className="gas-cuv-links">
            {tool.data.plans.map((plan) => <details key={plan.plan_id} data-agent-cuv-plan={plan.plan_id}>
              <summary>方案证据 {plan.plan_id}</summary>
              <p>{plan.label} · 累计图影响 {plan.affected_service_hours ?? "未知"} 服务对象·小时</p>
              <p>峰值影响 {plan.peak_affected_service_count ?? "未知"} · 图可达恢复 {plan.topology_restoration_hours === null
                ? "未知" : plan.topology_restoration_hours + " h"}</p>
              <p>推演配置 SHA-256 <code>{tool.evidence_scope?.source_sha256}</code></p>
            </details>)}
          </div>
          <button type="button" onClick={() => { c.closeAssistantDialog(); c.openWindow("impact"); }}>打开当前 CUV 比较与审计</button>
        </section>}
        {gasMonitoringObservation(tool.data?.monitoring_context) && <div className="gas-cuv-links">
          <button type="button" data-testid="gas-agent-observation-link" onClick={() => {
            const selected = tool.data?.monitoring_context;
            if (gasMonitoringObservation(selected)) c.openObservation(selected.reference);
          }}>查看原始观测 {tool.data.monitoring_context.reference.observation_id}</button>
          <details data-testid="gas-agent-monitoring-policy">
            <summary>样例阈值 {tool.data.monitoring_context.policy_id}</summary>
            <p>连续 {tool.data.monitoring_context.sustained_samples} 次越限 · 下限 {tool.data.monitoring_context.lower ?? "未设"}
              {" · 上限 "}{tool.data.monitoring_context.upper ?? "未设"} {tool.data.monitoring_context.observation.unit}</p>
            <p>监测来源 {tool.monitoring_scope?.dataset_id} · {tool.monitoring_scope?.dataset_version} · 清单 SHA-256
              {" "}<code>{tool.monitoring_scope?.source_sha256}</code></p>
          </details>
        </div>}
        <p className="gas-window-note">实际参数：{JSON.stringify(tool.parameters ?? {})} · {tool.elapsed_ms ?? "未知"} ms</p>
        {Array.isArray(tool.data?.related_pipe_ids) ? <p data-testid="gas-agent-related-pipes">合成事件关联管段 {tool.data.related_pipe_ids.length} 条</p> : null}
        {tool.tool_name==="map.locate" ? <p data-testid="gas-agent-gis-state">GIS精确定位：不可用；仅可使用服务端返回的合成拓扑关联。</p>:null}
        <details><summary>展开完整结构化查询结果</summary><pre>{JSON.stringify(tool.data, null, 2)}</pre></details>
        <div data-testid="gas-agent-event-links">{toolEventIds(tool).map((id) =>
          <button type="button" key={id} aria-label={`智能体事件详情 ${id}`} onClick={() => {c.closeAssistantDialog();c.selectEvent(id);}}>{id}</button>)}</div>
        <div className="gas-agent-asset-pills" data-testid="gas-agent-clickable-assets">{toolAssetIds(tool).map((id) =>
          <button type="button" key={id} aria-label={`智能体资产详情 ${id}`} onClick={() => {
            c.closeAssistantDialog(); c.selectAsset(id, false);
          }}>{id}</button>)}</div>
        {toolAssetIds(tool).length > 0 ? <button type="button" data-testid="gas-agent-locate-action"
          onClick={() => c.locateAssetsOnMap({ view: "topology", eventId: toolEventId(tool),
            assetId: toolEventId(tool) ? undefined : toolAssetIds(tool)[0] })}>
          定位服务端返回资产到合成拓扑
        </button> : null}
        {tool.evidence_refs?.length ? <details>
          <summary>来源引用（{tool.evidence_refs.length}）</summary>
          <ul className="gas-agent-evidence-list">{tool.evidence_refs.map((ref) => <li key={ref}><code>{ref}</code></li>)}</ul>
        </details> : null}
      </section>)}
      <section data-testid="gas-agent-evidence"><h4>证据与限制</h4>
        {result.limitations?.map((limitation) => <p role={blocked ? "status" : undefined} key={limitation}>{limitation}</p>)}
      </section>
    </div> : <p className="gas-window-note">尚无本次服务端结果；模型不可用时可使用明确标记的 local_rule_not_llm 工具路径。</p>}
  </section>;
}
