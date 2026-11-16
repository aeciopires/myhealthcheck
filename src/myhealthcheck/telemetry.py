"""OpenTelemetry tracing setup.

Tracing is off by default. Set ``OTEL_ENABLED=true`` and the standard
``OTEL_EXPORTER_OTLP_ENDPOINT`` (e.g. ``http://alloy:4318``) to export spans
over OTLP/HTTP to Grafana Alloy, an OpenTelemetry Collector, Tempo or Jaeger.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter

from myhealthcheck import __version__
from myhealthcheck.config import Settings

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

_EXCLUDED_URLS = "health,status,health/live,health/ready,metrics"


def setup_tracing(
    settings: Settings, app: FastAPI | None = None, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Configure the global tracer provider and instrument FastAPI/httpx."""
    if not settings.otel_enabled:
        logger.debug("tracing disabled")
        return None

    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        exporter = OTLPSpanExporter()

    resource = Resource.create(
        {"service.name": settings.otel_service_name, "service.version": __version__}
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=provider, excluded_urls=_EXCLUDED_URLS
        )
    logger.info("tracing enabled", extra={"service_name": settings.otel_service_name})
    return provider


def shutdown_tracing(provider: TracerProvider | None) -> None:
    if provider is None:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().uninstrument()
    provider.shutdown()
