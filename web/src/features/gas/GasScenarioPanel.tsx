import { useState } from "react";
import type { GasScenarioController } from "./useGasScenario";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasDrillComparison } from "./GasDrillComparison";
import { GasDrillTrend } from "./GasDrillTrend";
import { GasSourceStressPanel } from "./GasSourceStressPanel";
import { scenarioReference } from "./scenarioContract";
import "./gas-scenario.css";

const defaultQuestion = "这个合成故障情景改变了哪些管段与节点的图连通性？";
const pipeLabel = (id: string) => id.replace(/^syn-gas-pipe-/, "");
const edgeLabel = (id: string) => id.replace(/^syn-gas-edge-/, "");
type Tool = "explain" | "lookup" | "simulate" | "report";

export function GasScenarioPanel({ controller: c, selectedPipe }: { controller: GasScenarioController; selectedPipe?: string }) {
  const cockpit = useGasCockpit();
  const [question, setQuestion] = useState(defaultQuestion);
  const [tool, setTool] = useState<Tool>("explain");
  const drill = c.scenario;
  const status = drill?.transition.current_status;
  const analysis = drill?.analysis;
  const active = status === "analyzed";
  const comparison = c.comparison;
  const stressContext = comparison ? comparison[c.highlighted].report?.scenario : drill;
  const stressReference = stressContext ? scenarioReference(stressContext) : null;
  const ready = c.workspace.data;
  const busy = Boolean(c.busy);
  const gated = busy || Boolean(c.workspace.error);
  if (!c.key) return null;
  function openAsset(id: string) {
    cockpit.locateAssetsOnMap({ view: "topology", assetId: id });
  }
  function submit() {
    const prompt = question.trim();
    if (!prompt) return;
    if (comparison) c.queryTool("compare", prompt);
    else if (tool === "explain") void c.explain(prompt);
    else c.queryTool(tool, prompt);
  }
  return <section className="gas-drill" aria-label="合成故障演练" data-testid="gas-synthetic-drill" data-drill-status={status}>
    <header><h4>合成故障演练</h4><span>DEMO / SYNTHETIC</span></header>
    {ready && <details className="gas-drill-catalog">
      <summary>选择演练场景</summary>
      <div className="gas-drill-actions">
        {ready.presets.map(preset => <button type="button" key={preset.name} data-preset={preset.name} disabled={gated}
          onClick={() => void c.create(preset.pipe_ids[0], preset.fault_kind)}>{preset.label}</button>)}
      </div>
    </details>}
    {selectedPipe && !comparison && selectedPipe !== drill?.input.pipe_id &&
      <button type="button" disabled={gated} onClick={() => void c.create(selectedPipe)}>
        为 {pipeLabel(selectedPipe)} 创建合成情景
      </button>}
    {c.workspace.error && <p role="alert">{c.workspace.error.message} <button type="button" onClick={c.workspace.retry}>重试校验</button></p>}
    {c.busy && <p role="status">{c.busy}中…</p>}
    {c.error && <div role="alert"><p>{c.error.message}</p>
      {c.recover && <button type="button" disabled={busy} onClick={c.recover}>重新读取演练</button>}
    </div>}
    {drill && !comparison && !c.workspace.error && <>
      <div className="gas-drill-context">
        <button type="button" onClick={() => openAsset(drill.input.pipe_id)}>查看演练管段 {pipeLabel(drill.input.pipe_id)}</button>
        <span>{status === "draft" ? "待分析" : active ? "已分析" : "已回退"} · r{drill.transition.revision}</span>
      </div>
      <p>在合成图上排除场景管段，比较启用气源的图可达性。实际供气、停气、压力、流量和泄漏均未知。</p>
      <div className="gas-drill-actions">
        <button type="button" disabled={busy} onClick={() => void c.advance("analyze", () => openAsset(drill.input.pipe_id))}>
          {analysis ? "重新执行图连通分析" : "执行图连通分析"}
        </button>
        {active && <button type="button" disabled={busy} onClick={() => void c.advance("rollback")}>回退演练</button>}
        {analysis && <>
          <button type="button" disabled={busy} onClick={() => void c.download()}>导出演练报告</button>
          <button type="button" disabled={busy} onClick={() => void c.download("md")}>导出可读报告</button>
          <button type="button" disabled={busy} onClick={() => void c.download("html")}>导出演练打印版</button>
        </>}
      </div>
      {analysis && <div className="gas-drill-results">
        {!active && <p>历史分析保留在报告中，拓扑叠加已撤销。</p>}
        <dl>
          <div><dt>气源图可达节点</dt><dd>{analysis.impact.baseline_reachable_node_count} → {analysis.impact.scenario_reachable_node_count}</dd></div>
          <div><dt>新增图不可达</dt><dd>{analysis.impact.newly_unreachable_node_ids.length}</dd></div>
          <div><dt>涉及管段</dt><dd>{analysis.impact.affected_pipe_ids.length}</dd></div>
        </dl>
        {analysis.graph && <div className="gas-drill-graph" data-testid="gas-drill-graph"
          data-partitions={analysis.graph.scenario.components.length} data-graph-sha={analysis.graph.sha256}>
          <p>连通分区 {analysis.graph.baseline.components.length} → {analysis.graph.scenario.components.length}</p>
          <ul>{analysis.graph.scenario.components.map(group => <li key={group.node_ids[0]}>
            {group.node_ids.length} 节点 · {group.source_ids.map(id => id.replace("syn-gas-source-", "")).join("、") || "无启用气源"}
          </li>)}</ul>
          {analysis.graph.alternative_path_edge_ids.length > 0 &&
            <p data-testid="gas-drill-alt-path">替代路径 {analysis.graph.alternative_path_edge_ids.map(edgeLabel).join(" → ")}</p>}
        </div>}
        <div className="gas-drill-actions" aria-label="演练关联资产">
          {analysis.impact.affected_pipe_ids.map(id => <button type="button" key={id} onClick={() => openAsset(id)}>{pipeLabel(id)}</button>)}
          {analysis.impact.newly_unreachable_node_ids.map(id => <button type="button" key={id} onClick={() => openAsset(id)}>
            节点 {id.replace(/^syn-gas-node-/, "")}
          </button>)}
        </div>
        <details><summary>计算证据</summary>
          <p>{drill.input.binding.dataset_id} · {drill.input.binding.version} · {analysis.algorithm}</p>
          <code data-testid="gas-drill-analysis-sha">{analysis.analysis_sha256}</code>
          {analysis.graph && <p>图证据 <code>{analysis.graph.sha256}</code></p>}
        </details>
      </div>}
    </>}
    {!c.workspace.error && <GasDrillComparison controller={c} showTopology={cockpit.focusTopology} />}
    {comparison && !c.workspace.error && <div className="gas-drill-actions">
      <button type="button" disabled={busy} onClick={() => void c.download()}>导出对比 JSON</button>
      <button type="button" disabled={busy} onClick={() => void c.download("md")}>导出可读对比报告</button>
      <button type="button" disabled={busy} onClick={() => void c.download("html")}>导出对比打印版</button>
    </div>}
    {(active || comparison) && !c.workspace.error && <>
      <form className="gas-drill-question" onSubmit={event => { event.preventDefault(); submit(); }}>
        <label htmlFor="gas-drill-question">{comparison ? "Qwen 解释所选修订对比" : "Qwen 解释当前演练"}</label>
        {!comparison && <select className="gas-drill-tool-select" aria-label="演练只读工具" value={tool} disabled={busy}
          onChange={event => setTool(event.target.value as Tool)}>
          <option value="explain">证据解释</option><option value="lookup">场景查询</option><option value="simulate">复算拓扑</option>
          <option value="report">报告证据</option>
        </select>}
        <input id="gas-drill-question" value={question} onChange={event => setQuestion(event.target.value)} maxLength={600} />
        <button type="submit" disabled={busy || !question.trim()}>{comparison ? "解释所选对比" : "解释演练证据"}</button>
      </form>
      {c.answer && <div className="gas-drill-answer" data-testid="gas-drill-answer" data-explanation-source={c.answer.explanation_source}
        data-tool-name={c.answer.tool_name}>
        <strong>{c.answer.explanation_source === "qwen_evidence_selection" ? c.answer.model_id : "确定性证据 · 模型未参与回答"}</strong>
        <p>{c.answer.answer}</p>
        <details><summary>工具和来源证据</summary><code>{c.answer.tool_name} · {c.answer.request_id}</code>
          <ul>{c.answer.evidence_refs.map(ref => <li key={ref}><code>{ref}</code></li>)}</ul>
        </details>
      </div>}
    </>}
    {c.notice && <p role="status">{c.notice}</p>}
    {ready && <>
      <details className="gas-drill-trust">
        <summary>TrustShield 校验</summary>
        <p>{ready.trust.rule_version} · 原件 {ready.trust.source_files_verified} 份 · 合成观测 {ready.trust.observation_count} 条</p>
        <p>图校验 {ready.trust.graph_status} · 观测质量 {Object.entries(ready.trust.observation_quality).map(([quality, count]) => quality + " " + count).join("、")}</p>
        <p>当前内部合成身份。审计按操作者隔离。读取与对比锁定所选修订，写入校验最新修订。</p>
        {ready.trust.graph_warnings.map(warning => <p key={warning}>{warning}</p>)}
      </details>
      <GasDrillTrend binding={ready.binding} count={ready.trust.observation_count} />
      {cockpit.route.gasMap !== "gis" && <GasSourceStressPanel key={JSON.stringify([ready.binding, stressReference])}
        binding={ready.binding} scenario={stressReference} />}
    </>}
  </section>;
}
