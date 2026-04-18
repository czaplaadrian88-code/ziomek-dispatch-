"""F2.2 C3 narrow tests — scoring.py r6_soft_penalty integration.

Flag-gated: DEPRECATE_LEGACY_HARD_GATES False (default) → ignored.
Flag True → penalty (negative) subtracts from total.

Standalone executable.
"""
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import scoring
from dispatch_v2 import common


def test_scoring_ignores_r6_penalty_when_flag_false():
    """Default flag False → r6_soft_penalty kwarg ignored."""
    # Baseline without penalty
    r_baseline = scoring.score_candidate(
        courier_pos=(53.0, 23.0), restaurant_pos=(53.1, 23.1),
        bag_size=1, oldest_in_bag_min=20.0, road_km=2.0,
    )
    # Same inputs + penalty kwarg (flag still False by default)
    r_with_penalty = scoring.score_candidate(
        courier_pos=(53.0, 23.0), restaurant_pos=(53.1, 23.1),
        bag_size=1, oldest_in_bag_min=20.0, road_km=2.0,
        r6_soft_penalty=-9.0,
    )
    assert common.DEPRECATE_LEGACY_HARD_GATES is False, "test assumes default flag state"
    assert r_baseline["total"] == r_with_penalty["total"], \
        f"flag off → identical totals; got {r_baseline['total']} vs {r_with_penalty['total']}"
    assert r_with_penalty["metrics"]["r6_soft_penalty_applied"] == 0.0
    return True


def test_scoring_includes_r6_penalty_when_flag_true():
    """Flag True → r6_soft_penalty subtracts from total."""
    original = common.DEPRECATE_LEGACY_HARD_GATES
    try:
        common.DEPRECATE_LEGACY_HARD_GATES = True
        # Re-import scoring? No — scoring reads flag at call site if we re-import
        import importlib
        importlib.reload(scoring)  # pick up new flag state
        r_no_penalty = scoring.score_candidate(
            courier_pos=(53.0, 23.0), restaurant_pos=(53.1, 23.1),
            bag_size=1, oldest_in_bag_min=20.0, road_km=2.0,
            r6_soft_penalty=0.0,
        )
        r_with_penalty = scoring.score_candidate(
            courier_pos=(53.0, 23.0), restaurant_pos=(53.1, 23.1),
            bag_size=1, oldest_in_bag_min=20.0, road_km=2.0,
            r6_soft_penalty=-9.0,
        )
        assert r_with_penalty["total"] < r_no_penalty["total"], \
            f"flag on + negative penalty → lower total; {r_no_penalty['total']} vs {r_with_penalty['total']}"
        diff = r_no_penalty["total"] - r_with_penalty["total"]
        assert abs(diff - 9.0) < 0.01, f"diff should = 9.0 (abs of penalty); got {diff}"
        assert r_with_penalty["metrics"]["r6_soft_penalty_applied"] == -9.0
    finally:
        common.DEPRECATE_LEGACY_HARD_GATES = original
        importlib.reload(scoring)
    return True


def test_scoring_handles_missing_r6_metric_gracefully():
    """No r6_soft_penalty kwarg → default 0.0, zero impact."""
    r = scoring.score_candidate(
        courier_pos=(53.0, 23.0), restaurant_pos=(53.1, 23.1),
        bag_size=0, road_km=1.5,
    )
    # No exception, clean result
    assert "total" in r
    assert r["metrics"]["r6_soft_penalty_applied"] == 0.0
    return True


def main():
    tests = [
        ("scoring_ignores_r6_penalty_when_flag_false", test_scoring_ignores_r6_penalty_when_flag_false),
        ("scoring_includes_r6_penalty_when_flag_true", test_scoring_includes_r6_penalty_when_flag_true),
        ("scoring_handles_missing_r6_metric_gracefully", test_scoring_handles_missing_r6_metric_gracefully),
    ]
    print("=" * 60)
    print("F2.2 C3 narrow: scoring.py r6_soft_penalty integration tests")
    print("=" * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}")
            failed.append(name)
    print("=" * 60)
    print(f"{passed}/{len(tests)} PASS")
    if failed:
        print(f"FAILED: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
