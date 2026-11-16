"""REST endpoints. Interactive docs: ``/docs`` (Swagger UI) and ``/redoc``."""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST
from pydantic import ValidationError

from myhealthcheck import __version__
from myhealthcheck.api.schemas import (
    CheckRunResponse,
    ConfigResponse,
    DiscoveryResponse,
    ErrorResponse,
    HealthResponse,
    SummaryResponse,
    TargetCreate,
)
from myhealthcheck.discovery import DiscoveryError
from myhealthcheck.models import Source, Status, TargetCheck, TargetSpec, TargetState
from myhealthcheck.monitor import DiscoveryDisabledError, Monitor
from myhealthcheck.targets import split_url

health_router = APIRouter(tags=["health"])
api_router = APIRouter(prefix="/api/v1")

_NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Target not found"}
}


def get_monitor(request: Request) -> Monitor:
    monitor: Monitor = request.app.state.monitor
    return monitor


def spec_from_body(body: TargetCreate, source: Source) -> TargetSpec:
    try:
        base_url, paths, name = split_url(body.url, body.paths)
        return TargetSpec(
            name=body.name or name,
            base_url=base_url,
            paths=paths,
            match=body.match,
            expected_status=body.expected_status,
            expected_body=body.expected_body,
            timeout=body.timeout,
            labels=body.labels,
            source=source,
        )
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc.errors()[0]["msg"])
        ) from exc


# --------------------------------------------------------------------- health
@health_router.get("/health/live", response_model=HealthResponse, summary="Liveness probe")
async def live() -> HealthResponse:
    """The process is running and the event loop answers."""
    return HealthResponse(status="ok", version=__version__)


@health_router.get(
    "/health/ready",
    response_model=HealthResponse,
    summary="Readiness probe",
    responses={503: {"model": HealthResponse, "description": "Not ready yet"}},
)
async def ready(request: Request) -> Response:
    """Ready once the first check cycle finished and the storage answers."""
    monitor = get_monitor(request)
    storage_ok = await asyncio.to_thread(monitor.storage.ping)
    checks = {"storage": storage_ok, "initialized": monitor.ready}
    is_ready = all(checks.values())
    body = HealthResponse(
        status="ok" if is_ready else "not_ready",
        version=__version__,
        storage=monitor.storage.backend.value,
        checks=checks,
    )
    return JSONResponse(
        body.model_dump(),
        status_code=status.HTTP_200_OK if is_ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@health_router.get("/health", response_model=HealthResponse, summary="Health (legacy alias)")
@health_router.get("/status", response_model=HealthResponse, summary="Status (alias of /health)")
async def health(request: Request) -> HealthResponse:
    """Backwards compatible endpoint of the 2.x releases (same as liveness)."""
    return HealthResponse(
        status="ok", version=__version__, storage=get_monitor(request).storage.backend.value
    )


@health_router.get(
    "/metrics",
    summary="Prometheus metrics",
    response_class=Response,
    responses={200: {"content": {CONTENT_TYPE_LATEST: {}}}},
)
async def metrics(request: Request) -> Response:
    return Response(get_monitor(request).metrics.render(), media_type=CONTENT_TYPE_LATEST)


# -------------------------------------------------------------------- targets
@api_router.get(
    "/targets", response_model=list[TargetState], tags=["targets"], summary="List targets"
)
async def list_targets(
    request: Request,
    status_filter: Annotated[Status | None, Query(alias="status")] = None,
    source: Source | None = None,
    namespace: str | None = None,
) -> list[TargetState]:
    """List every monitored system with its endpoints and latest status."""
    monitor = get_monitor(request)
    return await asyncio.to_thread(monitor.storage.list_targets, status_filter, source, namespace)


@api_router.post(
    "/targets",
    response_model=TargetState,
    status_code=status.HTTP_201_CREATED,
    tags=["targets"],
    summary="Register a target",
)
async def create_target(
    request: Request,
    body: TargetCreate,
    check_now: Annotated[bool, Query(description="Run a check right away")] = True,
) -> TargetState:
    """Register a system to be monitored (stored with ``source=api``)."""
    monitor = get_monitor(request)
    spec = spec_from_body(body, Source.API)
    await monitor.add_target(spec)
    if check_now:
        await monitor.check_spec(spec)
    state = await asyncio.to_thread(monitor.storage.get_target, spec.id)
    if state is None:  # pragma: no cover - removed concurrently
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="target not found")
    return state


