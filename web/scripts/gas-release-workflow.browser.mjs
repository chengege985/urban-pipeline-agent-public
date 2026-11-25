import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { chromium } from "playwright";

const [runtime, output, commit, tree, mode, size, profile = "m0"] = process.argv.slice(2);
assert.ok(["m0", "pilot"].includes(profile));
assert.match(commit, /^[a-f0-9]{40}$/);
assert.match(tree, /^[a-f0-9]{40}$/);
assert.ok(["ready", "unavailable"].includes(mode));
assert.ok(["1920x1080", "1280x720"].includes(size));
const [width, height] = size.split("x").map(Number);
const readJson = async (path) => JSON.parse(await readFile(path, "utf8"));
const access = await readJson(join(runtime, "access.json"));
const cfg = await readJson(join(runtime, "config.json"));
assert.equal(cfg.source, commit);
assert.equal(cfg.cuv_collaboration_demo, true);
assert.equal(cfg.disposition_demo, true);
const web = "http://127.0.0.1:" + cfg.web_port;
const api = "http://127.0.0.1:" + cfg.api_port;
const base = "/api/v1/gas/engineering";
const agentPath = base + "/agent/query";
const observationPath = base + "/monitoring/observation";
const replayPath = base + "/cuv-replay";
const collaboration = replayPath + "/collaboration";
const engineeringPath = replayPath + "/engineering";
const benchmarkPath = replayPath + "/benchmark";
const physicalCapabilities = ["steady_pressure_flow", "supply_adequacy", "transient_recovery"];
const eventId = "syn-gas-event-fault";
const assetId = "syn-gas-pipe-K-001";
const observationId = "syn-gas-replay-pressure-k001-m12";
const hold = "syn-gas-cuv-plan-hold-isolation";
const staged = "syn-gas-cuv-plan-staged-release";
const disposition = base + "/events/" + eventId + "/disposition";
const records = { commit, tree, mode, size, profile, transport: "loopback TCP", checks: [], queries: [], screenshots: [], page_errors: [] };

await mkdir(output, { recursive: true, mode: 0o700 });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM_EXECUTABLE, headless: true });
records.browser = browser.version();
const browserContext = await browser.newContext({
  viewport: { width, height }, reducedMotion: "reduce",
  httpCredentials: { username: access.operator.username, password: access.operator.password },
});
const page = await browserContext.newPage();
page.setDefaultTimeout(14000);
page.on("pageerror", (error) => records.page_errors.push(error.name));

