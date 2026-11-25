import { useCallback, useEffect, useRef, useState } from "react";
import type {
  GasDrillComparison, GasDrillReference, GasDrillToolRequest, GasDrillToolResult, GasM2Binding,
  GasScenarioExplanation, GasSyntheticScenario,
} from "../../shared/contracts/platform";
import { useGasCockpit } from "./GasCockpitProvider";
import { GasApiError, toGasError } from "./engineeringApi";
import { gasScenarioId, scenarioReference } from "./scenarioContract";
import { comparisonMarkdown, scenarioMarkdown } from "./scenarioReport";
import { comparisonPrintReport, downloadGasFile, drillPrintReport } from "./printableGasReport";
import { useGasRead } from "./useGasRead";

interface DrillState {
  key: string | null;
  scenario?: GasSyntheticScenario;
  answer?: GasScenarioExplanation | GasDrillToolResult;
  comparison?: GasDrillComparison;
  highlighted?: "before" | "after";
  busy?: string;
  error?: GasApiError;
  notice?: string;
}

const storagePrefix = "wpn.gas.synthetic-scenario:";

function savedScenario(key: string) {
  try { return sessionStorage.getItem(key); } catch { return null; }
}

function rememberScenario(key: string, id: string | null) {
  try {
    if (id) sessionStorage.setItem(key, id);
    else sessionStorage.removeItem(key);
  } catch {
    // Browser storage may be unavailable. The backend ledger remains authoritative.
  }
}

export function useGasScenario(binding: GasM2Binding | null) {
  const { scenarioApi: api } = useGasCockpit();
  const key = binding ? storagePrefix + JSON.stringify([binding.dataset_id, binding.version, binding.registry_sha256]) : null;
  const [state, setState] = useState<DrillState>({ key: null });
  const inFlight = useRef<AbortController | null>(null);
  const current = state.key === key ? state : { key };
  const readWorkspace = useCallback((signal: AbortSignal) => api.drillWorkspace(binding!, signal), [api, binding]);
  const workspace = useGasRead(key, readWorkspace);

  useEffect(() => {
    if (binding && key) {
      const id = savedScenario(key);
      if (gasScenarioId(id)) void execute("读取演练", async signal => ({ scenario: await api.scenario(id, binding, signal) }));
    }
    return () => {
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [api, binding, key]);

  async function execute(busy: string, work: (signal: AbortSignal) => Promise<Partial<DrillState>>) {
    if (!key || inFlight.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    setState(previous => ({ ...(previous.key === key ? previous : { key }), busy, error: undefined, notice: undefined }));
    try {
      const patch = await work(controller.signal);
      if (controller.signal.aborted) return;
      if (patch.scenario) rememberScenario(key, patch.scenario.scenario_id);
      setState(previous => ({ ...previous, ...patch, key, busy: undefined }));
    } catch (caught) {
      if (controller.signal.aborted) return;
      const error = toGasError(caught);
      const invalidate = error.status !== undefined && [401, 403, 404, 409].includes(error.status);
      if (error.status === 404 && busy === "读取演练") rememberScenario(key, null);
      setState(previous => ({ ...(invalidate ? { key } : previous), busy: undefined, error }));
    } finally {
      if (inFlight.current === controller) inFlight.current = null;
    }
  }

  const drill = current.scenario;
  const comparison = current.comparison;
  const highlighted = current.highlighted ?? "after";
  return {
    ...current,
    workspace,
    highlighted,
    recover: binding && key && gasScenarioId(savedScenario(key)) ? () => {
      const id = savedScenario(key);
      if (gasScenarioId(id)) void execute("读取演练", async signal => ({
        scenario: await api.scenario(id, binding, signal), answer: undefined, comparison: undefined,
      }));
    } : undefined,
    create: (pipeId: string, faultKind: GasSyntheticScenario["input"]["fault_kind"] = "synthetic_edge_exclusion") =>
      binding && execute("创建演练", async signal => ({
        scenario: await api.createScenario(binding, pipeId, faultKind, signal), answer: undefined, comparison: undefined,
      })),
    advance: (action: "analyze" | "rollback", onAnalyzed?: () => void) => drill && execute(
      action === "analyze" ? "执行图连通分析" : "回退演练", async signal => {
        const scenario = await api.advanceScenario(drill, action, signal);
        if (action === "analyze" && !signal.aborted) onAnalyzed?.();
        if (!signal.aborted) workspace.retry();
        return { scenario, answer: undefined, comparison: undefined };
      }),
    explain: (question: string) => drill && execute("读取计算证据", async signal => ({
      answer: await api.explainScenario(drill, question, signal),
    })),
    compare: (before: GasDrillReference | null, after: GasDrillReference | null, show: () => void) =>
      binding && (before || after) && execute("核验选中修订", async signal => {
        const result = await api.compareScenarios({ binding, before, after }, signal);
        if (!signal.aborted) show();
        return { comparison: result, highlighted: "after", answer: undefined };
      }),
    selectSide: (side: "before" | "after") => setState(previous => ({ ...previous, highlighted: side })),
    leaveComparison: () => setState(previous => ({ ...previous, comparison: undefined, answer: undefined, notice: undefined })),
    queryTool: (tool: GasDrillToolRequest["tool"], question: string) => {
      if (!binding) return;
      const ref = drill && scenarioReference(drill);
      const context = tool === "compare" && comparison
        ? { selection: comparison.selection, expected_sha256: comparison.comparison_sha256 }
        : tool !== "compare" && ref
          ? { selection: { binding, before: null, after: ref }, expected_sha256: ref.analysis_sha256 } : null;
      if (context) void execute("运行只读工具", async signal => ({
        answer: await api.queryDrillTool({ tool, question, ...context }, signal),
      }));
    },
    download: (format: "json" | "md" | "html" = "json") => (comparison || drill) && execute("导出演练报告", async signal => {
      if (comparison) {
        const latest = await api.compareScenarios(comparison.selection, signal);
        if (latest.comparison_sha256 !== comparison.comparison_sha256)
          throw new GasApiError("http", "对比证据已变化，请重新选择修订。", 409);
        const content = format === "html" ? comparisonPrintReport(latest, highlighted)
          : format === "md" ? comparisonMarkdown(latest, highlighted) : JSON.stringify({ ...latest, highlighted }, null, 2) + "\n";
        if (!signal.aborted) downloadGasFile("gas-drill-comparison-" + comparison.comparison_sha256.slice(0, 12), content, format);
      } else if (drill) {
        const report = await api.scenarioReport(drill, signal);
        const content = format === "html" ? drillPrintReport(report)
          : format === "md" ? scenarioMarkdown(report) : JSON.stringify(report, null, 2) + "\n";
        if (!signal.aborted) downloadGasFile(drill.scenario_id + "-r" + drill.transition.revision, content, format);
      }
      return { notice: "报告已导出，含同一版本的计算与审计证据。" };
    }),
  };
}

export type GasScenarioController = ReturnType<typeof useGasScenario>;
