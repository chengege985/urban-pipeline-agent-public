# React Provider 字段适配矩阵 v1.0（UNIFY-PRODUCT-01 任务C）

日期：2026-11-03。范围：`02_比赛作品/web/`（统一产品的 React 正式源码）。
本轮只新增 Provider/Adapter 与契约测试，**未把页面切到 live**，未发布 rc.2。

## 1. Provider 状态语义

| 状态 | 触发条件 | 允许展示 | 禁止 |
| --- | --- | --- | --- |
| `demo` | `DemoProvider`（默认） | `demo-score-0.1.0`、`demo-timeline-0.1.1`、`demo-decision-0.2.0`、24 管段合成数据 | 声称是平台结果 |
| `live` | `PlatformProvider.probe()` 收到 `200` 且含 `platform_version` | 平台数据集视图、平台 Run 结果、平台时间线 | 在浏览器重算评分/断连/效用/恢复时间 |
| `unavailable` | 未探测、探测失败、`5xx`、超时、结构守卫失败 | 错误原因 + 重试/切换 demo | 展示上一次结果充当本次 |

方法级行为：

| 方法 | `demo` | `live`（本轮实现） | 不可用时 |
| --- | --- | --- | --- |
| `loadDataset` | 本地 24 管段 | `GET /datasets/{id}/view` → Adapter | 抛 `ProviderUnavailableError` |
| `evaluate` | `evaluateDemoHealth` | 返回视图内既有评分；**带 `override` 直接拒绝** | 同上 |
| `simulate` | 本地 BFS | `POST /simulation-runs` + 轮询 | 同上 |
| `comparePlans` | 本地 `compareDemoPlans` | `POST /decision-runs` + 轮询 | 同上 |
| `checkTimeline` | 本地能力检查 | `POST /timeline/check` | 同上 |
| `timeline` | 本地回放 | `POST /timeline/evaluate` | 同上 |
| `ask` | 模板回答 | **一律拒绝**（Agent 未复审） | `ProviderUnavailableError` |

## 2. 数据集字段适配（`DatasetView` → `WaterPulseDataset`）

### 2.1 元信息

| UI 字段 | 平台来源 | 变换 | 缺失/异常 | 语义风险 |
| --- | --- | --- | --- | --- |
| `meta.mode` | 常量 | 固定 `"live"` | — | 仅表示数据来自平台接口 |
| `meta.appVersion` | `meta.platform_version` | 原值 | 视图缺字段直接失败 | 平台版本 ≠ 评分版本 |
| `meta.dataVersion` | `meta.sha256` | 原值 | 缺字段失败 | 内容哈希，不是“最新版本” |
| `meta.modelVersion` | `pipes[].score.model_version` | 去重后唯一取该值，否则 `"unavailable"` | 多版本并存不选一个 | 不合并 `demo-score-0.1.0` |
| `meta.disclaimer` | 常量 + 视图限制 | 拼接 | — | 固定声明 |

### 2.2 管段

| UI 字段 | 平台来源 | 变换 | 缺失时 | 风险 |
| --- | --- | --- | --- | --- |
| `Pipe.id` | `pipes[].pipe_id` | 原值 | — | 与 `edge_id` 严格区分 |
| `Pipe.name` | `pipes[].name` | 回退 `pipe_id` | 回退显示 ID，不编造名称 | 低 |
| `Pipe.fromNodeId/toNodeId` | `pipes[].from_node_id/to_node_id` | 原值；缺省 `""` | 空串（不猜节点） | 中：空串不可参与拓扑 |
| `Pipe.material` | `asset_hints.material_hint` | 白名单匹配，未命中→`未知` | `未知` | 上游单位/命名未核实 |
| `Pipe.installYear` | `asset_hints.install_year_hint` | 原值 | `null` | 不参与前端评分 |
| `Pipe.diameterMm` | `asset_hints.diameter_mm_hint` | 原值 | `null` | 单位 mm（视图已声明） |
| `Pipe.depthM` | 无 | `null` | `null` | 平台视图不提供埋深 |
| `Pipe.pressure` | 无 | `未知` | `未知` | 平台压力等级未提供 |
| `Pipe.leakCount/repairCount` | 无 | `null` | `null` | 平台视图不提供 |
| `Pipe.monitoring` | 无 | `未知` | `未知` | 平台 monitoring 仅在评分行内 |
| `Pipe.lastInspection` | 无 | `null` | `null` | 不填当天日期 |
| `Pipe.serviceIds` | 无（服务只挂节点） | `[]` | `[]` | **不得**由节点反推管段-服务关系 |
| `Pipe.lengthM` | 无 | `null` | `null` | 不填 0 |
| `Pipe.note` | 常量说明 | — | — | 标注字段来源 |

