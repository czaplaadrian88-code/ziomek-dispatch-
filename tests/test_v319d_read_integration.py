"""V3.19d tests — read integration: simulate_bag_route_v2 base_sequence +
dispatch_pipeline caller hook.

Mock osrm. Standalone script.
"""
import os
import sys
from datetime import datetime, timezone, timedelta

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import osrm_client
from dispatch_v2.route_simulator_v2 import (
    OrderSim, simulate_bag_route_v2, DWELL_DROPOFF_MIN,
)
from dispatch_v2 import common as cm


_OSRM_MIN = {}


def _mock_route(from_ll, to_ll, use_cache=True):
    key = (tuple(from_ll), tuple(to_ll))
    dur = _OSRM_MIN.get(key, 5.0)
    return {"duration_s": dur * 60.0, "distance_m": dur * 1000,
            "duration_min": dur, "osrm_fallback": False}


def _mock_table(origins, destinations):
    out = []
    for o in origins:
        row = []
        for d in destinations:
            dur = _OSRM_MIN.get((tuple(o), tuple(d)), 5.0)
            if tuple(o) == tuple(d):
                dur = 0.0
            row.append({"duration_s": dur * 60.0, "distance_m": dur * 1000,
                        "duration_min": dur, "osrm_fallback": False})
        out.append(row)
    return out


osrm_client.route = _mock_route
osrm_client.table = _mock_table


def _reset():
    _OSRM_MIN.clear()


def _set(m):
    _OSRM_MIN.update(m)


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


def _now():
    return datetime(2026, 4, 20, 14, 0, 0, tzinfo=timezone.utc)


# ============================================================
print("=== V3.19d: simulator base_sequence ===")
# ============================================================

# Test 1 — base_sequence=None → current fresh TSP behavior (strategy != sticky)
_reset()
posA = (53.10, 23.10)
drop_x = (53.15, 23.20)
drop_y = (53.18, 23.22)
drop_new = (53.13, 23.16)
pickup_new = (53.11, 23.11)
_set({
    (posA, drop_x): 6.0, (posA, drop_y): 8.0,
    (drop_x, drop_y): 4.0, (drop_y, drop_x): 4.0,
    (drop_x, pickup_new): 5.0, (drop_y, pickup_new): 6.0,
    (pickup_new, drop_new): 3.0,
    (drop_x, drop_new): 4.0, (drop_y, drop_new): 4.0,
})
bag = [
    OrderSim("X", (0.0, 0.0), drop_x, picked_up_at=_now() - timedelta(minutes=20), status="picked_up"),
    OrderSim("Y", (0.0, 0.0), drop_y, picked_up_at=_now() - timedelta(minutes=25), status="picked_up"),
]
new_order = OrderSim("N", pickup_new, drop_new, status="assigned")

plan_fresh = simulate_bag_route_v2(
    courier_pos=posA, bag=bag, new_order=new_order, now=_now(),
    base_sequence=None,
)
check("base_sequence=None → strategy in {bruteforce,greedy}",
      plan_fresh.strategy in ("bruteforce", "greedy"))

# Test 2 — base_sequence=['X','Y'] → strategy="sticky", dropoff X before Y
plan_sticky = simulate_bag_route_v2(
    courier_pos=posA, bag=bag, new_order=new_order, now=_now(),
    base_sequence=["X", "Y"],
)
check("base_sequence=['X','Y'] → strategy='sticky'",
      plan_sticky.strategy == "sticky")
delivery_order = [oid for oid in plan_sticky.sequence]
x_idx = delivery_order.index("X") if "X" in delivery_order else -1
y_idx = delivery_order.index("Y") if "Y" in delivery_order else -1
check("sticky ['X','Y'] → X delivered before Y",
      x_idx >= 0 and y_idx >= 0 and x_idx < y_idx)

# Test 3 — base_sequence=['Y','X'] → reverse order respected
plan_rev = simulate_bag_route_v2(
    courier_pos=posA, bag=bag, new_order=new_order, now=_now(),
    base_sequence=["Y", "X"],
)
delivery_rev = list(plan_rev.sequence)
y_i = delivery_rev.index("Y") if "Y" in delivery_rev else -1
x_i = delivery_rev.index("X") if "X" in delivery_rev else -1
check("sticky ['Y','X'] → Y delivered before X",
      y_i >= 0 and x_i >= 0 and y_i < x_i)

# Test 4 — mismatch (base oids ≠ bag oids) → fallback to fresh TSP
plan_mismatch = simulate_bag_route_v2(
    courier_pos=posA, bag=bag, new_order=new_order, now=_now(),
    base_sequence=["WRONG1", "WRONG2"],
)
check("mismatch base_sequence → fallback (strategy != sticky)",
      plan_mismatch.strategy != "sticky")

# Test 5 — base_sequence wrong length → fallback
plan_len = simulate_bag_route_v2(
    courier_pos=posA, bag=bag, new_order=new_order, now=_now(),
    base_sequence=["X"],  # too short
)
check("base_sequence length mismatch → fallback",
      plan_len.strategy != "sticky")

