# GAS-INTEGRATION-PREP-01 测试矩阵

证据根目录：/workdata/WaterPipNet/01_工程项目/experiments/gas_adapter_dashboard_20261105/evidence/gas_integration_prep_01/

代码测试提交：442ce7414fd2e112506a026aacce811015a1533c；最终文档提交SHA以本分支 git rev-parse HEAD 为准。

| 项目 | 实际命令/环境 | 结果 | 证据 |
|---|---|---:|---|
| 燃气Adapter/OpenAPI | PYTHONPATH=engineering .venv-platform/bin/python -m pytest -q engineering/tests/test_gas_application.py engineering/tests/test_module_openapi.py | 11 passed | gas_integration_gas_tests.out |
| OpenAPI生成 | node web/scripts/generate-platform-types.mjs --check | 85 schemas，SHA 9a97933e... | gas_integration_contracts_check.out |
| 前端合同 | node web/scripts/test-platform-types.mjs | 7 assertions passed | gas_integration_contracts_test.out |
| TypeScript | tsc --noEmit --project web/tsconfig.json | RC=0 | gas_integration_typecheck_final.out |
| 生产构建 | npm --prefix web run build | RC=0 | gas_integration_build_final.out |
| Vitest | vitest run --maxWorkers=4 --minWorkers=1 | 189 passed，12 skipped | gas_integration_vitest_final.out |
| Chromium既有交互 | RUN_BROWSER=1 ... vitest run src/tests/interaction.browser.test.ts | 12 passed | gas_integration_browser_final.out |
| Chromium燃气页面 | 实际构建dist、FastAPI本地服务、静态代理、Playwright | 1 passed，仅GET燃气接口 | gas_integration_gas_browser_final.json |
| 模块边界 | python3 scripts/check_module_boundaries.py（隔离node_modules） | PASS，0 violations | gas_integration_boundaries_after.out |
| HTTP安全/异常 | FastAPI TestClient，临时SQLite | 200/401/403/404/503符合受测合同 | gas_integration_http_probe.json |
| Report冻结 | 5个Report/unified测试文件 | 324 passed，1 warning | gas_fix_report_regression.out |
| 平台回归 | 14个不依赖LangGraph运行时的既有测试文件 | 252 passed，5 failed，1 warning | gas_fix_platform_regression_no_langgraph.out |
| LangGraph集合 | 另含 test_m1fix2_run_context.py | 收集失败：缺langgraph | 初始日志，未计PASS |
| 全工程pytest | 未作为通过依据 | 未验证 | 报告说明 |

HTTP反例：
- WATER_API_TOKEN=tok,WATER_DEV_MODES=synthetic：列表/案例200，未知case404；
- 令牌配置下只填X-Water-Data-Scope：401；
- WATER_DEV_MODES=real：燃气合成接口403；
- 空或损坏WATER_GAS_FIXTURE_ROOT：列表和已知案例503，固定脱敏正文；
- 未配置Token时保留既有local-demo匿名合成开发例外，禁止用于真实或受控部署。