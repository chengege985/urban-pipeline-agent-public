import {describe,it,expect} from "vitest";
import {conversationContext, type GasConversationTurn} from "../features/gas/useGasAgent";
import {toolEventIds} from "../features/gas/gasAgentApi";
const assetTurn=(id:string):GasConversationTurn=>({input:"查看",status:"ready",error:null,
 result:{request_id:"1",status:"succeeded",data_mode:"synthetic",dataset_id:"syn-gas-network-html54-0.1.0",
 model_status:"unavailable",parser_source:"local_rule_not_llm",intent:"asset_query",
 tools:[{tool_name:"asset.query",status:"succeeded",limitation:"synthetic",data:{asset:{asset_id:id}}}]}});
describe("gas conversation context is explicit and bounded by returned objects",()=>{
 it("uses a returned asset for a pronoun",()=>{
  expect(conversationContext("该管段关联哪些事件",[assetTurn("syn-gas-pipe-K-001")],{})).toEqual({asset_id:"syn-gas-pipe-K-001"});
 });
 it("does not attach old context to a new explicit question",()=>{
  expect(conversationContext("查看K-002管段",[assetTurn("syn-gas-pipe-K-001")],{})).toEqual({});
 });
 it("uses the latest successful object across unrelated turns",()=>{
  const noObject={...assetTurn("old"),result:null,error:null};
  expect(conversationContext("刚才的管段",[assetTurn("first"),assetTurn("last"),noObject],{})).toEqual({asset_id:"last"});
 });
 it("respects explicit selection over previous context",()=>{
  expect(conversationContext("当前选中的管段",[assetTurn("old")],{asset_id:"selected"})).toEqual({asset_id:"selected"});
 });
 it("missing context is not fabricated",()=>{
  expect(conversationContext("上一轮的管段",[],{})).toEqual({});
 });
 it("keeps district hints separate from facility coordinates",()=>{
  expect(conversationContext("GIS区县资料",[],{district_id:"500112"})).toEqual({district_id:"500112"});
 });
 it("does not make event actions out of arbitrary items",()=>{
  expect(toolEventIds({tool_name:"event.query",status:"succeeded",limitation:"",data:{items:[{asset_id:"x"}]}})).toEqual([]);
 });
});

it("uses the selected event for affected pipes and clears asset scope on an event switch", () => {
  expect(conversationContext("受影响的管段有哪些？", [assetTurn("old")], { event_id: "active" }))
    .toEqual({ event_id: "active" });
  expect(conversationContext("这个管段关联哪些事件？", [assetTurn("old")], { event_id: "active" }))
    .toEqual({});
});

it("prefers the current asset for a deictic followup and preserves explicit IDs", () => {
  const selected = { asset_id: "selected", event_id: "isolation" };
  expect(conversationContext("这个管段关联哪些事件？", [assetTurn("old")], selected))
    .toEqual({ asset_id: "selected" });
  expect(conversationContext("打开K-999管段。", [assetTurn("old")], selected))
    .toEqual({});
  expect(conversationContext("解释它。", [], selected)).toEqual({});
});
