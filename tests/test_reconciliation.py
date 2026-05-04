"""TASK 2 Część B (2026-05-04) — Reconciliation Service tests.

Coverage:
  1. test_phantom_detection_simple — events.db active + state delivered → PHANTOM
  2. test_phantom_missing_from_state — events.db active + state missing → PHANTOM
  3. test_phantom_age_classification — 4h boundary (auto vs alert_only)
  4. test_ghost_detection — events.db terminal + state active → GHOST (alert)
  5. test_auto_resync_emits_correct_event — PHANTOM → emit COURIER_DELIVERED
  6. test_auto_resync_hard_cap_5 — eligible >cap → all become alert_only_hard_cap
  7. test_alert_only_below_4h — phantom <4h → no resync, alert
  8. test_no_double_resync_idempotent — emit dedup → no double-update
  9. test_safety_stop_on_anomaly — equivalent test_auto_resync_hard_cap (verbose)
  10. test_log_record_structure — all fields present
"""
import os
import sys
import sqlite3
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.reconciliation import (
    phantom_detector,
    auto_resync,
    reconcile_log,
    health_endpoint,
)


# ---------- Test infrastructure ----------

NOW_FIXED = datetime(2026, 5, 4, 12, 0, 0, tzinfo=timezone.utc)


def make_temp_events_db(rows):
    """Build temp events.db. rows = [(order_id, event_type, courier_id, created_at_iso), ...]"""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = sqlite3.connect(tmp.name)
    conn.execute("""CREATE TABLE events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        order_id TEXT,
        courier_id TEXT,
        payload TEXT,
        created_at TEXT NOT NULL,
        processed_at TEXT,
        status TEXT DEFAULT 'pending'
    )""")
    for i, (oid, et, cid, ts) in enumerate(rows):
        conn.execute(
            "INSERT INTO events (event_id, event_type, order_id, courier_id, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (f"ev_{i}", et, oid, cid, "{}", ts),
        )
    conn.commit()
    conn.close()
    return tmp.name


passed, failed = 0, 0


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1
        print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ---------- Tests ----------

def test_phantom_detection_simple():
    """events.db active + state delivered → PHANTOM with COURIER_DELIVERED inferred."""
    db = make_temp_events_db([
        ("O1", "COURIER_ASSIGNED", "100", "2026-05-03T00:00:00+00:00"),
    ])
    try:
        state = {"O1": {"status": "delivered"}}
        results = phantom_detector.detect_all(db, state, since_days=30, now_dt=NOW_FIXED)
        assert len(results) == 1, f"expected 1 phantom, got {len(results)}"
        d = results[0]
        assert d["order_id"] == "O1"
        assert d["classification"] == "PHANTOM"
        assert d["phantom_subtype"] == "STATE_TERMINAL"
        assert d["state_status"] == "delivered"
        assert d["inferred_terminal_event"] == "COURIER_DELIVERED"
        assert d["last_event_age_h"] >= 35  # ~36h
    finally:
        os.unlink(db)
t("phantom_detection_simple (state_terminal)", test_phantom_detection_simple)


def test_phantom_missing_from_state():
    """events.db active + state missing → PHANTOM with MISSING_FROM_STATE subtype."""
    db = make_temp_events_db([
        ("O2", "COURIER_PICKED_UP", "200", "2026-04-20T12:00:00+00:00"),
    ])
    try:
        state = {}  # O2 not in state
        results = phantom_detector.detect_all(db, state, since_days=30, now_dt=NOW_FIXED)
        assert len(results) == 1
        d = results[0]
        assert d["classification"] == "PHANTOM"
        assert d["phantom_subtype"] == "MISSING_FROM_STATE"
        assert d["state_status"] is None
        assert d["inferred_terminal_event"] == "COURIER_DELIVERED"
        assert d["inferred_reason"] == "state_missing_assume_delivered"
    finally:
        os.unlink(db)
t("phantom_missing_from_state", test_phantom_missing_from_state)


