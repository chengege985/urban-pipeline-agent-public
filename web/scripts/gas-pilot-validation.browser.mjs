import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright";

const [runtime, output, commit, tree, mode, viewport = "1920x1080"] = process.argv.slice(2);
assert.match(commit, /^[a-f0-9]{40}$/);
assert.match(tree, /^[a-f0-9]{40}$/);
assert.ok(["rules", "qwen"].includes(mode));
assert.ok(["1920x1080", "1280x720"].includes(viewport));
const [width, height] = viewport.split("x").map(Number);
const parse = async (path) => JSON.parse(await readFile(path, "utf8"));
const cfg = await parse(join(runtime, "config.json"));
const identity = await parse(join(runtime, "access.json"));
assert.equal(cfg.source, commit);
const origin = "http://127.0.0.1:" + cfg.web_port;
const api = "http://127.0.0.1:" + cfg.api_port;
const catalogue = "/api/v1/gas/engineering/pilot/datasets";
const dataset = "syn-gas-pilot-history-01";
const v2 = catalogue + "/" + dataset + "/versions/demo-v2";
const v1 = catalogue + "/" + dataset + "/versions/demo-v1";
const observation = "syn-gas-pilot-obs-p3";
const headers = { "X-Water-Token": identity.platform_token, "Content-Type": "application/json" };
const receipt = { commit, tree, mode, viewport, transport: "loopback TCP", screenshots: [], checks: [], page_errors: [] };

