# CODEX-WPN-M2-BACKEND-01 后端整改与回归报告

## 1. 基线、范围与身份

本轮工作在179服务器的独立 worktree 完成，未修改正式工程主工作树、GitHub main、数据库、部署配置、真实燃气数据或设备接口。

- 正式候选基线：`a6ca5956e466e7ae28641e92ca5ae76319df6fe8`
- 基线分支：`integration/gas-synthetic-rc01`
- 修复分支：`fix/wpn-m2-backend-hardening`
- 修复 worktree：`/tmp/wpn_m2_backend_hardening`
- 实现提交：`bad71c23ff8fc40053cd49c6a9a7a8fdf96fc603`
- 评测方式：合成 Fixture、隔离临时 Store、127.0.0.1 真实 TCP HTTP、现有 `.venv-platform`；未使用正式数据库和真实模型。

此前 Provenance 候选为 `abd8febdcdb2d1edfb063c1fba5426ab7c455044`（实现提交 `0c03e17...`）。本分支没有把该候选的报告或测试日志提交历史直接合入，而是以 RC01 为代码基线复核并保留其来源验证语义，再在本分支增加 Fixture 根目录服务端 allowlist、越界/符号链接约束和回归测试。

实现提交相对基线的差异只包含平台来源验证、燃气 Fixture 读取边界、Dataset 状态、Run/Feedback 查询、OpenAPI/生成类型和相关测试证据。没有修改 `engineering/src/agents/`、`engineering/src/pipe_health/`、`engineering/src/decision/`、`engineering/src/simulation/`、`engineering/src/unified/report/` 或前端业务组件。

## 2. 确认的问题与修复

| ID | 严重度 | 触发条件 | 基线实际行为 | 修复后行为 | 状态 |
| --- | --- | --- | --- | --- | --- |
| M2-P1-01 | P1 | 客户端设置 `WATER_GAS_FIXTURE_ROOT` 或 `WATER_SYNTHETIC_FIXTURE_ROOT` 指向任意可读目录 | 只要目录和文件形状满足要求即可被当作服务端 Fixture；来源边界不足 | 默认只接受仓库登记根目录；自定义目录必须同时位于服务端 `*_FIXTURE_ALLOWED_ROOTS` allowlist，拒绝符号链接、越界路径和缺失/异常登记 | PASS |
| M2-P1-02 | P1 | 燃气 Fixture 缺失、损坏或 JSON 结构无效 | 部分故障可落为未脱敏 500/内部异常 | `FixtureUnavailable` 统一映射为脱敏 503；未知 `case_id` 保持 404；客户端参数校验仍为 422 | PASS |
| M2-P1-03 | P1 | Snapshot 缺少边、方案或措施，但节点/评分仍在 | `DatasetView.status` 可能错误为 `succeeded` | 只有评分覆盖、nodes、edges、services、plans、actions 均满足时才为 `succeeded`，否则沿用 `partial` | PASS |
| M2-P2-01 | P2 | 其他主体记录排在当前主体记录之前且使用 limit | 先全局 LIMIT，再做主体过滤，可能隐藏当前主体合法记录 | Store 查询先按 actor/scope 过滤，再排序、offset、limit；Feedback 增加兼容的 limit/offset 查询参数 | PASS |

### M2-P1-01：来源安全收尾

`engineering/src/platform/provenance.py` 在导入 synthetic Snapshot 前校验已登记 Fixture、manifest、内容 SHA、输入摘要和请求模板。M2 增加 `WATER_SYNTHETIC_FIXTURE_ALLOWED_ROOTS` 作为服务端配置的附加根目录；`WATER_SYNTHETIC_FIXTURE_ROOT` 不能独立扩大可读范围。燃气 `adapter.py` 同样对 `WATER_GAS_FIXTURE_ROOT` 使用默认根目录和显式 `WATER_GAS_FIXTURE_ALLOWED_ROOTS` 的联合约束。请求头、客户端 `data_mode`、`source_refs`、`fixture_id` 或文件名本身都不能单独建立可信身份。

基线最小探针已复现：

```text
BASELINE_GAS_CUSTOM_ROOT_ACCEPTED= True
BASELINE_DATASET_MISSING_STRUCTURES_STATUS= succeeded
BASELINE_GLOBAL_LIMIT_HIDES_OWNER= True
```

修复后相同类输入在无服务端 allowlist 时为受控 503；加入服务端明确 allowlist 且内容通过登记校验时才允许读取。该 allowlist 必须由部署方以只读配置维护，不能由请求方修改。

### M2-P1-02：Fixture 异常合同

燃气读取路径保留专用 `FixtureUnavailable`，缺失文件、损坏 JSON、非法对象关系和不可用目录均转换为脱敏 503。不存在的案例仍先按资源语义返回 404；请求字段不合法仍由 FastAPI/Pydantic 返回 422。没有使用“捕获全部异常后返回空数据”的方式隐藏程序错误。

### M2-P1-03：Dataset 状态

