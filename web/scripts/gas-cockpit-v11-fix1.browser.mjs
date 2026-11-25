import { chromium } from "playwright";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";

const base = process.env.GAS_BASE_URL ?? "http://127.0.0.1:4193";
const executablePath = process.env.BROWSER_EXECUTABLE || undefined;
const fixture = JSON.parse(await readFile(resolve("src/tests/fixtures/gasCaseResponse.json"), "utf8"));
const evidenceDir = resolve("../docs/evidence/gas-cockpit-v11-fix1");
const results = [];
const errors = [];
const apiRequests = [];

function check(name, condition, detail = "") {
  const ok = Boolean(condition);
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"} ${name}${detail ? ` · ${detail}` : ""}`);
  if (!ok) errors.push(name);
}

async function main() {
  await mkdir(resolve(evidenceDir, "after"), { recursive: true });
  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--no-sandbox", "--enable-precise-memory-info"],
  });
  const page = await browser.newPage({ viewport: { width: 1920, height: 1080 } });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") consoleErrors.push(message.text()); });
  page.on("request", (request) => {
    const url = new URL(request.url());
    if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/agent/") || request.method() !== "GET") {
      apiRequests.push({ method: request.method(), path: url.pathname });
    }
  });
  await page.route("**/api/v1/gas/cases/**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(fixture) }));
  await page.goto(`${base}/#/gas`, { waitUntil: "networkidle" });
  await page.getByTestId("gas-page").waitFor();

  check("54-node synthetic cockpit renders", (await page.getByTestId("gas-page").innerText()).includes("54"));
  check("11 cockpit windows are present", await page.locator(".gas-window").count() === 11, String(await page.locator(".gas-window").count()));
  check("synthetic boundary is visible", (await page.getByTestId("gas-page").innerText()).includes("图通路可达不等同真实供气"));
  check("initial command layout is selected", await page.getByTestId("gas-page").getAttribute("data-layout") === "command");
  check("initial overview mode is selected", await page.getByTestId("gas-page").getAttribute("data-mode") === "overview");

  const geometry = await page.evaluate(() => {
    const rect = (selector) => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const r = node.getBoundingClientRect();
      return { width: r.width, height: r.height, x: r.x, y: r.y, right: r.right, bottom: r.bottom };
    };
    return { viewport: { width: innerWidth, height: innerHeight }, workspace: rect(".gas-cockpit-workspace"), topology: rect(".gas-window-topology"), canvas: rect(".gas-window-topology .gas-map-canvas"), svg: rect(".gas-window-topology svg") };
  });
  const topologyWidthRatio = geometry.topology.width / geometry.viewport.width;
  const topologyHeightRatio = geometry.topology.height / geometry.workspace.height;
  check("central topology meets desktop prominence", topologyWidthRatio >= 0.5 && topologyHeightRatio >= 0.55, JSON.stringify({ topologyWidthRatio, topologyHeightRatio, geometry }));

  const modeGroup = page.locator(".gas-toolbar-group").filter({ hasText: "运行模式" });
  await modeGroup.getByRole("button", { name: "风险分析", exact: true }).click();
  check("risk analysis mode changes focus", await page.getByTestId("gas-page").getAttribute("data-mode") === "risk");
  await modeGroup.getByRole("button", { name: "应急推演", exact: true }).click();
  check("emergency mode changes focus", await page.getByTestId("gas-page").getAttribute("data-mode") === "emergency");

  const layoutGroup = page.locator(".gas-toolbar-group").filter({ hasText: "布局预设" });
  await layoutGroup.getByRole("button", { name: "风险分析", exact: true }).click();
  check("analysis layout preset changes grid", await page.getByTestId("gas-page").getAttribute("data-layout") === "analysis");
  await layoutGroup.getByRole("button", { name: "应急推演", exact: true }).click();
  check("compact emergency layout preset changes grid", await page.getByTestId("gas-page").getAttribute("data-layout") === "compact");

  const timeline = page.locator(".gas-timeline-event");
  await timeline.last().click();
  check("timeline stage selection updates active event", await timeline.last().evaluate((node) => node.classList.contains("is-active")));
  check("timeline keeps the supplied 1h stage", (await page.locator(".gas-cockpit-footer").innerText()).includes("观察阶段"));

  await modeGroup.getByRole("button", { name: "综合态势", exact: true }).click();
  await layoutGroup.getByRole("button", { name: "综合态势", exact: true }).click();
  const topologySvg = page.locator(".gas-window-topology svg");
  const topologyGroup = page.locator(".gas-window-topology svg > g").last();
  const node = page.locator(".gas-topology-node").first();
  await node.click();
  check("node selection opens linked detail", (await page.locator(".gas-window-selected").innerText()).includes("坐标"));
  const valve = page.locator(".gas-node-valve").first();
  await valve.click({ force: true });
  check("valve selection keeps linked detail", (await page.locator(".gas-window-selected").innerText()).includes("阀门节点"));
  const edge = page.locator(".gas-topology-edge-hit").first();
  await edge.click({ force: true, timeout: 5000 });
  check("edge selection updates linked detail", (await page.locator(".gas-window-selected").innerText()).includes("方向"));

  const beforeZoom = await topologyGroup.getAttribute("transform");
  const svgBox = await topologySvg.boundingBox();
  if (svgBox) {
    await page.mouse.move(svgBox.x + svgBox.width / 2, svgBox.y + svgBox.height / 2);
    await page.mouse.wheel(0, -240);
  }
  await page.waitForTimeout(100);
  const afterZoom = await topologyGroup.getAttribute("transform");
  check("topology wheel zoom changes view transform", beforeZoom !== afterZoom, `${beforeZoom} -> ${afterZoom}`);
  const beforePan = afterZoom;
  const background = await page.locator(".gas-map-background").boundingBox();
  if (background) {
    await page.mouse.move(background.x + 10, background.y + 10);
    await page.mouse.down();
    await page.mouse.move(background.x + 70, background.y + 60);
    await page.mouse.up();
  }
  await page.waitForTimeout(100);
  const afterPan = await topologyGroup.getAttribute("transform");
  check("topology blank-space drag changes pan transform", beforePan !== afterPan, `${beforePan} -> ${afterPan}`);
  await page.getByRole("button", { name: "适配视图", exact: true }).click();
  check("topology fit-to-view resets transform", (await topologyGroup.getAttribute("transform")) === "translate(0 0) scale(1)");

  const play = page.locator(".gas-timeline-play");
  await play.click();
  check("timeline playback enters pause state", await play.innerText() === "暂停");
  await page.waitForTimeout(250);
  await play.click();
  check("timeline pause returns to play state", await play.innerText() === "播放");
  await timeline.first().click();
  await page.getByRole("button", { name: "重置", exact: true }).click();
  check("timeline reset returns to first event", await timeline.first().evaluate((node) => node.classList.contains("is-active")));

  const scenarioWindow = page.locator(".gas-window-scenario");
  const beforeTransform = await scenarioWindow.getAttribute("style");
  const head = scenarioWindow.locator(".gas-window-head");
  await head.scrollIntoViewIfNeeded();
  const headBox = await head.boundingBox();
  if (headBox) {
    await page.mouse.move(headBox.x + 40, headBox.y + 22);
    await page.mouse.down();
    await page.mouse.move(headBox.x + 70, headBox.y + 48);
    await page.mouse.up();
  }
  const afterTransform = await scenarioWindow.getAttribute("style");
  check("window drag changes its workspace offset", beforeTransform !== afterTransform, `${beforeTransform} -> ${afterTransform}`);
  await page.locator(".gas-window-topology .gas-window-close").click();
  check("window hide removes topology window", await page.locator(".gas-window-topology").count() === 0);
  await page.getByRole("button", { name: "恢复中央管网拓扑", exact: true }).click();
  check("window restore brings topology back", await page.locator(".gas-window-topology").count() === 1);

  const domBefore = await page.evaluate(() => document.querySelectorAll("*").length);
  const heapBefore = await page.evaluate(() => performance.memory?.usedJSHeapSize ?? null);
  for (let round = 0; round < 10; round += 1) {
    await page.getByRole("button", { name: "重置", exact: true }).click();
    await play.click();
    await page.waitForTimeout(3600);
  }
  const domAfter = await page.evaluate(() => document.querySelectorAll("*").length);
  const heapAfter = await page.evaluate(() => performance.memory?.usedJSHeapSize ?? null);
  check("10 timeline playback rounds keep DOM stable", domAfter <= domBefore + 8, JSON.stringify({ domBefore, domAfter, heapBefore, heapAfter }));

  const viewports = [[1920, 1080], [2560, 1440], [3840, 2160], [1280, 800], [1280, 720], [375, 812]];
  for (const [width, height] of viewports) {
    await page.setViewportSize({ width, height });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: resolve(evidenceDir, "after", `gas-cockpit-${width}x${height}.png`) });
  }

  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.reload({ waitUntil: "networkidle" });
  await page.getByTestId("gas-page").waitFor();
  check("prefers-reduced-motion disables playback", await page.locator(".gas-timeline-play").isDisabled());
  check("only expected synthetic GETs are made", apiRequests.filter((request) => request.path.startsWith("/api/v1/gas/cases/")).length === 2, JSON.stringify(apiRequests));
  check("no write or Agent request is made", !apiRequests.some((request) => request.method !== "GET" || request.path.startsWith("/agent/")), JSON.stringify(apiRequests));
  check("no page errors in browser probe", pageErrors.length === 0, pageErrors.join(" | "));
  check("no console errors in browser probe", consoleErrors.length === 0, consoleErrors.join(" | "));

  await writeFile(resolve(evidenceDir, "browser-results.json"), JSON.stringify({ base, generatedAt: new Date().toISOString(), results, errors, geometry, apiRequests, fixture: { nodes: fixture.nodes.length, edges: fixture.edges.length, services: fixture.services.length } }, null, 2));
  await browser.close();
  if (errors.length > 0) {
    console.error(`FAILED CHECKS: ${errors.join(", ")}`);
    process.exitCode = 1;
  } else {
    console.log(`ALL ${results.length} CHECKS PASSED`);
  }
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
