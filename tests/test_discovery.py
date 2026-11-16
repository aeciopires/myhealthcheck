from typing import Any

import pytest

from myhealthcheck import discovery as discovery_module
from myhealthcheck.config import Settings
from myhealthcheck.discovery import DiscoveryError, KubernetesDiscovery, load_core_api
from myhealthcheck.models import MatchMode, Source
from tests.conftest import FakeCoreV1Api, k8s_service


def make(settings: Settings, api: FakeCoreV1Api, **overrides: Any) -> KubernetesDiscovery:
    return KubernetesDiscovery(settings.model_copy(update=overrides), api=api)


def test_all_namespaces_minus_default_exclusions(settings: Settings) -> None:
    api = FakeCoreV1Api(
        {
            "default": [k8s_service("web", "default")],
            "kube-system": [k8s_service("dns", "kube-system")],
            "apps": [k8s_service("api", "apps", ports=[(8080, "http", "TCP")])],
        }
    )
    targets = make(settings, api).discover()
    assert sorted(t.base_url for t in targets) == [
        "http://api.apps.svc:8080",
        "http://web.default.svc:80",
    ]
    assert all(t.source is Source.KUBERNETES for t in targets)
    assert targets[0].namespace in {"default", "apps"}


def test_explicit_namespaces_do_not_list_namespaces(settings: Settings) -> None:
    api = FakeCoreV1Api({"a": [k8s_service("x", "a")], "b": []})
    disc = make(settings, api, discovery_namespaces=["a", "b", "a"])
    assert disc.resolve_namespaces() == ["a", "b"]
    assert api.list_namespace_calls == 0


def test_glob_include_and_exclude(settings: Settings) -> None:
    api = FakeCoreV1Api({}, namespaces=["team-a", "team-b", "team-a-dev", "other"])
    disc = make(
        settings,
        api,
        discovery_namespaces=["team-*"],
        discovery_exclude_namespaces=["*-dev"],
    )
    assert disc.resolve_namespaces() == ["team-a", "team-b"]


def test_ports_filter_default_and_wildcard(settings: Settings) -> None:
    svc = k8s_service(
        "multi",
        "ns",
        ports=[(80, "http", "TCP"), (3000, "app", "TCP"), (8888, None, None), (53, "dns", "UDP")],
    )
    api = FakeCoreV1Api({"ns": [svc]})
    default = make(settings, api, discovery_namespaces=["ns"]).discover()
    assert sorted(t.name for t in default) == ["multi:80", "multi:8888"]

    every = make(settings, api, discovery_namespaces=["ns"], discovery_ports=[]).discover()
    assert sorted(t.name for t in every) == ["multi:3000", "multi:80", "multi:8888"]


def test_annotations(settings: Settings) -> None:
    annotated = k8s_service(
        "custom",
        "ns",
        ports=[(80, "http", "TCP"), (3000, "metrics", "TCP")],
        annotations={
            "myhealthcheck.io/port": "metrics",
            "myhealthcheck.io/paths": "/livez, readyz",
            "myhealthcheck.io/scheme": "https",
            "myhealthcheck.io/match": "all",
            "myhealthcheck.io/expected-status": "200,204",
            "myhealthcheck.io/expected-body": "ok",
            "other.io/x": "ignored",
        },
        labels={"app": "custom"},
    )
    disabled = k8s_service("off", "ns", annotations={"myhealthcheck.io/enabled": "false"})
    external = k8s_service("ext", "ns", svc_type="ExternalName")
    no_ports = k8s_service("none", "ns", ports=[])
    no_ports.spec.ports = None
    api = FakeCoreV1Api({"ns": [annotated, disabled, external, no_ports]})
    (target,) = make(settings, api, discovery_namespaces=["ns"]).discover()
    assert target.base_url == "https://custom.ns.svc:3000"
    assert target.paths == ["/livez", "/readyz"]
    assert target.match is MatchMode.ALL
    assert target.expected_status == [200, 204]
    assert target.expected_body == "ok"
    assert target.labels == {"app": "custom"}