### 2.3 评分

| UI 字段 | 平台来源 | 变换 | 缺失时 |
| --- | --- | --- | --- |
| `HealthAssessment.score` | `pipes[].score.hs` | 原值（1 位小数） | `null` + `status="insufficient_data"` |
| `.grade` | `score.grade` | 枚举白名单，未识别→`数据不足` | `数据不足` |
| `.confidence` | `score.completeness_percent` | 原值 | `0`（仅当无评分） |
| `.validWeight` | 同上 /100 | UI 派生 | 同上 |
| `.factors` | 无（视图不返回指标分解） | `[]` | 空数组，`summary` 说明原因 |
| `.missing` | `score_issue` | 文本 | 缺评分时数组含原因 |
| `.version` | `family/version` | 拼接 | `"unavailable"` |
| `.status` | `score && hs !== null` | 派生 | — |

> 指标分解（8 因子）只在 demo 模式存在；live 模式必须显示“视图未返回”，不得用 DemoHealth 补齐。

### 2.4 节点 / 阀门 / 服务 / 隔离方案 / 情景

| UI 对象 | 平台来源 | 变换 | 缺失/异常 |
| --- | --- | --- | --- |
| `NetworkNode.x/y` | `nodes[].x/y` | 原值 | **任一节点缺坐标 → 抛错**（不编造布局） |
| `NetworkNode.kind` | `node.source` / 是否服务节点 | `source`→水源；服务节点→`service`；其余 `junction` | — |
| `Valve.id` | `edges[].edge_id`（`is_valve`） | 原值 | 阀门即边，不是独立设备 |
| `Valve.pipeId` | `edges[].pipe_id` | 原值 | — |
| `Valve.nodeId` | `edges[].from_node` | 仅显示 | 不代表阀门安装点核实 |
| `Valve.operable` | `edges[].operable` | `=== true` 才算可操作 | `null`→`false`（保守） |
| `ServiceObject.households/facilities` | `services[].count_value + count_unit` | `household`→户；`facility`→处；另一侧为 0 | `person` 或 `count_value=null` → **抛错**（不换算、不补 0） |
| `ServiceObject.type` | `services[].label` | 关键词映射（医院/学校/商业/居民），否则 `未知` | 仅影响图标与筛选 |
| `ServiceObject.description` | `supply_rule + node_ids` | 文本化 any/all | — |
| `IsolationPlan` | `isolation_plans[]` | `id=plan_id`、`valveIds=valve_ids`、`closedPipeIds=[]`、`evidenceIds=[evidence_ref]`、`version="platform-given"` | 平台“额外关闭管段”无此概念，保持空 |
| `NetworkScenario` | 由隔离方案**派生** | `id="scn-"+plan_id`、`failedPipeIds=边→管段映射`、`activeSourceIds=source&&active`、`severity="未知"` | 情景不是平台对象；严重度不编造 |
| `MaintenancePlan`（`plans`） | 平台视图无方案目录 | `[]` | 候选方案需显式给定 ID |
| `TimelineDataset` | 无预置事件表 | `{version:"platform-action-schedule", asOf:meta.as_of, events:[], tasks:[]}` | 不补造事件/任务 |

## 3. Run 结果适配

### 3.1 拓扑影响（`SimulationRunResult` → `ServiceImpact`）

| UI 字段 | 平台来源 | 变换 | 缺失时 |
| --- | --- | --- | --- |
| `status` | `result.status` | `succeeded`→`succeeded`，其余→`partial` | — |
| `failedPipeIds` | 由 `scenario.failedPipeIds` 提供 | 结构映射 | — |
| `closedPipeIds` | 无 | `[]` | 平台关闭阀门而非管段 |
| `valveIds` | `isolated_valve_ids` | 原值 | — |
| `activeSourceIds` | `source_node_ids`（结果信封额外字段） | 原值，缺省 `[]` | 缺省空数组 |
| `unreachableNodeIds` | `newly_unreachable_node_ids` | 原值 | 只表示“新增”不可达 |
| `affectedServices[]` | `affected_service_ids` + 数据集服务 | 名称/类型/关键性/计数取自数据集 | 服务不在数据集→抛错 |
| `affectedCriticalServiceIds` | `affected_critical_facility_ids` | 原值 | — |
| `affectedHouseholds` | `potentially_disconnected_counts.household` | 原值，**`null` 保留** | `null`（显示“—”） |
| `affectedFacilities` | `…counts.facility` | 同上 | `null` |
| `evidenceIds` | `evidence_refs` | 原值（`snapshot:…`） | 空则展示限制 |
| `limitations` | `limitations` + 未知计数提示 | 追加 | — |

