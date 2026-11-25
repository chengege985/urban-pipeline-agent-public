# 水脉守护 AI 前端规范（C1）

版本：`web-0.3.0-rc.2`  
数据版本：`synthetic-2026-10-30`  
时间线算法：`demo-timeline-0.1.1`  
时间线数据：`synthetic-timeline-2026-11-01`（基准 `2026-11-01T08:00:00+08:00`）  
演示模型版本：`demo-score-0.1.0`  
更新：2026-11-01

> 本规范只描述比赛前端与独立演示规则，不代表正式 P/C/R/H、失效概率、RUL、水力模型或企业内部 CUV。所有数据均为合成数据，不控制真实设施。

## 一、产品与范围

### 1.1 产品定位

水脉守护 AI 是面向 Hack4Good「技术向善」的合成演示 Web 产品：让非专业评委理解“一段管道出现问题，可能影响哪些服务对象、为什么需要关注、有哪些可行维护方案”。

### 1.2 本轮范围（C1—C5）

- 页面一：城市管网总览。
- 页面二：管段健康分析。
- 页面三：交互式情景分析（含预设隔离方案、SVG 故障/受影响区域高亮）。
- 页面四：AI 智能助手入口（演示助手）。
- 页面五：决策简报（预算/资源/依赖、方案选择、打印/JSON 导出、人工审核）。
- 合成数据、5 个服务对象、5 个情景、11 个预设阀门、10 个隔离方案、3 个候选方案。
- G01 服务时间线：计划图通路回放、任务完成/服务恢复分项、缺项与非法输入处理、报告冻结。
- DemoHealth 演示规则、12+ 条可重复测试向量。
- `demo / live / unavailable` 数据访问适配层。
- 类型检查、生产构建、桌面/移动端交互与打印检查。

### 1.3 明确不在本轮范围

- 比赛端不另建 LangGraph 多智能体；真实模型仅通过独立 FastAPI 的可选工具循环接入。
- Qoder Sites 公开发布与公开访问（需单独确认）。
- 真实 GIS、管网、水压、流量、工单、企业接口或生产数据。
- 自动阀门优化、真实水力仿真、真实工单/派单。

## 二、页面信息架构与路由

采用 hash 路由，适合静态部署，不需要服务端重写：

| 路由 | 页面 | 主要交互 |
| --- | --- | --- |
| `#/overview` | 城市管网总览 | SVG 管网、风险列表、服务/等级筛选、情景入口 |
| `#/pipe/:pipeId` | 管段健康分析 | 管段切换、评分、因子、缺失项、计算依据、进入情景 |
| `#/scenario/:scenarioId?pipe=:pipeId` | 交互式情景分析 | 情景/隔离选择、SVG 高亮、服务影响、管龄/漏损/压力/管材改数、预算/资源/依赖、方案选择 |
| `#/report/:sessionId` | 决策简报 | 管段/情景/隔离/评分/方案/证据/审核，打印 PDF、导出 JSON |
| `#/assistant?pipe=:pipeId&scenario=:scenarioId` | AI 智能助手入口 | 聊天、建议问题、工具结果卡片、引用、上下文切换 |
| `#/about` | 关于 | 数据、模型、边界、Provider 模式、后续路线 |

页面状态：

- `loading`：启动加载演示数据。
- `ready`：正常展示。
- `empty`：没有匹配管段或缺少上下文。
- `error`：选择/计算失败，显示原因，不伪造结论。
- `unavailable`：请求 live 但接口探测失败，提供重试/切换 demo。
- `refused`：助手拒绝概率、寿命、水力、关阀、派单等越权请求。

## 三、前端领域数据模型

> 这些类型是比赛前端 DTO，不与工程 `src/platform/schemas.py` 的同名业务契约混用。工程 `Assessment` / `SnapshotPayload` 不得直接作为前端类型复用。

### 3.1 `Pipe`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `string` | `WP-001`—`WP-024`，合成 ID |
| `name` | `string` | 管段名称 |
| `fromNodeId` / `toNodeId` | `string` | 合成节点 ID |
| `material` | `PipeMaterial` | PE/球墨铸铁/铸铁/钢/PVC/未知 |
| `installYear` | `number \| null` | 投运年份，缺失为 null |
| `diameterMm` | `number \| null` | 管径 mm |
| `depthM` | `number \| null` | 埋深 m |
| `pressure` | `PressureLevel` | 低压/常压/高压/未知 |
| `leakCount` | `number \| null` | 漏损次数 |
| `repairCount` | `number \| null` | 维修次数 |
| `monitoring` | `MonitoringLevel` | 正常/关注/异常/未知 |
| `lastInspection` | `string` | 最近巡检日期 |
| `serviceIds` | `string[]` | 关联服务对象 |
| `lengthM` | `number` | 管段长度 |
| `note` | `string` | 合成说明 |

