import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes, createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { readdir } from "node:fs/promises";
import { chromium } from "playwright";
const repo=resolve(".."), out=resolve(process.env.WPN_EVIDENCE_DIR ?? "../../wpn-frontend-editions-01-evidence");
await mkdir(out,{recursive:true});
const proof={businessInterception:false,checks:[],network:[],screenshots:[],errors:[]};
const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
let active;
try {
 for(const edition of ["competition","project"]) {
  const dist=resolve("dist",edition);
  const manifest=JSON.parse(await readFile(resolve(dist,"frontend-product.json"),"utf8"));
  assert.equal(manifest.publisher,edition==="competition"?"Qoder":"Codex");
  if(edition==="project") {
   assert.equal(manifest.waterRuntimeModuleCount,0);
   assert.equal(manifest.domains.join(","),"gas");
   for(const file of await readdir(resolve(dist,"assets"))) {
    if(file.endsWith(".js")) assert.doesNotMatch(await readFile(resolve(dist,"assets",file),"utf8"),/水务|供水|水脉|WaterPulse|DemoHealth|城市管网总览|既有平台报告/);
   }
  }
  const password=randomBytes(24).toString("hex");
  const service=spawn(process.env.GAS_TEST_PYTHON ?? "/tmp/gas-fix21-venv/bin/python",
    ["-u",resolve("scripts/serve-gas-agent-acceptance.py"),"--repo",repo,"--edition",edition],
    {env:{...process.env,GAS_AGENT_MODEL_MODE:"disabled",GAS_ACCEPTANCE_PASSWORD:password},stdio:["pipe","pipe","pipe"]});
  active=service;
  const messages=[];let partial="",stderr="";
  service.stdout.on("data",chunk=>{partial+=chunk.toString();const lines=partial.split("\n");partial=lines.pop();
   for(const line of lines){try{messages.push(JSON.parse(line));}catch{/* Non-JSON diagnostics aren't HTTP evidence. */}}});
  service.stderr.on("data",chunk=>{stderr+=chunk.toString();});
  async function wait(test) {
   const until=Date.now()+30000;
   while(Date.now()<until){const v=messages.find(test);if(v)return v;
    if(service.exitCode!==null)throw Error("acceptance service stopped");
    await new Promise(r=>setTimeout(r,40));}
   throw Error("acceptance service timeout");
  }
  async function fault(mode){const start=messages.length;service.stdin.write(mode+"\n");
   await wait(v=>messages.indexOf(v)>=start&&v.kind==="mode"&&v.mode===mode);}
  try {
   const ready=await wait(v=>v.kind==="ready");
   for(const [width,height] of [[1920,1080],[1280,720],[1024,700]]) {
    const context=await browser.newContext({viewport:{width,height},httpCredentials:{username:"tester",password},reducedMotion:"reduce"});
    const page=await context.newPage();page.setDefaultTimeout(12000);
    page.on("pageerror",err=>proof.errors.push({edition,width,error:err.message}));
    page.on("response",r=>{const path=new URL(r.url()).pathname;
     if(path.startsWith("/api/"))proof.network.push({edition,width,path,method:r.request().method(),status:r.status()});});
    const passed=name=>proof.checks.push({edition,width,height,name,pass:true});
    async function shot(name){const file=resolve(out,edition+"-"+name+"-"+width+"x"+height+".png");
     await page.screenshot({path:file,fullPage:true});
     proof.screenshots.push({file,sha256:createHash("sha256").update(await readFile(file)).digest("hex")});}
    async function gasOnlyUi(){
     assert.doesNotMatch(await page.locator("body").innerText(),/供水|水务|水脉|WaterPulse|DemoHealth|既有平台报告|城市管网总览/);
     const labels=await page.locator("[aria-label],[title],button,a").evaluateAll(nodes=>nodes.map(e=>[e.getAttribute("aria-label"),e.getAttribute("title"),e.textContent].join(" ")).join("\\n"));
     assert.doesNotMatch(labels,/供水|水务|水脉|既有平台报告/);
    }
    async function gasReady() {
     await page.getByTestId("gas-record-total").filter({hasText:"204"}).waitFor();
     await page.waitForFunction(() =>
      document.querySelectorAll(".gas-topology-node").length === 54 &&
      document.querySelectorAll(".gas-event-list > button").length === 3);
    }
    await page.goto(ready.url+"/",{waitUntil:"networkidle"});
    assert.match(await page.title(),edition==="competition"?/赛题3/:/项目演示/);
    if(edition==="competition") {
     await page.getByRole("heading",{name:"城市管网总览",exact:true}).waitFor();
     assert.equal(new URL(page.url()).hash,"#/overview");
     assert.equal(proof.network.filter(x=>x.edition===edition&&x.width===width).length,0);
     await shot("water");
     if(width===1920) {
      await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"管段分析",exact:true}).click();
      await page.getByRole("heading",{name:/WP-001/}).waitFor();
      await page.goto(ready.url+"/?mode=demo#/scenario/SC-HOSP?pipe=WP-016");
      await page.getByRole("button",{name:"选择方案",exact:true}).first().click();
      await page.getByRole("button",{name:"生成决策简报",exact:true}).click();
      await page.getByRole("heading",{name:"决策简报",exact:true}).waitFor();
      assert.match(await page.locator(".report-head").innerText(),/WP-016/);
      assert.ok(await page.getByRole("button",{name:"下载 JSON",exact:true}).isEnabled());
      const reportHash=new URL(page.url()).hash;
      await page.reload();await page.getByRole("heading",{name:"决策简报",exact:true}).waitFor();
      assert.equal(new URL(page.url()).hash,reportHash);await shot("water-report");
      await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"AI 助手",exact:true}).click();
      await page.getByLabel("向演示助手提问").fill("比较方案");
      await page.getByRole("button",{name:"发送",exact:true}).click();
      await page.locator(".chat-message.assistant .chat-text").first().waitFor();
      await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"关于",exact:true}).click();
      await page.getByRole("heading",{name:"关于水脉守护 AI",exact:true}).waitFor();
      passed("water pipe/scenario/comparison/report+reload/assistant/about retained");
     }
     await page.getByRole("navigation",{name:"主导航"}).getByRole("button",{name:"燃气中控",exact:true}).click();
     passed("competition water landing and explicit gas switch");
    } else {
     assert.equal(await page.getByRole("button",{name:"供水中控",exact:true}).count(),0);
     assert.equal(await page.getByRole("navigation",{name:"主导航"}).count(),0);
     passed("project gas-only landing");
    }
    await gasReady();
    assert.equal(await page.locator(".gas-topology-node").count(),54);
    assert.equal(await page.locator(".gas-topology-edge").count(),73);
    assert.equal(await page.getByTestId("gas-valve-count").innerText(),"31");
    assert.equal(await page.locator(".gas-event-list > button").count(),3);
    assert.match(await page.getByTestId("product-edition-label").innerText(),edition==="competition"?/赛题3/:/项目演示/);
    assert.equal(await page.locator(".gas-workbench").count(),1);
    assert.match(await page.locator(".gas-provenance-badge").innerText(),/合成.*未经业务验证/);
    await shot("gas-dark");
    if(edition==="project") {
     await gasOnlyUi();
     const icon=await page.locator('link[rel="icon"]').getAttribute("href");
     assert.match(decodeURIComponent(icon),/M35 6C40 20/);
     assert.doesNotMatch(await page.title(),/水/);
    }
    await page.locator(".gas-event-list > button").filter({hasText:"syn-gas-event-isolation"}).click();
    const event=page.getByTestId("gas-event-detail");await event.waitFor();
    const evidenceName={name:"证据引用",exact:true};
    const eventEvidence=event.getByRole("region",evidenceName);
    await eventEvidence.waitFor();
    const eventRefs=await eventEvidence.locator("li").allTextContents();
    const groups=event.getByTestId("gas-event-asset-groups");
    const assetKinds=await groups.locator("[data-asset-kind]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-asset-kind")));
    assert.ok(assetKinds.includes("pipe_asset") && assetKinds.includes("valve_asset"));
    const assets=groups.locator(".gas-related-list button");
    assert.equal(await assets.count(),9);
    const assetId=(await assets.first().getAttribute("aria-label")).replace("打开资产详情 ","");
    await Promise.all([page.waitForResponse(r=>r.url().endsWith("/assets/"+assetId)&&r.status()===200),assets.first().click()]);
    const selectedAsset=page.locator('[data-testid="gas-asset-detail"][data-asset-id="'+assetId+'"]');
    await selectedAsset.waitFor();
    const assetEvidence=selectedAsset.getByRole("region",evidenceName);
    await assetEvidence.waitFor();
    const assetRefs=await assetEvidence.locator("li").allTextContents();
    await page.locator(".gas-view-settings summary").click();
    await page.locator('[data-window-control="evidence"]').click();
    const evidenceDialog=page.getByRole("dialog");
    const evidence=evidenceDialog.getByRole("region",evidenceName);
    const eventGroup=evidence.getByRole("heading",{name:"事件 syn-gas-event-isolation",exact:true}).locator("..");
    const assetGroup=evidence.getByRole("heading",{name:"资产 "+assetId,exact:true}).locator("..");
    await eventGroup.waitFor();
    await assetGroup.waitFor();
    assert.deepEqual(await eventGroup.locator("li").allTextContents(),eventRefs);
    assert.deepEqual(await assetGroup.locator("li").allTextContents(),assetRefs);
    const noRefs="接口未提供证据引用。";
    if(!eventRefs.length)assert.equal(await eventGroup.getByText(noRefs,{exact:true}).count(),1);
    if(!assetRefs.length)assert.equal(await assetGroup.getByText(noRefs,{exact:true}).count(),1);
    await evidenceDialog.getByRole("button",{name:"关闭窗口",exact:true}).click();
    passed("event and asset evidence independently preserved after real drilldown");
    const topology=page.getByTestId("gas-topology-view");
    const topologyAsset='[data-asset-id="'+assetId+'"]';
    const matchingTopologyObject=topology.locator(topologyAsset).first();
    const focusedTopologyObject=topology.locator(topologyAsset+'[data-asset-focus="selected"]').first();
    await focusedTopologyObject.waitFor();
    assert.ok(await page.getByTestId("gas-topology-view").locator('[data-asset-focus="related"]').count() > 0);
    await page.getByRole("button",{name:"查看 GIS 位置状态",exact:true}).click();
    const gisBanner=page.getByTestId("gas-gis-locate-banner");
    await gisBanner.getByText("资产定位校验："+assetId,{exact:true}).waitFor();
    assert.match(await gisBanner.innerText(),/没有经授权的真实 GIS 点位/);
    assert.equal(await page.locator("[data-authorized-asset],[data-authorized-event]").count(),0);
    await gisBanner.getByRole("button",{name:"切换到合成拓扑定位资产",exact:true}).click();
    await focusedTopologyObject.waitFor();
    await page.getByRole("button",{name:"返回事件详情",exact:true}).click();
    await event.waitFor();
    assert.equal(await page.getByTestId("gas-asset-detail").count(),0);
    assert.match(new URL(page.url()).hash,/events\/syn-gas-event-isolation/);
    await matchingTopologyObject.press("Enter");
    await selectedAsset.waitFor();
    assert.match(new URL(page.url()).hash,/events\/syn-gas-event-isolation\/assets\//);
    const otherEvent=page.locator('.gas-event-list > button:not([data-event-id="syn-gas-event-isolation"])').first();
    const otherId=await otherEvent.getAttribute("data-event-id");
    await otherEvent.click();
    await page.locator('[data-testid="gas-event-detail"][data-event-id="'+otherId+'"]').waitFor();
    assert.equal(await page.getByTestId("gas-asset-detail").count(),0);
    assert.ok(!new URL(page.url()).hash.includes("/assets/"));
    passed("grouped assets, topology selection, GIS fallback, return to event and old identity cleared");
    await page.waitForTimeout(150);const requests=proof.network.length;
    await page.getByTestId("gas-theme-toggle").click();await page.waitForTimeout(200);
    assert.equal(proof.network.length,requests);await shot("gas-light");
    passed("one gas provider, 204/31/54/73/3 counts, real nine-asset drill and theme without refetch");
    await page.getByRole("button",{name:/资产分页目录/}).click();
    await page.waitForFunction(()=>document.querySelectorAll(".gas-asset-directory tbody tr").length===50);
    await page.getByRole("dialog").getByRole("button",{name:"末页",exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll(".gas-asset-directory tbody tr").length===4);
    await page.getByRole("dialog").getByRole("button",{name:"关闭窗口",exact:true}).click();
    await page.getByRole("button",{name:"重庆GIS",exact:true}).click();
    await page.getByTestId("gas-gis-unavailable").waitFor();
    assert.equal(await page.locator("[data-authorized-asset],[data-authorized-event]").count(),0);await shot("gas-gis");
    passed("50/final4 pagination and explicit unavailable authorized GIS");
    for(const [name,value] of [["风险分析","risk"],["应急推演","emergency"],["综合态势","overview"]]) {
     await page.getByRole("button",{name,exact:true}).click();
     await page.waitForFunction(value=>document.querySelector('[data-testid="gas-page"]')?.getAttribute("data-mode")===value,value);
    }
    const settings=page.locator(".gas-view-settings");
    for(const [name,value] of [["分析布局","analysis"],["紧凑布局","compact"],["指挥布局","command"]]) {
     if(await settings.getAttribute("open")===null)await settings.locator("summary").click();
     assert.equal(await page.locator("[data-window-control]").count(),11);
     await page.getByRole("button",{name,exact:true}).click();
     await page.waitForFunction(value=>document.querySelector('[data-testid="gas-page"]')?.getAttribute("data-layout")===value,value);
    }
    await page.keyboard.press("Escape");passed("three modes, three layouts, eleven window controls");
    if(edition==="project") {
     const ids=await page.locator("[data-window-control]").evaluateAll(nodes=>nodes.map(e=>e.getAttribute("data-window-control")));
     for(const id of ids) {
      if(await settings.getAttribute("open")===null)await settings.locator("summary").click();
      await page.locator('[data-window-control="'+id+'"]').click();
      await page.locator('.gas-window[data-window-id="'+id+'"]').waitFor();
      await gasOnlyUi();
      if(await page.getByRole("dialog").count())await page.getByRole("dialog").getByRole("button",{name:"关闭窗口",exact:true}).click();
     }
     passed("all eleven project windows and controls contain gas information only; flame identity");
    }
    await page.getByRole("button",{name:/智能助手/}).click();
    const panel=page.getByTestId("gas-agent-dialog");
    await panel.getByTestId("gas-agent-tools-status").filter({hasText:"ready"}).waitFor();
    await panel.getByLabel("燃气自然语言输入").fill("目前支持哪些CUV分析？");
    const query=page.waitForResponse(r=>r.url().endsWith("/agent/query")&&r.request().method()==="POST");
    await panel.getByRole("button",{name:"发送查询",exact:true}).click();
    const response=await query;assert.equal(response.status(),200);
    const body=await response.json();assert.equal(body.parser_source,"local_rule_not_llm");
    assert.equal(body.tools[0].tool_name,"capabilities.query");
    assert.deepEqual(new Set(body.tools[0].data.items.map(x=>x.status)),new Set(["ready_synthetic","unsupported","unavailable"]));
    await panel.getByTestId("gas-agent-result").waitFor();
    if(edition==="project")await gasOnlyUi();
    await panel.getByRole("button",{name:"关闭窗口",exact:true}).click();
    passed("real read-only Agent and CUV response, explicit rule mode");
    if(edition==="competition") {
     await page.getByRole("navigation",{name:"赛题3业务切换"}).getByRole("button",{name:"供水中控",exact:true}).click();
     await page.getByRole("heading",{name:"城市管网总览",exact:true}).waitFor();
     await page.reload();await page.getByRole("heading",{name:"城市管网总览",exact:true}).waitFor();
     passed("water/gas switch and refresh");
    } else {
     for(const path of ["/overview","/pipe/WP-016","/scenario/SC-HOSP","/assistant","/report/run-1","/about"]) {
      await page.goto(ready.url+"/?edition=competition&mode=live#"+path);await gasReady();
      assert.match(new URL(page.url()).hash,/^#\/gas/);
      assert.equal(await page.locator(".mode-banner,.main-nav").count(),0);
     }
     assert.ok(proof.network.filter(x=>x.edition===edition).every(x=>x.path.startsWith("/api/v1/gas/")));
     passed("water bookmarks and query spoof cannot mount water or fetch water API");
    }
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),"horizontal overflow");
    if(width===1920) {
     await page.goto(ready.url+"/#/gas");await gasReady();
     for(const [mode,status] of [["unauthenticated",401],["denied",403],["fixture_unavailable",503]]) {
      await fault(mode);
      await Promise.all([page.waitForResponse(r=>r.url().includes("/api/v1/gas/")&&r.status()===status),page.reload()]);
      await page.waitForTimeout(100);
      await page.getByRole("button",{name:"重试事件列表",exact:true}).waitFor();
      assert.equal(await page.locator(".gas-event-list > button").count(),0);
      if(status!==503) assert.equal(await page.locator(".gas-topology-node").count(),0);
      else assert.equal(await page.locator(".gas-topology-node").count(),54); // Case endpoint remains available; only engineering fixture reads are faulted.
      assert.equal(await page.getByRole("heading",{name:"城市管网总览",exact:true}).count(),0);
      await fault("normal");await page.reload();await gasReady();
      passed(mode+" is an error without demo fallback, then recovery");
     }
    }
    await context.close();
   }
  } finally {
   const exited=new Promise(r=>service.once("exit",r));service.stdin.end("stop\n");
   await Promise.race([exited,new Promise(r=>setTimeout(r,5000))]);if(service.exitCode===null)service.kill("SIGTERM");
   await writeFile(resolve(out,edition+"-http.json"),JSON.stringify(messages,null,2));
   await writeFile(resolve(out,edition+"-server.log"),stderr);active=null;
  }
 }
 assert.deepEqual(proof.errors,[]);proof.pass=true;
} catch(error){proof.pass=false;proof.failure=String(error);throw error;}
finally {
 if(active)active.kill("SIGTERM");await browser.close();
 await writeFile(resolve(out,"product-editions-browser.json"),JSON.stringify(proof,null,2));
 console.log(JSON.stringify({pass:proof.pass,groups:proof.checks.length,failure:proof.failure}));
}