> `person` 口径不映射到户/处；存在 `person` 或未知计数时只提示“合计不可用”，不换算。

### 3.2 方案比较（`DecisionRunResult` → `PlanComparison`）

| UI 字段 | 平台来源 | 变换 | 缺失时 |
| --- | --- | --- | --- |
| `status` | `result.status` | `succeeded`→`succeeded`，其余→`insufficient_data` | — |
| `decisionAlgorithmVersion` | `decision_algorithm_version` | 原值 `1.0.0` | — |
| `referenceCostYuan` | 无 | `null` | 演示参考尺度在平台无对应 |
| `effectBasis` | 常量 | `"platform_given_candidates"` | — |
| `recommendedPlanId` | `recommended_plan_id` | 原值；未知候选时为 `null` | 不自行挑选 |
| `rows[].costFen` | `metrics.total_cost_fen`（完整行）或行顶层 `total_cost_fen`（未知行） | 原值 | `null` |
| `rows[].costYuan` | 同上 | `/100` | `null` |
| `rows[].feasible` | `rows[].status` | `succeeded`→`true`；`infeasible`→`false`；其他→**`null`** | 三态 |
| `rows[].utility` | `rows[].utility` | 原值 | `null` |
| `rows[].remainingAffectedServices` | `metrics.remaining_disconnected_services` | 原值 | `null` |
| `rows[].restoredServiceCount` | 平台未在行内给出 | `null` | 不推算 |
| `rows[].riskReduction` | 平台无此口径 | `null` | 不用演示值 |
| `rows[].durationHours` | 平台无工期 | `null` | 不用恢复时间 |
| `rows[].type` | 平台无方案类型 | `null` | 不用抢修/更换/监测 |
| `rows[].resourceRequirements` | 平台在 `constraints.checks` 内 | `{}` + 限制说明 | 不搬运为需求表 |
| `rows[].rank` | `ranked_plan_ids` | 命中→下标+1；未命中→请求序追加 | 保证稳定 |
| `rows[].violations` | `reasons` | 未知行追加“结果未知” | — |
| `rows[].note` | `selection_reason` | 原值 | 空串 |
| `constraints.budgetYuan` | 请求 `budget_fen` | UI 原值回填 | — |

### 3.3 时间线（`TimelineEvaluationResponse` → `PlanTimelineResult`）

| UI 字段 | 平台来源 | 变换 | 缺失时 |
| --- | --- | --- | --- |
| `status` | `result.status` | 透传（含 `unsupported`） | — |
| `timelineStatus` | `result.timeline_status` | 透传 | `insufficient_data` |
| `planCompletion` | `recovery_times.plan_completion_hours` | 指标映射 | 缺指标→`insufficient_data` + 原因 |
| `firstTargetServiceRestored` | `first_target_service_restored_hours` | 同上 | 同上 |
| `allTargetServicesRestored` | `all_target_services_restored_hours` | 同上 | 同上 |
| `serviceFirstRestored{}` | `service_first_restored_hours` | 逐项 | 未返回→空对象 |
| `events[]` | `graph_events` + `timeline_request.event_evidence_refs` | 逐事件 | — |
| `asOf` | `dataset.as_of` | 原值 | — |
| `dataMode/sourceStatus` | `dataset.data_mode/source_status` | 透传 | — |
| `algorithmVersion` | `algorithm.version`（能力版 `1.0.1`） | 原值 | 缺失→抛错 |
| `issues` | `result.issues + missing_items` | 文本化 | — |
| `limitations` | `limitations` | 原值 | — |
| `inputRequest` | 本地请求 | 原值（用于回查） | — |

> `observationHorizonHours` 保留平台值（含“指标缺失但窗口=8”的情况）；`null` 只在平台未返回时出现。

### 3.4 预检（`TimelineCheckResponse` → `TimelineGate`）

| UI 字段 | 平台来源 | 说明 |
| --- | --- | --- |
| `status` | `status` | 与 `can_compute` 不同：`partial` 仍可能有可用字段 |
| `canCompute` | `result.can_compute` | 只有“全部请求输出完整”才为 true |
| `supported/unsupported/availableOutputs` | 同名 | 不改写 |
| `missingItems` | `missing_items` | 对象转 JSON 文本 |
| `registryVersion` | `result.registry_version` | `1.0.1` |
| `authorizationChecked` | 常量 `false` | 预检不做授权 |

