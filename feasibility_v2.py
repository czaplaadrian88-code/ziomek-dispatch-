"""feasibility_v2 - SLA-first check on top of route_simulator_v2.

Pipeline:
    fast filters (bag size, pickup reach, shift end)
        → simulate_bag_route_v2
        → SLA check via plan.sla_violations

Returns:
    (verdict, reason, metrics, plan)
    verdict ∈ {"MAYBE", "NO"}
    plan = RoutePlanV2 or None (None only when rejected by a fast filter)
"""
from dataclasses import replace
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional

from dispatch_v2 import osrm_client
from dispatch_v2.common import HAVERSINE_ROAD_FACTOR_BIALYSTOK
from dispatch_v2.route_simulator_v2 import (
    OrderSim,
    RoutePlanV2,
    simulate_bag_route_v2,
)


# Legacy static cap — zastąpiony przez R3_ABSOLUTE_CAP, zostawione dla kompatybilności.
MAX_BAG_SIZE = 6
MAX_PICKUP_REACH_KM = 15.0
SHIFT_END_BUFFER_MIN = 20
DEFAULT_SLA_MINUTES = 35

# ===== BARTEK GOLD STANDARD thresholds (see docs/BARTEK_GOLD_STANDARD.md) =====
# R1: max delivery spread in bag (p90 of Bartek clean sample, n=47 bundles).
R1_MAX_DELIV_SPREAD_KM = 8.0
# R3: absolute bag cap — Bartek never runs bag > 5 in 231-order clean sample.
R3_ABSOLUTE_CAP = 5
# R3: dynamic cap by spread. Stricter = fewer stops allowed at higher spread.
R3_DYNAMIC_MAX = [(5.0, 5), (8.0, 4), (float("inf"), 3)]
# R5: mixed-restaurant pickup spread — p100 Bartek = 1.79 km.
R5_MAX_MIXED_PICKUP_SPREAD_KM = 1.8


def _road_km(a, b) -> float:
    """Haversine * Białystok road factor."""
    return osrm_client.haversine(a, b) * HAVERSINE_ROAD_FACTOR_BIALYSTOK


def _valid(coord) -> bool:
    return bool(coord) and coord != (0.0, 0.0) and coord[0] != 0.0


def _max_deliv_spread_km(bag, new_delivery) -> float:
    """Max pair-wise road km across all bag deliveries + new delivery."""
    coords = [b.delivery_coords for b in bag if _valid(b.delivery_coords)]
    if _valid(new_delivery):
        coords.append(new_delivery)
    if len(coords) < 2:
        return 0.0
    best = 0.0
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            d = _road_km(coords[i], coords[j])
            if d > best:
                best = d
    return best


def _dynamic_bag_cap(spread_km: float) -> int:
    for threshold, cap in R3_DYNAMIC_MAX:
        if spread_km <= threshold:
            return cap
    return R3_ABSOLUTE_CAP


def _max_pickup_spread_from_bag(bag, new_pickup) -> float:
    """Max road km between new pickup and any bag pickup (skipping sentinels)."""
    if not _valid(new_pickup):
        return 0.0
    best = 0.0
    for b in bag:
        bp = b.pickup_coords
        if not _valid(bp):
            continue
        d = _road_km(bp, new_pickup)
        if d > best:
            best = d
    return best


