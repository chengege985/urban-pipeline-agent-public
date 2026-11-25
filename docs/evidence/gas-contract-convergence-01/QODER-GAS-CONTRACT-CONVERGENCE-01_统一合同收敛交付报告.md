# QODER-GAS-CONTRACT-CONVERGENCE-01 统一合同收敛交付报告

- 任务：燃气前端运行时合同与统一平台收敛
- 分支：`feature/gas-contract-convergence-01`
- 基提交：`be04c41`（`integration/gas-synthetic-candidate`）
- 交付提交：`728cd7bfbe53e99ec55255a17b6bd8f75b051917`（代码与证据）；本报告位于同一分支的后续 `docs(gas)` 提交（HEAD）
- 工作区：`/workdata/WaterPipNet/01_工程项目/experiments/gas_contract_convergence_20261106/repo`（独立 worktree）
- 交付日期：2026-11-06
- 状态：前端合同收敛与静态 Chromium 负向验证 DONE；真实 HTTP / 认证集成 BLOCKED；整体集成 PASS **不由本报告宣布**

---

## 0. 结论摘要

| 目标 | 结果 | 证据 |
| --- | --- | --- |
| 一个权威后端 OpenAPI | DONE：85 schemas / 28 paths，静态文档与运行时 `build_openapi` 字节一致 | `contract-identity.json`、`contracts:check` |
| 一套生成 TypeScript 类型 | DONE：仅 `src/generated/platformOpenapi.ts`，未手改，`contracts:check` 无 drift | SHA `6f1e29bd…` |
| 一个受控共享 Transport 入口 | DONE：燃气 API 无自建 fetch 客户端，复用 `FetchTransport` | `api.ts`、`git diff` |
| 一个运行时校验边界 | DONE：`features/gas/contract.ts` 覆盖 14 个嵌套 DTO + 跨集合引用 | 14 项单测 |
| 一套明确前端错误状态 | DONE：`contract_error` / `unavailable` + 细分 code | 浏览器 26 项检查 |
| 一个燃气业务数据来源 | DONE：唯一 `/api/v1/gas/cases/{case_id}`，无供水回退、无浏览器重算 | `GasPage.tsx` |
| 真实 HTTP 与认证集成 | **BLOCKED**：Codex 认证整改尚未提交、未经独立审核 | 第 11 节 |
| 与旧 71-schema 分支的关系 | 未 cherry-pick，按 85-schema 重写运行时边界 | 第 4 节 |

---

## 1. 基线与隔离环境核验

### 1.1 两条代码线与共同祖先

| 项 | 值 |
| --- | --- |
| 集成候选仓库/worktree | `experiments/modular_collab_20261104/repo` → worktree `experiments/gas_adapter_dashboard_20261105/integration-gas-synthetic-candidate` |
| 集成候选提交 | `be04c41 docs: refresh gas test matrix hash` |
| 前端修复仓库/worktree | `experiments/gas_http_robustness_20261106/frontend` |
| 前端修复提交 | `6790ca8 fix(gas): guard malformed HTTP responses and add retry states` |
| 共同祖先 | `69fcac25383e3110904cf77b1c18144695b9d7ef`（已验证：`be04c41` 祖先链包含该提交，领先 7 个提交；`6790ca8` 相对该提交领先 4 个提交） |
| 本任务 worktree | `experiments/gas_contract_convergence_20261106/repo`，从 `be04c41` 检出，未从 71-schema 分支覆盖 |
| `main` | `69fcac2`（本地）/ `602b568`（`origin/main`）；**未合并、未推送** |

### 1.2 Codex 认证整改状态（视为 BLOCK 依据）

- `fix/gas-integration-auth-default` 的 HEAD 仍为 `be04c41`，认证修复**尚未提交**。
- 该 worktree 未提交内容：`engineering/src/platform/app.py`、`engineering/src/platform/authz.py`、`engineering/tests/test_m1fix2_identity.py`、`engineering/tests/test_m1fix_authz.py`、新增 `engineering/tests/test_gas_auth_default.py`。
- 上述修复**没有**独立审核记录，也没有可引用的提交哈希。
- 因此本任务按要求：**先完成隔离分支上的前端适配，不形成正式集成版本，不做真实认证/HTTP 集成验证**。

### 1.3 保护范围遵守情况

