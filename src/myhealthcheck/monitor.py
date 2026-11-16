"""The orchestrator that ties discovery, checks, storage, metrics and alerts.

Analogy: the monitor is the *head nurse*. It keeps the list of patients up to
date (static targets + Kubernetes census), sends nurses on rounds every
``CHECK_INTERVAL`` seconds, writes results to the records cabinet (storage),
updates the board in the hallway (metrics) and pages the doctor (notifier) when
a patient's condition changes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any

from opentelemetry import trace

from myhealthcheck.checker import HealthChecker
from myhealthcheck.config import Settings
from myhealthcheck.discovery import DiscoveryError, KubernetesDiscovery
from myhealthcheck.metrics import Metrics
from myhealthcheck.models import Source, Status, TargetCheck, TargetSpec, utcnow
from myhealthcheck.notifier import Notifier
from myhealthcheck.storage import Storage
from myhealthcheck.targets import load_static_targets

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class DiscoveryDisabledError(RuntimeError):
    """Raised when discovery is requested but not enabled."""


class Monitor:
    """Owns the background loops and the core operations used by the API."""

    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        checker: HealthChecker,
        metrics: Metrics,
        notifier: Notifier,
        discovery: KubernetesDiscovery | None = None,
    ) -> None:
        self.settings = settings
        self.storage = storage
        self.checker = checker
        self.metrics = metrics
        self.notifier = notifier
        self.discovery = discovery
        self._semaphore = asyncio.Semaphore(settings.max_concurrency)
        self._tasks: list[asyncio.Task[None]] = []
        self._cycle_lock = asyncio.Lock()
        self.ready = False
        self.last_cycle_at: Any = None
        self.last_discovery_at: Any = None
        self.last_discovery_error: str | None = None
        self.metrics.set_storage_backend(storage.backend.value)

    # ---------------------------------------------------------- target list
    async def load_static_targets(self) -> int:
        specs = await asyncio.to_thread(
            load_static_targets, self.settings.health_endpoints, self.settings.targets_file
        )
        await self._sync(Source.STATIC, specs)
        logger.info(
            "static targets loaded",
            extra={"count": len(specs), "targets": [s.id for s in specs]},
        )
        return len(specs)

    async def _sync(self, source: Source, specs: list[TargetSpec]) -> list[str]:
        before = {s.id: s for s in await asyncio.to_thread(self.storage.list_specs)}
        _, removed = await asyncio.to_thread(self.storage.sync_source, source, specs)
        for target_id in removed:
            if target_id in before:
                self.metrics.forget_target(before[target_id])
        if removed:
            logger.info("targets removed", extra={"source": source.value, "targets": removed})
        return removed

    async def run_discovery(self) -> int:
        """Run Kubernetes discovery once and sync the discovered targets."""
        if self.discovery is None:
            raise DiscoveryDisabledError("kubernetes discovery is disabled")
        try:
            specs = await asyncio.to_thread(self.discovery.discover)
        except DiscoveryError as exc:
            self.metrics.discovery_runs.labels(result="error").inc()
            self.last_discovery_error = str(exc)
            logger.error("kubernetes discovery failed", extra={"error": str(exc)})
            raise
        await self._sync(Source.KUBERNETES, specs)
        self.metrics.discovery_runs.labels(result="success").inc()
        self.metrics.discovered_targets.set(len(specs))
        self.last_discovery_at = utcnow()
        self.last_discovery_error = None
        return len(specs)

    async def add_target(self, spec: TargetSpec) -> TargetSpec:
        await asyncio.to_thread(self.storage.upsert_target, spec)
        logger.info("target added", extra={"target": spec.id, "source": spec.source.value})
        return spec

    async def remove_target(self, target_id: str) -> bool:
        state = await asyncio.to_thread(self.storage.get_target, target_id)
        if state is None:
            return False
        await asyncio.to_thread(self.storage.delete_target, target_id)
        self.metrics.forget_target(state.spec)
        logger.info("target removed", extra={"target": target_id})
        return True

    # --------------------------------------------------------------- checks
    async def check_spec(self, spec: TargetSpec, persist: bool = True) -> TargetCheck:
        preferred = None
        if persist:
            state = await asyncio.to_thread(self.storage.get_target, spec.id)
            preferred = state.matched_path if state else None
        async with self._semaphore:
            check = await self.checker.check_target(
                spec, self.settings.default_health_paths, preferred_path=preferred
            )
        if persist:
            await self._record(spec, check)
        return check

    async def _record(self, spec: TargetSpec, check: TargetCheck) -> None:
        previous = await asyncio.to_thread(self.storage.save_check, check)
        if previous is None:  # target removed while being checked
            return
        self.metrics.record_check(spec, check)
        log_level = logging.INFO if check.status is not Status.DOWN else logging.WARNING
        logger.log(
            log_level,
            "target checked",
            extra={
                "event": "check",
                "target": spec.id,
                "target_name": spec.name,
                "namespace": spec.namespace,
                "source": spec.source.value,
                "status": check.status.value,
                "matched_path": check.matched_path,
                "response_time": check.response_time,
                "endpoints": [
                    {
                        "url": e.url,
                        "status": e.status.value,
                        "status_code": e.status_code,
                        "response_time": e.response_time,
                        "attempts": e.attempts,
                        "error": e.error,
                    }
                    for e in check.endpoints
                ],
            },
        )
        if self.notifier.should_alert(previous, check.status):
            severity = await self.notifier.notify(spec, previous, check)
            self.metrics.alerts_total.labels(severity=severity).inc()

    async def run_checks(self, target_ids: list[str] | None = None) -> list[TargetCheck]:
        """Check all targets (or a subset) concurrently."""
        async with self._cycle_lock:
            specs = await asyncio.to_thread(self.storage.list_specs)
            if target_ids is not None:
                wanted = set(target_ids)
                specs = [s for s in specs if s.id in wanted]
            start = time.perf_counter()
            with tracer.start_as_current_span("check_cycle") as span:
                span.set_attribute("myhealthcheck.targets", len(specs))
                results = await asyncio.gather(*(self.check_spec(s) for s in specs))
            elapsed = time.perf_counter() - start
            self.metrics.cycle_duration.observe(elapsed)
            if target_ids is None:
                self.metrics.last_cycle.set_to_current_time()
                self.last_cycle_at = utcnow()
            await self._refresh_counts()
            logger.info(
                "check cycle finished",
                extra={
                    "event": "cycle",
                    "targets": len(results),
                    "duration": round(elapsed, 3),
                    "down": sum(r.status is Status.DOWN for r in results),
                },
            )
            return list(results)

    async def _refresh_counts(self) -> None:
        states = await asyncio.to_thread(self.storage.list_targets)
        counts: dict[str, dict[str, int]] = {}
        for state in states:
            per_source = counts.setdefault(state.source.value, {})
            per_source[state.status.value] = per_source.get(state.status.value, 0) + 1
        self.metrics.set_target_counts(counts)

    # ------------------------------------------------------------ lifecycle
    async def _loop(
        self, name: str, interval: float, func: Callable[[], Coroutine[Any, Any, Any]]
    ) -> None:
        while True:
            await asyncio.sleep(interval)
            try:
                await func()
            except asyncio.CancelledError:  # pragma: no cover - shutdown path
                raise
            except Exception:
                logger.exception("background loop iteration failed", extra={"loop": name})

    async def initialize(self) -> None:
        """Load targets and run the first discovery + check cycle."""
        await self.load_static_targets()
        if self.discovery is not None:
            with contextlib.suppress(DiscoveryError):
                await self.run_discovery()
        await self.run_checks()
        self.ready = True

    async def _initialize_safely(self) -> None:
        try:
            await self.initialize()
        except Exception:
            logger.exception("initial check cycle failed")
            self.ready = True  # the API stays usable; the loops will retry

    async def start(self) -> None:
        """Spawn the first cycle and the periodic loops without blocking.

        The HTTP server starts answering right away (liveness OK) while the
        first cycle runs; ``/health/ready`` returns 503 until it finishes.
        """
        self._tasks.append(asyncio.create_task(self._initialize_safely()))
        self._tasks.append(
            asyncio.create_task(self._loop("checks", self.settings.check_interval, self.run_checks))
        )
        if self.discovery is not None:
            self._tasks.append(
                asyncio.create_task(
                    self._loop("discovery", self.settings.discovery_interval, self.run_discovery)
                )
            )
        logger.info(
            "monitor started",
            extra={
                "check_interval": self.settings.check_interval,
                "discovery": self.discovery is not None,
            },
        )

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self.ready = False
        logger.info("monitor stopped")
