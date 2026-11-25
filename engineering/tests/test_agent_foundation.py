"""V2基础层长期回归：基线恢复、输入批次隔离、确定性Tool及真实500条复算。

不加载PPU模型、不使用公网服务，测试通过不代表LangGraph或真实模型已经验收。
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError
import pytest

from src.agents.tools.domain import DomainTools, ToolAccess
from src.platform.baseline_guard import capture, sqlite_backup, verify
from src.platform.errors import InputIssues
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.schemas import DecisionRequest, RecomputeRequest, ScenarioRequest, SnapshotPayload
from src.platform.services import calculate_assessment
from src.platform.store import Conflict

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def batch():
    static = {"管线编号": "syn-001", "材质": "PE", "投运年份": 2005, "管径": 300,
              "起点埋深": 1, "终点埋深": 1, "压力": "低压"}
    dynamic = {"管线编号": "syn-001", "动态压力": .3, "余氯": .4, "浊度": .2,
               "瞬时流量": 1, "风险点数量": 0}
    return RecomputeRequest(as_of="2026-01-02T00:00:00+08:00", window_start="2026-01-01T00:00:00+08:00",
                            window_end="2026-01-02T00:00:00+08:00", static_rows=[static],
                            dynamic_rows=[{**dynamic, "时间": f"2026-01-01T0{i}:00:00+08:00"} for i in range(2)],
                            data_mode="synthetic", source_refs=["synthetic:基础层回归"])


@pytest.fixture
def principal():
    return BatchPrincipal(actor_id="test-user", scope="internal")


@pytest.fixture
def snap():
    data = SnapshotPayload.model_validate_json((ROOT / "data/synthetic/s01.json").read_text())
    return {"snapshot_id": "snap-test", "payload": data.model_dump(mode="json"), "as_of": data.as_of.isoformat(),
            "data_mode": "synthetic", "source_status": "constructed", "sha256": "fixture"}


def test_sqlite_backup_includes_committed_wal(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "snapshot.sqlite3"
    with sqlite3.connect(source) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE sample (value INTEGER)")
        db.execute("INSERT INTO sample VALUES (7)")
        db.commit()
        result = sqlite_backup(source, target)
        db.execute("INSERT INTO sample VALUES (8)")
        db.commit()
    with sqlite3.connect(target) as check:
        assert check.execute("SELECT value FROM sample").fetchall() == [(7,)]
    assert result["table_rows"] == {"sample": 1}
    assert result["integrity_check"] == "ok"
    with pytest.raises(FileExistsError):
        sqlite_backup(source, target)


def test_baseline_is_verifiable_and_never_overwritten(tmp_path):
    directory = tmp_path / "src"
    directory.mkdir()
    code = directory / "fixture.py"
    code.write_text("value = 1\n")
    first = capture(tmp_path)
    second = capture(tmp_path)
    assert first != second
    assert verify(first, tmp_path)["backup_intact"]
    code.write_text("value = 2\n")
    assert verify(first, tmp_path)["changed_sources"] == ["src/fixture.py"]
    assert (first / "files/src/fixture.py").read_text() == "value = 1\n"


def test_input_batch_idempotency_and_ownership(tmp_path, batch, principal):
    repo = InputBatchRepository(tmp_path)
    first = repo.register(batch, principal)
    assert repo.register(batch, principal) == first
    assert repo.load(first.batch_ref, principal) == batch
    assert first.static_rows == 1 and first.dynamic_rows == 2
    with pytest.raises(KeyError):
        repo.load(first.batch_ref, BatchPrincipal(actor_id="another", scope="internal"))
    with pytest.raises(KeyError):
        repo.load(first.batch_ref, BatchPrincipal(actor_id="test-user", scope="competition"))
    with pytest.raises(ValueError):
        repo.load("../../private", principal)


def test_input_batch_detects_tamper(tmp_path, batch, principal):
    repo = InputBatchRepository(tmp_path)
    ref = repo.register(batch, principal)
    path = repo._path(principal, ref.batch_ref)
    value = json.loads(path.read_text())
    value["payload"]["request"]["static_rows"][0]["管径"] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(Conflict):
        repo.load(ref.batch_ref, principal)


def test_input_batch_rejects_unknown_and_competition_real(tmp_path, batch, principal):
    repo = InputBatchRepository(tmp_path)
    changed = batch.model_copy(deep=True)
    changed.static_rows[0]["管径"] = None
    with pytest.raises(InputIssues):
        repo.register(changed, principal)
    real = batch.model_copy(update={"data_mode": "real"})
    with pytest.raises(PermissionError):
        repo.register(real, BatchPrincipal(actor_id="visitor", scope="competition"))


def test_tool_calls_shared_scoring_from_full_batch(tmp_path, batch, principal):
    repo = InputBatchRepository(tmp_path)
    ref = repo.register(batch, principal)
    access = ToolAccess(principal=principal, batch_refs={ref.batch_ref},
                        allowed_tools={"calculate_health", "validate_pipe_data"})
    tools = DomainTools(access, lambda _: None, repo)
    assert tools.execute("validate_pipe_data", {"batch_ref": ref.batch_ref})["result"]["valid"]
    actual = tools.execute("calculate_health", {"batch_ref": ref.batch_ref})
    expected = calculate_assessment(batch)
    assert actual["result"]["assessments"] == [a.model_dump(mode="json") for a in expected.assessments]
    assert actual["execution_status"] == "not_dispatched"
    assert not actual["result"]["persisted"]
    explanation = actual["result"]["explanations"]["syn-001"]
    assert explanation["status"] == "succeeded"
    assert explanation["reconciliation"]["Hs_raw"] == pytest.approx(expected.assessments[0].legacy_result.Hs)
    assert "explanations" not in expected.model_dump()
    assert actual == tools.execute("calculate_health", {"batch_ref": ref.batch_ref})
    with pytest.raises(ValidationError):
        tools.execute("calculate_health", {"batch_ref": ref.batch_ref, "formula": "return 100"})
    with pytest.raises(PermissionError):
        tools.execute("calculate_health", {"batch_ref": "batch-" + "0" * 64})


def test_tool_default_deny_and_missing_context(tmp_path, principal):
    repo = InputBatchRepository(tmp_path)
    deny = DomainTools(ToolAccess(principal=principal), lambda _: None, repo)
    assert deny.specifications() == []
    with pytest.raises(PermissionError):
        deny.execute("query_assessment", {"pipe_id": "syn-001"})
    tools = DomainTools(ToolAccess(principal=principal, allowed_tools={"simulate_outage"}), lambda _: None, repo)
    assert tools.execute("simulate_outage", {})["status"] == "insufficient_data"
    with pytest.raises(PermissionError):
        tools.execute("run_shell", {"command": "anything"})


def test_tools_preserve_simulation_and_decision(tmp_path, snap, principal):
    scenario = ScenarioRequest(snapshot_id=snap["snapshot_id"], as_of=snap["as_of"],
                               fault_edge_id="syn-fault", isolation_plan_id="syn-isolate")
    decision = DecisionRequest(**scenario.model_dump(), plan_ids=["syn-plan-repair", "syn-plan-backup", "syn-plan-combined"],
                               budget_fen=400000, resource_capacities={"repair_team": 1, "water_truck": 1})
    access = ToolAccess(principal=principal, snapshot_id=snap["snapshot_id"], scenario=scenario,
                        decision=decision, allowed_tools={"simulate_outage", "compare_action_plans"})
    tools = DomainTools(access, lambda _: snap, InputBatchRepository(tmp_path))
    before = deepcopy(snap)
    assert tools.execute("simulate_outage", {})["result"]["affected_service_ids"] == [
        "syn-hospital-001", "syn-households-C", "syn-households-U"]
    assert tools.execute("compare_action_plans", {})["result"]["recommended_plan_id"] == "syn-plan-combined"
    assert snap == before
    with pytest.raises(ValidationError):
        tools.execute("compare_action_plans", {"budget_fen": 99999999})


def test_snapshot_scope_and_bound_id(tmp_path, principal):
    from src.adapters.scoring import provided_sample
    data = provided_sample()
    snap = {"snapshot_id": "snap-internal", "payload": data.model_dump(mode="json")}
    visitor = BatchPrincipal(actor_id="visitor", scope="competition")
    tools = DomainTools(ToolAccess(principal=visitor, snapshot_id=snap["snapshot_id"], allowed_tools={"query_assessment"}),
                        lambda _: snap, InputBatchRepository(tmp_path))
    with pytest.raises(PermissionError):
        tools.execute("query_assessment", {"pipe_id": data.assessments[0].pipe_id})
    mismatch = DomainTools(ToolAccess(principal=principal, snapshot_id="another", allowed_tools={"query_assessment"}),
                           lambda _: snap, InputBatchRepository(tmp_path))
    with pytest.raises(PermissionError):
        mismatch.execute("query_assessment", {"pipe_id": data.assessments[0].pipe_id})


def test_agent_store_pauses_running_and_rejects_stale_approval(tmp_path, principal):
    from src.platform.agent_store import AgentStore
    store = AgentStore(f"sqlite:///{tmp_path / 'agent.sqlite3'}")
    thread = store.create_thread(principal)
    run = store.enqueue(principal, {"question": "测试"}, "k", thread_id=thread["thread_id"])
    assert store.claim()["run_id"] == run["run_id"]
    assert store.pause_running() == 1
    assert store.get_run(run["run_id"], principal)["status"] == "paused"
    # A paused checkpoint cannot be overwritten by a new chat in the same thread.
    with pytest.raises(Conflict):
        store.enqueue(principal, {"question": "审批"}, "blocked", thread_id=thread["thread_id"])
    second = store.enqueue(principal, {"question": "审批"}, "k2")
    store.claim()
    interrupt = {"type": "approval", "interrupt_id": "int-test", "revision": 0}
    store.finish(second["run_id"], "awaiting_approval", {"status": "partial"}, interrupt=interrupt)
    payload = {"interrupt_id": "int-test", "action": "accept", "reason": "ok", "expected_revision": 0, "values": {}}
    first = store.record_approval(second["run_id"], payload, principal)
    assert first["execution_status"] == "not_dispatched"
    assert store.record_approval(second["run_id"], payload, principal)["approval_id"] == first["approval_id"]
    with pytest.raises(Conflict):
        store.record_approval(second["run_id"], {**payload, "action": "reject"}, principal)


def test_resume_idempotency_digest_includes_resume_payload(tmp_path, principal):
    from src.platform.agent_store import AgentStore
    store = AgentStore(f"sqlite:///{tmp_path / 'agent-resume.sqlite3'}")
    thread = store.create_thread(principal)
    parent = store.enqueue(principal, {"question": "澄清"}, "parent", thread_id=thread["thread_id"])
    store.claim()
    store.finish(parent["run_id"], "awaiting_input", {},
                 interrupt={"type": "clarification", "interrupt_id": "int-test"})
    payload = {"interrupt_id": "int-test", "action": "clarify", "values": {"pipe_id": "a"},
               "reason": "", "expected_revision": 0}
    store.enqueue_resume(parent["run_id"], payload, "resume-key", principal)
    with pytest.raises(Conflict):
        store.enqueue_resume(parent["run_id"], {**payload, "values": {"pipe_id": "b"}}, "resume-key", principal)


def test_mount_detection_selects_nested_mount_and_unescapes_spaces():
    from deploy.ppu.preflight import mount_for
    text = "1 0 8:0 / / rw - ext4 /dev/sda rw\n2 1 0:1 / /workdata rw - fuse.ossfs ossfs rw\n3 1 8:1 / /mnt/data\\040disk rw - xfs /dev/sdb rw"
    assert mount_for(Path("/workdata/project"), text)["filesystem"] == "fuse.ossfs"
    assert mount_for(Path("/mnt/data disk/project"), text)["filesystem"] == "xfs"


def test_storage_preflight_blocks_unconfirmed_filesystem(tmp_path, monkeypatch):
    from deploy.ppu import preflight
    assert preflight.inspect_storage(None)["status"] == "blocked"
    monkeypatch.setattr(preflight, "mount_for", lambda _: {"filesystem": "fuse.ossfs", "source": "ossfs"})
    assert preflight.inspect_storage(tmp_path)["status"] == "blocked"
    monkeypatch.setattr(preflight, "mount_for", lambda _: {"filesystem": "xfs", "source": "/dev/test"})
    assert preflight.inspect_storage(tmp_path)["status"] == "candidate"


def test_500_rows_30_columns_recomputed_against_reference():
    from src.pipe_health.compare import compare_with_reference
    from src.pipe_health.data_io import load_tables
    from src.pipe_health.pipeline import DEFAULT_STATIC, DEFAULT_DYNAMIC, DEFAULT_REFERENCE, evaluate
    static, dynamic = load_tables(DEFAULT_STATIC, DEFAULT_DYNAMIC)
    actual = evaluate(static, dynamic, datetime.fromisoformat("2026-09-18T00:01:11+08:00"))
    report = compare_with_reference(actual, DEFAULT_REFERENCE)
    assert report["passed"] and report["cell_mismatches"] == 0
    assert report["max_numeric_difference"] == 0
    assert actual.shape == (500, 30)
    assert actual["健康度Hs"].mean() == pytest.approx(82.4535)
