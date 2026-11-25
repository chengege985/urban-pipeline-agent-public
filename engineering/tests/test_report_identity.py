"""RPT-01：已有Run报告的身份绑定；仅合成数据和隔离SQLite。"""
from copy import deepcopy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.platform.app import create_app
from src.platform.schemas import SnapshotPayload
from src.unified.report import ReportNotReady, build_report_from_run

ROOT = Path(__file__).resolve().parents[1]
CONFLICTS = ["snapshot", "run", "evidence"]


@pytest.fixture()
def completed_run(tmp_path, monkeypatch):
    for name in ("WATER_API_TOKEN", "WATER_PRINCIPALS", "AGENT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WATER_API_TOKEN", "report-identity-synthetic-token")
    monkeypatch.setenv("WATER_DEV_MODES", "synthetic")
    app = create_app(tmp_path, bootstrap=False, start_worker=False)
    with TestClient(app, headers={"X-Water-Token": "report-identity-synthetic-token"}) as client:
        payload = json.loads((ROOT / "data/synthetic/m1a_unified.json").read_text(encoding="utf-8"))
        # Historical provenance is frozen BEFORE import and computation.
        row = next(x for x in payload["assessments"] if x["pipe_id"] == "syn-pipe-fault")
        row["evidence_refs"].append("snapshot:historical-assessment-source")
        # This fixture intentionally mutates an evidence reference to exercise
        # Report identity. Seed it directly in isolated test storage; the HTTP
        # import contract now accepts only a server-verified immutable fixture.
        sid = app.state.store.save_snapshot(
            SnapshotPayload.model_validate(payload), "test-report-seed"
        )["snapshot_id"]

        def execute(kind="simulation"):
            body = {"snapshot_id": sid, "as_of": payload["as_of"], "fault_edge_id": "syn-fault",
                    "isolation_plan_id": "syn-isolate", "horizon_hours": 8}
            if kind == "decision":
                body.update(plan_ids=["syn-plan-repair", "syn-plan-backup"], budget_fen=500000,
                            resource_capacities={"repair_team": 2, "water_truck": 2})
            if kind == "assessment":
                inputs = json.loads((ROOT / "data/synthetic/m1a_unified_inputs.json").read_text(encoding="utf-8"))
                body = {k: inputs[k] for k in ("as_of", "window_start", "window_end", "static_rows", "dynamic_rows")}
                body.update(name="RPT-01 synthetic", data_mode="synthetic", source_refs=["synthetic:RPT-01"])
            response = client.post(f"/api/v1/{kind}-runs", json=body,
                                   headers={"X-Water-Client": "unified-react", "Idempotency-Key": kind})
            assert response.status_code == 202, response.text
            assert app.state.runtime.run_one()
            run = app.state.store.get_task(response.json()["run_id"])
            assert run["status"] == "succeeded", run
            snapshot = app.state.store.get_snapshot(run["result"]["snapshot_id"])
            return run, snapshot

        yield client, app, execute


def conflicting_run(run, conflict):
    bad = deepcopy(run)
    if conflict == "snapshot":
        bad["result"]["snapshot_id"] = "snap-other-result"
    elif conflict == "run":
        bad["result"]["run_id"] = "run-other-result"
    else:
        bad["result"]["evidence_refs"] = ["snapshot:snap-other-result", "run:run-other-result"]
    return bad


@pytest.mark.parametrize("conflict", CONFLICTS)
def test_original_report_identity_counterexamples(completed_run, conflict):
    _, _, execute = completed_run
    run, snapshot = execute()
    bad = conflicting_run(run, conflict)
    original = deepcopy(bad)
    with pytest.raises(ReportNotReady):
        build_report_from_run(bad, snapshot)
    assert bad == original  # 拒绝，不改写已有结果“修复”冲突。


