"""Z-P0-05 Faza A: retry/DLQ metadata bez automatycznego retry."""
from __future__ import annotations

import json
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


def _insert(
    conn,
    event_id: str,
    *,
    created_at: str,
    status: str = "pending",
    event_type: str = "NEW_ORDER",
    payload: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO events "
        "(event_id,event_type,order_id,courier_id,payload,created_at,status) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            event_id,
            event_type,
            event_id,
            None,
            json.dumps(payload or {}),
            created_at,
            status,
        ),
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


@pytest.mark.parametrize(
    "primary_code",
    [sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED],
)
def test_classify_failure_normalizes_extended_sqlite_lock_codes(primary_code):
    error = sqlite3.OperationalError("synthetic marker must not be inspected")
    error.sqlite_errorcode = primary_code | (7 << 8)
    assert event_retry.classify_failure(error) == event_retry.FailureDescriptor(
        event_retry.FailureClass.TRANSIENT,
        "sqlite_busy",
    )


def test_classify_failure_keeps_other_sqlite_operational_error_permanent():
    error = sqlite3.OperationalError("synthetic marker must not be inspected")
    error.sqlite_errorcode = sqlite3.SQLITE_IOERR | (3 << 8)
    assert event_retry.classify_failure(error) == event_retry.FailureDescriptor(
        event_retry.FailureClass.PERMANENT,
        "unexpected_failure",
    )


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


def test_migration_backfills_retry_alias_and_due_event_stays_visible(tmp_path):
    db_path = tmp_path / "legacy-retry.db"
    _legacy_db(db_path)
    due_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    conn = sqlite3.connect(db_path)
    conn.execute("ALTER TABLE events ADD COLUMN next_attempt_at TEXT")
    _insert(
        conn,
        "legacy-due",
        created_at=(due_at - timedelta(minutes=5)).isoformat(),
        status=event_retry.RETRY_STATUS,
    )
    conn.execute(
        "UPDATE events SET next_attempt_at=? WHERE event_id='legacy-due'",
        (due_at.isoformat(),),
    )
    conn.commit()
    conn.close()

    plan = event_retry_metadata.inspect(str(db_path))
    assert plan["next_retry_alias_backfill_count"] == 1
    assert plan["next_retry_alias_conflict_count"] == 0

    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    event_retry_metadata.apply_to_connection(conn)
    alias_row = conn.execute(
        "SELECT next_attempt_at,next_retry_at FROM events "
        "WHERE event_id='legacy-due'"
    ).fetchone()
    assert tuple(alias_row) == (due_at.isoformat(), due_at.isoformat())
    due = event_retry.due_retry_events(
        conn,
        now=due_at + timedelta(seconds=1),
    )
    assert [row["event_id"] for row in due] == ["legacy-due"]
    conn.close()


def test_migration_retry_alias_conflict_is_hold_without_partial_write(tmp_path):
    db_path = tmp_path / "conflict.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(
        conn,
        "conflict",
        created_at=at.isoformat(),
        status=event_retry.RETRY_STATUS,
    )
    conn.execute(
        """UPDATE events
           SET next_attempt_at=?, next_retry_at=?, idempotency_key=NULL
           WHERE event_id='conflict'""",
        (
            (at + timedelta(seconds=5)).isoformat(),
            (at + timedelta(seconds=10)).isoformat(),
        ),
    )
    plan = event_retry_metadata.inspect_connection(conn)
    assert plan["next_retry_alias_backfill_count"] == 0
    assert plan["next_retry_alias_conflict_count"] == 1
    with pytest.raises(RuntimeError, match="HOLD"):
        event_retry_metadata.apply_to_connection(conn)
    conflict_row = conn.execute(
        "SELECT next_attempt_at,next_retry_at,idempotency_key "
        "FROM events WHERE event_id='conflict'"
    ).fetchone()
    assert tuple(conflict_row) == (
        (at + timedelta(seconds=5)).isoformat(),
        (at + timedelta(seconds=10)).isoformat(),
        None,
    )
    conn.close()


def test_migration_cli_redacts_exception_text(monkeypatch, capsys):
    marker = "SYNTHETIC-SECRET-MARKER"

    def fail_inspect(_db_path):
        raise RuntimeError(marker)

    monkeypatch.setattr(event_retry_metadata, "inspect", fail_inspect)
    assert event_retry_metadata.main(["--db", "synthetic.db"]) == 2
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result == {
        "ok": False,
        "error_class": "permanent",
        "error_code": "unexpected_failure",
    }
    assert marker not in captured.out
    assert marker not in captured.err


