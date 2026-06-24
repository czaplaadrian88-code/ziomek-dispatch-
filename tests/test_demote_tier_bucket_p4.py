"""P-4 (audyt 2026-06-24): niezmiennik V3.16 demote vs R-LATE-PICKUP tiering.

NIEZMIENNIK BEZPIECZEŃSTWA (V3.16, case #467189/#474624): zdemotowany kandydat
„blind+empty" (syntetyczna pozycja, pusty bag — NIE wiadomo gdzie jest) NIE MOŻE
wyprzedzić kandydata „informed" (realna pozycja) — niezależnie od tiera spóźnienia
odbioru. Operator i tak nadpisuje takie propozycje (PANEL_OVERRIDE 19.6%).

ŻYWA ścieżka = Opcja B (`ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST=True`): klucz
`_late_pickup_score_first_key` ma bucket demote WYSOKO → niezmiennik trzymany. ✅
DORMANT ścieżka = OFF-mode (klucz `(_lp_tier, _orig_order)`): tier DOMINUJE, demote
tylko w tie-breaku → blind+empty z lepszym tierem wraca na top. ❌ (bug, nie-live).
"""
import pytest
from dispatch_v2 import dispatch_pipeline as D


class _Cand:
    def __init__(self, cid, score, pos_source, bag_size,
                 needs_extension=False, committed_breach=False):
        self.courier_id = cid
        self.score = score
        self.metrics = {
            "pos_source": pos_source,
            "r6_bag_size": bag_size,
            "new_pickup_needs_extension": needs_extension,
            "late_pickup_committed_breach": committed_breach,
            "new_pickup_late_min": 0,
        }


def _scenario():
    """Klasyczny konflikt: blind+empty (wyższy score, lepszy tier) vs informed (niższy
    score, gorszy tier). Po demote informed jest 1., blind ostatni — sprawdzamy czy
    sort tieringu to UTRZYMUJE."""
    informed = _Cand("A_informed", 50.0, D.INFORMED_POS_SOURCES[0], bag_size=2,
                     needs_extension=True)          # tier 1 (potrzebuje przedłużenia)
    blind = _Cand("B_blind_preshift", 90.0, "pre_shift", bag_size=0)  # tier 0 (na czas)
    # stan przed demote: blind na topie (wyższy score)
    feasible = D._demote_blind_empty([blind, informed], "TEST_P4")
    assert [c.courier_id for c in feasible] == ["A_informed", "B_blind_preshift"], \
        "demote musi ustawić informed przed blind+empty"
    orig = {id(c): i for i, c in enumerate(feasible)}
    return feasible, orig


def test_demote_sanity():
    """_demote_blind_empty faktycznie spycha blind+empty pod informed."""
    feasible, _ = _scenario()
    assert feasible[0].courier_id == "A_informed"


def test_optionB_preserves_demote_across_tiers():
    """ŻYWA ścieżka (Opcja B): informed wygrywa MIMO gorszego tiera. Niezmiennik trzymany."""
    feasible, orig = _scenario()
    lp = D._late_pickup_tier
    out = sorted(feasible, key=lambda c: D._late_pickup_score_first_key(
        c, lp(c), orig[id(c)], 5.0, 1.5, 60.0))
    assert out[0].courier_id == "A_informed", \
        "Opcja B: blind+empty (tier0) NIE może wyprzedzić informed (tier1)"


@pytest.mark.xfail(reason="P-4: OFF-mode klucz (_lp_tier,_orig_order) — tier dominuje, "
                          "demote ginie; fix = dołożyć bucket demote. Ścieżka dormant "
                          "(SCORE_FIRST=True żywo), test pilnuje przed flipem.",
                   strict=True)
def test_offmode_preserves_demote_across_tiers():
    """DORMANT ścieżka (OFF-mode): TEN SAM niezmiennik. Dziś PADA (xfail) — dowód buga.
    Po fixie (bucket w kluczu OFF-mode) usuń xfail → ma przejść."""
    feasible, orig = _scenario()
    lp = D._late_pickup_tier
    out = sorted(feasible, key=lambda c: (lp(c), orig[id(c)]))   # klucz OFF-mode (dp:5545)
    assert out[0].courier_id == "A_informed", \
        "OFF-mode: blind+empty (tier0) wraca na top mimo demote — INWERSJA"
