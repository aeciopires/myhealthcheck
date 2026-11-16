"""Alerts on status transitions.

Analogy: the notifier is the *hospital pager*. It only beeps when a patient's
condition changes (up -> down, down -> up, ...), not on every round, so people
are not flooded with repeated alarms.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from myhealthcheck.models import Status, TargetCheck, TargetSpec

logger = logging.getLogger(__name__)

_SEVERITY = {
    Status.DOWN: "critical",
    Status.DEGRADED: "warning",
    Status.UP: "resolved",
    Status.UNKNOWN: "info",
}


def severity_for(status: Status) -> str:
    return _SEVERITY[status]


class Notifier:
    """Emits an alert log line (and optionally a webhook) on status change."""

    def __init__(self, client: httpx.AsyncClient | None = None, webhook_url: str | None = None):
        self.client = client
        self.webhook_url = webhook_url

    @staticmethod
    def should_alert(previous: Status | None, current: Status) -> bool:
        if previous is None or previous is current:
            return False
        # First check of a healthy target is not news.
        return not (previous is Status.UNKNOWN and current is Status.UP)

    async def notify(self, spec: TargetSpec, previous: Status, check: TargetCheck) -> str:
        severity = severity_for(check.status)
        errors = sorted({e.error for e in check.endpoints if e.error})
        payload: dict[str, Any] = {
            "event": "alert",
            "severity": severity,
            "target": spec.id,
            "target_name": spec.name,
            "namespace": spec.namespace,
            "source": spec.source.value,
            "base_url": spec.base_url,
            "previous_status": previous.value,
            "status": check.status.value,
            "errors": errors,
            "checked_at": check.checked_at.isoformat(),
        }
        level = {"critical": logging.ERROR, "warning": logging.WARNING}.get(severity, logging.INFO)
        logger.log(level, f"target {spec.id} is {check.status.value}", extra=payload)

        if self.webhook_url and self.client is not None:
            try:
                response = await self.client.post(self.webhook_url, json=payload, timeout=10)
                response.raise_for_status()
            except httpx.HTTPError as exc:
                logger.warning("alert webhook failed", extra={"target": spec.id, "error": str(exc)})
        return severity