async function http(path, identity = access.operator, body) {
  return fetch(web + path, {
    method: body ? "POST" : "GET",
    headers: {
      Authorization: "Basic " + Buffer.from(identity.username + ":" + identity.password).toString("base64"),
      ...(body ? { "Content-Type": "application/json", Origin: web } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
}
function responseFor(path, method = "GET") {
  return page.waitForResponse((response) => new URL(response.url()).pathname === path
    && response.request().method() === method, { timeout: 20000 });
}
async function send(question) {
  const assistant = page.getByTestId("gas-agent-dialog");
  await assistant.getByRole("textbox", { name: "燃气自然语言输入" }).fill(question);
  await assistant.getByRole("button", { name: "发送查询", exact: true }).click();
}
async function query(label, trigger, toolName) {
  const pending = responseFor(agentPath, "POST");
  await trigger();
  const response = await pending;
  assert.equal(response.status(), 200, label);
  const result = await response.json();
  assert.equal(result.model_status, mode, label + " " + (result.error_code ?? ""));
  assert.equal(result.parser_source, mode === "ready" ? "qwen_intent" : "local_rule_not_llm", label);
  assert.equal(result.model_calls, mode === "ready" ? 2 : 0, label);
  if (mode === "ready") {
    assert.equal(result.model_id, "Qwen3.8-27B", label);
    assert.equal(result.explanation_source, "qwen_evidence_selection", label);
    assert.ok(result.explanation.includes(observationId), label);
  }
  assert.equal(result.tools.length, 1, label);
  const tool = result.tools[0];
  assert.equal(tool.tool_name, toolName, label);
  assert.deepEqual(result.evidence_scope, tool.evidence_scope);
  assert.equal(tool.monitoring_scope.scope, "gas_m0_monitoring");
  assert.equal(tool.monitoring_scope.source_sha256, tool.data.monitoring_context.reference.manifest_sha256);
  if (toolName === "cuv.query") assert.equal(tool.evidence_scope.scope, "gas_m0_graph_cuv");
  assert.equal(tool.data.monitoring_context.event.event_id, eventId);
  assert.equal(tool.data.monitoring_context.asset_id, assetId);
  assert.equal(tool.data.monitoring_context.observation.source_record_id, observationId);
  assert.ok(result.evidence_refs.some((item) => item.startsWith("gas-monitoring-row:")));
  assert.ok(result.evidence_refs.some((item) => item.startsWith("gas-event:" + eventId + ":")));
  await page.locator("[data-testid=gas-agent-result][data-request-id='" + result.request_id + "']").waitFor();
  records.queries.push({
    label, request_id: result.request_id, status: result.status, parser_source: result.parser_source, model_id: result.model_id,
    model_calls: result.model_calls, elapsed_ms: result.elapsed_ms, explanation: result.explanation,
    tool: toolName, parameters: tool.parameters, evidence_refs: result.evidence_refs,
    scope: tool.evidence_scope, monitoring_scope: tool.monitoring_scope, comparison_binding: tool.cuv_comparison_binding,
    observation: tool.data.monitoring_context.observation, comparison_change: tool.data.comparison_change,
    engineering: tool.engineering_assessment ?? undefined,
    confirmed_revision: tool.data.confirmed_comparison?.revision,
  });
  return tool.data;
}
async function shot(name) {
  const file = name + "-" + size + ".png";
  const path = join(output, file);
  await page.screenshot({ path, animations: "disabled" });
  records.screenshots.push({ file, sha256: createHash("sha256").update(await readFile(path)).digest("hex") });
}
function closeAssistant() {
  return page.getByTestId("gas-agent-dialog").getByRole("button", { name: "关闭窗口", exact: true }).click();
}
function check(name) {
  records.checks.push(name);
}

async function openPilotWorkflow() {
  const catalogue = base + "/pilot/datasets";
  const datasetPath = catalogue + "/syn-gas-pilot-history-01/versions";
  const v3 = datasetPath + "/demo-v3";
  const obs = "syn-gas-pilot-obs-p3";
  const blocked = catalogue + "/syn-gas-pilot-no-scope/versions/demo-v3";
  assert.equal((await fetch(api + catalogue)).status, 401);
  assert.equal((await http(blocked)).status, 403);
  await page.getByTestId("gas-record-total").filter({ hasText: "204" }).waitFor();
  assert.equal(await page.locator(".gas-topology-node").count(), 54);
  assert.equal(await page.locator(".gas-topology-edge").count(), 73);
  const districts = await http(base + "/districts");
  assert.equal(districts.status, 200);
  assert.deepEqual((await districts.json()).items, []);
  await page.getByRole("button", { name: "重庆GIS", exact: true }).click();
  const gis = page.getByTestId("gas-gis-view");
  const districtsPicker = gis.getByLabel("选择重庆行政区县");
  await districtsPicker.waitFor();
  assert.equal(await districtsPicker.locator('option[value^="500"]').count(), 37);
  await districtsPicker.selectOption("500103");
  await gis.locator('.gas-gis-district-node.is-selected[data-district-id="500103"]').waitFor();
  if (mode === "ready") await shot("00-gis-reference");
  await page.getByRole("button", { name: "合成拓扑", exact: true }).click();
  await gis.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "切换浅色主题", exact: true }).click();
  assert.equal(await page.getByTestId("gas-page").getAttribute("data-theme"), "light");
  await page.getByRole("button", { name: "切换深色主题", exact: true }).click();
  assert.equal(await page.getByTestId("gas-page").getAttribute("data-theme"), "dark");
  check("dual theme, 54 nodes, 73 pipes, 204 records, 37 GIS reference districts");

  await page.getByTestId("gas-monitoring-open").click();
  await page.getByRole("button", { name: "试点历史回放", exact: true }).click();
  const pilot = page.getByTestId("gas-pilot-panel");
  const picker = pilot.getByLabel("选择试点数据集");
  const workbench = pilot.getByTestId("gas-pilot-replay");
  const detail = pilot.getByTestId("gas-pilot-observation");
  async function choose(version) {
    const option = picker.getByRole("option", { name: new RegExp(" · " + version + " · ") });
    await option.waitFor({ state: "attached" });
    const next = responseFor(datasetPath + "/" + version + "/replay");
    await picker.selectOption({ value: await option.getAttribute("value") });
    const received = await next;
    assert.equal(received.status(), 200);
    await workbench.waitFor();
    return received.json();
  }

  const replay = await choose("demo-v3");
  assert.equal(replay.data_mode, "synthetic");
  assert.equal(replay.observations[3].observation.value, 7.2);
  assert.deepEqual(replay.applied_thresholds.map((item) => item.version), ["pilot-r1", "pilot-r2"]);
  assert.equal(replay.validation.duplicate_alarms.length, 1);
  await pilot.getByTestId("gas-pilot-quality").locator("summary").getByText(/0 项错误.*5 项提示/).waitFor();
  const rejected = pilot.getByTestId("gas-pilot-rejections");
  await rejected.locator(":scope > summary").click();
  await rejected.getByText(/demo-v3-rejected.*回执/).click();
  await rejected.getByRole("list", { name: "缺失字段" }).getByText("observed_at", { exact: true }).waitFor();
  await rejected.locator(":scope > summary").click();
  const trend = pilot.getByTestId("gas-pilot-trend");
  await trend.getByRole("list", { name: "生效阈值与切换时间" }).getByText("2026-11-18T01:08:00Z").waitFor();
  await trend.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("00-pilot-trend");
  const canvas = trend.locator("canvas");
  const box = await canvas.boundingBox();
  assert.ok(box);
  await canvas.click({ position: { x: box.width * (78 + 858 * 3 / 22) / 960,
    y: box.height * (206 - 168 * (7.2 - 5) / (9 - 5)) / 254 } });
  await detail.getByText(obs, { exact: true }).waitFor();
  await detail.getByRole("button", { name: "查看关联管段 syn-gas-pilot-pipe-a" }).click();
  await detail.getByTestId("gas-pilot-pipe").waitFor();
  await detail.getByRole("button", { name: "打开试点地图", exact: true }).click();
  await detail.getByTestId("gas-pilot-local-map").waitFor();
  await detail.getByRole("button", { name: "打开关联事件证据", exact: true }).click();
  await detail.getByText("synthetic-ticket-001", { exact: true }).waitFor();
  const waiting = responseFor(v3 + "/agent/query", "POST");
  await detail.getByLabel("试点只读问答").fill("为什么这条压力记录触发了告警？");
  await detail.getByRole("button", { name: "查询试点证据" }).click();
  const response = await waiting;
  assert.equal(response.status(), 200);
  const answer = await response.json();
  assert.equal(answer.observation_id, obs);
  assert.equal(answer.asset_id, "syn-gas-pilot-pipe-a");
  assert.equal(answer.threshold_version, "pilot-r1");
  assert.equal(answer.model_state, mode === "ready" ? "online" : "unavailable");
  assert.equal(answer.model_id, mode === "ready" ? "Qwen3.8-27B" : null);
  assert.equal(answer.model_calls, mode === "ready" ? 1 : 0);
  assert.ok(answer.answer.includes("触发证据含 3 条原始观测"));
  await detail.getByRole("button", { name: /^阈值版本 pilot-r1 / }).click();
  assert.equal(answer.evidence_scope.scope, "pilot_history");
  assert.equal(answer.evidence_scope.dataset_version, "demo-v3");
  assert.equal(answer.evidence_scope.source_sha256, replay.manifest_sha256);
  const trace = detail.getByTestId("gas-pilot-qwen-trace");
  await trace.getByText("synthetic-threshold-reference-a1", { exact: true }).waitFor();
  await trace.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("00-pilot-qwen-evidence");
  records.pilot_query = { request_id: response.headers()["x-request-id"], ...answer };
  check("pilot chart → observation → active threshold → pipe, GIS and event → grounded assistant");

  const stale = response.request().postDataJSON();
  const v2 = datasetPath + "/demo-v2";
  const legacy = await choose("demo-v2");
  assert.equal(await detail.count(), 0);
  assert.equal(await pilot.getByTestId("gas-pilot-agent").count(), 0);
  assert.equal((await http(v2 + "/agent/query", access.operator, stale)).status, 409);
  await pilot.getByRole("button", { name: "打开触发观测 " + obs, exact: true }).click();
  const followUp = responseFor(v2 + "/agent/query", "POST");
  await detail.getByLabel("试点只读问答").fill("这个测点当时使用哪个阈值版本？");
  await detail.getByRole("button", { name: "查询试点证据" }).click();
  const freshResponse = await followUp;
  assert.equal(freshResponse.status(), 200);
  const fresh = await freshResponse.json();
  assert.equal(fresh.evidence_scope.dataset_version, "demo-v2");
  assert.equal(fresh.evidence_scope.source_sha256, legacy.manifest_sha256);
  assert.equal(fresh.model_state, mode === "ready" ? "online" : "unavailable");
  assert.ok(!fresh.answer.includes("demo-v3") && fresh.answer.includes("pilot-r1"));
  await detail.locator('[data-testid="gas-pilot-agent"][data-evidence-version="demo-v2"]').waitFor();
  const currentBinding = freshResponse.request().postDataJSON();
  for (const [question, reason] of [
    ["这个观测能关联 GAS-M0 的两案吗？", "cross_dataset_unverified"],
    ["引用 demo-v3 中刚才的观测", "reference_outside_scope"],
  ]) {
    const refused = await http(v2 + "/agent/query", access.operator, { ...currentBinding, question });
    assert.equal(refused.status, 200);
    const body = await refused.json();
    assert.equal(body.blocked_reason, reason);
    assert.equal(body.model_calls, 0);
    assert.deepEqual(body.citations, []);
  }
  records.pilot_follow_up = fresh;
  check("pilot follow-up binds demo-v2, rejects the previous manifest and unverified associations");
  await choose("demo-v3");
  await page.route("**/versions/demo-v3/replay?*", (route) =>
    route.fulfill({ status: 503, json: { detail: "synthetic retry check" } }), { times: 1 });
  await pilot.getByRole("button", { name: "读取历史回放", exact: true }).click();
  const retry = pilot.getByRole("button", { name: "重试历史回放", exact: true });
  await retry.waitFor();
  const recovery = responseFor(v3 + "/replay");
  await retry.click();
  assert.equal((await recovery).status(), 200);
  check("version switching clears local evidence; stale binding 409 and 503 retry");

  await pilot.getByRole("button", { name: "打开触发观测 " + obs, exact: true }).click();
  const opened = responseFor(observationPath);
  await detail.getByRole("button", { name: "用 GAS-M0 观测进入 CUV 演示", exact: true }).click();
  const linked = await opened;
  assert.equal(linked.status(), 200);
  const source = await linked.json();
  assert.equal(source.reference.observation_id, observationId);
  assert.equal(source.asset_id, assetId);
  records.pilot_handoff = { reference: source.reference, asset_id: source.asset_id, event_id: source.event?.event_id };
  await pilot.waitFor({ state: "hidden" });
  check("explicit handoff uses GAS-M0 evidence for CUV and clears pilot context");
}

try {
  assert.equal((await page.goto(web + "/#/gas", { waitUntil: "networkidle" })).status(), 200);
  await page.locator("[data-testid=gas-demo-model-status][data-status='" + mode + "']").waitFor();
  assert.equal((await fetch(api + observationPath + "?observation_id=" + observationId + "&manifest_sha256=" + "0".repeat(64))).status, 401);
  assert.equal((await http(collaboration, access)).status, 403);
  assert.equal((await http(disposition, access, { expected_revision: 0, target_status: "acknowledged", reason: "验收" })).status, 403);
  check("FastAPI TCP 401 and reader 403");
  for (const [question, reason] of [
    ["用 demo-v3 的试点监测比较当前两个方案", "cross_dataset_unverified"],
    ["能推断现场供气恢复时长吗？", "engineering_evidence_unavailable"],
  ]) {
    const response = await http(agentPath, access.operator, { question, tool_hint: "cuv.query" });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.error_code, reason);
    assert.equal(body.model_calls, 0);
    assert.deepEqual(body.tools, []);
  }
  check("cross-scope association and unsupported engineering claims are blocked before model calls");
  const reportHttp = await http(replayPath);
  assert.equal(reportHttp.status, 200);
  const report = await reportHttp.json();
  const { network_content_sha256, case_content_sha256, replay_content_sha256 } = report.topology_evidence;
  const sourceBinding = { network_content_sha256, case_content_sha256, replay_content_sha256 };
  const queryString = (binding) => engineeringPath + "?" + new URLSearchParams(binding);
  assert.equal((await fetch(api + queryString(sourceBinding))).status, 401);
  const assessmentHttp = await http(queryString(sourceBinding), access);
  assert.equal(assessmentHttp.status, 200);
  const assessment = await assessmentHttp.json();
  assert.deepEqual(assessment.binding, sourceBinding);
  assert.equal(assessment.capabilities[0].status, "ready_synthetic");
  for (const capability of physicalCapabilities) {
    const refused = await http(agentPath, access.operator, {
      question: "计算当前 CUV 工程方案", tool_hint: "cuv.query", cuv_capability: capability,
    });
    assert.equal(refused.status, 200);
    const body = await refused.json();
    assert.equal(body.error_code, "engineering_capability_blocked");
    assert.equal(body.model_calls, 0);
    assert.deepEqual(body.tools[0].data, {});
    const item = body.tools[0].engineering_capability;
    assert.equal(item.capability_id, capability);
    assert.equal(item.status, "blocked");
    assert.equal(item.model_version, null);
    assert.ok(item.missing_input_ids.length);
  }
  assert.equal((await http(queryString({ ...sourceBinding, replay_content_sha256: "0".repeat(64) }))).status, 409);
  check("bound engineering status requires ACL; stale source and three unvalidated physical calculations are rejected");

  assert.equal((await fetch(api + benchmarkPath)).status, 401);
  assert.equal((await http(benchmarkPath + "?inlet_psia=900", access)).status, 422);
  const benchmarkHttp = await http(benchmarkPath, access);
  assert.equal(benchmarkHttp.status, 200);
  const benchmark = await benchmarkHttp.json();
  assert.equal(benchmark.scope, "public_reference_benchmark");
  assert.equal(benchmark.progress.benchmark_status, "benchmark_passed");
  assert.equal(benchmark.progress.field_validation_status, "pending_field_validation");
  assert.equal(benchmark.cases.length, 8);
  assert.ok(benchmark.cases.every(({ passed, solve }) => passed && solve.status === "converged" &&
    solve.supplied_inputs === 10 && solve.required_inputs === 10));
  assert.deepEqual(benchmark.cases.map((row) => row.expected.value), [163.26, 163.18, 100.012, 946.24, 814.7, 814.7, 13.30, 13.305]);
  assert.equal(benchmark.binding.model_version, "weymouth-uscs-0.2.0");
  assert.equal(benchmark.comparison.basis, "same_boundary_except_diameter");
  const diameter = benchmark.cases.find((row) => row.case_id === "case13-min-diameter-menon");
  assert.ok(Math.abs(diameter.solve.solution.internal_diameter_in - 13.303651) < 1e-6);
  assert.ok(diameter.forward_check.passed && diameter.forward_check.absolute_error_mmscfd < 1e-7);
  assert.equal(diameter.source_input.inlet_pressure.unit, "psig");
  assert.equal(diameter.source_input.atmosphere.value, 14.7);
  assert.equal(diameter.input.inlet_psia, 1414.7);
  assert.equal(benchmark.network_prototype.status, "benchmark_passed");
  assert.equal(benchmark.network_prototype.field_validation_status, "pending_field_validation");
  assert.equal(benchmark.network_prototype.checks.length, 5);
  assert.ok(benchmark.network_prototype.checks.every((row) => row.passed));
  const bound = { question: "比较公开 Weymouth 基准", tool_hint: "cuv.benchmark.query", benchmark_binding: benchmark.binding };
  const staleBenchmark = await http(agentPath, access.operator, {
    ...bound, benchmark_binding: { ...bound.benchmark_binding, suite_sha256: "0".repeat(64) },
  });
  assert.equal(staleBenchmark.status, 409);
  const mixed = await http(agentPath, access.operator, { ...bound, asset_id: assetId, event_id: eventId });
  assert.equal(mixed.status, 200);
  const mixedBody = await mixed.json();
  assert.equal(mixedBody.error_code, "benchmark_scope_conflict");
  assert.equal(mixedBody.model_calls, 0);
  assert.deepEqual(mixedBody.tools, []);
  check("public benchmark solves the fixed published cases and rejects client input, stale versions and case joins");

  if (profile === "pilot") {
    await openPilotWorkflow();
  } else {
    await page.getByTestId("gas-monitoring-open").click();
    const channel = page.locator("[data-monitoring-asset='" + assetId + "']");
    await channel.locator("li[data-monitoring-event=limit_exceeded]").first().locator("summary").click();
    const observed = responseFor(observationPath);
    await channel.locator("button[data-observation-id='" + observationId + "']").click();
    assert.equal((await observed).status(), 200);
  }
  const detail = page.getByTestId("gas-selected-observation");
  await detail.getByText("压力 6.9 kPa", { exact: true }).waitFor();
  await detail.getByText(eventId, { exact: true }).waitFor();
  await page.locator("[data-testid=gas-asset-detail][data-asset-id='" + assetId + "']").waitFor();
  await page.locator("[data-asset-id='" + assetId + "'][data-asset-focus=selected]").waitFor();
  assert.ok(decodeURIComponent(new URL(page.url()).hash).includes("/events/" + eventId + "/assets/" + assetId));
  assert.ok((await detail.innerText()).includes("样例上限 6 kPa"));
  if (mode === "ready") await shot("01-selected-observation");
  check("original observation → linked event and highlighted asset");

  await query("当前选中异常", () => detail.getByRole("button", { name: "向 Qwen 查询此异常" }).click(), "monitoring.query");
  const related = await query("关联管段", () => send("关联管段有哪些？"), "event.query");
  assert.deepEqual(related.related_pipe_ids, [assetId]);
  await query("异常证据", () => send("解释异常对应的证据"), "monitoring.query");
  const samplePolicy = page.getByTestId("gas-agent-monitoring-policy");
  await samplePolicy.locator("summary").click();
  await samplePolicy.getByText(/清单 SHA-256/).waitFor();
  if (mode === "ready") await shot("02-qwen-observation");
  await page.getByTestId("gas-agent-observation-link").click();
  await page.locator("[data-asset-id='" + assetId + "'][data-asset-focus=selected]").waitFor();
  check("Agent evidence jump returns to source and asset");

  await detail.getByRole("button", { name: "比较 CUV 两案" }).click();
  const comparison = page.getByTestId("gas-cuv-comparison");
  const before = await query("两案取舍", () => comparison.getByTestId("gas-cuv-ask-qwen").click(), "cuv.query");
  assert.deepEqual(before.plans.map((plan) => plan.affected_service_hours), [35, 37]);
  assert.ok(before.unknown_metrics.every((metric) => metric.status === "unknown" && metric.value === null));
  assert.equal(before.confirmed_comparison, null);
  const planProof = page.locator('[data-agent-cuv-plan="' + staged + '"]');
  await planProof.locator("summary").click();
  await planProof.getByText(/累计图影响 35/).waitFor();
  await closeAssistant();

  const human = comparison.getByTestId("gas-cuv-collaboration");
  await human.locator("summary").first().click();
  await human.getByRole("heading", { name: "已确认条件 · 版本 0" }).waitFor();
  await human.getByRole("combobox").selectOption("min_topology_restoration_hours");
  await human.getByLabel("新增图不可达服务峰值上限", { exact: true }).fill("10");
  await human.getByLabel("图可达恢复时限（小时）", { exact: true }).fill("4.5");
  await human.getByLabel("调整原因或反馈", { exact: true }).fill("合成联调：按图可达恢复优先");
  await human.getByTestId("gas-cuv-review").click();
  const draft = await query("待确认条件变化", () => human.getByTestId("gas-cuv-ask-draft").click(), "cuv.query");
  assert.equal(draft.comparison_change.status, "preview");
  assert.equal(draft.comparison_change.confirmed_revision, null);
  assert.deepEqual(draft.comparison_change.before.comparison.leading_plan_ids, [staged]);
  assert.deepEqual(draft.comparison_change.after_comparison.leading_plan_ids, [hold]);
  assert.deepEqual((await (await http(collaboration)).json()).audit, []);
  await closeAssistant();

  const confirming = responseFor(collaboration, "POST");
  await human.getByTestId("gas-cuv-confirm").click();
  const committed = await confirming;
  assert.equal(committed.status(), 200);
  const saved = await committed.json();
  assert.equal(saved.revision, 1);
  assert.equal(saved.audit[0].monitoring_ref.observation_id, observationId);
  assert.ok(saved.audit[0].actor_id && saved.audit[0].occurred_at);
  await human.getByRole("heading", { name: "已确认条件 · 版本 1" }).waitFor();
  await human.getByTestId("gas-cuv-collaboration-audit").locator("summary").first().click();
  if (mode === "ready") await shot("03-cuv-confirmed");
  await comparison.locator("xpath=ancestor::dialog").getByRole("button", { name: "关闭窗口", exact: true }).click();
  await page.getByRole("button", { name: /^智能助手/ }).click();
  const staleComparison = responseFor(agentPath, "POST");
  await send("上一轮两案的版本和差异是什么？");
  const staleResponse = await staleComparison;
  assert.equal(staleResponse.status(), 409);
  assert.equal((await staleResponse.json()).detail, "gas_cuv_collaboration_revision_conflict");
  await page.getByTestId("gas-agent-error").filter({ hasText: /推演输入或比较版本已变更/ }).waitFor();
  assert.equal(await page.getByTestId("gas-agent-history").count(), 0);
  await closeAssistant();
  await detail.getByRole("button", { name: "比较 CUV 两案" }).click();
  await human.locator("summary").first().click();
  const changed = await query("确认后解释变化", () => human.getByTestId("gas-cuv-ask-confirmed").click(), "cuv.query");
  const versionProof = page.getByTestId("gas-agent-cuv-version");
  await versionProof.locator("summary").click();
  await versionProof.getByText(saved.input_sha256, { exact: true }).waitFor();
  if (mode === "ready") await shot("03-cuv-qwen-version");
  check("stale agent follow-up is rejected; the confirmed control reads revision 1");
  assert.equal(changed.confirmed_comparison.revision, 1);
  assert.equal(changed.comparison_change.status, "confirmed");
  assert.equal(changed.comparison_change.confirmed_revision, 1);
  assert.equal(changed.comparison_change.before.revision, 0);
  assert.deepEqual(changed.comparison_change.after_comparison.leading_plan_ids, [hold]);
  await closeAssistant();

  const engineering = comparison.getByTestId("gas-cuv-engineering");
  await engineering.locator('[data-cuv-engineering-status="ready_synthetic"]').waitFor();
  assert.equal(await engineering.locator('[data-cuv-engineering-status="blocked"]').count(), 3);
  await engineering.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("03-engineering-input-readiness");
  await query("工程参数和校核就绪度", () => engineering.getByTestId("gas-cuv-ask-engineering").click(), "cuv.engineering.query");
  const engineeringQuery = records.queries.at(-1);
  assert.deepEqual(engineeringQuery.engineering.binding, sourceBinding);
  assert.equal(engineeringQuery.comparison_binding.expected_revision, 1);
  assert.equal(engineeringQuery.comparison_binding.input_sha256, saved.input_sha256);
  assert.deepEqual(engineeringQuery.engineering.capabilities.slice(1).map((item) => item.status), ["blocked", "blocked", "blocked"]);
  if (mode === "ready") assert.ok(engineeringQuery.explanation.includes("稳态压力与流量未就绪"));
  const engineeringAnswer = page.getByTestId("gas-agent-engineering");
  await engineeringAnswer.getByText("人工比较修订 1", { exact: true }).waitFor();
  await engineeringAnswer.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("03-engineering-qwen");
  assert.deepEqual((await (await http(collaboration)).json()).audit, saved.audit);
  await closeAssistant();
  check("Qwen engineering evidence is bound to the confirmed revision; audit and graph results stay consistent");

  const benchmarkPanel = comparison.getByTestId("gas-cuv-benchmark");
  await benchmarkPanel.locator(":scope > summary").click();
  const benchmarkCard = benchmarkPanel.getByTestId("gas-cuv-benchmark-result");
  await benchmarkCard.getByTestId("gas-cuv-benchmark-comparison").waitFor();
  assert.equal(await benchmarkCard.getAttribute("data-benchmark-status"), "benchmark_passed");
  assert.match(await benchmarkCard.innerText(), /163\.255858 MMSCFD/);
  const diameterCard = benchmarkCard.locator('[data-benchmark-case="case13-min-diameter-menon"]');
  await diameterCard.getByText("原始单位、代回残差与出处").click();
  await diameterCard.getByTestId("gas-cuv-forward-check").waitFor();
  assert.match(await diameterCard.innerText(), /1400 psig.*1414.7 psia/);
  assert.match(await diameterCard.innerText(), /13.303651 in.*文献结果 13.3 in/);
  await diameterCard.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("04-weymouth-benchmark");
  const networkCard = benchmarkCard.getByTestId("gas-cuv-network-prototype");
  await networkCard.locator(":scope > summary").click();
  await networkCard.getByText(/51\.004285 MMSCFD/).waitFor();
  await networkCard.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("04-series-parallel-benchmark");

  const queryingBenchmark = responseFor(agentPath, "POST");
  await benchmarkPanel.getByTestId("gas-cuv-ask-benchmark").click();
  const benchmarkResponse = await queryingBenchmark;
  assert.equal(benchmarkResponse.status(), 200);
  const benchmarkAnswer = await benchmarkResponse.json();
  assert.equal(benchmarkAnswer.dataset_id, benchmark.binding.suite_id);
  assert.equal(benchmarkAnswer.evidence_scope.scope, benchmark.scope);
  assert.equal(benchmarkAnswer.intent, "cuv_benchmark_query");
  assert.equal(benchmarkAnswer.tools[0].tool_name, "cuv.benchmark.query");
  assert.deepEqual(benchmarkAnswer.tools[0].benchmark_report, benchmark);
  assert.equal(benchmarkAnswer.model_calls, mode === "ready" ? 2 : 0);
  if (mode === "ready") {
    assert.equal(benchmarkAnswer.model_id, "Qwen3.8-27B");
    assert.equal(benchmarkAnswer.explanation_source, "qwen_evidence_selection");
    assert.ok(benchmarkAnswer.explanation.includes(benchmark.binding.suite_sha256));
    assert.ok(benchmarkAnswer.explanation.includes("1.647420 psia"));
    assert.ok(benchmarkAnswer.evidence_refs.some((ref) => ref.includes(diameter.input_sha256)));
    assert.ok(benchmarkAnswer.evidence_refs.some((ref) => ref.includes(benchmark.network_prototype.input_sha256)));
  }
  const benchmarkAgent = page.getByTestId("gas-agent-dialog").getByTestId("gas-cuv-benchmark-result");
  await benchmarkAgent.getByTestId("gas-cuv-benchmark-comparison").waitFor();
  await benchmarkAgent.scrollIntoViewIfNeeded();
  if (mode === "ready") await shot("04-weymouth-qwen");
  records.benchmark = benchmark;
  records.benchmark_query = benchmarkAnswer;
  assert.deepEqual((await (await http(collaboration)).json()).audit, saved.audit);
  await closeAssistant();
  check("Qwen or rule result is bound to the public solver evidence with no graph audit changes");
  await human.getByTestId("gas-cuv-collaboration-audit").locator("summary").first().click();
  await human.getByTestId("gas-cuv-audit-observation").click();
  await detail.waitFor();
  check("CUV draft is read-only and confirmed revision 1 links back to original evidence");

  await detail.getByRole("button", { name: "打开观测关联事件" }).click();
  const detailPanel = page.getByTestId("gas-event-detail");
  const action = detailPanel.getByTestId("gas-disposition-panel");
  await action.getByLabel("操作说明").fill("合成联调：已人工确认");
  const transitioning = responseFor(disposition, "POST");
  await action.getByTestId("gas-disposition-transition").click();
  const receipt = await (await transitioning).json();
  assert.equal(receipt.revision, 1);
  const audit = receipt.audit[0];
  assert.deepEqual([audit.from_status, audit.to_status], ["pending", "acknowledged"]);
  assert.equal(audit.monitoring_ref.observation_id, observationId);
  assert.equal(audit.event_id, eventId);
  await action.getByText("已记录已确认，处置版本 1。").waitFor();
  await action.getByTestId("gas-disposition-audit").locator("summary").click();
  await shot("04-disposition-audit");
  await action.getByTestId("gas-disposition-audit-observation").click();
  await page.locator("[data-asset-id='" + assetId + "'][data-asset-focus=selected]").waitFor();
  check("manual disposition and audit link to original observation");

  await page.locator(".gas-event-list button[data-event-id='syn-gas-event-isolation']").click();
  await page.locator("[data-testid=gas-event-detail][data-event-id='syn-gas-event-isolation']").waitFor();
  assert.equal(await detail.count(), 0);
  assert.ok(!new URL(page.url()).hash.includes(observationId));
  assert.deepEqual(records.page_errors, []);
  check("new event clears previous observation context");
  records.cuv_audit = saved.audit[0];
  records.disposition_audit = audit;
  records.status = "passed";
} finally {
  await browser.close();
  await writeFile(join(output, "workflow-" + size + ".json"), JSON.stringify(records, null, 2) + "\n", { mode: 0o600 });
}
console.log(JSON.stringify({ status: records.status, checks: records.checks.length, queries: records.queries.length, size, mode }));
