import assert from "node:assert/strict";
import { mkdir, readFile, writeFile, readdir, stat } from "node:fs/promises";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { chromium } from "playwright";
const base = new URL(process.env.GAS_UI_BASE_URL ?? "http://127.0.0.1:23473");
assert(["127.0.0.1", "localhost"].includes(base.hostname));
const origin = (offset = 0) => { const url = new URL(base); url.port = String(Number(base.port) + offset); return url.origin; };
const dir = resolve("docs/evidence/gas-cockpit-unify");
await mkdir(dir, { recursive: true });
const hash = (data) => createHash("sha256").update(data).digest("hex");
const proof = { baseline: "0940740db1743c49f640f6cdfc1ea5516559edb7", phase: "after", actualTcp: false, businessApiInterception: false, checks: [], responses: [], screenshots: [], geometry: [], windows: [], playbackRounds: [] };
const pending = [];
let browser;
const check = async (name, run) => { try { const detail = await run(); proof.checks.push({ name, ok: true, detail }); } catch (error) { proof.checks.push({ name, ok: false, error: String(error) }); } };
function observe(page, phase) {
  const errors = [], paths = [];
  let cases = 0;
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  page.on("request", (request) => { const url = new URL(request.url()); if (url.pathname.startsWith("/api/")) paths.push(url.pathname); });
  page.on("response", (response) => {
    const url = new URL(response.url()); if (!url.pathname.startsWith("/api/")) return;
    if (url.pathname.includes("/gas/cases/") && response.status() === 200) cases++;
    pending.push((async () => { let body; try { body = await response.json(); } catch { body = null; }
      const summary = body?.counts ? { status: body.status, data_mode: body.data_mode, validation_status: body.validation_status, case_id: body.case_id, network_id: body.network_id, counts: body.counts, evidence: body.evidence }
        : Array.isArray(body?.items) ? { status: body.status, data_mode: body.data_mode, validation_status: body.validation_status, total: body.total, loaded: body.items.length,
          ...(body.items[0]?.asset_id ? { ids: body.items.map((item) => item.asset_id) } : {}), ...(body.items[0]?.event_id ? { events: body.items.map(({ event_id, asset_ids }) => ({ event_id, asset_ids })) } : {}) } : body;
      proof.responses.push({ phase, method: response.request().method(), path: url.pathname + url.search, status: response.status(), body: summary });
    })());
  });
  return { errors, paths, get cases() { return cases; } };
}
async function shot(page, name) {
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: resolve(dir, name), fullPage: true });
  proof.screenshots.push({ name, sha256: hash(await readFile(resolve(dir, name))) });
}
async function api(path, offset = 0, status = 200) {
  const response = await fetch(origin(offset) + path, { signal: AbortSignal.timeout(25000) });
  assert.equal(response.status, status, path); return response.json();
}
async function ready(page) { await page.getByTestId("gas-record-total").filter({ hasText: "204" }).waitFor(); await page.locator(".gas-topology-node").first().waitFor(); }
async function related(page, id) {
  await page.getByRole("button", { name: `打开资产详情 ${id}`, exact: true }).click();
  await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
}
async function openEvent(page, id) {
  await page.locator(".gas-event-list button").filter({ hasText: id }).click();
  await page.locator(`[data-testid=gas-event-detail][data-event-id="${id}"]`).waitFor();
}
async function assetPageReady(page, offset = 0, type) {
  const expected = await api(`/api/v1/gas/engineering/assets?limit=50&offset=${offset}` + (type ? `&asset_type=${type}` : ""));
  await page.waitForFunction(({ first, count }) => { const rows = [...document.querySelectorAll(".gas-asset-directory tbody tr")]; return rows.length === count && (!first || rows[0]?.getAttribute("data-asset-id") === first); }, { first: expected.items[0]?.asset_id, count: expected.items.length });
  assert.deepEqual(await page.locator(".gas-asset-directory tbody tr").evaluateAll((rows) => rows.map((row) => row.getAttribute("data-asset-id"))), expected.items.map((item) => item.asset_id));
  return expected;
}
async function openControl(page, id) {
  const menu = page.locator(".gas-window-manager");
  if (!(await menu.getAttribute("open"))) { if ((await menu.getAttribute("open")) === null) await menu.locator("summary").click(); }
  await page.locator(`[data-window-control="${id}"]`).click();
  if (await page.getByRole("dialog").count() === 0 && await menu.getAttribute("open") !== null) await menu.locator("summary").click();
}
try {
  const deadline = Date.now() + 250000;
  while (Date.now() < deadline) {
    try { if ((await fetch(origin() + "/health", { signal: AbortSignal.timeout(1500) })).status === 200) { proof.actualTcp = true; break; } } catch {}
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  if (!proof.actualTcp) throw new Error("BLOCKED_BY_ENV: actual FastAPI TCP is unavailable");
  proof.gateway = await (await fetch(origin() + "/__acceptance/meta")).json();
  const value = await api("/api/v1/gas/cases/syn-gas-case-K-001");
  const events = await api("/api/v1/gas/engineering/events");
  const multi = events.items.find((event) => event.asset_ids.length === 9);
  assert(multi, "nine actual associated assets required");
  const valveIds = new Set(value.valves.map((item) => item.valve_id));
  assert.equal(multi.asset_ids.filter((id) => valveIds.has(id)).length, 2);
  proof.case = { case_id: value.case_id, counts: value.counts, evidence: value.evidence, validation_status: value.validation_status };
  browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });

  for (const [width, height] of [[375,812],[1280,720],[1280,800],[1920,1080],[2560,1440],[3840,2160]]) await check(`single cockpit, topology and selection ${width}x${height}`, async () => {
    const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" }); const observed = observe(page, `viewport-${width}x${height}`);
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page);
      assert.equal(await page.locator(".gas-topology-node").count(), 54);
      assert.equal(await page.locator(".gas-topology-edge").count(), 73);
      assert.equal(await page.getByTestId("gas-valve-count").innerText(), "31");
      assert.equal(await page.getByTestId("gas-node-count").innerText(), "54");
      assert.equal(await page.getByTestId("gas-record-total").innerText(), "204");
      assert.equal(await page.locator(".gas-window[data-window-id]").count(), 5, "default must not spread all eleven windows across the screen");
      await shot(page, `after-cockpit-${width}x${height}.png`);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      const dimensions = await page.evaluate(() => { const map = document.querySelector('[data-testid="gas-central-view"]').getBoundingClientRect(), work = document.querySelector('[data-testid="gas-workspace"]').getBoundingClientRect(); return { viewport: innerWidth, width: map.width, height: map.height, workspaceHeight: work.height, widthRatio: map.width / innerWidth, heightRatio: map.height / work.height }; });
      proof.geometry.push({ width, height, mode: "overview", layout: "command", ...dimensions });
      if (width === 1920) { assert(dimensions.widthRatio >= .55, "central width below 55% viewport"); assert(dimensions.heightRatio >= .55, "central height below 55% workspace"); }
      await openEvent(page, multi.event_id);
      assert.equal(await page.locator(".gas-related-list button").count(), 9);
      await page.waitForFunction(() => document.querySelectorAll(".gas-topology-edge.is-selected").length === 7 && document.querySelectorAll(".gas-topology-node.is-selected").length === 2);
      if (width === 1920) await shot(page, "after-event-nine-assets-1920.png");
      for (const id of width === 1920 ? multi.asset_ids : [multi.asset_ids[0], multi.asset_ids.at(-1)]) {
        await related(page, id);
        assert(new URL(page.url()).hash.includes(`/gas/events/${multi.event_id}/assets/${id}`));
        assert.equal(await page.getByTestId("gas-asset-record-kind").innerText(), valveIds.has(id) ? "阀门资产" : "管段资产");
        assert.equal(await page.locator(".gas-inspector").getAttribute("data-case-identity"), await page.getByTestId("gas-page").getAttribute("data-case-identity"));
      }
      await page.getByRole("button", { name: "定位到合成拓扑", exact: true }).click();
      for (const button of await page.getByRole("button", { name: /^定位(资产|事件)$/ }).all()) assert(await button.isDisabled(), "GIS location must stay disabled");
      if (width === 1920) await shot(page, "after-valve-inspector-1920.png");
      assert.equal(observed.cases, 1, "one Provider must load one completed case, not another case per detail");
      assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  await check("three modes and three layouts preserve central-view geometry", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce" }); const observed = observe(page, "mode-layouts");
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page);
      for (const [mode, name] of [["overview","综合态势"],["risk","风险分析"],["emergency","应急推演"]]) for (const [layout, label] of [["command","指挥布局"],["analysis","分析布局"],["compact","紧凑布局"]]) {
        await page.getByRole("button", { name, exact: true }).click(); await page.getByRole("button", { name: label, exact: true }).click();
        await page.waitForFunction(({ mode, layout }) => { const root = document.querySelector('[data-testid="gas-page"]'); return root?.getAttribute("data-mode") === mode && root?.getAttribute("data-layout") === layout; }, { mode, layout });
        const geometry = await page.evaluate(() => { const map = document.querySelector('[data-testid="gas-central-view"]').getBoundingClientRect(), workspace = document.querySelector('[data-testid="gas-workspace"]').getBoundingClientRect(); return { width: map.width, height: map.height, viewport: innerWidth, workspaceHeight: workspace.height, widthRatio: map.width / innerWidth, heightRatio: map.height / workspace.height }; });
        proof.geometry.push({ mode, layout, ...geometry }); assert(geometry.widthRatio >= .55); assert(geometry.heightRatio >= .55);
        if (mode === "risk") assert.match(await page.locator(".gas-cockpit-event-panel").innerText(), /unavailable/);
        if (mode === "emergency") assert.match(await page.locator(".gas-cockpit-event-panel").innerText(), /给定隔离/);
        if ((mode === "risk" && layout === "analysis") || (mode === "emergency" && layout === "compact")) await shot(page, `after-${mode}-${layout}-1920.png`);
      }
      assert.equal(observed.cases, 1); assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  await check("all eleven windows open, hide and restore without another case or stale layer state", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce" }); const observed = observe(page, "eleven-windows");
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page);
      const ids = await page.locator("[data-window-control]").evaluateAll((nodes) => nodes.map((node) => node.getAttribute("data-window-control")));
      assert.equal(ids.length, 11);
      for (const id of ids) {
        await openControl(page, id);
        const window = page.locator(`.gas-window[data-window-id="${id}"]`); await window.waitFor();
        const title = await window.locator("h2").innerText();
        if (id === "scenario") {
          await window.locator(".gas-scenario-conditions summary").click();
          const text = await window.locator(".gas-scenario-conditions").innerText();
          assert(text.includes(value.scenario_id) && text.includes(value.plan_id));
          assert(text.includes(String(value.analysis_window_hours)));
          await window.locator(".gas-scenario-conditions summary").click();
        }
        if (id === "boundary") {
          const text = await window.innerText();
          assert(text.includes(value.scoring.p_semantics) && text.includes(value.scoring.coverage_semantics));
          assert(text.includes(value.scoring.h_status));
        }
        if (id === "layers") { await window.getByLabel("管段基础关系", { exact: true }).uncheck(); assert.equal(await page.locator(".gas-topology-edge").count(), 0); await window.getByLabel("管段基础关系", { exact: true }).check(); assert.equal(await page.locator(".gas-topology-edge").count(), 73); }
        await window.getByRole("button", { name: `隐藏${title}`, exact: true }).click();
        assert.equal(await page.locator(`.gas-window[data-window-id="${id}"]`).count(), 0);
        await openControl(page, id); await page.locator(`.gas-window[data-window-id="${id}"]`).waitFor();
        proof.windows.push({ id, title, opened: true, hidden: true, restored: true });
        if (await page.getByRole("dialog").count()) await page.getByRole("button", { name: "关闭窗口", exact: true }).click();
        const menu = page.locator(".gas-window-manager"); if (await menu.getAttribute("open") !== null) await menu.locator("summary").click();
      }
      await page.getByRole("button", { name: "恢复全部窗口", exact: true }).click();
      assert.equal(await page.locator(".gas-window[data-window-id]").count(), 5);
      await page.locator(".gas-window-manager summary").click(); await shot(page, "after-eleven-window-manager.png");
      assert.equal(observed.cases, 1); assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  await check("topology zoom, pan, direct selection and window drag retain real detail linkage", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } }); const observed = observe(page, "topology-interaction");
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page);
      const view = page.getByTestId("gas-viewport-transform"); const initial = await view.getAttribute("transform");
      await page.getByRole("button", { name: "放大合成拓扑", exact: true }).click(); assert(Number(await view.getAttribute("data-zoom")) > 1);
      await page.getByRole("button", { name: "适配视图", exact: true }).click();
      const svg = page.getByRole("img", { name: "燃气合成管网拓扑图", exact: true }); const box = await svg.boundingBox(); assert(box);
      await page.mouse.move(box.x + 15, box.y + 15); await page.mouse.down(); await page.mouse.move(box.x + 90, box.y + 55, { steps: 6 }); await page.mouse.up();
      assert.notEqual(await view.getAttribute("transform"), initial);
      await svg.hover(); const zoom = Number(await view.getAttribute("data-zoom")); await page.mouse.wheel(0, -120); await page.waitForFunction((old) => Number(document.querySelector('[data-testid="gas-viewport-transform"]').getAttribute("data-zoom")) !== old, zoom);
      await page.getByRole("button", { name: "适配视图", exact: true }).click();
      const pipe = value.pipes[0].pipe_id;
      await page.locator(`g[data-asset-id="${pipe}"] .gas-topology-edge-hit`).click({ force: true });
      await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${pipe}"]`).waitFor();
      await page.getByRole("button", { name: "适配视图", exact: true }).click();
      const valve = value.valves[0]; await page.locator(`g[data-node-id="${valve.node_id}"]`).click({ force: true });
      await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${valve.valve_id}"]`).waitFor();
      const schematic = page.getByTestId("gas-schematic-context");
      await schematic.locator("summary").click();
      assert((await schematic.innerText()).includes(valve.node_id));
      assert((await schematic.innerText()).includes("px"));
      assert.equal(await schematic.getAttribute("data-source-case-id"), value.case_id);
      const heading = page.locator('[data-window-id="topology"] .gas-window-head'); const rect = await heading.boundingBox();
      await page.mouse.move(rect.x + 80, rect.y + 12); await page.mouse.down(); await page.mouse.move(rect.x + 105, rect.y + 27); await page.mouse.up();
      assert.notEqual(await page.locator('[data-window-id="topology"]').getAttribute("style"), "transform: translate(0px, 0px);");
      await page.getByRole("button", { name: "恢复全部窗口", exact: true }).click();
      assert.equal(observed.cases, 1); assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  await check("204-record pagination, last four, cross-page lookup and history/refresh", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce" }); const observed = observe(page, "asset-pagination");
    try {
      await page.goto(origin() + "/#/gas/assets", { waitUntil: "networkidle" }); await ready(page); await assetPageReady(page);
      await page.getByRole("button", { name: "末页", exact: true }).click(); const tail = await assetPageReady(page, 200); assert.equal(tail.total, 204); assert.equal(tail.items.length, 4);
      const id = tail.items.at(-1).asset_id;
      await page.getByRole("button", { name: `查看资产 ${id}`, exact: true }).click(); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
      await shot(page, "after-directory-last-page-1920.png");
      await page.reload({ waitUntil: "networkidle" }); await assetPageReady(page, 200); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
      await page.goBack(); await assetPageReady(page, 200); assert.equal(await page.getByTestId("gas-asset-detail").count(), 0);
      await page.goForward(); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
      await page.getByRole("button", { name: "首页", exact: true }).click(); await assetPageReady(page);
      await page.getByRole("textbox", { name: "查询资产编号", exact: true }).fill(id); await page.getByRole("button", { name: "查询详情", exact: true }).click(); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor();
      await page.getByRole("combobox", { name: "筛选接口原始类型" }).selectOption("valve"); const first = await assetPageReady(page, 0, "valve"); assert.equal(first.total, 62); assert.equal(await page.getByTestId("gas-valve-count").innerText(), "31");
      assert.equal(await page.locator('tr[data-record-kind="valve_asset"]').count(), 31);
      await page.getByRole("button", { name: "末页", exact: true }).click(); await assetPageReady(page, 50, "valve"); assert.equal(await page.locator('tr[data-record-kind="topology_node"]').count(), 12);
      await page.getByRole("combobox", { name: "筛选接口原始类型" }).selectOption("consumer"); await page.getByRole("combobox", { name: "筛选接口原始类型" }).selectOption("pipe"); await assetPageReady(page, 0, "pipe");
      assert.deepEqual(observed.errors, []); return { tail: 4, queried: id, rawValveRecords: 62, valveAssetKpi: 31 };
    } finally { await page.close(); }
  });
  await check("canonical root, legacy aliases, nested direct links and no water bootstrap", async () => {
    const page = await browser.newPage(); const observed = observe(page, "routing-bootstrap");
    try {
      for (const hash of ["", "#/gas-control-room", "#/gas-engineering"]) { await page.goto(origin() + "/?mode=platform&datasetId=must-not-bootstrap" + hash, { waitUntil: "networkidle" }); await ready(page); assert.equal(new URL(page.url()).hash, "#/gas"); }
      const id = multi.asset_ids.at(-1);
      for (const prefix of ["gas", "gas-engineering"]) { await page.goto(origin() + `/#/${prefix}/events/${multi.event_id}/assets/${id}`, { waitUntil: "networkidle" }); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor(); assert(new URL(page.url()).hash.startsWith("#/gas/events/")); await page.reload({ waitUntil: "networkidle" }); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${id}"]`).waitFor(); }
      assert(observed.paths.every((path) => path.startsWith("/api/v1/gas/")), JSON.stringify(observed.paths));
      assert.equal(await page.locator(".main-nav button").filter({ hasText: "燃气中控" }).count(), 1); assert.equal(await page.locator(".main-nav button").filter({ hasText: "事件与资产" }).count(), 0);
      assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  await check("event kind/state filters survive reload and browser history without another case owner", async () => {
    const page = await browser.newPage(); const observed = observe(page, "event-filter-history");
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page);
      await page.getByRole("combobox", { name: "筛选合成事件类型" }).selectOption("given_isolation");
      await page.waitForFunction(() => document.querySelectorAll(".gas-event-list button").length === 1);
      await page.getByRole("combobox", { name: "筛选合成事件状态" }).selectOption("synthetic_given");
      assert(new URL(page.url()).hash.includes("eventKind=given_isolation"));
      await page.waitForFunction(() => location.hash.includes("eventState=synthetic_given"));
      assert.equal(observed.cases, 1);
      await page.reload({ waitUntil: "networkidle" }); await ready(page);
      assert.equal(await page.getByRole("combobox", { name: "筛选合成事件类型" }).inputValue(), "given_isolation");
      assert.equal(await page.getByRole("combobox", { name: "筛选合成事件状态" }).inputValue(), "synthetic_given");
      assert.equal(await page.locator(".gas-event-list button").count(), 1);
      await page.goBack();
      await page.waitForFunction(() => document.querySelector('select[aria-label="筛选合成事件状态"]').value === "all");
      await page.goBack();
      await page.waitForFunction(() => document.querySelectorAll(".gas-event-list button").length === 3);
      assert.equal(observed.cases, 2, "reload adds one case; filter changes and history add none");
      assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  await check("GIS, risk and assistant entries retain truthful unavailable/context states", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } }); const observed = observe(page, "legal-capabilities");
    try {
      await page.goto(origin() + "/#/gas/analysis", { waitUntil: "networkidle" }); await ready(page);
      await page.getByTestId("gas-risk-unavailable").waitFor(); assert.match(await page.getByTestId("gas-risk-unavailable").innerText(), /不生成示例排行或演示分数/); await shot(page, "after-risk-unavailable.png");
      await page.getByRole("button", { name: "重庆GIS · 未就绪", exact: true }).click(); await page.getByTestId("gas-gis-unavailable").waitFor(); assert.equal(await page.locator("iframe, .amap-container").count(), 0); await shot(page, "after-gis-unavailable.png");
      await page.getByRole("button", { name: "返回GAS-M0合成拓扑", exact: true }).click(); await ready(page);
      await page.getByRole("button", { name: /燃气智能体入口/ }).click(); await page.getByTestId("gas-assistant-state").waitFor(); assert.match(await page.getByTestId("gas-assistant-state").innerText(), /context_only/); assert(await page.getByRole("button", { name: "发送给燃气智能体（未就绪）", exact: true }).isDisabled()); await shot(page, "after-assistant-context-only.png");
      assert(observed.paths.every((path) => path.startsWith("/api/v1/gas/"))); assert.deepEqual(observed.errors, []);
    } finally { await page.close(); }
  });
  for (const [offset, status, code] of [[1,401,"unauthenticated"],[2,403,"forbidden"],[3,503,"unavailable"]]) await check(`actual backend HTTP ${status}`, async () => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 800 } }); observe(page, `http-${status}`);
    try {
      const actual = await fetch(origin(offset) + "/api/v1/gas/engineering/events"); assert.equal(actual.status, status, "gateway connection error must not be counted as fixture 503");
      const body = await actual.json(); assert(!JSON.stringify(body).match(/Traceback|\/workdata|secret|bearer/i));
      await page.goto(origin(offset) + "/#/gas", { waitUntil: "networkidle" }); await page.locator(`[data-error-code="${code}"]`).first().waitFor(); assert.equal(await page.getByTestId("gas-record-total").innerText(), "—");
      assert.equal(await page.locator("[data-window-control]").count(), 11); assert.equal(await page.locator(".gas-window[data-window-id]").count(), 5); await shot(page, `after-http-${status}.png`);
      return { source: status === 503 ? "actual Codex fixture error boundary, not proxy-generated status" : "actual controlled backend authentication/dataset ACL", body };
    } finally { await page.close(); }
  });
  await check("404 and offline/recovery never retain the previous asset", async () => {
    const context = await browser.newContext();
    try {
      const page = await context.newPage(); observe(page, "missing-and-offline");
      await page.goto(origin() + "/#/gas/assets/syn-gas-missing-unify", { waitUntil: "networkidle" }); await page.locator('[data-error-code="not_found"]').waitFor(); assert.equal(await page.getByTestId("gas-asset-detail").count(), 0); await shot(page, "after-http-404.png");
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page); await openEvent(page, multi.event_id); await related(page, multi.asset_ids[0]);
      await context.setOffline(true); await page.getByRole("button", { name: `打开资产详情 ${multi.asset_ids.at(-1)}`, exact: true }).click(); await page.locator('[data-error-code="network"]').waitFor(); assert.equal(await page.getByTestId("gas-asset-detail").count(), 0);
      await context.setOffline(false); await page.getByRole("button", { name: "重试读取资产", exact: true }).click(); await page.locator(`[data-testid=gas-asset-detail][data-asset-id="${multi.asset_ids.at(-1)}"]`).waitFor();
    } finally { await context.close(); }
  });
  await check("ten real-clock playback rounds and reduced-motion lifecycle are stable", async () => {
    const context = await browser.newContext({ viewport: { width: 1920, height: 1080 }, reducedMotion: "no-preference" });
    await context.addInitScript(() => { const set = window.setInterval.bind(window), clear = window.clearInterval.bind(window), active = new Set(); let made = 0, removed = 0;
      window.setInterval = (...args) => { const id = set(...args); active.add(id); made++; return id; };
      window.clearInterval = (id) => { if (active.delete(id)) removed++; return clear(id); };
      window.__gasTimerProbe = { active: () => active.size, made: () => made, removed: () => removed };
    });
    try {
      const page = await context.newPage(); const observed = observe(page, "ten-playback-rounds"); await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" }); await ready(page);
      const controls = page.locator('[data-window-id="timeline"]');
      for (let round = 1; round <= 10; round++) {
        await controls.getByRole("button", { name: "重置", exact: true }).click(); await controls.getByRole("button", { name: "播放", exact: true }).click();
        await page.waitForFunction(() => document.querySelector('[data-testid="gas-timeline"]').getAttribute("data-playing") === "true");
        assert.equal(await page.evaluate(() => window.__gasTimerProbe.active()), 1);
        await page.waitForFunction(() => { const item = document.querySelector('[data-testid="gas-timeline"]'); return item.getAttribute("data-index") === "2" && item.getAttribute("data-playing") === "false" && window.__gasTimerProbe.active() === 0; }, null, { timeout: 12000 });
        proof.playbackRounds.push({ round, endIndex: 2, activeIntervalsAtEnd: await page.evaluate(() => window.__gasTimerProbe.active()), actualClock: true });
      }
      await controls.getByRole("button", { name: "重置", exact: true }).click(); await controls.getByRole("button", { name: "播放", exact: true }).click(); await page.waitForFunction(() => window.__gasTimerProbe.active() === 1);
      await page.emulateMedia({ reducedMotion: "reduce" }); await page.waitForFunction(() => window.__gasTimerProbe.active() === 0); assert(await controls.getByRole("button", { name: "播放", exact: true }).isDisabled());
      await controls.locator(".gas-timeline-event").last().click(); assert.equal(await page.getByTestId("gas-timeline").getAttribute("data-index"), "2");
      await page.emulateMedia({ reducedMotion: "no-preference" }); assert.equal(await page.evaluate(() => window.__gasTimerProbe.active()), 0);
      await shot(page, "after-ten-rounds-timeline.png"); assert.equal(observed.cases, 1); assert.deepEqual(observed.errors, []);
      return await page.evaluate(() => ({ made: window.__gasTimerProbe.made(), cleared: window.__gasTimerProbe.removed(), active: window.__gasTimerProbe.active() }));
    } finally { await context.close(); }
  });
} catch (error) {
  proof.gate = proof.actualTcp ? "FAILED" : "BLOCKED_BY_ENV"; proof.error = String(error); process.exitCode = 1;
} finally {
  await Promise.allSettled(pending); if (browser) await browser.close();
  const candidates = execFileSync("git", ["-C", "..", "ls-files", "--cached", "--others", "--exclude-standard", "--", "web/src", "web/scripts", "web/package.json"], { encoding: "utf8" }).trim().split("\n").filter(Boolean);
  const files = []; for (const path of candidates) { try { if ((await stat(resolve("..", path))).isFile()) files.push(path); } catch (error) { if (error.code !== "ENOENT") throw error; } }
  proof.sourceFiles = Object.fromEntries(await Promise.all(files.map(async (path) => [path, hash(await readFile(resolve("..", path)))])));
  proof.deletedSources = execFileSync("git", ["-C", "..", "diff", "0940740db1743c49f640f6cdfc1ea5516559edb7", "--name-only", "--diff-filter=D", "--", "web/src"], { encoding: "utf8" }).trim().split("\n").filter(Boolean);
  proof.buildFiles = Object.fromEntries(await Promise.all(["dist/index.html", ...(await readdir("dist/assets")).filter((name) => /^index-.*\.(js|css)$/.test(name)).map((name) => `dist/assets/${name}`)].map(async (path) => [path, hash(await readFile(path))])));
  proof.gate ??= proof.checks.length && proof.checks.every((item) => item.ok) ? "PASS" : "FAILED";
  await writeFile(resolve(dir, "after-evidence.json"), JSON.stringify(proof, null, 2) + "\n");
  console.log(JSON.stringify({ gate: proof.gate, checks: proof.checks, geometry: proof.geometry, windows: proof.windows, playbackRounds: proof.playbackRounds, responses: proof.responses.length, screenshots: proof.screenshots.length }, null, 2));
}
if (proof.gate !== "PASS") process.exitCode = 1;
