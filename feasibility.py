"""Feasibility - SLA-first check z symulacja trasy.

Wejscie: kurier pozycja + bag (OrderSim) + nowy order (OrderSim).
Wyjscie: (MAYBE/NO, reason, metrics).

Zastapil stary sztywny 8km+120° z 11.04.
Nowa logika: fast filters + pelna simulacja trasy, SLA check per order.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Optional

from dispatch_v2.route_simulator import OrderSim, RoutePlan, simulate_bag_route
from dispatch_v2.common import MAX_BAG_TSP_BRUTEFORCE, MAX_BAG_SANITY_CAP
from dispatch_v2 import osrm_client

# Konfiguracja (do przeniesienia do config.json po stabilizacji)
SLA_MINUTES = 35
SHIFT_END_BUFFER_MIN = 20
MAX_PICKUP_REACH_KM = 15.0


def check_feasibility(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    shift_end: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> Tuple[str, str, Dict]:
    """Sprawdza czy nowy order moze byc dodany do bagu kuriera.

    Args:
        courier_pos: (lat, lon) aktualnej lokalizacji
        bag: ordery juz w bagu (OrderSim z picked_up_at)
        new_order: nowy order do rozwazenia
        shift_end: koniec zmiany kuriera (None = ignoruj)
        now: timestamp symulacji (None = utc now)

    Returns:
        (verdict, reason, metrics) gdzie verdict in {"MAYBE", "NO"}
    """
    if now is None:
        now = datetime.now(timezone.utc)

    metrics: Dict = {"bag_size_before": len(bag)}

    # === FAST FILTERS ===

    # Sanity cap - anomaly (blad stanu/koordynatora)
    if len(bag) >= MAX_BAG_SANITY_CAP:
        return ("NO", f"bag_sanity_cap ({len(bag)}/{MAX_BAG_SANITY_CAP})", metrics)
    # TSP perf guard - wave size decyduje feasibility+TSP+SLA, nie ta stala
    if len(bag) >= MAX_BAG_TSP_BRUTEFORCE:
        return ("NO", f"bag_tsp_perf_cap ({len(bag)}/{MAX_BAG_TSP_BRUTEFORCE})", metrics)

    pickup_dist_km = osrm_client.haversine(courier_pos, new_order.pickup_coords)
    metrics["pickup_dist_km"] = round(pickup_dist_km, 2)
    if pickup_dist_km > MAX_PICKUP_REACH_KM:
        return ("NO", f"pickup_too_far ({pickup_dist_km:.1f} km)", metrics)

    if shift_end is not None:
        remaining_min = (shift_end - now).total_seconds() / 60.0
        metrics["shift_remaining_min"] = round(remaining_min, 1)
        if remaining_min < SHIFT_END_BUFFER_MIN:
            return ("NO", f"shift_ending ({remaining_min:.1f} min left)", metrics)

    # === SLA SIMULATION ===

    plan = simulate_bag_route(courier_pos, bag, new_order, now=now)
    if plan is None:
        return ("NO", "osrm_unreachable", metrics)

    metrics["sequence"] = plan.sequence
    metrics["total_duration_min"] = plan.total_duration_min
    metrics["traffic_multiplier"] = plan.multiplier_used

    all_orders = bag + [new_order]
    violations = []
    for o in all_orders:
        predicted = plan.predicted_delivered_at.get(o.order_id)
        if predicted is None:
            continue
        picked_up_at = o.picked_up_at or now
        if picked_up_at.tzinfo is None:
            from zoneinfo import ZoneInfo
            picked_up_at = picked_up_at.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
        picked_up_utc = picked_up_at.astimezone(timezone.utc)

        if predicted.tzinfo is None:
            predicted = predicted.replace(tzinfo=timezone.utc)
        predicted_utc = predicted.astimezone(timezone.utc)

        elapsed_min = (predicted_utc - picked_up_utc).total_seconds() / 60.0
        if elapsed_min > SLA_MINUTES:
            violations.append({
                "order_id": o.order_id,
                "elapsed_min": round(elapsed_min, 1),
                "over_sla_by_min": round(elapsed_min - SLA_MINUTES, 1),
            })

    metrics["sla_violations"] = violations

    if violations:
        worst = max(violations, key=lambda v: v["over_sla_by_min"])
        return (
            "NO",
            f"sla_violation ({worst['order_id']} +{worst['elapsed_min']}min, over by {worst['over_sla_by_min']})",
            metrics,
        )

    return ("MAYBE", "ok_sla_fits", metrics)
