# 统一 FastAPI—React 接口合同 v1.0（UNIFY-PRODUCT-01 任务B）

日期：2026-11-03。依据：`src/platform/**` 现有实现与 `04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json`（`0.1.1`，24 条路径 / 25 个操作）。
编号说明：`v1.0` 为本轮合同冻结，不代表三方业务确认；`status` 一律为 `not_business_validated`，直到正式验收。

## 1. 现有接口盘点（是否已存在、能否直接复用）

| 要求梳理的接口 | 现状 | 响应是否有 schema | 能否直接复用 | 结论 |
| --- | --- | --- | --- | --- |
| Dataset View | 已存在 `GET /api/v1/datasets/{dataset_id}/view` | **有**（`DatasetView`） | 可 | 统一产品页面唯一读取入口 |
| Snapshot | 已存在 `GET /api/v1/snapshots`、`GET /api/v1/snapshots/{id}`、`POST /api/v1/data-batches` | 无（自由对象） | 可（字段已稳定） | 本轮补 DTO 校验，不改路由 |
| Assessment | 已存在 `GET /api/v1/assessments?snapshot_id=&pipe_id=&grade=`、`POST /api/v1/assessment-runs` | 无 | 可 | 评分读取走前者，重算走异步 Run |
| Simulation Run | 已存在 `POST /api/v1/simulation-runs` + `GET /api/v1/runs/{run_id}` | 无（请求类型化，响应自由） | 可（需轮询） | 本轮冻结结果核心字段 |
| Decision Run | 已存在 `POST /api/v1/decision-runs` + `GET /api/v1/runs/{run_id}` | 无 | 可（需轮询） | 同上，枚举语义见 §4.6 |
| Timeline Check/Evaluate | 已存在 `POST /api/v1/timeline/check`、`/evaluate` | **有**（`TimelineCheckResponse`、`TimelineEvaluationResponse`） | 可 | 含逐字段可用性 |
| Agent Chat/Thread/Resume | 已存在 `POST /api/v1/agent/chat`、`GET /api/v1/agent/threads/{id}`、`POST /api/v1/agent/threads/{id}/resume`、`GET /api/v1/agent/runs/{id}/evidence` | 无；未启用时返回 `503`，无身份时 `401` | 有条件 | **本轮不接入**（安全复审未通过） |
| Report | **不存在**；只有 `GET /api/v1/runs/{run_id}/export`（原样导出 Run JSON） | — | 不可 | 只有纯投影函数 `src/unified/report.py`，HTTP 接口为**拟建** |
| Feedback | 已存在 `POST /api/v1/feedback`、`GET /api/v1/feedback?run_id=` | 无 | 可 | 带 `expected_revision` 乐观锁 |
| Capabilities | 已存在 `GET /api/v1/capabilities` | 无 | 可 | 服务探测与能力/数据集可见性来源 |

补充事实（本轮新测试固化，`tests/test_unified_contracts.py::test_openapi_typed_surface_is_still_limited`）：

- 25 个操作中只有 3 个声明了 200 响应 schema：数据集视图、时间线 check、时间线 evaluate。
- `/api/v1/feedback` 同时提供 GET 与 POST，故“路径 24 条 / 操作 25 个”。

## 2. 统一 DTO（冻结位置：`src/unified/contracts.py`，版本 `1.0.0`）

DTO 只描述**已经存在的响应**或本轮确定的报告投影；每一项都在测试里用真实响应验证过。

