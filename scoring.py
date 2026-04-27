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
from dispatch_v2.common import (
    DEPRECATE_LEGACY_HARD_GATES, ENABLE_WAVE_SCORING, MAX_BAG_TSP_BRUTEFORCE,
    ENABLE_FLEET_OVERLOAD_PENALTY, OVERLOAD_THRESHOLD_BAGS, OVERLOAD_PENALTY,
)

W_DYSTANS  = 0.30
W_OBCIAZENIE = 0.25
W_KIERUNEK = 0.25
W_CZAS     = 0.20

DIST_DECAY_KM = 5.0           # Bialystok (was 3, recalibrated 2026-04-25): exp(-d/5): 0km=100, 5km=37, 10km=14, 15km=5
# Future per-city scaling: DIST_DECAY_BY_CITY = {"bialystok": 5, "warsaw": 12, ...}
# Adrian's reasoning: exp utrzymuje gradient → algorytm zawsze rozróżnia kandydatów
# (10km vs 15km), liniowy by traktował oba jako "zero" dla typowych dystansów Białystoku.
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


def compute_wait_penalty(wait_min: float) -> float:
    """V3.27.1 Wait penalty (Adrian's quadratic table) — sprint sesja 1, 2026-04-26.

    Linear interpolacja między punktami tabeli `V327_WAIT_PENALTY_TABLE`. Sweet
    spot wait_min ≤ 20 → penalty=0. Powyżej tabeli (>60min) → hard fallback
    -1000. Flag-gated: gdy `ENABLE_V327_WAIT_PENALTY=False`, helper zwraca 0
    (zachowuje pre-V3.27.1 baseline).

    Args:
        wait_min: czas oczekiwania kuriera pod restauracją w minutach
            (max(0, effective_pickup_time - pickup_ready_at)). Mniejsze niż 0
            traktowane jako 0 (no-op edge).

    Returns:
        Float penalty ≤ 0. Sumarycznie dodawane per pickup w plan.sequence
        do score kandydata przez dispatch_pipeline (post-TSP, post-scoring layer).
    """
    from dispatch_v2 import common as _common
    if not _common.ENABLE_V327_WAIT_PENALTY:
        return 0.0
    if wait_min is None or wait_min <= 0:
        return 0.0

    table = _common.V327_WAIT_PENALTY_TABLE
    # Below first table entry → sweet spot, zero penalty
    if wait_min <= table[0][0]:
        return 0.0
    # Powyżej OSTATNIEGO entry (>60min) → hard fallback safety net.
    # wait_min == 60 trafia do interpolation loop i otrzymuje exact table value -700.
    if wait_min > table[-1][0]:
        return float(_common.V327_WAIT_PENALTY_HARD_FALLBACK)
    # Linear interpolacja między najbliższymi punktami
    for i in range(len(table) - 1):
        x1, y1 = table[i]
        x2, y2 = table[i + 1]
        if x1 <= wait_min <= x2:
            ratio = (wait_min - x1) / (x2 - x1)
            return y1 + ratio * (y2 - y1)
    return 0.0  # defensive fallthrough


