import type {
  GasDataTopic, GasDrillComparison, GasDrillGraphState, GasFieldMetric, GasM2Binding, GasM2Source,
  GasQualityWorkbench, GasScenarioAudit, GasScenarioReport, GasSourceStressResult,
} from "../../shared/contracts/platform";

interface PrintSection {
  title: string;
  notes?: Array<string | undefined>;
  columns?: string[];
  rows?: string[][];
}

const escape = (text: string) => text.replace(/[&<>"']/g, char => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
})[char]!);
const cells = (values: string[], tag: "th" | "td") => values.map(value => "<" + tag + ">" + escape(value) + "</" + tag + ">").join("");

function printDocument(title: string, subtitle: string, sections: PrintSection[]) {
  const body = sections.map(section => "<section><h2>" + escape(section.title) + "</h2>"
    + (section.notes ?? []).filter((note): note is string => note !== undefined).map(note => "<p>" + escape(note) + "</p>").join("")
    + (section.rows && section.columns ? "<table><thead><tr>" + cells(section.columns, "th") + "</tr></thead><tbody>"
      + section.rows.map(row => "<tr>" + cells(row, "td") + "</tr>").join("") + "</tbody></table>" : "") + "</section>").join("");
  return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
    + "<title>" + escape(title) + "</title><style>"
    + "@page{size:A4;margin:16mm}*{box-sizing:border-box}body{font:11pt/1.55 system-ui,'Noto Sans CJK SC','Microsoft YaHei','PingFang SC',sans-serif;color:#13262d;margin:0}"
    + "main{max-width:192mm;margin:auto;padding:12mm}header{border-bottom:3px solid #147b71;padding-bottom:4mm}"
    + "h1{font-size:23pt;margin:.1em 0}h2{font-size:14pt;color:#116054;margin:1em 0 .3em;break-after:avoid}"
    + "p{margin:.3em 0;overflow-wrap:anywhere}table{border-collapse:collapse;width:100%;table-layout:fixed}"
    + "th,td{padding:.42em .5em;border:1px solid #a6bcbd;text-align:left;vertical-align:top;overflow-wrap:anywhere}"
    + "th{background:#e6f1ef}thead{display:table-header-group}tr{break-inside:avoid}.scope{font-weight:650;color:#116054}"
    + "@media print{main{max-width:none;padding:0}header{break-after:avoid}body{print-color-adjust:exact}}</style><main>"
    + '<header><span class="scope">' + escape(subtitle) + "</span><h1>" + escape(title) + "</h1></header>"
    + body + "</main></html>";
}

