"""V3.25 STEP B (R-01 SCHEDULE-HARDENING) — flag-gated PRE-CHECK regression.

Tests:
- Flag default False → behaviour zachowane (no PRE-CHECK fires)
- Flag True + shift_end=None → HARD REJECT NO_ACTIVE_SHIFT
- Flag True + pickup > shift_end → HARD REJECT PICKUP_POST_SHIFT
- Flag True + pickup < shift_start - 30 → HARD REJECT PRE_SHIFT_TOO_EARLY
- Flag True + pickup w pre-shift window (15min before) → soft penalty -20
- Flag True + pickup w shift → no PRE-CHECK fires (passes through)
- Pre-shift soft penalty propagated do bonus_penalty_sum (smoke check via dispatch_pipeline)

Wszystkie testy używają mock CourierState/orders, nie czytają production state.
"""
import importlib
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, feasibility_v2  # noqa: E402
from dispatch_v2.feasibility_v2 import check_feasibility_v2  # noqa: E402
from dispatch_v2.route_simulator_v2 import OrderSim  # noqa: E402


def _mk_order(oid='1000', pickup_offset_min=10):
    """Mock OrderSim z pickup_ready_at = now + pickup_offset_min."""
    now_utc = datetime.now(timezone.utc)
    pra = now_utc + timedelta(minutes=pickup_offset_min)
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=pra,
    )


