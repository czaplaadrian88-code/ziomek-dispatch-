"""V3.28 Fix 6 (incident 03.05.2026) — mass fail fallback heuristic tests.

Helper `_v328_simple_heuristic_score` — pure proximity + tier scoring,
no OR-Tools, no constraints. Returns float score (higher = better).

Used jako fallback gdy >=V328_MASS_FAIL_RATIO_THRESHOLD (default 0.5)
kurierów crash w _v327_pool (mass fail incident pattern).

Trigger integration test wymaga full assess_order mock setup —
deferred do FAZA 7 replay batch verification (Fix 7).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as dp  # noqa: E402


def _make_cs(pos=None, tier_bag="std", name="Test Courier"):
    """Build minimal FleetCourier-like SimpleNamespace dla testów."""
    return SimpleNamespace(pos=pos, tier_bag=tier_bag, name=name)


def _make_order_event(pickup_coords=(53.13, 23.16)):
    return {
        "order_id": "470999",
        "pickup_coords": pickup_coords,
        "delivery_coords": (53.14, 23.17),
        "restaurant": "Test Restaurant",
    }


def test_normal_courier_with_gps_returns_negative_proximity_score():
    """Standard courier z GPS @ 1km od pickup → score ≈ -10 (proximity*10)."""
    cs = _make_cs(pos=(53.14, 23.16), tier_bag="std")  # ~1.1 km od (53.13, 23.16)
    score = dp._v328_simple_heuristic_score("100", cs, _make_order_event())
    # Approx -1.1km * 10 = -11.0 (haversine + std tier 0)
    assert -15.0 < score < -5.0, f"expected proximity score ~-10, got {score}"


def test_no_gps_returns_penalty():
    """Courier bez GPS (pos=None) → -1000 penalty."""
    cs = _make_cs(pos=None, tier_bag="std")
    score = dp._v328_simple_heuristic_score("101", cs, _make_order_event())
    assert score == -1000.0


def test_no_gps_pos_with_none_lat_returns_penalty():
    """pos=(None, None) tuple → -1000 penalty."""
    cs = _make_cs(pos=(None, None), tier_bag="std")
    score = dp._v328_simple_heuristic_score("102", cs, _make_order_event())
    assert score == -1000.0


def test_zero_pickup_coords_returns_penalty():
    """order_event z pickup_coords=(0,0) (placeholder) → -1000 penalty."""
    cs = _make_cs(pos=(53.13, 23.16), tier_bag="gold")
    score = dp._v328_simple_heuristic_score("103", cs, _make_order_event(pickup_coords=(0.0, 0.0)))
    assert score == -1000.0


def test_tier_gold_bonus_added():
    """Gold tier kurier @ 1km → score = -proximity + 5 (gold bonus)."""
    cs_std = _make_cs(pos=(53.14, 23.16), tier_bag="std")
    cs_gold = _make_cs(pos=(53.14, 23.16), tier_bag="gold")
    score_std = dp._v328_simple_heuristic_score("110", cs_std, _make_order_event())
    score_gold = dp._v328_simple_heuristic_score("111", cs_gold, _make_order_event())
    assert abs((score_gold - score_std) - 5.0) < 0.01, f"gold bonus should be +5, got {score_gold-score_std}"


def test_tier_stdplus_bonus_added():
    """std+ tier kurier @ 1km → score = -proximity + 2 (std+ bonus)."""
    cs_std = _make_cs(pos=(53.14, 23.16), tier_bag="std")
    cs_stdplus = _make_cs(pos=(53.14, 23.16), tier_bag="std+")
    score_std = dp._v328_simple_heuristic_score("120", cs_std, _make_order_event())
    score_stdplus = dp._v328_simple_heuristic_score("121", cs_stdplus, _make_order_event())
    assert abs((score_stdplus - score_std) - 2.0) < 0.01


def test_unknown_tier_defaults_to_std():
    """Unknown tier (np. 'platinum') → 0 bonus (std default)."""
    cs_std = _make_cs(pos=(53.14, 23.16), tier_bag="std")
    cs_unknown = _make_cs(pos=(53.14, 23.16), tier_bag="platinum")  # nieistniejący tier
    score_std = dp._v328_simple_heuristic_score("130", cs_std, _make_order_event())
    score_unknown = dp._v328_simple_heuristic_score("131", cs_unknown, _make_order_event())
    assert abs(score_std - score_unknown) < 0.01, "unknown tier should fallback to std (0 bonus)"


def test_proximity_decreases_score_with_distance():
    """Distant courier @ 5km → niższy score niż @ 1km."""
    cs_close = _make_cs(pos=(53.14, 23.16), tier_bag="std")  # ~1km
    cs_far = _make_cs(pos=(53.18, 23.20), tier_bag="std")  # ~5km
    score_close = dp._v328_simple_heuristic_score("140", cs_close, _make_order_event())
    score_far = dp._v328_simple_heuristic_score("141", cs_far, _make_order_event())
    assert score_close > score_far, f"closer courier should have higher score; close={score_close}, far={score_far}"


def test_no_tier_attribute_defaults_to_std():
    """Courier bez tier_bag attribute → std default (0 bonus)."""
    cs_no_tier = SimpleNamespace(pos=(53.14, 23.16), name="No Tier")  # brak tier_bag
    score = dp._v328_simple_heuristic_score("150", cs_no_tier, _make_order_event())
    cs_explicit_std = _make_cs(pos=(53.14, 23.16), tier_bag="std")
    score_std = dp._v328_simple_heuristic_score("151", cs_explicit_std, _make_order_event())
    assert abs(score - score_std) < 0.01


def test_exception_in_haversine_returns_penalty():
    """Edge: invalid pickup_coords causing haversine exception → -1000 (try/except)."""
    cs = _make_cs(pos=(53.14, 23.16), tier_bag="std")
    # pickup_coords ze stringami (osrm haversine może crashować)
    bad_event = {"pickup_coords": ("nan", "nan")}
    score = dp._v328_simple_heuristic_score("160", cs, bad_event)
    # Should NOT raise — try/except returns -1000
    assert score == -1000.0


def test_helper_returns_float_type():
    """Type guarantee: returns float (NIE int, None, str)."""
    cs = _make_cs(pos=(53.14, 23.16), tier_bag="gold")
    score = dp._v328_simple_heuristic_score("170", cs, _make_order_event())
    assert isinstance(score, float)
