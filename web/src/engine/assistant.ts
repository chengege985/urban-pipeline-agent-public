import { timelineMatchesContext } from "./timelineContext";
import { ASSISTANT_SUGGESTIONS, DEMO_MODEL_VERSION, DISCLAIMER } from "../domain/constants";
import type {
  AgentContext,
  AgentResponse,
  AgentToolCall,
  DemoConstraints,
  MaintenancePlan,
  Pipe,
  PlanTimelineResult,
  ServiceImpact,
  TimeMetric,
  TimelineGate,
} from "../domain/types";
import { formatCurrency, formatScore, topFactors } from "./format";
import { compareDemoPlans } from "./plans";

const REFUSAL =
  /爆管概率|失效概率|剩余寿命|剩余使用年限|RUL|水力计算|水力模型|直接关阀|自动关阀|下发工单|派单|预测未来|未来\s*\d+\s*个月/i;

const LIMITATIONS = [
  DISCLAIMER,
  "当前为演示助手，只使用本地规则、模板和已配置的合成数据；未连接 Qwen、真实 Agent、RAG 或企业接口。",
  "所有数值均来自 DemoHealth、拓扑影响或固定演示方案，不由语言模型生成。",
];

function makeId(prefix: string): string {
  const random = globalThis.crypto?.randomUUID?.() ?? Math.random().toString(36).slice(2);
  return `${prefix}-${random}`;
}

function tool(
  toolName: string,
  label: string,
  status: AgentToolCall["status"],
  summary: string,
  detail?: string,
): AgentToolCall {
  return { id: makeId("tool"), tool: toolName, label, status, summary, detail };
}

function refusalResponse(question: string): AgentResponse {
  return {
    id: makeId("agent"),
    status: "refused",
    mode: "demo",
    answer:
      "当前演示助手不能提供失效概率、剩余寿命、水力计算、自动关阀或派单结论。" +
      "我可以解释 DemoHealth 的因子、查看合成情景的服务影响，并对比已给出的维护方案。",
    toolCalls: [
      tool(
        "refuse_out_of_scope",
        "超出演示边界",
        "refused",
        "请求涉及未经验证的概率、水力或现场控制能力。",
        `原始问题：${question}`,
      ),
    ],
    evidenceIds: [`rule:demo-assistant-${DEMO_MODEL_VERSION}`],
    limitations: LIMITATIONS,
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: true,
  };
}

function missingContext(message: string): AgentResponse {
  return {
    id: makeId("agent"),
    status: "insufficient_data",
    mode: "demo",
    answer: message,
    toolCalls: [
      tool("get_context", "读取上下文", "insufficient_data", "当前对话缺少必要的管段或情景上下文。"),
    ],
    evidenceIds: [`rule:demo-context-${DEMO_MODEL_VERSION}`],
    limitations: LIMITATIONS,
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: false,
  };
}

function assessmentAnswer(pipe: Pipe | undefined, context: AgentContext): AgentResponse {
  const assessment = context.assessment;
  if (!pipe || !assessment) return missingContext("请先选择一段管段，我再解释它的演示评分和风险因子。");
  const top = topFactors(assessment, 3);
  const factorText = top
    .map((factor) => `${factor.label}（贡献 ${Math.round(factor.contribution * 100)}%，${factor.score} 分）`)
    .join("、");
  return {
    id: makeId("agent"),
    status: assessment.status,
    mode: "demo",
    answer:
      `${pipe.id}「${pipe.name}」的 DemoHealth 为 ${formatScore(assessment.score)} 分，` +
      `等级为「${assessment.grade}」，可信度 ${Math.round(assessment.confidence)}%。` +
      `主要贡献因子：${factorText || "暂无明显贡献"}。` +
      `${assessment.missing.length ? `缺失项：${assessment.missing.join("、")}，未按 0 分处理。` : "当前数据项齐全。"}` +
      `该分数只用于同一演示版本内的相对比较，不是正式健康度或失效概率。`,
    toolCalls: [
      tool(
        "get_pipe",
        "读取管段",
        "succeeded",
        `${pipe.id} · ${pipe.name}`,
        `${pipe.material} / 管龄 ${pipe.installYear ? 2026 - pipe.installYear : "未知"} 年 / 服务 ${pipe.serviceIds.length} 类对象`,
      ),
      tool(
        "evaluate_demo_health",
        "DemoHealth 评价",
        assessment.status === "succeeded" ? "succeeded" : "insufficient_data",
        `得分 ${formatScore(assessment.score)}，可信度 ${Math.round(assessment.confidence)}%`,
        assessment.summary,
      ),
    ],
    evidenceIds: [
      `rule:demo-health-${assessment.version}`,
      `data:${pipe.id}`,
      `rule:missing-policy-${assessment.version}`,
    ],
    limitations: LIMITATIONS,
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: false,
  };
}

