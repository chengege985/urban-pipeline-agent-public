import { useEffect, useState } from "react";
import type { GasAgentHealthResponse, GasAgentQueryResponse } from "./gasAgentApi";

export type GasTheme = "dark" | "light";
export const GAS_THEME_KEY = "wpn-gas-theme";
const GAS_THEME_EVENT = "wpn-gas-theme-change";
export function readGasTheme(storage?: Pick<Storage, "getItem">): GasTheme {
  try { return storage?.getItem(GAS_THEME_KEY) === "light" ? "light" : "dark"; }
  catch { return "dark"; }
}
export function useGasTheme() {
  const [theme, setTheme] = useState<GasTheme>(() => {
    try { return readGasTheme(window.localStorage); } catch { return "dark"; }
  });
  useEffect(() => {
    try { window.localStorage.setItem(GAS_THEME_KEY, theme); } catch { /* Preference is optional. */ }
  }, [theme]);
  useEffect(() => {
    const onThemeChange = (event: Event) => {
      const next = (event as CustomEvent<GasTheme>).detail;
      if (next === "dark" || next === "light") setTheme(next);
    };
    window.addEventListener(GAS_THEME_EVENT, onThemeChange);
    return () => window.removeEventListener(GAS_THEME_EVENT, onThemeChange);
  }, []);
  const toggleTheme = () => setTheme((current) => {
    const next = current === "dark" ? "light" : "dark";
    window.dispatchEvent(new CustomEvent<GasTheme>(GAS_THEME_EVENT, { detail: next }));
    return next;
  });
  return { theme, toggleTheme };
}
export function gasAgentPresentation(agent: {
  health: GasAgentHealthResponse | null; result: GasAgentQueryResponse | null;
  healthError: unknown; error: unknown; probing: boolean;
}) {
  if (agent.healthError || agent.error) return { tone: "error", label: "服务异常", detail: "当前请求未成功，请在助手中查看错误并重试。" };
  if (agent.probing) return { tone: "pending", label: "检测中", detail: "正在检测模型和只读工具状态。" };
  const model = agent.health?.model_status;
  const parser = agent.health?.parser_source;
  if (model === "ready" && parser === "qwen_intent" && agent.result?.model_status !== "unavailable")
    return { tone: "ready", label: "Qwen 在线", detail: agent.result?.model_id ?? "模型服务就绪；发送后查看实际模型与证据。" };
  if (model === "starting") return { tone: "pending", label: "Qwen 启动中", detail: "预热期间可使用只读规则查询。" };
  if (agent.health?.tools_status === "ready")
    return { tone: "fallback", label: model === "ready" ? "本次规则查询" : "只读工具可用", detail: model === "ready" ? "服务检测就绪，但本次结果来自 local_rule_not_llm；不将规则结果标为模型推理。" : "模型当前不可用；local_rule_not_llm 为明确标记的规则降级。" };
  return { tone: "pending", label: "状态待确认", detail: "尚未确认模型与工具是否可用。" };
}
