"""Opcja C (2026-05-07) — event_bus audit_log table tests.

Coverage:
- emit_audit basic insert + schema (no status column)
- emit_audit idempotency (INSERT OR IGNORE on PK)
- emit_audit rejects queue types (ValueError)
- AUDIT_EVENT_TYPES disjoint z QUEUE_EVENT_TYPES; union == EVENT_TYPES
- cleanup_audit_log retention
- get_pending_count filters by event_types (worker_stuck alert filter)
- _init_audit_log_table idempotent
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus  # noqa: E402


def _setup_tmp_db(monkeypatch_target_dict: dict):
    """Build empty events.db w tmp + override event_bus._db_path."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    f.close()
    db_path = f.name
    conn = sqlite3.connect(db_path)
    # Replicate prod schema (events table dla queue testów)
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
    monkeypatch_target_dict["original"] = event_bus._db_path
    event_bus._db_path = lambda: db_path
    # init audit_log table w tmp DB
    event_bus._init_audit_log_table()
    return db_path


def _restore_db(monkeypatch_target_dict: dict, db_path: str):
    event_bus._db_path = monkeypatch_target_dict["original"]
    Path(db_path).unlink(missing_ok=True)
    Path(db_path + "-wal").unlink(missing_ok=True)
    Path(db_path + "-shm").unlink(missing_ok=True)


# ─── Test 1: AUDIT/QUEUE/BROADCAST sets disjoint + cover EVENT_TYPES ────
def test_audit_queue_sets_disjoint_and_complete():
    """A4 (2026-05-08): teraz 3 sets — AUDIT, QUEUE, BROADCAST — disjoint
    + union == EVENT_TYPES."""
    audit = event_bus.AUDIT_EVENT_TYPES
    queue = event_bus.QUEUE_EVENT_TYPES
    broadcast = event_bus.BROADCAST_EVENT_TYPES
    assert audit & queue == set(), f"AUDIT and QUEUE overlap: {audit & queue}"
    assert audit & broadcast == set(), f"AUDIT and BROADCAST overlap: {audit & broadcast}"
    assert queue & broadcast == set(), f"QUEUE and BROADCAST overlap: {queue & broadcast}"
    union = audit | queue | broadcast
    assert union == event_bus.EVENT_TYPES, f"Union != EVENT_TYPES: missing {event_bus.EVENT_TYPES - union}"
    # Sanity: correction is audit-only too; no queue consumer may race state.
    assert audit == {
        "COURIER_ASSIGNED", "CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED",
        "PANEL_UNREACHABLE", "ORDER_RETURNED_TO_POOL", "ORDER_RESURRECTED",
        "ORDER_RECLAIMED_TO_CZASOWKA",
    }
    # Sanity: A4 broadcast types
    assert broadcast == {"CONFIG_RELOAD"}


# ─── Test 2: audit_log schema has NO status column ──────────────────────
def test_audit_log_schema_no_status_field():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        conn = sqlite3.connect(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()]
        conn.close()
        assert "status" not in cols, f"audit_log MUST be append-only, has status col: {cols}"
        assert "processed_at" not in cols, f"audit_log MUST be append-only, has processed_at col: {cols}"
        # Required cols
        for required in ["event_id", "event_type", "order_id", "courier_id", "payload", "created_at"]:
            assert required in cols, f"audit_log missing required col: {required}"
    finally:
        _restore_db(state, db_path)


# ─── Test 3: emit_audit basic insert ────────────────────────────────────
def test_emit_audit_basic_insert():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid = event_bus.emit_audit(
            "COURIER_ASSIGNED",
            order_id="123",
            courier_id="500",
            payload={"src": "test"},
            event_id="test_eid_1",
        )
        assert eid == "test_eid_1"
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT event_id, event_type, order_id, courier_id, payload FROM audit_log").fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0][0] == "test_eid_1"
        assert rows[0][1] == "COURIER_ASSIGNED"
        assert rows[0][2] == "123"
        assert rows[0][3] == "500"
        assert json.loads(rows[0][4]) == {"src": "test"}
    finally:
        _restore_db(state, db_path)


# ─── Test 4: emit_audit idempotent (INSERT OR IGNORE) ──────────────────
def test_emit_audit_idempotent_same_event_id():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        eid1 = event_bus.emit_audit("COURIER_ASSIGNED", order_id="999", event_id="dup_eid")
        eid2 = event_bus.emit_audit("COURIER_ASSIGNED", order_id="999", event_id="dup_eid")
        assert eid1 == "dup_eid"
        assert eid2 is None, f"second call should return None for duplicate, got {eid2}"
        conn = sqlite3.connect(db_path)
        cnt = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        conn.close()
        assert cnt == 1
    finally:
        _restore_db(state, db_path)


# ─── Test 5: emit_audit rejects queue types ─────────────────────────────
def test_emit_audit_rejects_queue_event_type():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        for queue_type in ["NEW_ORDER", "COURIER_PICKED_UP", "COURIER_DELIVERED"]:
            try:
                event_bus.emit_audit(queue_type, order_id="111", event_id=f"x_{queue_type}")
                assert False, f"emit_audit({queue_type}) should raise ValueError"
            except ValueError as e:
                assert "audit type" in str(e).lower() or queue_type in str(e)
    finally:
        _restore_db(state, db_path)


