import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from myhealthcheck import cli
from myhealthcheck.config import Settings
from myhealthcheck.telemetry import setup_tracing, shutdown_tracing
from tests.conftest import routes_transport


# ------------------------------------------------------------------------ cli
def test_parser_maps_flags_to_settings(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CHECK_INTERVAL=42\nHTTP_PORT=9999\n")
    args = cli.build_parser().parse_args(
        [
            "-f", str(env),
            "-t", "targets.yaml",
            "-u", "http://a:1/h",
            "-u", "http:b:2/s",
            "--paths", "/x,/y",
            "-o", "8081",
            "-p",
            "-n", "a,b",
            "-x", "kube-*",
            "-l", "app=web",
            "--ports", "80,3000",
            "-k", "kind",
            "--log-level", "debug",
            "--db-path", str(tmp_path / "db.sqlite"),
        ]
    )  # fmt: skip
    s = cli.settings_from_args(args)
    assert s.check_interval == 42
    assert s.http_port == 8081
    assert s.targets_file == Path("targets.yaml")
    assert s.health_endpoints == ["http://a:1/h", "http:b:2/s"]
    assert s.default_health_paths == ["/x", "/y"]
    assert s.discovery_enabled is True
    assert s.k8s_in_cluster is True
    assert s.discovery_namespaces == ["a", "b"]
    assert s.discovery_exclude_namespaces == ["kube-*"]
    assert s.discovery_label_selector == "app=web"
    assert s.discovery_ports == [80, 3000]
    assert s.k8s_context == "kind"
    assert s.log_level == "DEBUG"


def test_parser_defaults_do_not_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CHECK_INTERVAL", "7")
    s = cli.settings_from_args(cli.build_parser().parse_args([]))
    assert s.discovery_enabled is True
    assert s.check_interval == 7
    assert s.k8s_in_cluster is None


async def test_run_once(settings: Settings) -> None:
    settings = settings.model_copy(update={"health_endpoints": ["http://a:1/health"]})
    code, report = await cli.run_once(settings, routes_transport({"http://a:1/health": 200}))
    assert code == cli.EXIT_OK
    assert report["summary"]["by_status"]["up"] == 1
    assert report["targets"][0]["status"] == "up"
    assert "spec" not in report["targets"][0]

    code, _ = await cli.run_once(settings, routes_transport({}))
    assert code == cli.EXIT_DOWN


def test_main_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    async def fake_run_once(settings: Settings) -> tuple[int, dict[str, object]]:
        return cli.EXIT_DOWN, {"targets": [], "interval": settings.check_interval}

    monkeypatch.setattr(cli, "run_once", fake_run_once)
    code = cli.main(["--once", "-i", "5", "--db-path", str(tmp_path / "x.db")])
    out = capsys.readouterr()
    assert code == cli.EXIT_DOWN
    assert json.loads(out.out) == {"targets": [], "interval": 5.0}
    assert '"configuration loaded"' in out.err  # logs go to stderr in --once mode


def test_main_serve(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[Settings] = []
    monkeypatch.setattr(cli, "serve", served.append)
    assert cli.main(["-o", "8123"]) == cli.EXIT_OK
    assert served[0].http_port == 8123


def test_main_invalid_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECK_INTERVAL", "not-a-number")
    assert cli.main([]) == cli.EXIT_CONFIG


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "myhealthcheck" in capsys.readouterr().out


# ------------------------------------------------------------------ telemetry
def test_tracing_disabled_by_default(settings: Settings) -> None:
    assert setup_tracing(settings) is None
    shutdown_tracing(None)


async def test_tracing_exports_spans(settings: Settings) -> None:
    exporter = InMemorySpanExporter()
    app = FastAPI()

    @app.get("/ping")
    async def ping() -> dict[str, str]:
        async with httpx.AsyncClient(transport=routes_transport({"http://x/": 200})) as client:
            await client.get("http://x/")
        return {"ok": "yes"}

    provider = setup_tracing(
        settings.model_copy(update={"otel_enabled": True}), app, exporter=exporter
    )
    assert provider is not None
    with TestClient(app) as client:
        assert client.get("/ping").status_code == 200
    provider.force_flush()
    names = {span.name for span in exporter.get_finished_spans()}
    shutdown_tracing(provider)
    assert "GET /ping" in names
    assert any(name.startswith("GET") and name != "GET /ping" for name in names)


def test_tracing_default_exporter(settings: Settings) -> None:
    provider = setup_tracing(settings.model_copy(update={"otel_enabled": True}))
    assert provider is not None
    shutdown_tracing(provider)
