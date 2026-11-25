# FASTDEV-04：燃气只读 Agent 交互

基线 e10d2810f17d1a757bf2b4cff64695ff4830ca0a。同一 GasCockpitProvider 与 #/gas；
不修改冻结内核、算法、Report、main 或旧预览。

发送按钮真实调用 GET /api/v1/gas/engineering/agent/health 和
POST /api/v1/gas/engineering/agent/query。健康 DTO 增加 tools_status/read_only，
98 个 schema 均从运行时导出；不把 model_status=unavailable 解释成只读查询不可用。

服务端根据注册身份解析 K-001 及隔离事件别名。定位结果来自事件的 9 项关联资产；
无经纬度时只高亮合成拓扑。控制请求不执行；ACL 在读取数据前检查。
前端按合同校验结果，展示工具、对象、证据和限制。取消、超时、重复提交、卸载及
案例切换隔离旧响应，401/403/503 不向旧平台 Agent 探测或伪造回答。

可复跑命令（独立高速副本，保留 package-lock 和依赖边界基线）：
- engineering: PYTHONPATH=. python -m pytest tests/test_gas_agent_tools.py tests/test_gas_agent_http.py tests/test_gas_accel.py tests/test_gas_application.py tests/test_gas_auth_default.py tests/test_gas_s2_fix1_acl.py tests/test_gas_sprint01_fix2_integration.py tests/test_s1fix2_security_contract.py -q
- web: npm run typecheck; npm run contracts:check; npm run contracts:test; npm test; npm run build
- root: python scripts/check_module_boundaries.py
- web: GAS_TEST_PYTHON=<isolated-python> BROWSER_EXECUTABLE=<chromium-headless-shell> node scripts/gas-agent-real.browser.mjs

实测：Python 112 passed/1 warning；Vitest 331 passed/12 skipped；合同7断言；
TypeScript/构建通过；模块边界642文件/1413边/0违规；冻结47/47一致。
12 skipped 是原浏览器门控单元测试，另外执行真实 Chromium 三视口。
真实浏览器9组检查通过：五工具、资产下钻、事件9资产拓扑、401/403/503恢复、
真实TCP超时、旧响应隔离、取消及断网重试；无 page.route 模拟业务响应。
测试服务均只监听回环，使用临时测试登录与最小synthetic主体；不构成生产BFF。
Qwen尚未接通。当前parser_source=local_rule_not_llm，未调用模型。

已修复基线健康探测错误：不再从燃气接口失败回退旧平台接口并覆盖状态。
旧SSR测试随界面合同变化更新，保留不伪装模型、无结果时不显示工具成功的断言。