### 3.2 `HealthAssessment`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `pipeId` | `string` | 管段 ID |
| `status` | `"succeeded" \| "insufficient_data"` | 是否有有效指标 |
| `score` | `number \| null` | DemoHealth 0—100，1 位小数 |
| `grade` | `HealthGrade` | 基本健康/关注/重点检查/数据不足 |
| `confidence` | `number` | 有效权重占比 0—100 |
| `validWeight` | `number` | 有效权重（0—1） |
| `factors` | `HealthFactor[]` | 8 项因子 |
| `missing` | `string[]` | 缺失项名称 |
| `version` | `string` | `demo-score-0.1.0` |
| `summary` | `string` | 一句可解释摘要 |

### 3.3 其他模型

- `ServiceObject`：服务对象（医院/学校/商业/居民区）、节点、户数或设施数、是否关键。
- `NetworkNode`：水源/节点/服务节点，含 SVG 示意坐标（非真实经纬度）。
- `NetworkScenario`：故障管段、启用源、默认隔离方案、严重度、说明。
- `Valve`：合成阀门，关联管段/节点/可操作性。
- `IsolationPlan`：情景 ID、预设阀门、额外关闭管段、证据、版本。
- `ServiceImpact`：受影响服务、关键设施、户数、设施数、不可达节点、故障/隔离管段、证据、限制。
- `MaintenancePlan`：候选措施、费用、工期、风险下降、资源需求、依赖条件、约束。
- `DemoConstraints`：演示预算、资源容量、已确认依赖。
- `PlanComparison`：硬约束可行性、违规原因、剩余影响、演示效用、推荐方案、限制。
- `DecisionSession`：管段/情景/隔离/方案/约束、数据版本、模型版本、证据、审核状态与意见。
- `ReviewStatus`：`draft → pending_review → approved / rejected`。
- `EvidenceReference`：证据 ID、类型、说明、版本。
- `AgentResponse`：演示助手回答、工具调用卡片、证据 ID、限制、建议问题。
- `WaterPulseDataset`：Provider 返回的完整合成数据集（含阀门、隔离方案）。

## 四、合成数据与版本

### 4.1 数据来源

`src/data/network.ts` 程序化/手工构造：

- 24 条合成管段 `WP-001`—`WP-024`。
- 17 个节点：2 水源、10 节点、5 服务节点。
- 5 个服务对象：市第二医院、滨河实验学校、滨河商业中心、滨河一号院、老城二号院。
- 5 个情景：医院支线、学校支线、商业支线、居民区叠加故障、双源切换。
- 11 个预设阀门、10 个隔离方案（每个情景 2 个）。
- 3 个候选方案：立即抢修、计划更换、加密监测；每个方案含资源需求与依赖条件。

### 4.2 一致性约束

- 所有管段端点必须存在。
- 所有 `serviceIds` 必须存在。
- 所有情景的 `failedPipeIds` 与 `activeSourceIds` 必须存在。
- 管段/节点/服务 ID 不得指向真实设施。
- 情景影响由 `src/engine/network.ts` 的拓扑 BFS 计算，不能手工硬编码矛盾结果。
- 测试向量见 `src/data/vectors.ts` 与 `src/tests/`。

## 五、DemoHealth 演示规则

### 5.1 指标与权重

| 指标 | 权重 | 打分（1 最好，5 最差） |
| --- | ---: | --- |
| 管材 | 0.15 | PE/球墨铸铁/PVC=2；钢=3；铸铁=4；未知=null |
| 管龄 | 0.20 | <10=1；<20=2；<30=3；<40=4；≥40=5；缺失/负值=null |
| 管径 | 0.10 | <100mm=5；<200=4；<300=3；<400=2；≥400=1；缺失/≤0=null |
| 埋深 | 0.10 | <1.5m=1；<2.5=2；<3.5=3；<4.5=4；≥4.5=5；缺失/≤0=null |
| 运行压力 | 0.15 | 低压=1；常压=2；高压=4；未知=null |
| 漏损历史 | 0.15 | 0=1；1=2；2=3；3=4；≥4=5；缺失=null |
| 维修频次 | 0.05 | 0=1；1—2=2；3—4=3；5—6=4；≥7=5；缺失=null |
| 监测质量 | 0.10 | 正常=1；关注=3；异常=5；未知=null |