| 保护对象 | 是否修改 |
| --- | --- |
| Agent 冻结文件 | 否 |
| 燃气 / 供水业务算法（`engineering/src/**`） | 否（本提交 0 个 `engineering/` 文件） |
| GAS-M0 原始 fixture | 否（新 fixture 是 FastAPI 响应副本，独立文件） |
| Report、正式数据库 | 否 |
| Sites 部署、GitHub main | 否 |
| Codex 统一认证模块 | 否（仅共享 Transport 增加可选 `signal`） |

---

## 2. 合同版本差异表（71 → 85）

### 2.1 文档级

| 维度 | 旧线 `6790ca8` | 新线 `be04c41` / 本分支 |
| --- | --- | --- |
| OpenAPI 版本 | `0.1.1` | `0.1.1` |
| paths | 28 | 28 |
| schemas | **71** | **85** |
| Gas 相关 schema | 6：`GasCaseList`、`GasCaseResponse`、`GasCaseSummary`、`GasCounts`、`GasMetric`、`GasScoringContract` | 20（新增 14 个嵌套 DTO，见 2.3） |
| OpenAPI 文件 SHA-256 | `185dd7a98c80f342fd628de3c4549d041d4420cf5494d4c8cc4b5c0d006330c4` | `9a97933e5a36335efa0ae6569f63897a1e3f1230c8f841973d3e35f14f23a591` |
| Gas 路由 | `/api/v1/gas/cases`、`/api/v1/gas/cases/{case_id}` | 同（仅响应模型收紧） |
| 生成类型 diff | `Gas*` 接口 6 个 | `Gas*` 接口 20 个 |

### 2.2 顶层字段：28 个字段名相同但**语义不同**

`GasCaseResponse` 两条线均为 28 个必填 top-level 字段，字段名与顺序完全一致，这正是旧版"28 个字段相同"的表象。但同一批字段的**值类型**发生了结构性替换：

| 字段 | 旧线 `6790ca8` | 新线 `be04c41` |
| --- | --- | --- |
| `nodes` | `array<object additionalProperties=true>` | `array<GasNode>`（required 4，props 5） |
| `edges` | `array<object additionalProperties=true>` | `array<GasEdge>`（required 7，props 7） |
| `pipes` | `array<object additionalProperties=true>` | `array<GasPipe>`（required 7，props 7） |
| `valves` | `array<object additionalProperties=true>` | `array<GasValve>`（required 6，props 6） |
| `sources` | `array<object additionalProperties=true>` | `array<GasSource>`（required 6，props 8） |
| `consumers` | `array<object additionalProperties=true>` | `array<GasConsumer>`（required 8，props 9） |
| `services` | `array<object additionalProperties=true>` | `array<GasService>`（required 8，props 8） |
| `scenario` | `object additionalProperties=true` | `GasScenario`（required 5） |
| `isolation_plan` | `object additionalProperties=true` | `GasIsolationPlan`（required 6） |
| `timeline_events` | `array<object additionalProperties=true>` | `array<GasTimelineState>`（required 12） |
| `current` | `object additionalProperties=true` | `GasCurrent`（required 12） |
| `safety` | `object<string, boolean>` | `GasSafety`（required 4，含 literal 语义） |
| `metrics` | `object<GasMetric>` | `object<GasMetric>`（不变） |
| `counts` | `GasCounts` | `GasCounts`（不变） |
| `evidence` | `object<string \| null>` | 同（不变） |

结论：**旧版的 28 字段校验只能证明"键存在"，无法证明任何一条燃气业务记录合法**；按旧 validator 的覆盖范围（`validateNode` / `validateEdge` / `validateService` / `validateMetric` / `validateScoring` / `validateIsolationPlan`）无法发现管道、阀门、气源、用户、时间线状态的合同漂移。本任务因此没有沿用旧校验，而是按 85-schema 重写。

### 2.3 新增嵌套 DTO 清单（14 个）

| Schema | required 字段数 | 属性数 |
| --- | --- | --- |
| `GasNode` | 4 | 5 |
| `GasLayout` | 4 | 4 |
| `GasEdge` | 7 | 7 |
| `GasPipe` | 7 | 7 |
| `GasValve` | 6 | 6 |
| `GasSource` | 6 | 8 |
| `GasConsumer` | 8 | 9 |
| `GasConsumerCounts` | 3 | 3 |
| `GasService` | 8 | 8 |
| `GasScenario` | 5 | 5 |
| `GasIsolationPlan` | 6 | 6 |
| `GasTimelineState` | 12 | 12 |
| `GasCurrent` | 12 | 12 |
| `GasSafety` | 4 | 4 |

### 2.4 关键枚举 / literal 差异

