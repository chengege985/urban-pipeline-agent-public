// Windows controller: existing dependencies, SSH loopback, real Chromium HTTP.
import { createRequire } from "node:module";
import { spawn, execFileSync } from "node:child_process";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { randomBytes } from "node:crypto";
import { createServer } from "node:net";
import { readFile, writeFile, mkdir, access } from "node:fs/promises";
import path from "node:path";
import assert from "node:assert/strict";

const root = process.cwd();
const work = path.resolve(process.argv[2]);
const remote = process.argv[3];
if (!/^\/tmp\/codex-m1b-[a-z0-9]+$/.test(remote)) throw Error("isolated workspace required");
const host = process.env.M1B_SSH ?? "172.30.60.179";
const require = createRequire(path.join(root,"frontend/package.json"));
const {build} = require("esbuild");
const {chromium} = require("playwright");
const out = path.join(work,"results"); await mkdir(out,{recursive:true});
const staticDir = path.join(work,"static"); await mkdir(staticDir,{recursive:true});
await build({entryPoints:[path.join(root,"tests/integration/m1b_01/ui.tsx")],bundle:true,format:"esm",jsx:"automatic",
  outfile:path.join(staticDir,"bundle.js"),nodePaths:[path.join(root,"frontend/node_modules")],
  alias:{"@m1b/provider":path.join(work,"frontend_copy/web/src/providers/platformProvider.ts"),
    "@m1b/transport":path.join(work,"frontend_copy/web/src/providers/platform/transport.ts")}});
