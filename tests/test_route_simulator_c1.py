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


# ============================================================
# V3.27.1 BUG-2: TSP time windows tests
# ============================================================

def _build_v327_test_scenario(now, flag_enabled, captured_calls=None,
                               infeasible_first_call=False):
    """Helper: setup _ortools_plan call z mock tsp_solver capturing kwargs.

    Returns (captured_kwargs_list, plan_or_none).
    """
    from dispatch_v2 import common, route_simulator_v2 as rs2, tsp_solver
    _setup_mock()

    # Override flag dla testu
    orig_flag = common.ENABLE_V327_TSP_TIME_WINDOWS
    common.ENABLE_V327_TSP_TIME_WINDOWS = flag_enabled

    # Mock tsp_solver.solve_tsp_with_constraints — capture kwargs
    if captured_calls is None:
        captured_calls = []

    class _StubSolution:
        def __init__(self, sequence):
            self.sequence = sequence
            self.solver_status = "OK"
            self.elapsed_ms = 1.0
            self.warnings = []

    call_count = [0]
    def _mock_solver(**kwargs):
        captured_calls.append(dict(kwargs))
        call_count[0] += 1
        # First call infeasible (if requested), second call returns valid sequence
        if infeasible_first_call and call_count[0] == 1:
            return None
        # Build valid sequence: [0=courier, 1=pickup, 2=drop]
        n = kwargs.get("num_stops", 3)
        return _StubSolution(list(range(n)))

    orig_solver = tsp_solver.solve_tsp_with_constraints
    tsp_solver.solve_tsp_with_constraints = _mock_solver

    try:
        # Build minimal bag scenario: 1 bag order + 1 new order (bag_after_add=2)
        from dispatch_v2.route_simulator_v2 import OrderSim, _ortools_plan
        bag_order = OrderSim(
            order_id="B0",
            pickup_coords=(53.10, 23.10),
            delivery_coords=(53.20, 23.20),
            picked_up_at=None,
            pickup_ready_at=now + timedelta(minutes=5),
            status="assigned",
        )
        new_order = OrderSim(
            order_id="N1",
            pickup_coords=(53.15, 23.15),
            delivery_coords=(53.25, 23.25),
            picked_up_at=None,
            pickup_ready_at=now + timedelta(minutes=15),
            status="new",
        )
        bag = [bag_order]

        # Build nodes manually w pattern z route_simulator_v2:
        # [0=courier, 1=bag_pickup, 2=bag_delivery, 3=new_pickup, 4=new_delivery]
        nodes = [
            {"kind": "courier", "coords": (53.0, 23.0), "order_id": None, "ref": None},
            {"kind": "pickup", "coords": bag_order.pickup_coords, "order_id": "B0", "ref": bag_order},
            {"kind": "delivery", "coords": bag_order.delivery_coords, "order_id": "B0", "ref": bag_order},
            {"kind": "pickup", "coords": new_order.pickup_coords, "order_id": "N1", "ref": new_order},
            {"kind": "delivery", "coords": new_order.delivery_coords, "order_id": "N1", "ref": new_order},
        ]
        leg_min = lambda i, j: 5.0  # constant 5 min between any pair
        plan = _ortools_plan(
            nodes, leg_min,
            bag_delivery_idxs=[2, 4],
            bag_pickup_idxs_by_oid={"B0": 1, "N1": 3},
            new_pickup_idx=3, new_delivery_idx=4,
            new_order=new_order, bag=bag, now=now, sla_minutes=35.0,
        )
    finally:
        tsp_solver.solve_tsp_with_constraints = orig_solver
        common.ENABLE_V327_TSP_TIME_WINDOWS = orig_flag

    return captured_calls, plan


def test_v327_time_windows_disabled_baseline():
    """V3.27.1 BUG-2: gdy flag=False, time_windows=None passed do solver
    (zachowuje pre-V3.27.1 behavior, zero regression baseline)."""
    now = datetime(2026, 4, 26, 14, 0, tzinfo=timezone.utc)
    calls, _ = _build_v327_test_scenario(now, flag_enabled=False)
    assert len(calls) == 1, f"expected 1 solver call, got {len(calls)}"
    assert calls[0]["time_windows"] is None, \
        f"flag=False MUST pass time_windows=None, got {calls[0]['time_windows']}"


