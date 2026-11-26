
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import { chromium } from "playwright";

const evidence = resolve("../engineering/outputs/research/browser/sicily-agent");
await mkdir(evidence, { recursive: true });
const browser = await chromium.launch({ channel: "chrome", headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto(process.env.GAS_DEMO_WEB_URL ?? "http://127.0.0.1:5236/#/gas");
  await page.getByLabel("更换数据集").selectOption("sicily_transmission");
  await page.getByText("西西里 · 研究拓扑示意", { exact: false }).first().waitFor();
  const open = () => page.getByRole("button", { name: /智能助手.*西西里 CUV/ }).click();
  await page.locator('[data-window-id="tasks"]').getByRole("button", { name: "打开西西里助手" }).click();
  const dialog = page.getByTestId("gas-agent-dialog");
  await page.getByRole("dialog", { name: "西西里 CUV 助手" }).waitFor();
  const ask = async (question) => {
    await dialog.getByLabel("向西西里助手提问").fill(question);
    const ready = page.waitForResponse(r => r.url().endsWith("/agent/query") && r.request().method() === "POST");
    await dialog.getByRole("button", { name: "发送", exact: true }).click();
    const reply = await ready;
    assert.equal(reply.status(), 200, await reply.text());
    return reply.json();
  };
  const overview = await ask("当前有哪些数据？");
  assert.ok(overview.rows.some(row => row.value === 340));
  const asset = await ask("254 号管段连接哪两个节点？");
  assert.equal(asset.object.id, "254");
  await dialog.getByRole("button", { name: "在拓扑中查看" }).click();
  await dialog.waitFor({ state: "hidden" });
  await open();
  const comparison = await ask("按新增断连节点数优先，比较 342 和 254 号管段退出");
  assert.deepEqual([...comparison.plan.edges].sort(), ["254", "342"]);
  assert.ok(!comparison.runs);
  await page.screenshot({path:resolve(evidence,"01-confirmation.png")});
  const confirmation = page.waitForResponse(r => r.url().endsWith("/agent/confirm") && r.request().method() === "POST");
  await dialog.getByRole("button", { name: "确认运行", exact: true }).click();
  const answer = await (await confirmation).json();
  assert.equal(answer.execution_status, "completed", JSON.stringify(answer));
  assert.ok(answer.rows.some(row => row.value.includes("24 个")));
  await dialog.getByRole("button", { name: "回放管段 254 结果" }).waitFor();
  await page.screenshot({path:resolve(evidence,"02-comparison.png")});
  await dialog.getByRole("button", { name: "回放管段 254 结果" }).click();
  await page.getByText("当前阶段：给定管道退出", { exact: true }).waitFor();
  await open();
  const explanation = await ask("解释这次推演，为什么有节点断连？");
  assert.equal(explanation.runs[0].execution_id, answer.runs.find(run => run.edge_id === "254").execution_id);
  assert.match(explanation.answer, /24/);
  assert.deepEqual(errors, []);
  await writeFile(resolve(evidence,"verification.json"), JSON.stringify({
    browser: "Windows Chrome", overview_model: overview.model_status, asset_model: asset.model_status,
    comparison_model: comparison.model_status, explanation_model: explanation.model_status,
    confidence_boundary: "published research benchmark", runs: answer.runs, errors,
  }, null, 2));
  console.log("Sicily CUV browser interaction passed");
} finally {
  await browser.close();
}
