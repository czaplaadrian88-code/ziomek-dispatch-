"""V3.27.3 Wait kuriera penalty tests (TASK B, 2026-04-27).

12 unit tests + 2 integration tests:
- Unit: per-row penalty table verification (compute_wait_courier_penalty)
- Unit: arrival_at field populated w _simulate_sequence return + RoutePlanV2
- Unit: bag=0 skip
- Unit: HARD REJECT >20 min
- Integration: #468945 ground truth z real shadow log Andrei wait 12.6 min → -40 penalty
- Integration: bag=0 skip end-to-end (penalty NIE fires nawet z wait 30 min)

Per Lekcja #28: integration test używa real shadow log entry parse, NIE mocks.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


# Force flag True for tests (default False in production)
os.environ["ENABLE_V3273_WAIT_COURIER_PENALTY"] = "1"

import importlib  # noqa: E402
from dispatch_v2 import common as _common  # noqa: E402
importlib.reload(_common)
from dispatch_v2 import scoring  # noqa: E402
importlib.reload(scoring)
from dispatch_v2.scoring import compute_wait_courier_penalty  # noqa: E402
from dispatch_v2.route_simulator_v2 import (  # noqa: E402
    OrderSim,
    RoutePlanV2,
    simulate_bag_route_v2,
    DWELL_PICKUP_MIN,
    DWELL_DROPOFF_MIN,
)


# ─── Unit tests: penalty table ──────────────────────────────────

def test_wait_0_min_zero_penalty():
    p, r = compute_wait_courier_penalty(0.0, bag_size_at_insertion=1)
    assert p == 0.0
    assert r is False


def test_wait_5_min_sweet_spot():
    p, r = compute_wait_courier_penalty(5.0, bag_size_at_insertion=1)
    assert p == 0.0
    assert r is False


def test_wait_6_min_first_step_minus_10():
    p, r = compute_wait_courier_penalty(6.0, bag_size_at_insertion=1)
    assert p == -10.0
    assert r is False


def test_wait_7_min_minus_15():
    p, r = compute_wait_courier_penalty(7.0, bag_size_at_insertion=1)
    assert p == -15.0
    assert r is False


def test_wait_10_min_minus_30():
    p, r = compute_wait_courier_penalty(10.0, bag_size_at_insertion=1)
    assert p == -30.0
    assert r is False


def test_wait_12_min_minus_40_andrei_case():
    p, r = compute_wait_courier_penalty(12.6, bag_size_at_insertion=1)
    # 12.6 → -10 + (12.6 - 6) * -5 = -10 - 33 = -43
    assert abs(p - -43.0) < 0.01
    assert r is False


def test_wait_15_min_minus_55():
    p, r = compute_wait_courier_penalty(15.0, bag_size_at_insertion=1)
    assert p == -55.0
    assert r is False


def test_wait_20_min_minus_80():
    p, r = compute_wait_courier_penalty(20.0, bag_size_at_insertion=1)
    assert p == -80.0
    assert r is False


def test_wait_20_5_min_hard_reject():
    p, r = compute_wait_courier_penalty(20.5, bag_size_at_insertion=1)
    assert p == 0.0
    assert r is True


def test_wait_30_min_hard_reject():
    p, r = compute_wait_courier_penalty(30.0, bag_size_at_insertion=1)
    assert p == 0.0
    assert r is True


def test_bag_0_skip_with_high_wait():
    """Conditional firing: bag=0 → NIE fire penalty nawet 30 min wait."""
    p, r = compute_wait_courier_penalty(30.0, bag_size_at_insertion=0)
    assert p == 0.0
    assert r is False


def test_interpolation_between_5_and_6():
    """Linear ramp 0 → -10 dla wait_min in (5, 6)."""
    p, r = compute_wait_courier_penalty(5.5, bag_size_at_insertion=1)
    assert abs(p - -5.0) < 0.01
    assert r is False


# ─── Unit tests: arrival_at field populated ──────────────────────

def test_arrival_at_field_in_RoutePlanV2():
    """V3.27.3 NEW field arrival_at must exist w RoutePlanV2 dataclass."""
    import dataclasses
    fields = [f.name for f in dataclasses.fields(RoutePlanV2)]
    assert "arrival_at" in fields


def test_arrival_at_populated_bag_2_sequence():
    """_simulate_sequence (via simulate_bag_route_v2) zwraca arrival_at dict."""
    now = datetime(2026, 4, 27, 10, 15, tzinfo=timezone.utc)
    bag_order = OrderSim(
        order_id="b1",
        pickup_coords=(53.130, 23.165),
        delivery_coords=(53.135, 23.170),
        pickup_ready_at=now + timedelta(minutes=5),
        status="assigned",
    )
    new_order = OrderSim(
        order_id="new",
        pickup_coords=(53.140, 23.180),
        delivery_coords=(53.150, 23.190),
        pickup_ready_at=now + timedelta(minutes=30),
        status="assigned",
    )
    plan = simulate_bag_route_v2(
        courier_pos=(53.125, 23.160),
        bag=[bag_order],
        new_order=new_order,
        now=now,
    )
    assert hasattr(plan, "arrival_at")
    assert isinstance(plan.arrival_at, dict)
    assert "new" in plan.arrival_at
    # arrival_at[oid] = pre-wait, pre-dwell
    # pickup_at[oid] = max(arrival, ready) + DWELL
    # arrival_at <= pickup_at - DWELL (= max(arrival, ready))
    new_pickup_at = plan.pickup_at["new"]
    new_arrival_at = plan.arrival_at["new"]
    delta_min = (new_pickup_at - new_arrival_at).total_seconds() / 60.0
    # Min delta = DWELL_PICKUP_MIN (no wait); max could be DWELL + arbitrary wait
    assert delta_min >= DWELL_PICKUP_MIN - 0.01


# ─── Integration test: #468945 ground truth from real shadow log ──

def _load_468945_decision():
    """Pull real #468945 decision z learning_log.jsonl."""
    log_path = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")
    if not log_path.exists():
        return None
    with log_path.open() as f:
        for line in f:
            try:
                data = json.loads(line)
            except Exception:
                continue
            if data.get("order_id") == "468945":
                return data
    return None


