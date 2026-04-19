"""V3.19c sub B tests — log_read_shadow_diff (read integration shadow log)."""
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

_TMPDIR = Path(tempfile.mkdtemp(prefix="v319c_subb_"))
from dispatch_v2 import plan_manager as pm
from dispatch_v2 import common as cm

pm.PLANS_FILE = _TMPDIR / "courier_plans.json"
pm.LOCK_FILE = _TMPDIR / "courier_plans.lock"
pm.SHADOW_LOG_PATH = _TMPDIR / "v319c_read_shadow_log.jsonl"

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
    for p in (pm.PLANS_FILE, pm.LOCK_FILE, pm.SHADOW_LOG_PATH):
        if p.exists():
            p.unlink()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _read_shadow_entries():
    if not pm.SHADOW_LOG_PATH.exists():
        return []
    with open(pm.SHADOW_LOG_PATH) as f:
        return [json.loads(line) for line in f if line.strip()]


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


# ============================================================
print("=== V3.19c sub B: log_read_shadow_diff ===")
# ============================================================

# Test 1 — no saved plan → has_saved_plan=False, match with empty
_wipe()
pm.log_read_shadow_diff("100", ["A", "B"], {"A", "B"})
entries = _read_shadow_entries()
check("no saved → entry logged", len(entries) == 1)
check("no saved → has_saved_plan=False",
      entries[0]["has_saved_plan"] is False)
check("no saved → saved_bag_sequence=[]", entries[0]["saved_bag_sequence"] == [])

# Test 2 — saved plan matches fresh
_wipe()
pm.save_plan("101", _body(["A", "B"]))
pm.log_read_shadow_diff("101", ["A", "B"], {"A", "B"})
entries = _read_shadow_entries()
check("matches fresh → match=True", entries[0]["match"] is True)
check("matches → saved_plan_version=1",
      entries[0]["saved_plan_version"] == 1)

# Test 3 — saved plan differs from fresh → match=False
_wipe()
pm.save_plan("102", _body(["A", "B"]))
pm.log_read_shadow_diff("102", ["B", "A"], {"A", "B"})
entries = _read_shadow_entries()
check("differs → match=False", entries[0]["match"] is False)
check("differs logs both sequences",
      entries[0]["saved_bag_sequence"] == ["A", "B"]
      and entries[0]["fresh_bag_sequence"] == ["B", "A"])

# Test 4 — flag OFF → no write
_wipe()
pm.save_plan("103", _body(["X"]))
cm.ENABLE_SAVED_PLANS_READ_SHADOW = False
pm.log_read_shadow_diff("103", ["X"], {"X"})
cm.ENABLE_SAVED_PLANS_READ_SHADOW = True
check("flag OFF → no shadow log file entries",
      not pm.SHADOW_LOG_PATH.exists()
      or len(_read_shadow_entries()) == 0)

# Test 5 — empty active_bag_oids → skip (nothing to compare)
_wipe()
pm.save_plan("104", _body(["A"]))
pm.log_read_shadow_diff("104", ["A"], set())
check("empty active_bag → no write",
      not pm.SHADOW_LOG_PATH.exists())

# Test 6 — saved plan invalidated → has_saved_plan=False
_wipe()
pm.save_plan("105", _body(["A"]))
pm.invalidate_plan("105", "MANUAL")
pm.log_read_shadow_diff("105", ["A"], {"A"})
entries = _read_shadow_entries()
check("invalidated saved → has_saved_plan=False",
      entries[0]["has_saved_plan"] is False)

# Test 7 — fresh sequence includes new_order (not in bag) — bag orders only compared
_wipe()
pm.save_plan("106", _body(["A", "B"]))
# Fresh sequence has new order Z inserted between bag items
pm.log_read_shadow_diff("106", ["A", "Z", "B"], {"A", "B"},
                        extra={"new_order_id": "Z"})
entries = _read_shadow_entries()
check("Z filtered out — bag-order A,B matches",
      entries[0]["fresh_bag_sequence"] == ["A", "B"]
      and entries[0]["match"] is True)
check("extra field propagated",
      entries[0].get("extra", {}).get("new_order_id") == "Z")

# Test 8 — multiple entries append correctly
_wipe()
pm.save_plan("107", _body(["A"]))
for i in range(3):
    pm.log_read_shadow_diff("107", ["A"], {"A"})
entries = _read_shadow_entries()
check("3 log calls → 3 entries", len(entries) == 3)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19c SUB B: {passed}/{total} PASS")
print("=" * 60)

try:
    for p in _TMPDIR.iterdir():
        p.unlink()
    _TMPDIR.rmdir()
except Exception:
    pass

if failed:
    sys.exit(1)