| DTO | 对应接口 | 关键字段 | 校验方式 |
| --- | --- | --- | --- |
| `SnapshotSummary` / `SnapshotListEnvelope` | `GET /api/v1/snapshots` | `snapshot_id, sha256(64hex), created_at, name, data_mode, source_status, as_of, validation_status, counts{assessments,nodes,edges,services,plans}` | `extra="forbid"` 全量校验 |
| `AssessmentEnvelope` | `GET /api/v1/assessments` | `snapshot_id, data_mode, as_of, total, items[Assessment]` | 复用平台 `Assessment` |
| `CapabilityDataset` / `AlgorithmSummary` / `CapabilitiesResponse` | `GET /api/v1/capabilities` | `platform_version, scoring, simulation, decision, formal_scoring=unavailable, datasets[], data_modes[], algorithms[]` | `extra="forbid"` |
| `RunView` | `POST *-runs`、`GET /api/v1/runs/{id}` | `run_id, kind, status, created_at, finished_at, request, result, review_status, review_revision, execution_status=not_dispatched` | 状态枚举白名单 |
| `SimulationRunResult` | `result`（simulation） | `status, engine_type=topology, algorithm_version, affected_service_ids, affected_critical_facility_ids, potentially_disconnected_counts{household,person,facility}, known_count_subtotals, unknown_count_service_ids, newly_unreachable_node_ids, isolated_valve_ids, evidence_refs, limitations, missing_items` | 声明核心字段，保留其他诊断字段 |
| `DecisionRunResult` / `DecisionPlanRow` | `result`（decision） | `status, engine_type=demo_policy, decision_algorithm_version, baseline_key(64hex), plans[], recommended_plan_id, recommendation_status, tie_plan_ids, ranked_plan_ids, missing_items, decision_limitations` | 同上；`plans[].status` 五态 |
| `FeedbackRecord` / `FeedbackListEnvelope` | `POST/GET /api/v1/feedback` | `feedback_id, run_id, revision, actor_identity, execution_status, review_status, reviewer, reason_type, reason` | `extra="forbid"` |
| `ReportDocument` | **拟建** `GET /api/v1/runs/{run_id}/report` | `report_schema_version, report_id, source=existing_run_only, recomputation=none, run_id, run_kind, run_status, dataset{snapshot_id,data_version,as_of,data_mode,source_status,schema_version}, algorithms[], scenario, pipe{…,recomputed=false}, simulation, decision, timeline, evidence_ids, limitations` | 纯投影，本轮仅函数级验证 |

## 3. OpenAPI → 前端类型的生成规则

- 生成器：`02_比赛作品/web/scripts/generate-platform-types.mjs`，只读工程冻结 OpenAPI，不启动服务、不联网。
- 产物：`02_比赛作品/web/src/generated/platformOpenapi.ts`（52 个 schema，564 行），头部记录来源路径与来源 SHA-256。
- 输出确定性：不写时间戳；同一 OpenAPI 内容得到同一文件。
- 处理规则：`$ref`/`anyOf`/`enum`/`const`/`additionalProperties` 转成 TS 联合、对象与 `Record`；未声明 schema 的接口**不会**凭空生成类型。
- 缺口处理：`/simulation-runs`、`/decision-runs`、`/snapshots`、`/assessments`、`/feedback`、`/capabilities`、`/agent/*` 无响应 schema，因此另设**领域 Adapter + 运行时结构守卫**（`src/adapters/platformRuns.ts`、`platformTimeline.ts`、`platformDataset.ts`），守卫失败抛 `PlatformContractError`。

**禁止事项**：不得用 `as SomeDto` 把不兼容对象强转成领域类型；不得在 Adapter 里补造缺失字段。

## 4. 十个重点问题的处理条款

### 4.1 `pipe_id` / `edge_id` / `valve_id`

- `pipe_id`：资产管段，评分键；出现在 `assessments[].pipe_id`、`edges[].pipe_id`、`DatasetPipe.pipe_id`。
- `edge_id`：网络边（可含平行边/阀门边）；**边不等于管段**，一个 `pipe_id` 可对应多条边。
- `valve_id`：**就是阀门边的 `edge_id`**（`is_valve=true`），不是独立对象；平台校验 `valve_ids ⊆ edges ∩ is_valve`。
- 前端映射：`Valve.id = edge_id`、`Valve.pipeId = edge.pipe_id`、`Valve.nodeId = edge.from_node`（仅显示用）。
- 禁止：把 `edge_id` 当 `pipe_id` 使用（如把 `syn-v1` 当管段渲染）；把阀门 ID 当成独立设备编号。

### 4.2 多节点服务的 `any` / `all` 规则

- 平台 `Service{supply_rule: "any"|"all", node_ids[]}`：`any`=任一节点可达即供水，`all`=全部可达才供水；影响判定在 `src/simulation/topology.py` 内完成。
- 前端不得自行推断规则；`platformProvider` 只展示平台给出的 `affected_service_ids`。
- 展示层把规则写进 `ServiceObject.description`（“任一节点可达即供水 / 全部节点可达才供水”），不参与计算。
- 反例（已测试）：`syn-dual-inlet` 有 2 个节点、规则 `any`，正常情况下不因单点不可达而算受影响。

### 4.3 评分模型族与算法版本

- `model_family`：当前只有 `legacy_demo`；`model_version`：`0.1.1`。
- 数据集视图只在 `family/version` **完全匹配**时返回 `score`；否则 `score=null` + `score_issue` + `incompatible_pipe_ids`，**不重新贴标签**。
- 其他版本：`demo-score-0.1.0`（比赛演示）、`demo-policy`（方案价值配置）、`topology 0.1.0`、`decision 1.0.0`、`timeline 1.0.1`、`recovery 1.0.0`。
- 报告与前端必须逐项登记 `capability_id + version`；不得写“最新版本”。

