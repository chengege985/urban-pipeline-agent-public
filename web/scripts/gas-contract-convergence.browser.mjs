import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const base = process.env.GAS_BASE_URL ?? "http://127.0.0.1:4183";
const executablePath = process.env.BROWSER_EXECUTABLE || undefined;
const fixture = JSON.parse(await readFile(resolve("src/tests/fixtures/gasCaseResponse.json"), "utf8"));
const evidenceDir = resolve("../docs/evidence/gas-contract-convergence-01");
const results = [];
const errors = [];

function check(name, condition, detail = "") {
  results.push({ name, ok: Boolean(condition), detail });
  console.log(`${condition ? "PASS" : "FAIL"} ${name}${detail ? ` · ${detail}` : ""}`);
  if (!condition) errors.push(name);
}

function clone() {
  return JSON.parse(JSON.stringify(fixture));
}

async function openPage(browser, responseFactory, extraRoutes = {}, hash = "#/gas") {
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  const requests = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  let call = 0;
  await page.route("**/api/v1/gas/cases/**", async (route) => {
    call += 1;
    requests.push(route.request().url());
    const custom = extraRoutes[call];
    if (custom === "abort") return route.abort("failed");
    if (custom && typeof custom.delay === "number") await new Promise((resolveDelay) => setTimeout(resolveDelay, custom.delay));
    const response = custom?.response ?? responseFactory(call);
    if (response === "abort") return route.abort("failed");
    return route.fulfill({ status: response.status, contentType: "application/json", body: JSON.stringify(response.body) });
  });
  await page.goto(`${base}/${hash}`, { waitUntil: "domcontentloaded" });
  return { context, page, pageErrors, consoleErrors, requests, call: () => call };
}

async function main() {
  await mkdir(evidenceDir, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath, args: ["--no-sandbox"] });

  const valid = await openPage(browser, () => ({ status: 200, body: fixture }));
  await valid.page.locator('[data-testid="gas-page"]').waitFor();
  check("valid 54-node fixture renders", (await valid.page.locator('[data-testid="gas-page"]').innerText()).includes("54 节点"));
  check("valid request uses the gas case endpoint", valid.requests[0]?.endsWith("/api/v1/gas/cases/syn-gas-case-K-001") === true, valid.requests[0] ?? "missing");
  check("valid request ID is labelled frontend", (await valid.page.locator(".gas-notice").innerText()).includes("前端请求标识"));
  check("no server trace label", !(await valid.page.locator('[data-testid="gas-page"]').innerText()).includes("服务端追踪 ID"));
  check("valid page has no page error", valid.pageErrors.length === 0, valid.pageErrors.join(" | "));
  check("valid page has no console error", valid.consoleErrors.length === 0, valid.consoleErrors.join(" | "));
  await valid.page.screenshot({ path: resolve(evidenceDir, "valid-1280x900.png") });
  await valid.context.close();

  const malformed = clone();
  delete malformed.services;
  const broken = await openPage(browser, () => ({ status: 200, body: malformed }));
  await broken.page.locator('[data-testid="gas-error"]').waitFor();
  check("HTTP 200 missing services is contract_error", await broken.page.locator('[data-testid="gas-error"]').getAttribute("data-error-kind") === "contract_error");
  check("contract error names field path", (await broken.page.locator('[data-testid="gas-error"]').innerText()).includes("services"));
  check("contract error does not render page", await broken.page.locator('[data-testid="gas-page"]').count() === 0);
  check("contract error reports HTTP 200 boundary", (await broken.page.locator('[data-testid="gas-error"]').innerText()).includes("HTTP 200"));
  check("contract error does not show DemoHealth", !(await broken.page.locator('[data-testid="gas-error"]').innerText()).includes("DemoHealth"));
  check("contract error does not white-screen or throw", broken.pageErrors.length === 0, broken.pageErrors.join(" | "));
  await broken.page.screenshot({ path: resolve(evidenceDir, "malformed-1280x900.png") });
  await broken.context.close();

  for (const status of [401, 403, 404, 503]) {
    const result = await openPage(browser, () => ({ status, body: { detail: `拒绝 ${status}` } }));
    await result.page.locator('[data-testid="gas-error"]').waitFor();
    check(`HTTP ${status} is unavailable`, await result.page.locator('[data-testid="gas-error"]').getAttribute("data-error-kind") === "unavailable");
    check(`HTTP ${status} has no page error`, result.pageErrors.length === 0, result.pageErrors.join(" | "));
    await result.context.close();
  }

  const network = await openPage(browser, () => "abort");
  await network.page.locator('[data-testid="gas-error"]').waitFor();
  check("network abort is unavailable/network", (await network.page.locator('[data-testid="gas-error"]').innerText()).includes("network_error") || (await network.page.locator('[data-testid="gas-error"]').innerText()).includes("网络"));
  check("network abort does not white-screen", await network.page.locator('[data-testid="gas-page"]').count() === 0);
  await network.context.close();

  const retryTransport = await openPage(browser, (call) => call === 1
    ? { status: 503, body: { detail: "暂不可用" } }
    : { status: 200, body: fixture });
  await retryTransport.page.locator('[data-testid="gas-error"]').waitFor();
  await retryTransport.page.locator('[data-testid="gas-retry"]').click();
  await retryTransport.page.locator('[data-testid="gas-page"]').waitFor();
  check("retry recovers after 503", await retryTransport.page.locator('[data-testid="gas-page"]').count() === 1);
  check("retry performs a second request", retryTransport.requests.length === 2, String(retryTransport.requests.length));
  await retryTransport.context.close();

  const demoMode = await openPage(browser, () => ({ status: 200, body: fixture }), {}, "?mode=demo#/gas");
  await demoMode.page.locator('[data-testid="gas-page"]').waitFor();
  check("demo mode does not fall back to water data", !(await demoMode.page.locator('[data-testid="gas-page"]').innerText()).includes("DemoHealth"));
  await demoMode.context.close();

  const stale = await openPage(browser, (call) => call === 1
    ? { delay: 450, status: 200, body: fixture }
    : { status: 200, body: fixture });
  const stalePage = stale.page;
  await stalePage.goto(`${base}/#/about`, { waitUntil: "domcontentloaded" });
  await stalePage.waitForTimeout(800);
  check("unmount does not surface late gas result", stale.pageErrors.length === 0 && await stalePage.locator('[data-testid="gas-page"]').count() === 0, stale.pageErrors.join(" | "));
  await stale.context.close();

  await writeFile(resolve(evidenceDir, "browser-results.json"), JSON.stringify({
    base,
    generatedAt: new Date().toISOString(),
    results,
    errors,
    fixture: { nodes: fixture.nodes.length, edges: fixture.edges.length, services: fixture.services.length },
  }, null, 2));
  await browser.close();
  if (errors.length > 0) {
    console.error(`FAILED CHECKS: ${errors.join(", ")}`);
    process.exitCode = 1;
  } else {
    console.log(`ALL ${results.length} CHECKS PASSED`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