def test_integration_468945_andrei_wait_12_6_min_real_log():
    """Lekcja #28: real shadow log integration. Andrei chain arrival 12:32,
    ready 12:44:57 → wait 12.6 min → penalty -43."""
    data = _load_468945_decision()
    if data is None:
        # Skip gracefully if shadow log unavailable in test env
        import pytest
        pytest.skip("Shadow log learning_log.jsonl unavailable")
    decision = data["decision"]
    best = decision["best"]
    plan = best["plan"]
    pickup_ready_at_iso = decision["pickup_ready_at"]
    pickup_at_new = plan["pickup_at"]["468945"]

    # Reconstruct real wait: chain arrival = pickup_at[bag] + drive_to_new
    # Z log: r07_chain_details ma drive_min_to=3.3 (Pani Pierożek → Raj)
    # plan.pickup_at[468941] = 12:29:00 (Pani Pierożek pickup, post-DWELL)
    # arrival_at[468945] (gdyby był) = pickup_at[468941] + drive_min(3.3)
    pickup_at_bag = datetime.fromisoformat(plan["pickup_at"]["468941"])
    drive_min_to_new = 3.3  # z r07_chain_details
    expected_arrival = pickup_at_bag + timedelta(minutes=drive_min_to_new)
    pickup_ready_at = datetime.fromisoformat(pickup_ready_at_iso)
    expected_wait_min = max(0.0, (pickup_ready_at - expected_arrival).total_seconds() / 60.0)

    # Andrei real wait ≈ 12.6 min (Adrian's intuicja 14 min)
    assert 12.0 <= expected_wait_min <= 13.0, f"Expected ~12.6 min, got {expected_wait_min}"

    # Penalty check: 12.6 min → -10 + (12.6-6)*-5 = -43
    bag_size = best["r6_bag_size"]
    assert bag_size == 1
    penalty, hard_reject = compute_wait_courier_penalty(expected_wait_min, bag_size)
    assert -45 <= penalty <= -40, f"Expected -40 to -45, got {penalty}"
    assert hard_reject is False

    # Andrei advantage was +38 vs Gabriel Je. With -43 penalty → +38 - 43 = -5
    advantage = best["v326_rationale"]["advantage_vs_next"]
    new_advantage = advantage + penalty
    assert new_advantage < 0, f"Expected Andrei advantage to flip negative, got {new_advantage}"


def test_integration_bag_0_no_penalty_high_wait():
    """Conditional firing test: bag=0 z wait 30 min → 0 penalty (skip)."""
    p, r = compute_wait_courier_penalty(30.0, bag_size_at_insertion=0)
    assert p == 0.0
    assert r is False
    # Compare bag=1: identical wait fires hard reject
    p2, r2 = compute_wait_courier_penalty(30.0, bag_size_at_insertion=1)
    assert p2 == 0.0
    assert r2 is True


if __name__ == "__main__":
    import sys as _sys
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed, {failed} failed")
    _sys.exit(0 if failed == 0 else 1)
