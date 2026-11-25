import type { GasDrillComparison, GasDrillGraphState, GasScenarioReport } from "../../shared/contracts/platform";

function formatGraph(graph: GasDrillGraphState) {
  return `${graph.reachable_node_ids.length} 个图可达节点，${graph.components.length} 个连通分区。`
    + graph.components.map(row => ` ${row.node_ids.length} 节点（气源 ${row.source_ids.join("、") || "无"}）。`).join("");
}

function reportSection(report: GasScenarioReport) {
  const { scenario, audit, trust } = report;
  const { binding } = scenario.input;
  const analysis = scenario.analysis;
  const graph = analysis?.graph;
  return [
    `### ${scenario.input.pipe_id} · r${scenario.transition.revision}`,
    "",
    `场景 ${scenario.scenario_id}，修订状态 ${scenario.transition.current_status}。`,
    `数据 ${binding.dataset_id} ${binding.version}，登记 SHA-256 ${binding.registry_sha256}。`,
    `来源 ${scenario.source.source_uri ?? "未登记"}。`,
    `合成图 SHA-256 ${scenario.source.network_content_sha256}。`,
    `TrustShield ${trust.rule_version}，校验 ${trust.source_files_verified} 份原件、${trust.observation_count} 条合成观测，图校验 ${trust.graph_status}。`,
    ...trust.graph_warnings.map(warning => `- 图校验备注 ${warning}`),
    "",
    ...scenario.source.files.map(file => `- ${file.name} SHA-256 ${file.sha256}`),
    "",
    ...(analysis && graph ? [
      `计算规则 ${analysis.algorithm} · ${graph.rule_version}。`,
      `排除的合成图边 ${graph.excluded_edge_ids.join("、")}。`,
      `基线 ${formatGraph(graph.baseline)}`,
      `演练 ${formatGraph(graph.scenario)}`,
      `新增图不可达 ${analysis.impact.newly_unreachable_node_ids.join("、") || "无"}。`,
      `涉及管段 ${analysis.impact.affected_pipe_ids.join("、")}。`,
      `替代路径图边 ${graph.alternative_path_edge_ids.join(" → ") || "无已计算路径"}。`,
      `图证据 SHA-256 ${graph.sha256}。`,
      `分析 SHA-256 ${analysis.analysis_sha256}。`,
    ] : ["当前修订尚未计算。"]),
    "",
    report.limitation,
    "",
    "#### 审计记录",
    "",
    ...audit.map(row => `- r${row.revision} · ${row.occurred_at} · ${row.actor_id} · ${row.action} · ${row.previous_status ?? "起始"} → ${row.current_status} · ${row.analysis_sha256 ?? "待计算"}`),
    "",
    report.audit_boundary,
    "",
  ];
}

export function scenarioMarkdown(report: GasScenarioReport) {
  return ["# 燃气情景报告 · DEMO / SYNTHETIC", "", ...reportSection(report)].join("\n");
}

export function comparisonMarkdown(result: GasDrillComparison, highlighted: "before" | "after") {
  return [
    "# 燃气情景对比报告 · DEMO / SYNTHETIC",
    "",
    `规则 ${result.rule_version}，对比 SHA-256 ${result.comparison_sha256}。`,
    `导出时高亮 ${highlighted === "before" ? "前侧" : "后侧"}。`,
    `前侧 ${formatGraph(result.before.graph)}`,
    `后侧 ${formatGraph(result.after.graph)}`,
    `新增图不可达 ${result.newly_unreachable_node_ids.join("、") || "无"}。`,
    `新增图可达 ${result.newly_reachable_node_ids.join("、") || "无"}。`,
    "",
    ...(["before", "after"] as const).flatMap(side => {
      const report = result[side].report;
      return [`## ${side === "before" ? "前侧" : "后侧"}`, "", ...(report ? reportSection(report) : ["同源基线。", ""])];
    }),
    result.limitation,
    result.audit_boundary,
    "",
  ].join("\n");
}
