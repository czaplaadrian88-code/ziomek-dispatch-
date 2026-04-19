"""V3.19c sub D tests — GPS drift detection + invalidation."""
import os
import sys
import json
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_TMPDIR = Path(tempfile.mkdtemp(prefix="v319c_subd_"))
from dispatch_v2 import plan_manager as pm
from dispatch_v2 import plan_recheck as pr

pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"
pr.RECHECK_LOG_PATH = _TMPDIR / "plan_recheck_log.jsonl"
pr.ORDERS_STATE_PATH = _TMPDIR / "orders_state.json"
pr.GPS_PWA_PATH = _TMPDIR / "gps_positions_pwa.json"

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
    for p in (pm.PLANS_FILE, pm.LOCK_FILE,
              pr.RECHECK_LOG_PATH, pr.ORDERS_STATE_PATH, pr.GPS_PWA_PATH):
        if p.exists():
            p.unlink()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _plan_body_with_start(lat, lng, stops):
    return {
        "start_pos": {"lat": lat, "lng": lng, "source": "gps",
                      "source_ts": _now_iso()},
        "start_ts": _now_iso(),
        "stops": [
            {"order_id": oid, "type": "dropoff",
             "coords": {"lat": lat + 0.01, "lng": lng + 0.01},
             "dwell_min": 1.0, "status_at_plan_time": "picked_up"}
            for oid in stops
        ],
        "optimization_method": "bruteforce",
    }


def _write_gps(cid, lat, lon, age_min=1.0):
    ts = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).isoformat()
    data = {}
    if pr.GPS_PWA_PATH.exists():
        data = json.loads(pr.GPS_PWA_PATH.read_text())
    data[cid] = {"lat": lat, "lon": lon, "timestamp": ts}
    pr.GPS_PWA_PATH.write_text(json.dumps(data))


def _write_orders(oids):
    with open(pr.ORDERS_STATE_PATH, "w") as f:
        json.dump({oid: {"status": "picked_up", "courier_id": "1"} for oid in oids}, f)


# ============================================================
print("=== V3.19c sub D: GPS drift detection ===")
# ============================================================

# Test 1 — no GPS → no drift finding
_wipe()
pm.save_plan("200", _plan_body_with_start(53.13, 23.15, ["O1"]))
_write_orders(["O1"])
summary = pr.run_recheck()
check("no GPS positions file → healthy", summary["healthy"] == 1)
check("gps_drift_detected=0", summary["gps_drift_detected"] == 0)

# Test 2 — GPS within threshold (< 500m) → no drift
_wipe()
pm.save_plan("201", _plan_body_with_start(53.13, 23.15, ["O2"]))
_write_orders(["O2"])
_write_gps("201", 53.1305, 23.1503)  # ~50m away
summary = pr.run_recheck()
check("GPS within threshold → healthy", summary["healthy"] == 1
      and summary["gps_drift_detected"] == 0)

# Test 3 — GPS > 500m → drift detected
_wipe()
pm.save_plan("202", _plan_body_with_start(53.13, 23.15, ["O3"]))
_write_orders(["O3"])
_write_gps("202", 53.18, 23.22)  # ~7km away
summary = pr.run_recheck()
check("GPS drift > 500m → detected",
      summary["gps_drift_detected"] == 1)
check("with_issues=1", summary["with_issues"] == 1)
check("auto_invalidate OFF → gps_drift_invalidated=0",
      summary["gps_drift_invalidated"] == 0)

# Test 4 — stale GPS (age > 5min) → ignored for drift
_wipe()
pm.save_plan("203", _plan_body_with_start(53.13, 23.15, ["O4"]))
_write_orders(["O4"])
_write_gps("203", 53.18, 23.22, age_min=30.0)
summary = pr.run_recheck()
check("stale GPS (30min age) → no drift detected",
      summary["gps_drift_detected"] == 0)

# Test 5 — drift + ENABLE_GPS_DRIFT_INVALIDATION → auto-invalidate
_wipe()
pm.save_plan("204", _plan_body_with_start(53.13, 23.15, ["O5"]))
_write_orders(["O5"])
_write_gps("204", 53.18, 23.22)
pr.ENABLE_GPS_DRIFT_INVALIDATION = True
summary = pr.run_recheck()
check("flag ON + drift → auto invalidated",
      summary["gps_drift_invalidated"] == 1)
raw = pm.load_plans().get("204")
check("plan marked invalidated with reason=GPS_DRIFT",
      raw and raw.get("invalidation_reason") == "GPS_DRIFT")
pr.ENABLE_GPS_DRIFT_INVALIDATION = False

# Test 6 — placeholder start_pos (0,0) → skip drift check
_wipe()
pm.save_plan("205", _plan_body_with_start(0.0, 0.0, ["O6"]))
_write_orders(["O6"])
_write_gps("205", 53.13, 23.15)
summary = pr.run_recheck()
check("start_pos (0,0) placeholder → no drift check",
      summary["gps_drift_detected"] == 0)

# Test 7 — haversine sanity
d_m = pr._haversine_m((53.13, 23.15), (53.18, 23.22))
check("haversine Białystok center → outskirts ~7km",
      6000 < d_m < 8000)
d_small = pr._haversine_m((53.13, 23.15), (53.131, 23.151))
check("haversine ~100m small delta",
      50 < d_small < 150)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19c SUB D: {passed}/{total} PASS")
print("=" * 60)

try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
