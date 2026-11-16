"""HTTP health checker.

Analogy: the checker is a *nurse* doing rounds. For each patient (target) it
measures the pulse on one or more spots (paths). If a measurement fails because
of a hiccup (network error, timeout, 5xx) it tries again a few times before
writing "down" on the chart. A ``404`` is not a hiccup - it simply means the
spot does not exist - so it is never retried.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

import httpx
from opentelemetry import trace

from myhealthcheck.config import Settings
from myhealthcheck.models import (
    EndpointResult,
    MatchMode,
    Status,
    TargetCheck,
    TargetSpec,
    combine_status,
    utcnow,
)

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

Sleeper = Callable[[float], Awaitable[None]]


def _status_ok(code: int, expected: list[int]) -> bool:
    if expected:
        return code in expected
    return 200 <= code < 400


class HealthChecker:
    """Checks targets using a shared :class:`httpx.AsyncClient`."""

    def __init__(
        self,
        settings: Settings,
        client: httpx.AsyncClient,
        sleep: Sleeper = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.client = client
        self._sleep = sleep

    def _evaluate(
        self, target: TargetSpec, response: httpx.Response, elapsed: float
    ) -> tuple[Status, str | None]:
        expected = target.expected_status or self.settings.expected_status_codes
        if not _status_ok(response.status_code, expected):
            return Status.DOWN, f"unexpected status code {response.status_code}"
        if target.expected_body and target.expected_body not in response.text:
            return Status.DOWN, "expected body content not found"
        if elapsed > self.settings.response_time_threshold:
            return Status.DEGRADED, (
                f"response time {elapsed:.3f}s above threshold "
                f"{self.settings.response_time_threshold}s"
            )
        return Status.UP, None

    async def check_endpoint(self, target: TargetSpec, path: str) -> EndpointResult:
        """Check one URL with retries on transient failures."""
        url = target.url_for(path)
        timeout = target.timeout or self.settings.request_timeout
        attempts = self.settings.retry_attempts
        status, code, elapsed, error = Status.DOWN, None, None, None

        with tracer.start_as_current_span("check_endpoint") as span:
            span.set_attribute("myhealthcheck.target", target.id)
            span.set_attribute("url.full", url)
            attempt = 0
            for attempt in range(1, attempts + 1):
                start = time.perf_counter()
                transient = False
                try:
                    response = await self.client.get(url, timeout=timeout)
                    elapsed = time.perf_counter() - start
                    code = response.status_code
                    status, error = self._evaluate(target, response, elapsed)
                    transient = code >= 500
                except httpx.TimeoutException:
                    elapsed = time.perf_counter() - start
                    status, code, error, transient = Status.DOWN, None, "timeout", True
                except httpx.HTTPError as exc:
                    elapsed = time.perf_counter() - start
                    error = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                    status, code, transient = Status.DOWN, None, True

                if status is not Status.DOWN or not transient:
                    break
                logger.debug(
                    "check attempt failed",
                    extra={"target": target.id, "url": url, "attempt": attempt, "error": error},
                )
                if attempt < attempts:
                    await self._sleep(self.settings.retry_delay)

            span.set_attribute("myhealthcheck.status", status.value)
            if code is not None:
                span.set_attribute("http.response.status_code", code)

        return EndpointResult(
            url=url,
            path=path,
            status=status,
            status_code=code,
            response_time=round(elapsed, 6) if elapsed is not None else None,
            attempts=attempt,
            error=error,
        )

    async def check_target(
        self,
        target: TargetSpec,
        default_paths: list[str],
        preferred_path: str | None = None,
    ) -> TargetCheck:
        """Check a target according to its :class:`MatchMode`.

        * ``all``: every path is checked concurrently.
        * ``any``: paths are probed one by one (last healthy path first) and the
          probe stops at the first healthy answer.
        """
        paths = target.effective_paths(default_paths)
        with tracer.start_as_current_span("check_target") as span:
            span.set_attribute("myhealthcheck.target", target.id)
            span.set_attribute("myhealthcheck.match", target.match.value)

            if target.match is MatchMode.ALL:
                results = list(
                    await asyncio.gather(*(self.check_endpoint(target, p) for p in paths))
                )
            else:
                if preferred_path in paths:
                    paths = [preferred_path, *(p for p in paths if p != preferred_path)]
                results = []
                for path in paths:
                    result = await self.check_endpoint(target, path)
                    results.append(result)
                    if result.status is not Status.DOWN:
                        break

            status = combine_status([r.status for r in results], target.match)
            healthy = [r for r in results if r.status is not Status.DOWN]
            matched = healthy[0].path if healthy and target.match is MatchMode.ANY else None
            timings = [r.response_time for r in results if r.response_time is not None]
            span.set_attribute("myhealthcheck.status", status.value)

        return TargetCheck(
            target_id=target.id,
            status=status,
            endpoints=results,
            matched_path=matched,
            response_time=max(timings) if timings else None,
            checked_at=utcnow(),
        )