@pytest.mark.parametrize("migrated", [False, True], ids=["legacy", "migrated"])
@pytest.mark.parametrize("initial_status", [None, "pending", "failed", "processed"])
def test_mark_processed_default_preserves_historical_contract(
    tmp_path, monkeypatch, migrated, initial_status
):
    db_path = tmp_path / f"events-{migrated}-{initial_status}.db"
    if migrated:
        conn = _migrated_conn(db_path)
    else:
        _legacy_db(db_path)
        conn = sqlite3.connect(db_path)
    event_id = f"synthetic-{migrated}-{initial_status}"
    alias_value = "2026-07-09T12:05:00+00:00"
    if initial_status is not None:
        _insert(
            conn,
            event_id,
            created_at="2026-07-09T12:00:00+00:00",
            status=initial_status,
        )
        if migrated:
            conn.execute(
                """UPDATE events
                   SET next_attempt_at=?, next_retry_at=? WHERE event_id=?""",
                (alias_value, alias_value, event_id),
            )
            conn.commit()
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))

    assert event_bus.mark_processed(event_id) is True

    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT status,processed_at FROM events WHERE event_id=?",
        (event_id,),
    ).fetchone()
    if initial_status is None:
        assert row is None
    else:
        assert row[0] == "processed"
        assert row[1]
    assert conn.execute(
        "SELECT COUNT(*) FROM processed_events WHERE event_id=?",
        (event_id,),
    ).fetchone()[0] == 1
    if migrated and initial_status is not None:
        assert conn.execute(
            "SELECT next_attempt_at,next_retry_at FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone() == (alias_value, alias_value)
    conn.close()


def test_mark_processed_retry_consumer_opt_in_is_strict(tmp_path, monkeypatch):
    db_path = tmp_path / "strict.db"
    conn = _migrated_conn(db_path)
    at = "2026-07-09T12:05:00+00:00"
    for event_id, status in (
        ("due", event_retry.RETRY_STATUS),
        ("failed", "failed"),
        ("done", "processed"),
    ):
        _insert(
            conn,
            event_id,
            created_at="2026-07-09T12:00:00+00:00",
            status=status,
        )
    conn.execute(
        "UPDATE events SET next_attempt_at=?,next_retry_at=? WHERE event_id='due'",
        (at, at),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))

    assert event_bus.mark_processed("due", retry_consumer_enabled=True) is True
    assert event_bus.mark_processed("failed", retry_consumer_enabled=True) is False
    assert event_bus.mark_processed("missing", retry_consumer_enabled=True) is False
    assert event_bus.mark_processed("done", retry_consumer_enabled=True) is True

    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status,next_attempt_at,next_retry_at FROM events WHERE event_id='due'"
    ).fetchone() == ("processed", None, None)
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='failed'"
    ).fetchone()[0] == "failed"
    assert conn.execute(
        "SELECT COUNT(*) FROM processed_events WHERE event_id IN ('due','done')"
    ).fetchone()[0] == 2
    conn.close()


def test_mark_processed_retry_consumer_opt_in_requires_schema(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy-strict.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    _insert(
        conn,
        "pending",
        created_at="2026-07-09T12:00:00+00:00",
    )
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    assert event_bus.mark_processed(
        "pending", retry_consumer_enabled=True
    ) is False
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='pending'"
    ).fetchone()[0] == "pending"
    assert conn.execute("SELECT COUNT(*) FROM processed_events").fetchone()[0] == 0
    conn.close()


def test_mark_processed_default_does_not_call_retry_schema_helper(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy-no-retry-dependency.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path)
    _insert(
        conn,
        "pending",
        created_at="2026-07-09T12:00:00+00:00",
    )
    conn.close()
    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))

    def forbidden_helper(_conn):
        raise RuntimeError("retry dependency must stay outside legacy path")

    monkeypatch.setattr(event_retry, "has_retry_schema", forbidden_helper)
    assert event_bus.mark_processed("pending") is True
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='pending'"
    ).fetchone()[0] == "processed"
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
            TimeoutError("provider timeout"),
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


