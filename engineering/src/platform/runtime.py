"""持久化队列的单worker运行时，支持中断标记与显式重跑。

worker进程锁覆盖同一SQLite工作目录；不允许两个应用同时恢复或领取任务。
耗时LLM请求设30秒超时，图数据规模由契约限制，所有任务串行执行。
"""
import fcntl
import logging
import os
from pathlib import Path
from threading import Event, Thread

from src.agents.gateway import LLMGateway
from src.platform.errors import InputIssues

from .schemas import AssistantRequest, DecisionRequest, RecomputeRequest, ScenarioRequest
from .store import Store
from .authz import authorize_assessment, authorize_snapshot, require_permission, resolve_actor
from .provenance import with_trusted_assessment_provenance

logger = logging.getLogger(__name__)


class _AuthorizedLegacySearch:
    """No implicit project-directory corpus, and no cached authority after revoke."""

    def __init__(self, actor_id: str, scope: str):
        self.actor_id, self.scope = actor_id, scope

    def search(self, query: str):
        principal = resolve_actor(self.actor_id, self.scope)
        require_permission(principal, "run")
        root = os.getenv("WATER_LEGACY_MATERIALS_ROOT", "").strip()
        if "legacy-materials" not in principal.corpora or not root:
            return []
        from src.agents.materials import MaterialSearch
        # Server configuration is the only root authority. Never default to the
        # code/project root, and never accept a client path or stale index cache.
        citations = MaterialSearch(Path(root).resolve()).search(query)
        return [{**citation, "corpus_id": "legacy-materials"} for citation in citations]


class Runtime:
    def __init__(self, store: Store, source_root: Path):
        self.store, self.source_root = store, source_root
        self.gateway = LLMGateway()
        self.decision_provider = None
        self.professional_providers = None
        self.search = None
        self.stopping = Event()
        self.thread = None
        self.lockfile = None

    def start(self):
        self.lockfile = (self.store.runtime / "worker.lock").open("a+")
        try:
            fcntl.flock(self.lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lockfile.close()
            self.lockfile = None
            raise RuntimeError("此工作目录已有worker运行；请使用单个API进程") from None
        self.store.recover_interrupted()
        self.stopping.clear()
        self.thread = Thread(target=self.loop, name="water-worker", daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=40)
            if self.thread.is_alive():
                logger.error("任务尚未退出，保持worker独占锁直至进程结束")
                return
        if self.lockfile:
            fcntl.flock(self.lockfile, fcntl.LOCK_UN)
            self.lockfile.close()
            self.lockfile = None

    def loop(self):
        while not self.stopping.is_set():
            if not self.run_one():
                self.stopping.wait(0.3)

    def run_one(self):
        task = self.store.claim()
        if task is None:
            return False
        try:
            result = self.execute(task)
        except InputIssues as exc:
            result = {"status": "insufficient_data", "missing_items": exc.issues}
        except Exception as exc:
            result = {"status": "failed", "reason": "计算失败，请核对输入或服务配置", "error_type": type(exc).__name__}
        result["task_version"] = "0.1.0"
        try:
            self.store.finish(task["run_id"], result)
        except Exception:
            # 不伪造成功；无法落盘时终止领取新任务，下次启动将running标为failed。
            logger.exception("任务结果无法持久化，worker已停止领取新任务")
            self.stopping.set()
        return True

    def execute(self, task):
        kind, body = task["kind"], task["request"]
        actor_id, scope = self.store.task_owner(task["run_id"])
        principal = resolve_actor(actor_id, scope)
        require_permission(principal, "run")
        if kind == "assessment":
            request = RecomputeRequest.model_validate(body)
            trusted = authorize_assessment(principal, request.model_dump(mode="json"))
            request = with_trusted_assessment_provenance(request, trusted)
            from .services import calculate_assessment

            snapshot = calculate_assessment(request)
            saved = self.store.save_snapshot(snapshot, "recompute:" + task["run_id"])
            return {"status": "succeeded", "snapshot_id": saved["snapshot_id"],
                    "data_mode": snapshot.data_mode, "model_family": "legacy_demo",
                    "model_version": "0.1.1", "assessment_count": len(snapshot.assessments),
                    "validation_status": "not_business_validated",
                    "evidence_refs": ["snapshot:" + saved["snapshot_id"]]}
        snapshot = self.store.get_snapshot(body["snapshot_id"])
        authorize_snapshot(principal, snapshot, store=self.store)
        if kind == "simulation":
            from .services import simulate_snapshot

            return simulate_snapshot(snapshot, ScenarioRequest.model_validate(body))
        if kind == "decision":
            if self.decision_provider is None:
                from src.adapters.providers import DemoDecisionProvider

                self.decision_provider = DemoDecisionProvider()
            from .services import compare_snapshot
            return compare_snapshot(snapshot, DecisionRequest.model_validate(body), self.decision_provider)
        if kind == "assistant":
            if self.gateway.mode != "mock":
                raise PermissionError("旧助手仅支持本地mock；真实模型必须经Agent受控平台合同")
            from src.agents.workflow import run_assistant

            if self.decision_provider is None:
                from src.adapters.providers import DemoDecisionProvider

                self.decision_provider = DemoDecisionProvider()
            result = run_assistant(snapshot, AssistantRequest.model_validate(body), self.gateway,
                                   _AuthorizedLegacySearch(principal.actor_id, principal.scope),
                                   decision_function=self.decision_provider.compare)
            result["required_corpora"] = ["legacy-materials"] if result.get("citations") else []
            return result
        raise ValueError("未知任务类型")
