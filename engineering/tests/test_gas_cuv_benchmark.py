import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from src.gas_application import agent, cuv_benchmark
from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.model_provider import LocalGasModel
from src.gas_application.service import GasFixtureService
from src.gas_application.standalone import create_gas_app
from src.gas_application.weymouth import WeymouthInput, WeymouthSolver


PATH = "/api/v1/gas/engineering/cuv-replay/benchmark"
AGENT = "/api/v1/gas/engineering/agent/query"


def _forbidden(*args, **kwargs):
    pytest.fail("invalid benchmark context reached case data, the solver, or the model")


def test_reproduces_public_results_and_separately_checks_reverse_boundary_targets():
    report = cuv_benchmark.run_public_benchmark()
    assert report.scope == "public_reference_benchmark"
    assert report.progress.implementation_status == "candidate"
    assert report.progress.benchmark_status == "benchmark_passed"
    assert report.progress.field_validation_status == "pending_field_validation"
    cases = {row.case_id: row for row in report.cases}
    assert len(cases) == 8
    references = {row.case_id: (row.expected.origin, row.expected.value, row.unit) for row in report.cases}
    assert references == {
        "menon-ex13-flow": ("literature_result", 163.26, "MMSCFD"),
        "pipeflow-case03-flow": ("third_party_software", 163.18, "MMSCFD"),
        "pipeflow-case13-flow": ("third_party_software", 100.012, "MMSCFD"),
        "menon-course-ex11-outlet": ("literature_result", 946.24, "psia"),
        "case13-diameter-a": ("boundary_target", 814.7, "psia"),
        "case13-diameter-b": ("boundary_target", 814.7, "psia"),
        "case13-min-diameter-menon": ("literature_result", 13.30, "in"),
        "case13-min-diameter-pipeflow": ("third_party_software", 13.305, "in"),
    }
    assert cases["menon-ex13-flow"].solve.solution.flow_mmscfd == pytest.approx(163.255858, abs=1e-6)
    assert cases["pipeflow-case13-flow"].solve.solution.flow_mmscfd == pytest.approx(100.022345, abs=1e-6)
    assert cases["menon-course-ex11-outlet"].solve.solution.outlet_psia == pytest.approx(946.240821, abs=1e-6)
    assert cases["case13-min-diameter-menon"].solve.solution.internal_diameter_in == pytest.approx(13.303651, abs=1e-6)
    assert report.comparison.basis == "same_boundary_except_diameter"
    assert report.comparison.outlet_delta_psia == pytest.approx(1.64741991, abs=1e-7)
    assert report.comparison.flow_delta_mmscfd == 0
    assert len({entry.input_sha256 for entry in report.cases}) == 8
    assert all(entry.passed and entry.solve.supplied_inputs == entry.solve.required_inputs == 10 for entry in report.cases)
    assert all(entry.solve.solution.equation_residual_psi2 < 1e-3 for entry in report.cases)
    assert all(entry.solve.solution.standard_flow_balance_residual_mmscfd < 1e-8 for entry in report.cases)
    inverse = [entry for entry in report.cases if entry.input.boundary != "fixed_outlet_pressure"]
    assert len(inverse) == 5
    assert all(row.forward_check.passed and row.forward_check.absolute_error_mmscfd < 1e-7 for row in inverse)


def test_no_physical_result_for_incomplete_conflicting_or_infeasible_inputs():
    pressure_case = cuv_benchmark.run_public_benchmark().cases[0].input
    base = pressure_case.model_dump()
    invalid_cases = (
        ({"internal_diameter_in": None}, "missing_inputs"),
        ({"flow_mmscfd": 80}, "invalid_inputs"),
        ({"outlet_psia": 1300}, "outside_model_scope"),
        ({"boundary": "fixed_standard_flow", "outlet_psia": None, "flow_mmscfd": 4000}, "outside_model_scope"),
    )
    for changes, expected in invalid_cases:
        result = WeymouthSolver().solve(WeymouthInput(**(base | changes)))
        assert result.status == expected and result.solution is None
    for value in (float("nan"), 0, -1, True, "750"):
        with pytest.raises(ValidationError):
            WeymouthInput(**(base | {"outlet_psia": value}))
    with pytest.raises(ValidationError):
        WeymouthInput(**(base | {"elevation_ft": 100}))