def test_require_annotation_and_invalid_annotation(settings: Settings) -> None:
    opted_in = k8s_service("in", "ns", annotations={"myhealthcheck.io/enabled": "true"})
    not_opted = k8s_service("out", "ns")
    invalid = k8s_service(
        "bad",
        "ns",
        annotations={"myhealthcheck.io/enabled": "true", "myhealthcheck.io/match": "sometimes"},
    )
    api = FakeCoreV1Api({"ns": [opted_in, not_opted, invalid]})
    targets = make(
        settings, api, discovery_namespaces=["ns"], discovery_require_annotation=True
    ).discover()
    assert [t.name for t in targets] == ["in:80"]


def test_label_selector_and_domain(settings: Settings) -> None:
    api = FakeCoreV1Api({"ns": [k8s_service("web", "ns")]})
    (target,) = make(
        settings,
        api,
        discovery_namespaces=["ns"],
        discovery_label_selector="app=web",
        discovery_service_domain="svc.cluster.local",
    ).discover()
    assert api.selectors == ["app=web"]
    assert target.base_url == "http://web.ns.svc.cluster.local:80"


def test_namespace_errors(settings: Settings) -> None:
    api = FakeCoreV1Api({"ok": [k8s_service("web", "ok")]}, fail_namespaces={"bad"})
    targets = make(settings, api, discovery_namespaces=["bad", "ok"]).discover()
    assert [t.namespace for t in targets] == ["ok"]

    failing = FakeCoreV1Api({}, fail_list_namespaces=True)
    with pytest.raises(DiscoveryError, match="cannot list namespaces"):
        make(settings, failing).discover()


def test_api_is_lazily_loaded(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeCoreV1Api({})
    monkeypatch.setattr(discovery_module, "load_core_api", lambda _s: fake)
    disc = KubernetesDiscovery(settings)
    assert disc.api is fake
    assert disc.api is fake


def test_discovery_error_from_api_loader_is_propagated(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_settings: Settings) -> None:
        raise DiscoveryError("no config")

    monkeypatch.setattr(discovery_module, "load_core_api", boom)
    with pytest.raises(DiscoveryError, match="no config"):
        KubernetesDiscovery(settings).discover()


# --------------------------------------------------------------- load_core_api
class _FakeConfig:
    class ConfigException(Exception):  # noqa: N818 - mirrors kubernetes naming
        pass

    def __init__(self, incluster_ok: bool, kube_ok: bool = True) -> None:
        self.incluster_ok = incluster_ok
        self.kube_ok = kube_ok
        self.contexts: list[str | None] = []

    def load_incluster_config(self) -> None:
        if not self.incluster_ok:
            raise self.ConfigException("not in cluster")

    def load_kube_config(self, context: str | None = None) -> None:
        self.contexts.append(context)
        if not self.kube_ok:
            raise FileNotFoundError("no kubeconfig")


@pytest.fixture
def fake_k8s(monkeypatch: pytest.MonkeyPatch) -> Any:
    import kubernetes

    def install(cfg: _FakeConfig) -> _FakeConfig:
        monkeypatch.setattr(kubernetes, "config", cfg)
        monkeypatch.setattr(kubernetes.client, "CoreV1Api", lambda: "api")
        return cfg

    return install


def test_load_core_api_in_cluster(settings: Settings, fake_k8s: Any) -> None:
    cfg = fake_k8s(_FakeConfig(incluster_ok=True))
    assert load_core_api(settings) == "api"
    assert cfg.contexts == []


def test_load_core_api_falls_back_to_kubeconfig(settings: Settings, fake_k8s: Any) -> None:
    cfg = fake_k8s(_FakeConfig(incluster_ok=False))
    assert load_core_api(settings.model_copy(update={"k8s_context": "kind"})) == "api"
    assert cfg.contexts == ["kind"]


def test_load_core_api_kubeconfig_only(settings: Settings, fake_k8s: Any) -> None:
    cfg = fake_k8s(_FakeConfig(incluster_ok=True))
    load_core_api(settings.model_copy(update={"k8s_in_cluster": False}))
    assert cfg.contexts == [None]


def test_load_core_api_errors(settings: Settings, fake_k8s: Any) -> None:
    fake_k8s(_FakeConfig(incluster_ok=False))
    with pytest.raises(DiscoveryError):
        load_core_api(settings.model_copy(update={"k8s_in_cluster": True}))
    fake_k8s(_FakeConfig(incluster_ok=False, kube_ok=False))
    with pytest.raises(DiscoveryError, match="no kubeconfig"):
        load_core_api(settings)