### 4.4 费用分与元转换

- 平台唯一费用单位是**分**（`cost_fen`、`total_cost_fen`、`budget_fen`），整数字段带 `strict` 校验。
- 前端展示用元：`costYuan = costFen / 100`（保留原始 `costFen`，避免回推漂移）。
- 提交预算：`budgetYuan → fen` 必须**精确整数**；`1.005 元` 这类无法精确换算的输入直接拒绝（`PlatformContractError`，已测试）。
- 禁止：用 `Math.round` 静默吸收误差；把分与元混在同一字段。

### 4.5 时点、持续时间和恢复时刻

| 概念 | 平台字段 | 语义 | 禁止 |
| --- | --- | --- | --- |
| 评价时点 | `snapshot.as_of` / `DatasetView.meta.as_of` | 快照冻结的评价时钟；请求 `as_of` 必须与之一致 | 用当前时间替代 |
| 观察窗口 | `horizon_hours` | 从 `as_of` 起算的窗口长度 | 把窗口当恢复时间 |
| 任务结束 | `action_schedule[].finish_offset_hours` | 任务实际结束记录；缺省则“方案完成时间”不可得 | 用 `action.effective_after_hours` 冒充 |
| 措施生效 | `actions[].effective_after_hours` | 措施生效偏移（可驱动图事件），**不是任务结束** | 写成工期 |
| 服务恢复 | `recovery_times.*.value_hours` | 相对 `as_of` 的**图通路**恢复时刻 | 当成水力/水质恢复 |
| 工期/时长 | 平台不提供方案工期 | UI 的 `durationHours` 在平台模式为 `null` | 用恢复时间或 0 填充 |

实测（统一六管段案例）：`plan_completion_hours` 为 `insufficient_data`（原因为“缺少每个措施的 finish 或 start+duration”），`all_target_services_restored_hours = 6.0`，两者不可互换。

### 4.6 计算 `partial` 与 `insufficient_data`

- **`partial`**：有确定结果但覆盖不全（如服务计数口径缺失、节点未全覆盖）。
- **`insufficient_data`**：缺少必要输入，未形成结论（如未给资源容量导致的候选未知）。
- 前端映射：`PlanComparisonRow.feasible` 三态（`true/false/null`）；`null` 显示“结果未知”，**不得**显示为“不可行”。
- 候选存在未知时平台 `recommended_plan_id=null`，前端不得自行挑选推荐。
- `status` 五态（`succeeded/partial/insufficient_data/infeasible/failed`）只能透传，不得合并成布尔。

### 4.7 异步 run 的状态轮询

- 创建：`POST /api/v1/{simulation,decision,assessment}-runs` → `202` + `RunView{status=queued}`。
- 轮询：`GET /api/v1/runs/{run_id}`，直到 `status ∈ {succeeded, partial, insufficient_data, infeasible, failed}`。
- 幂等：写操作必须带 `Idempotency-Key`（≤128 字符）；同一键不同内容返回 `409`。
- 客户端要求（本轮实现于 `PlatformProvider`）：`pollIntervalMs` 默认 250ms、`pollTimeoutMs` 默认 60s；超时抛 `ProviderUnavailableError`，**不展示部分结果**。
- 禁止：把 `queued/running` 当成功；在轮询失败时回退到上一次结果冒充本次。

### 4.8 数据来源与权限

- 主体解析只在服务端：`WATER_PRINCIPALS`（token → `{actor_id, modes, datasets}`）> `WATER_API_TOKEN`（开发）+ `WATER_DEV_MODES/WATER_DEV_DATASETS` > 本地演示（默认仅 `synthetic`）。
- 请求头不授权：`X-Water-Data-Scope` 已废弃；`X-Water-Client` 只是原型标识。
- 前端必须在 `DatasetView.meta` 展示 `data_mode / source_status / as_of / sha256 / validation_status`。
- 拒绝响应统一 `403` + 通用文案，前端不得据此推断数据集存在性。
- `fixture_replay`（500 条样例）默认不可见，需 `WATER_LOCAL_DEMO_MODES` 显式开启。

### 4.9 报告与 Run 的证据绑定

