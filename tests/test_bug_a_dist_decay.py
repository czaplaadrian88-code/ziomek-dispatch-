"""Bug A regression guard (sprint 2026-04-25):
DIST_DECAY_KM = 5.0 (was 3.0). Adrian's decision: exp(-d/3) saturated zbyt
wcześnie dla dystansów Białystoku >>3 km — algorytm efektywnie traktował
10km i 15km jako same penalty (~0). Decay 5 utrzymuje gradient.

Verify:
- DIST_DECAY_KM == 5.0
- s_dystans(0) == 100, s_dystans(very_large) → 0
- s_dystans(15.91) > 0.15 pts (old decay=3 returned 0.50; new decay=5 returns 4.15)
- Monotonic decrease
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import scoring  # noqa: E402


def test_decay_value():
    assert scoring.DIST_DECAY_KM == 5.0, f"DIST_DECAY_KM should be 5.0, got {scoring.DIST_DECAY_KM}"


def test_dystans_at_zero_km():
    assert scoring.s_dystans(0) == 100.0


def test_dystans_at_decay_km():
    # s_dystans(5) = 100 * exp(-5/5) = 100 * exp(-1) ≈ 36.79
    expected = 100.0 * math.exp(-1.0)
    assert abs(scoring.s_dystans(5.0) - expected) < 0.01


def test_dystans_at_15km_recalibrated():
    # Adrian's reasoning: 15 km should still have meaningful penalty,
    # not effectively zero. Old decay=3 gave 0.67 pts; new decay=5 gives 4.98.
    val = scoring.s_dystans(15.0)
    assert val >= 4.0, f"s_dystans(15km) should be >=4 with new decay, got {val}"
    assert val <= 6.0, f"s_dystans(15km) should be <=6 with new decay, got {val}"


def test_monotonic_decrease():
    prev = scoring.s_dystans(0)
    for km in [1, 3, 5, 10, 15, 20]:
        curr = scoring.s_dystans(km)
        assert curr <= prev, f"non-monotonic at km={km}: prev={prev}, curr={curr}"
        prev = curr


def test_saturates_at_zero():
    # Very large distance → effectively 0
    assert scoring.s_dystans(100) < 1e-3


if __name__ == "__main__":
    test_decay_value()
    print("test_decay_value: PASS")
    test_dystans_at_zero_km()
    print("test_dystans_at_zero_km: PASS")
    test_dystans_at_decay_km()
    print("test_dystans_at_decay_km: PASS")
    test_dystans_at_15km_recalibrated()
    print("test_dystans_at_15km_recalibrated: PASS")
    test_monotonic_decrease()
    print("test_monotonic_decrease: PASS")
    test_saturates_at_zero()
    print("test_saturates_at_zero: PASS")
    print("ALL 6/6 PASS")
