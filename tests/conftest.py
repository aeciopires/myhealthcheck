"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from myhealthcheck.config import Settings
from myhealthcheck.storage import Storage

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make sure the developer's environment never leaks into the tests."""
    import os

    for key in list(os.environ):
        if key.startswith(("DISCOVERY_", "K8S_", "OTEL_", "USE_K8S")) or key in {
            "HEALTH_ENDPOINTS",
            "TARGETS_FILE",
            "ENDPOINTS_FILE",
            "CHECK_INTERVAL",
            "DB_PATH",
            "HTTP_PORT",
            "OUTPUT_PORT",
            "LOG_LEVEL",
            "DEFAULT_HEALTH_PATHS",
            "ALERT_WEBHOOK_URL",
        }:
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "db" / "test.db",
        retry_delay=0,
        retry_attempts=2,
        check_interval=1,
        response_time_threshold=1.0,
        default_health_paths=["/status", "/health"],
    )


@pytest.fixture
def storage(settings: Settings) -> Iterator[Storage]:
    store = Storage(settings.db_path, history_retention=3)
    yield store
    store.close()


def routes_transport(routes: dict[str, Any]) -> httpx.MockTransport:
    """Build a transport answering by full URL.

    Values may be an ``int`` (status code), an ``httpx.Response``, an exception
    instance (raised) or a callable ``request -> Response``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx drops default ports (":80"), so compare normalized URLs.
        normalized = {str(httpx.URL(url)): value for url, value in routes.items()}
        value = normalized.get(str(request.url), 404)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            result: httpx.Response = value(request)
            return result
        if isinstance(value, httpx.Response):
            return value
        return httpx.Response(int(value), text="ok")

    return httpx.MockTransport(handler)


# ------------------------------------------------------------ kubernetes fakes
def k8s_service(
    name: str,
    namespace: str,
    ports: list[tuple[int, str | None, str]] | None = None,
    annotations: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    svc_type: str = "ClusterIP",
) -> SimpleNamespace:
    ports = ports if ports is not None else [(80, "http", "TCP")]
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name, namespace=namespace, annotations=annotations, labels=labels
        ),
        spec=SimpleNamespace(
            type=svc_type,
            ports=[SimpleNamespace(port=p, name=n, protocol=proto) for p, n, proto in ports],
        ),
    )


class FakeCoreV1Api:
    """Minimal stand-in for ``kubernetes.client.CoreV1Api``."""

    def __init__(
        self,
        services: dict[str, list[SimpleNamespace]],
        namespaces: list[str] | None = None,
        fail_namespaces: set[str] | None = None,
        fail_list_namespaces: bool = False,
    ) -> None:
        self.services = services
        self.namespaces = namespaces if namespaces is not None else list(services)
        self.fail_namespaces = fail_namespaces or set()
        self.fail_list_namespaces = fail_list_namespaces
        self.list_namespace_calls = 0
        self.selectors: list[str] = []

    def list_namespace(self) -> SimpleNamespace:
        self.list_namespace_calls += 1
        if self.fail_list_namespaces:
            raise RuntimeError("forbidden")
        return SimpleNamespace(
            items=[SimpleNamespace(metadata=SimpleNamespace(name=n)) for n in self.namespaces]
        )

    def list_namespaced_service(self, namespace: str, label_selector: str = "") -> SimpleNamespace:
        self.selectors.append(label_selector)
        if namespace in self.fail_namespaces:
            raise RuntimeError("forbidden")
        return SimpleNamespace(items=self.services.get(namespace, []))
