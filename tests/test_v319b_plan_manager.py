"""V3.19b tests — plan_manager core.

Covers: atomic write, fcntl lock, save/load round-trip, version bump, CAS
concurrency, invalidate, advance, remove_stops, insert_stop_optimal, mark_stale,
load_plan active_bag mismatch auto-invalidate.

Runs as standalone Python script. Uses tmp dir for isolation.
"""
import os
import sys
import json
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

# redirect plan_manager storage to a tmp dir BEFORE importing
_TMPDIR = Path(tempfile.mkdtemp(prefix="v319b_test_"))
from dispatch_v2 import plan_manager as pm
pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"

passed = 0
failed = 0


def check(label: str, cond: bool):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label}")


def _wipe():
    if pm.PLANS_FILE.exists():
        pm.PLANS_FILE.unlink()
    if pm.LOCK_FILE.exists():
        pm.LOCK_FILE.unlink()


def _bialystok(t=0.0, l=0.0):
    return (53.13 + t, 23.15 + l)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _sample_plan_body():
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                      "source_ts": _now_iso()},
        "start_ts": _now_iso(),
        "stops": [
            {"order_id": "A", "type": "pickup",
             "coords": {"lat": 53.10, "lng": 23.10}, "dwell_min": 2.0,
             "status_at_plan_time": "assigned"},
            {"order_id": "A", "type": "dropoff",
             "coords": {"lat": 53.15, "lng": 23.20}, "dwell_min": 1.0,
             "status_at_plan_time": "assigned"},
        ],
        "optimization_method": "bruteforce",
    }


# ============================================================
print("=== V3.19b: save/load round-trip ===")
# ============================================================

_wipe()
# Test 1 — load on empty returns None
check("load_plan on empty returns None", pm.load_plan("123") is None)

# Test 2 — save_plan creates entry + version=1
saved = pm.save_plan("123", _sample_plan_body())
check("save_plan returns plan_version=1", saved["plan_version"] == 1)

# Test 3 — load_plan returns same plan
loaded = pm.load_plan("123")
check("load_plan round-trip matches saved stops",
      loaded is not None and len(loaded["stops"]) == 2)

# Test 4 — version bump on save
saved2 = pm.save_plan("123", _sample_plan_body())
check("save_plan version bump 1→2", saved2["plan_version"] == 2)

# Test 5 — CAS fail on wrong expected_version
try:
    pm.save_plan("123", _sample_plan_body(), expected_version=99)
    check("CAS expected_version mismatch raises ConcurrencyError", False)
except pm.ConcurrencyError:
    check("CAS expected_version mismatch raises ConcurrencyError", True)

# Test 6 — CAS succeed on matching version
saved3 = pm.save_plan("123", _sample_plan_body(), expected_version=2)
check("CAS expected_version=2 succeeds to 3", saved3["plan_version"] == 3)

# ============================================================
print("=== V3.19b: invalidate + load guard ===")
# ============================================================

# Test 7 — invalidate_plan sets invalidated_at
pm.invalidate_plan("123", "MANUAL")
raw = pm.load_plans().get("123")
check("invalidate_plan sets invalidated_at + reason",
      raw is not None and raw["invalidated_at"] is not None
      and raw["invalidation_reason"] == "MANUAL")

# Test 8 — load_plan returns None for invalidated
check("load_plan returns None for invalidated plan",
      pm.load_plan("123") is None)

# ============================================================
print("=== V3.19b: active_bag mismatch auto-invalidate ===")
# ============================================================

_wipe()
pm.save_plan("9", _sample_plan_body())
# Plan has dropoff for order "A". active_bag_oids = {"A"} → OK.
ok_load = pm.load_plan("9", active_bag_oids={"A"})
check("active_bag matches plan → loaded", ok_load is not None)
# active_bag_oids = {"B"} → mismatch → invalidate.
bad_load = pm.load_plan("9", active_bag_oids={"B"})
check("active_bag mismatch → auto-invalidate + None", bad_load is None)
# Verify invalidated on disk
raw9 = pm.load_plans().get("9")
check("auto-invalidate persisted on disk",
      raw9 is not None and raw9["invalidated_at"] is not None)

