"""Dataset-scoped read routes for a controlled gas pilot."""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import Field

from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import FORMAL_RUNTIME_MODES, GENERIC_DENIAL, Principal, authorize_dataset, require_authenticated

from .agent_evidence import ENGINEERING_CLAIM, GAS_M0_REFERENCE, PILOT_REFERENCE, GasAgentEvidenceScope
from .data_package import Digest, Identifier, IntakeError
from .model_provider import MODEL_ID, LocalGasModel, ModelUnavailable
from .pilot_contract import (
    Contract, PilotGeometry, PilotGrant, PilotIdentity, PilotLine, PilotPipe, PilotSensor, PilotSnapshot, PilotSource, UtcTime,
)
from .pilot_evidence import (
    PilotCitation, PilotQuestion, alarm_trace, describe_observation, describe_threshold, event_citation, event_fact,
    observation_citation, quality_fact, question_focus, threshold_citations, version_trace,
)
from .pilot_quality import PilotQualityReport, PilotRejectedQuality
from .pilot_replay import MAX_WINDOW, PilotReplayRequest, PilotReplayResult, classify, effective_policy, replay, sensor_policies
from .pilot_store import PilotDatasetEntry, PilotStore

_SECURITY = {"security": [{"WaterToken": []}]}
_OUTSIDE_READ = re.compile(r"开阀|关阀|阀门控制|关闭阀门|打开阀门|派单|复气|处置|执行|CUV|推演|预测|失效概率|计算.*(?:压力|流量)", re.I)
_DEMO_THRESHOLD_VERSION = re.compile(r"(?<![A-Za-z0-9_.-])pilot-r\d+(?![A-Za-z0-9_.-])", re.I)


class PilotCatalogue(Contract):
    configured: bool
    items: tuple[PilotDatasetEntry, ...]
    rejections: tuple[PilotRejectedQuality, ...] = ()


class PilotAccess(Contract):
    status: Literal["synthetic", "authorized"]
    valid_from: UtcTime | None = None
    valid_until: UtcTime | None = None
    asset_geometry: bool
    local_qwen: bool


class PilotDataset(PilotDatasetEntry):
    sensor_records: tuple[PilotSensor, ...]
    access: PilotAccess
    quality: PilotQualityReport
    engineering_analysis: Literal["unavailable"] = "unavailable"


class PilotMappedLine(Contract):
    geometry_id: Identifier
    source_crs: str
    scope_ref: Identifier
    geometry: PilotLine
    source: PilotSource


class PilotPipeDetail(PilotIdentity):
    pipe: PilotPipe
    geometry_status: Literal["not_requested", "outside_scope", "not_available", "available"]
    geometries_wgs84: tuple[PilotMappedLine, ...]


class PilotQueryBinding(Contract):
    manifest_sha256: Digest
    threshold_version: Identifier | None
    window_start: UtcTime | None = None
    window_end: UtcTime | None = None


class PilotAgentRequest(Contract):
    observation_id: Identifier
    question: Annotated[str, Field(min_length=1, pattern=r"\S", max_length=1200)]
    binding: PilotQueryBinding | None = None


class PilotAgentResult(PilotIdentity):
    evidence_scope: GasAgentEvidenceScope
    blocked_reason: Literal["cross_dataset_unverified", "reference_outside_scope", "engineering_evidence_unavailable"] | None = None
    observation_id: Identifier
    status: Literal["succeeded", "unavailable"]
    parser_source: Literal["local_rule_not_llm", "qwen_evidence_selection"]
    asset_id: Identifier
    focus: PilotQuestion = "observation"
    model_state: Literal["online", "fallback", "unavailable"] = "unavailable"
    fallback_reason: Literal["timeout", "model_response_unavailable"] | None = None
    model_id: str | None = None
    model_calls: int = 0
    answer: str
    evidence: tuple[PilotSource, ...]
    event_ids: tuple[Identifier, ...] = ()
    threshold_version: Identifier | None = None
    citations: tuple[PilotCitation, ...] = ()