旧线把 `safety` 当作任意布尔字典，新线把安全语义固化为四个具名布尔：`field_operation_instruction`、`safe_restoration_decision`、`physical_supply_verified`、`simulation_only`。旧线不区分 `GasCurrent` 与 `GasTimelineState`，新线要求时间线每个节点具备 `event_id`、`time_hours`、`kind`、`model_scope`、`physical_supply_verified` 等 12 项。这些都已在运行时校验中逐项落地。

---

## 3. 唯一 OpenAPI 与生成 TypeScript

### 3.1 唯一来源

- 源：真实 FastAPI 应用导出的 OpenAPI（`engineering/src/platform/app.py` 挂载 `gas_router()`，Pydantic v2 模型生成 85 schemas）。
- 静态文档：`engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json`
- 运行时文档：进程内 `build_openapi()` 重新生成
- 比对结果：`static_equals_runtime: true`，两侧 SHA 同为 `9a97933e5a36335efa0ae6569f63897a1e3f1230c8f841973d3e35f14f23a591`（见 `docs/evidence/gas-contract-convergence-01/contract-identity.json`）。

### 3.2 生成流程与产物

| 项 | 值 |
| --- | --- |
| 生成命令 | `npm run contracts:check` → `node scripts/generate-platform-types.mjs --check` |
| 生成产物 | `web/src/generated/platformOpenapi.ts` |
| 产物 SHA-256 | `6f1e29bd63e4aec5941bdedd9b6aafe2514121aa548c6ef72fbf1ff5b5def759` |
| 手工编辑 | 无。`--check` 模式在产物与合同不一致时以非零码退出；本次运行 exit 0 |
| 类型门面 | `web/src/features/gas/types.ts` 只做 `export type { … } from "../../shared/contracts/platform"`（19 个类型），无手写 DTO |
| 与旧线差异 | 旧线 `types.ts` 手写了 `GasNode`/`GasEdge`/`GasService` 等 65 行 DTO；本分支已删除该手写层 |

`contracts:test`：`platform type contract: 7 assertions passed`。

---

## 4. 实际源码 Diff

### 4.1 提交级统计

```
commit 728cd7bfbe53e99ec55255a17b6bd8f75b051917
19 files changed, 7680 insertions(+), 54 deletions(-)
```

| 文件 | 变化 |
| --- | --- |
| `web/src/features/gas/contract.ts` | 新增 561 行（运行时边界） |
| `web/src/features/gas/errors.ts` | 新增 45 行 |
| `web/src/features/gas/api.ts` | 重写，+119/-?（无自建 fetch 客户端） |
| `web/src/features/gas/GasPage.tsx` | 重写 +221 |
| `web/src/providers/platform/transport.ts` | +14（`signal` + `combinedSignal`） |
| `web/src/shared/api/platformTransport.ts` | 导出 `safeDetail` |
| `web/src/styles.css` | +13（loading/error 样式） |
| `web/tests/*`、`web/scripts/*` | 新增测试与 Chromium 脚本 |
| `docs/architecture/dependency_baseline.json` | 1 行：共享桥接 SHA 更新 |
| `docs/evidence/gas-contract-convergence-01/**` | 新增证据 |

### 4.2 请求入口：旧自建客户端 → 共享 Transport

旧线（`6790ca8`，`web/src/features/gas/api.ts` 150 行）：

```ts
class FetchGasTransport implements GasTransport {
  ...
  const response = await fetch(`${this.baseUrl}${request.path}`, { ... });
```

新线（`be04c41` + 本提交，`api.ts` 114 行）：

```ts
import { FetchTransport, safeDetail } from "../../shared/api/platformTransport";
...
const transport = options.transport ?? new FetchTransport(baseUrl);
response = await transport.send(options.signal
  ? { method: "GET", path, signal: options.signal }
  : { method: "GET", path });
```

- `api.ts` 内不再出现 `fetch(`，全仓燃气特性目录内也没有第二套客户端。
- 可注入 `PlatformTransport`（`MockTransport` / 测试替身）保持既有测试模式。

### 4.3 共享 Transport 的最小差异

```diff
 export interface TransportRequest {
   method: "GET" | "POST";
   path: string;
   body?: unknown;
   idempotencyKey?: string;
+  /** Optional caller cancellation; the shared transport owns the timeout signal. */
+  signal?: AbortSignal;
 }
```

```diff
   const controller = new AbortController();
   const timer = setTimeout(() => controller.abort(), this.timeoutMs);
+  const signal = request.signal ? combinedSignal(request.signal, controller.signal) : controller.signal;
   ...
-  signal: controller.signal,
+  signal,
```