def test_record_failure_off_stores_safe_code_and_keeps_legacy_failed(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    failed_at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(conn, "e1", created_at=failed_at.isoformat())
    assert event_retry.record_failed_attempt(
        conn, "e1", "broken\nline", failed_at=failed_at
    )
    row = conn.execute(
        "SELECT status,attempt_count,last_error,failure_class,error_code,"
        "last_failed_at,next_retry_at "
        "FROM events WHERE event_id='e1'"
    ).fetchone()
    assert tuple(row) == (
        "failed",
        1,
        "untyped_failure",
        "permanent",
        "untyped_failure",
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
        TimeoutError("timeout"),
        failed_at=at,
        expected_status="pending",
        expected_attempt_count=0,
    )
    stale = event_retry.record_failure(
        conn,
        "pending",
        TimeoutError("same delivery observed twice"),
        failed_at=at + timedelta(seconds=1),
        expected_status="pending",
        expected_attempt_count=0,
    )
    processed = event_retry.record_failure(
        conn,
        "done",
        TimeoutError("stale worker"),
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

    assert first == event_retry.FailureTransition(
        True, "failed", 1, None, "transient", "timeout"
    )
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
            conn, "e1", TimeoutError("timeout"), failed_at=at, enabled=True
        )

    policy = event_retry.RetryPolicy(max_attempts=2, backoff_seconds=(30.0,))
    with pytest.raises(ValueError, match="policy_id"):
        event_retry.record_failure(
            conn,
            "e1",
            TimeoutError("timeout"),
            failed_at=at,
            policy=policy,
            enabled=True,
        )
    scheduled = event_retry.record_failure(
        conn,
        "e1",
        TimeoutError("timeout"),
        failed_at=at,
        expected_status="pending",
        expected_attempt_count=0,
        policy=policy,
        policy_id="test-bounded-v1",
        enabled=True,
    )
    assert scheduled == event_retry.FailureTransition(
        True,
        event_retry.RETRY_STATUS,
        1,
        (at + timedelta(seconds=30)).isoformat(),
        "transient",
        "timeout",
    )

    exhausted = event_retry.record_failure(
        conn,
        "e1",
        TimeoutError("timeout again"),
        failed_at=at + timedelta(seconds=31),
        expected_status=event_retry.RETRY_STATUS,
        expected_attempt_count=1,
        policy=policy,
        policy_id="test-bounded-v1",
        enabled=True,
    )
    assert exhausted == event_retry.FailureTransition(
        True,
        event_retry.DEAD_LETTER_STATUS,
        2,
        None,
        "transient",
        "timeout",
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
        conn, "failed", TimeoutError("timeout"), failed_at=at
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
        conn, "poison", TimeoutError("temporary"), failed_at=failed_at
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
    _insert(
        conn,
        "poison",
        created_at=at.isoformat(),
        payload={"restaurant": "R", "delivery_address": "D"},
    )
    assert event_retry.record_failed_attempt(
        conn, "poison", TimeoutError("temporary"), failed_at=at
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
        reason="source_repaired",
        replayed_at=at + timedelta(hours=1),
    ) is False
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='poison'"
    ).fetchone()[0] == event_retry.DEAD_LETTER_STATUS

    assert event_retry.requeue_dead_letter(
        conn,
        "poison",
        reset_attempt_count=True,
        reason="source_repaired",
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
        "timeout",
        at.isoformat(),
        (at + timedelta(hours=1)).isoformat(),
        "source_repaired",
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
    assert row == ("failed", 1, "untyped_failure", None)
    assert event_bus.mark_failed("migrated", "stale duplicate") is False
    row = conn.execute(
        "SELECT status,attempt_count,last_error FROM events WHERE event_id='migrated'"
    ).fetchone()
    assert row == ("failed", 1, "untyped_failure")
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


def test_replay_tool_defaults_to_read_only_and_redacts_event_id(tmp_path):
    db_path = tmp_path / "events.db"
    conn = _migrated_conn(db_path)
    at = datetime(2026, 7, 9, 12, 0, tzinfo=UTC)
    _insert(
        conn,
        "dlq-1",
        created_at=at.isoformat(),
        payload={"restaurant": "R", "delivery_address": "D"},
    )
    assert event_retry.record_failed_attempt(
        conn, "dlq-1", "poison", failed_at=at
    )
    assert event_retry.move_to_dead_letter(
        conn,
        "dlq-1",
        expected_attempt_count=1,
        dead_lettered_at=at,
    )
    conn.close()
    rows = replay_dead_letter.list_dead_letters(str(db_path))
    assert [row["event_ref"] for row in rows] == [
        event_retry.event_reference("dlq-1")
    ]
    assert replay_dead_letter.requeue(
        str(db_path),
        "dlq-1",
        reason="test_only",
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
        reason="source_repaired",
        reset_attempt_count=False,
        confirmed=True,
        replayed_at=at + timedelta(hours=1),
        current_state={},
    ) is True
    assert replay_dead_letter.requeue(
        str(db_path),
        "dlq-1",
        reason="source_repaired",
        reset_attempt_count=False,
        confirmed=True,
        replayed_at=at + timedelta(hours=2),
        current_state={},
    ) is False
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT event_id,status,replay_count FROM events WHERE event_id='dlq-1'"
    ).fetchone() == ("dlq-1", "pending", 1)
    conn.close()
