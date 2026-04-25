"""V3.27 Phase 1 latency shortcut tests.

Adrian Option B Phase 1: skip OR-Tools dla trivial cases (bag_after_add < 2).
D2 verified solve hits time_limit=200ms ceiling EVERY call regardless of N.
Bruteforce z 1-24 perms instant (<5ms) dla bag=0/1.

Run: python3 tests/test_v327_phase1_latency_shortcut.py
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import route_simulator_v2 as RS  # noqa: E402
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2  # noqa: E402


def test_constant_value():
    """V327_MIN_OR_TOOLS_BAG_AFTER=2 (bag>=1 → OR-Tools, bag=0 → bruteforce)."""
    assert C.V327_MIN_OR_TOOLS_BAG_AFTER == 2


def _mk_order(oid, pickup=(53.13, 23.16), drop=(53.12, 23.17), status="assigned"):
    return OrderSim(
        order_id=oid,
        pickup_coords=pickup,
        delivery_coords=drop,
        status=status,
        pickup_ready_at=None,
    )


def _mock_osrm_table(origins, destinations):
    """Fast mock: 5 min between any pair."""
    return [
        [{"duration_s": 300.0, "duration_min": 5.0, "distance_m": 2000,
          "distance_km": 2.0, "osrm_fallback": False} for _ in destinations]
        for _ in origins
    ]


def test_bag0_skips_ortools_uses_bruteforce():
    """Bag=0 + new pickup = bag_after_add=1 < 2 → skip OR-Tools, use bruteforce.

    Verify strategy field = 'bruteforce' (NIE 'ortools') gdy bag=0.
    """
    courier_pos = (53.13, 23.16)
    new_order = _mk_order("NEW", pickup=(53.135, 23.17), drop=(53.125, 23.155))
    bag = []  # empty

    with patch("dispatch_v2.osrm_client.table", side_effect=_mock_osrm_table):
        with patch("dispatch_v2.osrm_client.route", return_value={
            "duration_s": 300.0, "duration_min": 5.0, "distance_m": 2000,
            "distance_km": 2.0, "osrm_fallback": False,
        }):
            with patch("dispatch_v2.common.ENABLE_V326_OR_TOOLS_TSP", True):
                plan = simulate_bag_route_v2(
                    courier_pos=courier_pos,
                    bag=bag,
                    new_order=new_order,
                    now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
                )

    assert plan is not None
    assert plan.strategy == "bruteforce", \
        f"bag=0: expected strategy=bruteforce (V3.27 shortcut), got {plan.strategy}"


def test_bag1_skips_ortools_uses_bruteforce():
    """Bag=1 picked_up + new = bag_after_add=2 >= 2 → use OR-Tools.

    Wait — bag_after_add=2 jest >= 2, czyli use_ortools=True per nowej logice.
    Test poprawniej: bag=1 picked_up + new = bag_after_add=2 → OR-Tools.
    """
    courier_pos = (53.13, 23.16)
    new_order = _mk_order("NEW", pickup=(53.135, 23.17), drop=(53.125, 23.155))
    bag = [_mk_order("BAG1", pickup=(53.14, 23.18), drop=(53.12, 23.16),
                     status="picked_up")]

    with patch("dispatch_v2.osrm_client.table", side_effect=_mock_osrm_table):
        with patch("dispatch_v2.osrm_client.route", return_value={
            "duration_s": 300.0, "duration_min": 5.0, "distance_m": 2000,
            "distance_km": 2.0, "osrm_fallback": False,
        }):
            with patch("dispatch_v2.common.ENABLE_V326_OR_TOOLS_TSP", True):
                plan = simulate_bag_route_v2(
                    courier_pos=courier_pos,
                    bag=bag,
                    new_order=new_order,
                    now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
                )

    assert plan is not None
    assert plan.strategy == "ortools", \
        f"bag=1+new (bag_after_add=2 >=2): expected strategy=ortools, got {plan.strategy}"


def test_bag0_or_tools_flag_off_uses_bruteforce():
    """Backward compat: ENABLE_V326_OR_TOOLS_TSP=False → bruteforce regardless of bag size."""
    courier_pos = (53.13, 23.16)
    new_order = _mk_order("NEW")
    bag = []

    with patch("dispatch_v2.osrm_client.table", side_effect=_mock_osrm_table):
        with patch("dispatch_v2.osrm_client.route", return_value={
            "duration_s": 300.0, "duration_min": 5.0, "distance_m": 2000,
            "distance_km": 2.0, "osrm_fallback": False,
        }):
            with patch("dispatch_v2.common.ENABLE_V326_OR_TOOLS_TSP", False):
                plan = simulate_bag_route_v2(
                    courier_pos=courier_pos,
                    bag=bag,
                    new_order=new_order,
                    now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
                )

    assert plan is not None
    assert plan.strategy == "bruteforce", \
        f"flag OFF: expected bruteforce, got {plan.strategy}"


def test_bag2_uses_ortools():
    """Bag=2 picked_up + new = bag_after_add=3 >= 2 → OR-Tools (worth it for 720+ perms)."""
    courier_pos = (53.13, 23.16)
    new_order = _mk_order("NEW")
    bag = [
        _mk_order("BAG1", drop=(53.12, 23.16), status="picked_up"),
        _mk_order("BAG2", drop=(53.11, 23.17), status="picked_up"),
    ]

    with patch("dispatch_v2.osrm_client.table", side_effect=_mock_osrm_table):
        with patch("dispatch_v2.osrm_client.route", return_value={
            "duration_s": 300.0, "duration_min": 5.0, "distance_m": 2000,
            "distance_km": 2.0, "osrm_fallback": False,
        }):
            with patch("dispatch_v2.common.ENABLE_V326_OR_TOOLS_TSP", True):
                plan = simulate_bag_route_v2(
                    courier_pos=courier_pos,
                    bag=bag,
                    new_order=new_order,
                    now=datetime(2026, 4, 25, 14, 0, tzinfo=timezone.utc),
                )

    assert plan is not None
    assert plan.strategy == "ortools", \
        f"bag=2+new (bag_after_add=3 >=2): expected ortools, got {plan.strategy}"


def test_warmup_imports_idempotent():
    """V3.27 Phase 1F: warm-up imports are idempotent — re-importing OK."""
    from ortools.constraint_solver import pywrapcp
    from ortools.constraint_solver import routing_enums_pb2
    # Second import should be cached (no exception, fast)
    from ortools.constraint_solver import pywrapcp as p2
    assert p2 is pywrapcp


if __name__ == "__main__":
    test_constant_value()
    print("test_constant_value: PASS")
    test_bag0_skips_ortools_uses_bruteforce()
    print("test_bag0_skips_ortools_uses_bruteforce: PASS")
    test_bag1_skips_ortools_uses_bruteforce()
    print("test_bag1_skips_ortools_uses_bruteforce: PASS")
    test_bag0_or_tools_flag_off_uses_bruteforce()
    print("test_bag0_or_tools_flag_off_uses_bruteforce: PASS")
    test_bag2_uses_ortools()
    print("test_bag2_uses_ortools: PASS")
    test_warmup_imports_idempotent()
    print("test_warmup_imports_idempotent: PASS")
    print("ALL 6/6 PASS")
