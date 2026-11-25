# 水脉守护 AI · WaterPulse AI 比赛前端

Vite + React + TypeScript 的独立合成演示站点，参加 2026 Qoder 高校创造力大赛 Hack4Good「技术向善」赛道。

> 合成数据演示，不控制真实设施；DemoHealth 不是正式健康度、失效概率、RUL 或水力计算结果。
> 默认助手为本地规则；配置真实模型后可由比赛 FastAPI 调用 Qwen3.6 工具链。

## 当前状态（2026-11-01）

- **C1 规范**：已完成，见 [SPEC.md](SPEC.md)。
- **C2 合成数据与演示规则**：24 条管段、17 个节点、5 个服务、5 个情景、10 个隔离方案、3 个候选方案。
- **C3 主交互**：总览、管段分析、交互式情景、演示助手。
- **C4 隔离交互**：预设隔离方案、阀门/关管高亮、重算拓扑影响。
- **C5 决策**：预算/资源/依赖硬约束、可行方案选择、一页简报、人工审核、JSON/打印导出。
- **G01 服务时间线**：独立合成计划事件回放（`demo-timeline-0.1.1`）；图通路恢复与任务完成分开计算；时间表跨页面/助手传递；缺任务结束记录保留已知通路时间；空目标、窗口内未恢复、非法输入分别展示；报告冻结时间线、请求与版本。
- **测试**：`tsc`、Vite 生产构建、**120 项 Vitest**、**8 项可选 Playwright 浏览器回归**、**69 项比赛 API 回归**均通过。
- **Qoder Sites**：`0.3.0-rc.2` 已私有发布，沿用原有 `selected` 访问范围；`0.3.0-rc.1` 冻结产物保留；未切换公开访问。rc.2 清单见 [../docs/releases/waterpulse-web-0.3.0-rc.2.md](../docs/releases/waterpulse-web-0.3.0-rc.2.md)。
- **Live API**：`server/` 提供同契约合成 API，新增 `/timeline/check` 与 `/timeline`；时间线 API 与前端 demo Provider 已按同一输入对照一致。

## 页面与路由

hash 路由，适合静态部署：

| 路由 | 页面 |
| --- | --- |
| `#/overview` | 城市管网总览 |
| `#/pipe/:pipeId` | 管段健康分析 |
| `#/scenario/:scenarioId?pipe=:pipeId` | 交互式情景分析 |
| `#/report/:sessionId` | 决策简报与人工审核 |
| `#/assistant?pipe=:pipeId&scenario=:scenarioId` | 演示助手 |
| `#/about` | 关于与数据边界 |

## 运行

需要 Node.js 20+（本机使用 Qoder 内置 Node）。也可以复用已安装的 `node_modules`：

```bash
cd /workdata/WaterPipNet/02_比赛作品/web
npm install
npm run dev -- --host 127.0.0.1 --port 5173
# 生产构建与预览
npm run build
npm run preview -- --host 127.0.0.1 --port 4174
```

Live 模式：

```bash
# 先启动 ../server（默认 8090）
VITE_WATERPULSE_MODE=live VITE_WATERPULSE_API_URL=http://127.0.0.1:8090 \
  npm run dev -- --host 127.0.0.1 --port 5173
```

`ApiProvider` 探测 `/api/v1/demo/meta` 失败时显示 unavailable，不伪造 live；可回退 demo。

## 数据与演示模型

- `DATA_VERSION = synthetic-2026-10-30`
- `DEMO_MODEL_VERSION = demo-score-0.1.0`
- 24 条 `WP-001`—`WP-024` 合成管段，17 个节点，5 个服务对象。
- 5 个情景：医院支线、学校支线、商业支线、居民区叠加故障、双源切换。
- 10 个预设隔离方案、3 个候选方案。
- 时间线：`synthetic-timeline-2026-11-01`，基准 `2026-11-01T08:00:00+08:00`；10 个图事件、15 条任务记录。
- DemoHealth：8 项指标、固定演示权重、缺失不计权重、可信度、因子贡献；规则详见 [SPEC.md](SPEC.md)。

## Provider 模式

- `demo`：默认，本地合成数据与演示规则，离线可用。
- `live`：配置 `VITE_WATERPULSE_MODE=live` 和 `VITE_WATERPULSE_API_URL`，且接口探测成功时启用。
- `unavailable`：live 探测/请求失败时明确提示，不伪造实时数据；可重试或切换 demo。

## 测试与验收

```bash
npm run typecheck
npm test
npm run build
# 可选浏览器回归（需要本机 Playwright Chromium）
RUN_BROWSER=1 BROWSER_EXECUTABLE=/path/to/chrome npx vitest run src/tests/interaction.browser.test.ts

# 比赛 API 回归（在工作区根目录）
cd ..
../.venv-agent/bin/python -B -m pytest server/tests -q
```

已通过（2026-11-01）：

- 120 项 Vitest（DemoHealth、C4/C5、G01 时间线、冻结报告版本、4xx 安全映射、时间表上下文）。
- 8 项 Playwright 浏览器回归：6h/9h 时间线、报告冻结与刷新、空目标、助手上下文、晚到响应、上游失败停止加载、跨情景清理时间表。
- 69 项比赛 API 回归：时间线边界、跨入口时间表、请求体限额、助手工具、demo/API 对照。
- TypeScript 类型检查、Vite 生产构建。
- 截图见 `../assets/screenshots/e2e_*.png`。

## 目录

```text
web/
├── SPEC.md
├── scripts/          export-demo-data.ts
├── src/domain/       领域类型与常量
├── src/data/         合成管网、情景、隔离方案、时间线、测试向量
├── src/engine/       DemoHealth、拓扑影响、方案约束、时间线、简报、演示助手
├── src/providers/    demo/live/unavailable 适配层
├── src/storage/      决策会话与审核 localStorage
├── src/components/   可视化与交互组件
├── src/pages/        总览、管段、情景、简报、助手、关于
└── src/tests/        58 项测试
```

## 边界

- 只使用合成数据；不读取真实 GIS、拓扑、压力、工单、企业接口或生产数据库。
- 不控制真实设施；不派单、不关阀；所有建议需人工复核。
- 不提供失效概率、剩余寿命、实时水力或物理级联结论。
- 服务恢复时间只表示计划图通路回放，不是水力、水质、实际供水量或现场执行。
- 不 `import` 工程 `01_工程项目/src/`；比赛代码只在本目录维护。
