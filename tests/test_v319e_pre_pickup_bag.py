"""V3.19e tests — pre-pickup bag semantics in route_simulator_v2.

Adresuje class-bug #467438 (11.5% shadow-log propozycji ma saved⊊fresh):
bag items z status="assigned" (pickup jeszcze nie nastąpił, np. wave #2
kurier_czas w przyszłości) były traktowane jako już picked_up → tylko
drop-node w simulator. Efekt: fantasy plan (drop przed pickup), zero
bundle discovery dla same-restaurant pickups.

V3.19e: dla bag items z status="assigned", simulator dodaje pickup-node
przed delivery-node. Gated przez ENABLE_V319E_PRE_PICKUP_BAG (default False).

Mock osrm_client. Uruchamia się jako standalone Python script.
"""
import sys
import os
from datetime import datetime, timezone, timedelta

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
_OSRM_FIXED_MIN = {}


def _mock_route(from_ll, to_ll, use_cache=True):
    key = (tuple(from_ll), tuple(to_ll))
    dur_min = _OSRM_FIXED_MIN.get(key, 5.0)
    return {"duration_s": dur_min * 60.0, "distance_m": dur_min * 1000,
            "duration_min": dur_min, "distance_km": dur_min,
            "osrm_fallback": False}


def _mock_table(origins, destinations):
    out = []
    for o in origins:
        row = []
        for d in destinations:
            dur_min = _OSRM_FIXED_MIN.get((tuple(o), tuple(d)), 5.0)
            if tuple(o) == tuple(d):
                dur_min = 0.0
            row.append({"duration_s": dur_min * 60.0, "distance_m": dur_min * 1000,
                        "duration_min": dur_min, "osrm_fallback": False})
        out.append(row)
    return out


osrm_client.route = _mock_route
osrm_client.table = _mock_table


def _reset_osrm():
    _OSRM_FIXED_MIN.clear()


def _set_osrm(mp):
    _OSRM_FIXED_MIN.update(mp)


def _set_v319e(enabled: bool):
    cm.ENABLE_V319E_PRE_PICKUP_BAG = enabled
    rsv.ENABLE_V319E_PRE_PICKUP_BAG = enabled


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


def _now(hour=16, minute=36):
    return datetime(2026, 4, 20, hour, minute, 0, tzinfo=timezone.utc)


# ============================================================
print("=== V3.19e: regression guards (flag=False lub brak pending) ===")
# ============================================================

# Test 1 — flag=False + bag z pending: legacy behavior (brak pickup-node dla bagu).
_reset_osrm()
_set_v319e(False)

rest_A = (53.13, 23.15)
drop_A = (53.14, 23.18)
rest_B = (53.12, 23.16)
drop_B = (53.15, 23.20)

bag_item_assigned = OrderSim(
    order_id="A",
    pickup_coords=rest_A,
    delivery_coords=drop_A,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=_now(hour=17, minute=0),
)
new_order = OrderSim(
    order_id="NEW",
    pickup_coords=rest_B,
    delivery_coords=drop_B,
    picked_up_at=None,
    status="assigned",
)
plan = simulate_bag_route_v2(
    courier_pos=rest_A, bag=[bag_item_assigned],
    new_order=new_order, now=_now(),
)
# Legacy behavior: tylko new_order ma pickup, bag item nie.
check("1. flag=False: pickup_at keys tylko new_order",
      set(plan.pickup_at.keys()) == {"NEW"})
check("1b. flag=False: sequence ma 2 drops + 1 new_pickup",
      plan.sequence == ["A", "NEW"] or plan.sequence == ["NEW", "A"])

# Test 2 — flag=True + bag all picked_up: brak pickup-node dla bagu (regression).
_set_v319e(True)
bag_item_picked = OrderSim(
    order_id="P",
    pickup_coords=rest_A,
    delivery_coords=drop_A,
    picked_up_at=_now(hour=16, minute=20),
    status="picked_up",
)
plan2 = simulate_bag_route_v2(
    courier_pos=drop_A, bag=[bag_item_picked],
    new_order=new_order, now=_now(),
)
check("2. flag=True all-picked bag: pickup_at keys tylko new_order",
      set(plan2.pickup_at.keys()) == {"NEW"})

# ============================================================
print("\n=== V3.19e: flag=True + pending bag items ===")
# ============================================================

