"""
Scoring dispatch v2 - Stage 3 pipeline.
4 komponenty 0-100, wagi 0.30/0.25/0.25/0.20.
Wzor: S = S_dyst*0.30 + S_obc*0.25 + S_kier*0.25 + S_czas*0.20

Wejscie: kandydat po feasibility MAYBE
Wyjscie: {total, components, reasoning, metrics}
"""
import math
from typing import List, Optional, Tuple
from dispatch_v2.geometry import haversine_km, bearing_deg, angle_between, bag_centroid
from dispatch_v2.common import DEPRECATE_LEGACY_HARD_GATES, MAX_BAG_TSP_BRUTEFORCE

W_DYSTANS  = 0.30
W_OBCIAZENIE = 0.25
W_KIERUNEK = 0.25
W_CZAS     = 0.20

DIST_DECAY_KM = 3.0           # exp(-d/3): 0km=100, 3km=37, 6km=13
TIME_PENALTY_START_MIN = 30   # do 30 min w bagu = 0 kary
TIME_PENALTY_FULL_MIN = 35    # 35 min w bagu = 100 kary

def s_dystans(dist_km: float) -> float:
    return max(0.0, min(100.0, 100.0 * math.exp(-dist_km / DIST_DECAY_KM)))

def s_obciazenie(bag_size: int) -> float:
    """Miekki sygnal obciazenia. Wave size faktycznie limitowane przez feasibility+TSP+SLA.
    Dla bag >= MAX_BAG_TSP_BRUTEFORCE zwraca 0 (TSP perf guard).
    Inaczej linear decay: 0=100, 1=80, 2=60, 3=40, 4=20."""
    if bag_size >= MAX_BAG_TSP_BRUTEFORCE:
        return 0.0
    return 100.0 * (1.0 - bag_size / MAX_BAG_TSP_BRUTEFORCE)

def s_kierunek(angle_deg: Optional[float]) -> float:
    if angle_deg is None:
        return 100.0  # pusty bag - kazdy kierunek neutralny
    return max(0.0, 100.0 * (1.0 - angle_deg / 180.0))

def time_penalty(oldest_in_bag_min: Optional[float]) -> float:
    if oldest_in_bag_min is None or oldest_in_bag_min <= TIME_PENALTY_START_MIN:
        return 0.0
    t = (oldest_in_bag_min - TIME_PENALTY_START_MIN) / (TIME_PENALTY_FULL_MIN - TIME_PENALTY_START_MIN)
    t = max(0.0, min(1.0, t))
    return (t ** 2.5) * 100.0

def s_czas(oldest_in_bag_min: Optional[float]) -> float:
    return max(0.0, 100.0 - time_penalty(oldest_in_bag_min))

def score_candidate(
    courier_pos: Tuple[float, float],
    restaurant_pos: Tuple[float, float],
    bag_drop_coords: Optional[List[Tuple[float, float]]] = None,
    bag_size: int = 0,
    oldest_in_bag_min: Optional[float] = None,
    road_km: Optional[float] = None,
    r6_soft_penalty: float = 0.0,
) -> dict:
    # Dystans - preferujemy road_km (z OSRM) jesli dostepne, inaczej haversine * 1.3
    if road_km is None:
        road_km = haversine_km(courier_pos, restaurant_pos) * 1.3

    # Kat dla direction score
    angle = None
    if bag_drop_coords:
        centroid = bag_centroid(bag_drop_coords)
        if centroid:
            brg_bag = bearing_deg(courier_pos, centroid)
            brg_new = bearing_deg(courier_pos, restaurant_pos)
            angle = angle_between(brg_bag, brg_new)

    sd = s_dystans(road_km)
    so = s_obciazenie(bag_size)
    sk = s_kierunek(angle)
    sc = s_czas(oldest_in_bag_min)

    total = sd * W_DYSTANS + so * W_OBCIAZENIE + sk * W_KIERUNEK + sc * W_CZAS

    # F2.2 C3 narrow (2026-04-18): R6 soft zone 30-35 min penalty, gated by flag.
    # Flag False → param ignored, zero behavior change (default).
    # Flag True → penalty (negative value from feasibility metrics) subtracts from total.
    r6_penalty_applied = 0.0
    if DEPRECATE_LEGACY_HARD_GATES and r6_soft_penalty != 0.0:
        total += r6_soft_penalty
        r6_penalty_applied = r6_soft_penalty

    reasoning = (
        f"dist={road_km:.1f}km→{sd:.0f} | bag={bag_size}/{MAX_BAG_TSP_BRUTEFORCE}→{so:.0f} | "
        f"ang={angle if angle is None else round(angle,0)}→{sk:.0f} | "
        f"czas={oldest_in_bag_min if oldest_in_bag_min is None else round(oldest_in_bag_min,0)}min→{sc:.0f} "
        f"= TOTAL {total:.1f}"
    )

    return {
        "total": round(total, 2),
        "components": {
            "dystans": round(sd, 2),
            "obciazenie": round(so, 2),
            "kierunek": round(sk, 2),
            "czas": round(sc, 2),
        },
        "weights": {"dystans": W_DYSTANS, "obciazenie": W_OBCIAZENIE, "kierunek": W_KIERUNEK, "czas": W_CZAS},
        "metrics": {
            "road_km": round(road_km, 2),
            "bag_size": bag_size,
            "angle_deg": round(angle, 1) if angle is not None else None,
            "oldest_in_bag_min": round(oldest_in_bag_min, 1) if oldest_in_bag_min is not None else None,
            "r6_soft_penalty_applied": round(r6_penalty_applied, 2),
        },
        "reasoning": reasoning,
    }
