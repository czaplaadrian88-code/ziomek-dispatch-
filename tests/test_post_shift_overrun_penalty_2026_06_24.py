"""Post-shift overrun penalty (Adrian 2026-06-24) — rosnąca kara za minuty dowozu
po końcu zmiany, jako WIODĄCY term selekcji best_effort (feasible=0).

Root case #483144 (Pizza Dealer → Kręta 48/97, 21:44 Warsaw):
  - Piotr K-531: zmiana do 22:00, dowóz 22:27 → +27 min po zmianie, R6 worek czysty (0)
  - Kuba  K-370: zmiana do 22:00, dowóz 22:38 → +38 min po zmianie, R6 breach 3.9
  - Patryk K-75: zmiana do 23:00, dowóz 22:27 → 0 nadwyżki, R6 breach 4.8 (carry, sunk)

Stary objm (carry-R6 PRIMARY, ślepy na koniec zmiany) wybierał Piotra (R6=0).
Adrian: „nie ma trafić ani do Kuby ani do Piotra" → po fladze ON trafia do Patryka
(0 nadwyżki). Flaga OFF = zachowanie bez zmian (Piotr).
"""
import sys
from types import SimpleNamespace

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import dispatch_v2.common as C  # noqa: E402
from dispatch_v2.dispatch_pipeline import (  # noqa: E402
    _best_effort_objm_pick,
    _best_effort_sort_key,
    _post_shift_overrun_penalty_of,
)

NEW_OID = "483144"


def _cand(cid, *, objm_r6, overrun_min, new_bag, score, r6_pov_n, pos="last_picked_up_pickup"):
    """Kandydat best_effort wierny rekordowi shadow #483144."""
    pen = C.post_shift_overrun_penalty(overrun_min)
    metrics = {
        "objm_r6_breach_max_min": objm_r6,
        "late_pickup_committed_max": 0.0,
        "new_pickup_late_min": 0.0,
        "post_shift_overrun_min": overrun_min,
        "post_shift_overrun_penalty": pen,
        "pos_source": pos,
        "r6_bag_size": 3,
        "r6_per_order_violations": [["x", 36.0]] * r6_pov_n,
    }
    plan = SimpleNamespace(
        per_order_delivery_times={NEW_OID: new_bag},
        sla_violations=0,
        total_duration_min=50.0,
        sum_bag_time_min=new_bag,
    )
    return SimpleNamespace(courier_id=cid, metrics=metrics, plan=plan, score=score)


def _trio():
    piotr = _cand("531", objm_r6=0.0, overrun_min=27.95, new_bag=28.63, score=-33.28, r6_pov_n=0)
    kuba = _cand("370", objm_r6=3.9, overrun_min=38.0, new_bag=38.87, score=-426.47, r6_pov_n=1)
    patryk = _cand("75", objm_r6=4.8, overrun_min=0.0, new_bag=27.71, score=-442.98, r6_pov_n=1)
    return piotr, kuba, patryk


# ── 1. Krzywa kary (common) ──────────────────────────────────────────────────
def test_penalty_grace_zero_below_5min():
    assert C.post_shift_overrun_penalty(0) == 0.0
    assert C.post_shift_overrun_penalty(4.9) == 0.0
    assert C.post_shift_overrun_penalty(5.0) == 0.0


def test_penalty_strictly_grows_above_grace():
    prev = -1.0
    for over in [6, 10, 15, 20, 27, 30, 38, 45, 60]:
        pen = C.post_shift_overrun_penalty(over)
        assert pen > prev, f"kara nie rośnie przy {over}: {pen} <= {prev}"
        prev = pen


def test_penalty_convex_each_minute_costs_more():
    """„rosnąca za każdą minutę" = przyrost kary rośnie (krzywa wypukła)."""
    d1 = C.post_shift_overrun_penalty(11) - C.post_shift_overrun_penalty(10)
    d2 = C.post_shift_overrun_penalty(21) - C.post_shift_overrun_penalty(20)
    d3 = C.post_shift_overrun_penalty(31) - C.post_shift_overrun_penalty(30)
    assert d1 < d2 < d3


def test_penalty_none_and_nonnumeric_safe():
    assert C.post_shift_overrun_penalty(None) == 0.0
    assert C.post_shift_overrun_penalty("x") == 0.0


def test_case_values_piotr_kuba_patryk():
    assert C.post_shift_overrun_penalty(27.95) > 0
    assert C.post_shift_overrun_penalty(38.0) > C.post_shift_overrun_penalty(27.95)
    assert C.post_shift_overrun_penalty(0.0) == 0.0


