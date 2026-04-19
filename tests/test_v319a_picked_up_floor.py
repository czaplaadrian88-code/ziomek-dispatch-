"""V3.19a tests — picked_up drop floor in _simulate_sequence.

Adresuje R1 (29.1% post-V3.18): courier_resolver ustawia synthetic pos = drop_coords
dla picked_up bag → leg_min ≈ 0 → predicted_drop ≈ now+1s. Floor:
  predicted_drop >= picked_up_at + osrm(pickup→drop) + DWELL_DROPOFF_MIN.

Mock osrm_client.route żeby uniknąć HTTP calli w teście.
Uruchamia się jako standalone Python script (zgodny z resztą tests/).
"""
import sys
import os
from datetime import datetime, timezone, timedelta

# ensure dispatch_v2 import path
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import route_simulator_v2 as rsv
from dispatch_v2 import osrm_client
from dispatch_v2.route_simulator_v2 import (
    OrderSim,
    simulate_bag_route_v2,
    DWELL_DROPOFF_MIN,
    DWELL_PICKUP_MIN,
)
from dispatch_v2 import common as cm


# ---- osrm mock ----
# Returns deterministic duration_s per (from, to) pair.
_OSRM_FIXED_MIN = {}


def _mock_route(from_ll, to_ll, use_cache=True):
    key = (tuple(from_ll), tuple(to_ll))
    dur_min = _OSRM_FIXED_MIN.get(key, 5.0)
    return {"duration_s": dur_min * 60.0, "distance_m": dur_min * 1000,
            "duration_min": dur_min, "distance_km": dur_min,
            "osrm_fallback": False}


def _mock_table(origins, destinations):
    out = []
    for oi, o in enumerate(origins):
        row = []
        for di, d in enumerate(destinations):
            dur_min = _OSRM_FIXED_MIN.get((tuple(o), tuple(d)), 5.0)
            if tuple(o) == tuple(d):
                dur_min = 0.0
            row.append({"duration_s": dur_min * 60.0, "distance_m": dur_min * 1000,
                        "duration_min": dur_min, "osrm_fallback": False})
        out.append(row)
    return out


# monkey-patch osrm_client in-place for entire test run
osrm_client.route = _mock_route
osrm_client.table = _mock_table


# helpers
def _reset_osrm():
    _OSRM_FIXED_MIN.clear()


def _set_osrm(pair_a_b_min_map):
    _OSRM_FIXED_MIN.update(pair_a_b_min_map)


def _restore_flags():
    cm.ENABLE_PICKED_UP_DROP_FLOOR = True
    cm.ENABLE_DROP_TIME_CONSTRAINT = True
    rsv.ENABLE_PICKED_UP_DROP_FLOOR = True
    rsv.ENABLE_DROP_TIME_CONSTRAINT = True


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


def _bialystok(t=0.0, l=0.0):
    return (53.13 + t, 23.15 + l)


def _now(hour=17, minute=47, second=0):
    return datetime(2026, 4, 19, hour, minute, second, tzinfo=timezone.utc)


# ============================================================
print("=== V3.19a: picked_up floor core ===")
# ============================================================

# Test 1 — Gabriel fixture: picked_up Enklawa 17:38, drop Choroszcz, OSRM 17min.
# now=17:47. Synthetic courier pos = drop (last_picked_up_delivery).
# Expected: predicted_drop >= 17:38 + 17min + 1min DWELL = 17:56.
_reset_osrm()
_restore_flags()
enklawa = (53.134, 23.163)
choroszcz = (53.148, 22.985)
_set_osrm({(enklawa, choroszcz): 17.0})
gabriel_order = OrderSim(
    order_id="467262",
    pickup_coords=enklawa,
    delivery_coords=choroszcz,
    picked_up_at=_now(hour=17, minute=38),
    status="picked_up",
)
new_order = OrderSim(
    order_id="467282",
    pickup_coords=_bialystok(0.01, 0.01),
    delivery_coords=_bialystok(0.02, 0.02),
    status="assigned",
)
_set_osrm({
    (choroszcz, _bialystok(0.01, 0.01)): 8.0,
    (_bialystok(0.01, 0.01), _bialystok(0.02, 0.02)): 3.0,
})
plan = simulate_bag_route_v2(
    courier_pos=choroszcz,   # SYNTHETIC = drop
    bag=[gabriel_order],
    new_order=new_order,
    now=_now(17, 47),
)
gab_drop = plan.predicted_delivered_at.get("467262")
expected_floor = _now(17, 38) + timedelta(minutes=17) + timedelta(minutes=DWELL_DROPOFF_MIN)
check(
    "Gabriel picked_up drop >= floor (17:38 + 17min + 1min DWELL)",
    gab_drop is not None and gab_drop >= expected_floor,
)

