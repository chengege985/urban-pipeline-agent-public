import type { PilotAccess, PilotQualityReport, PilotRejectedQuality } from "../../shared/contracts/platform";

const tables: Record<string, string> = {
  nodes: "节点", pipes: "管段", gis: "GIS", sensors: "测点", observations: "观测",
  events: "事件", thresholds: "样例阈值", threshold_rules: "批准阈值", truth_windows: "核验时段", truth_events: "核验事件",
};

const issues: Record<string, string> = {
  field_mapping_required: "必需字段未映射或表头不匹配",
  missing_required_value: "必需字段值缺失",
  invalid_field: "字段格式或取值无效",
  invalid_record: "字段组合不符合合同",
  invalid_cell: "无法读取单元格",
  missing_source: "缺少来源表",
  hash_mismatch: "文件摘要与清单不一致",
  file_unavailable: "源文件不可读",
  file_too_large: "源文件超出接收上限",
  empty_table: "只有表头，暂无记录",
  empty_required_entities: "必需资产表无可用记录",
  incomplete_coverage: "关联覆盖不足",
  quality_suspect: "源记录标记为可疑",
  quality_missing: "缺少有效读数",
  quality_invalid: "源记录标记为无效",
  source_observations_out_of_order: "来源时点逆序，回放按时间排序",
  duplicate_time: "同测点存在重复时点",
  duplicate_identity: "来源或业务主键重复",
  unknown_node: "找不到管段端点",
  unknown_pipe: "关联管段不在本包",
  unknown_sensor: "关联测点不在本包",
  unit_mismatch: "读数单位与测点不一致",
  threshold_binding_mismatch: "阈值的测点、资产、指标或单位不一致",
  threshold_not_approved: "阈值审批状态未达到生效要求",
  no_approved_threshold: "暂无可用于正式回放的批准阈值",
  geometry_scope_mismatch: "GIS 要素授权范围不匹配",
  formula_or_error_cell: "工作簿含公式或错误单元格",
};

export function GasPilotAccess({ access }: { access: PilotAccess }) {
  return <p data-testid="gas-pilot-authorization">
    <strong>{access.status === "authorized" ? "授权有效" : "合成验证"}</strong>
    {access.valid_from && access.valid_until
      ? <> · 起效 <time dateTime={access.valid_from}>{access.valid_from}</time>
        {" · 到期 "}<time dateTime={access.valid_until}>{access.valid_until}</time></>
      : " · 无业务授权期限"}
  </p>;
}

function Findings({ report }: { report: PilotQualityReport }) {
  const missing = report.missing_fields ?? [];
  return <>
    {missing.length > 0 && <ul aria-label="缺失字段">
      {missing.map((gap) => <li key={gap.table}>
        {tables[gap.table]}缺少字段 <code>{gap.fields.join("、")}</code>
      </li>)}
    </ul>}
    {report.issues.length > 0 ? <ul className="gas-pilot-issues">
      {report.issues.map((issue, index) => <li key={index} data-severity={issue.severity}>
        <strong>{issue.severity === "error" ? "拒收" : "提示"}</strong> · {tables[issue.table]} ·
        {" "}{issues[issue.code] ?? issue.code}
        {issue.row != null && " · 第 " + issue.row + " 行"}
        {issue.field && <> · <code>{issue.field}</code></>}
      </li>)}
    </ul> : <p>没有登记的质量问题。</p>}
    {!!report.findings_omitted && <p>还有 {report.findings_omitted} 项见受控离线回执。</p>}
  </>;
}

export function GasPilotQuality({ report }: { report: PilotQualityReport }) {
  return <details className="gas-monitoring-channel gas-pilot-diagnostics" data-testid="gas-pilot-quality">
    <summary>数据质量 · {report.issue_counts.error} 项错误 · {report.issue_counts.warning} 项提示</summary>
    <Findings report={report} />
  </details>;
}

export function GasPilotRejections({ items }: { items: PilotRejectedQuality[] }) {
  if (!items.length) return null;
  return <details className="gas-monitoring-channel gas-pilot-diagnostics" data-testid="gas-pilot-rejections">
    <summary>近期拒收回执（{items.length}）</summary>
    {items.map((attempt) => <details key={attempt.receipt}>
      <summary>{attempt.dataset_id} · {attempt.version} · 回执 #{attempt.receipt} · {attempt.quality.issue_counts.error} 项错误</summary>
      <p>拒收时间 <time dateTime={attempt.rejected_at}>{attempt.rejected_at}</time> · {attempt.data_mode}</p>
      <Findings report={attempt.quality} />
    </details>)}
  </details>;
}
