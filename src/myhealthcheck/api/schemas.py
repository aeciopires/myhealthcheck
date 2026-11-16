"""Request/response bodies of the REST API (they shape the OpenAPI document)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from myhealthcheck.models import MatchMode, StatusCode, TargetCheck


class TargetCreate(BaseModel):
    """Body of ``POST /api/v1/targets`` and ``POST /api/v1/probe``."""

    name: str | None = Field(
        None, description="Display name. Defaults to host:port of the URL.", max_length=253
    )
    url: str = Field(
        description=(
            "Base URL or full URL of the system. Accepted forms: "
            "`protocol://host:port/endpoint`, `protocol:host:port/endpoint`, `host:port`."
        ),
        examples=["http://kube-pires:3000/health", "http:10.0.0.5:8080/status"],
    )
    paths: list[str] = Field(
        default_factory=list,
        description="Health paths. Empty: the path of `url`, or the default paths.",
    )
    match: MatchMode = MatchMode.ANY
    expected_status: list[StatusCode] = Field(default_factory=list)
    expected_body: str | None = None
    timeout: float | None = Field(None, gt=0)
    labels: dict[str, str] = Field(default_factory=dict)


class HealthResponse(BaseModel):
    status: str = Field(examples=["ok"])
    version: str
    storage: str | None = None
    checks: dict[str, bool] = Field(default_factory=dict)


class SummaryResponse(BaseModel):
    total: int
    by_status: dict[str, int]
    by_source: dict[str, int]
    storage_backend: str
    storage_location: str
    discovery_enabled: bool
    last_cycle_at: datetime | None
    last_discovery_at: datetime | None
    last_discovery_error: str | None


class CheckRunResponse(BaseModel):
    checked: int
    results: list[TargetCheck]


class DiscoveryResponse(BaseModel):
    discovered: int
    finished_at: datetime | None


class ErrorResponse(BaseModel):
    detail: str


class ConfigResponse(BaseModel):
    settings: dict[str, Any]