```ts
function combinedSignal(external: AbortSignal, timeout: AbortSignal): AbortSignal {
  if (external.aborted) return external;
  const controller = new AbortController();
  const abort = () => controller.abort();
  external.addEventListener("abort", abort, { once: true });
  timeout.addEventListener("abort", abort, { once: true });
  return controller.signal;
}
```

```diff
-export { FetchTransport } from "../../providers/platform/transport";
+export { FetchTransport, safeDetail } from "../../providers/platform/transport";
```

### 4.4 未改动的关键文件（自证）

`git show --stat 728cd7b` 中 `engineering/` 文件数 = 0；`fix/gas-integration-auth-default` 的 5 个未提交文件与本次提交文件交集 = 0（第 12 节）。

---

## 5. 共享 Transport 适配证据

### 5.1 能力归属矩阵

| 能力 | 归属 | 证据 |
| --- | --- | --- |
| 认证上下文 | 平台 Transport（`X-Water-Token` 由上层注入，本次未改） | `transport.ts` header 构造未变 |
| 同源请求 | 平台 Transport（`baseUrl` + `path`） | `fetch(\`${this.baseUrl}${request.path}\`)` |
| 错误映射 | 平台 `safeDetail` + 燃气 `api.ts` 状态码映射 | `gasApi.test.ts` 敏感信息用例 |
| 请求超时 | 平台 Transport（`timeoutMs` → `AbortController`） | 既有实现，本次仅合并信号 |
| AbortSignal | 平台 Transport（本次新增可选 `signal`） | `combinedSignal` |
| 请求取消 | 页面 `AbortController` → `api.ts` → Transport | `gasApi.test.ts` 两例 |
| 响应校验 | 燃气 `contract.ts`（调用方边界，不回写 Transport） | 14 项单测 |
| 重试策略 | 页面显式按钮，Transport **不自动重试** | `GasPage` `attempt` 计数 |
| 请求追踪 | 前端 requestId（`gas-<uuid>`），非服务端 trace | UI 文案 + 浏览器断言 |

### 5.2 最小接口差异与所有权说明

- 差异仅 1 个可选字段 `TransportRequest.signal` + 1 个内部函数 `combinedSignal`，不改方法签名、不改返回类型、不改错误语义。
- 所有权：超时信号仍由 `FetchTransport` 持有；调用方信号只做合并，不覆盖超时。外部信号已中止时直接复用外部信号，避免多余监听。
- 认证模块（`engineering/src/platform/authz.py`、`app.py`）零改动。
- 共享桥接 SHA 变更记录在 `docs/architecture/dependency_baseline.json`：
  - `3bc8daec6a543daf7d1e548c4c15e32797ee991220cce0e018740f0d35282bc5` → `0bc4e59864382290a01fdbec4b8412b2b3403f41c1b999d44bca144b56148458`
  - `boundaries:check` 结果：`status=PASS`、`checked_files=438`、`dependency_edges=1211`、`violations=[]`、`legacy_cycle_count=0`。

---

## 6. 运行时校验边界

### 6.1 实现要点

`web/src/features/gas/contract.ts`（561 行，32 处 `addIssue` 调用点）：

| 校验维度 | 实现 |
| --- | --- |
| 顶层必填 | 28 个 key 逐一要求存在；`rejectUnknown` 拒绝未知顶层字段 |
| 顶层 literal | `schema_version=gas-case-view-0.1.0`、`data_mode=synthetic`、`status=partial`、`model_status=candidate`、`validation_status=not_business_validated`、`semantic=graph_connectivity_only` |
| 嵌套对象 | `validateLayout/Node/Edge/Pipe/Valve/Source/Consumer/Service/Scenario/IsolationPlan/State/Metric/Scoring/Safety/ConsumerCounts` 共 14 个 |
| 数组元素 | `requireRecordArray` 逐元素校验，错误路径带 `[index]` |
| 枚举 / 状态 | `node_type`、`direction`、`initial_state`、`cut_semantics`、`asset_status`、`consumer_kind`、`status`、`physical_supply_status`、`supply_rule`、`metric.status` 等 `requireOneOf` |
| 数字与范围 | `analysis_window_hours>=0`、`time_hours>=0`、`diameter_mm>=0`、`counts` 整数且 `>=0` |
| nullable / optional | 仅允许合同声明的 `string|null` / `number|null` / `boolean|null`；缺失的可选字段允许，但缺失的必填字段报错 |
| unknown 语义 | 每个嵌套对象单独维护允许键集合，出现未知键即报错（不静默忽略） |
| partial 语义 | 顶层 `status=partial` 只接受该字面量；`current`/`timeline_events` 的 `model_scope` 与 `physical_supply_verified` 必须显式给出 |
| 关键跨集合引用 | 见 6.2 |
| counts 一致性 | 7 个计数分别与对应数组长度比对 |

