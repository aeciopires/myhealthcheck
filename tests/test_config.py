from pathlib import Path

import pytest
from pydantic import ValidationError

from myhealthcheck.config import (
    DEFAULT_DISCOVERY_PORTS,
    DEFAULT_HEALTH_PATHS,
    Settings,
    load_settings,
    normalize_path,
    split_csv,
)


def test_defaults() -> None:
    s = Settings()
    assert s.check_interval == 60
    assert s.http_port == 8000
    assert s.default_health_paths[:2] == ["/status", "/health"]
    assert s.default_health_paths == list(DEFAULT_HEALTH_PATHS)
    assert s.discovery_ports == list(DEFAULT_DISCOVERY_PORTS) == [80, 8080, 8888]
    assert s.discovery_enabled is False
    assert "kube-system" in s.discovery_exclude_namespaces


def test_env_variables_and_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHECK_INTERVAL", "15")
    monkeypatch.setenv("DEFAULT_HEALTH_PATHS", "healthz, /ping ,,healthz")
    monkeypatch.setenv("DISCOVERY_PORTS", "80,3000")
    monkeypatch.setenv("EXPECTED_STATUS_CODES", "200,204")
    monkeypatch.setenv("DISCOVERY_NAMESPACES", "a,b")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    s = Settings()
    assert s.check_interval == 15
    assert s.default_health_paths == ["/healthz", "/ping"]
    assert s.discovery_ports == [80, 3000]
    assert s.expected_status_codes == [200, 204]
    assert s.discovery_namespaces == ["a", "b"]
    assert s.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["*", "all", "ALL"])
def test_discovery_ports_wildcard(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("DISCOVERY_PORTS", value)
    assert Settings().discovery_ports == []


def test_legacy_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OUTPUT_PORT", "9000")
    monkeypatch.setenv("ENDPOINTS_FILE", "endpoints.txt")
    monkeypatch.setenv("USE_K8S_DISCOVERY_ON_POD", "true")
    monkeypatch.setenv("K8S_NAMESPACE", "default")
    monkeypatch.setenv("K8S_LABEL_SELECTOR", "app=x")
    monkeypatch.setenv("K8S_CLUSTER_NAME", "kind-kind")
    s = Settings()
    assert s.http_port == 9000
    assert s.targets_file == Path("endpoints.txt")
    assert s.discovery_enabled is True
    assert s.discovery_namespaces == ["default"]
    assert s.discovery_label_selector == "app=x"
    assert s.k8s_context == "kind-kind"


def test_empty_values_become_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("K8S_LABEL_SELECTOR", " ")
    monkeypatch.setenv("TARGETS_FILE", "")
    s = Settings()
    assert s.discovery_label_selector is None
    assert s.targets_file is None


def test_invalid_values() -> None:
    with pytest.raises(ValidationError):
        Settings(default_health_paths=[])
    with pytest.raises(ValidationError):
        Settings(check_interval=0)
    with pytest.raises(ValidationError):
        Settings(http_port=70000)


def test_env_file_and_overrides(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("CHECK_INTERVAL=10\nHEALTH_ENDPOINTS=http://a:1/h,http://b:2\n")
    s = load_settings(env, check_interval=None, http_port=8081)
    assert s.check_interval == 10
    assert s.http_port == 8081
    assert s.health_endpoints == ["http://a:1/h", "http://b:2"]
    assert load_settings(http_port=None).http_port == 8000


def test_public_dict_masks_secrets() -> None:
    assert Settings(alert_webhook_url="https://hook/secret").public_dict()["alert_webhook_url"] == (
        "***"
    )
    assert Settings().public_dict()["alert_webhook_url"] is None


def test_helpers() -> None:
    assert split_csv(None) == []
    assert split_csv([1, 2]) == [1, 2]
    assert normalize_path("health") == "/health"
    assert normalize_path("//health ") == "/health"
