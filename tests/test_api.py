from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from myhealthcheck.api.app import create_app
from myhealthcheck.config import Settings
from myhealthcheck.discovery import DiscoveryError, KubernetesDiscovery
from myhealthcheck.models import TargetSpec
from tests.conftest import FakeCoreV1Api, k8s_service, routes_transport

ROUTES: dict[str, Any] = {
    "http://a:1/health": 200,
    "http://b:2/status": 200,
    "http://web.ns.svc:80/status": 200,
}


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    settings = settings.model_copy(
        update={"health_endpoints": ["http://a:1/health"], "alert_webhook_url": "http://hook"}
    )
    app = create_app(settings, transport=routes_transport(ROUTES), run_background=False)
    with TestClient(app) as test_client:
        yield test_client


def test_health_endpoints(client: TestClient) -> None:
    assert client.get("/health/live").json()["status"] == "ok"
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"storage": True, "initialized": True}
    assert client.get("/health").json()["storage"] == "file"
    assert client.get("/status").json()["status"] == "ok"


def test_ready_returns_503_before_first_cycle(client: TestClient) -> None:
    client.app.state.monitor.ready = False  # type: ignore[attr-defined]
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"


def test_metrics(client: TestClient) -> None:
    client.get("/api/v1/targets")
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "myhealthcheck_target_up" in response.text
    assert 'route="/api/v1/targets"' in response.text
    assert "myhealthcheck_build_info" in response.text


def test_list_and_filter_targets(client: TestClient) -> None:
    targets = client.get("/api/v1/targets").json()
    assert len(targets) == 1
    assert targets[0]["status"] == "up"
    assert targets[0]["endpoints"][0]["url"] == "http://a:1/health"
    assert client.get("/api/v1/targets", params={"status": "down"}).json() == []
    assert len(client.get("/api/v1/targets", params={"source": "static"}).json()) == 1
    assert client.get("/api/v1/targets", params={"status": "bogus"}).status_code == 422


def test_create_get_history_check_delete(client: TestClient) -> None:
    created = client.post(
        "/api/v1/targets", json={"url": "http:b:2/status", "labels": {"team": "x"}}
    )
    assert created.status_code == 201
    body = created.json()
    assert body["source"] == "api"
    assert body["status"] == "up"
    assert body["labels"] == {"team": "x"}
    target_id = body["id"]

    assert client.get(f"/api/v1/targets/{target_id}").json()["name"] == "b:2/status"
    assert client.post(f"/api/v1/targets/{target_id}/check").json()["status"] == "up"
    history = client.get(f"/api/v1/targets/{target_id}/history", params={"limit": 10}).json()
    assert len(history) == 2

    assert client.delete(f"/api/v1/targets/{target_id}").status_code == 204
    for method, path in [
        ("GET", f"/api/v1/targets/{target_id}"),
        ("DELETE", f"/api/v1/targets/{target_id}"),
        ("GET", f"/api/v1/targets/{target_id}/history"),
        ("POST", f"/api/v1/targets/{target_id}/check"),
    ]:
        assert client.request(method, path).status_code == 404


def test_create_without_check(client: TestClient) -> None:
    body = client.post(
        "/api/v1/targets",
        params={"check_now": "false"},
        json={"name": "custom", "url": "http://c:3", "paths": ["/ping"]},
    ).json()
    assert body["status"] == "unknown"
    assert body["paths"] == ["/ping"]


def test_create_with_paths_keeps_url_prefix(client: TestClient) -> None:
    body = client.post(
        "/api/v1/targets",
        params={"check_now": "false"},
        json={"url": "http://c:3/api", "paths": ["/health"]},
    ).json()
    assert body["base_url"] == "http://c:3/api"
    assert body["paths"] == ["/health"]


def test_create_invalid_url(client: TestClient) -> None:
    response = client.post("/api/v1/targets", json={"url": "ftp://nope"})
    assert response.status_code == 422


def test_run_checks_and_probe(client: TestClient) -> None:
    run = client.post("/api/v1/checks").json()
    assert run["checked"] == 1
    probe = client.post("/api/v1/probe", json={"url": "http://b:2/status"}).json()
    assert probe["status"] == "up"
    assert len(client.get("/api/v1/targets").json()) == 1  # probe is not stored


def test_summary_and_config(client: TestClient) -> None:
    summary = client.get("/api/v1/summary").json()
    assert summary["total"] == 1
    assert summary["by_status"]["up"] == 1
    assert summary["storage_backend"] == "file"
    assert summary["discovery_enabled"] is False
    settings = client.get("/api/v1/config").json()["settings"]
    assert settings["alert_webhook_url"] == "***"
    assert settings["check_interval"] == 1


def test_discovery_disabled_returns_409(client: TestClient) -> None:
    assert client.post("/api/v1/discovery").status_code == 409


def test_discovery_endpoint(settings: Settings) -> None:
    api = FakeCoreV1Api({"ns": [k8s_service("web", "ns")]})
    disc = KubernetesDiscovery(settings.model_copy(update={"discovery_namespaces": ["ns"]}), api)
    app = create_app(
        settings, transport=routes_transport(ROUTES), discovery=disc, run_background=False
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/discovery")
        assert response.status_code == 200
        assert response.json()["discovered"] == 1
        (target,) = client.get("/api/v1/targets", params={"namespace": "ns"}).json()
        assert target["status"] == "up"
        assert target["matched_path"] == "/status"


def test_discovery_error_returns_502(settings: Settings) -> None:
    class Broken(KubernetesDiscovery):
        def discover(self) -> list[TargetSpec]:
            raise DiscoveryError("boom")

    app = create_app(
        settings, transport=routes_transport({}), discovery=Broken(settings), run_background=False
    )
    with TestClient(app) as client:
        assert client.post("/api/v1/discovery").status_code == 502


def test_openapi_document(client: TestClient) -> None:
    spec = client.get("/openapi.json").json()
    assert spec["info"]["title"] == "myhealthcheck"
    for path in ("/api/v1/targets", "/api/v1/probe", "/api/v1/discovery", "/health/ready"):
        assert path in spec["paths"]
    assert client.get("/docs").status_code == 200


def test_background_mode(settings: Settings) -> None:
    app = create_app(settings, transport=routes_transport({}))
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        # first cycle + periodic check loop
        assert len(client.app.state.monitor._tasks) == 2  # type: ignore[attr-defined]