# Test 3 — flag=True + 1 pending bag: plan ma pickup-node dla pending,
# pickup-before-delivery ordering.
_reset_osrm()
_set_v319e(True)
plan3 = simulate_bag_route_v2(
    courier_pos=rest_A, bag=[bag_item_assigned],
    new_order=new_order, now=_now(),
)
check("3. flag=True + 1 pending: pickup_at ma A i NEW",
      set(plan3.pickup_at.keys()) == {"A", "NEW"})
check("3b. A pickup przed A delivery w predicted times",
      plan3.pickup_at["A"] <= plan3.predicted_delivered_at["A"])
check("3c. NEW pickup przed NEW delivery",
      plan3.pickup_at["NEW"] <= plan3.predicted_delivered_at["NEW"])

# Test 4 — flag=True + 2 pending bag: 2 pickup-nodes, każdy pickup przed
# swoją dostawą.
_reset_osrm()
bag_item_2 = OrderSim(
    order_id="B",
    pickup_coords=rest_B,
    delivery_coords=drop_B,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=_now(hour=17, minute=10),
)
bag_item_1 = OrderSim(
    order_id="A",
    pickup_coords=rest_A,
    delivery_coords=drop_A,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=_now(hour=17, minute=0),
)
new_order_far = OrderSim(
    order_id="NEW",
    pickup_coords=(53.20, 23.25),
    delivery_coords=(53.18, 23.22),
    picked_up_at=None,
    status="assigned",
)
plan4 = simulate_bag_route_v2(
    courier_pos=(53.10, 23.10), bag=[bag_item_1, bag_item_2],
    new_order=new_order_far, now=_now(),
)
check("4. flag=True + 2 pending: pickup_at dla wszystkich 3",
      set(plan4.pickup_at.keys()) == {"A", "B", "NEW"})
check("4b. A pickup < A drop", plan4.pickup_at["A"] <= plan4.predicted_delivered_at["A"])
check("4c. B pickup < B drop", plan4.pickup_at["B"] <= plan4.predicted_delivered_at["B"])

# Test 5 — pickup_ready_at w przyszłości: simulator waits.
_reset_osrm()
now5 = _now(hour=16, minute=30)
future_ready = _now(hour=17, minute=0)  # 30 min later
bag_wait = OrderSim(
    order_id="W",
    pickup_coords=rest_A,
    delivery_coords=drop_A,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=future_ready,
)
plan5 = simulate_bag_route_v2(
    courier_pos=rest_A, bag=[bag_wait],
    new_order=new_order, now=now5,
)
# Pickup should be >= future_ready (simulator waited)
check("5. pickup_ready_at respected: W pickup_at >= future_ready",
      plan5.pickup_at["W"] >= future_ready)

# Test 6 — sticky fallback: mixed bag + base_sequence → use fresh (strategy != sticky).
_reset_osrm()
plan6 = simulate_bag_route_v2(
    courier_pos=drop_A, bag=[bag_item_picked, bag_item_assigned],
    new_order=new_order, now=_now(),
    base_sequence=["P", "A"],  # locked order attempt
)
check("6. mixed bag + base_sequence: strategy != 'sticky' (fallback)",
      plan6.strategy != "sticky")
check("6b. mixed bag: pending A ma pickup-node w planie",
      "A" in plan6.pickup_at)

# ============================================================
print("\n=== V3.19e: fixture #467438 (Kacper Sa wave #2 case) ===")
# ============================================================

# Test 7 — Fixture z propozycji #467438: 2 picked_up (467377 Pruszynka,
# 467402 Piwo Kaczka) + 3 pending z Grill Kebab (467423/467424) i
# Trzy Po Trzy (467430) + new_order 467438 Grill Kebab.
# V3.19e: plan powinien mieć pickup dla 467423/467424/467430/467438.
_reset_osrm()
_set_v319e(True)
piwo_kaczka = (53.10, 23.10)  # restaurant
jodlowa = (53.11, 23.11)       # drop 467402
pruszynka = (53.12, 23.12)     # restaurant
filipowicza = (53.13, 23.13)   # drop 467377
grill_kebab = (53.14, 23.14)   # restaurant (467423, 467424, 467438 SAME)
piastowska = (53.15, 23.15)    # drop 467423
kraszewskiego = (53.16, 23.16) # drop 467424
waszyngtona = (53.17, 23.17)   # drop 467438
trzy_po_trzy = (53.18, 23.18)  # restaurant
warszawska = (53.19, 23.19)    # drop 467430

