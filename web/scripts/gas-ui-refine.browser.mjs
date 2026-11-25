import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { randomBytes, createHash } from "node:crypto";
import { mkdir, writeFile, readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";
const repo=resolve(".."), out=resolve(process.env.GAS_EVIDENCE_DIR ?? "../../wpn-gas-fastdev-04-05-evidence");
await mkdir(out,{recursive:true});
const label="ui-refine";

const password=randomBytes(24).toString("hex");
const python=process.env.GAS_TEST_PYTHON ?? "/tmp/gas-fix21-venv/bin/python";
const service=spawn(python,["-u",resolve("scripts/serve-gas-agent-acceptance.py"),"--repo",repo],{
  env:{...process.env,GAS_AGENT_MODEL_MODE:"disabled",GAS_ACCEPTANCE_PASSWORD:password},stdio:["pipe","pipe","pipe"],
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

const proof={businessInterception:false,checks:[],network:[],screenshots:[],geometry:[],pageerrors:[],console:[]};
let browser, page;
function passed(name, details={}){proof.checks.push({name,pass:true,...details});}
async function shot(name) {
  await page.evaluate(()=>document.fonts.ready);
  const path=resolve(out,name+".png"); await page.screenshot({path,fullPage:true});
  proof.screenshots.push({name,sha256:createHash("sha256").update(await readFile(path)).digest("hex")});
}
async function settings(){const el=page.locator(".gas-view-settings");if(await el.getAttribute("open")===null)await el.locator("summary").click();return el;}
async function openWindow(id){await settings();await page.locator(`[data-window-control="${id}"]`).click();}
async function closeDialog(){await page.getByRole("dialog").getByRole("button",{name:"关闭窗口",exact:true}).click();}
try {
  ready=await waitFor(v=>v.kind==="ready");
  browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE ??
    "/nix/waterpipnet-runtime/playwright-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell"});
  for(const [width,height] of [[1920,1080],[1280,720],[1024,700]]) {
    const context=await browser.newContext({viewport:{width,height},httpCredentials:{username:"tester",password},reducedMotion:"reduce"});
    page=await context.newPage();page.setDefaultTimeout(12000);
    page.on("pageerror",e=>proof.pageerrors.push(e.message));
    page.on("console",m=>{if(m.type()==="error")proof.console.push({width,text:m.text()});});
    page.on("response",r=>{const u=new URL(r.url());if(u.pathname.startsWith("/api/"))proof.network.push({width,path:u.pathname+u.search,method:r.request().method(),status:r.status()});});
    await page.goto(ready.url+"/#/gas",{waitUntil:"networkidle"});
    await page.getByTestId("gas-record-total").filter({hasText:"204"}).waitFor();
    await page.getByTestId("gas-agent-live-status").filter({hasText:"只读工具可用"}).waitFor();
    assert.equal(await page.getByTestId("gas-page").getAttribute("data-theme"),"dark");
    assert.equal(await page.locator(".gas-topology-node").count(),54);
    assert.equal(await page.locator(".gas-topology-edge").count(),73);
    assert.equal(await page.getByTestId("gas-valve-count").innerText(),"31");
    assert.equal(await page.getByTestId("gas-event-count").innerText(),"3");
    assert.equal(await page.locator(".gas-event-list > button").count(),3);
    assert.equal(await page.locator(".gas-window[data-window-id]").count(),5);
    assert.match(await page.getByTestId("gas-case-status").innerText(),/synthetic.*partial.*not_business_validated/);
    assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
    assert.equal(await page.locator('svg pattern[id*="grid"], svg filter[id*="glow"]').count(),0);
    const geometry=await page.evaluate(()=>{
      const m=document.querySelector('[data-testid="gas-central-view"]').getBoundingClientRect();
      const w=document.querySelector('[data-testid="gas-workspace"]').getBoundingClientRect();
      return {width:innerWidth,height:innerHeight,mapWidth:m.width,mapHeight:m.height,widthRatio:m.width/innerWidth,heightRatio:m.height/w.height};
    });
    proof.geometry.push(geometry);if(width===1920){assert(geometry.widthRatio>=.55);assert(geometry.heightRatio>=.55);}
    await shot("dark-topology-"+width+"x"+height);
    const requestCount=proof.network.length;
    await page.getByTestId("gas-theme-toggle").click();
    assert.equal(await page.getByTestId("gas-page").getAttribute("data-theme"),"light");
    await page.waitForTimeout(350);
    assert.equal(proof.network.length,requestCount,"theme must not refetch business or Agent data");
    await shot("light-topology-"+width+"x"+height);
    await page.reload({waitUntil:"networkidle"});
    assert.equal(await page.getByTestId("gas-page").getAttribute("data-theme"),"light");
    await page.getByTestId("gas-record-total").filter({hasText:"204"}).waitFor();
    passed("theme persistence; actual counts; no API reload or duplicate cockpit",{width});
    await page.getByRole("button",{name:"重庆GIS",exact:true}).click();
    await page.getByTestId("gas-gis-view").waitFor();
    assert.equal(await page.locator("[data-authorized-asset],[data-authorized-event]").count(),0);
    if(width<=1280)assert.equal(await page.getByTestId("gas-district-panel").count(),0);
    await shot("light-gis-"+width+"x"+height);
    await page.getByTestId("gas-theme-toggle").click();
    await shot("dark-gis-"+width+"x"+height);
    await page.getByLabel("选择重庆行政区县").selectOption("500103");
    await page.getByTestId("gas-district-panel").filter({hasText:"渝中"}).waitFor();
    assert.match(await page.getByTestId("gas-gis-unavailable").innerText(),/unavailable/);
    await page.getByRole("button",{name:"合成拓扑",exact:true}).click();
    passed("GIS reference, explicit unavailable coordinates, district expand",{width});
    await page.locator(".gas-event-list > button").filter({hasText:"syn-gas-event-isolation"}).click();
    await page.locator('[data-testid="gas-event-detail"][data-event-id="syn-gas-event-isolation"]').waitFor();
    const ids=await page.locator('[data-testid="gas-event-detail"] .gas-related-list button').evaluateAll(els=>els.map(e=>e.getAttribute("aria-label").replace("打开资产详情 ","")));
    assert.equal(ids.length,9);
    const assetId=ids.find(id=>id.includes("-pipe-"));
    assert(assetId);
    await Promise.all([page.waitForResponse(r=>r.url().endsWith("/assets/"+assetId)&&r.status()===200),page.getByRole("button",{name:"打开资产详情 "+assetId,exact:true}).click()]);
    await page.locator(`[data-testid="gas-asset-detail"][data-asset-id="${assetId}"]`).waitFor();
    const before=await page.evaluate(()=>({hash:location.hash,identity:document.querySelector('[data-testid="gas-page"]').getAttribute("data-case-identity")}));
    const calls=proof.network.length;await page.getByTestId("gas-theme-toggle").click();await page.waitForTimeout(200);
    assert.equal(proof.network.length,calls);
    assert.deepEqual(await page.evaluate(()=>({hash:location.hash,identity:document.querySelector('[data-testid="gas-page"]').getAttribute("data-case-identity")})),before);
    assert.equal(await page.locator(`[data-testid="gas-asset-detail"][data-asset-id="${assetId}"]`).count(),1);
    await shot("light-event-drill-"+width+"x"+height);
    await page.getByTestId("gas-theme-toggle").click();
    passed("nine actual event assets, HTTP detail, selection survives theme",{width,assetId});
    for(const theme of ["light","dark"]) {
      if(await page.getByTestId("gas-page").getAttribute("data-theme")!==theme)await page.getByTestId("gas-theme-toggle").click();
      for(const modeName of ["风险分析","应急推演","综合态势"]) {
        await page.getByRole("button",{name:modeName,exact:true}).click();
        await page.waitForFunction(mode=>document.querySelector('[data-testid="gas-page"]').getAttribute("data-mode")===mode,({"风险分析":"risk","应急推演":"emergency","综合态势":"overview"})[modeName]);
        assert.equal(await page.locator(".gas-window[data-window-id]").count(),5);
        if(width===1920&&modeName!=="综合态势")await shot(theme+"-"+({"风险分析":"risk","应急推演":"emergency"})[modeName]+"-1920x1080");
      }
      const grids=[];
      for(const title of ["分析布局","紧凑布局","指挥布局"]) {
        await settings(); await page.getByRole("button",{name:title,exact:true}).click();
        await page.waitForFunction(layout=>document.querySelector('[data-testid="gas-page"]').getAttribute("data-layout")===layout,({"分析布局":"analysis","紧凑布局":"compact","指挥布局":"command"})[title]);
        grids.push(await page.getByTestId("gas-workspace").evaluate(e=>getComputedStyle(e).gridTemplateColumns));
      }
      assert.equal(new Set(grids).size,3);
      await settings();assert.equal(await page.locator("[data-window-control]").count(),11);
      await page.keyboard.press("Escape");
      assert.equal(await page.locator(".gas-view-settings").getAttribute("open"),null);
      passed("three modes, three geometric layouts and keyboard-accessible eleven controls",{width,theme,grids});
    }
    await page.getByRole("button",{name:/资产分页目录/}).click();
    const directory=page.getByRole("dialog");
    await page.waitForFunction(()=>document.querySelectorAll(".gas-asset-directory tbody tr").length===50);
    await directory.getByRole("button",{name:"末页",exact:true}).click();
    await page.waitForFunction(()=>document.querySelectorAll(".gas-asset-directory tbody tr").length===4);
    assert.match(await page.getByTestId("gas-asset-page-progress").innerText(),/204/);
    assert.equal(await page.getByTestId("gas-valve-count").innerText(),"31");
    await closeDialog();
    passed("50 / final 4 asset paging without changing KPI scope",{width});
    await openWindow("impact");
    const cuv=page.getByRole("dialog");await cuv.getByTestId("gas-cuv-summary").waitFor();
    assert.match(await cuv.innerText(),/ready_synthetic/);
    await closeDialog();
    await page.getByRole("button",{name:/智能助手/}).click();
    await page.getByTestId("gas-agent-dialog").waitFor();
    assert.match(await page.getByTestId("gas-agent-execution-banner").innerText(),/unavailable/);
    assert.match(await page.getByTestId("gas-agent-tools-status").innerText(),/ready/);
    const drawerBox=await page.getByTestId("gas-agent-dialog").boundingBox();assert(drawerBox.x>=0&&drawerBox.x+drawerBox.width<=width+1);
    await shot("dark-assistant-rule-"+width+"x"+height);
    await closeDialog();
    passed("actual CUV capability and rule-mode assistant drawer reachable",{width});
    if(width===1920) {
      const titles={topology:"中央管网拓扑",scenario:"事件与情景",timeline:"处置时间线",impact:"故障与隔离影响",selected:"事件与资产详情",services:"服务对象清单",layers:"图层管理",data:"数据接入状态",evidence:"证据与版本",boundary:"评价边界",tasks:"任务与智能体入口"};
      for(const [id,title] of Object.entries(titles)) {
        await openWindow(id);
        const win=page.locator(`.gas-window[data-window-id="${id}"]`);await win.waitFor();
        await win.getByRole("button",{name:"隐藏"+title,exact:true}).click();
        assert.equal(await page.locator(`.gas-window[data-window-id="${id}"]`).count(),0);
        await openWindow(id);await page.locator(`.gas-window[data-window-id="${id}"]`).waitFor();
        if(await page.getByRole("dialog").count())await closeDialog();
      }
      passed("all eleven windows open, hide and restore");
      await settings();await page.getByRole("button",{name:"恢复全部窗口",exact:true}).click();
      const head=page.locator('.gas-window[data-window-id="scenario"] .gas-window-head');
      const box=await head.boundingBox();await page.mouse.move(box.x+40,box.y+15);await page.mouse.down();await page.mouse.move(box.x+65,box.y+30,{steps:5});await page.mouse.up();
      assert.match(await page.locator('.gas-window[data-window-id="scenario"]').getAttribute("style"),/translate\(25px, 15px\)/);
      await settings();await page.getByRole("button",{name:"恢复全部窗口",exact:true}).click();
      const oldZoom=await page.getByTestId("gas-viewport-transform").getAttribute("data-zoom");
      await page.getByRole("button",{name:"放大合成拓扑",exact:true}).click();
      assert.notEqual(await page.getByTestId("gas-viewport-transform").getAttribute("data-zoom"),oldZoom);
      await page.getByRole("button",{name:"适配视图",exact:true}).click();
      const viewportBefore=await page.getByTestId("gas-viewport-transform").getAttribute("transform");
      const canvas=await page.getByTestId("gas-central-view").boundingBox();
      await page.mouse.move(canvas.x+20,canvas.y+25);await page.mouse.down();
      await page.mouse.move(canvas.x+45,canvas.y+40,{steps:5});await page.mouse.up();
      assert.notEqual(await page.getByTestId("gas-viewport-transform").getAttribute("transform"),viewportBefore);
      await page.getByRole("button",{name:"适配视图",exact:true}).click();
      for(const selector of [".gas-node-valve",".gas-node-source"]) {
        const node=page.locator(".gas-topology-node"+selector).first();
        const [response]=await Promise.all([page.waitForResponse(r=>r.url().includes("/engineering/assets/")&&r.status()===200),node.locator("rect,circle").first().click()]);
        const value=await response.json();
        await page.locator(`[data-testid="gas-asset-detail"][data-asset-id="${value.asset_id}"]`).waitFor();
        await page.getByRole("button",{name:"适配视图",exact:true}).click();
      }
      passed("real mouse window drag, topology pan/zoom/fit, valve and source HTTP selection");
      for(const i of [1,2,0]) {
        await page.locator(".gas-timeline-event").nth(i).click();
        assert.equal(await page.getByTestId("gas-timeline").getAttribute("data-index"),String(i));
      }
      assert(await page.getByRole("button",{name:"播放",exact:true}).isDisabled());
      passed("timeline state selection and reduced-motion suppression");
      for(const [fault,status] of [["denied",403],["unauthenticated",401],["fixture_unavailable",503]]) {
        await mode(fault);
        await page.getByRole("button",{name:/智能助手/}).click();
        const panel=page.getByTestId("gas-agent-dialog");
        if(fault==="fixture_unavailable") {
          await panel.getByLabel("燃气自然语言输入").fill("查看当前有哪些燃气事件");
          await panel.getByRole("button",{name:"发送查询",exact:true}).click();
          await panel.getByTestId("gas-agent-error").waitFor();
          assert(proof.network.some(r=>r.path.endsWith("/agent/query")&&r.status===503));
        } else {
          await panel.getByRole("button",{name:"检测燃气服务",exact:true}).click();
          await panel.getByRole("alert").waitFor();
          assert(proof.network.some(r=>r.path.endsWith("/agent/health")&&r.status===status));
        }
        assert.equal(await page.locator(".gas-assistant-entry").getAttribute("data-agent-tone"),"error");
        await mode("normal");
        if(fault==="fixture_unavailable") {
          await panel.getByRole("button",{name:"重试查询",exact:true}).click();
          await panel.getByTestId("gas-agent-result").waitFor();
        } else {
          await panel.getByRole("button",{name:"检测燃气服务",exact:true}).click();
          await panel.getByTestId("gas-agent-tools-status").filter({hasText:"ready"}).waitFor();
        }
        await closeDialog();
      }
      passed("actual TCP 401/403 health rejection and 503 fixture query, recovery");
      await page.getByRole("button",{name:/智能助手/}).click();
      await context.setOffline(true);await page.getByRole("button",{name:"检测燃气服务",exact:true}).click();
      await page.getByRole("dialog").getByRole("alert").waitFor();await shot("dark-assistant-offline-1920x1080");
      await context.setOffline(false);await page.getByRole("button",{name:"检测燃气服务",exact:true}).click();
      await page.getByTestId("gas-agent-tools-status").filter({hasText:"ready"}).waitFor();await closeDialog();
      passed("actual browser offline, no fake Qwen response, recovery");
    }
    await context.close();
  }
  assert.deepEqual(proof.pageerrors,[]);
  proof.unexpectedConsole=proof.console.filter(item=>!(/Failed to load resource:.*(status of (401|403|503)|ERR_INTERNET_DISCONNECTED)/.test(item.text)));
  assert.deepEqual(proof.unexpectedConsole,[]);
  assert(proof.network.every(r=>r.path.startsWith("/api/v1/gas/")&&(r.method==="GET"||(r.method==="POST"&&r.path.endsWith("/agent/query")))),"visual tests must not write or use unrelated business endpoints");
  proof.status="PASS";
} catch(error) {
  proof.status="FAIL";proof.failure=String(error.stack ?? error);process.exitCode=1;
  if(page&&!page.isClosed())await shot("failure").catch(()=>{});
} finally {
  if(browser)await browser.close();
  service.stdin.write("stop\n");service.stdin.end();
  await Promise.race([new Promise(resolve=>service.once("exit",resolve)),new Promise(resolve=>setTimeout(()=>{service.kill("SIGTERM");resolve();},12000))]);
  proof.serviceStopped=messages.some(v=>v.kind==="stopped"&&v.api_stopped);
  await writeFile(resolve(out,label+"-browser.json"),JSON.stringify(proof,null,2));
  await writeFile(resolve(out,label+"-http.jsonl"),messages.map(x=>JSON.stringify(x)).join("\n")+"\n");
  await writeFile(resolve(out,label+"-service.log"),stderr);
  console.log(JSON.stringify({status:proof.status,checks:proof.checks.length,failure:proof.failure,geometry:proof.geometry,serviceStopped:proof.serviceStopped},null,2));
}
