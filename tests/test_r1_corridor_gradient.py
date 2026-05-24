"""F1 R1-CORRIDOR-GRADIENT (2026-05-24).

Testuje helper _r1_corridor_base_bonus: gradient liniowy na ujemnej stronie
zamiast klifu -35. Strona dodatnia (reward) bez zmian. Legacy zachowany gdy
gradient=False.
"""
import pytest

from dispatch_v2.dispatch_pipeline import _r1_corridor_base_bonus as B


def test_none_zero():
    assert B(None, True) == 0.0
    assert B(None, False) == 0.0


@pytest.mark.parametrize("cos,expected", [
    (0.9, 20.0), (0.86, 20.0),
    (0.6, 5.0),
    (0.3, 0.0), (0.01, 0.0),
])
def test_positive_side_unchanged(cos, expected):
    # reward identyczny w obu trybach (strona dodatnia, cos > 0)
    assert B(cos, True) == expected
    assert B(cos, False) == expected


def test_exact_zero_boundary():
    # cos==0.0: legacy kwirk (`> 0.0` False → wpada w -35); gradient naprawia → 0.0
    assert B(0.0, False) == -35.0
    assert B(0.0, True) == 0.0


def test_gradient_negative_linear():
    # liniowo 0@0 → -40@-1
    assert B(-0.05, True) == pytest.approx(-2.0)
    assert B(-0.2, True) == pytest.approx(-8.0)
    assert B(-0.5, True) == pytest.approx(-20.0)
    assert B(-0.9, True) == pytest.approx(-36.0)
    assert B(-1.0, True) == pytest.approx(-40.0)


def test_legacy_cliff_when_off():
    # klif: (-0.5,0] → -35, ≤-0.5 → -40
    assert B(-0.05, False) == -35.0
    assert B(-0.2, False) == -35.0
    assert B(-0.49, False) == -35.0
    assert B(-0.5, False) == -40.0
    assert B(-0.9, False) == -40.0


def test_gradient_kills_near_zero_false_positive():
    # kluczowa różnica: near-neutral nie dostaje już -35
    assert B(-0.054, True) > -5.0       # gradient: ~-2.2
    assert B(-0.054, False) == -35.0    # klif: pełne -35


def test_gradient_monotonic_decreasing():
    vals = [B(c / 10.0, True) for c in range(0, -11, -1)]  # 0.0 → -1.0
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1))
