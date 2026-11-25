"""Explicit human confirmation for comparing synthetic gas replay results."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
from threading import Lock
from typing import Iterator, Literal
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import (
    LOCAL_RUNTIME_MODES,
    LOOPBACK_BIND_HOSTS,
    Principal,
    authorize_dataset,
    require_authenticated,
    require_permission,
)

from .audit_storage import private_sqlite_path
from .contracts import GAS_DATASET_ID
from .cuv_evidence import GasCuvReplayResponse
from .cuv_replay import GasCuvMissingItem, GasCuvPlan, GasCuvTopologyEvidence, GasCuvUnknownMetric
from .network import _digest
from .service import GasFixtureService
from .monitoring_context import GasObservationRef, require_observation_event, resolve_observation


COLLABORATION_PERMISSION = "gas:cuv:collaborate"
MAX_CONFIRMATIONS = 100

GasCuvObjective = Literal["min_affected_service_hours", "min_topology_restoration_hours"]
GasCuvComparisonReason = Literal[
    "base_plan_blocked", "peak_limit_exceeded", "recovery_deadline_missed",
    "peak_unknown", "recovery_unknown", "objective_unknown",
]
GasCuvPersistence = Literal["process_memory", "sqlite_local"]


class _ImmutableModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class GasCuvCriteria(_ImmutableModel):
    model_config = ConfigDict(strict=True)

    objective: GasCuvObjective
    max_peak_affected_services: int | None = Field(ge=0)
    restore_by_hours: float | None = Field(ge=0, allow_inf_nan=False)


_DEFAULT_CRITERIA = GasCuvCriteria(
    objective="min_affected_service_hours", max_peak_affected_services=None, restore_by_hours=None,
)


class GasCuvQueryBinding(_ImmutableModel):
    model_config = ConfigDict(strict=True)

    expected_revision: int = Field(ge=0)
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class GasCuvDraftRequest(GasCuvQueryBinding):
    criteria: GasCuvCriteria


class GasCuvCollaborationRequest(GasCuvDraftRequest):
    confirmed: Literal[True]
    feedback: str = Field(min_length=1, max_length=280)
    monitoring_ref: GasObservationRef | None = None

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_explicit_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("需要人工明确确认")
        return value

    @field_validator("feedback", mode="before")
    @classmethod
    def normalize_feedback(cls, value: object) -> object:
        if isinstance(value, str):
            if any(unicodedata.category(char).startswith("C") for char in value):
                raise ValueError("反馈包含控制字符")
            return value.strip()
        return value


class GasCuvRankedPlan(_ImmutableModel):
    plan_id: str
    label: str
    status: Literal["eligible", "excluded", "insufficient_data"]
    rank: int | None
    affected_service_hours: float | None
    topology_restoration_hours: float | None
    peak_affected_service_count: int | None
    reasons: tuple[GasCuvComparisonReason, ...]


class GasCuvCriteriaComparison(_ImmutableModel):
    status: Literal["ranked", "no_eligible_plan"]
    leading_plan_ids: tuple[str, ...]
    plans: tuple[GasCuvRankedPlan, ...]


class GasCuvConfirmedComparison(_ImmutableModel):
    """Same actor's read-only projection. Human feedback and history stay in the ledger."""

    revision: int
    input_sha256: str
    criteria: GasCuvCriteria
    comparison: GasCuvCriteriaComparison


class GasCuvCollaborationAuditRecord(GasCuvConfirmedComparison):
    actor_id: str
    occurred_at: datetime
    previous_criteria: GasCuvCriteria
    feedback: str
    monitoring_ref: GasObservationRef | None = None


class GasCuvComparisonChange(_ImmutableModel):
    """Only comparison facts, scoped to the current operator and replay input."""

    status: Literal["preview", "confirmed"]
    before: GasCuvConfirmedComparison
    after_criteria: GasCuvCriteria
    after_comparison: GasCuvCriteriaComparison
    confirmed_revision: int | None


class GasCuvCollaborationSnapshot(GasCuvConfirmedComparison):
    case_id: str
    scenario_id: str
    scenario_at: datetime
    data_mode: Literal["synthetic"] = "synthetic"
    validation_status: Literal["not_business_validated"] = "not_business_validated"
    persistence: GasCuvPersistence
    topology_evidence: GasCuvTopologyEvidence
    audit: tuple[GasCuvCollaborationAuditRecord, ...]
    unknown_metrics: tuple[GasCuvUnknownMetric, ...]
    missing_items: tuple[GasCuvMissingItem, ...]