管龄固定按演示评价年 2026 计算；更换年份会改变管龄档位。

### 5.2 汇总公式

```text
r_i = w_i × (s_i − 1) / 4
R = Σr_i / Σw_valid
DemoHealth = 100 × (1 − R)
Confidence = Σw_valid / Σw_all × 100
因子贡献 = r_i / Σr_i
```

- `s_i = null` 的指标不计入 `Σw_valid`，列入 `missing`，**不按 0 分处理**。
- 全部指标缺失：`status = insufficient_data`，`score = null`，`grade = 数据不足`，`confidence = 0`。
- 等级阈值：DemoHealth ≥80 基本健康；≥65 关注；<65 重点检查。
- 所有风险为 0 时，因子贡献显示 0，不制造虚假的相对占比。

### 5.3 边界

- 不是正式 H/P/C/R/H；不提供失效概率、RUL、水力、物理级联。
- 不是业务验收；只用于同一演示版本内相对比较。
- 后续 Codex 冻结真实算法后，替换 `engine/demoHealth.ts` 适配器与版本号即可，页面不改。

## 六、情景与方案规则

### 6.1 拓扑影响与给定隔离

- 从 `scenario.activeSourceIds` 出发，在“未故障且未被隔离关闭”的管段上做 BFS。
- 故障管段集合 = `scenario.failedPipeIds`；额外关闭集合 = `isolationPlan.closedPipeIds`；两者取并集。
- 隔离方案只允许使用 `isolationPlan.valveIds` 中预置的合成阀门；不自动搜索阀门组合。
- 服务节点不可达 → 受影响服务；每个服务对象只统计一次，户数/设施数按服务对象唯一汇总；关键设施单列。
- 结果只表示“拓扑通路影响”，不代表压力、流量、实际缺供量或事故概率。
- 双源切换情景用于展示冗余：主源断开、备用源启用后服务保持连通。
- SVG 中红色虚线表示故障/隔离管段，受影响服务节点显示红色标记；目标管段可选中。
- 重置：恢复情景默认隔离方案；默认隔离方案不含额外关闭管段，因此与无额外隔离的基线影响一致。

### 6.2 方案硬约束与比较

```text
utility = 55% × min(风险下降/30, 1)
        + 25% × clip(1 − 费用/40000元, 0, 1)
        + 20% × (是否恢复受影响服务)
```

- 决策版本demo-decision-0.2.0：固定40000元参考尺度与固定演示权重；参考尺度独立于用户预算及候选集合，不是正式 CUV、AHP、熵权或预算审批。
- 只比较 3 个给定方案，不自动生成方案、不做全局最优搜索。
- 硬约束：
  - `costYuan <= budgetYuan`；
  - 每个资源的 `resourceRequirements <= resourceCapacities`；
  - `requiredDependencies` 全部在 `confirmedDependencies` 中。
- 任一硬约束不满足即 `feasible=false`，显示违规原因；**不可行方案不会成为推荐方案**。
- 剩余影响服务：方案恢复受影响服务则为 0，否则等于当前情景受影响服务数。
- 用户只能选择可行方案；未选择可行方案时禁用“生成决策简报”。
- 页面固定提示费用、方案完成时间、风险下降、资源与依赖均为合成演示假设。durationHours不是服务恢复时间；预设恢复效果不是物理重算。

### 6.3 决策简报

- 路由 `#/report/:sessionId`。
- 简报包含：管段编号与时间、数据/模型版本、管段属性、DemoHealth、故障/隔离、服务影响、候选方案与约束、选中方案与解释、缺失/限制、证据 ID、审核状态。
- 选中方案来自用户选择，不自动替换为“推荐方案”。
- 支持浏览器打印/另存 PDF；支持导出 JSON。
- 新简报从DecisionSession读取冻结输入/结果snapshot，包含override与decisionAlgorithmVersion；不以当前数据静默重算旧记录。旧会话无snapshot、版本不符或引用失效时标stale或拒绝，禁用审核，需重新生成。
- 费用、工期、资源、依赖、风险下降均标明为合成演示值；不声称业务最优。

