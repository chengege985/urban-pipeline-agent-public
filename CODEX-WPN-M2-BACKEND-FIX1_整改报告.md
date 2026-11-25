# CODEX-WPN-M2-BACKEND-FIX1 整改报告

## 1. 审查基线与范围

本轮在179服务器建立独立 worktree，只修改燃气 Adapter 和回归测试，未修改正式工程主工作树、main、部署、数据库、真实燃气数据、Agent、供水算法或 Report。

- M2父提交：7b0fe82a8f3aed6e57ebb75588d1e625732f2fcd
- RC01基线：a6ca5956e466e7ae28641e92ca5ae76319df6fe8
- FIX1分支：fix/wpn-m2-backend-fix1
- FIX1 worktree：/tmp/wpn_m2_backend_fix1
- 本轮代码提交：73152ffe30866eab51cb8456ff5b7435192b5fea
- 报告与证据初始提交：d6fb78a0a613bd2e1aab337a5f7061879b1e03d4
- FIX1最终提交：以交付结束时的 git rev-parse HEAD 与 Git Bundle list-heads 为准
- 指定Node：/opt/node-v22.18.0-linux-x64/bin/node
- Python环境：/workdata/WaterPipNet/.venv-platform
- 测试数据：仓库登记的54节点、73边合成燃气 Fixture 和临时复制/变异的合成 Fixture。

本轮开始前在M2父提交上复现了报告中的异常。变异只发生在临时目录，未写入仓库 Fixture。

## 2. 原始问题与独立复现

输入：合法 gas54_network.json 的 nodes[0].name 改为数字 123，case 文件保持不变，服务端显式允许临时 Fixture 根目录。

M2父提交的真实HTTP结果：

- GET /api/v1/gas/cases/syn-gas-case-K-001：500，正文为 Internal Server Error。
- GET /api/v1/gas/cases：200，仍返回案例摘要。

原因是详情路由返回完整字典后才由 FastAPI response_model 做校验，Pydantic 错误在路由边界外变成500；列表只构造摘要，没有校验完整节点结构。因此同一个损坏 Fixture 同时出现“详情失败、列表宣称可用”。

证据：benchmark_output/fix1_structure_repro_pre.log。

## 3. 最小修复

修改文件：

- engineering/src/gas_application/adapter.py
- engineering/tests/test_m2_backend_hardening.py

Adapter 在 evaluate_fixture 构造完整结果后调用现有 GasCaseResponse.model_validate。只捕获 Pydantic ValidationError，转换为现有 FixtureUnavailable；路由继续将该受控异常映射为脱敏503。已有的 ValueError、TypeError、KeyError 和 FixtureUnavailable 处理保持不变，没有增加全局异常吞咽。

list_cases 继续复用 evaluate_fixture，因此列表和详情现在共用完整 DTO 校验。结构不完整时列表也返回503，不会静默返回摘要并暗示案例完整可用。未知 case_id 仍在读取 Fixture 前返回404，认证和ACL仍由统一入口处理401/403。

新增结构异常反例：

1. nodes[0].name 类型错误；
2. nodes[0].node_id 缺失；
3. nodes[0].layout.x 为对象；
4. edges[0] 为数组而非对象。

每种变异都同时请求列表和详情，要求503，且响应不得包含绝对路径、Fixture内容或堆栈。合法案例同时验证详情和列表为200，节点数54、边数73保持不变。

FIX1结果：

- 结构类型错误详情：503；
- 结构类型错误列表：503；
- 错误正文：燃气合成候选暂不可用；
- 绝对临时路径：未泄漏；
- traceback：未泄漏；
- 合法案例：详情200、列表200，54 nodes、73 edges。

证据：benchmark_output/fix1_structure_repro_post.log、benchmark_output/fix1_real_http.log。

## 4. 模块边界复核

### 4.1 指定Node的完整检查

