import { useCallback } from "react";
import type { GasCuvBenchmarkBinding, GasCuvBenchmarkReport } from "../../shared/contracts/platform";
import { GasReadError } from "./GasDetails";
import { WEYMOUTH_SUITE } from "./cuvBenchmarkContract";
import type { GasEngineeringApi } from "./engineeringApi";
import { useGasRead } from "./useGasRead";

const referenceLabels = {
  literature_result: "文献结果",
  third_party_software: "第三方软件值",
  boundary_target: "边界目标",
};

function reading(entry: { value: number; unit: string }) {
  const unit = ({ degR: "°R", degF: "°F", m3: "m³", "m3/day": "m³/d", "million_ft3/day": "百万 ft³/d" } as Record<string, string>)[entry.unit] ?? entry.unit;
  return entry.value + " " + unit;
}

export function GasCuvBenchmarkEvidence({ report }: { report: GasCuvBenchmarkReport }) {
  const { binding, progress, comparison, network_prototype: network } = report;
  const references = new Map(report.references.map((row) => [row.id, row]));
  return <div data-testid="gas-cuv-benchmark-result" data-benchmark-status={progress.benchmark_status}>
    <p>公开单管 · <code>{binding.model_version}</code> · {progress.benchmark_status === "benchmark_passed"
      ? "基准通过" : "基准未通过"} · 待现场校核</p>
    <p>{report.applicability}</p>
    <ul className="gas-cuv-missing" aria-label="基准算例与误差">
      {report.cases.map(({ case_id, label, source_input, input, input_source, expected, input_sha256, solve,
        passed, unit, absolute_error, relative_error_percent, forward_check }) => {
        const solution = solve.solution;
        const reference = references.get(expected.source_id);
        const source = references.get(input_source.source_id);
        return <li key={case_id} data-benchmark-case={case_id}>
          <strong>{label}</strong> · 输入 {solve.supplied_inputs}/{solve.required_inputs} · {passed ? "求解并通过对照" : "未通过"}
          {solution && <p>
            {solution[expected.metric].toFixed(6)} {unit}
            {unit === "MMSCFD" && "（标况）"}{unit === "psia" && "（绝压）"}
            {" · "}{referenceLabels[expected.origin]} {expected.value} {unit}
            {" · "}绝对差 {absolute_error?.toFixed(6)} {unit} · 相对差 {relative_error_percent?.toFixed(4)}%
          </p>}
          {!passed && <p>{solve.reason ?? (forward_check?.passed === false ? "反求代回校验未通过" : "参考误差超出容差")}
            {solve.missing_fields?.length ? " · 缺项 " + solve.missing_fields.join("、") : ""}</p>}
          <details>
            <summary>原始单位、代回残差与出处</summary>
            <p>输入 SHA-256 <code>{input_sha256}</code></p>
            <p>入口原值 {reading(source_input.inlet_pressure)}{source_input.outlet_pressure
              && <> · 出口原值 {reading(source_input.outlet_pressure)}</>}
              {source_input.atmosphere && <> · 表压参考 {reading(source_input.atmosphere)}</>}
              {" · "}入口换算 {input.inlet_psia} psia{input.outlet_psia != null && <> · 出口换算 {input.outlet_psia} psia</>}</p>
            <p>标况 {reading(source_input.base_pressure)}、{reading(source_input.base_temperature)}
              {" · "}流动温度 {reading(source_input.gas_temperature)} · 管长 {reading(source_input.length)}
              {source_input.internal_diameter && <> · 已知内径 {reading(source_input.internal_diameter)}</>}
              {source_input.flow && <> · 指定标况流量 {reading(source_input.flow)}</>}</p>
            {solution && <p>压力方程残差 {solution.equation_residual_psi2.toExponential(3)} psi²
              {" · "}标况流量闭合差 {solution.standard_flow_balance_residual_mmscfd.toExponential(3)} MMSCFD
              {" · "}迭代 {solution.iterations} 次</p>}
            {forward_check && <p data-testid="gas-cuv-forward-check">代回流量 {forward_check.recalculated_mmscfd?.toFixed(9) ?? "未求得"} MMSCFD
              {" · "}目标 {forward_check.expected_mmscfd} MMSCFD · 闭合差 {forward_check.absolute_error_mmscfd?.toExponential(3) ?? "未知"} MMSCFD</p>}
            <p>{input_source.notes} {input_source.locator}
              {source && <> · <a href={source.url} target="_blank" rel="noreferrer">原始输入出处</a></>}</p>
            <p>对照容差 {expected.tolerance} {unit}。{expected.tolerance_basis} {expected.locator}
              {reference && <> · <a href={reference.url} target="_blank" rel="noreferrer">参考结果出处</a></>}</p>
          </details>
        </li>;
      })}
    </ul>
    {comparison && <p data-testid="gas-cuv-benchmark-comparison">
      相同入口压力、标况流量、管长及介质条件下，取径 B 相对 A 的出口绝对压力差 {comparison.outlet_delta_psia.toFixed(6)} psia
      {" · "}指定标况流量差 {comparison.flow_delta_mmscfd.toFixed(6)} MMSCFD · 现场安全性待工程校核
    </p>}
    {report.comparison_unavailable_reason && <p>取径比较不可用。{report.comparison_unavailable_reason}</p>}
    {network && <details data-testid="gas-cuv-network-prototype">
      <summary>{network.label} · {network.status === "benchmark_passed" ? "基准通过" : "基准未通过"} · 待现场校核</summary>
      <p><code>{network.model_version}</code> · {network.scope}</p>
      <p>入口 {reading(network.input.inlet_pressure)} · 表压参考 {reading(network.input.atmosphere)}
        {" · "}末端流量 {reading(network.input.terminal_flow)}（标况） · 输入 SHA-256 <code>{network.input_sha256}</code></p>
      <ul className="gas-cuv-missing">
        {network.checks.map((row) => <li key={row.id}>{row.label} · 求解 {row.solved?.toFixed(6) ?? "未求得"} {row.unit}
          {" · "}文献 {row.literature} {row.unit} · 第三方软件 {row.third_party} {row.unit}
          {" · "}文献绝对差 {row.absolute_error?.toFixed(6) ?? "未知"} {row.unit}</li>)}
      </ul>
      {network.solution && <p>最大节点流量闭合差 {network.solution.max_flow_balance_residual_mmscfd.toExponential(3)} MMSCFD
        {" · "}最大管段压力方程残差 {network.solution.max_pressure_equation_residual_psi2.toExponential(3)} psi²</p>}
      {network.reason && <p>{network.reason}</p>}
      <p>{network.tolerance_basis} {network.input_source.locator} · {network.comparison_source.locator}
        {" · "}<a href={network.equation_reference_url} target="_blank" rel="noreferrer">General Flow 参考方程</a>
        {references.get(network.comparison_source.source_id) && <> · <a href={references.get(network.comparison_source.source_id)?.url}
          target="_blank" rel="noreferrer">串并联公开对照表</a></>}</p>
    </details>}
    <details>
      <summary>算例版本与模型来源</summary>
      <p><code>{binding.suite_id}</code> · {binding.suite_version} · 算例清单 SHA-256 <code>{binding.suite_sha256}</code></p>
      <a href={report.equation_reference_url} target="_blank" rel="noreferrer">Weymouth 参考方程</a>
    </details>
  </div>;
}

export function GasCuvBenchmarkPanel({ api, onAsk }: {
  api: Pick<GasEngineeringApi, "cuvBenchmark">; onAsk(binding: GasCuvBenchmarkBinding): void;
}) {
  const read = useCallback((signal: AbortSignal) => api.cuvBenchmark(signal), [api]);
  const result = useGasRead(WEYMOUTH_SUITE, read);
  return <details className="gas-cuv-card" data-testid="gas-cuv-benchmark">
    <summary>公开 Weymouth 稳态基准</summary>
    {result.status === "loading" && <p role="status">正在求解公开算例并核对参考结果。</p>}
    {result.error && <GasReadError error={result.error} retry={result.retry} label="重试读取公开基准" />}
    {result.data && <>
      <GasCuvBenchmarkEvidence report={result.data} />
      {result.data.progress.benchmark_status === "benchmark_passed"
        && <button type="button" data-testid="gas-cuv-ask-benchmark" onClick={() => onAsk(result.data!.binding)}>
          让 Qwen 复算并解释公开基准
        </button>}
    </>}
  </details>;
}
