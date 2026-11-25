# FASTDEV-05：本地 Qwen 与燃气只读工具

在 FASTDEV-04 上增加 opt-in LocalGasModel；默认仍为规则路径。没有新建第二套网关。
复用 local_llm_fix2 历史仓，独立修复提交：
caf64df4d8f6e0fe5a2d794c75f88ca46fa3aef8
（父提交 58d7b3cfc8c8d88612e7f19784ac31023790b758）。
该仓独立历史以 bundle 交付，不拼接到工程 Git 历史。

模型服务只允许回环；网关固定回环上游、禁重定向、服务端鉴权、脱敏错误和 health。
保留既有原子额度预留/结算、幂等并发 lease、504、管理参数验证。网关55项通过。
不读取旧网关数据库或 FRP 凭据；测试密钥临时生成、仅 /nix 权限600文件保管；
本轮不声称已经轮换其他服务历史泄露的凭据。

Agent 前置统一身份/模式/数据集 ACL；拒绝请求不接触模型或 Fixture。
模型只返回白名单计划，ID限于服务端解析/明确选择的合法对象；不允许凭模型选择
未指明的事件。模型解释只选择完整证据句，不能自由改写数值、物理状态或风险结论。
无效计划、超时、畸形模型响应均回到显式 local_rule_not_llm，并记录尝试次数。
不支持 RAG、设备控制、真实GIS坐标生成或真实业务风险评价。

实际环境：2块 PPU-ZW810E96GB；/usr/local/bin/python3；
sglang 0.5.18+asllm2.2.0.9；既有 Qwen3.8-27B 18权重分片全部完成加载。
模型配置 architecture 为 qwen3_5；served-model-name 为 Qwen3.8-27B。
已记录配置/索引SHA，未重新计算55.56GB权重全部哈希；不宣称独立模型来源鉴证。
不下载权重。首次启动脚本曾因缺少 multiprocessing main guard 失败，
修正测试启动器后双PPU加载成功；不是硬件不可用。

启用条件（服务端环境，不进 Vite/浏览器）：
- WATER_RUNTIME_MODE=controlled（或local/development）
- GAS_AGENT_MODEL_MODE=local_qwen
- GAS_AGENT_GATEWAY_URL=http://127.0.0.1:<网关端口>
- GAS_AGENT_GATEWAY_KEY_FILE=<当前UID拥有、权限600的常规文件>
- Key须来自隔离网关测试scope，限定Qwen模型、额度和并发；不使用admin身份查询。
- 模型和网关监听127.0.0.1，关闭代理头信任，SQLite仅放可丢弃的本地测试盘。

运行已存在网关时，设置 LOCAL_GATEWAY_DATA/DB/CONFIG/BACKUP 到独立私有测试目录，
LOCAL_GATEWAY_UPSTREAM 指向回环模型服务。LOCAL_GATEWAY_UPSTREAM_TOKEN 仅由启动进程
从权限600文件读入，不写命令行/工程文件。admin token同样只保管在测试私有目录。
SGLang使用原有PPU SDK环境，tp-size=2、bfloat16、context-length=8192、
mem-fraction-static=0.65、max-running-requests=2；禁用CUDA graph；
HF_HUB_OFFLINE=1/TRANSFORMERS_OFFLINE=1；API key由进程内读取，main guard必需。
生产会话BFF、多人身份、原始凭据轮换仍需另行完成，不把验收代理当正式登录系统。

复验：
- engineering: PYTHONPATH=. python -m pytest tests/test_gas_model_provider.py tests/test_gas_agent_tools.py tests/test_gas_agent_http.py tests/test_gas_accel.py tests/test_gas_application.py tests/test_gas_auth_default.py tests/test_gas_s2_fix1_acl.py tests/test_gas_sprint01_fix2_integration.py tests/test_s1fix2_security_contract.py -q
- root（显式模型配置）：python engineering/scripts/gas_model_smoke.py --output <证据目录>/real-tcp.json
- web：npm run typecheck；npm run contracts:check；npm run contracts:test；npm test；npm run build
- web（显式模型配置）：node scripts/gas-agent-real.browser.mjs
- 规则浏览器回归：GAS_AGENT_MODEL_MODE=disabled GAS_EVIDENCE_LABEL=05-rule node scripts/gas-agent-real.browser.mjs
- root：python scripts/check_module_boundaries.py
测试产物在交付目录，不包含密钥、数据库或原始模型日志。

OpenAPI仍98 schemas（FASTDEV-03基线已有Agent schemas），运行时生成TS；
增加明确的 qwen_intent/ready、模型ID、调用次数/usage及证据选取说明。
既有字段和五种工具兼容，未替换历史94 schema版本合同，未修改冻结47文件。

本轮实测：Python142 passed/1 warning；独立网关55 passed/1 warning；
Vitest332 passed/12 skipped（原浏览器门控项）；TypeScript、合同7断言、生产构建通过。
模块边界646文件/1426边/0违规；冻结47/47一致。
真实TCP五场景均经过两次Qwen调用；401/403/404/422/503及设备控制拒绝通过。
真实Chromium规则模式与Qwen模式各9组、三视口通过，包含断网/超时/取消/竞态恢复，
业务请求未使用page.route；各记录55个API响应。两条路径分别保存JSON和截图。
初次浏览器运行后仅收紧模型响应形态校验，已在最终源码重新运行两种模式。
未进行全量权重来源鉴证、广泛自然语言准确率评测、生产会话或多用户工程发布验收。
