"""Application settings.

Every option can be set through an environment variable, a ``.env`` file or a
CLI flag (CLI > environment > ``.env`` > defaults). Legacy variable names used
by the 2.x releases are still accepted through ``AliasChoices`` so existing
deployments keep working.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

#: Paths probed on every target that does not declare its own ``paths``.
#: Order matters: in ``any`` mode the paths are probed in this order and the
#: probe stops at the first healthy answer.
DEFAULT_HEALTH_PATHS: tuple[str, ...] = (
    "/status",  # generic
    "/health",  # generic / kube-pires / many frameworks
    "/healthz",  # Kubernetes style
    "/livez",  # Kubernetes style
    "/readyz",  # Kubernetes style
    "/ready",  # Loki, Tempo
    "/ping",  # Traefik, InfluxDB
    "/-/healthy",  # Prometheus, Alertmanager
    "/-/ready",  # Prometheus, Alertmanager
    "/api/health",  # Grafana
    "/health/live",  # ASP.NET Core / MicroProfile style
    "/health/ready",  # ASP.NET Core / MicroProfile style
    "/actuator/health",  # Spring Boot
    "/q/health",  # Quarkus
    "/_health",  # misc
    "/healthcheck",  # misc
)

#: Service ports scanned by Kubernetes discovery by default.
DEFAULT_DISCOVERY_PORTS: tuple[int, ...] = (80, 8080, 8888)

#: Namespaces that are skipped by default when discovering *all* namespaces.
DEFAULT_EXCLUDED_NAMESPACES: tuple[str, ...] = ("kube-system", "kube-public", "kube-node-lease")

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
CsvStrList = Annotated[list[str], NoDecode]
CsvIntList = Annotated[list[int], NoDecode]


def _alias(*names: str) -> AliasChoices:
    return AliasChoices(*names)


def split_csv(value: Any) -> Any:
    """Turn ``"a, b,,c"`` into ``["a", "b", "c"]``; leave lists untouched."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def normalize_path(path: str) -> str:
    """Guarantee that an HTTP path starts with a single ``/``."""
    path = path.strip()
    return "/" + path.lstrip("/")


