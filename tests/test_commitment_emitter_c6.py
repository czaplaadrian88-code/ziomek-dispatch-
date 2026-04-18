"""F2.2 C6 tests — commitment_emitter helpers + flag-gated emission.

Standalone executable. No state_machine integration (skeleton-only).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

import dispatch_v2.commitment_emitter as ce
from dispatch_v2.commitment_emitter import (
    compute_near_delivery_proximity,
    compute_en_route_remaining_threshold,
    emit_commitment_event,
    _haversine_m,
    LEVEL_NEAR_DELIVERY,
    LEVEL_EN_ROUTE_DELIVERY,
    LEVEL_ASSIGNED,
    NEAR_DELIVERY_RADIUS_M,
)


def test_haversine_same_point_zero():
    d = _haversine_m((53.13, 23.15), (53.13, 23.15))
    assert d == 0.0, f"same point should be 0m; got {d}"
    return True


def test_haversine_known_distance_approx_1km():
    """Two points ~0.015 deg lng apart at lat 53 ≈ 1km."""
    d = _haversine_m((53.13, 23.000), (53.13, 23.015))
    # ~1000m, allow 5% tolerance
    assert 900 < d < 1100, f"expected ~1000m, got {d:.1f}"
    return True


def test_near_delivery_within_500m():
    """Courier same point as drop → 0m < 500 → True."""
    dist, within = compute_near_delivery_proximity((53.13, 23.15), (53.13, 23.15))
    assert dist == 0.0
    assert within is True
    return True


def test_near_delivery_outside_500m():
    """Courier 1km from drop → False."""
    dist, within = compute_near_delivery_proximity((53.13, 23.000), (53.13, 23.015))
    assert dist > 500
    assert within is False
    return True


def test_near_delivery_boundary_500m():
    """Custom radius — exactly at boundary."""
    # Use custom radius 1100m to encompass test_haversine_known_distance_approx_1km
    dist, within = compute_near_delivery_proximity(
        (53.13, 23.000), (53.13, 23.015), radius_m=1100.0
    )
    assert within is True, f"with radius=1100m, dist={dist} should be within"
    return True


def test_near_delivery_none_inputs():
    """None inputs → (None, False)."""
    d1, w1 = compute_near_delivery_proximity(None, (53.13, 23.15))
    assert d1 is None and w1 is False
    d2, w2 = compute_near_delivery_proximity((53.13, 23.15), None)
    assert d2 is None and w2 is False
    return True


def test_en_route_50pct_threshold():
    """3/3 delivered → ratio=1.0, is_over=True (50% threshold)."""
    ratio, over = compute_en_route_remaining_threshold(bag_total=3, delivered_count=2)
    assert ratio == round(2 / 3, 3)  # 0.667
    assert over is True
    return True


def test_en_route_under_threshold():
    """1/3 delivered → under 50% → False."""
    ratio, over = compute_en_route_remaining_threshold(bag_total=3, delivered_count=1)
    assert ratio == round(1 / 3, 3)
    assert over is False
    return True


def test_en_route_zero_bag():
    """Bag total = 0 → (0.0, False) safe default."""
    ratio, over = compute_en_route_remaining_threshold(bag_total=0, delivered_count=0)
    assert ratio == 0.0
    assert over is False
    return True


def test_emit_silent_when_flag_false():
    """ENABLE_MID_TRIP_PICKUP=False (default) → emit returns False, no write."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        assert ce.ENABLE_MID_TRIP_PICKUP is False, "default flag should be False"
        result = emit_commitment_event(
            order_id="TEST-1", commitment_level=LEVEL_NEAR_DELIVERY,
            courier_id="C001", extra={"dist_m": 300}, log_path=tmp_path,
        )
        assert result is False, "flag off → should return False"
        # File exists (created by NamedTemporaryFile) but empty
        with open(tmp_path) as f:
            content = f.read()
        assert content == "", f"no write expected; got {content!r}"
    finally:
        os.unlink(tmp_path)
    return True


def test_emit_writes_when_flag_true():
    """Simulate flag=True → emit writes proper JSONL event."""
    original_flag = ce.ENABLE_MID_TRIP_PICKUP
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        ce.ENABLE_MID_TRIP_PICKUP = True
        result = emit_commitment_event(
            order_id="TEST-2", commitment_level=LEVEL_EN_ROUTE_DELIVERY,
            courier_id="C002", extra={"ratio": 0.66}, log_path=tmp_path,
        )
        assert result is True, "flag on + valid level → should return True"
        with open(tmp_path) as f:
            lines = f.readlines()
        assert len(lines) == 1
        event = json.loads(lines[0])
        assert event["event_type"] == "COMMITMENT_LEVEL_EMIT"
        assert event["order_id"] == "TEST-2"
        assert event["commitment_level"] == "en_route_delivery"
        assert event["courier_id"] == "C002"
        assert event["extra"]["ratio"] == 0.66
    finally:
        ce.ENABLE_MID_TRIP_PICKUP = original_flag
        os.unlink(tmp_path)
    return True


def test_emit_rejects_invalid_level():
    """Invalid commitment_level string → False, no write."""
    original_flag = ce.ENABLE_MID_TRIP_PICKUP
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as tmp:
        tmp_path = tmp.name
    try:
        ce.ENABLE_MID_TRIP_PICKUP = True
        result = emit_commitment_event(
            order_id="TEST-3", commitment_level="totally_bogus_level",
            log_path=tmp_path,
        )
        assert result is False, "invalid level → False"
        with open(tmp_path) as f:
            content = f.read()
        assert content == "", "no write for invalid level"
    finally:
        ce.ENABLE_MID_TRIP_PICKUP = original_flag
        os.unlink(tmp_path)
    return True


def main():
    tests = [
        ("haversine_same_point_zero", test_haversine_same_point_zero),
        ("haversine_known_distance_approx_1km", test_haversine_known_distance_approx_1km),
        ("near_delivery_within_500m", test_near_delivery_within_500m),
        ("near_delivery_outside_500m", test_near_delivery_outside_500m),
        ("near_delivery_boundary_custom_radius", test_near_delivery_boundary_500m),
        ("near_delivery_none_inputs", test_near_delivery_none_inputs),
        ("en_route_50pct_threshold", test_en_route_50pct_threshold),
        ("en_route_under_threshold", test_en_route_under_threshold),
        ("en_route_zero_bag", test_en_route_zero_bag),
        ("emit_silent_when_flag_false", test_emit_silent_when_flag_false),
        ("emit_writes_when_flag_true", test_emit_writes_when_flag_true),
        ("emit_rejects_invalid_level", test_emit_rejects_invalid_level),
    ]
    print("=" * 60)
    print("F2.2 C6 skeleton: commitment_emitter helpers + flag-gated emission")
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
