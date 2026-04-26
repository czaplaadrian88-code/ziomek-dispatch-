"""V3.27.1 Wait penalty tests — Adrian's quadratic table (sprint sesja 1, 2026-04-26).

Standalone executable. Tests:
1. wait_penalty_disabled_baseline (flag=False, helper zwraca 0)
2. wait_penalty_table_lookups (7 cases dokładnie z tabeli)
3. wait_penalty_interpolation (3 cases between points, math verified)
4. wait_penalty_hard_fallback (>60 → -1000)
5. wait_penalty_summed_across_bag (bonus: 3 pickups suma = -130)
6. wait_penalty_below_sweet_spot (≤20 → 0)
"""
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common, scoring


def _set_flag(value: bool):
    common.ENABLE_V327_WAIT_PENALTY = value


def test_v327_wait_penalty_disabled_baseline():
    _set_flag(False)
    # Wartości które normalnie dałyby penalty — flag=False zeruje
    for wait in [25, 30, 35, 40, 50, 60, 80]:
        got = scoring.compute_wait_penalty(wait)
        assert got == 0.0, f"flag=False MUST return 0 for wait={wait}, got {got}"


def test_v327_wait_penalty_table_lookups():
    """7 cases dokładnie z Adrian's table — assert exact values."""
    _set_flag(True)
    expected = [
        (20.0, 0.0),
        (25.0, -10.0),
        (30.0, -30.0),
        (35.0, -90.0),
        (40.0, -150.0),
        (50.0, -400.0),
        (60.0, -700.0),
    ]
    for wait, expected_pen in expected:
        got = scoring.compute_wait_penalty(wait)
        assert abs(got - expected_pen) < 0.001, \
            f"wait={wait}: expected {expected_pen}, got {got}"


def test_v327_wait_penalty_interpolation():
    """Linear interpolacja between table points — math verified."""
    _set_flag(True)
    # 22.5 → 0 + (-10 - 0) × (22.5-20)/(25-20) = -10 × 0.5 = -5.0
    got = scoring.compute_wait_penalty(22.5)
    assert abs(got - (-5.0)) < 0.001, f"wait=22.5: expected -5.0, got {got}"
    # 27 → -10 + (-30 - -10) × (27-25)/(30-25) = -10 + (-20)*0.4 = -18.0
    got = scoring.compute_wait_penalty(27.0)
    assert abs(got - (-18.0)) < 0.001, f"wait=27: expected -18.0, got {got}"
    # 45 → -150 + (-400 - -150) × (45-40)/(50-40) = -150 + (-250)*0.5 = -275.0
    got = scoring.compute_wait_penalty(45.0)
    assert abs(got - (-275.0)) < 0.001, f"wait=45: expected -275.0, got {got}"


def test_v327_wait_penalty_hard_fallback():
    """wait > 60 (poza tabelą) → V327_WAIT_PENALTY_HARD_FALLBACK = -1000."""
    _set_flag(True)
    for wait in [60.001, 70, 80, 100, 1000]:
        got = scoring.compute_wait_penalty(wait)
        assert got == -1000.0, f"wait={wait}: expected -1000.0, got {got}"


def test_v327_wait_penalty_below_sweet_spot():
    """wait ≤ 20 → 0 (sweet spot, no penalty). Negative wait → 0 (defensive)."""
    _set_flag(True)
    for wait in [-5, 0, 0.5, 5, 10, 15, 19.99, 20]:
        got = scoring.compute_wait_penalty(wait)
        assert got == 0.0, f"wait={wait}: expected 0.0, got {got}"


def test_v327_wait_penalty_summed_across_bag():
    """Bonus test: 3 pickups z wait 25/30/35 → suma penalty = -10 + -30 + -90 = -130."""
    _set_flag(True)
    waits = [25.0, 30.0, 35.0]
    total = sum(scoring.compute_wait_penalty(w) for w in waits)
    expected = -10.0 + -30.0 + -90.0
    assert abs(total - expected) < 0.001, \
        f"summed waits {waits}: expected {expected}, got {total}"


def test_v327_wait_penalty_none_input():
    """Defensive: None input → 0 (treat as no wait, edge case for missing data)."""
    _set_flag(True)
    got = scoring.compute_wait_penalty(None)
    assert got == 0.0, f"wait=None: expected 0.0, got {got}"