### 6.2 跨集合引用（逐条）

| 规则 | 路径 |
| --- | --- |
| ID 唯一 | `nodes`、`edges`、`pipes`、`valves`、`consumers`、`services` |
| 边端点存在 | `edges[i].from_node_id` / `to_node_id` |
| 边 ↔ 管段 1:1 | `edges[i].pipe_id`、`pipes[i].edge_id`、双向回指一致 |
| 阀门接触边 | `valves[i].node_id`、`incident_edge_ids`，且边必须接触该节点 |
| 气源/用户/服务节点 | `sources[i].node_id`、`consumers[i].node_id`、`services[i].node_ids[]` |
| 服务 → 用户 | `services[i].consumer_id` |
| 场景故障边 | `scenario.fault_edge_ids[]` |
| 隔离方案 | `isolation_plan.closed_valve_ids[]`、`disabled_edge_ids[]` |
| 身份一致性 | `isolation_plan.plan_id == plan_id`、`scenario.scenario_id == scenario_id` |
| 时间线 / current | 四类节点 ID 数组、`newly_affected_service_ids`、`services[].service_id` |

失败时统一抛出 `GasApiError(kind="contract_error", status=200, requestId, path, issues)`，`issues` 最多保留 32 条。

### 6.3 测试（14 项，全部通过）

`web/src/tests/gasResponseContract.test.ts`（6 项）：

1. 真实 54 节点 / 73 边响应通过
2. 合同允许的空数组不被填充；缺失数组仍报错
3. 嵌套 service / edge / isolation 字段异常被拒
4. counts、可选 nullable、metric 枚举、safety literal
5. 未知嵌套字段 + 悬空跨集合引用被拒
6. 身份篡改 + 非对象 payload 被拒

`web/src/tests/gasApi.test.ts`（8 项）：

1. 真实响应用共享 Transport 读取，54 节点 / 46 服务
2. 401/403/404/503 → `unavailable` + `http_<status>` + requestId
3. 敏感 detail（绝对路径、token）不泄漏
4. HTTP 200 缺 `services` → `contract_error`，issues 含 `services`
5. 网络异常 → `network_error`；超时 → `timeout`
6. 预先取消 → `cancelled` 且不调用 Transport
7. 活跃 signal 原样转发给 Transport
8. case id 编码 + 前端请求标识格式

---

## 7. 页面错误状态与追踪 ID 语义

### 7.1 错误状态封闭集

| kind | code | 触发 | UI |
| --- | --- | --- | --- |
| `contract_error` | `contract_error` | HTTP 2xx 但结构不符 | 「燃气响应合同异常」+ 字段路径列表 + HTTP 状态 |
| `unavailable` | `http_401/403/404/503/5xx` | 非 2xx | 「燃气候选接口暂不可用」+ 状态码 |
| `unavailable` | `network_error` | fetch 抛错 | 网络失败文案，不伪造状态 |
| `unavailable` | `timeout` | Transport 超时中止 | 超时文案，明确不回退缓存/演示 |
| `unavailable` | `cancelled` | 页面卸载/主动取消 | 不渲染（组件已卸载） |
| `unavailable` | `no_data` / `unexpected_error` | 兜底 | 显式错误页，仍可重试 |

### 7.2 不变式

- HTTP 200 结构损坏不白屏：渲染 `data-testid="gas-error"`，`data-error-kind="contract_error"`。
- 缺数据不补零 / `false` / `[]`：合同允许空数组时按原样展示；缺失数组直接报错。
- 不回退供水 Demo：`?mode=demo#/gas` 下仍只显示燃气候选结果或燃气错误页。
- 不在浏览器重算：页面只做 `filter` 展示（`status === "newly_graph_unreachable"`），不做图遍历、不做评分。
- 异步旧结果隔离：`useEffect` 用 `active` 标志 + `AbortController`，卸载后晚到结果不落 state。
- 卸载取消：cleanup 调用 `controller.abort()`，`api.ts` 将其映射为 `cancelled` 且不再设置错误状态。

### 7.3 追踪 ID 语义