@pytest.mark.parametrize("conflict", CONFLICTS)
def test_conflicting_report_http_is_409_without_recomputation(completed_run, monkeypatch, conflict):
    client, app, execute = completed_run
    run, _ = execute()
    bad = conflicting_run(run, conflict)
    stored_lookup = app.state.store.get_task

    def lookup(rid):
        return deepcopy(bad) if rid == run["run_id"] else stored_lookup(rid)

    def forbidden(*args, **kwargs):
        pytest.fail("Report must not execute or recalculate a task")

    monkeypatch.setattr(app.state.store, "get_task", lookup)
    monkeypatch.setattr(app.state.runtime, "execute", forbidden)
    from src.platform import services
    for name in ("calculate_assessment", "simulate_snapshot", "compare_snapshot"):
        monkeypatch.setattr(services, name, forbidden)
    response = client.get(f"/api/v1/runs/{run['run_id']}/report")
    assert response.status_code == 409, response.text
    assert stored_lookup(run["run_id"])["result"] == run["result"]


@pytest.mark.parametrize("refs", [[], ["snapshot:other"], ["run:other"], ["snapshot:"], ["run:"]])
def test_evidence_anchor_cannot_be_missing_or_mixed(completed_run, refs):
    _, _, execute = completed_run
    run, snapshot = execute()
    # 除空集缺锚点外，均保留正确锚点再加错误引用，不能只测“有无正确项”。
    run["result"]["evidence_refs"] = ([f"snapshot:{snapshot['snapshot_id']}"] + refs) if refs else []
    with pytest.raises(ReportNotReady):
        build_report_from_run(run, snapshot)


@pytest.mark.parametrize("field", ["run_id", "snapshot_id"])
def test_result_identity_is_required(completed_run, field):
    _, _, execute = completed_run
    run, snapshot = execute()
    run["result"].pop(field)
    with pytest.raises(ReportNotReady):
        build_report_from_run(run, snapshot)


@pytest.mark.parametrize("kind", ["assessment", "simulation", "decision"])
def test_valid_reports_preserve_results_and_source_references(completed_run, monkeypatch, kind):
    client, app, execute = completed_run
    run, snapshot = execute(kind)
    assert f"run:{run['run_id']}" not in run["result"]["evidence_refs"]
    before_run, before_snapshot = deepcopy(run), deepcopy(snapshot)
    first = build_report_from_run(run, snapshot)
    assert first.recomputation == "none"
    assert first.dataset["snapshot_id"] == run["result"]["snapshot_id"]
    assert {f"snapshot:{snapshot['snapshot_id']}", f"run:{run['run_id']}"} <= set(first.evidence_ids)
    assert build_report_from_run(run, snapshot).model_dump() == first.model_dump()
    assert (run, snapshot) == (before_run, before_snapshot)
    if kind == "assessment":
        assert "snapshot_id" not in run["request"]  # 评分使用其输出快照。
        assert first.algorithms[0].version == "0.1.1"
    else:
        assert getattr(first, kind) == run["result"]
        assert first.pipe.rs == 1.86 and first.pipe.hs == 78.5

    def forbidden(*args, **kwargs):
        pytest.fail("Report read attempted business recomputation")
    from src.platform import services
    for name in ("calculate_assessment", "simulate_snapshot", "compare_snapshot"):
        monkeypatch.setattr(services, name, forbidden)
    a = client.get(f"/api/v1/runs/{run['run_id']}/report")
    b = client.get(f"/api/v1/runs/{run['run_id']}/report")
    assert a.status_code == b.status_code == 200
    assert a.json() == b.json() == first.model_dump(mode="json")
    run["result"]["evidence_refs"] += ["rule:approved-rule", "algorithm:0.1.1", "scenario:synthetic"]
    report = build_report_from_run(run, snapshot)
    assert "rule:approved-rule" in report.evidence_ids
    if kind != "assessment":
        assert "snapshot:historical-assessment-source" in report.pipe.evidence_refs


# RPT01-FIX2: Qoder RPT01-09/10/11 (function), 15/17/16 (persisted HTTP).
NESTED_IDENTITIES = [("simulation", "scenario"), ("decision", "scenario"), ("decision", "baseline")]


def _nested_conflict(run, field, defect="foreign_snapshot"):
    bad = deepcopy(run)
    if defect == "missing_snapshot":
        bad["result"][field].pop("snapshot_id")
    elif defect.startswith("block_"):
        bad["result"][field] = {
            "block_null": None, "block_list": [], "block_string": "snapshot", "block_bool": True,
        }[defect]
    else:
        bad["result"][field]["snapshot_id"] = {
            "foreign_snapshot": "snap-other-result", "null_snapshot": None,
            "bool_snapshot": True, "number_snapshot": 1, "list_snapshot": [],
            "empty_snapshot": "", "blank_snapshot": "   ",
        }[defect]
    return bad


