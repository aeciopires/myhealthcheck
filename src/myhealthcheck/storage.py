"""SQLite storage with automatic fallback.

Analogy: storage is the *medical records cabinet*. We first try to keep it in
the main archive room (``DB_PATH``). If that room is locked (read-only volume,
missing permissions) we use the temporary storeroom (the OS temp directory).
If even that is locked, we keep the records on a clipboard (``:memory:``) -
everything still works, but the records vanish when the process stops.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from myhealthcheck.models import (
    EndpointResult,
    Source,
    Status,
    TargetCheck,
    TargetSpec,
    TargetState,
    utcnow,
)

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    source        TEXT NOT NULL,
    namespace     TEXT,
    base_url      TEXT NOT NULL,
    spec          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'unknown',
    matched_path  TEXT,
    response_time REAL,
    endpoints     TEXT NOT NULL DEFAULT '[]',
    last_checked  TEXT,
    last_change   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_targets_source ON targets(source);
CREATE TABLE IF NOT EXISTS check_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id     TEXT NOT NULL REFERENCES targets(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,
    matched_path  TEXT,
    response_time REAL,
    endpoints     TEXT NOT NULL,
    checked_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_history_target ON check_history(target_id, id);
"""


class StorageBackend(StrEnum):
    FILE = "file"
    TEMP = "temp"
    MEMORY = "memory"


