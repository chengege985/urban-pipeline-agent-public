"""Agent线程、运行、审批与工具证据的持久化。

测试/单机可使用显式配置的SQLite；生产通过AGENT_DATABASE_URL连接PostgreSQL。
原确定性任务仍由Store管理，本模块不读写其表，也不复用其恢复逻辑。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

from sqlalchemy import JSON, Integer, String, UniqueConstraint, create_engine, select, text, case
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from src.platform.input_batches import BatchPrincipal
from src.platform.store import Conflict, canonical, fingerprint

TERMINAL = {"succeeded", "partial", "insufficient_data", "infeasible", "failed", "cancelled", "superseded"}
ACTIVE = {"queued", "running", "paused", "awaiting_input", "awaiting_approval"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentBase(DeclarativeBase):
    pass


class AgentThreadRow(AgentBase):
    __tablename__ = "agent_threads"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String)
    scope: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String)


class AgentRunRow(AgentBase):
    __tablename__ = "agent_runs"
    __table_args__ = (UniqueConstraint("thread_id", "request_key", name="uq_agent_run_key"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(String, index=True)
    actor_id: Mapped[str] = mapped_column(String)
    scope: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    parent_run_id: Mapped[str | None] = mapped_column(String, nullable=True)
    request_key: Mapped[str] = mapped_column(String)
    request_digest: Mapped[str] = mapped_column(String)
    request: Mapped[dict] = mapped_column(JSON)
    resume: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String, index=True)
    phase: Mapped[str | None] = mapped_column(String, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    interrupt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String, nullable=True)
    review_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentToolRow(AgentBase):
    __tablename__ = "agent_tools"
    __table_args__ = (UniqueConstraint("run_id", "evidence_id", name="uq_agent_tool_evidence"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    tool_name: Mapped[str] = mapped_column(String)
    evidence_id: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String)


class AgentApprovalRow(AgentBase):
    __tablename__ = "agent_approvals"
    __table_args__ = (UniqueConstraint("run_id", "interrupt_id", name="uq_agent_approval"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    run_id: Mapped[str] = mapped_column(String, index=True)
    interrupt_id: Mapped[str] = mapped_column(String)
    actor_id: Mapped[str] = mapped_column(String)
    action: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String)


class AgentExecutionBindingRow(AgentBase):
    """Server-only job/execution binding; checkpoint dictionaries are not authority."""
    __tablename__ = "agent_execution_bindings"
    job_id: Mapped[str] = mapped_column(String, primary_key=True)
    execution_run_id: Mapped[str] = mapped_column(String)
    actor_id: Mapped[str] = mapped_column(String)
    scope: Mapped[str] = mapped_column(String)
    context: Mapped[dict] = mapped_column(JSON)
    kernel_hash: Mapped[str] = mapped_column(String)
    process_id: Mapped[str] = mapped_column(String)
    checkpoint_hash: Mapped[str | None] = mapped_column(String, nullable=True)


class AgentExecutionEventRow(AgentBase):
    """Observed execute-node outputs, never deserialized into kernel receipts."""
    __tablename__ = "agent_execution_events"
    __table_args__ = (UniqueConstraint("job_id", "evidence_id", name="uq_execution_event"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(String)
    evidence_id: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)


class AgentStore:
    def __init__(self, url: str):
        if not url:
            raise ValueError("AGENT_DATABASE_URL未配置")
        if url.startswith("sqlite:///"):
            path = Path(url.removeprefix("sqlite:///"))
            path.parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {})
        AgentBase.metadata.create_all(self.engine)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def _write(self):
        """Serialize short queue mutations across processes, without schema changes.

        SQLite's reserved writer transaction and PostgreSQL's transaction advisory
        lock protect the same read-check-write protocol. Never hold this lock during
        model/tool execution. All writers must use the updated store implementation.
        """
        with self.session() as db:
            try:
                if self.engine.dialect.name == "sqlite":
                    db.execute(text("BEGIN IMMEDIATE"))
                elif self.engine.dialect.name == "postgresql":
                    db.execute(text("SELECT pg_advisory_xact_lock(1464881230)"))
                else:
                    raise RuntimeError("Agent queue supports only SQLite or PostgreSQL")
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def close(self):
        self.engine.dispose()

    def _thread(self, db, thread_id, principal):
        row = db.get(AgentThreadRow, thread_id)
        if row is None:
            raise KeyError("线程不存在")
        if row.actor_id != principal.actor_id or row.scope != principal.scope:
            raise PermissionError("线程不属于当前主体")
        return row

    def create_thread(self, principal: BatchPrincipal, thread_id: str | None = None):
        tid = thread_id or "thread-" + uuid.uuid4().hex
        with self._write() as db:
            if db.get(AgentThreadRow, tid):
                return self._thread_view(self._thread(db, tid, principal))
            db.add(AgentThreadRow(id=tid, actor_id=principal.actor_id, scope=principal.scope,
                                  created_at=now(), updated_at=now()))
            return self._thread_view(db.get(AgentThreadRow, tid))

    @staticmethod
    def _thread_view(row):
        return {"thread_id": row.id, "actor_id": row.actor_id, "scope": row.scope,
                "created_at": row.created_at, "updated_at": row.updated_at}

    @staticmethod
    def _run_view(row):
        return {"run_id": row.id, "thread_id": row.thread_id, "kind": row.kind,
                "parent_run_id": row.parent_run_id,
                "execution_run_id": (row.result or {}).get("run_id"),
                "snapshot_id": (row.request or {}).get("snapshot_id"),
                "status": row.status, "phase": row.phase,
                "result": row.result, "interrupt": row.interrupt,
                "review_status": row.review_status, "review_revision": row.review_revision,
                "created_at": row.created_at, "finished_at": row.finished_at,
                "execution_status": "not_dispatched",
                "resumed_by_run_id": (row.interrupt or {}).get("resumed_by_run_id")}

    def get_thread(self, thread_id: str, principal: BatchPrincipal):
        with self.session() as db:
            row = self._thread(db, thread_id, principal)
            runs = db.scalars(select(AgentRunRow).where(AgentRunRow.thread_id == thread_id)
                              .order_by(AgentRunRow.created_at.desc()).limit(50)).all()
            return {**self._thread_view(row), "runs": [self._run_view(r) for r in runs]}

    def enqueue(self, principal: BatchPrincipal, request: dict, key: str,
                thread_id: str | None = None, kind: str = "chat", parent_run_id: str | None = None,
                resume: dict | None = None):
        if kind != "chat" or resume is not None or parent_run_id is not None:
            raise ValueError("恢复任务必须通过enqueue_resume消费中断")
        digest = fingerprint(request)
        with self._write() as db:
            if thread_id:
                thread = self._thread(db, thread_id, principal)
                old = db.scalar(select(AgentRunRow).where(AgentRunRow.thread_id == thread.id,
                                                        AgentRunRow.request_key == key))
            else:
                # The key is scoped to actor+scope when creating a new thread.
                rows = db.scalars(select(AgentRunRow).where(AgentRunRow.actor_id == principal.actor_id,
                    AgentRunRow.scope == principal.scope, AgentRunRow.request_key == key)).all()
                if len(rows) > 1:
                    raise Conflict("历史幂等键存在多个运行，请使用新键并核查旧记录")
                old = rows[0] if rows else None
                thread = None
            if old:
                if old.kind != "chat" or old.request_digest != digest:
                    raise Conflict("同一幂等键对应不同内容")
                return self._run_view(old)
            if thread is None:
                thread = AgentThreadRow(id="thread-" + uuid.uuid4().hex, actor_id=principal.actor_id,
                                        scope=principal.scope, created_at=now(), updated_at=now())
                db.add(thread)
            # A waiting checkpoint must be consumed before a new chat can replace it.
            waiting = db.scalar(select(AgentRunRow.id).where(AgentRunRow.thread_id == thread.id,
                AgentRunRow.status.in_(["awaiting_input", "awaiting_approval", "paused"])))
            if waiting:
                raise Conflict("线程有待恢复的任务，请先恢复或取消该任务")
            run = AgentRunRow(id="arun-" + uuid.uuid4().hex, thread_id=thread.id, actor_id=principal.actor_id,
                scope=principal.scope, kind="chat", parent_run_id=None, request_key=key,
                request_digest=digest, request=request, resume=None, status="queued", created_at=now(), review_revision=0)
            db.add(run)
            thread.updated_at = now()
            db.flush()
            return self._run_view(run)

    def find_by_key(self, principal: BatchPrincipal, key: str):
        """创建线程前的请求重试查询；同键不同内容由enqueue的摘要检查拒绝。"""
        with self.session() as db:
            rows = db.scalars(select(AgentRunRow).where(AgentRunRow.actor_id == principal.actor_id,
                                                        AgentRunRow.scope == principal.scope,
                                                        AgentRunRow.request_key == key)).all()
            return {**self._run_view(rows[0]), "request": rows[0].request} if rows else None

    def claim(self):
        with self._write() as db:
            queued = db.scalars(select(AgentRunRow).where(AgentRunRow.status == "queued")
                .order_by(case((AgentRunRow.kind == "resume", 0), else_=1), AgentRunRow.created_at)).all()
            for row in queued:
                busy = db.scalar(select(AgentRunRow.id).where(AgentRunRow.thread_id == row.thread_id,
                    AgentRunRow.status.in_(["running", "awaiting_input", "awaiting_approval", "paused"])))
                if busy:
                    continue
                # Resume checkpoint before any older queued chat in this thread.
                if row.kind == "chat" and any(r.kind == "resume" and r.thread_id == row.thread_id for r in queued):
                    continue
                row.status = "running"
                db.flush()
                return {**self._run_view(row), "request": row.request, "resume": row.resume,
                        "actor_id": row.actor_id, "scope": row.scope}
            return None

    def finish(self, run_id: str, status: str, result: dict, *, interrupt=None, phase=None):
        if status not in {"succeeded", "partial", "insufficient_data", "infeasible", "failed",
                          "cancelled", "awaiting_input", "awaiting_approval", "paused"}:
            raise ValueError("非法Agent状态")
        with self._write() as db:
            row = db.get(AgentRunRow, run_id)
            if row is None or row.status != "running":
                raise Conflict("只有运行中的Agent任务可以完成")
            row.status, row.phase = status, phase
            row.result, row.interrupt = result, interrupt
            if status == "awaiting_approval":
                row.review_status = "pending_review"
            row.finished_at = now() if status in TERMINAL else None
            db.get(AgentThreadRow, row.thread_id).updated_at = now()
            return self._run_view(row)

    def get_run(self, run_id: str, principal: BatchPrincipal):
        with self.session() as db:
            row = db.get(AgentRunRow, run_id)
            if row is None:
                raise KeyError("运行不存在")
            if row.actor_id != principal.actor_id or row.scope != principal.scope:
                raise PermissionError("运行不属于当前主体")
            binding = db.get(AgentExecutionBindingRow, run_id)
            return {**self._run_view(row), "execution_run_id": binding.execution_run_id if binding else None}

    def pause_running(self):
        """Maintenance only, after all workers stop; never called at worker startup.

        Marks stale runs for operator inspection. It does not auto-replay tools or
        requeue arbitrary checkpoints; the public resume API handles interrupts.
        """
        with self._write() as db:
            rows = db.scalars(select(AgentRunRow).where(AgentRunRow.status == "running")).all()
            for row in rows:
                row.status, row.phase = "paused", "worker_restarted"
                row.result = {"status": "paused", "reason": "服务重启，需显式恢复检查点"}
            return len(rows)

    def record_tools(self, run_id: str, results: list[dict]):
        with self._write() as db:
            for item in results:
                existing = db.scalar(select(AgentToolRow).where(AgentToolRow.run_id == run_id,
                                                                AgentToolRow.evidence_id == item["evidence_id"]))
                if existing:
                    continue
                db.add(AgentToolRow(id="atool-" + uuid.uuid4().hex, run_id=run_id, tool_name=item["tool_name"],
                                    evidence_id=item["evidence_id"], payload=item, created_at=now()))

    def evidence(self, run_id: str, principal: BatchPrincipal):
        run = self.get_run(run_id, principal)
        with self.session() as db:
            tools = db.scalars(select(AgentToolRow).where(AgentToolRow.run_id == run_id)
                               .order_by(AgentToolRow.created_at)).all()
            return {"run_id": run_id, "status": run["status"], "execution_status": "not_dispatched",
                    "tools": [r.payload for r in tools], "result": run["result"],
                    "execution_history": self.execution_history(run_id, principal)}

    def bind_execution(self, task: dict, context: dict, *, kernel_hash: str, process_id: str):
        with self._write() as db:
            row = db.get(AgentRunRow, task["run_id"])
            if row is None or row.status != "running":
                raise Conflict("Only a claimed job can bind execution")
            if (row.actor_id, row.scope, row.thread_id) != (task["actor_id"], task["scope"], task["thread_id"]):
                raise PermissionError("Execution identity mismatch")
            if context["principal_id"] != row.actor_id or context.get("snapshot_id") != row.request.get("snapshot_id"):
                raise PermissionError("Execution context mismatch")
            if db.get(AgentExecutionBindingRow, row.id):
                raise Conflict("Execution identity already bound")
            db.add(AgentExecutionBindingRow(job_id=row.id, execution_run_id=context["run_id"],
                actor_id=row.actor_id, scope=row.scope, context=context, kernel_hash=kernel_hash,
                process_id=process_id, checkpoint_hash=None))

    def execution_binding(self, run_id: str, principal: BatchPrincipal):
        self.get_run(run_id, principal)
        with self.session() as db:
            row = db.get(AgentExecutionBindingRow, run_id)
            if row is None:
                raise Conflict("No trusted execution binding; legacy checkpoint cannot resume")
            return {key: getattr(row, key) for key in ('job_id', 'execution_run_id', 'actor_id', 'scope',
                    'context', 'kernel_hash', 'process_id', 'checkpoint_hash')}

    def checkpoint_execution(self, job_id: str, values: dict):
        with self._write() as db:
            row = db.get(AgentExecutionBindingRow, job_id)
            task = db.get(AgentRunRow, job_id)
            if row is None or task.status != 'running':
                raise Conflict("Only active bound execution can seal checkpoint")
            row.checkpoint_hash = fingerprint(values)

    def _record_execution_update(self, job_id: str, records: list[dict]):
        """Worker observer only; no HTTP write route and no receipt issuing API."""
        with self._write() as db:
            task = db.get(AgentRunRow, job_id)
            binding = db.get(AgentExecutionBindingRow, job_id)
            if task is None or task.status != 'running' or binding is None:
                raise Conflict("Execution event requires active server binding")
            for record in records:
                receipt, output = record['receipt'], record['output']
                if (receipt['run_id'], receipt['principal_id'], receipt['snapshot_id']) != (
                        binding.execution_run_id, task.actor_id, task.request.get('snapshot_id')):
                    raise PermissionError("Execution event identity mismatch")
                if (receipt['result_hash'] != fingerprint(output['result']) or
                        any(receipt[k] != output[k] for k in ('tool_name', 'tool_version', 'status', 'evidence_id'))):
                    raise Conflict("Execution event output mismatch")
                payload = {**record, 'kernel_hash': binding.kernel_hash, 'job_id': job_id}
                digest = fingerprint(payload)
                old = db.scalar(select(AgentExecutionEventRow).where(
                    AgentExecutionEventRow.job_id == job_id,
                    AgentExecutionEventRow.evidence_id == receipt['evidence_id']))
                if old:
                    if old.digest != digest:
                        raise Conflict("Execution evidence cannot be rewritten")
                    continue
                db.add(AgentExecutionEventRow(id='exec-' + uuid.uuid4().hex, job_id=job_id,
                    evidence_id=receipt['evidence_id'], payload=payload, digest=digest, created_at=now()))

    def execution_history(self, run_id: str, principal: BatchPrincipal):
        self.get_run(run_id, principal)
        with self.session() as db:
            rows = db.scalars(select(AgentExecutionEventRow).where(AgentExecutionEventRow.job_id == run_id)
                              .order_by(AgentExecutionEventRow.created_at, AgentExecutionEventRow.id)).all()
            for row in rows:
                if fingerprint(row.payload) != row.digest:
                    raise Conflict("Execution archive integrity failure")
            return {'trust': 'platform_observed_execution', 'usable_as_live_receipt': False,
                    'records': [row.payload for row in rows]}

    def pending_interrupt(self, run_id: str, principal: BatchPrincipal):
        run = self.get_run(run_id, principal)
        if run["status"] not in {"awaiting_input", "awaiting_approval"} or not run["interrupt"]:
            raise Conflict("该运行没有待处理的中断")
        return run

    def record_approval(self, run_id: str, payload: dict, principal: BatchPrincipal):
        with self._write() as db:
            return self._record_approval(db, run_id, payload, principal)

    def _record_approval(self, db, run_id: str, payload: dict, principal: BatchPrincipal):
        row = db.get(AgentRunRow, run_id)
        if row is None or row.actor_id != principal.actor_id or row.scope != principal.scope:
            raise PermissionError("无权审批该运行")
        if row.status != "awaiting_approval" or not row.interrupt:
            raise Conflict("该运行不在等待审批状态")
        if payload["interrupt_id"] != row.interrupt.get("interrupt_id"):
            raise Conflict("中断ID不匹配")
        # 幂等重试先返回已有审批，不能让版本递增把同请求误判为过期。
        existing = db.scalar(select(AgentApprovalRow).where(AgentApprovalRow.run_id == run_id,
                                                            AgentApprovalRow.interrupt_id == payload["interrupt_id"]))
        if existing:
            current = {k: v for k, v in existing.payload.items()
                       if k in {"interrupt_id", "action", "reason", "expected_revision", "values"}}
            requested = {k: v for k, v in payload.items()
                         if k in {"interrupt_id", "action", "reason", "expected_revision", "values"}}
            if current != requested:
                raise Conflict("同一中断已有不同的审批内容")
            return existing.payload
        if payload["expected_revision"] != row.review_revision:
            raise Conflict("审核版本已变化，请刷新后重试")
        row.review_status, row.review_revision = payload["action"], row.review_revision + 1
        value = {**payload, "approval_id": "ap-" + uuid.uuid4().hex, "actor_id": principal.actor_id,
                 "created_at": now(), "execution_status": "not_dispatched"}
        db.add(AgentApprovalRow(id=value["approval_id"], run_id=run_id, interrupt_id=payload["interrupt_id"],
                                actor_id=principal.actor_id, action=payload["action"],
                                reason=payload.get("reason", ""), payload=value, created_at=value["created_at"]))
        return value


    def find_interrupt(self, thread_id: str, interrupt_id: str, principal: BatchPrincipal):
        """Find the exact interrupt, including consumed ones for idempotent retries."""
        with self.session() as db:
            self._thread(db, thread_id, principal)
            rows = db.scalars(select(AgentRunRow).where(AgentRunRow.thread_id == thread_id)
                              .order_by(AgentRunRow.created_at.desc())).all()
            row = next((r for r in rows if (r.interrupt or {}).get("interrupt_id") == interrupt_id), None)
            if row is None:
                raise Conflict("线程中不存在该中断")
            return self._run_view(row)

    def enqueue_resume(self, parent_run_id: str, payload: dict, key: str, principal: BatchPrincipal):
        with self._write() as db:
            parent = db.get(AgentRunRow, parent_run_id)
            if parent is None:
                raise KeyError("运行不存在")
            if parent.actor_id != principal.actor_id or parent.scope != principal.scope:
                raise PermissionError("运行不属于当前主体")
            interrupt = parent.interrupt or {}
            if payload["interrupt_id"] != interrupt.get("interrupt_id"):
                raise Conflict("中断ID不匹配")
            children = db.scalars(select(AgentRunRow).where(AgentRunRow.parent_run_id == parent_run_id,
                                                          AgentRunRow.kind == "resume")).all()
            if children:
                if len(children) != 1 or children[0].resume != payload:
                    raise Conflict("该中断已由不同恢复请求消费")
                # Reconcile a legacy one-child record only on an authenticated,
                # identical retry; do not silently choose between old duplicates.
                if parent.status in {"awaiting_input", "awaiting_approval"}:
                    parent.status, parent.phase, parent.finished_at = "superseded", "resumed", now()
                    parent.interrupt = {**interrupt, "consumed": True, "resumed_by_run_id": children[0].id}
                    db.flush()
                # A changed transport idempotency key cannot create another child.
                return self._run_view(children[0])
            if parent.status not in {"awaiting_input", "awaiting_approval"}:
                raise Conflict("该运行没有可消费的中断")
            action = payload.get("action")
            if interrupt.get("type") == "clarification":
                if payload.get("expected_revision", 0) != parent.review_revision:
                    raise Conflict("中断版本已变化，请刷新后重试")
                if action not in {"clarify", "cancel"}:
                    raise ValueError("澄清只接受clarify或cancel动作")
            elif interrupt.get("type") == "approval":
                if action not in {"accept", "reject", "revise", "cancel"}:
                    raise ValueError("审批动作不合法")
                self._record_approval(db, parent_run_id, payload, principal)
            else:
                raise ValueError("未知中断类型")
            if db.scalar(select(AgentRunRow.id).where(AgentRunRow.thread_id == parent.thread_id,
                                                    AgentRunRow.request_key == key)):
                raise Conflict("该幂等键已用于其他运行")
            child = AgentRunRow(id="arun-" + uuid.uuid4().hex, thread_id=parent.thread_id,
                actor_id=principal.actor_id, scope=principal.scope, kind="resume", parent_run_id=parent_run_id,
                request_key=key, request_digest=fingerprint({"kind": "resume", "parent_run_id": parent_run_id,
                    "request": parent.request, "resume": payload}), request=parent.request, resume=payload,
                status="queued", created_at=now(), review_revision=0)
            db.add(child)
            parent.status, parent.phase, parent.finished_at = "superseded", "resumed", now()
            parent.interrupt = {**interrupt, "consumed": True, "resumed_by_run_id": child.id}
            db.get(AgentThreadRow, parent.thread_id).updated_at = now()
            db.flush()
            return self._run_view(child)
