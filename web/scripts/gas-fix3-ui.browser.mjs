import assert from "node:assert/strict";
import { mkdir, readFile, writeFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { chromium } from "playwright";

const before = process.argv.includes("--before");
const base = new URL(process.env.GAS_UI_BASE_URL ?? "http://127.0.0.1:22473");
assert(["127.0.0.1", "localhost"].includes(base.hostname));
const origin = (offset = 0) => { const url = new URL(base); url.port = String(Number(base.port) + offset); return url.origin; };
const dir = resolve("docs/evidence/gas-sprint01-fix3-ui");
await mkdir(dir, { recursive: true });
const browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });
const checks = [], screenshots = [], responses = [], pending = [], viewportResults = [];
const stage = before ? "before" : "after";
const check = async (name, run) => { try { const result = await run(); checks.push({ name, ok: true, result }); } catch (error) { checks.push({ name, ok: false, detail: String(error) }); } };
const hash = (bytes) => createHash("sha256").update(bytes).digest("hex");
const bodySummary = (body) => body && Array.isArray(body.items)
  ? { status: body.status, data_mode: body.data_mode, validation_status: body.validation_status, total: body.total, loaded: body.items.length,
      ...(body.items[0]?.asset_id ? { asset_ids: body.items.map((item) => item.asset_id) } : {}),
      ...(body.items[0]?.event_id ? { events: body.items.map(({ event_id, asset_ids, closed_valve_ids }) => ({ event_id, asset_ids, closed_valve_ids })) } : {}) }
  : body?.counts ? { status: body.status, data_mode: body.data_mode, validation_status: body.validation_status, counts: body.counts, evidence: body.evidence }
  : body;
