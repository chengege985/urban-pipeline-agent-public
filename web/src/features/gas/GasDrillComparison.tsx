import { useState } from "react";
import type { GasDrillChoice, GasDrillSide } from "../../shared/contracts/platform";
import type { GasScenarioController } from "./useGasScenario";

const baseline = "baseline";
const key = (row: GasDrillChoice) => row.reference.scenario_id + "#" + row.reference.revision;
const label = (row: GasDrillChoice) =>
  (row.fault_kind === "synthetic_dual_source_split" ? "双气源分区" : row.pipe_id.replace("syn-gas-pipe-", ""))
  + " · r" + row.reference.revision + (row.latest ? "" : " · 历史") + " · " + row.reference.scenario_id.slice(-6);

function sideLabel(value: GasDrillSide) {
  const snapshot = value.report?.scenario;
  if (!snapshot) return "同源基线";
  const name = snapshot.input.fault_kind === "synthetic_dual_source_split"
    ? "双气源分区" : snapshot.input.pipe_id.replace("syn-gas-pipe-", "");
  return name + " · r" + snapshot.transition.revision;
}

export function GasDrillComparison({ controller: c, showTopology }: { controller: GasScenarioController; showTopology(): void }) {
  const [before, setBefore] = useState(baseline);
  const [after, setAfter] = useState("");
  const choices = c.workspace.data?.versions ?? [];
  const selectedBefore = choices.find(row => key(row) === before);
  const selectedAfter = choices.find(row => key(row) === after) ?? (after ? undefined : choices[0]);
  const busy = Boolean(c.busy);
  const result = c.comparison;
  if (!choices.length && !result) return null;
  return <details className="gas-drill-comparison">
    <summary>场景与修订对比</summary>
    <form className="gas-drill-compare-inputs" onSubmit={event => {
      event.preventDefault();
      void c.compare(selectedBefore?.reference ?? null, selectedAfter?.reference ?? null, showTopology);
    }}>
      <label>前侧
        <select aria-label="前侧修订" value={before} onChange={event => { setBefore(event.target.value); c.leaveComparison(); }} disabled={busy}>
          <option value={baseline}>同源基线</option>
          {choices.map(row => <option key={key(row)} value={key(row)}>{label(row)}</option>)}
        </select>
      </label>
      <label>后侧
        <select aria-label="后侧修订" value={selectedAfter ? key(selectedAfter) : baseline} onChange={event => {
          setAfter(event.target.value);
          c.leaveComparison();
        }} disabled={busy}>
          <option value={baseline}>同源基线</option>
          {choices.map(row => <option key={key(row)} value={key(row)}>{label(row)}</option>)}
        </select>
      </label>
      <button type="submit" disabled={busy || Boolean(c.workspace.error) || !(selectedBefore || selectedAfter)}>对比选中修订</button>
    </form>
    {result && <div className="gas-drill-compare-result" data-testid="gas-drill-comparison" data-comparison-sha={result.comparison_sha256}>
      {(["before", "after"] as const).map(side => {
        const item = result[side];
        const pressed = c.highlighted === side;
        return <section key={side}>
          <strong>{side === "before" ? "前侧" : "后侧"} · {sideLabel(item)}</strong>
          <p>{item.graph.reachable_node_ids.length} 个图可达节点 · {item.graph.components.length} 个连通分区</p>
          {item.graph.components.map(group => <p key={group.node_ids[0]}>{group.node_ids.length} 节点 · {group.source_ids.map(id => id.replace("syn-gas-source-", "")).join("、") || "无启用气源"}</p>)}
          <button type="button" aria-pressed={pressed} disabled={busy} onClick={() => { c.selectSide(side); showTopology(); }}>
            {side === "before" ? "高亮前侧" : "高亮后侧"}
          </button>
        </section>;
      })}
      <p>新增图不可达 {result.newly_unreachable_node_ids.length} · 新增图可达 {result.newly_reachable_node_ids.length}</p>
      <button type="button" onClick={c.leaveComparison} disabled={busy}>退出对比</button>
    </div>}
  </details>;
}