def test_phantom_age_classification():
    """Auto-resync threshold: phantom >4h → eligible auto, <4h → alert_only_young."""
    # 1h ago = young, 5h ago = old
    young_ts = (NOW_FIXED - timedelta(hours=1)).isoformat()
    old_ts = (NOW_FIXED - timedelta(hours=5)).isoformat()
    db = make_temp_events_db([
        ("YOUNG", "COURIER_ASSIGNED", "100", young_ts),
        ("OLD", "COURIER_ASSIGNED", "200", old_ts),
    ])
    try:
        state = {"YOUNG": {"status": "delivered"}, "OLD": {"status": "delivered"}}
        discrepancies = phantom_detector.detect_all(db, state, since_days=30, now_dt=NOW_FIXED)

        emitted = []
        def fake_emit(**kw): emitted.append(kw); return kw.get("event_id")
        def fake_update(_): return None

        result = auto_resync.auto_resync_phantoms(
            discrepancies, fake_emit, fake_update,
            age_threshold_hours=4.0, hard_cap_per_run=10,
        )
        assert result["counts"]["phantoms_total"] == 2
        assert result["counts"]["alerts_only_young"] == 1, result["counts"]
        assert result["counts"]["auto_resyncs"] == 1
        # Verify YOUNG was alerted, OLD was resynced
        actions_by_oid = {a["order_id"]: a["action"] for a in result["actions"]}
        assert actions_by_oid["YOUNG"] == "alert_only_young"
        assert actions_by_oid["OLD"] == "resynced"
    finally:
        os.unlink(db)
t("phantom_age_classification (4h boundary)", test_phantom_age_classification)


def test_ghost_detection():
    """events.db terminal + state active → GHOST."""
    db = make_temp_events_db([
        ("G1", "COURIER_DELIVERED", "100", "2026-05-03T00:00:00+00:00"),
    ])
    try:
        state = {"G1": {"status": "assigned"}}
        results = phantom_detector.detect_all(db, state, since_days=30, now_dt=NOW_FIXED)
        assert len(results) == 1
        d = results[0]
        assert d["classification"] == "GHOST"
        assert d["state_status"] == "assigned"
        assert d["inferred_terminal_event"] is None  # ghosts never auto-resync
    finally:
        os.unlink(db)
t("ghost_detection", test_ghost_detection)


def test_auto_resync_emits_correct_event():
    """Phantom with state.delivered → emit COURIER_DELIVERED z payload source=reconciliation_inferred."""
    discrepancies = [{
        "order_id": "R1", "courier_id": "393",
        "last_event_type": "COURIER_ASSIGNED", "last_event_ts": "2026-05-03T00:00:00+00:00",
        "last_event_age_h": 36.0, "state_status": "delivered",
        "classification": "PHANTOM", "phantom_subtype": "STATE_TERMINAL",
        "inferred_terminal_event": "COURIER_DELIVERED", "inferred_reason": "state_status=delivered",
    }]
    emitted = []
    def fake_emit(**kw): emitted.append(kw); return kw.get("event_id")
    def fake_update(e): emitted.append({"_state_update": e}); return None

    result = auto_resync.auto_resync_phantoms(
        discrepancies, fake_emit, fake_update,
        age_threshold_hours=4.0, hard_cap_per_run=10,
    )
    assert result["counts"]["auto_resyncs"] == 1
    emit_calls = [e for e in emitted if "_state_update" not in e]
    assert len(emit_calls) == 1
    e = emit_calls[0]
    assert e["event_type"] == "COURIER_DELIVERED"
    assert e["order_id"] == "R1"
    assert e["courier_id"] == "393"
    assert e["payload"]["source"] == "reconciliation_inferred"
    assert e["event_id"] == "R1_COURIER_DELIVERED_phantom_resync"
t("auto_resync_emits_correct_event", test_auto_resync_emits_correct_event)