# ============================================================
print("=== V3.19b: advance_plan ===")
# ============================================================

_wipe()
# plan has 2 orders: A + B, each with dropoff
body = _sample_plan_body()
body["stops"].append({
    "order_id": "B", "type": "dropoff",
    "coords": {"lat": 53.18, "lng": 23.22}, "dwell_min": 1.0,
    "status_at_plan_time": "picked_up",
})
pm.save_plan("7", body)

# advance order A delivered → remove dropoff(A), start_pos updated
pm.advance_plan("7", "A", _now_iso(), delivery_coords=(53.15, 23.20))
after = pm.load_plan("7")
drop_oids = [s["order_id"] for s in after["stops"] if s["type"] == "dropoff"]
check("advance_plan removed A dropoff", "A" not in drop_oids)
check("advance_plan kept B dropoff", "B" in drop_oids)
check("advance_plan updated start_pos source=last_delivered",
      after["start_pos"]["source"] == "last_delivered")

# advance order B → plan empty → invalidate
pm.advance_plan("7", "B", _now_iso(), delivery_coords=(53.18, 23.22))
after_all = pm.load_plans().get("7")
check("advance last stop → invalidate ORDER_DELIVERED_ALL",
      after_all["invalidated_at"] is not None
      and after_all["invalidation_reason"] == "ORDER_DELIVERED_ALL")

# ============================================================
print("=== V3.19b: remove_stops ===")
# ============================================================

_wipe()
body = _sample_plan_body()  # has pickup+dropoff for "A"
body["stops"].append({
    "order_id": "B", "type": "dropoff",
    "coords": {"lat": 53.18, "lng": 23.22}, "dwell_min": 1.0,
    "status_at_plan_time": "picked_up",
})
pm.save_plan("5", body)
pm.remove_stops("5", "A")
after = pm.load_plan("5")
a_stops = [s for s in after["stops"] if s["order_id"] == "A"]
check("remove_stops purges both pickup+dropoff for A", len(a_stops) == 0)
check("remove_stops keeps B", any(s["order_id"] == "B" for s in after["stops"]))

# ============================================================
print("=== V3.19b: insert_stop_optimal ===")
# ============================================================


def _fake_leg(a, b):
    # Manhattan-like deterministic cost
    return abs(a[0] - b[0]) * 50.0 + abs(a[1] - b[1]) * 50.0


existing_plan = {
    "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                  "source_ts": _now_iso()},
    "start_ts": _now_iso(),
    "stops": [
        {"order_id": "X", "type": "dropoff",
         "coords": {"lat": 53.15, "lng": 23.20}, "dwell_min": 1.0,
         "status_at_plan_time": "picked_up"},
    ],
    "optimization_method": "bruteforce",
}
new_stops = [
    {"order_id": "Y", "type": "pickup",
     "coords": {"lat": 53.10, "lng": 23.10}, "dwell_min": 2.0,
     "status_at_plan_time": "assigned"},
    {"order_id": "Y", "type": "dropoff",
     "coords": {"lat": 53.14, "lng": 23.18}, "dwell_min": 1.0,
     "status_at_plan_time": "assigned"},
]
updated = pm.insert_stop_optimal(
    existing_plan, new_stops,
    datetime.now(timezone.utc), _fake_leg,
)
check("insert_stop_optimal returns dict with stops",
      "stops" in updated and len(updated["stops"]) == 3)
check("insert_stop_optimal method=incremental",
      updated["optimization_method"] == "incremental")
# pickup for Y must precede dropoff for Y
ypu_idx = next(i for i, s in enumerate(updated["stops"])
               if s["order_id"] == "Y" and s["type"] == "pickup")
ydr_idx = next(i for i, s in enumerate(updated["stops"])
               if s["order_id"] == "Y" and s["type"] == "dropoff")
check("insert_stop_optimal pickup precedes dropoff for new order",
      ypu_idx < ydr_idx)

# Dropoff-only path (order already picked_up externally)
new_stops_only_drop = [
    {"order_id": "Z", "type": "dropoff",
     "coords": {"lat": 53.18, "lng": 23.22}, "dwell_min": 1.0,
     "status_at_plan_time": "picked_up"},
]
u2 = pm.insert_stop_optimal(
    existing_plan, new_stops_only_drop,
    datetime.now(timezone.utc), _fake_leg,
)
check("insert_stop_optimal dropoff-only path",
      len([s for s in u2["stops"] if s["order_id"] == "Z"]) == 1)