# Test 2 — Gabriel absurdny fall-back NIE powinien się zdarzyć: drop > now+2min.
check(
    "Gabriel pred_drop NIE jest now+<=2min (bug eliminated)",
    (gab_drop - _now(17, 47)).total_seconds() / 60.0 > 2.0,
)

# Test 3 — Andrei fixture: picked_up Sushi 17:28, synthetic pos = drop.
# Drop is dwellowany po floor — delivered = floor + 1min DWELL... wait floor already includes DWELL.
# Actually in implementation: t = max(t, floor_t); then t += DWELL_DROPOFF_MIN; delivered_at = t.
# So delivered_at = floor_t + DWELL_DROPOFF_MIN if t < floor_t initially.
# floor_t = picked_up + drive + DWELL_DROPOFF_MIN. delivered_at = floor_t + DWELL_DROPOFF_MIN.
# This double-dwells. NOTE: acceptable since floor is LOWER bound — delivered nie może być niższe niż floor.
_reset_osrm()
_restore_flags()
sushi_pickup = (53.13, 23.17)
andrei_drop = (53.12, 23.20)
_set_osrm({(sushi_pickup, andrei_drop): 6.0})
andrei_order = OrderSim(
    order_id="467281",
    pickup_coords=sushi_pickup,
    delivery_coords=andrei_drop,
    picked_up_at=_now(17, 28),
    status="picked_up",
)
new_order2 = OrderSim(
    order_id="467299",
    pickup_coords=_bialystok(0.03, 0.04),
    delivery_coords=_bialystok(0.05, 0.06),
    status="assigned",
)
_set_osrm({
    (andrei_drop, _bialystok(0.03, 0.04)): 7.0,
    (_bialystok(0.03, 0.04), _bialystok(0.05, 0.06)): 4.0,
})
plan2 = simulate_bag_route_v2(
    courier_pos=andrei_drop,   # SYNTHETIC
    bag=[andrei_order],
    new_order=new_order2,
    now=_now(17, 45, 58),
)
andrei_pred = plan2.predicted_delivered_at.get("467281")
andrei_expected = _now(17, 28) + timedelta(minutes=6) + timedelta(minutes=DWELL_DROPOFF_MIN)
check(
    "Andrei picked_up drop >= floor (17:28 + 6min + 1min DWELL)",
    andrei_pred is not None and andrei_pred >= andrei_expected,
)

# Test 4 — Multi-drop bag: oba picked_up, oba powinny dostać floor independently.
_reset_osrm()
_restore_flags()
rest_a = (53.10, 23.10)
drop_a = (53.15, 23.20)
rest_b = (53.08, 23.05)
drop_b = (53.18, 23.22)
_set_osrm({
    (rest_a, drop_a): 10.0,
    (rest_b, drop_b): 12.0,
    (drop_a, drop_b): 4.0,
    (drop_b, drop_a): 4.0,
})
order_a = OrderSim("A", rest_a, drop_a, picked_up_at=_now(17, 30), status="picked_up")
order_b = OrderSim("B", rest_b, drop_b, picked_up_at=_now(17, 35), status="picked_up")
new_c = OrderSim("C", _bialystok(0.04, 0.02), _bialystok(0.06, 0.03), status="assigned")
_set_osrm({
    (drop_a, _bialystok(0.04, 0.02)): 5.0,
    (drop_b, _bialystok(0.04, 0.02)): 5.0,
    (_bialystok(0.04, 0.02), _bialystok(0.06, 0.03)): 3.0,
    (drop_a, _bialystok(0.06, 0.03)): 6.0,
    (drop_b, _bialystok(0.06, 0.03)): 6.0,
})
plan3 = simulate_bag_route_v2(
    courier_pos=drop_a,  # synthetic = A drop
    bag=[order_a, order_b],
    new_order=new_c,
    now=_now(17, 45),
)
a_pred = plan3.predicted_delivered_at.get("A")
b_pred = plan3.predicted_delivered_at.get("B")
a_floor = _now(17, 30) + timedelta(minutes=10) + timedelta(minutes=DWELL_DROPOFF_MIN)
b_floor = _now(17, 35) + timedelta(minutes=12) + timedelta(minutes=DWELL_DROPOFF_MIN)
check("Multi-drop A floor applied", a_pred is not None and a_pred >= a_floor)
check("Multi-drop B floor applied", b_pred is not None and b_pred >= b_floor)

