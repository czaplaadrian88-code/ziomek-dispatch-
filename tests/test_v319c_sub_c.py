"""V3.19c sub C tests — plan_recheck consistency checker."""
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

_TMPDIR = Path(tempfile.mkdtemp(prefix="v319c_subc_"))
from dispatch_v2 import plan_manager as pm
from dispatch_v2 import plan_recheck as pr

pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"
pr.RECHECK_LOG_PATH = _TMPDIR / "plan_recheck_log.jsonl"
pr.ORDERS_STATE_PATH = _TMPDIR / "orders_state.json"

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
              pr.RECHECK_LOG_PATH, pr.ORDERS_STATE_PATH):
        if p.exists():
            p.unlink()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _write_orders_state(orders: dict):
    with open(pr.ORDERS_STATE_PATH, "w") as f:
        json.dump(orders, f)


def _body(stops_seq):
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": "gps",
                      "source_ts": _now_iso()},
        "start_ts": _now_iso(),
        "stops": [
            {"order_id": oid, "type": "dropoff",
             "coords": {"lat": 53.15, "lng": 23.20}, "dwell_min": 1.0,
             "status_at_plan_time": "picked_up"}
            for oid in stops_seq
        ],
        "optimization_method": "bruteforce",
    }


def _read_recheck_log():
    if not pr.RECHECK_LOG_PATH.exists():
        return []
    with open(pr.RECHECK_LOG_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


# ============================================================
print("=== V3.19c sub C: plan_recheck ===")
# ============================================================

# Test 1 — empty store, returns clean summary
_wipe()
summary = pr.run_recheck()
check("empty store: total=0 healthy=0", summary["total_plans"] == 0
      and summary["healthy"] == 0)

# Test 2 — all-active plan, orders_state matches → healthy
_wipe()
_write_orders_state({
    "O1": {"status": "picked_up", "courier_id": "100"},
    "O2": {"status": "picked_up", "courier_id": "100"},
})
pm.save_plan("100", _body(["O1", "O2"]))
summary = pr.run_recheck()
check("healthy plan: active=1 healthy=1",
      summary["active_plans"] == 1 and summary["healthy"] == 1)

# Test 3 — plan references delivered order → issue flagged
_wipe()
_write_orders_state({
    "O3": {"status": "delivered", "courier_id": "101"},
    "O4": {"status": "picked_up", "courier_id": "101"},
})
pm.save_plan("101", _body(["O3", "O4"]))
pr.AUTO_INVALIDATE_STALE = False
summary = pr.run_recheck()
check("delivered order in plan → with_issues",
      summary["with_issues"] == 1 and summary["auto_invalidated"] == 0)
entries = _read_recheck_log()
check("recheck log has terminal_status finding",
      len(entries) == 1 and any("terminal_status" in i for i in entries[0]["issues"]))

# Test 4 — auto-invalidate when flag on + all terminal
_wipe()
_write_orders_state({
    "O5": {"status": "delivered", "courier_id": "102"},
})
pm.save_plan("102", _body(["O5"]))
pr.AUTO_INVALIDATE_STALE = True
summary = pr.run_recheck()
check("auto_invalidate ON + all delivered → invalidated",
      summary["auto_invalidated"] == 1)
pr.AUTO_INVALIDATE_STALE = False

# Test 5 — missing order in orders_state → flagged
_wipe()
_write_orders_state({})
pm.save_plan("103", _body(["GHOST_ORDER"]))
summary = pr.run_recheck()
check("missing order → flagged",
      summary["with_issues"] == 1)
entries = _read_recheck_log()
check("recheck log has missing_in_orders_state",
      len(entries) == 1 and "GHOST_ORDER" in entries[0]["missing_orders"])

# Test 6 — invalidated plan NOT checked
_wipe()
pm.save_plan("104", _body(["X"]))
pm.invalidate_plan("104", "MANUAL")
_write_orders_state({"X": {"status": "picked_up", "courier_id": "104"}})
summary = pr.run_recheck()
check("invalidated plan skipped (active_plans=0)",
      summary["active_plans"] == 0 and summary["total_plans"] == 1)

# Test 7 — stale age flagged
_wipe()
pm.save_plan("105", _body(["A"]))
# Backdate last_modified_at 3h ago
with pm._locked(exclusive=True):
    plans = pm._read_raw()
    plans["105"]["last_modified_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=3)
    ).isoformat()
    pm._write_raw(plans)
_write_orders_state({"A": {"status": "picked_up", "courier_id": "105"}})
summary = pr.run_recheck()
check("stale age flagged as issue",
      summary["with_issues"] == 1)
entries = _read_recheck_log()
check("recheck log has stale_age issue",
      len(entries) == 1 and any("stale_age" in i for i in entries[0]["issues"]))

# Test 8 — mixed healthy + issues
_wipe()
_write_orders_state({
    "G1": {"status": "picked_up", "courier_id": "200"},
    "G2": {"status": "delivered", "courier_id": "201"},
})
pm.save_plan("200", _body(["G1"]))
pm.save_plan("201", _body(["G2"]))
summary = pr.run_recheck()
check("mixed: active=2 healthy=1 issues=1",
      summary["active_plans"] == 2 and summary["healthy"] == 1
      and summary["with_issues"] == 1)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19c SUB C: {passed}/{total} PASS")
print("=" * 60)

try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
