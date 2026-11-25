from __future__ import annotations
from collections.abc import Callable
import math
from typing import Annotated
from fastapi import APIRouter, Query, Request, Response
from fastapi.exceptions import HTTPException, RequestValidationError
from src.platform.api_models import COMMON_ERRORS
from src.platform.authz import authorize_dataset, resolve_principal
from .adapter import FixtureUnavailable, CASE_ID, evaluate_fixture, list_cases
from .agent import GasAgentHealthResponse, GasAgentQueryRequest, GasAgentQueryResponse, GasAgentService
from .capabilities import GasCapabilityResponse, capability_catalog
from .contracts import AssetResponse, DistrictResponse, EventResponse, GasAsset, GasCaseList, GasCaseResponse, GasErrorResponse, GasEvent, GAS_DATASET_ID
from .cuv_collaboration import (
    gas_cuv_collaboration_operator,
    gas_cuv_collaboration_router,
    gas_cuv_collaboration_store,
)
from .cuv_benchmark import GasCuvBenchmarkReport, run_public_benchmark
from .cuv_engineering import GasCuvEngineeringAssessment, GasCuvSourceBinding, engineering_assessment
from .cuv_evidence import GasCuvReplayResponse
from .public_data import GasPublicDataService, gas_public_data_router
from .data_m2 import gas_m2_router
from .data_workbench import gas_data_workbench_router
from .disposition import gas_disposition_router
from .monitoring import GasMonitoringReport
from .monitoring_context import GasMonitoringObservation, GasObservationRef, resolve_observation
from .pilot_api import gas_pilot_router
from .service import GasFixtureService
from .simulation import GasSimulationService, gas_simulation_router
from .synthetic_scenario import gas_scenario_router

def _authorize_synthetic(request: Request) -> None:
    principal = resolve_principal(request)
    authorize_dataset(principal, GAS_DATASET_ID, "synthetic")

def _fixture_unavailable() -> HTTPException:
    return HTTPException(503, "燃气合成候选暂不可用")

def _invalid_bbox(reason: str) -> None:
    raise RequestValidationError([{"loc": ("query", "bbox"), "msg": reason, "type": "value_error"}])