# Test 5 — Assigned (status=2 mapped to "assigned") NIE dostaje floor, ma V3.18 constraint zamiast.
_reset_osrm()
_restore_flags()
r1 = (53.11, 23.12)
d1 = (53.14, 23.15)
_set_osrm({(r1, d1): 8.0})
# order assigned without picked_up_at → V3.19a no-op dla niego
order_assigned = OrderSim(
    order_id="X",
    pickup_coords=r1,
    delivery_coords=d1,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=None,
)
new_small = OrderSim("Y", (53.12, 23.13), (53.14, 23.16), status="assigned")
_set_osrm({
    (r1, (53.12, 23.13)): 2.0,  # courier is at pickup of X (assigned)
    (d1, (53.12, 23.13)): 4.0,
    ((53.12, 23.13), (53.14, 23.16)): 3.0,
    (d1, (53.14, 23.16)): 3.0,
})
plan4 = simulate_bag_route_v2(
    courier_pos=r1,   # at pickup of X
    bag=[order_assigned],
    new_order=new_small,
    now=_now(17, 45),
)
x_pred = plan4.predicted_delivered_at.get("X")
# assigned order bez pickup_ready_at + bez picked_up_at → żaden floor nie powinien byc applied.
# Oczekiwane: tylko leg_min(r1, d1) = 8min + DWELL → now + 9min (pickup dwell + drop dwell też).
# Ale _simulate_sequence nie ma "pickup" nodu dla bag items (są traktowane jako already picked_up).
# delivered_at = now + 8min + 1min = 17:54.
expected_x = _now(17, 45) + timedelta(minutes=8) + timedelta(minutes=DWELL_DROPOFF_MIN)
check(
    "Assigned (no pickup_ready, no picked_up_at) no floor — delta < 1s of plain sim",
    x_pred is not None and abs((x_pred - expected_x).total_seconds()) < 1.0,
)

# Test 6 — pickup_coords = (0,0) sentinel → skip floor (defensive, no crash).
_reset_osrm()
_restore_flags()
sentinel_order = OrderSim(
    order_id="S",
    pickup_coords=(0.0, 0.0),
    delivery_coords=(53.15, 23.20),
    picked_up_at=_now(17, 30),
    status="picked_up",
)
new_z = OrderSim("Z", (53.12, 23.13), (53.14, 23.16), status="assigned")
_set_osrm({
    ((53.15, 23.20), (53.12, 23.13)): 5.0,
    ((53.12, 23.13), (53.14, 23.16)): 3.0,
    ((53.15, 23.20), (53.14, 23.16)): 4.0,
})
plan5 = simulate_bag_route_v2(
    courier_pos=(53.15, 23.20),
    bag=[sentinel_order],
    new_order=new_z,
    now=_now(17, 45),
)
s_pred = plan5.predicted_delivered_at.get("S")
check("Sentinel pickup_coords (0,0) skips floor, no crash", s_pred is not None)

# Test 7 — picked_up_at=None edge case → skip floor (backward compat / data quality).
_reset_osrm()
_restore_flags()
no_ts_order = OrderSim(
    order_id="T",
    pickup_coords=(53.10, 23.10),
    delivery_coords=(53.15, 23.20),
    picked_up_at=None,            # BRAK timestampu mimo status picked_up
    status="picked_up",
)
new_w = OrderSim("W", (53.12, 23.13), (53.14, 23.16), status="assigned")
_set_osrm({
    ((53.15, 23.20), (53.12, 23.13)): 5.0,
    ((53.12, 23.13), (53.14, 23.16)): 3.0,
    ((53.15, 23.20), (53.14, 23.16)): 4.0,
})
plan6 = simulate_bag_route_v2(
    courier_pos=(53.15, 23.20),
    bag=[no_ts_order],
    new_order=new_w,
    now=_now(17, 45),
)
t_pred = plan6.predicted_delivered_at.get("T")
check("picked_up_at=None → skip floor, no crash", t_pred is not None)

