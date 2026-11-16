"""Domain models shared by the checker, storage, discovery and API layers."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from myhealthcheck.config import normalize_path

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def slugify(*parts: str | None) -> str:
    """Build a URL-safe identifier from the non-empty ``parts``."""
    joined = "-".join(p for p in parts if p)
    return _SLUG_RE.sub("-", joined.lower()).strip("-")


class Status(StrEnum):
    """Health status of an endpoint or a target."""

    UP = "up"
    DEGRADED = "degraded"  # answered correctly, but slower than the threshold
    DOWN = "down"
    UNKNOWN = "unknown"  # never checked yet


class Source(StrEnum):
    """Where a target definition came from."""

    STATIC = "static"  # targets file / HEALTH_ENDPOINTS
    KUBERNETES = "kubernetes"  # service discovery
    API = "api"  # created with POST /api/v1/targets


class MatchMode(StrEnum):
    """How the endpoint results of a target are combined."""

    ANY = "any"  # healthy when at least one path answers (path probing)
    ALL = "all"  # healthy only when every path answers


StatusCode = Annotated[int, Field(ge=100, le=599)]


class TargetSpec(BaseModel):
    """Definition of a monitored system (what to check, not its state)."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=253, examples=["kube-pires"])
    base_url: str = Field(examples=["http://kube-pires:3000"])
    paths: list[str] = Field(
        default_factory=list,
        description="Health paths to check. Empty means the default health paths.",
        examples=[["/health"]],
    )
    match: MatchMode = MatchMode.ANY
    expected_status: list[StatusCode] = Field(
        default_factory=list,
        description="Accepted HTTP status codes. Empty means any 2xx or 3xx.",
    )
    expected_body: str | None = Field(
        None, description="Substring that must be present in the response body."
    )
    timeout: float | None = Field(None, gt=0, description="Per-request timeout in seconds.")
    source: Source = Source.STATIC
    namespace: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        parts = urlsplit(value.strip())
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError("base_url must be an absolute http(s) URL")
        return f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"

    @field_validator("paths")
    @classmethod
    def _validate_paths(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_path(p) for p in value if p.strip()))

    @computed_field  # type: ignore[prop-decorator]
    @property
    def id(self) -> str:
        """Stable identifier, unique per source/namespace/name."""
        return slugify(self.source.value, self.namespace, self.name)

    def effective_paths(self, default_paths: list[str]) -> list[str]:
        return list(self.paths) or list(default_paths)

    def url_for(self, path: str) -> str:
        return f"{self.base_url}{normalize_path(path)}"


class EndpointResult(BaseModel):
    """Outcome of checking one URL (after retries)."""

    url: str
    path: str
    status: Status
    status_code: int | None = None
    response_time: float | None = Field(None, description="Seconds of the last attempt.")
    attempts: int = 1
    error: str | None = None
    checked_at: datetime = Field(default_factory=utcnow)


class TargetCheck(BaseModel):
    """Outcome of checking every relevant endpoint of a target."""

    target_id: str
    status: Status
    endpoints: list[EndpointResult]
    matched_path: str | None = None
    response_time: float | None = None
    checked_at: datetime = Field(default_factory=utcnow)


class TargetState(BaseModel):
    """Target definition plus its latest known state (what the API returns)."""

    id: str
    name: str
    source: Source
    namespace: str | None
    base_url: str
    paths: list[str]
    match: MatchMode
    labels: dict[str, str] = Field(default_factory=dict)
    status: Status = Status.UNKNOWN
    matched_path: str | None = None
    response_time: float | None = None
    last_checked: datetime | None = None
    last_change: datetime | None = None
    endpoints: list[EndpointResult] = Field(default_factory=list)
    spec: TargetSpec


def combine_status(statuses: list[Status], mode: MatchMode) -> Status:
    """Combine endpoint statuses into a target status."""
    known = [s for s in statuses if s is not Status.UNKNOWN]
    if not known:
        return Status.UNKNOWN
    if mode is MatchMode.ANY:
        if Status.UP in known:
            return Status.UP
        if Status.DEGRADED in known:
            return Status.DEGRADED
        return Status.DOWN
    if all(s is Status.UP for s in known):
        return Status.UP
    if all(s is Status.DOWN for s in known):
        return Status.DOWN
    return Status.DEGRADED
