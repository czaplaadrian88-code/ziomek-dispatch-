"""V3.28 ETAP 2 — pre_shift departure clamp tests.

Sprawdza że simulate_bag_route_v2 z earliest_departure > now wymusza start
planu od earliest_departure (shift_start case dla pre_shift kuriera). Plan
timestamps (pickup_at, predicted_delivered_at) bazują na earliest_departure
zamiast real now.

6 cases: clamp activates / clamp ignored when earlier / no clamp = now /
naive datetime normalized / pickup_at after clamp / delivered_at after clamp.

Manual stdlib runner — pytest available but matching project convention.
"""
import sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from datetime import datetime, timezone, timedelta
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim


def _mk_order(oid="471402", pickup=(53.13, 23.16), drop=(53.14, 23.18),
              status="new", pickup_ready_at=None):
    return OrderSim(
        order_id=oid,
        pickup_coords=pickup,
        delivery_coords=drop,
        status=status,
        pickup_ready_at=pickup_ready_at,
    )


def test_earliest_departure_shifts_now_when_later():
    """now=10:00 UTC, earliest_departure=11:00 UTC → simulator effective_now=11:00.
    Plan timestamps bazują na 11:00, NIE 10:00."""
    now = datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc)
    ed = datetime(2026, 5, 8, 11, 0, 0, tzinfo=timezone.utc)
    order = _mk_order()
    plan = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order,
        now=now, earliest_departure=ed,
    )
    # plan.pickup_at[oid] musi być >= 11:00 UTC (effective start)
    pa = plan.pickup_at.get(order.order_id) if plan.pickup_at else None
    assert pa is not None, f"plan.pickup_at brak dla {order.order_id}"
    assert pa >= ed, (
        f"pickup_at {pa.isoformat()} < earliest_departure {ed.isoformat()} "
        f"— clamp NIE odpalił"
    )


def test_earliest_departure_ignored_when_earlier():
    """now=11:30, earliest_departure=11:00 (przeszłość) → effective_now=11:30,
    earliest_departure ignorowany."""
    now = datetime(2026, 5, 8, 11, 30, 0, tzinfo=timezone.utc)
    ed = datetime(2026, 5, 8, 11, 0, 0, tzinfo=timezone.utc)  # earlier
    order = _mk_order()
    plan = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order,
        now=now, earliest_departure=ed,
    )
    pa = plan.pickup_at.get(order.order_id) if plan.pickup_at else None
    assert pa is not None
    # pickup_at musi być >= now (11:30), NIE >= ed (11:00) — ed ignorowany
    assert pa >= now, f"pickup_at {pa.isoformat()} < now {now.isoformat()}"


def test_no_earliest_departure_uses_now():
    """earliest_departure=None → backward compat (effective_now=now)."""
    now = datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc)
    order = _mk_order()
    plan_clamp = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order, now=now,
        earliest_departure=None,
    )
    plan_baseline = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order, now=now,
    )
    # Oba plany identyczne (clamp=None == brak param)
    pa_a = plan_clamp.pickup_at.get(order.order_id)
    pa_b = plan_baseline.pickup_at.get(order.order_id)
    assert pa_a == pa_b, f"clamp=None NIE backward compat: {pa_a} vs {pa_b}"


def test_naive_datetime_normalized():
    """earliest_departure naive (bez tzinfo) → traktowany jako UTC."""
    now = datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc)
    ed_naive = datetime(2026, 5, 8, 11, 0, 0)  # NO tzinfo
    order = _mk_order()
    plan = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order,
        now=now, earliest_departure=ed_naive,
    )
    pa = plan.pickup_at.get(order.order_id)
    assert pa is not None
    ed_utc = ed_naive.replace(tzinfo=timezone.utc)
    assert pa >= ed_utc, (
        f"naive ed nie znormalizowany jako UTC: pickup_at {pa} < ed {ed_utc}"
    )


def test_pickup_at_after_clamp_solo_order():
    """Solo order (bag pusty), now=10:00, ed=11:00 → pickup_at >= 11:00 (drive
    z courier_pos do pickup może dodać minuty, ale start nie wcześniej niż ed)."""
    now = datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc)
    ed = datetime(2026, 5, 8, 11, 0, 0, tzinfo=timezone.utc)
    order = _mk_order()
    plan = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order,
        now=now, earliest_departure=ed,
    )
    pa = plan.pickup_at.get(order.order_id)
    delta_from_ed_min = (pa - ed).total_seconds() / 60.0
    # pickup_at = ed + drive_min + dwell — musi być >= ed (clamp efekt)
    # i <= ed + 30 min (synthetic position w Białymstoku, drive max kilka minut)
    assert 0 <= delta_from_ed_min <= 60, (
        f"pickup_at delta_from_ed = {delta_from_ed_min:.1f} min "
        f"poza zakresem [0, 60] dla solo Białystok"
    )


def test_predicted_delivered_at_after_clamp():
    """End-to-end: clamp shifts predicted_delivered_at również, nie tylko pickup_at."""
    now = datetime(2026, 5, 8, 10, 0, 0, tzinfo=timezone.utc)
    ed = datetime(2026, 5, 8, 11, 0, 0, tzinfo=timezone.utc)
    order = _mk_order()
    plan_baseline = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order, now=now,
    )
    plan_clamped = simulate_bag_route_v2(
        courier_pos=(53.13, 23.17), bag=[], new_order=order,
        now=now, earliest_departure=ed,
    )
    da_baseline = plan_baseline.predicted_delivered_at.get(order.order_id)
    da_clamped = plan_clamped.predicted_delivered_at.get(order.order_id)
    assert da_baseline is not None and da_clamped is not None
    # delivery clamped musi być co najmniej godzinę później niż baseline
    delta_h = (da_clamped - da_baseline).total_seconds() / 3600.0
    assert 0.9 <= delta_h <= 1.1, (
        f"predicted_delivered_at delta {delta_h:.2f}h ≠ ~1h "
        f"(baseline={da_baseline} clamped={da_clamped})"
    )


# ----- runner -----

def main():
    tests = [
        ('earliest_departure_shifts_now_when_later',
         test_earliest_departure_shifts_now_when_later),
        ('earliest_departure_ignored_when_earlier',
         test_earliest_departure_ignored_when_earlier),
        ('no_earliest_departure_uses_now', test_no_earliest_departure_uses_now),
        ('naive_datetime_normalized', test_naive_datetime_normalized),
        ('pickup_at_after_clamp_solo_order', test_pickup_at_after_clamp_solo_order),
        ('predicted_delivered_at_after_clamp', test_predicted_delivered_at_after_clamp),
    ]
    print('=' * 60)
    print('V3.28 ETAP 2 — pre_shift departure clamp')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS {name}')
            passed += 1
        except AssertionError as e:
            print(f'  FAIL {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  FAIL {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