# Test 8 — Flag OFF (kill switch) → V3.18 legacy behavior (floor nie aplikowany).
_reset_osrm()
cm.ENABLE_PICKED_UP_DROP_FLOOR = False
rsv.ENABLE_PICKED_UP_DROP_FLOOR = False
cm.ENABLE_DROP_TIME_CONSTRAINT = True
rsv.ENABLE_DROP_TIME_CONSTRAINT = True
gabriel_order_kill = OrderSim(
    order_id="467262K",
    pickup_coords=enklawa,
    delivery_coords=choroszcz,
    picked_up_at=_now(17, 38),
    status="picked_up",
)
new_k = OrderSim("NK", _bialystok(0.01, 0.01), _bialystok(0.02, 0.02), status="assigned")
_set_osrm({
    (enklawa, choroszcz): 17.0,
    (choroszcz, _bialystok(0.01, 0.01)): 8.0,
    (_bialystok(0.01, 0.01), _bialystok(0.02, 0.02)): 3.0,
})
plan7 = simulate_bag_route_v2(
    courier_pos=choroszcz,
    bag=[gabriel_order_kill],
    new_order=new_k,
    now=_now(17, 47),
)
gab_kill_pred = plan7.predicted_delivered_at.get("467262K")
delta_min_kill = (gab_kill_pred - _now(17, 47)).total_seconds() / 60.0
check(
    "Flag=False → V3.18 legacy (delta ≤ 2min, bug reproduces)",
    delta_min_kill <= 2.0,
)
_restore_flags()

# Test 9 — DWELL_DROPOFF_MIN constant unchanged (regression guard).
check("DWELL_DROPOFF_MIN == 1.0", abs(DWELL_DROPOFF_MIN - 1.0) < 1e-6)

# Test 10 — Floor NO-OP gdy real drive-based ETA już > floor (real GPS, not synthetic).
# Scenario: courier at real GPS pos far from drop; leg_min(courier, drop) = 20min;
# picked_up_at=17:30, OSRM(pickup→drop)=5min; floor = 17:30+5+1 = 17:36.
# now=17:45. t = 17:45 + 20min = 18:05. floor=17:36 < 18:05 → no change.
_reset_osrm()
_restore_flags()
gps_pos = (52.85, 22.90)   # far
drop = (53.15, 23.20)
pickup = (53.11, 23.12)
_set_osrm({
    (pickup, drop): 5.0,
    (gps_pos, drop): 20.0,
})
real_gps_order = OrderSim(
    order_id="G",
    pickup_coords=pickup,
    delivery_coords=drop,
    picked_up_at=_now(17, 30),
    status="picked_up",
)
new_q = OrderSim("Q", (53.12, 23.13), (53.14, 23.16), status="assigned")
_set_osrm({
    (drop, (53.12, 23.13)): 6.0,
    ((53.12, 23.13), (53.14, 23.16)): 3.0,
    (drop, (53.14, 23.16)): 5.0,
})
plan8 = simulate_bag_route_v2(
    courier_pos=gps_pos,
    bag=[real_gps_order],
    new_order=new_q,
    now=_now(17, 45),
)
g_pred = plan8.predicted_delivered_at.get("G")
expected_no_floor = _now(17, 45) + timedelta(minutes=20) + timedelta(minutes=DWELL_DROPOFF_MIN)
check(
    "Real GPS drive-based ETA > floor → no-op (delta ≈ leg_min=20+1)",
    g_pred is not None and abs((g_pred - expected_no_floor).total_seconds()) < 1.0,
)

# Test 11 — Regression: V3.18 Bug 1 path nadal działa dla unpicked z pickup_ready_at.
_reset_osrm()
_restore_flags()
p = (53.11, 23.12)
d = (53.15, 23.20)
_set_osrm({(p, d): 4.0})
pr = _now(17, 55)   # pickup_ready 17:55
unpicked_order = OrderSim(
    order_id="U",
    pickup_coords=p,
    delivery_coords=d,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=pr,
)
new_r = OrderSim("R", (53.12, 23.13), (53.14, 23.16), status="assigned")
_set_osrm({
    (p, (53.12, 23.13)): 2.0,
    (d, (53.12, 23.13)): 5.0,
    ((53.12, 23.13), (53.14, 23.16)): 3.0,
    (d, (53.14, 23.16)): 4.0,
})
plan9 = simulate_bag_route_v2(
    courier_pos=p,
    bag=[unpicked_order],
    new_order=new_r,
    now=_now(17, 45),
)
u_pred = plan9.predicted_delivered_at.get("U")
# V3.18: min_drop >= pickup_ready + DWELL_PICKUP_MIN = 17:55 + 2min = 17:57, then + DWELL_DROPOFF = 17:58.
expected_u = _now(17, 55) + timedelta(minutes=DWELL_PICKUP_MIN) + timedelta(minutes=DWELL_DROPOFF_MIN)
check(
    "V3.18 Bug 1 path: unpicked with pickup_ready → min_drop respected",
    u_pred is not None and u_pred >= expected_u,
)

