"""Human-only workflow for synthetic gas events in an opt-in loopback demo."""

from __future__ import annotations

from datetime import UTC, datetime
import os
from threading import Lock
from typing import Annotated, Literal
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import (
    LOCAL_RUNTIME_MODES,
    LOOPBACK_BIND_HOSTS,
    Principal,
    authorize_dataset,
    require_authenticated,
    require_permission,
)

from .contracts import GAS_DATASET_ID
from .service import GasFixtureService
from .monitoring_context import GasObservationRef, require_observation_event, resolve_observation


GasDispositionStatus = Literal["pending", "acknowledged", "in_progress", "completed"]
DISPOSITION_PERMISSION = "gas:disposition"
NEXT_STATUS: dict[GasDispositionStatus, GasDispositionStatus | None] = {
    "pending": "acknowledged",
    "acknowledged": "in_progress",
    "in_progress": "completed",
    "completed": None,
}

EventId = Annotated[str, Path(min_length=1, max_length=128)]


class GasDispositionTransition(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    expected_revision: int = Field(ge=0)
    target_status: GasDispositionStatus
    reason: str = Field(min_length=1, max_length=280)
    monitoring_ref: GasObservationRef | None = None

    @field_validator("reason", mode="before")
    @classmethod
    def normalize_reason(cls, value: object) -> object:
        if isinstance(value, str):
            if any(unicodedata.category(char).startswith("C") for char in value):
                raise ValueError("处置说明包含控制字符")
            return value.strip()
        return value


class GasDispositionAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: str
    actor_id: str
    occurred_at: datetime
    from_status: GasDispositionStatus
    to_status: GasDispositionStatus
    reason: str
    revision: int = Field(ge=1)
    monitoring_ref: GasObservationRef | None = Field(default=None, exclude_if=lambda value: value is None)


class GasDispositionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    data_mode: Literal["synthetic"] = "synthetic"
    status: GasDispositionStatus
    revision: int = Field(ge=0)
    next_status: GasDispositionStatus | None
    audit: tuple[GasDispositionAuditRecord, ...]


class GasDispositionStore:
    """An app instance holds its own bounded audit state. Restart clears it."""

    def __init__(self) -> None:
        self._audit: dict[str, tuple[GasDispositionAuditRecord, ...]] = {}
        self._lock = Lock()

    def read(self, event_id: str) -> GasDispositionSnapshot:
        with self._lock:
            return self._snapshot(event_id)

    def _snapshot(self, event_id: str) -> GasDispositionSnapshot:
        audit = self._audit.get(event_id, ())
        status: GasDispositionStatus = audit[-1].to_status if audit else "pending"
        return GasDispositionSnapshot(
            event_id=event_id,
            status=status,
            revision=len(audit),
            next_status=NEXT_STATUS[status],
            audit=audit,
        )

    def transition(
        self, event_id: str, actor_id: str, change: GasDispositionTransition
    ) -> GasDispositionSnapshot:
        with self._lock:
            current = self._snapshot(event_id)
            if change.expected_revision != current.revision:
                raise HTTPException(409, "gas_disposition_revision_conflict")
            if change.target_status != current.next_status:
                raise HTTPException(409, "gas_disposition_transition_conflict")
            record = GasDispositionAuditRecord(
                event_id=event_id,
                actor_id=actor_id,
                occurred_at=datetime.now(UTC),
                from_status=current.status,
                to_status=change.target_status,
                reason=change.reason,
                revision=current.revision + 1,
                monitoring_ref=change.monitoring_ref,
            )
            self._audit[event_id] = (*current.audit, record)
            return self._snapshot(event_id)


def _deployment_enabled() -> bool:
    return (
        os.getenv("GAS_DISPOSITION_DEMO", "").strip() == "1"
        and os.getenv("WATER_RUNTIME_MODE", "").strip().lower() in LOCAL_RUNTIME_MODES | {"controlled"}
        and os.getenv("WATER_BIND_HOST", "").strip().lower() in LOOPBACK_BIND_HOSTS
        and bool(os.getenv("WATER_PRINCIPALS", "").strip())
    )


def gas_disposition_router(fixture_service: GasFixtureService) -> APIRouter:
    enabled = _deployment_enabled()
    store = GasDispositionStore()
    router = APIRouter()
    path = "/events/{event_id}/disposition"
    security = {"security": [{"WaterToken": []}]}

    def authorize_operator(request: Request) -> Principal:
        if not enabled:
            raise HTTPException(404, "gas_disposition_disabled")
        peer = request.client.host if request.client else None
        if peer not in LOOPBACK_BIND_HOSTS:
            raise HTTPException(403, "gas_disposition_access_denied")
        if any(
            name.lower() in {"forwarded", "via", "forwarded-for", "forwarded-host", "forwarded-proto"}
            or name.lower().startswith(("x-forwarded-", "x-real-"))
            for name in request.headers
        ):
            raise HTTPException(403, "gas_disposition_access_denied")
        actor = require_authenticated(request)
        if actor.scope != "internal" or actor.actor_id == "dev-token":
            raise HTTPException(403, "gas_disposition_access_denied")
        require_permission(actor, DISPOSITION_PERMISSION)
        authorize_dataset(actor, GAS_DATASET_ID, "synthetic")
        return actor

    def require_event(event_id: str) -> None:
        try:
            event = fixture_service.event(event_id)
        except HTTPException as exc:
            if exc.status_code != 404:
                raise
            raise HTTPException(404, "gas_disposition_event_not_found") from None
        if event.data_mode != "synthetic" or event.source_status != "synthetic_fixture":
            raise HTTPException(404, "gas_disposition_event_not_found")

    @router.get(path, response_model=GasDispositionSnapshot, responses=COMMON_ERRORS, openapi_extra=security)
    def read_gas_disposition(
        event_id: EventId, response: Response, operator: Principal = Depends(authorize_operator)
    ) -> GasDispositionSnapshot:
        require_event(event_id)
        response.headers["Cache-Control"] = "no-store"
        return store.read(event_id)

    @router.post(path, response_model=GasDispositionSnapshot, responses=COMMON_ERRORS, openapi_extra=security)
    def transition_gas_disposition(
        event_id: EventId,
        change: GasDispositionTransition,
        response: Response,
        operator: Principal = Depends(authorize_operator),
    ) -> GasDispositionSnapshot:
        require_event(event_id)
        response.headers["Cache-Control"] = "no-store"
        if change.monitoring_ref:
            observation = resolve_observation(fixture_service, change.monitoring_ref)
            require_observation_event(observation, event_id)
        return store.transition(event_id, operator.actor_id, change)

    return router
