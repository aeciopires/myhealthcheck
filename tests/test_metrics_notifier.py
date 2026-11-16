import logging

import httpx
import pytest

from myhealthcheck.metrics import Metrics
from myhealthcheck.models import EndpointResult, Source, Status, TargetCheck, TargetSpec
from myhealthcheck.notifier import Notifier, severity_for

SPEC = TargetSpec(name="web", base_url="http://web:80", source=Source.KUBERNETES, namespace="ns")


def make_check(status: Status, response_time: float | None = 0.2) -> TargetCheck:
    return TargetCheck(
        target_id=SPEC.id,
        status=status,
        response_time=response_time,
        endpoints=[
            EndpointResult(url="http://web/h", path="/h", status=status, error="boom"),
            EndpointResult(url="http://web/s", path="/s", status=status),
        ],
    )


# -------------------------------------------------------------------- metrics
def test_record_and_forget_target() -> None:
    m = Metrics()
    m.set_storage_backend("temp")
    m.record_check(SPEC, make_check(Status.DOWN))
    m.record_check(SPEC, make_check(Status.UP, response_time=None))
    text = m.render().decode()
    assert 'myhealthcheck_storage_backend{backend="temp"} 1.0' in text
    assert 'myhealthcheck_target_up{name="web",namespace="ns",source="kubernetes"' in text
    assert 'status="up",target="kubernetes-ns-web"} 1.0' in text
    assert 'myhealthcheck_checks_total{source="kubernetes",status="down"' in text
    assert "myhealthcheck_check_duration_seconds_count" in text

    m.forget_target(SPEC)
    m.forget_target(SPEC)  # second call must not raise
    text = m.render().decode()
    assert "myhealthcheck_target_up{" not in text
    assert "myhealthcheck_target_status{" not in text


def test_target_counts() -> None:
    m = Metrics()
    m.set_target_counts({"static": {"up": 2}})
    text = m.render().decode()
    assert 'myhealthcheck_targets{source="static",status="up"} 2.0' in text
    assert 'myhealthcheck_targets{source="api",status="down"} 0.0' in text


# ------------------------------------------------------------------- notifier
@pytest.mark.parametrize(
    ("previous", "current", "expected"),
    [
        (None, Status.DOWN, False),
        (Status.UP, Status.UP, False),
        (Status.UNKNOWN, Status.UP, False),
        (Status.UNKNOWN, Status.DOWN, True),
        (Status.UP, Status.DEGRADED, True),
        (Status.DOWN, Status.UP, True),
    ],
)
def test_should_alert(previous: Status | None, current: Status, expected: bool) -> None:
    assert Notifier.should_alert(previous, current) is expected


def test_severity() -> None:
    assert severity_for(Status.DOWN) == "critical"
    assert severity_for(Status.UNKNOWN) == "info"


async def test_notify_logs_alert(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="myhealthcheck.notifier"):
        severity = await Notifier().notify(SPEC, Status.UP, make_check(Status.DOWN))
    assert severity == "critical"
    record = caplog.records[-1]
    assert record.levelno == logging.ERROR
    assert record.__dict__["event"] == "alert"
    assert record.__dict__["errors"] == ["boom"]


async def test_notify_webhook_success_and_failure(caplog: pytest.LogCaptureFixture) -> None:
    received: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request.content)
        return httpx.Response(200 if len(received) == 1 else 500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = Notifier(client, "http://hook/x")
        assert await notifier.notify(SPEC, Status.DOWN, make_check(Status.UP)) == "resolved"
        with caplog.at_level(logging.WARNING):
            await notifier.notify(SPEC, Status.UP, make_check(Status.DEGRADED))
    assert len(received) == 2
    assert b'"status":"up"' in received[0].replace(b" ", b"")
    assert any(r.getMessage() == "alert webhook failed" for r in caplog.records)
