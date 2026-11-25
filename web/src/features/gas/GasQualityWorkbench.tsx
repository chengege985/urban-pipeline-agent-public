import { useCallback } from "react";
import type { GasM2Binding } from "../../shared/contracts/platform";
import { GasReadError } from "./GasDetails";
import { GasEvidenceAsk } from "./GasEvidenceAsk";
import { useGasCockpit } from "./GasCockpitProvider";
import { useGasRead } from "./useGasRead";
import { downloadGasFile, formatGasMetric, qualityPrintReport } from "./printableGasReport";

const metricNames = { completeness: "完整率", linkage: "关联有效率", observations: "观测质量", provenance: "来源可追溯性" };

export function GasQualityWorkbench({ binding }: { binding: GasM2Binding }) {
  const { dataM2Api } = useGasCockpit();
  const fetchData = useCallback((signal: AbortSignal) => dataM2Api.qualityWorkbench(binding, signal), [dataM2Api, binding]);
  const resource = useGasRead(JSON.stringify(binding), fetchData);
  const data = resource.data;
  if (resource.error) return <GasReadError error={resource.error} retry={resource.retry} label="重试质量工作台" />;
  if (!data) return <p role="status">正在计算本版本质量指标…</p>;
  return <section className="gas-quality-workbench gas-topic" data-testid="gas-quality-workbench" aria-label="数据质量工作台">
    <header><h3>燃气数据质量工作台</h3><span>DEMO / SYNTHETIC</span></header>
    <div className="gas-quality-cards">{data.metrics.map(row => <article key={row.id} data-quality-metric={row.id}>
      <h4>{metricNames[row.id as keyof typeof metricNames] ?? row.id}</h4>
      <strong>{formatGasMetric(row)}</strong><p>{row.definition}</p>
    </article>)}</div>
    <p>正式健康评分 · {data.health_score}。指标用于数据准备。</p>
    {data.incomplete_mapping_fields.length > 0 && <details><summary>缺项映射</summary>
      <ul>{data.incomplete_mapping_fields.map(field => <li key={field}><code>{field}</code></li>)}</ul></details>}
    {data.unsupported_mappings.length > 0 && <p role="status">尚未支持的映射 {data.unsupported_mappings.join("、")}。完整率仅覆盖可核验映射。</p>}
    <section aria-label="G01–G15 数据准备矩阵">
      <h4>G01–G15 数据准备矩阵</h4><p>{data.indicator_version}</p>
      <div className="gas-indicator-grid">{data.preparation.map(row => <article key={row.code} data-indicator={row.code}>
        <h5>{row.code} {row.name}</h5>
        <dl>{([["已有字段", row.existing_fields], ["缺失字段", row.missing_fields], ["可合成演练字段", row.synthetic_possible],
          ["待授权字段", row.authorization_needed]] as const).map(([label, fields]) => <div key={label}>
          <dt>{label}</dt><dd>{fields.join("、") || "—"}</dd>
        </div>)}</dl>
      </article>)}</div>
    </section>
    <details><summary>来源与质量摘要</summary>
      <p>{data.evidence_sha256}</p><ul>{[...data.sources, data.indicator_source].map(file => <li key={file.name}>
        {file.name} <code>{file.sha256}</code>
      </li>)}</ul>
    </details>
    <div className="gas-topic-actions"><button type="button" onClick={() => downloadGasFile("gas-quality-workbench", qualityPrintReport(data), "html")}>
      导出质量打印版</button></div>
    <GasEvidenceAsk key={data.evidence_sha256} label="Qwen 解释数据质量" prompt="解释完整率的分母、缺项和正式评分状态。"
      ask={(question, signal) => dataM2Api.topicQuery({ tool: "quality", binding, selection: null,
        expected_sha256: data.evidence_sha256, question }, signal)} />
  </section>;
}
