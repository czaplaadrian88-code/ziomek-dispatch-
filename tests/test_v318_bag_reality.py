"""V3.18 Bag Reality Check — regression tests.

Testy dla:
  Bug 1 — drop_time constraint dla unpicked bag items (route_simulator_v2)
  Bug 2 — fleet overload penalty (scoring + fleet_context)
  Bug 3 — CourierBagState.is_free single source (bag_state)
  Integrity — frozen immutability, Warsaw TZ, kill-switch

Wywołaj z /root/.openclaw/workspace/scripts/:
  python3 dispatch_v2/tests/test_v318_bag_reality.py
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

# Ensure scripts/ is on path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.bag_state import (
    build_courier_bag_state, CourierBagState, OrderInBag,
    WARSAW, ACTIVE_STATUSES, PICKED_UP_STATUSES,
)
from dispatch_v2.fleet_context import build_fleet_context, FleetContext
from dispatch_v2 import scoring
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
from dispatch_v2 import common as C

_results = {"pass": 0, "fail": 0, "fail_msgs": []}


def expect(name, cond, detail=""):
    if cond:
        _results["pass"] += 1
        print(f"  ✅ {name}")
    else:
        _results["fail"] += 1
        _results["fail_msgs"].append(f"{name}: {detail}")
        print(f"  ❌ {name}  {detail}")


def test_bag_state_builder():
    print("\n=== BAG_STATE: builder + properties ===")
    # 1 empty bag
    s = build_courier_bag_state("1", "Test", "gps", (53.13, 23.14), [])
    expect("1. empty bag bag_size=0", s.bag_size == 0)
    expect("2. empty bag is_free=True", s.is_free is True)
    expect("3. empty bag no unpicked", s.has_unpicked_orders is False)

    # 4-5 mixed statuses
    s = build_courier_bag_state("2", "Test2", "gps", (53.13, 23.14), [
        {"order_id": "1000", "status": 3, "pickup_time": datetime(2026, 4, 19, 19, 12, tzinfo=WARSAW)},
        {"order_id": "1001", "status": 5, "pickup_time": datetime(2026, 4, 19, 18, 30, tzinfo=WARSAW)},
        {"order_id": "1002", "status": 7},  # terminal → excluded
    ])
    expect("4. terminal status filtered (bag_size=2)", s.bag_size == 2, f"got {s.bag_size}")
    expect("5. has_unpicked_orders True for status=3", s.has_unpicked_orders is True)
    expect("6. picked_up_orders count=1", len(s.picked_up_orders) == 1)
    expect("7. pending_pickup count=1", len(s.pending_pickup_orders) == 1)

    # 8 frozen immutability
    try:
        s.orders = ()
        expect("8. frozen blocks reassignment", False, "should have raised")
    except Exception:
        expect("8. frozen blocks reassignment", True)

    # 9 Warsaw TZ enforced
    naive_dt = datetime(2026, 4, 19, 19, 12)
    s = build_courier_bag_state("3", "T3", "gps", None, [
        {"order_id": "2000", "status": 3, "pickup_time": naive_dt},
    ])
    expect("9. naive pickup_time → Warsaw TZ",
           s.orders[0].pickup_time.tzinfo is not None)


def test_fleet_context():
    print("\n=== FLEET_CONTEXT: builder + overload_delta ===")
    states = [
        build_courier_bag_state("1", "A", "gps", (53, 23), [
            {"order_id": "1", "status": 3}, {"order_id": "2", "status": 5}]),  # bag=2
        build_courier_bag_state("2", "B", "gps", (53, 23), [
            {"order_id": "3", "status": 3}, {"order_id": "4", "status": 5},
            {"order_id": "5", "status": 5}]),  # bag=3
        build_courier_bag_state("3", "C", "gps", (53, 23), [
            {"order_id": f"o{i}", "status": 5} for i in range(5)]),  # bag=5 overload
        build_courier_bag_state("4", "D", "pre_shift", (53, 23), []),  # excluded
    ]
    fc = build_fleet_context(states)
    expect("10. active_couriers=3 (pre_shift empty excluded)", fc.active_couriers == 3)
    expect("11. avg_bag=3.33", fc.avg_bag == 3.33)
    expect("12. max_bag=5", fc.max_bag == 5)
    expect("13. total_snapshot=4", fc.total_couriers_snapshot == 4)
    expect("14. overload_delta(5) > threshold", fc.overload_delta(5) == 1)
    expect("15. overload_delta(6) >= 2", fc.overload_delta(6) == 2)
    expect("16. overload_delta under avg <= 0", fc.overload_delta(2) <= 0)

    # empty fleet edge
    fc_empty = build_fleet_context([])
    expect("17. empty fleet is_empty", fc_empty.is_empty is True)
    expect("18. empty fleet overload_delta returns 0", fc_empty.overload_delta(5) == 0)


def test_bug2_scoring_overload():
    print("\n=== BUG 2: scoring overload penalty ===")
    states = [
        build_courier_bag_state("1", "A", "gps", (53, 23), [
            {"order_id": "1", "status": 3}]),  # bag=1
        build_courier_bag_state("2", "B", "gps", (53, 23), [
            {"order_id": "2", "status": 3}, {"order_id": "3", "status": 5}]),  # bag=2
    ]
    fc = build_fleet_context(states)  # avg = 1.5

    # 19 under-loaded: no penalty
    r = scoring.score_candidate((53, 23), (53.01, 23.01), bag_size=1, fleet_context=fc)
    expect("19. bag=1 no overload penalty", r["metrics"]["overload_penalty_applied"] == 0.0)

    # 20 over-loaded: penalty applied (5-1.5=3.5 > threshold 2)
    r = scoring.score_candidate((53, 23), (53.01, 23.01), bag_size=5, fleet_context=fc)
    expect("20. bag=5 over-loaded → penalty applied",
           r["metrics"]["overload_penalty_applied"] == C.OVERLOAD_PENALTY,
           f"got {r['metrics']['overload_penalty_applied']}")

    # 21 no fleet_context → legacy path, zero penalty
    r = scoring.score_candidate((53, 23), (53.01, 23.01), bag_size=5)
    expect("21. no fleet_context → zero overload penalty",
           r["metrics"]["overload_penalty_applied"] == 0.0)

    # 22 flag kill-switch (simulated via backup+restore)
    original = C.ENABLE_FLEET_OVERLOAD_PENALTY
    try:
        C.ENABLE_FLEET_OVERLOAD_PENALTY = False
        # Re-import scoring to pick up flag change? Actually scoring reads flag at call time
        import importlib
        importlib.reload(scoring)
        r = scoring.score_candidate((53, 23), (53.01, 23.01), bag_size=5, fleet_context=fc)
        expect("22. flag disabled → zero penalty",
               r["metrics"]["overload_penalty_applied"] == 0.0)
    finally:
        C.ENABLE_FLEET_OVERLOAD_PENALTY = original
        importlib.reload(scoring)


def test_bug1_drop_time_constraint():
    print("\n=== BUG 1: drop_time >= pickup_time constraint ===")
    now = datetime(2026, 4, 19, 18, 0, tzinfo=timezone.utc)

    # 23 unpicked bag item with future pickup_ready → drop constrained
    bag = [OrderSim(
        order_id="mama_thai_pending",
        pickup_coords=(53.130, 23.160),
        delivery_coords=(53.1301, 23.1601),  # very close = low drive time
        status="assigned",
        pickup_ready_at=datetime(2026, 4, 19, 18, 30, tzinfo=timezone.utc),
    )]
    new_order = OrderSim(
        order_id="new_rukola",
        pickup_coords=(53.125, 23.155),
        delivery_coords=(53.140, 23.170),
        status="assigned",
    )
    plan = simulate_bag_route_v2((53.1301, 23.1601), bag, new_order, now=now)
    mama_drop = plan.predicted_delivered_at.get("mama_thai_pending")
    expect("23. unpicked drop_time >= pickup_ready + dwell",
           mama_drop >= bag[0].pickup_ready_at + timedelta(minutes=2),
           f"drop={mama_drop}")

    # 24 picked_up bag item: constraint NOT applied (legacy unconstrained)
    bag2 = [OrderSim(
        order_id="already_picked",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.1301, 23.1601),
        status="picked_up",
        pickup_ready_at=datetime(2026, 4, 19, 18, 30, tzinfo=timezone.utc),
    )]
    plan2 = simulate_bag_route_v2((53.1301, 23.1601), bag2, new_order, now=now)
    picked_drop = plan2.predicted_delivered_at.get("already_picked")
    expect("24. picked_up NOT constrained (early drop allowed)",
           picked_drop < bag2[0].pickup_ready_at + timedelta(minutes=2),
           f"drop={picked_drop}")


def test_bug3_is_free_defense():
    print("\n=== BUG 3: CourierBagState.is_free single-source ===")
    # 25 is_free=True only when zero active orders
    s_empty = build_courier_bag_state("1", "A", "gps", None, [])
    expect("25. empty bag is_free=True", s_empty.is_free is True)

    # 26 is_free=False with any active order
    s_one = build_courier_bag_state("2", "B", "gps", None, [
        {"order_id": "1", "status": 3}])
    expect("26. bag_size=1 is_free=False", s_one.is_free is False)

    # 27 terminal statuses don't count as active (mocked)
    s_term = build_courier_bag_state("3", "C", "gps", None, [
        {"order_id": "1", "status": 7}, {"order_id": "2", "status": 9}])
    expect("27. terminal-only bag is_free=True (terminal filtered)",
           s_term.is_free is True)


def test_regression_v317_baseline_compat():
    print("\n=== REGRESSION: V3.12-V3.17 compat ===")
    # 28 scoring accepts legacy call (no fleet_context)
    r = scoring.score_candidate((53, 23), (53.01, 23.01),
                                 bag_size=0, oldest_in_bag_min=None)
    expect("28. legacy scoring call (no fleet_context) works",
           isinstance(r, dict) and "total" in r)

    # 29 route_simulator accepts legacy calls without pickup_ready
    now = datetime(2026, 4, 19, 18, 0, tzinfo=timezone.utc)
    plan = simulate_bag_route_v2(
        (53.13, 23.16), [], OrderSim(
            order_id="solo", pickup_coords=(53.12, 23.15), delivery_coords=(53.14, 23.17),
            status="assigned",
        ), now=now)
    expect("29. solo plan still works", plan is not None and plan.total_duration_min > 0)

    # 30 fleet_context None handling in scoring
    r = scoring.score_candidate((53, 23), (53.01, 23.01), bag_size=5, fleet_context=None)
    expect("30. None fleet_context → no penalty", r["metrics"]["overload_penalty_applied"] == 0.0)


def main():
    test_bag_state_builder()
    test_fleet_context()
    test_bug2_scoring_overload()
    test_bug1_drop_time_constraint()
    test_bug3_is_free_defense()
    test_regression_v317_baseline_compat()

    total = _results["pass"] + _results["fail"]
    print("\n" + "=" * 60)
    print(f"V3.18 BAG REALITY CHECK: {_results['pass']}/{total} PASS")
    print("=" * 60)
    if _results["fail"]:
        print("\nFAILED:")
        for m in _results["fail_msgs"]:
            print(f"  - {m}")
    sys.exit(0 if _results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
