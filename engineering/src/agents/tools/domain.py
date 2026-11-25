"""Agent的确定性工具协议，不依赖LLM、LangGraph或外部数据库。

授权上下文由服务端构造，模型只能从已授予工具中选择，并提交小规模引用参数。
权限、预算、情景、快照、时间窗不由工具调用的自由JSON决定。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Callable

from pydantic import ConfigDict, Field

from src.platform.errors import InputIssues
from src.adapters.explanations import explain_legacy_health
from src.platform.input_batches import BatchPrincipal, InputBatchRepository
from src.platform.schemas import Contract, DecisionRequest, ID, ScenarioRequest, SnapshotPayload
from src.platform.services import calculate_assessment, compare_snapshot, query_assessment, simulate_snapshot
from src.platform.store import fingerprint


class PipeArguments(Contract):
    pipe_id: ID


class BatchArguments(Contract):
    batch_ref: str = Field(pattern=r"^batch-[0-9a-f]{64}$")


class NoArguments(Contract):
    """情景与方案已经由用户在服务端上下文中确定，不接受模型改写预算等参数。"""


class ToolAccess(Contract):
    """服务端认证及业务校验后的授权。默认没有任何工具或批次访问权限。"""
    model_config = ConfigDict(extra="forbid", frozen=True)
    principal: BatchPrincipal
    snapshot_id: ID | None = None
    allowed_tools: frozenset[str] = frozenset()
    batch_refs: frozenset[str] = frozenset()
    scenario: ScenarioRequest | None = None
    decision: DecisionRequest | None = None


TOOL_SCHEMAS = {
    "query_assessment": (PipeArguments, "查询当前已授权快照中的指定管段评分，不执行重新计算。"),
    "validate_pipe_data": (BatchArguments, "校验已登记的完整输入批次与时间窗，不补造缺失值。"),
    "calculate_health": (BatchArguments, "使用已登记输入批次调用0.1.1评分，返回真实计算结果。"),
    "simulate_outage": (NoArguments, "按已确认情景试算新增通路影响，不计算实际缺供量或概率。"),
    "compare_action_plans": (NoArguments, "按已确认的候选、预算和目标配置比较方案，不下发工单。"),
}


class DomainTools:
    """支持后续LangChain绑定，当前可独立验证权限、数据引用和公式一致性。

    传入 ``ledger`` 与 ``run_id`` 时，本类即唯一可信执行器：只有真实调用返回后才向
    执行账本登记回执。未传账本的旧调用仍可执行，但不会产生可用于认证的记录。
    """
    def __init__(self, access: ToolAccess, snapshot_lookup: Callable, batches: InputBatchRepository,
                 decision_provider=None, *, ledger=None, run_id: str | None = None):
        self.access = access.model_copy(deep=True)
        self.snapshot_lookup = snapshot_lookup
        self.batches = batches
        self.decision_provider = decision_provider
        self.ledger = ledger
        self.run_id = run_id

    def specifications(self) -> list[dict]:
        """仅发布当前任务可用的工具JSON Schema，不返回内部路径或凭据。"""
        return [{"type": "function", "function": {"name": name, "description": description,
                "parameters": schema.model_json_schema()}}
                for name, (schema, description) in TOOL_SCHEMAS.items() if name in self.access.allowed_tools]

    def _snapshot(self) -> dict:
        sid = self.access.snapshot_id
        if sid is None:
            raise InputIssues(["请先选择一个已授权的冻结快照"])
        snapshot = deepcopy(self.snapshot_lookup(sid))
        if snapshot["snapshot_id"] != sid:
            raise PermissionError("返回快照与授权上下文不一致")
        data = SnapshotPayload.model_validate(snapshot["payload"])
        if self.access.principal.scope == "competition" and data.data_mode != "synthetic":
            raise PermissionError("比赛工具不允许读取内部或来源未确认的快照")
        return snapshot

    def _batch(self, ref):
        if ref not in self.access.batch_refs:
            raise PermissionError("批次未授予当前任务")
        return self.batches.load(ref, self.access.principal)

    def execute(self, name: str, arguments: dict) -> dict:
        """无副作用执行。保存证据/结果由上层任务管理负责；非法授权直接拒绝。"""
        if name not in TOOL_SCHEMAS or name not in self.access.allowed_tools:
            raise PermissionError("工具不存在或未授予当前任务")
        parsed = TOOL_SCHEMAS[name][0].model_validate(arguments)
        refs = []
        try:
            if name == "query_assessment":
                snapshot = self._snapshot()
                result = query_assessment(snapshot, parsed.pipe_id)
                refs = ["snapshot:" + snapshot["snapshot_id"]]
            elif name in {"validate_pipe_data", "calculate_health"}:
                request = self._batch(parsed.batch_ref)
                refs = [parsed.batch_ref]
                if name == "validate_pipe_data":
                    from src.adapters.scoring import validate_recompute
                    static, dynamic = validate_recompute(request)
                    result = {"status": "succeeded", "valid": True, "static_rows": len(static),
                              "dynamic_rows": len(dynamic), "as_of": request.as_of.isoformat(),
                              "window_start": request.window_start.isoformat(), "window_end": request.window_end.isoformat()}
                else:
                    scores = calculate_assessment(request)
                    result = {"status": "succeeded", "model_version": "0.1.1", "model_family": "legacy_demo",
                              "as_of": scores.as_of.isoformat(), "data_mode": scores.data_mode,
                              "assessments": [a.model_dump(mode="json") for a in scores.assessments],
                              "persisted": False, "validation_status": "not_business_validated"}
            elif name == "simulate_outage":
                if self.access.scenario is None:
                    raise InputIssues(["缺少已确认的故障情景与隔离方案"])
                result = simulate_snapshot(self._snapshot(), self.access.scenario.model_copy(deep=True))
            else:
                if self.access.decision is None:
                    raise InputIssues(["缺少已确认的候选方案、预算、资源或目标配置"])
                result = compare_snapshot(self._snapshot(), self.access.decision.model_copy(deep=True), self.decision_provider)
        except InputIssues as exc:
            result = {"status": "insufficient_data", "missing_items": exc.issues}
        except KeyError:
            result = {"status": "insufficient_data", "missing_items": ["数据引用不存在或当前主体无权访问"]}
        result = deepcopy(result)
        # Add derived explanations to the tool envelope only; frozen snapshots are unchanged.
        assessments = result.get("assessments", [])
        if "assessment" in result:
            assessments = [result["assessment"]]
        if assessments:
            result["explanations"] = {
                row["pipe_id"]: explain_legacy_health(row.get("indicator_scores", {}), row.get("model_version", "0.1.1"))
                for row in assessments
            }
        refs += result.get("evidence_refs", [])
        identity = {"tool": name, "version": "domain-tools-1", "args": parsed.model_dump(mode="json"),
                    "context": self.access.model_dump(mode="json"), "result": result}
        # frozenset序列化顺序不应影响同一授权的证据ID。
        identity["context"]["allowed_tools"] = sorted(self.access.allowed_tools)
        identity["context"]["batch_refs"] = sorted(self.access.batch_refs)
        eid = "tool:" + fingerprint(identity)
        envelope = {"tool_name": name, "tool_version": "domain-tools-1", "status": result["status"],
                    "result": result, "evidence_id": eid, "evidence_refs": list(dict.fromkeys([eid] + refs)),
                    "scope": self.access.principal.scope, "execution_status": "not_dispatched"}
        # 只在此处登记：工具已真实执行并拿到返回；失败/异常路径不会到达。
        if self.ledger is not None and self.run_id:
            try:
                self.ledger._register(run_id=str(self.run_id), snapshot_id=self.access.snapshot_id,
                                      principal_id=self.access.principal.actor_id,
                                      tool_result=envelope, arguments=parsed.model_dump(mode="json"))
            except (AttributeError, TypeError, ValueError):
                # 账本不可用时不影响工具返回，但也不会产生可信回执。
                pass
        return envelope
