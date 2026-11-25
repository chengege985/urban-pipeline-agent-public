"""Agent运行时：独立持久化队列、检查点恢复、工具留痕与审批。

不与旧Runtime共用任务表；启动worker不修改其他worker的在途状态。
停工维护可显式pause_running；任意崩溃检查点不自动重放，澄清/审批沿中断恢复。
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sqlite3
import hashlib
import uuid
from threading import Event, Thread

from langgraph.types import Command

from src.agents.graph import build_graph, request_fingerprint
from src.agents.providers.llm import build_llm
from src.agents.rag.retriever import FakeEmbedding, OpenAIEmbedding, build_retriever
from src.agents.state import AgentSettings
from src.platform.agent_schemas import AgentChatRequest, AgentResumeRequest
from src.platform.agent_store import AgentStore
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.authz import resolve_actor, require_permission, authorize_snapshot
from src.platform.agent_security import AgentAccessBoundary
from src.platform.store import Conflict, fingerprint

logger = logging.getLogger(__name__)


def jsonable(value):
    """只用于持久化/返回，不改变工具结果本身。"""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def settings_from_env() -> AgentSettings:
    return AgentSettings(
        llm_mode=os.getenv("AGENT_LLM_MODE", "fake"),
        llm_base_url=os.getenv("AGENT_LLM_BASE_URL", ""),
        llm_model=os.getenv("AGENT_LLM_MODEL", ""),
        embedding_mode=os.getenv("AGENT_EMBEDDING_MODE", "fake"),
        embedding_base_url=os.getenv("AGENT_EMBEDDING_BASE_URL", ""),
        embedding_model=os.getenv("AGENT_EMBEDDING_MODEL", ""),
        embedding_dimensions=int(os.getenv("AGENT_EMBEDDING_DIMENSIONS", "1024")),
        embedding_query_prefix=os.getenv("AGENT_EMBEDDING_QUERY_PREFIX", ""),
        embedding_document_prefix=os.getenv("AGENT_EMBEDDING_DOCUMENT_PREFIX", ""),
        model_timeout_seconds=int(os.getenv("AGENT_MODEL_TIMEOUT_SECONDS", "60")),
        request_timeout_seconds=int(os.getenv("AGENT_REQUEST_TIMEOUT_SECONDS", "300")),
    )


def build_checkpointer(root: Path):
    """memory只允许测试；生产必须显式配置sqlite/postgres检查点。"""
    mode = os.getenv("AGENT_CHECKPOINT", "memory")
    if mode == "memory":
        if os.getenv("AGENT_ALLOW_MEMORY") != "1":
            raise RuntimeError("生产检查点不能使用memory；请配置AGENT_CHECKPOINT=sqlite或postgres")
        from langgraph.checkpoint.memory import MemorySaver
        return MemorySaver()
    if mode == "sqlite":
        path = root / os.getenv("AGENT_CHECKPOINT_SQLITE", "experiments/agent/checkpoints.sqlite3")
        path.parent.mkdir(parents=True, exist_ok=True)
        from langgraph.checkpoint.sqlite import SqliteSaver
        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    if mode == "postgres":
        dsn = os.getenv("AGENT_CHECKPOINT_DSN", "")
        if not dsn:
            raise RuntimeError("AGENT_CHECKPOINT_DSN未配置")
        try:
            import psycopg
            from langgraph.checkpoint.postgres import PostgresSaver
        except ImportError as exc:
            raise RuntimeError("缺少psycopg或langgraph-checkpoint-postgres；不能伪造PostgreSQL检查点") from exc
        # 连接由saver持有；setup只创建检查点表，不改业务schema。
        conn = psycopg.connect(dsn, autocommit=True)
        saver = PostgresSaver(conn)
        saver.setup()
        return saver
    raise ValueError("未知AGENT_CHECKPOINT模式")


def build_agent_runtime(root: Path) -> "AgentRuntime":
    """按环境变量构建真实Agent；缺少数据库/检查点明确失败，不退回旧任务表。"""
    store = AgentStore(os.getenv("AGENT_DATABASE_URL", ""))
    runtime = AgentRuntime(store, root)
    return runtime


def build_embedding(settings: AgentSettings):
    if settings.embedding_mode == "openai":
        return OpenAIEmbedding(settings.embedding_base_url, settings.embedding_model,
                               settings.embedding_dimensions, settings.embedding_query_prefix,
                               settings.embedding_document_prefix)
    return FakeEmbedding(settings.embedding_dimensions)


class AgentRuntime:
    def __init__(self, store: AgentStore, root: Path, *, graph=None, checkpointer=None,
                 settings: AgentSettings | None = None, retriever=None, llm=None):
        self.store, self.root = store, root.resolve()
        self.settings = settings or settings_from_env()
        if self.settings.llm_mode != 'fake' or self.settings.embedding_mode != 'fake':
            raise RuntimeError('M1-FIX2平台合同仅开放离线Fake模型；真实模型需另行准入')
        self.llm = llm or build_llm(self.settings)
        # Loading/indexing documents is an explicit server provisioning action,
        # never an automatic consequence of starting the HTTP application.
        self.retriever = retriever
        self.batches = InputBatchRepository(self.root)
        from src.platform.store import Store
        self.snapshots = Store(self.root)
        self.access = AgentAccessBoundary(principal_lookup=resolve_actor,
            snapshot_lookup=self.snapshots.get_snapshot, batches=self.batches, retriever=self.retriever,
            snapshot_store=self.snapshots)
        self.graph = graph or build_graph(
            llm=self.llm, retriever=self.access.retriever, snapshot_lookup=self.access.snapshot_lookup,
            batches=self.access.batches, checkpointer=checkpointer or build_checkpointer(self.root), settings=self.settings)
        self.process_id = uuid.uuid4().hex
        kernel_root = Path(__file__).resolve().parents[1] / 'agents'
        self.kernel_hash = fingerprint({str(p.relative_to(kernel_root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                        for p in sorted(kernel_root.rglob('*.py'))})
        self.stopping = Event()
        self.thread = None

    def submit(self, principal: BatchPrincipal, request: AgentChatRequest, key: str | None = None):
        current = resolve_actor(principal.actor_id, principal.scope)
        require_permission(current, 'run')
        with self.access.bind(principal):
            if request.snapshot_id:
                self.access.snapshot_lookup(request.snapshot_id)
            for ref in request.batch_refs:
                self.access.batches.load(ref, principal)
        key = key or request.question[:120] + ":" + str(request.snapshot_id)
        return self.store.enqueue(principal, request.model_dump(mode="json"), key,
                                  thread_id=request.thread_id)

    def resume(self, parent_run_id: str, request: AgentResumeRequest, principal: BatchPrincipal, key: str | None = None):
        parent = self.store.get_run(parent_run_id, principal)
        current = resolve_actor(principal.actor_id, principal.scope)
        require_permission(current, 'resume')
        if request.action in {'accept', 'reject', 'revise'}:
            require_permission(current, 'review')
        self.authorize_history(principal, parent)
        if parent['status'] != 'superseded':
            self._resume_state(parent_run_id, parent['thread_id'], principal, request.model_dump(mode='json'))
        # Store atomically checks identical payload for an already consumed
        # interrupt. A transport retry never replays a finished graph.
        return self.store.enqueue_resume(parent_run_id, request.model_dump(mode="json"),
                                         key or "resume:" + request.interrupt_id, principal)

    def authorize_history(self, principal: BatchPrincipal, run: dict):
        current = resolve_actor(principal.actor_id, principal.scope)
        result = run.get('result') or {}
        if run.get('snapshot_id'):
            authorize_snapshot(current, self.snapshots.get_snapshot(run['snapshot_id']), store=self.snapshots)
        for citation in result.get('citations', []):
            if citation.get('corpus_id') not in current.corpora:
                raise PermissionError('历史资料的语料权限已撤销')
        for ref in result.get('batch_refs', []):
            with self.access.bind(principal):
                self.access.batches.load(ref, principal)

    def _resume_state(self, parent_id, thread_id, principal, payload):
        current = resolve_actor(principal.actor_id, principal.scope)
        require_permission(current, 'resume')
        if payload.get('action') in {'accept', 'reject', 'revise'}:
            require_permission(current, 'review')
        parent = self.store.get_run(parent_id, principal)
        self.authorize_history(principal, parent)
        binding = self.store.execution_binding(parent_id, principal)
        config = {'configurable': {'thread_id': thread_id}}
        checkpoint = self.graph.get_state(config)
        state = jsonable(checkpoint.values)
        if (not checkpoint.next or not binding['checkpoint_hash'] or fingerprint(state) != binding['checkpoint_hash']
                or binding['kernel_hash'] != self.kernel_hash
                or state.get('run_id') != binding['execution_run_id']
                or state.get('principal') != {'actor_id': principal.actor_id, 'scope': principal.scope}
                or state.get('snapshot_id') != binding['context'].get('snapshot_id')
                or (parent.get('interrupt') or {}).get('interrupt_id') != payload.get('interrupt_id')):
            raise Conflict('恢复检查点与服务端执行记录不一致')
        if binding['process_id'] != self.process_id and (state.get('verified_facts') or state.get('execution_receipts')):
            raise Conflict('unsupported_cross_process_receipt_restore: 历史回执不能重建当前内核账本')
        # Clarification can contain nested references; validate/authorize them
        # before consuming the interrupt, not only after Graph resumes.
        from src.agents.context import validate_context
        with self.access.bind(principal):
            validate_context(state, self.access.snapshot_lookup, self.access.batches,
                             payload.get('values', {}) if payload.get('action') == 'clarify' else {})
        return state, binding

    def _trusted_run_context(self, task: dict, state: dict, principal=None) -> dict:
        """Build run_context only from persisted task identity and snapshot data.

        Client text never supplies these fields. Unknown or unsupported dataset
        modes stay unknown and therefore fail closed at the outbound guard.
        """
        snapshot_id = state.get("snapshot_id")
        data_mode = "unknown"
        if isinstance(snapshot_id, str) and snapshot_id:
            try:
                snapshot = self.snapshots.get_snapshot(snapshot_id)
            except KeyError:
                snapshot = None
            data_mode = str(((snapshot or {}).get("payload") or {}).get("data_mode") or "unknown")
        # A synthetic dataset does not classify arbitrary user text as synthetic.
        question_sensitivity = 'unknown'
        return {
            "run_id": task["run_id"],
            "thread_id": task["thread_id"],
            "principal_id": task["actor_id"],
            "snapshot_id": snapshot_id,
            "question_sensitivity": question_sensitivity,
            "request_fingerprint": request_fingerprint(state),
            "authorization_context": {
                "scope": task["scope"], "snapshot_id": snapshot_id, "data_mode": data_mode,
                "corpus_ids": sorted(principal.corpora) if principal else [],
                "permissions": sorted(principal.permissions) if principal else [],
                "policy": "server-resolved-v1",
            },
        }

    def _observe_execution(self, job_id, update, state):
        """Capture only execute-node events from this trusted graph stream.

        These are historical execution records, not live kernel credentials.
        No restore path calls the kernel Ledger's private registration method.
        """
        outputs = {r.get('evidence_id'): r for r in update.get('tool_results', [])}
        records = []
        for receipt in update.get('execution_receipts', []):
            output = outputs.get(receipt.get('evidence_id'))
            if output is None:
                continue
            name = receipt['tool_name']
            arguments = ([{'pipe_id': (state.get('route') or {}).get('pipe_id')}] if name == 'query_assessment'
                         else [{'batch_ref': ref} for ref in state.get('batch_refs', [])] if name == 'calculate_health'
                         else [{}])
            if receipt.get('arguments_hash') not in {fingerprint(a) for a in arguments}:
                raise Conflict('执行参数与受控节点输入不一致')
            records.append({'receipt': receipt, 'output': output, 'source': 'graph.execute'})
        self.store._record_execution_update(job_id, records)

    def _invoke(self, task, state, config):
        value = state
        if task['kind'] == 'resume':
            value = Command(resume=task['resume'])
        result, interrupts = state, None
        for mode, update in self.graph.stream(value, config=config, stream_mode=['updates', 'values']):
            if mode == 'values':
                result = update
            elif mode == 'updates':
                if 'execute' in update:
                    self._observe_execution(task['run_id'], update['execute'], result)
                if '__interrupt__' in update:
                    interrupts = update['__interrupt__']
        if interrupts:
            return {**result, '__interrupt__': interrupts}
        return result

    def process_once(self):
        task = self.store.claim()
        if task is None:
            return False
        state = {"thread_id": task["thread_id"], "run_id": task["run_id"],
                 "principal": {"actor_id": task["actor_id"], "scope": task["scope"]},
                 "question": task["request"].get("question", ""),
                 "snapshot_id": task["request"].get("snapshot_id"),
                 "pipe_id": task["request"].get("pipe_id"),
                 "batch_refs": task["request"].get("batch_refs", []),
                 "scenario": task["request"].get("scenario"),
                 "decision": task["request"].get("decision"),
                 "status": "running", "phase": "started", "tool_results": [], "citations": [],
                 "evidence_refs": [], "missing": [], "errors": [], "allowed_tools": [],
                 "answer": "", "answer_mode": "", "clarification": {}, "approval": {},
                 "approval_result": {}, "capability_status": "supported", "error_kind": None}
        config = {"configurable": {"thread_id": task["thread_id"], "run_id": task["run_id"]}}
        try:
            principal = BatchPrincipal(actor_id=task['actor_id'], scope=task['scope'])
            current = resolve_actor(principal.actor_id, principal.scope)
            require_permission(current, 'run')
            with self.access.bind(principal):
                if task['kind'] == 'resume':
                    state, _ = self._resume_state(task['parent_run_id'], task['thread_id'], principal, task['resume'])
                if state.get('snapshot_id'):
                    self.access.snapshot_lookup(state['snapshot_id'])
                if task['kind'] != 'resume':
                    state['run_context'] = self._trusted_run_context(task, state, current)
                self.store.bind_execution(task, state['run_context'], kernel_hash=self.kernel_hash, process_id=self.process_id)
                result = self._invoke(task, state, config)
            interrupts = result.pop("__interrupt__", None)
            result = jsonable(result)
            checkpoint = jsonable(self.graph.get_state(config).values)
            self.store.checkpoint_execution(task['run_id'], checkpoint)
            self.store.record_tools(task["run_id"], result.get("tool_results", []))
            if interrupts:
                interrupt = interrupts[0] if isinstance(interrupts, (list, tuple)) else interrupts
                value = interrupt.get("value", interrupt) if isinstance(interrupt, dict) else getattr(interrupt, "value", interrupt)
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except ValueError:
                        value = {"type": "clarification", "message": value[:500]}
                value = jsonable(value)
                if not isinstance(value, dict):
                    value = {"type": "clarification", "message": str(value)[:500]}
                status = "awaiting_approval" if value.get("type") == "approval" else "awaiting_input"
                self.store.finish(task["run_id"], status, result, interrupt=value,
                                  phase="pending_review" if status == "awaiting_approval" else "clarification_required")
            else:
                self.store.finish(task["run_id"], result.get("status", "failed"), result,
                                  phase=result.get("phase", "completed"))
        except Exception as exc:
            # 不把凭据、模型原文或内部堆栈写入结果；日志保留类型供排查。
            logger.exception("Agent运行失败：%s", type(exc).__name__)
            try:
                self.store.finish(task["run_id"], "failed",
                                  {"status": "failed", "error_type": type(exc).__name__,
                                   "answer": "Agent运行失败；未生成确定性建议。"}, phase="runtime_failed")
            except Exception:
                self.stopping.set()
        return True

    def start(self):
        # Starting another worker must not pause work owned by an existing worker.
        # Stale-running recovery is an explicit, quiescent maintenance operation.
        self.thread = Thread(target=self.loop, name="water-agent-worker", daemon=True)
        self.thread.start()

    def loop(self):
        while not self.stopping.is_set():
            if not self.process_once():
                self.stopping.wait(0.5)

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=30)
        try:
            self.store.close()
        except Exception:
            pass