bag467 = [
    # picked_up (bag already has)
    OrderSim(order_id="467377", pickup_coords=pruszynka, delivery_coords=filipowicza,
             picked_up_at=_now(hour=16, minute=2), status="picked_up"),
    OrderSim(order_id="467402", pickup_coords=piwo_kaczka, delivery_coords=jodlowa,
             picked_up_at=_now(hour=16, minute=9), status="picked_up"),
    # pending (wave #2 assigned, kurier_czas future)
    OrderSim(order_id="467423", pickup_coords=grill_kebab, delivery_coords=piastowska,
             picked_up_at=None, status="assigned",
             pickup_ready_at=_now(hour=17, minute=6)),
    OrderSim(order_id="467424", pickup_coords=grill_kebab, delivery_coords=kraszewskiego,
             picked_up_at=None, status="assigned",
             pickup_ready_at=_now(hour=17, minute=6)),
    OrderSim(order_id="467430", pickup_coords=trzy_po_trzy, delivery_coords=warszawska,
             picked_up_at=None, status="assigned",
             pickup_ready_at=_now(hour=17, minute=10)),
]
new467438 = OrderSim(
    order_id="467438",
    pickup_coords=grill_kebab,  # SAME jako 467423/467424 → bundle potential!
    delivery_coords=waszyngtona,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=_now(hour=17, minute=6),
)
plan7 = simulate_bag_route_v2(
    courier_pos=filipowicza,  # last drop position
    bag=bag467, new_order=new467438, now=_now(hour=16, minute=36),
)
# 4 pickups spodziewane: 3 pending bag (467423/424/430) + new (467438)
expected_pickups = {"467423", "467424", "467430", "467438"}
check("7. fixture #467438: pickup_at ma 467423/424/430/438",
      set(plan7.pickup_at.keys()) == expected_pickups)
# 6 drops w planie
check("7b. plan.sequence ma 6 drops (2 picked + 3 pending + new)",
      len(plan7.sequence) == 6)
# Każdy pickup przed odpowiednim drop
for oid in expected_pickups:
    check(f"7c. {oid} pickup < drop",
          plan7.pickup_at[oid] <= plan7.predicted_delivered_at[oid])

# Test 8 — per_order_delivery_times poprawne dla pending bag item.
# Pre-V3.19e dla assigned bag item elapsed = now → drop (bo pu=now fallback).
# Post-V3.19e elapsed = pickup_at → drop (real elapsed after pickup).
_reset_osrm()
_set_osrm({
    ((53.10, 23.10), (53.11, 23.11)): 5.0,  # courier → rest_A = 5 min
    ((53.11, 23.11), (53.12, 23.12)): 3.0,  # rest_A → drop_A = 3 min
    ((53.10, 23.10), (53.12, 23.12)): 8.0,  # courier → drop_A (unused)
})
courier_start = (53.10, 23.10)
rest_single = (53.11, 23.11)
drop_single = (53.12, 23.12)
now8 = _now(hour=16, minute=0)
bag_single = OrderSim(
    order_id="SINGLE",
    pickup_coords=rest_single,
    delivery_coords=drop_single,
    picked_up_at=None,
    status="assigned",
    pickup_ready_at=now8,  # ready immediately
)
plan8 = simulate_bag_route_v2(
    courier_pos=courier_start, bag=[bag_single],
    new_order=new_order, now=now8,
)
# Sprawdź: per_order_delivery_times["SINGLE"] = drop - pickup ≈ 5 (drive) + 1 (dwell)
# NIE = drop - now ≈ 9 (5 drive + 2 dwell pickup + 3 drive + 1 dwell drop?)
if plan8.per_order_delivery_times and "SINGLE" in plan8.per_order_delivery_times:
    elapsed = plan8.per_order_delivery_times["SINGLE"]
    check(f"8. per_order_delivery_times[SINGLE]={elapsed}min = pickup→drop (expected ~4)",
          3.0 <= elapsed <= 6.0)
else:
    check("8. per_order_delivery_times[SINGLE] present", False)

# restore flag for other tests
_set_v319e(False)

print("\n" + "=" * 60)
print(f"V3.19e PRE-PICKUP BAG: {passed}/{passed + failed} PASS" if failed == 0
      else f"V3.19e PRE-PICKUP BAG: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