def _assess_plan(plan: GasCuvPlan, criteria: GasCuvCriteria) -> GasCuvRankedPlan:
    violations: list[GasCuvComparisonReason] = []
    missing: list[GasCuvComparisonReason] = []
    if plan.status == "blocked":
        violations.append("base_plan_blocked")
    else:
        peak = plan.peak_affected_service_count
        if criteria.max_peak_affected_services is not None:
            if peak is None:
                missing.append("peak_unknown")
            elif peak > criteria.max_peak_affected_services:
                violations.append("peak_limit_exceeded")
        restoration = plan.topology_restoration_hours
        if criteria.restore_by_hours is not None:
            if restoration is None:
                missing.append("recovery_unknown")
            elif restoration > criteria.restore_by_hours:
                violations.append("recovery_deadline_missed")
        if _objective_value(plan, criteria.objective) is None:
            missing.append("objective_unknown")
    status = "excluded" if violations else "insufficient_data" if missing else "eligible"
    return GasCuvRankedPlan(
        plan_id=plan.plan_id,
        label=plan.label,
        status=status,
        rank=None,
        affected_service_hours=plan.affected_service_hours,
        topology_restoration_hours=plan.topology_restoration_hours,
        peak_affected_service_count=plan.peak_affected_service_count,
        reasons=(*violations, *missing),
    )


def _objective_value(plan: GasCuvPlan | GasCuvRankedPlan, objective: GasCuvObjective) -> float | None:
    if objective == "min_affected_service_hours":
        return plan.affected_service_hours
    return plan.topology_restoration_hours


def _compare(report: GasCuvReplayResponse, criteria: GasCuvCriteria) -> GasCuvCriteriaComparison:
    assessments = tuple(_assess_plan(plan, criteria) for plan in report.plans)
    values = {
        plan.plan_id: value for plan in assessments
        if plan.status == "eligible" and (value := _objective_value(plan, criteria.objective)) is not None
    }
    ranks = {value: rank for rank, value in enumerate(sorted(set(values.values())), start=1)}
    ranked = tuple(
        plan.model_copy(update={"rank": ranks[values[plan.plan_id]]}) if plan.plan_id in values else plan
        for plan in assessments
    )
    return GasCuvCriteriaComparison(
        status="ranked" if ranks else "no_eligible_plan",
        leading_plan_ids=tuple(plan.plan_id for plan in ranked if plan.rank == 1),
        plans=ranked,
    )


_AuditKey = tuple[str, str, str]


def _private_db_path(path: Path) -> Path:
    try:
        return private_sqlite_path(path)
    except RuntimeError:
        raise RuntimeError("燃气 CUV 审计数据库配置无效") from None