function observe(page, phase) {
  const errors = [], paths = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", (request) => { const url = new URL(request.url()); if (url.pathname.startsWith("/api/")) paths.push(url.pathname); });
  page.on("response", (response) => {
    const url = new URL(response.url());
    if (!url.pathname.startsWith("/api/")) return;
    pending.push((async () => { let body; try { body = await response.json(); } catch { body = null; }
      responses.push({ phase, method: response.request().method(), path: url.pathname + url.search, status: response.status(), body: bodySummary(body) }); })());
  });
  return { errors, paths };
}
async function shot(page, name) {
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: resolve(dir, name), fullPage: true });
  screenshots.push({ name, sha256: hash(await readFile(resolve(dir, name))) });
}
async function api(path, offset = 0) {
  const response = await fetch(origin(offset) + path, { signal: AbortSignal.timeout(20000) });
  assert.equal(response.status, 200, path);
  return response.json();
}
async function readEvent(page, id) {
  await page.locator(".gas-event-list button").filter({ hasText: id }).click();
  await page.locator(`[data-testid="gas-event-detail"][data-event-id="${id}"]`).waitFor();
}
async function readRelated(page, id) {
  await page.getByRole("button", { name: `打开资产详情 ${id}`, exact: true }).click();
  await page.locator(`[data-testid="gas-asset-detail"][data-asset-id="${id}"]`).waitFor();
}
async function catalogueReady(page, offset = 0, type) {
  const path = `/api/v1/gas/engineering/assets?limit=50&offset=${offset}` + (type ? `&asset_type=${type}` : "");
  const expected = await api(path);
  const rows = page.locator(".gas-asset-directory tbody tr");
  await page.waitForFunction(({ count, first }) => {
    const rows = [...document.querySelectorAll(".gas-asset-directory tbody tr")];
    return rows.length === count && (!first || rows[0]?.getAttribute("data-asset-id") === first);
  }, { count: expected.items.length, first: expected.items[0]?.asset_id });
  assert.deepEqual(await rows.evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-asset-id"))), expected.items.map((item) => item.asset_id));
  return expected;
}
let catalogue;
try {
  const deadline = Date.now() + 310000;
  let ready = false;
  while (Date.now() < deadline) {
    try { ready = (await fetch(origin() + "/health", { signal: AbortSignal.timeout(1500) })).status === 200; } catch { /* actual backend startup */ }
    if (ready) break;
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  assert(ready, "actual FastAPI did not start");
  catalogue = await api("/api/v1/gas/cases/syn-gas-case-K-001");
  const events = await api("/api/v1/gas/engineering/events");
  const multi = events.items.find((event) => event.asset_ids.length === 9);
  assert(multi, "confirmed nine-associated-asset event required");
  const valveIds = new Set(catalogue.valves.map((item) => item.valve_id));
  assert.equal(multi.asset_ids.filter((id) => valveIds.has(id)).length, 2);
  const all = await api("/api/v1/gas/engineering/assets?limit=1&offset=0");
  assert.equal(all.total, 204);
  if (before) {
    for (const [width, height] of [[1920, 1080], [375, 812]]) await check(`before ${width}x${height}`, async () => {
      const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" }); observe(page, `before-${width}`);
      try {
        await page.goto(origin() + "/#/gas-engineering", { waitUntil: "networkidle" });
        const summary = await page.locator(".gas-resource-strip").innerText();
        assert.match(summary, /200 \/ 共 204/);
        assert.match(summary, /62 阀门/);
        await readEvent(page, multi.event_id);
        assert.equal(await page.locator(".gas-related-list button").count(), 9);
        await readRelated(page, multi.asset_ids.at(-1));
        await shot(page, `before-events-${width}x${height}.png`);
        return { displayedSummary: summary, relatedAssets: 9 };
      } finally { await page.close(); }
    });
    await check("before topology", async () => {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } }); observe(page, "before-topology");
      try { await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await page.locator(".gas-topology-node").first().waitFor();
        assert.equal(await page.locator(".gas-topology-node").count(), 54); await shot(page, "before-topology-1920x1080.png"); }
      finally { await page.close(); }
    });
  } else {
    for (const [width, height] of [[375,812],[1280,720],[1280,800],[1920,1080],[2560,1440],[3840,2160]]) await check(`statistics and nine-asset drill ${width}x${height}`, async () => {
      const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" }); const observed = observe(page, `viewport-${width}x${height}`);
      try {
        await page.goto(origin() + "/#/gas-engineering", { waitUntil: "networkidle" });
        for (const [testId, value] of [["gas-pipe-count",73],["gas-node-count",54],["gas-valve-count",31],["gas-consumer-count",46],["gas-record-total",204]]) {
          assert.equal(await page.getByTestId(testId).innerText(), String(value));
        }
        assert.match(await page.locator(".gas-record-summary").innerText(), /31 个阀门节点/);
        assert.equal(await page.getByTestId("gas-events-status").innerText(), "partial");
        assert.equal(await page.locator(".gas-validation-boundary").getAttribute("data-validation-status"), "not_business_validated");
        await readEvent(page, multi.event_id);
        assert(new URL(page.url()).hash.includes(`/events/${multi.event_id}`));
        assert.equal(await page.locator(".gas-related-list button").count(), 9);
        const ids = width === 1920 ? multi.asset_ids : [multi.asset_ids[0], multi.asset_ids.at(-1)];
        for (const id of ids) {
          await readRelated(page, id);
          assert(new URL(page.url()).hash.includes(`/assets/${id}`));
          assert.equal(await page.getByTestId("gas-asset-record-kind").innerText(), valveIds.has(id) ? "阀门资产" : "管段资产");
        }
        for (const button of await page.getByRole("button", { name: /^定位(资产|事件)$/ }).all()) assert(await button.isDisabled());
        assert.equal(await page.locator(".topbar [aria-current=page]").innerText(), "事件与资产");
        const dimensions = await page.evaluate(() => ({ viewport: innerWidth, document: document.documentElement.scrollWidth }));
        assert(dimensions.document <= width + 1);
        viewportResults.push({ width, height, horizontalOverflow: dimensions.document > width, relatedAssets: 9 });
        await shot(page, `after-events-${width}x${height}.png`);
        if ([375,1920].includes(width)) {
          await page.getByRole("button", { name: "资产目录", exact: true }).click();
          await catalogueReady(page);
          await page.getByRole("button", { name: "末页", exact: true }).click();
          const tail = await catalogueReady(page, 200);
          assert.equal(tail.items.length, 4);
          await page.getByRole("button", { name: `查看资产 ${tail.items.at(-1).asset_id}`, exact: true }).click();
          await page.getByTestId("gas-asset-detail").waitFor();
          await shot(page, `after-directory-${width}x${height}.png`);
        }
        assert.deepEqual(observed.errors, []);
      } finally { await page.close(); }
    });
    await check("pagination, exact-ID lookup, browser back/forward and refresh", async () => {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } }); observe(page, "pagination-history");
      try {
        await page.goto(origin() + "/#/gas-engineering?tab=assets", { waitUntil: "networkidle" });
        await catalogueReady(page);
        await page.getByRole("button", { name: "末页", exact: true }).click();
        const tail = await catalogueReady(page, 200);
        assert.match(await page.getByTestId("gas-asset-page-progress").innerText(), /本页已加载 4 \/ 总记录 204/);
        const id = tail.items.at(-1).asset_id;
        await page.getByRole("button", { name: `查看资产 ${id}`, exact: true }).click();
        await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
        await page.reload({ waitUntil: "networkidle" });
        await catalogueReady(page, 200);
        await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
        await page.goBack(); await catalogueReady(page, 200);
        assert.equal(await page.getByTestId("gas-asset-detail").count(), 0);
        await page.goForward(); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
        await page.getByRole("button", { name: "首页", exact: true }).click(); await catalogueReady(page);
        await page.getByRole("textbox", { name: "查询资产编号", exact: true }).fill(id);
        await page.getByRole("button", { name: "查询详情", exact: true }).click();
        await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
        assert(!new URL(page.url()).hash.includes("page=5"), "lookup is an actual independent detail GET, not a page scan");
        return { tailCount: 4, queriedId: id };
      } finally { await page.close(); }
    });
    await check("raw valve filter keeps total while record origins stay separate", async () => {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } }); observe(page, "valve-origin-filter");
      try {
        await page.goto(origin() + "/#/gas-engineering?tab=assets", { waitUntil: "networkidle" }); await catalogueReady(page);
        await page.getByRole("combobox", { name: "筛选接口原始类型" }).selectOption("valve");
        const first = await catalogueReady(page, 0, "valve"); assert.equal(first.total, 62);
        assert.equal(await page.locator('tr[data-record-kind="valve_asset"]').count(), 31);
        assert.equal(await page.getByTestId("gas-valve-count").innerText(), "31");
        await page.getByRole("button", { name: "末页", exact: true }).click(); await catalogueReady(page, 50, "valve");
        assert.equal(await page.locator('tr[data-record-kind="topology_node"]').count(), 12);
        const nodeId = catalogue.nodes.find((node) => node.node_type === "valve" && !first.items.some((item) => item.asset_id === node.node_id))?.node_id;
        assert(nodeId); await page.getByRole("button", { name: `查看资产 ${nodeId}`, exact: true }).click();
        await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${nodeId}"]`).waitFor();
        assert.equal(await page.getByTestId("gas-asset-record-kind").innerText(), "拓扑节点");
        assert.equal(await page.getByTestId("gas-record-total").innerText(), "204");
        await shot(page, "after-valve-record-distinction.png");
        return { rawValveTypeRecords: 62, valveAssets: 31, valveTopologyNodes: 31 };
      } finally { await page.close(); }
    });
    await check("rapid filters and pages cannot overwrite current selection", async () => {
      const page = await browser.newPage(); observe(page, "rapid-navigation");
      try {
        await page.goto(origin() + "/#/gas-engineering?tab=assets", { waitUntil: "networkidle" }); await catalogueReady(page);
        await page.getByRole("button", { name: "下一页", exact: true }).click();
        await page.getByRole("button", { name: "下一页", exact: true }).click(); await catalogueReady(page, 100);
        const select = page.getByRole("combobox", { name: "筛选接口原始类型" });
        await select.selectOption("consumer"); await select.selectOption("pipe"); await select.selectOption("valve");
        await catalogueReady(page, 0, "valve");
        const id = catalogue.valves[0].valve_id;
        await page.getByRole("button", { name: `查看资产 ${id}`, exact: true }).click();
        await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor(); await page.waitForLoadState("networkidle");
        assert.equal(await page.getByTestId("gas-asset-detail").getAttribute("data-asset-id"), id);
        assert.equal(await page.getByTestId("gas-valve-count").innerText(), "31");
      } finally { await page.close(); }
    });
    await check("direct nested and compatibility routes restore nine-asset context", async () => {
      const page = await browser.newPage(); observe(page, "direct-routes");
      try {
        const id = multi.asset_ids.at(-1);
        for (const prefix of ["gas-engineering", "gas"]) {
          await page.goto(origin() + `/#/${prefix}/events/${multi.event_id}/assets/${id}`, { waitUntil: "networkidle" });
          await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
          assert.equal(await page.locator(".gas-related-list button").count(), 9);
          await page.reload({ waitUntil: "networkidle" });
          await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
        }
      } finally { await page.close(); }
    });
    await check("gas routes skip water bootstrap and TopBar preserves both entries", async () => {
      const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } }); const observed = observe(page, "bootstrap-isolation");
      try {
        await page.goto(origin() + "/?mode=platform&datasetId=must-not-bootstrap#/gas-engineering", { waitUntil: "networkidle" });
        assert.equal(await page.getByTestId("gas-valve-count").innerText(), "31");
        await page.getByRole("button", { name: "燃气中控", exact: true }).click(); await page.locator(".gas-topology-node").first().waitFor();
        assert.equal(await page.locator(".gas-topology-node").count(), 54);
        assert.equal(await page.locator(".gas-node-valve").count(), 31);
        assert.match(await page.locator(".gas-cockpit-summary").innerText(), /阀门资产\s+31/);
        await shot(page, "after-topology-1920x1080.png");
        await page.getByRole("button", { name: "事件与资产", exact: true }).click();
        await page.getByTestId("gas-record-total").waitFor(); await page.waitForLoadState("networkidle");
        assert(observed.paths.length > 0 && observed.paths.every((path) => path.startsWith("/api/v1/gas/")), JSON.stringify(observed.paths));
        assert.deepEqual(observed.errors, []);
      } finally { await page.close(); }
    });
    for (const [offset, code, status] of [[1,"unauthenticated",401],[2,"forbidden",403]]) await check(`actual ACL ${status}`, async () => {
      const page = await browser.newPage(); observe(page, `http-${status}`);
      try { await page.goto(origin(offset) + "/#/gas-engineering", { waitUntil: "networkidle" });
        await page.locator(`[data-error-code="${code}"]`).first().waitFor();
        assert.equal(await page.getByTestId("gas-record-total").innerText(), "—");
        assert.equal(await page.locator(".gas-source-badge strong").innerText(), "unknown");
        await shot(page, `after-error-${status}.png`);
      } finally { await page.close(); }
    });
    await check("actual missing asset 404 and malformed route are explicit", async () => {
      const page = await browser.newPage(); observe(page, "missing-invalid-route");
      try { await page.goto(origin() + "/#/gas-engineering/assets/syn-gas-unknown-fix3", { waitUntil: "networkidle" });
        await page.locator('#gas-asset-panel [data-error-code="not_found"]').waitFor();
        assert.equal(await page.getByTestId("gas-asset-detail").count(), 0);
        await shot(page, "after-error-404.png");
        await page.goto(origin() + "/#/gas-engineering/events/%E0%A4%A", { waitUntil: "networkidle" });
        assert.match(await page.getByRole("alert").innerText(), /无法识别/);
      } finally { await page.close(); }
    });
    await check("offline asset transition clears old detail and retry reads actual API", async () => {
      const context = await browser.newContext();
      try { const page = await context.newPage(); observe(page, "offline-recovery");
        await page.goto(origin() + "/#/gas-engineering", { waitUntil: "networkidle" }); await readEvent(page, multi.event_id); await readRelated(page, multi.asset_ids[0]);
        await context.setOffline(true);
        await page.getByRole("button", { name: `打开资产详情 ${multi.asset_ids.at(-1)}`, exact: true }).click();
        await page.locator('#gas-asset-panel [data-error-code="network"]').waitFor();
        assert.equal(await page.getByTestId("gas-asset-detail").count(), 0);
        await context.setOffline(false); await page.getByRole("button", { name: "重试读取资产", exact: true }).click();
        await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${multi.asset_ids.at(-1)}"]`).waitFor();
      } finally { await context.close(); }
    });
  }
} finally {
  await Promise.allSettled(pending); await browser.close();
  const files = execFileSync("git", ["-C", "..", "ls-files", "--cached", "--others", "--exclude-standard", "--", "web/src", "web/scripts"], { encoding: "utf8" }).trim().split("\n").filter(Boolean);
  const evidence = { baseline: "efaa5eeed86d9535def36db81e5fc14420f2d8b4", stage, generatedAt: new Date().toISOString(),
    businessApiInterception: false, data: "GAS-M0 synthetic candidate; actual controlled FastAPI via loopback-only test proxy",
    case: catalogue ? { counts: catalogue.counts, status: catalogue.status, data_mode: catalogue.data_mode, validation_status: catalogue.validation_status, evidence: catalogue.evidence } : null,
    checks, viewportResults, responses, screenshots,
    sourceFiles: Object.fromEntries(await Promise.all(files.map(async (path) => [path, hash(await readFile(resolve("..", path)))]))),
    buildFiles: Object.fromEntries(await Promise.all(["dist/index.html", ...(await readdir("dist/assets")).filter((name) => /^index-.*\.(js|css)$/.test(name)).map((name) => `dist/assets/${name}`)].map(async (path) => [path, hash(await readFile(path))]))),
    limitations: ["Acceptance proxy is not a production BFF", "No authorized GIS; no new risk scores", "Backend ACL and fixture files unchanged"] };
  await writeFile(resolve(dir, `${stage}-evidence.json`), JSON.stringify(evidence, null, 2) + "\n");
  console.log(JSON.stringify({ stage, checks, viewportResults, responses: responses.length, screenshots: screenshots.length }, null, 2));
}
if (!checks.length || checks.some((item) => !item.ok)) process.exitCode = 1;
