# HY4-GAS-SYNTHETIC-RC01 联合集成独立验收报告

## 1. 审查范围与版本

本报告针对 `integration/gas-synthetic-rc01` 的联合候选，只允许合成数据、只读接口和隔离内网验证，不代表真实燃气工程验收、公网发布或设备控制授权。

- 基线：`integration/gas-synthetic-candidate` / `be04c41aa377c80701d73649834a22f078026d45`
- 后端认证输入：`fix/gas-integration-auth-default` / `442ae27ead10f60120b9331691ee48ae9eae38dd`
- 前端合同输入：`feature/gas-contract-convergence-01` / `053f0886d7898d16eed275e535f2d7355dc53575`
- 后端合并提交：`640fae43419498a55de69a0e658dd825d304f0fd`
- 当前联合源码提交：`4862808aca9107207eb1f263abfda949e79945de`
- 三方共同祖先：`be04c41aa377c80701d73649834a22f078026d45`
- 工作树：`/workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/integration-gas-synthetic-rc01`
- 正式工程树：未覆盖；未连接真实数据库、模型、设备或公网。

`442ae27` 的安全专项在该提交上独立复跑为 `23 passed, 1 warning`，联合分支再次复跑同一燃气安全、Adapter、OpenAPI范围并保留日志。该结果是独立复验，不沿用开发方历史 PASS 声明。

## 2. 关键文件身份

| 文件 | SHA-256 |
|---|---|
| `engineering/experiments/gas_m0/data/gas54_network.json` | `991d8f74b6d8e4799ae8e7778e137954e3b6a6b1ae619ea885138358eff9e306` |
| `engineering/experiments/gas_m0/data/gas54_case.json` | `60a7f94ea4d972c95e416ac9e20920e59802a02a0ac539861ca109f4beeccc46` |
| `engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json` | `9a97933e5a36335efa0ae6569f63897a1e3f1230c8f841973d3e35f14f23a591` |
| `web/src/generated/platformOpenapi.ts` | `6f1e29bd63e4aec5941bdedd9b6aafe2514121aa548c6ef72fbf1ff5b5def759` |
| `web/src/shared/contracts/platform.ts` | `0078b0d4f731f3c29005d5cdbd964ff94f75096b9a91d50e8a19aa1d9c5434b5` |
| `web/src/features/gas/types.ts` | `a19db84fc27fd2bb355ef21ba87121e779e96cd4eebac6dca7c3b2db2fdd2110` |

`types.ts` 为生成合同的受控重导出，未保留第二套手写 API DTO。OpenAPI 为 85 个 schema；生成检查显示 SHA 与静态 OpenAPI 一致。

冻结文件清单 `docs/architecture/FROZEN_FILES.json` 共 47 个文件（Agent20、algorithm22、Report5），联合分支复算结果 `47/47 PASS`，详见 `evidence/gas_synthetic_rc01/frozen_sha_check.log`。

## 3. 认证与真实 HTTP

真实服务在 127.0.0.1 临时端口启动，使用 `bootstrap=False`、`start_worker=False` 和隔离平台根目录。实测矩阵见 `evidence/gas_synthetic_rc01/real_http_matrix.log`：

| 场景 | 实测结果 |
|---|---|
| controlled 模式无服务端认证配置 | 启动失败，`RuntimeError`，fail-closed |
| `/health`（令牌模式） | 200 |
| 无令牌访问 gas case | 401 |
| 错误令牌 | 401 |
| 正确令牌、synthetic 模式 | 200 |
| 未知 `case_id` | 404 |
| ACL 主体无 `synthetic` 模式 | 403 |
| ACL 主体有 `synthetic` 模式 | 200 |
| Fixture 根目录不可用 | 503，正文为脱敏业务错误 |

客户端 `X-Water-Client` 未被视作认证依据；GET 路径没有写入、Agent、Report 或设备控制调用。受控发布必须由服务端 Token/主体 ACL 和网络策略提供认证，不可依靠匿名或浏览器自填标识。

## 4. 合成燃气业务语义

合法案例 `syn-gas-case-K-001` 的真实服务响应统计为：54 节点、73 edges、73 pipes、31 valves、2 sources、46 consumers、46 services。响应状态为 `partial`，`model_status=candidate`，`validation_status=not_business_validated`，`physical_supply_verified=false`。

接口明确保持以下边界：

- 只做合成案例的图结构/隔离候选分析。
- 无压力、流量、泄漏概率、扩散、真实供气恢复或现场控制结论。
- 评分 `status=unavailable`，G01/G02/G04 仅作为候选合同必填标识，当前没有逐管段可追溯评分输入。
- `H` 保持 unsupported，`P` 不是失效概率，`E` 不是预测置信度。
- 不把无向图连通性改写成物理供气状态，页面应显示未知。