def test_v327_time_windows_enabled_passes_pickup_constraints():
    """V3.27.1 BUG-2: gdy flag=True, time_windows is list with proper
    pickup constraints (open=ready-now, close=open+60min) per pickup node."""
    now = datetime(2026, 4, 26, 14, 0, tzinfo=timezone.utc)
    calls, _ = _build_v327_test_scenario(now, flag_enabled=True)
    assert len(calls) == 1, f"expected 1 solver call (no fallback), got {len(calls)}"
    tw = calls[0]["time_windows"]
    assert isinstance(tw, list), f"flag=True MUST pass time_windows=list, got {type(tw)}"
    assert len(tw) == 5, f"expected 5 windows (5 nodes), got {len(tw)}"
    # node 0 = courier → loose (0, 120)
    assert tw[0] == (0.0, 120.0), f"courier window {tw[0]} != (0.0, 120.0)"
    # node 1 = bag_pickup ready=+5min → open=5.0, close=65.0
    assert abs(tw[1][0] - 5.0) < 0.1, f"bag_pickup open {tw[1][0]} != 5.0"
    assert abs(tw[1][1] - 65.0) < 0.1, f"bag_pickup close {tw[1][1]} != 65.0"
    # node 2 = bag_delivery → loose (0, 120)
    assert tw[2] == (0.0, 120.0), f"bag_delivery window {tw[2]} != (0.0, 120.0)"
    # node 3 = new_pickup ready=+15min → open=15.0, close=75.0
    assert abs(tw[3][0] - 15.0) < 0.1, f"new_pickup open {tw[3][0]} != 15.0"
    assert abs(tw[3][1] - 75.0) < 0.1, f"new_pickup close {tw[3][1]} != 75.0"
    # node 4 = new_delivery → loose (0, 120)
    assert tw[4] == (0.0, 120.0), f"new_delivery window {tw[4]} != (0.0, 120.0)"


def test_v327_time_windows_infeasible_fallback_retries_no_constraints():
    """V3.27.1 BUG-2 fallback: gdy solver returns None pierwszym razem (z time
    windows), retry bez constraints dla baseline-safety. Solver MUSI być
    wywołany 2× — drugi raz z time_windows=None."""
    now = datetime(2026, 4, 26, 14, 0, tzinfo=timezone.utc)
    calls, plan = _build_v327_test_scenario(now, flag_enabled=True,
                                              infeasible_first_call=True)
    assert len(calls) == 2, f"expected 2 solver calls (fallback), got {len(calls)}"
    # First call z time_windows (list)
    assert isinstance(calls[0]["time_windows"], list), \
        f"first call MUST have time_windows=list, got {calls[0]['time_windows']}"
    # Second call z time_windows=None (fallback)
    assert calls[1]["time_windows"] is None, \
        f"fallback call MUST have time_windows=None, got {calls[1]['time_windows']}"


def main():
    tests = [
        ('per_order_times_populated_for_normal_bag', test_per_order_times_populated_for_normal_bag),
        ('per_order_times_correlation_with_total', test_per_order_times_correlation_with_total),
        ('per_order_times_all_orders_covered', test_per_order_times_all_orders_covered),
        ('per_order_times_backward_compat_existing_fields', test_per_order_times_backward_compat_existing_fields),
        ('compute_helper_returns_none_when_delivered_missing', test_compute_helper_returns_none_when_delivered_missing),
        ('v327_time_windows_disabled_baseline', test_v327_time_windows_disabled_baseline),
        ('v327_time_windows_enabled_passes_pickup_constraints', test_v327_time_windows_enabled_passes_pickup_constraints),
        ('v327_time_windows_infeasible_fallback_retries_no_constraints', test_v327_time_windows_infeasible_fallback_retries_no_constraints),
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
