"""M1-FIX2: platform-injected trusted run_context; no model or worker is started."""
from __future__ import annotations

from src.platform.agent_runtime import AgentRuntime


class SnapshotStore:
    def __init__(self, mode: str | None = "synthetic"):
        self.mode = mode

    def get_snapshot(self, snapshot_id: str):
        if self.mode is None or snapshot_id == "missing":
            raise KeyError(snapshot_id)
        return {"snapshot_id": snapshot_id, "payload": {"data_mode": self.mode}}


class FakeStore:
    def __init__(self, task):
        self.task = task
        self.finished = None
        self.tools = []

    def claim(self):
        return self.task

    def record_tools(self, run_id, results):
        self.tools.extend(results)

    def finish(self, run_id, status, result, **kwargs):
        self.finished = (run_id, status, result, kwargs)

    def close(self):
        pass


class FakeGraph:
    def __init__(self):
        self.state = None

    def invoke(self, state, config):
        self.state = state
        return {"status": "succeeded", "phase": "completed", "tool_results": []}


def task(request=None):
    return {"run_id": "arun-test", "thread_id": "thread-test", "actor_id": "actor-a",
            "scope": "internal", "kind": "chat", "resume": None,
            "request": request or {"question": "查询评分", "snapshot_id": "snap-syn",
                                   "pipe_id": "syn-pipe-fault", "batch_refs": [], "scenario": None,
                                   "decision": None, "question_sensitivity": "synthetic"}}


def runtime_for(mode="synthetic"):
    runtime = AgentRuntime.__new__(AgentRuntime)
    runtime.store = FakeStore(task())
    runtime.graph = FakeGraph()
    runtime.snapshots = SnapshotStore(mode)
    return runtime


def test_trusted_run_context_is_derived_from_persisted_task_and_snapshot():
    runtime = runtime_for("synthetic")
    current_task = task()
    state = {**current_task["request"], "principal": {"actor_id": "actor-a", "scope": "internal"}}
    context = runtime._trusted_run_context(current_task, state)
    assert context["run_id"] == "arun-test"
    assert context["principal_id"] == "actor-a"
    assert context["snapshot_id"] == "snap-syn"
    assert context["question_sensitivity"] == "unknown"
    assert context["authorization_context"]["scope"] == "internal"
    assert context["authorization_context"]["data_mode"] == "synthetic"
    assert len(context["request_fingerprint"]) == 64


def test_unknown_and_real_modes_fail_closed_for_question_sensitivity():
    for mode, expected in (("synthetic", "unknown"), ("real", "unknown"), ("fixture_replay", "unknown"), (None, "unknown")):
        runtime = runtime_for(mode)
        current_task = task()
        assert runtime._trusted_run_context(current_task, current_task["request"])["question_sensitivity"] == expected