def _store() -> PilotStore | None:
    directory = os.getenv("GAS_PILOT_STORE", "").strip()
    if not directory:
        return None
    try:
        return PilotStore(Path(directory))
    except IntakeError:
        raise HTTPException(503, "试点存储不可用") from None


def _grant_for_reader(store: PilotStore, identity: PilotIdentity, ref: str | None, viewer: Principal) -> PilotGrant | None:
    if identity.data_mode == "real_authorized" and (
        viewer.actor_id == "dev-token" or os.getenv("WATER_RUNTIME_MODE", "").strip().lower() not in FORMAL_RUNTIME_MODES
    ):
        raise HTTPException(403, GENERIC_DENIAL)
    try:
        grant = store.approved(identity, ref)
    except IntakeError:
        raise HTTPException(403, GENERIC_DENIAL) from None
    if grant is not None and viewer.actor_id not in grant.subjects:
        raise HTTPException(403, GENERIC_DENIAL)
    return grant


def _approved(store: PilotStore, entry: PilotDatasetEntry, ref: str | None, viewer: Principal) -> PilotGrant | None:
    grant = _grant_for_reader(store, entry, ref, viewer)
    try:
        store.ensure_admitted(entry, grant)
    except IntakeError:
        raise HTTPException(409, "试点版本已隔离或授权范围已变更，请重新选择") from None
    return grant


def _scope(request: Request, response: Response, dataset_id: str, version: str):
    viewer = require_authenticated(request)
    if viewer.scope != "internal" or dataset_id not in (viewer.datasets or ()):
        raise HTTPException(403, GENERIC_DENIAL)
    response.headers["Cache-Control"] = "no-store"
    store = _store()
    if store is None:
        raise HTTPException(404, "试点入口未配置")
    try:
        found = store.entry(dataset_id, version)
    except IntakeError:
        raise HTTPException(503, "试点存储不可用") from None
    if found is None:
        raise HTTPException(404, "试点版本不存在")
    entry, ref = found
    authorize_dataset(viewer, dataset_id, entry.data_mode)
    grant = _approved(store, entry, ref, viewer)
    try:
        return entry, store.snapshot(entry), grant, store
    except IntakeError as error:
        if error.code in {"dataset_quarantined", "dataset_requires_readmission"}:
            raise HTTPException(409, "试点版本已隔离或授权范围已变更，请重新选择") from None
        raise HTTPException(503, "试点存储不可用") from None


def _identity(snapshot: PilotSnapshot) -> dict:
    return snapshot.model_dump(include=set(PilotIdentity.model_fields))


def _line(row: PilotGeometry) -> PilotMappedLine:
    return PilotMappedLine(geometry_id=row.geometry_id, source_crs=row.crs, scope_ref=row.scope_ref,
                           geometry=row.wgs84(), source=row.source)


