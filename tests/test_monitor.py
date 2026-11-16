import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from myhealthcheck.api.app import build_monitor
from myhealthcheck.config import Settings
from myhealthcheck.discovery import DiscoveryError, KubernetesDiscovery
from myhealthcheck.models import Source, Status, TargetSpec
from myhealthcheck.monitor import DiscoveryDisabledError, Monitor
from tests.conftest import FakeCoreV1Api, k8s_service, routes_transport


class BrokenDiscovery(KubernetesDiscovery):
    def discover(self) -> list[TargetSpec]:
        raise DiscoveryError("api down")


@pytest.fixture
def routes() -> dict[str, Any]:
    return {"http://a:1/health": 200, "http://web.ns.svc:80/status": 200}


@pytest.fixture
async def monitor(settings: Settings, routes: dict[str, Any]) -> AsyncIterator[Monitor]:
    settings = settings.model_copy(update={"health_endpoints": ["http://a:1/health"]})
    async with httpx.AsyncClient(transport=routes_transport(routes)) as client:
        mon = build_monitor(settings, client)
        yield mon
        await mon.stop()
        mon.storage.close()


async def test_initialize_checks_static_targets(monitor: Monitor) -> None:
    await monitor.initialize()
    assert monitor.ready
    (state,) = monitor.storage.list_targets()
    assert state.status is Status.UP
    assert monitor.last_cycle_at is not None
    text = monitor.metrics.render().decode()
    assert 'myhealthcheck_targets{source="static",status="up"} 1.0' in text


async def test_alert_on_transition(monitor: Monitor, routes: dict[str, Any]) -> None:
    await monitor.initialize()
    routes["http://a:1/health"] = 500
    await monitor.run_checks()
    text = monitor.metrics.render().decode()
    assert 'myhealthcheck_alerts_total{severity="critical"} 1.0' in text
    (state,) = monitor.storage.list_targets()
    assert state.status is Status.DOWN


async def test_run_checks_subset_and_preferred_path(monitor: Monitor) -> None:
    extra = TargetSpec(name="b", base_url="http://b:2", source=Source.API)
    await monitor.add_target(extra)
    await monitor.load_static_targets()
    results = await monitor.run_checks([extra.id])
    assert [r.target_id for r in results] == [extra.id]
    assert monitor.last_cycle_at is None  # partial runs do not count as a cycle


async def test_add_remove_target(monitor: Monitor) -> None:
    spec = TargetSpec(name="b", base_url="http://b:2", source=Source.API)
    await monitor.add_target(spec)
    await monitor.check_spec(spec)
    assert await monitor.remove_target(spec.id) is True
    assert await monitor.remove_target(spec.id) is False


async def test_check_spec_without_persisting(monitor: Monitor) -> None:
    spec = TargetSpec(name="a", base_url="http://a:1", paths=["/health"])
    check = await monitor.check_spec(spec, persist=False)
    assert check.status is Status.UP
    assert monitor.storage.list_targets() == []


async def test_record_skips_removed_target(monitor: Monitor) -> None:
    spec = TargetSpec(name="ghost", base_url="http://ghost:1")
    await monitor.check_spec(spec)  # not stored -> silently ignored
    assert monitor.storage.get_target(spec.id) is None


async def test_discovery_disabled(monitor: Monitor) -> None:
    with pytest.raises(DiscoveryDisabledError):
        await monitor.run_discovery()


async def test_discovery_sync_and_removal(settings: Settings, routes: dict[str, Any]) -> None:
    api = FakeCoreV1Api({"ns": [k8s_service("web", "ns"), k8s_service("old", "ns")]})
    disc = KubernetesDiscovery(settings.model_copy(update={"discovery_namespaces": ["ns"]}), api)
    async with httpx.AsyncClient(transport=routes_transport(routes)) as client:
        mon = build_monitor(settings, client, discovery=disc)
        try:
            await mon.initialize()
            ids = {s.id for s in mon.storage.list_targets()}
            assert ids == {"kubernetes-ns-web-80", "kubernetes-ns-old-80"}
            assert 'target="kubernetes-ns-old-80"' in mon.metrics.render().decode()

            api.services["ns"] = [k8s_service("web", "ns")]
            assert await mon.run_discovery() == 1
            assert {s.id for s in mon.storage.list_targets()} == {"kubernetes-ns-web-80"}
            assert 'target="kubernetes-ns-old-80"' not in mon.metrics.render().decode()
            assert mon.last_discovery_at is not None
        finally:
            mon.storage.close()


async def test_discovery_failure_is_tolerated_on_start(settings: Settings) -> None:
    async with httpx.AsyncClient(transport=routes_transport({})) as client:
        mon = build_monitor(settings, client, discovery=BrokenDiscovery(settings))
        try:
            await mon.initialize()
            assert mon.ready
            assert mon.last_discovery_error == "api down"
            assert 'myhealthcheck_discovery_runs_total{result="error"} 1.0' in (
                mon.metrics.render().decode()
            )
        finally:
            mon.storage.close()


async def test_build_monitor_creates_discovery_when_enabled(settings: Settings) -> None:
    enabled = settings.model_copy(update={"discovery_enabled": True})
    async with httpx.AsyncClient() as client:
        mon = build_monitor(enabled, client)
        assert isinstance(mon.discovery, KubernetesDiscovery)
        mon.storage.close()


async def test_start_and_stop_loops(settings: Settings, tmp_path: Path) -> None:
    fast = settings.model_copy(update={"check_interval": 0.01, "discovery_interval": 0.01})
    api = FakeCoreV1Api({"ns": []})
    disc = KubernetesDiscovery(fast.model_copy(update={"discovery_namespaces": ["ns"]}), api)
    async with httpx.AsyncClient(transport=routes_transport({})) as client:
        mon = build_monitor(fast, client, discovery=disc)
        await mon.start()
        await asyncio.sleep(0.1)
        assert len(mon._tasks) == 3  # first cycle + checks loop + discovery loop
        assert mon.ready
        await mon.stop()
        assert not mon.ready
        assert mon._tasks == []
        mon.storage.close()


async def test_loop_survives_errors(monitor: Monitor) -> None:
    calls = {"n": 0}

    async def failing() -> None:
        calls["n"] += 1
        raise RuntimeError("boom")

    task = asyncio.create_task(monitor._loop("test", 0.001, failing))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls["n"] > 1


async def test_start_tolerates_initialization_failure(
    monitor: Monitor, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom() -> None:
        raise RuntimeError("init failed")

    monkeypatch.setattr(monitor, "initialize", boom)
    await monitor.start()
    await monitor._tasks[0]
    assert monitor.ready