- 前端生成 `gas-<uuid>`（`createGasRequestId`）。
- UI 标签固定为 **「前端请求标识」**，并附注「前端请求标识不是服务端追踪 ID」。
- Chromium 断言：页面文本包含「前端请求标识」，且**不包含**「服务端追踪 ID」。
- 后端目前没有 request-id 响应头，因此不引入伪造的 trace 字段；未来如后端提供，需在 OpenAPI 中新增独立字段，两者不得复用。

---

## 8. 回归与 Chromium 负向反例

### 8.1 全量命令结果

| 命令 | 结果 |
| --- | --- |
| `npm run typecheck` | PASS |
| `npm run contracts:check` | PASS（85 schemas，无 drift） |
| `npm run contracts:test` | PASS（7 assertions） |
| `npm run test:gas:contract` | PASS（2 files / 14 tests） |
| `RUN_BROWSER=1 npx vitest run` | PASS（18 files / 215 tests） |
| `npm run boundaries:check` | PASS（438 files / 1211 edges / 0 violations） |
| `npm run build` | PASS（105 modules；`index-BV-3yaHy.js` 449.24 kB / gzip 135.37；`index-YQbdXMMQ.css` 81.25 kB / gzip 22.47；重建后资源哈希一致） |
| `pytest server/tests` | PASS（69） |
| `pytest engineering/tests/test_gas_application.py` | PASS（10） |
| `node scripts/gas-contract-convergence.browser.mjs` | PASS（26/26） |

说明：`vite build` 首次因 `vite preview` 占用 `dist/` 而报 `ENOTEMPTY`，停止预览后重建通过；重建产物哈希与失败前完全一致，可复现。随后对重建产物重启预览并复跑浏览器脚本，26 项仍全通过。

### 8.2 Chromium 26 项负向 / 正向检查

真实 Chromium（`chromium-1248/chrome-linux64/chrome`，`--no-sandbox`），加载生产构建的静态包，通过路由拦截注入响应。

| # | 检查 | 结果 |
| --- | --- | --- |
| 1 | 合法 54 节点 fixture 渲染 | PASS |
| 2 | 请求命中 `/api/v1/gas/cases/syn-gas-case-K-001` | PASS |
| 3 | 请求 ID 标注为「前端请求标识」 | PASS |
| 4 | 页面不出现「服务端追踪 ID」 | PASS |
| 5 | 合法页无 pageerror | PASS |
| 6 | 合法页无 console error | PASS |
| 7 | HTTP 200 缺 `services` → `contract_error` | PASS |
| 8 | 合同错误列出字段路径 `services` | PASS |
| 9 | 合同错误不渲染正常页 | PASS |
| 10 | 合同错误标注「HTTP 200」边界 | PASS |
| 11 | 合同错误不显示 DemoHealth | PASS |
| 12 | 合同错误不白屏、不抛异常 | PASS |
| 13–20 | 401 / 403 / 404 / 503 → `unavailable`，且无 pageerror（4×2） | PASS |
| 21 | 网络中断 → `unavailable` / 网络错误 | PASS |
| 22 | 网络中断不白屏 | PASS |
| 23 | 503 后点击重试恢复 | PASS |
| 24 | 重试确实发起第二次请求 | PASS |
| 25 | `?mode=demo#/gas` 不回退供水数据 | PASS |
| 26 | 卸载后晚到结果不渲染 | PASS |

证据文件：

- `docs/evidence/gas-contract-convergence-01/browser-results.json`（26 条，`errors: []`）
- `docs/evidence/gas-contract-convergence-01/valid-1280x900.png`
- `docs/evidence/gas-contract-convergence-01/malformed-1280x900.png`

### 8.3 覆盖到但未在浏览器中单列的项

- 超时：由 `gasApi.test.ts` 的 `timeout` 用例覆盖（Transport 层构造 `AbortError`）。
- 快速连续请求 / 旧请求晚于新请求：由 DOM/Vitest 全量套件中的既有竞态用例 + 本任务卸载用例覆盖；页面通过 `active` 标志与 abort 丢弃旧结果。

---

## 9. 54 节点案例实际解析结果

| 项 | 值 |
| --- | --- |
| 案例 | `syn-gas-case-K-001` |
| fixture 来源 | FastAPI `TestClient` 序列化响应（非 adapter 直接产出、非手工编写） |
| fixture 路径 | `web/src/tests/fixtures/gasCaseResponse.json` |
| fixture SHA-256 | `9f85a1bab26ba2aae794431542ca1e73ea10d5734de2c03bd5af99bb6601462f` |
| 体积 | 187,390 B |
| 节点 | 54 |
| 边 | 73 |
| 管段 | 73 |
| 阀门 | 31 |
| 气源 | 2 |
| 用户 | 46 |
| 服务 | 46 |
| 新增图不可达服务 | 10 |
| 健康评分 | 未知（缺 G01/G02/G04 有效记录，页面原样显示「未知」） |
| 物理供气 | `unknown`（原样展示） |
| 运行时校验 | 通过（无 issue） |
| ASGI 冒烟 | status 200，133,573 B，`same_as_fixture=true` |