class Settings(BaseSettings):
    """Runtime configuration of myhealthcheck."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        validate_by_name=True,
        validate_by_alias=True,
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ server
    http_host: str = Field("0.0.0.0", validation_alias=_alias("HTTP_HOST"))
    http_port: int = Field(
        8000, ge=1, le=65535, validation_alias=_alias("HTTP_PORT", "OUTPUT_PORT")
    )
    log_level: LogLevel = Field("INFO", validation_alias=_alias("LOG_LEVEL"))

    # ------------------------------------------------------------------ checks
    #: Seconds between two check cycles (default: every 60 seconds).
    check_interval: float = Field(60.0, ge=1, validation_alias=_alias("CHECK_INTERVAL"))
    request_timeout: float = Field(5.0, gt=0, validation_alias=_alias("REQUEST_TIMEOUT"))
    response_time_threshold: float = Field(
        3.0, gt=0, validation_alias=_alias("RESPONSE_TIME_THRESHOLD")
    )
    retry_attempts: int = Field(3, ge=1, le=10, validation_alias=_alias("RETRY_ATTEMPTS"))
    retry_delay: float = Field(2.0, ge=0, validation_alias=_alias("RETRY_DELAY"))
    max_concurrency: int = Field(50, ge=1, validation_alias=_alias("MAX_CONCURRENCY"))
    verify_tls: bool = Field(True, validation_alias=_alias("VERIFY_TLS"))
    default_health_paths: CsvStrList = Field(
        default_factory=lambda: list(DEFAULT_HEALTH_PATHS),
        validation_alias=_alias("DEFAULT_HEALTH_PATHS"),
    )
    expected_status_codes: CsvIntList = Field(
        default_factory=list, validation_alias=_alias("EXPECTED_STATUS_CODES")
    )

    # ---------------------------------------------------------- static targets
    health_endpoints: CsvStrList = Field(
        default_factory=list, validation_alias=_alias("HEALTH_ENDPOINTS")
    )
    targets_file: Path | None = Field(
        None, validation_alias=_alias("TARGETS_FILE", "ENDPOINTS_FILE")
    )

    # ---------------------------------------------------- kubernetes discovery
    discovery_enabled: bool = Field(
        False,
        validation_alias=_alias(
            "DISCOVERY_ENABLED", "USE_K8S_DISCOVERY", "USE_K8S_DISCOVERY_ON_POD"
        ),
    )
    discovery_namespaces: CsvStrList = Field(
        default_factory=list, validation_alias=_alias("DISCOVERY_NAMESPACES", "K8S_NAMESPACE")
    )
    discovery_exclude_namespaces: CsvStrList = Field(
        default_factory=lambda: list(DEFAULT_EXCLUDED_NAMESPACES),
        validation_alias=_alias("DISCOVERY_EXCLUDE_NAMESPACES"),
    )
    #: Only Service ports in this list are checked. Empty list = every TCP port.
    #: A service can opt in to any other port with the ``myhealthcheck.io/port``
    #: annotation.
    discovery_ports: CsvIntList = Field(
        default_factory=lambda: list(DEFAULT_DISCOVERY_PORTS),
        validation_alias=_alias("DISCOVERY_PORTS"),
    )
    discovery_label_selector: str | None = Field(
        None, validation_alias=_alias("DISCOVERY_LABEL_SELECTOR", "K8S_LABEL_SELECTOR")
    )
    discovery_require_annotation: bool = Field(
        False, validation_alias=_alias("DISCOVERY_REQUIRE_ANNOTATION")
    )
    discovery_interval: float = Field(300.0, ge=5, validation_alias=_alias("DISCOVERY_INTERVAL"))
    discovery_scheme: Literal["http", "https"] = Field(
        "http", validation_alias=_alias("DISCOVERY_SCHEME")
    )
    discovery_service_domain: str = Field(
        "svc", validation_alias=_alias("DISCOVERY_SERVICE_DOMAIN")
    )
    k8s_context: str | None = Field(
        None, validation_alias=_alias("K8S_CONTEXT", "K8S_CLUSTER_NAME")
    )
    k8s_in_cluster: bool | None = Field(None, validation_alias=_alias("K8S_IN_CLUSTER"))

    # ----------------------------------------------------------------- storage
    db_path: Path = Field(Path("data/myhealthcheck.db"), validation_alias=_alias("DB_PATH"))
    history_retention: int = Field(100, ge=1, validation_alias=_alias("HISTORY_RETENTION"))

    # --------------------------------------------------------- observability
    otel_enabled: bool = Field(False, validation_alias=_alias("OTEL_ENABLED"))
    otel_service_name: str = Field("myhealthcheck", validation_alias=_alias("OTEL_SERVICE_NAME"))

    # ---------------------------------------------------------------- alerting
    alert_webhook_url: str | None = Field(None, validation_alias=_alias("ALERT_WEBHOOK_URL"))

    # -------------------------------------------------------------- validators
    @field_validator(
        "default_health_paths",
        "health_endpoints",
        "discovery_namespaces",
        "discovery_exclude_namespaces",
        "expected_status_codes",
        "discovery_ports",
        mode="before",
    )
    @classmethod
    def _split_lists(cls, value: Any) -> Any:
        values = split_csv(value)
        # "*" or "all" means "no filter" (useful for DISCOVERY_PORTS).
        if isinstance(values, list) and [str(v).lower() for v in values] in (["*"], ["all"]):
            return []
        return values

    @field_validator("default_health_paths")
    @classmethod
    def _normalize_paths(cls, value: list[str]) -> list[str]:
        paths = list(dict.fromkeys(normalize_path(p) for p in value))
        if not paths:
            raise ValueError("default_health_paths must contain at least one path")
        return paths

    @field_validator("log_level", mode="before")
    @classmethod
    def _upper_level(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value

    @field_validator("discovery_label_selector", "k8s_context", "alert_webhook_url", mode="before")
    @classmethod
    def _empty_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("targets_file", mode="before")
    @classmethod
    def _empty_path_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    def public_dict(self) -> dict[str, Any]:
        """Settings safe to expose through the API (secrets masked)."""
        data = self.model_dump(mode="json")
        if data.get("alert_webhook_url"):
            data["alert_webhook_url"] = "***"
        return data


def load_settings(env_file: Path | str | None = None, **overrides: Any) -> Settings:
    """Build :class:`Settings` from an optional ``.env`` file plus explicit overrides.

    ``overrides`` with value ``None`` are ignored so that unset CLI flags never
    shadow environment variables.
    """
    clean = {key: value for key, value in overrides.items() if value is not None}
    if env_file is not None:
        return Settings(_env_file=env_file, **clean)
    return Settings(**clean)
