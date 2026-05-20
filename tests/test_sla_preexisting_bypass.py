"""SLA pre-existing bypass tests — diagnoza 474863 Gabryś (2026-05-20).

Bug: feasibility_v2 odrzucał kuriera mając picked_up order którego carry-time
już przekroczył 35 min PRZED dodaniem nowego ordera (drive+dwell minimum >35
min vs current `now`). `plan.sla_violations` reject działał bez sprawdzenia
czy violation wynika z pre-existing stanu vs wpływu nowego ordera.

Test odtwarza dokładny capture Gabriela cid=179 dla oid=474863 z
`obj_replay_capture.jsonl` 2026-05-20 12:26:46.294 (gold tier, bag={474835
picked_up od 11:58, 474843 assigned, 474858 Goodboy assigned}, new=474863
Goodboy).

Pre-fix: VERDICT=NO `sla_violation (474835 +38.6min, over by 3.6)`.
Post-fix: VERDICT=MAYBE `ok_sla_fits` (pre-existing breach nie blokuje).
"""
import sys
import os
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

# Force F1+F2 ON like production (deterministic plan TSP front-loadu picked_up)
os.environ.setdefault("ENABLE_OBJ_R6_SOFT_DEADLINE", "1")
os.environ.setdefault("OBJ_R6_DEADLINE_PENALTY_COEFF", "100")
os.environ.setdefault("ENABLE_OBJ_SPAN_COST", "1")
os.environ.setdefault("OBJ_SPAN_COST_COEFF", "1.0")

from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2 import common as C


def _dt(s):
    d = datetime.fromisoformat(s)
    return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d


def _gabrys_474863_fixture():
    """Capture 12:26:46.294 — gold tier, bag w Rukola Kacz, picked_up 474835 28min."""
    courier_pos = (53.121879, 23.146168)
    now = _dt("2026-05-20T12:26:39+00:00")
    shift_start = _dt("2026-05-20T10:00:00+00:00")
    shift_end = _dt("2026-05-20T19:00:00+00:00")
    bag = [
        OrderSim(order_id="474835",
                 pickup_coords=(53.121879, 23.146168),
                 delivery_coords=(53.1237496, 23.1753324),
                 picked_up_at=_dt("2026-05-20T11:58:52+00:00"),
                 status="picked_up",
                 pickup_ready_at=_dt("2026-05-20T11:52:00+00:00")),
        OrderSim(order_id="474843",
                 pickup_coords=(53.121879, 23.146168),
                 delivery_coords=(53.12799709999999, 23.148256),
                 pickup_ready_at=_dt("2026-05-20T12:52:00+00:00")),
        OrderSim(order_id="474858",
                 pickup_coords=(53.115336, 23.14607),
                 delivery_coords=(53.13333429999999, 23.148017),
                 pickup_ready_at=_dt("2026-05-20T12:47:00+00:00")),
    ]
    new_order = OrderSim(order_id="474863",
                         pickup_coords=(53.115336, 23.14607),
                         delivery_coords=(53.1371447, 23.163277),
                         pickup_ready_at=_dt("2026-05-20T12:41:24+00:00"))
    return courier_pos, bag, new_order, now, shift_start, shift_end


def test_preexisting_breach_bypasses_sla_reject():
    """Gabryś case: picked_up 474835 carry 37+min (pre-existing). Fix → MAYBE."""
    C.ENABLE_SLA_PREEXISTING_BYPASS = True
    pos, bag, new, now, ss, se = _gabrys_474863_fixture()
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=pos, bag=bag, new_order=new,
        shift_end=se, shift_start=ss, now=now,
        pickup_ready_at=new.pickup_ready_at, courier_tier="gold",
    )
    assert verdict == "MAYBE", f"expected MAYBE post-fix, got {verdict} ({reason})"
    pe = metrics.get("sla_violations_pre_existing") or []
    blocking = metrics.get("sla_violations_blocking_count")
    assert any(v["order_id"] == "474835" for v in pe), \
        f"474835 powinien być pre-existing: {pe}"
    assert blocking == 0, f"blocking count powinno być 0, jest {blocking}"


def test_flag_off_legacy_reject():
    """Flag OFF → legacy behavior, NO reject jak przed fixem."""
    C.ENABLE_SLA_PREEXISTING_BYPASS = False
    try:
        pos, bag, new, now, ss, se = _gabrys_474863_fixture()
        verdict, reason, metrics, plan = check_feasibility_v2(
            courier_pos=pos, bag=bag, new_order=new,
            shift_end=se, shift_start=ss, now=now,
            pickup_ready_at=new.pickup_ready_at, courier_tier="gold",
        )
        assert verdict == "NO", f"flag OFF powinno reject, got {verdict}"
        assert "sla_violation" in reason and "474835" in reason
    finally:
        C.ENABLE_SLA_PREEXISTING_BYPASS = True


