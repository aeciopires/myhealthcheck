import io
import json
import logging
import sys

import pytest
from opentelemetry.sdk.trace import TracerProvider
from pydantic import ValidationError

from myhealthcheck.logging_config import JsonFormatter, configure_logging
from myhealthcheck.models import MatchMode, Source, Status, TargetSpec, combine_status, slugify


# ------------------------------------------------------------------ logging
def _record(msg: str = "hello", **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("test", logging.INFO, __file__, 1, msg, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_json_formatter_basic_fields() -> None:
    data = json.loads(JsonFormatter().format(_record(target="t1", count=2)))
    assert data["message"] == "hello"
    assert data["level"] == "info"
    assert data["service"] == "myhealthcheck"
    assert data["target"] == "t1"
    assert data["count"] == 2
    assert "trace_id" not in data
    assert data["timestamp"].endswith("+00:00")


def test_json_formatter_exception_and_trace() -> None:
    tracer = TracerProvider().get_tracer("test")
    try:
        raise ValueError("boom")
    except ValueError:
        record = logging.LogRecord("t", logging.ERROR, __file__, 1, "x", None, sys.exc_info())
    record.stack_info = "stack"
    with tracer.start_as_current_span("span"):
        data = json.loads(JsonFormatter().format(record))
    assert "ValueError: boom" in data["exception"]
    assert data["stack"] == "stack"
    assert len(data["trace_id"]) == 32
    assert len(data["span_id"]) == 16


def test_configure_logging_writes_json() -> None:
    stream = io.StringIO()
    configure_logging("debug", stream=stream)
    logging.getLogger("myhealthcheck.test").debug("msg", extra={"k": "v"})
    line = json.loads(stream.getvalue().strip().splitlines()[-1])
    assert line == line | {"message": "msg", "k": "v", "level": "debug"}
    assert logging.getLogger("httpx").level == logging.WARNING
    configure_logging("INFO")


# ------------------------------------------------------------------- models
def test_target_spec_normalization_and_id() -> None:
    spec = TargetSpec(
        name="Web App",
        base_url="HTTP://web:8080/",
        paths=["health", "/health", " "],
        source=Source.KUBERNETES,
        namespace="Prod",
    )
    assert spec.base_url == "http://web:8080"
    assert spec.paths == ["/health"]
    assert spec.id == "kubernetes-prod-web-app"
    assert spec.url_for("ready") == "http://web:8080/ready"
    assert spec.effective_paths(["/x"]) == ["/health"]
    assert TargetSpec(name="a", base_url="http://a").effective_paths(["/x"]) == ["/x"]


@pytest.mark.parametrize("url", ["ftp://a", "a:80", "/path", "http://"])
def test_target_spec_rejects_bad_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        TargetSpec(name="x", base_url=url)


def test_slugify() -> None:
    assert slugify("static", None, "a:80/health") == "static-a-80-health"


@pytest.mark.parametrize(
    ("statuses", "mode", "expected"),
    [
        ([], MatchMode.ANY, Status.UNKNOWN),
        ([Status.UNKNOWN], MatchMode.ALL, Status.UNKNOWN),
        ([Status.DOWN, Status.UP], MatchMode.ANY, Status.UP),
        ([Status.DOWN, Status.DEGRADED], MatchMode.ANY, Status.DEGRADED),
        ([Status.DOWN, Status.DOWN], MatchMode.ANY, Status.DOWN),
        ([Status.UP, Status.UP], MatchMode.ALL, Status.UP),
        ([Status.DOWN, Status.DOWN], MatchMode.ALL, Status.DOWN),
        ([Status.UP, Status.DOWN], MatchMode.ALL, Status.DEGRADED),
        ([Status.UP, Status.DEGRADED], MatchMode.ALL, Status.DEGRADED),
    ],
)
def test_combine_status(statuses: list[Status], mode: MatchMode, expected: Status) -> None:
    assert combine_status(statuses, mode) is expected