def _persist_nested_clone(app, run):
    """Only the fixture's temporary SQLite is written, before exercising Report."""
    from src.platform.store import TaskRow
    clone_id = run["run_id"] + "-nested"
    with app.state.store.session.begin() as db:
        original = db.get(TaskRow, run["run_id"])
        values = {column.name: deepcopy(getattr(original, column.name)) for column in TaskRow.__table__.columns}
        values.update(id=clone_id, key=original.key + ":nested", result=deepcopy(run["result"]))
        values["result"]["run_id"] = clone_id
        values["result"]["artifact_ref"] = f"outputs/platform/{clone_id}/result.json"
        # The original execution result has no run anchor; preserve that valid contract.
        assert not any(ref.startswith("run:") for ref in values["result"]["evidence_refs"])
        db.add(TaskRow(**values))
    return app.state.store.get_task(clone_id)


def _report_read_guard(app, snapshot_id, monkeypatch):
    """Count recomputation, reject foreign snapshot reads and any write during Report."""
    from src.platform import services
    counts = {name: 0 for name in ("calculate_assessment", "simulate_snapshot", "compare_snapshot", "runtime.execute")}
    for name in counts:
        def forbidden(*args, _name=name, **kwargs):
            counts[_name] += 1
            pytest.fail("Report attempted recomputation: " + _name)
        target, attribute = (app.state.runtime, "execute") if name == "runtime.execute" else (services, name)
        monkeypatch.setattr(target, attribute, forbidden)
    lookup = app.state.store.get_snapshot
    reads = []
    def snapshot_lookup(sid):
        reads.append(sid)
        assert sid == snapshot_id, "Report tried to fetch another snapshot"
        return lookup(sid)
    monkeypatch.setattr(app.state.store, "get_snapshot", snapshot_lookup)
    def no_write(*args, **kwargs):
        pytest.fail("Report attempted persistent mutation")
    for method in ("finish", "enqueue", "save_snapshot", "write_artifact", "feedback"):
        monkeypatch.setattr(app.state.store, method, no_write)
    return counts, reads


@pytest.mark.parametrize("kind,field", NESTED_IDENTITIES)
def test_qoder_nested_function_counterexamples(completed_run, monkeypatch, record_property, kind, field):
    _, app, execute = completed_run
    run, snapshot = execute(kind)
    bad = _nested_conflict(run, field)
    before = deepcopy((bad, snapshot))
    counts, reads = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    try:
        with pytest.raises(ReportNotReady):
            build_report_from_run(bad, snapshot)
    finally:
        record_property("recomputation_counts", json.dumps(counts))
        assert not any(counts.values()) and reads == []
        assert (bad, snapshot) == before
        assert app.state.store.get_task(run["run_id"]) == run


@pytest.mark.parametrize("kind,field", NESTED_IDENTITIES)
def test_qoder_nested_persisted_http_counterexamples(completed_run, monkeypatch, record_property, kind, field):
    client, app, execute = completed_run
    run, snapshot = execute(kind)
    cloned = _persist_nested_clone(app, _nested_conflict(run, field))
    counts, reads = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    try:
        response = client.get(f"/api/v1/runs/{cloned['run_id']}/report")
        record_property("http_status", response.status_code)
        assert response.status_code == 409, response.text
        assert "snap-other-result" not in response.text
    finally:
        record_property("recomputation_counts", json.dumps(counts))
        assert not any(counts.values())
        assert reads and set(reads) == {snapshot["snapshot_id"]}
        assert app.state.store.get_task(cloned["run_id"]) == cloned
        assert app.state.store.get_task(run["run_id"]) == run


