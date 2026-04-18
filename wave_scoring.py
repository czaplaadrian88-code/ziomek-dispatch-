"""F2.2 C5: wave_scoring — adaptive scoring module (SKELETON).

Currently: same_restaurant_boost only (quick-win per sekcja 3.3 bag_size=0 = 50.2% TIER_A).
Other features are stubs (food_court, pair_affinity, stretch_bonus, context_peak):
to be filled in subsequent C5 (b)-(e) iterations.

Feature flag: ENABLE_WAVE_SCORING (default False). Caller MUST check flag before
adding output to candidate score. This module returns pure adjustments — no side effects.

Per F2.2_SECTION_4_ARCHITECTURE_SPEC sekcja 4.1 (wave_scoring.py API).
"""
from typing import List, Optional, Tuple

from dispatch_v2.common import ENABLE_WAVE_SCORING

# Per Architecture Spec sekcja 4.1 default weights:
SAME_RESTAURANT_BOOST = 8.0          # f_same_restaurant_boost (C5 (a) quick-win)
FOOD_COURT_BOOST = 3.0               # f_food_court_boost (C5 (b), stub)
PAIR_AFFINITY_STRONG_BOOST = 2.0     # f_restaurant_pair_affinity (C5 (c), stub)
PAIR_AFFINITY_WEAK_BOOST = 0.5       # f_restaurant_pair_affinity weak (C5 (c), stub)
WAVE_CONTINUATION_BOOST = 1.5        # f_wave_continuation (directional, stub)

# Thresholds for same_restaurant_boost per sekcja 3.3 empirical bag_size=0 finding:
SAME_RESTAURANT_BAG_SIZE_MAX = 3     # kurier bag size strict less than
SAME_RESTAURANT_BAG_TIME_MAX = 25.0  # minutes, bag_time upper limit for safe auto-attach


def compute_same_restaurant_boost(
    candidate_restaurant: Optional[str],
    bag_restaurants: List[str],
    courier_bag_size: int,
    courier_bag_time_min: float,
) -> float:
    """F2.2 C5 (a) quick-win: same-restaurant auto-attach bonus.

    Per sekcja 3.3: 50.2% TIER_A missed events have courier bag_size=0 from
    same restaurant as incoming order. This rule captures the bulk of that quick-win.

    Args:
        candidate_restaurant: canonical_name of new order's restaurant (or None)
        bag_restaurants: canonical_names present in courier's current bag
        courier_bag_size: len(bag) before adding candidate
        courier_bag_time_min: elapsed minutes since oldest bag item pickup

    Returns:
        SAME_RESTAURANT_BOOST if match + safe bag state, else 0.0.

    Does NOT check ENABLE_WAVE_SCORING — caller is responsible for gating.
    """
    if not candidate_restaurant:
        return 0.0
    if courier_bag_size >= SAME_RESTAURANT_BAG_SIZE_MAX:
        return 0.0
    if courier_bag_time_min >= SAME_RESTAURANT_BAG_TIME_MAX:
        return 0.0
    if candidate_restaurant in bag_restaurants:
        return SAME_RESTAURANT_BOOST
    return 0.0


# ============================================================
# C5 iterative features — STUBS (return 0.0 until implemented)
# ============================================================

def compute_food_court_boost(*args, **kwargs) -> float:
    """F2.2 C5 (b) stub: food-court effect (zero-distance pairs per sekcja 3.2).

    Not implemented yet. Will use canonical 'same_building_id' or 11 zero-distance
    strong pairs from wave_audit_transitions_2026-04-18.csv.
    """
    return 0.0


def compute_pair_affinity_boost(*args, **kwargs) -> float:
    """F2.2 C5 (c) stub: restaurant pair affinity lookup.

    Not implemented yet. Will read 220 strong pairs from
    wave_audit_transitions_2026-04-18.csv + classify strong/weak per Signal strength.
    """
    return 0.0


def compute_stretch_bonus(*args, **kwargs) -> float:
    """F2.2 C5 (d) stub: asymmetric stretch bonus per speed tier.

    Not implemented yet. Will read courier_speed_tiers.json (C4 output) and apply
    tier-specific bonus zones per Architecture Spec sekcja 2.3.
    """
    return 0.0


def compute_context_peak_multiplier(*args, **kwargs) -> float:
    """F2.2 C5 (e) stub: context-aware PEAK regime multiplier.

    Not implemented yet. Will use 11 PEAK cells lookup per sekcja 3.5.
    """
    return 0.0


# ============================================================
# Top-level API per Architecture Spec 4.1
# ============================================================

def compute_wave_adjustment(
    candidate_restaurant: Optional[str] = None,
    bag_restaurants: Optional[List[str]] = None,
    courier_bag_size: int = 0,
    courier_bag_time_min: float = 0.0,
    # Future args (C5 b/c/d/e): courier_tier, regime, demand_context, pending_queue, ...
) -> float:
    """F2.2 C5 main entrypoint — composes all active features.

    Returns 0.0 if ENABLE_WAVE_SCORING=False (caller-neutral).
    Currently only same_restaurant_boost is active; other features are stubs.

    Sum semantics: each feature returns additive bonus/penalty; final adjustment
    is sum of all active features. Clamping (if needed) applied by caller.
    """
    if not ENABLE_WAVE_SCORING:
        return 0.0
    if bag_restaurants is None:
        bag_restaurants = []
    adjustment = 0.0
    adjustment += compute_same_restaurant_boost(
        candidate_restaurant=candidate_restaurant,
        bag_restaurants=bag_restaurants,
        courier_bag_size=courier_bag_size,
        courier_bag_time_min=courier_bag_time_min,
    )
    adjustment += compute_food_court_boost()
    adjustment += compute_pair_affinity_boost()
    adjustment += compute_stretch_bonus()
    adjustment += compute_context_peak_multiplier()
    return adjustment
