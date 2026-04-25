"""F2.2 C1 tests — per_order_delivery_times field w RoutePlanV2.

Zero kontaktu z OSRM (mock via monkeypatch osrm_client.table). Standalone executable.
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import route_simulator_v2
from dispatch_v2.route_simulator_v2 import (
    OrderSim, RoutePlanV2, simulate_bag_route_v2,
    _compute_per_order_delivery_minutes,
)


class FakeCell(dict):
    """Dict z .get() that returns 300s (5 min) legs by default."""


def _mock_osrm_table(points_a, points_b):
    """All legs = 300s (5 min). Matrix square shape."""
    n = len(points_a)
    row = lambda: [{"duration_s": 300, "osrm_fallback": False} for _ in range(n)]
    return [row() for _ in range(n)]


def _setup_mock():
    route_simulator_v2.osrm_client.table = _mock_osrm_table


def _build_bag(n, now):
    """N bag orders, picked_up 2 min ago each."""
    return [
        OrderSim(
            order_id=f"B{i}",
            pickup_coords=(53.1 + i * 0.01, 23.1 + i * 0.01),
            delivery_coords=(53.2 + i * 0.01, 23.2 + i * 0.01),
            picked_up_at=now - timedelta(minutes=2),
            status="picked_up",
        )
        for i in range(n)
    ]


def test_per_order_times_populated_for_normal_bag():
    _setup_mock()
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = _build_bag(2, now)
    new_order = OrderSim(
        order_id="NEW1",
        pickup_coords=(53.15, 23.15),
        delivery_coords=(53.25, 23.25),
        status="assigned",
    )
    plan = simulate_bag_route_v2((53.0, 23.0), bag, new_order, now=now)
    assert plan.per_order_delivery_times is not None, "must populate field"
    assert len(plan.per_order_delivery_times) == 3, f"2 bag + 1 new; got {plan.per_order_delivery_times}"
    for oid, mins in plan.per_order_delivery_times.items():
        assert mins > 0, f"order {oid} elapsed {mins} should be positive"
    return True


def test_per_order_times_correlation_with_total():
    """Sanity: żaden per-order elapsed > total_duration_min (+ tolerance bo pickup może być przed now)."""
    _setup_mock()
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = _build_bag(3, now)
    new_order = OrderSim(
        order_id="NEW2",
        pickup_coords=(53.2, 23.2),
        delivery_coords=(53.3, 23.3),
        status="assigned",
    )
    plan = simulate_bag_route_v2((53.0, 23.0), bag, new_order, now=now)
    assert plan.per_order_delivery_times is not None
    # Per-order elapsed może być > total_duration_min dla bag items
    # (bo pickup_at był w przeszłości - now - 2min). Sanity: max nie jest absurdalne (np. <120 min).
    max_elapsed = max(plan.per_order_delivery_times.values())
    assert max_elapsed < 120, f"max per-order {max_elapsed} unreasonable"
    # new_order's elapsed not exceeding total (pickup in plan)
    assert plan.per_order_delivery_times['NEW2'] <= plan.total_duration_min + 0.1
    return True


def test_per_order_times_all_orders_covered():
    _setup_mock()
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = _build_bag(2, now)
    new_order = OrderSim(
        order_id="NEW3",
        pickup_coords=(53.12, 23.12),
        delivery_coords=(53.22, 23.22),
        status="assigned",
    )
    plan = simulate_bag_route_v2((53.0, 23.0), bag, new_order, now=now)
    assert plan.per_order_delivery_times is not None
    expected_oids = {'B0', 'B1', 'NEW3'}
    got_oids = set(plan.per_order_delivery_times.keys())
    assert got_oids == expected_oids, f"expected {expected_oids}, got {got_oids}"
    return True


def test_per_order_times_backward_compat_existing_fields():
    """Assert pre-C1 fields still accessible (no breaking change)."""
    _setup_mock()
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = _build_bag(1, now)
    new_order = OrderSim(
        order_id="NEW4",
        pickup_coords=(53.13, 23.13),
        delivery_coords=(53.23, 23.23),
        status="assigned",
    )
    plan = simulate_bag_route_v2((53.0, 23.0), bag, new_order, now=now)
    # Old fields all present, correct types
    assert isinstance(plan.sequence, list)
    assert isinstance(plan.predicted_delivered_at, dict)
    assert isinstance(plan.pickup_at, dict)
    assert isinstance(plan.total_duration_min, (int, float))
    assert plan.total_duration_min > 0
    assert plan.strategy in ('bruteforce', 'greedy', 'ortools', 'sticky', 'greedy_fallback')  # V3.27: extended dla OR-Tools LIVE
    assert isinstance(plan.sla_violations, int)
    assert isinstance(plan.osrm_fallback_used, bool)
    # New C1 field
    assert plan.per_order_delivery_times is not None
    return True


def test_compute_helper_returns_none_when_delivered_missing():
    """Fail-closed semantic: missing delivered_at → None."""
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = _build_bag(1, now)
    new_order = OrderSim(
        order_id="NEW5",
        pickup_coords=(53.0, 23.0),
        delivery_coords=(53.1, 23.1),
        status="assigned",
    )
    # Bag B0 delivered_at missing → should return None
    delivered_at = {'NEW5': now + timedelta(minutes=30)}  # only new_order, bag missing
    pickup_at = {'NEW5': now + timedelta(minutes=5)}
    result = _compute_per_order_delivery_minutes(delivered_at, pickup_at, bag, new_order, now)
    assert result is None, f"must return None when any delivered_at missing; got {result}"
    return True


def main():
    tests = [
        ('per_order_times_populated_for_normal_bag', test_per_order_times_populated_for_normal_bag),
        ('per_order_times_correlation_with_total', test_per_order_times_correlation_with_total),
        ('per_order_times_all_orders_covered', test_per_order_times_all_orders_covered),
        ('per_order_times_backward_compat_existing_fields', test_per_order_times_backward_compat_existing_fields),
        ('compute_helper_returns_none_when_delivered_missing', test_compute_helper_returns_none_when_delivered_missing),
    ]
    print('=' * 60)
    print('F2.2 C1: per_order_delivery_times field + helper tests')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ✅ {name}')
            passed += 1
        except AssertionError as e:
            print(f'  ❌ {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
