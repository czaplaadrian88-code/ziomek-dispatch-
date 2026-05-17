"""route_metrics — metryki jakości planu trasy (pure functions).

Sprint OBJ F0 (2026-05-17). Jedno źródło metryk jakości dla:
  - instrumentacji produkcyjnej (shadow_decisions: idle_/thermal_/r6_breach_/span_),
  - offline replay harness (kalibracja objective F1/F2).

Wszystkie metryki wyprowadzane z gotowych pól RoutePlanV2 — zero zmian w
route_simulator_v2._simulate_sequence. Pure, bez I/O, bez stanu — łatwe do testu.

Cel sprintu: objective OR-Tools nie wycenia idle ani thermalu (diagnozy 474253 /
474297). Te metryki kwantyfikują problem przed zmianą objective i służą do
kalibracji współczynników kar F1 (R6 soft bound) i F2 (span cost).
"""
from datetime import datetime, timezone
from typing import Any, Dict

# R-35MIN-MAX (REGULY_BIZNESOWE_2026-04-22) — hard rule: max 35 min dostawy.
R6_SLA_MIN = 35.0


def _to_utc(dt: Any):
    """ISO-naive/aware datetime → aware UTC; None gdy nie-datetime."""
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def compute_plan_metrics(plan: Any, dwell_pickup_min: float) -> Dict[str, float]:
    """Metryki jakości planu trasy.

    Args:
        plan: RoutePlanV2 — wymaga pól total_duration_min, per_order_delivery_times,
              pickup_at, arrival_at.
        dwell_pickup_min: postój obsługowy pod restauracją (route-constant per tier).
              Odejmowany od (pickup_at - arrival_at) by wydzielić czysty idle.

    Returns dict:
        route_span_min      — makespan: now → ostatnia dostawa (= total_duration_min).
        idle_total_min      — suma czekania kuriera pod restauracjami (przyjazd
                              przed gotowością jedzenia). Objaw braku wyceny idle
                              w objective (diagnoza 474253).
        max_thermal_age_min — najdłuższy czas jedzenia od gotowości do dostawy.
        r6_breach_max_min   — największe przekroczenie R6 (35 min) wśród zleceń.
        r6_breach_count     — liczba zleceń z czasem > 35 min (diagnoza 474297).
    """
    span = float(getattr(plan, "total_duration_min", 0.0) or 0.0)

    # Thermal / R6 — z per_order_delivery_times (anchor=gotowość/picked_up → drop;
    # doktryna Adriana 2026-05-10, patrz _compute_per_order_delivery_minutes).
    pod = getattr(plan, "per_order_delivery_times", None) or {}
    times = [float(v) for v in pod.values() if v is not None]
    max_thermal = max(times) if times else 0.0
    breaches = [t - R6_SLA_MIN for t in times if t > R6_SLA_MIN]
    r6_breach_max = max(breaches) if breaches else 0.0
    r6_breach_count = len(breaches)

    # Idle — z arrival_at (surowy przyjazd) i pickup_at (po wait + dwell).
    #   wait = (pickup_at - arrival_at) - dwell_pickup, clamp ≥0.
    # Fix-7 super-pickup: wiele oidów dzieli jeden przyjazd do restauracji — dedup
    # po parze (arrival, pickup) by nie liczyć tego samego postoju wielokrotnie.
    # arrival_at zawiera tylko węzły pickup → picked_up bag (bez pickup-node) nie
    # wchodzi, co jest poprawne (brak postoju pod restauracją dla już odebranych).
    arrival_at = getattr(plan, "arrival_at", None) or {}
    pickup_at = getattr(plan, "pickup_at", None) or {}
    idle_total = 0.0
    seen = set()
    for oid, arr in arrival_at.items():
        arr_u = _to_utc(arr)
        pu_u = _to_utc(pickup_at.get(oid))
        if arr_u is None or pu_u is None:
            continue
        key = (arr_u, pu_u)
        if key in seen:
            continue
        seen.add(key)
        gap_min = (pu_u - arr_u).total_seconds() / 60.0
        wait = gap_min - float(dwell_pickup_min)
        if wait > 0:
            idle_total += wait

    return {
        "route_span_min": round(span, 2),
        "idle_total_min": round(idle_total, 2),
        "max_thermal_age_min": round(max_thermal, 2),
        "r6_breach_max_min": round(r6_breach_max, 2),
        "r6_breach_count": r6_breach_count,
    }
