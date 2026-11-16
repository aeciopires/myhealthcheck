"""Prometheus metrics exposed on ``GET /metrics``."""

from __future__ import annotations

import contextlib

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

from myhealthcheck.models import Source, Status, TargetCheck, TargetSpec

_LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)


class Metrics:
    """All application metrics, bound to their own registry (easy to test)."""

    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        r = self.registry
        target_labels = ["target", "name", "namespace", "source"]

        self.build_info = Info("myhealthcheck_build", "Build information", registry=r)
        self.storage_backend = Gauge(
            "myhealthcheck_storage_backend",
            "Storage backend in use (1 = active)",
            ["backend"],
            registry=r,
        )
        self.target_up = Gauge(
            "myhealthcheck_target_up",
            "1 when the target is up or degraded, 0 when it is down",
            target_labels,
            registry=r,
        )
        self.target_status = Gauge(
            "myhealthcheck_target_status",
            "Current status of the target (one-hot by status label)",
            [*target_labels, "status"],
            registry=r,
        )
        self.target_response = Gauge(
            "myhealthcheck_target_response_seconds",
            "Response time of the last check of the target",
            target_labels,
            registry=r,
        )
        self.check_duration = Histogram(
            "myhealthcheck_check_duration_seconds",
            "Response time of health check requests",
            ["target", "source"],
            buckets=_LATENCY_BUCKETS,
            registry=r,
        )
        self.checks_total = Counter(
            "myhealthcheck_checks_total",
            "Number of target checks by resulting status",
            ["target", "source", "status"],
            registry=r,
        )
        self.targets = Gauge(
            "myhealthcheck_targets",
            "Number of monitored targets by source and status",
            ["source", "status"],
            registry=r,
        )
        self.cycle_duration = Histogram(
            "myhealthcheck_check_cycle_duration_seconds",
            "Duration of a full check cycle",
            buckets=(0.1, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
            registry=r,
        )
        self.last_cycle = Gauge(
            "myhealthcheck_last_check_cycle_timestamp_seconds",
            "Unix time of the last finished check cycle",
            registry=r,
        )
        self.discovery_runs = Counter(
            "myhealthcheck_discovery_runs_total",
            "Kubernetes discovery runs by result",
            ["result"],
            registry=r,
        )
        self.discovered_targets = Gauge(
            "myhealthcheck_discovered_targets",
            "Targets found by the last Kubernetes discovery",
            registry=r,
        )
        self.alerts_total = Counter(
            "myhealthcheck_alerts_total",
            "Alerts emitted on status changes",
            ["severity"],
            registry=r,
        )
        self.http_requests = Counter(
            "myhealthcheck_http_requests_total",
            "HTTP requests served by the API",
            ["method", "route", "status_code"],
            registry=r,
        )
        self.http_duration = Histogram(
            "myhealthcheck_http_request_duration_seconds",
            "Latency of HTTP requests served by the API",
            ["method", "route"],
            buckets=_LATENCY_BUCKETS,
            registry=r,
        )

    @staticmethod
    def _labels(spec: TargetSpec) -> dict[str, str]:
        return {
            "target": spec.id,
            "name": spec.name,
            "namespace": spec.namespace or "",
            "source": spec.source.value,
        }

    def set_storage_backend(self, backend: str) -> None:
        for name in ("file", "temp", "memory"):
            self.storage_backend.labels(backend=name).set(1 if name == backend else 0)

    def record_check(self, spec: TargetSpec, check: TargetCheck) -> None:
        labels = self._labels(spec)
        self.target_up.labels(**labels).set(0 if check.status is Status.DOWN else 1)
        for status in Status:
            self.target_status.labels(**labels, status=status.value).set(
                1 if status is check.status else 0
            )
        if check.response_time is not None:
            self.target_response.labels(**labels).set(check.response_time)
            self.check_duration.labels(target=spec.id, source=spec.source.value).observe(
                check.response_time
            )
        self.checks_total.labels(
            target=spec.id, source=spec.source.value, status=check.status.value
        ).inc()

    def forget_target(self, spec: TargetSpec) -> None:
        """Drop the time series of a target that is no longer monitored."""
        labels = self._labels(spec)
        for gauge in (self.target_up, self.target_response):
            with contextlib.suppress(KeyError):
                gauge.remove(*labels.values())
        for status in Status:
            with contextlib.suppress(KeyError):
                self.target_status.remove(*labels.values(), status.value)
            with contextlib.suppress(KeyError):
                self.checks_total.remove(spec.id, spec.source.value, status.value)
        with contextlib.suppress(KeyError):
            self.check_duration.remove(spec.id, spec.source.value)

    def set_target_counts(self, summary: dict[str, dict[str, int]]) -> None:
        """``summary`` maps source -> status -> count."""
        for source in Source:
            for status in Status:
                count = summary.get(source.value, {}).get(status.value, 0)
                self.targets.labels(source=source.value, status=status.value).set(count)

    def render(self) -> bytes:
        return generate_latest(self.registry)
