"""F2.2 C4 tests — speed_tier_tracker classify + atomic write.

Standalone executable. Uses pure helper functions (classify_tier, percentile).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.speed_tier_tracker import (
    classify_tier,
    percentile,
    atomic_write_json,
    FAST_P90_MAX,
    NORMAL_P90_MAX,
    MIN_SINGLETONS_FOR_TIER,
)


def test_tier_classification_happy_path():
    """FAST / NORMAL / SAFE boundaries with sufficient n."""
    n = 100
    assert classify_tier(20.0, n) == "FAST", f"p90=20 → FAST"
    assert classify_tier(28.0, n) == "NORMAL", f"p90=28 → NORMAL"
    assert classify_tier(40.0, n) == "SAFE", f"p90=40 → SAFE"
    return True


def test_insufficient_data_default():
    """< 30 singletons → INSUFFICIENT_DATA regardless of p90."""
    assert classify_tier(20.0, 29) == "INSUFFICIENT_DATA"
    assert classify_tier(None, 0) == "INSUFFICIENT_DATA"
    assert classify_tier(15.0, MIN_SINGLETONS_FOR_TIER - 1) == "INSUFFICIENT_DATA"
    return True


def test_boundary_exactly_25_fast():
    """p90 == 25.0 boundary: FAST (inclusive upper bound)."""
    assert classify_tier(25.0, 100) == "FAST"
    return True


def test_boundary_over_25_normal():
    """p90 = 25.01 just over FAST limit → NORMAL."""
    assert classify_tier(25.01, 100) == "NORMAL"
    return True


def test_boundary_exactly_32_normal():
    """p90 == 32.0: NORMAL (inclusive upper bound)."""
    assert classify_tier(32.0, 100) == "NORMAL"
    return True


def test_boundary_over_32_safe():
    """p90 = 32.01 → SAFE."""
    assert classify_tier(32.01, 100) == "SAFE"
    return True


def test_p90_computation_basic():
    """p90 on 100 values 1..100 should be 90.1."""
    xs = list(range(1, 101))
    p = percentile(xs, 0.9)
    # Linear interpolation: k = 99 * 0.9 = 89.1, between xs[89]=90 and xs[90]=91
    assert 90 <= p <= 91, f"p90(1..100) in [90,91], got {p}"
    return True


def test_p90_empty_returns_none():
    assert percentile([], 0.9) is None
    return True


def test_atomic_write_produces_valid_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "test_tiers.json"
        payload = {
            "generated_at": "2026-04-18T17:00:00Z",
            "tiers": {
                "Bartek O.": {"tier": "FAST", "p90_singleton_delivery_min": 23.0, "n_singletons": 100},
            },
            "summary": {"FAST": 1, "NORMAL": 0, "SAFE": 0, "INSUFFICIENT_DATA": 0},
        }
        atomic_write_json(out, payload)
        assert out.exists(), f"output file missing"
        with open(out) as f:
            data = json.load(f)
        assert data["tiers"]["Bartek O."]["tier"] == "FAST"
        assert data["summary"]["FAST"] == 1
    return True


def main():
    tests = [
        ("tier_classification_happy_path", test_tier_classification_happy_path),
        ("insufficient_data_default", test_insufficient_data_default),
        ("boundary_exactly_25_fast", test_boundary_exactly_25_fast),
        ("boundary_over_25_normal", test_boundary_over_25_normal),
        ("boundary_exactly_32_normal", test_boundary_exactly_32_normal),
        ("boundary_over_32_safe", test_boundary_over_32_safe),
        ("p90_computation_basic", test_p90_computation_basic),
        ("p90_empty_returns_none", test_p90_empty_returns_none),
        ("atomic_write_produces_valid_json", test_atomic_write_produces_valid_json),
    ]
    print("=" * 60)
    print("F2.2 C4: speed_tier_tracker tests")
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
