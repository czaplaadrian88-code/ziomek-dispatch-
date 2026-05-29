"""B3 (audyt 2026-05-28) — ciągły gradient zamiast sentinela -1000 dla wait>60min.

Standalone executable. Weryfikuje:
1. b3_flag_off_preserves_sentinel — flag OFF → legacy -1000 flat (regression guard)
2. b3_continuity_at_60 — flag ON: brak klifu -700→-1000 (gradient @60.001 ≈ -700)
3. b3_monotone_nonincreasing — flag ON: penalty maleje monotonicznie >60
4. b3_floor_cap — flag ON: nigdy poniżej floor (-2000)
5. b3_in_table_unchanged — flag ON nie zmienia wartości ≤60 (tabela + interpolacja)
6. b3_slope_math — flag ON: dokładne wartości gradientu (slope -40, floor -2000)
"""
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common, scoring


def test_b3_flag_off_preserves_sentinel():
    """B3 OFF (default) → legacy hard fallback -1000 dla wait>60 (zero zmiany prod)."""
    common.ENABLE_V327_WAIT_PENALTY = True
    common.ENABLE_B3_WAIT_GRADIENT = False
    for wait in [60.001, 70, 80, 100, 1000]:
        got = scoring.compute_wait_penalty(wait)
        assert got == -1000.0, f"B3 OFF wait={wait}: expected -1000.0, got {got}"


def test_b3_continuity_at_60():
    """B3 ON → brak nieciągłości na granicy 60min (tabela -700, gradient ≈ -700)."""
    common.ENABLE_V327_WAIT_PENALTY = True
    common.ENABLE_B3_WAIT_GRADIENT = True
    at_60 = scoring.compute_wait_penalty(60.0)        # interpolation loop → -700
    just_over = scoring.compute_wait_penalty(60.001)  # gradient branch
    assert at_60 == -700.0, f"@60 expected -700.0, got {at_60}"
    assert abs(just_over - (-700.0)) < 0.5, \
        f"@60.001 must be continuous (~-700), got {just_over} (cliff!)"
    # KLUCZOWE: gradient @60.001 NIE jest starym sentinelem -1000
    assert just_over > -701.0, f"@60.001 cliff to sentinel not removed: {just_over}"


def test_b3_monotone_nonincreasing():
    """B3 ON → penalty monotonicznie nierosnąca dla rosnącego wait>60."""
    common.ENABLE_V327_WAIT_PENALTY = True
    common.ENABLE_B3_WAIT_GRADIENT = True
    prev = None
    for wait in [60.5, 62, 65, 70, 80, 90, 100, 150, 500]:
        got = scoring.compute_wait_penalty(wait)
        if prev is not None:
            assert got <= prev + 1e-9, \
                f"non-monotone at wait={wait}: {got} > prev {prev}"
        prev = got


def test_b3_floor_cap():
    """B3 ON → penalty nigdy poniżej floor B3_WAIT_GRADIENT_FLOOR."""
    common.ENABLE_V327_WAIT_PENALTY = True
    common.ENABLE_B3_WAIT_GRADIENT = True
    floor = common.B3_WAIT_GRADIENT_FLOOR
    for wait in [95, 100, 200, 1000, 100000]:
        got = scoring.compute_wait_penalty(wait)
        assert got >= floor - 1e-9, f"wait={wait}: {got} below floor {floor}"
    assert scoring.compute_wait_penalty(100000) == floor, \
        "extreme wait must saturate at floor"


def test_b3_in_table_unchanged():
    """B3 ON nie rusza wartości ≤60min (tabela + interpolacja niezmienione)."""
    common.ENABLE_V327_WAIT_PENALTY = True
    expected = [(20.0, 0.0), (25.0, -10.0), (30.0, -30.0), (35.0, -90.0),
                (40.0, -150.0), (50.0, -400.0), (60.0, -700.0),
                (22.5, -5.0), (45.0, -275.0)]
    for flag in (False, True):
        common.ENABLE_B3_WAIT_GRADIENT = flag
        for wait, exp in expected:
            got = scoring.compute_wait_penalty(wait)
            assert abs(got - exp) < 0.001, \
                f"B3={flag} wait={wait}: expected {exp}, got {got}"


def test_b3_slope_math():
    """B3 ON → dokładne wartości gradientu: val = -700 + slope*(wait-60), cap floor."""
    common.ENABLE_V327_WAIT_PENALTY = True
    common.ENABLE_B3_WAIT_GRADIENT = True
    slope = common.B3_WAIT_GRADIENT_SLOPE_PER_MIN
    floor = common.B3_WAIT_GRADIENT_FLOOR
    for wait in [61, 65, 67.5, 70, 80, 90]:
        exp = max(-700.0 + slope * (wait - 60.0), floor)
        got = scoring.compute_wait_penalty(wait)
        assert abs(got - exp) < 0.001, f"wait={wait}: expected {exp}, got {got}"


def main():
    _orig_v327 = common.ENABLE_V327_WAIT_PENALTY
    _orig_b3 = common.ENABLE_B3_WAIT_GRADIENT
    tests = [
        ('b3_flag_off_preserves_sentinel', test_b3_flag_off_preserves_sentinel),
        ('b3_continuity_at_60', test_b3_continuity_at_60),
        ('b3_monotone_nonincreasing', test_b3_monotone_nonincreasing),
        ('b3_floor_cap', test_b3_floor_cap),
        ('b3_in_table_unchanged', test_b3_in_table_unchanged),
        ('b3_slope_math', test_b3_slope_math),
    ]
    print('=' * 60)
    print('B3 wait-penalty gradient (audyt 2026-05-28) tests')
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
        common.ENABLE_V327_WAIT_PENALTY = _orig_v327
        common.ENABLE_B3_WAIT_GRADIENT = _orig_b3
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
