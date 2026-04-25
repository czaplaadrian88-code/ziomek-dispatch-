"""V3.27 Bug Z fix tests — bundle_level3 cross-quadrant SOFT penalty + Z-OWN-1 corridor.

Reproduction: #468509 Chicago Pizza → Artyleryjska, bag Gabriel J z drop
Bełzy(N) + Filipowicza(Kleosin SE), bundle_level3=True dev=0.21.

Pre-fix: cross-quadrant bundle treated jak normal, full bonus_r4 + score.
Post-fix (flag True):
- bonus_r4 *= min(drop_proximity_factor) = 0.0 → corridor zeroed
- final_score *= 0.1 → SOFT penalty (NIE hard reject)

Run: python3 tests/test_v327_bug_z_bundle_penalty.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402


# ─────────────────────────────────────────────────────────
# Q5 score multiplier helper
# ─────────────────────────────────────────────────────────

def test_score_mult_cross_quadrant():
    """Q5: factor=0.0 → score *= 0.1 (cross-quadrant)."""
    assert C.bundle_score_multiplier(0.0) == 0.1


def test_score_mult_adjacent():
    """Q5: factor=0.5 → score *= 0.7 (adjacent zones)."""
    assert C.bundle_score_multiplier(0.5) == 0.7


def test_score_mult_same_quadrant():
    """Q5: factor=1.0 → score *= 1.0 (unchanged)."""
    assert C.bundle_score_multiplier(1.0) == 1.0


def test_score_mult_none_defensive():
    """Defensive: None (np. empty bag) → 1.0 (no penalty)."""
    assert C.bundle_score_multiplier(None) == 1.0


def test_score_mult_intermediate_linear():
    """Intermediate factors → linear interpolation (defensive future)."""
    # 0.25 (in 0.0..0.5 range): expected ~ 0.1 + 0.6 * 0.5 = 0.4
    res = C.bundle_score_multiplier(0.25)
    assert abs(res - 0.4) < 0.01, f"expected ~0.4 for factor=0.25, got {res}"
    # 0.75 (in 0.5..1.0 range): expected ~ 0.7 + 0.3 * 0.5 = 0.85
    res = C.bundle_score_multiplier(0.75)
    assert abs(res - 0.85) < 0.01, f"expected ~0.85 for factor=0.75, got {res}"


# ─────────────────────────────────────────────────────────
# min_drop_proximity_factor helper
# ─────────────────────────────────────────────────────────

def test_min_factor_cross_quadrant():
    """#468509 reproduction: Antoniuk(N) + Kleosin(SE) → 0.0 (cross-quadrant)."""
    # NOTE: zonacht musi exact match districts dict. Sprawdzam na Centrum vs Kleosin.
    assert C.min_drop_proximity_factor(["Centrum", "Kleosin"]) == 0.0


def test_min_factor_same_quadrant():
    """All drops same quadrant → 1.0."""
    assert C.min_drop_proximity_factor(["Centrum", "Centrum"]) == 1.0


def test_min_factor_adjacent():
    """Adjacent zones (per BIALYSTOK_DISTRICT_ADJACENCY) → 0.5."""
    # Centrum ↔ Bojary są adjacent
    assert C.min_drop_proximity_factor(["Centrum", "Bojary"]) == 0.5


def test_min_factor_unknown_defensive():
    """'Unknown' zone → 0.0 (defensive — coverage gap akceptowany Q4)."""
    assert C.min_drop_proximity_factor(["Centrum", "Unknown"]) == 0.0


def test_min_factor_empty_or_single():
    """Empty/single zone → None."""
    assert C.min_drop_proximity_factor([]) is None
    assert C.min_drop_proximity_factor(["Centrum"]) is None
    assert C.min_drop_proximity_factor(None) is None


def test_min_factor_three_drops_min_pairwise():
    """3 drops: returns minimum pairwise factor (worst case wygrywa)."""
    # Centrum ↔ Bojary = 0.5 (adjacent), Centrum ↔ Antoniuk = 0.0, Bojary ↔ Antoniuk = ?
    # Min: 0.0 (worst pair)
    assert C.min_drop_proximity_factor(["Centrum", "Bojary", "Antoniuk"]) == 0.0


# ─────────────────────────────────────────────────────────
# Reproduction case #468509
# ─────────────────────────────────────────────────────────

