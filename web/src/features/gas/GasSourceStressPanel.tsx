import { useCallback, useState } from "react";
import type { GasDrillReference, GasM2Binding, GasSourceStressRequest } from "../../shared/contracts/platform";
import { GasEvidenceAsk } from "./GasEvidenceAsk";
import { GasReadError } from "./GasDetails";
import { useGasCockpit } from "./GasCockpitProvider";
import { useGasRead } from "./useGasRead";
import { downloadGasFile, sourceStressPrintReport } from "./printableGasReport";

const short = (id: string) => id.replace(/^syn-gas-(source|edge|node)-/, "");

export function GasSourceStressPanel({ binding, scenario }: { binding: GasM2Binding; scenario: GasDrillReference | null }) {
  const { scenarioApi, caseView } = useGasCockpit();
  const sources = caseView.data?.sources.filter(row => row.enabled) ?? [];
  const [source, setSource] = useState("");
  const [submitted, setSubmitted] = useState<GasSourceStressRequest | null>(null);
  const selectedSource = source || sources[0]?.source_id || "";
  const run = useCallback((signal: AbortSignal) => scenarioApi.sourceStress(submitted!, signal), [scenarioApi, submitted]);
  const result = useGasRead(submitted ? JSON.stringify(submitted) : null, run);
  const data = result.data;
  return <details className="gas-drill-stress">
    <summary>气源退出拓扑测试</summary>
    <p>DEMO · {scenario ? "选中情景 r" + scenario.revision : "同源合成基线"}。按已登记启用气源计算图可达性。</p>
    <form className="gas-drill-compare-inputs" onSubmit={event => {
      event.preventDefault();
      const payload = { binding, scenario, source_id: selectedSource };
      if (submitted && JSON.stringify(payload) === JSON.stringify(submitted)) result.retry();
      else setSubmitted(payload);
    }}>
      <label>模拟退出气源<select aria-label="模拟退出气源" value={selectedSource}
        onChange={event => { setSource(event.target.value); setSubmitted(null); }}>
        {sources.map(row => <option key={row.source_id} value={row.source_id}>{short(row.source_id)}</option>)}
      </select></label>
      <button type="submit" disabled={!selectedSource || result.status === "loading"}>运行气源退出分析</button>
    </form>
    {result.status === "loading" && <p role="status">正在复算图证据…</p>}
    {result.error && <GasReadError error={result.error} retry={result.retry} label="重试气源分析" />}
    {data && <div className="gas-drill-graph" data-testid="gas-source-stress-result" data-stress-sha={data.stress_sha256}>
      <p><strong>气源图可达 {data.before.reachable_node_ids.length} → {data.after.reachable_node_ids.length}</strong></p>
      <p>连通分量 {data.before.components.length} → {data.after.components.length} · 新增图不可达 {data.newly_unreachable_node_ids.length}</p>
      <ul>{data.after.components.map(group => <li key={group.node_ids[0]}>
        {group.node_ids.length} 节点 · {group.source_ids.map(short).join("、") || "无启用气源"}
      </li>)}</ul>
      <p>单气源依赖 {Object.entries(data.single_source_dependencies).map(([id,nodes]) => short(id) + " / " + nodes.length + " 节点").join("、") || "无"}</p>
      <p>替代气源 {data.alternate_source_id ? short(data.alternate_source_id) : "无可达气源"}</p>
      {data.alternate_source_path_edge_ids.length > 0 && <p>替代气源图路径 {data.alternate_source_path_edge_ids.map(short).join(" → ")}</p>}
      {data.scenario_alternative_path_edge_ids.length > 0 && <p>场景替代路径 {data.scenario_alternative_path_edge_ids.map(short).join(" → ")}</p>}
      <details><summary>图可达集合与证据</summary>
        <p>退出前 {data.before.reachable_node_ids.map(short).join("、")}</p>
        <p>退出后 {data.after.reachable_node_ids.map(short).join("、")}</p>
        <p>新增图不可达 {data.newly_unreachable_node_ids.map(short).join("、") || "无"}</p>
        <code>{data.stress_sha256}</code>
      </details>
      <p>{data.limitation}</p>
      <div className="gas-topic-actions">
        <button type="button" onClick={() => downloadGasFile("gas-source-stress", JSON.stringify(data, null, 2), "json")}>导出气源证据 JSON</button>
        <button type="button" onClick={() => downloadGasFile("gas-source-stress", sourceStressPrintReport(data), "html")}>导出气源专题打印版</button>
      </div>
      <GasEvidenceAsk key={data.stress_sha256} label="Qwen 解释气源退出"
        prompt="解释所选修订的气源退出、连通分量和图不可达变化。"
        ask={(question, signal) => scenarioApi.explainSourceStress({
          selection: data.selection, expected_sha256: data.stress_sha256, question,
        }, signal)} />
    </div>}
  </details>;
}
