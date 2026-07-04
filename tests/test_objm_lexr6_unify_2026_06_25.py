"""Parytet + anty-regresja unifikacji lex_qual (objm-lexr6-unify, 2026-06-25).

_best_effort_objm_pick i _best_effort_objm_shadow MUSZA uzywac KANONU
objm_lexr6.lex_qual (jedno zrodlo prawdy), nie wlasnej kopii inline. Test:
 1) pick == min(safe, key=objm_lexr6.lex_qual) przy fladze OFF i ON (parytet);
 2) term post_shift faktycznie idzie przez kanon (zmienia zwyciezce tylko przy ON);
 3) brak ponownej dywergencji: zrodlo picka nie definiuje wlasnego _lex_qual.

L6.C1 (2026-07-04): zamrozenie _objm_lexr6_shadow pod at#152 WYGASLO (walidacja
PASS — at-200 03.07 GO, SELECT LIVE) → cien przepiety na kanon i OBJETY parytetem
(test_shadow_no_inline_lexqual nizej). Koniec swiadomej baseline.
"""
import inspect
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import objm_lexr6  # noqa: E402
from dispatch_v2.dispatch_pipeline import _best_effort_objm_pick  # noqa: E402


class _Plan:
    def __init__(self, newbag):
        self.per_order_delivery_times = {"NEW": newbag} if newbag is not None else {}


class _Cand:
    def __init__(self, cid, objm_r6, newbag, committed=0.0, new_late=0.0, post_shift=None):
        self.courier_id = cid
        self.plan = _Plan(newbag)
        self.metrics = {
            "objm_r6_breach_max_min": objm_r6,
            "late_pickup_committed_max": committed,
            "new_pickup_late_min": new_late,
        }
        if post_shift is not None:
            self.metrics["post_shift_overrun_penalty"] = post_shift


def _expected_canon(pool, new_oid, cap_min):
    """Mirror bezpiecznika z _best_effort_objm_pick; ranking = kanon objm_lexr6.lex_qual."""
    def _newbag(c):
        pod = getattr(getattr(c, "plan", None), "per_order_delivery_times", None) or {}
        v = pod.get(new_oid)
        if isinstance(v, (int, float)):
            return float(v)
        m = (getattr(c, "metrics", None) or {}).get("sum_bag_time_min")
        return float(m) if isinstance(m, (int, float)) else None
    safe = [c for c in pool if (_newbag(c) is None or _newbag(c) <= cap_min)]
    base = safe if safe else pool
    return min(base, key=objm_lexr6.lex_qual)


def test_pick_equals_canon_flag_off():
    # Domyslnie ENABLE_POST_SHIFT_OVERRUN_PENALTY OFF -> kanon = krotka 3-elem. R6-primary.
    pool = [
        _Cand("A", objm_r6=40.0, newbag=20.0),
        _Cand("B", objm_r6=5.0, newbag=20.0),
        _Cand("C", objm_r6=18.0, newbag=50.0),  # > cap -> poza safe
    ]
    got = _best_effort_objm_pick(pool, "NEW", cap_min=35.0)
    assert got is _expected_canon(pool, "NEW", 35.0)
    assert got.courier_id == "B"


def test_pick_equals_canon_flag_on(monkeypatch):
    monkeypatch.setattr(
        objm_lexr6.C, "decision_flag",
        lambda name, default=False: name == "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
    )
    pool = [
        _Cand("LATE", objm_r6=2.0, newbag=20.0, post_shift=30.0),  # super R6, ale konczy po zmianie
        _Cand("WIN", objm_r6=8.0, newbag=20.0, post_shift=0.0),    # gorszy R6, ale w oknie
        _Cand("MID", objm_r6=4.0, newbag=20.0, post_shift=12.0),
    ]
    got = _best_effort_objm_pick(pool, "NEW", cap_min=35.0)
    assert got is _expected_canon(pool, "NEW", 35.0)
    # ON: post_shift WIODACY -> WIN (post_shift 0) bije LATE (post_shift 30) mimo gorszego R6
    assert got.courier_id == "WIN"


def test_post_shift_flips_winner_only_when_on(monkeypatch):
    pool = [
        _Cand("LATE", objm_r6=2.0, newbag=20.0, post_shift=30.0),
        _Cand("WIN", objm_r6=8.0, newbag=20.0, post_shift=0.0),
    ]
    # OFF (domyslnie): R6-primary -> LATE (2 < 8)
    assert _best_effort_objm_pick(pool, "NEW", cap_min=35.0).courier_id == "LATE"
    # ON: post_shift-primary -> WIN
    monkeypatch.setattr(
        objm_lexr6.C, "decision_flag",
        lambda name, default=False: name == "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
    )
    assert _best_effort_objm_pick(pool, "NEW", cap_min=35.0).courier_id == "WIN"


def test_no_inline_lexqual_redivergence():
    # Anty-regresja: pick MUSI delegowac do kanonu, nie trzymac wlasnej kopii _lex_qual.
    src = inspect.getsource(_best_effort_objm_pick)
    assert "objm_lexr6" in src, "pick musi uzywac kanonu objm_lexr6.lex_qual"
    assert "def _lex_qual" not in src, "pick nie moze trzymac inline _lex_qual (re-dywergencja!)"


def test_shadow_no_inline_lexqual_redivergence():
    """L6.C1 (2026-07-04): cien _objm_lexr6_shadow przepiety na kanon po wygasnieciu
    zamrozenia at#152 — nie moze trzymac wlasnej kopii _lex_qual ani _objm."""
    from dispatch_v2.dispatch_pipeline import _objm_lexr6_shadow
    src = inspect.getsource(_objm_lexr6_shadow)
    assert "objm_lexr6" in src, "cien musi uzywac kanonu objm_lexr6.lex_qual"
    assert "def _lex_qual" not in src, "cien nie moze trzymac inline _lex_qual (re-dywergencja!)"
    assert "def _objm(" not in src, "cien nie moze trzymac inline _objm (kanon ma objm())"


def test_shadow_lexqual_parity_both_post_shift_states(monkeypatch):
    """Parytet cien↔kanon przy OBU stanach POST_SHIFT (golden L6.D2-lite): krotka
    kanonu = 3-elem. przy OFF (bajt-parytet z dawnym inline) / 4-elem. przy ON."""
    c = _Cand("X", objm_r6=7.5, newbag=20.0, committed=1.0, new_late=2.0, post_shift=9.0)
    # OFF (domyslnie)
    assert objm_lexr6.lex_qual(c) == (7.5, 1.0, 2.0)
    # ON
    monkeypatch.setattr(
        objm_lexr6.C, "decision_flag",
        lambda name, default=False: name == "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
    )
    assert objm_lexr6.lex_qual(c) == (9.0, 7.5, 1.0, 2.0)
