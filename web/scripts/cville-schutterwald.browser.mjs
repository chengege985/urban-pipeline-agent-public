import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const browser = await chromium.launch({ channel: "chrome", headless: true });
const evidence = resolve("../engineering/outputs/research/browser");
await mkdir(evidence, { recursive: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto(process.env.GAS_DEMO_WEB_URL ?? "http://127.0.0.1:5173/#/gas");
  const datasets = page.locator("select").filter({ has: page.locator('option[value="schutterwald"]') });
  const scenarios = page.locator('[data-window-id="scenario"]');
  if (process.env.GAS_TEST_SOURCE !== "schutterwald") {
    await datasets.selectOption("charlottesville");
    await page.getByRole("button", { name: "风险分析", exact: true }).click();
    const cityAnalysis = page.getByRole("dialog", { name: "历史事件分析" });
    await cityAnalysis.getByText("月度记录数", { exact: true }).waitFor({ timeout: 100000 });
    await cityAnalysis.getByLabel("按类别统计").selectOption("areas");
    await cityAnalysis.getByText("按原始行政区分组", { exact: true }).waitFor();
    await page.screenshot({ path: resolve(evidence, "03-charlottesville-analysis.png"), fullPage: true });
    await cityAnalysis.getByRole("button", { name: "关闭窗口" }).click();
  }
  if (process.env.GAS_TEST_SOURCE !== "charlottesville") {
    await datasets.selectOption("schutterwald");
    await page.getByTestId("gas-topology-view").waitFor({ timeout: 35000 });
    await scenarios.getByRole("button", { name: "管段失效 · 部分建筑不可达" }).click();
    const request = page.waitForResponse((response) => response.request().method() === "GET" && response.url().includes("/schutterwald/run?"), { timeout: 75000 });
    await scenarios.getByRole("button", { name: "确认并执行推演" }).click();
    const run = await (await request).json();
    assert.equal(run.dataset_id, "schutterwald");
    assert.equal(run.hydraulics.status, "ok", JSON.stringify(run.hydraulics));
    await scenarios.getByText("新增不可达 1 个模型建筑接入点。").waitFor();
    assert.ok(await page.locator("[data-node-id].is-unreachable").count());
    await page.screenshot({ path: resolve(evidence, "02-schutterwald.png"), fullPage: true });
    await page.getByRole("button", { name: "风险分析", exact: true }).click();
    const research = page.getByRole("dialog", { name: "故障与隔离影响" });
    await research.getByText("全网压力变化节点数（0.01 bar 为展示分组）").waitFor();
    await research.getByLabel("节点状态").selectOption("missing");
    await research.getByLabel("节点压力详情").selectOption(run.unreachable_nodes[0]);
    await research.getByText("无结果", { exact: true }).waitFor();
    await research.getByLabel("故障影响").selectOption("zero");
    await research.getByText(/24 \/ 1053 条配气管线/).waitFor();
    await page.screenshot({ path: resolve(evidence, "04-schutterwald-pressure.png"), fullPage: true });
    await research.getByRole("button", { name: "关闭窗口" }).click();
    await datasets.selectOption("charlottesville");
    assert.equal(await page.getByText("新增不可达 1 个模型建筑接入点。").count(), 0);
  }
  assert.deepEqual(errors, []);
  console.log("Chrome check passed:", process.env.GAS_TEST_SOURCE ?? "both");
} finally {
  await browser.close();
}
