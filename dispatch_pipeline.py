"""dispatch_pipeline - per-order assessment: feasibility → scoring → rank → verdict.

Input:  NEW_ORDER event dict + fleet snapshot + restaurant_meta.
Output: PipelineResult with ranked candidates and final verdict.

Verdicts:
    PROPOSE — best candidate is feasible, send to Telegram for approval
    KOORD   — early-bird (>=60 min ahead) OR R28 best_effort (no feasible, SLA compromise)
    SKIP    — no candidate with any plan (fleet empty / all fast-filter rejections).
              R29 says never hang; SKIP always alerts Adrian.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any

from dispatch_v2.route_simulator_v2 import OrderSim, RoutePlanV2, DWELL_PICKUP_MIN
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2 import scoring
from dispatch_v2 import common as C
from dispatch_v2.common import parse_panel_timestamp, WARSAW, HAVERSINE_ROAD_FACTOR_BIALYSTOK, get_fallback_speed_kmh
from dispatch_v2.osrm_client import haversine
import math

log = logging.getLogger(__name__)


def _point_to_segment_km(p, a, b) -> float:
    """Najkrótsza odległość punktu p od odcinka [a, b] w km.
    Equirectangular projection — wystarczająca dla skali Białegostoku (<30 km)."""
    lat0 = (a[0] + b[0] + p[0]) / 3.0
    coslat = math.cos(math.radians(lat0))
    def to_xy(pt):
        return (pt[1] * coslat * 111.32, pt[0] * 111.32)
    ax, ay = to_xy(a)
    bx, by = to_xy(b)
    px, py = to_xy(p)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


def _min_dist_to_route_km(point, courier_pos, bag_dropoffs) -> Optional[float]:
    """Min dystans od punktu do polyline kurier→bag_dropoff_1→bag_dropoff_2...
    None gdy bag pusty lub brak coords."""
    if not bag_dropoffs:
        return None
    nodes = [courier_pos] + [d for d in bag_dropoffs if d]
    if len(nodes) < 2:
        return None
    return min(_point_to_segment_km(point, nodes[i], nodes[i+1]) for i in range(len(nodes)-1))


EARLY_BIRD_THRESHOLD_MIN = 60
TOP_N_CANDIDATES = 5
DEFAULT_FLEET_PREP_VARIANCE_MIN = 13.0


@dataclass
class Candidate:
    courier_id: str
    name: Optional[str]
    score: float
    feasibility_verdict: str  # "MAYBE" | "NO"
    feasibility_reason: str
    plan: Optional[RoutePlanV2]
    metrics: Dict[str, Any] = field(default_factory=dict)
    best_effort: bool = False


@dataclass
class PipelineResult:
    order_id: str
    verdict: str  # "PROPOSE" | "KOORD" | "SKIP"
    reason: str
    best: Optional[Candidate]
    candidates: List[Candidate]
    pickup_ready_at: Optional[datetime]
    restaurant: Optional[str]
    delivery_address: Optional[str] = None


def get_pickup_ready_at(
    restaurant_name: Optional[str],
    pickup_at: Optional[datetime],
    now: datetime,
    meta: Optional[dict],
) -> Optional[datetime]:
    """Effective pickup-ready time = panel-declared pickup_at (czysto, bez bufora).

    F1.8g: usunięty historyczny bufor prep_variance_min (D16). Display w
    propozycji Telegram pokazywał czas powiększony o medianę spóźnień restauracji,
    co Adrian odbierał jako bug. restaurant_meta.prep_variance_min nadal
    dostępne dla alertów/monitoringu (R17/R19), ale NIE doliczane do pickup_ready_at.
    """
    if pickup_at is None:
        return None
    if pickup_at.tzinfo is None:
        pickup_at = pickup_at.replace(tzinfo=WARSAW)
    pickup_utc = pickup_at.astimezone(timezone.utc)
    return max(now, pickup_utc)


def _bag_dict_to_ordersim(d: dict) -> OrderSim:
    picked = parse_panel_timestamp(d.get("picked_up_at"))
    pra = parse_panel_timestamp(d.get("pickup_at_warsaw"))  # F2.1c R8 T_KUR
    status = d.get("status", "assigned")
    pickup_c = d.get("pickup_coords") or (0.0, 0.0)
    deliv_c = d.get("delivery_coords") or (0.0, 0.0)
    return OrderSim(
        order_id=str(d.get("order_id") or d.get("id") or ""),
        pickup_coords=tuple(pickup_c),
        delivery_coords=tuple(deliv_c),
        picked_up_at=picked,
        status="picked_up" if status == "picked_up" else "assigned",
        pickup_ready_at=pra,  # F2.1c R8 T_KUR propagation
    )


def _oldest_in_bag_min(bag: List[OrderSim], now: datetime) -> Optional[float]:
    ages: List[float] = []
    for o in bag:
        if o.picked_up_at is None:
            continue
        pu = o.picked_up_at
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        ages.append((now - pu.astimezone(timezone.utc)).total_seconds() / 60.0)
    return max(ages) if ages else None


def assess_order(
    order_event: dict,
    fleet_snapshot: Dict[str, Any],
    restaurant_meta: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PipelineResult:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    order_id = str(order_event.get("order_id") or "")
    restaurant = order_event.get("restaurant")
    delivery_address = order_event.get("delivery_address")
    pickup_coords = tuple(order_event.get("pickup_coords") or (0.0, 0.0))
    delivery_coords = tuple(order_event.get("delivery_coords") or (0.0, 0.0))

    pickup_at_raw = order_event.get("pickup_at_warsaw") or order_event.get("pickup_at")
    pickup_at = parse_panel_timestamp(pickup_at_raw) if pickup_at_raw else None

    # Early bird → KOORD
    if pickup_at is not None:
        pu = pickup_at if pickup_at.tzinfo else pickup_at.replace(tzinfo=WARSAW)
        minutes_ahead = (pu.astimezone(timezone.utc) - now).total_seconds() / 60.0
        if minutes_ahead >= EARLY_BIRD_THRESHOLD_MIN:
            return PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=f"early_bird ({minutes_ahead:.0f} min ahead)",
                best=None,
                candidates=[],
                pickup_ready_at=None,
                restaurant=restaurant,
                delivery_address=delivery_address,
            )

    pickup_ready_at = get_pickup_ready_at(restaurant, pickup_at, now, restaurant_meta)

    new_order = OrderSim(
        order_id=order_id,
        pickup_coords=pickup_coords,
        delivery_coords=delivery_coords,
        status="assigned",
        pickup_ready_at=pickup_ready_at,
    )

    # Traffic-aware fallback speed dla estymat ETA (zgodne z P0.5 common.py)
    fleet_speed_kmh = get_fallback_speed_kmh(now)

    candidates: List[Candidate] = []
    new_rest_norm = (restaurant or "").strip().lower()

    for cid, cs in fleet_snapshot.items():
        courier_pos = getattr(cs, "pos", None)
        if courier_pos is None:
            continue
        bag_raw = getattr(cs, "bag", []) or []
        bag_sim = [_bag_dict_to_ordersim(b) for b in bag_raw]

        # POZIOM 1 same-restaurant: order w bagu ze statusem "assigned" (kurier
        # jeszcze JEDZIE do pickupu) z tej samej restauracji co nowy order.
        # Picked_up SKIP: kurier już odjechał od restauracji, nie wraca po więcej.
        bundle_level1 = None
        if new_rest_norm:
            for b in bag_raw:
                if b.get("status") != "assigned":
                    continue
                br = (b.get("restaurant") or "").strip().lower()
                if br and br == new_rest_norm:
                    bundle_level1 = b.get("restaurant")
                    break

        # POZIOM 2 nearby pickup (<1.5 km): tylko w restauracjach gdzie kurier
        # jeszcze ma jechać po pickup (status="assigned"). Skip jeśli L1 lub
        # pickup_coords sentinel (0, 0).
        bundle_level2 = None
        bundle_level2_dist = None
        if (bundle_level1 is None
                and pickup_coords != (0.0, 0.0)
                and pickup_coords[0] != 0.0):
            for b in bag_raw:
                if b.get("status") != "assigned":
                    continue
                bag_pc = b.get("pickup_coords")
                if not bag_pc:
                    continue
                try:
                    dist = haversine(tuple(bag_pc), pickup_coords)
                except Exception:
                    continue
                if dist < 1.5:
                    bundle_level2 = b.get("restaurant")
                    bundle_level2_dist = round(dist, 2)
                    break

        # POZIOM 3 corridor delivery (<2.0 km): nowa dostawa leży w korytarzu
        # trasy kurier → bag deliveries. Niezależny od L1/L2.
        bundle_level3 = False
        bundle_level3_dev = None
        if (delivery_coords != (0.0, 0.0)
                and delivery_coords[0] != 0.0):
            bag_drops = [b.get("delivery_coords") for b in bag_raw if b.get("delivery_coords")]
            dev = _min_dist_to_route_km(delivery_coords, tuple(courier_pos), bag_drops)
            if dev is not None and dev < 2.0:
                bundle_level3 = True
                bundle_level3_dev = round(dev, 2)

        # SLA 45 min dla bundli (per dane historyczne 86%/95% w 35/45 min).
        # Solo (pusty bag) zostaje 35 min — nie poluzowujemy sytuacji bez bundlingu.
        sla_minutes = 45 if bag_sim else 35

        verdict, reason, metrics, plan = check_feasibility_v2(
            courier_pos=tuple(courier_pos),
            bag=bag_sim,
            new_order=new_order,
            shift_end=getattr(cs, "shift_end", None),
            now=now,
            sla_minutes=sla_minutes,
        )

        # F1.8f hard guard: kurier którego zmiana kończy się PRZED pickup_ready_at
        # nie może wziąć tego zlecenia (nawet jeśli SHIFT_END_BUFFER_MIN przeszło).
        cs_shift_end = getattr(cs, "shift_end", None)
        if cs_shift_end is not None and pickup_ready_at is not None:
            if cs_shift_end.tzinfo is None:
                cs_shift_end_utc = cs_shift_end.replace(tzinfo=timezone.utc)
            else:
                cs_shift_end_utc = cs_shift_end.astimezone(timezone.utc)
            if pickup_ready_at > cs_shift_end_utc:
                verdict = "NO"
                end_hhmm = cs_shift_end.strftime("%H:%M") if hasattr(cs_shift_end, "strftime") else "?"
                reason = f"shift_end_before_pickup (zmiana do {end_hhmm}, odbiór później)"
                plan = None

        bag_drop_coords = [b.delivery_coords for b in bag_sim]
        oldest = _oldest_in_bag_min(bag_sim, now)
        score_result = scoring.score_candidate(
            courier_pos=tuple(courier_pos),
            restaurant_pos=pickup_coords,
            bag_drop_coords=bag_drop_coords or None,
            bag_size=len(bag_sim),
            oldest_in_bag_min=oldest,
        )

        # F1.7 fix: travel_min = plan-based (uwzględnia bag + waiting na pickup_ready),
        # używane przez compute_assign_time. Display ETA jest osobne (drive_min).
        km_to_pickup_haversine = haversine(tuple(courier_pos), pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK

        # drive_min: czysty czas jazdy z pozycji kuriera → restauracja (haversine).
        # Per-candidate distinct (różne dystansy = różne ETA display).
        drive_min = (km_to_pickup_haversine / fleet_speed_kmh) * 60.0 if fleet_speed_kmh > 0 else 0.0
        drive_arrival_utc = now + timedelta(minutes=drive_min)

        eta_source = "haversine"
        if plan is not None and order_id in (plan.pickup_at or {}):
            arrive_pickup_utc = plan.pickup_at[order_id] - timedelta(minutes=DWELL_PICKUP_MIN)
            if arrive_pickup_utc.tzinfo is None:
                arrive_pickup_utc = arrive_pickup_utc.replace(tzinfo=timezone.utc)
            travel_min = max(0.0, (arrive_pickup_utc - now).total_seconds() / 60.0)
            eta_pickup_utc = arrive_pickup_utc
            eta_source = "plan"
        else:
            travel_min = drive_min
            eta_pickup_utc = drive_arrival_utc

        # Bundle bonus — sumowanie L1 + L2 + R4 (Bartek Gold Standard).
        # L1 = +25 (same restaurant), L2 = max(0, 20 - dist*10).
        # R4 (zastępuje L3): tier-based free-stop curve × weight 1.5.
        #   dev ≤ 0.5 km  → raw 100      (full free stop)
        #   0.5 < dev ≤ 1.5 → raw 50*(1.5-d)/1.0 linear
        #   1.5 < dev ≤ 2.5 → raw 20*(2.5-d)/1.0 linear
        #   > 2.5 km       → raw 0
        bonus_l1 = 25.0 if bundle_level1 else 0.0
        bonus_l2 = max(0.0, 20.0 - bundle_level2_dist * 10.0) if bundle_level2_dist is not None else 0.0
        if bundle_level3_dev is None:
            bonus_r4_raw = 0.0
        else:
            d = bundle_level3_dev
            if d <= 0.5:
                bonus_r4_raw = 100.0
            elif d <= 1.5:
                bonus_r4_raw = 50.0 * (1.5 - d)
            elif d <= 2.5:
                bonus_r4_raw = 20.0 * (2.5 - d)
            else:
                bonus_r4_raw = 0.0
        bonus_r4 = bonus_r4_raw * 1.5  # R4 weight per Bartek Gold Standard
        bundle_bonus = bonus_l1 + bonus_l2 + bonus_r4

        # Availability bonus: kiedy kurier będzie wolny po obecnym bagu (BEZ nowego ordera).
        # Liczone z plan.predicted_delivered_at[last_bag_oid_in_seq] (Opcja B).
        # Bag pusty → free_at_min=0 → bonus +10 (już wolny).
        free_at_min = 0.0
        if bag_sim and plan is not None and plan.predicted_delivered_at:
            bag_oids_set = {o.order_id for o in bag_sim}
            bag_in_seq = [oid for oid in (plan.sequence or []) if oid in bag_oids_set]
            if bag_in_seq:
                last_bag_oid = bag_in_seq[-1]
                free_at_dt = plan.predicted_delivered_at.get(last_bag_oid)
                if free_at_dt is not None:
                    if free_at_dt.tzinfo is None:
                        free_at_dt = free_at_dt.replace(tzinfo=timezone.utc)
                    free_at_min = max(0.0, (free_at_dt - now).total_seconds() / 60.0)

        if free_at_min <= 0:
            availability_bonus = 10.0
        elif free_at_min < 15:
            availability_bonus = 8.0
        elif free_at_min < 30:
            availability_bonus = 5.0
        else:
            availability_bonus = 0.0

        # F2.1b penalties — R6 soft BAG_TIME + R9 stopover + R9 wait.
        # R8 soft pozostaje None (placeholder do F2.1c — brak T_KUR propagation).
        # Wszystkie penalties ≤ 0 (ujemne albo zero), dodawane do final_score.

        # R6 soft: zone 30-35 min BAG_TIME. Hard cap 35 min jest w feasibility_v2
        # (F2.1b step 3), tu widzimy tylko przypadki 30-35 min które przeszły hard.
        # Reuse metrics.r6_max_bag_time_min (step 3) — zero duplicate computation.
        bonus_r6_soft_pen: Optional[float] = None
        if plan is not None:
            r6_max_bag_time = metrics.get("r6_max_bag_time_min")
            if r6_max_bag_time is None:
                log.warning(
                    f"R6 soft skip: metrics.r6_max_bag_time_min missing "
                    f"despite plan!=None (expected after krok #6 restart)"
                )
                r6_max_bag_time = 0.0
            if r6_max_bag_time > C.BAG_TIME_SOFT_MIN:
                bonus_r6_soft_pen = -(r6_max_bag_time - C.BAG_TIME_SOFT_MIN) * C.BAG_TIME_SOFT_PENALTY_PER_MIN
            else:
                bonus_r6_soft_pen = 0.0

        # R9 stopover — differential tax (bag=0 → 0, bag=1 → -8, bag=2 → -16, ...).
        # Rationale: scoring porównuje kandydatów względem kosztu DODANIA stopu,
        # nie absolutnego. Zgodny z op.1 "podatek przystankowy".
        bonus_r9_stopover = -len(bag_sim) * C.STOPOVER_SCORE_PER_STOP

        # R9 wait — penalty za przewidywane oczekiwanie pod restauracją > 5 min.
        # Wait = max(0, T_KUR_from_now - effective_drive_min).
        #
        # F2.1b step 4.1 fix: dla no_gps/pre_shift courierów drive_min z linii 285
        # jest liczony z SYNTHETIC courier_pos (fallback do BIALYSTOK_CENTER lub
        # last-known), co dla restauracji w centrum daje sztucznie niski drive_min
        # (~2-3 min) → wait_pred zawyżony → nierealny penalty.
        # Historyczny bug: order #466290 Chicago Pizza @ 2026-04-15T19:16:45 UTC,
        # Patryk 5506 (no_gps), bonus_r9_wait_pen = -101.76.
        #
        # Fix: effective_drive_min replikuje post-loop normalization (linie 453-469):
        #   no_gps     → max(15, prep_remaining_min)   (zgodne z linią 450)
        #   pre_shift  → shift_start_min                (zgodne z linią 465)
        #   inne       → drive_min                       (bez zmian dla GPS)
        bonus_r9_wait_pen = 0.0
        if pickup_ready_at is not None:
            _pos_src = getattr(cs, "pos_source", None)
            if _pos_src == "no_gps":
                _prep_rem = max(0.0, (pickup_ready_at - now).total_seconds() / 60.0)
                effective_drive_min = max(15.0, _prep_rem)
            elif _pos_src == "pre_shift":
                effective_drive_min = float(getattr(cs, "shift_start_min", 0) or 0)
            else:
                effective_drive_min = drive_min
            tkur_from_now_min = (pickup_ready_at - now).total_seconds() / 60.0
            wait_pred_min = max(0.0, tkur_from_now_min - effective_drive_min)
            if wait_pred_min > C.RESTAURANT_WAIT_SOFT_MIN:
                bonus_r9_wait_pen = -(wait_pred_min - C.RESTAURANT_WAIT_SOFT_MIN) * C.RESTAURANT_WAIT_PENALTY_PER_MIN

        # Suma penalties (R6 None → 0). R8 F2.1c — wyliczany z r8_pickup_span_min.
        _r8_span = metrics.get("r8_pickup_span_min") or 0
        bonus_r8_soft_pen = (
            -(_r8_span - C.PICKUP_SPAN_SOFT_START_MIN) * C.PICKUP_SPAN_SOFT_PENALTY_PER_MIN
            if _r8_span > C.PICKUP_SPAN_SOFT_START_MIN else 0.0
        )
        bonus_penalty_sum = (bonus_r6_soft_pen or 0.0) + bonus_r8_soft_pen + bonus_r9_stopover + bonus_r9_wait_pen

        # Post-wave override (F2.1c): brak GPS + wszystkie picked_up + kończy ≤15 min
        # Kurier zaraz wraca do centrum → bonus scoring
        pos_source_effective = getattr(cs, "pos_source", "no_gps")
        all_picked_up = (
            len(bag_sim) > 0 and
            all(getattr(o, "status", "") == "picked_up" for o in bag_sim)
        )
        wave_bonus = 0.0
        if (all_picked_up and
                pos_source_effective != "gps" and
                free_at_min <= C.POST_WAVE_FREE_MAX_MIN):
            pos_source_effective = "post_wave"
            wave_bonus = C.POST_WAVE_BONUS_FAST
        elif (all_picked_up and
                pos_source_effective != "gps" and
                free_at_min <= 30):
            pos_source_effective = "post_wave"
            wave_bonus = C.POST_WAVE_BONUS_SLOW

        final_score = score_result["total"] + bundle_bonus + availability_bonus + wave_bonus + bonus_penalty_sum

        enriched_metrics = {
            **metrics,
            "score": score_result,
            "km_to_pickup": round(km_to_pickup_haversine, 2),
            "travel_min": round(travel_min, 1),
            "drive_min": round(drive_min, 1),
            "eta_pickup_utc": eta_pickup_utc.isoformat(),
            "eta_drive_utc": drive_arrival_utc.isoformat(),
            "eta_source": eta_source,
            "pos_source": getattr(cs, "pos_source", None),
            "shift_start_min": getattr(cs, "shift_start_min", None),
            "bundle_level1": bundle_level1,
            "bundle_level2": bundle_level2,
            "bundle_level2_dist": bundle_level2_dist,
            "bundle_level3": bundle_level3,
            "bundle_level3_dev": bundle_level3_dev,
            "bonus_l1": round(bonus_l1, 2),
            "bonus_l2": round(bonus_l2, 2),
            "bonus_r4_raw": round(bonus_r4_raw, 2),
            "bonus_r4": round(bonus_r4, 2),
            "bundle_bonus": round(bundle_bonus, 2),
            "availability_bonus": round(availability_bonus, 2),
            "wave_bonus": round(wave_bonus, 2),
            "pos_source": pos_source_effective,
            "free_at_min": round(free_at_min, 1),
            "sla_minutes_used": sla_minutes,
            # F2.1b/F2.1c penalties. R8 aktywne od F2.1c (T_KUR propagation step 1-4).
            "bonus_r6_soft_pen": (
                round(bonus_r6_soft_pen, 2)
                if bonus_r6_soft_pen is not None else None
            ),
            "bonus_r8_soft_pen": round(bonus_r8_soft_pen, 2),
            "bonus_r9_stopover": round(bonus_r9_stopover, 2),
            "bonus_r9_wait_pen": round(bonus_r9_wait_pen, 2),
            "bonus_penalty_sum": round(bonus_penalty_sum, 2),
        }

        candidates.append(Candidate(
            courier_id=str(cid),
            name=getattr(cs, "name", None),
            score=final_score,
            feasibility_verdict=verdict,
            feasibility_reason=reason,
            plan=plan,
            metrics=enriched_metrics,
        ))

    # F1.7 no_gps fallback: kurier z syntetycznym pos (centrum) dostaje
    # neutralne km/ETA. km_to_pickup = średnia floty (tylko z realnych pos),
    # travel_min = max(15, prep_remaining_min). Score liczony z centrum został,
    # bo i tak jest blisko mediany floty — nie faworyzuje, nie wyklucza.
    real_kms = [
        c.metrics.get("km_to_pickup")
        for c in candidates
        if c.metrics.get("pos_source") not in ("no_gps", None)
        and c.metrics.get("km_to_pickup") is not None
    ]
    fleet_avg_km = (sum(real_kms) / len(real_kms)) if real_kms else 5.0
    prep_remaining_min = 0.0
    if pickup_ready_at is not None:
        ready_utc = pickup_ready_at if pickup_ready_at.tzinfo else pickup_ready_at.replace(tzinfo=timezone.utc)
        prep_remaining_min = max(0.0, (ready_utc.astimezone(timezone.utc) - now).total_seconds() / 60.0)
    no_gps_travel_min = max(15.0, prep_remaining_min)
    no_gps_eta_utc = now + timedelta(minutes=no_gps_travel_min)

    for c in candidates:
        ps = c.metrics.get("pos_source")
        if ps == "no_gps":
            c.metrics["km_to_pickup"] = round(fleet_avg_km, 2)
            c.metrics["travel_min"] = round(no_gps_travel_min, 1)
            c.metrics["drive_min"] = round(no_gps_travel_min, 1)
            c.metrics["eta_pickup_utc"] = no_gps_eta_utc.isoformat()
            c.metrics["eta_drive_utc"] = no_gps_eta_utc.isoformat()
            c.metrics["eta_source"] = "no_gps_fallback"
        elif ps == "pre_shift":
            # Kurier zaczyna zmianę za N min — travel_min = N (czas oczekiwania).
            # Bez km (nieznane gdzie będzie). eta_pickup = start zmiany.
            shift_min = float(c.metrics.get("shift_start_min") or 0.0)
            shift_eta = (now + timedelta(minutes=shift_min)).isoformat()
            c.metrics["km_to_pickup"] = None
            c.metrics["travel_min"] = round(shift_min, 1)
            c.metrics["drive_min"] = round(shift_min, 1)
            c.metrics["eta_pickup_utc"] = shift_eta
            c.metrics["eta_drive_utc"] = shift_eta
            c.metrics["eta_source"] = "pre_shift"
            # F1.8e: hard exclude jeśli pre_shift kurier nie zdąży na pickup_ready.
            # Bez tego scoring promuje go pomimo niedostępności (np. odbiór za 26
            # min, kurier startuje za 46 min → nie zdąży).
            if shift_min > prep_remaining_min + 0.01:
                c.feasibility_verdict = "NO"
                c.feasibility_reason = (
                    f"pre_shift_too_late (start za {shift_min:.0f} min, "
                    f"odbiór za {prep_remaining_min:.0f} min)"
                )

    # Feasible (MAYBE) → rank by score.
    # R2 Bartek Gold Standard tie-breaker: przy równym score, preferuj
    # kandydata o niższej corridor deviation (bundle_level3_dev).
    # Brak dev (pusty bag / solo) → 999 (sortuje się na koniec przy tie).
    feasible = [c for c in candidates if c.feasibility_verdict == "MAYBE"]
    feasible.sort(key=lambda c: (-c.score, c.metrics.get("bundle_level3_dev") if c.metrics.get("bundle_level3_dev") is not None else 999.0))

    if feasible:
        top = feasible[:TOP_N_CANDIDATES]
        return PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"feasible={len(feasible)} best={top[0].courier_id}",
            best=top[0],
            candidates=top,
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
        )

    # R28 best_effort: NO candidates that still produced a plan (SLA-only rejections)
    with_plan = [c for c in candidates if c.plan is not None]
    with_plan.sort(key=lambda c: (c.plan.sla_violations, c.plan.total_duration_min))
    if with_plan:
        best = with_plan[0]
        best.best_effort = True
        return PipelineResult(
            order_id=order_id,
            verdict="KOORD",
            reason=f"best_effort (0 feasible, best_violations={best.plan.sla_violations})",
            best=best,
            candidates=with_plan[:TOP_N_CANDIDATES],
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
        )

    # R29 fallback: nothing landed — alert Adrian
    return PipelineResult(
        order_id=order_id,
        verdict="SKIP",
        reason=f"no_candidates (fleet_n={len(candidates)}) — ALERT ADRIAN",
        best=None,
        candidates=candidates,
        pickup_ready_at=pickup_ready_at,
        restaurant=restaurant,
        delivery_address=delivery_address,
    )
