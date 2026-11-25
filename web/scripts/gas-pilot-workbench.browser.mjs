import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright";

const [runtime, output, commit, tree, mode, viewport] = process.argv.slice(2);
assert.match(commit, /^[a-f0-9]{40}$/);
assert.match(tree, /^[a-f0-9]{40}$/);
assert.ok(["rules", "qwen"].includes(mode));
assert.ok(["1920x1080", "1280x720"].includes(viewport));
const [width, height] = viewport.split("x").map(Number);
const parse = async (path) => JSON.parse(await readFile(path, "utf8"));
const cfg = await parse(join(runtime, "config.json"));
const access = await parse(join(runtime, "access.json"));
assert.equal(cfg.source, commit);
const web = "http://127.0.0.1:" + cfg.web_port;
const api = "http://127.0.0.1:" + cfg.api_port;
const root = "/api/v1/gas/engineering/pilot/datasets";
const source = root + "/syn-gas-pilot-history-01/versions";
const v3 = source + "/demo-v3";
const obs = "syn-gas-pilot-obs-p3";
const suspect = "syn-gas-pilot-obs-p14";
const headers = { "X-Water-Token": access.platform_token, "Content-Type": "application/json" };
const receipt = { status: "failed", commit, tree, mode, viewport, transport: "loopback TCP", checks: [], queries: [], screenshots: [] };
const errors = [];
await mkdir(output, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
receipt.browser = browser.version();
const context = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce",
  httpCredentials: { username: access.username, password: access.password } });
const page = await context.newPage();
page.setDefaultTimeout(16000);
page.on("pageerror", (error) => errors.push(error.name));
const receive = (path, method = "GET") => page.waitForResponse((response) =>
  new URL(response.url()).pathname === path && response.request().method() === method, { timeout: 25000 });

async function screenshot(locator, stem) {
  await locator.scrollIntoViewIfNeeded();
  const file = stem + "-" + viewport + ".png";
  const path = join(output, file);
  await page.screenshot({ path, animations: "disabled" });
  receipt.screenshots.push({ file, sha256: createHash("sha256").update(await readFile(path)).digest("hex") });
}

const panel = page.getByTestId("gas-pilot-panel");
const selection = panel.getByLabel("选择试点数据集");
const detail = panel.getByTestId("gas-pilot-observation");
const result = panel.getByTestId("gas-pilot-agent");
const evidence = result.getByTestId("gas-pilot-qwen-trace");
const trend = panel.getByTestId("gas-pilot-trend");

async function openPilot() {
  await page.getByTestId("gas-monitoring-open").click();
  await page.getByRole("button", { name: "试点历史回放", exact: true }).click();
}

async function selectVersion(version) {
  const option = selection.getByRole("option", { name: new RegExp(" · " + version + " · ") });
  await option.waitFor({ state: "attached" });
  const read = receive(source + "/" + version + "/replay");
  await selection.selectOption({ value: await option.getAttribute("value") });
  const response = await read;
  assert.equal(response.status(), 200);
  return response.json();
}

async function openAlarm() {
  await panel.getByRole("button", { name: "打开触发观测 " + obs, exact: true }).click();
  await detail.getByText(obs, { exact: true }).waitFor();
}

async function query(question, observation, threshold, focus, phrase) {
  const waiting = receive(v3 + "/agent/query", "POST");
  await detail.getByLabel("试点只读问答").fill(question);
  await detail.getByRole("button", { name: "查询试点证据" }).click();
  const response = await waiting;
  assert.equal(response.status(), 200);
  const data = await response.json();
  assert.equal(data.observation_id, observation);
  assert.equal(data.asset_id, "syn-gas-pilot-pipe-a");
  assert.equal(data.threshold_version, threshold);
  assert.equal(data.focus, focus);
  assert.equal(data.model_state, mode === "qwen" ? "online" : "unavailable");
  assert.equal(data.model_id, mode === "qwen" ? "Qwen3.8-27B" : null);
  assert.equal(data.model_calls, mode === "qwen" ? 1 : 0);
  assert.ok(data.answer.includes(phrase), data.answer);
  assert.ok(data.citations.every((cite) => cite.source.source_id.startsWith("syn-gas-pilot-")));
  receipt.queries.push({ request_id: response.headers()["x-request-id"], question, ...data });
  await result.getByTestId("gas-pilot-model-state").waitFor();
  return data;
}