def _ts(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _endpoints_json(endpoints: list[EndpointResult]) -> str:
    return json.dumps([e.model_dump(mode="json") for e in endpoints])


def _endpoints_from_json(raw: str) -> list[EndpointResult]:
    return [EndpointResult.model_validate(e) for e in json.loads(raw)]


class Storage:
    """Thread-safe SQLite repository for targets and check history."""

    def __init__(self, path: Path | str, history_retention: int = 100) -> None:
        self.requested_path = Path(path)
        self.history_retention = history_retention
        self._lock = threading.RLock()
        self.backend, self.location, self._conn = self._open()

    # ------------------------------------------------------------- lifecycle
    def _candidates(self) -> list[tuple[StorageBackend, str]]:
        temp = Path(tempfile.gettempdir()) / "myhealthcheck" / self.requested_path.name
        return [
            (StorageBackend.FILE, str(self.requested_path)),
            (StorageBackend.TEMP, str(temp)),
            (StorageBackend.MEMORY, ":memory:"),
        ]

    @staticmethod
    def _connect(location: str) -> sqlite3.Connection:
        if location != ":memory:":
            Path(location).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(location, check_same_thread=False, timeout=10)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            if location != ":memory:":
                conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        except sqlite3.Error:
            conn.close()
            raise
        return conn

    def _open(self) -> tuple[StorageBackend, str, sqlite3.Connection]:
        last_error: Exception | None = None
        for backend, location in self._candidates():
            try:
                conn = self._connect(location)
            except (OSError, sqlite3.Error) as exc:
                last_error = exc
                logger.warning(
                    "storage location unavailable, trying next fallback",
                    extra={"backend": backend.value, "location": location, "error": str(exc)},
                )
                continue
            logger.info("storage ready", extra={"backend": backend.value, "location": location})
            return backend, location, conn
        raise RuntimeError(f"no storage backend available: {last_error}")  # pragma: no cover

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
        except sqlite3.Error:
            return False
        return True

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # --------------------------------------------------------------- targets
    def upsert_target(self, spec: TargetSpec) -> None:
        now = _ts(utcnow())
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO targets (id, name, source, namespace, base_url, spec,
                                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name, source = excluded.source,
                    namespace = excluded.namespace, base_url = excluded.base_url,
                    spec = excluded.spec, updated_at = excluded.updated_at
                """,
                (
                    spec.id,
                    spec.name,
                    spec.source.value,
                    spec.namespace,
                    spec.base_url,
                    spec.model_dump_json(exclude={"id"}),
                    now,
                    now,
                ),
            )

    def sync_source(self, source: Source, specs: list[TargetSpec]) -> tuple[int, list[str]]:
        """Make the stored targets of ``source`` equal to ``specs``.

        Returns ``(number of upserted targets, ids removed)``.
        """
        wanted = {s.id for s in specs}
        with self._lock:
            existing = {
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM targets WHERE source = ?", (source.value,)
                )
            }
            for spec in specs:
                self.upsert_target(spec)
            removed = sorted(existing - wanted)
            with self._tx() as conn:
                conn.executemany("DELETE FROM targets WHERE id = ?", [(i,) for i in removed])
        return len(specs), removed

    def delete_target(self, target_id: str) -> bool:
        with self._tx() as conn:
            cursor = conn.execute("DELETE FROM targets WHERE id = ?", (target_id,))
        return cursor.rowcount > 0

    def _row_to_state(self, row: sqlite3.Row) -> TargetState:
        spec = TargetSpec.model_validate_json(row["spec"])
        return TargetState(
            id=row["id"],
            name=row["name"],
            source=Source(row["source"]),
            namespace=row["namespace"],
            base_url=row["base_url"],
            paths=list(spec.paths),
            match=spec.match,
            labels=dict(spec.labels),
            status=Status(row["status"]),
            matched_path=row["matched_path"],
            response_time=row["response_time"],
            last_checked=_dt(row["last_checked"]),
            last_change=_dt(row["last_change"]),
            endpoints=_endpoints_from_json(row["endpoints"]),
            spec=spec,
        )

    def get_target(self, target_id: str) -> TargetState | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM targets WHERE id = ?", (target_id,)).fetchone()
        return self._row_to_state(row) if row else None

    def list_targets(
        self,
        status: Status | None = None,
        source: Source | None = None,
        namespace: str | None = None,
    ) -> list[TargetState]:
        clauses, params = [], []
        for column, value in (("status", status), ("source", source), ("namespace", namespace)):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value.value if isinstance(value, StrEnum) else value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        query = f"SELECT * FROM targets {where} ORDER BY source, namespace, name"  # noqa: S608
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [self._row_to_state(r) for r in rows]

    def list_specs(self) -> list[TargetSpec]:
        with self._lock:
            rows = self._conn.execute("SELECT spec FROM targets ORDER BY id").fetchall()
        return [TargetSpec.model_validate_json(r["spec"]) for r in rows]

    # ---------------------------------------------------------------- checks
    def save_check(self, check: TargetCheck) -> Status | None:
        """Persist a check; return the previous status (``None`` if target is gone)."""
        endpoints = _endpoints_json(check.endpoints)
        checked_at = _ts(check.checked_at)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT status FROM targets WHERE id = ?", (check.target_id,)
            ).fetchone()
            if row is None:
                return None
            previous = Status(row["status"])
            conn.execute(
                """
                UPDATE targets SET status = ?, matched_path = ?, response_time = ?,
                    endpoints = ?, last_checked = ?,
                    last_change = CASE WHEN status != ? THEN ? ELSE last_change END
                WHERE id = ?
                """,
                (
                    check.status.value,
                    check.matched_path,
                    check.response_time,
                    endpoints,
                    checked_at,
                    check.status.value,
                    checked_at,
                    check.target_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO check_history (target_id, status, matched_path, response_time,
                                           endpoints, checked_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    check.target_id,
                    check.status.value,
                    check.matched_path,
                    check.response_time,
                    endpoints,
                    checked_at,
                ),
            )
            conn.execute(
                """
                DELETE FROM check_history WHERE target_id = ? AND id NOT IN (
                    SELECT id FROM check_history WHERE target_id = ?
                    ORDER BY id DESC LIMIT ?)
                """,
                (check.target_id, check.target_id, self.history_retention),
            )
        return previous

    def history(self, target_id: str, limit: int = 50) -> list[TargetCheck]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM check_history WHERE target_id = ? ORDER BY id DESC LIMIT ?",
                (target_id, limit),
            ).fetchall()
        return [
            TargetCheck(
                target_id=r["target_id"],
                status=Status(r["status"]),
                matched_path=r["matched_path"],
                response_time=r["response_time"],
                endpoints=_endpoints_from_json(r["endpoints"]),
                checked_at=_dt(r["checked_at"]) or utcnow(),
            )
            for r in rows
        ]

    def summary(self) -> dict[str, Any]:
        with self._lock:
            by_status = {
                r["status"]: r["n"]
                for r in self._conn.execute(
                    "SELECT status, COUNT(*) AS n FROM targets GROUP BY status"
                )
            }
            by_source = {
                r["source"]: r["n"]
                for r in self._conn.execute(
                    "SELECT source, COUNT(*) AS n FROM targets GROUP BY source"
                )
            }
        return {
            "total": sum(by_status.values()),
            "by_status": {s.value: by_status.get(s.value, 0) for s in Status},
            "by_source": {s.value: by_source.get(s.value, 0) for s in Source},
        }