# ─── Test 6: emit_audit rejects unknown type ────────────────────────────
def test_emit_audit_rejects_unknown_type():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        try:
            event_bus.emit_audit("NONEXISTENT_TYPE", order_id="1", event_id="x")
            assert False, "should raise"
        except ValueError:
            pass
    finally:
        _restore_db(state, db_path)


# ─── Test 7: cleanup_audit_log retention ────────────────────────────────
def test_cleanup_audit_log_retention():
    # SP-B2-PEAKWIN fix flake (2026-06-11): cleanup_audit_log pomija peak
    # (11-14/17-20 Warsaw) → test odpalony w peaku dostawał deleted=0.
    # Testujemy retencję, nie bramkę peak — mrozimy bramkę na False.
    state = {}
    db_path = _setup_tmp_db(state)
    _orig_peak = event_bus._is_peak_window
    event_bus._is_peak_window = lambda now=None: False
    try:
        # Insert raw old + recent
        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
            ("old_eid", "COURIER_ASSIGNED", "1", None, "{}", "2025-01-01T00:00:00+00:00"),
        )
        conn.execute(
            "INSERT INTO audit_log VALUES (?, ?, ?, ?, ?, ?)",
            ("recent_eid", "COURIER_ASSIGNED", "2", None, "{}", event_bus.now_iso()),
        )
        conn.commit()
        conn.close()
        deleted = event_bus.cleanup_audit_log(retention_days=90)
        assert deleted == 1, f"expected 1 deleted, got {deleted}"
        conn = sqlite3.connect(db_path)
        remaining = [r[0] for r in conn.execute("SELECT event_id FROM audit_log").fetchall()]
        conn.close()
        assert remaining == ["recent_eid"]
    finally:
        event_bus._is_peak_window = _orig_peak
        _restore_db(state, db_path)


# ─── Test 8: get_pending_count filter by event_types ────────────────────
def test_get_pending_count_filters_by_event_types():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        # Inject 5 NEW_ORDER pending + 100 COURIER_ASSIGNED-style "legacy pending"
        # (pre-Opcja-C state where audit types accumulated in events table).
        conn = sqlite3.connect(db_path)
        for i in range(5):
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"new_{i}", "NEW_ORDER", str(i), None, "{}", event_bus.now_iso(), None, "pending"),
            )
        for i in range(100):
            conn.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (f"asg_{i}", "COURIER_ASSIGNED", str(i), None, "{}", event_bus.now_iso(), None, "pending"),
            )
        conn.commit()
        conn.close()

        global_cnt = event_bus.get_pending_count()
        queue_cnt = event_bus.get_pending_count(event_types=list(event_bus.QUEUE_EVENT_TYPES))
        audit_cnt = event_bus.get_pending_count(event_types=list(event_bus.AUDIT_EVENT_TYPES))

        assert global_cnt == 105, f"global={global_cnt}"
        assert queue_cnt == 5, f"queue={queue_cnt} (NEW_ORDER only) — WORKER_STUCK alert sees this"
        assert audit_cnt == 100, f"audit={audit_cnt}"
        # The whole point of Opcja C: queue << global, alert nie pali na audit pending
    finally:
        _restore_db(state, db_path)


# ─── Test 9: _init_audit_log_table idempotent ───────────────────────────
def test_init_audit_log_table_idempotent():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        # Already initialized in setup; second call must not fail
        event_bus._init_audit_log_table()
        event_bus._init_audit_log_table()
        # Insert + verify still works
        eid = event_bus.emit_audit("COURIER_ASSIGNED", order_id="1", event_id="post_reinit")
        assert eid == "post_reinit"
    finally:
        _restore_db(state, db_path)


# ─── Test 10: emit() (queue path) NIE pisze do audit_log ────────────────
def test_emit_queue_path_does_not_write_to_audit_log():
    state = {}
    db_path = _setup_tmp_db(state)
    try:
        # Queue emit (NEW_ORDER) should land in events, not audit_log
        eid = event_bus.emit(
            "NEW_ORDER",
            order_id="500",
            payload={"x": 1},
            event_id="queue_eid",
        )
        assert eid == "queue_eid"
        conn = sqlite3.connect(db_path)
        events_cnt = conn.execute("SELECT COUNT(*) FROM events WHERE event_id='queue_eid'").fetchone()[0]
        audit_cnt = conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        conn.close()
        assert events_cnt == 1
        assert audit_cnt == 0, "emit() must NOT write to audit_log"
    finally:
        _restore_db(state, db_path)


# ─── Runner ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    tests = [
        ("audit_queue_sets_disjoint_and_complete", test_audit_queue_sets_disjoint_and_complete),
        ("audit_log_schema_no_status_field", test_audit_log_schema_no_status_field),
        ("emit_audit_basic_insert", test_emit_audit_basic_insert),
        ("emit_audit_idempotent_same_event_id", test_emit_audit_idempotent_same_event_id),
        ("emit_audit_rejects_queue_event_type", test_emit_audit_rejects_queue_event_type),
        ("emit_audit_rejects_unknown_type", test_emit_audit_rejects_unknown_type),
        ("cleanup_audit_log_retention", test_cleanup_audit_log_retention),
        ("get_pending_count_filters_by_event_types", test_get_pending_count_filters_by_event_types),
        ("init_audit_log_table_idempotent", test_init_audit_log_table_idempotent),
        ("emit_queue_path_does_not_write_to_audit_log", test_emit_queue_path_does_not_write_to_audit_log),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL  {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'=' * 50}\nResult: {passed}/{len(tests)} PASS, {failed} FAIL")
    sys.exit(0 if failed == 0 else 1)
