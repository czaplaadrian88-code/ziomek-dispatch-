"""BUG-1 (2026-06-05) — invalidate_plan_on_bag_change + _save_plan_on_assign_signal.

Symptom: reassign/PANEL_OVERRIDE dorzuca order do worka, ale _save_plan_on_assign
cicho pomija zapis (proposed_cid != courier_id) → plan_version stoi → apka nie
odświeża worka aż do 5-min plan_recheck. Fix: gdy order NIE jest pokryty zapisanym
planem kuriera, unieważnij plan (invalidated_at bump) → SSE PLAN_UPDATED →
natychmiastowy pełny GET.

Redirects plan_manager storage + pending_proposals do tmp. Steruje flagą przez
podmianę common.flag. Runs as standalone Python script.
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

_TMPDIR = Path(tempfile.mkdtemp(prefix="bug1_inv_"))
from dispatch_v2 import plan_manager as pm
from dispatch_v2 import panel_watcher as pw
import dispatch_v2.common as cm

pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"
pw._PENDING_PROPOSALS_PATH = str(_TMPDIR / "pending_proposals.json")

# Deterministyczne sterowanie flagą BUG-1 (niezależne od flags.json).
_FLAG_STATE = {"invalidate": True}
_orig_flag = cm.flag


def _flag_stub(name, default=False):
    if name == "ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE":
        return _FLAG_STATE["invalidate"]
    return _orig_flag(name, default)


cm.flag = _flag_stub

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


def _plan_body(order_ids):
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                      "source_ts": _now_iso()},
        "start_ts": _now_iso(),
        "stops": [
            {"order_id": str(o), "type": "dropoff",
             "coords": {"lat": 53.20, "lng": 23.25}, "dwell_min": 1.0,
             "status_at_plan_time": "assigned"}
            for o in order_ids
        ],
        "optimization_method": "bruteforce",
    }


def _write_pending(order_id, cid, sequence):
    dr = {
        "ts": _now_iso(),
        "best": {
            "courier_id": cid,
            "pos_source": "last_picked_up_delivery",
            "plan_expected_version": 0,
            "plan": {
                "sequence": sequence,
                "predicted_delivered_at": {str(o): _now_iso() for o in sequence},
                "pickup_at": {},
                "strategy": "bruteforce",
            },
            "bag_context": [],
        },
    }
    with open(pw._PENDING_PROPOSALS_PATH, "w", encoding="utf-8") as f:
        json.dump({str(order_id): {"ts": _now_iso(), "decision_record": dr}}, f)


def _raw(cid):
    return pm.load_plans().get(str(cid))


# ============================================================
# Test 1 — order NIE pokryty planem → invalidate fires
_wipe()
pm.save_plan("700", _plan_body(["A", "B"]))
pw._invalidate_plan_on_bag_change("C", "700")
r = _raw("700")
check("not-covered: invalidated_at set", r and r.get("invalidated_at") is not None)
check("not-covered: reason BAG_CHANGED", r and r.get("invalidation_reason") == "BAG_CHANGED")
check("not-covered: load_plan returns None (apka → refresh)", pm.load_plan("700") is None)

# Test 2 — order pokryty planem → no-op (plan zostaje aktywny)
_wipe()
pm.save_plan("701", _plan_body(["A", "B"]))
pw._invalidate_plan_on_bag_change("A", "701")
after = pm.load_plan("701")
check("covered: plan stays active (no-op)",
      after is not None and after.get("invalidated_at") is None)

# Test 3 — brak planu → no-op, brak crasha
_wipe()
pw._invalidate_plan_on_bag_change("X", "702")
check("no-plan: no crash, still None", pm.load_plan("702") is None)

# Test 4 — flaga OFF → no-op mimo niepokrytego ordera
_wipe()
pm.save_plan("703", _plan_body(["A"]))
_FLAG_STATE["invalidate"] = False
pw._invalidate_plan_on_bag_change("Z", "703")
_FLAG_STATE["invalidate"] = True
check("flag off: plan NOT invalidated", pm.load_plan("703") is not None)

# Test 5 — ENABLE_SAVED_PLANS OFF → no-op
_wipe()
pm.save_plan("704", _plan_body(["A"]))
cm.ENABLE_SAVED_PLANS = False
pw._invalidate_plan_on_bag_change("Z", "704")
cm.ENABLE_SAVED_PLANS = True
check("saved-plans off: plan NOT invalidated", pm.load_plan("704") is not None)

# Test 6 — wrapper PANEL_OVERRIDE: propozycja na 800, przypisane do 801 (stary plan
# nie pokrywa nowego ordera) → save pominięty, plan 801 unieważniony, 800 bez planu
_wipe()
pm.save_plan("801", _plan_body(["OLD"]))
_write_pending("NEW1", "800", ["NEW1"])
pw._save_plan_on_assign_signal("NEW1", "801")
r801 = _raw("801")
check("override: B(801) plan invalidated (BAG_CHANGED)",
      r801 and r801.get("invalidated_at") is not None
      and r801.get("invalidation_reason") == "BAG_CHANGED")
check("override: proposed courier 800 got NO plan", _raw("800") is None)

# Test 7 — wrapper happy-path: propozycja na 900, przypisane do 900 → plan zapisany,
# pokrywa order → invalidate no-op (plan aktywny)
_wipe()
_write_pending("NEW2", "900", ["NEW2"])
pw._save_plan_on_assign_signal("NEW2", "900")
after = pm.load_plan("900")
check("happy: plan saved + active", after is not None and after.get("invalidated_at") is None)
check("happy: plan covers NEW2",
      after is not None and any(s["order_id"] == "NEW2" for s in after["stops"]))

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"BUG-1 INVALIDATE_PLAN_ON_BAG_CHANGE: {passed}/{total} PASS")
print("=" * 60)

cm.flag = _orig_flag
try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