# Test 6 — picked_up new_order (no pickup node) + sticky
new_picked = OrderSim("N2", pickup_new, drop_new,
                     picked_up_at=_now() - timedelta(minutes=5),
                     status="picked_up")
_set({
    (drop_x, drop_new): 4.0, (drop_y, drop_new): 4.0,
    (drop_new, drop_x): 4.0, (drop_new, drop_y): 4.0,
})
plan_pu = simulate_bag_route_v2(
    courier_pos=posA, bag=bag, new_order=new_picked, now=_now(),
    base_sequence=["X", "Y"],
)
check("sticky + picked_up new → strategy='sticky'",
      plan_pu.strategy == "sticky")
check("sticky + picked_up → 3 dropoffs w sequence",
      len(plan_pu.sequence) == 3)

# Test 7 — empty bag + base_sequence=[] → current solo path
plan_solo = simulate_bag_route_v2(
    courier_pos=posA, bag=[], new_order=new_order, now=_now(),
    base_sequence=[],
)
check("empty bag + base_sequence=[] → strategy NOT sticky",
      plan_solo.strategy != "sticky")

# Test 8 — sticky lock_first constraint: pickup N nie może być position 0
# (bag order A wiezione, nie zawracaj do nowej restauracji z jedzeniem w bagu)
# plan_sticky already tested — verify delivery order: X or Y first (NOT N pickup/drop)
first_seq_oid = plan_sticky.sequence[0]
check("sticky lock_first: first dropoff to bag item (NOT new_order)",
      first_seq_oid in ("X", "Y"))

# Test 9 — regression V3.19a: floor still applied for picked_up bag items
# when sticky used
gab_pickup = (53.134, 23.163)
gab_drop = (53.148, 22.985)
_OSRM_MIN[(gab_pickup, gab_drop)] = 17.0
gab_order = OrderSim(
    "GAB", gab_pickup, gab_drop,
    picked_up_at=_now() - timedelta(minutes=9),  # picked_up 13:51 if now=14:00
    status="picked_up",
)
new_after = OrderSim("NA", pickup_new, drop_new, status="assigned")
_set({
    (gab_drop, pickup_new): 6.0,
    (pickup_new, drop_new): 3.0,
    (gab_drop, drop_new): 5.0,
})
plan_gab = simulate_bag_route_v2(
    courier_pos=gab_drop,  # synthetic pos = drop
    bag=[gab_order],
    new_order=new_after,
    now=_now(),
    base_sequence=["GAB"],
)
gab_pred = plan_gab.predicted_delivered_at.get("GAB")
# Floor: picked_up(13:51) + 17min drive + 1min DWELL = 14:09.
expected_floor = _now() - timedelta(minutes=9) + timedelta(minutes=17) + timedelta(minutes=DWELL_DROPOFF_MIN)
check("V3.19a floor still applied in sticky path",
      gab_pred is not None and gab_pred >= expected_floor)

# Test 10 — regression: plan.predicted_delivered_at has entries for all bag + new
check("sticky plan has predicted_delivered_at for all 3 orders (X,Y,N)",
      all(oid in plan_sticky.predicted_delivered_at for oid in ("X", "Y", "N")))

# ============================================================
print("=== V3.19d: feasibility_v2 passthrough ===")
# ============================================================

from dispatch_v2 import feasibility_v2

# Test 11 — check_feasibility_v2 signature includes base_sequence
import inspect
sig = inspect.signature(feasibility_v2.check_feasibility_v2)
check("feasibility_v2.check_feasibility_v2 has base_sequence kwarg",
      "base_sequence" in sig.parameters)

# Test 12 — passthrough: base_sequence propagated do simulator
_reset()
_set({
    (posA, drop_x): 6.0, (posA, drop_y): 8.0,
    (drop_x, drop_y): 4.0, (drop_y, drop_x): 4.0,
    (drop_x, pickup_new): 5.0, (drop_y, pickup_new): 6.0,
    (pickup_new, drop_new): 3.0,
    (drop_x, drop_new): 4.0, (drop_y, drop_new): 4.0,
})
bag_for_f = [
    OrderSim("X", (0.0, 0.0), drop_x, picked_up_at=_now() - timedelta(minutes=20), status="picked_up"),
    OrderSim("Y", (0.0, 0.0), drop_y, picked_up_at=_now() - timedelta(minutes=25), status="picked_up"),
]
_, _, _, plan_f = feasibility_v2.check_feasibility_v2(
    courier_pos=posA, bag=bag_for_f, new_order=new_order, now=_now(),
    base_sequence=["Y", "X"],
)
if plan_f is not None:
    y_f = plan_f.sequence.index("Y") if "Y" in plan_f.sequence else -1
    x_f = plan_f.sequence.index("X") if "X" in plan_f.sequence else -1
    check("feasibility_v2 base_sequence=[Y,X] → plan respects order",
          y_f >= 0 and x_f >= 0 and y_f < x_f and plan_f.strategy == "sticky")
else:
    check("feasibility_v2 base_sequence passthrough — plan not None", False)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19d READ INTEGRATION: {passed}/{total} PASS")
print("=" * 60)

if failed:
    sys.exit(1)
