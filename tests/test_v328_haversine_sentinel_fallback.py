"""V3.28 #28 tech debt: defensive fallback dla (0,0) sentinel leak.

Hot path `_v327_eval_courier` + R29 SOLO fallback przyjmują `cs.pos` z courier_resolver.
courier_resolver.dispatchable_fleet substituuje BIALYSTOK_CENTER dla no_gps, ALE
(0,0) sentinel leakuje przez inne paths (stale GPS API read, missing init).
Bez guard'a haversine raise ValueError (Lekcja #81) → V328_CP_SOLVER_FAIL_PER_COURIER
spam (10.05 ~110/30min peak). Mirror Faza 7 Etap 0 pattern.
"""
import pytest

from dispatch_v2.dispatch_pipeline import (
    _sanitize_courier_pos,
    _BIALYSTOK_CENTER_FALLBACK,
)


def test_sanitize_zero_zero_sentinel_returns_bialystok_center():
    assert _sanitize_courier_pos((0.0, 0.0)) == _BIALYSTOK_CENTER_FALLBACK


def test_sanitize_zero_int_sentinel_returns_bialystok_center():
    """Int (0, 0) — float() coerce w helper."""
    assert _sanitize_courier_pos((0, 0)) == _BIALYSTOK_CENTER_FALLBACK


def test_sanitize_real_position_passthrough():
    real = (53.137686, 23.168566)
    assert _sanitize_courier_pos(real) == real


def test_sanitize_none_passthrough():
    assert _sanitize_courier_pos(None) is None


def test_sanitize_list_form_zero_zero():
    """List [0.0, 0.0] (zamiast tuple) — `len` + `[]` access still works."""
    assert _sanitize_courier_pos([0.0, 0.0]) == _BIALYSTOK_CENTER_FALLBACK


def test_sanitize_invalid_type_returns_none():
    """Non-numeric coords — defensive return None (skip courier)."""
    assert _sanitize_courier_pos(("x", "y")) is None
    assert _sanitize_courier_pos((None, None)) is None


def test_sanitize_single_element_passthrough():
    """Len < 2 — out of scope dla (0,0) sentinel check; passthrough do caller."""
    assert _sanitize_courier_pos((0.0,)) == (0.0,)


def test_bialystok_center_constant_matches_known_value():
    """Match courier_resolver.BIALYSTOK_CENTER + chain_eta.BIALYSTOK_CENTER (3 sources, tech debt OK)."""
    assert _BIALYSTOK_CENTER_FALLBACK == (53.1325, 23.1688)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
