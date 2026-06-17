"""Tests for _objm_lexr6_shadow (D2 SHADOW selector, 2026-06-17).
Gwarancje: (1) ZERO mutacji top/feasible/werdyktu (observational), (2) poprawny lex R6→committed→new-late,
(3) restrykcja do grupy (tier×bucket) zwycięzcy, (4) flip detection + delty, (5) defensywność (brak crasha)."""
import dispatch_v2.dispatch_pipeline as P
from dispatch_v2.dispatch_pipeline import _objm_lexr6_shadow, _is_informed_cand, _late_pickup_tier


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


def test_stub_bucket_sane():
    # sanity: 'gps' jest informed (bucket 0), brak breach/ext → tier 0
    c = _mk(1, 10)
    assert _is_informed_cand(c) is True
    assert _late_pickup_tier(c) == 0


def test_flip_lower_r6_breach():
    w = _mk(1, 20.0, new_late=5.0, wait=0.0)   # zwycięzca: R6-breach 20
    a = _mk(2, 5.0, new_late=3.0, wait=8.0)     # lepszy na R6 (5), ta sama grupa
    top, feasible = [w], [w, a]
    _objm_lexr6_shadow(top, feasible, order_id="T1")
    m = w.metrics
    assert m["objm_lexr6_best_cid"] == "2"
    assert m["objm_lexr6_flip"] is True
    assert m["objm_lexr6_group_size"] == 2
    assert m["objm_lexr6_d_r6_breach"] == -15.0     # 5 - 20
    assert m["objm_lexr6_d_new_late"] == -2.0       # 3 - 5
    assert m["objm_lexr6_d_idle"] == 8.0            # 8 - 0 (koszt idle)


def test_no_flip_when_winner_is_min():
    w = _mk(1, 3.0)
    a = _mk(2, 20.0)
    top, feasible = [w], [w, a]
    _objm_lexr6_shadow(top, feasible, order_id="T2")
    assert w.metrics["objm_lexr6_best_cid"] == "1"
    assert w.metrics["objm_lexr6_flip"] is False
    assert "objm_lexr6_d_r6_breach" not in w.metrics   # brak delt gdy brak flipu


def test_group_restriction_tier():
    # 'lepszy' kandydat (niski R6) jest tier2 (committed_breach) — INNA grupa → NIE wybrany
    w = _mk(1, 20.0, breach=False)                 # tier0
    a = _mk(2, 1.0, breach=True)                   # tier2 (łamie committed) — niższy R6 ale inny tier
    top, feasible = [w], [w, a]
    _objm_lexr6_shadow(top, feasible, order_id="T3")
    assert w.metrics["objm_lexr6_flip"] is False   # tier2 nie wchodzi do grupy tier0
    assert w.metrics["objm_lexr6_group_size"] == 1


def test_group_restriction_bucket():
    # 'lepszy' kandydat ma pos_source=pre_shift+bag0 (bucket 2) — inna grupa niż gps (bucket 0)
    w = _mk(1, 20.0, pos="gps", bag=2)
    a = _mk(2, 1.0, pos="pre_shift", bag=0)
    top, feasible = [w], [w, a]
    _objm_lexr6_shadow(top, feasible, order_id="T4")
    assert w.metrics["objm_lexr6_flip"] is False
    assert w.metrics["objm_lexr6_group_size"] == 1


def test_no_mutation_of_inputs():
    w = _mk(1, 20.0)
    a = _mk(2, 5.0)
    top = [w]
    feasible = [w, a]
    top_ids = [id(c) for c in top]
    feas_ids = [id(c) for c in feasible]
    _objm_lexr6_shadow(top, feasible, order_id="T5")
    # OBSERWACYJNY: kolejność i tożsamość list NIETKNIĘTE — werdykt (top[0]) bez zmian
    assert [id(c) for c in top] == top_ids
    assert [id(c) for c in feasible] == feas_ids
    assert top[0] is w


def test_lex_committed_before_new_late():
    # remis na R6 (oba 0) → decyduje committed, potem new-late
    w = _mk(1, 0.0, committed=10.0, new_late=0.0)
    a = _mk(2, 0.0, committed=2.0, new_late=30.0)   # gorszy new-late, ale niższy committed → wygrywa
    top, feasible = [w], [w, a]
    _objm_lexr6_shadow(top, feasible, order_id="T6")
    assert w.metrics["objm_lexr6_best_cid"] == "2"
    assert w.metrics["objm_lexr6_d_committed"] == -8.0


def test_defensive_missing_objm_and_bad_metrics():
    # brak objm_r6_breach_max_min → fallback 9e9 (na koniec), brak crasha
    w = Cand(1, pos_source="gps", r6_bag_size=2)   # brak pól objm
    a = _mk(2, 5.0)
    top, feasible = [w], [w, a]
    _objm_lexr6_shadow(top, feasible, order_id="T7")   # nie rzuca
    assert w.metrics["objm_lexr6_best_cid"] == "2"      # a (R6=5) < w (brak=9e9)
    # metrics=None → graceful return, brak crasha
    w2 = Cand(3); w2.metrics = None
    _objm_lexr6_shadow([w2], [w2], order_id="T8")        # nie rzuca, nic nie pisze


def test_empty_inputs():
    _objm_lexr6_shadow([], [], order_id="T9")            # no-op
    _objm_lexr6_shadow(None, None, order_id="T10")       # no-op
