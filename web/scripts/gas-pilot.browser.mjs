import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright";

const [runtime, output, commit, tree, mode] = process.argv.slice(2);
assert.match(commit, /^[a-f0-9]{40}$/);
assert.match(tree, /^[a-f0-9]{40}$/);
assert.ok(["rules", "qwen"].includes(mode));
const parse = async (path) => JSON.parse(await readFile(path, "utf8"));
const cfg = await parse(join(runtime, "config.json"));
const identity = await parse(join(runtime, "access.json"));
assert.equal(cfg.source, commit);
const origin = "http://127.0.0.1:" + cfg.web_port;
const api = "http://127.0.0.1:" + cfg.api_port;
const catalogue = "/api/v1/gas/engineering/pilot/datasets";
const version = catalogue + "/syn-gas-pilot-history-01/versions/demo-v1";
const observation = "syn-gas-pilot-obs-p3";
const receipt = { commit, tree, mode, transport: "real loopback TCP", screenshots: [], checks: [], page_errors: [] };
await mkdir(output, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
receipt.browser = browser.version();
const context = await browser.newContext({
  viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce",
  httpCredentials: { username: identity.username, password: identity.password },
});
const page = await context.newPage();
page.setDefaultTimeout(15000);
page.on("pageerror", (error) => receipt.page_errors.push(error.name));

const receive = (path, method = "GET") => page.waitForResponse((res) => new URL(res.url()).pathname === path
  && res.request().method() === method, { timeout: 20000 });
async function screenshot(target, name) {
  await target.scrollIntoViewIfNeeded();
  const file = name + "-1920x1080.png";
  const path = join(output, file);
  await page.screenshot({ path, animations: "disabled" });
  receipt.screenshots.push({ file, sha256: createHash("sha256").update(await readFile(path)).digest("hex") });
}

try {
  assert.equal((await fetch(api + catalogue)).status, 401);
  assert.equal((await fetch(api + catalogue + "/syn-gas-pilot-other/versions/demo-v1", {
    headers: { "X-Water-Token": identity.platform_token },
  })).status, 403);
  receipt.checks.push("FastAPI TCP unauthenticated and out-of-scope dataset rejected");

  assert.equal((await page.goto(origin + "/#/gas", { waitUntil: "networkidle" })).status(), 200);
  const modeStatus = mode === "qwen" ? "ready" : "unavailable";
  await page.locator("[data-testid=gas-demo-model-status][data-status='" + modeStatus + "']").waitFor();
  await page.getByTestId("gas-monitoring-open").click();
  await page.getByTestId("gas-monitoring-panel").waitFor();
  await page.getByRole("button", { name: "试点历史回放", exact: true }).click();
  const panel = page.getByTestId("gas-pilot-panel");
  const selection = panel.getByLabel("选择试点数据集");
  await selection.waitFor();
  assert.equal(await selection.locator("option").count(), 2);
  const readReplay = receive(version + "/replay");
  await selection.selectOption({ index: 1 });
  const response = await readReplay;
  assert.equal(response.status(), 200);
  const report = await response.json();
  await panel.getByTestId("gas-pilot-replay").waitFor();
  assert.equal(new URL(page.url()).hash, "#/gas");
  assert.equal(report.data_mode, "synthetic");
  assert.deepEqual(report.segments.map((segment) => segment.threshold.version), ["v1", "v2"]);
  assert.deepEqual(report.segments[0].channel.events.map((event) => event.kind),
    ["limit_exceeded", "recovered", "data_missing", "recovered"]);
  assert.equal(report.observations[3].observation.value, 7.2);
  assert.equal(report.observations[8].classification, "normal");
  await screenshot(panel.getByTestId("gas-pilot-data-mode"), "01-pilot-replay");
  receipt.checks.push("labelled dataset, scoped historical replay and threshold version change");

  await panel.getByRole("button", { name: "打开触发观测 " + observation, exact: true }).click();
  const selected = panel.getByTestId("gas-pilot-observation");
  await selected.getByText(observation, { exact: true }).waitFor();
  const pipeResponse = receive(version + "/pipes/syn-gas-pilot-pipe-a");
  await selected.getByRole("button", { name: "查看关联管段 syn-gas-pilot-pipe-a" }).click();
  const pipe = await pipeResponse;
  assert.equal(pipe.status(), 200);
  assert.equal((await pipe.json()).geometry_status, "available");
  await selected.getByTestId("gas-pilot-pipe").waitFor();
  await selected.getByTestId("gas-pilot-linked-events").locator("summary").click();
  await screenshot(selected.getByTestId("gas-pilot-pipe"), "02-pilot-pipe-evidence");
  receipt.checks.push("selected observation drills into its same-version pipe, GIS and event evidence");

  const gotQuery = receive(version + "/agent/query", "POST");
  await selected.getByRole("button", { name: "查询试点证据" }).click();
  const result = await gotQuery;
  assert.equal(result.status(), 200);
  const answer = await result.json();
  assert.equal(answer.observation_id, observation);
  assert.equal(answer.parser_source, mode === "qwen" ? "qwen_evidence_selection" : "local_rule_not_llm");
  assert.equal(answer.model_calls, mode === "qwen" ? 1 : 0);
  assert.equal(answer.model_id, mode === "qwen" ? "Qwen3.8-27B" : null);
  assert.match(answer.answer, /7\.2 kPa/);
  assert.match(answer.answer, /synthetic-rule-only-a1/);
  assert.ok(answer.evidence.some((entry) => entry.table === "events" && entry.source_record_id === "event-source-row-a"));
  assert.ok(answer.evidence.every((entry) => entry.source_id.startsWith("syn-gas-pilot-")));
  const agent = selected.getByTestId("gas-pilot-agent");
  await agent.waitFor();
  await screenshot(agent, "03-pilot-agent");
  receipt.agent = { request_id: result.headers()["x-request-id"], ...answer };
  receipt.checks.push("read-only agent answers from the selected version and cites input evidence");

  await panel.getByLabel("试点测点").selectOption("syn-gas-pilot-flow-b");
  await selected.waitFor({ state: "hidden" });
  await panel.getByRole("button", { name: "读取历史回放" }).click();
  await panel.getByTestId("gas-pilot-replay").getByText("syn-gas-pilot-obs-f2", { exact: true }).waitFor();
  assert.deepEqual(receipt.page_errors, []);
  receipt.checks.push("sensor change clears previous observation and answer");
  receipt.status = "passed";
} finally {
  await browser.close();
  await writeFile(join(output, "pilot-browser.json"), JSON.stringify(receipt, null, 2) + "\n", { mode: 0o600 });
}
console.log(JSON.stringify({ status: receipt.status, mode, checks: receipt.checks.length, screenshots: receipt.screenshots.length }));