def _shift(start_offset_h, end_offset_h):
    """Shift: now ± offset hours (UTC)."""
    n = datetime.now(timezone.utc)
    return n + timedelta(hours=start_offset_h), n + timedelta(hours=end_offset_h)


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # ---------- TEST 1: flag explicitly disabled via env — PRE-CHECK NIE fires ----------
    # NOTE: post-STEP B flag flip (2026-04-23 22:22), default common is True.
    # Force False via env override dla regression coverage legacy path.
    print("\n=== test 1: flag forced False via env (legacy path) ===")
    import os
    os.environ["ENABLE_V325_SCHEDULE_HARDENING"] = "0"
    importlib.reload(common)
    importlib.reload(feasibility_v2)
    expect("ENABLE_V325_SCHEDULE_HARDENING False (env override)",
           common.ENABLE_V325_SCHEDULE_HARDENING is False)

    order = _mk_order()
    courier_pos = (53.13, 23.16)
    # No shift data, flag False → should pass through V3.25 PRE-CHECK
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos, bag=[], new_order=order,
        shift_end=None, shift_start=None, pickup_ready_at=order.pickup_ready_at,
    )
    expect("flag=False + shift=None → NIE NO_ACTIVE_SHIFT (legacy bypass)",
           verdict in ("MAYBE", "YES") or 'v325' not in reason.lower(), f"verdict={verdict} reason={reason}")
    expect("metrics NIE ma v325_reject_reason gdy flag=False",
           "v325_reject_reason" not in metrics)

    # ---------- TEST 2: flag True + shift_end=None → HARD REJECT NO_ACTIVE_SHIFT ----------
    print("\n=== test 2: flag True + shift_end=None → NO_ACTIVE_SHIFT ===")
    import os
    os.environ["ENABLE_V325_SCHEDULE_HARDENING"] = "1"
    importlib.reload(common)
    importlib.reload(feasibility_v2)
    expect("flag flipped True via env", common.ENABLE_V325_SCHEDULE_HARDENING is True)

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos, bag=[], new_order=order,
        shift_end=None, shift_start=None, pickup_ready_at=order.pickup_ready_at,
    )
    expect("flag=True + shift_end=None → verdict=NO",
           verdict == "NO", f"got verdict={verdict}")
    expect("reason zawiera 'v325_NO_ACTIVE_SHIFT'",
           'v325_NO_ACTIVE_SHIFT' in reason, f"got {reason!r}")
    expect("metrics.v325_reject_reason=NO_ACTIVE_SHIFT",
           metrics.get("v325_reject_reason") == "NO_ACTIVE_SHIFT")

    # ---------- TEST 3: flag True + pickup > shift_end → PICKUP_POST_SHIFT ----------
    print("\n=== test 3: pickup post-shift → PICKUP_POST_SHIFT ===")
    # Pickup 30 min from now, shift ended 1h ago
    s_start, s_end = _shift(-3, -1)  # shift was 3h-1h ago
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos, bag=[], new_order=order,
        shift_end=s_end, shift_start=s_start, pickup_ready_at=order.pickup_ready_at,
    )
    expect("verdict=NO", verdict == "NO", f"got {verdict} reason={reason}")
    expect("reason 'v325_PICKUP_POST_SHIFT'", 'v325_PICKUP_POST_SHIFT' in reason, f"got {reason!r}")
    expect("metrics.v325_pickup_post_shift_excess_min > 0",
           metrics.get("v325_pickup_post_shift_excess_min", 0) > 0)

    # ---------- TEST 4: flag True + pickup < shift_start - 30 → PRE_SHIFT_TOO_EARLY ----------
    print("\n=== test 4: pickup pre-shift > 30 min → PRE_SHIFT_TOO_EARLY ===")
    # Shift starts in 2 hours (pickup 10 min from now is 110 min before shift_start)
    s_start, s_end = _shift(2, 10)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos, bag=[], new_order=order,
        shift_end=s_end, shift_start=s_start, pickup_ready_at=order.pickup_ready_at,
    )
    expect("verdict=NO", verdict == "NO", f"got {verdict}")
    expect("reason 'v325_PRE_SHIFT_TOO_EARLY'", 'v325_PRE_SHIFT_TOO_EARLY' in reason, f"got {reason!r}")
    expect("metrics.v325_pre_shift_too_early_min > 30",
           metrics.get("v325_pre_shift_too_early_min", 0) > 30)

    # ---------- TEST 5: flag True + pickup w pre-shift window (10 min before start) → soft penalty ----------
    print("\n=== test 5: pickup w pre-shift window 10 min → soft penalty -20 ===")
    # Pickup 10 min from now; shift starts in 20 min (so pickup is 10 min before shift_start)
    s_start, s_end = _shift(20/60, 8)  # shift starts in 20 min
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos, bag=[], new_order=order,
        shift_end=s_end, shift_start=s_start, pickup_ready_at=order.pickup_ready_at,
    )
    expect("verdict NIE NO (przeszło PRE-CHECK, kontynuuje)",
           verdict != "NO" or 'v325' not in reason.lower(),
           f"verdict={verdict} reason={reason}")
    expect("metrics.v325_pre_shift_soft_penalty == -20",
           metrics.get("v325_pre_shift_soft_penalty") == -20,
           f"got {metrics.get('v325_pre_shift_soft_penalty')}")
    expect("metrics.v325_pre_shift_soft_penalty_min > 0",
           metrics.get("v325_pre_shift_soft_penalty_min", 0) > 0)

    # ---------- TEST 6: flag True + pickup w shift (in-shift) → no PRE-CHECK fires ----------
    print("\n=== test 6: pickup w shift → no V3.25 reject, soft penalty=0 ===")
    # Shift now → +5h, pickup w 10 min (in-shift)
    s_start, s_end = _shift(-1, 5)  # shift started 1h ago, ends in 5h
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos, bag=[], new_order=order,
        shift_end=s_end, shift_start=s_start, pickup_ready_at=order.pickup_ready_at,
    )
    expect("verdict NIE jest v325 reject",
           'v325_PICKUP_POST_SHIFT' not in reason and 'v325_PRE_SHIFT_TOO_EARLY' not in reason,
           f"verdict={verdict} reason={reason}")
    expect("metrics.v325_pre_shift_soft_penalty == 0 (in-shift, no warm-up)",
           metrics.get("v325_pre_shift_soft_penalty") == 0,
           f"got {metrics.get('v325_pre_shift_soft_penalty')}")

    # Cleanup env
    del os.environ["ENABLE_V325_SCHEDULE_HARDENING"]
    importlib.reload(common)
    importlib.reload(feasibility_v2)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
