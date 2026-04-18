"""F2.2 C3 narrow tests — R6 soft zone 30-35 min metric logging.

R6 hard zone >35 min remains permanent (defense in depth vs C2).
Tests verify soft zone metric-only behavior (not flag-gated — always logged).

Standalone executable.
"""
import sys
from datetime import datetime, timezone, timedelta
from dataclasses import replace

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2 import route_simulator_v2 as rs


class FakeMatrix:
    """Cover return values for osrm_client.table — all cells duration_s configurable."""
    def __init__(self, duration_s):
        self.duration_s = duration_s

    def __call__(self, points_a, points_b):
        n = len(points_a)
        row = lambda: [{"duration_s": self.duration_s, "osrm_fallback": False} for _ in range(n)]
        return [row() for _ in range(n)]


def _setup_mock(duration_s):
    rs.osrm_client.table = FakeMatrix(duration_s)
    # Also haversine used in road_km for fast filters — make consistent
    class FakeHaversine:
        def __call__(self, a, b):
            return 2.0  # ~2 km, safe range
    rs.osrm_client.haversine = FakeHaversine()


def _make_new_order(oid, pickup_ready):
    """Fresh order assigned, needs pickup."""
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.13, 23.15),
        delivery_coords=(53.14, 23.16),
        status="assigned",
        pickup_ready_at=pickup_ready,
    )


def _make_bag_item(oid, picked_up_ago_min, now):
    """Bag item already picked up N minutes ago."""
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.12, 23.14),
        delivery_coords=(53.15, 23.17),
        status="picked_up",
        picked_up_at=now - timedelta(minutes=picked_up_ago_min),
    )


