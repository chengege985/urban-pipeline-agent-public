# CODEX-GAS-INTEGRATION-FIX1 independent commands

- `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_gas_auth_default.py engineering/tests/test_gas_application.py engineering/tests/test_module_openapi.py`
- `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_m1fix_authz.py engineering/tests/test_m1fix2_identity.py engineering/tests/test_m1fix2_platform.py`
- `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_report_identity.py engineering/tests/test_report_integrity.py engineering/tests/test_report_leaf_integrity.py engineering/tests/test_unified_case_consistency.py engineering/tests/test_unified_contracts.py`
- `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_capabilities.py engineering/tests/test_m1a_dataset_timeline.py`
- `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_m1fix_contracts.py engineering/tests/test_module_tool_contracts.py engineering/tests/test_decision_contracts.py engineering/tests/test_recovery_contract.py engineering/tests/test_scoring_contracts.py engineering/tests/test_service_timeline.py`
- `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python -m pytest -q engineering/tests/test_platform.py engineering/tests/test_workbench.py`
- HTTP matrix: `PYTHONPATH=engineering /workdata/WaterPipNet/.venv-platform/bin/python /tmp/gas_fix1_http_matrix.py`

All commands were run in this integration worktree with temporary TestClient/SQLite resources. No formal database, model endpoint, production credential, or deployment was used.
