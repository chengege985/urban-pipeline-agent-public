import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes, createHash } from "node:crypto";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
const repo=resolve(".."), out=resolve(process.env.GAS_EVIDENCE_DIR ?? "../../wpn-gas-fastdev-04-05-evidence");
await mkdir(out,{recursive:true});
const realModel=process.env.GAS_AGENT_MODEL_MODE==="local_qwen";
const label=process.env.GAS_EVIDENCE_LABEL ?? (realModel?"05":"04");

const password=randomBytes(24).toString("hex");
const python=process.env.GAS_TEST_PYTHON ?? "/tmp/gas-fix21-venv/bin/python";
const service=spawn(python,["-u",resolve("scripts/serve-gas-agent-acceptance.py"),"--repo",repo],{
  env:{...process.env,GAS_ACCEPTANCE_PASSWORD:password},stdio:["pipe","pipe","pipe"],
});
let partial="",stderr="",ready;
const messages=[],waiters=[];
service.stdout.on("data",(buf)=>{
  partial+=buf.toString(); const lines=partial.split("\n");partial=lines.pop();
  for(const line of lines) { let value;try{value=JSON.parse(line);}catch{continue;}messages.push(value);if(value.kind==="ready") ready=value;
    for(const w of [...waiters]) if(w.test(value)){waiters.splice(waiters.indexOf(w),1);w.resolve(value);}
  }
});
service.stderr.on("data",(buf)=>{stderr+=buf.toString();});
function waitFor(test,ms=30000){
  const existing=messages.find(test);if(existing)return Promise.resolve(existing);
  return new Promise((resolve,reject)=>{
    const w={test,resolve:(v)=>{clearTimeout(timer);resolve(v);}};
    const timer=setTimeout(()=>{const i=waiters.indexOf(w);if(i>=0)waiters.splice(i,1);reject(new Error("harness response timeout"));},ms);
    waiters.push(w);
  });
}
async function mode(value){
  const start=messages.length;service.stdin.write(value+"\n");
  await waitFor((v)=>messages.indexOf(v)>=start && v.kind==="mode" && v.mode===value);
}
const proof={businessInterception:false,checks:[],network:[],screenshots:[],errors:[]};
let browser;
try {
  ready=await waitFor(v=>v.kind==="ready");
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE ??
    "/nix/waterpipnet-runtime/playwright-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell"});
  for(const [width,height] of [[1920,1080],[1280,720],[1024,700]]) {
    const context=await browser.newContext({viewport:{width,height},httpCredentials:{username:"tester",password},reducedMotion:"reduce"});
    const page=await context.newPage();
    page.on("pageerror",err=>proof.errors.push(err.message));
    page.on("response",r=>{
      if(new URL(r.url()).pathname.startsWith("/api/"))proof.network.push({viewport:width,path:new URL(r.url()).pathname,method:r.request().method(),status:r.status()});
    });
    await page.goto(ready.url+"/#/gas",{waitUntil:"networkidle"});
    await page.getByTestId("gas-record-total").filter({hasText:"204"}).waitFor();
    assert.equal(await page.locator(".gas-topology-node").count(),54);
    assert.equal(await page.locator(".gas-topology-edge").count(),73);
    assert.equal(await page.getByTestId("gas-valve-count").innerText(),"31");
    await page.getByRole("button",{name:/独立展开面板/}).click();
    const panel=page.getByTestId("gas-agent-dialog");
    await panel.getByTestId("gas-agent-tools-status").filter({hasText:"ready"}).waitFor();
    assert.match(await panel.getByTestId("gas-agent-execution-banner").innerText(),realModel?/qwen_intent/:/unavailable/);
    async function query(question,status=200) {
      await panel.getByLabel("燃气自然语言输入").fill(question);
      const pending=page.waitForResponse(r=>r.url().endsWith("/agent/query") && r.request().method()==="POST");
      await panel.getByRole("button",{name:"发送查询",exact:true}).click();
      const response=await pending;assert.equal(response.status(),status);
      if(status===200){
        await panel.getByTestId("gas-agent-result").waitFor();
        const data=await response.json();
        await page.waitForFunction(id=>document.querySelector('[data-testid="gas-agent-result"]')?.getAttribute("data-request-id")===id,data.request_id);
        proof.results ??= [];proof.results.push({question, ...data});
        if(realModel && data.tools.length){
          assert.equal(data.parser_source,"qwen_intent"); assert.equal(data.model_status,"ready");
          assert.equal(data.model_id,"Qwen3.8-27B"); assert.equal(data.model_calls,2);
          assert.equal(data.explanation_source,"qwen_evidence_selection");
          assert.ok(data.model_usage.total_tokens>0 && data.evidence_refs.length>0);
          await panel.getByTestId("gas-agent-model-explanation").waitFor();
        } else {
          assert.equal(data.parser_source,"local_rule_not_llm"); assert.equal(data.model_status,"unavailable");
        }
        return data;
      }
      await panel.getByTestId("gas-agent-error").waitFor();
      assert.equal(await panel.getByTestId("gas-agent-result").count(),0);
    }
    let data=await query("查看K-001管段的信息");
    assert.equal(data.tools[0].data.asset.asset_id,"syn-gas-pipe-K-001");
    const assetPending=page.waitForResponse(r=>r.url().endsWith("/assets/syn-gas-pipe-K-001")&&r.status()===200);
    await panel.getByRole("button",{name:"智能体资产详情 syn-gas-pipe-K-001",exact:true}).click();
    await assetPending;
    await page.locator('[data-testid="gas-asset-detail"][data-asset-id="syn-gas-pipe-K-001"]').waitFor();
    await page.getByRole("button",{name:/独立展开面板/}).click();
    data=await query("查看当前有哪些燃气事件");assert.equal(data.tools[0].data.items.length,3);
    data=await query("定位隔离事件关联的管段");assert.equal(data.tools[0].data.asset_ids.length,9);
    await panel.getByTestId("gas-agent-locate-action").click();
    await page.waitForFunction(()=>document.querySelectorAll(".gas-topology-edge.is-selected").length===7&&document.querySelectorAll(".gas-topology-node.is-selected").length===2);
    await page.getByRole("button",{name:/独立展开面板/}).click();
    data=await query("目前支持哪些CUV分析？");
    assert.deepEqual(new Set(data.tools[0].data.items.map(x=>x.status)),new Set(["ready_synthetic","unsupported","unavailable"]));
    data=await query("解释这个隔离事件");assert.equal(data.tools[0].data.event.event_id,"syn-gas-event-isolation");
    const file=resolve(out,label+"-agent-"+width+"x"+height+".png");await page.screenshot({path:file,fullPage:true});
    proof.screenshots.push({file,sha256:createHash("sha256").update(await readFile(file)).digest("hex")});
    proof.checks.push({name:"five tools, asset drill, 9-asset topology and model/tool separation",width,height,pass:true});
    if(width===1920){
      for(const [fault,status] of [["denied",403],["unauthenticated",401],["fixture_unavailable",503]]){
        await mode(fault);await query("查看当前有哪些燃气事件",status);
        await mode("normal");await panel.getByRole("button",{name:"重试查询",exact:true}).click();
        await panel.getByTestId("gas-agent-result").waitFor();
        proof.checks.push({name:fault+" recovery",pass:true});
      }
      data=await query("关闭阀门并执行现场操作");assert.equal(data.tools.length,0);assert.equal(data.status,"unavailable");
      await mode("slow");
      await panel.getByLabel("燃气自然语言输入").fill("查看当前有哪些燃气事件");
      await panel.getByRole("button",{name:"发送查询",exact:true}).click();
      await page.waitForTimeout(150);
      await mode("normal");
      await panel.getByRole("button",{name:"目前支持哪些CUV分析？",exact:true}).click();
      await panel.getByTestId("gas-agent-tool").filter({hasText:"capabilities.query"}).waitFor();
      const winning=await panel.getByTestId("gas-agent-result").getAttribute("data-request-id");
      await page.waitForTimeout(17500);
      assert.equal(await panel.getByTestId("gas-agent-result").getAttribute("data-request-id"),winning);
      proof.checks.push({name:"cancel previous slow query and ignore stale response",pass:true});
      await mode("slow");
      await panel.getByRole("button",{name:"查看当前有哪些燃气事件",exact:true}).click();
      await panel.getByTestId("gas-agent-error").filter({hasText:"超时"}).waitFor({timeout:20000});
      await mode("normal");await panel.getByRole("button",{name:"重试查询",exact:true}).click();await panel.getByTestId("gas-agent-result").waitFor();
      proof.checks.push({name:"actual TCP timeout and retry",pass:true});
      await mode("slow");await panel.getByRole("button",{name:"查看当前有哪些燃气事件",exact:true}).click();
      await panel.getByRole("button",{name:"取消查询",exact:true}).click();await mode("normal");
      assert.match(await panel.getByTestId("gas-agent-request-state").innerText(),/cancelled/);
      await context.setOffline(true);
      await panel.getByRole("button",{name:"重试查询",exact:true}).click();await panel.getByTestId("gas-agent-error").waitFor();
      await context.setOffline(false);await panel.getByRole("button",{name:"重试查询",exact:true}).click();await panel.getByTestId("gas-agent-result").waitFor();
      proof.checks.push({name:"cancel, real browser offline and recovery",pass:true});

      await panel.getByRole("button", {name:"关闭窗口", exact:true}).click();
      await page.goto(ready.url + "/#/gas?map=gis");
      const gis = page.getByTestId("gas-gis-view");
      const districts = gis.getByLabel("选择重庆行政区县");
      const cluster = gis.getByRole("button", {name:"聚焦主城核心区8区"}).locator("circle");
      const markers = gis.locator(".gas-gis-district-node");
      await cluster.waitFor();
      const initialCount = await markers.count();
      assert.ok(initialCount > 1);
      for (const entry of ["视角重置", "全市域"]) {
        await cluster.click();
        await gis.locator('.gas-gis-district-node.is-selected[data-district-id="500103"]').waitFor();
        assert.equal(await gis.getByRole("button", {name:"主城核心区", exact:true}).getAttribute("aria-pressed"), "true");
        if (entry === "视角重置") await gis.getByRole("button", {name:entry, exact:true}).click();
        else await districts.selectOption("");
        await gis.getByText("重庆全域 · 37区县概览", {exact:true}).waitFor();
        assert.equal(new URL(page.url()).hash, "#/gas?map=gis");
        assert.equal(await districts.inputValue(), "");
        assert.equal(await gis.getByRole("button", {name:"全部(37)", exact:true}).getAttribute("aria-pressed"), "true");
        assert.equal(await gis.locator(".gas-gis-district-node.is-selected").count(), 0);
        assert.equal(await gis.getByTestId("gas-gis-viewport-transform").getAttribute("data-zoom"), "1");
        assert.equal(await markers.count(), initialCount);
        proof.checks.push({name:"restore complete city map via " + entry, pass:true});
      }
    }
    await context.close();
  }
  assert.deepEqual(proof.errors,[]);
  proof.status="PASS";
} catch(err){proof.status="FAIL";proof.failure=String(err);process.exitCode=1;}
finally {
  if(browser)await browser.close();
  service.stdin.write("stop\n");service.stdin.end();
  await Promise.race([new Promise(resolve=>service.once("exit",resolve)),new Promise(resolve=>setTimeout(()=>{service.kill("SIGTERM");resolve();},12000))]);
  proof.serviceStopped=messages.some(v=>v.kind==="stopped"&&v.api_stopped);
  await writeFile(resolve(out,label+"-browser.json"),JSON.stringify(proof,null,2));
  await writeFile(resolve(out,label+"-http.jsonl"),messages.map(x=>JSON.stringify(x)).join("\n")+"\n");
  await writeFile(resolve(out,label+"-service.log"),stderr);
  console.log(JSON.stringify({status:proof.status,checks:proof.checks,screenshots:proof.screenshots,failure:proof.failure,serviceStopped:proof.serviceStopped},null,2));
}
