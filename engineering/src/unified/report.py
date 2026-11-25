"""UNIFY-PRODUCT-01：从既有 Run 组装报告投影（不重算业务数值）。

约束：

- 只接受已进入终态的 Run；`queued`/`running` 直接拒绝，不用空结果冒充实测。
- 评分、影响、方案、时间线数值全部逐字段搬运，`recomputation="none"`。
- 缺少对应结果时字段为 None 并在 limitations 说明，不补零、不重新计算。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from src.unified.contracts import (
    ReportAlgorithmRef,
    ReportAssessmentRef,
    ReportDocument,
    ReportEvidenceRef,
    parse_run_result,
)

TERMINAL = {"succeeded", "partial", "insufficient_data", "infeasible", "failed"}
EVIDENCE_KINDS = ("snapshot", "scenario", "isolation", "plan", "action", "run", "rule", "algorithm")
BASE_LIMITATIONS = [
    "报告只引用既有 Run 与冻结快照，未重新计算健康度、拓扑影响、方案效用或恢复时间。",
    "legacy_demo_0.1.1 的 Rs/Hs 是简化演示评分，不是正式 P/H、失效概率或 RUL。",
    "拓扑通路结果不代表压力、流量、水质达标或实际供水量。",
    "本报告不是业务验收、预算审批、派单或关阀依据。",
]


class ReportNotReady(RuntimeError):
    """Run 未完成或不含可用结果，不能生成报告。"""


def _evidence_id(raw: str) -> ReportEvidenceRef | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    head = raw.partition(":")[0]
    if head not in EVIDENCE_KINDS:
        return None
    return ReportEvidenceRef(id=raw, kind=head)


def _algorithms(run: dict[str, Any], result: dict[str, Any]) -> list[ReportAlgorithmRef]:
    """只登记本次实际使用、且在结果中声明的算法版本。"""
    rows: list[ReportAlgorithmRef] = []
    if run.get("kind") == "simulation" and result.get("engine_type") == "topology":
        rows.append(ReportAlgorithmRef(
            capability_id="given_isolation_topology", semantics="topological_reachability",
            version=str(result.get("algorithm_version")),
            validation_status=str(result.get("validation_status"))))
    if run.get("kind") == "decision":
        rows.append(ReportAlgorithmRef(
            capability_id="candidate_plan_ranking", semantics="given_candidates_only",
            version=str(result.get("decision_algorithm_version")),
            validation_status=str(result.get("validation_status"))))
    if run.get("kind") == "assessment":
        rows.append(ReportAlgorithmRef(
            capability_id="legacy_health", semantics="legacy_demo_0.1.1",
            version=str(result.get("model_version")),
            validation_status=str(result.get("validation_status"))))
    return rows


def fault_pipe_id(snapshot: dict[str, Any], request: dict[str, Any]) -> str | None:
    """故障边→管段为结构映射（一条边只属于一个管段），不涉及任何业务计算。"""
    edge_id = request.get("fault_edge_id")
    if not isinstance(edge_id, str):
        return None
    for edge in snapshot["payload"].get("edges", []):
        if edge.get("edge_id") == edge_id:
            pipe_id = edge.get("pipe_id")
            return pipe_id if isinstance(pipe_id, str) else None
    return None


def _assessment(snapshot: dict[str, Any], pipe_id: str | None) -> ReportAssessmentRef | None:
    if pipe_id is None:
        return None
    row = next((item for item in snapshot["payload"].get("assessments", [])
                if item.get("pipe_id") == pipe_id), None)
    if row is None:
        return None
    legacy = row.get("legacy_result") or {}
    return ReportAssessmentRef(
        pipe_id=pipe_id, model_family=str(row.get("model_family")),
        model_version=str(row.get("model_version")),
        rs=legacy.get("Rs"), hs=legacy.get("Hs"), grade=str(legacy.get("grade")),
        completeness_percent=legacy.get("completeness_percent"),
        evidence_refs=list(row.get("evidence_refs") or []), recomputed=False,
    )


def _validate_result_identity(run: dict[str, Any], snapshot: dict[str, Any], result: dict[str, Any], *, timeline=None, feedback=None) -> None:
    from src.unified.report_integrity import IntegrityConflict, validate_report_integrity
    try:
        validate_report_integrity(run, snapshot, timeline=timeline, feedback=feedback)
    except IntegrityConflict as exc:
        raise ReportNotReady(str(exc)) from None


def build_report_from_run(
    run: dict[str, Any],
    snapshot: dict[str, Any],
    *,
    timeline: dict[str, Any] | None = None,
    feedback: list[dict[str, Any]] | None = None,
) -> ReportDocument:
    """组装报告；任何业务数值都来自 run.result 或快照原文。"""
    status = run.get("status")
    if status not in TERMINAL:
        raise ReportNotReady(f"Run 尚未结束：{status}")
    result = run.get("result")
    if not isinstance(result, dict):
        raise ReportNotReady("Run 没有可引用的结果")
    request = run.get("request") if isinstance(run.get("request"), dict) else {}
    expected_snapshot = request.get("snapshot_id")
    if expected_snapshot is None and run.get("kind") == "assessment":
        expected_snapshot = result.get("snapshot_id")
    if expected_snapshot != snapshot.get("snapshot_id"):
        raise ReportNotReady("Run 请求与所给快照不一致")
    _validate_result_identity(run, snapshot, result, timeline=timeline, feedback=feedback)
    if parse_run_result(run) is None:
        raise ReportNotReady("该 Run 类型没有统一的报告投影")

    pipe = _assessment(snapshot, fault_pipe_id(snapshot, request))
    algorithms = _algorithms(run, result)

    evidence: list[str] = []
    for raw in [*(result.get("evidence_refs") or []), *((pipe.evidence_refs if pipe else []) or [])]:
        if isinstance(raw, str) and raw not in evidence:
            evidence.append(raw)
    evidence.append("run:" + str(run.get("run_id")))
    refs = [ref for ref in (_evidence_id(raw) for raw in evidence) if ref is not None]

    limitations = list(dict.fromkeys([*BASE_LIMITATIONS, *(result.get("limitations") or [])]))
    if pipe is None:
        limitations.append("该 Run 没有对应管段的评分记录，报告不补造评分。")
    if timeline is not None:
        limitations.append("附加时间线仅核对快照与情景合同，未认证为本Run的持久化工具执行回执。")
    if timeline is None:
        limitations.append("本次报告未附服务时间线结果。")

    payload = snapshot["payload"]
    identity = {
        "run_id": run.get("run_id"), "snapshot_id": snapshot.get("snapshot_id"),
        "data_version": snapshot.get("sha256"), "status": status,
        "algorithms": [(item.capability_id, item.version) for item in algorithms],
    }
    from src.platform.store import fingerprint

    return ReportDocument(
        report_id="report-" + fingerprint(identity),
        business_status=str(status),
        run_id=str(run.get("run_id")), run_kind=str(run.get("kind")), run_status=status,
        dataset={
            "snapshot_id": snapshot.get("snapshot_id"), "data_mode": snapshot.get("data_mode"),
            "source_status": snapshot.get("source_status"), "as_of": snapshot.get("as_of"),
            "data_version": snapshot.get("sha256"),
            "schema_version": payload.get("schema_version") or "0.1.0",
            "name": snapshot.get("name"),
        },
        algorithms=algorithms,
        scenario=deepcopy(request) if request else None,
        pipe=pipe,
        simulation=deepcopy(result) if run.get("kind") == "simulation" else None,
        decision=deepcopy(result) if run.get("kind") == "decision" else None,
        timeline=deepcopy(timeline) if timeline is not None else None,
        feedback=[dict(item) for item in (feedback or [])],
        evidence_ids=[ref.id for ref in refs],
        limitations=limitations,
    )
