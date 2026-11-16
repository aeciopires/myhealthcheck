import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from myhealthcheck import storage as storage_module
from myhealthcheck.models import EndpointResult, Source, Status, TargetCheck, TargetSpec
from myhealthcheck.storage import Storage, StorageBackend


def spec(name: str, source: Source = Source.STATIC, namespace: str | None = None) -> TargetSpec:
    return TargetSpec(name=name, base_url=f"http://{name}:80", source=source, namespace=namespace)


def check(target_id: str, status: Status, minutes: int = 0) -> TargetCheck:
    at = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=minutes)
    return TargetCheck(
        target_id=target_id,
        status=status,
        matched_path="/health",
        response_time=0.1,
        checked_at=at,
        endpoints=[
            EndpointResult(
                url="http://x/health", path="/health", status=status, status_code=200, checked_at=at
            )
        ],
    )


def test_file_backend(storage: Storage) -> None:
    assert storage.backend is StorageBackend.FILE
    assert Path(storage.location).is_file()
    assert storage.ping()


def test_upsert_get_list_delete(storage: Storage) -> None:
    a, b = spec("a"), spec("b", Source.KUBERNETES, "prod")
    storage.upsert_target(a)
    storage.upsert_target(b)
    storage.upsert_target(a)  # idempotent
    state = storage.get_target(a.id)
    assert state is not None
    assert state.status is Status.UNKNOWN
    assert state.spec == a
    assert [s.id for s in storage.list_targets()] == [b.id, a.id]
    assert [s.id for s in storage.list_targets(source=Source.STATIC)] == [a.id]
    assert [s.id for s in storage.list_targets(namespace="prod")] == [b.id]
    assert storage.list_targets(status=Status.UP) == []
    assert {s.id for s in storage.list_specs()} == {a.id, b.id}
    assert storage.delete_target(a.id) is True
    assert storage.delete_target(a.id) is False
    assert storage.get_target(a.id) is None


def test_sync_source_removes_missing(storage: Storage) -> None:
    storage.upsert_target(spec("static"))
    storage.sync_source(Source.KUBERNETES, [spec("x", Source.KUBERNETES, "n")])
    count, removed = storage.sync_source(Source.KUBERNETES, [spec("y", Source.KUBERNETES, "n")])
    assert count == 1
    assert removed == ["kubernetes-n-x"]
    assert {s.name for s in storage.list_specs()} == {"static", "y"}


def test_save_check_history_and_retention(storage: Storage) -> None:
    target = spec("a")
    storage.upsert_target(target)
    assert storage.save_check(check(target.id, Status.UP, 0)) is Status.UNKNOWN
    assert storage.save_check(check(target.id, Status.UP, 1)) is Status.UP
    for minute in range(2, 6):
        storage.save_check(check(target.id, Status.DOWN, minute))

    state = storage.get_target(target.id)
    assert state is not None
    assert state.status is Status.DOWN
    assert state.last_change == datetime(2026, 1, 1, 0, 2, tzinfo=UTC)
    assert state.last_checked == datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert state.endpoints[0].status_code == 200

    history = storage.history(target.id)
    assert len(history) == 3  # history_retention=3
    assert history[0].checked_at > history[-1].checked_at
    assert len(storage.history(target.id, limit=1)) == 1
    assert storage.save_check(check("missing", Status.UP)) is None


def test_delete_cascades_history(storage: Storage) -> None:
    target = spec("a")
    storage.upsert_target(target)
    storage.save_check(check(target.id, Status.UP))
    storage.delete_target(target.id)
    assert storage.history(target.id) == []


def test_summary(storage: Storage) -> None:
    storage.upsert_target(spec("a"))
    storage.upsert_target(spec("b", Source.API))
    storage.save_check(check(spec("a").id, Status.DOWN))
    summary = storage.summary()
    assert summary["total"] == 2
    assert summary["by_status"]["down"] == 1
    assert summary["by_status"]["unknown"] == 1
    assert summary["by_source"] == {"static": 1, "kubernetes": 0, "api": 1}


def test_transaction_rollback(storage: Storage) -> None:
    with pytest.raises(sqlite3.IntegrityError), storage._tx() as conn:
        conn.execute(
            "INSERT INTO targets (id, name, source, base_url, spec, created_at, updated_at) "
            "VALUES ('x', 'x', 'static', 'http://x', '{}', 'now', 'now')"
        )
        conn.execute("INSERT INTO targets (id) VALUES ('x')")
    assert storage.get_target("x") is None


def test_ping_fails_when_closed(tmp_path: Path) -> None:
    store = Storage(tmp_path / "x.db")
    store.close()
    assert store.ping() is False


def test_fallback_to_temp_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setattr(storage_module.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    store = Storage(blocker / "sub" / "db.sqlite")
    try:
        assert store.backend is StorageBackend.TEMP
        assert store.location == str(tmp_path / "tmp" / "myhealthcheck" / "db.sqlite")
    finally:
        store.close()


def test_fallback_to_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(storage_module.tempfile, "gettempdir", lambda: str(blocker))
    store = Storage(blocker / "db.sqlite")
    try:
        assert store.backend is StorageBackend.MEMORY
        store.upsert_target(spec("a"))
        assert len(store.list_specs()) == 1
    finally:
        store.close()


def test_corrupted_file_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corrupted = tmp_path / "bad.db"
    corrupted.write_bytes(b"this is not sqlite" * 100)
    monkeypatch.setattr(storage_module.tempfile, "gettempdir", lambda: str(tmp_path / "tmp"))
    store = Storage(corrupted)
    try:
        assert store.backend is StorageBackend.TEMP
    finally:
        store.close()
