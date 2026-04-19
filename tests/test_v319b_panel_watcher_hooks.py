"""V3.19b panel_watcher hook tests — save_plan / advance_plan / remove_stops.

Redirects plan_manager storage + pending_proposals to tmp dir. Patches helpers
in panel_watcher module so they hit the tmp store. Runs as standalone Python.
"""
import os
import sys
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

_TMPDIR = Path(tempfile.mkdtemp(prefix="v319b_pw_"))
from dispatch_v2 import plan_manager as pm
from dispatch_v2 import panel_watcher as pw

pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"

# Point panel_watcher helpers at tmp pending_proposals file.
pw._PENDING_PROPOSALS_PATH = str(_TMPDIR / "pending_proposals.json")

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
    for p in (pm.PLANS_FILE, pm.LOCK_FILE, Path(pw._PENDING_PROPOSALS_PATH)):
        if p.exists():
            p.unlink()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _write_pending(order_id: str, decision_record: dict):
    with open(pw._PENDING_PROPOSALS_PATH, "w", encoding="utf-8") as f:
        json.dump({str(order_id): {"ts": _now_iso(),
                                    "decision_record": decision_record}}, f)


def _sample_dr(cid: str, sequence, predicted, pickup_at=None, bag_context=None):
    return {
        "ts": _now_iso(),
        "best": {
            "courier_id": cid,
            "pos_source": "last_picked_up_delivery",
            "plan": {
                "sequence": sequence,
                "predicted_delivered_at": predicted,
                "pickup_at": pickup_at or {},
                "strategy": "bruteforce",
            },
            "bag_context": bag_context or [],
        },
    }


# ============================================================
print("=== V3.19b panel_watcher hooks ===")
# ============================================================

# Test 1 — _save_plan_on_assign writes plan when pending + cid match
_wipe()
_write_pending("O1", _sample_dr(
    cid="503",
    sequence=["BAG1", "O1"],
    predicted={"BAG1": "2026-04-19T20:30:00+00:00",
               "O1": "2026-04-19T20:50:00+00:00"},
    pickup_at={"O1": "2026-04-19T20:45:00+00:00"},
    bag_context=[{"order_id": "BAG1"}],
))
pw._save_plan_on_assign("O1", "503")
plan = pm.load_plan("503")
check("save_plan_on_assign: plan exists after hook", plan is not None)
check("save_plan_on_assign: stops include pickup for new order",
      plan is not None and any(s["type"] == "pickup" and s["order_id"] == "O1"
                               for s in plan["stops"]))
check("save_plan_on_assign: BAG1 marked picked_up",
      plan is not None and any(s["order_id"] == "BAG1"
                               and s["status_at_plan_time"] == "picked_up"
                               for s in plan["stops"]))

# Test 2 — PANEL_OVERRIDE: best.cid='503', assigned='999' → skip save
_wipe()
_write_pending("O2", _sample_dr(
    cid="503", sequence=["O2"],
    predicted={"O2": "2026-04-19T20:45:00+00:00"}))
pw._save_plan_on_assign("O2", "999")
check("save_plan_on_assign: PANEL_OVERRIDE → no plan for 999",
      pm.load_plan("999") is None)

# Test 3 — no pending → silent skip
_wipe()
pw._save_plan_on_assign("GHOST", "503")
check("save_plan_on_assign: no pending → silent skip",
      pm.load_plan("503") is None)

# Test 4 — flag OFF → no-op
from dispatch_v2 import common as cm
_wipe()
_write_pending("O3", _sample_dr(
    cid="503", sequence=["O3"],
    predicted={"O3": "2026-04-19T20:45:00+00:00"}))
cm.ENABLE_SAVED_PLANS = False
pw._save_plan_on_assign("O3", "503")
check("save_plan_on_assign: flag OFF → no plan written",
      pm.load_plan("503") is None)
cm.ENABLE_SAVED_PLANS = True

# Test 5 — _advance_plan_on_deliver removes stops for delivered order
_wipe()
# Seed a plan with 2 orders
body = {
    "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                  "source_ts": _now_iso()},
    "start_ts": _now_iso(),
    "stops": [
        {"order_id": "A", "type": "dropoff",
         "coords": {"lat": 53.15, "lng": 23.20}, "dwell_min": 1.0,
         "status_at_plan_time": "picked_up"},
        {"order_id": "B", "type": "dropoff",
         "coords": {"lat": 53.18, "lng": 23.22}, "dwell_min": 1.0,
         "status_at_plan_time": "picked_up"},
    ],
    "optimization_method": "bruteforce",
}
pm.save_plan("77", body)
pw._advance_plan_on_deliver("77", "A", "2026-04-19T20:30:00+00:00",
                            [53.15, 23.20])
after = pm.load_plan("77")
remaining_oids = [s["order_id"] for s in after["stops"]]
check("advance_plan_on_deliver: A removed after delivery",
      "A" not in remaining_oids)
check("advance_plan_on_deliver: start_pos source=last_delivered",
      after["start_pos"]["source"] == "last_delivered")

# Test 6 — _advance_plan_on_deliver flag OFF → no-op
_wipe()
pm.save_plan("88", body)
cm.ENABLE_SAVED_PLANS = False
pw._advance_plan_on_deliver("88", "A", _now_iso(), None)
cm.ENABLE_SAVED_PLANS = True
after = pm.load_plan("88")
oids = [s["order_id"] for s in after["stops"]]
check("advance_plan_on_deliver: flag OFF → stops intact",
      "A" in oids and "B" in oids)

# Test 7 — _remove_stops_on_return purges all stops for cancelled order
_wipe()
body2 = {
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
        {"order_id": "C", "type": "dropoff",
         "coords": {"lat": 53.20, "lng": 23.25}, "dwell_min": 1.0,
         "status_at_plan_time": "picked_up"},
    ],
    "optimization_method": "bruteforce",
}
pm.save_plan("99", body2)
pw._remove_stops_on_return("99", "A")
after = pm.load_plan("99")
a_stops = [s for s in after["stops"] if s["order_id"] == "A"]
check("remove_stops_on_return: both pickup+dropoff for A removed",
      len(a_stops) == 0)
check("remove_stops_on_return: C retained",
      any(s["order_id"] == "C" for s in after["stops"]))

# Test 8 — empty courier_id (koordynator holding bucket) → no-op
_wipe()
pm.save_plan("55", body)
pw._advance_plan_on_deliver("", "A", _now_iso(), None)
after = pm.load_plan("55")
check("advance_plan_on_deliver: empty cid → no crash + plan intact",
      after is not None and len(after["stops"]) == 2)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19b PANEL_WATCHER HOOKS: {passed}/{total} PASS")
print("=" * 60)

# cleanup
try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