function impactAnswer(context: AgentContext): AgentResponse {
  const impact = context.impact;
  if (!impact) return missingContext("请先在情景分析页选择一个故障情景，我再解释它可能影响哪些服务对象。");
  const names = impact.affectedServices.map((service) => service.name).join("、") || "暂无服务对象";
  const critical = impact.affectedCriticalServiceIds.length
    ? `其中关键设施：${impact.affectedServices
        .filter((service) => service.critical)
        .map((service) => service.name)
        .join("、")}。`
    : "当前情景未影响关键设施。";
  return {
    id: makeId("agent"),
    status: impact.status,
    mode: "demo",
    answer:
      `情景「${impact.scenarioId}」下，受影响服务对象为：${names}。` +
      `影响居民约 ${impact.affectedHouseholds ?? "未知"} 户、设施 ${impact.affectedFacilities ?? "未知"} 处；${critical}` +
      `这只是合成管网的拓扑通路影响，不代表实际缺供量、压力或事故概率。`,
    toolCalls: [
      tool(
        "simulate_service_impact",
        "拓扑影响试算",
        impact.status === "succeeded" ? "succeeded" : "insufficient_data",
        `受影响服务 ${impact.affectedServices.length} 个，关键设施 ${impact.affectedCriticalServiceIds.length} 个`,
        `不可达节点：${impact.unreachableNodeIds.join("、") || "无"}`,
      ),
    ],
    evidenceIds: impact.evidenceIds,
    limitations: [...LIMITATIONS, ...impact.limitations],
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: false,
  };
}

function plansAnswer(impact: ServiceImpact, plans: MaintenancePlan[], constraints?: DemoConstraints): AgentResponse {
  if (plans.length === 0) return missingContext("当前没有可用的候选维护方案。");
  if (!constraints) return missingContext("方案比较需要明确预算、资源容量和已确认依赖，请先在情景页设置。没有据此推荐方案。");
  const comparison = compareDemoPlans(impact.scenarioId, impact, plans, constraints);
  if (comparison.status !== "succeeded") return missingContext("当前拓扑条件不完整，不能形成方案推荐。");
  const recommended = comparison.rows.find((row) => row.planId === comparison.recommendedPlanId);
  const rows = comparison.rows
    .map(
      (row) =>
        `${row.rank}. ${row.name}：费用约 ${formatCurrency(row.costYuan)}，` +
        `风险下降 ${row.riskReduction === null ? "未知" : `${row.riskReduction} 点`}，` +
        `合成预设可恢复服务 ${row.restoredServiceCount ?? "未知"} 个，` +
        `${row.feasible === true ? "满足当前约束" : row.feasible === null ? `结果未知：${row.violations.join("、")}` : `不可行：${row.violations.join("、")}`}`,
    )
    .join("；");
  return {
    id: makeId("agent"),
    status: "succeeded",
    mode: "demo",
    answer:
      `以下是当前合成情景下的候选措施演示比较：${rows}。` +
      (recommended ? `当前预算 ${constraints.budgetYuan} 元下推荐「${recommended.name}」；` : `当前预算 ${constraints.budgetYuan} 元及资源约束下没有可行方案，不形成推荐。`) +
      `不是正式 CUV、预算审批或派单结论。`,
    toolCalls: [
      tool(
        "get_maintenance_plans",
        "读取候选方案",
        "succeeded",
        `共 ${plans.length} 个候选方案`,
        plans.map((plan) => `${plan.name}（${plan.type}）`).join("、"),
      ),
      tool(
        "compare_demo_plans",
        "演示方案比较",
        "succeeded",
        `推荐 ${comparison.recommendedPlanId ?? "无"}`,
        comparison.utilityNote,
      ),
    ],
    evidenceIds: [
      `scenario:${impact.scenarioId}`,
      ...comparison.rows.map((row) => `plan:${row.planId}`),
    ],
    limitations: [...LIMITATIONS, ...comparison.limitations],
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: false,
  };
}

function timelineMetricText(metric: TimeMetric): string {
  if (metric.status === "already_available") return "0 小时（初始已可达）";
  if (metric.valueHours !== null) return `${metric.valueHours} 小时${metric.beyondHorizon ? "（超出观察窗口）" : ""}`;
  if (metric.status === "not_recovered_within_horizon") return "观察窗口内未恢复";
  if (metric.status === "not_applicable") return "不适用";
  return "待补数据";
}

