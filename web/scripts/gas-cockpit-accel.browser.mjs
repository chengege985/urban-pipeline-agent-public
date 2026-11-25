import assert from "node:assert/strict";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
import { chromium } from "playwright";

const base = new URL(process.env.GAS_UI_BASE_URL ?? "http://127.0.0.1:24673");
assert(["127.0.0.1", "localhost"].includes(base.hostname));
const origin = (offset = 0) => {
  const url = new URL(base);
  url.port = String(Number(base.port) + offset);
  return url.origin;
};

const outDir = resolve("..", ".runtime/evidence/gas-cockpit-accel");
await mkdir(outDir, { recursive: true });
const hash = (buf) => createHash("sha256").update(buf).digest("hex");

const proof = {
  phase: "v1.1-accel",
  actualTcp: false,
  checks: [],
  geometry: [],
  screenshots: [],
};

let browser;
const check = async (name, fn) => {
  try {
    const detail = await fn();
    proof.checks.push({ name, ok: true, detail });
  } catch (err) {
    proof.checks.push({ name, ok: false, error: String(err) });
  }
};

function attachObserver(page) {
  const errors = [];
  const apiPaths = [];
  const externalHosts = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  page.on("request", (req) => {
    const url = new URL(req.url());
    if (!["127.0.0.1", "localhost"].includes(url.hostname)) {
      externalHosts.push(url.href);
    }
    if (url.pathname.startsWith("/api/")) {
      apiPaths.push(url.pathname);
    }
  });
  return { errors, apiPaths, externalHosts };
}

async function shot(page, name) {
  await page.evaluate(() => document.fonts.ready);
  const fullPath = resolve(outDir, name);
  await page.screenshot({ path: fullPath, fullPage: true });
  proof.screenshots.push({ name, sha256: hash(await readFile(fullPath)) });
}

async function ready(page) {
  await page.getByTestId("gas-record-total").filter({ hasText: "204" }).waitFor();
}

