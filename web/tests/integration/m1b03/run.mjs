// Real Chromium + complete Vite product build + real isolated FastAPI.
// Usage: node tests/integration/m1b03/run.mjs <fresh-work-directory> [dist] [backend-source]
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {once} from 'node:events';
import {createInterface} from 'node:readline';
import {randomBytes} from 'node:crypto';
import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import path from 'node:path';
const here = path.dirname(fileURLToPath(import.meta.url));
const web = path.resolve(here, '../../..');
const require = createRequire(path.join(web, 'package.json'));
const {chromium} = require('playwright');
const work = path.resolve(process.argv[2] ?? '/tmp/m1b03-browser-' + Date.now());
const dist = path.resolve(process.argv[3] ?? path.join(web, 'dist'));
const backend = process.argv[4] ?? '/workdata/WaterPipNet/01_工程项目';
const out = path.join(work, 'results');
await mkdir(out, {recursive:true});
const sessions = {alice:randomBytes(32).toString('hex'), bob:randomBytes(32).toString('hex')};
const checks=[], responses=[], attempts=[], pending=[], pageErrors=[], consoleErrors=[], external=[], logs=[];
const check = (name, pass, actual) => checks.push({name, pass:!!pass, actual});
const gateway = spawn('/workdata/WaterPipNet/.venv-agent/bin/python', ['-B',path.join(here,'server.py'),work,dist,backend], {stdio:['pipe','pipe','pipe'],cwd:work});
gateway.stderr.on('data', b=>logs.push(b.toString()));
gateway.stdin.write(JSON.stringify({sessions})+'\n');
let browser, fatal, ready, base;
try {
  const lines=createInterface({input:gateway.stdout});
  ready=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('gateway start timeout')),45000);
    gateway.once('exit',code=>{clearTimeout(timer);reject(new Error('gateway exited '+code));});
    lines.on('line',line=>{try {const x=JSON.parse(line);if(x.ready){clearTimeout(timer);resolve(x);}}catch{logs.push(line);}});
  });
  base=`http://127.0.0.1:${ready.port}`;
  let listening=false;for(let i=0;i<100;i++){try{const r=await fetch(base+'/api/v1/capabilities');if([200,401].includes(r.status)){listening=true;break;}}catch{}await new Promise(resolve=>setTimeout(resolve,100));}
  if(!listening)throw new Error('gateway socket did not become ready');
  browser=await chromium.launch({headless:true,executablePath:'/root/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome',args:['--disable-background-networking']});
  async function context(actor){
    const ctx=await browser.newContext({viewport:{width:1440,height:1100}});
    if(actor)await ctx.addCookies([{name:'m1b_session',value:sessions[actor],url:base,httpOnly:true,sameSite:'Strict'}]);
    await ctx.route('**/*',route=>{if(route.request().url().startsWith(base+'/'))return route.continue();external.push(route.request().url());return route.abort();});
    ctx.on('request',req=>attempts.push({method:req.method(),path:new URL(req.url()).pathname,actor}));
    ctx.on('response',res=>{
      const url=new URL(res.url());if(!url.pathname.startsWith('/api/'))return;
      pending.push((async()=>{let body;try{body=await res.json();}catch{body=null;}
        responses.push({actor,method:res.request().method(),path:url.pathname,status:res.status(),body,
          tokenHeaderPresent:!!res.request().headers()['x-water-token'],requestBody:res.request().postData()});})());
    });
    ctx.on('page',page=>{page.on('pageerror',err=>pageErrors.push(String(err)));page.on('console',msg=>{if(msg.type()==='error')consoleErrors.push(msg.text());});});
    return ctx;
  }
  const ctx=await context('alice');const page=await ctx.newPage();
  const urlFor=(fixture)=>`${base}/?mode=platform&datasetId=${fixture.snapshot_id}#/report/${fixture.run_id}`;
  const reportOnlyStart=attempts.length;
  const texts={};
  for(const name of ['complete','unknown_resource','partial_population','selected_time_missing','unknown_cost']){
    const f=ready.fixtures[name];
    await page.goto(urlFor(f));
    await page.getByTestId('report-status').waitFor({timeout:30000});
    await page.getByTestId('report-plan-syn-plan-backup').waitFor({timeout:30000});
    const body=await page.getByTestId('platform-report-page').innerText();texts[name]=body;
    check(name+'_identity',body.includes(f.run_id)&&body.includes(f.snapshot_id),body.slice(0,450));
    check(name+'_backend_status',body.includes(f.report.business_status),f.report.business_status);
    check(name+'_no_demo_identity',!body.includes('WP-')&&!body.includes('SC-HOSP'),'no local demo IDs');
    const readField=async (root,label)=>await root.locator('dt').filter({hasText:new RegExp('^'+label.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'$')}).locator('..').locator('dd').innerText();
    for(const plan of f.report.decision.plans){
      const card=page.getByTestId('report-plan-'+plan.plan_id);
      const metrics=plan.metrics??{};
      const expectations=[['方案效用',plan.utility],['费用（分）',Object.hasOwn(metrics,'total_cost_fen')?metrics.total_cost_fen:plan.total_cost_fen],['剩余断连服务（条服务记录）',metrics.remaining_disconnected_services],['最后网络动作生效时间（小时）',metrics.restoration_hours],['备用覆盖关键服务（条服务记录）',metrics.backup_covered_critical_services]];
      for(const [label,value] of expectations){
        const actual=await readField(card,label);const expected=value==null?'未知（未提供）':String(value);
        check(name+'_'+plan.plan_id+'_'+label,actual===expected,{expected,actual});
      }
    }
    if(name==='complete'){
      const backup=await page.getByTestId('report-plan-syn-plan-backup').innerText();
      check('legitimate_null_is_unknown',backup.includes('未知（未提供）'),backup);
      check('restoration_hours_has_honest_label',backup.includes('最后网络动作生效时间（小时）'),backup);
      check('model_semantics_preserved',body.includes('0.1.1')&&body.includes('legacy_demo'),body.includes('legacy_demo'));
    }
    if(name==='unknown_resource')check('unknown_is_not_failure',body.includes('未知（unknown）'),body);
    if(name==='partial_population'){
      const impact=page.getByTestId('report-plan-syn-plan-backup').locator('section').filter({has:page.getByRole('heading',{name:'方案拓扑影响',exact:true})});
      const impactText=await impact.innerText();
      check('partial_is_not_complete_or_error',impactText.includes('部分结果（partial）'),impactText);
      check('partial_households_unknown',await readField(impact,'居民户数（户）')==='未知（未提供）',await readField(impact,'居民户数（户）'));
    }
    if(name==='selected_time_missing')check('selected_missing_no_invented_utility',body.includes('未知（未提供）'),body);
    if(name==='unknown_cost')check('null_cost_remains_unknown',body.includes('未知（未提供）'),body);
    await page.screenshot({path:path.join(out,`${name}.png`),fullPage:true});
  }
  await writeFile(path.join(out,'report_texts.json'),JSON.stringify(texts,null,2));
  await page.goto(urlFor(ready.fixtures.partial_simulation));await page.getByTestId('report-status').waitFor();
  const partialSimulationText=await page.getByTestId('platform-report-page').innerText();
  check('partial_simulation_top_level_status',(await page.getByTestId('report-status').innerText()).includes('部分结果（partial）'),await page.getByTestId('report-status').innerText());
  const partialTopology=page.locator('section').filter({has:page.getByRole('heading',{name:'拓扑影响',exact:true})});
  const household=await partialTopology.locator('dt').filter({hasText:'居民户数（户）'}).locator('..').locator('dd').innerText();
  check('partial_simulation_unknown_households',household==='未知（未提供）',household);
  check('partial_simulation_no_invented_decision',await page.getByTestId('report-plan-syn-plan-backup').count()===0&&!partialSimulationText.includes('后端推荐方案'),'no candidate ranking');
  await page.screenshot({path:path.join(out,'partial-simulation-report.png'),fullPage:true});
  await page.goto(urlFor(ready.fixtures.complete));await page.getByTestId('report-status').waitFor();
  const reportPath=`/api/v1/runs/${ready.fixtures.complete.run_id}/report`;
  const downloadWait=page.waitForEvent('download');
  await page.getByRole('button',{name:'下载 JSON',exact:true}).click();
  const download=await downloadWait;const downloaded=path.join(out,'downloaded-report.json');await download.saveAs(downloaded);
  check('download_preserves_backend_document',JSON.stringify(JSON.parse(await readFile(downloaded,'utf8')))===JSON.stringify(ready.fixtures.complete.report),'exact JSON document');
  const beforeRefresh=attempts.filter(x=>x.path===reportPath).length;
  await Promise.all([page.waitForResponse(res=>new URL(res.url()).pathname===reportPath&&res.status()===200),page.getByTestId('report-refresh').click()]);
  await page.getByTestId('report-status').waitFor();
  check('refresh_reloads_backend',attempts.filter(x=>x.path===reportPath).length>beforeRefresh,attempts.filter(x=>x.path===reportPath).length-beforeRefresh);
  const beforeReload=attempts.filter(x=>x.path===reportPath).length;
  await page.reload();await page.getByTestId('report-status').waitFor();
  check('browser_reload_reloads_backend',attempts.filter(x=>x.path===reportPath).length>beforeReload,'GET report after reload');
  // Same SPA route switch: stale success must disappear as soon as the new request starts.
  const pendingPath=`/api/v1/runs/${ready.fixtures.pending.run_id}/report`;
  await page.route('**'+pendingPath,async route=>{await new Promise(resolve=>setTimeout(resolve,500));await route.continue();});
  await page.evaluate(id=>{location.hash='#/report/'+id;},ready.fixtures.pending.run_id);
  await page.waitForTimeout(100);
  const transition=await page.getByTestId('platform-report-page').innerText();
  check('route_switch_hides_stale_report',!transition.includes(ready.fixtures.complete.run_id),transition);
  await page.getByTestId('report-error').waitFor();
  let error=await page.getByTestId('report-error').innerText();
  check('pending_409_visible',error.includes('409')&&error.includes('报告暂不可用'),error);
  await page.screenshot({path:path.join(out,'pending-409.png'),fullPage:true});
  await page.unroute('**'+pendingPath);
  let releaseLate, markLate;
  const lateGate=new Promise(resolve=>{releaseLate=resolve;});const lateObserved=new Promise(resolve=>{markLate=resolve;});
  await page.route('**'+reportPath,async route=>{markLate();await lateGate;await route.continue();});
  await page.evaluate(id=>{location.hash='#/report/'+id;},ready.fixtures.complete.run_id);
  await lateObserved;
  await page.evaluate(id=>{location.hash='#/report/'+id;},ready.fixtures.pending.run_id);
  await page.getByTestId('report-error').waitFor();
  const lateResponse=page.waitForResponse(res=>new URL(res.url()).pathname===reportPath&&res.status()===200);
  releaseLate();await lateResponse;await page.waitForTimeout(150);
  const raceText=await page.getByTestId('platform-report-page').innerText();
  check('late_old_200_cannot_replace_new_409',raceText.includes('409')&&raceText.includes(ready.fixtures.pending.run_id)&&!raceText.includes(ready.fixtures.complete.run_id),raceText);
  await page.unroute('**'+reportPath);
  for(const [name,status] of [['identity_conflict',409],['missing',404]]){
    const f=ready.fixtures[name]??{...ready.fixtures.complete,run_id:'m1b03-not-found'};
    await page.goto(urlFor(f));await page.getByTestId('report-error').waitFor();
    error=await page.getByTestId('report-error').innerText();
    check(name+'_'+status+'_visible',error.includes(String(status)),error);
    check(name+'_no_demo_fallback',(await page.locator('body').innerText()).includes('合成演示模式')===false,'no demo mode');
    await page.screenshot({path:path.join(out,`${name}-${status}.png`),fullPage:true});
  }
  check('report_only_navigation_is_read_only',attempts.slice(reportOnlyStart).filter(x=>x.method!=='GET').length===0,attempts.slice(reportOnlyStart).filter(x=>x.method!=='GET'));
  const reportOnlyApi=attempts.slice(reportOnlyStart).filter(x=>x.path.startsWith('/api/'));
  check('direct_report_never_bootstraps_dataset_or_capabilities',reportOnlyApi.every(x=>x.method==='GET'&&/^\/api\/v1\/runs\/[^/]+\/report$/.test(x.path)),reportOnlyApi);
  for(const [actor,status] of [['bob',403]]){
    const denied=await context(actor);const deniedPage=await denied.newPage();
    await deniedPage.goto(urlFor(ready.fixtures.complete));
    await deniedPage.getByTestId('report-error').waitFor({timeout:30000});
    const text=await deniedPage.getByTestId('report-error').innerText();
    check((actor??'anonymous')+'_'+status+'_visible',text.includes(String(status)),text);
    check((actor??'anonymous')+'_no_report_content',await deniedPage.getByTestId('report-plan-syn-plan-backup').count()===0,'no plan data');
    await deniedPage.screenshot({path:path.join(out,`${actor??'anonymous'}-${status}.png`),fullPage:true});
    await denied.close();
  }
  const anonymous=await context(null);const anonymousPage=await anonymous.newPage();
  const anonymousStart=responses.length;
  await anonymousPage.goto(urlFor(ready.fixtures.complete));
  await anonymousPage.getByTestId('report-error').waitFor({timeout:30000});
  await Promise.all(pending);
  const anonymousText=await anonymousPage.locator('body').innerText();
  check('anonymous_cold_report_401_visible',responses.slice(anonymousStart).some(x=>x.path===reportPath&&x.status===401)&&anonymousText.includes('401'),anonymousText);
  check('anonymous_cold_start_no_automatic_demo',await anonymousPage.locator('.mode-banner.mode-demo').count()===0&&await anonymousPage.getByTestId('report-plan-syn-plan-backup').count()===0,anonymousText);
  const anonymousReport=await anonymousPage.evaluate(async rid=>{const r=await fetch('/api/v1/runs/'+rid+'/report');return {status:r.status,body:await r.json()};},ready.fixtures.complete.run_id);
  check('anonymous_direct_report_401',anonymousReport.status===401,anonymousReport);
  await anonymousPage.screenshot({path:path.join(out,'anonymous-cold-start-401.png'),fullPage:true});await anonymous.close();
  const expiring=await context('alice');const expiringPage=await expiring.newPage();
  await expiringPage.goto(urlFor(ready.fixtures.complete));await expiringPage.getByTestId('report-status').waitFor();
  await expiring.clearCookies();await expiringPage.getByTestId('report-refresh').click();await expiringPage.getByTestId('report-error').waitFor();
  const expiredText=await expiringPage.getByTestId('report-error').innerText();
  check('expired_session_report_401_visible',expiredText.includes('401'),expiredText);
  check('expired_session_clears_report',await expiringPage.getByTestId('report-plan-syn-plan-backup').count()===0,'no cached plan');
  await expiringPage.screenshot({path:path.join(out,'expired-session-401.png'),fullPage:true});await expiring.close();
  // Actual formal Scenario compare -> actual persisted Run -> report page.
  const f=ready.fixtures.complete;
  await page.goto(`${base}/?mode=platform&datasetId=${f.snapshot_id}#/scenario/scn-syn-isolate?pipe=syn-pipe-fault`);
  await page.getByText('交互式情景分析',{exact:false}).first().waitFor({timeout:60000});
  await page.getByTestId('open-platform-report').waitFor({timeout:60000});
  await page.waitForFunction(()=>{const b=document.querySelector('[data-testid="open-platform-report"]');return b&&!b.disabled;},{timeout:60000});
  await page.screenshot({path:path.join(out,'scenario-report-entry.png'),fullPage:true});
  await Promise.all(pending);
  const decision=responses.filter(x=>x.path==='/api/v1/decision-runs'&&x.status===202).at(-1);
  check('scenario_has_real_async_run',!!decision?.body?.run_id&&decision?.body?.status==='queued',decision?.body?.run_id);
  await page.waitForLoadState('networkidle');
  const beforeReport=attempts.length;
  await page.getByTestId('open-platform-report').click();
  await page.getByTestId('report-status').waitFor({timeout:30000});
  check('scenario_links_its_run',page.url().endsWith('#/report/'+decision?.body?.run_id),page.url());
  check('scenario_report_does_not_compute',attempts.slice(beforeReport).every(x=>x.method==='GET'),attempts.slice(beforeReport));
  await page.screenshot({path:path.join(out,'scenario-real-report.png'),fullPage:true});
  await page.goto(urlFor(ready.fixtures.complete));await page.getByTestId('report-status').waitFor();
  const withoutDatasetStart=attempts.length;
  await page.goto(`${base}/?mode=platform#/report/${ready.fixtures.complete.run_id}`);await page.getByTestId('report-status').waitFor();
  const withoutDatasetApi=attempts.slice(withoutDatasetStart).filter(x=>x.path.startsWith('/api/'));
  check('run_id_only_direct_link',withoutDatasetApi.length>0&&withoutDatasetApi.every(x=>x.path===reportPath&&x.method==='GET'),withoutDatasetApi);
  await page.screenshot({path:path.join(out,'desktop-viewport.png'),fullPage:false});
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:path.join(out,'report-mobile-390.png'),fullPage:true});
  await page.screenshot({path:path.join(out,'mobile-viewport.png'),fullPage:false});
  const mobileOverflow=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
  check('mobile_no_horizontal_overflow',mobileOverflow.scroll<=mobileOverflow.viewport+1&&mobileOverflow.body<=mobileOverflow.viewport+1,mobileOverflow);
  await page.setViewportSize({width:1440,height:1100});
  await Promise.all(pending);
  const audit=await page.evaluate(async()=>await(await fetch('/m1b/audit')).json());
  check('real_report_responses_present',audit.report_checks.length>=15,audit.report_checks.length);
  check('report_compute_calls_zero',audit.report_checks.every(x=>x.compute_calls.length===0),audit.report_checks.map(x=>x.compute_calls));
  check('report_sql_writes_zero',audit.report_checks.every(x=>x.sql_writes.length===0),audit.report_checks.map(x=>x.sql_writes));
  check('report_file_writes_zero',audit.report_checks.every(x=>x.file_writes.length===0),audit.report_checks.map(x=>x.file_writes));
  check('report_foreign_snapshot_reads_zero',audit.report_checks.every(x=>x.foreign_snapshot_reads.length===0),audit.report_checks.map(x=>x.foreign_snapshot_reads));
  check('report_persisted_data_unchanged',audit.report_checks.every(x=>x.persisted_unchanged),audit.report_checks.map(x=>({path:x.path,status:x.status,unchanged:x.persisted_unchanged})));
  check('backend_outbound_zero',audit.outbound_attempts.length===0,audit.outbound_attempts);
  check('browser_outbound_zero',external.length===0,external);
  check('browser_tokens_absent',responses.every(x=>!x.tokenHeaderPresent),responses.length);
  check('test_cookie_httponly',!(await page.evaluate(()=>document.cookie)).includes('m1b_session'),'HttpOnly');
  check('no_runtime_page_error',pageErrors.length===0,pageErrors);
  check('no_http_500',responses.every(x=>x.status<500),responses.filter(x=>x.status>=500));
  await writeFile(path.join(out,'audit_live.json'),JSON.stringify(audit,null,2));
  await writeFile(path.join(out,'environment.json'),JSON.stringify({node:process.version,chromium:browser.version(),playwright:require('playwright/package.json').version,web,dist,backend,work,dataMode:'synthetic',bind:'127.0.0.1',realModel:false},null,2));
} catch(error){
  fatal=String(error.stack??error);check('harness_completed',false,fatal);
  if(browser){let i=0;for(const ctx of browser.contexts())for(const page of ctx.pages()){
    try{await page.screenshot({path:path.join(out,'failure-page-'+i+'.png'),fullPage:true});await writeFile(path.join(out,'failure-page-'+i+'.txt'),await page.locator('body').innerText());}catch{}i++;
  }}
}
finally{
  if(browser)await browser.close();
  if(gateway.exitCode===null){gateway.stdin.end('STOP\n');await Promise.race([once(gateway,'exit'),new Promise(resolve=>setTimeout(resolve,10000))]);if(gateway.exitCode===null)gateway.kill('SIGTERM');}
  await Promise.allSettled(pending);
  const result={passed:checks.filter(x=>x.pass).length,failed:checks.filter(x=>!x.pass).length,checks,fatal,pageErrors,consoleErrors,external};
  await writeFile(path.join(out,'checks.json'),JSON.stringify(result,null,2));
  await writeFile(path.join(out,'responses.json'),JSON.stringify(responses,null,2));
  await writeFile(path.join(out,'requests.json'),JSON.stringify(attempts,null,2));
  await writeFile(path.join(out,'gateway.log'),logs.join(''));
  console.log(JSON.stringify({work,passed:result.passed,failed:result.failed,fatal,failedChecks:checks.filter(x=>!x.pass).map(x=>x.name)}));
  process.exitCode=result.failed?1:0;
}
