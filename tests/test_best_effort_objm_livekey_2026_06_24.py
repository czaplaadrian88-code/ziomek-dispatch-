"""Test _best_effort_objm_pick (live-flip ENABLE_BEST_EFFORT_OBJM_R6_KEY, 2026-06-24).

Rdzeń carry-aware guarded selekcji best_effort: PRIMARY = objm_r6_breach_max_min (carry-inclusive,
nie new-pickup-only r6_per_order_violations — case #482817), BEZPIECZNIK = new-order bag <= cap.
Helper = JEDNO ŹRÓDŁO PRAWDY współdzielone przez shadow i live-flip.
"""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.dispatch_pipeline import _best_effort_objm_pick  # noqa: E402


class _Plan:
    def __init__(self, newbag):
        # per_order_delivery_times: {new_oid: food_age_min}
        self.per_order_delivery_times = {"NEW": newbag} if newbag is not None else {}


class _Cand:
    def __init__(self, cid, objm_r6, newbag, committed=0.0, new_late=0.0):
        self.courier_id = cid
        self.plan = _Plan(newbag)
        self.metrics = {
            "objm_r6_breach_max_min": objm_r6,
            "late_pickup_committed_max": committed,
            "new_pickup_late_min": new_late,
        }


def test_picks_min_carry_breach():
    # B ma najniższy carry-breach (objm_r6) — wygrywa, choć nie pierwszy
    a = _Cand("A", objm_r6=40.0, newbag=20.0)
    b = _Cand("B", objm_r6=5.0, newbag=20.0)
    c = _Cand("C", objm_r6=18.0, newbag=20.0)
    assert _best_effort_objm_pick([a, b, c], "NEW", cap_min=35.0).courier_id == "B"


def test_carry_blind_winner_loses_to_carry_aware():
    # Mirror #482817/#483000: carry-blind wskazałby kandydata z carry-breach (jego breach jest
    # na NIESIONYM = niewidoczny dla r6_per_order), objm widzi prawdę i wybiera czystego.
    carry_blind = _Cand("BLIND", objm_r6=58.0, newbag=15.0)   # ślepy "czysty", realnie brudny
    carry_aware = _Cand("AWARE", objm_r6=2.0, newbag=15.0)
    assert _best_effort_objm_pick([carry_blind, carry_aware], "NEW", cap_min=35.0).courier_id == "AWARE"


def test_guard_excludes_new_order_breach():
    # X ma min carry-breach ale psuje NOWY order (newbag 50 > cap 35) → guard go odrzuca,
    # wybiera bezpiecznego Y (newbag 30 <= 35) mimo wyższego carry-breach.
    x = _Cand("X", objm_r6=1.0, newbag=50.0)
    y = _Cand("Y", objm_r6=12.0, newbag=30.0)
    assert _best_effort_objm_pick([x, y], "NEW", cap_min=35.0).courier_id == "Y"


def test_guard_fallback_when_none_safe():
    # Gdy ŻADEN kandydat nie chroni nowego ordera (wszyscy > cap) → fallback pure carry-min (raw).
    x = _Cand("X", objm_r6=1.0, newbag=50.0)
    y = _Cand("Y", objm_r6=12.0, newbag=48.0)
    assert _best_effort_objm_pick([x, y], "NEW", cap_min=35.0).courier_id == "X"


def test_newbag_none_treated_as_safe():
    # Brak per_order_delivery_times[new] → _newbag None → traktowany jako bezpieczny (nie wyklucza).
    x = _Cand("X", objm_r6=3.0, newbag=None)
    y = _Cand("Y", objm_r6=20.0, newbag=10.0)
    assert _best_effort_objm_pick([x, y], "NEW", cap_min=35.0).courier_id == "X"


def test_empty_pool_returns_none():
    assert _best_effort_objm_pick([], "NEW", cap_min=35.0) is None


def test_missing_objm_metric_sorts_last():
    # Kandydat bez objm_r6_breach_max_min → 9e9 (na dół), nie wygrywa fałszywie.
    good = _Cand("GOOD", objm_r6=10.0, newbag=20.0)
    nometr = _Cand("NOMET", objm_r6=None, newbag=20.0)
    assert _best_effort_objm_pick([good, nometr], "NEW", cap_min=35.0).courier_id == "GOOD"
