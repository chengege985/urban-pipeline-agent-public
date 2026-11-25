import assert from "node:assert/strict";
import {readFile,writeFile,mkdir} from "node:fs/promises";
import {resolve} from "node:path";
import {createHash} from "node:crypto";
import {chromium} from "playwright";
const runtime=process.env.GAS_DEV_RUNTIME ?? "/nix/wpn-gas-fastdev06-runtime";
const out=process.env.GAS_EVIDENCE_DIR ?? "/nix/wpn-gas-fastdev-06-evidence";
const cfg=JSON.parse(await readFile(resolve(runtime,"config.json"),"utf8"));
const access=JSON.parse(await readFile(resolve(runtime,"access.json"),"utf8"));
const url="http://127.0.0.1:"+cfg.web_port;
await mkdir(out,{recursive:true});
const proof={businessInterception:false,network:[],checks:[],screenshots:[],errors:[]};
const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE ??
"/nix/waterpipnet-runtime/playwright-browsers/chromium_headless_shell-1248/chrome-headless-shell-linux64/chrome-headless-shell"});
try {
for(const [width,height] of [[1920,1080],[1280,720]]) {
 const context=await browser.newContext({viewport:{width,height},httpCredentials:{username:access.username,password:access.password},reducedMotion:"reduce"});
 const page=await context.newPage();
 page.on("pageerror",e=>proof.errors.push(e.message));
 page.on("response",r=>{if(new URL(r.url()).pathname.startsWith("/api/"))proof.network.push({width,path:new URL(r.url()).pathname,status:r.status(),method:r.request().method()});});
 await page.goto(url+"/#/gas",{waitUntil:"networkidle"});
 await page.getByTestId("gas-record-total").filter({hasText:"204"}).waitFor();
 assert.equal(await page.getByTestId("gas-valve-count").innerText(),"31");
 assert.equal(await page.locator(".gas-topology-node").count(),54);
 assert.equal(await page.locator(".gas-topology-edge").count(),73);
 const open=()=>page.getByRole("button",{name:/独立展开面板/}).click();
 await open();
 const panel=page.getByTestId("gas-agent-dialog");
 await panel.getByTestId("gas-agent-execution-banner").filter({hasText:"qwen_intent"}).waitFor();
 async function query(q) {
   await panel.getByLabel("燃气自然语言输入").fill(q);
   const pending=page.waitForResponse(r=>r.url().endsWith("/agent/query")&&r.request().method()==="POST");
   await panel.getByRole("button",{name:"发送查询",exact:true}).click();
   const response=await pending;assert.equal(response.status(),200);
   const body=await response.json();
   await page.waitForFunction(id=>document.querySelector('[data-testid="gas-agent-result"]')?.getAttribute("data-request-id")===id,body.request_id);
   assert.equal(body.parser_source,"qwen_intent");
   assert.equal(body.model_id,"Qwen3.8-27B");assert.ok(body.evidence_refs.length);
   proof.checks.push({width,query:q,input:response.request().postDataJSON(),tool:body.tools[0]?.tool_name,
      parameters:body.tools[0]?.parameters,request_id:body.request_id,model_calls:body.model_calls,
      model_elapsed_ms:body.model_elapsed_ms,pass:true});
   return body;
 }
 let body=await query("查看K-001管段的信息");
 assert.equal(body.tools[0].data.asset.asset_id,"syn-gas-pipe-K-001");
 body=await query("该管段关联哪些事件");
 assert.equal(body.tools[0].parameters.asset_id,"syn-gas-pipe-K-001");
 assert.ok(body.tools[0].data.items.every(e=>e.asset_ids.includes("syn-gas-pipe-K-001")));
 const eventResponse=page.waitForResponse(r=>r.url().endsWith("/events/syn-gas-event-isolation")&&r.status()===200);
 await panel.getByRole("button",{name:"智能体事件详情 syn-gas-event-isolation",exact:true}).click();
 await eventResponse;await open();
 body=await query("查看N11阀门的详情");assert.equal(body.tools[0].data.asset.asset_id,"syn-gas-valve-N11");
 const assetResponse=page.waitForResponse(r=>r.url().endsWith("/assets/syn-gas-valve-N11")&&r.status()===200);
 await panel.getByRole("button",{name:"智能体资产详情 syn-gas-valve-N11",exact:true}).click();await assetResponse;
 await open();
 body=await query("定位隔离事件关联的管段");assert.equal(body.tools[0].data.asset_ids.length,9);
 await panel.getByTestId("gas-agent-locate-action").click();
 await page.waitForFunction(()=>document.querySelectorAll(".gas-topology-edge.is-selected").length===7&&document.querySelectorAll(".gas-topology-node.is-selected").length===2);
 await open();
 body=await query("查询区县信息和GIS可用状态");
 assert.equal(body.tools[0].data.districts.status,"unavailable");
 await panel.getByTestId("gas-agent-gis-state").filter({hasText:"不可用"}).waitFor();
 body=await query("风险评价为何不可用");
 assert.equal(body.tools[0].tool_name,"capabilities.query");
 const history=panel.getByTestId("gas-agent-history");
 await history.locator("summary").click();
 assert.equal(await history.locator("article").count(),6);
 await history.locator("summary").click();
 await panel.getByTestId("gas-agent-result").scrollIntoViewIfNeeded();
 const shot=resolve(out,"06-preview-"+width+"x"+height+".png");
 await page.screenshot({path:shot,fullPage:true});
 proof.screenshots.push({file:shot,sha256:createHash("sha256").update(await readFile(shot)).digest("hex")});
 await history.locator("summary").click();
 await history.getByRole("button",{name:"清空本次对话",exact:true}).click();
 assert.equal(await panel.getByTestId("gas-agent-result").count(),0);
 assert.equal(await panel.getByTestId("gas-agent-history").count(),0);
 await page.reload({waitUntil:"networkidle"});
 await open();assert.equal(await panel.getByTestId("gas-agent-history").count(),0);
 const storage=await page.evaluate(()=>JSON.stringify({session:{...sessionStorage},local:{...localStorage}}));
 assert.ok(!storage.includes(access.password)&&!storage.includes(access.platform_token));
 await context.close();
}
assert.deepEqual(proof.errors,[]);proof.status="PASS";
}catch(e){proof.status="FAIL";proof.failure=String(e);process.exitCode=1;}
finally{await browser.close();await writeFile(resolve(out,"06-preview-browser.json"),JSON.stringify(proof,null,2));console.log(JSON.stringify({status:proof.status,checks:proof.checks.length,screenshots:proof.screenshots,failure:proof.failure}));}