@pytest.mark.parametrize("kind,field", NESTED_IDENTITIES)
@pytest.mark.parametrize("defect", [
    "missing_snapshot", "null_snapshot", "bool_snapshot", "number_snapshot", "list_snapshot",
    "empty_snapshot", "blank_snapshot", "block_null", "block_list", "block_string", "block_bool",
])
def test_present_nested_identity_requires_valid_snapshot(completed_run, monkeypatch, kind, field, defect):
    client, app, execute = completed_run
    run, snapshot = execute(kind)
    bad = _nested_conflict(run, field, defect)
    cloned = _persist_nested_clone(app, bad)
    before = deepcopy((bad, snapshot))
    counts, _ = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    with pytest.raises(ReportNotReady):
        build_report_from_run(bad, snapshot)
    assert client.get(f"/api/v1/runs/{cloned['run_id']}/report").status_code == 409
    assert (bad, snapshot) == before and not any(counts.values())
    assert app.state.store.get_task(cloned["run_id"]) == cloned
    assert app.state.store.get_task(run["run_id"]) == run


@pytest.mark.parametrize("kind,field", NESTED_IDENTITIES)
def test_omitted_optional_nested_block_preserves_current_schema(completed_run, monkeypatch, kind, field):
    from src.unified.contracts import parse_run_result
    client, app, execute = completed_run
    run, snapshot = execute(kind)
    compatible = deepcopy(run)
    compatible["result"].pop(field)
    # Result DTOs allow omitted extension blocks; omission is not explicit null.
    assert parse_run_result(compatible) is not None
    clone = _persist_nested_clone(app, compatible)
    counts, _ = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    report = build_report_from_run(compatible, snapshot)
    assert field not in getattr(report, kind)
    response = client.get(f"/api/v1/runs/{clone['run_id']}/report")
    assert response.status_code == 200 and field not in response.json()[kind]
    assert not any(counts.values()) and app.state.store.get_task(clone["run_id"]) == clone


@pytest.mark.parametrize("kind", ["assessment", "simulation", "decision"])
def test_nested_valid_http_keeps_authoritative_identity(completed_run, monkeypatch, kind):
    client, app, execute = completed_run
    run, snapshot = execute(kind)
    counts, reads = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    response = client.get(f"/api/v1/runs/{run['run_id']}/report")
    assert response.status_code == 200
    report = response.json()
    assert report["recomputation"] == "none"
    assert report["run_id"] == run["run_id"]
    assert report["dataset"]["snapshot_id"] == snapshot["snapshot_id"]
    assert {"run:" + run["run_id"], "snapshot:" + snapshot["snapshot_id"]} <= set(report["evidence_ids"])
    if kind != "assessment":
        assert report[kind] == run["result"]
        assert report[kind]["scenario"]["snapshot_id"] == snapshot["snapshot_id"]
    if kind == "decision":
        assert report[kind]["baseline"]["snapshot_id"] == snapshot["snapshot_id"]
    assert not any(counts.values()) and set(reads) == {snapshot["snapshot_id"]}
    assert app.state.store.get_task(run["run_id"]) == run


@pytest.mark.parametrize("case,expected", [("anonymous", 401), ("other_actor", 403), ("queued", 409), ("unknown", 404)])
def test_nested_fix_preserves_report_access_states(completed_run, monkeypatch, case, expected):
    from src.platform.store import TaskRow
    client, app, execute = completed_run
    run, snapshot = execute()
    rid, headers = run["run_id"], {}
    if case == "anonymous":
        headers["X-Water-Token"] = ""
    elif case == "other_actor":
        monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({"bob-synthetic-token": {
            "actor_id": "bob", "modes": ["synthetic"], "datasets": [snapshot["snapshot_id"]], "permissions": [],
        }}))
        headers["X-Water-Token"] = "bob-synthetic-token"
    elif case == "queued":
        with app.state.store.session.begin() as db:
            row = db.get(TaskRow, rid)
            row.status, row.result, row.finished_at = "queued", None, None
        run = app.state.store.get_task(rid)
    else:
        rid = "run-does-not-exist"
    counts, _ = _report_read_guard(app, snapshot["snapshot_id"], monkeypatch)
    response = client.get(f"/api/v1/runs/{rid}/report", headers=headers)
    assert response.status_code == expected, response.text
    assert not any(counts.values()) and app.state.store.get_task(run["run_id"]) == run
