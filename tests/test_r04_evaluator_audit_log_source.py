"""Tech debt #39 — R‑04 evaluator reads from audit_log when events table is empty.

Tests verify that compute_courier_metrics() correctly unions events + audit_log
and deduplicates dual‑written rows.
"""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.r04_evaluator import compute_courier_metrics  # noqa: E402


def _make_db(events_rows: list, audit_rows: list) -> str:
    """Create a temporary SQLite DB with events + audit_log tables and given rows.

    Each row is a tuple (order_id, event_type, created_at).
    """
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
        CREATE TABLE audit_log (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT NOT NULL
        );
    """)
    for oid, etype, ts in events_rows:
        eid = f"{oid}_{etype}_{ts}"
        conn.execute(
            "INSERT INTO events (event_id, event_type, order_id, courier_id, payload, created_at, status) "
            "VALUES (?, ?, ?, 'cid1', '{}', ?, 'pending')",
            (eid, etype, oid, ts),
        )
    for oid, etype, ts in audit_rows:
        eid = f"{oid}_{etype}_{ts}_audit"
        conn.execute(
            "INSERT INTO audit_log (event_id, event_type, order_id, courier_id, payload, created_at) "
            "VALUES (?, ?, ?, 'cid1', '{}', ?)",
            (eid, etype, oid, ts),
        )
    conn.commit()
    conn.close()
    return db_path


def _cleanup(db_path: str):
    Path(db_path).unlink(missing_ok=True)
    Path(db_path + "-wal").unlink(missing_ok=True)
    Path(db_path + "-shm").unlink(missing_ok=True)


# ─── Test 1: metrics read from audit_log when events table empty ──────────
def test_metrics_read_from_audit_log_when_events_empty():
    now = datetime.now(timezone.utc)
    # Insert 50 COURIER_DELIVERED rows in audit_log spread across recent days
    # inside peak hours (11-14 Warsaw).  Use a simple schema that makes them
    # all peak deliveries.
    audit_rows = []
    for i in range(50):
        day_offset = i // 10  # 0..4 days ago
        hour = 10  # 12:00 Warsaw (CEST UTC+2), peak window
        ts = (now - timedelta(days=day_offset)).replace(
            hour=hour, minute=0, second=0, microsecond=0
        ).isoformat()
        oid = f"audit_oid_{i}"
        audit_rows.append((oid, "COURIER_DELIVERED", ts))
    db_path = _make_db([], audit_rows)
    try:
        schema = {
            "peak_window_warsaw_hours": [11, 12, 13],
            "insufficient_data": {
                "min_peak_deliveries_30d": 1,
                "min_peak_active_days_30d": 1,
                "min_speed_data_completeness_pct": 0.0,
            },
        }
        m = compute_courier_metrics(
            cid="cid1",
            name="Test",
            schema=schema,
            db_path=db_path,
            log_path="/dev/null",
            window_days=30,
            now_utc=now,
        )
        # All 50 deliveries are in peak hours → peak_deliveries_30d should be 50
        assert m.peak_deliveries_30d == 50, f"got {m.peak_deliveries_30d}"
        assert m.peak_active_days_30d >= 1
    finally:
        _cleanup(db_path)


# ─── Test 2: union of events + audit_log ──────────────────────────────────
def test_metrics_union_events_and_audit_log():
    now = datetime.now(timezone.utc)
    events_rows = []
    audit_rows = []
    for i in range(20):
        ts = (now - timedelta(days=i % 5)).replace(
            hour=10, minute=0, second=0, microsecond=0  # 12:00 Warsaw (CEST UTC+2), peak window
        ).isoformat()
        oid = f"ev_oid_{i}"
        events_rows.append((oid, "COURIER_DELIVERED", ts))
    for i in range(30):
        ts = (now - timedelta(days=i % 5)).replace(
            hour=10, minute=0, second=0, microsecond=0  # 12:00 Warsaw (CEST UTC+2), peak window
        ).isoformat()
        oid = f"aud_oid_{i}"
        audit_rows.append((oid, "COURIER_DELIVERED", ts))
    db_path = _make_db(events_rows, audit_rows)
    try:
        schema = {
            "peak_window_warsaw_hours": [11, 12, 13],
            "insufficient_data": {
                "min_peak_deliveries_30d": 1,
                "min_peak_active_days_30d": 1,
                "min_speed_data_completeness_pct": 0.0,
            },
        }
        m = compute_courier_metrics(
            cid="cid1",
            name="Test",
            schema=schema,
            db_path=db_path,
            log_path="/dev/null",
            window_days=30,
            now_utc=now,
        )
        # 20 + 30 = 50 distinct oids, all peak → peak_deliveries_30d == 50
        assert m.peak_deliveries_30d == 50, f"got {m.peak_deliveries_30d}"
    finally:
        _cleanup(db_path)


# ─── Test 3: deduplication of dual‑written rows ───────────────────────────
def test_metrics_deduplicates_dual_written_rows():
    now = datetime.now(timezone.utc)
    # Insert the same 10 (oid, event_type, created_at) tuples in BOTH tables
    rows = []
    for i in range(10):
        ts = (now - timedelta(days=i % 3)).replace(
            hour=10, minute=0, second=0, microsecond=0  # 12:00 Warsaw (CEST UTC+2), peak window
        ).isoformat()
        oid = f"dup_oid_{i}"
        rows.append((oid, "COURIER_DELIVERED", ts))
    db_path = _make_db(rows, rows)  # same rows in both tables
    try:
        schema = {
            "peak_window_warsaw_hours": [11, 12, 13],
            "insufficient_data": {
                "min_peak_deliveries_30d": 1,
                "min_peak_active_days_30d": 1,
                "min_speed_data_completeness_pct": 0.0,
            },
        }
        m = compute_courier_metrics(
            cid="cid1",
            name="Test",
            schema=schema,
            db_path=db_path,
            log_path="/dev/null",
            window_days=30,
            now_utc=now,
        )
        # 10 distinct oids → peak_deliveries_30d == 10 (not 20)
        assert m.peak_deliveries_30d == 10, f"got {m.peak_deliveries_30d}"
    finally:
        _cleanup(db_path)
