import { useCallback } from "react";
import type { GasCuvEngineeringAssessment, GasCuvReplayResponse } from "../../shared/contracts/platform";
import { GasReadError } from "./GasDetails";
import type { GasEngineeringApi } from "./engineeringApi";
import { useGasRead } from "./useGasRead";

export function CuvEngineeringCapabilities({ assessment, detailed = false }: {
  assessment: GasCuvEngineeringAssessment; detailed?: boolean;
}) {
  const inputs = new Map(assessment.inputs.map((input) => [input.input_id, input]));
  return <ul className="gas-cuv-missing" aria-label="工程分析能力">
    {assessment.capabilities.map((item) => <li key={item.capability_id} data-cuv-engineering-status={item.status}
      data-cuv-capability-id={item.capability_id}>
      <strong>{item.label}</strong> · {item.status === "ready_synthetic" ? "合成可用" : "缺少工程校核"}
      {item.model_version && <> · <code>{item.model_version}</code></>}
      {item.solver_progress?.implementation_status === "candidate" && <>
        {" · "}公开基准候选 <code>{item.solver_progress.model_version}</code>
      </>}
      {item.missing_input_ids.length > 0 &&
        <p>缺项 {item.missing_input_ids.map((id) => inputs.get(id)?.label ?? id).join("、")}</p>}
      {detailed && <details>
        <summary>适用范围与校核依据</summary>
        <p>{item.scope}</p>
        {item.method && <p>{item.method}</p>}
        <p>校核状态 <code>{item.validation_status}</code></p>
        {item.status === "blocked" && <p>{item.reasons.join(" ")}</p>}
        <ul>{item.calibration_requirements.map((requirement) => <li key={requirement}>{requirement}</li>)}</ul>
        {item.validation_evidence_refs.map((ref) => <p key={ref}><code>{ref}</code></p>)}
      </details>}
    </li>)}
  </ul>;
}

export function GasCuvEngineeringPanel({ report, api, onAsk }: {
  report: GasCuvReplayResponse; api: Pick<GasEngineeringApi, "cuvEngineering">; onAsk(): void;
}) {
  const read = useCallback((signal: AbortSignal) => api.cuvEngineering(report, signal), [api, report]);
  const state = useGasRead(report.topology_evidence.replay_content_sha256, read);
  const assessment = state.data;
  return <section className="gas-cuv-card" data-testid="gas-cuv-engineering">
    <h4>工程模型校核与输入</h4>
    {state.status === "loading" && <p role="status">正在核对工程能力与方案证据。</p>}
    {state.error && <GasReadError error={state.error} retry={state.retry} label="重试读取工程状态" />}
    {assessment && <>
      <p>{assessment.inputs.filter((input) => input.status === "verified_synthetic").length}
        /{assessment.inputs.length} 组输入有合成证据 · <code>{assessment.contract_version}</code></p>
      <CuvEngineeringCapabilities assessment={assessment} detailed />
      <details>
        <summary>输入合同与结果来源</summary>
        <p>数据版本 <code>{assessment.evidence_scope.dataset_version}</code> · 方案配置 SHA-256
          {" "}<code>{assessment.binding.replay_content_sha256}</code></p>
        <p>方案 {assessment.plan_ids.join("、")}</p>
        <dl className="gas-fields">
          {assessment.inputs.map((input) => <div key={input.input_id}>
            <dt>{input.label} · {input.status === "verified_synthetic" ? "有合成证据" : "缺失"}</dt>
            <dd>{input.fields.join("、")}。{input.source}。{input.unit_time_basis}。</dd>
          </div>)}
        </dl>
      </details>
      <button type="button" data-testid="gas-cuv-ask-engineering" onClick={onAsk}>让 Qwen 解释工程就绪度</button>
    </>}
  </section>;
}
