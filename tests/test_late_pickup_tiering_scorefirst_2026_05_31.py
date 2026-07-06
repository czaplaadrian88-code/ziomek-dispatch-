"""R-LATE-PICKUP Opcja B (2026-05-31) — score-first tiering + shadow counterfactual.

Naprawia nadkorektę starego tieringu: tier-0 (odbiór ≤5 min na czas) bił KAŻDY tier-1
NIEZALEŻNIE od score → krzyżowo-miejskie bundle wygrywały mimo R1/R6 w score
(477330 Andrei −5.3 11.7km bił Michała Ro +36.4 1-zlec.). Opcja B: tier-2 (łamanie
committed) twardy demote; reszta ranking po score (z V3.16 demote-bucketami) MINUS
gradient kara ∝ new_pickup_late_min. Stary tiering liczony równolegle w cieniu.

Spec: eod_drafts/2026-05-31/SPEC_late_pickup_tiering_fix.md
"""
from dispatch_v2.core import selection as _k12s  # K12: selekcja/werdykt (skan obu zrodel)
import importlib
import inspect

from dispatch_v2 import common, dispatch_pipeline, shadow_dispatcher
from dispatch_v2.dispatch_pipeline import (
    _late_pickup_tier,
    _late_pickup_soft_penalty,
    _late_pickup_score_first_key,
)


class FakeCand:
    """Minimalny kandydat do testu sortu (score + metrics + tożsamość)."""
    def __init__(self, cid, name, score, pos_source, late_min=0.0,
                 needs_ext=False, committed_breach=False, bag_size=1,
                 spread=None, r6=None):
        self.courier_id = cid
        self.name = name
        self.score = score
        self.feasibility_verdict = "MAYBE"
        self.metrics = {
            "pos_source": pos_source,
            "new_pickup_late_min": late_min,
            "new_pickup_needs_extension": needs_ext,
            "late_pickup_committed_breach": committed_breach,
            "r6_bag_size": bag_size,
            "deliv_spread_km": spread,
            "r6_max_bag_time_min": r6,
        }


def _sort_optionB(cands, free=5.0, coeff=1.5, cap=60.0):
    orig = {id(c): i for i, c in enumerate(cands)}
    return sorted(cands, key=lambda c: _late_pickup_score_first_key(
        c, _late_pickup_tier(c), orig[id(c)], free, coeff, cap))


# === flaga + stałe ===

def test_flag_present_default_on():
    assert common.ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST is True


def test_soft_constants_defaults():
    assert common.LATE_PICKUP_SOFT_FREE_MIN == 5.0
    assert common.LATE_PICKUP_SOFT_COEFF == 1.5
    assert common.LATE_PICKUP_SOFT_CAP == 60.0


def test_flag_off_via_env(monkeypatch):
    monkeypatch.setenv("ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", "0")
    m = importlib.reload(common)
    assert m.ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST is False
    monkeypatch.setenv("ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", "1")
    importlib.reload(common)


# === tier classification ===

def test_tier_levels():
    assert _late_pickup_tier(FakeCand("1", "a", 0, "gps")) == 0
    assert _late_pickup_tier(FakeCand("1", "a", 0, "gps", needs_ext=True)) == 1
    assert _late_pickup_tier(FakeCand("1", "a", 0, "gps", committed_breach=True)) == 2
    # committed breach wins nad needs_ext
    assert _late_pickup_tier(
        FakeCand("1", "a", 0, "gps", needs_ext=True, committed_breach=True)) == 2


# === soft penalty math ===

def test_soft_penalty_free_zone():
    assert _late_pickup_soft_penalty(FakeCand("1", "a", 0, "gps", late_min=4.0), 5.0, 1.5, 60.0) == 0.0
    assert _late_pickup_soft_penalty(FakeCand("1", "a", 0, "gps", late_min=5.0), 5.0, 1.5, 60.0) == 0.0


def test_soft_penalty_gradient():
    # 15 min late → (15-5)*1.5 = 15.0
    assert _late_pickup_soft_penalty(FakeCand("1", "a", 0, "gps", late_min=15.0), 5.0, 1.5, 60.0) == 15.0


def test_soft_penalty_cap():
    # 200 min → cap 60
    assert _late_pickup_soft_penalty(FakeCand("1", "a", 0, "gps", late_min=200.0), 5.0, 1.5, 60.0) == 60.0


def test_soft_penalty_missing_metric_zero():
    c = FakeCand("1", "a", 0, "gps")
    c.metrics["new_pickup_late_min"] = None
    assert _late_pickup_soft_penalty(c, 5.0, 1.5, 60.0) == 0.0


# === KLUCZOWY scenariusz 477330: tier-0 cross-city NIE bije tier-1 high-score ===

def test_477330_tier0_bundle_does_not_beat_tier1_highscore():
    andrei = FakeCand("484", "Andrei K", -5.3, "gps", late_min=2.0,
                      needs_ext=False, spread=11.71, r6=32.0)          # tier 0 (na czas)
    michal = FakeCand("518", "Michał Ro", 36.4, "last_assigned_pickup",
                      late_min=10.0, needs_ext=True, spread=7.8, r6=25.0)  # tier 1
    out = _sort_optionB([andrei, michal])
    # Opcja B: Michał adj = 36.4 - 1.5*(10-5)=28.9 > Andrei -5.3 → Michał wygrywa
    assert out[0].courier_id == "518", "tier-1 high-score MUSI bić tier-0 cross-city low-score"
    assert out[1].courier_id == "484"