## 5. 测试证据

### 后端与安全

- `test_gas_auth_default.py test_gas_application.py test_module_openapi.py`：`23 passed, 1 warning`，见 `backend_gas_security_openapi.log`。
- Report/统一合同回归：`324 passed, 1 warning`，见 `report_regression.log`。
- 供水原先五项 Fixture 阻断测试，在临时映射且 SHA 已核对的原有结果文件上：`5 passed, 1 warning`，见 `water_fixture_five.log`。测试后删除 Excel、metadata 和符号链接，未入 Git、未写正式树。
- LangGraph 边界测试使用 `.venv-agent`：`23 passed`，见 `langgraph_boundary_with_fixture.log`。在无 Fixture 时独立重跑为 `21 passed, 2 errors`，错误均为缺少供水 metadata，见 `langgraph_boundary.log`；补齐已核对的临时 Fixture 后全套通过。
- 冻结 SHA：`47 passed, 0 failed`。

### 前端合同与构建

在从联合提交同步的临时 Windows 前端目录执行：

- `npm run contracts:test`：7 项合同断言通过。
- `npm run typecheck`：通过。
- `npm run contracts:check`：通过，OpenAPI SHA=`9a97933...`、schemas=85。
- `npm run build`：通过，Vite 生产构建完成。
- `npm test -- --run`：17 个测试文件通过、1 个浏览器交互文件跳过；`203 passed, 12 skipped`。

前端现有 `gas-contract-convergence-01` 浏览器记录为 26/26，但其 API 请求由 `page.route` mock，不能作为 live FastAPI 证据。对当前联合构建执行真实浏览器访问 `http://127.0.0.1:4183/#/gas` 时，受本机 Chromium 沙箱策略阻止，返回 `ERR_NETWORK_ACCESS_DENIED`；因此“React 浏览器直接读取 live FastAPI”在本轮标记 `UNVERIFIED`，没有把 mock 结果写成真实联调通过。

服务器 179 没有 Node/npm，无法在该服务器直接运行 React 浏览器套件；这属于环境限制，不是代码通过证明。

## 6. 结论矩阵

| 审查项 | 结论 | 依据 |
|---|---|---|
| A 认证安全 | PASS | 23 项专项 + 真实 401/403/404/503 矩阵 |
| B OpenAPI/TypeScript | PASS | 静态/生成 SHA 一致、85 schemas、typecheck、contracts check |
| C FastAPI真实网络 | PASS | uvicorn 127.0.0.1 实测矩阵 |
| D React到live FastAPI浏览器 | UNVERIFIED | Chromium 本机拒绝本地网络；现有 26/26 为 mock |
| E 合成业务与专业边界 | PASS | 54/73/31/2/46 计数及 unknown/partial/candidate 语义 |
| F Agent/供水算法/Report冻结 | PASS | 47/47 冻结 SHA；Report 324 回归；LangGraph 23 |
| G 受控内网合成发布 | PASS WITH CONDITIONS | 需服务端认证、loopback/内网网络策略、脱敏日志和可回滚停止机制 |
| H GitHub main 合并 | BLOCK | 仍需独立复验 live 浏览器证据及集成审批，未执行合并 |

## 7. 最小剩余条件与风险

1. 在具备浏览器访问本地服务能力的隔离环境重新执行真实 React→FastAPI 流程，至少确认合法 200、401、403、404、503、超时/取消和重试；mock 浏览器结果不能替代。
2. 部署时禁止公网监听和默认匿名访问；若浏览器不携带令牌，必须由同源受控代理或服务端会话注入认证，不能把 Token 编译进前端包。
3. 不得把 `partial`、`unknown` 或候选评分提升为正式燃气健康等级或物理供气恢复结论。
4. 五项供水测试依赖的原有样本只可在已核验授权环境临时挂载；本报告的通过不表示该文件应提交到 Git。
5. 本联合候选未接入真实燃气数据、真实模型、真实设备和公网，不允许以本报告启用这些能力。

## 8. 分级裁决

- 隔离合成代码与接口测试：**PASS**。
- 受控内网合成环境：**PASS WITH CONDITIONS**，满足第 7 节认证、网络、浏览器现场验证和回滚条件后可候选运行。
- 真实燃气数据/真实模型/设备控制：**BLOCK**。
- 公网 Agent 或燃气 API：**BLOCK**。
- 合并 GitHub main：**BLOCK**，等待 HY4 对最终联合提交完成独立审查并补齐 live 浏览器证据。

本报告仅记录 Codex 在联合提交上的独立证据，不能替代尚未完成的 HY4 最终签字，也不构成部署许可。