def test_new_order_over_sla_no_bypass():
    """New order sam ma carry >35 min → reject (nie bypass — to new-induced)."""
    C.ENABLE_SLA_PREEXISTING_BYPASS = True
    # Kurier solo, new order pickup_ready w odległej przyszłości, drop daleko.
    now = _dt("2026-05-20T12:00:00+00:00")
    pos = (53.121879, 23.146168)
    # pickup_ready 1h w przyszłości — czas pickup→drop bez problemu, ale total
    # od ready_at może być >35 jeśli long drive
    new = OrderSim(order_id="N1",
                   pickup_coords=(53.115336, 23.14607),
                   delivery_coords=(53.2, 23.5),  # daleko, ~25km
                   pickup_ready_at=_dt("2026-05-20T12:05:00+00:00"))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=pos, bag=[], new_order=new,
        shift_end=_dt("2026-05-20T19:00:00+00:00"),
        shift_start=_dt("2026-05-20T10:00:00+00:00"),
        now=now,
        pickup_ready_at=new.pickup_ready_at, courier_tier="gold",
    )
    if metrics.get("sla_violations_count", 0) > 0:
        assert verdict == "NO", \
            f"new-induced sla violation MUST reject; got {verdict}: {reason}"
        assert metrics.get("sla_violations_blocking_count", 0) > 0


def test_picked_up_detour_no_bypass():
    """Picked_up violation ALE plan robi new pickup PRZED dropem (detour) → NO.

    P3-D4 already handles this; ensures bypass nie nadpisuje delta logic.
    Konstruowany scenariusz: picked_up order daleko od courier_pos, nowy pickup
    bardzo blisko courier_pos. TSP wybierze new pickup pierwszy → detour.
    """
    C.ENABLE_SLA_PREEXISTING_BYPASS = True
    now = _dt("2026-05-20T12:00:00+00:00")
    pos = (53.115336, 23.14607)  # Goodboy
    bag = [
        OrderSim(order_id="OLD",
                 pickup_coords=(53.115336, 23.14607),  # już pickedup
                 delivery_coords=(53.25, 23.5),  # daleko, ~30km
                 picked_up_at=_dt("2026-05-20T11:30:00+00:00"),  # 30 min temu
                 status="picked_up",
                 pickup_ready_at=_dt("2026-05-20T11:25:00+00:00")),
    ]
    # New: ten sam Goodboy pickup, blisko Old picked_up location
    new = OrderSim(order_id="NEW",
                   pickup_coords=(53.115336, 23.14607),
                   delivery_coords=(53.12, 23.15),
                   pickup_ready_at=_dt("2026-05-20T12:05:00+00:00"))
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=pos, bag=bag, new_order=new,
        shift_end=_dt("2026-05-20T19:00:00+00:00"),
        shift_start=_dt("2026-05-20T10:00:00+00:00"),
        now=now,
        pickup_ready_at=new.pickup_ready_at, courier_tier="gold",
    )
    # Tu sla violation może być, ale P3-D4 / per-order R6 powinny dalej oceniać.
    # Test: jeśli plan dostarcza OLD AFTER new pickup → R6_picked_up_delta_reject
    # albo nasz blocking count > 0 (OLD drop > new pickup_at).
    if metrics.get("sla_violations_count", 0) > 0:
        pe = metrics.get("sla_violations_pre_existing") or []
        # OLD nie może być w pre_existing jeśli plan robi NEW pickup przed dropem OLD
        if plan and plan.pickup_at.get("NEW") and plan.predicted_delivered_at.get("OLD"):
            new_pu = plan.pickup_at["NEW"]
            old_drop = plan.predicted_delivered_at["OLD"]
            if new_pu.tzinfo is None:
                new_pu = new_pu.replace(tzinfo=timezone.utc)
            if old_drop.tzinfo is None:
                old_drop = old_drop.replace(tzinfo=timezone.utc)
            if old_drop > new_pu:
                # OLD dostarczony PO odebraniu NEW = detour, NIE pre-existing
                assert not any(v["order_id"] == "OLD" for v in pe), \
                    f"OLD nie powinien być pre-existing przy detour: {pe}"


if __name__ == "__main__":
    tests = [
        ("preexisting_breach_bypasses_sla_reject", test_preexisting_breach_bypasses_sla_reject),
        ("flag_off_legacy_reject", test_flag_off_legacy_reject),
        ("new_order_over_sla_no_bypass", test_new_order_over_sla_no_bypass),
        ("picked_up_detour_no_bypass", test_picked_up_detour_no_bypass),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL {name}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {name}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{passed+failed} PASS")
    sys.exit(0 if failed == 0 else 1)
