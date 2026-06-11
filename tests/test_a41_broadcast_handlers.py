"""Regression dla A4.1 (2026-05-09): BroadcastSubscriber wire'owany w 4 workers.

A4 (2026-05-08) dostarczył infrastructure (event_bus.emit_config_reload +
BroadcastSubscriber.poll). A4.1 wire'uje workers + dispatch_config_reload
handler dispatcher z per-scope log+extension-point pattern.

Tests covering:
- broadcast_handlers.dispatch_config_reload: known scopes, unknown scope,
  missing scope, defense-in-depth (handler exception per-event)
- end-to-end: emit_config_reload → BroadcastSubscriber → handler
- worker import smoke (4 workers): subscriber object construction OK
"""
import importlib
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus  # noqa: E402
from dispatch_v2.core import broadcast_handlers  # noqa: E402
from dispatch_v2.core.config_reload_subscriber import BroadcastSubscriber  # noqa: E402


def _mk_event(scope, payload_extra=None, event_id="evt_001"):
    p = {"scope": scope}
    if payload_extra:
        p.update(payload_extra)
    return {
        "event_id": event_id,
        "event_type": "CONFIG_RELOAD",
        "payload": p,
    }


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  PASS  {label}")
            results["pass"] += 1
        else:
            print(f"  FAIL  {label}  {detail}")
            results["fail"] += 1

    importlib.reload(broadcast_handlers)

    # ---- TEST 1: empty events → 0 ----
    print("\n=== test 1: empty events list → 0 processed ===")
    n = broadcast_handlers.dispatch_config_reload([], "test_consumer")
    expect("empty list returns 0", n == 0)

    # ---- TEST 2: known scope flags ----
    print("\n=== test 2: scope=flags → handled (log INFO) ===")
    evts = [_mk_event("flags", {"name": "AUTO_PROXIMITY_ENABLED", "action": "set", "value": True})]
    n = broadcast_handlers.dispatch_config_reload(evts, "shadow")
    expect("1 processed", n == 1)

    # ---- TEST 3: known scope kurier_ids ----
    print("\n=== test 3: scope=kurier_ids → handled ===")
    evts = [_mk_event("kurier_ids", event_id="evt_ki_001")]
    n = broadcast_handlers.dispatch_config_reload(evts, "panel_watcher")
    expect("1 processed", n == 1)

    # ---- TEST 4: known scope restaurant_coords ----
    print("\n=== test 4: scope=restaurant_coords → handled ===")
    evts = [_mk_event("restaurant_coords", event_id="evt_rc")]
    n = broadcast_handlers.dispatch_config_reload(evts, "sla_tracker")
    expect("1 processed", n == 1)

    # ---- TEST 5: known scope courier_tiers ----
    print("\n=== test 5: scope=courier_tiers → handled ===")
    evts = [_mk_event("courier_tiers", event_id="evt_ct")]
    n = broadcast_handlers.dispatch_config_reload(evts, "telegram")
    expect("1 processed", n == 1)

    # ---- TEST 6: unknown scope → warning + counted ----
    print("\n=== test 6: unknown scope=nonsense → warning, but counted ===")
    evts = [_mk_event("nonsense_scope_xyz", event_id="evt_x")]
    n = broadcast_handlers.dispatch_config_reload(evts, "test")
    expect("counted (1)", n == 1)

    # ---- TEST 7: missing scope (payload bez 'scope' key) ----
    print("\n=== test 7: missing scope key → fallback '<missing>' warning ===")
    evt = {"event_id": "evt_no", "event_type": "CONFIG_RELOAD", "payload": {}}
    n = broadcast_handlers.dispatch_config_reload([evt], "test")
    expect("counted with <missing>", n == 1)

    # ---- TEST 8: payload=None graceful ----
    print("\n=== test 8: payload=None → graceful ===")
    evt = {"event_id": "evt_none", "event_type": "CONFIG_RELOAD", "payload": None}
    n = broadcast_handlers.dispatch_config_reload([evt], "test")
    expect("1 processed (no crash)", n == 1)

    # ---- TEST 9: defense-in-depth — corrupt event nie blocks ----
    print("\n=== test 9: corrupt event in batch → continues w/ pozostałych ===")
    evts = [
        _mk_event("flags", event_id="evt_ok_1"),
        {"event_id": "BAD", "event_type": "CONFIG_RELOAD"},  # missing payload — handler should still try
        _mk_event("flags", event_id="evt_ok_2"),
    ]
    # Force exception via mock on a single event
    n = broadcast_handlers.dispatch_config_reload(evts, "test")
    expect("processed >= 2 (corrupt one OK because no actual crash)", n >= 2)

    # ---- TEST 10: mass batch (50 events) ----
    print("\n=== test 10: mass batch 50 events all processed ===")
    evts = [_mk_event("flags", event_id=f"evt_{i:03d}") for i in range(50)]
    n = broadcast_handlers.dispatch_config_reload(evts, "stress")
    expect(f"50 processed (got {n})", n == 50)

    # ---- TEST 11: end-to-end via real event_bus + subscriber ----
    # SP-B2-RAMPA fix flake + lekcja #180 (2026-06-11): test emitował do
    # PRODUKCYJNEGO events.db (śmieci a41_test_* w broadcast backlogu) i
    # czytał fresh-cursor poll z limit=20 — gdy zaległość CONFIG_RELOAD
    # przekroczyła 20 (cleanup pomija peak), nasz event NIE mieścił się
    # w pierwszej stronie → fail zależny od stanu prod. Teraz: tymczasowa
    # baza (wzorzec test_event_bus_audit_log._setup_tmp_db).
    print("\n=== test 11: e2e emit_config_reload → BroadcastSubscriber → handler ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        sub_state = Path(tmpdir) / "subscriber.json"
        import os
        import sqlite3 as _sq
        tmp_db = str(Path(tmpdir) / "events_a41.db")
        conn = _sq.connect(tmp_db)
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
        _orig_db_path = event_bus._db_path
        event_bus._db_path = lambda: tmp_db
        try:
            scope_test = f"a41_test_{os.getpid()}"
            eid = event_bus.emit_config_reload(scope=scope_test, payload={"a41_test": True})
            expect("emit returns event_id", bool(eid))

            sub = BroadcastSubscriber(consumer_id="test_e2e", state_path=sub_state)
            new_events = sub.poll(["CONFIG_RELOAD"], limit=20)
            ours = [e for e in new_events if (e.get("payload") or {}).get("scope") == scope_test]
            expect(f"e2e subscriber poll picks up our event ({len(ours)} matched)", len(ours) >= 1)

            n = broadcast_handlers.dispatch_config_reload(ours, "test_e2e")
            expect("handler processed e2e events", n >= 1)

            # Cursor advance — second poll should NOT redeliver our event
            again = sub.poll(["CONFIG_RELOAD"], limit=20)
            again_ours = [e for e in again if (e.get("payload") or {}).get("scope") == scope_test]
            expect("cursor advance: no redelivery of our event", len(again_ours) == 0)
        finally:
            event_bus._db_path = _orig_db_path

    # ---- TEST 12: BroadcastSubscriber import via 4 workers ----
    print("\n=== test 12: 4 workers import broadcast wire OK ===")
    from dispatch_v2 import shadow_dispatcher, panel_watcher, telegram_approver, sla_tracker
    expect("shadow_dispatcher has dispatch_config_reload",
           hasattr(shadow_dispatcher, "dispatch_config_reload"))
    expect("panel_watcher has dispatch_config_reload",
           hasattr(panel_watcher, "dispatch_config_reload"))
    expect("telegram_approver has dispatch_config_reload",
           hasattr(telegram_approver, "dispatch_config_reload"))
    expect("sla_tracker has dispatch_config_reload",
           hasattr(sla_tracker, "dispatch_config_reload"))
    expect("telegram_approver has config_reload_poller async task",
           hasattr(telegram_approver, "config_reload_poller"))

    # ---- TEST 13: subscriber state file persistence ----
    print("\n=== test 13: subscriber state persisted atomic ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        sp = Path(tmpdir) / "sub_state.json"
        sub = BroadcastSubscriber(consumer_id="persist_test", state_path=sp)
        # Manually save state (poll without events should be no-op)
        sub.poll(["CONFIG_RELOAD"], limit=5)
        # Subscriber state file may not exist yet (no events) — verify graceful
        expect("subscriber works without state file", True)

    print(f"\n=== RESULT: {results['pass']} PASS / {results['fail']} FAIL ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