RC01和FIX1均使用 /opt/node-v22.18.0-linux-x64/bin/node 运行 scripts/check_module_boundaries.py。两次都在TypeScript AST阶段停止：

Error: Cannot find module 'typescript'

因此完整的Python+TypeScript模块边界门禁当前为 BLOCKED_BY_ENV，不能把它写成PASS。Node二进制存在，但服务器没有该脚本需要的typescript运行包；本轮没有联网安装或修改依赖。

- RC01证据：benchmark_output/fix1_boundary_rc01.log
- FIX1父状态证据：benchmark_output/fix1_boundary_pre.log
- FIX1代码后重跑证据：benchmark_output/fix1_boundary_current.log

### 4.2 Python依赖部分与四条违规

在不写入基线的临时分析中，调用同一 check_module_boundaries.py 的 Python 依赖解析部分：

- RC01：PASS，467文件、841条Python依赖边、103条初始化边、无违规；
- FIX1：FAIL，477文件、869条Python依赖边、104条初始化边，4条违规。

四条违规是：

1. engineering/src/agents/graph.py:24 -> engineering/src/platform/store.py，from；
2. engineering/src/agents/graph.py:24 -> engineering/src/platform/__init__.py，package_init，via store.py；
3. engineering/src/agents/tools/domain.py:18 -> engineering/src/platform/store.py，from；
4. engineering/src/agents/tools/domain.py:18 -> engineering/src/platform/__init__.py，package_init，via store.py。

归因实验将FIX1工作树内 store.py 临时替换为RC01基线字节后，上述4条相关违规全部消失。没有修改 Agent 源码，也没有新增业务模块到 Agent 的调用。四条门禁结果完全由 store.py 的目标SHA变化触发：

- 依赖基线旧SHA：d9c08a23307570a674a6ee2e1ca3ae8ba6548563a4c7580d646ba44d810fded2；
- FIX1当前SHA：e9cac3b936bcfb1b3425eb9d1244f952d21f4ea858d03cd12a6b72d59bc98215；
- graph.py SHA未变：695d915fef5981a898a201647983994ae4f97942d396becf2bdf9496ed0448a7；
- tools/domain.py SHA未变：d837bbe365f0ed710bde0bf6a5ff522429ac17eba1e1c36da3401a4ff9cfc59d；
- platform/__init__.py SHA未变：8c38effd79842b28bf47038bba73cb3a1c81c3b414b1882b5f3f914dbc8f3d53。

store.py 的修改属于正确的存储层职责：在SQL查询中先按 actor_id 和 scope 过滤，再排序、offset、limit，防止跨主体记录占用分页额度。不能为了让边界检查变绿而把权限过滤错误迁移到API层。

本轮没有覆盖 docs/architecture/dependency_baseline.json。需要Owner审批的材料是：

- 批准将当前 store.py SHA登记为已有 Agent→Platform Store 受控例外的目标版本；
- 确认两条直接 Agent→store 边和由包初始化派生的两条 package_init 边继续属于受控共享依赖；
- 审批后再由独立共享合同变更更新 dependency_baseline.json，不能在本分支静默覆盖；
- 在隔离Node环境补装/提供typescript包后重新运行完整边界检查。

证据：benchmark_output/fix1_boundary_python_diagnostic.log。

## 5. 回归结果

以下均为本轮在FIX1代码上独立执行，未沿用开发方历史日志：

| 范围 | 实测结果 |
| --- | --- |
| 来源安全、结构异常、认证、ACL、Run/Feedback、Dataset | 28 passed，1 warning，16.21s |
| Report、身份、来源、平台及M2相关回归 | 327 passed，1 warning，556.89s |
| 燃气、认证、Dataset、OpenAPI、统一合同 | 44 passed，1 warning，41.34s |
| 独立燃气进程 | 10 passed，1.14s |
| OpenAPI导出比较 | 临时导出与提交OpenAPI SHA相同 |
| TypeScript生成合同 | Node 7 assertions passed，85 schemas |
| 真实TCP HTTP | 健康200；无Token401；合法详情200；结构损坏详情503；结构损坏列表503 |
| 冻结文件 | 47个文件均与RC01基线和冻结清单一致 |

