# CODEX-WPN-GAS-COCKPIT-V11-FIX1 整改与版本归一报告

## 1. 审查与开发快照

本轮在 179 的独立工作树完成，未修改正式主工作树、`main`、Qoder Sites 或后端服务：

| 项目 | 身份 |
| --- | --- |
| V1.1 开发提交 | `080596dce93170301175b70f2c6c01ae13359fb1` |
| V1.1 tree | `9cde6cb329293cb52bf60bb7cf7b985ad0d2c504` |
| V1.1 父提交 | `a6757752b3d62bf5ea5f217b7f8148593a3daef7` |
| 179 原审查树 | `a6ca5956e466e7ae28641e92ca5ae76319df6fe8`，tree `e9df4480aaa8cff41ad06547dfd7741907a9d98f` |
| 179 原审查分支 | `feature/gas-cockpit-restore-publish-01`，工作树含 15 项未提交变更 |
| FIX1 分支 | `fix/wpn-gas-cockpit-v11-fix1` |
| FIX1 代码提交 | `3da4ee8e5909b08751757308fa867295ff69c0c8` |
| FIX1 画布提交 | `ea641fcc1975194fbe70c96bf7f5aad3345e49dc` |
| FIX1 画布提交 tree | `89845e93274382f859f5f52a5403226d21094f28` |

179 原审查树没有 `080596d` 对象。已通过 Git Bundle 传递完整提交对象并在独立仓库检出，Bundle SHA-256 为：

`4eb1b5024dfd27c6ef1fdf917aad597a2083efe42794586592fc666054a2d9a5`

Bundle 验证通过，工作树 HEAD 与 V1.1 提交一致；没有把 179 的脏 `gas-control-room` 工作树复制为正式版本。

## 2. 四项 Fixture 导入问题归因

在原 179 脏树独立复跑模块边界检查得到 4 项 `unresolved_local_import`：

```text
web/src/gas-control-room/data/loadGasFixture.ts:1 ../fixtures/gas54_network.json?url
web/src/gas-control-room/data/loadGasFixture.ts:2 ../fixtures/gas54_case.json?url
web/src/gas-control-room/data/loadGasFixture.ts:3 ../fixtures/gas54_result.json?url
web/src/gas-control-room/data/loadGasFixture.ts:4 ../fixtures/manifest.json?url
```

该树的检查结果为 519 个文件、1277 条依赖、4 项违规。它属于另一套未提交实现，不属于 `080596d` 的源码。

在 `080596d` 及 FIX1 工作树中没有 `gas-control-room` 目录，也没有任何 TypeScript/TSX/MJS 的 `?url` Fixture 导入；燃气页面继续使用既有 API DTO 和合成响应。FIX1 没有关闭或放宽边界检查器，也没有修改正式依赖基线。

## 3. 实际源码修改

### 3.1 中央拓扑比例

在 `.gas-window` 通用规则之后为中央拓扑窗口增加桌面最小高度 `1050px`，避免通用 `min-height: 0` 覆盖拓扑尺寸。地图画布改为纵向 flex，SVG 填满实际画布；坐标、viewBox、节点、边、阀门及业务数据均未改变。

### 3.2 浏览器事件稳定性

移除 React wheel 事件中的 `preventDefault()`。Chromium 原实现会产生 `Unable to preventDefault inside passive event listener invocation` 控制台错误；缩放状态更新逻辑保留，补充探针验证 wheel 缩放、空白区域平移和适配视图。

### 3.3 测试与证据

新增 `web/scripts/gas-cockpit-v11-fix1.browser.mjs`，在原 19 项探针之外补充阀门选择、缩放、平移、播放/暂停/重置、10 轮播放资源稳定性、请求方法及 API/Agent 写请求检查。所有请求使用合成 Fixture 路由拦截，不连接真实 FastAPI。

## 4. 1920×1080 布局实测

测量对象明确为真实可见的 `.gas-window-topology`、`.gas-map-canvas` 和 SVG，视口为 Chromium `1920×1080`。

| 指标 | V1.1 原始提交 | FIX1 | 结论 |
| --- | ---: | ---: | --- |
| 主工作区宽度 | 1836 px | 1836 px | 保持 |
| 拓扑窗口宽度 | 1220.66 px | 1220.66 px | 63.58% 视口，超过 50% |
| 拓扑窗口高度 | 929.97 px | 1050 px | 53.58% → 56.59% 主工作区 |
| 地图画布高度 | 766.58 px | 886.61 px | 实际绘图区增大 |
| SVG 可视宽度 | 1194.66 px | 1194.66 px | 62.22% 视口 |
| SVG 可视高度 | 764.58 px | 884.61 px | 填满画布，未使用隐藏 SVG |

补充探针结果：拓扑窗口宽度比例 `0.635758`，拓扑窗口/主工作区高度比例 `0.565857`。三种模式和三种布局仍由同一组件状态切换，11 个窗口未删除。

