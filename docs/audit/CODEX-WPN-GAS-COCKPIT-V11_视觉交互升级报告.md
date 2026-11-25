# CODEX-WPN-GAS-COCKPIT-V11 视觉交互升级报告

## 1. 版本核验与开发边界

开发分支为 `feature/wpn-gas-cockpit-v11`，基于 GitHub `origin/main` 的 `8d70738ff0ca1223e9609e0fb941224e4df9d0cd` 建立。

核验结果如下。

- 目标 Qoder 站点 `gas-cockpit-arsyibophfs.qoder.website` 实际返回通用 WaterPulse 页面，未暴露可核验的燃气中控源码提交。
- GitHub 仓库没有可直接移植的 `gas-control-room` 或 `GAS-SCREEN-VISUAL-02` React 分支。
- 工作区保留两份原始燃气 HTML 原型和 `gas54_network.json`，其 54 节点、73 边及 schematic 坐标作为展示参考。
- 本轮采用已核验的 React `GasPage`、统一 FastAPI Gas DTO 和原始合成原型作为输入，未把原始 HTML 的风险公式、压力、流量、泄漏或预测数值搬入前端。

这是一次受控的源码升级候选，不是 Qoder 发布，也没有覆盖站点、合并 `main` 或连接真实燃气数据。

## 2. 实际修改

### `web/src/features/gas/GasCockpit.tsx`

新增完整燃气中控视图层，保留统一 API 的只读数据来源。页面包含 11 个可隐藏窗口。

1. 中央管网拓扑。
2. 情景与触发。
3. 故障与隔离影响。
4. 当前选中对象。
5. 处置时间线。
6. 服务对象清单。
7. 图层管理。
8. 数据接入状态。
9. 证据与版本。
10. 评价边界。
11. 任务与报告入口。

中央 SVG 拓扑支持节点、阀门和管段的形状区分，故障/隔离边使用橙色强调，选中对象显示详情。支持滚轮缩放、空白区域平移、一键适配视图和状态覆盖层。

### `web/src/features/gas/GasPage.tsx`

保留原有请求、取消、错误和合同校验流程。成功响应交给 `GasCockpit` 展示，前端不执行图遍历、评分或时间线计算。

### `web/src/styles.css`

新增深海蓝视觉 Token、响应式窗口网格、三种布局、三种运行模式、拓扑图层、时间线、证据和安全边界样式。保留 `prefers-reduced-motion` 支持。

### `web/scripts/gas-cockpit-v11.browser.mjs`

新增 Chromium 实际交互探针，使用批准的合成 `gasCaseResponse.json` 响应，不连接真实服务。

## 3. 业务语义保持

- 页头明确显示 `synthetic`、`partial`、`graph_connectivity_only` 和 `not_business_validated`。
- 54 节点、73 管段、31 阀门、2 气源、46 消费对象均来自接口响应。
- 0h、0.5h、1h 事件来自当前案例时间线，页面注明这些是给定合成事件，不是专业预测。
- 物理供气、压力、流量、失效概率和正式 H 均显示未知、未接入或不支持。
- `P` 仍按候选条件/风险分数解释，`E` 仍按数据覆盖解释。
- 没有新增关阀、复气、设备控制、泄漏扩散或真实安全结论接口。
- 任务与报告入口只触发既有导航，报告仍由平台服务端读取。

## 4. 布局和交互

三种模式分别为综合态势、风险分析和应急推演。三种布局分别为综合态势、风险分析和应急推演预设。布局使用命名网格区域，窗口支持拖拽偏移、隐藏、恢复和 CSS 调整大小。

时间线支持阶段点击、播放、暂停和重置。减少动态设置下播放按钮禁用，拓扑、状态和业务数值不会因动画生成新结果。

## 5. 测试结果

### TypeScript、Vitest 和合同

| 检查 | 结果 |
| --- | --- |
| `tsc -p web/tsconfig.json --pretty false` | 通过 |
| `vitest run` | 203 passed、12 skipped。12 项为既有浏览器文件在未启用浏览器环境时跳过 |
| `node scripts/generate-platform-types.mjs --check` | 通过，85 schemas |
| `node scripts/test-platform-types.mjs` | 7 assertions passed |
| 生产构建 | 通过，Vite 产物生成 |
| 模块边界 | PASS，0 violations、511 checked files、1245 dependency edges、104 initialization edges、23 legacy exceptions、0 cycles |

模块边界证据位于 `docs/evidence/gas-cockpit-v11/boundary.stdout.json` 和 `boundary.json`。

### Chromium 实际浏览器

`gas-cockpit-v11.browser.mjs` 在本地生产构建上执行 19 项断言，结果为 19 passed。

覆盖内容包括：

- 54 节点合成案例和 11 个窗口。
- 三种运行模式和三种布局预设。
- 0h/0.5h/1h 时间线切换。
- 节点和管段选择。
- 窗口拖拽、隐藏和恢复。
- `prefers-reduced-motion`。
- 页面错误和控制台错误为零。

浏览器探针使用 Playwright 路由拦截返回合成 Fixture，因此证明了真实 Chromium 中的页面交互和状态渲染，没有证明真实 FastAPI TCP 服务联调。生产构建本身已通过 Vite 构建。

断言结果位于 `docs/evidence/gas-cockpit-v11/browser-results.json`。

### 截图

改造前截图来自 `origin/main` 的原始 GasPage，改造后截图来自 V11 构建。两组均覆盖：

- 1920×1080
- 2560×1440
- 3840×2160
- 1280×800
- 1280×720
- 375×812

截图目录：

- `docs/evidence/gas-cockpit-v11/before/`
- `docs/evidence/gas-cockpit-v11/after/`

## 6. SHA 与构建产物

修改文件 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `web/src/features/gas/GasPage.tsx` | `1a2067e518ad767a60e1f09487ffb6324cc0cb808e0c494762d667651597f28e` |
| `web/src/features/gas/GasCockpit.tsx` | `19d70504e3710e9edefdb316b16145fcc463a8690ebb48a14d7c791c4581a37d` |
| `web/src/styles.css` | `52d76135d0113ee0d4a60738897f6eb1cbaf4ae4b47342aff2867456a356bc40` |
| `web/scripts/gas-cockpit-v11.browser.mjs` | `cb66bf8e3f2e7764c1f2d8e6944e866664527d9e98fbfcab13850627acb36eb0` |

构建产物逐文件 SHA 位于 `docs/evidence/gas-cockpit-v11/build-sha256.txt`，该清单 SHA-256 为 `f3ed132481bd924b2aac515ba0fa3525e71bea9b90c7c5e7c8d08e67474d2fb3`。

## 7. 未完成事项与停止条件

- 未执行真实 FastAPI 服务的 TCP 浏览器联调。浏览器探针使用合成响应拦截，真实服务联调应由后续独立验收执行。
- 未接入真实工程燃气数据、真实模型或设备控制接口。
- 未发布 Qoder Sites，未覆盖现有站点，未合并 `main`。
- V11 之前的草稿工作树与本分支隔离，本分支只包含本报告所列的燃气前端升级。

本轮开发、测试和证据整理完成后停止，交 Gemini Argon 与 WorkBuddy HY4 独立审核。
