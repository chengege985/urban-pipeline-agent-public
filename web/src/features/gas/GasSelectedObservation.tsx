import { useGasCockpit } from "./GasCockpitProvider";
import { GasReadError } from "./GasDetails";
import { GasMonitoringSource, gasMonitoringMetricLabel, gasMonitoringStateLabel } from "./GasMonitoringPanel";

export function GasSelectedObservation() {
  const c = useGasCockpit();
  const { observation, selectedObservation: selected } = c;
  if (observation.error) return <GasReadError error={observation.error} retry={observation.retry} label="重试读取原始观测" />;
  if (!selected) return <p role="status" className="gas-window-note">{observation.data
    ? "观测与当前事件或资产不匹配，请从回放重新选择。"
    : "正在核对观测记录及关联资产。"}</p>;

  const row = selected.observation;
  const down = selected.direction === "below_lower";
  return <section className="gas-monitoring-channel gas-selected-observation"
    data-testid="gas-selected-observation" data-observation-id={row.source_record_id}>
    <h3>合成异常观测</h3>
    <p><strong>{gasMonitoringMetricLabel[selected.metric]} {row.value} {row.unit}</strong> · {gasMonitoringStateLabel[selected.state ?? "limit_exceeded"]} · 有效读数</p>
    <p><time dateTime={row.observed_at}>{row.observed_at}</time> · <code>{row.source_record_id}</code></p>
    <p>连续 {selected.sustained_samples} 次越过样例{down ? "下限" : "上限"} {down ? selected.lower : selected.upper} {row.unit}
      <br />阈值来源 <code>{selected.policy_id}</code></p>
    <p>关联资产 <code>{selected.asset_id}</code></p>
    {selected.event ? <p>同一合成案例事件 <code>{selected.event.event_id}</code></p> : <p>此时点暂无关联案例事件。</p>}
    <div className="gas-cuv-links">
      {c.route.gasAssetId !== selected.asset_id && <button type="button" onClick={c.openObservationAsset}>打开观测关联资产</button>}
      {selected.event && c.route.gasAssetId && <button type="button" onClick={c.closeAsset}>打开观测关联事件</button>}
      <button type="button" onClick={() => void c.askObservationAgent()}>向 Qwen 查询此异常</button>
      {selected.event && <button type="button" onClick={() => { c.setMode("emergency"); c.openWindow("impact"); }}>比较 CUV 两案</button>}
    </div>
    <details>
      <summary>原始行与来源摘要</summary>
      <p>合成数据 {selected.dataset_id} · 版本 {selected.dataset_version}</p>
      <p><GasMonitoringSource item={row} /> · 第 {row.source_row} 行</p>
      <p>清单 SHA-256 <code>{selected.reference.manifest_sha256}</code></p>
      <ul>{selected.sustaining_observations.map((entry) => <li key={entry.source_record_id}>
        <time dateTime={entry.observed_at}>{entry.observed_at}</time> · {entry.value} {entry.unit} · <code>{entry.source_record_id}</code>
      </li>)}</ul>
    </details>
  </section>;
}