function timelineAnswer(timeline: PlanTimelineResult, gate?: TimelineGate): AgentResponse {
  const invalid = timeline.status === "invalid_input";
  const perTarget = timeline.targetServiceIds
    .map((id) => {
      const metric = timeline.serviceFirstRestored[id];
      return `${id}：${metric ? timelineMetricText(metric) : "未返回"}`;
    })
    .join("；");
  const mappedStatus: "succeeded" | "partial" | "insufficient_data" =
    invalid || timeline.status === "invalid_input" || timeline.status === "unsupported" ? "insufficient_data" : timeline.status;
  return {
    id: makeId("agent"),
    status: mappedStatus,
    mode: "demo",
    answer: invalid
      ? `当前时间线输入未通过校验，未生成任何恢复时间：${timeline.issues.join("；") || "输入不完整"}。`
      : `当前方案（${timeline.planId}）的时间线计算结果：方案全部任务完成 ${timelineMetricText(timeline.planCompletion)}；` +
        `首个目标图通路恢复 ${timelineMetricText(timeline.firstTargetServiceRestored)}；` +
        `全部目标首次同时可达 ${timelineMetricText(timeline.allTargetServicesRestored)}。` +
        `${perTarget ? `逐目标：${perTarget}。` : ""}` +
        `基准 ${timeline.asOf}，观察窗口 ${timeline.horizonHours} 小时。` +
        `这里展示的是计划图通路和任务结束记录，不是水力、水质、实际供水量或现场执行。` +
        `${timeline.planCompletion.status === "insufficient_data" ? "缺少任务结束信息时只保留已知的时间线，不补造完成时间。" : ""}`,
    toolCalls: [
      tool(
        "analyze_plan_timeline",
        "计划图通路时间线",
        mappedStatus === "succeeded" ? "succeeded" : mappedStatus === "partial" ? "partial" : "insufficient_data",
        `方案 ${timeline.planId} · ${timeline.targetServiceIds.length} 个目标 · ${timeline.algorithmVersion}`,
        invalid
          ? `输入未通过校验；图事件：0 个`
          : `方案完成：${timelineMetricText(timeline.planCompletion)}；图事件：${timeline.events.length} 个${gate?.missingItems.length ? `；缺项：${gate.missingItems.join("、")}` : ""}`,
      ),
    ],
    evidenceIds: timeline.evidenceIds,
    limitations: [...LIMITATIONS, ...timeline.limitations],
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: false,
  };
}

export function askDemoAssistant(
  question: string,
  context: AgentContext,
  plans: MaintenancePlan[] = [],
): AgentResponse {
  const normalized = question.trim();
  if (!normalized) return missingContext("请输入一个问题，例如“为什么这段管段优先检查？”。");
  if (REFUSAL.test(normalized)) return refusalResponse(normalized);
  if (/恢复|多久|完成时间|时间线|任务结束|全部恢复|首次恢复/.test(normalized)) {
    return context.timeline && timelineMatchesContext(context)
      ? timelineAnswer(context.timeline, context.timelineGate)
      : missingContext("时间线未生成或与当前情景、方案、目标、时间表或版本不一致，请重新计算后再询问。");
  }
  if (/方案|维修|抢修|更换|监测|措施/.test(normalized)) {
    return context.impact
      ? plansAnswer(context.impact, plans, context.constraints)
      : missingContext("方案比较需要先选择一个故障情景；请先在情景分析页选择情景。");
  }
  if (/影响|服务|医院|学校|居民|商业|故障|中断|爆管/.test(normalized)) {
    return impactAnswer(context);
  }
  if (/为什么|解释|优先|因子|评分|健康|可信度|数据/.test(normalized)) {
    return assessmentAnswer(context.pipe, context);
  }
  return {
    id: makeId("agent"),
    status: "succeeded",
    mode: "demo",
    answer:
      "我可以解释 DemoHealth 的评分因子、查看合成情景下的服务影响，或比较已给出的维护方案。" +
      "请选择一段管段或一个情景，也可以直接点击下方建议问题。",
    toolCalls: [
      tool("get_context", "读取上下文", "succeeded", "演示助手已就绪，等待问题。"),
    ],
    evidenceIds: [`rule:demo-assistant-${DEMO_MODEL_VERSION}`],
    limitations: LIMITATIONS,
    suggestedQuestions: ASSISTANT_SUGGESTIONS,
    refused: false,
  };
}
