# 燃气工程唯一发布入口

当前在 `web` 执行 `npm run build:project`，产物位于 `web/dist/project`，入口为 `#/gas`。
一键演示仍使用 `npm run build:gas-demo`。工程版的事件、资产、GIS、质量回放、推演、智能体和合成证据报告均在当前中控内使用。

## 历史构建记录

下列双业务构建命令仅供复现已归档的比赛和回滚版本。当前开发与发布范围为燃气工程。

当前以同一套 React 源码提供两个产品构建。发布责任固定为：项目演示站点由 Codex 负责，赛题3比赛站点由 Qoder 负责。业务组件、算法、API Client、OpenAPI 与 GasCockpitProvider 不复制；构建身份固定，不由 URL 或浏览器缓存切换。

| 构建 | 默认入口 | 保留功能 |
|---|---|---|
| 赛题3比赛版 competition | `#/overview` | 原供水总览、管段详情、情景/隔离/方案比较、时间线、助手、简报与平台只读Report、关于；完整燃气中控 |
| 项目演示版 project | `#/gas` | 完整燃气中控、双主题、11窗口、三模式/三布局、GIS参考/合成拓扑、资产分页、事件与9资产下钻、CUV及Agent |

比赛版从水务导航进入“燃气中控”，从燃气顶栏“供水中控”返回水务。旧 `#/gas-engineering`、`#/gas-control-room` 链接继续归一为 `#/gas`，编码后的事件/资产ID、分页及其他燃气路由参数保持原合同。项目版访问水务书签会转到燃气首页，不挂载水务Provider或请求水务API。项目版不展示水务菜单、按钮、品牌、说明或兼容平台报告入口；燃气火焰为其独立网站图标。

原显式 `#/overview` 被错误转为燃气的路由缺陷已修复。历史水务浏览器用例显式选择 competition 身份，原断言保持不变。

## 发布责任与构建隔离

- Codex：燃气项目演示站点，使用 project 产物；按既有localhost/SSH受控访问边界发布与维护，不自动扩大公网范围。
- Qoder：赛题3比赛站点，使用 competition 产物，保留供水与燃气；不操作项目演示站点。
- App 在构建期选择 GasProjectApp 或 CompetitionApp。供水页面、DemoProvider、评分/方案前端逻辑、旧助手/Report和静态水务样例只进入比赛构建。
- 项目入口只导入 GasSurface；它与比赛入口共用唯一GasPage/Provider，不导入水务useDataSource。
- Vite对实际渲染的模块清单执行水务依赖门禁；project发现水务运行模块即构建失败。frontend-product.json同时记录publisher及waterRuntimeModuleCount。
- 共享传输中的既有API名称和服务端鉴权合同不因产品命名变更而改名；生成合同与冻结历史资产保留。本次界面清理不是删除比赛业务或科研历史。

## 开发与构建

Node22，现有 package-lock。从 `web/` 执行：

```sh
npm ci
npm run dev:competition   # localhost 比赛版
npm run dev:project       # localhost 项目演示版
npm run build:editions    # 同时产生下面两份产物
npm run preview:competition -- --port 24773
npm run preview:project -- --port 24781
```

- 比赛版：`web/dist/competition/`
- 项目演示版：`web/dist/project/`
- 每份根目录的 `frontend-product.json` 标识 edition、领域和默认路由；不可混用两目录的 index.html 或 assets。
- 原 `npm run dev/build` 默认 project，原默认构建目录仍为 `web/dist/`。它会清理 dist，包括旧双版本产物；需要两版时最后执行 `npm run build:editions`。
- 保留相对资源路径和 hash 路由，页面刷新不要求服务器识别业务子路径。
- 仅通过 Vite 的 competition/project 模式选择产品；查询参数 `edition=competition` 无法给项目版开启水务。
- SSH访问、认证和网络策略沿既有受控预览方案；以上 preview 命令只提供静态服务，不会创建认证层或自动启动后端。

## 数据和权限

供水默认 DemoProvider，只使用既有明确标识的合成数据。显式 platform/live 连接失败仍显示不可用，必须用户明确选择Demo才切换，不静默补演示数据。原只读平台Report、未知值/partial及身份合同不变。