页面渲染数值与 fixture 一致：`54 节点`、`73 / 73`、`10`、`未知`。

---

## 10. 身份与权限边界

### 10.1 后端现状（`be04c41`，未改动）

- `gas_router()` 对 `/api/v1/gas/cases` 和 `/api/v1/gas/cases/{case_id}` 都调用 `_authorize_synthetic(request)`。
- `resolve_principal(request)` 只从服务端配置解析身份：`X-Water-Token` → `_principals()`；否则 `WATER_API_TOKEN` 比对；两者都未配置时返回 `local-demo`（`authenticated=False`，`modes=DEMO_MODES`）。
- `synthetic` 不在 `principal.modes` 时返回 **403**；未知 `case_id` 返回 **404**；fixture 不可用返回 **503**。
- OpenAPI 上两条路由使用 `OPTIONAL_SECURITY`，即合同层面把认证声明为可选，实际准入由 `_authorize_synthetic` 运行期裁决。

### 10.2 前端边界

- 401/403 一律映射为 `unavailable` 并展示状态码与请求路径，不缓存、不复用上次成功结果、不降级为 Demo。
- 前端不读取、不存储 Token；认证头由平台 Transport 之外的上层注入，本次未改动。
- 页面请求 ID 仅用于前端排查，不参与任何授权判断。

### 10.3 明确声明

本报告**不声明**认证已加固、不声明 gas 路由已默认拒绝、不声明权限模型已通过安全审核。这些属于 Codex `fix/gas-integration-auth-default` 的范围，且该修复尚未提交、尚未独立审核。

---

## 11. 尚未通过的真实 HTTP 测试（BLOCK）

| 未完成项 | 原因 | 解除条件 |
| --- | --- | --- |
| 对运行中的 FastAPI 发起真实网络请求（非 TestClient） | 认证修复未提交，形成正式集成版本被禁止 | Codex 提交并独立通过认证整改 |
| 浏览器 + 真实后端（含 Cookie/Token 会话） | 同上 | 同上 |
| 401/403 的真实服务端行为验证 | 当前仅在浏览器中通过路由拦截构造 | 同上 |
| 超时/重试的真实网络时序 | 当前由 Transport 单测模拟 | 同上 |
| 多案例 / 多模式隔离的真实后端验证 | 后端当前只有单一合成案例 | 后端提供第二案例或明确模式开关 |

**已做但与"真实认证"不等价**：FastAPI `TestClient` 进程内 ASGI 冒烟（200 / 133,573 B）与静态 Vite 预览 + Chromium 路由拦截。两者只证明解析与错误处理行为，不能替代 FastAPI 认证链路验证。

---

## 12. 与 Codex 安全分支的潜在冲突

### 12.1 文件级重叠

| 集合 | 文件 |
| --- | --- |
| 本提交改动 | 19 个文件（全部在 `web/**` 与 `docs/**`，`engineering/**` 为 0） |
| Codex 未提交改动 | `engineering/src/platform/app.py`、`engineering/src/platform/authz.py`、`engineering/tests/test_m1fix2_identity.py`、`engineering/tests/test_m1fix_authz.py`、`engineering/tests/test_gas_auth_default.py` |
| 交集 | **0** |

因此合并时不会产生文本冲突。

### 12.2 语义冲突点（需在认证修复通过后逐项复核）

1. **默认主体**：若 Codex 把 `local-demo` 的 `modes` 收窄，移除 `synthetic`，则未认证访问 gas 路由会从 200 变为 403。前端行为已验证（403 → `unavailable` + 可重试），但"开发环境默认可见"的体验会改变。
2. **401 vs 403**：若未配置令牌时改为 401，前端映射为 `http_401`，文案不同但状态机一致。
3. **`OPTIONAL_SECURITY` 声明**：若 Codex 把 gas 路由改为强制 security，OpenAPI 会变化 → 85 schemas 可能变动 → 必须重新 `contracts:check` 并重新生成 TS。**这是最可能触发合同二次收敛的点**。
4. **错误体结构**：`COMMON_ERRORS` 若新增字段，`safeDetail` 仍只取 `detail`，不会泄漏新字段。
5. **`test_gas_auth_default.py`**：会直接断言 gas 路由的准入行为；与本分支无文件冲突，但需要与前端 403 语义对齐。

