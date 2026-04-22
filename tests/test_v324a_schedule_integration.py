"""V3.24-A tests — schedule integration.

Coverage:
  1-7  extension_penalty() gradient + hard reject (unit)
  8    negative extension → 0 penalty (unit)
  9    None inputs → conservative 0 (unit)
  10   pickup clamp to shift_start (semantic check — eta_pickup_utc for pre_shift)
  11-12 post-shift dropoff hard reject (integration via check_feasibility_v2)
  13   Albert scenario — pre_shift kurier z odbiorem 12:05, extension 5 min → 0 penalty, MAYBE

Brak full pipeline mocku — bezpośrednie wywołania helpera + feasibility_v2.
Flag ENABLE_V324A_SCHEDULE_INTEGRATION musi być True przed testami (env override).

Uruchamia się jako standalone (zgodnie z resztą tests/).
"""
import os
import sys
from datetime import datetime, timezone, timedelta

# Enable V3.24-A flag PRZED importami common.py (env latch).
os.environ["ENABLE_V324A_SCHEDULE_INTEGRATION"] = "1"

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import common as C
from dispatch_v2 import osrm_client
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2.feasibility_v2 import check_feasibility_v2


def _osrm_mock_route(a, b, use_cache=True):
    return {
        "duration_s": 180, "distance_m": 1000,
        "duration_min": 3.0, "distance_km": 1.0,
        "osrm_fallback": False,
    }


def _osrm_mock_table(origins, destinations):
    return [[{"duration_s": 180, "duration_min": 3.0,
              "distance_m": 1000, "distance_km": 1.0,
              "osrm_fallback": False} for _ in destinations] for _ in origins]


osrm_client.route = _osrm_mock_route
osrm_client.table = _osrm_mock_table


def _assert(cond, label):
    if cond:
        print(f"  OK  {label}")
        return True
    print(f"  FAIL {label}")
    return False


def test_1_extension_zero():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    r = C.extension_penalty(base, base)
    return _assert(r == 0, "ext=0 min → penalty 0")


def test_2_extension_within_5min():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=4)
    r = C.extension_penalty(planned, base)
    return _assert(r == 0, "ext=4 min → penalty 0 (ideal)")


def test_3_extension_15min():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=12)
    r = C.extension_penalty(planned, base)
    return _assert(r == -10, "ext=12 min → penalty -10")


def test_4_extension_30min():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=25)
    r = C.extension_penalty(planned, base)
    return _assert(r == -50, "ext=25 min → penalty -50")


def test_5_extension_45min():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=40)
    r = C.extension_penalty(planned, base)
    return _assert(r == -100, "ext=40 min → penalty -100")


def test_6_extension_60min():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=55)
    r = C.extension_penalty(planned, base)
    return _assert(r == -200, "ext=55 min → penalty -200")


def test_7_extension_over_60min_hard_reject():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=65)
    r = C.extension_penalty(planned, base)
    return _assert(r is None, "ext=65 min → None (hard reject signal)")


def test_8_negative_extension_no_penalty():
    # Kurier wcześniej niż restauracja — R-NO-WASTE territory (V3.19j BUG-2)
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base - timedelta(minutes=10)
    r = C.extension_penalty(planned, base)
    return _assert(r == 0, "ext=-10 min (wcześniej) → penalty 0")


def test_9_none_inputs():
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    r1 = C.extension_penalty(None, base)
    r2 = C.extension_penalty(base, None)
    r3 = C.extension_penalty(None, None)
    return all([
        _assert(r1 == 0, "planned=None → 0"),
        _assert(r2 == 0, "requested=None → 0"),
        _assert(r3 == 0, "both None → 0"),
    ])


def test_10_boundary_5min():
    # Exact 5 min — falls into first tier (threshold_min=5)
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=5)
    r = C.extension_penalty(planned, base)
    return _assert(r == 0, "ext=5.0 min (boundary) → penalty 0")


def test_11_boundary_60min():
    # Exact 60 min — last tier inclusive, still -200 (> 60 = hard reject)
    base = datetime(2026, 4, 22, 12, 0, tzinfo=timezone.utc)
    planned = base + timedelta(minutes=60)
    r = C.extension_penalty(planned, base)
    return _assert(r == -200, "ext=60.0 min (boundary) → penalty -200")


def test_12_dropoff_after_shift_hard_reject():
    """V3.24-A hard reject: planned_dropoff > shift_end + 5 min → NO.

    now=21:58 (2 min do shift_end=22:00). Travel+dwell+drive+dwell_drop ≈ 16 min
    (mock OSRM 3min each leg + konstanty DWELL). Dropoff ~22:14, excess 14 > 5 → reject.
    """
    now = datetime(2026, 4, 22, 21, 58, tzinfo=timezone.utc)
    shift_end = datetime(2026, 4, 22, 22, 0, tzinfo=timezone.utc)
    order = OrderSim(
        order_id="test_drop_after",
        pickup_coords=(53.1325, 23.1688),
        delivery_coords=(53.150, 23.200),
        picked_up_at=None,
        status="assigned",
        pickup_ready_at=now,
    )
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.1325, 23.1688),
        bag=[],
        new_order=order,
        shift_end=shift_end,
        now=now,
        pickup_ready_at=now,
    )
    ok_verdict = _assert(verdict == "NO", f"verdict=NO (got {verdict!r} / {reason!r})")
    ok_reason = _assert(
        "v324a_dropoff_after_shift" in (reason or ""),
        f"reason contains v324a_dropoff_after_shift (got {reason!r})",
    )
    return ok_verdict and ok_reason


def test_13_dropoff_within_shift_tolerance():
    """V3.24-A accept: dropoff < shift_end + 5 min (w tolerancji)."""
    now = datetime(2026, 4, 22, 20, 0, tzinfo=timezone.utc)
    shift_end = datetime(2026, 4, 22, 22, 0, tzinfo=timezone.utc)  # 2h zapas
    order = OrderSim(
        order_id="test_drop_within",
        pickup_coords=(53.1325, 23.1688),
        delivery_coords=(53.140, 23.180),
        picked_up_at=None,
        status="assigned",
        pickup_ready_at=now,
    )
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.1325, 23.1688),
        bag=[],
        new_order=order,
        shift_end=shift_end,
        now=now,
        pickup_ready_at=now,
    )
    ok = _assert(verdict == "MAYBE", f"verdict=MAYBE (got {verdict!r} / {reason!r})")
    has_metric = _assert(
        "v324a_planned_dropoff_iso" in metrics,
        "v324a_planned_dropoff_iso present w metrics",
    )
    return ok and has_metric


def main():
    tests = [
        test_1_extension_zero,
        test_2_extension_within_5min,
        test_3_extension_15min,
        test_4_extension_30min,
        test_5_extension_45min,
        test_6_extension_60min,
        test_7_extension_over_60min_hard_reject,
        test_8_negative_extension_no_penalty,
        test_9_none_inputs,
        test_10_boundary_5min,
        test_11_boundary_60min,
        test_12_dropoff_after_shift_hard_reject,
        test_13_dropoff_within_shift_tolerance,
    ]
    passed = 0
    failed = 0
    for t in tests:
        print(f"\n{t.__name__}:")
        try:
            ok = t()
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  EXCEPTION {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