export function downloadGasFile(name: string, contents: string, extension: "html" | "md" | "json") {
  const type = ({ html: "text/html", md: "text/markdown", json: "application/json" })[extension];
  const url = URL.createObjectURL(new Blob([contents], { type: type + ";charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name + "." + extension;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function formatGasMetric(metric: GasFieldMetric) {
  if (metric.ratio === null) return "unknown · 无可检查分母";
  return (metric.ratio * 100).toFixed(2) + "% · " + metric.valid + "/" + metric.checked;
}

const scopeLine = (binding: GasM2Binding) => binding.dataset_id + " " + binding.version + " · 登记 SHA-256 " + binding.registry_sha256;
const fileSection = (files: GasM2Source[]): PrintSection => ({
  title: "原件与摘要", columns: ["登记原件", "SHA-256"], rows: files.map(file => [file.name, file.sha256]),
});
function auditSection(audit: GasScenarioAudit[], boundary: string | undefined): PrintSection {
  return {
    title: "审计链", notes: [boundary], columns: ["修订", "操作者 / UTC", "动作与状态", "分析 SHA-256"],
    rows: audit.map(row => ["r" + row.revision, row.actor_id + " · " + row.occurred_at,
      row.action + " · " + (row.previous_status ?? "起始") + " → " + row.current_status, row.analysis_sha256 ?? "待计算"]),
  };
}
const graphLine = (graph: GasDrillGraphState) => graph.reachable_node_ids.length + " 个图可达节点 · " + graph.components.length + " 个连通分量";
const limit = "正式健康评分与实际供气、压力、流量、泄漏和停气预测均为 unavailable。";

export function topicPrintReport(topic: GasDataTopic, name: string) {
  return printDocument(name + " · 燃气专题", topic.selection.scope === "demo" ? "DEMO / SYNTHETIC" : "重庆 GIS · 行政参考", [
    { title: "范围与登记", notes: [scopeLine(topic.binding), topic.rule_version, "专题 SHA-256 " + topic.evidence_sha256,
      "来源状态 " + topic.source_status, "登记资产 " + topic.registered_assets + " · 登记事件 " + topic.registered_events,
      "字段完整率 " + formatGasMetric(topic.field_completeness), "授权管线坐标 unavailable。"] },
    ...(topic.selected_asset ? [{ title: topic.selected_asset.asset_id, columns: ["登记字段","值"],
      rows: Object.entries(topic.selected_asset.fields).map(([key,value]) => [key, value === null ? "unknown" : JSON.stringify(value) ?? "unknown"]) }] : []),
    { title: "来源与审计范围", notes: [topic.administrative_source_uri ?? "资料来源见下列登记引用。",
      ...topic.evidence_refs, "只读专题不写入演练账本。", limit] },
  ]);
}

export function qualityPrintReport(data: GasQualityWorkbench) {
  return printDocument("燃气数据质量工作台", "DEMO / SYNTHETIC", [
    { title: "数据质量", notes: [scopeLine(data.binding), data.rule_version, "质量 SHA-256 " + data.evidence_sha256, limit],
      columns: ["指标口径","计算结果"], rows: data.metrics.map(item => [item.definition, formatGasMetric(item)]) },
    { title: "G01–G15 数据准备矩阵", notes: [data.indicator_version], columns: ["指标","已有","缺失","可合成","待授权"],
      rows: data.preparation.map(row => [row.code + " " + row.name, ...[row.existing_fields, row.missing_fields,
        row.synthetic_possible, row.authorization_needed].map(fields => fields.join("、") || "—")]) },
    fileSection([...data.sources, data.indicator_source]),
    { title: "证据范围", notes: [...data.evidence_refs, "只读质量计算不写入演练账本。"] },
  ]);
}

export function sourceStressPrintReport(data: GasSourceStressResult) {
  const ref = data.selection.scenario;
  return printDocument("气源退出拓扑专题", "DEMO / SYNTHETIC", [
    { title: "输入与计算", notes: [scopeLine(data.selection.binding), data.rule_version,
      ref ? ref.scenario_id + " r" + ref.revision + " · 分析 SHA-256 " + ref.analysis_sha256 : "同源合成基线",
      "模拟退出 " + data.selection.source_id, "固定排除图边 " + (data.excluded_edge_ids.join("、") || "无"),
      "计算 SHA-256 " + data.stress_sha256] },
    { title: "图结果", notes: ["退出前 " + graphLine(data.before), "退出后 " + graphLine(data.after),
      "新增图不可达 " + (data.newly_unreachable_node_ids.join("、") || "无"),
      "保留启用气源 " + (data.remaining_source_ids.join("、") || "无"),
      "替代气源 " + (data.alternate_source_id ?? "无可达气源"),
      "替代气源路径 " + (data.alternate_source_path_edge_ids.join(" → ") || "无图边"),
      "场景替代路径 " + (data.scenario_alternative_path_edge_ids.join(" → ") || "无已计算路径"), data.limitation],
      columns: ["单气源依赖", "所含节点"], rows: Object.entries(data.single_source_dependencies).map(([id,nodes]) => [id,nodes.join("、")]) },
    fileSection(data.source.files), auditSection(data.audit, data.audit_boundary),
  ]);
}

function drillSections(report: GasScenarioReport): PrintSection[] {
  const snapshot = report.scenario, analysis = snapshot.analysis;
  return [
    { title: snapshot.input.pipe_id + " · r" + snapshot.transition.revision, notes: [
      scopeLine(snapshot.input.binding), snapshot.scenario_id, snapshot.transition.current_status,
      analysis ? analysis.algorithm + " · 分析 SHA-256 " + analysis.analysis_sha256 : "本修订待分析",
      ...(analysis?.graph ? [analysis.graph.rule_version + " · 图证据 " + analysis.graph.sha256,
        "排除图边 " + analysis.graph.excluded_edge_ids.join("、"), graphLine(analysis.graph.scenario),
        "新增图不可达 " + (analysis.impact.newly_unreachable_node_ids.join("、") || "无")] : []),
      report.limitation,
    ] },
    fileSection(snapshot.source.files), auditSection(report.audit, report.audit_boundary),
  ];
}

export function drillPrintReport(report: GasScenarioReport) {
  return printDocument("燃气情景专题", "DEMO / SYNTHETIC", drillSections(report));
}

export function comparisonPrintReport(data: GasDrillComparison, side: "before" | "after") {
  return printDocument("燃气情景修订对比", "DEMO / SYNTHETIC", [
    { title: "对比结果", notes: [scopeLine(data.selection.binding), data.rule_version,
      "对比 SHA-256 " + data.comparison_sha256, "当前高亮 " + (side === "before" ? "前侧" : "后侧"),
      "前侧 " + graphLine(data.before.graph), "后侧 " + graphLine(data.after.graph),
      "新增图不可达 " + (data.newly_unreachable_node_ids.join("、") || "无"),
      "新增图可达 " + (data.newly_reachable_node_ids.join("、") || "无"), data.limitation] },
    ...[data.before.report, data.after.report].flatMap(row => row ? drillSections(row) : [{ title: "同源基线" }]),
  ]);
}