- 报告必须包含 `run_id`、`dataset.snapshot_id`、`dataset.data_version`(=快照 `sha256`)、`algorithms[].capability_id/version/validation_status`、`evidence_ids`、`recomputation="none"`。
- `report_id` 仅由 `(run_id, snapshot_id, data_version, status, algorithms)` 决定；同一组输入重复生成必须得到同一 `report_id`（已测试）。
- 报告中的业务数值必须是 Run 结果的**字段副本**（`report.decision == run.result`），不得重新计算或重排。
- 缺少评分时 `pipe=null` 并写限制，不补造。

### 4.10 数据缺失时不自动补零

已落到代码的硬规则：

1. 评分缺失 → `score=null` + `score_issue`，不补 0 分、不改等级。
2. 服务计数缺失 → `potentially_disconnected_counts[unit]=null`；前端 `affectedHouseholds/affectedFacilities` 为 `null`，显示“—”。
3. 候选指标缺失 → `metrics=null`、`utility=null`，推荐位留空。
4. 任务结束时间缺失 → `plan_completion_hours.status=insufficient_data`、`value_hours=null`。
5. 节点缺坐标 → Adapter 抛错，不编造示意坐标。
6. 平台无该口径（风险下降、工期、方案类型、服务类型）→ `null`/“未知”，不用演示值代替。

## 5. 前端适配合同（实现位置）

| 文件 | 职责 | 禁止 |
| --- | --- | --- |
| `web/src/generated/platformOpenapi.ts` | OpenAPI 生成类型 | 手改 |
| `web/src/providers/platform/transport.ts` | `FetchTransport` / `MockTransport`、安全错误文案 | 解释业务字段 |
| `web/src/adapters/platformDataset.ts` | `DatasetView → WaterPulseDataset`、结构守卫 | 重算评分/断开/坐标 |
| `web/src/adapters/platformTimeline.ts` | 时间线响应 → `PlanTimelineResult/TimelineGate` | 用默认窗口/0 填充 |
| `web/src/adapters/platformRuns.ts` | Run 结果 → `ServiceImpact/PlanComparison`、分↔元 | 重排候选、补 riskReduction |
| `web/src/adapters/platformReport.ts` | Run → 报告投影 | 重新计算业务数值 |
| `web/src/providers/platformProvider.ts` | 实现 `WaterPulseProvider`，状态与轮询 | 调用真实 Agent |

## 6. 最小新增方案（**拟建，本轮未实现**）

以下三项都是“加类型/加只读投影”，不改既有授权判定，也不改业务算法。未实现前不得在文档或页面写成已具备。

### 6.1 `GET /api/v1/runs/{run_id}/report`

- 目的：让页面报告完全来自服务端投影，替代前端重建分支。
- 最小实现：复用 `authorize_run` → `store.get_task` → `store.get_snapshot` → `src/unified/report.build_report_from_run`；`response_model=ReportDocument`。
- 约束：只读；`queued/running` 返回 `409`；快照与 Run 不一致返回 `409`；不写库、不重算。
- 验收：Python 侧已具备纯函数测试；补路由后需增加 HTTP 级测试并更新 OpenAPI。

### 6.2 为现有自由对象补 `response_model`（纯类型化）

- 涉及：`/api/v1/snapshots`、`/snapshots/{id}`、`/assessments`、`/capabilities`、`/runs`、`/runs/{id}`、`/feedback`、`/simulation-runs`、`/decision-runs`。
- 最小实现：直接使用 §2 已冻结的 DTO；不得在此过程中改变字段名或取值。
- 风险：`RunView.result` 是 `kind` 相关联合，需要按 `kind` 分别声明或保留自由对象并仅类型化外层。

### 6.3 Agent 侧 `run_context` 注入（非新接口）

- 位置：`AgentRuntime.process_once`（IDE-AGENT-05 §11 已给补丁建议，需 M1-FIX2 负责人合并）。
- 本轮不实现、不伪造；未注入时 Agent 对外部模型的自由文本分类为 `unknown`，属 fail-closed。

## 7. 本轮明确“未完成”的事项

1. 报告 HTTP 接口不存在（`GET /api/v1/runs/{run_id}/report` 返回 404，已断言）。
2. `/simulation-runs`、`/decision-runs` 等仍无响应 schema（已断言，避免被当作已完成）。
3. 真实 Agent 未启用（无 `AGENT_ENABLED`），`POST /api/v1/agent/chat` 返回 `401/503`（已断言）。
4. 前端页面尚未切到 `PlatformProvider`（本轮只完成 Provider/Adapter 与契约测试）。
5. 未做浏览器到服务的 E2E、未发布站点、未调用真实模型。
6. 平台 CORS/Origin 与 `TrustedHost` 仍按本地原型限制（见依赖清单）。
