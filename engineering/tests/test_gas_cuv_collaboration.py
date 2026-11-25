"""Operator confirmation preserves the synthetic-only replay and evidence."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
import stat
from threading import Barrier

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from src.gas_application.contracts import GAS_DATASET_ID
from src.gas_application.cuv_collaboration import (
    COLLABORATION_PERMISSION,
    GasCuvCollaborationRequest,
    GasCuvCollaborationStore,
)
from src.gas_application.service import GasFixtureService
from src.gas_application.standalone import create_gas_app
from src.platform.app import create_app


REPLAY = "/api/v1/gas/engineering/cuv-replay"
COLLABORATION = f"{REPLAY}/collaboration"
STAGED = "syn-gas-cuv-plan-staged-release"
HOLD = "syn-gas-cuv-plan-hold-isolation"
IMPACT = "min_affected_service_hours"
DURATION = "min_topology_restoration_hours"


def _headers(token):
    return {"X-Water-Token": token, "X-Water-Client": "unified-react"}


OPERATOR = _headers("collab-first")
PEER = _headers("collab-peer")
READER = _headers("collab-reader")


@pytest.fixture
def demo(monkeypatch):
    for var in ("WATER_API_TOKEN", "ALLOW_LOCAL_SYNTHETIC_ANONYMOUS",
                "WATER_ALLOW_LOCAL_SYNTHETIC_ANONYMOUS", "GAS_CUV_COLLABORATION_AUDIT_DB"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GAS_CUV_COLLABORATION_DEMO", "1")
    monkeypatch.setenv("GAS_AGENT_MODEL_MODE", "disabled")
    monkeypatch.setenv("WATER_RUNTIME_MODE", "controlled")
    monkeypatch.setenv("WATER_BIND_HOST", "127.0.0.1")

    common = {"scope": "internal", "modes": ["synthetic"], "datasets": [GAS_DATASET_ID]}
    operator = {**common, "permissions": [COLLABORATION_PERMISSION]}
    monkeypatch.setenv("WATER_PRINCIPALS", json.dumps({
        "collab-first": {**operator, "actor_id": "first-synthetic-operator"},
        "collab-peer": {**operator, "actor_id": "separate-synthetic-operator"},
        "collab-reader": {**common, "actor_id": "synthetic-reader", "permissions": []},
        "collab-other-scope": {**operator, "actor_id": "competition-operator", "scope": "competition"},
        "collab-other-dataset": {**operator, "actor_id": "other-dataset-operator", "datasets": []},
    }))


def _loopback(app):
    return TestClient(app, client=("127.0.0.1", 50123))


def _proposal(
    snapshot, objective=DURATION, *, peak=None, deadline=None, feedback="合成图目标确认",
):
    return {
        "expected_revision": snapshot["revision"],
        "input_sha256": snapshot["input_sha256"],
        "confirmed": True,
        "criteria": {
            "objective": objective,
            "max_peak_affected_services": peak,
            "restore_by_hours": deadline,
        },
        "feedback": feedback,
    }


@pytest.mark.parametrize("condition", ["disabled", "non_loopback", "staging", "production"])
def test_collaboration_requires_explicit_loopback_demo(demo, monkeypatch, condition):
    if condition == "disabled":
        monkeypatch.setenv("GAS_CUV_COLLABORATION_DEMO", "0")
    elif condition == "non_loopback":
        monkeypatch.setenv("WATER_BIND_HOST", "0.0.0.0")
    else:
        monkeypatch.setenv("WATER_RUNTIME_MODE", condition)
    with _loopback(create_gas_app()) as client:
        assert client.get(REPLAY, headers=READER).status_code == 200
        rejected = client.get(COLLABORATION, headers=OPERATOR)
        assert rejected.status_code == 404
        assert rejected.json()["detail"] == "gas_cuv_collaboration_disabled"


def test_only_direct_loopback_operator_can_read_or_confirm(demo):
    app = create_gas_app()
    with _loopback(app) as client:
        accepted = client.get(COLLABORATION, headers=OPERATOR)
        assert accepted.status_code == 200
        payload = _proposal(accepted.json())
        for headers, expected in (
            ({}, 401),
            (READER, 403),
            (_headers("collab-other-scope"), 403),
            (_headers("collab-other-dataset"), 403),
            ({**OPERATOR, "X-Forwarded-Host": "127.0.0.1"}, 403),
            ({**OPERATOR, "Forwarded": "for=127.0.0.1"}, 403),
        ):
            assert client.get(COLLABORATION, headers=headers).status_code == expected
            assert client.post(COLLABORATION, headers=headers, json=payload).status_code == expected
        assert client.get(COLLABORATION, headers=OPERATOR).json()["revision"] == 0
    with TestClient(app, client=("198.51.100.4", 50123)) as client:
        assert client.get(COLLABORATION, headers=OPERATOR).status_code == 403


def test_agent_binds_operator_comparison_to_current_input_and_revision(demo):
    agent = "/api/v1/gas/engineering/agent/query"
    with _loopback(create_gas_app()) as client:
        report = client.get(REPLAY, headers=READER).json()
        source = report["topology_evidence"]
        baseline = client.get(COLLABORATION, headers=OPERATOR).json()
        payload = {
            "question": "比较当前 CUV 的两个方案",
            "tool_hint": "cuv.query",
            "case_binding": {key: source[key] for key in ("network_content_sha256", "case_content_sha256")},
            "cuv_replay_sha256": source["replay_content_sha256"],
            "cuv_comparison_binding": {"expected_revision": 0, "input_sha256": baseline["input_sha256"]},
        }
        assert client.post(agent, json=payload).status_code == 401
        assert client.post(agent, headers=READER, json=payload).status_code == 403
        accepted = client.post(agent, headers=OPERATOR, json=payload)
        assert accepted.status_code == 200
        body = accepted.json()
        assert body["evidence_scope"]["scope"] == "gas_m0_graph_cuv"
        assert body["evidence_scope"]["source_sha256"] == payload["cuv_replay_sha256"]
        assert body["tools"][0]["cuv_comparison_binding"] == payload["cuv_comparison_binding"]

        for change in (
            {"case_binding": payload["case_binding"] | {"network_content_sha256": "0" * 64}},
            {"cuv_replay_sha256": "0" * 64},
            {"cuv_comparison_binding": payload["cuv_comparison_binding"] | {"input_sha256": "0" * 64}},
        ):
            assert client.post(agent, headers=OPERATOR, json=payload | change).status_code == 409
        confirmed = client.post(COLLABORATION, headers=OPERATOR, json=_proposal(baseline))
        assert confirmed.status_code == 200
        stale = client.post(agent, headers=OPERATOR, json=payload)
        assert stale.status_code == 409
        assert stale.json()["detail"] == "gas_cuv_collaboration_revision_conflict"
        payload["cuv_comparison_binding"]["expected_revision"] = 1
        current = client.post(agent, headers=OPERATOR, json=payload).json()["tools"][0]
        assert current["data"]["confirmed_comparison"]["revision"] == 1
        assert current["data"]["comparison_change"]["before"]["revision"] == 0
        unknown = client.post(agent, headers=OPERATOR, json=payload | {"question": "比较第 9 版的 CUV 方案"})
        assert unknown.status_code == 200 and unknown.json()["status"] == "unavailable"
        assert not unknown.json()["evidence_refs"] and unknown.json()["model_calls"] == 0
        engineering = payload | {"tool_hint": "cuv.engineering.query", "question": "当前 CUV 工程校核还缺哪些输入？"}
        engineering["cuv_comparison_binding"] = payload["cuv_comparison_binding"] | {"expected_revision": 0}
        assert client.post(agent, headers=OPERATOR, json=engineering).status_code == 409
        engineering["cuv_comparison_binding"] = payload["cuv_comparison_binding"]
        status = client.post(agent, headers=OPERATOR, json=engineering)
        assert status.status_code == 200
        tools = status.json()["tools"]
        assert tools[0]["cuv_comparison_binding"]["expected_revision"] == 1
        assert tools[0]["engineering_assessment"]["binding"]["replay_content_sha256"] == source["replay_content_sha256"]
        assert len(client.get(COLLABORATION, headers=OPERATOR).json()["audit"]) == 1


@pytest.mark.parametrize("platform", [False, True], ids=["standalone", "platform"])
def test_goal_and_constraints_recompute_with_feedback_and_immutable_evidence(demo, tmp_path, platform):
    app = create_app(root=tmp_path, start_worker=False, bootstrap=False) if platform else create_gas_app()
    with _loopback(app) as client:
        baseline = client.get(REPLAY, headers=READER).json()
        initial = client.get(COLLABORATION, headers=OPERATOR)
        assert initial.status_code == 200
        assert initial.headers["cache-control"] == "no-store"
        snapshot = initial.json()
        assert snapshot["audit"] == []
        assert snapshot["revision"] == 0
        assert snapshot["persistence"] == "process_memory"
        assert snapshot["comparison"]["leading_plan_ids"] == [STAGED]
        assert snapshot["topology_evidence"] == baseline["topology_evidence"]
        assert snapshot["unknown_metrics"] == baseline["unknown_metrics"]
        assert snapshot["missing_items"] == baseline["missing_items"]

        first = client.post(COLLABORATION, headers=OPERATOR, json=_proposal(snapshot, feedback="  优先恢复图可达  "))
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        current = first.json()
        assert current["comparison"]["leading_plan_ids"] == [HOLD]
        assert current["revision"] == 1
        now = datetime.now(UTC)
        assert datetime.fromisoformat(current["audit"][0]["occurred_at"]) <= now
        assert current["audit"][0]["actor_id"] == "first-synthetic-operator"
        assert current["audit"][0]["feedback"] == "优先恢复图可达"
        assert current["audit"][0]["previous_criteria"] == snapshot["criteria"]

        constrained = client.post(COLLABORATION, headers=OPERATOR, json=_proposal(current, IMPACT, deadline=4.5))
        assert constrained.status_code == 200
        second = constrained.json()
        assert second["revision"] == 2
        assert second["comparison"]["leading_plan_ids"] == [HOLD]
        for plan in second["comparison"]["plans"]:
            if plan["plan_id"] == STAGED:
                assert plan["status"] == "excluded"
                assert plan["reasons"] == ["recovery_deadline_missed"]
                assert plan["rank"] is None

        bounded = client.post(COLLABORATION, headers=OPERATOR, json=_proposal(second, peak=9))
        assert bounded.status_code == 200
        result = bounded.json()
        assert result["comparison"]["status"] == "no_eligible_plan"
        assert result["comparison"]["leading_plan_ids"] == []
        assert result["revision"] == 3
        assert [record["revision"] for record in result["audit"]] == [1, 2, 3]
        assert result["audit"][:2] == second["audit"]
        assert result["audit"][2]["comparison"] == result["comparison"]
        assert all(entry["status"] == "unknown" and entry["value"] is None for entry in result["unknown_metrics"])
        assert client.get(COLLABORATION, headers=PEER).json()["audit"] == []
        assert client.get(REPLAY, headers=READER).json() == baseline


@pytest.mark.parametrize("invalid", [
    {"confirmed": False}, {"confirmed": 1}, {"confirmed": "true"},
    {"expected_revision": True}, {"expected_revision": "0"}, {"expected_revision": -1},
    {"input_sha256": "not-a-sha"}, {"actor_id": "forged"}, {"feedback": " "},
    {"feedback": "带有\n控制字符"}, {"feedback": "字" * 281},
    {"criteria": {"objective": IMPACT, "max_peak_affected_services": 9.5, "restore_by_hours": None}},
    {"criteria": {"objective": IMPACT, "max_peak_affected_services": None, "restore_by_hours": -1}},
    {"criteria": {"objective": "actual_supply", "max_peak_affected_services": None, "restore_by_hours": None}},
])
def test_unconfirmed_or_non_graph_inputs_do_not_append_audit(demo, invalid):
    with _loopback(create_gas_app()) as client:
        baseline = client.get(COLLABORATION, headers=OPERATOR).json()
        response = client.post(COLLABORATION, headers=OPERATOR, json={**_proposal(baseline), **invalid})
        assert response.status_code == 422
        assert client.get(COLLABORATION, headers=OPERATOR).json() == baseline


def _private_path(tmp_path):
    directory = tmp_path / "ledger"
    directory.mkdir(mode=0o700)
    return directory / "collaboration.sqlite3"


def test_confirmed_audit_survives_app_restart_with_private_path(demo, monkeypatch, tmp_path):
    path = _private_path(tmp_path)
    monkeypatch.setenv("GAS_CUV_COLLABORATION_AUDIT_DB", str(path))
    with _loopback(create_gas_app()) as first:
        initial = first.get(COLLABORATION, headers=OPERATOR).json()
        assert initial["persistence"] == "sqlite_local"
        response = first.post(COLLABORATION, headers=OPERATOR, json=_proposal(initial))
        assert response.status_code == 200
        confirmed = response.json()
    with _loopback(create_gas_app()) as second:
        assert second.get(COLLABORATION, headers=OPERATOR).json() == confirmed
        assert second.get(COLLABORATION, headers=PEER).json()["revision"] == 0
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("invalid", ["shared_dir", "symbolic_link", "git_directory", "shared_file", "relative"])
def test_configured_audit_never_falls_back_when_path_is_unsafe(demo, monkeypatch, tmp_path, invalid):
    path = _private_path(tmp_path)
    if invalid == "shared_dir":
        path.parent.chmod(0o755)
    elif invalid == "shared_file":
        path.touch()
        path.chmod(0o644)
    elif invalid == "git_directory":
        (path.parent / ".git").mkdir()
    elif invalid == "symbolic_link":
        path.symlink_to(Path("external.sqlite3"))
    else:
        path = Path("not-an-absolute-audit-path.sqlite3")
    monkeypatch.setenv("GAS_CUV_COLLABORATION_AUDIT_DB", str(path))
    with pytest.raises(RuntimeError, match="CUV 审计数据库配置无效"):
        create_gas_app()


@pytest.mark.parametrize("durable", [False, True], ids=["memory", "sqlite"])
def test_same_revision_cannot_be_confirmed_twice(tmp_path, durable):
    report = GasFixtureService().cuv_replay()
    path = _private_path(tmp_path) if durable else None
    first = GasCuvCollaborationStore(path)
    second = GasCuvCollaborationStore(path) if durable else first
    baseline = first.read("same-actor", report).model_dump(mode="json")
    ready = Barrier(2)

    def submit(store):
        ready.wait(timeout=5)
        try:
            store.confirm("same-actor", report, GasCuvCollaborationRequest(**_proposal(baseline)))
            return 200
        except HTTPException as exc:
            assert exc.detail == "gas_cuv_collaboration_revision_conflict"
            return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as threads:
        results = list(threads.map(submit, (first, second)))
    assert sorted(results) == [200, 409]
    assert first.read("same-actor", report).revision == 1
    assert len(first.read("same-actor", report).audit) == 1


def test_changed_input_rejects_stale_digest_and_hides_old_projection():
    report = GasFixtureService().cuv_replay()
    store = GasCuvCollaborationStore()
    baseline = store.read("actor", report).model_dump(mode="json")
    request = GasCuvCollaborationRequest(**_proposal(baseline, feedback="操作者私有说明"))
    saved = store.confirm("actor", report, request)
    projection = store.confirmed_comparison("actor", report)
    assert projection is not None
    assert projection.comparison == saved.comparison
    assert set(projection.model_dump()) == {"revision", "input_sha256", "criteria", "comparison"}
    assert store.confirmed_comparison("another-actor", report) is None

    changed = report.model_copy(update={"comparison_basis": report.comparison_basis + " 变更测试。"})
    new_snapshot = store.read("actor", changed)
    assert new_snapshot.input_sha256 != saved.input_sha256
    assert new_snapshot.criteria.objective == IMPACT
    assert new_snapshot.audit == saved.audit
    assert store.confirmed_comparison("actor", changed) is None
    with pytest.raises(HTTPException) as error:
        store.confirm("actor", changed, request.model_copy(update={"expected_revision": 1}))
    assert error.value.status_code == 409
    assert error.value.detail == "gas_cuv_collaboration_input_conflict"
    assert store.read("actor", changed).audit == saved.audit


def test_rank_has_no_tiebreak_and_insufficient_data_stays_unranked():
    report = GasFixtureService().cuv_replay()
    staged, hold = report.plans
    hold_equal = hold.model_copy(update={"affected_service_hours": staged.affected_service_hours})
    tied = report.model_copy(update={"plans": [staged, hold_equal]})
    store = GasCuvCollaborationStore()
    compared = store.read("actor", tied)
    assert compared.comparison.leading_plan_ids == (STAGED, HOLD)
    assert [plan.rank for plan in compared.comparison.plans] == [1, 1]

    unknown = hold.model_copy(update={"topology_restoration_hours": None})
    blocked = staged.model_copy(update={"status": "blocked"})
    incomplete = report.model_copy(update={"plans": [blocked, unknown]})
    baseline = store.read("actor", incomplete).model_dump(mode="json")
    request = GasCuvCollaborationRequest(**_proposal(baseline, deadline=5))
    saved = store.confirm("actor", incomplete, request)
    assert saved.comparison.status == "no_eligible_plan"
    assert saved.comparison.leading_plan_ids == ()
    assert [plan.rank for plan in saved.comparison.plans] == [None, None]
    assert saved.comparison.plans[0].reasons == ("base_plan_blocked",)
    assert saved.comparison.plans[1].reasons == ("recovery_unknown", "objective_unknown")