def test_no_benchmark_promotion_on_iteration_failure_or_bad_reference_result():
    report = cuv_benchmark.run_public_benchmark(solver=WeymouthSolver(max_iterations=1))
    assert report.progress.benchmark_status == "failed" and report.comparison is None
    assert report.cases[0].solve.status == "non_converged"
    assert report.cases[0].solve.solution is None

    class OutOfToleranceSolver:
        def solve(self, inputs):
            run = WeymouthSolver().solve(inputs)
            return run.model_copy(update={"solution": run.solution.model_copy(update={"flow_mmscfd": 1.0})})

    drifted = cuv_benchmark.run_public_benchmark(solver=OutOfToleranceSolver())
    assert drifted.cases[0].solve.status == "converged" and not drifted.cases[0].passed
    assert drifted.progress.benchmark_status == "failed" and drifted.comparison is None
    assert drifted.progress.field_validation_status == "pending_field_validation"


def test_fixed_benchmark_endpoint_requires_existing_gas_read_acl(monkeypatch):
    monkeypatch.delenv("WATER_API_TOKEN", raising=False)
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    common = {"scope": "internal", "modes": ["synthetic"], "permissions": []}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "reader": {**common, "actor_id": "benchmark-reader", "datasets": [GAS_DATASET_ID]},
        "other": {**common, "actor_id": "other-reader", "datasets": []},
    }))
    monkeypatch.setattr(LocalGasModel, "from_environment", lambda: None)
    with TestClient(create_gas_app()) as client:
        headers = {"X-Water-Token": "reader"}
        assert client.get(PATH).status_code == 401
        assert client.get(PATH, headers={"X-Water-Token": "other"}).status_code == 403
        assert client.get(PATH + "?outlet_psia=1", headers=headers).status_code == 422
        response = client.get(PATH, headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        data = response.json()
        payload = {"question": "解释公开基准的误差", "benchmark_binding": data["binding"], "tool_hint": "cuv.benchmark.query"}
        result = client.post(AGENT, headers=headers, json=payload)
        assert result.status_code == 200
        assert result.json()["tools"][0]["benchmark_report"] == data
        assert result.json()["evidence_scope"]["scope"] == "public_reference_benchmark"
        stale = payload | {"benchmark_binding": data["binding"] | {"model_version": "old"}}
        assert client.post(AGENT, headers=headers, json=stale).status_code == 409


def test_benchmark_rejects_stale_bindings_and_case_joins_before_solver(monkeypatch):
    report = cuv_benchmark.run_public_benchmark()
    bench = agent.GasAgentService(GasFixtureService())
    monkeypatch.setattr(bench, "_context", _forbidden)
    monkeypatch.setattr(WeymouthSolver, "solve", _forbidden)
    monkeypatch.setattr(LocalGasModel, "from_environment", _forbidden)
    for request in (
        agent.GasAgentQueryRequest(question="解释公开 Weymouth 基准与 K-001", tool_hint="cuv.benchmark.query"),
        agent.GasAgentQueryRequest(question="解释当前管段的公开基准"),
        agent.GasAgentQueryRequest(question="解释试点历史 demo-v3 和公开基准"),
        agent.GasAgentQueryRequest(question="解释公开基准", asset_id="syn-gas-pipe-K-001"),
    ):
        result = bench.query(request)
        assert result.error_code == "benchmark_scope_conflict" and not result.tools and result.model_calls == 0
    stale = report.binding.model_copy(update={"suite_sha256": "0" * 64})
    with pytest.raises(HTTPException) as error:
        bench.query(agent.GasAgentQueryRequest(question="解释公开基准", benchmark_binding=stale))
    assert error.value.status_code == 409