await writeFile(path.join(staticDir,"index.html"),'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>M1-B-01 合成联调</title><div id="root"></div><script type="module" src="/bundle.js"></script></html>');
for(const file of ["bundle.js","index.html"]){execFileSync("scp",[path.join(staticDir,file),`${host}:${remote}/static/${file}`]);}
const sessions={alice:randomBytes(32).toString("hex"),bob:randomBytes(32).toString("hex")};
const logs=[];const checks=[];const responses=[];const errors=[];const blockedExternal=[];const attempts=[];
let server,tunnel,browser;
function check(name,value,actual){checks.push({name,pass:!!value,actual});assert.ok(value,name);}
async function freePort(){const s=createServer();s.listen(0,"127.0.0.1");await once(s,"listening");const p=s.address().port;await new Promise(r=>s.close(r));return p;}
async function ready(child){
  const lines=createInterface({input:child.stdout});
  return await new Promise((resolve,reject)=>{
    const timeout=setTimeout(()=>reject(Error("isolated server start timeout")),30000);
    child.once("exit",c=>{clearTimeout(timeout);reject(Error("isolated server exited: "+c));});
    lines.on("line",line=>{try {const x=JSON.parse(line);if(x.ready){clearTimeout(timeout);resolve(x);}}catch{logs.push(line);}});
  });
}
try{
  server=spawn("ssh",["-T",host,`cd /workdata/WaterPipNet/01_工程项目 && /workdata/WaterPipNet/.venv-agent/bin/python -B ${remote}/server.py ${remote}`],{stdio:["pipe","pipe","pipe"],windowsHide:true});
  server.stderr.on("data",b=>logs.push(b.toString()));
  const started=ready(server);server.stdin.write(JSON.stringify({sessions})+"\n");const {port}=await started;
  const local=await freePort();const base=`http://127.0.0.1:${local}`;
  tunnel=spawn("ssh",["-N","-o","ExitOnForwardFailure=yes","-L",`127.0.0.1:${local}:127.0.0.1:${port}`,host],{stdio:["ignore","ignore","pipe"],windowsHide:true});
  tunnel.stderr.on("data",b=>logs.push(b.toString()));
  for(let i=0;i<50;i++){try{if((await fetch(base)).status===200)break;}catch{}if(i===49)throw Error("tunnel not ready");await new Promise(r=>setTimeout(r,200));}
  const candidates=[process.env.M1B_BROWSER,"C:/Users/ASUS/AppData/Local/ms-playwright/chromium-1248/chrome-win64/chrome.exe",chromium.executablePath()].filter(Boolean);
  let executablePath;for(const p of candidates){try{await access(p);executablePath=p;break;}catch{}}
  browser=await chromium.launch({headless:true,executablePath,args:["--disable-background-networking"]});
  async function context(actor){
    const ctx=await browser.newContext({viewport:{width:1440,height:1000}});
    ctx.on("request",request=>{attempts.push({method:request.method(),path:new URL(request.url()).pathname});});
    if(actor)await ctx.addCookies([{name:"m1b_session",value:sessions[actor],url:base,httpOnly:true,sameSite:"Strict"}]);
    await ctx.route("**/*",route=>{if(route.request().url().startsWith(base+"/"))return route.continue();blockedExternal.push(route.request().url());return route.abort();});
    return ctx;
  }
  const ctx=await context("alice");const page=await ctx.newPage();
  page.on("pageerror",e=>errors.push(e.message));
  const pending=[];
  page.on("response",res=>{
    if(!res.url().includes("/api/"))return;
    pending.push((async()=>{let body;try{body=await res.json();}catch{body=null;}const headers=await res.request().allHeaders();responses.push({method:res.request().method(),path:new URL(res.url()).pathname,status:res.status(),tokenHeaderPresent:"x-water-token" in headers,request:res.request().postDataJSON(),body});})());
  });
  await page.goto(base);await page.getByTestId("state").filter({hasText:"done:load"}).waitFor();
  const result=async()=>JSON.parse(await page.getByTestId("result").innerText());
  const click=async(label,state)=>{await page.getByRole("button",{name:label,exact:true}).click();await page.waitForFunction(s=>document.querySelector('[data-testid="state"]').textContent===s,"done:"+state,{timeout:45000});return result();};
  const loaded=await result();
  check("six_pipe_live_fixture",loaded.pipes===6,loaded);
  await page.getByLabel("管段").selectOption("syn-pipe-fault");
  const score=await click("读取已有评分","score");
  check("existing_score_78_5",score.score===78.5,score);
  const recompute=await click("服务器重新评分","assessment");
  check("assessment_run_completed",recompute.run.status==="succeeded" && recompute.run.result.model_version==="0.1.1",recompute);
  check("assessment_snapshot_is_output",recompute.run.result.snapshot_id!==loaded.snapshotId && recompute.scoresStatus===200,recompute.run.result.snapshot_id);
  const recalculated=recompute.scores.items.find(x=>x.pipe_id==="syn-pipe-fault");
  check("recomputed_score_and_identity",recompute.scores.snapshot_id===recompute.run.result.snapshot_id && recompute.run.result.run_id===recompute.run.run_id && recalculated.legacy_result.Hs===78.5 && recalculated.legacy_result.Rs===1.86,recalculated);
  const impact=await click("计算故障影响","simulate");
  check("topology_fixture",impact.affectedHouseholds===200 && impact.affectedFacilities===1 &&
    JSON.stringify(impact.affectedServices.map(x=>x.serviceId).sort())===JSON.stringify(["syn-hospital-001","syn-households-C","syn-households-U"]),impact);
  const plans=await click("比较治理方案","plans");
  const combined=plans.rows.find(x=>x.planId==="syn-plan-combined");
  check("combined_recommended",plans.recommendedPlanId==="syn-plan-combined" && combined.costFen===370000 && combined.costYuan===3700 && combined.remainingAffectedServices===0,plans);
  const missing=await click("验证缺资源","missing");
  check("missing_resource_not_infeasible",missing.status==="insufficient_data" && missing.recommendedPlanId===null && missing.rows.find(x=>x.planId==="syn-plan-backup").feasible===null,missing);
  const timeline=await click("计算恢复时间","timeline");
  check("timeline_partial_restoration_6h",timeline.result.planCompletion.valueHours===null && timeline.result.planCompletion.status==="insufficient_data" && timeline.result.firstTargetServiceRestored.valueHours===6 && timeline.result.allTargetServicesRestored.valueHours===6 && timeline.result.serviceFirstRestored["syn-hospital-001"].valueHours===6,timeline);
  const schedule=await click("补9小时任务结束记录","schedule");
  check("completion_9h_recovery_6h",schedule.planCompletion.valueHours===9 && schedule.planCompletion.beyondHorizon===true && schedule.firstTargetServiceRestored.valueHours===6,schedule);
  await page.screenshot({path:path.join(out,"browser.png"),fullPage:true});
  await Promise.all(pending);
  const run=responses.find(x=>x.path==="/api/v1/simulation-runs" && x.status===202).body;
  const bob=await context("bob"),anon=await context(null);
  for(const [label,c,status] of [["cross_principal",bob,403],["anonymous",anon,401]]){
    const p=await c.newPage();await p.goto(base);
    const value=await p.evaluate(async path=>{const r=await fetch(path,{headers:{"X-Water-Client":"unified-react","X-Agent-Actor":"m1b-alice","X-Water-Token":"spoof"}});return r.status;},`/api/v1/runs/${run.run_id}`);
    check(label,value===status,value);
  }
  const configReply=await page.evaluate(async()=>await(await fetch("/m1b/config")).json());
  const denied=await page.evaluate(async sid=>(await fetch(`/api/v1/datasets/${sid}/view`)).status,configReply.deniedSnapshotId);
  check("dataset_scope_denied",denied===403,denied);
  const audit=await page.evaluate(async()=>await(await fetch("/m1b/audit")).json());
  check("server_computed",audit.counts.calculate_assessment===1 && audit.counts.simulate_snapshot===1 && audit.counts.compare_snapshot===2,audit.counts);
  check("report_and_agent_not_called",![...audit.requests,...attempts].some(x=>x.path.endsWith("/report")||x.path.includes("/agent/")) && await page.getByRole("button",{name:"报告：等待RPT01独立PASS"}).isDisabled(),audit.requests.length);
  await Promise.all(pending);
  check("browser_no_token_header",responses.every(x=>!x.tokenHeaderPresent),responses.length);
  check("session_cookie_http_only",!(await page.evaluate(()=>document.cookie)).includes("m1b_session"),true);
  check("no_external_network",blockedExternal.length===0 && audit.outboundAttempts.length===0,{browser:blockedExternal,backend:audit.outboundAttempts});
  check("no_page_errors",errors.length===0,errors);
  await writeFile(path.join(out,"backend_audit.json"),JSON.stringify(audit,null,2));
  await writeFile(path.join(out,"browser_environment.json"),JSON.stringify({browser:browser.version(),node:process.version,base,executablePath,react:require("react/package.json").version,playwright:require("playwright/package.json").version},null,2));
}catch(error){checks.push({name:"runner_error",pass:false,actual:error.stack});}
finally{
  if(browser)await browser.close();
  if(server){server.stdin.end("STOP\n");await Promise.race([once(server,"exit"),new Promise(r=>setTimeout(r,10000))]);if(server.exitCode===null)server.kill();}
  if(tunnel)tunnel.kill();
  await writeFile(path.join(out,"results.json"),JSON.stringify({checks,responses,attempts,errors,logs,blockedExternal},null,2));
  console.log(JSON.stringify({passed:checks.filter(x=>x.pass).length,failed:checks.filter(x=>!x.pass),output:out}));
}
if(checks.some(x=>!x.pass))process.exitCode=1;