def _explain(snapshot: PilotSnapshot, grant: PilotGrant | None, request: PilotAgentRequest) -> PilotAgentResult:
    if snapshot.data_mode == "real_authorized" and (grant is None or "local_qwen" not in grant.uses):
        raise HTTPException(403, "当前授权用途未开放试点智能体查询")
    identity = _identity(snapshot)
    observation = next((row for row in snapshot.observations if row.observation_id == request.observation_id), None)
    if observation is None:
        raise HTTPException(404, "试点观测不存在")
    sensor = next(row for row in snapshot.sensors if row.sensor_id == observation.sensor_id)
    pipe = next(row for row in snapshot.pipes if row.pipe_id == sensor.pipe_id)
    threshold = effective_policy(sensor_policies(snapshot, sensor.sensor_id), observation.observed_at)
    threshold_version = threshold.version if threshold else None
    binding = request.binding
    if (binding is None and snapshot.data_mode == "real_authorized"
        or binding is not None and (binding.manifest_sha256 != snapshot.manifest_sha256
                                     or binding.threshold_version != threshold_version)):
        raise HTTPException(409, "会话所选数据集或阈值版本已失效，请重新选择观测")
    start, end = (binding.window_start, binding.window_end) if binding else (None, None)
    if (start is None) != (end is None):
        raise HTTPException(422, "请同时提供回放窗口的起止时间")
    if start is not None and end is not None:
        if end - start > MAX_WINDOW or end < start:
            raise HTTPException(422, "回放证据窗口须按时间顺序，最长七天")
        if grant and not grant.history_from <= start <= end < grant.history_to:
            raise HTTPException(403, "请求回放时段超出授权范围")
        if not start <= observation.observed_at <= end:
            raise HTTPException(409, "当前观测已不在所选回放窗口")
    scope = GasAgentEvidenceScope(
        scope="pilot_history", dataset_id=snapshot.dataset_id, dataset_version=snapshot.version,
        data_mode=snapshot.data_mode, source_sha256=snapshot.manifest_sha256,
    )

    def blocked(
        reason: Literal["cross_dataset_unverified", "reference_outside_scope", "engineering_evidence_unavailable"],
        message: str,
    ) -> PilotAgentResult:
        return PilotAgentResult(
            **identity, evidence_scope=scope, observation_id=observation.observation_id, status="unavailable",
            asset_id=pipe.pipe_id, threshold_version=threshold_version, parser_source="local_rule_not_llm",
            blocked_reason=reason, answer=message, evidence=(),
        )

    gas_m0_refs = {value.casefold() for value in GAS_M0_REFERENCE.findall(request.question)}
    if gas_m0_refs - {snapshot.dataset_id.casefold()}:
        return blocked("cross_dataset_unverified",
                       "试点历史回放与 GAS-M0 合成 CUV 没有已核验的资产、事件或时点关联。请切换到 GAS-M0 查看其算例。")
    if _OUTSIDE_READ.search(request.question) or ENGINEERING_CLAIM.search(request.question):
        return blocked("engineering_evidence_unavailable",
                       "试点助手支持历史观测、阈值、资产和事件证据。实际供气、泄漏及复气结论缺少经校核的工程证据。")
    focus = question_focus(request.question)
    if focus == "asset_events":
        events = sorted((row for row in snapshot.events if pipe.pipe_id in row.pipe_ids),
                        key=lambda row: (row.recorded_at, row.event_id), reverse=True)
    else:
        events = [row for row in snapshot.events if observation.observation_id in row.observation_ids]
    facts = [
        f"试点历史回放数据集 {scope.dataset_id}，版本 {scope.dataset_version}，"
        f"来源模式 {scope.data_mode}，清单 SHA-256 {scope.source_sha256}。",
        f"{describe_observation(observation)}，测点 {sensor.sensor_id}，管段 {pipe.pipe_id}。",
    ]
    citations = [observation_citation(observation),
                 PilotCitation(kind="sensor", record_id=sensor.sensor_id, source=sensor.source),
                 PilotCitation(kind="asset", record_id=pipe.pipe_id, source=pipe.source)]
    if threshold:
        label = "合成样例规则" if snapshot.data_mode == "synthetic" else "已登记运营方阈值"
        facts.append(f"{label} {threshold.threshold_id}，版本 {threshold.version}，"
                     f"批准引用 {threshold.approval_ref}，{describe_threshold(threshold)}，"
                     f"回放分类 {classify(observation, threshold)}。")
        for rule in threshold.rule_inputs:
            facts.append(f"阈值条款 {rule.rule_id}，{rule.threshold_type} {rule.threshold_value:g} {rule.unit}，"
                         f"证据 {rule.evidence_reference}，版本 {rule.version}。")
        citations.extend(threshold_citations(threshold))
    else:
        facts.append("该观测时点暂无适用的已登记阈值，未判定越限。")

    required = []
    if focus == "asset_events":
        required.append(f"当前数据集版本中，管段 {pipe.pipe_id} 有 {len(events)} 条历史关联事件。"
                        + ("显示最新 16 条。" if len(events) > 16 else ""))
        required.extend(event_fact(row) for row in events[:16])
    elif not events:
        facts.append("当前观测没有已登记的关联事件。")
    else:
        facts.extend(event_fact(row) for row in events[:16])
    citations.extend(event_citation(row) for row in events[:16])

    if focus == "quality":
        required.append(quality_fact(observation))
        if observation.quality != "valid":
            required.append("此质量状态不作为持续越限的有效样本。")
    elif focus == "threshold" and threshold:
        required.append(f"该观测对应版本 {threshold.version}，有效区间为 "
                        f"{threshold.effective_from.isoformat()} 至 {threshold.effective_until.isoformat()}（截止不含）。")
    elif focus in {"alarm", "version_change"}:
        if start is None:
            required.append("缺少回放窗口证据，请在历史回放中打开该观测后重试。")
        else:
            selected_replay = replay(snapshot, PilotReplayRequest(sensor_id=sensor.sensor_id, start=start, end=end),
                                     allow_truth=False)
            if focus == "alarm":
                trace, references = alarm_trace(selected_replay, observation, threshold)
            else:
                trace, references = version_trace(selected_replay)
            required.extend(trace)
            citations.extend(references)

    scoped_refs = {snapshot.dataset_id, snapshot.version}
    scoped_refs.update(item.record_id for item in citations)
    scoped_refs.update(item.threshold_version for item in citations if item.threshold_version)
    mentioned_refs = PILOT_REFERENCE.findall(request.question) + _DEMO_THRESHOLD_VERSION.findall(request.question)
    if {ref.casefold() for ref in mentioned_refs} - {ref.casefold() for ref in scoped_refs}:
        return blocked("reference_outside_scope", "问题引用的数据集、版本或记录超出当前证据范围。请重新选择对应的版本和观测。")

    facts.extend(required)
    answer = "\n".join(facts)
    parser_source = "local_rule_not_llm"
    model_state = "unavailable"
    fallback_reason = None
    model_id = None
    model = None
    try:
        model = LocalGasModel.from_environment()
        if model is not None:
            selected = model.explain(facts, question=request.question)
            sentences = selected.splitlines()
            if not sentences or any(sentence not in facts for sentence in sentences):
                raise ModelUnavailable("pilot_evidence_scope_mismatch")
            answer = "\n".join(dict.fromkeys([*facts[:3], *required, *sentences]))
            parser_source = "qwen_evidence_selection"
            model_state = "online"
            model_id = MODEL_ID
    except ModelUnavailable as error:
        model_state = "fallback"
        fallback_reason = "timeout" if str(error) == "model_timeout" else "model_response_unavailable"
    citations = list({(item.kind, item.record_id): item for item in citations}.values())
    return PilotAgentResult(
        **identity, evidence_scope=scope, observation_id=observation.observation_id, asset_id=pipe.pipe_id, status="succeeded",
        focus=focus, parser_source=parser_source, model_state=model_state, fallback_reason=fallback_reason,
        model_id=model_id, model_calls=model.calls if model is not None else 0, answer=answer,
        evidence=tuple(item.source for item in citations), event_ids=tuple(row.event_id for row in events[:16]),
        threshold_version=threshold_version, citations=citations,
    )


