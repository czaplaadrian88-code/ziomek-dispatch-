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
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional

from dispatch_v2 import osrm_client
from dispatch_v2 import common as C
from dispatch_v2.common import (
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    MAX_BAG_SANITY_CAP,
    WARSAW,
)
from dispatch_v2.route_simulator_v2 import (
    OrderSim,
    RoutePlanV2,
    simulate_bag_route_v2,
)

log = logging.getLogger(__name__)


# Hard cap per D3 MAX_BAG_SANITY_CAP (=8). F1.9b: R3 dynamic cap został
# zsoftowany po shadow-data 14.04 (za ostry, blokował Bartka na spread 7 km).
# Absolute hard block = sanity cap. R3 spread/dyn_cap nadal liczone jako
# telemetria w metrics, ale nie rejectują.
MAX_BAG_SIZE = MAX_BAG_SANITY_CAP
MAX_PICKUP_REACH_KM = 15.0
SHIFT_END_BUFFER_MIN = 20
DEFAULT_SLA_MINUTES = 35

# ===== BARTEK GOLD STANDARD thresholds (see docs/BARTEK_GOLD_STANDARD.md) =====
# R1: max delivery spread in bag (p90 of Bartek clean sample, n=47 bundles).
R1_MAX_DELIV_SPREAD_KM = 8.0
# R3: dynamic cap — computed for telemetry only (F1.9b: no longer a hard block).
# Kept in metrics so we can observe what R3 WOULD have rejected.
R3_DYNAMIC_MAX = [(5.0, 5), (8.0, 4), (float("inf"), 3)]
# R5: mixed-restaurant pickup spread — p100 Bartek = 1.79 km.
R5_MAX_MIXED_PICKUP_SPREAD_KM = 2.5  # F2.1c: poluzowane z 1.8 (p100 Bartek) → 2.5 (akceptowalny mixed pickup spread)


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

    # D3 sanity cap (MAX_BAG_SIZE = MAX_BAG_SANITY_CAP = 8). R3 absolute cap
    # usunięty w F1.9b po shadow data — blokował Bartka na legit bundlach.
    bag_after = len(bag) + 1
    if len(bag) >= MAX_BAG_SIZE:
        return ("NO", f"bag_full ({len(bag)}/{MAX_BAG_SIZE})", metrics, None)

    # R7 (F2.1b) — long-haul isolation w peak hours.
    # Długa trasa (>4.5 km) NIE MOŻE być bundlowana w peak (14-17 Warsaw).
    # Solo (bag pusty) zawsze OK — R7 dotyczy tylko bundli.
    # Telemetry liczone ZAWSZE (nawet solo), reject warunkowy (bag+longhaul+peak).
    if _valid(new_order.pickup_coords) and _valid(new_order.delivery_coords):
        r7_ride_km = _road_km(new_order.pickup_coords, new_order.delivery_coords)
        r7_warsaw_hour = now.astimezone(WARSAW).hour
        r7_in_peak = (
            C.LONG_HAUL_PEAK_HOURS_START
            <= r7_warsaw_hour
            <= C.LONG_HAUL_PEAK_HOURS_END
        )
        metrics["r7_ride_km"] = round(r7_ride_km, 2)
        metrics["r7_warsaw_hour"] = r7_warsaw_hour
        metrics["r7_in_peak"] = r7_in_peak
        metrics["r7_is_longhaul"] = r7_ride_km > C.LONG_HAUL_DISTANCE_KM
        metrics["r7_bag_size"] = len(bag)
        if bag and r7_ride_km > C.LONG_HAUL_DISTANCE_KM and r7_in_peak:
            return (
                "NO",
                f"R7_longhaul_peak ({r7_ride_km:.1f}km>{C.LONG_HAUL_DISTANCE_KM:.1f}, hour={r7_warsaw_hour})",
                metrics,
                None,
            )

    # R1 spread outlier (hard block). R3 dynamic cap zsoftowany — liczymy
    # metryki do telemetrii (learning_log) ale NIE rejectujemy.
    if bag and _valid(new_order.delivery_coords):
        spread_km = _max_deliv_spread_km(bag, new_order.delivery_coords)
        metrics["deliv_spread_km"] = round(spread_km, 2)
        metrics["dynamic_bag_cap"] = _dynamic_bag_cap(spread_km)
        metrics["r3_soft_would_block"] = bag_after > metrics["dynamic_bag_cap"]
        if spread_km > R1_MAX_DELIV_SPREAD_KM:
            metrics["r1_violation_km"] = round(spread_km - R1_MAX_DELIV_SPREAD_KM, 2)
        else:
            metrics["r1_violation_km"] = 0.0

    # R5 mixed-restaurant pickup spread — same restaurant → spread=0 (no fire).
    if bag and _valid(new_order.pickup_coords):
        pickup_spread_km = _max_pickup_spread_from_bag(bag, new_order.pickup_coords)
        metrics["pickup_spread_km"] = round(pickup_spread_km, 2)
        if pickup_spread_km > R5_MAX_MIXED_PICKUP_SPREAD_KM:
            metrics["r5_violation_km"] = round(pickup_spread_km - R5_MAX_MIXED_PICKUP_SPREAD_KM, 2)
        else:
            metrics["r5_violation_km"] = 0.0

    # R8 (F2.1c) — pickup_span hard cap (T_KUR spread w bagu).
    if bag:
        bag_size_after = len(bag) + 1
        pra_list = [b.pickup_ready_at for b in bag if b.pickup_ready_at is not None and b.status != "picked_up"]  # F2.1c hotfix: picked_up już odebrany, historyczny T_KUR nie liczy się do span
        if new_order.pickup_ready_at is not None:
            pra_list.append(new_order.pickup_ready_at)
        if len(pra_list) >= 2:
            span_min = (max(pra_list) - min(pra_list)).total_seconds() / 60.0
            metrics["r8_pickup_span_min"] = round(span_min, 1)
            hard_cap = (
                C.PICKUP_SPAN_HARD_BUNDLE3_MIN if bag_size_after >= 3
                else C.PICKUP_SPAN_HARD_BUNDLE2_MIN
            )
            if span_min > hard_cap:
                metrics["r8_violation_min"] = round(span_min - hard_cap, 2)
            else:
                metrics["r8_violation_min"] = 0.0
        else:
            metrics["r8_pickup_span_min"] = None  # graceful degradation

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

    # R6 (F2.1b) — BAG_TIME termiczny hard cap (C.BAG_TIME_HARD_MAX_MIN = 35 min).
    # SLA check wyżej używa sla_minutes (35 solo / 45 bundla — Bartek Gold).
    # R6 jest STRICTER dla bundli: chroni termicznie bez względu na SLA budżet.
    # Działa też solo (Opcja A): jedzenie stygnie identycznie niezależnie od bag size.
    # Reużywa plan.predicted_delivered_at + plan.pickup_at z istniejącego simulate.
    r6_max_bag_time = 0.0
    r6_worst_oid: Optional[str] = None
    for o in list(bag) + [new_order]:
        pred = plan.predicted_delivered_at.get(o.order_id)
        if pred is None:
            log.warning(f"R6 skip: brak predicted_delivered_at dla {o.order_id}")
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
        bag_time_min = (pred - pu).total_seconds() / 60.0
        if bag_time_min > r6_max_bag_time:
            r6_max_bag_time = bag_time_min
            r6_worst_oid = o.order_id
    metrics["r6_max_bag_time_min"] = round(r6_max_bag_time, 1)
    metrics["r6_worst_oid"] = r6_worst_oid
    metrics["r6_is_solo"] = len(bag) == 0
    metrics["r6_bag_size"] = len(bag)
    if r6_max_bag_time > C.BAG_TIME_HARD_MAX_MIN:
        return (
            "NO",
            f"R6_bag_time_exceeded ({r6_worst_oid} {r6_max_bag_time:.1f}min>{C.BAG_TIME_HARD_MAX_MIN})",
            metrics,
            plan,
        )

    return ("MAYBE", "ok_sla_fits", metrics, plan)
