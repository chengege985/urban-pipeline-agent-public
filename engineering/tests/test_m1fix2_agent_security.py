"""Platform access boundaries: synthetic fixtures only, no model or network."""
from types import SimpleNamespace

import pytest

from src.platform.agent_security import AgentAccessBoundary
from src.platform.input_batches import BatchPrincipal


def principal(**changes):
    values = dict(actor_id="alice", authenticated=True, scope="internal",
                  modes=frozenset({"synthetic"}), datasets=frozenset({"snap-a"}),
                  corpora=frozenset({"corpus-a"}),
                  permissions=frozenset({"run", "resume", "review"}))
    return SimpleNamespace(**{**values, **changes})


class Batches:
    def __init__(self, mode="synthetic"):
        self.mode, self.calls = mode, 0

    def load(self, ref, caller):
        self.calls += 1
        if caller.actor_id != "alice":
            raise PermissionError("batch owner")
        return SimpleNamespace(data_mode=self.mode)


class Retriever:
    corpus_id = "corpus-a"
    embedding_service_id = "fake"
    embedding_endpoint = ""
    vector_status = {"available": False, "mode": "bm25"}

    def __init__(self):
        self.calls = 0

    def search(self, query, *, scope, limit=5, classification=None):
        self.calls += 1
        return ([{"content": "synthetic allowed content"}], "bm25")


@pytest.fixture
def boundary():
    current = [principal()]
    retriever, batches = Retriever(), Batches()
    lookup_calls = []

    def lookup(snapshot_id):
        lookup_calls.append(snapshot_id)
        return {"snapshot_id": snapshot_id, "data_mode": "synthetic",
                "payload": {"data_mode": "synthetic"}}

    value = AgentAccessBoundary(principal_lookup=lambda actor, scope: current[0],
                                snapshot_lookup=lookup, batches=batches, retriever=retriever)
    return value, current, retriever, batches, lookup_calls


def test_no_execution_context_cannot_read_any_resource(boundary):
    value, _, retriever, batches, lookup_calls = boundary
    for action in (lambda: value.snapshot_lookup("snap-a"),
                   lambda: value.retriever.search("q", scope="internal"),
                   lambda: value.batches.load("batch-any", BatchPrincipal(actor_id="alice", scope="internal"))):
        with pytest.raises(PermissionError):
            action()
    assert retriever.calls == batches.calls == len(lookup_calls) == 0


def test_authorized_snapshot_and_corpus_are_available(boundary):
    value, _, retriever, _, _ = boundary
    with value.bind(BatchPrincipal(actor_id="alice", scope="internal")):
        assert value.snapshot_lookup("snap-a")["snapshot_id"] == "snap-a"
        assert value.retriever.search("q", scope="internal")[1] == "bm25"
    assert retriever.calls == 1


def test_cross_dataset_and_corpus_revocation_fail_closed(boundary):
    value, current, retriever, _, _ = boundary
    with value.bind(BatchPrincipal(actor_id="alice", scope="internal")):
        with pytest.raises(PermissionError):
            value.snapshot_lookup("snap-b")
        current[0] = principal(corpora=frozenset())
        with pytest.raises(PermissionError):
            value.retriever.search("secret", scope="internal")
    assert retriever.calls == 0


@pytest.mark.parametrize("changes", [{"authenticated": False}, {"actor_id": "bob"},
                                     {"scope": "competition"}, {"permissions": frozenset()}])
def test_stale_identity_or_permission_is_rechecked(boundary, changes):
    value, current, retriever, batches, lookup_calls = boundary
    with value.bind(BatchPrincipal(actor_id="alice", scope="internal")):
        current[0] = principal(**changes)
        with pytest.raises(PermissionError):
            value.snapshot_lookup("snap-a")
        with pytest.raises(PermissionError):
            value.retriever.search("q", scope="internal")
    assert retriever.calls == batches.calls == len(lookup_calls) == 0


def test_caller_cannot_change_retrieval_scope_or_batch_identity(boundary):
    value, _, retriever, batches, _ = boundary
    with value.bind(BatchPrincipal(actor_id="alice", scope="internal")):
        with pytest.raises(PermissionError):
            value.retriever.search("q", scope="competition")
        with pytest.raises(PermissionError):
            value.batches.load("batch-any", BatchPrincipal(actor_id="bob", scope="internal"))
    assert retriever.calls == batches.calls == 0


@pytest.mark.parametrize("mode", ["real", "fixture_replay", "unknown"])
def test_unbound_non_synthetic_batch_is_not_authorized(boundary, mode):
    value, current, _, batches, _ = boundary
    batches.mode = mode
    current[0] = principal(modes=frozenset({"synthetic", "real", "fixture_replay"}))
    with value.bind(BatchPrincipal(actor_id="alice", scope="internal")):
        with pytest.raises(PermissionError, match="数据集"):
            value.batches.load("batch-any", BatchPrincipal(actor_id="alice", scope="internal"))


def test_synthetic_owned_batch_requires_current_mode_permission(boundary):
    value, current, _, _, _ = boundary
    caller = BatchPrincipal(actor_id="alice", scope="internal")
    with value.bind(caller):
        assert value.batches.load("batch-any", caller).data_mode == "synthetic"
        current[0] = principal(modes=frozenset())
        with pytest.raises(PermissionError):
            value.batches.load("batch-any", caller)


def test_bind_cleans_up_after_exception_and_scopes_are_nested(boundary):
    value, _, _, _, _ = boundary
    caller = BatchPrincipal(actor_id="alice", scope="internal")
    with value.bind(caller):
        with pytest.raises(PermissionError):
            with value.bind(BatchPrincipal(actor_id="bob", scope="internal")):
                value.snapshot_lookup("snap-a")
        assert value.snapshot_lookup("snap-a")["snapshot_id"] == "snap-a"
    with pytest.raises(PermissionError):
        value.snapshot_lookup("snap-a")