## 4. 报告字段适配（`RunEnvelope` → `PlatformReportDocument`）

| 报告字段 | 来源 | 说明 |
| --- | --- | --- |
| `reportId` | `run_id + snapshotId + dataVersion + status + algorithms` 的 FNV-1a | 同输入同 ID；不是安全哈希 |
| `runId/runKind/runStatus` | `RunView` | 原样 |
| `dataset.*` | `DatasetView.meta` | `dataVersion=sha256` |
| `algorithms[]` | Run 结果中的 `algorithm_version` / `decision_algorithm_version` / 时间线 `algorithmVersion` | 逐项版本，不写“最新” |
| `pipe.{hs,grade,…}` | 数据集视图评分 | `recomputed=false`；`rs=null`（视图不返回 Rs） |
| `simulation/decision` | Run 结果 | **字段副本**，不重排、不重算 |
| `timeline` | 调用方传入的 evaluate 结果 | 未附则 `undefined` + 限制 |
| `evidenceIds` | `snapshot:…`、`run:…` + 结果证据 | 去重 |
| `limitations` | 固定 4 条 + 数据集限制 + 缺项提示 | — |

## 5. 明确“不可映射/不映射”的字段

| UI 概念 | 原因 | 本轮处理 | 后续条件 |
| --- | --- | --- | --- |
| `riskReduction`（风险下降） | 平台无该指标（属演示 CUV 替代物） | `null`，显示“—” | 正式 CUV 指标族交付 |
| `durationHours`（工期） | 平台无工期字段；不得用恢复时间 | `null` | 任务排期接口 |
| `MaintenancePlan` 目录 | 平台只有 `actions/plans`，无演示方案元数据 | `plans: []` | 平台提供方案目录或由上游给定 |
| `Planned severity`（严重度） | 平台未分级 | `"未知"` → “未分级” | 引入分级模型 |
| `ServiceType` | 平台无类型字段 | 关键词映射，失败即“未知” | 上游补类型枚举 |
| `DemoHealth` 因子分解 | 平台视图不返回指标分解 | `factors: []` + 说明 | 复用 `legacy_health_explanation` 暴露接口 |
| 时间线预置事件/任务 | 平台时间线由措施排期驱动 | 空表 | 任务排期数据接入 |
| Agent 回答 | Agent 未复审 | 抛 `ProviderUnavailableError` | M1-FIX2 + 安全复审通过 |

## 6. 契约测试覆盖（`web/src/tests/platformProvider.test.ts`，21 项）

1. 探测成功→`live`；缺 `platform_version`→`unavailable`。
2. 传输异常→`unavailable`，不伪造 live。
3. 六管段评分原样（`78.5 / 基本健康 / 100%`）。
4. 服务 `any` 规则与户/处映射；`person` 口径拒绝。
5. 节点缺坐标→显式失败。
6. 阀门=边、情景派生、`plans=[]`、时间线空表。
7. `evaluate` 带 override 被拒绝（不在浏览器重算）。
8. 4xx 原因透传 + 敏感路径被过滤。
9. 异步 Run：入队→轮询→结果、请求体字段、幂等键格式。
10. 轮询超时拒绝展示部分结果。
11. Run `failed` 状态不被当成成功。
12. 比较：分→元、`utility`/排名/推荐来自平台。
13. 未知候选→`feasible=null` 且无推荐。
14. 候选数量/重复/非整数预算被拒绝。
15. 时间线：`plan_completion` 缺失保持 `insufficient_data`，服务恢复 6h。
16. 预检逐字段可用性透传。
17. 时间线结构不完整→显式失败。
18. 报告引用同一 Run/快照/版本，`recomputation="none"`。
19. 未结束或快照不一致的 Run 不能生成报告。
20. Agent 未启用→拒绝。
21. DemoProvider 行为不变（24 管段、`demo`）。

## 7. 与页面接线的前置条件（未做）

1. `useDataSource` 增加 `platform` 模式（`?mode=live&dataset=<snapshot_id>`），保留 `demo/unavailable`。
2. 页面读取 `describeDataset().limitations` 并展示缺项，不得静默省略。
3. 报告页在 live 模式只渲染 `PlatformReportDocument`，禁用 `buildDecisionReport` 重建分支。
4. 助手页在 live 模式显示“智能体未启用（安全复审未完成）”，不得回退模板回答。
5. M1-FIX2 复审通过 + 授权策略确认后，才允许真实 HTTP 联调与站点暴露。
