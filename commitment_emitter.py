"""F2.2 C6: Mid-trip pickup commitment emitter (SKELETON).

Helper functions + event emitter for commitment_level transitions beyond the
3 that state_machine.py already tracks (planned/assigned/picked_up).

Adds computation helpers for:
  - near_delivery detection (courier <500m of next drop)
  - en_route_remaining tracking (>=50% bag delivered)

Emission gated by ENABLE_MID_TRIP_PICKUP flag (default False).
When True → appends events to dispatch_state/commitment_levels_log.jsonl.

Standalone module — no integration with state_machine.py lifecycle yet.
C7 sprint will wire callers in dispatch_pipeline / panel_watcher.

Per F2.2_SECTION_4_ARCHITECTURE_SPEC sekcja 4.3 + 5.C6.
"""
import json
import math
from datetime import datetime, timezone
from typing import Optional, Tuple

from dispatch_v2.common import ENABLE_MID_TRIP_PICKUP

COMMITMENT_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/commitment_levels_log.jsonl"
NEAR_DELIVERY_RADIUS_M = 500.0
EN_ROUTE_REMAINING_THRESHOLD = 0.5  # 50% of bag delivered

# Commitment level strings (matches state_machine.COMMITMENT_LEVELS keys)
LEVEL_PLANNED = "planned"
LEVEL_ASSIGNED = "assigned"
LEVEL_ARRIVED_AT_PICKUP = "arrived_at_pickup"
LEVEL_PICKED_UP = "picked_up"
LEVEL_EN_ROUTE_DELIVERY = "en_route_delivery"
LEVEL_NEAR_DELIVERY = "near_delivery"

VALID_LEVELS = {
    LEVEL_PLANNED, LEVEL_ASSIGNED, LEVEL_ARRIVED_AT_PICKUP,
    LEVEL_PICKED_UP, LEVEL_EN_ROUTE_DELIVERY, LEVEL_NEAR_DELIVERY,
}


def _haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Great-circle distance in meters between two lat/lng points."""
    R = 6371000.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


def compute_near_delivery_proximity(
    courier_pos: Optional[Tuple[float, float]],
    drop_pos: Optional[Tuple[float, float]],
    radius_m: float = NEAR_DELIVERY_RADIUS_M,
) -> Tuple[Optional[float], bool]:
    """Returns (distance_m, is_within_radius). None inputs → (None, False)."""
    if not courier_pos or not drop_pos:
        return (None, False)
    if not (isinstance(courier_pos, (tuple, list)) and len(courier_pos) == 2):
        return (None, False)
    if not (isinstance(drop_pos, (tuple, list)) and len(drop_pos) == 2):
        return (None, False)
    dist_m = _haversine_m(courier_pos, drop_pos)
    return (round(dist_m, 1), dist_m <= radius_m)


def compute_en_route_remaining_threshold(
    bag_total: int,
    delivered_count: int,
    threshold: float = EN_ROUTE_REMAINING_THRESHOLD,
) -> Tuple[float, bool]:
    """Returns (ratio_delivered, is_over_threshold). bag_total<=0 → (0.0, False)."""
    if bag_total <= 0:
        return (0.0, False)
    ratio = delivered_count / bag_total
    return (round(ratio, 3), ratio >= threshold)


def emit_commitment_event(
    order_id: str,
    commitment_level: str,
    courier_id: Optional[str] = None,
    extra: Optional[dict] = None,
    log_path: Optional[str] = None,
) -> bool:
    """Append COMMITMENT_LEVEL_EMIT event to log. Gated by ENABLE_MID_TRIP_PICKUP.

    Returns:
        True if event written, False otherwise (flag off, or write error).
    """
    if not ENABLE_MID_TRIP_PICKUP:
        return False
    if commitment_level not in VALID_LEVELS:
        return False
    path = log_path or COMMITMENT_LOG_PATH
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": "COMMITMENT_LEVEL_EMIT",
        "order_id": str(order_id),
        "commitment_level": commitment_level,
        "courier_id": str(courier_id) if courier_id is not None else None,
        "extra": extra or {},
    }
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            f.flush()
        return True
    except Exception:
        return False