def test_extension_acceptable_when_delivery_much_better():
    """Adrian: lepiej przedłużyć odbiór i zawieźć szybko. Kara gentle (coeff 1.5)."""
    fast_deliv = FakeCand("1", "good", 30.0, "gps", late_min=20.0, needs_ext=True, r6=20.0)
    slow_deliv = FakeCand("2", "bad", 0.0, "gps", late_min=0.0, r6=34.0)  # tier 0, ale słaby dowóz
    out = _sort_optionB([fast_deliv, slow_deliv])
    # good adj = 30 - 1.5*15 = 7.5 > bad 0.0 → przedłużenie odbioru wygrywa
    assert out[0].courier_id == "1"


# === tier-2 committed breach = ostateczność (twardy demote) ===

def test_tier2_committed_breach_demoted_last():
    breach = FakeCand("1", "breach", 100.0, "gps", committed_breach=True)  # wysoki score ALE łamie committed
    ontime = FakeCand("2", "ontime", -10.0, "gps")                         # niski score, tier 0
    out = _sort_optionB([breach, ontime])
    assert out[0].courier_id == "2", "tier-2 (łamanie committed) idzie na koniec mimo wysokiego score"
    assert out[-1].courier_id == "1"


# === V3.16 demote zachowany: blind+empty zostaje ostatni ===

def test_blind_empty_stays_last():
    blind = FakeCand("1", "Gabriel", 120.0, "no_gps", bag_size=0)   # blind+empty, zawyżony score
    informed = FakeCand("2", "Andrei", -3.0, "gps")                 # informed, niski score
    out = _sort_optionB([blind, informed])
    assert out[0].courier_id == "2", "informed bije blind+empty mimo score 120 (V3.16)"
    assert out[-1].courier_id == "1"


def test_informed_before_other_before_blind():
    informed = FakeCand("1", "inf", -20.0, "gps")
    other = FakeCand("2", "oth", 50.0, None)  # pos_source None → ani informed ani blind → bucket 1
    blind = FakeCand("3", "bli", 200.0, "no_gps", bag_size=0)
    out = _sort_optionB([blind, other, informed])
    assert [c.courier_id for c in out] == ["1", "2", "3"]


# === Fix #5 (2026-05-31): last_picked_up_pickup = INFORMED ===

def test_fix5_last_picked_up_pickup_is_informed():
    from dispatch_v2.dispatch_pipeline import INFORMED_POS_SOURCES, _is_informed_cand
    assert "last_picked_up_pickup" in INFORMED_POS_SOURCES
    assert _is_informed_cand(FakeCand("1", "x", 0, "last_picked_up_pickup")) is True


def test_fix5_477329_pawel_no_longer_demoted_to_other():
    """477329: Paweł SC (last_picked_up_pickup, +12.2) MUSI być w bucket informed (0),
    nie spychany pod gorzej-punktowanych informed jak przed fix #5."""
    pawel = FakeCand("376", "Paweł SC", 12.2, "last_picked_up_pickup",
                     late_min=8.0, needs_ext=True)          # tier 1, ale top-score
    jakub = FakeCand("370", "Jakub OL", -5.4, "last_assigned_pickup", late_min=2.0)  # tier 0
    out = _sort_optionB([jakub, pawel])
    # Paweł adj = 12.2 - 1.5*(8-5)=7.7 > Jakub -5.4 → Paweł wygrywa (oba bucket 0 informed)
    assert out[0].courier_id == "376", "Paweł (informed po fix #5) bije Jakuba mimo tier-1"


# === source-regression: LIVE + shadow + serializer ===

def test_optionB_block_present():
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    assert "ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST" in src
    assert "_late_pickup_score_first_key" in src


def test_shadow_counterfactual_computed():
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    assert "_old_winner" in src
    assert "late_pickup_shadow" in src
    assert "LATE_PICKUP_SCORE_FIRST_DIVERGENCE" in src
    # stary tiering liczony bez mutacji feasible (sorted, nie .sort)
    assert "_old_sorted = sorted(feasible" in src


def test_shadow_attached_to_result():
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    assert "_result_pf.late_pickup_shadow = late_pickup_shadow" in src


def test_shadow_serialized():
    src = inspect.getsource(shadow_dispatcher)
    assert '"late_pickup_shadow"' in src
    assert 'getattr(result, "late_pickup_shadow", None)' in src


def test_flag_off_falls_back_to_old_tiering():
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    idx = src.find("ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", src.find("_old_winner"))
    section = src[idx:idx + 800]
    # gałąź else = identyczny stary in-place sort
    assert "_lp_tier(c), _orig_order[id(c)]" in section


def test_per_candidate_late_pickup_serialized():
    """LOCATION A — per-candidate metryki late-pickup w _serialize_candidate.

    Bez prefiksu auto-prop → MUSZĄ być explicit. Bez tego tier per-candidate jest
    niewidoczny w shadow logu (tylko zwycięzca) → łamie encoding-checklist (Lekcja #80,
    SPEC §6 krok 1: audytowalność Opcji B score-first).
    """
    src = inspect.getsource(shadow_dispatcher._serialize_candidate)
    for key in (
        "late_pickup_max_min",
        "late_pickup_committed_max",
        "late_pickup_committed_breach",
        "new_pickup_late_min",
        "new_pickup_eta_iso",
        "new_pickup_needs_extension",
    ):
        assert f'"{key}": m.get("{key}")' in src, f"LOCATION A brak klucza {key}"
