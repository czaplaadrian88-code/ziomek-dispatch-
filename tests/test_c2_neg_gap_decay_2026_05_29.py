"""C2 (audyt 2026-05-28) — decay/cap dla silnie ujemnego gap (stara fala).

Standalone executable. Weryfikuje:
1. c2_flag_off_preserves_flat — flag OFF → gap<0 zawsze +30 (regression, zgodne z V3.19h test#5)
2. c2_plateau — flag ON: |gap| ≤ FULL_BONUS_MIN → pełny bonus (mild anticipation)
3. c2_decay — flag ON: |gap| w oknie decay → liniowy spadek do FLOOR_FRAC*BONUS
4. c2_floor — flag ON: |gap| ≥ FULL+SPAN → FLOOR_FRAC*BONUS (default 0)
5. c2_positive_side_unchanged — gap≥0 identyczny niezależnie od C2 flag
6. c2_none_edge — gap=None → 0
7. c2_slope_math — dokładne wartości decay
"""
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common as C


def test_c2_flag_off_preserves_flat():
    """C2 OFF (default) → każdy gap<0 daje FLAT +30 (legacy, zero zmiany prod)."""
    C.ENABLE_C2_NEG_GAP_DECAY = False
    for gap in [-0.1, -5, -10, -20, -50, -200]:
        got = C.bug2_wave_continuation_bonus(gap)
        assert got == C.BUG2_WAVE_CONTINUATION_BONUS, \
            f"C2 OFF gap={gap}: expected {C.BUG2_WAVE_CONTINUATION_BONUS}, got {got}"


def test_c2_plateau():
    """C2 ON → |gap| ≤ FULL_BONUS_MIN dostaje pełny bonus (realna kontynuacja fali)."""
    C.ENABLE_C2_NEG_GAP_DECAY = True
    full = C.C2_NEG_GAP_FULL_BONUS_MIN
    for over in [0.1, full / 2, full]:
        got = C.bug2_wave_continuation_bonus(-over)
        assert got == C.BUG2_WAVE_CONTINUATION_BONUS, \
            f"C2 ON |gap|={over} (≤plateau): expected full bonus, got {got}"


def test_c2_decay():
    """C2 ON → poza plateau liniowy decay (monotonicznie malejący w |gap|)."""
    C.ENABLE_C2_NEG_GAP_DECAY = True
    full = C.C2_NEG_GAP_FULL_BONUS_MIN
    span = C.C2_NEG_GAP_DECAY_SPAN_MIN
    prev = C.BUG2_WAVE_CONTINUATION_BONUS
    for frac_pt in [0.0, 0.25, 0.5, 0.75, 1.0]:
        over = full + frac_pt * span
        got = C.bug2_wave_continuation_bonus(-over)
        assert got <= prev + 1e-9, f"non-monotone at |gap|={over}: {got} > {prev}"
        prev = got


def test_c2_floor():
    """C2 ON → |gap| ≥ FULL+SPAN saturuje na FLOOR_FRAC*BONUS (default 0)."""
    C.ENABLE_C2_NEG_GAP_DECAY = True
    full = C.C2_NEG_GAP_FULL_BONUS_MIN
    span = C.C2_NEG_GAP_DECAY_SPAN_MIN
    floor_val = C.BUG2_WAVE_CONTINUATION_BONUS * C.C2_NEG_GAP_FLOOR_FRAC
    for over in [full + span, full + span + 10, full + span + 1000]:
        got = C.bug2_wave_continuation_bonus(-over)
        assert abs(got - floor_val) < 0.001, \
            f"|gap|={over}: expected floor {floor_val}, got {got}"


def test_c2_positive_side_unchanged():
    """gap≥0 (kurier czeka) identyczny niezależnie od C2 flag (C2 rusza tylko gap<0)."""
    expected = [(0.0, 30.0), (2.5, 22.5), (5.0, 15.0), (7.5, 7.5),
                (10.0, 0.0), (25.0, 0.0)]
    for flag in (False, True):
        C.ENABLE_C2_NEG_GAP_DECAY = flag
        for gap, exp in expected:
            got = C.bug2_wave_continuation_bonus(gap)
            assert abs(got - exp) < 0.001, \
                f"C2={flag} gap={gap}: expected {exp}, got {got}"


def test_c2_none_edge():
    """gap=None → 0 niezależnie od flag."""
    for flag in (False, True):
        C.ENABLE_C2_NEG_GAP_DECAY = flag
        assert C.bug2_wave_continuation_bonus(None) == 0.0


def test_c2_slope_math():
    """C2 ON → dokładny decay: bonus = BONUS*(1 - frac*(1-FLOOR_FRAC)),
    frac = min((|gap|-FULL)/SPAN, 1)."""
    C.ENABLE_C2_NEG_GAP_DECAY = True
    bonus = C.BUG2_WAVE_CONTINUATION_BONUS
    full = C.C2_NEG_GAP_FULL_BONUS_MIN
    span = C.C2_NEG_GAP_DECAY_SPAN_MIN
    ffrac = C.C2_NEG_GAP_FLOOR_FRAC
    for over in [full + 1, full + span / 2, full + span, full + span + 5]:
        frac = min((over - full) / span, 1.0)
        exp = bonus * (1.0 - frac * (1.0 - ffrac))
        got = C.bug2_wave_continuation_bonus(-over)
        assert abs(got - exp) < 0.001, f"|gap|={over}: expected {exp}, got {got}"


def main():
    _orig = C.ENABLE_C2_NEG_GAP_DECAY
    tests = [
        ('c2_flag_off_preserves_flat', test_c2_flag_off_preserves_flat),
        ('c2_plateau', test_c2_plateau),
        ('c2_decay', test_c2_decay),
        ('c2_floor', test_c2_floor),
        ('c2_positive_side_unchanged', test_c2_positive_side_unchanged),
        ('c2_none_edge', test_c2_none_edge),
        ('c2_slope_math', test_c2_slope_math),
    ]
    print('=' * 60)
    print('C2 neg-gap decay (audyt 2026-05-28) tests')
    print('=' * 60)
    passed = 0
    failed = []
    try:
        for name, fn in tests:
            try:
                fn()
                print(f'  ✅ {name}')
                passed += 1
            except AssertionError as e:
                print(f'  ❌ {name}: {e}')
                failed.append(name)
            except Exception as e:
                print(f'  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}')
                failed.append(name)
    finally:
        C.ENABLE_C2_NEG_GAP_DECAY = _orig
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