await mkdir(output, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
receipt.browser = browser.version();
const context = await browser.newContext({
  viewport: { width, height }, reducedMotion: "reduce",
  httpCredentials: { username: identity.username, password: identity.password },
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on("pageerror", (error) => receipt.page_errors.push(error.name));

const receive = (path, method = "GET") => page.waitForResponse((res) => new URL(res.url()).pathname === path
  && res.request().method() === method, { timeout: 25000 });

async function screenshot(target, name) {
  await target.scrollIntoViewIfNeeded();
  const file = name + "-" + viewport + ".png";
  const path = join(output, file);
  await page.screenshot({ path, animations: "disabled" });
  receipt.screenshots.push({ file, sha256: createHash("sha256").update(await readFile(path)).digest("hex") });
}

try {
  assert.equal((await fetch(api + catalogue)).status, 401);
  const unavailable = catalogue + "/syn-gas-pilot-ungranted/versions/demo-v2";
  assert.equal((await fetch(api + unavailable, { headers })).status, 403);
  assert.equal((await fetch(api + unavailable + "/agent/query", {
    method: "POST", headers, body: JSON.stringify({ question: "解释观测", observation_id: observation }),
  })).status, 403);
  receipt.checks.push("HTTP 401 without credentials, 403 for both dataset and model query outside ACL");

  assert.equal((await page.goto(origin + "/#/gas", { waitUntil: "networkidle" })).status(), 200);
  const modeStatus = mode === "qwen" ? "ready" : "unavailable";
  await page.locator("[data-testid=gas-demo-model-status][data-status='" + modeStatus + "']").waitFor();
  await page.getByTestId("gas-monitoring-open").click();
  await page.getByRole("button", { name: "试点历史回放", exact: true }).click();
  const panel = page.getByTestId("gas-pilot-panel");
  const catalogueSelect = panel.getByLabel("选择试点数据集");
  const preferred = catalogueSelect.getByRole("option", { name: /demo-v2.*当前推荐版本/ });
  await preferred.waitFor({ state: "attached" });
  const readReplay = receive(v2 + "/replay");
  await catalogueSelect.selectOption({ label: await preferred.innerText() });
  const response = await readReplay;
  assert.equal(response.status(), 200);
  const report = await response.json();
  assert.equal(response.headers()["cache-control"], "no-store");
  assert.equal(report.data_mode, "synthetic");
  assert.deepEqual(report.segments.map((entry) => entry.threshold.version), ["pilot-r1", "pilot-r2"]);
  assert.equal(report.observations[13].observation.quality, "missing");
  assert.equal(report.observations[14].observation.quality, "suspect");
  const validation = report.validation;
  assert.equal(validation.status, "truth_comparison");
  assert.equal(validation.metrics.mode, "synthetic_truth_check");
  assert.equal(validation.duplicate_alarms.length, 1);
  assert.deepEqual([
    validation.metrics.false_alarms.length, validation.metrics.missed_events,
    validation.metrics.matches[0].detection_delay_seconds, validation.metrics.matches[0].recovery_delay_seconds,
  ], [1, 1, 120, 30]);
  const trend = panel.getByTestId("gas-pilot-trend");
  await trend.waitFor();
  assert.ok(await trend.locator("canvas").evaluate((canvas) =>
    new Set(canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data).size > 12));
  const checks = panel.getByTestId("gas-pilot-validation");
  await checks.getByText("合成真值对照").waitFor();
  await checks.getByText("误报 1 · 漏报 1 · 恢复检测 1 / 2").waitFor();
  await screenshot(trend, "pilot02-replay");
  receipt.replay = {
    data_mode: report.data_mode, dataset: report.dataset_id, version: report.version, observations: report.observations.length,
    manifest_sha256: report.manifest_sha256, threshold_versions: report.segments.map((entry) => entry.threshold.version),
    transitions: report.segments.flatMap((entry) => entry.channel.events.map(({ at, kind, cause }) => ({ at, kind, cause }))),
    validation,
  };
  receipt.checks.push("approved thresholds, quality exclusions, version deduplication and synthetic truth comparison");

  await panel.getByRole("button", { name: "打开触发观测 " + observation, exact: true }).click();
  const selected = panel.getByTestId("gas-pilot-observation");
  await selected.getByText(observation, { exact: true }).waitFor();
  const pipeResponse = receive(v2 + "/pipes/syn-gas-pilot-pipe-a");
  await selected.getByRole("button", { name: "查看关联管段 syn-gas-pilot-pipe-a" }).click();
  const pipe = await pipeResponse;
  assert.equal(pipe.status(), 200);
  assert.equal((await pipe.json()).geometry_status, "available");
  await selected.getByTestId("gas-pilot-pipe").waitFor();
  await selected.getByRole("button", { name: "打开试点地图", exact: true }).click();
  await selected.getByTestId("gas-pilot-local-map").waitFor();
  await selected.getByRole("button", { name: "打开关联事件证据", exact: true }).click();
  await selected.getByText("synthetic-ticket-001", { exact: true }).waitFor();
  assert.equal(new URL(page.url()).hash, "#/gas");
  receipt.checks.push("observation navigates to same-version asset, authorized GIS and event evidence");

  async function query(question) {
    const waiting = receive(v2 + "/agent/query", "POST");
    await selected.getByLabel("试点只读问答").fill(question);
    await selected.getByRole("button", { name: "查询试点证据" }).click();
    const read = await waiting;
    assert.equal(read.status(), 200);
    assert.deepEqual(read.request().postDataJSON().binding, {
      manifest_sha256: report.manifest_sha256, threshold_version: "pilot-r1",
      window_start: report.window_start, window_end: report.window_end,
    });
    const answer = await read.json();
    assert.equal(answer.observation_id, observation);
    assert.equal(answer.parser_source, mode === "qwen" ? "qwen_evidence_selection" : "local_rule_not_llm");
    assert.equal(answer.model_id, mode === "qwen" ? "Qwen3.8-27B" : null);
    assert.equal(answer.model_calls, mode === "qwen" ? 1 : 0);
    assert.equal(answer.threshold_version, "pilot-r1");
    assert.match(answer.answer, /7\.2 kPa/);
    assert.match(answer.answer, /synthetic-threshold-reference-a1/);
    assert.doesNotMatch(answer.answer, /pilot-r2|draft-ignored|revoked-ignored/);
    assert.deepEqual(answer.event_ids, ["syn-gas-pilot-event-a"]);
    assert.ok(answer.citations.some((entry) => entry.kind === "threshold" && entry.source.table === "threshold_rules"));
    assert.ok(answer.citations.every((entry) => entry.source.source_id.startsWith("syn-gas-pilot-")));
    return { request_id: read.headers()["x-request-id"], ...answer };
  }
  receipt.agent = [await query("解释当前观测、阈值和关联事件的证据。")];
  const result = selected.getByTestId("gas-pilot-agent");
  await result.waitFor();
  await result.getByRole("button", { name: "观测 " + observation, exact: true }).click();
  await result.getByTestId("gas-pilot-qwen-trace").getByText(observation, { exact: true }).waitFor();
  await result.getByRole("button", { name: /^来源 syn-gas-pilot-threshold_rules-source$/ }).click();
  const evidence = result.getByTestId("gas-pilot-qwen-trace");
  await evidence.getByText("synthetic-threshold-reference-a1", { exact: true }).waitFor();
  await result.getByRole("button", { name: /^阈值版本 pilot-r1 / }).click();
  await screenshot(evidence, "pilot02-qwen-evidence");

  receipt.agent.push(await query("这个阈值的版本和批准记录是什么？"));
  await selected.getByTestId("gas-pilot-turns").getByText("本观测的最近 2 轮问答").waitFor();
  const stale = await fetch(api + v1 + "/agent/query", {
    method: "POST", headers, body: JSON.stringify({
      question: "沿用上一轮阈值", observation_id: observation,
      binding: { manifest_sha256: report.manifest_sha256, threshold_version: "pilot-r1" },
    }),
  });
  assert.equal(stale.status, 409);
  const oldReplay = receive(v1 + "/replay");
  await catalogueSelect.selectOption({ label: await catalogueSelect.getByRole("option", { name: /demo-v1/ }).innerText() });
  assert.equal((await oldReplay).status(), 200);
  await selected.waitFor({ state: "hidden" });
  await checks.getByText("规则一致性检查", { exact: true }).waitFor();
  assert.equal(await panel.getByTestId("gas-pilot-qwen-trace").count(), 0);
  assert.deepEqual(receipt.page_errors, []);
  receipt.checks.push("same-scope multi-turn evidence, clickable source reference, stale version 409 and clean context switch");
  receipt.status = "passed";
} finally {
  await browser.close();
  await writeFile(join(output, "pilot02-browser.json"), JSON.stringify(receipt, null, 2) + "\n", { mode: 0o600 });
}
console.log(JSON.stringify({ status: receipt.status, mode, viewport, checks: receipt.checks.length, screenshots: receipt.screenshots.length }));
