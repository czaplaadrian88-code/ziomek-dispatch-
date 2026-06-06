"""FAIL-12 (audyt Ziomka 2026-06-03) — fail-OPEN gdy grafik padł a kurier pracuje.

Root-cause: gdy Google Sheet padnie/jest niepełny → cs.shift_end=None mimo że kurier
FIZYCZNIE pracuje. feasibility Gate 1 hard-rejectuje go (NO_ACTIVE_SHIFT, fail-CLOSED).
D2 ratuje tylko gdy CAŁY arkusz stale (>30min) ORAZ jest ON (default OFF). Precedens:
incident #471036 — 3 aktywnych kurierów odrzuconych v325_NO_ACTIVE_SHIFT.

FAIL-12: gdy shift_end=None ALE kurier ma TWARDY dowód pracy niezależny od grafiku
(len(bag)>0 LUB pos_source=="gps") → fail-OPEN (przepuść Gate 1). Sam BRAK GPS NIE
wystarcza (świadomy dyskryminator vs FAIL-07). R6 35min/SLA/post-shift dalej niżej.
Krok 1: pure pass-through + obserwowalność (metryka + log), BEZ kary scoringowej.

Standalone executable. Gate aktywny (ENABLE_V325_SCHEDULE_HARDENING default True). Weryfikuje:
1. fail12_flag_off_bag_still_rejects — flag OFF (default) + shift_end None + bag>0 → HARD REJECT (regression, zero zmiany prod)
2. fail12_off_gps_baseline — flag OFF + shift_end None + gps → HARD REJECT (baseline)
3. fail12_on_bag_failopen — flag ON + shift_end None + bag>0 → fail-OPEN (NIE NO_ACTIVE_SHIFT, fail12_schedule_failopen=True, signal=bag)
4. fail12_on_gps_failopen — flag ON + shift_end None + pusty bag + pos_source=gps → fail-OPEN (signal=gps)
5. fail12_on_blind_empty_still_rejects — flag ON + shift_end None + pusty bag + no_gps → HARD REJECT (brak dowodu pracy)
6. fail12_on_with_shift_inert — flag ON + shift_end OBECNY + bag>0 → inert (brak fail12 keys, normal in-shift)
7. fail12_d2_precedence — D2 ON+stale ma pierwszeństwo nad FAIL-12 (d2 path, brak fail12 keys)
8. fail12_no_v325_reject_reason — fail-OPEN → metrics.v325_reject_reason NIE ustawiony (falsy)
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


def _feas(shift_end=None, shift_start=None, bag_n=0, pos_source=None, stale=False):
    order = _mk_order()
    bag = [_mk_order(oid=f'b{i}', pickup_offset_min=5) for i in range(bag_n)]
    return check_feasibility_v2(
        courier_pos=_COURIER_POS, bag=bag, new_order=order,
        shift_end=shift_end, shift_start=shift_start,
        pickup_ready_at=order.pickup_ready_at,
        pos_source=pos_source,
        schedule_source_stale=stale,
    )


def test_fail12_flag_off_bag_still_rejects():
    """FAIL-12 OFF (default) → nawet bag>0 daje HARD REJECT NO_ACTIVE_SHIFT (zero zmiany prod)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = False
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = False
    verdict, reason, metrics, _ = _feas(shift_end=None, bag_n=2, pos_source="gps")
    assert verdict == "NO", f"expected NO, got {verdict}"
    assert "v325_NO_ACTIVE_SHIFT" in reason, f"got {reason!r}"
    assert metrics.get("v325_reject_reason") == "NO_ACTIVE_SHIFT"
    assert "fail12_schedule_failopen" not in metrics, "FAIL-12 OFF nie powinno ustawiać metryki"


def test_fail12_off_gps_baseline():
    """FAIL-12 OFF + gps → HARD REJECT NO_ACTIVE_SHIFT (baseline)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = False
    verdict, reason, metrics, _ = _feas(shift_end=None, bag_n=0, pos_source="gps")
    assert verdict == "NO"
    assert "v325_NO_ACTIVE_SHIFT" in reason
    assert "fail12_schedule_failopen" not in metrics


def test_fail12_on_bag_failopen():
    """FAIL-12 ON + shift_end None + bag>0 → fail-OPEN: NIE hard-reject, signal=bag."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = False
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = True
    verdict, reason, metrics, _ = _feas(shift_end=None, bag_n=1, pos_source="no_gps")
    assert "v325_NO_ACTIVE_SHIFT" not in reason, \
        f"bag>0 powinien ominąć hard-reject (sygnał pracy), got reason={reason!r}"
    assert metrics.get("fail12_schedule_failopen") is True, \
        f"oczekiwano fail12_schedule_failopen=True, got {metrics.get('fail12_schedule_failopen')}"
    assert metrics.get("fail12_signal") == "bag", f"got signal={metrics.get('fail12_signal')}"


