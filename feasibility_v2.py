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
from dispatch_v2.route_simulator_v2 import (
    OrderSim,
    RoutePlanV2,
    simulate_bag_route_v2,
)


MAX_BAG_SIZE = 6
MAX_PICKUP_REACH_KM = 15.0
SHIFT_END_BUFFER_MIN = 20
DEFAULT_SLA_MINUTES = 35


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

    if len(bag) >= MAX_BAG_SIZE:
        return ("NO", f"bag_full ({len(bag)}/{MAX_BAG_SIZE})", metrics, None)

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
