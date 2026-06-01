"""SELECTION VETO SHADOW (2026-06-01) — veto kierunkowe, shadow-only.

Diagnoza (eod_drafts/2026-06-01/SELECTION_cross_direction_verdict.md): przeciw-
kierunkowi zwycięzcy wygrywają przez override klucza selekcji (bucket/tier), nie na
score. Ten shadow liczy „co by wybrał veto kierunkowe" obok live. Tu testujemy CZYSTĄ
funkcję _selection_veto_winner + obecność flag/stałych. ZERO mutacji feasible.
"""
from dispatch_v2 import common
from dispatch_v2.dispatch_pipeline import _selection_veto_winner


class FakeCand:
    def __init__(self, cid, score, cosine, pos_source="gps", bag_size=1, spread=None):
        self.courier_id = cid
        self.name = f"K-{cid}"
        self.score = score
        self.feasibility_verdict = "MAYBE"
        self.metrics = {
            "r1_avg_pairwise_cosine": cosine,
            "pos_source": pos_source,
            "r6_bag_size": bag_size,
            "deliv_spread_km": spread,
        }


B, OK = -0.5, -0.1  # cos_block, cos_ok


# ── flaga + stałe ──

def test_flag_default_off():
    assert common.ENABLE_SELECTION_VETO_SHADOW is False


def test_constants_defaults():
    assert common.SELECTION_VETO_COS_BLOCK == -0.5
    assert common.SELECTION_VETO_COS_OK == -0.1
    assert common.SELECTION_VETO_INFORMED_ONLY is True


# ── logika veta ──

def test_empty_pool():
    w, ch, r = _selection_veto_winner([], B, OK, True)
    assert w is None and ch is False and r == "empty_pool"


def test_live_not_cross_no_change():
    # live aligned (cos +0.9) → brak veta
    pool = [FakeCand("1", 50, 0.9), FakeCand("2", 10, 0.8)]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is False and r == "live_not_cross" and w.courier_id == "1"


def test_live_cross_none_cosine_no_change():
    # live solo (cos None) = brak kierunku → nie jest „cross" → brak veta
    pool = [FakeCand("1", 50, None, bag_size=0)]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is False and r == "live_not_cross"


def test_veto_applies_to_informed_aligned():
    # live mocno-cross (-0.99), alt informed aligned (+0.8) → veto na alt
    pool = [FakeCand("1", 20, -0.99), FakeCand("2", 5, 0.8, pos_source="gps")]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is True and r == "veto_applied" and w.courier_id == "2"


def test_veto_picks_highest_score_noncross():
    # wiele nie-cross → najlepszy score wygrywa
    pool = [FakeCand("1", 20, -0.99),
            FakeCand("2", 5, 0.5, pos_source="gps"),
            FakeCand("3", 12, 0.2, pos_source="gps")]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is True and w.courier_id == "3"


def test_veto_solo_alt_counts_as_noncross():
    # alt solo (cos None) = brak konfliktu → kwalifikuje się
    pool = [FakeCand("1", 20, -0.99),
            FakeCand("2", 8, None, pos_source="gps", bag_size=0)]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is True and w.courier_id == "2"


def test_no_noncross_alt():
    # wszystkie alts też cross → brak zmiany
    pool = [FakeCand("1", 20, -0.99), FakeCand("2", 30, -0.7, pos_source="gps")]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is False and r == "no_noncross_alt" and w.courier_id == "1"


def test_informed_only_excludes_blind():
    # jedyny nie-cross to no_gps → informed_only=True blokuje, False przepuszcza
    pool = [FakeCand("1", 20, -0.99),
            FakeCand("2", 8, 0.7, pos_source="no_gps", bag_size=0)]
    w_inf, ch_inf, r_inf = _selection_veto_winner(pool, B, OK, True)
    assert ch_inf is False and r_inf == "no_noncross_alt"
    w_any, ch_any, _ = _selection_veto_winner(pool, B, OK, False)
    assert ch_any is True and w_any.courier_id == "2"


def test_cos_block_threshold_boundary():
    # live cos = -0.4 (> block -0.5) → NIE cross → brak veta
    pool = [FakeCand("1", 20, -0.4), FakeCand("2", 5, 0.8)]
    w, ch, r = _selection_veto_winner(pool, B, OK, True)
    assert ch is False and r == "live_not_cross"
