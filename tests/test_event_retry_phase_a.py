"""Z-P0-05 Faza A: retry/DLQ metadata bez automatycznego retry."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

# Musza byc ustawione przed importem event_bus/common: zero produkcyjnego loga
# podczas collect. Testy nie wywoluja load_flags i nie zmieniaja sciezki flag
# procesu testowego, wiec nie zatruwaja kolekcji innych modulow.
os.environ.setdefault("DISPATCH_UNDER_PYTEST", "1")

from dispatch_v2 import event_bus, event_retry, replay_dead_letter
from dispatch_v2.migrations import event_retry_metadata


UTC = timezone.utc


def _legacy_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _insert(conn, event_id: str, *, created_at: str, status: str = "pending") -> None:
    conn.execute(
        "INSERT INTO events "
        "(event_id,event_type,order_id,courier_id,payload,created_at,status) "
        "VALUES (?,?,?,?,?,?,?)",
        (event_id, "NEW_ORDER", event_id, None, "{}", created_at, status),
    )
    conn.commit()


def _migrated_conn(path) -> sqlite3.Connection:
    _legacy_db(path)
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    event_retry_metadata.apply_to_connection(conn)
    return conn


def test_retry_policy_requires_explicit_complete_backoff():
    with pytest.raises(TypeError):
        event_retry.RetryPolicy()  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="exactly"):
        event_retry.RetryPolicy(max_attempts=3, backoff_seconds=(10.0,))
    with pytest.raises(ValueError, match="integer"):
        event_retry.RetryPolicy(max_attempts=True, backoff_seconds=())
    with pytest.raises(ValueError, match="finite"):
        event_retry.RetryPolicy(max_attempts=2, backoff_seconds=(float("nan"),))
    policy = event_retry.RetryPolicy(
        max_attempts=3, backoff_seconds=(10.0, 60.0)
    )
    failed_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    assert policy.next_attempt_at(attempt_count=1, failed_at=failed_at) == (
        failed_at + timedelta(seconds=10)
    )
    assert policy.next_attempt_at(attempt_count=3, failed_at=failed_at) is None


def test_plan_failure_is_pure_and_moves_to_dlq_only_at_explicit_limit():
    policy = event_retry.RetryPolicy(2, (5.0,))
    failed_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    first = event_retry.plan_failure(
        policy, previous_attempt_count=0, failed_at=failed_at
    )
    exhausted = event_retry.plan_failure(
        policy, previous_attempt_count=1, failed_at=failed_at
    )
    assert first.action == event_retry.RETRY_STATUS
    assert first.next_attempt_at == (failed_at + timedelta(seconds=5)).isoformat()
    assert exhausted.action == event_retry.DEAD_LETTER_STATUS
    assert exhausted.next_attempt_at is None


def test_migration_inspect_is_read_only_and_apply_is_idempotent(tmp_path):
    db_path = tmp_path / "events.db"
    _legacy_db(db_path)
    before = event_retry_metadata.inspect(str(db_path))
    assert before["ready"] is False
    assert "attempt_count" in before["missing_columns"]

    conn = sqlite3.connect(db_path, isolation_level=None)
    first = event_retry_metadata.apply_to_connection(conn)
    second = event_retry_metadata.apply_to_connection(conn)
    assert first["after"]["ready"] is True
    assert second["before"]["ready"] is True
    assert second["after"]["missing_columns"] == []
    assert event_retry.has_retry_schema(conn)
    conn.close()


def test_migration_refuses_missing_events_table(tmp_path):
    conn = sqlite3.connect(tmp_path / "empty.db", isolation_level=None)
    with pytest.raises(RuntimeError, match="events table"):
        event_retry_metadata.apply_to_connection(conn)
    conn.close()


def test_migration_rejects_wrong_column_without_partial_apply(tmp_path):
    db_path = tmp_path / "wrong-column.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("ALTER TABLE events ADD COLUMN attempt_count TEXT")
    with pytest.raises(RuntimeError, match="incompatible"):
        event_retry_metadata.apply_to_connection(conn)
    columns = {
        row[1]: row[2] for row in conn.execute("PRAGMA table_info(events)")
    }
    assert columns["attempt_count"] == "TEXT"
    assert "last_error" not in columns
    conn.close()


def test_migration_rejects_wrong_index_without_partial_apply(tmp_path):
    db_path = tmp_path / "wrong-index.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("CREATE INDEX idx_events_retry_due ON events(status)")
    with pytest.raises(RuntimeError, match="incompatible"):
        event_retry_metadata.apply_to_connection(conn)
    assert "attempt_count" not in {
        row[1] for row in conn.execute("PRAGMA table_info(events)")
    }
    conn.close()


def test_migration_rolls_back_all_ddl_on_precommit_failure(tmp_path, monkeypatch):
    db_path = tmp_path / "rollback.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    broken = event_retry_metadata.MIGRATION_INDEXES + (
        (
            "idx_events_retry_broken",
            "CREATE INDEX idx_events_retry_broken ON events(no_such_column)",
        ),
    )
    monkeypatch.setattr(event_retry_metadata, "MIGRATION_INDEXES", broken)
    with pytest.raises(sqlite3.OperationalError):
        event_retry_metadata.apply_to_connection(conn)
    assert "attempt_count" not in {
        row[1] for row in conn.execute("PRAGMA table_info(events)")
    }
    assert conn.in_transaction is False
    conn.close()


def test_rw_admin_paths_never_create_missing_db(tmp_path):
    migration_typo = tmp_path / "missing migration?#.db"
    replay_typo = tmp_path / "missing replay?#.db"
    with pytest.raises(sqlite3.OperationalError):
        event_retry_metadata.apply(str(migration_typo))
    assert migration_typo.exists() is False

    with pytest.raises(sqlite3.OperationalError):
        replay_dead_letter.requeue(
            str(replay_typo),
            "e1",
            reason="operator test",
            reset_attempt_count=False,
            confirmed=True,
        )
    assert replay_typo.exists() is False


def test_schedule_retry_default_off_is_strict_noop(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    created = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "e1", created_at=created.isoformat())
    changed = event_retry.schedule_retry(
        conn,
        "e1",
        expected_attempt_count=1,
        next_attempt_at=created + timedelta(minutes=1),
    )
    row = conn.execute(
        "SELECT status,attempt_count,last_error,next_attempt_at FROM events WHERE event_id='e1'"
    ).fetchone()
    assert event_retry.AUTOMATIC_RETRY_ENABLED is False
    assert changed is False
    assert tuple(row) == ("pending", 0, None, None)
    conn.close()


def test_explicit_schedule_due_order_and_metrics(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    base = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "later-created", created_at=(base + timedelta(seconds=1)).isoformat())
    _insert(conn, "first-created", created_at=base.isoformat())
    for eid in ("later-created", "first-created"):
        assert event_retry.record_failed_attempt(
            conn,
            eid,
            "provider timeout\nsecret-free detail",
            failed_at=base,
        )
        assert event_retry.schedule_retry(
            conn,
            eid,
            expected_attempt_count=1,
            next_attempt_at=base + timedelta(seconds=30),
            enabled=True,
        )
    due = event_retry.due_retry_events(
        conn, now=base + timedelta(seconds=31), limit=10
    )
    assert [row["event_id"] for row in due] == ["first-created", "later-created"]
    assert due[0]["payload"] == {}
    metrics = event_retry.queue_retry_stats(
        conn, now=base + timedelta(seconds=60)
    )
    assert metrics["retry_scheduled"] == 2
    assert metrics["dead_letter"] == 0
    assert metrics["oldest_retry_age_seconds"] == 60.0
    conn.close()


def test_record_failure_captures_error_but_never_schedules(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    failed_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "e1", created_at=failed_at.isoformat())
    assert event_retry.record_failed_attempt(
        conn, "e1", "broken\nline", failed_at=failed_at
    )
    row = conn.execute(
        "SELECT status,attempt_count,last_error,last_failed_at,next_attempt_at "
        "FROM events WHERE event_id='e1'"
    ).fetchone()
    assert tuple(row) == (
        "failed",
        1,
        "broken line",
        failed_at.isoformat(),
        None,
    )
    conn.close()


def test_atomic_failure_default_off_is_cas_and_never_clobbers(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "pending", created_at=at.isoformat())
    _insert(conn, "done", created_at=at.isoformat(), status="processed")

    first = event_retry.record_failure(
        conn,
        "pending",
        "timeout",
        failed_at=at,
        expected_status="pending",
        expected_attempt_count=0,
    )
    stale = event_retry.record_failure(
        conn,
        "pending",
        "same delivery observed twice",
        failed_at=at + timedelta(seconds=1),
        expected_status="pending",
        expected_attempt_count=0,
    )
    processed = event_retry.record_failure(
        conn,
        "done",
        "stale worker",
        failed_at=at,
        expected_status="pending",
    )
    with pytest.raises(ValueError, match="expected_status"):
        event_retry.record_failure(
            conn,
            "done",
            "caller attempted unsafe transition",
            failed_at=at,
            expected_status="processed",
        )

    assert first == event_retry.FailureTransition(True, "failed", 1, None)
    assert stale.changed is False
    assert stale.status == "failed"
    assert processed.changed is False
    rows = conn.execute(
        "SELECT event_id,status,attempt_count,last_error FROM events "
        "ORDER BY event_id"
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("done", "processed", 0, None),
        ("pending", "failed", 1, "timeout"),
    ]
    assert conn.in_transaction is False
    conn.close()


def test_atomic_failure_applies_only_explicit_policy(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "e1", created_at=at.isoformat())

    with pytest.raises(ValueError, match="RetryPolicy"):
        event_retry.record_failure(
            conn, "e1", "timeout", failed_at=at, enabled=True
        )

    policy = event_retry.RetryPolicy(max_attempts=2, backoff_seconds=(30.0,))
    scheduled = event_retry.record_failure(
        conn,
        "e1",
        "timeout",
        failed_at=at,
        expected_status="pending",
        expected_attempt_count=0,
        policy=policy,
        enabled=True,
    )
    assert scheduled == event_retry.FailureTransition(
        True,
        event_retry.RETRY_STATUS,
        1,
        (at + timedelta(seconds=30)).isoformat(),
    )

    exhausted = event_retry.record_failure(
        conn,
        "e1",
        "poison again",
        failed_at=at + timedelta(seconds=31),
        expected_status=event_retry.RETRY_STATUS,
        expected_attempt_count=1,
        policy=policy,
        enabled=True,
    )
    assert exhausted == event_retry.FailureTransition(
        True, event_retry.DEAD_LETTER_STATUS, 2, None
    )
    row = conn.execute(
        "SELECT status,attempt_count,next_attempt_at,last_failed_at,"
        "dead_lettered_at FROM events WHERE event_id='e1'"
    ).fetchone()
    assert tuple(row) == (
        event_retry.DEAD_LETTER_STATUS,
        2,
        None,
        (at + timedelta(seconds=31)).isoformat(),
        (at + timedelta(seconds=31)).isoformat(),
    )
    assert conn.in_transaction is False
    conn.close()


def test_schedule_retry_cas_rejects_wrong_count_and_processed(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "failed", created_at=at.isoformat())
    _insert(conn, "done", created_at=at.isoformat(), status="processed")
    assert event_retry.record_failed_attempt(
        conn, "failed", "timeout", failed_at=at
    )

    assert event_retry.schedule_retry(
        conn,
        "failed",
        expected_attempt_count=2,
        next_attempt_at=at + timedelta(seconds=30),
        enabled=True,
    ) is False
    assert event_retry.schedule_retry(
        conn,
        "done",
        expected_attempt_count=1,
        next_attempt_at=at + timedelta(seconds=30),
        enabled=True,
    ) is False
    assert event_retry.schedule_retry(
        conn,
        "failed",
        expected_attempt_count=1,
        next_attempt_at=at + timedelta(seconds=30),
        enabled=True,
    ) is True
    assert event_retry.schedule_retry(
        conn,
        "failed",
        expected_attempt_count=1,
        next_attempt_at=at + timedelta(seconds=60),
        enabled=True,
    ) is False
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='done'"
    ).fetchone()[0] == "processed"
    conn.close()


def test_dead_letter_cas_preserves_real_failure_timestamp(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    failed_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    moved_at = failed_at + timedelta(hours=2)
    _insert(conn, "poison", created_at=failed_at.isoformat())
    assert event_retry.record_failed_attempt(
        conn, "poison", "bad payload", failed_at=failed_at
    )
    assert event_retry.move_to_dead_letter(
        conn,
        "poison",
        expected_attempt_count=2,
        dead_lettered_at=moved_at,
    ) is False
    assert event_retry.move_to_dead_letter(
        conn,
        "poison",
        expected_attempt_count=1,
        dead_lettered_at=moved_at,
    ) is True
    assert event_retry.move_to_dead_letter(
        conn,
        "poison",
        expected_attempt_count=1,
        dead_lettered_at=moved_at + timedelta(seconds=1),
    ) is False
    row = conn.execute(
        "SELECT status,processed_at,last_failed_at,dead_lettered_at "
        "FROM events WHERE event_id='poison'"
    ).fetchone()
    assert tuple(row) == (
        event_retry.DEAD_LETTER_STATUS,
        failed_at.isoformat(),
        failed_at.isoformat(),
        moved_at.isoformat(),
    )
    conn.close()


def test_dead_letter_and_requeue_are_explicit_and_audited(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "poison", created_at=at.isoformat())
    assert event_retry.record_failed_attempt(
        conn, "poison", "invalid payload", failed_at=at
    )
    assert event_retry.move_to_dead_letter(
        conn,
        "poison",
        expected_attempt_count=1,
        dead_lettered_at=at,
    )
    assert event_retry.requeue_dead_letter(
        conn,
        "poison",
        reset_attempt_count=True,
        reason="fixed parser in isolated branch",
        replayed_at=at + timedelta(hours=1),
    ) is False
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='poison'"
    ).fetchone()[0] == event_retry.DEAD_LETTER_STATUS

    assert event_retry.requeue_dead_letter(
        conn,
        "poison",
        reset_attempt_count=True,
        reason="fixed parser in isolated branch",
        replayed_at=at + timedelta(hours=1),
        enabled=True,
    )
    row = conn.execute(
        "SELECT status,attempt_count,replay_count,last_error,dead_lettered_at,"
        "last_replayed_at,last_replay_reason FROM events WHERE event_id='poison'"
    ).fetchone()
    assert tuple(row) == (
        "pending",
        0,
        1,
        "invalid payload",
        at.isoformat(),
        (at + timedelta(hours=1)).isoformat(),
        "fixed parser in isolated branch",
    )
    conn.close()


def test_requeue_requires_reason_when_explicitly_enabled(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "e1", created_at=at.isoformat(), status="dead_letter")
    with pytest.raises(ValueError, match="reason"):
        event_retry.requeue_dead_letter(
            conn,
            "e1",
            reset_attempt_count=False,
            reason="",
            replayed_at=at,
            enabled=True,
        )
    conn.close()


def test_mark_failed_old_schema_keeps_legacy_contract(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    _insert(conn, "legacy", created_at="2026-07-09T12:00:00+00:00")
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    assert event_bus.mark_failed("legacy", "legacy failure") is True
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status,processed_at FROM events WHERE event_id='legacy'"
    ).fetchone()
    assert row[0] == "failed"
    assert row[1]
    conn.close()


def test_mark_failed_migrated_schema_records_diagnosis_without_retry(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "migrated.db"
    conn = _migrated_conn(db_path)
    _insert(conn, "migrated", created_at="2026-07-09T12:00:00+00:00")
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    assert event_bus.mark_failed("migrated", "typed provider failure") is True
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status,attempt_count,last_error,next_attempt_at "
        "FROM events WHERE event_id='migrated'"
    ).fetchone()
    assert row == ("failed", 1, "typed provider failure", None)
    assert event_bus.mark_failed("migrated", "stale duplicate") is False
    row = conn.execute(
        "SELECT status,attempt_count,last_error FROM events WHERE event_id='migrated'"
    ).fetchone()
    assert row == ("failed", 1, "typed provider failure")
    conn.close()


def test_mark_failed_never_clobbers_processed(tmp_path, monkeypatch):
    db_path = tmp_path / "processed.db"
    conn = _migrated_conn(db_path)
    _insert(conn, "done", created_at="2026-07-09T12:00:00+00:00", status="processed")
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    assert event_bus.mark_failed("done", "late stale worker") is False
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status,attempt_count,last_error FROM events WHERE event_id='done'"
    ).fetchone() == ("processed", 0, None)
    conn.close()


def test_replay_tool_defaults_to_read_only_and_preserves_event_id(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "dlq-1", created_at=at.isoformat())
    assert event_retry.record_failed_attempt(
        conn, "dlq-1", "poison", failed_at=at
    )
    event_retry.move_to_dead_letter(
        conn,
        "dlq-1",
        expected_attempt_count=1,
        dead_lettered_at=at,
    )
    conn.close()
    rows = replay_dead_letter.list_dead_letters(str(db_path))
    assert [row["event_id"] for row in rows] == ["dlq-1"]
    assert replay_dead_letter.requeue(
        str(db_path),
        "dlq-1",
        reason="test",
        reset_attempt_count=False,
    ) is False
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT event_id,status FROM events WHERE event_id='dlq-1'"
    ).fetchone() == ("dlq-1", "dead_letter")
    conn.close()

    assert replay_dead_letter.requeue(
        str(db_path),
        "dlq-1",
        reason="explicit replay",
        reset_attempt_count=False,
        confirmed=True,
        replayed_at=at + timedelta(hours=1),
    ) is True
    assert replay_dead_letter.requeue(
        str(db_path),
        "dlq-1",
        reason="stale duplicate replay",
        reset_attempt_count=False,
        confirmed=True,
        replayed_at=at + timedelta(hours=2),
    ) is False
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT event_id,status,replay_count FROM events WHERE event_id='dlq-1'"
    ).fetchone() == ("dlq-1", "pending", 1)
    conn.close()
