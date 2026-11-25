# 燃气中控双主题与工作台视觉升级

以 FASTDEV-06 `225fefe5910ae07a3a2a2a629f80fe3bf62d0e32` 为基线，在 `feature/wpn-gas-ui-refine-01` 实施。沿用原 GasCockpit / GasCockpitProvider / #/gas，后端、生成合同和冻结文件均无修改。

设计来源为 Owner 接收的 Gemini v0.2 HTML 与 CSS Token。HTML SHA256 为 `7bdf903afd2675917033dc52042514099856de64cc74b2fa03daa442e1eb20b6`，Token 文件为 `5e7f9657631d380cae602444f5753b89571a7d4b0f69269cfc46d1cf0dfff445`。设计稿中的在线状态演示没有移入产品。

## 本轮实现

- 默认深海蓝 / 可选浅色工作台，共享组件和 Provider；仅保存主题偏好 `wpn-gas-theme`，无会话、业务数据或凭据持久化。主题切换不重新请求数据、不丢失选中资产和路由。
- 合并顶部应用栏、六项实际合同 KPI、三栏工作区与底部时间线。11窗口、三模式、三种真实布局和窗口恢复入口保留。
- 原智能助手以右侧抽屉展开，保留历史、查询、取消、重试、资产/事件链接、证据和拓扑动作。健康与查询响应分别呈现，规则结果不标成 Qwen 推理；错误不被旧成功状态遮盖。
- 合成来源徽标常驻。无坐标时保持行政参考图，不生成设施坐标；风险/CUV状态继续来自既有API。去掉密集SVG网格和模糊发光滤镜。
- 1280×720默认收起区县浮层，选择区县可自动展开；1024×700使用适度页面纵向滚动，避免压缩掉地图与时间线。复用项目本地中文字体。

## 复验

命令在锁定 Node22、既有 Python venv 和 Chromium 环境执行；路径由环境变量指定，不改锁文件。

```sh
cd web
npm run typecheck
npm run contracts:check
npm run contracts:test
npm test
npm run build
# 真正的FastAPI TCP与Chromium；不拦截业务响应。
GAS_AGENT_MODEL_MODE=disabled node scripts/gas-ui-refine.browser.mjs
GAS_AGENT_MODEL_MODE=disabled node scripts/gas-agent-real.browser.mjs
# 真实模型须使用已批准、健康的回环网关；密钥仅服务端文件注入。
GAS_AGENT_MODEL_MODE=local_qwen node scripts/gas-agent-real.browser.mjs
cd ..
python3 scripts/check_module_boundaries.py
```

`GAS_TEST_PYTHON`、`BROWSER_EXECUTABLE`、`LD_LIBRARY_PATH`、`GAS_EVIDENCE_DIR`用于选择隔离运行环境。Qwen测试额外使用`GAS_AGENT_GATEWAY_URL`与`GAS_AGENT_GATEWAY_KEY_FILE`。不将密钥写入源码或浏览器。

- Vitest默认：348通过，12项历史浏览器门控默认跳过。
- 显式 `RUN_BROWSER=1 npm test`：357通过、3失败。同环境原基线相同的浏览器文件为9通过、3失败；失败均在`interaction.browser.test.ts:162`等待已被燃气入口替代的“城市管网总览”。这是现有旧入口合同与历史测试不一致，本轮未修改其断言，不能称全量测试全部通过。
- 26组新的真实HTTP/Chromium检查：两主题×三视口、模式/布局、11窗口开关恢复、鼠标拖拽/缩放/平移、节点选择、资产分页、9资产下钻、CUV、规则/服务错误、主题不重取业务数据。
- 既有Agent真实HTTP/Chromium：规则9组通过；真实Qwen最终串行复跑9组通过（15个成功模型查询、30次推理调用），首次降级失败与后续复跑均保留在证据目录。覆盖三个视口、五类工具、权限拒绝、故障恢复、过期响应、超时、取消和断网。
- 93项燃气/Agent/ACL/DTO相关Python测试通过（含真实TCP测试）。模型隔离单测与真实Qwen浏览器证据分别登记。
- 当前运行时与静态OpenAPI均98 schemas，生成TS无漂移；没有回退为历史94 schemas。47冻结资产不变，模块边界0违规。

具体日志、截图、Network记录、模型证据及最终提交/构建SHA在同轮持久化交付目录提供；数量不叠加为某个“全量验收”。

真实Qwen在本轮一次全套复跑中转入规则路径，严格模型来源断言因此失败。UI如实显示了降级。首次失败脚本未保留该次降级响应体，不能推断为特定环境故障；本轮只改善测试记录，未放宽断言、模型校验或修改网关。后续复跑成功也不撤销此稳定性风险。

## 使用与待复核

仅为受控合成开发候选。测试访问层采用回环监听、临时测试身份和受限数据集授权，并非生产BFF。临时验收服务均关闭；V1旧预览与原开发预览不替换。真实工程数据、正式身份系统、授权GIS和专业风险模型准入条件未改变。

Gemini轻量复核重点：1280×720事件区滚动密度；1024×700页面纵向滚动与浮层位置；浅色下地图、详情和助手状态对比度。无需重做源码开发或完整审计。

回滚：不合并main，直接返回原FASTDEV-06提交。主题偏好可在界面切换回深色，或只移除`wpn-gas-theme`键；不触及业务存储。后续燃气功能继续沿同一Provider、API及只读工具合同推进。
