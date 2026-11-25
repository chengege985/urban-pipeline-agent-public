// Migration browser smoke. Product source is read-only; all output is isolated.
// node check_local_browser.mjs <python> <engineering> <web-root> <new-output> [chromium] [main|ui-v3]
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const [python, engineering, webArg, outputArg, executable, flavor='main'] = process.argv.slice(2);
if (!python || !engineering || !webArg || !outputArg) throw new Error('Usage: <python> <engineering> <web-root> <new-output> [chromium] [main|ui-v3]');
const web = path.resolve(webArg), output = path.resolve(outputArg);
await mkdir(path.dirname(output), { recursive:true });
await mkdir(output, { recursive:false });
const require = createRequire(path.join(web, 'package.json'));
const { chromium } = require('playwright');
const helper = path.join(path.dirname(fileURLToPath(import.meta.url)), 'start_synthetic_local.py');
const child = spawn(python, ['-B', helper, '--backend-root', path.resolve(engineering),
  '--web-dist', path.join(web, 'dist'), '--runtime-dir', path.join(output, 'runtime'), '--port', '0'],
  {stdio:['ignore','pipe','pipe'], windowsHide:true});
const logs=[], checks=[], http=[], errors=[], deniedExternal=[];
child.stderr.on('data', chunk=>logs.push(chunk.toString()));
let browser, fatal;
function check(name, passed, actual) { checks.push({name, passed:!!passed, actual}); if (!passed) throw new Error(name+': '+JSON.stringify(actual)); }
try {
  const ready = await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(new Error('local synthetic gateway timeout')),45000);
    child.once('exit',code=>{clearTimeout(timer);reject(new Error('gateway exited '+code));});
    createInterface({input:child.stdout}).on('line',line=>{try{const data=JSON.parse(line);if(data.ready){clearTimeout(timer);resolve(data);}}catch{}});
  });
  const base=ready.base_url;
  for(let i=0;i<100;i++){try{if((await fetch(base+'/health')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true, ...(executable?{executablePath:executable}:{}), args:['--disable-background-networking']});
  const ctx=await browser.newContext({viewport:{width:1440,height:1000}});
  await ctx.route('**/*',route=>{
    if(route.request().url().startsWith(base+'/')) return route.continue();
    deniedExternal.push(new URL(route.request().url()).origin);return route.abort();
  });
  const page=await ctx.newPage();
  page.on('pageerror',e=>errors.push(String(e)));
  page.on('response',response=>{const req=response.request(); const url=new URL(req.url());
    if(url.pathname.startsWith('/api/'))http.push({method:req.method(),path:url.pathname,status:response.status(),tokenHeader:!!req.headers()['x-water-token']});});
  await page.goto(ready.login_url);
  await page.getByText('统一平台接口已接通',{exact:true}).waitFor({timeout:60000});
  check('platform_page_loaded',true, 'server-backed synthetic view');
  check('dataset_view_real_http',http.some(x=>x.path===`/api/v1/datasets/${ready.snapshot_id}/view`&&x.status===200),http);
  check('cookie_not_visible_to_script',!(await page.evaluate(()=>document.cookie)).includes('waterpulse_local'), 'HttpOnly');
  await page.screenshot({path:path.join(output,'platform-overview.png'),fullPage:true});
  if(flavor==='main'){
    await page.goto(`${base}/?mode=platform&datasetId=${ready.snapshot_id}#/scenario/scn-syn-isolate?pipe=syn-pipe-fault`);
    await page.getByTestId('open-platform-report').waitFor({timeout:60000});
    await page.waitForFunction(()=>{const button=document.querySelector('[data-testid="open-platform-report"]');return button&&!button.disabled;}, null, {timeout:60000});
    check('real_decision_created',http.some(x=>x.path==='/api/v1/decision-runs'&&x.status===202),http.filter(x=>x.method==='POST'));
    await page.getByTestId('open-platform-report').click();
    await page.getByTestId('report-status').waitFor({timeout:30000});
    check('same_run_report_http',http.some(x=>/\/runs\/[^/]+\/report$/.test(x.path)&&x.status===200),page.url().split('#')[1]);
    check('report_has_bound_snapshot',(await page.getByTestId('platform-report-page').innerText()).includes(ready.snapshot_id),'snapshot identity');
    await page.screenshot({path:path.join(output,'platform-report.png'),fullPage:true});
  }else{
    check('ui_candidate_no_report_request',!http.some(x=>x.path.endsWith('/report')), 'candidate report gate unchanged');
  }
  check('browser_backend_token_absent',http.every(x=>!x.tokenHeader),http.length);
  check('no_browser_runtime_error',errors.length===0,errors);
  check('no_http_500',http.every(x=>x.status<500),http.filter(x=>x.status>=500));
  check('no_external_page_request',deniedExternal.length===0,deniedExternal);
}catch(error){fatal=String(error);process.exitCode=1;}
finally{
  if(browser)await browser.close();
  if(child.exitCode===null){child.kill(process.platform==='win32'?'SIGTERM':'SIGINT');
    await Promise.race([new Promise(resolve=>child.once('exit',resolve)),new Promise(resolve=>setTimeout(resolve,10000))]);
    if(child.exitCode===null)child.kill('SIGKILL');}
  await writeFile(path.join(output,'browser_summary.json'),JSON.stringify({checks,passed:checks.filter(x=>x.passed).length,total:checks.length,fatal,
    http,errors,deniedExternal,flavor,scope:'migration smoke, not full UI independent acceptance'},null,2));
  await writeFile(path.join(output,'server-stderr.log'),logs.join(''));
  console.log(JSON.stringify({passed:checks.filter(x=>x.passed).length,total:checks.length,fatal,output}));
}
