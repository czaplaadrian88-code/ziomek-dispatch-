"""Regression dla F10 (audit STATE_OWNERSHIP 2026-05-07): duplicate
COURIER_DELIVERED audit_log entries fix via canonical event_id.

Pre-fix: 4 emitters używały różnych event_id namespaces:
  - panel_watcher main flow: {oid}_COURIER_DELIVERED_panel
  - V3.20 packs_ghost_detect: {oid}_COURIER_DELIVERED_packs_ghost
  - panel_watcher reconcile section: {oid}_COURIER_DELIVERED_reconcile
  - reconciliation/auto_resync.py: {oid}_COURIER_DELIVERED_phantom_resync

Different PKs → 2-4 audit_log entries dla SAME order_id → R-04 tier
metrics inflated (delivered counted 2× → fałszywy promote nad 50-deliv próg).

Post-fix: wszystkie 4 emitters używają {oid}_COURIER_DELIVERED_canonical.
INSERT OR IGNORE collapse na 1 audit_log entry. Audit "co wykryło first" =
payload.deliv_source field zamiast event_id variant. Pierwszy emit wins
(ostatnie INSERT OR IGNORE no-op).
"""
import importlib
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  PASS  {label}")
            results["pass"] += 1
        else:
            print(f"  FAIL  {label}  {detail}")
            results["fail"] += 1

    # --- TEST 1: 4 emit sites use canonical event_id ---
    print("\n=== test 1: grep 4 emit sites use _COURIER_DELIVERED_canonical ===")
    pw_path = Path(__file__).resolve().parents[1] / "panel_watcher.py"
    ar_path = Path(__file__).resolve().parents[1] / "reconciliation" / "auto_resync.py"

    pw_text = pw_path.read_text()
    ar_text = ar_path.read_text()

    # panel_watcher.py: 3 emit sites (main / packs_ghost / reconcile)
    pw_canonical_count = pw_text.count("_COURIER_DELIVERED_canonical")
    expect(f"panel_watcher.py 3× _COURIER_DELIVERED_canonical (got {pw_canonical_count})",
           pw_canonical_count == 3)
    expect("panel_watcher.py 0× _COURIER_DELIVERED_panel (legacy)",
           pw_text.count("_COURIER_DELIVERED_panel") == 0)
    expect("panel_watcher.py 0× _COURIER_DELIVERED_packs_ghost (legacy)",
           pw_text.count("_COURIER_DELIVERED_packs_ghost") == 0)
    expect("panel_watcher.py 0× _COURIER_DELIVERED_reconcile (legacy)",
           pw_text.count("_COURIER_DELIVERED_reconcile") == 0)

    # auto_resync.py: 1 site for COURIER_DELIVERED (ORDER_RETURNED_TO_POOL zostaje phantom_resync)
    ar_canonical_count = ar_text.count("_COURIER_DELIVERED_canonical")
    expect(f"auto_resync.py 1× _COURIER_DELIVERED_canonical (got {ar_canonical_count})",
           ar_canonical_count == 1)
    expect("auto_resync.py wciąż używa phantom_resync dla ORDER_RETURNED_TO_POOL",
           "_phantom_resync" in ar_text)

    # --- TEST 2: payload deliv_source field ---
    print("\n=== test 2: każdy emit site ma deliv_source w payload ===")
    expect("panel_watcher.py main: deliv_source=panel",
           '"deliv_source": "panel"' in pw_text)
    expect("panel_watcher.py packs_ghost: deliv_source=packs_ghost_detect",
           '"deliv_source": "packs_ghost_detect"' in pw_text)
    expect("panel_watcher.py reconcile: deliv_source=reconcile",
           '"deliv_source": "reconcile"' in pw_text)
    expect("auto_resync.py: deliv_source=reconciliation_inferred",
           '"deliv_source": "reconciliation_inferred"' in ar_text)

    # --- TEST 3: end-to-end INSERT OR IGNORE collapse via mocked _db_path ---
    print("\n=== test 3: 4 emits same oid → 1 events row (PK collision) ===")
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "events.db"
        # Init schema (mirror event_bus.init() — events + processed_events tables)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    order_id TEXT,
                    courier_id TEXT,
                    payload TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT DEFAULT 'pending',
                    processed_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    processed_at TEXT NOT NULL
                )
            """)

        # Patch _db_path → tmpdir DB
        from dispatch_v2 import event_bus
        with mock.patch.object(event_bus, "_db_path", return_value=str(db_path)):
            oid = f"F10_TEST_{os.getpid()}"
            cid = "999"
            canonical_eid = f"{oid}_COURIER_DELIVERED_canonical"

            # Simulate 4 emitters all firing same oid (race in real prod)
            ev1 = event_bus.emit(
                "COURIER_DELIVERED", order_id=oid, courier_id=cid,
                payload={"deliv_source": "panel"},
                event_id=canonical_eid,
            )
            ev2 = event_bus.emit(
                "COURIER_DELIVERED", order_id=oid, courier_id=cid,
                payload={"deliv_source": "packs_ghost_detect"},
                event_id=canonical_eid,
            )
            ev3 = event_bus.emit(
                "COURIER_DELIVERED", order_id=oid, courier_id=cid,
                payload={"deliv_source": "reconcile"},
                event_id=canonical_eid,
            )
            ev4 = event_bus.emit(
                "COURIER_DELIVERED", order_id=oid, courier_id=cid,
                payload={"deliv_source": "reconciliation_inferred"},
                event_id=canonical_eid,
            )

            # First emit wins (returns event_id), subsequent return None (idempotent skip)
            expect("ev1 returns event_id (first emit)", ev1 == canonical_eid)
            expect("ev2 returns None (idempotent skip)", ev2 is None)
            expect("ev3 returns None (idempotent skip)", ev3 is None)
            expect("ev4 returns None (idempotent skip)", ev4 is None)

            # Verify events table has only 1 row dla tego oid
            with sqlite3.connect(str(db_path)) as conn:
                cur = conn.execute(
                    "SELECT count(*), payload FROM events WHERE order_id=? AND event_type=?",
                    (oid, "COURIER_DELIVERED"),
                )
                row = cur.fetchone()
                row_count = row[0]
                payload_str = row[1] if row_count else ""
            expect(f"events table 1 row dla oid (got {row_count})", row_count == 1)

            # Verify pierwsze deliv_source survived (panel)
            expect("pierwsze deliv_source 'panel' survived (first-write-wins)",
                   '"deliv_source": "panel"' in payload_str)
            expect("subsequent deliv_source 'reconcile' NIE w persisted payload",
                   '"deliv_source": "reconcile"' not in payload_str)
            expect("subsequent deliv_source 'reconciliation_inferred' NIE w persisted payload",
                   '"deliv_source": "reconciliation_inferred"' not in payload_str)

    # --- TEST 4: ORDER_RETURNED_TO_POOL nadal używa phantom_resync ---
    print("\n=== test 4: ORDER_RETURNED_TO_POOL zachowuje _phantom_resync (osobny path) ===")
    # Verify auto_resync.py logic — not changed for ORDER_RETURNED_TO_POOL
    expect("auto_resync.py {oid}_{inferred}_phantom_resync dla non-DELIVERED",
           'event_id = f"{oid}_{inferred}_phantom_resync"' in ar_text)

    # --- TEST 5: backwards-compat audit_log ---
    print("\n=== test 5: audit_log payloads zachowują 'source' field (backward-compat) ===")
    # panel_watcher packs_ghost ma source=packs_ghost_detect (legacy field)
    expect("panel_watcher packs_ghost zachowuje source field",
           '"source": "packs_ghost_detect"' in pw_text)
    expect("panel_watcher reconcile zachowuje source field",
           '"source": "reconcile"' in pw_text)
    expect("auto_resync.py zachowuje source field reconciliation_inferred",
           '"source": "reconciliation_inferred"' in ar_text)

    print(f"\n=== RESULT: {results['pass']} PASS / {results['fail']} FAIL ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