`build_dataset_view()` 继续使用已有 `partial` 语义，没有新增专业评分状态。合成数据缺拓扑边、服务方案或动作时，结果保留可用字段并标记 `partial`；不会把未知值填成零，也不会产生新的健康等级或燃气物理结论。

### M2-P2-01：权限过滤与分页

`Store.tasks()` 和 `Store.feedbacks()` 将主体与 scope 条件放入查询条件，再执行排序、offset 和 limit。`/api/v1/feedback` 的 `limit`/`offset` 为向后兼容的可选参数，默认仍返回最多100条；响应对象和既有字段不变。Report 读取 Feedback 时也传入当前主体，避免关联读取绕过 ACL。

## 3. 实际修改文件与 Diff

实现提交修改/新增：

- `engineering/src/platform/provenance.py`（新增，服务端 synthetic 来源验证）
- `engineering/src/platform/app.py`（导入/Assessment provenance、Run/Feedback 查询及 Report ACL）
- `engineering/src/platform/authz.py`、`engineering/src/platform/runtime.py`（与来源身份传递配套）
- `engineering/src/gas_application/adapter.py`（Fixture 根目录 allowlist 与异常边界）
- `engineering/src/platform/dataset_api.py`（完整数据结构决定 succeeded/partial）
- `engineering/src/platform/store.py`（SQL 级 actor/scope 过滤和分页）
- `engineering/04_实施方案/02_接口与数据/openapi/waterpipnet-platform.openapi.json`
- `web/src/generated/platformOpenapi.ts`
- 相关既有测试及 `engineering/tests/test_m2_backend_hardening.py`
- `benchmark_output/m2_pre_probe.log`、`m2_core_regression.log`、`m2_real_http.log`、`m2_contract.log`、`m2_boundary.log`

实现提交相对基线共20个路径发生变化（5份证据日志、其余15个后端、测试和合同文件）；未修改冻结 Agent、算法、Report 和前端业务视觉文件。

## 4. 反例、正常路径与真实 HTTP

### 4.1 来源与 Fixture

- 伪造可读外部 gas root、无服务器 allowlist：503，响应不含路径或异常栈。
- 伪造 synthetic root、无服务器 allowlist：503。
- 同一目录加入服务器显式 allowlist且 Fixture 登记和内容摘要一致：合法读取。
- 缺失/损坏 gas JSON：503。
- 未知 `case_id`：404。
- 未认证：401；有效 Token 但主体不具备 synthetic ACL：403；具备权限的 synthetic GET：200。

### 4.2 Dataset 与分页

- 缺少 `edges` 或 `plans/actions`：`status=partial`。
- 多主体记录交错时，当前主体的合法 Run/Feedback 不会被其他主体占用 limit。
- 合法主体和 scope 的排序、offset、limit 仍按原响应模型返回。

### 4.3 真实 TCP HTTP

在隔离临时 Store、127.0.0.1:8798、`uvicorn --loop asyncio --http h11` 下实际启动和停止服务，探针结果：

```text
HEALTH=200
NO_TOKEN_GAS=401
VALID_TOKEN_GAS=200
VALID_BODY data_mode=synthetic status=partial model_status=candidate nodes=54 edges=73 pipes=73
```

服务已停止，无残留监听进程。该验证没有使用 TestClient 冒充网络联调。

## 5. 测试矩阵

以下均为本轮在当前提交或当前代码内容上实际执行的结果，开发方历史结果未直接计入：

| 范围 | 实际命令/环境 | 结果 |
| --- | --- | --- |
| M2 来源、认证、Fixture | `tests/test_m2_backend_hardening.py tests/test_data_provenance.py tests/test_gas_auth_default.py`，179 `.venv-platform` | **23 passed, 1 warning** |
| 相关平台/Report/身份回归 | `test_report_integrity.py test_report_identity.py test_m2_backend_hardening.py test_data_provenance.py test_m1fix2_identity.py test_m1fix2_platform.py test_unified_contracts.py test_m1fix_contracts.py` | **322 passed, 1 warning，210.18s** |
| 燃气、Dataset、OpenAPI | `test_gas_application.py test_gas_auth_default.py test_m1a_dataset_timeline.py test_module_openapi.py test_unified_contracts.py` | **44 passed, 1 warning** |
| 燃气独立进程 | `tests/test_gas_application.py` | **10 passed** |
| OpenAPI静态合同 | `python -m src.platform.openapi_export` 后静态解析 | OpenAPI 3.1.0，28 paths，85 schemas，205 refs，缺失 refs=0 |
| 生成 TypeScript 合同 | Node 临时合同目录，`generate-platform-types.mjs --check`、`test-platform-types.mjs` | **通过；7 assertions；85 exports** |
| 真实 HTTP | uvicorn 127.0.0.1:8798，h11/asyncio | 200/401/200 探针通过，进程已停止 |
| 模块边界 | `PYTHONPATH=engineering .venv-platform/bin/python scripts/check_module_boundaries.py` | **BLOCKED_BY_ENV**：179没有 `node`，无法运行 `.mjs` 依赖解析；不能计为 PASS |

