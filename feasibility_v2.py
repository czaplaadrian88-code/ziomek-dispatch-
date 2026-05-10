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
import json
import logging
from dataclasses import replace
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional

from dispatch_v2 import osrm_client
from dispatch_v2 import common as C
from dispatch_v2.common import (
    ENABLE_C2_SHADOW_LOG,
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    MAX_BAG_SANITY_CAP,
    USE_PER_ORDER_GATE,
    WARSAW,
)
from dispatch_v2.route_simulator_v2 import (
    OrderSim,
    RoutePlanV2,
    simulate_bag_route_v2,
)

log = logging.getLogger(__name__)

C2_PER_ORDER_THRESHOLD_MIN = 35.0
C2_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/c2_shadow_log.jsonl"


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
    return R3_DYNAMIC_MAX[-1][1]


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


def _detect_waves(
    bag, new_order, time_window_min: float = 12.0, space_threshold_km: float = 1.5
) -> List[List[str]]:
    """V3.28 P2 — wave detection (Adrian doktryna 2026-05-10).

    Wave = grupa orderów z `pickup_ready_at` w jednym oknie czasowym (±time_window_min)
    i pickup_coords w jednym korytarzu (≤space_threshold_km od poprzedniej w grupie).

    Filozofia: kurier robi atomic burst pickup → atomic burst drop, potem opcjonalnie
    kolejna fala. Bag z 1 falą = idealny ("linia/okrąg"). Bag z 2+ fal = OK jeśli
    inter-wave deadhead jest sensowny.

    Pomija picked_up bag orders (już mają punkt odbioru za sobą).

    Returns: List[List[order_id]] — każda lista to fala. Empty bag → [].
    """
    candidates = []
    for o in list(bag) + [new_order]:
        if getattr(o, "status", "assigned") == "picked_up":
            continue
        if getattr(o, "pickup_ready_at", None) is None:
            continue
        if not _valid(o.pickup_coords):
            continue
        candidates.append(o)
    if not candidates:
        return []
    candidates.sort(key=lambda o: o.pickup_ready_at)
    waves: List[List[str]] = [[candidates[0].order_id]]
    last = candidates[0]
    for o in candidates[1:]:
        dt = abs((o.pickup_ready_at - last.pickup_ready_at).total_seconds()) / 60.0
        dx = _road_km(o.pickup_coords, last.pickup_coords)
        if dt <= time_window_min and dx <= space_threshold_km:
            waves[-1].append(o.order_id)
        else:
            waves.append([o.order_id])
        last = o
    return waves


def _inter_wave_deadhead_km(waves: List[List[str]], all_orders) -> Tuple[float, float, int]:
    """Sum/max deadhead km between waves (drop_last_Wn → pickup_first_Wn+1).

    Approximation: gdy nie znamy faktycznego sequence dropów per fali, używamy
    pickup_coords pierwszego ordera w każdej fali jako proxy dla "punktu zwrotu".

    Returns: (total_deadhead_km, max_inter_wave_km, n_inter_wave_segments)
    """
    if len(waves) < 2:
        return (0.0, 0.0, 0)
    by_oid = {o.order_id: o for o in all_orders}
    total = 0.0
    mx = 0.0
    segs = 0
    for i in range(len(waves) - 1):
        w_now = waves[i]
        w_next = waves[i + 1]
        # Last drop tej fali ≈ pickup ostatniego ordera (proxy — nie znamy faktycznego drop sequence tu)
        end_oid = w_now[-1]
        start_oid = w_next[0]
        end_o = by_oid.get(end_oid)
        start_o = by_oid.get(start_oid)
        if end_o is None or start_o is None:
            continue
        # Lepszy proxy: deliv_coords ostatniego ordera w fali (gdzie kurier "wraca")
        # vs pickup_coords pierwszego ordera następnej fali
        end_pos = end_o.delivery_coords if _valid(end_o.delivery_coords) else end_o.pickup_coords
        start_pos = start_o.pickup_coords
        if not _valid(end_pos) or not _valid(start_pos):
            continue
        d = _road_km(end_pos, start_pos)
        total += d
        if d > mx:
            mx = d
        segs += 1
    return (round(total, 2), round(mx, 2), segs)