def gas_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/gas", tags=["gas-candidate"])
    @router.get("/cases", response_model=GasCaseList, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def cases(request: Request):
        _authorize_synthetic(request)
        try: return {"items": list_cases()}
        except FixtureUnavailable: raise _fixture_unavailable() from None
    @router.get("/cases/{case_id}", response_model=GasCaseResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def case(case_id: str, request: Request):
        _authorize_synthetic(request)
        if case_id != CASE_ID: raise HTTPException(404, "燃气合成案例不存在")
        try: return evaluate_fixture(case_id)
        except FixtureUnavailable: raise _fixture_unavailable() from None
    return router

def gas_engineering_router(
    service: GasFixtureService | None = None, *, model_starting: Callable[[], bool] | None = None,
    public_data_service: GasPublicDataService | None = None,
) -> APIRouter:
    fixture_service = service or GasFixtureService()
    public_service = public_data_service or GasPublicDataService()
    agent_service = GasAgentService(
        fixture_service, model_starting=model_starting, public_data_service=public_service,
    )
    collaboration_store = gas_cuv_collaboration_store()
    router = APIRouter(prefix="/api/v1/gas/engineering", tags=["gas-engineering"])
    def principal(request: Request):
        resolved = resolve_principal(request)
        authorize_dataset(resolved, GAS_DATASET_ID, "synthetic")
        return resolved
    @router.get("/agent/health", response_model=GasAgentHealthResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def agent_health(request: Request):
        principal(request)
        return agent_service.health()

    @router.post("/agent/query", response_model=GasAgentQueryResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def agent_query(payload: GasAgentQueryRequest, request: Request, response: Response):
        resolved = principal(request)
        def confirmed_comparison(report: GasCuvReplayResponse):
            try:
                operator = gas_cuv_collaboration_operator(request)
            except HTTPException as exc:
                if (payload.cuv_draft is not None or payload.cuv_comparison_binding is not None
                    or exc.status_code not in (401, 403, 404)):
                    raise
                return None
            return collaboration_store.comparison_for_query(
                operator.actor_id, report, draft=payload.cuv_draft, binding=payload.cuv_comparison_binding,
            )

        result = agent_service.query(payload, principal=resolved, cuv_comparison=confirmed_comparison)
        response.headers["X-Request-ID"] = result.request_id
        return result

    @router.get("/capabilities", response_model=GasCapabilityResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def capabilities(request: Request):
        principal(request)
        return capability_catalog()
    @router.get("/districts", response_model=DistrictResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def districts(request: Request):
        principal(request)
        return fixture_service.districts()
    @router.get("/cuv-replay", response_model=GasCuvReplayResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def cuv_replay(request: Request, response: Response) -> GasCuvReplayResponse:
        principal(request)
        response.headers["Cache-Control"] = "no-store"
        return fixture_service.cuv_replay()

    @router.get("/cuv-replay/engineering", response_model=GasCuvEngineeringAssessment, responses=COMMON_ERRORS,
                openapi_extra={"security": [{"WaterToken": []}]})
    def cuv_engineering(
        request: Request, response: Response, binding: Annotated[GasCuvSourceBinding, Query()],
    ) -> GasCuvEngineeringAssessment:
        principal(request)
        response.headers["Cache-Control"] = "no-store"
        report = fixture_service.cuv_replay(case_binding=binding, replay_sha256=binding.replay_content_sha256)
        return engineering_assessment(report)

    @router.get("/cuv-replay/benchmark", response_model=GasCuvBenchmarkReport, responses=COMMON_ERRORS,
                openapi_extra={"security": [{"WaterToken": []}]})
    def cuv_benchmark(request: Request, response: Response) -> GasCuvBenchmarkReport:
        principal(request)
        if request.query_params:
            raise HTTPException(422, "gas_cuv_benchmark_fixed_inputs")
        response.headers["Cache-Control"] = "no-store"
        return run_public_benchmark()

    @router.get("/monitoring", response_model=GasMonitoringReport, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def monitoring(request: Request, response: Response) -> GasMonitoringReport:
        principal(request)
        response.headers["Cache-Control"] = "no-store"
        return fixture_service.monitoring()

    @router.get("/monitoring/observation", response_model=GasMonitoringObservation, responses=COMMON_ERRORS,
                openapi_extra={"security": [{"WaterToken": []}]})
    def monitoring_observation(
        request: Request, response: Response, reference: Annotated[GasObservationRef, Query()],
    ) -> GasMonitoringObservation:
        principal(request)
        response.headers["Cache-Control"] = "no-store"
        return resolve_observation(fixture_service, reference)

    @router.get("/assets", response_model=AssetResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def assets(request: Request, asset_type: str | None = Query(default=None), district_id: str | None = Query(default=None),
               bbox: str | None = Query(default=None, max_length=128, description="WGS84顺序为minLon,minLat,maxLon,maxLat"), limit: int = Query(default=200, ge=1, le=1000),
               offset: int = Query(default=0, ge=0)):
        principal(request)
        if bbox is not None:
            try:
                values = [float(x.strip()) for x in bbox.split(",")]
            except ValueError:
                _invalid_bbox("bbox必须是minLon,minLat,maxLon,maxLat")
            if (len(values) != 4 or any(not math.isfinite(x) for x in values)
                    or not (-180 <= values[0] <= 180 and -180 <= values[2] <= 180)
                    or not (-90 <= values[1] <= 90 and -90 <= values[3] <= 90)
                    or values[0] > values[2] or values[1] > values[3]):
                _invalid_bbox("bbox范围无效")
        return fixture_service.assets(asset_type=asset_type, district_id=district_id, bbox=bbox, limit=limit, offset=offset)
    @router.get("/assets/{asset_id}", response_model=GasAsset, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def asset(asset_id: str, request: Request):
        principal(request)
        return fixture_service.asset(asset_id)
    @router.get("/events", response_model=EventResponse, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def events(request: Request, district_id: str | None = Query(default=None), status: str | None = Query(default=None),
               time_from: str | None = Query(default=None), time_to: str | None = Query(default=None),
               limit: int = Query(default=100, ge=1, le=1000)):
        principal(request)
        if time_from is not None or time_to is not None:
            raise HTTPException(422, "当前合成事件没有可用事件时点，不能按时间筛选")
        return fixture_service.events(limit=limit, district_id=district_id, status=status)
    @router.get("/events/{event_id}", response_model=GasEvent, responses=COMMON_ERRORS, openapi_extra={"security": [{"WaterToken": []}]})
    def event(event_id: str, request: Request):
        principal(request)
        return fixture_service.event(event_id)
    router.include_router(gas_m2_router(fixture_service))
    router.include_router(gas_data_workbench_router(fixture_service))
    router.include_router(gas_scenario_router(fixture_service))
    router.include_router(gas_pilot_router())
    router.include_router(gas_disposition_router(fixture_service))
    router.include_router(gas_cuv_collaboration_router(fixture_service, store=collaboration_store))
    router.include_router(gas_public_data_router(public_service))
    router.include_router(gas_simulation_router(GasSimulationService(fixture_service, public_service)))
    return router

__all__ = ["gas_router", "gas_engineering_router"]
