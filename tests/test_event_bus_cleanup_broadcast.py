"""A4.1 follow-up (2026-05-08) — event_bus.cleanup_broadcast() tests.

Coverage:
- deletes broadcast events older than retention_days
- preserves broadcast events younger than retention
- preserves queue + audit events (status != 'broadcast')
- skip during peak window (Warsaw lunch/dinner)
- dry-run helper _dry_run_broadcast counts bez DELETE
"""
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus, event_bus_cleanup  # noqa: E402


def _setup_tmp_db(state: dict):
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db_path = f.name
    conn = sqlite3.connect(db_path)
    conn.executescript("""
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
        CREATE INDEX idx_events_status ON events(status);
        CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()
    state["original"] = event_bus._db_path
    event_bus._db_path = lambda: db_path
    event_bus._init_audit_log_table()
    return db_path


def _restore_db(state: dict, db_path: str):
    event_bus._db_path = state["original"]
    Path(db_path).unlink(missing_ok=True)
    Path(db_path + "-wal").unlink(missing_ok=True)
    Path(db_path + "-shm").unlink(missing_ok=True)


def _insert_event(db_path: str, event_id: str, event_type: str, status: str, days_old: int):
    """Insert event z created_at = now - days_old days."""
    created_at = (datetime.now(timezone.utc) - timedelta(days=days_old)).strftime("%Y-%m-%d %H:%M:%S")
    conn = sqlite3.connect(db_path)
    conn.execute(
        """INSERT INTO events (event_id, event_type, status, created_at, payload)
           VALUES (?, ?, ?, ?, '{}')""",
        (event_id, event_type, status, created_at),
    )
    conn.commit()
    conn.close()


# ─── Test 1: cleanup_broadcast deletes only old broadcast events ──────────
def test_cleanup_broadcast_deletes_old_broadcast():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        _insert_event(db_path, "old_bc", "CONFIG_RELOAD", "broadcast", days_old=10)
        _insert_event(db_path, "new_bc", "CONFIG_RELOAD", "broadcast", days_old=2)
        with patch.object(event_bus, "_is_peak_window", return_value=False):
            deleted = event_bus.cleanup_broadcast(retention_days=7)
        assert deleted == 1
        conn = sqlite3.connect(db_path)
        rows = [r[0] for r in conn.execute("SELECT event_id FROM events").fetchall()]
        conn.close()
        assert "new_bc" in rows
        assert "old_bc" not in rows
    finally:
        _restore_db(state, db_path)


# ─── Test 2: cleanup_broadcast preserves queue/processed events ───────────
def test_cleanup_broadcast_preserves_non_broadcast():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        # All > retention age but only broadcast should die
        _insert_event(db_path, "old_pending", "NEW_ORDER", "pending", days_old=10)
        _insert_event(db_path, "old_processed", "NEW_ORDER", "processed", days_old=10)
        _insert_event(db_path, "old_failed", "NEW_ORDER", "failed", days_old=10)
        _insert_event(db_path, "old_broadcast", "CONFIG_RELOAD", "broadcast", days_old=10)
        with patch.object(event_bus, "_is_peak_window", return_value=False):
            deleted = event_bus.cleanup_broadcast(retention_days=7)
        assert deleted == 1
        conn = sqlite3.connect(db_path)
        remaining = sorted(r[0] for r in conn.execute("SELECT event_id FROM events").fetchall())
        conn.close()
        assert remaining == ["old_failed", "old_pending", "old_processed"]
    finally:
        _restore_db(state, db_path)


# ─── Test 3: cleanup_broadcast skip during peak window ───────────────────
def test_cleanup_broadcast_skip_peak():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        _insert_event(db_path, "old_bc", "CONFIG_RELOAD", "broadcast", days_old=10)
        with patch.object(event_bus, "_is_peak_window", return_value=True):
            deleted = event_bus.cleanup_broadcast(retention_days=7)
        assert deleted == 0
        conn = sqlite3.connect(db_path)
        cnt = conn.execute("SELECT COUNT(*) FROM events WHERE status='broadcast'").fetchone()[0]
        conn.close()
        assert cnt == 1, "peak skip should NOT touch DB"
    finally:
        _restore_db(state, db_path)


# ─── Test 4: dry_run_broadcast counts bez DELETE ─────────────────────────
def test_dry_run_broadcast_counts_no_delete():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        _insert_event(db_path, "old_bc1", "CONFIG_RELOAD", "broadcast", days_old=10)
        _insert_event(db_path, "old_bc2", "CONFIG_RELOAD", "broadcast", days_old=8)
        _insert_event(db_path, "new_bc", "CONFIG_RELOAD", "broadcast", days_old=3)
        cnt = event_bus_cleanup._dry_run_broadcast()
        assert cnt == 2, f"expected 2 events older than 7d, got {cnt}"
        conn = sqlite3.connect(db_path)
        total = conn.execute("SELECT COUNT(*) FROM events WHERE status='broadcast'").fetchone()[0]
        conn.close()
        assert total == 3, "dry-run must NOT delete anything"
    finally:
        _restore_db(state, db_path)


# ─── Test 5: cleanup_broadcast retention boundary ────────────────────────
def test_cleanup_broadcast_retention_boundary():
    """Default retention=7d — event 6.9d old preserved, 7.1d deleted."""
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        # Use SQL datetime arithmetic dla precise boundary control
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO events (event_id, event_type, status, created_at, payload) "
            "VALUES ('just_under', 'CONFIG_RELOAD', 'broadcast', datetime('now', '-6.9 days'), '{}')"
        )
        conn.execute(
            "INSERT INTO events (event_id, event_type, status, created_at, payload) "
            "VALUES ('just_over', 'CONFIG_RELOAD', 'broadcast', datetime('now', '-7.1 days'), '{}')"
        )
        conn.commit()
        conn.close()
        with patch.object(event_bus, "_is_peak_window", return_value=False):
            deleted = event_bus.cleanup_broadcast(retention_days=7)
        assert deleted == 1
        conn = sqlite3.connect(db_path)
        remaining = [r[0] for r in conn.execute("SELECT event_id FROM events").fetchall()]
        conn.close()
        assert remaining == ["just_under"]
    finally:
        _restore_db(state, db_path)
