// Browser integration fixture, not a replacement for Qoder's product pages.
import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { PlatformProvider } from "@m1b/provider";
import { FetchTransport } from "@m1b/transport";

const transport = new FetchTransport("");
const calls: unknown[] = [];
const tracked = { async send(request: any) {
  const response = await transport.send(request);
  calls.push({ request, response });
  return response;
} };
let provider: PlatformProvider;
let config: any;
(window as any).m1b = { calls };

function App() {
  const [dataset, setDataset] = useState<any>();
  const [pipeId, setPipeId] = useState("syn-pipe-fault");
  const [state, setState] = useState("loading");
  const [result, setResult] = useState<any>(null);
  async function action(label: string, work: () => Promise<any>) {
    setState("running:" + label); setResult(null);
    try { const value = await work(); setResult(value); setState("done:" + label); }
    catch (error) { setState("error:" + label); setResult({ error: String(error) }); }
  }
  useEffect(() => { void action("load", async () => {
    const response = await fetch("/m1b/config");
    if (!response.ok) throw new Error("test session required");
    config = await response.json();
    provider = new PlatformProvider({transport: tracked, datasetId: config.snapshotId});
    if (!await provider.probe()) throw new Error("platform unavailable");
    const data = await provider.loadDataset(); setDataset(data);
    return {mode: provider.mode, snapshotId: config.snapshotId, pipes: data.pipes.length};
  }); }, []);
  const constraints = {budgetYuan: 5000, resourceCapacities: {repair_team: 2, water_truck: 2}, confirmedDependencies: []};
  const plans = ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"];
  const timeline = {scenarioId: "scn-syn-isolate", isolationPlanId: "syn-isolate", planId: "syn-plan-combined",
    targetServiceIds: ["syn-hospital-001"], horizonHours: 8};
  async function assessment() {
    let response = await tracked.send({method: "POST", path: "/api/v1/assessment-runs", body: config.recompute,
      idempotencyKey: "m1b-assessment-" + crypto.randomUUID()});
    if (response.status !== 202) throw new Error(JSON.stringify(response));
    let run: any = response.body;
    for (let i = 0; ["queued", "running"].includes(run.status); i++) {
      if (i >= 150) throw new Error("assessment polling timeout");
      await new Promise(resolve => setTimeout(resolve, 200));
      response = await tracked.send({method: "GET", path: "/api/v1/runs/" + run.run_id});
      if (response.status !== 200) throw new Error(JSON.stringify(response));
      run = response.body;
    }
    if (run.status !== "succeeded") throw new Error(JSON.stringify(run));
    const scores: any = await tracked.send({method: "GET", path: "/api/v1/assessments?snapshot_id=" + run.result.snapshot_id});
    return {run, scores: scores.body, scoresStatus: scores.status, selectedPipeId: pipeId,
      note: "新评分输出快照不含运行网络；推演仍使用原合成网络fixture。"};
  }
  return <main style={{fontFamily: "sans-serif", margin: 32, maxWidth: 1100}}>
    <h1>M1-B-01 React–FastAPI 合成联调</h1>
    <p>独立测试入口；调用现有未修改的PlatformProvider。未接正式页面、真实模型或设施。</p>
    <p>恢复指标仅表示图通路恢复，不代表流量、压力或水质达标。</p>
    <label>管段 <select aria-label="管段" value={pipeId} onChange={e=>setPipeId(e.target.value)}>
      {dataset?.pipes.map((p: any)=><option key={p.id} value={p.id}>{p.id}</option>)}
    </select></label>
    <div style={{display:"flex", gap:8, flexWrap:"wrap", margin:"20px 0"}}>
      <button disabled={!dataset} onClick={()=>action("score",()=>provider.evaluate(dataset.pipes.find((p:any)=>p.id===pipeId)))}>读取已有评分</button>
      <button disabled={!dataset} onClick={()=>action("assessment",assessment)}>服务器重新评分</button>
      <button disabled={!dataset || pipeId!=="syn-pipe-fault"} onClick={()=>action("simulate",()=>provider.simulate("scn-syn-isolate","syn-isolate"))}>计算故障影响</button>
      <button disabled={!dataset} onClick={()=>action("plans",()=>provider.comparePlans("scn-syn-isolate",plans,constraints,"syn-isolate"))}>比较治理方案</button>
      <button disabled={!dataset} onClick={()=>action("missing",()=>provider.comparePlans("scn-syn-isolate",plans,{...constraints,resourceCapacities:{repair_team:2}},"syn-isolate"))}>验证缺资源</button>
      <button disabled={!dataset} onClick={()=>action("timeline",async()=>({gate:await provider.checkTimeline(timeline),result:await provider.timeline(timeline)}))}>计算恢复时间</button>
      <button disabled={!dataset} onClick={()=>action("schedule",()=>provider.timeline({...timeline,actionSchedule:[{actionId:"syn-repair",finishOffsetHours:9},{actionId:"syn-backup",finishOffsetHours:9}]}))}>补9小时任务结束记录</button>
      <button disabled>报告：等待RPT01独立PASS</button>
    </div>
    <p data-testid="state">{state}</p>
    <pre data-testid="result" style={{whiteSpace:"pre-wrap", background:"#f4f6f8", padding:20}}>{JSON.stringify(result,null,2)}</pre>
  </main>;
}
createRoot(document.getElementById("root")!).render(<App/>);