try {
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(origin() + "/health", { signal: AbortSignal.timeout(1500) });
      if (res.status === 200) {
        proof.actualTcp = true;
        break;
      }
    } catch {}
    await new Promise((r) => setTimeout(r, 400));
  }
  assert(proof.actualTcp, "acceptance backend unavailable");

  const eventsRes = await (await fetch(origin() + "/api/v1/gas/engineering/events")).json();
  const multiEvent = eventsRes.items.find((e) => (e.asset_ids ?? []).length === 9);
  assert(multiEvent, "expected 9-asset event");

  browser = await chromium.launch({ headless: true, executablePath: process.env.BROWSER_EXECUTABLE });

  for (const [width, height] of [[375, 812], [1280, 720], [1280, 800], [1920, 1080], [2560, 1440], [3840, 2160]]) {
    await check(`GIS Chongqing map layout responsive ${width}x${height}`, async () => {
      const page = await browser.newPage({ viewport: { width, height }, reducedMotion: "reduce" });
      const obs = attachObserver(page);
      try {
        await page.goto(origin() + "/#/gas?map=gis", { waitUntil: "networkidle" });
        await ready(page);
        await page.getByTestId("gas-gis-view").waitFor();
        await page.getByTestId("gas-gis-unavailable").waitFor();
        assert.equal(await page.locator("iframe, .amap-container").count(), 0);
        assert.equal(await page.locator('select[aria-label="选择重庆行政区县"] option[value^="500"]').count(), 37);
        assert.equal(await page.locator("[data-district-id]").count(), 30);
        assert.equal(obs.externalHosts.length, 0);
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
        assert.equal(overflow, false, `horizontal overflow at ${width}x${height}`);
        const metrics = await page.evaluate(() => {
          const map = document.querySelector('[data-testid="gas-central-view"]').getBoundingClientRect();
          const ws = document.querySelector('[data-testid="gas-workspace"]').getBoundingClientRect();
          return { width: map.width, height: map.height, widthRatio: map.width / window.innerWidth, heightRatio: map.height / ws.height };
        });
        proof.geometry.push({ width, height, view: "gis", ...metrics });
        if (width >= 1280) {
          assert(metrics.widthRatio >= 0.55, `widthRatio too small: ${metrics.widthRatio}`);
          assert(metrics.heightRatio >= 0.55, `heightRatio too small: ${metrics.heightRatio}`);
        }
        if ([375, 1280, 1920].includes(width) && height !== 800) {
          await shot(page, `v11-gis-${width}x${height}.png`);
        }
        assert.deepEqual(obs.errors, []);
        return metrics;
      } finally {
        await page.close();
      }
    });
  }

  await check("Chongqing GIS interactions: district select, reload persistence, zone filter, pan/zoom/reset, layers, event locate", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce" });
    const obs = attachObserver(page);
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" });
      await ready(page);
      await page.getByRole("button", { name: "重庆GIS · 未就绪", exact: true }).click();
      await page.getByTestId("gas-gis-view").waitFor();

      await page.getByLabel("选择重庆行政区县").selectOption("500103");
      await page.getByTestId("gas-district-panel").filter({ hasText: "渝中区 (500103)" }).waitFor();
      assert(new URL(page.url()).hash.includes("district=500103"));

      await page.reload({ waitUntil: "networkidle" });
      await page.getByTestId("gas-district-panel").filter({ hasText: "渝中区 (500103)" }).waitFor();

      await page.locator(".gas-zone-pills").getByRole("button", { name: "主城新区", exact: true }).click();
      const newAreaCount = await page.locator("[data-district-id]").count();
      assert.equal(newAreaCount, 12);
      await page.locator(".gas-zone-pills").getByRole("button", { name: "全部(37)", exact: true }).click();
      assert.equal(await page.locator("[data-district-id]").count(), 37);

      await page.getByRole("button", { name: "聚焦主城", exact: true }).click();
      assert.equal(await page.getByTestId("gas-gis-viewport-transform").getAttribute("data-zoom"), "2.8");
      assert.equal(await page.locator("[data-district-id]").count(), 37);

      const beforeDrag = await page.getByTestId("gas-gis-viewport-transform").getAttribute("transform");
      const box = await page.getByTestId("gas-gis-canvas").boundingBox();
      assert(box);
      await page.mouse.move(box.x + box.width * 0.25, box.y + box.height * 0.25);
      await page.mouse.down();
      await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.40, { steps: 4 });
      await page.mouse.up();
      const afterDrag = await page.getByTestId("gas-gis-viewport-transform").getAttribute("transform");
      assert.notEqual(beforeDrag, afterDrag);

      await page.getByRole("button", { name: "放大GIS地图", exact: true }).click();
      await page.getByRole("button", { name: "缩小GIS地图", exact: true }).click();

      await page.getByLabel("重庆行政区划(参考)", { exact: true }).uncheck();
      assert.equal(await page.locator("[data-district-id]").count(), 0);
      await page.getByLabel("重庆行政区划(参考)", { exact: true }).check();
      assert.equal(await page.locator("[data-district-id]").count(), 37);

      await page.getByRole("button", { name: "视角重置", exact: true }).click();
      assert.equal(await page.getByTestId("gas-gis-viewport-transform").getAttribute("data-zoom"), "1");
      assert.equal(await page.locator("[data-district-id]").count(), 30);

      await page.locator(".gas-event-list button").filter({ hasText: multiEvent.event_id }).click();
      await page.getByTestId("gas-gis-locate-banner").filter({ hasText: multiEvent.event_id }).waitFor();
      await shot(page, "v11-gis-event-locate-1920.png");

      await page.getByRole("button", { name: `切换到合成拓扑高亮 ${multiEvent.asset_ids.length} 项`, exact: true }).click();
      await page.waitForFunction(() => document.querySelectorAll(".gas-topology-edge.is-selected").length === 7);
      assert.deepEqual(obs.errors, []);
      return { newAreaCount, expandedAllDistricts: 37, clusteredOverview: 30, returnedToTopology: true };
    } finally {
      await page.close();
    }
  });

  await check("Leadership risk cockpit, priority sorting, pipe specs & CUV boundaries", async () => {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1080 }, reducedMotion: "reduce" });
    const obs = attachObserver(page);
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" });
      await ready(page);
      await page.getByTestId("gas-priority-cue").waitFor();
      await page.getByTestId("gas-district-summary-card").locator("summary").click();
      await page.locator('[data-testid="gas-priority-cue"] .gas-priority-jump').click();
      await page.getByTestId("gas-event-detail").waitFor();

      await page.getByRole("button", { name: "风险分析", exact: true }).click();
      await page.getByRole("button", { name: "风险就绪状态", exact: true }).click();
      await page.getByTestId("gas-risk-readiness").filter({ hasText: "0 / 3 核心指标" }).waitFor();
      await page.getByTestId("gas-official-ranking-slot").filter({ hasText: "暂无经授权结果" }).waitFor();
      assert.equal(await page.locator("[data-testid='gas-evidence-priority-list'] button").count(), 3);
      await shot(page, "v11-risk-cockpit-1920.png");

      await page.getByRole("button", { name: "综合态势", exact: true }).click();
      await page.locator(".gas-event-list button").filter({ hasText: multiEvent.event_id }).click();
      const firstPipe = multiEvent.asset_ids.find((id) => id.includes("pipe"));
      assert(firstPipe);
      await page.getByRole("button", { name: `打开资产详情 ${firstPipe}`, exact: true }).click();
      await page.locator(`[data-testid="gas-asset-detail"][data-asset-id="${firstPipe}"]`).waitFor();
      await page.getByTestId("gas-pipe-case-context").locator("summary").click();
      const pipeContext = await page.getByTestId("gas-pipe-case-context").innerText();
      assert(pipeContext.includes("mm"));
      await shot(page, "v11-pipe-detail-1920.png");

      await page.getByRole("button", { name: "应急推演", exact: true }).click();
      const winMgr = page.locator(".gas-window-manager");
      if ((await winMgr.getAttribute("open")) === null) await winMgr.locator("summary").click();
      await page.locator('[data-window-control="impact"]').click();
      await page.getByTestId("gas-cuv-summary").waitFor();
      const boundaryCount = await page.locator("[data-testid='gas-cuv-boundaries'] li").count();
      assert.equal(boundaryCount, 5);
      await shot(page, "v11-cuv-impact-1920.png");
      assert.deepEqual(obs.errors, []);
      return { boundaryCount };
    } finally {
      await page.close();
    }
  });

  await check("Gas Agent quick entry, dialog parse, executable locate, asset drill & honest service probes", async () => {
    const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, reducedMotion: "reduce" });
    const obs = attachObserver(page);
    try {
      await page.goto(origin() + "/#/gas", { waitUntil: "networkidle" });
      await ready(page);
      await shot(page, "v11-cockpit-1280x720.png");

      const quickForm = page.getByTestId("gas-agent-quick-entry");
      await quickForm.locator("input").fill("定位重庆市渝中区 GIS 参考底图");
      await quickForm.getByRole("button", { name: "解析", exact: true }).click();

      const dialog = page.getByTestId("gas-agent-dialog");
      await dialog.waitFor();
      assert(await dialog.getByTestId("gas-agent-intent").innerText().then((t) => t.includes("locate_district")));
      await dialog.getByRole("button", { name: "执行：GIS定位至 渝中区", exact: true }).click();
      await page.getByTestId("gas-gis-view").waitFor();
      assert(new URL(page.url()).hash.includes("district=500103"));

      await page.goto(origin() + "/#/gas/assistant", { waitUntil: "networkidle" });
      await ready(page);
      await page.getByTestId("gas-assistant-state").waitFor();
      assert(obs.apiPaths.every((p) => p.startsWith("/api/v1/gas/")), "assistant mount must not auto-probe agent routes");

      await page.getByLabel("燃气自然语言输入").fill(`定位 ${multiEvent.event_id} 关联资产`);
      await page.getByRole("button", { name: "本地规则解析", exact: true }).click();
      assert.equal(await page.locator("[data-testid='gas-agent-clickable-assets'] button").count(), 9);
      await shot(page, "v11-agent-assistant-1280x720.png");

      assert.deepEqual(obs.errors, []);
      const countBeforeProbe = obs.errors.length;

      await page.getByRole("button", { name: "检测平台 Agent 服务", exact: true }).click();
      await page.waitForFunction(() => document.querySelector('[data-testid="gas-agent-execution-banner"]')?.textContent?.includes("503"));
      const probeErrors = obs.errors.slice(countBeforeProbe);
      assert(probeErrors.every((msg) => msg.includes("503 (Service Unavailable)")), `unexpected errors during probe: ${probeErrors}`);

      await page.getByRole("button", { name: multiEvent.asset_ids[0], exact: true }).click();
      await page.locator(`[data-testid="gas-asset-detail"][data-asset-id="${multiEvent.asset_ids[0]}"]`).waitFor();
    } finally {
      await page.close();
    }

    const unauthPage = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    try {
      await unauthPage.goto(origin(1) + "/#/gas/assistant", { waitUntil: "networkidle" });
      await unauthPage.getByRole("button", { name: "检测平台 Agent 服务", exact: true }).click();
      await unauthPage.waitForFunction(() => document.querySelector('[data-testid="gas-agent-execution-banner"]')?.textContent?.includes("unauthenticated"));
    } finally {
      await unauthPage.close();
    }

    const edgePage = await browser.newPage({ viewport: { width: 1280, height: 800 } });
    try {
      await edgePage.goto(origin() + "/#/gas/assistant", { waitUntil: "networkidle" });
      await edgePage.route("**/api/v1/agent/health", (route) => route.fulfill({ status: 403, contentType: "application/json", body: "{}" }));
      await edgePage.getByRole("button", { name: "检测平台 Agent 服务", exact: true }).click();
      await edgePage.waitForFunction(() => document.querySelector('[data-testid="gas-agent-execution-banner"]')?.textContent?.includes("forbidden"));
      await edgePage.unroute("**/api/v1/agent/health");
      await edgePage.route("**/api/v1/agent/health", (route) => route.abort("timedout"));
      await edgePage.getByRole("button", { name: "检测平台 Agent 服务", exact: true }).click();
      await edgePage.getByTestId("gas-agent-execution-banner").waitFor();
    } finally {
      await edgePage.close();
    }
    return { realUnavailableAnd401Tested: true, mocked403AndAbortTested: true };
  });
} catch (error) {
  proof.gate = "FAILED";
  proof.error = String(error);
  process.exitCode = 1;
} finally {
  if (browser) await browser.close();
  proof.gate ??= proof.checks.length && proof.checks.every((c) => c.ok) ? "PASS" : "FAILED";
  await writeFile(resolve(outDir, "accel-evidence.json"), JSON.stringify(proof, null, 2) + "\n");
  console.log(JSON.stringify({ gate: proof.gate, checks: proof.checks, geometryCount: proof.geometry.length, screenshots: proof.screenshots.map((s) => s.name) }, null, 2));
}
if (proof.gate !== "PASS") process.exitCode = 1;