def check_per_order_35min_rule(
    plan: RoutePlanV2,
    threshold_min: float = C2_PER_ORDER_THRESHOLD_MIN,
) -> Tuple[bool, Dict]:
    """F2.2 C2: Per-order delivery time hard gate.

    Uses plan.per_order_delivery_times (populated by C1). Fail-closed on None.

    Returns:
        (passes, details) where passes=True if all orders <= threshold.
        details = {'violations': [(oid, elapsed), ...], 'max_elapsed', 'total_orders',
                   'per_order_data_available': bool}
    """
    details = {
        "violations": [],
        "max_elapsed": 0.0,
        "total_orders": 0,
        "per_order_data_available": False,
    }
    if plan.per_order_delivery_times is None:
        return (False, details)
    details["per_order_data_available"] = True
    details["total_orders"] = len(plan.per_order_delivery_times)
    for oid, elapsed in plan.per_order_delivery_times.items():
        if elapsed > details["max_elapsed"]:
            details["max_elapsed"] = round(float(elapsed), 2)
        if elapsed > threshold_min:
            details["violations"].append((oid, round(float(elapsed), 2)))
    passes = len(details["violations"]) == 0
    return (passes, details)


def _emit_c2_shadow_diff_event(
    current_verdict: str,
    c2_passes: bool,
    c2_details: Dict,
    plan: RoutePlanV2,
    metrics: Dict,
    new_order_id: str,
    bag_size_before: int,
) -> None:
    """Append C2_SHADOW_DIFF event to dispatch_state/c2_shadow_log.jsonl.

    Only called when current verdict (with existing gates) differs from C2+existing combo.
    Zero impact on dispatch flow — log-only.
    """
    new_verdict = current_verdict if c2_passes else "NO"
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": "C2_SHADOW_DIFF",
        "current_verdict": current_verdict,
        "new_verdict_if_c2_enabled": new_verdict,
        "c2_would_reject": not c2_passes,
        "per_order_data_available": c2_details["per_order_data_available"],
        "max_elapsed_min": c2_details["max_elapsed"],
        "total_orders": c2_details["total_orders"],
        "violations": c2_details["violations"],
        "per_order_delivery_times": dict(plan.per_order_delivery_times) if plan.per_order_delivery_times else None,
        "sequence": plan.sequence,
        "total_duration_min": plan.total_duration_min,
        "strategy": plan.strategy,
        "new_order_id": new_order_id,
        "bag_size_before": bag_size_before,
        "r6_max_bag_time_min": metrics.get("r6_max_bag_time_min"),
    }
    try:
        with open(C2_SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception as e:
        log.warning(f"C2 shadow log write failed: {e}")


def check_feasibility_v2(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    shift_end: Optional[datetime] = None,
    shift_start: Optional[datetime] = None,  # V3.25 STEP B (R-01 PRE-CHECK)
    now: Optional[datetime] = None,
    pickup_ready_at: Optional[datetime] = None,
    sla_minutes: int = DEFAULT_SLA_MINUTES,
    base_sequence: Optional[List[str]] = None,  # V3.19d passthrough
    r07_chain_eta_utc: Optional[datetime] = None,  # V3.26 STEP 6 (R-07 v2) — chain_eta source of truth dla R-01 MANDATORY
    pos_source: Optional[str] = None,  # V3.28 ETAP 2 — pre_shift departure clamp gate
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
    # TODO C3 deferred (2026-04-18): refactor to soft penalty if LONG_HAUL_DISTANCE_KM
    # threshold lowered from 99km. Currently dormant rule, no production impact.
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
        # V3.28 P1 — R1 directionality (corridor cosine) — Adrian doktryna 2026-05-10.
        # Spread w km nie wystarcza: 8 km drops w jednym kierunku (Nowe Miasto cluster) =
        # OK trasa; 4 km drops w przeciwnych dzielnicach (Skorupy + Henrykowo) = bad.
        # Mierzymy "kierunkowość" jako średnia cosine similarity wektorów courier→drop.
        # avg_cos ≈ 1.0: wszystkie drops w tym samym kierunku (tight corridor)
        # avg_cos ≈ 0.0: drops w prostopadłych kierunkach
        # avg_cos ≈ -1.0: drops w przeciwnych kierunkach (opposite split)
        if _valid(courier_pos):
            drops_all: List[Tuple[float, float]] = []
            for b in bag:
                if _valid(b.delivery_coords):
                    drops_all.append(b.delivery_coords)
            drops_all.append(new_order.delivery_coords)
            if len(drops_all) >= 2:
                dirs: List[Tuple[float, float]] = []
                for d in drops_all:
                    vx = d[0] - courier_pos[0]
                    vy = d[1] - courier_pos[1]
                    n = (vx * vx + vy * vy) ** 0.5
                    if n > 1e-9:
                        dirs.append((vx / n, vy / n))
                if len(dirs) >= 2:
                    cos_sum = 0.0
                    pairs = 0
                    for i in range(len(dirs)):
                        for j in range(i + 1, len(dirs)):
                            cos_sum += dirs[i][0] * dirs[j][0] + dirs[i][1] * dirs[j][1]
                            pairs += 1
                    metrics["r1_avg_pairwise_cosine"] = round(
                        cos_sum / pairs if pairs else 0.0, 3
                    )

    # R5 mixed-restaurant pickup spread — same restaurant → spread=0 (no fire).
    if bag and _valid(new_order.pickup_coords):
        pickup_spread_km = _max_pickup_spread_from_bag(bag, new_order.pickup_coords)
        metrics["pickup_spread_km"] = round(pickup_spread_km, 2)
        if pickup_spread_km > R5_MAX_MIXED_PICKUP_SPREAD_KM:
            metrics["r5_violation_km"] = round(pickup_spread_km - R5_MAX_MIXED_PICKUP_SPREAD_KM, 2)
        else:
            metrics["r5_violation_km"] = 0.0
        # V3.28 P1 — R5 pickup detour per order — Adrian doktryna 2026-05-10.
        # Spread w km nie wystarcza: Wasilków pickup + Galeria Biała pickup → Iłłady drops
        # = pickup spread 5km > 2.5km próg, ale Galeria Biała JEST PO DRODZE z courier do
        # Wasilkowa, więc real detour ~0. Mierzymy "po drodze" jako:
        # detour_total = nearest-neighbor route(courier→all_pickups) - solo_route(courier→first_pickup).
        # Per-order detour = detour_total / n_pickups. <0.5 km = po drodze.
        if _valid(courier_pos):
            pickups_open: List[Tuple[float, float]] = []
            for b in bag:
                if _valid(b.pickup_coords) and getattr(b, "status", "assigned") != "picked_up":
                    pickups_open.append(b.pickup_coords)
            pickups_open.append(new_order.pickup_coords)
            if len(pickups_open) >= 2:
                # Solo baseline: courier → najbliższy pickup (greedy)
                solo_first = min(pickups_open, key=lambda p: _road_km(courier_pos, p))
                solo_km = _road_km(courier_pos, solo_first)
                # Multi route: nearest-neighbor sequencing all pickups
                remaining = list(pickups_open)
                cur = courier_pos
                multi_km = 0.0
                while remaining:
                    nxt = min(remaining, key=lambda p: _road_km(cur, p))
                    multi_km += _road_km(cur, nxt)
                    cur = nxt
                    remaining.remove(nxt)
                detour_total = max(0.0, multi_km - solo_km)
                metrics["r5_pickup_detour_total_km"] = round(detour_total, 2)
                metrics["r5_pickup_detour_per_order_km"] = round(
                    detour_total / len(pickups_open), 2
                )

    # V3.28 P2 — wave detection (Adrian doktryna 2026-05-10).
    # Cluster orderów po pickup_ready_at + pickup_coords. Bag z 1 falą = idealny
    # ("linia/okrąg"). Bag z 2+ fal = OK jeśli inter-wave deadhead sensowny.
    # NIE refactoryzujemy TSP tutaj — eksponujemy tylko metrykę dla scoring.
    waves = _detect_waves(bag, new_order)
    metrics["n_waves"] = len(waves)
    if len(waves) >= 2:
        deadhead_total, deadhead_max, n_segs = _inter_wave_deadhead_km(
            waves, list(bag) + [new_order]
        )
        metrics["inter_wave_deadhead_total_km"] = deadhead_total
        metrics["inter_wave_deadhead_max_km"] = deadhead_max
        metrics["inter_wave_n_segments"] = n_segs
    else:
        metrics["inter_wave_deadhead_total_km"] = 0.0
        metrics["inter_wave_deadhead_max_km"] = 0.0
        metrics["inter_wave_n_segments"] = 0

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

    # V3.25 STEP B (R-01 SCHEDULE-HARDENING) — unconditional PRE-CHECK przed
    # scoring path. Fail-CLOSED: brak shift_end → HARD REJECT (NO_ACTIVE_SHIFT)
    # zamiast silent bypass H1 (pre-V3.25). Pickup vs shift window:
    #   pickup > shift_end → HARD REJECT PICKUP_POST_SHIFT
    #   pickup < shift_start - 30 min → HARD REJECT PRE_SHIFT_TOO_EARLY
    #   pickup ∈ [shift_start - 30, shift_start) → soft penalty -20 (warm-up)
    # Dropoff hard-reject zachowane w V3.24-A line ~386 (post-simulate).
    if C.ENABLE_V325_SCHEDULE_HARDENING:
        # V3.26 STEP 6 (R-07 v2) MANDATORY integration gdy flag True (Adrian ACK #5):
        # chain_eta jest source of truth dla R-01 schedule check. Konsystencja
        # priorytet — bez chain_eta R-01 używa pickup_ready_at (kurier arrive time
        # INNY niż restaurant ready time).
        if C.ENABLE_V326_R07_CHAIN_ETA and r07_chain_eta_utc is not None:
            pickup_ref = r07_chain_eta_utc
            metrics["v325_pickup_ref_source"] = "r07_chain_eta"
        else:
            pickup_ref = pickup_ready_at if pickup_ready_at is not None else now
            metrics["v325_pickup_ref_source"] = "pickup_ready_at"
        if pickup_ref.tzinfo is None:
            pickup_ref = pickup_ref.replace(tzinfo=timezone.utc)
        # Gate 1: brak shift_end → courier nie ma active shift mapping
        if shift_end is None:
            metrics["v325_reject_reason"] = "NO_ACTIVE_SHIFT"
            return ("NO", "v325_NO_ACTIVE_SHIFT (cs.shift_end=None — brak schedule mapping)", metrics, None)
        # Normalize shift_end TZ
        _shift_end = shift_end.replace(tzinfo=timezone.utc) if shift_end.tzinfo is None else shift_end
        # Gate 2: pickup post-shift hard reject
        if pickup_ref > _shift_end:
            excess = (pickup_ref - _shift_end).total_seconds() / 60.0
            metrics["v325_pickup_post_shift_excess_min"] = round(excess, 2)
            metrics["v325_reject_reason"] = "PICKUP_POST_SHIFT"
            return (
                "NO",
                f"v325_PICKUP_POST_SHIFT (pickup {pickup_ref.strftime('%H:%M')} "
                f"vs shift_end {_shift_end.strftime('%H:%M')}, excess +{excess:.1f}min)",
                metrics, None,
            )
        # Gate 3: pre-shift hard reject + soft penalty zone
        if shift_start is not None:
            _shift_start = shift_start.replace(tzinfo=timezone.utc) if shift_start.tzinfo is None else shift_start
            too_early_min = (_shift_start - pickup_ref).total_seconds() / 60.0
            if too_early_min > C.V325_PRE_SHIFT_HARD_REJECT_MIN:
                metrics["v325_pre_shift_too_early_min"] = round(too_early_min, 2)
                metrics["v325_reject_reason"] = "PRE_SHIFT_TOO_EARLY"
                return (
                    "NO",
                    f"v325_PRE_SHIFT_TOO_EARLY (pickup {pickup_ref.strftime('%H:%M')} "
                    f"vs shift_start {_shift_start.strftime('%H:%M')}, before by {too_early_min:.1f}min)",
                    metrics, None,
                )
            if 0 < too_early_min <= C.V325_PRE_SHIFT_HARD_REJECT_MIN:
                # Pre-shift warm-up zone — soft penalty (kurier może zacząć ale otrzyma penalty w scoring).
                metrics["v325_pre_shift_soft_penalty_min"] = round(too_early_min, 2)
                metrics["v325_pre_shift_soft_penalty"] = C.V325_PRE_SHIFT_SOFT_PENALTY
            else:
                metrics["v325_pre_shift_soft_penalty"] = 0
        # Gate 4: dropoff hard reject post-simulate (V3.25 explicit, mirrors V3.24-A
        # but flag-gated osobno) — patrz blok niżej dot. v325_dropoff_after_shift_check.

    if shift_end is not None:
        if shift_end.tzinfo is None:
            shift_end = shift_end.replace(tzinfo=timezone.utc)
        remaining_min = (shift_end - now).total_seconds() / 60.0
        metrics["shift_remaining_min"] = round(remaining_min, 1)
        # V3.24-A: legacy SHIFT_END_BUFFER_MIN=20 check skipowany gdy flag ON
        # (zastąpiony dokładniejszym post-simulate planned_dropoff > shift_end+5 check,
        # patrz niżej tuż po R6). Flag OFF → legacy behavior.
        if not C.ENABLE_V324A_SCHEDULE_INTEGRATION:
            if remaining_min < SHIFT_END_BUFFER_MIN:
                return ("NO", f"shift_ending ({remaining_min:.1f} min left)", metrics, None)

    # === SLA SIMULATION ===

    if pickup_ready_at is not None and new_order.pickup_ready_at is None:
        new_order = replace(new_order, pickup_ready_at=pickup_ready_at)

    # V3.28 ETAP 2 (2026-05-08): pre_shift departure clamp. Pre_shift/no_gps
    # kurier z shift_start > now → simulate dostaje earliest_departure=shift_start.
    # Plan timestamps shift'owane od shift_start (eliminuje fikcyjny "kurier
    # startuje teraz" dla kuriera który jeszcze nie pracuje). Flag-gated.
    earliest_departure = None
    if (getattr(C, "ENABLE_PRE_SHIFT_DEPARTURE_CLAMP", False)
            and shift_start is not None
            and pos_source in ("pre_shift", "no_gps")
            and shift_start > now):
        earliest_departure = shift_start
        metrics["earliest_departure_utc"] = earliest_departure.isoformat()
        metrics["pre_shift_clamp_applied"] = True

    plan = simulate_bag_route_v2(
        courier_pos, bag, new_order, now=now, sla_minutes=sla_minutes,
        base_sequence=base_sequence, earliest_departure=earliest_departure,
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

    # R6 (F2.1b + V3.28 ANCHOR FIX 2026-05-10) — BAG_TIME termiczny PER-ORDER hard cap.
    #
    # Doktryna Adriana 2026-05-10: 35 min jest JEDYNĄ twardą regułą, per-zlecenie.
    # Anchor selection (Lekcja #84 thermal anchor):
    #   - new_order: anchor = pickup_ready_at (real ready time z restauracji)
    #   - bag order, NOT yet picked_up: anchor = pickup_ready_at (jedzenie czeka od ready)
    #   - bag order, ALREADY picked_up: anchor = picked_up_at (real pickup), SOFT only
    #     (kurier już wiezie — nie odrzucamy z bagu, fizycznie nie ma sensu cofać)
    #
    # Dlaczego nie plan.pickup_at (jak pre-V3.28): TSP może projektować pickup later
    # niż ready_at (np. +37 min gdy kurier zajęty), maskując 70+ min real thermal.
    # Diagnoza 2026-05-10 472189: r6_max_bag_time=34 min "pass" while real thermal 70 min.
    r6_max_bag_time = 0.0
    r6_worst_oid: Optional[str] = None
    r6_per_order_violations: List[Tuple[str, float]] = []
    r6_picked_up_violations: List[Tuple[str, float]] = []
    for o in list(bag) + [new_order]:
        pred = plan.predicted_delivered_at.get(o.order_id)
        if pred is None:
            # Lekcja #32 fail-loud: brak predicted = bug w simulator
            log.warning(
                f"R6 missing predicted_delivered_at oid={o.order_id} "
                f"bag_size={len(bag)} new_oid={new_order.order_id} — conservative skip"
            )
            continue
        if pred.tzinfo is None:
            pred = pred.replace(tzinfo=timezone.utc)
        is_new = o is new_order
        is_picked = (not is_new) and (
            getattr(o, "picked_up_at", None) is not None
            or getattr(o, "status", None) == "picked_up"
        )
        # Anchor selection per-status
        anchor: Optional[datetime] = None
        anchor_src: str = "now"
        if is_picked:
            pu = o.picked_up_at
            if pu is not None:
                if pu.tzinfo is None:
                    pu = pu.replace(tzinfo=timezone.utc)
                anchor = pu.astimezone(timezone.utc)
                anchor_src = "picked_up_at"
        else:
            pra = getattr(o, "pickup_ready_at", None)
            if pra is not None:
                if pra.tzinfo is None:
                    pra = pra.replace(tzinfo=timezone.utc)
                anchor = pra.astimezone(timezone.utc)
                anchor_src = "pickup_ready_at"
            elif o.order_id in plan.pickup_at:
                pu = plan.pickup_at[o.order_id]
                if pu.tzinfo is None:
                    pu = pu.replace(tzinfo=timezone.utc)
                anchor = pu.astimezone(timezone.utc)
                anchor_src = "tsp_pickup_at"
        if anchor is None:
            anchor = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        bag_time_min = (pred - anchor).total_seconds() / 60.0
        if bag_time_min > r6_max_bag_time:
            r6_max_bag_time = bag_time_min
            r6_worst_oid = o.order_id
        # Per-order violation tracking (split picked-up vs not)
        if bag_time_min > C.BAG_TIME_HARD_MAX_MIN:
            if is_picked:
                r6_picked_up_violations.append((o.order_id, round(bag_time_min, 1)))
            else:
                r6_per_order_violations.append((o.order_id, round(bag_time_min, 1)))
    metrics["r6_max_bag_time_min"] = round(r6_max_bag_time, 1)
    metrics["r6_worst_oid"] = r6_worst_oid
    metrics["r6_is_solo"] = len(bag) == 0
    metrics["r6_bag_size"] = len(bag)
    metrics["r6_per_order_violations"] = r6_per_order_violations
    metrics["r6_picked_up_violations"] = r6_picked_up_violations
    # F2.2 C3 narrow (2026-04-18): R6 soft warning zone (30, 35] — metric-only.
    if 30.0 < r6_max_bag_time <= C.BAG_TIME_HARD_MAX_MIN:
        metrics["r6_soft_penalty"] = round(-3.0 * (r6_max_bag_time - 30.0), 2)
        metrics["r6_soft_zone_active"] = True
    else:
        metrics["r6_soft_penalty"] = 0.0
        metrics["r6_soft_zone_active"] = False
    # V3.28 ANCHOR FIX: hard reject TYLKO za assigned-but-not-picked + new_order >35.
    # Picked_up orders są tracked ale NIE rejected (kurier kończy w drodze).
    if r6_per_order_violations:
        worst_oid, worst_bt = max(r6_per_order_violations, key=lambda v: v[1])
        return (
            "NO",
            f"R6_per_order_>35min ({worst_oid} {worst_bt:.1f}min, "
            f"thermal anchor=ready_at; n_violations={len(r6_per_order_violations)})",
            metrics,
            plan,
        )

    # V3.24-A: hard reject gdy planned dropoff nowego ordera > shift_end +
    # V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN (default 5 min). Precyzyjniejsze
    # niż legacy SHIFT_END_BUFFER_MIN=20 (który zbyt gruby — odrzucał kurierów
    # którzy zdążyliby solo order 3-min przed shift_end).
    if C.ENABLE_V324A_SCHEDULE_INTEGRATION and shift_end is not None:
        pred_new = plan.predicted_delivered_at.get(new_order.order_id)
        if pred_new is not None:
            if pred_new.tzinfo is None:
                pred_new = pred_new.replace(tzinfo=timezone.utc)
            excess_min = (pred_new - shift_end).total_seconds() / 60.0
            metrics["v324a_planned_dropoff_iso"] = pred_new.isoformat()
            metrics["v324a_dropoff_excess_min"] = round(excess_min, 2)
            if excess_min > C.V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN:
                return (
                    "NO",
                    f"v324a_dropoff_after_shift (dropoff {pred_new.strftime('%H:%M')} "
                    f"vs shift_end {shift_end.strftime('%H:%M')}, excess +{excess_min:.1f}min)",
                    metrics,
                    plan,
                )

    # F2.2 C2 — per-order 35min hard gate (shadow mode by default).
    # Current verdict at this point is MAYBE (survived all other gates).
    # check_per_order_35min_rule uses plan.per_order_delivery_times (C1 field).
    c2_passes, c2_details = check_per_order_35min_rule(plan)
    metrics["c2_passes"] = c2_passes
    metrics["c2_max_elapsed_min"] = c2_details["max_elapsed"]
    metrics["c2_violations_count"] = len(c2_details["violations"])
    metrics["c2_per_order_data_available"] = c2_details["per_order_data_available"]

    if ENABLE_C2_SHADOW_LOG and not c2_passes:
        _emit_c2_shadow_diff_event(
            current_verdict="MAYBE",
            c2_passes=c2_passes,
            c2_details=c2_details,
            plan=plan,
            metrics=metrics,
            new_order_id=new_order.order_id,
            bag_size_before=metrics.get("bag_size_before", 0),
        )

    if USE_PER_ORDER_GATE and not c2_passes:
        worst_oid, worst_elapsed = max(c2_details["violations"], key=lambda v: v[1]) \
            if c2_details["violations"] else ("?", c2_details["max_elapsed"])
        return (
            "NO",
            f"C2_per_order_35min_exceeded ({worst_oid} {worst_elapsed:.1f}min>{C2_PER_ORDER_THRESHOLD_MIN})",
            metrics,
            plan,
        )

    return ("MAYBE", "ok_sla_fits", metrics, plan)
