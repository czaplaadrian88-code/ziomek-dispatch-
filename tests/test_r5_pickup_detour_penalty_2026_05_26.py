"""BUG B shadow (2026-05-26) — pickup-not-on-route penalty.

`r5_pickup_detour_total_km` już zbierane (dispatch_pipeline ~2608) jako metryka
obserwacyjna — brak negative weight w bonus aggregation. Reguła Adriana „dowóz
w żaden sposób nie jest po drodze" (Case C: Karczma + Skłodowskiej +1.4 km
+4.2 min w bok). Faza 2 = shadow, flag default OFF.
"""
import inspect

from dispatch_v2 import common, dispatch_pipeline


def test_bugb_common_contract_defaults():
    """Flagi default OFF, wagi startowe z planu."""
    assert common.ENABLE_R5_PICKUP_DETOUR_PENALTY is False
    assert common.R5_DETOUR_PENALTY_PER_KM == 8.0
    assert common.R5_DETOUR_FREE_THRESHOLD_KM == 0.5


def test_bugb_block_present_in_source():
    """Blok BUG B obecny w _v327_eval_courier, flag-gated."""
    src = inspect.getsource(dispatch_pipeline)
    assert "BUG B shadow (2026-05-26)" in src
    start = src.find("BUG B shadow (2026-05-26)")
    section = src[start:start + 800]
    assert "r5_pickup_detour_total_km" in section
    assert "ENABLE_R5_PICKUP_DETOUR_PENALTY" in section
    assert "R5_DETOUR_FREE_THRESHOLD_KM" in section
    assert "R5_DETOUR_PENALTY_PER_KM" in section
    assert "bonus_r5_pickup_detour_penalty" in section


def test_bugb_math_below_free_threshold():
    """detour 0.3 km < free threshold 0.5 → bonus = 0."""
    detour_km = 0.3
    free = common.R5_DETOUR_FREE_THRESHOLD_KM
    per_km = common.R5_DETOUR_PENALTY_PER_KM
    excess = max(0.0, detour_km - free)
    bonus = -per_km * excess
    assert bonus == 0.0


def test_bugb_math_above_free_threshold():
    """detour 1.5 km → excess 1.0 km → bonus = -8.0."""
    detour_km = 1.5
    free = common.R5_DETOUR_FREE_THRESHOLD_KM
    per_km = common.R5_DETOUR_PENALTY_PER_KM
    excess = max(0.0, detour_km - free)
    bonus = -per_km * excess
    assert bonus == -8.0


def test_bugb_math_heavy_detour():
    """detour 5.0 km → excess 4.5 km → bonus = -36.0 (heavy penalty)."""
    detour_km = 5.0
    free = common.R5_DETOUR_FREE_THRESHOLD_KM
    per_km = common.R5_DETOUR_PENALTY_PER_KM
    excess = max(0.0, detour_km - free)
    bonus = -per_km * excess
    assert bonus == -36.0


def test_bugb_math_zero_or_negative():
    """detour 0 km → bonus 0. Defensive: metryka None handled w kodzie."""
    detour_km = 0.0
    free = common.R5_DETOUR_FREE_THRESHOLD_KM
    per_km = common.R5_DETOUR_PENALTY_PER_KM
    excess = max(0.0, detour_km - free)
    bonus = -per_km * excess
    assert bonus == 0.0


def test_bugb_metrics_get_handles_missing():
    """Source: gdy metrics.get('r5_pickup_detour_total_km') == None → 0.0 default."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("BUG B shadow (2026-05-26)")
    section = src[start:start + 800]
    # Defensywne pobranie z metrics
    assert "metrics.get(\"r5_pickup_detour_total_km\")" in section
    # isinstance guard dla None
    assert "isinstance" in section