Starlette测试客户端仅产生既有弃用警告，不影响通过结果。全量Agent/LangGraph测试不在本轮PASS范围；其运行环境仍需经批准的langgraph依赖。完整TS边界门禁因typescript包缺失记为BLOCKED_BY_ENV。

证据文件：

- benchmark_output/fix1_security_source_regression.log
- benchmark_output/fix1_related_regression.log
- benchmark_output/fix1_gas_openapi_regression.log
- benchmark_output/fix1_gas_isolated.log
- benchmark_output/fix1_contract_regression.log
- benchmark_output/fix1_real_http.log
- benchmark_output/fix1_frozen_sha.log

## 6. API合同与冻结资产

本轮没有改变燃气API路径、DTO字段、错误状态或OpenAPI。原因是修复发生在服务端返回响应前的内部完整性门禁：

- OpenAPI SHA-256：dc52322e26cf583f7c777d125121ece99e42f7e2256ffeed6a57322429154b38；
- 生成TypeScript SHA-256：456d20b4655ad10ae76644916fd29208e0c464010b498016bbca2db9944de7d6；
- OpenAPI临时导出与提交文件逐字节一致；
- Node生成器检查和7项合同断言通过。

错误合同保持：

- 未认证401；
- 已认证但无合成ACL 403；
- 未知case_id 404；
- 服务端Fixture损坏或响应结构不合法503；
- 客户端请求校验仍使用422。

冻结47文件SHA全部未变。未修改 dependency_baseline.json，也未修改 FROZEN_FILES.json。

## 7. 持久化备份

FIX1最终提交完成后，以Git Bundle形式备份到：

/workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/incoming/codex-wpn-m2-backend-fix1.bundle

已执行 git bundle verify、git bundle list-heads，并从Bundle独立clone后校验分支头与FIX1工作树HEAD一致。完整历史Bundle校验通过，证据保存在 /workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/incoming/codex-wpn-m2-backend-fix1.bundle.verify.log；Bundle文件的最终SHA在交付响应和持久化目录中核对。该Bundle不推送远程，不改共享worktree。

## 8. 提交身份、回滚与裁决

代码提交：73152ffe30866eab51cb8456ff5b7435192b5fea；报告/证据初始提交：d6fb78a0a613bd2e1aab337a5f7061879b1e03d4；最终分支对象以交付结束时 git rev-parse HEAD 和 Git Bundle list-heads 为准。

回滚方式：在FIX1分支执行 git revert 73152ff；不对正式工程执行reset、force或文件覆盖。

本轮代码正确性结论：

- Fixture结构异常边界：PASS；
- 合法54节点、73边案例：PASS；
- 认证、ACL、404及脱敏错误合同：PASS；
- OpenAPI/TS合同：PASS；
- 冻结47文件：PASS；
- 完整模块边界门禁：BLOCKED_BY_ENV；
- 四条边界违规的代码归因：已确认由store.py SHA变化触发；
- 依赖基线Owner审批：PENDING。

整体候选裁决：BLOCK。

BLOCK原因是完整模块边界检查尚未因服务器缺少typescript而完成，且 store.py 新SHA尚未获得 dependency_baseline.json 的Owner审批。代码修复本身可交Gemini Argon复审，但不能据此合并main或部署。

下一步仅需：

1. 在隔离环境提供与脚本匹配的typescript依赖并重跑RC01/FIX1完整检查；
2. 由Owner审阅并批准 store.py 受控依赖SHA更新；
3. 独立审核确认四条边界结果后，再决定是否更新共享基线。

本报告不授权真实工程数据、真实模型、公网API或设备控制。