def test_auto_resync_hard_cap_5():
    """6 eligible phantoms with hard_cap=5 → all become alert_only_hard_cap_exceeded."""
    discrepancies = [
        {
            "order_id": f"H{i}", "courier_id": "100",
            "last_event_type": "COURIER_ASSIGNED",
            "last_event_ts": "2026-05-03T00:00:00+00:00",
            "last_event_age_h": 36.0,
            "state_status": "delivered",
            "classification": "PHANTOM",
            "phantom_subtype": "STATE_TERMINAL",
            "inferred_terminal_event": "COURIER_DELIVERED",
            "inferred_reason": "test",
        }
        for i in range(6)
    ]
    emitted = []
    def fake_emit(**kw): emitted.append(kw); return kw.get("event_id")
    def fake_update(_): return None

    result = auto_resync.auto_resync_phantoms(
        discrepancies, fake_emit, fake_update,
        age_threshold_hours=4.0, hard_cap_per_run=5,
    )
    assert result["counts"]["hard_cap_hit"] is True
    assert result["counts"]["auto_resyncs"] == 0
    assert len(emitted) == 0  # NO emits when hard cap exceeded
    assert all(a["action"] == "alert_only_hard_cap_exceeded" for a in result["actions"])
t("auto_resync_hard_cap_5", test_auto_resync_hard_cap_5)


def test_alert_only_below_4h():
    """Phantom <4h → no resync, alert_only_young."""
    discrepancies = [{
        "order_id": "Y1", "courier_id": "100",
        "last_event_type": "COURIER_ASSIGNED",
        "last_event_ts": "2026-05-04T11:00:00+00:00",
        "last_event_age_h": 1.0,  # 1h
        "state_status": "delivered",
        "classification": "PHANTOM",
        "phantom_subtype": "STATE_TERMINAL",
        "inferred_terminal_event": "COURIER_DELIVERED",
        "inferred_reason": "test",
    }]
    emitted = []
    def fake_emit(**kw): emitted.append(kw); return kw.get("event_id")
    def fake_update(_): return None

    result = auto_resync.auto_resync_phantoms(
        discrepancies, fake_emit, fake_update,
        age_threshold_hours=4.0, hard_cap_per_run=10,
    )
    assert result["counts"]["alerts_only_young"] == 1
    assert result["counts"]["auto_resyncs"] == 0
    assert len(emitted) == 0
t("alert_only_below_4h", test_alert_only_below_4h)


def test_no_double_resync_idempotent():
    """Emit returns None (dedup) → action skipped_dedup, no state update."""
    discrepancies = [{
        "order_id": "D1", "courier_id": "100",
        "last_event_type": "COURIER_ASSIGNED",
        "last_event_ts": "2026-05-03T00:00:00+00:00",
        "last_event_age_h": 36.0,
        "state_status": "delivered",
        "classification": "PHANTOM",
        "phantom_subtype": "STATE_TERMINAL",
        "inferred_terminal_event": "COURIER_DELIVERED",
        "inferred_reason": "test",
    }]
    state_updates = []
    def fake_emit(**kw): return None  # dedup
    def fake_update(e): state_updates.append(e)

    result = auto_resync.auto_resync_phantoms(
        discrepancies, fake_emit, fake_update,
        age_threshold_hours=4.0, hard_cap_per_run=10,
    )
    assert result["counts"]["auto_resyncs"] == 0
    assert len(state_updates) == 0  # NO state update when emit deduped
    assert result["actions"][0]["action"] == "skipped_dedup"
t("no_double_resync_idempotent", test_no_double_resync_idempotent)