def gas_pilot_router() -> APIRouter:
    router = APIRouter(prefix="/pilot")
    dataset_path = "/datasets/{dataset_id}/versions/{version}"

    @router.get("/datasets", response_model=PilotCatalogue, responses=COMMON_ERRORS, openapi_extra=_SECURITY)
    def pilot_catalogue(request: Request, response: Response) -> PilotCatalogue:
        viewer = require_authenticated(request)
        if viewer.scope != "internal":
            raise HTTPException(403, GENERIC_DENIAL)
        response.headers["Cache-Control"] = "no-store"
        store = _store()
        if store is None:
            return PilotCatalogue(configured=False, items=())
        try:
            datasets = viewer.datasets or frozenset()
            candidates = store.entries(datasets)
            rejected = store.rejected(datasets)
        except IntakeError:
            raise HTTPException(503, "试点存储不可用") from None
        items = []
        for entry, ref in candidates:
            if entry.data_mode not in viewer.modes or (entry.data_mode == "real_authorized" and viewer.actor_id == "dev-token"):
                continue
            try:
                _approved(store, entry, ref, viewer)
            except HTTPException:
                continue
            items.append(entry)
        rejections = []
        for attempt, ref, scope_digest in rejected:
            if attempt.data_mode not in viewer.modes:
                continue
            try:
                grant = _grant_for_reader(store, attempt, ref, viewer)
            except HTTPException:
                continue
            if grant is None or grant.scope_digest() == scope_digest:
                rejections.append(attempt)
            if len(rejections) == 10:
                break
        return PilotCatalogue(configured=True, items=tuple(items), rejections=tuple(rejections))

    @router.get(dataset_path, response_model=PilotDataset, responses=COMMON_ERRORS, openapi_extra=_SECURITY)
    def pilot_dataset(dataset_id: Identifier, version: Identifier, request: Request, response: Response) -> PilotDataset:
        entry, snapshot, grant, store = _scope(request, response, dataset_id, version)
        access = PilotAccess(status="authorized" if grant else "synthetic",
                             valid_from=grant.valid_from if grant else None,
                             valid_until=grant.valid_until if grant else None,
                             asset_geometry=grant is None or "asset_geometry" in grant.uses,
                             local_qwen=grant is None or "local_qwen" in grant.uses)
        try:
            quality = store.quality(entry)
        except IntakeError:
            raise HTTPException(503, "试点质量回执不可用") from None
        return PilotDataset(**entry.model_dump(), sensor_records=snapshot.sensors, access=access, quality=quality)

    @router.get(dataset_path + "/replay", response_model=PilotReplayResult, responses=COMMON_ERRORS, openapi_extra=_SECURITY)
    def pilot_replay(dataset_id: Identifier, version: Identifier, request: Request, response: Response,
                     window: Annotated[PilotReplayRequest, Query()]) -> PilotReplayResult:
        _, snapshot, grant, _ = _scope(request, response, dataset_id, version)
        if grant and any(value is not None and not grant.history_from <= value < grant.history_to
                         for value in (window.start, window.end)):
            raise HTTPException(403, "请求回放时段超出授权范围")
        return replay(snapshot, window, allow_truth=grant is None or "threshold_validation" in grant.uses)

    @router.get(dataset_path + "/pipes/{pipe_id}", response_model=PilotPipeDetail,
                responses=COMMON_ERRORS, openapi_extra=_SECURITY)
    def pilot_pipe(dataset_id: Identifier, version: Identifier, pipe_id: Identifier, request: Request, response: Response,
                   at: UtcTime | None = None) -> PilotPipeDetail:
        _, snapshot, grant, _ = _scope(request, response, dataset_id, version)
        pipe = next((row for row in snapshot.pipes if row.pipe_id == pipe_id), None)
        if pipe is None:
            raise HTTPException(404, "试点管段不存在")
        lines = ()
        if at is None:
            state = "not_requested"
        elif grant and ("asset_geometry" not in grant.uses or not grant.history_from <= at < grant.history_to):
            state = "outside_scope"
        else:
            lines = tuple(_line(row) for row in snapshot.gis if row.pipe_id == pipe_id and row.valid_from <= at.date() <= row.valid_until)
            state = "available" if lines else "not_available"
        return PilotPipeDetail(**_identity(snapshot), pipe=pipe, geometry_status=state, geometries_wgs84=lines)

    @router.post(dataset_path + "/agent/query", response_model=PilotAgentResult,
                 responses=COMMON_ERRORS, openapi_extra=_SECURITY)
    def pilot_agent(dataset_id: Identifier, version: Identifier, payload: PilotAgentRequest,
                    request: Request, response: Response) -> PilotAgentResult:
        _, snapshot, grant, _ = _scope(request, response, dataset_id, version)
        return _explain(snapshot, grant, payload)

    return router