### 6.4 人工审核

- 静态演示使用浏览器 `localStorage` 保存 `DecisionSession` 与审核状态；刷新后可恢复，清理浏览器存储会丢失记录。
- 状态机：`draft → pending_review → approved / rejected`；`approved/rejected` 可重新开启为 `draft`。
- 每次流转递增 `reviewRevision`，保存审核人、意见、更新时间；审核意见在简报中显示。
- 审核对象与管段、情景、隔离方案、选中方案、数据版本、模型版本和证据 ID 关联。
- 审核通过仅代表本地演示记录获得确认，不代表真实维修工单已批准、下发或执行。
- 禁止模拟真实关阀、工单下发或执行成功。

### 6.5 G01 服务时间线

- 数据：独立合成 `TimelineDataset`，包含 `version / asOf / events / tasks`；事件声明 `actionId`、恢复边、生效小时与证据；任务声明结束小时或保持未知。不得从 `MaintenancePlan.durationHours` 推导恢复时刻。
- 计算：按 `asOf` 后的声明事件回放图通路；判据固定 `topological_reachability`，时间基准 `planned`。仅表示计划图通路，不是水力、水质、实际供水量或现场执行。
- 指标：`planCompletion`（全部任务完成的最大偏移）、`firstTargetServiceRestored`（初始不可用目标中最早恢复）、`allTargetServicesRestored`（首次全部同时可达，不代表持续可达）、逐服务 `serviceFirstRestored`。
- 状态：`succeeded / already_available / insufficient_data / not_recovered_within_horizon / not_applicable`。`null` 不得显示为 0 或窗口上限；0 是合法值；超出窗口的时间不裁剪，标 `beyondHorizon`。
- 任务结束缺失只隐藏该指标，保留已知图通路时间；空目标显式返回“不适用”；遗漏目标先澄清，不能当作空集合。
- 非法 schedule（重复/未知行动、布尔或非有限小时、结束与开始+持续矛盾）整体返回 `invalid_input`，不生成事件、不显示任何时间数字；API 结构错误为 422，未知情景/隔离/方案为 404。
- 切换情景、隔离、方案、目标、窗口或时间表时旧结果与旧确认立即失效；晚到响应不得覆盖新输入。
- 简报冻结当次时间线、预检、目标、窗口与证据；旧会话无时间线时显示“未生成”，不补造。
- 时间线查询本身不产生治理建议审批；审核只表示人工审阅。

## 七、Provider 数据访问适配层

### 7.1 接口

`WaterPulseProvider`：

```ts
mode: "demo" | "live" | "unavailable"
loadDataset(): Promise<WaterPulseDataset>
evaluate(pipe, override?): Promise<HealthAssessment>
simulate(scenarioId, isolationPlanId?): Promise<ServiceImpact>
comparePlans(scenarioId, planIds?, constraints?, isolationPlanId?): Promise<PlanComparison>
checkTimeline(request): Promise<TimelineGate>
timeline(request): Promise<PlanTimelineResult>
ask(question, context): Promise<AgentResponse>
probe?(): Promise<boolean>
```

### 7.2 模式规则

| 模式 | 条件 | 行为 |
| --- | --- | --- |
| `demo` | 默认；`VITE_WATERPULSE_MODE` 未设置或为 demo | 本地合成数据与演示规则，离线可用 |
| `live` | 配置 `VITE_WATERPULSE_MODE=live` + `VITE_WATERPULSE_API_URL`，且 `/api/v1/demo/meta` 探测成功 | 调用比赛 FastAPI（`server/`）；只有探测成功才显示 live |
| `unavailable` | live 探测失败/缺少地址/请求失败 | 明确提示，不展示伪造实时数据；提供重试/切换 demo |

### 7.3 比赛 API 契约（`server/` 已实现）

- `GET /api/v1/demo/meta`
- `GET /api/v1/demo/dataset`
- `POST /api/v1/demo/evaluate`
- `POST /api/v1/demo/simulate`
- `POST /api/v1/demo/compare`
- `POST /api/v1/demo/timeline/check`
- `POST /api/v1/demo/timeline`
- `POST /api/v1/demo/assistant`

统一返回应包含 `status`、`mode`、`version`、`evidenceIds`、`limitations`、`dataMode`、`executionStatus=not_dispatched`。完整字段见 `../docs/API契约_20261030.md`。

## 八、演示助手边界