两版燃气均使用同一 `/api/v1/gas/*` 受控接口。区分 synthetic / partial / not_business_validated、真实模型或local_rule_not_llm；页面版本不决定数据授权。Qwen是否在线取决于当前服务状态，不因比赛版改为假回答。没有授权设施坐标、正式风险结果的能力继续不可用。

纯静态比赛托管不会自动获得燃气API或Qwen服务。若宿主只能提供静态资源，燃气会明确显示接口不可用；接入Sites仍需单独按已有API路径、身份与权限合同适配。本轮不自动连接公网与129、不嵌入服务端长期Token、不修改Qoder站点。前端路由范围不是后端权限边界，服务端Token及数据集ACL继续执行。

## 验证命令

```sh
npm run typecheck
npm run contracts:check
npm run contracts:test
npm test
RUN_BROWSER=1 npm test
npm run build:editions
npm run test:editions:browser
python3 ../scripts/check_module_boundaries.py
```

真实浏览器脚本 `scripts/product-editions.browser.mjs` 启动隔离FastAPI、受限合成身份和localhost访问层，使用真实TCP，不拦截业务API。供水离线链路是实际DemoProvider计算，不冒充水务FastAPI实测。测试专用Basic身份及后端Token随机生成，仅在子进程内使用；测试结束停止临时服务。

需按环境提供 `GAS_TEST_PYTHON`、`BROWSER_EXECUTABLE` 以及Chromium共享库路径。完整既有燃气26组交互和五类Agent工具另由 `gas-ui-refine.browser.mjs` 与 `gas-agent-real.browser.mjs` 覆盖（使用默认project构建）。

## 版本保护与后续交接

起点 `db93aa42ad2204f9344803ca44986f86def0c8cf`。本轮核实GitHub main仍为 `04943a05c3fcb7452ee92fb19c7dad78a596acec`；当前燃气工作沿已交付分支继续，未回退到旧main。

原服务器 `02_比赛作品` 有未提交内容，未覆盖、清理或吸收其未审核修改。历史UI候选和原Sites发布记录不因本轮入口整理自动获准。本次不改变任何后端、冻结47文件、生成合同、锁文件或已批准的依赖基线。

独立分支完成后交付两份构建的身份、源码差量、浏览器截图和测试日志；不合并main、不部署、不替换V1或FASTDEV06预览。

## 本轮实测记录（2026-11-17）

- TypeScript、OpenAPI/TS漂移检查、合同单测、默认project构建及两版独立构建：通过。
- 默认Vitest：361通过、12浏览器门控未启用；显式RUN_BROWSER=1：373全部通过。原3项水务总览导航失败在恢复正确比赛路由后通过，业务断言未删改。
- 双版本真实TCP/Chromium：43组通过，3视口、22张截图、187条业务响应、未捕获页面异常0。
- 既有燃气UI真实浏览器：26组通过；五类规则Agent真实HTTP/浏览器：9组通过。
- 模块边界0违规；冻结47项、整个engineering/server、98-schema OpenAPI、生成TS、锁文件与依赖基线不变。
- 供水平台Report浏览器合同用隔离Mock覆盖，不冒充供水完整后端真实TCP验收。本轮真实TCP范围为燃气。
- 本轮未重新运行真实Qwen推理；模型调用链源码和实时状态保留，规则路径明确标识local_rule_not_llm。真实模型运行环境和既有降级稳定性事项不因前端入口整合自动关闭。
- 仅双构建候选；没有合并、发布或替换现有预览。

## 项目版燃气专属清理（2026-11-17补充）

项目不只是隐藏水务导航：构建入口已隔离，项目实际水务运行模块为0。项目移除兼容平台报告按钮、水务品牌和原水滴图标，使用燃气标题与火焰图标。比赛版继续保留水务与燃气。

实测：373项Vitest全通过；两版真实TCP/Chromium46组通过，其中逐一检查项目三个视口、11窗口、按钮辅助标签、Agent结果和产物JS，没有水务界面内容；项目标题与图标身份正确。TypeScript、两版生产构建、合同检查及模块边界通过。冻结47项、后端与生成合同未改。本次没有替换在线站点，发布责任按上述Codex/Qoder分工执行。
