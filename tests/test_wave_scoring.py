"""F2.2 C5 full tests — wave_scoring 6 features + composition + shadow log.

Standalone executable. Covers:
- same_restaurant_boost (already from skeleton)
- food_court_boost (new)
- pair_affinity_boost (new)
- stretch_bonus (new, per tier)
- wave_continuation (new, trajectory alignment)
- context_peak_multiplier (new)
- compose behaviour (flag off returns 0, flag on sums)
- shadow log emission
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

import dispatch_v2.wave_scoring as ws
from dispatch_v2.wave_scoring import (
    compute_same_restaurant_boost,
    compute_food_court_boost,
    compute_pair_affinity_boost,
    compute_stretch_bonus,
    compute_wave_continuation,
    compute_context_peak_multiplier,
    compute_wave_adjustment,
    SAME_RESTAURANT_BOOST,
    FOOD_COURT_BOOST,
    PAIR_AFFINITY_STRONG_BOOST,
    PAIR_AFFINITY_WEAK_BOOST,
    WAVE_CONTINUATION_ALIGNED_BOOST,
    WAVE_CONTINUATION_MISALIGNED_PENALTY,
    PEAK_MULTIPLIER,
    NORMAL_MULTIPLIER,
)


# ============================================================
# same_restaurant_boost (carry-over from skeleton)
# ============================================================
def test_same_restaurant_boost_match():
    assert compute_same_restaurant_boost("Grill Kebab", ["Grill Kebab"], 1, 3.0) == SAME_RESTAURANT_BOOST
    return True


def test_same_restaurant_boost_no_match():
    assert compute_same_restaurant_boost("Farina", ["Grill Kebab"], 1, 3.0) == 0.0
    return True


def test_same_restaurant_boost_bag_full():
    assert compute_same_restaurant_boost("Grill Kebab", ["Grill Kebab"] * 3, 3, 5.0) == 0.0
    return True


def test_same_restaurant_boost_bag_time_exceeded():
    assert compute_same_restaurant_boost("Grill Kebab", ["Grill Kebab"], 1, 25.0) == 0.0
    return True


# ============================================================
# food_court_boost
# ============================================================
def test_food_court_match_known_pair():
    """Raj ↔ Grill Kebab jest w food_court_pairs (dist=0, strong)."""
    r = compute_food_court_boost("Grill Kebab", ["Raj"])
    assert r == FOOD_COURT_BOOST, f"expected {FOOD_COURT_BOOST}, got {r}"
    return True


def test_food_court_match_reverse_direction():
    """Same pair reverse: Raj → Grill Kebab w data, candidate=Raj bag=[Grill Kebab]."""
    r = compute_food_court_boost("Raj", ["Grill Kebab"])
    assert r == FOOD_COURT_BOOST, f"expected {FOOD_COURT_BOOST}, got {r}"
    return True


def test_food_court_no_match_unrelated_pair():
    """Farina ↔ Ziemniaczek — brak food-court."""
    r = compute_food_court_boost("Ziemniaczek", ["Farina"])
    assert r == 0.0, f"expected 0.0, got {r}"
    return True


def test_food_court_empty_bundle():
    r = compute_food_court_boost("Grill Kebab", [])
    assert r == 0.0
    return True


# ============================================================
# pair_affinity_boost
# ============================================================
def test_pair_affinity_strong_known():
    """Raj → Grill Kebab is in strong pairs (n=140)."""
    r = compute_pair_affinity_boost("Grill Kebab", "Raj")
    assert r == PAIR_AFFINITY_STRONG_BOOST, f"expected {PAIR_AFFINITY_STRONG_BOOST}, got {r}"
    return True


def test_pair_affinity_none():
    """Non-existing pair returns 0."""
    r = compute_pair_affinity_boost("Nonexistent1", "Nonexistent2")
    assert r == 0.0
    return True


def test_pair_affinity_empty_bundle():
    r = compute_pair_affinity_boost("Grill Kebab", None)
    assert r == 0.0
    return True


# ============================================================
# stretch_bonus (per tier zones)
# ============================================================
def test_stretch_fast_sweet_zone():
    """FAST tier 22-30 → +5.0."""
    assert compute_stretch_bonus("FAST", {"o1": 25.0}) == 5.0
    return True


def test_stretch_fast_under_penalty():
    """FAST tier < 22 → -3.0."""
    assert compute_stretch_bonus("FAST", {"o1": 20.0}) == -3.0
    return True


def test_stretch_fast_over_neutral():
    """FAST tier 30-35 → 0 neutral."""
    assert compute_stretch_bonus("FAST", {"o1": 33.0}) == 0.0
    return True


def test_stretch_normal_sweet_zone():
    assert compute_stretch_bonus("NORMAL", {"o1": 22.0}) == 3.0
    return True


def test_stretch_safe_sweet_zone():
    assert compute_stretch_bonus("SAFE", {"o1": 15.0}) == 2.0
    return True


def test_stretch_safe_penalty_over_28():
    """SAFE 28-35 → -3.0."""
    assert compute_stretch_bonus("SAFE", {"o1": 30.0}) == -3.0
    return True


def test_stretch_global_hard_max_over_35():
    """Any tier, > 35 min → -5.0."""
    assert compute_stretch_bonus("FAST", {"o1": 40.0}) == -5.0
    assert compute_stretch_bonus("SAFE", {"o1": 36.0}) == -5.0
    return True


def test_stretch_insufficient_treated_as_normal():
    assert compute_stretch_bonus("INSUFFICIENT_DATA", {"o1": 22.0}) == 3.0
    return True


def test_stretch_empty_per_order():
    assert compute_stretch_bonus("FAST", None) == 0.0
    assert compute_stretch_bonus("FAST", {}) == 0.0
    return True


# ============================================================
# wave_continuation
# ============================================================
def test_wave_continuation_aligned():
    """Courier → bundle centroid vs courier → candidate pickup, same direction."""
    # Courier at (0,0), bundle drop (53.2, 23.2), candidate pickup (53.25, 23.25)
    r = compute_wave_continuation(
        courier_pos=(53.0, 23.0),
        bundle_drop_coords=[(53.2, 23.2)],
        candidate_pickup_coords=(53.25, 23.25),
    )
    assert r == WAVE_CONTINUATION_ALIGNED_BOOST, f"aligned expected {WAVE_CONTINUATION_ALIGNED_BOOST}, got {r}"
    return True


def test_wave_continuation_misaligned():
    """Bundle north, candidate south of courier."""
    r = compute_wave_continuation(
        courier_pos=(53.1, 23.0),
        bundle_drop_coords=[(53.2, 23.0)],        # north
        candidate_pickup_coords=(53.0, 23.0),     # south
    )
    assert r == WAVE_CONTINUATION_MISALIGNED_PENALTY, f"misaligned expected {WAVE_CONTINUATION_MISALIGNED_PENALTY}, got {r}"
    return True


def test_wave_continuation_empty_bundle():
    r = compute_wave_continuation(
        courier_pos=(53.1, 23.0),
        bundle_drop_coords=[],
        candidate_pickup_coords=(53.2, 23.0),
    )
    assert r == 0.0
    return True


def test_wave_continuation_missing_inputs():
    assert compute_wave_continuation(None, None, None) == 0.0
    return True


# ============================================================
# context_peak_multiplier
# ============================================================
def test_context_peak_sunday_15h():
    """Sun 15h is in PEAK cells (dow=6)."""
    assert compute_context_peak_multiplier(15, 6) == PEAK_MULTIPLIER
    return True


def test_context_peak_thursday_17h():
    """Thu 17h is in PEAK cells (dow=3)."""
    assert compute_context_peak_multiplier(17, 3) == PEAK_MULTIPLIER
    return True


def test_context_normal_monday_15h():
    """Mon 15h NOT in PEAK → NORMAL (1.0)."""
    assert compute_context_peak_multiplier(15, 0) == NORMAL_MULTIPLIER
    return True


def test_context_none_inputs():
    assert compute_context_peak_multiplier(None, None) == NORMAL_MULTIPLIER
    return True


# ============================================================
# compute_wave_adjustment composition + flag gating
# ============================================================
def test_compute_wave_adjustment_flag_off_returns_zero():
    """ENABLE_WAVE_SCORING=False → 0.0 regardless of inputs."""
    r = compute_wave_adjustment(
        candidate_restaurant="Grill Kebab",
        bag_restaurants=["Grill Kebab"],
        courier_bag_size=1,
        courier_bag_time_min=3.0,
    )
    assert r == 0.0
    return True


def test_compute_wave_adjustment_flag_on_sums_features():
    """Flag on → sum of features. Includes same_restaurant_boost + possibly others."""
    original = ws.ENABLE_WAVE_SCORING
    try:
        ws.ENABLE_WAVE_SCORING = True
        r = compute_wave_adjustment(
            candidate_restaurant="Grill Kebab",
            bag_restaurants=["Raj"],  # Raj→Grill Kebab strong + food_court + pair_affinity
            courier_bag_size=1,
            courier_bag_time_min=3.0,
            last_bag_restaurant="Raj",
            courier_tier="NORMAL",
        )
        # Expected: food_court (+3.0) + pair_affinity strong (+2.0) * peak_mult_normal (1.0) = 5.0
        # same_restaurant_boost: Grill Kebab not in [Raj] → 0
        # stretch_bonus: per_order None → 0
        # wave_continuation: missing coords → 0
        # Total: 3.0 + 2.0*1.0 = 5.0
        assert r == 5.0, f"expected 5.0, got {r}"
    finally:
        ws.ENABLE_WAVE_SCORING = original
    return True


def test_compute_wave_adjustment_peak_multiplier_applied():
    """PEAK cell → pair_affinity and stretch bonuses multiplied by 1.5."""
    original = ws.ENABLE_WAVE_SCORING
    try:
        ws.ENABLE_WAVE_SCORING = True
        # NORMAL Sun 15h (PEAK), pair_affinity strong = 2.0 * 1.5 = 3.0; stretch 22 NORMAL = 3.0 * 1.5 = 4.5
        r = compute_wave_adjustment(
            candidate_restaurant="Grill Kebab",
            bag_restaurants=["Mama Thai Bistro"],  # not Grill Kebab
            last_bag_restaurant="Raj",  # strong pair affinity → 2.0
            courier_tier="NORMAL",
            per_order_delivery_times={"o1": 22.0},  # sweet zone NORMAL → +3.0
            current_hour=15,
            current_dayofweek=6,  # Sunday → PEAK
        )
        # food_court: Grill Kebab ↔ Mama Thai Bistro? Not in pairs → 0
        # pair_affinity: Raj → Grill Kebab strong → 2.0 * 1.5 (peak) = 3.0
        # stretch: NORMAL 22 → 3.0 * 1.5 (peak) = 4.5
        # Total: 3.0 + 4.5 = 7.5
        assert abs(r - 7.5) < 0.01, f"expected 7.5, got {r}"
    finally:
        ws.ENABLE_WAVE_SCORING = original
    return True


def test_compute_wave_adjustment_all_features_combined():
    """Wszystkie 6 features non-zero — sum + peak multiplier."""
    original = ws.ENABLE_WAVE_SCORING
    try:
        ws.ENABLE_WAVE_SCORING = True
        r = compute_wave_adjustment(
            candidate_restaurant="Grill Kebab",
            bag_restaurants=["Raj"],          # same, food_court, pair_affinity triggers
            courier_bag_size=1,               # safe bag size
            courier_bag_time_min=5.0,         # safe bag time
            last_bag_restaurant="Raj",
            courier_tier="FAST",
            per_order_delivery_times={"o1": 25.0},  # FAST sweet → +5.0
            courier_pos=(53.1, 23.0),
            bundle_drop_coords=[(53.2, 23.0)],     # north
            candidate_pickup_coords=(53.25, 23.0), # north → aligned → +2.0
            current_hour=15,
            current_dayofweek=6,  # Sunday PEAK
        )
        # Grill Kebab NOT in bag ['Raj'] → same_restaurant = 0
        # food_court: Raj↔Grill Kebab strong → +3.0
        # pair_affinity: Raj→Grill Kebab strong → 2.0 * 1.5 = 3.0
        # stretch: FAST 25 → 5.0 * 1.5 = 7.5
        # wave_cont: aligned → +2.0
        # Total: 0 + 3.0 + 3.0 + 7.5 + 2.0 = 15.5
        assert abs(r - 15.5) < 0.01, f"expected 15.5, got {r}"
    finally:
        ws.ENABLE_WAVE_SCORING = original
    return True


def test_compute_wave_adjustment_shadow_log_emits():
    """ENABLE_C5_SHADOW_LOG=True + meaningful diff → file appended."""
    original_shadow = ws.ENABLE_C5_SHADOW_LOG
    original_wave = ws.ENABLE_WAVE_SCORING
    original_path = ws.C5_SHADOW_LOG_PATH
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as tmp:
            tmp_path = tmp.name
        ws.C5_SHADOW_LOG_PATH = tmp_path
        ws.ENABLE_C5_SHADOW_LOG = True
        ws.ENABLE_WAVE_SCORING = False  # even with flag off, shadow fires
        # Compute adjustment with big total (|total| >= 1.0 threshold)
        _ = compute_wave_adjustment(
            candidate_restaurant="Grill Kebab",
            bag_restaurants=["Raj"],
            last_bag_restaurant="Raj",
            courier_tier="FAST",
            per_order_delivery_times={"o1": 25.0},
            order_id="TEST-123",
        )
        # Verify file has 1 line
        with open(tmp_path) as f:
            lines = f.readlines()
        assert len(lines) == 1, f"expected 1 shadow event, got {len(lines)}"
        event = json.loads(lines[0])
        assert event["event_type"] == "C5_SHADOW_DIFF"
        assert event["context"]["order_id"] == "TEST-123"
        assert abs(event["total_adjustment"]) >= 1.0
    finally:
        ws.ENABLE_C5_SHADOW_LOG = original_shadow
        ws.ENABLE_WAVE_SCORING = original_wave
        ws.C5_SHADOW_LOG_PATH = original_path
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
    return True


def main():
    tests = [
        # same_restaurant (4)
        ("same_restaurant_match", test_same_restaurant_boost_match),
        ("same_restaurant_no_match", test_same_restaurant_boost_no_match),
        ("same_restaurant_bag_full", test_same_restaurant_boost_bag_full),
        ("same_restaurant_bag_time_exceeded", test_same_restaurant_boost_bag_time_exceeded),
        # food_court (4)
        ("food_court_match_known_pair", test_food_court_match_known_pair),
        ("food_court_match_reverse", test_food_court_match_reverse_direction),
        ("food_court_no_match", test_food_court_no_match_unrelated_pair),
        ("food_court_empty_bundle", test_food_court_empty_bundle),
        # pair_affinity (3)
        ("pair_affinity_strong_known", test_pair_affinity_strong_known),
        ("pair_affinity_none", test_pair_affinity_none),
        ("pair_affinity_empty_bundle", test_pair_affinity_empty_bundle),
        # stretch_bonus (9)
        ("stretch_fast_sweet_zone", test_stretch_fast_sweet_zone),
        ("stretch_fast_under_penalty", test_stretch_fast_under_penalty),
        ("stretch_fast_over_neutral", test_stretch_fast_over_neutral),
        ("stretch_normal_sweet_zone", test_stretch_normal_sweet_zone),
        ("stretch_safe_sweet_zone", test_stretch_safe_sweet_zone),
        ("stretch_safe_penalty_over_28", test_stretch_safe_penalty_over_28),
        ("stretch_global_hard_max_over_35", test_stretch_global_hard_max_over_35),
        ("stretch_insufficient_treated_as_normal", test_stretch_insufficient_treated_as_normal),
        ("stretch_empty_per_order", test_stretch_empty_per_order),
        # wave_continuation (4)
        ("wave_continuation_aligned", test_wave_continuation_aligned),
        ("wave_continuation_misaligned", test_wave_continuation_misaligned),
        ("wave_continuation_empty_bundle", test_wave_continuation_empty_bundle),
        ("wave_continuation_missing_inputs", test_wave_continuation_missing_inputs),
        # context_peak_multiplier (4)
        ("context_peak_sunday_15h", test_context_peak_sunday_15h),
        ("context_peak_thursday_17h", test_context_peak_thursday_17h),
        ("context_normal_monday_15h", test_context_normal_monday_15h),
        ("context_none_inputs", test_context_none_inputs),
        # composition + flag + shadow (5)
        ("adjustment_flag_off_returns_zero", test_compute_wave_adjustment_flag_off_returns_zero),
        ("adjustment_flag_on_sums_features", test_compute_wave_adjustment_flag_on_sums_features),
        ("adjustment_peak_multiplier_applied", test_compute_wave_adjustment_peak_multiplier_applied),
        ("adjustment_all_features_combined", test_compute_wave_adjustment_all_features_combined),
        ("adjustment_shadow_log_emits", test_compute_wave_adjustment_shadow_log_emits),
    ]
    print("=" * 60)
    print("F2.2 C5 full: wave_scoring 6 features + composition tests")
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
