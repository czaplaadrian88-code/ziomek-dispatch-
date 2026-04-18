"""F2.2 C5 full: wave_scoring — adaptive scoring engine (6 features).

Per Architecture Spec sekcja 4.1 + 5.C5.

Features (all default OFF — ENABLE_WAVE_SCORING=False → total=0):
  1. same_restaurant_boost (C5 a — quick-win, 50.2% TIER_A bag_size=0)
  2. food_court_boost (C5 b — 16 zero-distance strong pairs from sekcja 3.2)
  3. pair_affinity_boost (C5 c — 220 strong + 180 weak pairs from sekcja 3.2)
  4. stretch_bonus (C5 d — asymmetric per speed tier, UPDATE A)
  5. wave_continuation (bundle trajectory alignment with candidate)
  6. context_peak_multiplier (C5 e — 11 PEAK cells from sekcja 3.5)

Composition:
  adjustments = sum(features 1-5)
  if peak: adjustments *= context_peak_multiplier (applied to stretch + pair_affinity only)
  final: return total adjustment

Integration: scoring.py accepts wave_adjustment kwarg, gated by ENABLE_WAVE_SCORING.
Shadow log: dispatch_state/c5_shadow_log.jsonl when ENABLE_C5_SHADOW_LOG=True.
"""
import csv
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dispatch_v2.common import ENABLE_C5_SHADOW_LOG, ENABLE_WAVE_SCORING

# Default weights per Architecture Spec 4.1 — tunable via Adrian calibration post shadow.
SAME_RESTAURANT_BOOST = 8.0
FOOD_COURT_BOOST = 3.0
PAIR_AFFINITY_STRONG_BOOST = 2.0
PAIR_AFFINITY_WEAK_BOOST = 0.5
WAVE_CONTINUATION_ALIGNED_BOOST = 2.0
WAVE_CONTINUATION_MISALIGNED_PENALTY = -1.0

# same_restaurant safety thresholds (sekcja 3.3 empirical)
SAME_RESTAURANT_BAG_SIZE_MAX = 3
SAME_RESTAURANT_BAG_TIME_MAX = 25.0

# Speed tier stretch bonus zones (spec sekcja 2.3) — explicit zones per tier
# FAST:    <22 → -3.0, [22, 30] → +5.0, (30, 35] → 0 neutral
# NORMAL:  <18 → -1.0, [18, 26] → +3.0, (26, 35] → 0 neutral
# SAFE:    <13 → 0 acceptable, [13, 20] → +2.0, (20, 28] → 0 neutral, (28, 35] → -3.0 penalty
# ALL:     >35 → -5.0 (beyond global hard limit; C2 should reject, defensive)
STRETCH_GLOBAL_HARD_MAX = 35.0
STRETCH_OVER_HARD_PENALTY = -5.0

# Wave continuation thresholds (cosine similarity)
WAVE_ALIGN_COSINE_HIGH = 0.7   # > 0.7 → aligned bonus
WAVE_ALIGN_COSINE_LOW = 0.3    # < 0.3 → misaligned penalty (opposite directions)

# Peak multiplier applied to stretch + pair_affinity w PEAK cells
PEAK_MULTIPLIER = 1.5
NORMAL_MULTIPLIER = 1.0

# Shadow log threshold — emit event only for meaningful diffs (magnitude > 1.0)
SHADOW_LOG_MIN_DIFF = 1.0
C5_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/c5_shadow_log.jsonl"

# Data file paths (loaded lazily, cached at module level)
_DATA_DIR = Path("/root/.openclaw/workspace/docs/wave_audit_outputs/2026-04-18")
_TRANSITIONS_CSV = _DATA_DIR / "wave_audit_transitions_2026-04-18.csv"
_PEAK_REGIMES_CSV = _DATA_DIR / "wave_audit_peak_regimes_2026-04-18.csv"

# Cached lookups
_FOOD_COURT_PAIRS: Optional[set] = None         # set of (R_from, R_to) where dist==0, strong
_PAIR_AFFINITY: Optional[Dict[Tuple[str, str], str]] = None  # (from, to) → "strong"|"weak"
_PEAK_CELLS: Optional[set] = None               # set of (hour, dayofweek)


def _load_food_court_pairs() -> set:
    global _FOOD_COURT_PAIRS
    if _FOOD_COURT_PAIRS is not None:
        return _FOOD_COURT_PAIRS
    pairs = set()
    try:
        if _TRANSITIONS_CSV.exists():
            with open(_TRANSITIONS_CSV) as f:
                for r in csv.DictReader(f):
                    if r.get("signal_strength") != "strong":
                        continue
                    dist_s = r.get("median_distance_km", "")
                    if dist_s == "" or (dist_s and float(dist_s) == 0.0):
                        pairs.add((r["R_from"], r["R_to"]))
    except Exception:
        pass
    _FOOD_COURT_PAIRS = pairs
    return pairs