## 5. 测试矩阵

所有结果均在 FIX1 最终代码工作树执行；开发方历史日志未作为本轮通过依据。

| 检查 | 实测结果 |
| --- | --- |
| TypeScript `npm run typecheck` | PASS |
| OpenAPI `npm run contracts:check` | PASS，85 schemas，生成类型 SHA `dc52322e26cf583f7c777d125121ece99e42f7e2256ffeed6a57322429154b38` |
| 生成类型 `npm run contracts:test` | PASS，7 assertions |
| Vitest `npm test` | 203 passed，12 skipped；17 个文件通过、1 个浏览器文件 12 项因非浏览器运行器跳过 |
| 生产构建 `npm run build` | PASS，Vite 106 modules |
| 模块边界 | PASS，531 checked files、1245 dependency edges、104 initialization edges、0 violations、0 cycles |
| 原 V1.1 Chromium 探针 | PASS，19/19 |
| FIX1 Chromium 补充探针 | PASS，30/30 |
| 冻结资产 | PASS，47/47 SHA 一致 |

19 项原探针覆盖 54 节点合成案例、11 窗口、三模式/三布局、0h/0.5h/1h 时间线、节点/管段选择、窗口拖拽/隐藏/恢复、reduced-motion、页面错误和控制台错误。30 项补充探针另外覆盖阀门、缩放、平移、播放/暂停/重置、10 轮播放 DOM/heap 观察及只读请求约束。

浏览器实际观察到两次 `GET /api/v1/gas/cases/syn-gas-case-K-001`（首次加载和 reduced-motion reload），没有非 GET、Agent 或写请求。这里是 Playwright 合成路由拦截证据，不等同真实 FastAPI TCP 联调；本轮按任务要求没有启动或部署后端。

## 6. 文件 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| `web/src/features/gas/GasCockpit.tsx` | `217eafd3959c0e1dba360002c98ccd7f279ac15049e511e97e99de730aaf70aa` |
| `web/src/styles.css` | `818388bf17d1be1ab7adfd39170bb58e6c87a086f87ed23c19fcfd25c34a20ba` |
| `web/scripts/gas-cockpit-v11-fix1.browser.mjs` | `ff6034d5d2c479c2c0f49f46470c47159463e2f2fb00ebf3b01b38abfe22bcd2` |
| `docs/evidence/gas-cockpit-v11-fix1/browser-results.json` | `6a36c8ea7ec9ef3767e2da31f2a70bd2cd0dfef900510da97094ba36004172db` |
| `docs/evidence/gas-cockpit-v11-fix1/browser-results-19.json` | `bdeefdfe72c8537d11e16901ba324681aaa05344666a390d21e8fe214bbb15f7` |
| `docs/evidence/gas-cockpit-v11-fix1/build-sha256.txt` | `f4dcb5c19fff2b216765ec571424be70b28261258704bdb470611960c278305f` |
| `docs/evidence/gas-cockpit-v11-fix1/frozen-sha.log` | `4d81d7591a6e20340e356fea429806856dac1ce61d9c93a93236f3136d8d5aab` |

构建产物清单的 SHA-256 为 `f4dcb5c19fff2b216765ec571424be70b28261258704bdb470611960c278305f`，构建文件位于 `docs/evidence/gas-cockpit-v11-fix1/build-sha256.txt`。发布包应从 FIX1 分支重新执行 `npm run build`，不携带 `node_modules`、Bundle、凭据或真实数据。

## 7. 业务与安全边界

- 仅展示 54 节点、73 管段、31 阀门、2 气源、46 消费对象的合成案例。
- 保留 `synthetic`、`partial`、`graph_connectivity_only`、`not_business_validated`、`unknown` 等语义。
- 不计算压力、流量、泄漏扩散、失效概率、正式 H 或现场关阀/复气指令。
- 不修改 FastAPI、OpenAPI、Agent、供水算法、Report 或部署配置。
- 报告与任务入口仍只做既有导航；页面没有新增写接口或后端副作用。
- 真实 FastAPI TCP、真实工程数据、真实模型和公网部署均未执行，也不因本轮测试通过而获准。

## 8. 交付与后续复核

FIX1 代码和证据提交链为：

```text
080596d  ->  3da4ee8  ->  ea641fc
```

报告文件在 `ea641fc` 之后作为独立交付提交加入；最终报告提交 SHA 以 179 上 `git rev-parse HEAD` 和 `git show -s` 为准。Qoder 只能使用该最终提交的 `web/dist` 构建包进行候选发布，不能使用旧 `a6ca595` 脏树或另一套 `gas-control-room` 实现。

本轮完成的是版本归一、边界问题排除、拓扑比例与前端回归，不代表正式上线或真实燃气业务验收。