@api_router.get(
    "/targets/{target_id}",
    response_model=TargetState,
    responses=_NOT_FOUND,
    tags=["targets"],
    summary="Get a target",
)
async def get_target(request: Request, target_id: str) -> TargetState:
    state = await asyncio.to_thread(get_monitor(request).storage.get_target, target_id)
    if state is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="target not found")
    return state


@api_router.delete(
    "/targets/{target_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    tags=["targets"],
    summary="Remove a target",
)
async def delete_target(request: Request, target_id: str) -> Response:
    """Stop monitoring a target. Discovered or static targets come back on the
    next discovery run / restart if they are still declared."""
    if not await get_monitor(request).remove_target(target_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="target not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@api_router.get(
    "/targets/{target_id}/history",
    response_model=list[TargetCheck],
    responses=_NOT_FOUND,
    tags=["targets"],
    summary="Check history of a target",
)
async def target_history(
    request: Request, target_id: str, limit: Annotated[int, Query(ge=1, le=1000)] = 50
) -> list[TargetCheck]:
    storage = get_monitor(request).storage
    if await asyncio.to_thread(storage.get_target, target_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="target not found")
    return await asyncio.to_thread(storage.history, target_id, limit)


@api_router.post(
    "/targets/{target_id}/check",
    response_model=TargetState,
    responses=_NOT_FOUND,
    tags=["checks"],
    summary="Check one target now",
)
async def check_target(request: Request, target_id: str) -> TargetState:
    monitor = get_monitor(request)
    if await asyncio.to_thread(monitor.storage.get_target, target_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="target not found")
    await monitor.run_checks([target_id])
    state = await asyncio.to_thread(monitor.storage.get_target, target_id)
    if state is None:  # pragma: no cover - removed concurrently
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="target not found")
    return state


# --------------------------------------------------------------------- checks
@api_router.post(
    "/checks", response_model=CheckRunResponse, tags=["checks"], summary="Run a check cycle"
)
async def run_checks(request: Request) -> CheckRunResponse:
    """Check every target now, without waiting for the next interval."""
    results = await get_monitor(request).run_checks()
    return CheckRunResponse(checked=len(results), results=results)


@api_router.post(
    "/probe", response_model=TargetCheck, tags=["checks"], summary="Ad-hoc check (not stored)"
)
async def probe(request: Request, body: TargetCreate) -> TargetCheck:
    """Check any URL once and return the result without registering it."""
    spec = spec_from_body(body, Source.API)
    return await get_monitor(request).check_spec(spec, persist=False)


# ------------------------------------------------------------------ discovery
@api_router.post(
    "/discovery",
    response_model=DiscoveryResponse,
    tags=["discovery"],
    summary="Run Kubernetes discovery",
    responses={
        409: {"model": ErrorResponse, "description": "Discovery disabled"},
        502: {"model": ErrorResponse, "description": "Kubernetes API error"},
    },
)
async def run_discovery(request: Request) -> DiscoveryResponse:
    monitor = get_monitor(request)
    try:
        count = await monitor.run_discovery()
    except DiscoveryDisabledError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except DiscoveryError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return DiscoveryResponse(discovered=count, finished_at=monitor.last_discovery_at)


# ----------------------------------------------------------------------- info
@api_router.get("/summary", response_model=SummaryResponse, tags=["info"], summary="Overall status")
async def summary(request: Request) -> SummaryResponse:
    monitor = get_monitor(request)
    data = await asyncio.to_thread(monitor.storage.summary)
    return SummaryResponse(
        **data,
        storage_backend=monitor.storage.backend.value,
        storage_location=monitor.storage.location,
        discovery_enabled=monitor.discovery is not None,
        last_cycle_at=monitor.last_cycle_at,
        last_discovery_at=monitor.last_discovery_at,
        last_discovery_error=monitor.last_discovery_error,
    )


@api_router.get(
    "/config", response_model=ConfigResponse, tags=["info"], summary="Effective configuration"
)
async def config(request: Request) -> ConfigResponse:
    """Effective settings (secrets are masked)."""
    return ConfigResponse(settings=get_monitor(request).settings.public_dict())