def _load_pair_affinity() -> Dict[Tuple[str, str], str]:
    global _PAIR_AFFINITY
    if _PAIR_AFFINITY is not None:
        return _PAIR_AFFINITY
    out: Dict[Tuple[str, str], str] = {}
    try:
        if _TRANSITIONS_CSV.exists():
            with open(_TRANSITIONS_CSV) as f:
                for r in csv.DictReader(f):
                    sig = r.get("signal_strength", "")
                    if sig in ("strong", "weak"):
                        out[(r["R_from"], r["R_to"])] = sig
    except Exception:
        pass
    _PAIR_AFFINITY = out
    return out


def _load_peak_cells() -> set:
    global _PEAK_CELLS
    if _PEAK_CELLS is not None:
        return _PEAK_CELLS
    cells = set()
    try:
        if _PEAK_REGIMES_CSV.exists():
            with open(_PEAK_REGIMES_CSV) as f:
                for r in csv.DictReader(f):
                    if r.get("regime") == "PEAK":
                        cells.add((int(r["hour"]), int(r["dayofweek"])))
    except Exception:
        pass
    _PEAK_CELLS = cells
    return cells


# ============================================================
# Feature 1: same_restaurant_boost (already live from skeleton)
# ============================================================
def compute_same_restaurant_boost(
    candidate_restaurant: Optional[str],
    bag_restaurants: List[str],
    courier_bag_size: int,
    courier_bag_time_min: float,
) -> float:
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
# Feature 2: food_court_boost (zero-distance strong pairs, C5 b)
# ============================================================
def compute_food_court_boost(
    candidate_restaurant: Optional[str],
    bag_restaurants: List[str],
) -> float:
    if not candidate_restaurant or not bag_restaurants:
        return 0.0
    pairs = _load_food_court_pairs()
    for bag_r in bag_restaurants:
        if (bag_r, candidate_restaurant) in pairs or (candidate_restaurant, bag_r) in pairs:
            return FOOD_COURT_BOOST
    return 0.0


# ============================================================
# Feature 3: pair_affinity (220 strong + 180 weak pairs, C5 c)
# ============================================================
def compute_pair_affinity_boost(
    candidate_restaurant: Optional[str],
    last_bag_restaurant: Optional[str],
) -> float:
    if not candidate_restaurant or not last_bag_restaurant:
        return 0.0
    affinity = _load_pair_affinity()
    signal = affinity.get((last_bag_restaurant, candidate_restaurant))
    if signal == "strong":
        return PAIR_AFFINITY_STRONG_BOOST
    if signal == "weak":
        return PAIR_AFFINITY_WEAK_BOOST
    return 0.0


# ============================================================
# Feature 4: stretch_bonus (asymmetric per tier, C5 d)
# ============================================================
def compute_stretch_bonus(
    courier_tier: str,
    per_order_delivery_times: Optional[Dict[str, float]],
) -> float:
    """Explicit zones per tier. INSUFFICIENT_DATA treated as NORMAL."""
    if not per_order_delivery_times:
        return 0.0
    max_e = max(per_order_delivery_times.values())
    if max_e > STRETCH_GLOBAL_HARD_MAX:
        return STRETCH_OVER_HARD_PENALTY
    if courier_tier == "FAST":
        if max_e < 22.0:
            return -3.0
        if max_e <= 30.0:
            return 5.0
        return 0.0  # (30, 35] neutral
    if courier_tier == "SAFE":
        if max_e < 13.0:
            return 0.0  # acceptable under-use for SAFE tier
        if max_e <= 20.0:
            return 2.0
        if max_e <= 28.0:
            return 0.0  # (20, 28] neutral
        return -3.0  # (28, 35] penalty zone
    # NORMAL / INSUFFICIENT_DATA treated as NORMAL
    if max_e < 18.0:
        return -1.0
    if max_e <= 26.0:
        return 3.0
    return 0.0  # (26, 35] neutral


# ============================================================
# Feature 5: wave_continuation (trajectory alignment)
# ============================================================
def _bearing_deg(origin: Tuple[float, float], target: Tuple[float, float]) -> float:
    """Great-circle bearing origin→target in degrees [0, 360)."""
    lat1, lon1 = math.radians(origin[0]), math.radians(origin[1])
    lat2, lon2 = math.radians(target[0]), math.radians(target[1])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _cosine_similarity_bearings(b1: float, b2: float) -> float:
    """Cosine similarity of two bearings (degrees). 1.0=same, -1.0=opposite, 0=90deg."""
    return math.cos(math.radians(abs(b1 - b2)))


def compute_wave_continuation(
    courier_pos: Optional[Tuple[float, float]],
    bundle_drop_coords: Optional[List[Tuple[float, float]]],
    candidate_pickup_coords: Optional[Tuple[float, float]],
) -> float:
    if not courier_pos or not bundle_drop_coords or not candidate_pickup_coords:
        return 0.0
    # Bundle centroid (average drop coords)
    valid_drops = [c for c in bundle_drop_coords if c and len(c) == 2]
    if not valid_drops:
        return 0.0
    cx = sum(c[0] for c in valid_drops) / len(valid_drops)
    cy = sum(c[1] for c in valid_drops) / len(valid_drops)
    bundle_centroid = (cx, cy)
    bearing_bundle = _bearing_deg(courier_pos, bundle_centroid)
    bearing_candidate = _bearing_deg(courier_pos, candidate_pickup_coords)
    cos_sim = _cosine_similarity_bearings(bearing_bundle, bearing_candidate)
    if cos_sim > WAVE_ALIGN_COSINE_HIGH:
        return WAVE_CONTINUATION_ALIGNED_BOOST
    if cos_sim < WAVE_ALIGN_COSINE_LOW:
        return WAVE_CONTINUATION_MISALIGNED_PENALTY
    return 0.0


