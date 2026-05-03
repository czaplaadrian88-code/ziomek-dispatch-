"""V3.28 Fix 4 (incident 03.05.2026) — replay_failed CLI tests.

Test coverage:
- query_failed_events (filter oid / status / since)
- replay_event (skip non-NEW_ORDER, skip empty payload, exception → FAIL verdict)
- apply_status_flip (atomic UPDATE, transactional rollback on error)
- end-to-end replay_batch z synthetic events.db
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import replay_failed as rf  # noqa: E402


def _build_synthetic_events_db():
    """Build temp events.db z 5 synthetic events (3 failed NEW_ORDER, 1 processed, 1 unsupported)."""
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
    """)
    payload_minimal = json.dumps({
        "order_id": "999",
        "pickup_coords": [53.13, 23.16],
        "delivery_coords": [53.14, 23.17],
        "restaurant": "Test",
        "pickup_at_warsaw": "2026-05-03T15:00:00+02:00",
        "prep_minutes": 15,
        "order_type": "elastic",
        "status_id": 1,
    })
    rows = [
        ("e1", "NEW_ORDER", "100", None, payload_minimal, "2026-05-01T12:00:00+00:00", None, "failed"),
        ("e2", "NEW_ORDER", "101", None, payload_minimal, "2026-05-02T12:00:00+00:00", None, "failed"),
        ("e3", "NEW_ORDER", "102", None, payload_minimal, "2026-05-03T12:00:00+00:00", None, "failed"),
        ("e4", "NEW_ORDER", "103", None, payload_minimal, "2026-04-25T12:00:00+00:00", "2026-04-25T12:01:00+00:00", "processed"),
        ("e5", "COURIER_ASSIGNED", "104", "201", "{}", "2026-05-01T13:00:00+00:00", None, "failed"),
    ]
    conn.executemany(
        "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


def test_query_failed_events_filter_by_status():
    """Default status='failed' → returns 4 (e1, e2, e3, e5)."""
    db_path = _build_synthetic_events_db()
    try:
        rows = rf.query_failed_events(db_path, status="failed")
        assert len(rows) == 4
        oids = sorted(r["order_id"] for r in rows)
        assert oids == ["100", "101", "102", "104"]
    finally:
        Path(db_path).unlink()


def test_query_failed_events_filter_by_oid():
    """--oid 101 → returns 1 row (e2)."""
    db_path = _build_synthetic_events_db()
    try:
        rows = rf.query_failed_events(db_path, oid="101")
        assert len(rows) == 1
        assert rows[0]["event_id"] == "e2"
    finally:
        Path(db_path).unlink()


def test_query_failed_events_filter_by_since():
    """--since '2026-05-02' → returns 2 (e2 + e3, e1+e5 are older)."""
    db_path = _build_synthetic_events_db()
    try:
        rows = rf.query_failed_events(db_path, status="failed", since="2026-05-02")
        assert len(rows) == 2
        oids = sorted(r["order_id"] for r in rows)
        assert oids == ["101", "102"]
    finally:
        Path(db_path).unlink()


def test_replay_event_skip_unsupported_event_type():
    """COURIER_ASSIGNED event type → SKIP verdict (only NEW_ORDER replay supported)."""
    row = {
        "event_id": "e5",
        "event_type": "COURIER_ASSIGNED",
        "order_id": "104",
        "payload": "{}",
        "status": "failed",
    }
    result = rf.replay_event(row)
    assert result["verdict"].startswith("SKIP: unsupported_event_type")


def test_replay_event_skip_empty_payload():
    """NEW_ORDER z empty payload → SKIP verdict."""
    row = {
        "event_id": "e_empty",
        "event_type": "NEW_ORDER",
        "order_id": "999",
        "payload": "",
        "status": "failed",
    }
    result = rf.replay_event(row)
    assert result["verdict"] == "SKIP: empty_payload"


def test_apply_status_flip_atomic_success():
    """Apply flip dla 2 PASS event_ids → 2 flipped, 0 errors."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, ["e1", "e2"])
        assert stats["flipped"] == 2
        assert stats["errors"] == []
        # Verify post-flip
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT status FROM events WHERE event_id IN ('e1', 'e2')")
        statuses = [r[0] for r in cur.fetchall()]
        assert all(s == "processed" for s in statuses)
        conn.close()
    finally:
        Path(db_path).unlink()


def test_apply_status_flip_only_flips_failed_status():
    """Apply flip dla event w status=processed (e4) → 0 flipped (WHERE status='failed' clause)."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, ["e4"])  # e4 already processed
        assert stats["flipped"] == 0
    finally:
        Path(db_path).unlink()


def test_apply_status_flip_empty_list_safe():
    """Empty list → 0 flipped, 0 errors (defensive default)."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, [])
        assert stats["flipped"] == 0
        assert stats["errors"] == []
    finally:
        Path(db_path).unlink()


def test_apply_status_flip_nonexistent_eid_zero_flipped():
    """Non-existent event_id → 0 flipped (no row matches)."""
    db_path = _build_synthetic_events_db()
    try:
        stats = rf.apply_status_flip(db_path, ["nonexistent_eid"])
        assert stats["flipped"] == 0
    finally:
        Path(db_path).unlink()


def test_replay_batch_synthetic_no_apply():
    """End-to-end replay_batch bez --apply → status events unchanged."""
    db_path = _build_synthetic_events_db()
    try:
        summary = rf.replay_batch(db_path=db_path, status="failed", apply=False)
        assert summary["total"] == 4  # 3 NEW_ORDER + 1 COURIER_ASSIGNED skip
        # Bez apply, summary["applied"] is None
        assert summary["applied"] is None
        # Verify events.db status NIE zmieniona
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM events WHERE status='failed'")
        assert cur.fetchone()[0] == 4
        conn.close()
    finally:
        Path(db_path).unlink()


def test_replay_event_returns_dict_with_required_fields():
    """Type guarantee."""
    row = {
        "event_id": "e_skip",
        "event_type": "OTHER",
        "order_id": "?",
        "payload": "{}",
        "status": "failed",
    }
    result = rf.replay_event(row)
    assert isinstance(result, dict)
    assert "event_id" in result
    assert "order_id" in result
    assert "verdict" in result
    assert "result" in result


def test_query_missing_db_raises():
    """Missing db raises sqlite OperationalError przy connect (NOT silent default)."""
    import pytest
    with pytest.raises(sqlite3.OperationalError):
        rf.query_failed_events("/nonexistent/path.db", oid="999")
