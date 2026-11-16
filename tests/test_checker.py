import httpx
import pytest

from myhealthcheck.checker import HealthChecker
from myhealthcheck.config import Settings
from myhealthcheck.models import MatchMode, Status, TargetSpec
from tests.conftest import routes_transport

BASE = "http://svc:80"


def spec(**kwargs: object) -> TargetSpec:
    return TargetSpec.model_validate({"name": "svc", "base_url": BASE, **kwargs})


async def run_endpoint(settings: Settings, routes: dict, target: TargetSpec, path: str):  # type: ignore[no-untyped-def]
    sleeps: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    async with httpx.AsyncClient(transport=routes_transport(routes)) as client:
        result = await HealthChecker(settings, client, sleep=fake_sleep).check_endpoint(
            target, path
        )
    return result, sleeps


async def test_endpoint_up(settings: Settings) -> None:
    result, sleeps = await run_endpoint(settings, {f"{BASE}/health": 200}, spec(), "/health")
    assert result.status is Status.UP
    assert result.status_code == 200
    assert result.attempts == 1
    assert result.error is None
    assert sleeps == []


async def test_404_is_not_retried(settings: Settings) -> None:
    result, sleeps = await run_endpoint(settings, {}, spec(), "/health")
    assert result.status is Status.DOWN
    assert result.status_code == 404
    assert result.attempts == 1
    assert "unexpected status code 404" in (result.error or "")
    assert sleeps == []


async def test_5xx_is_retried(settings: Settings) -> None:
    result, sleeps = await run_endpoint(settings, {f"{BASE}/h": 503}, spec(), "/h")
    assert result.status is Status.DOWN
    assert result.attempts == settings.retry_attempts
    assert sleeps == [settings.retry_delay]


async def test_retry_recovers(settings: Settings) -> None:
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200)

    result, sleeps = await run_endpoint(settings, {f"{BASE}/h": flaky}, spec(), "/h")
    assert result.status is Status.UP
    assert result.attempts == 2
    assert len(sleeps) == 1


async def test_timeout_and_connection_errors(settings: Settings) -> None:
    result, _ = await run_endpoint(settings, {f"{BASE}/h": httpx.ReadTimeout("slow")}, spec(), "/h")
    assert result.error == "timeout"
    result, _ = await run_endpoint(settings, {f"{BASE}/h": httpx.ConnectError("")}, spec(), "/h")
    assert result.error == "ConnectError"
    result, _ = await run_endpoint(
        settings, {f"{BASE}/h": httpx.ConnectError("refused")}, spec(), "/h"
    )
    assert result.error == "ConnectError: refused"


async def test_expected_status_and_body(settings: Settings) -> None:
    routes = {f"{BASE}/h": httpx.Response(204, text="")}
    result, _ = await run_endpoint(settings, routes, spec(expected_status=[204]), "/h")
    assert result.status is Status.UP

    routes = {f"{BASE}/readyz": httpx.Response(200, json={"status": "ready"})}
    result, _ = await run_endpoint(
        settings, routes, spec(expected_body='"status":"ready"'), "/readyz"
    )
    assert result.status is Status.UP
    result, _ = await run_endpoint(settings, routes, spec(expected_body="nope"), "/readyz")
    assert result.status is Status.DOWN
    assert result.error == "expected body content not found"

    strict = settings.model_copy(update={"expected_status_codes": [201]})
    result, _ = await run_endpoint(strict, {f"{BASE}/h": 200}, spec(), "/h")
    assert result.status is Status.DOWN


async def test_slow_response_is_degraded(settings: Settings) -> None:
    slow = settings.model_copy(update={"response_time_threshold": 0.0000001})
    result, _ = await run_endpoint(slow, {f"{BASE}/h": 200}, spec(), "/h")
    assert result.status is Status.DEGRADED
    assert "above threshold" in (result.error or "")


async def test_check_target_any_mode_probes_in_order(settings: Settings) -> None:
    routes = {f"{BASE}/health": 200, f"{BASE}/ready": 200}
    async with httpx.AsyncClient(transport=routes_transport(routes)) as client:
        checker = HealthChecker(settings, client)
        check = await checker.check_target(spec(), ["/status", "/health", "/ready"])
        assert check.status is Status.UP
        assert check.matched_path == "/health"
        assert [e.path for e in check.endpoints] == ["/status", "/health"]

        # The last healthy path is tried first next time.
        check = await checker.check_target(
            spec(), ["/status", "/health", "/ready"], preferred_path="/ready"
        )
        assert [e.path for e in check.endpoints] == ["/ready"]

        # Nothing answers.
        check = await checker.check_target(spec(), ["/x", "/y"], preferred_path="/gone")
        assert check.status is Status.DOWN
        assert check.matched_path is None


async def test_check_target_all_mode(settings: Settings) -> None:
    routes = {f"{BASE}/live": 200}
    async with httpx.AsyncClient(transport=routes_transport(routes)) as client:
        checker = HealthChecker(settings, client)
        check = await checker.check_target(
            spec(paths=["/live", "/ready"], match=MatchMode.ALL), ["/ignored"]
        )
    assert check.status is Status.DEGRADED
    assert check.matched_path is None
    assert {e.path for e in check.endpoints} == {"/live", "/ready"}
    assert check.response_time is not None


@pytest.mark.parametrize("timeout", [None, 1.5])
async def test_timeout_is_forwarded(settings: Settings, timeout: float | None) -> None:
    seen: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"]["read"])
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await HealthChecker(settings, client).check_endpoint(spec(timeout=timeout), "/h")
    assert seen == [timeout or settings.request_timeout]