class GasCuvCollaborationStore:
    """Keep confirmed criteria and comparisons isolated by actor and scenario."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._lock = Lock()
        self._audit: dict[_AuditKey, tuple[GasCuvCollaborationAuditRecord, ...]] = {}
        self._db_path = _private_db_path(db_path) if db_path is not None else None
        if self._db_path is not None:
            with self._session(writing=True) as connection:
                assert connection is not None
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS gas_cuv_audit ("
                    "actor_id TEXT NOT NULL, case_id TEXT NOT NULL, scenario_id TEXT NOT NULL, "
                    "revision INTEGER NOT NULL, record TEXT NOT NULL, "
                    "PRIMARY KEY (actor_id, case_id, scenario_id, revision))"
                )

    @contextmanager
    def _session(self, *, writing: bool = False) -> Iterator[sqlite3.Connection | None]:
        if self._db_path is None:
            with self._lock:
                yield None
            return
        try:
            with closing(sqlite3.connect(self._db_path, timeout=3)) as connection, connection:
                if writing:
                    connection.execute("BEGIN IMMEDIATE")
                yield connection
        except (sqlite3.Error, ValidationError):
            raise HTTPException(503, "gas_cuv_collaboration_audit_unavailable") from None

    def _records(
        self, key: _AuditKey, connection: sqlite3.Connection | None,
    ) -> tuple[GasCuvCollaborationAuditRecord, ...]:
        if connection is None:
            return self._audit.get(key, ())
        rows = connection.execute(
            "SELECT record FROM gas_cuv_audit WHERE actor_id = ? AND case_id = ? AND scenario_id = ? ORDER BY revision",
            key,
        )
        return tuple(GasCuvCollaborationAuditRecord.model_validate_json(record) for (record,) in rows)

    def _append(
        self, key: _AuditKey, records: tuple[GasCuvCollaborationAuditRecord, ...],
        record: GasCuvCollaborationAuditRecord, connection: sqlite3.Connection | None,
    ) -> None:
        if connection is None:
            self._audit[key] = (*records, record)
        else:
            connection.execute(
                "INSERT INTO gas_cuv_audit (actor_id, case_id, scenario_id, revision, record) VALUES (?, ?, ?, ?, ?)",
                (*key, record.revision, record.model_dump_json()),
            )

    def _snapshot(
        self, report: GasCuvReplayResponse, digest: str, audit: tuple[GasCuvCollaborationAuditRecord, ...],
    ) -> GasCuvCollaborationSnapshot:
        latest = audit[-1] if audit else None
        active = latest if latest is not None and latest.input_sha256 == digest else None
        return GasCuvCollaborationSnapshot(
            case_id=report.case_id,
            scenario_id=report.scenario_id,
            scenario_at=report.scenario_at,
            input_sha256=digest,
            revision=latest.revision if latest is not None else 0,
            criteria=active.criteria if active is not None else _DEFAULT_CRITERIA,
            comparison=active.comparison if active is not None else _compare(report, _DEFAULT_CRITERIA),
            persistence="sqlite_local" if self._db_path is not None else "process_memory",
            topology_evidence=report.topology_evidence,
            audit=audit,
            unknown_metrics=tuple(report.unknown_metrics),
            missing_items=tuple(report.missing_items),
        )

    def read(self, actor_id: str, report: GasCuvReplayResponse) -> GasCuvCollaborationSnapshot:
        key = (actor_id, report.case_id, report.scenario_id)
        digest = _digest(report.model_dump(mode="json"))
        with self._session() as connection:
            return self._snapshot(report, digest, self._records(key, connection))

    def confirmed_comparison(
        self, actor_id: str, report: GasCuvReplayResponse,
    ) -> GasCuvConfirmedComparison | None:
        return self.comparison_for_query(actor_id, report)[1]

    def comparison_for_query(
        self, actor_id: str, report: GasCuvReplayResponse, draft: GasCuvDraftRequest | None = None,
        binding: GasCuvQueryBinding | None = None,
    ) -> tuple[GasCuvQueryBinding, GasCuvConfirmedComparison | None, GasCuvComparisonChange | None]:
        key = (actor_id, report.case_id, report.scenario_id)
        digest = _digest(report.model_dump(mode="json"))
        with self._session() as connection:
            audit = self._records(key, connection)

        revision = audit[-1].revision if audit else 0
        current = GasCuvQueryBinding(expected_revision=revision, input_sha256=digest)
        for expected in (binding, draft):
            if expected is not None:
                if expected.input_sha256 != digest:
                    raise HTTPException(409, "gas_cuv_collaboration_input_conflict")
                if expected.expected_revision != revision:
                    raise HTTPException(409, "gas_cuv_collaboration_revision_conflict")

        def projection(entry: GasCuvCollaborationAuditRecord | None) -> GasCuvConfirmedComparison | None:
            if entry is None or entry.input_sha256 != digest:
                return None
            return GasCuvConfirmedComparison(
                revision=entry.revision, input_sha256=digest, criteria=entry.criteria, comparison=entry.comparison,
            )

        active = projection(audit[-1] if audit else None)
        if not draft and not active:
            return current, None, None
        baseline = GasCuvConfirmedComparison(
            revision=0, input_sha256=digest, criteria=_DEFAULT_CRITERIA, comparison=_compare(report, _DEFAULT_CRITERIA),
        )
        if draft:
            return current, active, GasCuvComparisonChange(
                status="preview", before=active or baseline, after_criteria=draft.criteria,
                after_comparison=_compare(report, draft.criteria), confirmed_revision=None,
            )
        assert active is not None
        previous = projection(audit[-2] if len(audit) > 1 else None) or baseline
        return current, active, GasCuvComparisonChange(
            status="confirmed", before=previous, after_criteria=active.criteria,
            after_comparison=active.comparison, confirmed_revision=active.revision,
        )

    def confirm(
        self, actor_id: str, report: GasCuvReplayResponse, request: GasCuvCollaborationRequest,
    ) -> GasCuvCollaborationSnapshot:
        key = (actor_id, report.case_id, report.scenario_id)
        digest = _digest(report.model_dump(mode="json"))
        with self._session(writing=True) as connection:
            audit = self._records(key, connection)
            current = self._snapshot(report, digest, audit)
            if request.input_sha256 != digest:
                raise HTTPException(409, "gas_cuv_collaboration_input_conflict")
            if request.expected_revision != current.revision:
                raise HTTPException(409, "gas_cuv_collaboration_revision_conflict")
            if len(audit) >= MAX_CONFIRMATIONS:
                raise HTTPException(409, "gas_cuv_collaboration_audit_limit")
            record = GasCuvCollaborationAuditRecord(
                revision=current.revision + 1,
                actor_id=actor_id,
                occurred_at=datetime.now(UTC),
                input_sha256=digest,
                previous_criteria=current.criteria,
                criteria=request.criteria,
                feedback=request.feedback,
                monitoring_ref=request.monitoring_ref,
                comparison=_compare(report, request.criteria),
            )
            result = self._snapshot(report, digest, (*audit, record))
            self._append(key, audit, record, connection)
            return result


def _deployment_enabled() -> bool:
    return (
        os.getenv("GAS_CUV_COLLABORATION_DEMO", "").strip() == "1"
        and os.getenv("WATER_RUNTIME_MODE", "").strip().lower() in LOCAL_RUNTIME_MODES | {"controlled"}
        and os.getenv("WATER_BIND_HOST", "").strip().lower() in LOOPBACK_BIND_HOSTS
        and bool(os.getenv("WATER_PRINCIPALS", "").strip())
    )


def gas_cuv_collaboration_store() -> GasCuvCollaborationStore:
    configured_db = os.getenv("GAS_CUV_COLLABORATION_AUDIT_DB", "").strip()
    if _deployment_enabled() and configured_db:
        return GasCuvCollaborationStore(Path(configured_db))
    return GasCuvCollaborationStore()


def gas_cuv_collaboration_operator(request: Request) -> Principal:
    if not _deployment_enabled():
        raise HTTPException(404, "gas_cuv_collaboration_disabled")
    peer = request.client.host if request.client else None
    if peer not in LOOPBACK_BIND_HOSTS or any(
        name in {"forwarded", "via", "forwarded-for", "forwarded-host", "forwarded-proto"}
        or name.startswith(("x-forwarded-", "x-real-"))
        for name in request.headers
    ):
        raise HTTPException(403, "gas_cuv_collaboration_access_denied")
    actor = require_authenticated(request)
    if actor.scope != "internal" or actor.actor_id == "dev-token":
        raise HTTPException(403, "gas_cuv_collaboration_access_denied")
    require_permission(actor, COLLABORATION_PERMISSION)
    authorize_dataset(actor, GAS_DATASET_ID, "synthetic")
    return actor


def gas_cuv_collaboration_router(
    fixture_service: GasFixtureService, store: GasCuvCollaborationStore | None = None,
) -> APIRouter:
    enabled = _deployment_enabled()
    store = store if store is not None else gas_cuv_collaboration_store()
    router = APIRouter()
    path = "/cuv-replay/collaboration"
    security = {"security": [{"WaterToken": []}]}

    def authorize_operator(request: Request) -> Principal:
        if not enabled:
            raise HTTPException(404, "gas_cuv_collaboration_disabled")
        return gas_cuv_collaboration_operator(request)

    @router.get(path, response_model=GasCuvCollaborationSnapshot, responses=COMMON_ERRORS, openapi_extra=security)
    def read_gas_cuv_collaboration(
        response: Response, actor: Principal = Depends(authorize_operator),
    ) -> GasCuvCollaborationSnapshot:
        response.headers["Cache-Control"] = "no-store"
        return store.read(actor.actor_id, fixture_service.cuv_replay())

    @router.post(path, response_model=GasCuvCollaborationSnapshot, responses=COMMON_ERRORS, openapi_extra=security)
    def confirm_gas_cuv_collaboration(
        request: GasCuvCollaborationRequest,
        response: Response,
        actor: Principal = Depends(authorize_operator),
    ) -> GasCuvCollaborationSnapshot:
        response.headers["Cache-Control"] = "no-store"
        report = fixture_service.cuv_replay()
        if request.monitoring_ref:
            observation = resolve_observation(fixture_service, request.monitoring_ref, replay=report)
            require_observation_event(observation, None)
        return store.confirm(actor.actor_id, report, request)

    return router
