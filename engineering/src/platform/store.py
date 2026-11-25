"""SQLite索引与不可变文件存储。

单机原型使用一个数据库和一个计算worker。快照按内容哈希定位；请求幂等键
与内容一起检查。审核状态独立于任务状态，任何审核都不代表现场执行。
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from threading import RLock
import uuid

from sqlalchemy import JSON, Integer, String, create_engine, event, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .schemas import FeedbackRequest, SnapshotPayload


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def fingerprint(value) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


class Conflict(ValueError):
    """同一幂等键不同内容，或审核版本过期。"""


class Base(DeclarativeBase):
    pass


class SnapshotRow(Base):
    __tablename__ = "snapshots"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str] = mapped_column(String)
    digest: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)


class ImportRow(Base):
    __tablename__ = "import_keys"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    digest: Mapped[str] = mapped_column(String)
    snapshot_id: Mapped[str] = mapped_column(String)


class TaskRow(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True)
    digest: Mapped[str] = mapped_column(String)
    kind: Mapped[str] = mapped_column(String)
    # Owner is assigned from the trusted server principal at enqueue time.
    actor_id: Mapped[str | None] = mapped_column(String, nullable=True)
    scope: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    review_status: Mapped[str | None] = mapped_column(String, nullable=True)
    review_revision: Mapped[int] = mapped_column(Integer, default=0)


class FeedbackRow(Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, unique=True)
    digest: Mapped[str] = mapped_column(String)
    run_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSON)


class Store:
    """数据文件只能写入root下的固定子目录，外部请求不能传入磁盘路径。"""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.runtime = self.root / "experiments/platform"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{self.runtime / 'platform.sqlite3'}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )

        @event.listens_for(self.engine, "connect")
        def configure(dbapi_connection, _):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self._ensure_task_owner_columns()
        self.session = sessionmaker(self.engine, expire_on_commit=False)
        self.lock = RLock()

    def _ensure_task_owner_columns(self):
        """Additive migration for old local databases; never drops data."""
        with self.engine.begin() as conn:
            columns = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tasks)")}
            if "actor_id" not in columns:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN actor_id VARCHAR")
            if "scope" not in columns:
                conn.exec_driver_sql("ALTER TABLE tasks ADD COLUMN scope VARCHAR")
            # Preserve the established migration label. It is not an authenticated
            # identity; HTTP authorization explicitly denies this historical owner.
            conn.exec_driver_sql("UPDATE tasks SET actor_id='local-demo' WHERE actor_id IS NULL")
            conn.exec_driver_sql("UPDATE tasks SET scope='internal' WHERE scope IS NULL")

    def write_artifact(self, relative: str, content: dict):
        """只创建，不覆盖；目录与文件名由服务端生成。"""
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("输出路径超出项目目录")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as f:
            f.write(json.dumps(content, ensure_ascii=False, indent=2, allow_nan=False))
        return relative

    def save_snapshot(self, payload: SnapshotPayload, key: str) -> dict:
        value = payload.model_dump(mode="json")
        digest = fingerprint(value)
        sid = "snap-" + digest[:24]
        with self.lock, self.session.begin() as db:
            old = db.get(ImportRow, key)
            if old and old.digest != digest:
                raise Conflict("同一导入幂等键对应不同内容")
            row = db.get(SnapshotRow, sid)
            if row is None:
                row = SnapshotRow(id=sid, digest=digest, created_at=now(), payload=value)
                # 内容寻址文件在崩溃后可能已创建，必须核对内容，不能覆盖。
                relative = f"data/platform/snapshots/{sid}.json"
                path = self.root / relative
                if path.exists():
                    if fingerprint(json.loads(path.read_text(encoding="utf-8"))) != digest:
                        raise Conflict("快照文件内容与哈希不一致")
                else:
                    self.write_artifact(relative, value)
                db.add(row)
            if old is None:
                db.add(ImportRow(id=key, digest=digest, snapshot_id=sid))
            return self.snapshot_view(row, detail=False)

    @staticmethod
    def snapshot_view(row, detail=True):
        p = row.payload
        value = {"snapshot_id": row.id, "sha256": row.digest, "created_at": row.created_at,
                 "name": p["name"], "data_mode": p["data_mode"], "source_status": p["source_status"],
                 "as_of": p["as_of"], "validation_status": "not_business_validated",
                 "counts": {k: len(p[k]) for k in ("assessments", "nodes", "edges", "services", "plans")}}
        if detail:
            value["payload"] = p
        return value

    def get_snapshot(self, sid: str) -> dict:
        with self.session() as db:
            row = db.get(SnapshotRow, sid)
            if row is None:
                raise KeyError("快照不存在")
            return self.snapshot_view(row)

    def imported(self, key: str) -> str | None:
        """已导入的固定内置键直接复用，避免每次启动重建500条评分对象。"""
        with self.session() as db:
            row = db.get(ImportRow, key)
            return row.snapshot_id if row else None

    def snapshots(self):
        with self.session() as db:
            rows = db.scalars(select(SnapshotRow).order_by(SnapshotRow.created_at.desc())).all()
            return [self.snapshot_view(r, False) for r in rows]

    @staticmethod
    def task_view(row):
        return {"run_id": row.id, "kind": row.kind, "status": row.status,
                "created_at": row.created_at, "finished_at": row.finished_at,
                "request": row.request, "result": row.result, "review_status": row.review_status,
                "review_revision": row.review_revision, "execution_status": "not_dispatched"}

    def enqueue(self, kind: str, request: dict, key: str, principal=None) -> dict:
        identity = kind + ":" + key
        digest = fingerprint(request)
        actor_id = str(getattr(principal, "actor_id", "local-demo") or "local-demo")
        scope = str(getattr(principal, "scope", "internal") or "internal")
        with self.lock, self.session.begin() as db:
            old = db.scalar(select(TaskRow).where(TaskRow.key == identity))
            if old:
                if old.digest != digest:
                    raise Conflict("同一任务幂等键对应不同内容")
                # A retry cannot reuse an idempotency key owned by another principal.
                if (old.actor_id or "local-demo") != actor_id or (old.scope or "internal") != scope:
                    raise Conflict("同一任务幂等键不属于当前主体")
                return self.task_view(old)
            row = TaskRow(id="run-" + uuid.uuid4().hex, key=identity, digest=digest, kind=kind,
                          actor_id=actor_id, scope=scope, status="queued", created_at=now(),
                          request=request, review_revision=0)
            db.add(row)
            return self.task_view(row)

    def claim(self) -> dict | None:
        """事务领取一个待执行任务；worker的进程锁保证单实例。"""
        with self.lock, self.session.begin() as db:
            row = db.scalar(select(TaskRow).where(TaskRow.status == "queued").order_by(TaskRow.created_at).limit(1))
            if row is None:
                return None
            row.status = "running"
            return self.task_view(row)

    def finish(self, rid: str, result: dict):
        allowed = {"succeeded", "partial", "insufficient_data", "infeasible", "failed"}
        if result.get("status") not in allowed:
            raise ValueError("引擎返回了非法任务状态")
        with self.lock, self.session.begin() as db:
            row = db.get(TaskRow, rid)
            if row is None or row.status != "running":
                raise Conflict("只有运行中的任务可以完成")
            result = {**result, "run_id": rid}
            relative = f"outputs/platform/{rid}/result.json"
            self.write_artifact(relative, result)
            row.result = {**result, "artifact_ref": relative}
            row.status, row.finished_at = result["status"], now()
            if result.get("recommendation_status") == "pending_review":
                row.review_status = "pending_review"

    def recover_interrupted(self):
        """只在取得worker独占锁后调用；不自动重算中断任务。"""
        with self.session.begin() as db:
            rows = db.scalars(select(TaskRow).where(TaskRow.status == "running")).all()
            for row in rows:
                row.status, row.finished_at = "failed", now()
                row.result = {"status": "failed", "reason": "worker中断，请显式重跑", "run_id": row.id}

    def get_task(self, rid: str):
        with self.session() as db:
            row = db.get(TaskRow, rid)
            if row is None:
                raise KeyError("任务不存在")
            return self.task_view(row)

    def task_owner(self, rid: str) -> tuple[str, str]:
        """Trusted owner lookup for Run/Report authorization; not a response field."""
        with self.session() as db:
            row = db.get(TaskRow, rid)
            if row is None:
                raise KeyError("任务不存在")
            return (row.actor_id or "local-demo", row.scope or "internal")

    def has_generated_snapshot(self, principal, snapshot_id: str) -> bool:
        """A derived synthetic grant is proven by a completed owned calculation.

        No external grant dictionary is accepted and no independent ACL table can
        drift from the persisted result. Real input/output still needs explicit ACL.
        """
        with self.session() as db:
            rows = db.scalars(select(TaskRow).where(
                TaskRow.actor_id == principal.actor_id, TaskRow.scope == principal.scope,
                TaskRow.kind == "assessment", TaskRow.status == "succeeded"))
            return any((row.request or {}).get("data_mode") == "synthetic"
                       and (row.result or {}).get("data_mode") == "synthetic"
                       and (row.result or {}).get("snapshot_id") == snapshot_id for row in rows)

    def tasks(self, limit=50, *, principal=None, offset=0):
        with self.session() as db:
            stmt = select(TaskRow)
            if principal is not None:
                stmt = stmt.where(TaskRow.actor_id == principal.actor_id,
                                  TaskRow.scope == principal.scope)
            stmt = stmt.order_by(TaskRow.created_at.desc()).offset(offset).limit(limit)
            return [self.task_view(r) for r in db.scalars(stmt)]

    def feedback(self, request: FeedbackRequest, key: str, principal=None):
        data = request.model_dump(mode="json")
        digest = fingerprint(data)
        actor_identity = str(getattr(principal, "actor_id", "self_reported_local_demo") or "self_reported_local_demo")
        with self.lock, self.session.begin() as db:
            old = db.scalar(select(FeedbackRow).where(FeedbackRow.key == key))
            if old:
                if old.digest != digest:
                    raise Conflict("同一反馈幂等键对应不同内容")
                if old.payload.get("actor_identity") != actor_identity:
                    raise Conflict("同一反馈幂等键不属于当前主体")
                return old.payload
            row = db.get(TaskRow, request.run_id)
            if row is None:
                raise KeyError("任务不存在")
            if principal is not None:
                owner_actor = row.actor_id or "local-demo"
                owner_scope = row.scope or "internal"
                if (getattr(principal, "actor_id", None), getattr(principal, "scope", "internal")) != (
                    owner_actor, owner_scope
                ):
                    raise PermissionError("当前主体无权登记该运行的审核")
            if row.status != "succeeded" or row.review_status is None:
                raise Conflict("只有计算完成且生成待审核建议的任务才能登记审核")
            if row.review_revision != request.expected_revision:
                raise Conflict("审核版本已变化，请刷新后重试")
            row.review_status = request.review_status
            row.review_revision += 1
            value = {**data, "feedback_id": "fb-" + uuid.uuid4().hex, "created_at": now(),
                     "revision": row.review_revision, "actor_identity": actor_identity,
                     "execution_status": "not_dispatched"}
            db.add(FeedbackRow(id=value["feedback_id"], key=key, digest=digest,
                               run_id=row.id, created_at=value["created_at"], payload=value))
            return value

    def feedbacks(self, rid: str | None = None, *, principal=None, limit=100, offset=0):
        with self.session() as db:
            stmt = select(FeedbackRow)
            if principal is not None:
                stmt = stmt.join(TaskRow, TaskRow.id == FeedbackRow.run_id).where(
                    TaskRow.actor_id == principal.actor_id,
                    TaskRow.scope == principal.scope,
                )
            if rid:
                stmt = stmt.where(FeedbackRow.run_id == rid)
            stmt = stmt.order_by(FeedbackRow.created_at.desc()).offset(offset).limit(limit)
            return [r.payload for r in db.scalars(stmt)]