# ============================================================
print("=== V3.19b: concurrent writers stress ===")
# ============================================================

_wipe()
pm.save_plan("1", _sample_plan_body())

# Fork two Python subprocesses doing 50 CAS-free save_plan each.
worker_code = f"""
import sys, os
sys.path.insert(0, {repr(_SCRIPTS)})
from dispatch_v2 import plan_manager as pm
from datetime import datetime, timezone
pm.PLANS_FILE = __import__("pathlib").Path({repr(str(pm.PLANS_FILE))})
pm.LOCK_FILE = __import__("pathlib").Path({repr(str(pm.LOCK_FILE))})
BODY = {{
    "start_pos": {{"lat": 53.13, "lng": 23.15, "source": "gps",
                   "source_ts": datetime.now(timezone.utc).isoformat()}},
    "start_ts": datetime.now(timezone.utc).isoformat(),
    "stops": [{{"order_id": "A", "type": "dropoff",
                "coords": {{"lat": 53.15, "lng": 23.20}}, "dwell_min": 1.0,
                "status_at_plan_time": "assigned"}}],
    "optimization_method": "bruteforce",
}}
for _ in range(50):
    pm.save_plan("1", BODY)
"""
procs = [
    subprocess.Popen([sys.executable, "-c", worker_code]),
    subprocess.Popen([sys.executable, "-c", worker_code]),
]
for p in procs:
    p.wait(timeout=30)
final = pm.load_plan("1")
# version bumps monotonically; after 1 + 2*50 = 101 saves expected.
check("concurrent save: plan_version == 101 (1 initial + 2*50)",
      final is not None and final["plan_version"] == 101)
# JSON still valid:
check("concurrent save: file parses as valid JSON",
      isinstance(pm.load_plans(), dict))

# ============================================================
print("=== V3.19b: atomic-write crash safety ===")
# ============================================================

_wipe()
pm.save_plan("2", _sample_plan_body())
# Simulate "mid-write" by writing a huge temp file then not replacing.
# _atomic_write should always os.replace AFTER fsync — if we peek PLANS_FILE
# between saves, it must be a fully parseable JSON. Do 10 saves and verify
# after each that file parses and has plan_version >= previous.
prev_version = 1
for i in range(10):
    pm.save_plan("2", _sample_plan_body())
    # file must always parse
    with open(pm.PLANS_FILE) as fh:
        d = json.load(fh)
    assert isinstance(d, dict)
    new_v = d["2"]["plan_version"]
    assert new_v > prev_version
    prev_version = new_v
check("10 consecutive saves all produce valid JSON + monotonic version",
      prev_version == 11)

# ============================================================
print("=== V3.19b: mark_stale (GPS drift) ===")
# ============================================================

_wipe()
pm.save_plan("42", _sample_plan_body())
pm.mark_stale("42", "GPS_DRIFT")
raw42 = pm.load_plans().get("42")
check("mark_stale sets invalidation_reason=GPS_DRIFT",
      raw42["invalidation_reason"] == "GPS_DRIFT"
      and raw42["invalidated_at"] is not None)
check("load_plan after mark_stale returns None",
      pm.load_plan("42") is None)

# ============================================================
print("=== V3.19b: validation ===")
# ============================================================

try:
    pm.save_plan("bad", {"stops": []})
    check("save_plan validates missing start_pos", False)
except ValueError:
    check("save_plan validates missing start_pos", True)

try:
    pm.save_plan("bad2", {
        "start_pos": {"lat": 53.1, "lng": 23.1, "source": "gps"},
        "start_ts": _now_iso(),
        "stops": [{"order_id": "Q", "type": "banana",
                   "coords": {"lat": 53.1, "lng": 23.1}}],
        "optimization_method": "bruteforce",
    })
    check("save_plan validates stop type enum", False)
except ValueError:
    check("save_plan validates stop type enum", True)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19b PLAN_MANAGER: {passed}/{total} PASS")
print("=" * 60)

# cleanup tmpdir
try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