# ============================================================
# Feature 6: context_peak_multiplier (PEAK lookup, C5 e)
# ============================================================
def compute_context_peak_multiplier(
    current_hour: Optional[int],
    current_dayofweek: Optional[int],
) -> float:
    if current_hour is None or current_dayofweek is None:
        return NORMAL_MULTIPLIER
    peak_cells = _load_peak_cells()
    if (current_hour, current_dayofweek) in peak_cells:
        return PEAK_MULTIPLIER
    return NORMAL_MULTIPLIER


# ============================================================
# Shadow log emitter
# ============================================================
def _emit_c5_shadow_diff(breakdown: Dict[str, float], total: float, context: Dict) -> None:
    """Append C5_SHADOW_DIFF event to c5_shadow_log.jsonl when total magnitude > threshold."""
    try:
        event = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event_type": "C5_SHADOW_DIFF",
            "total_adjustment": round(total, 3),
            "breakdown": {k: round(v, 3) for k, v in breakdown.items()},
            "context": context,
            "flag_wave_scoring": ENABLE_WAVE_SCORING,
        }
        with open(C5_SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception:
        pass  # never fail main flow on log error


# ============================================================
# Top-level API — aggregate 6 features
# ============================================================
def compute_wave_adjustment(
    candidate_restaurant: Optional[str] = None,
    bag_restaurants: Optional[List[str]] = None,
    courier_bag_size: int = 0,
    courier_bag_time_min: float = 0.0,
    # F2.2 C5 full (2026-04-18) extended context:
    last_bag_restaurant: Optional[str] = None,
    courier_tier: str = "INSUFFICIENT_DATA",
    per_order_delivery_times: Optional[Dict[str, float]] = None,
    courier_pos: Optional[Tuple[float, float]] = None,
    bundle_drop_coords: Optional[List[Tuple[float, float]]] = None,
    candidate_pickup_coords: Optional[Tuple[float, float]] = None,
    current_hour: Optional[int] = None,
    current_dayofweek: Optional[int] = None,
    # Context identifiers for shadow log
    order_id: Optional[str] = None,
    courier_anon: Optional[str] = None,
) -> float:
    """F2.2 C5 main entrypoint — sum of all 6 features, multiplied by context.

    Returns 0.0 if ENABLE_WAVE_SCORING=False AND ENABLE_C5_SHADOW_LOG=False.
    If ENABLE_C5_SHADOW_LOG=True: features computed regardless of flag, emits diff events.
    If ENABLE_WAVE_SCORING=True: adjustment applied to final score (caller responsibility).

    Returns float sum adjustment (can be negative). Peak multiplier amplifies
    stretch + pair_affinity.
    """
    if bag_restaurants is None:
        bag_restaurants = []

    # Always compute (enables shadow logging when flag=False)
    srb = compute_same_restaurant_boost(
        candidate_restaurant, bag_restaurants, courier_bag_size, courier_bag_time_min
    )
    fcb = compute_food_court_boost(candidate_restaurant, bag_restaurants)
    paf = compute_pair_affinity_boost(candidate_restaurant, last_bag_restaurant)
    stb = compute_stretch_bonus(courier_tier, per_order_delivery_times)
    wcn = compute_wave_continuation(courier_pos, bundle_drop_coords, candidate_pickup_coords)
    peak_mult = compute_context_peak_multiplier(current_hour, current_dayofweek)

    # Peak multiplier applies to stretch + pair_affinity (other features unaffected)
    stb_after_peak = stb * peak_mult
    paf_after_peak = paf * peak_mult

    total = srb + fcb + paf_after_peak + stb_after_peak + wcn

    breakdown = {
        "same_restaurant_boost": srb,
        "food_court_boost": fcb,
        "pair_affinity_boost": paf,
        "pair_affinity_after_peak": paf_after_peak,
        "stretch_bonus": stb,
        "stretch_after_peak": stb_after_peak,
        "wave_continuation": wcn,
        "context_peak_multiplier": peak_mult,
    }

    # Shadow log (observational — regardless of flag)
    if ENABLE_C5_SHADOW_LOG and abs(total) >= SHADOW_LOG_MIN_DIFF:
        context = {
            "order_id": order_id,
            "courier_anon": courier_anon,
            "courier_tier": courier_tier,
            "courier_bag_size": courier_bag_size,
            "hour": current_hour,
            "dayofweek": current_dayofweek,
            "peak_regime": peak_mult == PEAK_MULTIPLIER,
        }
        _emit_c5_shadow_diff(breakdown, total, context)

    if not ENABLE_WAVE_SCORING:
        return 0.0

    return total