def compute_wait_courier_penalty(
    wait_min: float,
    bag_size_at_insertion: int,
) -> Tuple[float, bool]:
    """V3.27.3 Wait kuriera penalty (2026-04-27).

    Penalty za czekanie kuriera pod restauracją po chain-aware arrival.
    `wait_min = max(0, pickup_ready_at - plan.arrival_at[oid])` (kurier idle
    przed restauracja, NIE plan.pickup_at - ready_at).

    Conditional firing: bag_size_at_insertion >= 1 (kurier ma już dowóz w aucie,
    jedzenie stygnie podczas idle). bag=0 skip — kurier wolny i tak czeka na
    zlecenie, lepiej mu cokolwiek dać.

    Linear gradient table:
      ≤5 min sweet spot   → 0
      6 min               → -10  (first step)
      7-20 min            → -10 + (wait_min - 6) * -5  (-5/min above 6)
      >20 min             → HARD REJECT (return penalty=0, reject=True)

    Args:
        wait_min: czas idle kuriera pod restauracją (min)
        bag_size_at_insertion: liczba orderów w bagu PRZED nowym insert
            (= len(bag) before adding new_order)

    Returns:
        Tuple (penalty, hard_reject):
        - penalty (float): score adjustment, ≤ 0
        - hard_reject (bool): True gdy candidate powinien być infeasible

    Flag-gated: gdy ENABLE_V3273_WAIT_COURIER_PENALTY=False zwraca (0.0, False).
    """
    from dispatch_v2 import common as _common
    if not _common.ENABLE_V3273_WAIT_COURIER_PENALTY:
        return (0.0, False)
    if bag_size_at_insertion < 1:
        return (0.0, False)
    if wait_min is None or wait_min <= _common.V3273_WAIT_COURIER_THRESHOLD_MIN:
        return (0.0, False)
    if wait_min > _common.V3273_WAIT_COURIER_HARD_REJECT_MIN:
        return (0.0, True)
    # Wait min in (5, 20]: linear gradient
    # Formula: penalty = first_step + (wait_min - 6) * per_min_step
    # wait=6: -10; wait=7: -15; wait=8: -20; ... wait=20: -80
    extra_min_above_6 = max(0.0, wait_min - 6.0)
    penalty = _common.V3273_WAIT_COURIER_FIRST_STEP_PENALTY + extra_min_above_6 * _common.V3273_WAIT_COURIER_PER_MIN_PENALTY
    # For wait_min in (5, 6) interpolate from 0 to -10 linearly
    if wait_min < 6.0:
        ratio = (wait_min - _common.V3273_WAIT_COURIER_THRESHOLD_MIN) / (6.0 - _common.V3273_WAIT_COURIER_THRESHOLD_MIN)
        penalty = ratio * _common.V3273_WAIT_COURIER_FIRST_STEP_PENALTY
    return (penalty, False)


def score_candidate(
    courier_pos: Tuple[float, float],
    restaurant_pos: Tuple[float, float],
    bag_drop_coords: Optional[List[Tuple[float, float]]] = None,
    bag_size: int = 0,
    oldest_in_bag_min: Optional[float] = None,
    road_km: Optional[float] = None,
    r6_soft_penalty: float = 0.0,
    wave_adjustment: float = 0.0,
    fleet_context=None,  # V3.18: FleetContext | None (duck-typed, unused when flag off)
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

    # F2.2 C5 full (2026-04-18): wave_adjustment from wave_scoring module, gated by flag.
    # Flag False → kwarg ignored, zero behavior change.
    # Flag True → adjustment (can be negative or positive) applied to total.
    wave_adjustment_applied = 0.0
    if ENABLE_WAVE_SCORING and wave_adjustment != 0.0:
        total += wave_adjustment
        wave_adjustment_applied = wave_adjustment

    # V3.18 Bug 2: fleet overload penalty. Gdy courier bag_size > fleet_avg + threshold,
    # scoring dostaje penalty (np. -20). Zapobiega sytuacji gdy courier z 5/4 bagiem
    # (over-limit) dostaje top-1 score mimo że inni kurierzy z bag=2/3 są dostępni.
    # Flag False OR fleet_context None → zero wpływu.
    overload_penalty_applied = 0.0
    if ENABLE_FLEET_OVERLOAD_PENALTY and fleet_context is not None:
        try:
            delta = fleet_context.overload_delta(bag_size)
            if delta > OVERLOAD_THRESHOLD_BAGS:
                total += OVERLOAD_PENALTY
                overload_penalty_applied = OVERLOAD_PENALTY
        except Exception:
            pass  # unknown fleet_context shape → skip defensive

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
            "wave_adjustment_applied": round(wave_adjustment_applied, 2),
            "overload_penalty_applied": round(overload_penalty_applied, 2),
        },
        "reasoning": reasoning,
    }
