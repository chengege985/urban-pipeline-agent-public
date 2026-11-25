import { describe, it, expect, vi, afterEach } from "vitest";
import { GasAgentApi, validAgentResponse, toolAssetIds } from "../features/gas/gasAgentApi";
import { BrowserGasTransport } from "../features/gas/engineeringApi";
import { gasCuvEngineeringCapability } from "../features/gas/cuvEngineeringContract";
import { probeGasAgentService } from "../features/gas/assistantModel";
afterEach(() => vi.unstubAllGlobals());
const response = {
  request_id: "test-1", status: "partial", data_mode: "synthetic", dataset_id: "syn-gas-network-html54-0.1.0",
  model_status: "unavailable", parser_source: "local_rule_not_llm", intent: "map_locate",
  tools: [{ tool_name: "map.locate", tool_version: "gas-tools-0.1.0", status: "partial",
    data: { view: "topology", event_id: "syn-gas-event-isolation", asset_ids: ["syn-gas-pipe-K-001"], coordinate_authorized: false, location_status: "not_available" },
    evidence_refs: ["gas-m0:gas54_case.json"], limitation: "schematic only" }],
  evidence_refs: ["gas-m0:gas54_case.json"], limitations: ["no model"],
};
describe("Gas Agent actual HTTP contract adapter", () => {
  it("sends a real POST body with same-origin cookies and no service token", async () => {
    const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify(response), { status: 200 }));
    vi.stubGlobal("fetch", fetcher);
    const body = { question: "定位隔离事件关联的管段" };
    const data = await new GasAgentApi(new BrowserGasTransport()).query(body);
    expect(fetcher.mock.calls[0][0]).toBe("/api/v1/gas/engineering/agent/query");
    const options = fetcher.mock.calls[0][1] as RequestInit;
    expect(options.method).toBe("POST");
    expect(options.credentials).toBe("same-origin");
    expect(options.body).toBe(JSON.stringify(body));
    expect(JSON.stringify(options.headers)).not.toMatch(/token|authorization/i);
    expect(data.model_status).toBe("unavailable");
    expect(toolAssetIds(data.tools[0])).toEqual(["syn-gas-pipe-K-001"]);
  });
  for (const status of [401,403,404,422,503]) it("retains HTTP " + status + " without legacy API fallback", async () => {
    const transport = { send: vi.fn(async () => ({ status, body: {detail:"denied"} })) };
    await expect(new GasAgentApi(transport).query({question:"事件"})).rejects.toMatchObject({status});
    expect(transport.send).toHaveBeenCalledTimes(1);
  });
  it("rejects forged coordinates, modes, tools and malformed successful responses", () => {
    expect(validAgentResponse(response)).toBe(true);
    expect(validAgentResponse({...response,data_mode:"real"})).toBe(false);
    expect(validAgentResponse({...response,parser_source:"qwen"})).toBe(false);
    expect(validAgentResponse({...response,tools:[{...response.tools[0],tool_name:"valve.close"}]})).toBe(false);
    expect(validAgentResponse({...response,tools:[{...response.tools[0],data:{...response.tools[0].data,coordinate_authorized:true}}]})).toBe(false);
  });
  it("renders physical capabilities only as missing verified engineering inputs", () => {
    const blocked = {
      capability_id: "steady_pressure_flow", label: "稳态压力与流量", scope: "获准工程工况",
      method: null, model_version: null, status: "blocked", validation_status: "not_business_validated",
      required_input_ids: ["boundary_conditions"], missing_input_ids: ["boundary_conditions"],
      calibration_requirements: ["独立测点校核"], validation_evidence_refs: [], reasons: ["缺少专业校核"],
    };
    expect(gasCuvEngineeringCapability(blocked)).toBe(true);
    expect(gasCuvEngineeringCapability({ ...blocked, status: "ready_synthetic", validation_status: "synthetic_validated",
      method: "unregistered", model_version: "unknown", missing_input_ids: [], validation_evidence_refs: ["unverified"] })).toBe(false);
    expect(gasCuvEngineeringCapability({ ...blocked, model_version: "unverified" })).toBe(false);
  });
  it("keeps validated model provenance separate from the rule fallback", () => {
    const model = {...response,model_status:"ready",parser_source:"qwen_intent",model_id:"Qwen3.8-27B",
      model_calls:2,model_usage:{total_tokens:42},explanation:"服务端证据句",explanation_source:"qwen_evidence_selection"};
    expect(validAgentResponse(model)).toBe(true);
    expect(validAgentResponse({...model,model_calls:"2"})).toBe(false);
    expect(validAgentResponse({...model,model_usage:{total_tokens:-1}})).toBe(false);
    expect(validAgentResponse({...model,model_id:"unapproved-model"})).toBe(false);
    expect(validAgentResponse({...model,explanation:42})).toBe(false);
    expect(validAgentResponse({...model,explanation_source:"unverified_prose"})).toBe(false);
  });
  it("does not replace 503 with an unrelated legacy 200", async () => {
    const fetcher = vi.fn(async () => new Response("{}",{status:503}));
    vi.stubGlobal("fetch",fetcher);
    expect(await probeGasAgentService("")).toMatchObject({status:"unavailable",httpStatus:503});
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("propagates cancellation and rejects broken 200", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(new GasAgentApi({send: async () => {throw new DOMException("aborted","AbortError");}}).query({question:"事件"},controller.signal)).rejects.toMatchObject({name:"AbortError"});
    await expect(new GasAgentApi({send:async()=>({status:200,body:{}})}).query({question:"事件"})).rejects.toMatchObject({code:"invalid_response"});
  });
});