def test_fail12_on_gps_failopen():
    """FAIL-12 ON + shift_end None + pusty bag + pos_source=gps → fail-OPEN, signal=gps."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = True
    verdict, reason, metrics, _ = _feas(shift_end=None, bag_n=0, pos_source="gps")
    assert "v325_NO_ACTIVE_SHIFT" not in reason, \
        f"świeży GPS powinien ominąć hard-reject, got reason={reason!r}"
    assert metrics.get("fail12_schedule_failopen") is True
    assert metrics.get("fail12_signal") == "gps", f"got signal={metrics.get('fail12_signal')}"


def test_fail12_on_blind_empty_still_rejects():
    """HARD safety: FAIL-12 ON ale pusty bag + brak świeżego GPS (no_gps) → nadal HARD REJECT."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = True
    verdict, reason, metrics, _ = _feas(shift_end=None, bag_n=0, pos_source="no_gps")
    assert verdict == "NO", f"pusty bag + no_gps powinno hard-reject, got {verdict}"
    assert "v325_NO_ACTIVE_SHIFT" in reason, f"got {reason!r}"
    assert metrics.get("v325_reject_reason") == "NO_ACTIVE_SHIFT"
    assert "fail12_schedule_failopen" not in metrics, "brak dowodu pracy → FAIL-12 NIE fail-open"


def test_fail12_on_with_shift_inert():
    """FAIL-12 ON + shift_end OBECNY (in-shift) + bag>0 → inert (else nie dotyczy), brak fail12 keys."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = True
    s_start, s_end = _shift(-1, 5)  # shift started 1h ago, ends in 5h (in-shift)
    verdict, reason, metrics, _ = _feas(shift_end=s_end, shift_start=s_start, bag_n=1, pos_source="gps")
    assert "fail12_schedule_failopen" not in metrics, "shift obecny → FAIL-12 nie ingeruje (shift_end!=None)"
    assert "v325_PICKUP_POST_SHIFT" not in reason and "v325_PRE_SHIFT_TOO_EARLY" not in reason, \
        f"in-shift nie powinno być v325 reject, got {reason!r}"


def test_fail12_d2_precedence():
    """D2 ON + stale ma pierwszeństwo (sprawdzane PRZED FAIL-12) → d2 path, brak fail12 keys."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = True
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = True
    _, reason, metrics, _ = _feas(shift_end=None, bag_n=1, pos_source="gps", stale=True)
    assert metrics.get("d2_stale_schedule_soft") is True, "D2 ON+stale powinno wygrać (pierwszy if)"
    assert "fail12_schedule_failopen" not in metrics, "D2 path → FAIL-12 nie odpala (elif)"
    assert "v325_NO_ACTIVE_SHIFT" not in reason


def test_fail12_no_v325_reject_reason():
    """fail-OPEN → metrics.v325_reject_reason NIE ustawiony (falsy)."""
    C.ENABLE_V325_SCHEDULE_HARDENING = True
    C.ENABLE_D2_STALE_SCHEDULE_SOFT = False
    C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = True
    _, _, metrics, _ = _feas(shift_end=None, bag_n=1, pos_source="no_gps")
    assert not metrics.get("v325_reject_reason"), \
        f"fail-OPEN nie powinno ustawiać v325_reject_reason, got {metrics.get('v325_reject_reason')}"


def main():
    _orig_v325 = C.ENABLE_V325_SCHEDULE_HARDENING
    _orig_d2 = C.ENABLE_D2_STALE_SCHEDULE_SOFT
    _orig_f12 = C.ENABLE_FAIL12_SCHEDULE_FAILOPEN
    tests = [
        ('fail12_flag_off_bag_still_rejects', test_fail12_flag_off_bag_still_rejects),
        ('fail12_off_gps_baseline', test_fail12_off_gps_baseline),
        ('fail12_on_bag_failopen', test_fail12_on_bag_failopen),
        ('fail12_on_gps_failopen', test_fail12_on_gps_failopen),
        ('fail12_on_blind_empty_still_rejects', test_fail12_on_blind_empty_still_rejects),
        ('fail12_on_with_shift_inert', test_fail12_on_with_shift_inert),
        ('fail12_d2_precedence', test_fail12_d2_precedence),
        ('fail12_no_v325_reject_reason', test_fail12_no_v325_reject_reason),
    ]
    print('=' * 60)
    print('FAIL-12 schedule fail-OPEN (audyt 2026-06-03) tests')
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
        C.ENABLE_FAIL12_SCHEDULE_FAILOPEN = _orig_f12
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
