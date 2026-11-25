"""Select bounded evidence from the current authorized pilot replay and asset."""

from __future__ import annotations

from collections import Counter
import re
from typing import Literal

from .data_package import Identifier
from .pilot_contract import Contract, PilotEvent, PilotObservation, PilotSource, PilotThreshold
from .pilot_replay import PilotReplayResult, classify

PilotQuestion = Literal["observation", "alarm", "threshold", "quality", "version_change", "asset_events"]


class PilotCitation(Contract):
    kind: Literal["observation", "sensor", "asset", "threshold", "event"]
    record_id: Identifier
    source: PilotSource
    threshold_version: Identifier | None = None
    detail: str | None = None


def question_focus(question: str) -> PilotQuestion:
    if re.search(r"换版|切换.*阈值|阈值.*(?:变化|变更|切换)|比较.*阈值|新旧.*阈值", question):
        return "version_change"
    if re.search(r"可疑|质量|无效|缺测", question):
        return "quality"
    if re.search(r"(?:管段|资产).*(?:事件|工单)|(?:事件|工单).*关联.*(?:管段|资产)", question):
        return "asset_events"
    if re.search(r"告警|报警|触发|持续越限|为何越限", question):
        return "alarm"
    if re.search(r"哪个阈值|阈值(?:版本|来自|是多少|的版本)|使用.*阈值", question):
        return "threshold"
    return "observation"


def describe_observation(row: PilotObservation) -> str:
    value = "原始值缺失" if row.value is None else f"原始值 {row.value:g} {row.unit}"
    return f"观测 {row.observation_id}，时点 {row.observed_at.isoformat()}，{value}，质量 {row.quality}"


def observation_citation(row: PilotObservation) -> PilotCitation:
    return PilotCitation(kind="observation", record_id=row.observation_id, source=row.source,
                         detail=describe_observation(row) + "。")


def event_fact(event: PilotEvent) -> str:
    return f"关联事件 {event.event_id}，状态 {event.status}，证据记录 {event.evidence_ref}。"


def event_citation(event: PilotEvent) -> PilotCitation:
    return PilotCitation(kind="event", record_id=event.event_id, source=event.source,
                         detail=event_fact(event) + f"记录时间 {event.recorded_at.isoformat()}。")


def describe_threshold(threshold: PilotThreshold) -> str:
    return "、".join(label + f" {value:g} {threshold.unit}" for label, value in
                    (("下限", threshold.lower), ("上限", threshold.upper)) if value is not None)


def threshold_citations(threshold: PilotThreshold) -> list[PilotCitation]:
    period = f"生效 {threshold.effective_from.isoformat()} 至 {threshold.effective_until.isoformat()}（截止不含）。"
    if threshold.rule_inputs:
        return [
            PilotCitation(kind="threshold", record_id=rule.rule_id, source=rule.source, threshold_version=rule.version,
                          detail=f"{'上限' if rule.threshold_type == 'upper' else '下限'} "
                                 f"{rule.threshold_value:g} {rule.unit}，批准引用 {rule.evidence_reference}。" + period)
            for rule in threshold.rule_inputs
        ]
    return [PilotCitation(kind="threshold", record_id=threshold.threshold_id, source=threshold.source,
                          threshold_version=threshold.version,
                          detail=f"{describe_threshold(threshold)}，批准引用 {threshold.approval_ref}。" + period)]


def quality_fact(observation: PilotObservation) -> str:
    if observation.quality_reason:
        return f"来源将观测 {observation.observation_id} 标记为 {observation.quality}，登记原因为「{observation.quality_reason}」。"
    if observation.quality == "valid":
        return f"源记录把观测 {observation.observation_id} 标为有效。"
    return f"源记录标记为 {observation.quality}，没有提供原因，证据不足以判断具体成因。"


def alarm_trace(
    report: PilotReplayResult, observation: PilotObservation, threshold: PilotThreshold | None,
) -> tuple[list[str], list[PilotCitation]]:
    trigger = next((event for segment in report.segments for event in segment.channel.events
                    if event.kind == "limit_exceeded" and event.at == observation.observed_at), None)
    if trigger is None or threshold is None:
        status = "该观测为单点越限。" if classify(observation, threshold) == "anomaly" else ""
        return [status + "所选回放窗口内没有该观测触发持续越限的证据。"], []
    samples = {sample.observed_at: sample for sample in trigger.evidence}
    support = [
        point.observation for point in report.observations
        if point.observation.observed_at in samples
        and point.observation.source.source_record_id == samples[point.observation.observed_at].source_record_id
    ]
    direction = ("超过上限" if trigger.cause == "above_upper" else "低于下限")
    facts = [f"阈值版本 {threshold.version} 要求连续 {threshold.sustained_samples} 个有效样本"
             f"{direction}（{describe_threshold(threshold)}）。回放在 {trigger.at.isoformat()} 记录持续越限，"
             f"对应观测 {observation.observation_id}，触发证据含 {len(support)} 条原始观测。"]
    duplicate = next((item for item in report.validation.duplicate_alarms if item.alarm.at == trigger.at), None)
    if duplicate:
        facts.append(f"该越限已在版本 {duplicate.original.threshold_version} 的 "
                     f"{duplicate.original.at.isoformat()} 记录，换版回放保留此证据并已去重。")
    return facts, [observation_citation(row) for row in support]


def version_trace(report: PilotReplayResult) -> tuple[list[str], list[PilotCitation]]:
    if len(report.segments) < 2:
        return ["所选窗口内适用的阈值版本少于两个，证据不足以比较换版结果。"], []
    segments = report.segments[:12]
    duplicate = Counter(item.alarm.threshold_version for item in report.validation.duplicate_alarms)
    facts = [f"在各版本生效时段分别回放，共 {len(report.segments)} 个版本，"
             f"连续越限换版去重 {len(report.validation.duplicate_alarms)} 条。"]
    if len(segments) < len(report.segments):
        facts.append("以下列出前 12 个适用版本，完整版本及事件见历史回放。")
    citations = []
    for segment in segments:
        threshold = segment.threshold
        count = Counter(event.kind for event in segment.channel.events)
        facts.append(f"版本 {threshold.version} 从 {threshold.effective_from.isoformat()} 生效，"
                     f"{describe_threshold(threshold)}。该时段新增持续越限 "
                     f"{count['limit_exceeded'] - duplicate[threshold.version]} 次，"
                     f"缺测 {count['data_missing']} 次，恢复 {count['recovered']} 次，"
                     f"重复越限 {duplicate[threshold.version]} 次。")
        citations.extend(threshold_citations(threshold))
    return facts, citations
