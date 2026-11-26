import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const evidence = resolve("../engineering/outputs/research/browser/sicily");
await mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(process.env.GAS_DEMO_WEB_URL ?? "http://127.0.0.1:5173/#/gas");
  const sources = page.getByLabel("更换数据集");
  await sources.selectOption("gaslib_582");
  await page.getByTestId("public-gaslib-nodes").waitFor();
  await sources.selectOption("sicily_transmission");
  await page.getByText("西西里 · 研究拓扑示意", { exact: false }).first().waitFor();
  const scenario = page.locator('[data-window-id="scenario"]');
  await scenario.getByLabel("给定退出管段").selectOption("1");
  await page.screenshot({ path: resolve(evidence, "01-topology.png") });
  await page.getByRole("button", { name: "风险分析", exact: true }).click();
  const risk = page.getByRole("dialog");
  await risk.getByText("作者发布结果", { exact: false }).waitFor();
  await risk.getByLabel("代表日").selectOption("20260108");
  await risk.getByLabel("小时").selectOption("20260108-18");
  await risk.getByText("作者发布结果", { exact: false }).waitFor();
  await page.screenshot({ path: resolve(evidence, "02-condition.png") });
  await risk.getByRole("button", { name: "关闭窗口" }).click();
  await page.getByRole("button", { name: "应急推演", exact: true }).click();
  const time = Date.now();
  const responseWait = page.waitForResponse((response) => response.url().includes("/sicily_transmission/topology?")
    && response.request().method() === "POST", { timeout: 8000 });
  await scenario.getByRole("button", { name: "执行拓扑推演" }).click();
  const response = await responseWait;
  assert.equal(response.status(), 200);
  const result = await response.json();
  assert.equal(result.case_id, "20260108-18");
  assert.equal(result.edge_id, "1");
  assert.equal(result.unreachable_nodes.length, 1);
  assert.equal(result.hydraulics.status, "not_requested");
  const timeline = page.getByTestId("gas-timeline");
  await timeline.getByRole("button", { name: /给定管段退出/ }).waitFor();
  const elapsed = Date.now() - time;
  await page.getByText("当前阶段：给定管道退出", { exact: true }).waitFor();
  await page.screenshot({ path: resolve(evidence, "04-topology-replay.png") });
  await timeline.getByRole("button", { name: /恢复基准连接/ }).click();
  await page.getByText("当前阶段：全网连接", { exact: true }).waitFor();
  await timeline.getByRole("button", { name: "播放", exact: true }).click();
  await page.getByText("当前阶段：给定管道退出", { exact: true }).waitFor();
  await page.getByText("当前阶段：全网连接", { exact: true }).waitFor();
  await scenario.getByRole("button", { name: "查看退出影响" }).click();
  await page.getByRole("dialog").getByText("新增断连节点 1 个", { exact: false }).waitFor();
  await page.screenshot({ path: resolve(evidence, "03-comparison.png") });
  await page.getByRole("dialog").getByRole("button", { name: "关闭窗口" }).click();
  await page.route("**/sicily_transmission/run?*", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1000));
    await route.fulfill({ json: { ...result, hydraulics: { status: "not_estimable", reason: "测试用复核失败" } } });
  });
  await scenario.getByRole("button", { name: "另算稳态压力" }).click();
  await scenario.getByText("正在独立求解稳态压力", { exact: false }).waitFor();
  await timeline.getByRole("button", { name: /给定管段退出/ }).click();
  await page.getByText("当前阶段：给定管道退出", { exact: true }).waitFor();
  await scenario.getByText("测试用复核失败", { exact: true }).waitFor();
  await page.unroute("**/sicily_transmission/run?*");
  await scenario.getByRole("button", { name: "有替代通路 · 342" }).click();
  await scenario.getByRole("button", { name: "执行拓扑推演" }).click();
  await scenario.getByText("两端之间存在替代通路", { exact: false }).waitFor();
  assert.equal(await page.locator('[data-stage-kind="alternate"][data-active="true"]').count(), 10);
  await scenario.getByRole("button", { name: "支网断连 · 254" }).click();
  await scenario.getByRole("button", { name: "执行拓扑推演" }).click();
  await scenario.getByText("24 个新增断连节点、23 条断连支网管段", { exact: false }).waitFor();
  assert.equal(await page.locator('[data-stage-kind="branch"][data-active="true"]').count(), 23);
  await page.screenshot({ path: resolve(evidence, "05-disconnected-branch.png") });
  await sources.selectOption("gaslib_582");
  await page.getByTestId("public-gaslib-nodes").waitFor();
  assert.equal(await scenario.getByText("查看退出影响", { exact: true }).count(), 0);
  assert.deepEqual(errors, []);
  await writeFile(resolve(evidence, "verification.json"), JSON.stringify({
    case_id: result.case_id, edge_id: result.edge_id, execution_id: result.execution_id, topology_browser_ms: elapsed,
    hydraulics: result.hydraulics.status, timeline_during_delayed_hydraulics: "passed (browser stub)", errors,
  }, null, 2));
  console.log("Sicily topology and timeline passed; browser latency (ms):", elapsed);
} finally {
  await browser.close();
}