try {
  assert.equal((await fetch(api + root)).status, 401);
  const noScope = root + "/syn-gas-pilot-ungranted/versions/demo-v3";
  assert.equal((await fetch(api + noScope, { headers })).status, 403);
  assert.equal((await fetch(api + noScope + "/agent/query", {
    headers, method: "POST", body: JSON.stringify({ observation_id: obs, question: "查看观测" }),
  })).status, 403);
  receipt.checks.push("FastAPI TCP 401 and 403 for catalogue, dataset and Agent scope");

  await page.goto(web + "/#/gas", { waitUntil: "networkidle" });
  const state = mode === "qwen" ? "ready" : "unavailable";
  await page.locator("[data-testid=gas-demo-model-status][data-status='" + state + "']").waitFor();
  await openPilot();
  const report = await selectVersion("demo-v3");
  assert.equal(report.data_mode, "synthetic");
  assert.deepEqual(report.applied_thresholds.map((rule) => rule.version), ["pilot-r1", "pilot-r2"]);
  assert.equal(report.validation.duplicate_alarms.length, 1);
  assert.equal(report.observations[13].observation.quality, "missing");
  assert.equal(report.observations[14].observation.quality, "suspect");
  assert.equal(new URL(page.url()).hash, "#/gas");
  await panel.getByTestId("gas-pilot-authorization").getByText("合成验证").waitFor();
  const quality = panel.getByTestId("gas-pilot-quality");
  await quality.locator("summary").click();
  await quality.getByText("源记录标记为可疑", { exact: false }).waitFor();
  const rejections = panel.getByTestId("gas-pilot-rejections");
  await rejections.locator(":scope > summary").click();
  await rejections.getByText(/demo-v3-rejected.*回执/).click();
  await rejections.getByRole("list", { name: "缺失字段" }).getByText("observed_at", { exact: true }).waitFor();
  await screenshot(rejections, "pilot03-quality");
  await rejections.locator(":scope > summary").click();
  await quality.locator("summary").click();
  await trend.getByRole("list", { name: "生效阈值与切换时间" }).getByText("2026-11-18T01:08:00Z").waitFor();
  await screenshot(trend, "pilot03-trend");
  receipt.checks.push("active policy change, suspect and missing markers, recorded rejection and missing field");

  const canvas = trend.locator("canvas");
  const box = await canvas.boundingBox();
  assert.ok(box);
  await canvas.click({ position: { x: box.width * (78 + 858 * 3 / 22) / 960,
    y: box.height * (206 - 168 * (7.2 - 5) / (9 - 5)) / 254 } });
  await detail.getByText(obs, { exact: true }).waitFor();
  await detail.getByRole("button", { name: "查看关联管段 syn-gas-pilot-pipe-a" }).click();
  await detail.getByTestId("gas-pilot-pipe").waitFor();
  await detail.getByRole("button", { name: "打开关联事件证据" }).click();
  await detail.getByText("synthetic-ticket-001", { exact: true }).waitFor();
  const alarm = await query("为什么这条压力记录触发了告警？", obs, "pilot-r1", "alarm", "触发证据含 3 条原始观测");
  assert.equal(alarm.citations.filter((cite) => cite.kind === "observation").length, 3);
  await result.getByRole("button", { name: "观测 syn-gas-pilot-obs-p1", exact: true }).click();
  await evidence.getByText("syn-gas-pilot-obs-p1", { exact: true }).waitFor();
  await query("这个测点当时使用哪个阈值版本？", obs, "pilot-r1", "threshold", "该观测对应版本 pilot-r1");
  await panel.getByRole("button", { name: suspect, exact: true }).click();
  await detail.getByTestId("gas-pilot-quality-reason").getByText(/采集端自检未通过/).waitFor();
  await query("为什么这条记录被标记为可疑？", suspect, "pilot-r2", "quality", "采集端自检未通过");
  await openAlarm();
  await query("切换阈值版本后，哪些告警结果变化了？", obs, "pilot-r1", "version_change", "连续越限换版去重 1 条");
  await result.getByRole("button", { name: /^阈值版本 pilot-r2 / }).click();
  await evidence.getByText("synthetic-threshold-reference-a2", { exact: true }).waitFor();
  await screenshot(evidence, "pilot03-version-evidence");
  await query("当前管段关联哪些历史事件？", obs, "pilot-r1", "asset_events", "有 2 条历史关联事件");
  await result.getByRole("button", { name: "事件 syn-gas-pilot-event-b", exact: true }).click();
  await evidence.getByText(/synthetic-ticket-002/).waitFor();
  receipt.checks.push("chart to raw record, asset and Agent. Five evidence questions and citations including second version");

  await page.route("**/versions/demo-v3/replay?*", (route) => route.fulfill({ status: 503, json: { detail: "synthetic retry check" } }), { times: 1 });
  await panel.getByLabel("试点测点").selectOption("syn-gas-pilot-flow-b");
  await panel.getByRole("button", { name: "读取历史回放" }).click();
  const retry = panel.getByRole("button", { name: "重试历史回放" });
  await retry.waitFor();
  const flowRead = receive(v3 + "/replay");
  await retry.click();
  const flow = await (await flowRead).json();
  assert.equal(flow.sensor.metric, "flow");
  assert.equal(flow.applied_thresholds[0].version, "pilot-flow-r1");
  await trend.getByText(/流量历史趋势/).waitFor();
  await panel.getByRole("button", { name: "syn-gas-pilot-obs-f2", exact: true }).click();
  await detail.getByRole("button", { name: "打开试点地图" }).click();
  await detail.getByTestId("gas-pilot-map").getByText(/GIS 未就绪/).waitFor();
  await detail.getByRole("button", { name: "定位试点拓扑" }).click();
  await detail.getByTestId("gas-pilot-topology").getByText("syn-gas-pilot-pipe-b", { exact: true }).waitFor();
  receipt.checks.push("recover from 503, flow history and topology fallback without authorized geometry");

  await page.route("**/versions/demo-v3/agent/query", (route) => {
    const body = route.request().postDataJSON();
    return route.continue({ postData: JSON.stringify({ ...body, binding: { ...body.binding, threshold_version: "old-version" } }) });
  }, { times: 1 });
  const stale = receive(v3 + "/agent/query", "POST");
  await detail.getByRole("button", { name: "查询试点证据" }).click();
  assert.equal((await stale).status(), 409);
  await panel.getByTestId("gas-pilot-scope-notice").getByText(/旧观测和问答已清空/).waitFor();
  await detail.waitFor({ state: "hidden" });
  await selectVersion("demo-v2");
  assert.equal(await result.count(), 0);
  await page.getByRole("button", { name: "关闭窗口", exact: true }).click();
  await page.getByRole("button", { name: "切换浅色主题", exact: true }).click();
  assert.equal(await page.getByTestId("gas-page").getAttribute("data-theme"), "light");
  await openPilot();
  await selectVersion("demo-v3");
  await trend.getByText(/压力历史趋势/).waitFor();
  await screenshot(trend, "pilot03-trend-light");
  assert.deepEqual(errors, []);
  receipt.checks.push("real 409 clears stale selection, version reselect works, light theme renders");
  receipt.status = "passed";
} finally {
  await browser.close();
  await writeFile(join(output, "pilot03-browser.json"), JSON.stringify({ ...receipt, page_errors: errors }, null, 2) + "\n", { mode: 0o600 });
}
console.log(JSON.stringify({ status: receipt.status, mode, viewport, checks: receipt.checks.length, queries: receipt.queries.length }));
