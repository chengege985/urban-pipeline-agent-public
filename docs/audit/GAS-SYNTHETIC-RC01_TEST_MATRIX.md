# GAS-SYNTHETIC-RC01 测试矩阵

| 编号 | 命令/方法 | 环境 | 实测结果 | 证据 |
|---|---|---|---|---|
| S01 | `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_gas_auth_default.py engineering/tests/test_gas_application.py engineering/tests/test_module_openapi.py` | 179联合worktree、.venv-platform | 23 passed, 1 warning | `evidence/gas_synthetic_rc01/backend_gas_security_openapi.log` |
| S02 | `pytest -q engineering/tests/test_report_identity.py test_report_integrity.py test_report_leaf_integrity.py test_unified_case_consistency.py test_unified_contracts.py` | 179联合worktree、临时SQLite/TestClient | 324 passed, 1 warning | `evidence/gas_synthetic_rc01/report_regression.log` |
| S03 | 5项原供水 Fixture 阻断测试，临时挂载已核验结果与metadata | 179联合worktree、临时文件/符号链接 | 5 passed, 1 warning；测试后清理 | `evidence/gas_synthetic_rc01/water_fixture_five.log` |
| S04 | `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-agent/bin/python -m pytest -q engineering/tests/test_review_boundaries.py` | 179联合worktree、.venv-agent、临时Fixture | 23 passed；无Fixture时21 passed/2 errors，错误为缺metadata | `evidence/gas_synthetic_rc01/langgraph_boundary_with_fixture.log`, `langgraph_boundary.log` |
| S05 | 47个冻结文件 SHA-256 复算 | 179联合worktree | 47/47 PASS | `evidence/gas_synthetic_rc01/frozen_sha_check.log` |
| S06 | `npm run contracts:test` | Windows临时同步前端、现有node_modules | 7 assertions passed | 本报告 |
| S07 | `npm run typecheck` | 同上 | PASS | 本报告 |
| S08 | `npm run contracts:check` | 同上，OpenAPI同步到临时合同目录 | PASS，SHA=`9a97933...`，schemas=85 | 本报告 |
| S09 | `npm run build` | 同上 | PASS，Vite生产构建 | 本报告 |
| S10 | `npm test -- --run` | 同上 | 17 files passed、1 skipped；203 passed、12 skipped | 本报告 |
| S11 | `npm run test:gas:browser` | 同上、Playwright/Chromium | UNVERIFIED：Chromium访问127.0.0.1被环境拒绝，`ERR_NETWORK_ACCESS_DENIED` | 本报告 |
| S12 | uvicorn真实HTTP矩阵 | 179，127.0.0.1临时端口、隔离根目录 | 启动 fail-closed；401/403/200/404/503符合合同 | `evidence/gas_synthetic_rc01/real_http_matrix.log` |

所有测试均未使用正式数据库、生产凭据、真实模型、真实燃气数据或公网端口。浏览器 mocked 记录不是 live HTTP 证据，已单独标记。