"""REPORT-INTEGRITY-CORE: semantic integrity regressions using isolated synthetic Runs.

The 21 Qoder mutations are tested independently at the pure projection and HTTP
boundaries. All persistence below prepares temporary fixtures before report reads.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import re

import pytest
from sqlalchemy import event

from test_report_identity import completed_run, _persist_nested_clone, _report_read_guard
from src.unified.report import ReportNotReady, build_report_from_run
from src.platform.schemas import SnapshotPayload


FOREIGN = "snap-886e4c32befa5c5b8ab96588"
ORIGINAL_PATHS = [
    ("baseline_impact", "snapshot_id"),
    ("baseline_impact", "scenario", "snapshot_id"),
    ("baseline_impact", "evidence_refs"),
]
for _i in range(3):
    ORIGINAL_PATHS += [
        ("plans", _i, "baseline", "snapshot_id"),
        ("plans", _i, "baseline_impact", "snapshot_id"),
        ("plans", _i, "baseline_impact", "scenario", "snapshot_id"),
        ("plans", _i, "impact", "snapshot_id"),
        ("plans", _i, "impact", "scenario", "snapshot_id"),
        ("plans", _i, "impact", "evidence_refs"),
    ]


def _path_name(path):
    return ".".join(map(str, path))


def _parent(value, path):
    for key in path[:-1]:
        value = value[key]
    return value


def _put(result, path, value):
    _parent(result, path)[path[-1]] = deepcopy(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@pytest.fixture()
def full_decision(completed_run):
    client, app, execute = completed_run
    initial, snapshot = execute("simulation")
    request = {**initial["request"],
               "plan_ids": ["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
               "budget_fen": 500000,
               "resource_capacities": {"repair_team": 2, "water_truck": 2}}
    response = client.post("/api/v1/decision-runs", json=request,
                           headers={"X-Water-Client": "unified-react", "Idempotency-Key": "integrity-three"})
    assert response.status_code == 202, response.text
    assert app.state.runtime.run_one()
    run = app.state.store.get_task(response.json()["run_id"])
    assert run["status"] == "succeeded", run
    assert len(run["result"]["plans"]) == 3
    return client, app, run, snapshot


@contextmanager
def _guard(app, snapshot, monkeypatch, record_property):
    counts, reads = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    writes = []

    def sql_watch(conn, cursor, statement, parameters, context, executemany):
        if re.match(r"\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", statement, re.I):
            writes.append(statement.split()[0].upper())
            pytest.fail("Report attempted a database write")

    event.listen(app.state.store.engine, "before_cursor_execute", sql_watch)
    try:
        yield counts, reads
    finally:
        event.remove(app.state.store.engine, "before_cursor_execute", sql_watch)
        record_property("report_recomputation_counts", json.dumps(counts))
        record_property("report_database_writes", len(writes))
        record_property("report_foreign_snapshot_reads", sum(s != snapshot["snapshot_id"] for s in reads))
        assert not any(counts.values()) and not writes
        assert all(s == snapshot["snapshot_id"] for s in reads)


def _reject_pair(fixture, bad, layer, monkeypatch, record_property):
    client, app, original, snapshot = fixture
    tested = _persist_nested_clone(app, bad) if layer == "http" else bad
    before = deepcopy((tested, snapshot))
    with _guard(app, snapshot, monkeypatch, record_property):
        if layer == "function":
            with pytest.raises(ReportNotReady):
                build_report_from_run(tested, snapshot)
            record_property("actual", "ReportNotReady")
        else:
            response = client.get(f"/api/v1/runs/{tested['run_id']}/report")
            record_property("actual", response.status_code)
            assert response.status_code == 409, response.text
            assert app.state.store.get_task(tested["run_id"]) == tested
        assert (tested, snapshot) == before
        assert app.state.store.get_task(original["run_id"]) == original


@pytest.mark.parametrize("layer", ["function", "http"])
@pytest.mark.parametrize("path", ORIGINAL_PATHS, ids=_path_name)
def test_original_21_deep_conflicts(full_decision, monkeypatch, record_property, layer, path):
    bad = deepcopy(full_decision[2])
    sid = full_decision[3]["snapshot_id"]
    value = ["snapshot:" + sid, "snapshot:" + FOREIGN] if path[-1] == "evidence_refs" else FOREIGN
    _put(bad["result"], path, value)
    record_property("original_path", _path_name(path))
    record_property("mutation", json.dumps(value))
    _reject_pair(full_decision, bad, layer, monkeypatch, record_property)


EXTRA_MUTATIONS = [
    (("data_mode",), "real"),
    (("plans", 0, "constraints", "status"), "infeasible"),
    (("baseline_impact", "source_node_ids"), ["syn-H"]),
    (("baseline_impact", "isolated_valve_ids"), []),
    (("plans", 0, "impact", "verified"), True),
    (("execution_receipts",), [{"verified": True, "evidence_id": "0" * 64}]),
    (("source_status",), "declared_real"),
    (("as_of",), "2026-10-30T08:00:00+08:00"),
    (("decision_algorithm_version",), "same-tool-different-version"),
    (("algorithm_version",), "0.2.0"),
    (("task_version",), "unknown"),
    (("baseline", "payload_sha256"), "0" * 64),
    (("baseline_key",), "1" * 64),
    (("baseline", "metric_definition_version"), "unknown"),
    (("baseline", "topology_engine_version"), "unknown"),
    (("baseline", "service_criterion_version"), "unknown"),
    (("baseline", "target_service_ids"), ["syn-hospital-001"]),
    (("scenario", "horizon_hours"), 7),
    (("scenario", "budget_fen"), 500001),
    (("baseline_impact", "data_mode"), "real"),
    (("baseline_impact", "algorithm_version"), "unknown"),
    (("baseline_impact", "scenario", "fault_edge_id"), "syn-hospital"),
    (("baseline_impact", "evidence_refs"), ["run:other-execution"]),
    (("plans", 0, "plan_id"), "syn-plan-not-in-request"),
    (("plans", 0, "action_ids"), ["syn-backup"]),
    (("plans", 0, "decision_algorithm_version"), "unknown"),
    (("plans", 0, "baseline_key"), "2" * 64),
    (("plans", 0, "baseline", "payload_sha256"), "3" * 64),
    (("plans", 0, "value_profile_key"), "4" * 64),
    (("plans", 0, "constraints", "plan_id"), "syn-plan-backup"),
    (("plans", 0, "constraints", "action_ids"), ["syn-backup"]),
    (("plans", 0, "constraints", "decision_algorithm_version"), "unknown"),
    (("plans", 0, "constraints", "evidence_refs"), ["synthetic:another-action-source"]),
    (("plans", 0, "evidence_refs"), ["synthetic:another-action-source"]),
    (("plans", 0, "impact", "algorithm_version"), "unknown"),
    (("plans", 0, "impact", "evidence_refs"), ["run:other-execution"]),
    (("plans", 0, "metric_units", "total_cost_fen"), "yuan"),
    (("value_profile", "version"), "other-version"),
    (("value_profile", "profile_id"), "syn-other-profile"),
    (("value_profile", "evidence_ref"), "synthetic:other-policy"),
]


@pytest.mark.parametrize("layer", ["function", "http"])
@pytest.mark.parametrize("path,value", EXTRA_MUTATIONS, ids=[_path_name(p) for p, _ in EXTRA_MUTATIONS])
def test_semantic_contract_single_field(full_decision, monkeypatch, record_property, layer, path, value):
    bad = deepcopy(full_decision[2])
    _put(bad["result"], path, value)
    record_property("mutation_path", _path_name(path))
    _reject_pair(full_decision, bad, layer, monkeypatch, record_property)


REQUIRED_PATHS = [
    ("baseline_impact", "snapshot_id"),
    ("baseline_impact", "evidence_refs"),
    ("baseline_impact", "scenario", "snapshot_id"),
    ("plans", 0, "baseline", "snapshot_id"),
    ("plans", 1, "baseline_impact", "snapshot_id"),
    ("plans", 2, "impact", "snapshot_id"),
    ("plans", 2, "impact", "evidence_refs"),
    ("plans", 2, "constraints", "plan_id"),
    ("plans", 0, "action_ids"),
    ("plans", 0, "decision_algorithm_version"),
]


@pytest.mark.parametrize("layer", ["function", "http"])
@pytest.mark.parametrize("defect", ["missing", "null", "bool"])
@pytest.mark.parametrize("path", REQUIRED_PATHS, ids=_path_name)
def test_required_semantic_fields_missing_or_mistyped(full_decision, monkeypatch, record_property, layer, defect, path):
    bad = deepcopy(full_decision[2])
    if defect == "missing":
        _parent(bad["result"], path).pop(path[-1])
    else:
        _put(bad["result"], path, None if defect == "null" else True)
    _reject_pair(full_decision, bad, layer, monkeypatch, record_property)


@pytest.mark.parametrize("layer", ["function", "http"])
@pytest.mark.parametrize("case", ["self_consistent_foreign", "self_consistent_baseline_hash",
                                  "self_consistent_profile_hash", "old_run_execution_anchor"])
def test_internal_agreement_does_not_replace_authority(full_decision, monkeypatch, record_property, layer, case):
    bad = deepcopy(full_decision[2])
    result = bad["result"]
    if case == "self_consistent_foreign":
        for path in ORIGINAL_PATHS:
            _put(result, path, ["snapshot:" + FOREIGN] if path[-1] == "evidence_refs" else FOREIGN)
        result["scenario"]["snapshot_id"] = FOREIGN
        result["baseline"]["snapshot_id"] = FOREIGN
    elif case == "self_consistent_baseline_hash":
        altered = deepcopy(result["baseline"])
        altered["payload_sha256"] = "f" * 64
        key = _digest(altered)
        result["baseline"], result["baseline_key"] = altered, key
        for row in result["plans"]:
            row["baseline"], row["baseline_key"] = deepcopy(altered), key
    elif case == "self_consistent_profile_hash":
        result["value_profile"]["version"] = "forged-but-internally-consistent"
        key = _digest(result["value_profile"])
        for row in result["plans"]:
            row["value_profile_key"] = key
    else:
        # A genuine other execution exists, but its evidence belongs to that Run.
        client, app, _, snapshot = full_decision
        response = client.post("/api/v1/decision-runs", json=bad["request"],
                               headers={"X-Water-Client": "unified-react", "Idempotency-Key": "other-execution"})
        assert response.status_code == 202
        assert app.state.runtime.run_one()
        other = app.state.store.get_task(response.json()["run_id"])
        assert other["run_id"] != bad["run_id"]
        result["plans"][0]["impact"]["evidence_refs"] = [
            "snapshot:" + snapshot["snapshot_id"], "run:" + other["run_id"]]
    _reject_pair(full_decision, bad, layer, monkeypatch, record_property)


def _execute_variant(full_decision, change):
    client, app, initial, snapshot = full_decision
    request = deepcopy(initial["request"])
    payload = deepcopy(snapshot["payload"])
    change(payload, request)
    if payload != snapshot["payload"]:
        # The HTTP synthetic import contract accepts only immutable server-registered
        # fixtures. These cases intentionally mutate a snapshot to exercise report
        # semantics, so seed the isolated Store directly rather than weakening the
        # provenance gate.
        request["snapshot_id"] = app.state.store.save_snapshot(
            SnapshotPayload.model_validate(payload), "test-report-variant"
        )["snapshot_id"]
        snapshot = app.state.store.get_snapshot(request["snapshot_id"])
    response = client.post("/api/v1/decision-runs", json=request,
                           headers={"X-Water-Client": "unified-react", "Idempotency-Key": "variant-run"})
    assert response.status_code == 202, response.text
    assert app.state.runtime.run_one()
    return client, app, app.state.store.get_task(response.json()["run_id"]), snapshot


@pytest.mark.parametrize("case", ["complete", "unknown_constraints", "partial_population"])
def test_legal_results_and_unknowns_are_preserved(full_decision, monkeypatch, record_property, case):
    if case == "complete":
        fixture = full_decision
    else:
        def change(payload, request):
            if case == "unknown_constraints":
                request["resource_capacities"].pop("water_truck")
            else:
                next(s for s in payload["services"] if s["service_id"] == "syn-households-C")["count_value"] = None
        fixture = _execute_variant(full_decision, change)
    client, app, run, snapshot = fixture
    if case == "unknown_constraints":
        assert run["status"] == "insufficient_data"
        row = next(r for r in run["result"]["plans"] if r["plan_id"] == "syn-plan-backup")
        assert row["status"] == "insufficient_data" and "impact" not in row
    elif case == "partial_population":
        assert run["result"]["baseline_impact"]["status"] == "partial"
        assert run["result"]["baseline_impact"]["potentially_disconnected_counts"]["household"] is None
    before = deepcopy((run, snapshot))
    with _guard(app, snapshot, monkeypatch, record_property):
        report = build_report_from_run(run, snapshot)
        response = client.get(f"/api/v1/runs/{run['run_id']}/report")
        assert response.status_code == 200, response.text
        assert report.decision == response.json()["decision"] == run["result"]
        assert report.recomputation == "none"
        assert (run, snapshot) == before
        assert app.state.store.get_task(run["run_id"]) == run


@pytest.mark.parametrize("layer", ["function", "http"])
def test_offline_effect_without_execution_provenance_is_not_authenticated(full_decision, monkeypatch, record_property, layer):
    bad = deepcopy(full_decision[2])
    row = bad["result"]["plans"][0]
    row["impact"] = {"status": "succeeded", "engine_type": "offline_scenario",
                     "affected_service_ids": [], "evidence_refs": ["synthetic:unrecorded-offline-model"],
                     "note": "provider declared result"}
    row["restoration_hours_semantics"] = "provider_declared_unverified"
    _reject_pair(full_decision, bad, layer, monkeypatch, record_property)


@pytest.mark.parametrize("layer", ["function", "http"])
def test_actual_offline_unknown_run_requires_report_contract(full_decision, monkeypatch, record_property, layer):
    def change(payload, request):
        payload["plans"][0]["offline_effect"] = {
            "as_of": payload["as_of"], "fault_edge_id": request["fault_edge_id"],
            "isolation_plan_id": request["isolation_plan_id"], "horizon_hours": request["horizon_hours"],
            "evidence_ref": "synthetic:declared-offline-effect-without-execution-sidecar",
            "remaining_disconnected_service_ids": [], "restoration_hours": 6,
            "backup_covered_critical_service_ids": [],
        }
    fixture = _execute_variant(full_decision, change)
    run = fixture[2]
    assert run["status"] == "insufficient_data"
    row = run["result"]["plans"][0]
    assert row["status"] == "insufficient_data" and "impact" not in row
    # The current engine omits required report-row fields for this diagnostic
    # result. Reject explicitly; do not fill in a trusted offline calculation.
    assert "label" not in row
    _reject_pair(fixture, deepcopy(run), layer, monkeypatch, record_property)


@pytest.mark.parametrize("layer", ["function", "http"])
def test_foreign_artifact_is_not_a_current_execution_locator(full_decision, monkeypatch, record_property, layer):
    from src.platform.store import TaskRow
    client, app, original, snapshot = full_decision
    bad = deepcopy(original)
    if layer == "http":
        bad = _persist_nested_clone(app, bad)
    bad["result"]["artifact_ref"] = "outputs/platform/run-other/result.json"
    if layer == "http":
        # Keep the conflict after the fixture clone assigns its own legal locator.
        with app.state.store.session.begin() as db:
            db.get(TaskRow, bad["run_id"]).result = deepcopy(bad["result"])
    before = deepcopy(bad)
    with _guard(app, snapshot, monkeypatch, record_property):
        if layer == "function":
            with pytest.raises(ReportNotReady):
                build_report_from_run(bad, snapshot)
        else:
            response = client.get(f"/api/v1/runs/{bad['run_id']}/report")
            assert response.status_code == 409
            assert app.state.store.get_task(bad["run_id"]) == before
        assert bad == before


@pytest.mark.parametrize("field", ["payload", "sha256", "data_mode"])
def test_snapshot_authority_cannot_be_changed_after_execution(full_decision, field):
    _, _, run, snapshot = full_decision
    bad = deepcopy(snapshot)
    if field == "payload":
        bad["payload"]["assessments"][0]["evidence_refs"].append("snapshot:other")
    elif field == "sha256":
        bad[field] = "a" * 64
    else:
        bad[field] = "real"
    with pytest.raises(ReportNotReady):
        build_report_from_run(run, bad)
