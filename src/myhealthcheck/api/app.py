"""FastAPI application factory and lifespan (startup/shutdown)."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response

from myhealthcheck import __version__
from myhealthcheck.api.routes import api_router, health_router
from myhealthcheck.checker import HealthChecker
from myhealthcheck.config import Settings
from myhealthcheck.discovery import KubernetesDiscovery
from myhealthcheck.metrics import Metrics
from myhealthcheck.monitor import Monitor
from myhealthcheck.notifier import Notifier
from myhealthcheck.storage import Storage
from myhealthcheck.telemetry import setup_tracing, shutdown_tracing

logger = logging.getLogger("myhealthcheck.api")

_QUIET_PATHS = {"/health", "/status", "/health/live", "/health/ready", "/metrics"}

OPENAPI_TAGS = [
    {"name": "targets", "description": "Monitored systems, their endpoints and status."},
    {"name": "checks", "description": "Trigger health checks on demand."},
    {"name": "discovery", "description": "Kubernetes service discovery."},
    {"name": "info", "description": "Summary and effective configuration."},
    {"name": "health", "description": "Probes and metrics of myhealthcheck itself."},
]


def build_monitor(
    settings: Settings,
    client: httpx.AsyncClient,
    discovery: KubernetesDiscovery | None = None,
    metrics: Metrics | None = None,
) -> Monitor:
    """Wire the components together (composition root)."""
    if discovery is None and settings.discovery_enabled:
        discovery = KubernetesDiscovery(settings)
    metrics = metrics or Metrics()
    metrics.build_info.info({"version": __version__})
    return Monitor(
        settings=settings,
        storage=Storage(settings.db_path, settings.history_retention),
        checker=HealthChecker(settings, client),
        metrics=metrics,
        notifier=Notifier(client, settings.alert_webhook_url),
        discovery=discovery,
    )


def create_app(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    discovery: KubernetesDiscovery | None = None,
    run_background: bool = True,
) -> FastAPI:
    """Create the FastAPI app.

    ``transport`` and ``discovery`` exist to inject fakes in tests;
    ``run_background=False`` skips the periodic loops (only the first cycle runs).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with httpx.AsyncClient(
            transport=transport,
            verify=settings.verify_tls,
            follow_redirects=True,
            headers={"User-Agent": f"myhealthcheck/{__version__}"},
        ) as client:
            monitor = build_monitor(settings, client, discovery=discovery)
            app.state.monitor = monitor
            if run_background:
                await monitor.start()
            else:
                await monitor.initialize()
            try:
                yield
            finally:
                await monitor.stop()
                monitor.storage.close()
                shutdown_tracing(app.state.tracer_provider)

    app = FastAPI(
        title="myhealthcheck",
        version=__version__,
        description=(
            "Monitors the availability of other applications using static targets "
            "and Kubernetes service discovery. Prometheus metrics on `/metrics`."
        ),
        openapi_tags=OPENAPI_TAGS,
        license_info={"name": "GPL-3.0", "url": "https://www.gnu.org/licenses/gpl-3.0.html"},
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(api_router)
    app.state.tracer_provider = setup_tracing(settings, app)

    @app.middleware("http")
    async def observe_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start = time.perf_counter()
        response = await call_next(request)
        elapsed = time.perf_counter() - start
        route = request.scope.get("route")
        route_path = getattr(route, "path", "unmatched")
        monitor: Monitor | None = getattr(request.app.state, "monitor", None)
        if monitor is not None:
            monitor.metrics.http_requests.labels(
                request.method, route_path, str(response.status_code)
            ).inc()
            monitor.metrics.http_duration.labels(request.method, route_path).observe(elapsed)
        logger.log(
            logging.DEBUG if request.url.path in _QUIET_PATHS else logging.INFO,
            "http request",
            extra={
                "event": "http_request",
                "method": request.method,
                "path": request.url.path,
                "route": route_path,
                "status_code": response.status_code,
                "duration": round(elapsed, 6),
                "client": request.client.host if request.client else None,
            },
        )
        return response

    return app
