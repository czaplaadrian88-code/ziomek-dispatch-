"""D2 (audyt 2026-05-28) — soft-degrade zamiast BRAK KANDYDATÓW gdy grafik STALE.

Root-cause: load_schedule() zwraca {} (awaria pliku) → dispatchable_fleet pomija
shift mapping → cs.shift_end None → feasibility Gate 1 hard-rejectuje CAŁĄ flotę
(NO_ACTIVE_SHIFT) → BRAK KANDYDATÓW z powodu awarii, nie realnej niedostępności.
D2: gdy grafik wykryty jako STALE (schedule_source_stale, ten sam 30min próg co
shift_notifications.worker), Gate 1 soft-degraduje (penalty -75) zamiast hard-reject.

Standalone executable. Gate aktywny (ENABLE_V325_SCHEDULE_HARDENING default True). Weryfikuje:
1. d2_flag_off_stale_still_rejects — D2 OFF (default) + shift_end None + stale → HARD REJECT NO_ACTIVE_SHIFT (regression, zero zmiany prod)
2. d2_off_fresh_baseline — D2 OFF + shift_end None + fresh → HARD REJECT NO_ACTIVE_SHIFT (baseline)
3. d2_on_stale_soft_degrades — D2 ON + shift_end None + stale → soft-degrade (NIE NO_ACTIVE_SHIFT, d2_stale_schedule_soft=True)
4. d2_on_stale_penalty_magnitude — D2 ON + stale → metrics.d2_soft_penalty == D2_STALE_SCHEDULE_SOFT_PENALTY (-75)
5. d2_on_fresh_still_rejects — D2 ON + shift_end None + fresh → HARD REJECT NO_ACTIVE_SHIFT (HARD safety: D2 softens TYLKO stale)
6. d2_on_with_shift_inert — D2 ON + stale + shift_end OBECNY → D2 inert (else branch, brak d2_soft_penalty), normal in-shift
7. d2_soft_no_v325_reject_reason — D2 ON + stale → metrics.v325_reject_reason NIE ustawiony (falsy)
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common as C
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


_COURIER_POS = (53.13, 23.16)


def _mk_order(oid='1000', pickup_offset_min=10):
    """Mock OrderSim z pickup_ready_at = now + offset (haversine fallback, zero network)."""
    pra = datetime.now(timezone.utc) + timedelta(minutes=pickup_offset_min)
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=pra,
    )


def _shift(start_offset_h, end_offset_h):
    n = datetime.now(timezone.utc)
    return n + timedelta(hours=start_offset_h), n + timedelta(hours=end_offset_h)


def _feas(stale, shift_end=None, shift_start=None):
    order = _mk_order()
    return check_feasibility_v2(
        courier_pos=_COURIER_POS, bag=[], new_order=order,
        shift_end=shift_end, shift_start=shift_start,
        pickup_ready_at=order.pickup_ready_at,
        schedule_source_stale=stale,
    )


def test_d2_flag_off_stale_still_rejects():
    """D2 OFF (default) → nawet stale grafik daje HARD REJECT NO_ACTIVE_SHIFT (zero zmiany prod)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = False
    verdict, reason, metrics, _ = _feas(stale=True, shift_end=None)
    assert verdict == "NO", f"expected NO, got {verdict}"
    assert "v325_NO_ACTIVE_SHIFT" in reason, f"got {reason!r}"
    assert metrics.get("v325_reject_reason") == "NO_ACTIVE_SHIFT"
    assert "d2_soft_penalty" not in metrics, "D2 OFF nie powinno ustawiać d2_soft_penalty"


def test_d2_off_fresh_baseline():
    """D2 OFF + fresh grafik → HARD REJECT NO_ACTIVE_SHIFT (baseline, identyczny jak stale gdy OFF)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = False
    verdict, reason, metrics, _ = _feas(stale=False, shift_end=None)
    assert verdict == "NO"
    assert "v325_NO_ACTIVE_SHIFT" in reason
    assert "d2_soft_penalty" not in metrics


def test_d2_on_stale_soft_degrades():
    """D2 ON + shift_end None + stale → soft-degrade: NIE hard-reject NO_ACTIVE_SHIFT."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = True
    verdict, reason, metrics, _ = _feas(stale=True, shift_end=None)
    assert "v325_NO_ACTIVE_SHIFT" not in reason, \
        f"D2 ON+stale powinno ominąć hard-reject, got reason={reason!r}"
    assert metrics.get("d2_stale_schedule_soft") is True, \
        f"oczekiwano d2_stale_schedule_soft=True, got {metrics.get('d2_stale_schedule_soft')}"


