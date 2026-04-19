"""V3.19c sub A tests — mark_picked_up + gc_invalidated + panel_watcher hook."""
import os
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_TMPDIR = Path(tempfile.mkdtemp(prefix="v319c_suba_"))
from dispatch_v2 import plan_manager as pm
from dispatch_v2 import panel_watcher as pw
from dispatch_v2 import common as cm

pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"

passed = 0
failed = 0


def check(label, cond):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label}")


def _wipe():
    for p in (pm.PLANS_FILE, pm.LOCK_FILE):
        if p.exists():
            p.unlink()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _body_with_pickup(oid_new, oid_bag):
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                      "source_ts": _now_iso()},
        "start_ts": _now_iso(),
        "stops": [
            {"order_id": oid_new, "type": "pickup",
             "coords": {"lat": 53.10, "lng": 23.10}, "dwell_min": 2.0,
             "status_at_plan_time": "assigned"},
            {"order_id": oid_bag, "type": "dropoff",
             "coords": {"lat": 53.18, "lng": 23.22}, "dwell_min": 1.0,
             "status_at_plan_time": "picked_up"},
            {"order_id": oid_new, "type": "dropoff",
             "coords": {"lat": 53.15, "lng": 23.20}, "dwell_min": 1.0,
             "status_at_plan_time": "assigned"},
        ],
        "optimization_method": "bruteforce",
    }


# ============================================================
print("=== V3.19c sub A: mark_picked_up ===")
# ============================================================

# Test 1 — mark_picked_up prunes pickup stop + marks dropoff
_wipe()
pm.save_plan("10", _body_with_pickup("N1", "B1"))
v_before = pm.load_plan("10")["plan_version"]
pm.mark_picked_up("10", "N1", _now_iso())
after = pm.load_plan("10")
pickup_stops = [s for s in after["stops"] if s["order_id"] == "N1" and s["type"] == "pickup"]
check("mark_picked_up prunes pickup stop for N1", len(pickup_stops) == 0)
drop_n1 = [s for s in after["stops"] if s["order_id"] == "N1" and s["type"] == "dropoff"][0]
check("mark_picked_up flips status_at_plan_time→picked_up",
      drop_n1["status_at_plan_time"] == "picked_up")
check("mark_picked_up bumps plan_version",
      after["plan_version"] == v_before + 1)

# Test 2 — no-op when order_id not in plan
_wipe()
pm.save_plan("11", _body_with_pickup("N2", "B2"))
v = pm.load_plan("11")["plan_version"]
pm.mark_picked_up("11", "GHOST", _now_iso())
check("mark_picked_up GHOST order → no version bump",
      pm.load_plan("11")["plan_version"] == v)

# Test 3 — no-op when plan invalidated
_wipe()
pm.save_plan("12", _body_with_pickup("N3", "B3"))
pm.invalidate_plan("12", "MANUAL")
pm.mark_picked_up("12", "N3", _now_iso())
raw = pm.load_plans().get("12")
check("mark_picked_up on invalidated plan → no change",
      raw["invalidated_at"] is not None)

# Test 4 — panel_watcher hook gating: flag OFF → no-op
_wipe()
pm.save_plan("13", _body_with_pickup("N4", "B4"))
cm.ENABLE_SAVED_PLANS = False
pw._update_plan_on_picked_up("13", "N4")
cm.ENABLE_SAVED_PLANS = True
after = pm.load_plan("13")
check("hook flag OFF → no pickup prune",
      any(s["type"] == "pickup" and s["order_id"] == "N4" for s in after["stops"]))

# Test 5 — panel_watcher hook empty cid → silent skip
_wipe()
pm.save_plan("14", _body_with_pickup("N5", "B5"))
v5 = pm.load_plan("14")["plan_version"]
pw._update_plan_on_picked_up("", "N5")
check("hook empty cid → no change",
      pm.load_plan("14")["plan_version"] == v5)

# Test 6 — panel_watcher hook happy path
_wipe()
pm.save_plan("15", _body_with_pickup("N6", "B6"))
pw._update_plan_on_picked_up("15", "N6")
after = pm.load_plan("15")
check("hook happy path prunes pickup stop",
      not any(s["type"] == "pickup" and s["order_id"] == "N6" for s in after["stops"]))

# ============================================================
print("=== V3.19c sub A: gc_invalidated ===")
# ============================================================

# Test 7 — GC removes invalidated older than cutoff
_wipe()
pm.save_plan("20", _body_with_pickup("N7", "B7"))
pm.save_plan("21", _body_with_pickup("N8", "B8"))
pm.invalidate_plan("20", "MANUAL")
# Backdate 20's invalidated_at to 48h ago
with pm._locked(exclusive=True):
    plans = pm._read_raw()
    plans["20"]["invalidated_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=48)
    ).isoformat()
    pm._write_raw(plans)
removed = pm.gc_invalidated(older_than_hours=24.0)
check("gc_invalidated removes old invalidated (48h > 24h)", removed == 1)
check("gc preserves non-invalidated plan 21",
      pm.load_plan("21") is not None)

# Test 8 — GC keeps recent invalidated
_wipe()
pm.save_plan("30", _body_with_pickup("N9", "B9"))
pm.invalidate_plan("30", "ORDER_DELIVERED_ALL")
removed = pm.gc_invalidated(older_than_hours=24.0)
raw30 = pm.load_plans().get("30")
check("gc_invalidated keeps recent (just-invalidated)",
      removed == 0 and raw30 is not None)

# Test 9 — GC with zero plans → returns 0
_wipe()
removed = pm.gc_invalidated(older_than_hours=24.0)
check("gc_invalidated on empty store returns 0", removed == 0)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19c SUB A: {passed}/{total} PASS")
print("=" * 60)

try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