def test_v327_wait_penalty_additive_serialization_disabled():
    """V3.27.1 ADDITIVE A/B serialization: gdy flag=False, score używa legacy
    ALE oba pola (legacy + v327) muszą być w shadow_dispatcher LOCATION A i B.
    Verifies _serialize_candidate output schema."""
    from dispatch_v2 import shadow_dispatcher
    _set_flag(False)
    # Mock candidate object z metrics field zawierającym oba bonus_r9_*
    class _MockPlan:
        sequence = []
        total_duration_min = 0.0
        strategy = "test"
        sla_violations = 0
        osrm_fallback_used = False
        per_order_delivery_times = None
        predicted_delivered_at = {}
        pickup_at = {}
    class _MockCand:
        courier_id = "999"
        name = "Test"
        score = 100.0
        feasibility_verdict = "MAYBE"
        feasibility_reason = "test"
        best_effort = False
        plan = _MockPlan()
        bag_context = []
        metrics = {
            "bonus_r9_wait_pen": -50.0,         # used (legacy gdy flag=False)
            "bonus_r9_wait_pen_legacy": -50.0,  # ZAWSZE serializowane
            "bonus_r9_wait_pen_v327": 0.0,       # v327 = 0 gdy flag=False
        }
    # Faktycznie wywołać _serialize_candidate
    out = shadow_dispatcher._serialize_candidate(_MockCand())
    assert "bonus_r9_wait_pen" in out, "missing bonus_r9_wait_pen"
    assert "bonus_r9_wait_pen_legacy" in out, "LOCATION A missing bonus_r9_wait_pen_legacy"
    assert "bonus_r9_wait_pen_v327" in out, "LOCATION A missing bonus_r9_wait_pen_v327"
    assert out["bonus_r9_wait_pen"] == -50.0
    assert out["bonus_r9_wait_pen_legacy"] == -50.0
    assert out["bonus_r9_wait_pen_v327"] == 0.0


def test_v327_wait_penalty_additive_serialization_enabled():
    """V3.27.1 ADDITIVE A/B serialization gdy flag=True: score używa v327,
    legacy nadal computed/serializowane dla A/B comparison."""
    from dispatch_v2 import shadow_dispatcher
    _set_flag(True)
    class _MockPlan:
        sequence = []
        total_duration_min = 0.0
        strategy = "test"
        sla_violations = 0
        osrm_fallback_used = False
        per_order_delivery_times = None
        predicted_delivered_at = {}
        pickup_at = {}
    class _MockCand:
        courier_id = "999"
        name = "Test"
        score = 100.0
        feasibility_verdict = "MAYBE"
        feasibility_reason = "test"
        best_effort = False
        plan = _MockPlan()
        bag_context = []
        # Scenariusz: legacy = 0 (wait_pred_min < SOFT_MIN), v327 = -130 (3 pickups 25/30/35)
        metrics = {
            "bonus_r9_wait_pen": -130.0,         # used = v327 gdy flag=True
            "bonus_r9_wait_pen_legacy": 0.0,     # legacy nadal 0 (linear nie fired)
            "bonus_r9_wait_pen_v327": -130.0,    # v327 quadratic suma
        }
    out = shadow_dispatcher._serialize_candidate(_MockCand())
    assert out["bonus_r9_wait_pen"] == -130.0, f"used={out['bonus_r9_wait_pen']}"
    assert out["bonus_r9_wait_pen_legacy"] == 0.0
    assert out["bonus_r9_wait_pen_v327"] == -130.0


def main():
    # Reset flag po testach żeby nie zmieniać global state w innych testach
    _orig_flag = common.ENABLE_V327_WAIT_PENALTY
    tests = [
        ('v327_wait_penalty_disabled_baseline', test_v327_wait_penalty_disabled_baseline),
        ('v327_wait_penalty_table_lookups', test_v327_wait_penalty_table_lookups),
        ('v327_wait_penalty_interpolation', test_v327_wait_penalty_interpolation),
        ('v327_wait_penalty_hard_fallback', test_v327_wait_penalty_hard_fallback),
        ('v327_wait_penalty_below_sweet_spot', test_v327_wait_penalty_below_sweet_spot),
        ('v327_wait_penalty_summed_across_bag', test_v327_wait_penalty_summed_across_bag),
        ('v327_wait_penalty_none_input', test_v327_wait_penalty_none_input),
        ('v327_wait_penalty_additive_serialization_disabled', test_v327_wait_penalty_additive_serialization_disabled),
        ('v327_wait_penalty_additive_serialization_enabled', test_v327_wait_penalty_additive_serialization_enabled),
    ]
    print('=' * 60)
    print('V3.27.1 Wait penalty (Adrian quadratic table) tests')
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
        common.ENABLE_V327_WAIT_PENALTY = _orig_flag
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
