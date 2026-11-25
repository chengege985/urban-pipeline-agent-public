import { describe, expect, it } from "vitest";
import { GAS_THEME_KEY, gasAgentPresentation, readGasTheme } from "../features/gas/gasTheme";
import type { GasAgentHealthResponse, GasAgentQueryResponse } from "../features/gas/gasAgentApi";
const health: GasAgentHealthResponse = { enabled: true, read_only: true, model_status: "ready", parser_source: "qwen_intent", tools_status: "ready", provider_configured: true, status: "ready", reason: "local model" };
const state = {health, result: null, healthError: null, error: null, probing: false};
const result = (model_status: "ready" | "unavailable") => ({model_status, parser_source: model_status === "ready" ? "qwen_intent" : "local_rule_not_llm", model_id: "Qwen3.8-27B"} as GasAgentQueryResponse);
describe("Gas workbench preferences and truthful service status", () => {
  it("defaults to dark for no, invalid or denied storage", () => {
    expect(readGasTheme()).toBe("dark");
    expect(readGasTheme({getItem: () => "unknown"})).toBe("dark");
    expect(readGasTheme({getItem: () => {throw new Error("blocked");}})).toBe("dark");
  });
  it("reads only the scoped, valid light preference", () => {
    const keys: string[] = [];
    expect(readGasTheme({getItem: (key) => {keys.push(key);return "light";}})).toBe("light");
    expect(keys).toEqual([GAS_THEME_KEY]);
  });
  it("does not invent availability before health returns", () => {
    expect(gasAgentPresentation({...state, health:null}).tone).toBe("pending");
  });
  it("uses validated health and distinguishes live model from rule tools", () => {
    expect(gasAgentPresentation(state).tone).toBe("ready");
    expect(gasAgentPresentation({...state,health:{...health,model_status:"unavailable",parser_source:"local_rule_not_llm",status:"unavailable"}}).tone).toBe("fallback");
  });
  it("never displays an old Qwen answer as current online health", () => {
    expect(gasAgentPresentation({...state,result:result("ready"),health:{...health,model_status:"unavailable",parser_source:"local_rule_not_llm",status:"unavailable"}}).tone).toBe("fallback");
  });
  it("shows query degradation even after a successful health probe", () => {
    expect(gasAgentPresentation({...state,result:result("unavailable")}).tone).toBe("fallback");
  });
  it.each(["healthError","error"] as const)("does not hide %s behind previous successes", (key) => {
    expect(gasAgentPresentation({...state,result:result("ready"),[key]:new Error("denied")}).tone).toBe("error");
  });
  it("keeps a health refresh distinct from confirmed readiness", () => {
    expect(gasAgentPresentation({...state,probing:true}).tone).toBe("pending");
  });
});
