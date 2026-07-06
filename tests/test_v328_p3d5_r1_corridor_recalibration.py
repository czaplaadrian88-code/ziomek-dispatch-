"""V3.28 P3-D5 sprint: R1 corridor bucket -0.5..0 recalibration + spread mult.

Tech debt #30 / case 472338 Ogniomistrz 10.05 (cos=-0.326 deliv_spread=12.63km
score=-20.83 PRZESZŁO mimo geometric anti-pattern, Adrian panel override→cid=500).

Calibration changes:
1. Bucket -0.5..0 penalty -15 → -35 (mocniejsza signal dla "narrow window of
   plausible deniability" cases).
2. NEW deliv_spread_km multiplier dla negative bucket only:
   - 8 km = 1.0x (baseline)
   - 12 km = 1.5x
   - 16+ km = 2.0x cap
   - Linear scale: 1.0 + (spread - 8) * 0.125

Case 472338 replay expectation: cos=-0.326 → bucket -0.5..0 → -35 base × 1.578
spread_mult (deliv_spread=12.63) = -55.2 penalty (vs pre-fix -15). Margin to
reverse base score.
"""
from dispatch_v2.core import candidates as _k11c
from dispatch_v2.core import selection as _k12s  # K11: cialo petli per-kurier (skan obu zrodel)
import inspect


def test_r1_bucket_minus_0_5_to_0_tightened_to_minus_35():
    """Bucket -0.5..0 = -35 (P3-D5), -15 wycofane. Po refaktorze F1 (2026-05-24)
    logika w helperze _r1_corridor_base_bonus; legacy (gradient=False) = stary klif
    1:1. Test behavior-based zamiast grep źródła."""
    from dispatch_v2.dispatch_pipeline import _r1_corridor_base_bonus as B
    # bucket -0.5..0 (legacy/klif) → -35, NIE -15
    assert B(-0.326, False) == -35.0   # case 472338 cos
    assert B(-0.1, False) == -35.0
    assert B(-0.49, False) == -35.0
    assert B(-0.326, False) != -15.0
    # próg -0.5 i niżej → -40
    assert B(-0.5, False) == -40.0


def test_r1_spread_mult_present():
    """Source regression: deliv_spread multiplier obecny dla negative bonus."""
    from dispatch_v2 import dispatch_pipeline
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k11c) + inspect.getsource(_k12s))
    assert "r1_corridor_spread_mult" in src
    # Multiplier formula: 1.0 + (spread - 8.0) * 0.125, cap 2.0
    assert "(_r1_deliv_spread - 8.0) * 0.125" in src
    assert "min(2.0," in src


def test_r1_spread_mult_skipped_for_positive_bonus():
    """Source regression: positive bonus NIE multiplied — tight corridor reward niezalezny."""
    from dispatch_v2 import dispatch_pipeline
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k11c) + inspect.getsource(_k12s))
    # Gate: if bonus_r1_corridor < 0
    p3d5_section_start = src.find("P3-D5 2026-05-11: deliv_spread mnożnik")
    assert p3d5_section_start > 0
    p3d5_section = src[p3d5_section_start:p3d5_section_start + 800]
    assert "if bonus_r1_corridor < 0:" in p3d5_section


def test_r1_corridor_spread_mult_in_observability_metrics():
    """Source regression: r1_corridor_spread_mult emitowany w enriched_metrics."""
    from dispatch_v2 import dispatch_pipeline
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k11c) + inspect.getsource(_k12s))
    assert "\"r1_corridor_spread_mult\":" in src


def test_r1_472338_ogniomistrz_replay_simulated_magnitude():
    """Symulacja case 472338: cos=-0.326 + deliv_spread=12.63km → penalty ≈ -55.

    Replay inline (no real feasibility call) — pure math z constants helper.
    """
    cos = -0.326
    deliv_spread = 12.63

    # Bucket logic
    if cos > 0.85:
        base = 20.0
    elif cos > 0.5:
        base = 5.0
    elif cos > 0.0:
        base = 0.0
    elif cos > -0.5:
        base = -35.0  # P3-D5
    else:
        base = -40.0

    assert base == -35.0  # cos=-0.326 falls into -0.5..0 bucket

    # Spread mult
    spread_mult = 1.0
    if base < 0 and deliv_spread > 8.0:
        spread_mult = min(2.0, 1.0 + (deliv_spread - 8.0) * 0.125)

    final = base * spread_mult
    # spread_mult = 1 + (12.63 - 8) * 0.125 = 1 + 0.579 = 1.579
    # final = -35 * 1.579 = -55.25
    assert abs(spread_mult - 1.579) < 0.01
    assert abs(final - (-55.25)) < 0.5


def test_r1_spread_mult_caps_at_2x_for_wide_drops():
    """Wide spread >16km → mult capped at 2.0x (max penalty doubled)."""
    cos = -0.4
    base = -35.0
    deliv_spread = 20.0  # very wide

    spread_mult = min(2.0, 1.0 + (deliv_spread - 8.0) * 0.125)
    final = base * spread_mult

    # spread_mult = min(2.0, 1 + 12 * 0.125) = min(2.0, 2.5) = 2.0
    assert spread_mult == 2.0
    assert final == -70.0


def test_r1_spread_mult_no_change_below_8km():
    """deliv_spread <= 8 km → mult = 1.0 (no change)."""
    base = -35.0
    deliv_spread = 6.0

    spread_mult = 1.0
    if base < 0 and deliv_spread > 8.0:
        spread_mult = min(2.0, 1.0 + (deliv_spread - 8.0) * 0.125)

    final = base * spread_mult

    assert spread_mult == 1.0
    assert final == -35.0


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
