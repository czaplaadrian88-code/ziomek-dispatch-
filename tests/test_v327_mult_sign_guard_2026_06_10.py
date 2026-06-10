"""Z-02 (audyt 2026-06-10) — sign-guard mnożnika Bug Z + rozdzielenie Unknown.

Bug 1: `final_score *= v327_bundle_score_mult` bez guardu znaku — na UJEMNYM
score ×0.1 = 10× POPRAWA (−80 → −8 bije −50 same-quadrant). Inwersja rankingu
w trudnych przypadkach (wszyscy kandydaci ujemni).

Bug 2: 'Unknown' (luka pokrycia districts) traktowany jak realny cross-quadrant
(factor 0.0 → mult 0.1) — brak danych karany jak twardy sygnał geometryczny.

Fix: common.apply_bundle_score_mult (guard znaku) +
common.min_drop_proximity_factor_split (Unknown → mult 0.7, znany cross → 0.1).
Flaga ENABLE_V327_MULT_SIGN_GUARD (env default ON, hot-reload kill-switch
flags.json). Pipeline: dispatch_pipeline.py blok v327 (compute + apply).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402


# ─────────────────────────────────────────────────────────
# apply_bundle_score_mult — guard znaku
# ─────────────────────────────────────────────────────────

def test_sign_guard_directional_neg80_cross_vs_neg50_same():
    """KIERUNKOWY (spec ETAP 2): −80 cross-quadrant NIE pokonuje −50 same-quadrant.

    Pre-fix: −80 × 0.1 = −8 > −50 → inwersja (gorszy geometrycznie wygrywa).
    Post-fix: −80 zostaje −80 < −50 → ranking poprawny.
    """
    cross_score, guarded = C.apply_bundle_score_mult(-80.0, 0.1, sign_guard_on=True)
    same_score, _ = C.apply_bundle_score_mult(-50.0, 1.0, sign_guard_on=True)
    assert guarded is True
    assert cross_score == -80.0
    assert cross_score < same_score, (
        f"cross-quadrant {cross_score} musi przegrywać z same-quadrant {same_score}")


def test_sign_guard_off_preserves_legacy_inversion():
    """Kill-switch OFF = stare zachowanie (inwersja wraca) — rollback path."""
    score, guarded = C.apply_bundle_score_mult(-80.0, 0.1, sign_guard_on=False)
    assert guarded is False
    assert abs(score - (-8.0)) < 1e-9


def test_sign_guard_positive_score_mult_applies():
    """Dodatni score: mnożnik działa normalnie (kara zachowana)."""
    score, guarded = C.apply_bundle_score_mult(80.0, 0.1, sign_guard_on=True)
    assert guarded is False
    assert abs(score - 8.0) < 1e-9


def test_sign_guard_zero_score_not_multiplied():
    """Score 0.0: mnożenie no-op matematycznie, guard oznacza pominięcie."""
    score, guarded = C.apply_bundle_score_mult(0.0, 0.1, sign_guard_on=True)
    assert score == 0.0
    assert guarded is True


def test_mult_one_is_noop_regardless_of_sign():
    """mult=1.0 (same-quadrant / pusty bag / flaga bundle OFF) → no-op, nie guarded."""
    for s in (-50.0, 0.0, 70.0):
        score, guarded = C.apply_bundle_score_mult(s, 1.0, sign_guard_on=True)
        assert score == s
        assert guarded is False


def test_sign_guard_default_reads_module_flag():
    """sign_guard_on=None → fallback do env-const ENABLE_V327_MULT_SIGN_GUARD."""
    prev = C.ENABLE_V327_MULT_SIGN_GUARD
    try:
        C.ENABLE_V327_MULT_SIGN_GUARD = True
        score, guarded = C.apply_bundle_score_mult(-40.0, 0.1)
        assert (score, guarded) == (-40.0, True)
        C.ENABLE_V327_MULT_SIGN_GUARD = False
        score, guarded = C.apply_bundle_score_mult(-40.0, 0.1)
        assert guarded is False
        assert abs(score - (-4.0)) < 1e-9
    finally:
        C.ENABLE_V327_MULT_SIGN_GUARD = prev


# ─────────────────────────────────────────────────────────
# min_drop_proximity_factor_split — Unknown vs realny cross-quadrant
# ─────────────────────────────────────────────────────────

def test_split_real_cross_quadrant_detected():
    """Znane strefy cross-quadrant → (0.0, False) — mult zostaje 0.1."""
    mf, unk = C.min_drop_proximity_factor_split(["Antoniuk", "Kleosin"])
    assert mf == 0.0
    assert unk is False


def test_split_unknown_only_no_geometric_signal():
    """Unknown + 1 znana → (None, True) — brak sygnału geometrycznego."""
    mf, unk = C.min_drop_proximity_factor_split(["Unknown", "Antoniuk"])
    assert mf is None
    assert unk is True


def test_split_same_zone_with_unknown():
    """2× ta sama znana strefa + Unknown → (1.0, True) — mult z Unknown 0.7, nie 0.1."""
    mf, unk = C.min_drop_proximity_factor_split(["Antoniuk", "Antoniuk", "Unknown"])
    assert mf == 1.0
    assert unk is True


def test_split_cross_quadrant_wins_over_unknown():
    """Znany cross-quadrant + Unknown → (0.0, True) — twardy sygnał ma priorytet (0.1)."""
    mf, unk = C.min_drop_proximity_factor_split(["Antoniuk", "Kleosin", "Unknown"])
    assert mf == 0.0
    assert unk is True


def test_split_none_and_empty_treated_as_unknown():
    """None / pusta strefa traktowane jak Unknown (spójnie z drop_proximity_factor)."""
    mf, unk = C.min_drop_proximity_factor_split(["Antoniuk", None])
    assert mf is None and unk is True
    mf, unk = C.min_drop_proximity_factor_split(["Antoniuk", ""])
    assert mf is None and unk is True


def test_split_empty_input_defensive():
    assert C.min_drop_proximity_factor_split([]) == (None, False)
    assert C.min_drop_proximity_factor_split(None) == (None, False)


# ─────────────────────────────────────────────────────────
# Złożenie pipeline'owe: mult z split + Unknown cap 0.7
# ─────────────────────────────────────────────────────────

def _pipeline_mult(zones):
    """Replika logiki bloku v327 w dispatch_pipeline (guard ON): mult finalny."""
    mf_known, has_unknown = C.min_drop_proximity_factor_split(zones)
    mult = C.bundle_score_multiplier(mf_known)
    if has_unknown:
        mult = min(mult, C.V327_BUNDLE_UNKNOWN_SCORE_MULT)
    return mult


def test_compose_unknown_softened_to_07():
    """Unknown-only 0.0: pre-fix mult 0.1, post-fix 0.7 (defensive, nie kara twarda)."""
    assert _pipeline_mult(["Antoniuk", "Unknown"]) == 0.7


def test_compose_real_cross_stays_01():
    """Realny cross-quadrant: 0.1 bez zmian (nawet z Unknown obok)."""
    assert _pipeline_mult(["Antoniuk", "Kleosin"]) == 0.1
    assert _pipeline_mult(["Antoniuk", "Kleosin", "Unknown"]) == 0.1


def test_compose_same_quadrant_noop():
    assert _pipeline_mult(["Antoniuk", "Antoniuk"]) == 1.0