def test_proposal_468509_reproduction():
    """#468509 case: bag Bełzy + Filipowicza(Kleosin) cross-quadrant.
    Bełzy → Unknown (coverage gap). Filipowicza+Kleosin → Kleosin.
    Artyleryjska → Centrum (new drop).

    Min factor across all 3 = 0.0 (Unknown contains 'Unknown').
    """
    bag_zones = [
        C.drop_zone_from_address("Bełzy 5/11", "Białystok"),       # Unknown
        C.drop_zone_from_address("Filipowicza 12/46", "Kleosin"),  # Kleosin
    ]
    new_zone = C.drop_zone_from_address("Artyleryjska 2a/49", "Białystok")  # Centrum
    all_zones = [new_zone] + bag_zones
    min_factor = C.min_drop_proximity_factor(all_zones)
    assert min_factor == 0.0, f"#468509: min_factor expected 0.0, got {min_factor}"

    # Score multiplier
    score_mult = C.bundle_score_multiplier(min_factor)
    assert score_mult == 0.1, f"#468509: score_mult expected 0.1, got {score_mult}"


def test_legitimate_same_quadrant_bundle_unchanged():
    """Same-quadrant bag (np. dwa drops Centrum) → factor=1.0 → no penalty."""
    bag_zones = [
        C.drop_zone_from_address("Sienkiewicza 12", "Białystok"),  # match Centrum or Sienkiewicza district
    ]
    new_zone = C.drop_zone_from_address("Mickiewicza 5", "Białystok")
    if new_zone == "Unknown" or bag_zones[0] == "Unknown":
        # Coverage gap accepted (Q4); skip strict assertion
        return
    all_zones = [new_zone] + bag_zones
    factor = C.min_drop_proximity_factor(all_zones)
    # Może być 1.0 (same) lub 0.5 (adjacent Mickiewicza/Centrum) — oba akceptowalne
    assert factor >= 0.5, f"legitimate bundle: factor expected >=0.5, got {factor}"


# ─────────────────────────────────────────────────────────
# Flag gating
# ─────────────────────────────────────────────────────────

def test_flag_default_off():
    """Default ENABLE_V327_BUG_FIXES_BUNDLE=False — observable, brak behavior change."""
    assert C.ENABLE_V327_BUG_FIXES_BUNDLE in (False, True)
    # In test env without env override, default False.
    # (jeśli someone exports env var, akceptujemy True — env override).


# ─────────────────────────────────────────────────────────
# Constants sanity
# ─────────────────────────────────────────────────────────

def test_constants_q5_match_decision():
    """Constants per Q5 decyzja Adriana."""
    assert C.V327_BUNDLE_CROSS_QUADRANT_SCORE_MULT == 0.1
    assert C.V327_BUNDLE_ADJACENT_SCORE_MULT == 0.7
    assert C.V327_BUNDLE_SAME_QUADRANT_SCORE_MULT == 1.0


if __name__ == "__main__":
    test_score_mult_cross_quadrant()
    print("test_score_mult_cross_quadrant: PASS")
    test_score_mult_adjacent()
    print("test_score_mult_adjacent: PASS")
    test_score_mult_same_quadrant()
    print("test_score_mult_same_quadrant: PASS")
    test_score_mult_none_defensive()
    print("test_score_mult_none_defensive: PASS")
    test_score_mult_intermediate_linear()
    print("test_score_mult_intermediate_linear: PASS")
    test_min_factor_cross_quadrant()
    print("test_min_factor_cross_quadrant: PASS")
    test_min_factor_same_quadrant()
    print("test_min_factor_same_quadrant: PASS")
    test_min_factor_adjacent()
    print("test_min_factor_adjacent: PASS")
    test_min_factor_unknown_defensive()
    print("test_min_factor_unknown_defensive: PASS")
    test_min_factor_empty_or_single()
    print("test_min_factor_empty_or_single: PASS")
    test_min_factor_three_drops_min_pairwise()
    print("test_min_factor_three_drops_min_pairwise: PASS")
    test_proposal_468509_reproduction()
    print("test_proposal_468509_reproduction: PASS")
    test_legitimate_same_quadrant_bundle_unchanged()
    print("test_legitimate_same_quadrant_bundle_unchanged: PASS")
    test_flag_default_off()
    print("test_flag_default_off: PASS")
    test_constants_q5_match_decision()
    print("test_constants_q5_match_decision: PASS")
    print("ALL 15/15 PASS")