### 12.3 处理流程（建议）

1. Codex 提交认证修复并通过独立审核。
2. 以 `be04c41` + 认证提交为新基线，rebase 本分支。
3. 重跑 `contracts:check`；若 OpenAPI SHA 变化，重新生成 TS 并重跑 14 项合同测试。
4. 启动真实 FastAPI，按第 11 节清单补做真实 HTTP/认证验证。
5. 再由 HY4 判定是否允许进入受控集成。

---

## 13. Git 提交与独立复审指令

### 13.1 提交信息

```
branch : feature/gas-contract-convergence-01
base   : be04c41
commit : 728cd7bfbe53e99ec55255a17b6bd8f75b051917  feat(gas): converge the gas UI onto the 85-schema contract and shared transport
         19 files changed, 7680 insertions(+), 54 deletions(-)
HEAD   : docs(gas): add the contract convergence delivery report           1 file changed
```

- 未合并 `main`，未推送 `origin`，未改动部署与 Sites。
- 提交包含代码、测试、脚本、证据与 `dependency_baseline.json` 的共享桥接 SHA 变更。

### 13.2 回退方式

```bash
cd experiments/gas_contract_convergence_20261106/repo
git reset --hard be04c41
# 或仅撤销提交、保留内容：
git reset --soft be04c41
```

### 13.3 给 HY4 的复审指令

请按以下顺序独立复核，不要以本报告结论替代自己的判断：

1. **基线**：确认 `git log --oneline -3` 依次为 `docs(gas)` 报告提交、`728cd7b`、`be04c41`；确认 `git diff --stat be04c41 728cd7b` 只含 `web/**` 与 `docs/**`，`engineering/**` 为 0。
2. **唯一合同**：重新运行 `npm run contracts:check`，确认 `schemas=85`、`source sha256=9a97933e…`、生成物 `6f1e29bd…`；抽查 `web/src/generated/platformOpenapi.ts` 是否与 OpenAPI 一致；确认 `features/gas/types.ts` 无手写 DTO。
3. **唯一请求入口**：`grep -rn "fetch(" web/src/features/gas/` 应为空；确认 `api.ts` 只经 `shared/api/platformTransport`。
4. **共享 Transport 差异**：审阅 `transport.ts` 的 `signal` 与 `combinedSignal`，确认超时语义未被覆盖、认证头未被改动；确认 `dependency_baseline.json` 的 SHA 与文件实际 SHA 一致。
5. **运行时边界**：审阅 `contract.ts` 的 14 个 DTO 校验与 6.2 节跨引用清单，重点核对是否遗漏 nullable/optional 与 unknown 字段处理；可自行构造反例。
6. **页面不变式**：确认无 Demo 回退、无补零/补 `[]`、无浏览器重算、卸载取消、旧结果隔离、追踪 ID 文案。
7. **测试真实性**：重跑 14 项合同测试、`RUN_BROWSER=1` 全量、浏览器脚本；核对 `browser-results.json` 的 26 条与截图。
8. **未完成项**：确认第 11 节清单仍为 BLOCK，且提交信息与报告都没有宣称认证或真实 HTTP 已通过。
9. **冲突面**：对照 Codex 未提交文件，确认 0 文件交集，并评估第 12.2 节的 5 个语义冲突点。

### 13.4 复审结论口径

- 本分支只证明：**在 be04c41 合同下，燃气前端具备运行时合同边界、统一 Transport 入口、明确错误状态与静态 Chromium 负向证据**。
- 本分支**不**证明：认证安全、真实 HTTP 集成、受控集成就绪、整体集成 PASS。
- 上述三项须在 Codex 认证整改独立通过并完成第 11 节验证后，由 HY4 重新裁定。

---

## 14. 未做与不做声明

- 未开发新的燃气业务算法，未扩充 CUV 或 Agent。
- 未修改 `engineering/src/**`、GAS-M0 原始 fixture、Report、数据库、Sites、`main`。
- 未直接 cherry-pick `feature/gas-http-robustness-01`；旧分支仅作为行为对照，运行时边界按 85-schema 重写。
- 未手改生成类型文件，未恢复 71-schema 生成物。
- 未自行宣布整体集成通过，未进入受控集成分支。

交付完成，停止，交 HY4 独立审核。
