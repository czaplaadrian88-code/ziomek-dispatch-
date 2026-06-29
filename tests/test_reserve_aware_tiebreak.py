"""#3 top10 (2026-06-29): test reserve-aware tie-break SHADOW eval (log-only).

Pokrywa would_fire + WSZYSTKIE bramki: winner wolny vs zajęty, same-tier (brak
inwersji committed), margin score, R6>40 wykluczone, sentinel/best_effort wykluczone.
Pure helper _reserve_aware_tiebreak_eval — ZERO mutacji, log-only obserwator.
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.dispatch_pipeline import _reserve_aware_tiebreak_eval  # noqa: E402


class _Cand:
    def __init__(self, cid, score, bag, r6max=15.0):
        self.courier_id = cid
        self.score = score
        self.metrics = {"bag_size_before": bag, "max_bag_time_min": r6max}


# tier 0 dla wszystkich (brak inwersji late-pickup), chyba że test zmienia
def _tier0(c):
    return 0


def test_would_fire_free_winner_carry_in_margin():
    winner = _Cand("400", 100.0, 0)        # wolny (bag 0)
    carry = _Cand("509", 80.0, 2, r6max=20.0)  # jadący, Δ=20 ≤ margin 30
    other_free = _Cand("370", 95.0, 0)
    out = _reserve_aware_tiebreak_eval(winner, [winner, other_free, carry], 0, _tier0, 30.0)
    assert out["would_fire"] is True
    assert out["carry_cid"] == "509"
    assert out["dscore_free_minus_carry"] == 20.0
    assert out["carry_bag_before"] == 2


def test_no_fire_winner_not_free():
    winner = _Cand("400", 100.0, 1)        # zwycięzca JUŻ wiezie (bag 1) → nie pal rezerwy
    carry = _Cand("509", 80.0, 2)
    out = _reserve_aware_tiebreak_eval(winner, [winner, carry], 0, _tier0, 30.0)
    assert out["would_fire"] is False
    assert out["winner_free"] is False


def test_no_fire_gap_too_big():
    winner = _Cand("400", 100.0, 0)
    carry = _Cand("509", 50.0, 2)          # Δ=50 > margin 30 → silnik miał powód (bundle gorszy)
    out = _reserve_aware_tiebreak_eval(winner, [winner, carry], 0, _tier0, 30.0)
    assert out["would_fire"] is False
    assert out["winner_free"] is True


def test_no_fire_different_late_pickup_tier():
    winner = _Cand("400", 100.0, 0)
    carry = _Cand("509", 90.0, 2)          # w marginesie ALE inny tier → inwersja committed → wyklucz
    def _tier(c):
        return 1 if c.courier_id == "509" else 0
    out = _reserve_aware_tiebreak_eval(winner, [winner, carry], 0, _tier, 30.0)
    assert out["would_fire"] is False


def test_no_fire_r6_over_cap():
    winner = _Cand("400", 100.0, 0)
    carry = _Cand("509", 90.0, 2, r6max=42.0)  # bundle R6 42 > 40 → psułby świeżość → wyklucz
    out = _reserve_aware_tiebreak_eval(winner, [winner, carry], 0, _tier0, 30.0)
    assert out["would_fire"] is False


def test_no_fire_sentinel_score():
    winner = _Cand("400", 100.0, 0)
    carry = _Cand("509", -1e9, 2)          # sentinel/best_effort → wyklucz
    out = _reserve_aware_tiebreak_eval(winner, [winner, carry], 0, _tier0, 30.0)
    assert out["would_fire"] is False


def test_picks_best_carrier_by_score():
    winner = _Cand("400", 100.0, 0)
    c1 = _Cand("509", 75.0, 2)
    c2 = _Cand("289", 90.0, 1)             # lepszy score → wybrany jako carry
    out = _reserve_aware_tiebreak_eval(winner, [winner, c1, c2], 0, _tier0, 30.0)
    assert out["would_fire"] is True
    assert out["carry_cid"] == "289"
    assert out["n_carrier_candidates"] == 2


def test_flag_default_off_and_on_contract():
    """ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW = log-only OBSERWATOR (nie zmienia verdict/best/
    reason — tylko pole reserve_tiebreak_shadow). Stała default OFF (flags.json override runtime
    via C.flag). Gdy gate ON, pipeline woła _reserve_aware_tiebreak_eval (efekt = zapis dict);
    OFF → None. Tu asercja kontraktu ON (helper) + default literału OFF (parytet flag_registry)."""
    import dispatch_v2.common as C
    assert getattr(C, "ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW") is False  # default literał OFF
    winner = _Cand("400", 100.0, 0)
    carry = _Cand("509", 80.0, 2)
    on_effect = _reserve_aware_tiebreak_eval(winner, [winner, carry], 0, _tier0, 30.0)
    assert on_effect["would_fire"] is True   # ON-ścieżka produkuje log-efekt
    # OFF-ścieżka (winner zajęty) → brak fire (parytet z gate-None semantyką)
    busy = _Cand("400", 100.0, 1)
    assert _reserve_aware_tiebreak_eval(busy, [busy, carry], 0, _tier0, 30.0)["would_fire"] is False
