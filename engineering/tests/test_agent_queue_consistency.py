"""Queue and interruption regressions; isolated SQLite, no running service DB."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import os
import uuid
import pytest
from sqlalchemy import select, create_engine, text
from sqlalchemy.engine import make_url
from src.platform.agent_store import AgentStore, AgentApprovalRow, AgentRunRow, Conflict
from src.platform.input_batches import BatchPrincipal
from src.platform.agent_schemas import AgentChatRequest
from src.platform.agent_runtime import AgentRuntime

P = BatchPrincipal(actor_id="queue-test", scope="internal")

@pytest.fixture(params=["sqlite", "postgres"] if os.getenv("WATER_AGENT_TEST_POSTGRES_DSN") else ["sqlite"])
def store(tmp_path, request):
    # PostgreSQL is opt-in and must point to a dedicated disposable test cluster.
    admin = None
    if request.param == "postgres":
        dsn = os.environ["WATER_AGENT_TEST_POSTGRES_DSN"]
        schema = "agent_queue_test_" + uuid.uuid4().hex
        admin = create_engine(dsn)
        with admin.begin() as db:
            db.execute(text(f'CREATE SCHEMA "{schema}"'))
        url = make_url(dsn).update_query_dict({"options": f"-csearch_path={schema}"})
        value = AgentStore(url.render_as_string(hide_password=False))
    else:
        value = AgentStore(f"sqlite:///{tmp_path / 'queue.sqlite3'}")
    try:
        yield value
    finally:
        value.close()
        if admin is not None:
            with admin.begin() as db:
                db.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
            admin.dispose()


def simultaneous(call, count=6):
    barrier = Barrier(count, timeout=15)
    def worker(i):
        barrier.wait()
        return call(i)
    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(worker, range(count)))


def pending(store, kind="clarification", key="parent"):
    run = store.enqueue(P, {"question": "fixture"}, key)
    assert store.claim()["run_id"] == run["run_id"]
    status = "awaiting_approval" if kind == "approval" else "awaiting_input"
    store.finish(run["run_id"], status, {"status": "partial"},
        interrupt={"type": kind, "interrupt_id": "int-" + key, "revision": 0})
    return run


def payload(run, action="clarify"):
    return {"interrupt_id": "int-" + ("approve" if action == "accept" else "parent"),
            "action": action, "reason": "fixture", "expected_revision": 0, "values": {}}


def test_new_thread_submit_idempotency_is_atomic(store, monkeypatch, tmp_path):
    import json
    from src.platform.authz import resolve_actor
    from src.platform.agent_security import AgentAccessBoundary
    from src.platform.input_batches import InputBatchRepository
    monkeypatch.setenv('WATER_PRINCIPALS', json.dumps({'test-token': {
        'actor_id': P.actor_id, 'scope': P.scope, 'modes': ['synthetic']}}))
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.store = store
    runtime.access = AgentAccessBoundary(principal_lookup=resolve_actor,
        snapshot_lookup=lambda _: None, batches=InputBatchRepository(tmp_path))
    request = AgentChatRequest(question="same request")
    rows = simultaneous(lambda _: runtime.submit(P, request, "same-key"))
    assert len({r["run_id"] for r in rows}) == 1
    assert len({r["thread_id"] for r in rows}) == 1
    with pytest.raises(Conflict):
        runtime.submit(P, AgentChatRequest(question="different"), "same-key")


def test_existing_thread_enqueue_idempotency_is_atomic(store):
    tid = store.create_thread(P)["thread_id"]
    rows = simultaneous(lambda _: store.enqueue(P, {"question": "same"}, "key", thread_id=tid))
    assert len({r["run_id"] for r in rows}) == 1


def test_only_one_worker_claims_a_run(store):
    run = store.enqueue(P, {"question": "fixture"}, "claim")
    rows = simultaneous(lambda _: store.claim())
    assert [r["run_id"] for r in rows if r] == [run["run_id"]]


def test_claim_serializes_thread_not_independent_threads(store):
    first = store.enqueue(P, {"question": "one"}, "first")
    second = store.enqueue(P, {"question": "two"}, "second", thread_id=first["thread_id"])
    other = store.enqueue(P, {"question": "other"}, "other")
    assert store.claim()["run_id"] == first["run_id"]
    assert store.claim()["run_id"] == other["run_id"]
    assert store.claim() is None
    store.finish(first["run_id"], "succeeded", {})
    assert store.claim()["run_id"] == second["run_id"]


def test_concurrent_resume_is_consumed_once_even_with_different_keys(store):
    run = pending(store)
    data = payload(run)
    rows = simultaneous(lambda i: store.enqueue_resume(run["run_id"], data, f"resume-{i}", P))
    assert len({r["run_id"] for r in rows}) == 1
    parent = store.get_run(run["run_id"], P)
    assert parent["status"] == "superseded" and parent["phase"] == "resumed"
    assert parent["resumed_by_run_id"] == rows[0]["run_id"]
    assert parent["interrupt"]["consumed"] is True
    assert store.claim()["run_id"] == rows[0]["run_id"]
    store.finish(rows[0]["run_id"], "insufficient_data", {})
    assert store.enqueue_resume(run["run_id"], data, "later-key", P)["run_id"] == rows[0]["run_id"]
    with pytest.raises(Conflict):
        store.enqueue_resume(run["run_id"], {**data, "action": "cancel"}, "different", P)


def test_resume_precedes_already_queued_chat_and_new_interrupt_can_resume(store):
    run = store.enqueue(P, {"question": "fixture"}, "parent")
    store.claim()
    waiting_chat = store.enqueue(P, {"question": "later"}, "later", thread_id=run["thread_id"])
    store.finish(run["run_id"], "awaiting_input", {},
                 interrupt={"type": "clarification", "interrupt_id": "int-parent"})
    assert store.claim() is None
    child = store.enqueue_resume(run["run_id"], payload(run), "resume", P)
    assert store.claim()["run_id"] == child["run_id"]
    store.finish(child["run_id"], "awaiting_input", {},
                 interrupt={"type": "clarification", "interrupt_id": "int-round-two"})
    second = {**payload(run), "interrupt_id": "int-round-two", "values": {"pipe_id": "a"}}
    grandchild = store.enqueue_resume(child["run_id"], second, "resume-two", P)
    assert store.claim()["run_id"] == grandchild["run_id"]
    store.finish(grandchild["run_id"], "succeeded", {})
    assert store.claim()["run_id"] == waiting_chat["run_id"]


def test_resume_approval_and_enqueue_are_one_transaction(store):
    run = pending(store, "approval", "approve")
    data = payload(run, "accept")
    child = store.enqueue_resume(run["run_id"], data, "resume", P)
    assert store.get_run(run["run_id"], P)["review_revision"] == 1
    assert store.enqueue_resume(run["run_id"], data, "retry", P)["run_id"] == child["run_id"]
    with store.session() as db:
        assert len(db.scalars(select(AgentApprovalRow)).all()) == 1


def test_failed_enqueue_rolls_back_approval_and_interrupt_consumption(store):
    run = pending(store, "approval", "approve")
    with pytest.raises(Conflict):
        store.enqueue_resume(run["run_id"], payload(run, "accept"), "approve", P)
    parent = store.get_run(run["run_id"], P)
    assert parent["status"] == "awaiting_approval" and parent["review_revision"] == 0
    with store.session() as db:
        assert db.scalars(select(AgentApprovalRow)).all() == []


def test_find_exact_consumed_interrupt_not_latest_pending(store):
    run = pending(store)
    child = store.enqueue_resume(run["run_id"], payload(run), "resume", P)
    store.claim()
    store.finish(child["run_id"], "awaiting_input", {},
                 interrupt={"type": "clarification", "interrupt_id": "int-new"})
    assert store.find_interrupt(run["thread_id"], "int-parent", P)["run_id"] == run["run_id"]
    assert store.find_interrupt(run["thread_id"], "int-new", P)["run_id"] == child["run_id"]
    with pytest.raises(Conflict):
        store.find_interrupt(run["thread_id"], "unknown", P)


def test_rejected_scope_revision_and_direct_resume_cannot_mutate(store):
    run = pending(store)
    with pytest.raises(PermissionError):
        store.enqueue_resume(run["run_id"], payload(run), "resume", BatchPrincipal(actor_id="other", scope="internal"))
    with pytest.raises(Conflict):
        store.enqueue_resume(run["run_id"], {**payload(run), "expected_revision": 2}, "resume", P)
    with pytest.raises(ValueError):
        store.enqueue(P, {}, "bypass", kind="resume", parent_run_id=run["run_id"], resume=payload(run))
    with pytest.raises(Conflict):
        store.enqueue(P, {"question": "new"}, "new", thread_id=run["thread_id"])
    assert store.get_run(run["run_id"], P)["status"] == "awaiting_input"


def test_worker_start_does_not_pause_other_worker_run(store):
    run = store.enqueue(P, {"question": "fixture"}, "running")
    store.claim()
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.store, runtime.stopping, runtime.thread = store, Event(), None
    runtime.start()
    runtime.stopping.set()
    runtime.thread.join(timeout=3)
    assert store.get_run(run["run_id"], P)["status"] == "running"


def test_upgrade_can_finish_previously_recorded_approval(store):
    run = pending(store, "approval", "approve")
    data = payload(run, "accept")
    store.record_approval(run["run_id"], data, P)
    assert store.get_run(run["run_id"], P)["review_revision"] == 1
    child = store.enqueue_resume(run["run_id"], data, "resume", P)
    assert child["parent_run_id"] == run["run_id"]
    assert store.get_run(run["run_id"], P)["status"] == "superseded"
    with store.session() as db:
        assert len(db.scalars(select(AgentApprovalRow)).all()) == 1


def test_identical_retry_closes_legacy_unconsumed_parent(store):
    run = pending(store)
    data = payload(run)
    child = store.enqueue_resume(run["run_id"], data, "resume", P)
    # Reproduce the pre-fix persisted state without touching any running database.
    with store._write() as db:
        parent = db.get(AgentRunRow, run["run_id"])
        parent.status, parent.phase, parent.finished_at = "awaiting_input", "clarification_required", None
        parent.interrupt = {"type": "clarification", "interrupt_id": "int-parent"}
    assert store.enqueue_resume(run["run_id"], data, "another-key", P)["run_id"] == child["run_id"]
    assert store.get_run(run["run_id"], P)["status"] == "superseded"
    assert store.claim()["run_id"] == child["run_id"]
