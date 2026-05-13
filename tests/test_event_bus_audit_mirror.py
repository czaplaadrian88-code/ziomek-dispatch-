"""Tech debt #39 — audit mirror for COURIER_PICKED_UP / COURIER_DELIVERED.

Tests verify that emit() for these types also writes to audit_log,
and that failures in the mirror do not block the queue emit.
"""
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus  # noqa: E402


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


def _count_events(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    conn.close()
    return cnt


def _count_audit_log(db_path: str) -> int:
    conn = sqlite3.connect(db_path)
    cnt = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
    conn.close()
    return cnt


# ─── Test 1: COURIER_DELIVERED mirrors to audit_log ──────────────────────
def test_emit_courier_delivered_mirrors_to_audit_log():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid = event_bus.emit(
            "COURIER_DELIVERED",
            order_id="oid1",
            courier_id="cid1",
            payload={"ts": "2026-05-13T12:00:00Z"},
        )
        assert eid is not None
        assert _count_events(db_path) == 1
        assert _count_audit_log(db_path) == 1
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT event_id FROM audit_log WHERE event_id=?", (eid,)
        ).fetchone()
        conn.close()
        assert row is not None
    finally:
        _restore_db(state, db_path)


# ─── Test 2: COURIER_PICKED_UP mirrors to audit_log ──────────────────────
def test_emit_courier_picked_up_mirrors_to_audit_log():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid = event_bus.emit(
            "COURIER_PICKED_UP",
            order_id="oid2",
            courier_id="cid2",
            payload={"ts": "2026-05-13T12:05:00Z"},
        )
        assert eid is not None
        assert _count_events(db_path) == 1
        assert _count_audit_log(db_path) == 1
    finally:
        _restore_db(state, db_path)


# ─── Test 3: NEW_ORDER does NOT mirror ────────────────────────────────────
def test_emit_new_order_does_not_mirror():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid = event_bus.emit(
            "NEW_ORDER",
            order_id="oid3",
            payload={"restaurant": "Test"},
        )
        assert eid is not None
        assert _count_events(db_path) == 1
        assert _count_audit_log(db_path) == 0
    finally:
        _restore_db(state, db_path)


# ─── Test 4: duplicate event_id → audit_log still has 1 row ──────────────
def test_audit_mirror_idempotent_on_dup_event_id():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid = "dup_test_123"
        r1 = event_bus.emit(
            "COURIER_DELIVERED",
            order_id="oid4",
            courier_id="cid4",
            payload={},
            event_id=eid,
        )
        assert r1 == eid
        r2 = event_bus.emit(
            "COURIER_DELIVERED",
            order_id="oid4",
            courier_id="cid4",
            payload={},
            event_id=eid,
        )
        assert r2 is None  # duplicate
        assert _count_audit_log(db_path) == 1
    finally:
        _restore_db(state, db_path)


# ─── Test 5: mirror survives cleanup (events row deleted, audit_log kept) ─
def test_audit_mirror_survives_cleanup_48h():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid = event_bus.emit(
            "COURIER_DELIVERED",
            order_id="oid5",
            courier_id="cid5",
            payload={},
        )
        assert eid is not None
        # mark processed and backdate processed_at to simulate 48h+ passage
        event_bus.mark_processed(eid)
        conn = sqlite3.connect(db_path)
        conn.execute(
            "UPDATE events SET processed_at = datetime('now', '-3 days') WHERE event_id=?",
            (eid,),
        )
        conn.execute(
            "UPDATE processed_events SET processed_at = datetime('now', '-3 days') WHERE event_id=?",
            (eid,),
        )
        conn.commit()
        conn.close()
        # cleanup with retention_hours=0 (delete everything older than now)
        with patch.object(event_bus, "_is_peak_window", return_value=False):
            deleted = event_bus.cleanup(retention_hours=0)
        assert deleted >= 1
        assert _count_events(db_path) == 0
        assert _count_audit_log(db_path) == 1
    finally:
        _restore_db(state, db_path)


# ─── Test 6: mirror failure does NOT block queue emit ─────────────────────
def test_audit_mirror_failure_does_not_block_queue_emit():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        def _broken_mirror(*a, **kw):
            raise RuntimeError("simulated mirror failure")
        with patch.object(event_bus, "_emit_audit_mirror", _broken_mirror):
            eid = event_bus.emit(
                "COURIER_DELIVERED",
                order_id="oid6",
                courier_id="cid6",
                payload={},
            )
        assert eid is not None
        assert _count_events(db_path) == 1
        # audit_log may be empty because mirror failed
    finally:
        _restore_db(state, db_path)
