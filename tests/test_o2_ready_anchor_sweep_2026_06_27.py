"""O2 RE-SEQ Faza 1 (2026-06-27) — testy ON≠OFF + cap-Z + parytet overage + byte-id OFF.

Flaga ENABLE_O2_READY_ANCHOR_SWEEP (master, ETAP4, default OFF). Faza 1 = ready-anchor +
overage objektyw (czas_late = Faza 2, brak deadline na OrderSim). Pokrywa:
  - _compute_o2_metrics: overage (Σ max(0, age−cap)) + max_carried_age (po niesionych),
    parytet z bundle_calib._max_carried_age; fail-closed None.
  - _select_best_with_tie_breaker: OFF → legacy (sla_violations); ON → o2_score; cap-Z filtr.
  - byte-identyczność OFF (flaga domyślna nie zmienia selekcji).
"""
from datetime import datetime, timezone

import dispatch_v2.common as C
from dispatch_v2 import route_simulator_v2 as RS

NOW = datetime(2026, 6, 27, tzinfo=timezone.utc)


class _O:
    def __init__(self, oid, status="assigned", picked_up_at=None):
        self.order_id = oid
        self.status = status
        self.picked_up_at = picked_up_at


def _plan(seq, sla, dur, o2=None, mca=None):
    return RS.RoutePlanV2(
        sequence=seq, predicted_delivered_at={}, pickup_at={},
        total_duration_min=dur, strategy="", sla_violations=sla,
        osrm_fallback_used=False, o2_score=o2, max_carried_age=mca,
    )


# ---- _compute_o2_metrics ----

def test_overage_continuous():
    # niesiony 50min (cap 35 → 15 overage), nowy 20min (0), drugi niesiony 40 (5)
    bag = [_O("A", "picked_up"), _O("B", "picked_up")]
    new = _O("N")
    ov, mca = RS._compute_o2_metrics({"A": 50.0, "B": 40.0, "N": 20.0}, bag, new, 35.0)
    assert ov == 20.0          # 15 + 5 + 0
    assert mca == 50.0         # max po niesionych A/B


def test_max_carried_age_only_picked_up():
    # nowy ma 80min ale NIE jest niesiony → nie liczy się do max_carried_age
    bag = [_O("A", "picked_up")]
    new = _O("N")
    ov, mca = RS._compute_o2_metrics({"A": 30.0, "N": 80.0}, bag, new, 35.0)
    assert mca == 30.0         # tylko A (picked_up); N pominięty mimo 80
    assert ov == 45.0          # 0 (A pod 35) + 45 (N: 80−35)


def test_no_carried_max_age_zero():
    bag = [_O("A", "assigned")]   # nic niesionego
    ov, mca = RS._compute_o2_metrics({"A": 50.0}, bag, _O("N"), 35.0)
    assert mca == 0.0             # cap-Z nie wiąże (brak carried)


def test_fail_closed_none():
    ov, mca = RS._compute_o2_metrics(None, [], _O("N"), 35.0)
    assert ov is None and mca is None


# ---- selektor ON≠OFF + cap-Z ----

def test_selector_off_is_legacy_sla(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda n, d=False: d)   # wszystko default (OFF)
    a = _plan(["A"], sla=0, dur=50, o2=20.0, mca=30.0)     # sla-best
    b = _plan(["B"], sla=1, dur=45, o2=5.0, mca=25.0)      # o2-best
    win = RS._select_best_with_tie_breaker([a, b], NOW, nodes=None)
    assert win.sequence == ["A"]   # OFF → sla_violations rządzi (legacy)


def test_selector_on_uses_o2(monkeypatch):
    monkeypatch.setattr(C, "flag",
                        lambda n, d=False: True if n == "ENABLE_O2_READY_ANCHOR_SWEEP" else d)
    a = _plan(["A"], sla=0, dur=50, o2=20.0, mca=30.0)
    b = _plan(["B"], sla=1, dur=45, o2=5.0, mca=25.0)
    win = RS._select_best_with_tie_breaker([a, b], NOW, nodes=None)
    assert win.sequence == ["B"]   # ON → o2_score rządzi (świeższy mimo gorszego sla)


def test_selector_on_off_differ(monkeypatch):
    a = _plan(["A"], sla=0, dur=50, o2=20.0, mca=30.0)
    b = _plan(["B"], sla=1, dur=45, o2=5.0, mca=25.0)
    monkeypatch.setattr(C, "flag", lambda n, d=False: d)
    off = RS._select_best_with_tie_breaker([a, b], NOW, nodes=None)
    monkeypatch.setattr(C, "flag",
                        lambda n, d=False: True if n == "ENABLE_O2_READY_ANCHOR_SWEEP" else d)
    on = RS._select_best_with_tie_breaker([a, b], NOW, nodes=None)
    assert off.sequence != on.sequence   # flaga FAKTYCZNIE zmienia decyzję


def test_cap_z_hard_filter(monkeypatch):
    # ON, Z=35. C ma najlepszy o2 (1.0) ale carried 50>35 (poza Z) → D wygrywa (carried 25)
    monkeypatch.setattr(C, "flag",
                        lambda n, d=False: True if n == "ENABLE_O2_READY_ANCHOR_SWEEP" else d)
    c = _plan(["C"], sla=0, dur=40, o2=1.0, mca=50.0)
    dd = _plan(["D"], sla=0, dur=40, o2=9.0, mca=25.0)
    win = RS._select_best_with_tie_breaker([c, dd], NOW, nodes=None)
    assert win.sequence == ["D"]   # cap-Z wyrzuca C mimo lepszego o2


def test_cap_z_fallback_when_all_over(monkeypatch):
    # ON, wszystkie carried>35 → fallback na pełną pulę, min o2 wygrywa
    monkeypatch.setattr(C, "flag",
                        lambda n, d=False: True if n == "ENABLE_O2_READY_ANCHOR_SWEEP" else d)
    c = _plan(["C"], sla=0, dur=40, o2=3.0, mca=60.0)
    dd = _plan(["D"], sla=0, dur=40, o2=9.0, mca=55.0)
    win = RS._select_best_with_tie_breaker([c, dd], NOW, nodes=None)
    assert win.sequence == ["C"]   # brak under-Z → least-bad o2


# ---- parytet overage z bundle_calib ----

def test_overage_parity_with_bundle_calib_formula():
    """Overage silnika = bundle_calib: Σ max(0, age − R6_MAX). carry_ready=ages."""
    from dispatch_v2.tools import bundle_calib_shadow as BC
    ages = {"A": 50.0, "B": 38.0, "C": 20.0}
    expected = sum(max(0.0, a - BC.R6_MAX_MIN) for a in ages.values())  # 15+3+0=18
    bag = [_O("A", "picked_up"), _O("B", "picked_up")]
    ov, _ = RS._compute_o2_metrics(ages, bag, _O("C"), BC.R6_MAX_MIN)
    assert ov == round(expected, 1)