全量 pytest 没有计入通过：179当前缺少 `langgraph` 等依赖，历史全量收集曾有11项 collection error；本轮未安装系统级依赖，也未把这些环境阻断伪装成测试通过。真实 Agent/LangGraph 全量回归和依赖 Node 的模块边界检查需在具备批准依赖的隔离环境补跑。

一次混合进程运行出现 `test_gas_modules_do_not_import_water_scoring` 的导入顺序污染；同一测试在独立 gas 进程中10项全通过。该现象已记录为测试隔离限制，不改变代码或测试期望。

## 6. OpenAPI、前端和冻结资产

本轮 OpenAPI 变化为 `/api/v1/feedback` 增加可选 `limit`（1–500）和 `offset`（>=0）查询参数；响应 schema 和错误语义未改。静态 OpenAPI 与生成 TypeScript 已重新生成并通过漂移检查：

- OpenAPI SHA-256：`dc52322e26cf583f7c777d125121ece99e42f7e2256ffeed6a57322429154b38`
- `web/src/generated/platformOpenapi.ts` SHA-256：`456d20b4655ad10ae76644916fd29208e0c464010b498016bbca2db9944de7d6`

关键源码 SHA-256：

```text
engineering/src/gas_application/adapter.py  4e9e1ff05f5fdf955ee0a66210e98ffc17fba0d134c7a0982a0c5cf93e112e21
engineering/src/platform/app.py              8ffc20e7e17e10b74dc189be11951d0af2a64210a2e42f66da23f5f329ed4a7c
engineering/src/platform/authz.py            cbff63bf5632d6ea71e3ca3305b6a3ec2081a73e9d9aeef943d1abad06b84cb8
engineering/src/platform/dataset_api.py      c5bbf6e77e29921beb9de8fbf93a1628a26ee1994de1dc167cb5f8ca03a9a3db
engineering/src/platform/provenance.py       b4eb5d7f90383f8e2c2a2a26f65cdc2b7751489a874b026e0f024290debb7fff
engineering/src/platform/runtime.py          f421494408b0d7671f2390d1e7b438d511ed1beefb2bf5d2f0e169cc62224fd5
engineering/src/platform/store.py            e9cac3b936bcfb1b3425eb9d1244f952d21f4ea858d03cd12a6b72d59bc98215
engineering/tests/test_m2_backend_hardening.py 6b0dabe43f4c6bc82a3500e2dc0b77096e61a352c14eef026fb944647f131b06
engineering/tests/test_data_provenance.py    2f44d80418d3fd7a249b4ce988f9dff9cb563f33a1465e52c226c0c74591ac24
engineering/tests/test_gas_auth_default.py   484a50fba49fda6c5ef55cf824b2dc6b1b2bb87b946da45acc2492750ea2f326
```

实现提交未触碰冻结 Agent20、算法22、Report5 的路径；冻结范围 `git diff a6ca5956..bad71c2` 为空。当前候选仅支持合成只读业务，不接入真实燃气、真实模型或控制设备。

## 7. Owner 待审批事项与限制

1. `WATER_SYNTHETIC_FIXTURE_ALLOWED_ROOTS` 和 `WATER_GAS_FIXTURE_ALLOWED_ROOTS` 必须由部署方以只读、受控配置提供；不能允许用户或请求头写入。默认安全模式仍拒绝未经登记的路径。
2. 新增合成 Fixture 必须先登记 manifest、内容哈希、输入摘要、数据主体和 ACL；不能靠 `data_mode=synthetic` 或客户端 source_refs 自动取得信任。
3. 为保持来源门禁，原先依赖 HTTP 直接播种任意 synthetic Snapshot 的测试改为在隔离 Store 中播种；这不是放松服务端合同，而是把测试数据准备与外部导入路径分开。
4. 179缺少 Node 和 LangGraph 依赖，模块边界及完整 Agent 回归需在批准的隔离运行环境补跑。缺失依赖不影响本轮已通过的后端、合同和真实 HTTP 证据，但阻止宣称全量通过。
5. uvicorn 稳定启动已在本地回归命令中使用 `--loop asyncio --http h11`；本轮未把启动脚本改成公网或 `0.0.0.0` 监听。当前验证只允许127.0.0.1。

## 8. 提交、回滚和建议裁决

建议将本候选交 Gemini Argon 和 HY4 做独立复验，**不合并 main、不部署**。本轮候选的工程建议结论为：**PASS（仅表示本轮指定后端整改已完成并可交独立审核；不是生产发布许可）**。

- 实现提交：`bad71c23ff8fc40053cd49c6a9a7a8fdf96fc603`
- 回滚：在该分支执行 `git revert bad71c23ff8fc40053cd49c6a9a7a8fdf96fc603`，或删除该隔离 worktree；不对正式主工作树执行 reset/force 操作。
- 独立审核重点：服务端 allowlist 的部署权限、跨主体来源 ACL、Fixture 损坏 503、Dataset partial 语义、Run/Feedback 分页和冻结资产 SHA。

本报告不替代 Gemini/HY4 的独立结论，也不授权真实数据、真实模型、公网 API 或设备控制。
