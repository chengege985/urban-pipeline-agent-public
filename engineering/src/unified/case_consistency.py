"""UNIFY-PRODUCT-01：同一案例在不同入口的一致性指纹。

入口只允许三类来源：页面读取（数据集视图）、后端算法（Run 结果）、受控工具（Agent）。
本模块只做字段提取与比较，不执行任何业务计算，也不把不一致自动修正为一致。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


@dataclass(frozen=True)
class PipeCase:
    """单管段评分指纹：同一 pipe_id 在同一模型族/版本下必须给出同一数值与证据。"""

    pipe_id: str
    model_family: str
    model_version: str
    rs: float | None
    hs: float | None
    grade: str
    completeness_percent: float | None
    evidence_refs: tuple[str, ...]

    @classmethod
    def from_assessment(cls, row: dict[str, Any]) -> "PipeCase":
        legacy = row.get("legacy_result") or {}
        return cls(
            pipe_id=str(row["pipe_id"]), model_family=str(row.get("model_family")),
            model_version=str(row.get("model_version")), rs=_number(legacy.get("Rs")),
            hs=_number(legacy.get("Hs")), grade=str(legacy.get("grade")),
            completeness_percent=_number(legacy.get("completeness_percent")),
            evidence_refs=tuple(row.get("evidence_refs") or ()),
        )

    @classmethod
    def from_dataset_view(cls, pipe: dict[str, Any]) -> "PipeCase":
        """数据集视图只暴露已合法评分的管段；缺失时不得构造占位值。"""
        score = pipe.get("score")
        if not isinstance(score, dict):
            raise ValueError("该管段在数据集视图里没有可用评分：" + str(pipe.get("pipe_id")))
        return cls(
            pipe_id=str(pipe["pipe_id"]), model_family=str(score.get("model_family")),
            model_version=str(score.get("model_version")), rs=_number(score.get("rs")),
            hs=_number(score.get("hs")), grade=str(score.get("grade")),
            completeness_percent=_number(score.get("completeness_percent")),
            evidence_refs=tuple(score.get("evidence_refs") or ()),
        )

    @classmethod
    def from_tool(cls, tool_output: dict[str, Any], pipe_id: str) -> "PipeCase":
        """工具结果中的 assessment 原样引用，工具本身不重算。"""
        assessment = (tool_output.get("result") or {}).get("assessment")
        if not isinstance(assessment, dict):
            raise ValueError("工具结果没有 assessment，不能用于一致性比较")
        if assessment.get("pipe_id") != pipe_id:
            raise ValueError("工具返回的管段与请求不一致")
        return cls.from_assessment(assessment)

    def identity(self) -> tuple[str, str, str]:
        return (self.pipe_id, self.model_family, self.model_version)

    def values(self) -> tuple[float | None, float | None, str, float | None]:
        return (self.rs, self.hs, self.grade, self.completeness_percent)

    def evidence(self) -> tuple[str, ...]:
        return self.evidence_refs


@dataclass(frozen=True)
class ImpactCase:
    """拓扑影响指纹：单位计数缺失保持 None，不参与相等比较时补零。"""

    status: str
    algorithm_version: str
    affected_service_ids: tuple[str, ...]
    critical_service_ids: tuple[str, ...]
    counts: tuple[tuple[str, int | None], ...]
    isolated_valve_ids: tuple[str, ...]
    snapshot_ref: str | None

    @classmethod
    def from_simulation_result(cls, result: dict[str, Any]) -> "ImpactCase":
        counts = result.get("potentially_disconnected_counts") or {}
        refs = [ref for ref in (result.get("evidence_refs") or []) if isinstance(ref, str)]
        return cls(
            status=str(result.get("status")), algorithm_version=str(result.get("algorithm_version")),
            affected_service_ids=tuple(sorted(result.get("affected_service_ids") or ())),
            critical_service_ids=tuple(sorted(result.get("affected_critical_facility_ids") or ())),
            counts=tuple((unit, counts.get(unit)) for unit in sorted(counts)),
            isolated_valve_ids=tuple(sorted(result.get("isolated_valve_ids") or ())),
            snapshot_ref=next((ref for ref in refs if ref.startswith("snapshot:")), None),
        )


@dataclass(frozen=True)
class TimelineCase:
    """时间线指纹：区分任务完成、服务恢复与未在窗口内恢复。"""

    algorithm_version: str
    criterion_id: str | None
    criterion_version: str | None
    plan_completion: tuple[str, float | None]
    first_target: tuple[str, float | None]
    all_targets: tuple[str, float | None]
    hospital_first: tuple[str, float | None]
    event_evidence: tuple[str, ...]

    @classmethod
    def from_evaluation(cls, response: dict[str, Any]) -> "TimelineCase":
        result = response.get("result") or {}
        recovery = result.get("recovery_times") or {}
        algorithm = response.get("algorithm") or {}

        def metric(value: Any) -> tuple[str, float | None]:
            if not isinstance(value, dict):
                return ("missing", None)
            return (str(value.get("status")), _number(value.get("value_hours")))

        per_service = recovery.get("service_first_restored_hours") or {}
        timeline_request = result.get("timeline_request") or {}
        return cls(
            algorithm_version=str(algorithm.get("recovery_summary_version") or algorithm.get("version")),
            criterion_id=algorithm.get("criterion_id"),
            criterion_version=algorithm.get("criterion_version"),
            plan_completion=metric(recovery.get("plan_completion_hours")),
            first_target=metric(recovery.get("first_target_service_restored_hours")),
            all_targets=metric(recovery.get("all_target_services_restored_hours")),
            hospital_first=metric(per_service.get("syn-hospital-001")),
            event_evidence=tuple(timeline_request.get("event_evidence_refs") or ()),
        )


def compare_pipe_cases(*cases: tuple[str, PipeCase]) -> dict[str, Any]:
    """比较同管段的多入口指纹；返回差异明细，而不是断言。"""
    if len(cases) < 2:
        raise ValueError("至少需要两个入口才能比较")
    reference_name, reference = cases[0]
    rows, consistent = [], True
    for name, case in cases:
        same_identity = case.identity() == reference.identity()
        same_values = case.values() == reference.values()
        same_evidence = case.evidence() == reference.evidence()
        ok = same_identity and same_values and same_evidence
        consistent = consistent and ok
        rows.append({
            "entry": name, "pipe_id": case.pipe_id, "model_family": case.model_family,
            "model_version": case.model_version, "rs": case.rs, "hs": case.hs,
            "grade": case.grade, "completeness_percent": case.completeness_percent,
            "evidence_refs": list(case.evidence()),
            "same_identity": same_identity, "same_values": same_values,
            "same_evidence": same_evidence, "consistent": ok,
        })
    return {"consistent": consistent, "reference": reference_name, "entries": rows}