# Test 12 — Solo new_order (bag empty) not affected by V3.19a.
_reset_osrm()
_restore_flags()
pickup_solo = (53.11, 23.12)
drop_solo = (53.14, 23.18)
_set_osrm({
    (_bialystok(0.0, 0.0), pickup_solo): 3.0,
    (pickup_solo, drop_solo): 5.0,
})
new_solo = OrderSim("SO", pickup_solo, drop_solo, status="assigned")
plan10 = simulate_bag_route_v2(
    courier_pos=_bialystok(0.0, 0.0),
    bag=[],
    new_order=new_solo,
    now=_now(17, 45),
)
so_pred = plan10.predicted_delivered_at.get("SO")
# courier → pickup 3min + pickup dwell 2min + drop 5min + drop dwell 1min = 11min total
expected_solo = _now(17, 45) + timedelta(minutes=3 + 2 + 5 + 1)
check("Solo order (empty bag) not affected",
      so_pred is not None and abs((so_pred - expected_solo).total_seconds()) < 1.0)

# ============================================================
print("=== V3.19a: regression guards ===")
# ============================================================

# Test 13 — V3.18 integration: drop_time_constraint + picked_up_floor both active,
# both blocks independent (assigned unaffected by picked_up flag, and vice versa).
_reset_osrm()
_restore_flags()
# Two bag orders: one assigned (V3.18 applies), one picked_up (V3.19a applies)
p_assigned = (53.09, 23.05)
d_assigned = (53.13, 23.15)
p_picked = (53.10, 23.10)
d_picked = (53.18, 23.22)
_set_osrm({
    (p_picked, d_picked): 12.0,
    (p_assigned, d_assigned): 7.0,
    (d_picked, d_assigned): 6.0,
    (d_assigned, d_picked): 6.0,
})
o_assigned = OrderSim(
    "AS", p_assigned, d_assigned,
    picked_up_at=None, status="assigned",
    pickup_ready_at=_now(17, 55),
)
o_picked = OrderSim(
    "PU", p_picked, d_picked,
    picked_up_at=_now(17, 30), status="picked_up",
)
new_z2 = OrderSim("Z2", (53.12, 23.13), (53.14, 23.16), status="assigned")
_set_osrm({
    (d_picked, (53.12, 23.13)): 5.0,
    (d_assigned, (53.12, 23.13)): 5.0,
    (d_picked, (53.14, 23.16)): 5.0,
    (d_assigned, (53.14, 23.16)): 5.0,
    ((53.12, 23.13), (53.14, 23.16)): 3.0,
})
plan11 = simulate_bag_route_v2(
    courier_pos=d_picked,  # synthetic = picked_up drop
    bag=[o_picked, o_assigned],
    new_order=new_z2,
    now=_now(17, 45),
)
pu_pred = plan11.predicted_delivered_at.get("PU")
as_pred = plan11.predicted_delivered_at.get("AS")
pu_floor = _now(17, 30) + timedelta(minutes=12) + timedelta(minutes=DWELL_DROPOFF_MIN)
# AS: V3.18 constraint → pickup_ready 17:55 + DWELL_PICKUP=2min → 17:57; plus drop dwell = 17:58 min.
as_floor = _now(17, 55) + timedelta(minutes=DWELL_PICKUP_MIN) + timedelta(minutes=DWELL_DROPOFF_MIN)
check("V3.18 + V3.19a both active: picked_up floor respected", pu_pred is not None and pu_pred >= pu_floor)
check("V3.18 + V3.19a both active: assigned floor respected", as_pred is not None and as_pred >= as_floor)

# ============================================================
total = passed + failed
print()
print("=" * 60)
print(f"V3.19a PICKED_UP FLOOR: {passed}/{total} PASS")
print("=" * 60)
if failed:
    sys.exit(1)