def test_d2_on_stale_penalty_magnitude():
    """D2 ON + stale → metrics.d2_soft_penalty == D2_STALE_SCHEDULE_SOFT_PENALTY (-75)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = True
    _, _, metrics, _ = _feas(stale=True, shift_end=None)
    assert metrics.get("d2_soft_penalty") == C.D2_STALE_SCHEDULE_SOFT_PENALTY, \
        f"got {metrics.get('d2_soft_penalty')} vs {C.D2_STALE_SCHEDULE_SOFT_PENALTY}"
    assert abs(C.D2_STALE_SCHEDULE_SOFT_PENALTY - (-75.0)) < 1e-9, \
        f"default penalty powinien być -75, got {C.D2_STALE_SCHEDULE_SOFT_PENALTY}"


def test_d2_on_fresh_still_rejects():
    """HARD safety: D2 ON ale grafik ŚWIEŻY (realny brak shiftu) → nadal HARD REJECT NO_ACTIVE_SHIFT."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = True
    verdict, reason, metrics, _ = _feas(stale=False, shift_end=None)
    assert verdict == "NO", f"fresh+no-shift powinno hard-reject, got {verdict}"
    assert "v325_NO_ACTIVE_SHIFT" in reason, f"got {reason!r}"
    assert metrics.get("v325_reject_reason") == "NO_ACTIVE_SHIFT"
    assert "d2_soft_penalty" not in metrics, "fresh → D2 NIE soft-degraduje"


def test_d2_on_with_shift_inert():
    """D2 ON + stale ale shift_end OBECNY (in-shift) → D2 inert (else branch), normal path, brak d2 keys."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = True
    s_start, s_end = _shift(-1, 5)  # shift started 1h ago, ends in 5h (in-shift, pickup w 10 min)
    verdict, reason, metrics, _ = _feas(stale=True, shift_end=s_end, shift_start=s_start)
    assert "d2_soft_penalty" not in metrics, "shift obecny → D2 nie ingeruje"
    assert "d2_stale_schedule_soft" not in metrics
    assert "v325_PICKUP_POST_SHIFT" not in reason and "v325_PRE_SHIFT_TOO_EARLY" not in reason, \
        f"in-shift nie powinno być v325 reject, got {reason!r}"


def test_d2_soft_no_v325_reject_reason():
    """D2 ON + stale soft-degrade → metrics.v325_reject_reason NIE ustawiony (falsy)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = True
    _, _, metrics, _ = _feas(stale=True, shift_end=None)
    assert not metrics.get("v325_reject_reason"), \
        f"soft-degrade nie powinno ustawiać v325_reject_reason, got {metrics.get('v325_reject_reason')}"


def main():
    _orig_v325 = C.ENABLE_V325_SCHEDULE_HARDENING
    _orig_d2 = C.ENABLE_D2_STALE_SCHEDULE_SOFT
    tests = [
        ('d2_flag_off_stale_still_rejects', test_d2_flag_off_stale_still_rejects),
        ('d2_off_fresh_baseline', test_d2_off_fresh_baseline),
        ('d2_on_stale_soft_degrades', test_d2_on_stale_soft_degrades),
        ('d2_on_stale_penalty_magnitude', test_d2_on_stale_penalty_magnitude),
        ('d2_on_fresh_still_rejects', test_d2_on_fresh_still_rejects),
        ('d2_on_with_shift_inert', test_d2_on_with_shift_inert),
        ('d2_soft_no_v325_reject_reason', test_d2_soft_no_v325_reject_reason),
    ]
    print('=' * 60)
    print('D2 stale-schedule soft-degrade (audyt 2026-05-28) tests')
    print('=' * 60)
    passed = 0
    failed = []
    try:
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
    finally:
        C.ENABLE_V325_SCHEDULE_HARDENING = _orig_v325
        C.ENABLE_D2_STALE_SCHEDULE_SOFT = _orig_d2
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