# ── 2. objm pick (live key ENABLE_BEST_EFFORT_OBJM_R6_KEY) ────────────────────
def test_objm_pick_flag_off_keeps_piotr(monkeypatch):
    """OFF = zachowanie sprzed zmiany: carry-R6 PRIMARY → Piotr (R6 breach 0)."""
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", False, raising=False)
    piotr, kuba, patryk = _trio()
    pick = _best_effort_objm_pick([piotr, kuba, patryk], NEW_OID)
    assert pick.courier_id == "531"


def test_objm_pick_flag_on_picks_patryk_not_kuba_not_piotr(monkeypatch):
    """ON = post-shift WIODĄCY → Patryk (0 nadwyżki); ani Kuba ani Piotr."""
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", True, raising=False)
    piotr, kuba, patryk = _trio()
    pick = _best_effort_objm_pick([piotr, kuba, patryk], NEW_OID)
    assert pick.courier_id == "75"


def test_objm_pick_flag_on_order_independent(monkeypatch):
    """Niezależne od kolejności wejścia (min deterministyczny)."""
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", True, raising=False)
    piotr, kuba, patryk = _trio()
    for perm in ([kuba, patryk, piotr], [patryk, piotr, kuba], [piotr, patryk, kuba]):
        assert _best_effort_objm_pick(perm, NEW_OID).courier_id == "75"


# ── 3. sort_key (FEAS-01 fallback) ───────────────────────────────────────────
def test_sort_key_flag_off_keeps_piotr(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", False, raising=False)
    piotr, kuba, patryk = _trio()
    winner = min([piotr, kuba, patryk], key=_best_effort_sort_key)
    assert winner.courier_id == "531"  # r6_pov=0 wygrywa


def test_sort_key_flag_on_picks_patryk(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", True, raising=False)
    piotr, kuba, patryk = _trio()
    winner = min([piotr, kuba, patryk], key=_best_effort_sort_key)
    assert winner.courier_id == "75"


# ── 4. fail-open: brak nadwyżki / brak metryki → 0 (zero demote) ──────────────
def test_helper_zero_when_flag_off(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", False, raising=False)
    piotr, _, _ = _trio()
    assert _post_shift_overrun_penalty_of(piotr) == 0.0


def test_helper_zero_when_metric_missing(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", True, raising=False)
    c = SimpleNamespace(metrics={}, plan=None, score=0.0)
    assert _post_shift_overrun_penalty_of(c) == 0.0


def test_no_overrun_courier_not_demoted(monkeypatch):
    """Kurier w oknie zmiany (overrun 0) nie dostaje kary nawet przy fladze ON."""
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", True, raising=False)
    _, _, patryk = _trio()
    assert _post_shift_overrun_penalty_of(patryk) == 0.0


# ── 5. PARYTET LEXR6 (objm_lexr6.lex_qual — selektor feasible, „robimy 3") ─────
def _olx():
    from dispatch_v2 import objm_lexr6 as olx
    return olx


def test_lexr6_flag_off_byte_identical_3tuple(monkeypatch):
    """OFF: krotka 3-elem. bajt-identyczna jak dawne inline (tests at#152 nietknięte)."""
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", False, raising=False)
    olx = _olx()
    c = SimpleNamespace(metrics={"objm_r6_breach_max_min": 3.0,
                                 "late_pickup_committed_max": 2.0,
                                 "new_pickup_late_min": 1.0,
                                 "post_shift_overrun_penalty": 999.0})
    assert olx.lex_qual(c) == (3.0, 2.0, 1.0)  # post-shift IGNOROWANY gdy OFF


def test_lexr6_flag_on_post_shift_leads(monkeypatch):
    """ON: 4-elem., post-shift WIODĄCY — kurier po zmianie (pen>0) przegrywa z w-oknie (0)."""
    monkeypatch.setattr(C, "ENABLE_POST_SHIFT_OVERRUN_PENALTY", True, raising=False)
    olx = _olx()
    piotr = SimpleNamespace(metrics={"objm_r6_breach_max_min": 0.0,
                                     "post_shift_overrun_penalty": 422.6})
    patryk = SimpleNamespace(metrics={"objm_r6_breach_max_min": 4.8,
                                      "post_shift_overrun_penalty": 0.0})
    assert olx.lex_qual(patryk) < olx.lex_qual(piotr)   # w oknie wygrywa
    assert min([piotr, patryk], key=olx.lex_qual) is patryk
