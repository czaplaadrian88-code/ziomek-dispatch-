"""Testy dzielonego modułu OBJM-LEXR6 (P1#5, 2026-06-19).

Pokrywa kanoniczne helpery wydzielone z dispatch_pipeline._objm_lexr6_d2_pick /
_objm_lexr6_shadow (były duplikowane inline) + DOWÓD RÓWNOWAŻNOŚCI: `pick` daje
bajt-identyczny wynik jak dawna logika inline (re-implementowana lokalnie w teście).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import objm_lexr6 as olx  # noqa: E402


# --- klasyfikatory testowe (wstrzykiwane jak prawdziwe z dispatch_pipeline) -----
def _is_informed(c):
    return getattr(c, "kind", "") == "informed"


def _is_blind_empty(c):
    return getattr(c, "kind", "") == "blind_empty"


def _is_pre_shift(c):
    return getattr(c, "kind", "") == "pre_shift"


def _tier(c):
    return getattr(c, "tier", 0)


def _cand(cid, *, kind="other", tier=0, r6=None, committed=None, new_late=None):
    m = {}
    if r6 is not None:
        m["objm_r6_breach_max_min"] = r6
    if committed is not None:
        m["late_pickup_committed_max"] = committed
    if new_late is not None:
        m["new_pickup_late_min"] = new_late
    return SimpleNamespace(courier_id=cid, kind=kind, tier=tier, metrics=m)


_KW = dict(late_pickup_tier=_tier, is_informed=_is_informed,
           is_blind_empty=_is_blind_empty, is_pre_shift=_is_pre_shift)


# --- helpery pojedynczo ---------------------------------------------------------
def test_objm_reads_numeric_else_none():
    c = _cand("1", r6=5.5)
    assert olx.objm(c, "objm_r6_breach_max_min") == 5.5
    assert olx.objm(c, "missing") is None
    assert olx.objm(SimpleNamespace(metrics=None), "x") is None
    assert olx.objm(SimpleNamespace(metrics={"x": "nan-str"}), "x") is None


def test_lex_qual_order_and_defaults():
    # brak R6 → 9e9 na pierwszej pozycji (na koniec sortu)
    assert olx.lex_qual(_cand("1"))[0] == 9e9
    assert olx.lex_qual(_cand("1", r6=3.0, committed=2.0, new_late=1.0)) == (3.0, 2.0, 1.0)
    # committed/new_late brak → 0.0
    assert olx.lex_qual(_cand("1", r6=3.0)) == (3.0, 0.0, 0.0)


def test_bucket_classification():
    assert olx.bucket(_cand("i", kind="informed"), **{k: _KW[k] for k in ("is_informed", "is_blind_empty", "is_pre_shift")}) == 0
    assert olx.bucket(_cand("b", kind="blind_empty"), is_informed=_is_informed, is_blind_empty=_is_blind_empty, is_pre_shift=_is_pre_shift) == 2
    assert olx.bucket(_cand("p", kind="pre_shift"), is_informed=_is_informed, is_blind_empty=_is_blind_empty, is_pre_shift=_is_pre_shift) == 2
    assert olx.bucket(_cand("o", kind="other"), is_informed=_is_informed, is_blind_empty=_is_blind_empty, is_pre_shift=_is_pre_shift) == 1


def test_group_of_same_tier_bucket_as_winner():
    w = _cand("w", kind="other", tier=0)
    same = _cand("s", kind="other", tier=0)
    diff_tier = _cand("dt", kind="other", tier=1)
    diff_bucket = _cand("db", kind="informed", tier=0)
    grp = olx.group_of([w, same, diff_tier, diff_bucket], w, **_KW)
    assert {c.courier_id for c in grp} == {"w", "s"}


# --- pick: zachowanie brzegowe --------------------------------------------------
def test_pick_empty_returns_none():
    assert olx.pick([], **_KW) is None


def test_pick_empty_group_returns_winner():
    # winner sam w swojej grupie tier×bucket → zwróć winner
    w = _cand("w", kind="other", tier=0, r6=10.0)
    other = _cand("o", kind="informed", tier=0, r6=0.0)  # inny bucket
    assert olx.pick([w, other], **_KW).courier_id == "w"


def test_pick_lowest_r6_in_group_wins():
    w = _cand("w", kind="other", tier=0, r6=10.0)
    better = _cand("b", kind="other", tier=0, r6=2.0)
    assert olx.pick([w, better], **_KW).courier_id == "b"


def test_pick_lex_tiebreak_committed_then_new_late():
    w = _cand("w", kind="other", tier=0, r6=5.0, committed=9.0, new_late=9.0)
    tie_r6_better_committed = _cand("c", kind="other", tier=0, r6=5.0, committed=3.0, new_late=9.0)
    tie_better_new_late = _cand("n", kind="other", tier=0, r6=5.0, committed=3.0, new_late=1.0)
    # spośród równego R6: najniższy committed, potem new_late
    assert olx.pick([w, tie_r6_better_committed, tie_better_new_late], **_KW).courier_id == "n"


def test_pick_stable_first_on_full_tie():
    a = _cand("a", kind="other", tier=0, r6=4.0, committed=1.0, new_late=1.0)
    b = _cand("b", kind="other", tier=0, r6=4.0, committed=1.0, new_late=1.0)
    # pełny remis → min stabilny zwraca PIERWSZY (kolejność feasible zachowana)
    assert olx.pick([a, b], **_KW).courier_id == "a"


# --- DOWÓD RÓWNOWAŻNOŚCI z dawną logiką inline d2_pick --------------------------
def _legacy_d2_pick(feasible):
    """Wierna kopia dawnej inline logiki _objm_lexr6_d2_pick (sprzed P1#5)."""
    if not feasible:
        return None
    _w0 = feasible[0]

    def _bucket(c):
        if _is_informed(c):
            return 0
        if _is_blind_empty(c) or _is_pre_shift(c):
            return 2
        return 1

    def _objm(c, k):
        v = (getattr(c, "metrics", None) or {}).get(k)
        return float(v) if isinstance(v, (int, float)) else None

    def _lex_qual(c):
        r6 = _objm(c, "objm_r6_breach_max_min")
        return (r6 if r6 is not None else 9e9,
                _objm(c, "late_pickup_committed_max") or 0.0,
                _objm(c, "new_pickup_late_min") or 0.0)

    _w_tb = (_tier(_w0), _bucket(_w0))
    _grp = [c for c in feasible if (_tier(c), _bucket(c)) == _w_tb]
    return min(_grp, key=_lex_qual) if _grp else _w0


def test_pick_equivalent_to_legacy_inline():
    kinds = ["other", "informed", "blind_empty", "pre_shift"]
    cases = []
    # deterministyczna siatka kandydatów (różne kind/tier/r6/committed/new_late)
    n = 0
    for k in kinds:
        for tier in (0, 1, 2):
            for r6 in (None, 0.0, 3.0, 3.0):
                for com in (None, 1.0, 5.0):
                    n += 1
                    cases.append(_cand(f"c{n}", kind=k, tier=tier, r6=r6,
                                       committed=com, new_late=(n % 4) * 1.0))
    # przesuwane okna feasible (różny winner = feasible[0])
    for start in range(0, len(cases) - 5):
        feasible = cases[start:start + 6]
        got = olx.pick(feasible, **_KW)
        exp = _legacy_d2_pick(feasible)
        assert getattr(got, "courier_id", None) == getattr(exp, "courier_id", None), \
            f"rozjazd przy oknie start={start}"
