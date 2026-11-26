
import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const evidence = resolve("../engineering/outputs/research/browser/ngn-sample");
await mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
const errors = [];
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(process.env.GAS_DEMO_WEB_URL ?? "http://127.0.0.1:5224/#/gas");
  const source = page.getByLabel("更换数据集");
  await source.selectOption("ngn_distribution_example");
  const map = page.locator(".gas-ngn-model-map");
  await map.waitFor();
  assert.equal(await map.getByRole("button").count(), 103);
  const scenario = page.locator('[data-window-id="scenario"]');
  const outage = map.getByRole("button", {name:"LINE_10077", exact: true});
  const point = await outage.evaluate(path => {
    const p = path.getPointAtLength(path.getTotalLength() / 2).matrixTransform(path.getScreenCTM());
    return { x: p.x, y: p.y };
  });
  await page.mouse.click(point.x, point.y);
  await scenario.getByLabel("模型负荷").selectOption("1");
  assert.equal(await scenario.getByLabel("退出管段").inputValue(), "LINE_10077");
  let executionRequests = 0;
  page.on("request", r => { if (r.url().includes("/ngn_distribution_example/run?")) executionRequests++; });
  await page.getByRole("button", {name: "应急推演", exact: true}).click();
  const pending = page.waitForResponse(r => r.url().includes("/ngn_distribution_example/run?"), {timeout: 130000});
  await scenario.getByRole("button", {name:"计算管段退出"}).click();
  const result = await (await pending).json();
  assert.equal(result.status, "ok", JSON.stringify(result));
  assert.deepEqual(result.after.unavailable_sinks, ["HOUSE_09868"]);
  const timeline = page.getByTestId("gas-timeline");
  const details = page.getByTestId("ngn-stage-details");
  const faultPipe = map.locator('[data-run-pipe="LINE_10077"]');
  const affected = map.locator('[data-load-point="HOUSE_09868"]');
  await timeline.getByRole("button", {name: /基准连通/}).waitFor();
  assert.equal(await faultPipe.getAttribute("data-stage-status"), "active");
  assert.equal(await affected.getAttribute("data-stage-status"), "reachable");
  await details.getByLabel("查看负荷点拓扑证据").selectOption("HOUSE_09868");
  await details.getByText(/^气源 /).waitFor();
  await timeline.getByRole("button", {name: /给定管段退出/}).click();
  await details.getByText("新增不可达负荷点", {exact:false}).waitFor();
  assert.equal(await faultPipe.getAttribute("data-stage-status"), "unavailable");
  assert.equal(await affected.getAttribute("data-stage-status"), "unavailable");
  assert.equal(await details.getByRole("columnheader", {name: "退出后"}).first().count(), 1);
  await details.getByText("该负荷点无源可达，未生成路径。").waitFor();
  await page.screenshot({path:resolve(evidence,"01-outage.png")});
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("link", {name: "下载计算证据"}).click();
  const downloaded = JSON.parse(await readFile(await (await downloadPromise).path(), "utf8"));
  assert.equal(downloaded.execution_id, result.execution_id);
  await timeline.getByRole("button", {name: /恢复基准连接/}).click();
  await details.getByText("当前阶段 · 恢复基准连接").waitFor();
  assert.equal(await faultPipe.getAttribute("data-stage-status"), "active");
  assert.equal(await affected.getAttribute("data-stage-status"), "reachable");
  assert.equal(await details.getByRole("columnheader", {name: "退出后"}).count(), 0);
  await details.getByText(/^气源 /).waitFor();
  await timeline.getByRole("button", {name: "重置"}).click();
  await timeline.getByRole("button", {name: "播放", exact: true}).click();
  await timeline.locator('[aria-pressed="true"]').filter({hasText:"给定管段退出"}).waitFor();
  await timeline.locator('[aria-pressed="true"]').filter({hasText:"恢复基准连接"}).waitFor();
  assert.equal(executionRequests, 1);
  await scenario.getByLabel("模型负荷").selectOption("2");
  await timeline.getByText("无可回放阶段").waitFor();
  assert.equal(await details.count(), 0);
  assert.equal(executionRequests, 1);
  await page.getByRole("button", {name:"风险分析", exact:true}).click();
  const dialog = page.getByRole("dialog");
  const trendPending = page.waitForResponse(r => r.url().includes("trend=true"), {timeout: 130000});
  await dialog.getByRole("button", {name:"计算十个基准工况"}).click();
  const trend = await (await trendPending).json();
  assert.equal(Object.values(trend.series).filter(r => r.status === "ok").length, 10);
  await dialog.getByLabel("各工况基准压力").waitFor();
  await page.screenshot({path:resolve(evidence,"02-period-pressure.png")});
  await dialog.getByRole("button", {name:"关闭窗口"}).click();
  await source.selectOption("sicily_transmission");
  await page.getByText("西西里 · 研究拓扑示意", {exact:false}).first().waitFor();
  assert.equal(await map.count(), 0);
  await page.getByRole("button", {name:"风险分析", exact:true}).click();
  await dialog.getByText("作者发布结果", {exact:false}).first().waitFor();
  if (await dialog.getByRole("button", {name:"复核当前基准稳态"}).isEnabled()) {
    const baselinePending = page.waitForResponse(r => r.url().includes("/sicily_transmission/baseline?"), {timeout: 100000});
    await dialog.getByRole("button", {name:"复核当前基准稳态"}).click();
    const baseline = await (await baselinePending).json();
    assert.ok(["ok", "not_estimable"].includes(baseline.hydraulics.status), JSON.stringify(baseline));
    await dialog.getByText(/本站独立复核/).waitFor();
    await page.screenshot({path:resolve(evidence,"03-sicily-verification.png")});
    await writeFile(resolve(evidence,"sicily-baseline.json"), JSON.stringify(baseline, null, 2));
  }
  assert.deepEqual(errors, []);
  await writeFile(resolve(evidence,"verification.json"), JSON.stringify({
    browser: "Windows Chrome", execution_id: result.execution_id, stable_result_sha256: result.stable_result_sha256,
    track: "103 model pipes, three-stage outage replay, map/detail linkage, result download, 10 baseline periods, Sicily mode", page_errors: errors,
  }, null, 2));
  console.log("NGN sample browser interactions passed");
} finally {
  await browser.close();
}
