"""Kubernetes service discovery.

Analogy: discovery is a *census taker* walking through the neighbourhoods
(namespaces) and writing down every house (Service) and its doors (ports).
Each ``Service`` port becomes one target whose URL is
``<scheme>://<service>.<namespace>.<domain>:<port>``.

Only Service ports listed in ``DISCOVERY_PORTS`` (default ``80,8080,8888``)
are checked; set ``DISCOVERY_PORTS=*`` to check every TCP port.

Services can fine-tune how they are checked with annotations::

    myhealthcheck.io/enabled: "false"          # skip this service
    myhealthcheck.io/paths: "/healthz,/ready"  # custom paths (instead of defaults)
    myhealthcheck.io/port: "http"              # only this port (name or number),
                                               # even if not in DISCOVERY_PORTS
    myhealthcheck.io/scheme: "https"
    myhealthcheck.io/match: "all"              # any (default) | all
    myhealthcheck.io/expected-status: "200,204"
    myhealthcheck.io/expected-body: "ok"
"""

from __future__ import annotations

import fnmatch
import logging
from typing import Any

from opentelemetry import trace
from pydantic import ValidationError

from myhealthcheck.config import Settings, split_csv
from myhealthcheck.models import MatchMode, Source, TargetSpec

logger = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

ANNOTATION_PREFIX = "myhealthcheck.io/"


class DiscoveryError(RuntimeError):
    """Raised when the Kubernetes API cannot be reached."""


def load_core_api(settings: Settings) -> Any:
    """Create a ``CoreV1Api`` using in-cluster config or a kubeconfig context."""
    from kubernetes import client, config  # imported lazily: heavy and optional

    in_cluster = settings.k8s_in_cluster
    try:
        if in_cluster is not False:
            try:
                config.load_incluster_config()  # type: ignore[no-untyped-call]
                logger.info("using in-cluster Kubernetes configuration")
                return client.CoreV1Api()
            except config.ConfigException:
                if in_cluster:
                    raise
        config.load_kube_config(context=settings.k8s_context)  # type: ignore[no-untyped-call]
        logger.info(
            "using kubeconfig Kubernetes configuration",
            extra={"context": settings.k8s_context or "current-context"},
        )
        return client.CoreV1Api()
    except Exception as exc:
        raise DiscoveryError(f"cannot load Kubernetes configuration: {exc}") from exc


def _matches_any(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


class KubernetesDiscovery:
    """Discover targets from Kubernetes ``Service`` objects."""

    def __init__(self, settings: Settings, api: Any | None = None) -> None:
        self.settings = settings
        self._api = api

    @property
    def api(self) -> Any:
        if self._api is None:
            self._api = load_core_api(self.settings)
        return self._api

    def resolve_namespaces(self) -> list[str]:
        """Return the namespaces to scan.

        * ``DISCOVERY_NAMESPACES`` set: exactly those (glob patterns are
          expanded against the cluster namespaces), minus exclusions.
        * empty: every namespace in the cluster minus exclusions.
        """
        include = self.settings.discovery_namespaces
        exclude = self.settings.discovery_exclude_namespaces
        has_glob = any(any(ch in ns for ch in "*?[") for ns in include)

        if include and not has_glob:
            # No need to list namespaces (works with namespaced RBAC only).
            candidates = include
        else:
            items = self.api.list_namespace().items
            names = [ns.metadata.name for ns in items]
            candidates = [n for n in names if _matches_any(n, include)] if include else names
        return [ns for ns in dict.fromkeys(candidates) if not _matches_any(ns, exclude)]

    def _targets_for_service(self, svc: Any) -> list[TargetSpec]:
        meta, spec = svc.metadata, svc.spec
        annotations: dict[str, str] = {
            k.removeprefix(ANNOTATION_PREFIX): v
            for k, v in (meta.annotations or {}).items()
            if k.startswith(ANNOTATION_PREFIX)
        }
        enabled = annotations.get("enabled", "").strip().lower()
        if enabled == "false" or (self.settings.discovery_require_annotation and enabled != "true"):
            return []
        if getattr(spec, "type", None) == "ExternalName":
            return []

        port_filter = annotations.get("port")
        scheme = annotations.get("scheme", self.settings.discovery_scheme)
        match = annotations.get("match", MatchMode.ANY.value)
        paths = split_csv(annotations.get("paths"))
        expected_status = split_csv(annotations.get("expected-status"))
        host = f"{meta.name}.{meta.namespace}.{self.settings.discovery_service_domain}"

        targets: list[TargetSpec] = []
        for port in spec.ports or []:
            if (port.protocol or "TCP") != "TCP":
                continue
            if port_filter:
                # An explicit annotation wins over DISCOVERY_PORTS.
                if port_filter not in {str(port.port), port.name}:
                    continue
            elif self.settings.discovery_ports and port.port not in self.settings.discovery_ports:
                continue
            try:
                targets.append(
                    TargetSpec(
                        name=f"{meta.name}:{port.port}",
                        base_url=f"{scheme}://{host}:{port.port}",
                        paths=paths,
                        match=match,
                        expected_status=expected_status,
                        expected_body=annotations.get("expected-body"),
                        source=Source.KUBERNETES,
                        namespace=meta.namespace,
                        labels=dict(meta.labels or {}),
                    )
                )
            except ValidationError as exc:
                logger.warning(
                    "ignoring service with invalid annotations",
                    extra={
                        "service": meta.name,
                        "namespace": meta.namespace,
                        "error": str(exc.errors()[0]["msg"]),
                    },
                )
        return targets

    def discover(self) -> list[TargetSpec]:
        """Scan the namespaces and return the discovered targets (blocking call)."""
        with tracer.start_as_current_span("kubernetes_discovery") as span:
            try:
                namespaces = self.resolve_namespaces()
            except DiscoveryError:
                raise
            except Exception as exc:
                raise DiscoveryError(f"cannot list namespaces: {exc}") from exc

            targets: list[TargetSpec] = []
            for namespace in namespaces:
                try:
                    services = self.api.list_namespaced_service(
                        namespace=namespace,
                        label_selector=self.settings.discovery_label_selector or "",
                    )
                except Exception as exc:
                    logger.warning(
                        "cannot list services in namespace",
                        extra={"namespace": namespace, "error": str(exc)},
                    )
                    continue
                for svc in services.items:
                    targets.extend(self._targets_for_service(svc))

            span.set_attribute("myhealthcheck.namespaces", len(namespaces))
            span.set_attribute("myhealthcheck.targets", len(targets))
            logger.info(
                "kubernetes discovery finished",
                extra={"namespaces": namespaces, "targets": len(targets)},
            )
            return targets
