"""R6BREACH-01 / GATE-02 SHADOW (2026-06-05) — post-selekcyjny guard R6, shadow-only.

Diagnoza (eod_drafts/2026-06-05/R6BREACH_01_GATE_02_design.md): R6 (35-min hard
odbiór→dostawa) jest dla worków egzekwowane TYLKO jako SOFT (kara w score), więc
kandydat z wysokim score bazowym może wygrać mimo r6_max_bag_time_min>35 nawet gdy w
puli jest feasible ≤35. Ten shadow liczy „kogo wskazałby guard" obok live. Tu testujemy
CZYSTĄ funkcję _r6_breach_guard_winner + obecność flagi. ZERO mutacji feasible.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common  # noqa: E402
from dispatch_v2.dispatch_pipeline import _r6_breach_guard_winner  # noqa: E402


class FakeCand:
    def __init__(self, cid, score, r6, pos_source="gps", bag_size=2):
        self.courier_id = cid
        self.name = f"K-{cid}"
        self.score = score
        self.feasibility_verdict = "MAYBE"
        self.metrics = {
            "r6_max_bag_time_min": r6,
            "pos_source": pos_source,
            "r6_bag_size": bag_size,
        }


HARD = 35.0


# ── flaga ──

def test_flag_default_off():
    assert common.ENABLE_R6_BREACH_GUARD_SHADOW is False


def test_hard_max_constant():
    assert common.BAG_TIME_HARD_MAX_MIN == 35


# ── logika guarda ──

def test_empty_pool():
    w, ch, r, n = _r6_breach_guard_winner([], HARD)
    assert w is None and ch is False and r == "empty_pool" and n == 0


def test_live_within_r6_no_change():
    # live r6=30 (≤35) → brak guarda
    pool = [FakeCand("1", 50, 30.0), FakeCand("2", 10, 25.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is False and r == "live_within_r6" and w.courier_id == "1" and n == 0


def test_live_at_boundary_35_is_within():
    # r6 dokładnie 35 = ≤ hard → NIE breach
    pool = [FakeCand("1", 50, 35.0), FakeCand("2", 10, 20.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is False and r == "live_within_r6"


def test_live_r6_none_treated_as_within():
    # brak sygnału R6 dla live → konserwatywnie nie guardujemy
    pool = [FakeCand("1", 50, None, bag_size=0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is False and r == "live_within_r6"


def test_live_breaches_no_clean_alt():
    # live r6=40 breach, jedyny alt r6=38 też breach → brak czystego
    pool = [FakeCand("1", 50, 40.0), FakeCand("2", 30, 38.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is False and r == "no_clean_alt" and w.courier_id == "1" and n == 0


def test_live_breaches_one_clean_alt_swaps():
    # live r6=42 breach, alt r6=25 czysty → guard wskazuje alt
    pool = [FakeCand("1", 50, 42.0), FakeCand("2", 12, 25.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is True and r == "r6_guard_applied" and w.courier_id == "2" and n == 1


def test_guard_picks_highest_score_among_clean():
    # live breach, wiele czystych → najlepszy score (NIE pierwszy) wygrywa
    pool = [FakeCand("1", 50, 41.0),
            FakeCand("2", 5, 30.0),
            FakeCand("3", 18, 28.0),
            FakeCand("4", 11, 33.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is True and w.courier_id == "3" and n == 3


def test_clean_alt_boundary_35_counts():
    # alt r6 dokładnie 35 = czysty (≤ hard)
    pool = [FakeCand("1", 50, 40.0), FakeCand("2", 9, 35.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is True and w.courier_id == "2" and n == 1


def test_clean_alt_with_none_r6_excluded():
    # live breach; jedyny alt ma r6=None → NIE liczy się jako czysty → brak swapu
    pool = [FakeCand("1", 50, 44.0), FakeCand("2", 30, None, bag_size=0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    assert ch is False and r == "no_clean_alt" and n == 0


def test_n_clean_alts_count_mixed():
    # live breach; alts: 25(czysty), 50(breach), None(excl), 33(czysty) → n=2
    pool = [FakeCand("1", 60, 39.0),
            FakeCand("2", 20, 25.0),
            FakeCand("3", 15, 50.0),
            FakeCand("4", 8, None),
            FakeCand("5", 22, 33.0)]
    w, ch, r, n = _r6_breach_guard_winner(pool, HARD)
    # najlepszy score wśród czystych {2:20, 5:22} = cid 5
    assert ch is True and w.courier_id == "5" and n == 2


def test_no_mutation_of_input_order():
    # SHADOW: helper NIE zmienia kolejności feasible (live[0] zostaje live[0])
    pool = [FakeCand("1", 50, 42.0), FakeCand("2", 12, 25.0)]
    _r6_breach_guard_winner(pool, HARD)
    assert pool[0].courier_id == "1" and pool[1].courier_id == "2"