def test_dry_run_no_emit():
    """dry_run=True → no emit, action=would_resync_dry_run."""
    discrepancies = [{
        "order_id": "DRY1", "courier_id": "100",
        "last_event_type": "COURIER_ASSIGNED",
        "last_event_ts": "2026-05-03T00:00:00+00:00",
        "last_event_age_h": 36.0,
        "state_status": "delivered",
        "classification": "PHANTOM",
        "phantom_subtype": "STATE_TERMINAL",
        "inferred_terminal_event": "COURIER_DELIVERED",
        "inferred_reason": "test",
    }]
    emitted = []
    def fake_emit(**kw): emitted.append(kw); return kw.get("event_id")
    def fake_update(_): return None

    result = auto_resync.auto_resync_phantoms(
        discrepancies, fake_emit, fake_update,
        age_threshold_hours=4.0, hard_cap_per_run=10,
        dry_run=True,
    )
    assert result["counts"]["dry_run"] is True
    assert result["counts"]["auto_resyncs"] == 0  # no resync in dry-run
    assert len(emitted) == 0
    assert result["actions"][0]["action"] == "would_resync_dry_run"
    assert result["actions"][0]["would_emit"] == "COURIER_DELIVERED"
t("dry_run_no_emit", test_dry_run_no_emit)


def test_log_record_structure():
    """All required fields present in log records."""
    actions = [{
        "order_id": "L1", "courier_id": "100",
        "last_event_type": "COURIER_ASSIGNED",
        "last_event_ts": "2026-05-03T00:00:00+00:00",
        "last_event_age_h": 36.0, "state_status": "delivered",
        "classification": "PHANTOM", "phantom_subtype": "STATE_TERMINAL",
        "inferred_terminal_event": "COURIER_DELIVERED",
        "inferred_reason": "test",
        "action": "resynced",
        "emitted_event_id": "L1_COURIER_DELIVERED_phantom_resync",
    }]
    counts = {"phantoms_total": 1, "auto_resyncs": 1}
    records = reconcile_log.build_records(actions, run_id="test_run", counts=counts)
    assert len(records) == 2  # 1 action + 1 RUN_SUMMARY
    rec = records[0]
    required = ["ts", "run_id", "type", "order_id", "courier_id", "last_event_type",
                "last_event_age_h", "state_status", "action", "inferred_terminal_event",
                "emitted_event_id"]
    for k in required:
        assert k in rec, f"missing field: {k}"
    assert records[1]["type"] == "RUN_SUMMARY"
    assert records[1]["counts"] == counts
t("log_record_structure", test_log_record_structure)


def test_log_round_trip_atomic():
    """append_records + query_recent_summary → consistent counts."""
    tmpf = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmpf.close()
    log_path = Path(tmpf.name)
    try:
        actions = [
            {"order_id": "A", "classification": "PHANTOM", "action": "resynced",
             "last_event_age_h": 36.0, "state_status": "delivered"},
            {"order_id": "B", "classification": "PHANTOM", "action": "alert_only_young",
             "last_event_age_h": 1.0, "state_status": "delivered"},
            {"order_id": "C", "classification": "GHOST", "action": "alert_only_ghost",
             "last_event_age_h": 5.0, "state_status": "assigned"},
        ]
        records = reconcile_log.build_records(actions, "test", {"hard_cap_hit": False})
        reconcile_log.append_records(records, log_path=log_path)
        summary = reconcile_log.query_recent_summary(log_path=log_path, hours=24)
        assert summary["discrepancies_24h"]["phantoms"] == 2
        assert summary["discrepancies_24h"]["ghosts"] == 1
        assert summary["discrepancies_24h"]["auto_resyncs"] == 1
        assert summary["discrepancies_24h"]["manual_alerts"] == 2  # young + ghost
    finally:
        os.unlink(tmpf.name)
t("log_round_trip_atomic", test_log_round_trip_atomic)


def test_health_endpoint_response():
    """get_reconciliation_health returns expected schema."""
    tmpf = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmpf.close()
    log_path = Path(tmpf.name)
    try:
        # Empty log → status ok, zero counts
        result = health_endpoint.get_reconciliation_health(log_path=log_path)
        assert result["status"] == "ok"
        assert result["endpoint_version"] == "1"
        assert "discrepancies_24h" in result
    finally:
        os.unlink(tmpf.name)
t("health_endpoint_response", test_health_endpoint_response)


print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
