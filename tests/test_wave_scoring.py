"""F2.2 C5 tests — wave_scoring.compute_same_restaurant_boost + compose behaviour.

Standalone executable.
"""
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.wave_scoring import (
    compute_same_restaurant_boost,
    compute_wave_adjustment,
    SAME_RESTAURANT_BOOST,
    SAME_RESTAURANT_BAG_SIZE_MAX,
    SAME_RESTAURANT_BAG_TIME_MAX,
)


def test_same_restaurant_boost_match():
    """Candidate restaurant matches bag item + safe bag state → boost."""
    result = compute_same_restaurant_boost(
        candidate_restaurant="Grill Kebab",
        bag_restaurants=["Grill Kebab"],
        courier_bag_size=1,
        courier_bag_time_min=3.0,
    )
    assert result == SAME_RESTAURANT_BOOST, f"expected {SAME_RESTAURANT_BOOST}, got {result}"
    return True


def test_same_restaurant_boost_no_match():
    """Different restaurant → 0."""
    result = compute_same_restaurant_boost(
        candidate_restaurant="Farina",
        bag_restaurants=["Grill Kebab"],
        courier_bag_size=1,
        courier_bag_time_min=3.0,
    )
    assert result == 0.0, f"expected 0.0, got {result}"
    return True


def test_same_restaurant_boost_empty_bag():
    """Empty bag → no boost (no resto to match). Solo dispatch not scope of this bonus."""
    result = compute_same_restaurant_boost(
        candidate_restaurant="Grill Kebab",
        bag_restaurants=[],
        courier_bag_size=0,
        courier_bag_time_min=0.0,
    )
    assert result == 0.0, f"expected 0.0 (empty bag), got {result}"
    return True


def test_same_restaurant_boost_bag_full():
    """bag_size >= threshold → 0 even if match."""
    result = compute_same_restaurant_boost(
        candidate_restaurant="Grill Kebab",
        bag_restaurants=["Grill Kebab", "Grill Kebab", "Grill Kebab"],
        courier_bag_size=SAME_RESTAURANT_BAG_SIZE_MAX,
        courier_bag_time_min=5.0,
    )
    assert result == 0.0, f"bag full ({SAME_RESTAURANT_BAG_SIZE_MAX}) → no boost, got {result}"
    return True


def test_same_restaurant_boost_bag_time_exceeded():
    """bag_time >= limit → 0 (food going cold, no more attachments)."""
    result = compute_same_restaurant_boost(
        candidate_restaurant="Grill Kebab",
        bag_restaurants=["Grill Kebab"],
        courier_bag_size=1,
        courier_bag_time_min=SAME_RESTAURANT_BAG_TIME_MAX,
    )
    assert result == 0.0, f"bag_time={SAME_RESTAURANT_BAG_TIME_MAX} → no boost, got {result}"
    return True


def test_same_restaurant_boost_none_candidate():
    """None candidate restaurant → 0 (guard)."""
    result = compute_same_restaurant_boost(
        candidate_restaurant=None,
        bag_restaurants=["Grill Kebab"],
        courier_bag_size=1,
        courier_bag_time_min=2.0,
    )
    assert result == 0.0, f"None candidate → 0.0, got {result}"
    return True


def test_same_restaurant_boost_multi_bag_one_match():
    """Multi-resto bag, ONE match → boost."""
    result = compute_same_restaurant_boost(
        candidate_restaurant="Farina",
        bag_restaurants=["Grill Kebab", "Farina"],
        courier_bag_size=2,
        courier_bag_time_min=10.0,
    )
    assert result == SAME_RESTAURANT_BOOST, f"one match in multi-bag → boost, got {result}"
    return True


def test_compute_wave_adjustment_flag_off_returns_zero():
    """ENABLE_WAVE_SCORING=False → composition returns 0 regardless of inputs."""
    # Flag default False (confirmed w common.py). Calling compute_wave_adjustment
    # z matching args should still return 0.0 because flag gates everything.
    result = compute_wave_adjustment(
        candidate_restaurant="Grill Kebab",
        bag_restaurants=["Grill Kebab"],
        courier_bag_size=1,
        courier_bag_time_min=3.0,
    )
    assert result == 0.0, f"flag off → 0.0 regardless of inputs, got {result}"
    return True


def test_compute_wave_adjustment_flag_on_sums_features():
    """Simulate flag=True by monkey-patching; verify sum of active + stubs."""
    import dispatch_v2.wave_scoring as ws
    original_flag = ws.ENABLE_WAVE_SCORING
    try:
        ws.ENABLE_WAVE_SCORING = True
        result = ws.compute_wave_adjustment(
            candidate_restaurant="Grill Kebab",
            bag_restaurants=["Grill Kebab"],
            courier_bag_size=1,
            courier_bag_time_min=3.0,
        )
        # Only same_restaurant_boost is active; stubs return 0
        assert result == SAME_RESTAURANT_BOOST, f"flag on: expected {SAME_RESTAURANT_BOOST}, got {result}"
    finally:
        ws.ENABLE_WAVE_SCORING = original_flag
    return True


def main():
    tests = [
        ("same_restaurant_boost_match", test_same_restaurant_boost_match),
        ("same_restaurant_boost_no_match", test_same_restaurant_boost_no_match),
        ("same_restaurant_boost_empty_bag", test_same_restaurant_boost_empty_bag),
        ("same_restaurant_boost_bag_full", test_same_restaurant_boost_bag_full),
        ("same_restaurant_boost_bag_time_exceeded", test_same_restaurant_boost_bag_time_exceeded),
        ("same_restaurant_boost_none_candidate", test_same_restaurant_boost_none_candidate),
        ("same_restaurant_boost_multi_bag_one_match", test_same_restaurant_boost_multi_bag_one_match),
        ("compute_wave_adjustment_flag_off_returns_zero", test_compute_wave_adjustment_flag_off_returns_zero),
        ("compute_wave_adjustment_flag_on_sums_features", test_compute_wave_adjustment_flag_on_sums_features),
    ]
    print("=" * 60)
    print("F2.2 C5 skeleton: wave_scoring tests")
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