def check_feasibility_v2(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    shift_end: Optional[datetime] = None,
    now: Optional[datetime] = None,
    pickup_ready_at: Optional[datetime] = None,
    sla_minutes: int = DEFAULT_SLA_MINUTES,
) -> Tuple[str, str, Dict, Optional[RoutePlanV2]]:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    metrics: Dict = {"bag_size_before": len(bag)}

    # === FAST FILTERS ===

    # R3 absolute cap — Bartek Gold Standard: bag_after ≤ 5 always.
    bag_after = len(bag) + 1
    if bag_after > R3_ABSOLUTE_CAP:
        return ("NO", f"R3_bag_cap ({bag_after}>{R3_ABSOLUTE_CAP})", metrics, None)

    # R1 spread outlier + R3 dynamic cap (require delivery coords on both sides).
    if bag and _valid(new_order.delivery_coords):
        spread_km = _max_deliv_spread_km(bag, new_order.delivery_coords)
        metrics["deliv_spread_km"] = round(spread_km, 2)
        if spread_km > R1_MAX_DELIV_SPREAD_KM:
            return (
                "NO",
                f"R1_spread_outlier ({spread_km:.1f}km>{R1_MAX_DELIV_SPREAD_KM:.1f})",
                metrics,
                None,
            )
        dyn_cap = _dynamic_bag_cap(spread_km)
        metrics["dynamic_bag_cap"] = dyn_cap
        if bag_after > dyn_cap:
            return (
                "NO",
                f"R3_dynamic_bag (spread={spread_km:.1f}km, cap={dyn_cap}, after={bag_after})",
                metrics,
                None,
            )

    # R5 mixed-restaurant pickup spread — same restaurant → spread=0 (no fire).
    if bag and _valid(new_order.pickup_coords):
        pickup_spread_km = _max_pickup_spread_from_bag(bag, new_order.pickup_coords)
        metrics["pickup_spread_km"] = round(pickup_spread_km, 2)
        if pickup_spread_km > R5_MAX_MIXED_PICKUP_SPREAD_KM:
            return (
                "NO",
                f"R5_mixed_rest_pickup ({pickup_spread_km:.1f}km>{R5_MAX_MIXED_PICKUP_SPREAD_KM:.1f})",
                metrics,
                None,
            )

    pickup_dist_km = osrm_client.haversine(courier_pos, new_order.pickup_coords)
    metrics["pickup_dist_km"] = round(pickup_dist_km, 2)
    if pickup_dist_km > MAX_PICKUP_REACH_KM:
        return ("NO", f"pickup_too_far ({pickup_dist_km:.1f} km)", metrics, None)

    if shift_end is not None:
        if shift_end.tzinfo is None:
            shift_end = shift_end.replace(tzinfo=timezone.utc)
        remaining_min = (shift_end - now).total_seconds() / 60.0
        metrics["shift_remaining_min"] = round(remaining_min, 1)
        if remaining_min < SHIFT_END_BUFFER_MIN:
            return ("NO", f"shift_ending ({remaining_min:.1f} min left)", metrics, None)

    # === SLA SIMULATION ===

    if pickup_ready_at is not None and new_order.pickup_ready_at is None:
        new_order = replace(new_order, pickup_ready_at=pickup_ready_at)

    plan = simulate_bag_route_v2(
        courier_pos, bag, new_order, now=now, sla_minutes=sla_minutes,
    )

    metrics["sequence"] = plan.sequence
    metrics["total_duration_min"] = plan.total_duration_min
    metrics["strategy"] = plan.strategy
    metrics["osrm_fallback_used"] = plan.osrm_fallback_used
    metrics["sla_violations_count"] = plan.sla_violations

    if plan.sla_violations > 0:
        violations_detail = []
        for o in list(bag) + [new_order]:
            pred = plan.predicted_delivered_at.get(o.order_id)
            if pred is None:
                continue
            if o.order_id in plan.pickup_at:
                pu = plan.pickup_at[o.order_id]
            elif o.picked_up_at is not None:
                pu = o.picked_up_at
                if pu.tzinfo is None:
                    pu = pu.replace(tzinfo=timezone.utc)
                pu = pu.astimezone(timezone.utc)
            else:
                pu = now
            elapsed_min = (pred - pu).total_seconds() / 60.0
            if elapsed_min > sla_minutes:
                violations_detail.append({
                    "order_id": o.order_id,
                    "elapsed_min": round(elapsed_min, 1),
                    "over_sla_by_min": round(elapsed_min - sla_minutes, 1),
                })
        metrics["sla_violations"] = violations_detail
        worst = max(violations_detail, key=lambda v: v["over_sla_by_min"])
        return (
            "NO",
            f"sla_violation ({worst['order_id']} +{worst['elapsed_min']}min, over by {worst['over_sla_by_min']})",
            metrics,
            plan,
        )

    return ("MAYBE", "ok_sla_fits", metrics, plan)
