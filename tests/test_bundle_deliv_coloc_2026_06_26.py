"""BUNDLE-DELIVERY-COLOCATION (Adrian 2026-06-26, case Dariusz Maruszak 509).

Forced-bundle z 2 TWARDYCH reguł (R6 ≤35 czyste + committed ±5 honorowane), gdy
nowa dostawa skolokowana z dostawą w bagu (różne restauracje, ten sam adres) —
zamyka pickup-centryczną ślepotę L1/L2. Test ON≠OFF + bramki HARD + parytet flagi.

Real case: Street Mama Thai (42pp 72a, 53.1397/23.2264) + Raj (42pp 72E,
53.1401/23.2264) = dropy 37 m, R6 29.9, committed_breach False.
"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.dispatch_pipeline import compute_bundle_deliv_coloc as F
from dispatch_v2 import common as C

# --- case 509 ---
BAG = [{"delivery_coords": [53.1397479, 23.2263973], "restaurant": "Street Mama Thai"}]
RAJ = (53.1400844, 23.2263682)            # 37 m od dropu Street Mama Thai
M_OK = {"r6_max_bag_time_min": 29.9, "r6_per_order_violations": [],
        "r6_picked_up_violations": []}
KW = dict(km_threshold=0.3, bonus_max=20.0, r6_hard_max=35.0, level1=None, level2=None)


def test_flag_off_inactive():
    km, active, bonus = F(BAG, RAJ, M_OK, False, flag_on=False, **KW)
    assert active is False and bonus == 0.0


def test_flag_on_forced_bundle_credit():
    km, active, bonus = F(BAG, RAJ, M_OK, False, flag_on=True, **KW)
    assert active is True
    assert km == 0.037
    assert bonus > 0  # max(0, 20 - 0.037*10) ≈ 19.63


def test_on_neq_off():
    off = F(BAG, RAJ, M_OK, False, flag_on=False, **KW)
    on = F(BAG, RAJ, M_OK, False, flag_on=True, **KW)
    assert off != on  # flaga ZMIENIA decyzję (ETAP4 dowód nie-martwoty)


def test_hard_gate_r6_violation_blocks():
    m = {"r6_max_bag_time_min": 40.0, "r6_per_order_violations": [("x", 40.0)],
         "r6_picked_up_violations": []}
    _, active, bonus = F(BAG, RAJ, m, False, flag_on=True, **KW)
    assert active is False and bonus == 0.0  # R6 (HARD) nie spełnione → brak kredytu


def test_hard_gate_committed_breach_blocks():
    _, active, bonus = F(BAG, RAJ, M_OK, True, flag_on=True, **KW)  # breach=True
    assert active is False and bonus == 0.0  # committed >±5 → brak kredytu


def test_far_drop_not_colocated():
    _, active, bonus = F([{"delivery_coords": [53.20, 23.30]}], RAJ, M_OK, False,
                         flag_on=True, **KW)
    assert active is False and bonus == 0.0  # > 0.3 km → nie bundle


def test_no_double_credit_when_l1_or_l2():
    kw = dict(KW); kw["level2"] = "Jakas Restauracja"
    _, active, bonus = F(BAG, RAJ, M_OK, False, flag_on=True, **kw)
    assert active is False and bonus == 0.0  # L2 już daje kredyt → nie dubluj


def test_r6_max_at_hard_limit_ok():
    m = {"r6_max_bag_time_min": 35.0, "r6_per_order_violations": [],
         "r6_picked_up_violations": []}
    _, active, _ = F(BAG, RAJ, m, False, flag_on=True, **KW)
    assert active is True  # R6=35 (granica) ≤ hard_max → OK


def test_historical_patterns_fire():
    # 3 z 8 trafień replay (różne restauracje, ten sam adres, R6 czyste, committed OK)
    cases = [
        ([{"delivery_coords": [53.10, 23.10]}], (53.1005, 23.1005)),   # ~70 m
        ([{"delivery_coords": [53.13, 23.18]}], (53.1300, 23.1800)),   # 0 m (drop-dist 0.0)
    ]
    for bag, nd in cases:
        _, active, bonus = F(bag, nd, M_OK, False, flag_on=True, **KW)
        assert active is True and bonus > 0


def test_empty_bag_inactive():
    _, active, bonus = F([], RAJ, M_OK, False, flag_on=True, **KW)
    assert active is False and bonus == 0.0


def test_flag_wiring_on_off(monkeypatch):
    # toggluje PRAWDZIWĄ flagę decyzyjną ENABLE_BUNDLE_DELIVERY_COLOCATION
    # (decision_flag: flags.json → stała modułu → False; brak w flags.json → patch działa)
    monkeypatch.setattr(C, "ENABLE_BUNDLE_DELIVERY_COLOCATION", False, raising=False)
    off = F(BAG, RAJ, M_OK, False,
            flag_on=C.decision_flag("ENABLE_BUNDLE_DELIVERY_COLOCATION"), **KW)
    monkeypatch.setattr(C, "ENABLE_BUNDLE_DELIVERY_COLOCATION", True, raising=False)
    on = F(BAG, RAJ, M_OK, False,
           flag_on=C.decision_flag("ENABLE_BUNDLE_DELIVERY_COLOCATION"), **KW)
    assert off[1] is False and on[1] is True
    assert off != on  # PRAWDZIWA flaga zmienia decyzję


# --- #geocode-centroid guard (audyt 28.06): wyklucz fałszywy 0km coloc na defaultowym centroidzie ---
CENTROID = (53.1325, 23.1688)   # BIALYSTOK_CENTER — Google zwraca to dla nieznanego adresu (122 adresy cache)


def test_centroid_guard_blocks_fake_coloc():
    # dwa drops OBA na centroidzie miasta = fałszywy 0km. guard OFF=kredyt (bug), ON=wykluczony.
    bag = [{"delivery_coords": list(CENTROID)}]
    off = F(bag, CENTROID, M_OK, False, flag_on=True, centroid_guard=False, **KW)
    on = F(bag, CENTROID, M_OK, False, flag_on=True, centroid_guard=True, **KW)
    assert off[1] is True and off[2] > 0          # OFF: fałszywy bundle (stan sprzed fixu)
    assert on[1] is False and on[2] == 0.0        # ON: guard wyklucza centroid


def test_centroid_guard_preserves_real_coloc():
    # realny coloc (case 509, 37m, daleko od centrum) — guard ON NIE rusza
    on = F(BAG, RAJ, M_OK, False, flag_on=True, centroid_guard=True, **KW)
    assert on[1] is True and on[2] > 0


def test_centroid_guard_bag_drop_on_centroid_skipped():
    # nowa dostawa realna, ale drop w bagu na centroidzie → ta para pominięta (jej 0km fałszywy)
    bag = [{"delivery_coords": list(CENTROID)}]
    on = F(bag, RAJ, M_OK, False, flag_on=True, centroid_guard=True, **KW)
    assert on[1] is False and on[2] == 0.0


def test_centroid_guard_flag_wiring_on_off(monkeypatch):
    # wiring ENABLE_BUNDLE_COLOC_CENTROID_GUARD (ETAP4 dowód nie-martwoty): OFF=fałszywy centroid
    # coloc, ON=wykluczony. decision_flag czyta (zestrippowany) flags.json → fallback stała modułu.
    bag = [{"delivery_coords": list(CENTROID)}]
    monkeypatch.setattr(C, "ENABLE_BUNDLE_COLOC_CENTROID_GUARD", False, raising=False)
    off = F(bag, CENTROID, M_OK, False, flag_on=True,
            centroid_guard=C.decision_flag("ENABLE_BUNDLE_COLOC_CENTROID_GUARD"), **KW)
    monkeypatch.setattr(C, "ENABLE_BUNDLE_COLOC_CENTROID_GUARD", True, raising=False)
    on = F(bag, CENTROID, M_OK, False, flag_on=True,
           centroid_guard=C.decision_flag("ENABLE_BUNDLE_COLOC_CENTROID_GUARD"), **KW)
    assert off[1] is True and on[1] is False
    assert off != on  # PRAWDZIWA flaga zmienia decyzję
