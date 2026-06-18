"""Tests for FAZA 2 live-flip selektor objm-lexr6 (_objm_lexr6_d2_pick + reorder), 2026-06-18.
Gwarancje: (1) D2-pick = min(R6→committed→new-late) W OBRĘBIE grupy (tier×bucket) feasible[0],
(2) restrykcja do grupy (tier ORAZ bucket), (3) fail-open na feasible[0] gdy brak metryk/pusto,
(4) reorder identity-safe: D2 na czoło, reszta zachowana, długość niezmieniona,
(5) flaga OFF (default) = brak reorderu (no-op produkcyjny).

Mirror fixtur z test_objm_lexr6_shadow.py — ta sama grupacja/lex co funkcja-cień (intencjonalnie
zduplikowana w kodzie pod walidację at#152)."""
import dispatch_v2.dispatch_pipeline as P
import dispatch_v2.common as C
from dispatch_v2.dispatch_pipeline import _objm_lexr6_d2_pick, _late_pickup_tier, _is_informed_cand


class Cand:
    def __init__(self, cid, **metrics):
        self.courier_id = cid
        self.metrics = metrics


def _mk(cid, r6, *, committed=0.0, new_late=0.0, wait=0.0, pos="gps", bag=2,
        breach=False, ext=False):
    return Cand(cid, pos_source=pos, r6_bag_size=bag,
                objm_r6_breach_max_min=r6, late_pickup_committed_max=committed,
                new_pickup_late_min=new_late, v3273_wait_courier_max_min=wait,
                late_pickup_committed_breach=breach, new_pickup_needs_extension=ext)


# --- helper: D2 pick -------------------------------------------------------

def test_pick_lowest_r6_in_group():
    w = _mk(1, 20.0)
    a = _mk(2, 5.0)        # niższy R6, ta sama grupa
    b = _mk(3, 12.0)
    assert _objm_lexr6_d2_pick([w, a, b]) is a


def test_lex_order_committed_then_new_late():
    # remis R6 → rozstrzyga committed; remis committed → new_late
    w = _mk(1, 5.0, committed=10.0, new_late=1.0)
    a = _mk(2, 5.0, committed=3.0, new_late=9.0)   # niższy committed wygrywa mimo wyższego new_late
    assert _objm_lexr6_d2_pick([w, a]) is a
    x = _mk(10, 5.0, committed=2.0, new_late=8.0)
    y = _mk(11, 5.0, committed=2.0, new_late=2.0)  # remis R6+committed → niższy new_late
    assert _objm_lexr6_d2_pick([x, y]) is y


def test_winner_already_min_returns_feasible0():
    w = _mk(1, 3.0)
    a = _mk(2, 20.0)
    assert _objm_lexr6_d2_pick([w, a]) is w


def test_group_restriction_tier():
    # niższy R6 ale tier2 (łamie committed) — INNA grupa niż tier0 winner → NIE wybrany
    w = _mk(1, 20.0, breach=False)        # tier0
    a = _mk(2, 1.0, breach=True)          # tier2
    assert _objm_lexr6_d2_pick([w, a]) is w


def test_group_restriction_bucket():
    # niższy R6 ale bucket2 (pre_shift+bag0) — inna grupa niż gps bucket0 → NIE wybrany
    w = _mk(1, 20.0, pos="gps", bag=2)
    a = _mk(2, 1.0, pos="pre_shift", bag=0)
    assert _objm_lexr6_d2_pick([w, a]) is w


def test_fail_open_missing_metrics():
    # brak objm_r6_breach_max_min → 9e9 (na koniec), winner zostaje; zero crasha
    w = Cand(1, pos_source="gps", r6_bag_size=2)
    a = Cand(2, pos_source="gps", r6_bag_size=2)
    out = _objm_lexr6_d2_pick([w, a])
    assert out is w                       # oba 9e9 → min stabilny = pierwszy


def test_empty_returns_none():
    assert _objm_lexr6_d2_pick([]) is None
    assert _objm_lexr6_d2_pick(None) is None


# --- reorder semantics (mirror produkcyjnego bloku Fazy 2) ------------------

def _reorder(feasible):
    """Dokładne odwzorowanie produkcyjnego bloku ENABLE_OBJM_LEXR6_SELECT (identity-safe)."""
    _d2 = _objm_lexr6_d2_pick(feasible)
    if _d2 is not None and _d2 is not feasible[0]:
        _idx = next((i for i, c in enumerate(feasible) if c is _d2), None)
        if _idx is not None:
            feasible.pop(_idx)
            feasible.insert(0, _d2)
    return feasible


def test_reorder_moves_d2_to_front_preserves_rest():
    w = _mk(1, 20.0)
    a = _mk(2, 5.0)
    b = _mk(3, 12.0)
    f = [w, a, b]
    _reorder(f)
    assert f[0] is a                      # D2 na czoło
    assert set(id(c) for c in f) == {id(w), id(a), id(b)}   # ten sam zbiór
    assert len(f) == 3                    # nic nie zgubione/zdublowane


def test_reorder_noop_when_winner_is_d2():
    w = _mk(1, 3.0)
    a = _mk(2, 20.0)
    f = [w, a]
    _reorder(f)
    assert f[0] is w and f[1] is a        # bez zmian


def test_reorder_identity_safe_with_equalish_cands():
    # kandydaci o identycznych metrykach (gdyby __eq__ był wartościowy, .remove zdjąłby zły) —
    # pop-po-id musi zdjąć właściwy obiekt
    w = _mk(1, 20.0)
    a = _mk(2, 5.0)
    b = _mk(3, 5.0)                        # te same metryki co a, ale gorszy w kolejności (stabilność)
    f = [w, a, b]
    _reorder(f)
    assert f[0] is a                      # min stabilny = pierwszy z równych (a przed b)
    assert len(f) == 3 and f.count(a) == 1 and f.count(b) == 1


# --- flag gate -------------------------------------------------------------

def test_flag_default_off():
    # KANON: flaga domyślnie OFF (flags.json + common.py) → produkcja niezmieniona do flipu
    assert C.flag("ENABLE_OBJM_LEXR6_SELECT", False) is False