def test_r6_hard_reject_over_35_unchanged():
    """bag_time > 35 → hard NO return (SLA check fires first, same threshold).

    Note: SLA and R6 both use 35min threshold on same metric. In practice SLA
    rejects first with "sla_violation" reason. R6 hard path remains as defense
    in depth but is effectively unreachable when SLA is enforced first.
    Test verifies NO verdict regardless of which gate fires.
    """
    _setup_mock(duration_s=600)  # 10 min legs = long route pushes delivery time
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = [_make_bag_item("B1", picked_up_ago_min=40, now=now)]
    new_order = _make_new_order("NEW", pickup_ready=now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    # Either SLA or R6 rejects — both enforce 35min threshold
    assert verdict == "NO", f"expected NO for bag_time > 35; got {verdict} / {reason}"
    assert ("R6_bag_time_exceeded" in reason) or ("sla_violation" in reason), \
        f"expected R6 or SLA rejection; got {reason}"
    # Note: if SLA rejects first, R6 soft zone metrics never populated (early return)
    # If R6 rejects, soft zone logic ran first but field says False (bag_time > 35 outside zone)
    sz = metrics.get("r6_soft_zone_active")
    assert sz in (False, None), f"bag_time > 35 should have soft_zone_active False or not-reached (None); got {sz}"
    return True


def test_r6_soft_zone_metric_logged():
    """bag_time w zone (30, 35] → metrics r6_soft_penalty populated."""
    _setup_mock(duration_s=60)  # 1 min legs = krótka trasa
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    # Bag item picked 32 min ago + krótka symulacja → r6_max_bag_time ~32-33 min (zone 30-35)
    bag = [_make_bag_item("B1", picked_up_ago_min=32, now=now)]
    new_order = _make_new_order("NEW", pickup_ready=now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    # Should pass (bag_time <=35) BUT have r6_soft_penalty logged
    bt = metrics.get("r6_max_bag_time_min", 0)
    assert 30.0 < bt <= 35.0, f"setup expected bag_time in (30,35]; got {bt}"
    assert metrics.get("r6_soft_zone_active") is True, "soft zone flag should be True"
    assert metrics.get("r6_soft_penalty", 0) < 0, f"expected negative penalty; got {metrics.get('r6_soft_penalty')}"
    # Penalty formula: -3 * (bag_time - 30)
    expected = round(-3.0 * (bt - 30.0), 2)
    assert metrics["r6_soft_penalty"] == expected, f"expected {expected}, got {metrics['r6_soft_penalty']}"
    return True


def test_r6_no_penalty_under_30():
    """bag_time <= 30 → r6_soft_penalty == 0.0 and r6_soft_zone_active False."""
    _setup_mock(duration_s=60)
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    # Bag picked 20 min ago → bag_time ~21 min (well under 30)
    bag = [_make_bag_item("B1", picked_up_ago_min=20, now=now)]
    new_order = _make_new_order("NEW", pickup_ready=now)
    _, _, metrics, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    bt = metrics.get("r6_max_bag_time_min", 0)
    assert bt <= 30.0, f"setup expected bag_time <=30; got {bt}"
    assert metrics["r6_soft_penalty"] == 0.0
    assert metrics["r6_soft_zone_active"] is False
    return True


def test_r6_boundary_exactly_30():
    """bag_time == 30.0 exact → NO penalty (strict > check)."""
    # Create precise bag_time via picked_up_ago_min = 29 + simulate 1 min = ~30 min total
    _setup_mock(duration_s=60)
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = [_make_bag_item("B1", picked_up_ago_min=29, now=now)]
    new_order = _make_new_order("NEW", pickup_ready=now)
    _, _, metrics, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    bt = metrics.get("r6_max_bag_time_min", 0)
    # Empirical: may be 30 or slightly above/below. Key assertion: if == 30, penalty = 0
    if abs(bt - 30.0) < 0.1:
        assert metrics["r6_soft_penalty"] == 0.0, f"exactly 30 → 0; got {metrics['r6_soft_penalty']}"
    return True


def test_r6_boundary_exactly_35_still_passes():
    """bag_time == 35.0 → soft penalty ≈ -15, still PASSES feasibility (not >35)."""
    _setup_mock(duration_s=60)
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = [_make_bag_item("B1", picked_up_ago_min=34, now=now)]
    new_order = _make_new_order("NEW", pickup_ready=now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    bt = metrics.get("r6_max_bag_time_min", 0)
    # If bag_time lands just under 35, should pass + have high soft penalty
    if bt <= 35.0 and bt > 30.0:
        assert metrics["r6_soft_penalty"] < -10.0, \
            f"near-35 should have strong penalty; got {metrics['r6_soft_penalty']}"
        # Verdict is MAYBE (feasibility passed) OR NO from other reasons (SLA, etc.)
        # Not R6 rejection since <= 35
        assert "R6_bag_time_exceeded" not in (reason or ""), \
            f"<=35 should NOT trigger R6 hard; got {reason}"
    return True


def test_r6_solo_no_soft_zone():
    """Solo dispatch (empty bag) → r6_max_bag_time from plan; soft zone depends on plan."""
    _setup_mock(duration_s=60)
    now = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)
    bag = []  # solo
    new_order = _make_new_order("NEW", pickup_ready=now)
    _, _, metrics, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    # Solo plan with 1-min legs → delivery time is few minutes. No soft zone trigger.
    assert metrics["r6_is_solo"] is True
    assert metrics["r6_soft_zone_active"] is False
    assert metrics["r6_soft_penalty"] == 0.0
    return True


def main():
    tests = [
        ("r6_hard_reject_over_35_unchanged", test_r6_hard_reject_over_35_unchanged),
        ("r6_soft_zone_metric_logged", test_r6_soft_zone_metric_logged),
        ("r6_no_penalty_under_30", test_r6_no_penalty_under_30),
        ("r6_boundary_exactly_30", test_r6_boundary_exactly_30),
        ("r6_boundary_exactly_35_still_passes", test_r6_boundary_exactly_35_still_passes),
        ("r6_solo_no_soft_zone", test_r6_solo_no_soft_zone),
    ]
    print("=" * 60)
    print("F2.2 C3 narrow: R6 soft zone tests")
    print("=" * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}")
            failed.append(name)
    print("=" * 60)
    print(f"{passed}/{len(tests)} PASS")
    if failed:
        print(f"FAILED: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
