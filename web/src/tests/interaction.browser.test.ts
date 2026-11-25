import { build } from "esbuild";
import { chromium, type Browser, type Page } from "playwright";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { DemoProvider } from "../providers/demoProvider";
const env = (globalThis as typeof globalThis & { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};

// Opt-in browser regression. Uses in-memory routes only: no server, model or dist build.
describe.runIf(env.RUN_BROWSER === "1")("analysis context in the actual React UI", () => {
  let browser: Browser;
  let bundle: string;
  async function buildApp(gasDemo = false) {
    const result = await build({ entryPoints: ["src/main.tsx"], bundle: true, write: false, format: "iife", jsx: "automatic",
      define: { "import.meta.env": JSON.stringify({ VITE_WATERPULSE_API_URL: "http://fixture.local", VITE_WPN_EDITION: gasDemo ? "project" : "competition", VITE_WPN_GAS_DEMO: gasDemo ? "1" : undefined }), "process.env.NODE_ENV": '"development"' },
      plugins: [{ name: "no-styles-in-behaviour-test", setup(builder) { builder.onLoad({ filter: /\.css$/ }, () => ({ contents: "", loader: "js" })); } }],
    });
    return result.outputFiles[0].text;
  }
  beforeAll(async () => {
    bundle = await buildApp();
    browser = await chromium.launch({ headless: true, executablePath: env.BROWSER_EXECUTABLE, args: ["--no-sandbox"] });
  }, 60000);
  afterAll(async () => { await browser?.close(); });

  it("keeps historical water bookmarks inside the gas project without mounting the water workspace", async () => {
    const gasBundle = await buildApp(true);
    const page = await browser.newPage();
    const apiPaths: string[] = [];
    try {
      await page.route("**/*", async route => {
        const { pathname } = new URL(route.request().url());
        if (pathname === "/") return route.fulfill({ contentType: "text/html", body: '<div id="root"></div><script src="/app.js"></script>' });
        if (pathname === "/app.js") return route.fulfill({ contentType: "application/javascript", body: gasBundle });
        apiPaths.push(pathname);
        return route.fulfill({ status: 503, json: { detail: "Fixture unavailable" } });
      });
      for (const path of ["/overview", "/pipe/WP-016", "/scenario/test", "/assistant", "/report/test", "/about"]) {
        await page.goto("http://fixture.local/#" + path);
        await page.waitForURL("**/#/gas");
        await page.getByRole("heading", { name: "重庆燃气智能监管", exact: true }).waitFor();
        expect(await page.getByRole("navigation", { name: "主导航" }).count()).toBe(0);
        expect(await page.getByRole("button", { name: /既有平台报告/ }).count()).toBe(0);
        expect(await page.getByRole("button", { name: /燃气证据报告/ }).isVisible()).toBe(true);
        expect(await page.locator("[data-window-control]").count()).toBe(11);
      }
      expect(apiPaths.some(p => p.startsWith("/api/v1/gas/"))).toBe(true);
      expect(apiPaths.every(p => p.startsWith("/api/v1/gas/"))).toBe(true);
    } finally {
      await page.close();
    }
  }, 60000);

  async function openPage(options: { rejectExtended?: boolean; delayReplace8?: boolean } = {}): Promise<Page> {
    const page = await browser.newPage();
    const provider = new DemoProvider();
    let delayedTimeline = false;
    await page.route("**/*", async (route) => {
      const request = route.request(); const pathname = new URL(request.url()).pathname;
      if (pathname === "/") return route.fulfill({ contentType: "text/html", body: '<div id="root"></div><script src="/app.js"></script>' });
      if (pathname === "/app.js") return route.fulfill({ contentType: "application/javascript", body: bundle });
      const body = request.postDataJSON() ?? {};
      let response: unknown;
      if (pathname.endsWith("/meta")) response = (await provider.loadDataset()).meta;
      else if (pathname.endsWith("/dataset")) response = await provider.loadDataset();
      else if (pathname.endsWith("/evaluate")) response = await provider.evaluate(body.pipe, body.override);
      else if (pathname.endsWith("/simulate")) {
        if (options.rejectExtended && body.isolationPlanId === "ISO-HOSP-EXT") return route.fulfill({ status: 422, json: { detail: "fixture rejected" } });
        response = await provider.simulate(body.scenarioId, body.isolationPlanId);
      } else if (pathname.endsWith("/compare")) response = await provider.comparePlans(body.scenarioId, body.planIds, body.constraints, body.isolationPlanId);
      else if (pathname.endsWith("/timeline/check") || pathname.endsWith("/timeline")) {
        if (options.delayReplace8 && !delayedTimeline && body.planId === "PLAN-REPLACE" && body.horizonHours === 8) {
          delayedTimeline = true;
          await new Promise((resolve) => setTimeout(resolve, 800));
        }
        response = pathname.endsWith("/timeline/check")
          ? await provider.checkTimeline(body)
          : await provider.timeline(body);
      } else if (pathname.endsWith("/assistant")) response = await provider.ask(body.question, body.context);
      else return route.fulfill({ status: 404, body: "unknown fixture route" });
      return route.fulfill({ json: response });
    });
    await page.goto("http://fixture.local/?mode=live#/scenario/SC-HOSP?pipe=WP-016");
    await page.getByRole("button", { name: "选择方案", exact: true }).first().waitFor();
    return page;
  }

  it("a direct platform Report opens without dataset bootstrap and refresh remains a report-only GET", async () => {
    const page = await browser.newPage();
    const runId = "run-direct-report-browser";
    const snapshotId = "snap-direct-report-browser";
    const reportPath = `/api/v1/runs/${runId}/report`;
    const calls: Array<{ method: string; path: string }> = [];
    // Synthetic UI boundary fixture; backend validity is covered by the separate real HTTP harness.
    const report = {
      report_schema_version: "unified-report-1.0", report_id: "report-direct-browser",
      source: "existing_run_only", recomputation: "none", run_id: runId,
      run_kind: "simulation", run_status: "partial", business_status: "partial",
      dataset: { snapshot_id: snapshotId, data_version: "b".repeat(64), as_of: "2026-11-04T00:00:00+00:00",
        data_mode: "synthetic", source_status: "constructed", schema_version: "0.1.0", name: "" },
      algorithms: [{ capability_id: "given_isolation_topology", semantics: "topological_reachability",
        version: "0.1.0", validation_status: "not_business_validated" }],
      scenario: null, pipe: null, decision: null, timeline: null,
      simulation: { run_id: runId, snapshot_id: snapshotId, status: "partial",
        affected_service_ids: ["synthetic-unknown-count"], affected_critical_facility_ids: [],
        potentially_disconnected_counts: { household: null, person: null, facility: 0 },
        unknown_count_service_ids: ["synthetic-unknown-count"] },
      feedback: [], evidence_ids: [`run:${runId}`, `snapshot:${snapshotId}`], limitations: ["Synthetic browser probe"],
    };
    await page.route("**/*", async route => {
      const request = route.request(); const pathname = new URL(request.url()).pathname;
      if (pathname === "/") return route.fulfill({ contentType: "text/html", body: '<div id="root"></div><script src="/app.js"></script>' });
      if (pathname === "/app.js") return route.fulfill({ contentType: "application/javascript", body: bundle });
      calls.push({ method: request.method(), path: pathname });
      if (pathname === reportPath && request.method() === "GET") return route.fulfill({ json: report });
      return route.fulfill({ status: 403, json: { detail: "Dataset bootstrap and computation are intentionally unavailable." } });
    });
    // Deliberately no datasetId: the frozen Report owns its own dataset identity.
    await page.goto(`http://fixture.local/?mode=platform#/report/${runId}`);
    await page.getByTestId("report-status").waitFor({ timeout: 10000 });
    expect(await page.getByTestId("report-status").innerText()).toContain("partial");
    expect(await page.getByRole("heading", { name: runId, exact: true }).count()).toBe(1);
    expect(await page.locator(".report-head").innerText()).toContain(snapshotId);
    const topology = page.locator("section", { has: page.getByRole("heading", { name: "拓扑影响", exact: true }) });
    expect(await topology.innerText()).toContain("未知（未提供）");
    expect(calls.length).toBeGreaterThan(0); // StrictMode may repeat the initial read.
    expect(calls.every(call => call.method === "GET" && call.path === reportPath)).toBe(true);
    const initialReads = calls.length;
    const refreshed = page.waitForResponse(response => new URL(response.url()).pathname === reportPath);
    await page.getByTestId("report-refresh").click();
    await refreshed;
    await page.getByTestId("report-status").waitFor();
    expect(calls.length).toBe(initialReads + 1);
    expect(calls.every(call => call.method === "GET" && call.path === reportPath)).toBe(true);
    await page.close();
  }, 60000);

  it.each([
    { entry: "platform failure followed by explicit demo", query: "?mode=platform&datasetId=fixture-denied",
      probePath: "/api/v1/capabilities" },
    { entry: "live failure followed by explicit demo", query: "?mode=live",
      probePath: "/api/v1/demo/meta" },
    { entry: "the default offline demo", query: "", probePath: null },
  ])("$entry keeps local reports and demo mode across navigation and browser history", async ({ query, probePath }) => {
    const page = await browser.newPage();
    const calls: Array<{ method: string; path: string }> = [];
    // Reject every API request in memory. No service, model, or pre-seeded local report is used.
    await page.route("**/*", async route => {
      const request = route.request(); const pathname = new URL(request.url()).pathname;
      if (pathname === "/") return route.fulfill({ contentType: "text/html", body: '<div id="root"></div><script src="/app.js"></script>' });
      if (pathname === "/app.js") return route.fulfill({ contentType: "application/javascript", body: bundle });
      calls.push({ method: request.method(), path: pathname });
      return route.fulfill({ status: 503, json: { detail: "Synthetic unavailable API fixture." } });
    });
    try {
      await page.goto("http://fixture.local/" + query + "#/scenario/SC-HOSP?pipe=WP-016");
      if (probePath) {
        await page.getByRole("heading", { name: "实时接口暂不可用", exact: true }).waitFor({ timeout: 10000 });
        expect(await page.locator(".mode-banner.mode-demo").count()).toBe(0);
        expect(calls.length).toBeGreaterThan(0); // StrictMode may repeat the initial probe.
        expect(calls.every(call => call.method === "GET" && call.path === probePath)).toBe(true);
        await page.getByRole("button", { name: "使用演示数据", exact: true }).click();
      }
      await page.getByRole("button", { name: "选择方案", exact: true }).first().waitFor({ timeout: 10000 });
      const callsBeforeNavigation = [...calls];
      if (!probePath) expect(callsBeforeNavigation).toEqual([]);

      async function expectDemo() {
        await page.locator(".mode-banner.mode-demo").waitFor({ timeout: 10000 });
        expect(await page.getByTestId("platform-report-page").count()).toBe(0);
        expect(await page.locator(".unavailable-screen").count()).toBe(0);
        // Also forbids any new Capabilities probe or accidental platform Report GET.
        expect(calls).toEqual(callsBeforeNavigation);
      }

      async function expectLocalReport() {
        await page.getByRole("heading", { name: "决策简报", exact: true }).waitFor({ timeout: 10000 });
        expect(await page.locator(".report-page .report-head").innerText()).toContain("WP-016");
        expect(await page.locator(".report-health").count()).toBe(1);
        expect(await page.getByRole("button", { name: "下载 JSON", exact: true }).isEnabled()).toBe(true);
        await expectDemo();
      }

      async function generateReport() {
        await page.getByRole("button", { name: "选择方案", exact: true }).first().click();
        await page.getByRole("button", { name: "生成决策简报", exact: true }).click();
        await expectLocalReport();
        expect(new URL(page.url()).hash).toMatch(/^#\/report\/[^/]+$/);
        return page.url();
      }

      await expectDemo();
      const firstReportUrl = await generateReport();
      await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "城市总览", exact: true }).click();
      await page.getByRole("heading", { name: "城市管网总览", exact: true }).waitFor({ timeout: 10000 });
      await expectDemo();

      await page.goBack();
      await expectLocalReport();
      expect(page.url()).toBe(firstReportUrl);
      await page.goForward();
      await page.getByRole("heading", { name: "城市管网总览", exact: true }).waitFor({ timeout: 10000 });
      await expectDemo();

      // Re-enter through the UI and generate another valid local report in the same page session.
      await page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "情景模拟", exact: true }).click();
      await page.getByRole("button", { name: "选择方案", exact: true }).first().waitFor({ timeout: 10000 });
      await expectDemo();
      const secondReportUrl = await generateReport();
      await page.goBack();
      await page.getByRole("button", { name: "选择方案", exact: true }).first().waitFor({ timeout: 10000 });
      await expectDemo();
      await page.goForward();
      await expectLocalReport();
      expect(page.url()).toBe(secondReportUrl);
    } finally {
      await page.close();
    }
  }, 60000);

  it("an invalid replacement isolation removes old impact and all selectable plans", async () => {
    const page = await openPage({ rejectExtended: true });
    await page.getByLabel("预设隔离方案").selectOption("ISO-HOSP-EXT");
    await page.locator(".map-panel .inline-warning").filter({ hasText: "HTTP 422" }).waitFor();
    expect(await page.getByRole("button", { name: "选择方案", exact: true }).count()).toBe(0);
    expect(await page.getByRole("button", { name: "生成决策简报" }).isDisabled()).toBe(true);
    expect(await page.locator(".impact-metrics").first().innerText()).not.toMatch(/\d/);
    await page.close();
  }, 60000);

  it("a what-if report keeps the displayed score and the assistant inherits the changed budget", async () => {
    const page = await openPage();
    const age = page.locator('input[type="range"]').first();
    await age.focus(); await age.press("Home");
    await page.waitForFunction(() => document.querySelectorAll(".before-after b")[1]?.textContent === "37.5");
    await page.getByRole("button", { name: "选择方案", exact: true }).first().click();
    await page.getByRole("button", { name: "生成决策简报" }).click();
    await page.getByRole("heading", { name: "决策简报", exact: true }).waitFor();
    expect(await page.locator(".report-health").innerText()).toContain("37.5");
    await page.goto("http://fixture.local/?mode=live#/scenario/SC-HOSP?pipe=WP-016");
    await page.getByLabel("演示预算（元）").fill("5000");
    await page.getByText("当前没有满足全部硬约束的方案，不能生成决策建议。").waitFor();
    await page.getByRole("button", { name: "问演示助手" }).click();
    await page.getByLabel("向演示助手提问").fill("比较方案");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.locator(".chat-message.assistant .chat-text").first().waitFor();
    expect(await page.locator(".chat-message.assistant .chat-text").first().innerText()).toContain("5000");
    expect(await page.locator(".chat-message.assistant .chat-text").first().innerText()).toContain("没有可行方案");
    await page.close();
  }, 60000);

  it("timeline keeps 6h while completion is pending and freezes the same evidence into the report", async () => {
    const page = await openPage();
    const timeline = page.locator(".timeline-panel");
    await timeline.getByText("能力预检：succeeded").waitFor();
    await timeline.locator(".timeline-metric").nth(0).locator("b").filter({ hasText: "待补数据" }).waitFor();
    expect(await timeline.locator(".timeline-metric").nth(1).locator("b").innerText()).toBe("6 h");
    expect(await timeline.locator(".timeline-metric").nth(2).locator("b").innerText()).toBe("6 h");
    expect(await timeline.locator(".timeline-table").innerText()).toContain("市第二医院");

    await page.getByRole("button", { name: "选择方案", exact: true }).first().click();
    await page.getByRole("button", { name: "生成决策简报" }).click();
    await page.getByRole("heading", { name: "服务恢复时间线" }).waitFor();
    const reportTimeline = page.locator("section", { has: page.getByRole("heading", { name: "服务恢复时间线" }) });
    expect(await reportTimeline.locator(".timeline-metric").nth(0).locator("b").innerText()).toBe("待补数据");
    expect(await reportTimeline.locator(".timeline-metric").nth(1).locator("b").innerText()).toBe("6 h");
    expect(await reportTimeline.locator(".event-axis").innerText()).toContain("WP-016");
    await page.reload();
    await page.getByRole("heading", { name: "服务恢复时间线" }).waitFor();
    const reloaded = page.locator("section", { has: page.getByRole("heading", { name: "服务恢复时间线" }) });
    expect(await reloaded.locator(".timeline-metric").nth(1).locator("b").innerText()).toBe("6 h");
    expect(await reloaded.locator(".event-axis").innerText()).toContain("WP-016");
    await page.close();
  }, 60000);

  it("timeline schedule override changes completion only, and empty targets are not applicable", async () => {
    const page = await openPage();
    const timeline = page.locator(".timeline-panel");
    await timeline.getByText("能力预检：succeeded").waitFor();
    const schedule = page.getByLabel("补录任务结束（小时，仅本次假设）");
    await schedule.fill("9");
    await timeline.locator(".timeline-metric").nth(0).locator("b").filter({ hasText: "9 h" }).waitFor();
    expect(await timeline.locator(".timeline-metric").nth(0).locator("b").innerText()).toContain("超出窗口");
    expect(await timeline.locator(".timeline-metric").nth(1).locator("b").innerText()).toBe("6 h");

    await schedule.fill("");
    await timeline.locator(".timeline-metric").nth(0).locator("b").filter({ hasText: "待补数据" }).waitFor();

    const hospitalTarget = page.locator(".target-chip").filter({ hasText: "市第二医院" }).locator("input");
    expect(await hospitalTarget.isChecked()).toBe(true);
    await hospitalTarget.uncheck();
    await timeline.locator(".timeline-metric").nth(2).locator("b").filter({ hasText: "不适用" }).waitFor();
    const metrics = await timeline.locator(".timeline-metrics").innerText();
    expect(metrics).not.toContain("0 h");
    expect(metrics).toContain("待补数据");
    await page.close();
  }, 60000);

  it("assistant answers from the same timeline context without opening a review", async () => {
    const page = await openPage();
    const timeline = page.locator(".timeline-panel");
    await timeline.getByText("能力预检：succeeded").waitFor();
    await timeline.locator(".timeline-metric").nth(1).locator("b").filter({ hasText: "6 h" }).waitFor();
    await page.getByRole("button", { name: "问演示助手" }).click();
    await page.getByLabel("向演示助手提问").fill("这个方案多久恢复？");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    const answer = page.locator(".chat-message.assistant .chat-text").first();
    await answer.waitFor();
    const text = await answer.innerText();
    expect(text).toContain("6 小时");
    expect(text).toContain("待补数据");
    expect(text).not.toContain("接受建议");
    expect(await page.getByLabel("审核人").count()).toBe(0);
    await page.close();
  }, 60000);

  it("a late replace/horizon-8 timeline response cannot overwrite the new horizon-30 result", async () => {
    const page = await openPage({ delayReplace8: true });
    const timeline = page.locator(".timeline-panel");
    await timeline.getByText("能力预检：succeeded").waitFor();
    await page.locator(".timeline-controls select").selectOption("PLAN-REPLACE");
    await timeline.getByText("正在校验能力并计算时间线…").waitFor();
    await page.getByLabel("观察窗口（小时）").fill("30");
    await timeline.locator(".event-axis .event-row").filter({ hasText: "24h" }).waitFor({ timeout: 15000 });
    expect(await timeline.locator(".timeline-metric").nth(1).innerText()).toContain("24 h");
    await page.waitForTimeout(1200);
    expect(await timeline.locator(".event-axis").innerText()).toContain("24h");
    expect(await timeline.locator(".timeline-metric").nth(1).innerText()).toContain("24 h");
    await page.close();
  }, 60000);
  it("upstream impact failure cancels an in-flight timeline and ends loading", async () => {
    const page = await openPage({ delayReplace8: true, rejectExtended: true });
    const timeline = page.locator(".timeline-panel");
    await timeline.getByText("能力预检：succeeded").waitFor();
    await page.locator(".timeline-controls select").selectOption("PLAN-REPLACE");
    await timeline.getByText("正在校验能力并计算时间线…").waitFor();
    await page.getByLabel("预设隔离方案").selectOption("ISO-HOSP-EXT");
    await timeline.getByText(/时间线不可用.*HTTP 422/).waitFor();
    expect(await timeline.getByText("正在校验能力并计算时间线…").count()).toBe(0);
    await page.waitForTimeout(1200);
    expect(await timeline.locator(".timeline-metrics").count()).toBe(0);
    expect(await timeline.getByText("正在校验能力并计算时间线…").count()).toBe(0);
    await page.close();
  }, 60000);

  it("assistant inherits the 9h schedule and clears it when switching to another scenario", async () => {
    const page = await openPage(); const timeline = page.locator(".timeline-panel");
    await timeline.getByText("能力预检：succeeded").waitFor();
    await page.getByLabel("补录任务结束（小时，仅本次假设）").fill("9");
    await timeline.locator(".timeline-metric").nth(0).locator("b").filter({ hasText: "9 h" }).waitFor();
    await page.getByRole("button", { name: "问演示助手" }).click();
    await page.getByLabel("向演示助手提问").fill("这个方案多久恢复？");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.locator(".chat-message.assistant .chat-text").filter({ hasText: "9 小时" }).waitFor();
    await page.locator(".context-panel select").nth(1).selectOption("SC-SCHOOL");
    await page.getByLabel("向演示助手提问").fill("现在多久恢复？");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.locator(".chat-message.assistant .chat-text").filter({ hasText: "请重新计算" }).waitFor();
    const last = await page.locator(".chat-message.assistant .chat-text").last().innerText();
    expect(last).not.toContain("9 小时"); expect(last).not.toContain("6 小时");
    await page.close();
  }, 60000);

});
