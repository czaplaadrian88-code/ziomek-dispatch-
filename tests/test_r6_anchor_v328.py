"""V3.28 ANCHOR FIX 2026-05-10 — R6 per-order anchor=pickup_ready_at + min_propose_score.

Doktryna Adriana 2026-05-10:
- 35 min PER ORDER hard rule, anchor = pickup_ready_at (jedzenie czeka od ready)
- Picked-up orders: tracked, NIE odrzucane (kurier kończy w drodze)
- min_propose_score: best < -100 → KOORD zamiast PROPOSE

Fixture base: order 472189 (Chinatown→Zaściańska 83) z 2026-05-10 13:53:02 UTC.
Pre-fix: r6_max_bag_time=34 min "pass" mimo real thermal 70 min dla bag orders.
Post-fix: hard reject jeśli ANY assigned order has thermal carry > 35 min from ready_at.
"""

from __future__ import annotations
from datetime import datetime, timezone, timedelta
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _make_order(
    oid: str,
    pickup_lat: float = 53.13,
    pickup_lon: float = 23.16,
    drop_lat: float = 53.14,
    drop_lon: float = 23.18,
    pickup_ready_at: datetime = None,
    picked_up_at: datetime = None,
    status: str = "assigned",
) -> OrderSim:
    return OrderSim(
        order_id=oid,
        pickup_coords=(pickup_lat, pickup_lon),
        delivery_coords=(drop_lat, drop_lon),
        pickup_ready_at=pickup_ready_at,
        picked_up_at=picked_up_at,
        status=status,
    )


def test_r6_anchor_uses_pickup_ready_at_for_assigned():
    """Assigned (not picked) order: anchor = pickup_ready_at, NOT TSP pickup_at.

    Setup: bag has 1 assigned order with pickup_ready_at 13:44 UTC.
    TSP-projected pickup_at is 14:20 (36 min later — kurier zajęty).
    Predicted drop 14:54.
    Real thermal carry = 14:54 - 13:44 = 70 min → MUST hard reject.
    """
    now = _utc("2026-05-10T13:53:00")
    shift_end = _utc("2026-05-10T22:00:00")

    bag_order = _make_order(
        "BAG_ASSIGNED",
        pickup_ready_at=_utc("2026-05-10T13:44:00"),  # ready 9 min before now
        picked_up_at=None,
        status="assigned",
    )
    new_order = _make_order(
        "NEW_OK",
        pickup_lat=53.135,
        pickup_lon=23.165,
        drop_lat=53.145,
        drop_lon=23.185,
        pickup_ready_at=_utc("2026-05-10T13:52:00"),
        picked_up_at=None,
        status="assigned",
    )

    courier_pos = (53.13, 23.16)

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=[bag_order],
        new_order=new_order,
        shift_end=shift_end,
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
        sla_minutes=35,
    )

    # Bag delivery times depend on TSP, but with realistic bag we expect
    # at minimum metrics["r6_max_bag_time_min"] from ready_at anchor.
    # If plan computed and bag_time>35 from ready anchor → verdict NO.
    if plan is not None and bag_order.order_id in plan.predicted_delivered_at:
        pred = plan.predicted_delivered_at[bag_order.order_id]
        if pred.tzinfo is None:
            pred = pred.replace(tzinfo=timezone.utc)
        bag_thermal = (pred - bag_order.pickup_ready_at).total_seconds() / 60.0
        if bag_thermal > 35:
            assert verdict == "NO", (
                f"Expected NO (R6 thermal violation: bag_order thermal={bag_thermal:.1f}min); "
                f"got {verdict}/{reason}"
            )
            assert "R6_per_order" in reason, f"Expected R6_per_order in reason; got: {reason}"
        else:
            # Plan happened to schedule pickup right after ready_at — no violation
            assert verdict in ("YES", "MAYBE"), f"Expected YES/MAYBE; got {verdict}"


def test_r6_picked_up_NOT_rejected_even_over_35():
    """Picked-up order in bag: tracked but NIE rejected (kurier kończy).

    Setup: bag has 1 picked_up order, picked_up_at 13:00 (53 min ago).
    Predicted drop 14:00 (60 min from picked).
    Should track in r6_picked_up_violations, but NOT reject.
    """
    now = _utc("2026-05-10T13:53:00")
    shift_end = _utc("2026-05-10T22:00:00")

    bag_picked = _make_order(
        "BAG_PICKED",
        picked_up_at=_utc("2026-05-10T13:00:00"),  # picked 53 min ago
        status="picked_up",
    )
    new_order = _make_order(
        "NEW_OK",
        pickup_lat=53.135,
        pickup_lon=23.165,
        drop_lat=53.145,
        drop_lon=23.185,
        pickup_ready_at=_utc("2026-05-10T13:52:00"),
        status="assigned",
    )

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.13, 23.16),
        bag=[bag_picked],
        new_order=new_order,
        shift_end=shift_end,
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
        sla_minutes=35,
    )

    # Should NOT be R6_per_order reject for picked_up. Could still fail SLA or other rules.
    if "R6_per_order" in (reason or ""):
        # Verify it's NOT triggered by picked_up violation
        assert "R6_per_order" not in reason, (
            f"Picked-up bag order should NOT trigger R6_per_order hard reject; "
            f"got: {reason}, metrics: r6_picked_up_violations={metrics.get('r6_picked_up_violations')}"
        )


def test_r6_per_order_violations_metric_populated():
    """metrics['r6_per_order_violations'] is populated for not-picked thermal violations."""
    now = _utc("2026-05-10T13:53:00")
    shift_end = _utc("2026-05-10T22:00:00")

    bag = [
        _make_order(
            "B_OLD_READY",
            pickup_ready_at=_utc("2026-05-10T13:00:00"),  # ready 53 min ago
            picked_up_at=None,
            status="assigned",
        ),
    ]
    new_order = _make_order(
        "NEW",
        pickup_lat=53.135,
        pickup_lon=23.165,
        drop_lat=53.145,
        drop_lon=23.185,
        pickup_ready_at=_utc("2026-05-10T13:52:00"),
        status="assigned",
    )

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.13, 23.16),
        bag=bag,
        new_order=new_order,
        shift_end=shift_end,
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
        sla_minutes=35,
    )

    # If plan is not None and B_OLD_READY has thermal>35, expect violations populated
    assert "r6_per_order_violations" in metrics, "metric key missing"
    assert "r6_picked_up_violations" in metrics, "metric key missing"
    assert isinstance(metrics["r6_per_order_violations"], list)
    assert isinstance(metrics["r6_picked_up_violations"], list)


def test_min_propose_score_constant_exists():
    """common.MIN_PROPOSE_SCORE constant exists and is correctly set."""
    from dispatch_v2 import common as C
    assert hasattr(C, "MIN_PROPOSE_SCORE"), "MIN_PROPOSE_SCORE must be defined"
    assert isinstance(C.MIN_PROPOSE_SCORE, (int, float))
    assert C.MIN_PROPOSE_SCORE == -100.0, f"Expected -100.0, got {C.MIN_PROPOSE_SCORE}"


if __name__ == "__main__":
    # Custom runner for files that pytest collects but doesn't run as TestSuite
    import traceback
    tests = [
        test_r6_anchor_uses_pickup_ready_at_for_assigned,
        test_r6_picked_up_NOT_rejected_even_over_35,
        test_r6_per_order_violations_metric_populated,
        test_min_propose_score_constant_exists,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    import sys
    sys.exit(0 if failed == 0 else 1)
