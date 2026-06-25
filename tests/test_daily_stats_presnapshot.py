"""Test fixu pre-prune-snapshot dla daily_stats (2026-06-25).

Dowodzi ON≠OFF: flaga DAILY_STATS_USE_PRESNAPSHOT przełącza źródło orders.
Bez pytest — runner-agnostyczny (uruchamiany venv sheets/dispatch bezpośrednio).
"""
import sys, json, tempfile
from pathlib import Path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import daily_stats_sheets as D
from datetime import date

def _setup(tmp, snap_orders):
    """Tworzy snapshot orders_state_<target+1>.json w tmp; patchuje SNAPSHOT_DIR."""
    target = date(2026, 6, 24)
    snap = Path(tmp) / f"orders_state_{date(2026,6,25).isoformat()}.json"
    if snap_orders is not None:
        snap.write_text(json.dumps(snap_orders), encoding="utf-8")
    D.SNAPSHOT_DIR = tmp
    return target

def run():
    target = date(2026, 6, 24)
    SNAP = {"1": {"status": "delivered"}, "2": {"status": "delivered"}, "3": {"status": "delivered"}}
    LIVE = {"9": {"status": "delivered"}}  # zdziesiątkowany żywy stan (1 order)

    orig_flag = D.flag
    orig_get_all = D.state_machine.get_all
    orig_dir = D.SNAPSHOT_DIR
    passed = 0
    try:
        D.state_machine.get_all = lambda: dict(LIVE)

        # --- T1: flaga ON + snapshot istnieje → snapshot (3 orders) ---
        with tempfile.TemporaryDirectory() as tmp:
            _setup(tmp, SNAP)
            D.flag = lambda name, default=None: True if name == "DAILY_STATS_USE_PRESNAPSHOT" else orig_flag(name, default)
            got = D._load_orders_for_day(target)
            assert len(got) == 3, f"T1 ON+snapshot: oczekiwano 3, jest {len(got)}"
            passed += 1; print("T1 OK: flaga ON + snapshot → snapshot (3 orders)")

        # --- T2: flaga OFF → żywy stan (1 order), IGNORUJE snapshot ---
        with tempfile.TemporaryDirectory() as tmp:
            _setup(tmp, SNAP)
            D.flag = lambda name, default=None: False if name == "DAILY_STATS_USE_PRESNAPSHOT" else orig_flag(name, default)
            got = D._load_orders_for_day(target)
            assert len(got) == 1, f"T2 OFF: oczekiwano 1 (live), jest {len(got)}"
            passed += 1; print("T2 OK: flaga OFF → live state (1 order) — ON≠OFF dowiedzione")

        # --- T3: flaga ON + snapshot BRAK → fallback do live (1 order) ---
        with tempfile.TemporaryDirectory() as tmp:
            _setup(tmp, None)  # nie tworzy pliku
            D.flag = lambda name, default=None: True if name == "DAILY_STATS_USE_PRESNAPSHOT" else orig_flag(name, default)
            got = D._load_orders_for_day(target)
            assert len(got) == 1, f"T3 ON+brak snapshotu: oczekiwano 1 (fallback live), jest {len(got)}"
            passed += 1; print("T3 OK: flaga ON + snapshot brak → fallback live (1 order)")

        # --- T4: count_orders_by_hour z podanym orders nie woła state_machine ---
        sentinel = {"x": {"status": "delivered", "czas_kuriera_warsaw": "2026-06-24T18:30:00"}}
        D.state_machine.get_all = lambda: (_ for _ in ()).throw(AssertionError("get_all NIE powinno być wołane gdy orders podane"))
        c = D.count_orders_by_hour(target, sentinel)
        assert c.get(18) == 1, f"T4: oczekiwano bucket 18=1, jest {c.get(18)}"
        passed += 1; print("T4 OK: count_orders_by_hour(orders=...) używa podanych danych")
    finally:
        D.flag = orig_flag
        D.state_machine.get_all = orig_get_all
        D.SNAPSHOT_DIR = orig_dir

    print(f"\n{passed}/4 PASS")
    return 0 if passed == 4 else 1

if __name__ == "__main__":
    sys.exit(run())