- 默认使用本地规则/模板；配置 `COMPETITION_LLM_BASE_URL` 后由比赛 FastAPI 调用 Qwen3.6 工具链，响应 `engine=llm`。
- 可回答：评分解释、服务影响、候选方案比较、下一步建议。
- 必须拒绝：爆管概率、失效概率、剩余寿命/RUL、水力计算、直接关阀、派单/工单、预测未来。
- 所有数字来自 DemoHealth、拓扑影响或固定方案；模型不生成新数值。
- 回答展示工具调用卡片与证据 ID；缺少上下文时明确要求选择，不编造。
- 模板路径固定展示“演示助手 · 非真实模型”；真实模型路径不得写成正式业务验收。

## 九、视觉、响应式与可访问性

- 深蓝/青蓝/浅色背景；专业城市基础设施管理风格；不使用发光线条或纯数字大屏。
- 桌面优先 1240px 内容宽度；移动端 390px 宽度无横向溢出。
- SVG 管网有节点/服务标签与键盘焦点；管段可点击/Enter/Space 进入详情。
- 颜色不是唯一信息载体：等级同时显示文字/分数/徽标。
- 支持 `prefers-reduced-motion`；焦点样式清晰；表单有 label。
- 中文字体使用本地打包的 Noto Sans SC Variable 子集（SIL OFL 1.1），不依赖系统字体。

## 十、测试与验收

### 10.1 命令

```bash
cd /workdata/WaterPipNet/02_比赛作品/web
npm install
npm run typecheck
npm test
npm run build
npm run preview -- --host 127.0.0.1 --port 4174
```

### 10.2 必须通过

- TypeScript `tsc -b` 无错误。
- Vite 生产构建成功。
- 12 条 DemoHealth 测试向量全部通过，另有数据一致性、情景、方案、C4 隔离、C5 约束、简报、审核、助手、Provider、G01 时间线测试。
- 选择管段 → 详情 → 情景 → 隔离 → 改数重算 → 预算/资源/依赖 → 选择可行方案 → 生成简报 → 审核可操作。
- 选择隔离方案后服务影响重算；重置恢复默认隔离与原始输入。
- 不可行方案不显示为推荐，且不能生成简报。
- 简报与当前管段、情景、隔离、方案、数据/模型版本、证据一致。
- 审核状态 `draft → pending_review → approved / rejected` 正确流转，意见刷新后仍保存。
- 断网/无后端时 demo 模式可用；不访问企业内部 API/真实数据。
- 无 console error；移动端无横向溢出；打印/PDF 与中文显示正常。
- 助手不伪称真实模型；越权问题被拒绝，不能真实关阀、派单或预测概率。

### 10.3 当前验收结果

- `npm run typecheck`：通过。
- `npm test`：120 项通过（12 个测试文件；浏览器回归默认跳过）。G01 覆盖 6h 通路、6h/9h 完成、超窗口 24h、窗口内未恢复、空目标、非法 schedule、助手与冻结报告。
- 可选浏览器回归：8 项通过（`RUN_BROWSER=1`），覆盖时间线展示/补录/空目标/助手/报告冻结/晚到响应/失败停止加载/跨情景清理。
- 比赛 API 回归：69 项通过（`../.venv-agent/bin/python -B -m pytest server/tests -q`）；时间线边界、跨入口时间表、请求限额与 demo/API 对照一致。
- `npm run build`：通过；候选产物在 `web/release/waterpulse-web-0.3.0-rc.2/dist/`。
- `0.3.0-rc.2` 已私有发布并保持 `selected` 访问范围；`0.3.0-rc.1` 冻结产物保留。

## 十一、版本与变更规则

- 修改演示规则：更新 `DEMO_MODEL_VERSION`、文档、测试向量；不得静默改权重。
- 修改数据：更新 `DATA_VERSION`；保持 ID 与引用一致；补充/调整测试向量。
- 修改 API 适配：保持 `DemoProvider` 与 `ApiProvider` 同一接口；live 失败必须回到 `unavailable`，不得回退冒充 live。
- 页面只从 `WaterPulseDataset` 读取数据；不在组件内硬编码业务数值。


## 20261031验证更新

70项前端回归（含2项隔离Chromium交互）、TypeScript检查通过。上方原58项是此前交付快照。本次未覆盖dist或发布。live比较走Provider；助手携带冻结约束和override；模型只选工具，最终业务数值由确定性工具结果渲染。
