# CODEX-GAS-INTEGRATION-FIX1 安全与身份整改报告

- 日期：2026-11-06
- 基线：`be04c41aa377c80701d73649834a22f078026d45`
- 分支：`fix/gas-integration-auth-default`
- 工作树：`/workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/integration-gas-synthetic-candidate`
- 状态：整改候选，等待 HY4 独立复验；不宣布 PASS，main 仍 BLOCK。
- 代码修复提交：`498552a53b77580328c2ec216871ba2d0069b689`；报告与SHA清单随后固化在同一分支，最终交付 HEAD 以 `git rev-parse HEAD` 核对。

## 1. 基线与旧反例

本轮依据用户提供的 HY4 结论、上一轮交付报告和服务器证据复核。服务器中可读取的同名 Antigravity 报告属于燃气视觉分支，不作为 `be04c41` 的审计结论。使用的证据为 `docs/audit/GAS-INTEGRATION-PREP-01_REPORT.md`、`evidence/gas_integration_prep_01/gas_integration_http_probe.json` 和既有平台回归日志。

旧版本在无 `WATER_API_TOKEN`、无 `WATER_PRINCIPALS` 时自动创建匿名 `local-demo`，既有 HTTP 证据记录 no_token/unauth 的燃气案例和列表均为 200。该反例已在当前分支复验为默认 401。

## 2. 最小修复

修改统一入口 `engineering/src/platform/authz.py`：

- 默认无服务端认证、无显式本地例外时返回 401；
- 增加 `ALLOW_LOCAL_SYNTHETIC_ANONYMOUS`（兼容 `WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS`）；
- 匿名例外必须同时为 local/development 运行模式、显式 loopback 绑定、无 Token/ACL，且只授予 synthetic 读取；
- production/staging/controlled 缺少服务端认证时启动 RuntimeError；
- ACL JSON 运行中损坏时返回脱敏 503；
- `X-Water-Client`、`X-Water-Data-Scope` 不能构造身份或升级权限。

修改 `engineering/src/platform/app.py`：

- 启动统一调用 `validate_security_configuration()`；
- health 的 access_mode 区分 token、local_synthetic_anonymous、authentication_required；
- `__main__.py` 使用 `WATER_BIND_HOST`（默认 loopback）启动，匿名例外要求显式声明 loopback。
- 燃气路由继续复用统一认证依赖。

新增 `engineering/tests/test_gas_auth_default.py`，覆盖无凭据、无效 Token、ACL、loopback 例外、写入、real、404、503、损坏 ACL 和伪造请求头。更新旧认证测试，使匿名合成测试显式声明 local loopback 例外；无 Token 私有快照读取改为 401。

## 3. HTTP 安全矩阵

证据：`evidence/gas_integration_fix1/http_matrix_summary.json`，均来自临时 TestClient/SQLite。

| 场景 | 结果 |
|---|---:|
| 无 Token/主体/本地例外，燃气 GET | 401 |
| 无效 Token | 401 |
| ACL 无 synthetic 权限 | 403 |
| ACL 有 synthetic 权限 | 200 |
| 显式 local + loopback synthetic GET | 200 |
| local 匿名写入 | 401 |
| local 绑定 0.0.0.0 | 401 |
| local 读取 real 快照 | 403 |
| 不存在 case_id | 404 |
| Fixture 缺失/损坏 | 503，正文脱敏 |
| production 无认证 | 启动 RuntimeError |
| 伪造 X-Water-Client/Data-Scope | 401 |
| 运行中 ACL JSON 损坏 | 503，正文不含路径/traceback |

## 4. 本轮实测回归

| 范围 | 结果 |
|---|---|
| 燃气安全、Adapter、OpenAPI | 23 passed, 1 warning |
| 认证/身份/平台专项 | 30 passed, 1 failed, 1 warning |
| Report/统一合同 | 324 passed, 1 warning |
| 能力与 M1-A 数据/时间线 | 42 passed, 1 warning |
| 共享合同、工具、决策、评分、恢复、时间线 | 157 passed, 2 failed, 1 warning |
| platform/workbench | 35 passed, 2 failed, 1 warning |
| `test_review_boundaries.py` | UNVERIFIED：当前 venv 缺 `langgraph`，收集阶段停止 |
| 冻结 Agent/算法/Report SHA | 全部一致 |

日志和命令见 `evidence/gas_integration_fix1/`。

## 5. 五项供水 Fixture 阻断

本轮没有复制生产资料、伪造样本或删测试。以下失败与本次认证整改无关，baseline/candidate 已有相同表现：

