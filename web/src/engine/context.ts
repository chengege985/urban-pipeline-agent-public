import type { AnalysisContext, WaterPulseDataset } from "../domain/types";

/** Stable identity for an input, independent of object-key insertion order. */
export function inputKey(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(inputKey).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.entries(value).sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => `${JSON.stringify(key)}:${inputKey(item)}`).join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

export function currentContext(context: AnalysisContext | null | undefined, dataset: WaterPulseDataset): AnalysisContext | null {
  if (!context || context.dataVersion !== dataset.meta.dataVersion || context.modelVersion !== dataset.meta.modelVersion) return null;
  if (!dataset.pipes.some((pipe) => pipe.id === context.pipeId)) return null;
  if (!dataset.isolationPlans.some((plan) => plan.id === context.isolationPlanId && plan.scenarioId === context.scenarioId)) return null;
  return context;
}
