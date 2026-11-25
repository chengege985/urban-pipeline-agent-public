"""Per-execution platform ACL adapters for the frozen Agent kernel.

The graph receives these adapters instead of unfiltered repositories. Identity is
bound by the server runtime, then resolved against current server policy at every
read. Neither a model argument nor an HTTP header can establish this context.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Callable

from fastapi import HTTPException

from src.platform.authz import authorize_snapshot
from src.platform.input_batches import BatchPrincipal


class AgentAccessBoundary:
    def __init__(self, *, principal_lookup: Callable, snapshot_lookup: Callable,
                 batches, retriever=None, snapshot_store=None):
        self._principal_lookup = principal_lookup
        self._snapshot_lookup = snapshot_lookup
        self._snapshot_store = snapshot_store
        self._identity = ContextVar("agent_execution_identity", default=None)
        self.batches = _AuthorizedBatches(self, batches)
        self.retriever = _AuthorizedRetriever(self, retriever) if retriever is not None else None

    @contextmanager
    def bind(self, principal: BatchPrincipal):
        """Called only with the task identity obtained from the trusted queue."""
        identity = (principal.actor_id, principal.scope)
        token = self._identity.set(identity)
        try:
            self.current_principal()
            yield
        finally:
            self._identity.reset(token)

    def current_principal(self):
        identity = self._identity.get()
        if identity is None:
            raise PermissionError("Agent读取缺少服务端执行身份")
        try:
            principal = self._principal_lookup(*identity)
        except (HTTPException, KeyError, ValueError) as exc:
            raise PermissionError("Agent执行主体授权已失效") from exc
        if (principal is None or not principal.authenticated
                or (principal.actor_id, principal.scope) != identity
                or "run" not in getattr(principal, "permissions", frozenset())):
            raise PermissionError("Agent执行主体或运行权限已失效")
        return principal

    def snapshot_lookup(self, snapshot_id: str):
        principal = self.current_principal()
        # Deny an explicitly excluded dataset before fetching its contents.
        if (principal.datasets is not None and snapshot_id not in principal.datasets
                and not (self._snapshot_store is not None
                         and self._snapshot_store.has_generated_snapshot(principal, snapshot_id))):
            raise PermissionError("当前主体无权读取该数据集")
        snapshot = self._snapshot_lookup(snapshot_id)
        if snapshot.get("snapshot_id") != snapshot_id:
            raise PermissionError("返回快照与请求数据集不一致")
        try:
            authorize_snapshot(principal, snapshot, store=self._snapshot_store)
        except HTTPException as exc:
            raise PermissionError("当前主体无权读取该数据集") from exc
        return snapshot


class _AuthorizedBatches:
    def __init__(self, boundary: AgentAccessBoundary, repository):
        self._boundary, self._repository = boundary, repository

    def load(self, batch_ref: str, caller: BatchPrincipal):
        principal = self._boundary.current_principal()
        if (caller.actor_id, caller.scope) != (principal.actor_id, principal.scope):
            raise PermissionError("输入批次主体与当前执行不一致")
        request = self._repository.load(batch_ref, caller)
        # RecomputeRequest currently has no source-dataset identity. Ownership of
        # a batch alone cannot authorize a real/unknown dataset. Do not infer an
        # ACL from a data_mode string or a source_refs label supplied by clients.
        if request.data_mode != "synthetic":
            raise PermissionError("非合成输入批次缺少可信来源数据集绑定")
        if "synthetic" not in principal.modes:
            raise PermissionError("当前主体无权使用合成输入批次")
        return request


class _AuthorizedRetriever:
    def __init__(self, boundary: AgentAccessBoundary, retriever):
        self._boundary, self._retriever = boundary, retriever

    @property
    def corpus_id(self):
        return getattr(self._retriever, "corpus_id", None)

    @property
    def embedding_service_id(self):
        return getattr(self._retriever, "embedding_service_id", "unknown")

    @property
    def embedding_endpoint(self):
        return getattr(self._retriever, "embedding_endpoint", "")

    @property
    def endpoint(self):
        return getattr(self._retriever, "endpoint", "")

    @property
    def vector_status(self):
        return getattr(self._retriever, "vector_status", {"available": False, "mode": "none"})

    def search(self, query: str, *, scope: str, limit: int = 5, classification=None):
        principal = self._boundary.current_principal()
        corpus_id = getattr(self._retriever, "corpus_id", None)
        if (scope != principal.scope or not isinstance(corpus_id, str)
                or not corpus_id.strip()
                or corpus_id not in getattr(principal, "corpora", frozenset())):
            # Must reject before query embedding or BM25 returns any source text.
            raise PermissionError("当前主体无权检索该语料")
        return self._retriever.search(query, scope=scope, limit=limit,
                                      classification=classification)