1. `test_platform.py::test_sample_500_preserved`：缺少 `engineering/outputs/pipe_health/pipe_health_20261028_150251_2c9bbd/V3.0健康评价结果.xlsx`；
2. `test_m1fix_authz.py::test_fixture_replay_is_denied_by_default_and_can_be_enabled`：缺少 `engineering/experiments/pipe_health/pipe_health_20261028_150251_2c9bbd/metadata.json`；
3. `test_m1fix_contracts.py::test_partial_assessment_coverage_lists_missing_pipes`：同一 `metadata.json`；
4. `test_m1fix_contracts.py::test_incompatible_score_version_is_not_relabelled`：同一 `metadata.json`；
5. `test_workbench.py::test_four_pages_and_decision_submission`：bootstrap 依赖上述 xlsx。

这些测试继续标记 BLOCKED_BY_FIXTURE，不能计入 PASS。燃气合成、安全、OpenAPI 和 Report 回归不依赖这些文件，已独立通过。关闭它们只能在受控环境取得原有授权水样本并核验 SHA。

## 6. 冻结与边界

`docs/architecture/FROZEN_FILES.json` 复算结果：Agent20、algorithm22、Report5 全部 SHA 一致。未修改 Agent、供水算法、拓扑、时间线、Report、GAS-M0 Fixture、正式数据库、部署配置或 GitHub main；正式工程树未覆盖。

## 7. 交付身份 SHA

当前集成仓 `web/src/features/gas/types.ts`：`a19db84fc27fd2bb355ef21ba87121e779e96cd4eebac6dca7c3b2db2fdd2110`。

此前 `repair-gas-audit@0918605104c83728fc843e4f47f250deac667432` 的同路径 SHA：`4a0f610bd1c8a692984d7456923fd59c56d904dd6df934ef4527a381294aa3b5`。两者内容不同：集成仓 re-export 到 `../../shared/contracts/platform`，修复仓 re-export 到 `../../generated/platformOpenAPI`。0918605 不在当前集成仓对象历史中；本轮不 cherry-pick、rebase 或改源码迎合旧哈希。最终清单 `CODEX-GAS-INTEGRATION-FIX1_CHANGED_FILES.sha256` 只记录当前集成 HEAD 的文件身份。

## 8. 限制、回滚与 HY4 复验

仍未连接真实燃气数据、真实模型、设备或公网。local anonymous 只适合 loopback 离线演示；正式/受控部署必须配置服务端认证。缺少 langgraph 和五项原水样本的限制仍待独立环境补齐。

回滚仅限本分支：
```bash
git reset --hard be04c41aa377c80701d73649834a22f078026d45
git clean -fd evidence/gas_integration_fix1
```

HY4 可复验：
```bash
cd /workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/integration-gas-synthetic-candidate
PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_gas_auth_default.py engineering/tests/test_gas_application.py engineering/tests/test_module_openapi.py
PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python /tmp/gas_fix1_http_matrix.py
git diff --check
```

本交付只完成安全整改候选，不合并 main、不部署、不接入真实数据。



## 9. 2026-11-07 补充复验

本轮补齐了隔离 Agent 测试环境：使用已有 `.venv-agent`（含 `langgraph==1.2.18`）执行边界测试，不向平台虚拟环境或生产环境安装依赖。

供水 Fixture 来源已按 `evidence/gas_integration_fix1/fixture_provenance_20261107.md` 核对。服务器正式资产中的 `metadata.json` 声明结果 SHA 为 `2131ec61873876d3e1395e2d4819bc0b09ec63336a0eb39daabe2a2d3485385d`，Windows 现有结果文件和本轮临时传输文件均为同一 SHA。临时文件只用于隔离测试，已删除，没有进入 Git 或正式工程树。

补充实测结果：

- 原 5 项供水阻断反例：`5 passed, 1 warning`；
- 认证、身份、平台、合同、供水 platform/workbench：`81 passed, 1 warning`；
- `test_review_boundaries.py`：`23 passed`（使用 `.venv-agent` 和已核对 Fixture）；
- HY4 安全专项：`23 passed, 1 warning`；HTTP 401/403/404/503 矩阵无变化；
- 工作台测试改为显式服务端测试 Token/ACL，不恢复匿名 `fixture_replay` 访问，不改变生产安全策略。

因此，原先的五项 `BLOCKED_BY_FIXTURE` 已在受控、来源可核验的临时 Fixture 环境中关闭。候选分支本身不携带该原始 Excel，正式受控环境必须按上述来源和 SHA 挂载授权 Fixture；不能把它当作公开数据随源码发布。

## 10. 发布分级结论

- 隔离合成测试环境：可以继续发布/联调，但必须使用显式 Token/ACL 或 loopback 本地例外，并记录 Fixture SHA；
- 受控内网合成环境：可以准备发布候选，前提是部署前挂载同源 Fixture、配置服务端认证并复跑矩阵；
- 正式工程真实数据环境：暂不允许；当前没有真实燃气数据、真实模型或设备控制授权；
- 公网发布：不允许；
- 合并 main：仍需独立发布审查和合并门禁，不在本轮执行。
