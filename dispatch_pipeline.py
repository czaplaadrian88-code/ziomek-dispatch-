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
from dispatch_v2.common import (
    parse_panel_timestamp,
    WARSAW,
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    get_fallback_speed_kmh,
    ENABLE_CZAS_KURIERA_PROPAGATION,
)
from dispatch_v2.osrm_client import haversine
from dispatch_v2.bag_state import build_courier_bag_state, CourierBagState
from dispatch_v2.fleet_context import build_fleet_context, FleetContext
import math

log = logging.getLogger(__name__)


BLIND_POS_SOURCES = ("no_gps", "pre_shift", "none")
INFORMED_POS_SOURCES = (
    "gps", "last_assigned_pickup", "last_picked_up_delivery",
    "last_picked_up_recent", "last_delivered", "post_wave",
)


def _is_blind_empty_cand(c) -> bool:
    """V3.16: kandydat z synthetic pos (no_gps/pre_shift/none) i pustym bagiem."""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    bsize = c.metrics.get("r6_bag_size", 0) if hasattr(c, "metrics") and c.metrics else 0
    return ps in BLIND_POS_SOURCES and (bsize or 0) == 0


def _is_informed_cand(c) -> bool:
    """V3.16: kandydat z real pos source (fresh GPS lub recent panel activity)."""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    return ps in INFORMED_POS_SOURCES


def _demote_blind_empty(feasible: list, order_id=None) -> list:
    """V3.16 demotion: jeśli top-1 jest blind+empty AND istnieje informed alt,
    reorder — informed first (stable), other middle, blind+empty last.
    Guard "all blind": jeśli żadnego informed → zostaw bez zmian.
    """
    try:
        flag = bool(getattr(C, "ENABLE_NO_GPS_EMPTY_DEMOTE", True))
    except Exception:
        flag = True
    if not flag or not feasible:
        return feasible
    if not _is_blind_empty_cand(feasible[0]):
        return feasible
    informed = [c for c in feasible if _is_informed_cand(c)]
    if not informed:
        return feasible  # all blind — nie degraduj (empty shift edge)
    original_top_cid = feasible[0].courier_id
    other = [c for c in feasible
             if not _is_informed_cand(c) and not _is_blind_empty_cand(c)]
    blind_empty = [c for c in feasible if _is_blind_empty_cand(c)]
    reordered = informed + other + blind_empty
    log.info(
        f"NO_GPS_DEMOTE order={order_id}: top cid={original_top_cid} "
        f"(no_gps+empty) demoted; informed_alts={len(informed)}; "
        f"new_top_cid={reordered[0].courier_id}"
    )
    return reordered


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


def _bag_dict_to_order_in_bag_raw(d: dict) -> dict:
    """V3.18: bag dict → orders_raw entry dla build_courier_bag_state.

    Translate string status ('assigned'/'picked_up') na int (3/5).
    Panel raw ma czas_odbioru_timestamp → pickup_time (Warsaw).
    """
    str_status = d.get("status", "assigned")
    int_status = 5 if str_status == "picked_up" else 3
    # V3.19f: czas_kuriera_warsaw first-choice pod flagą (panel commitment HH:MM
    # declared arrival). Fallback chain: pickup_at_warsaw → czas_odbioru_timestamp.
    pickup_t = None
    if ENABLE_CZAS_KURIERA_PROPAGATION:
        pickup_t = parse_panel_timestamp(d.get("czas_kuriera_warsaw"))
    if pickup_t is None:
        pickup_t = (
            parse_panel_timestamp(d.get("pickup_at_warsaw"))
            or parse_panel_timestamp(d.get("czas_odbioru_timestamp"))
        )
    added = parse_panel_timestamp(d.get("assigned_at")) or parse_panel_timestamp(d.get("created_at"))
    return {
        "order_id": str(d.get("order_id") or d.get("id") or ""),
        "restaurant_address": d.get("restaurant") or d.get("restaurant_address", ""),
        "restaurant_coords": tuple(d["pickup_coords"]) if d.get("pickup_coords") else None,
        "drop_address": d.get("delivery_address", ""),
        "drop_coords": tuple(d["delivery_coords"]) if d.get("delivery_coords") else None,
        "pickup_time": pickup_t,
        "predicted_drop_time": None,  # computed later by route_simulator
        "status": int_status,
        "added_at": added,
    }


def _build_fleet_context_from_snapshot(
    fleet_snapshot: Dict[str, Any],
    now: datetime,
) -> FleetContext:
    """V3.18: build FleetContext z fleet_snapshot dla Bug 2 (overload penalty).

    Per courier: minimal CourierBagState (tylko bag_size + pos_source matter).
    """
    bag_states = []
    for cid, cs in fleet_snapshot.items():
        bag_raw = getattr(cs, "bag", []) or []
        orders_raw = [_bag_dict_to_order_in_bag_raw(b) for b in bag_raw]
        bag_states.append(build_courier_bag_state(
            courier_id=str(cid),
            nick=getattr(cs, "name", "?") or "?",
            pos_source=getattr(cs, "pos_source", "?") or "?",
            position=getattr(cs, "pos", None),
            orders_raw=orders_raw,
            now=now,
        ))
    return build_fleet_context(bag_states, now=now)


def _bag_dict_to_ordersim(d: dict) -> OrderSim:
    picked = parse_panel_timestamp(d.get("picked_up_at"))
    # V3.19f: czas_kuriera_warsaw first-choice dla pickup_ready_at (F2.1c R8 T_KUR).
    # Fallback do pickup_at_warsaw (pre-V3.19f behavior) gdy flaga False albo brak.
    pra = None
    if ENABLE_CZAS_KURIERA_PROPAGATION:
        pra = parse_panel_timestamp(d.get("czas_kuriera_warsaw"))
    if pra is None:
        pra = parse_panel_timestamp(d.get("pickup_at_warsaw"))
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
    *,
    # F2.2 C7 skeleton (2026-04-18): additive kwargs for wave_scoring/commitment wire-up.
    # Existing 2 callers (shadow_dispatcher, test_decision_engine_f21) pass positional
    # args only → these kwargs stay None, zero behavior change.
    # When ENABLE_PENDING_QUEUE_VIEW=True AND kwargs=None → auto-fetch providers.
    pending_queue: Optional[list] = None,
    demand_context: Optional[dict] = None,
) -> PipelineResult:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # F2.2 C7: Auto-fetch providers when flag enabled and caller passed None
    from dispatch_v2.common import ENABLE_PENDING_QUEUE_VIEW
    if ENABLE_PENDING_QUEUE_VIEW:
        if pending_queue is None:
            try:
                from dispatch_v2.pending_queue_provider import get_pending_queue
                pending_queue = get_pending_queue()
            except Exception:
                pending_queue = []
        if demand_context is None:
            try:
                from dispatch_v2.pending_queue_provider import compute_demand_context
                demand_context = compute_demand_context(now)
            except Exception:
                demand_context = {}
    # pending_queue and demand_context are available for downstream wave_scoring
    # wire-up in future C7 iteration. Current flow below unchanged.

    order_id = str(order_event.get("order_id") or "")
    restaurant = order_event.get("restaurant")
    delivery_address = order_event.get("delivery_address")
    pickup_coords = tuple(order_event.get("pickup_coords") or (0.0, 0.0))
    delivery_coords = tuple(order_event.get("delivery_coords") or (0.0, 0.0))

    # V3.19f: czas_kuriera_warsaw first-choice pod flagą (panel HH:MM commitment).
    # Fallback do pickup_at_warsaw (pre-V3.19f behavior) gdy flaga False albo brak.
    pickup_at_raw = None
    _ck_used = False
    if ENABLE_CZAS_KURIERA_PROPAGATION:
        _ck_warsaw = order_event.get("czas_kuriera_warsaw")
        if _ck_warsaw:
            pickup_at_raw = _ck_warsaw
            _ck_used = True
    if pickup_at_raw is None:
        pickup_at_raw = order_event.get("pickup_at_warsaw") or order_event.get("pickup_at")
    pickup_at = parse_panel_timestamp(pickup_at_raw) if pickup_at_raw else None
    if _ck_used and pickup_at is not None:
        log.debug(
            f"V3.19f: pickup_ready_at=czas_kuriera={pickup_at_raw} "
            f"(vs pickup_at_warsaw={order_event.get('pickup_at_warsaw')}) "
            f"oid={order_id}"
        )

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

    # V3.18 (2026-04-19): FleetContext once per event dla scoring overload penalty.
    # Flag ENABLE_UNIFIED_BAG_STATE=False → fleet_context=None, scoring ignoruje kwarg.
    fleet_context: Optional[FleetContext] = None
    if C.ENABLE_UNIFIED_BAG_STATE:
        try:
            fleet_context = _build_fleet_context_from_snapshot(fleet_snapshot, now.astimezone(WARSAW))
        except Exception as e:
            log.warning(f"V3.18 fleet_context build failed ({e}), falling back to None")
            fleet_context = None

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

        # V3.19d: read integration — extract base_sequence z saved plan dla
        # bag ordering. Triple guard: flag True + bag non-empty + saved match.
        # Mismatch / exception → base_sequence=None (fresh TSP fallback).
        _base_sequence = None
        if bag_sim:
            try:
                from dispatch_v2.common import ENABLE_SAVED_PLANS_READ
                if ENABLE_SAVED_PLANS_READ:
                    from dispatch_v2 import plan_manager as _pm_read
                    _bag_oids = {str(o.order_id) for o in bag_sim}
                    _saved = _pm_read.load_plan(str(cid), active_bag_oids=_bag_oids)
                    if _saved is not None:
                        _seq = [
                            str(s["order_id"]) for s in _saved.get("stops", [])
                            if s.get("type") == "dropoff"
                            and str(s.get("order_id")) in _bag_oids
                        ]
                        if set(_seq) == _bag_oids and len(_seq) == len(_bag_oids):
                            _base_sequence = _seq
            except Exception:
                _base_sequence = None

        verdict, reason, metrics, plan = check_feasibility_v2(
            courier_pos=tuple(courier_pos),
            bag=bag_sim,
            new_order=new_order,
            shift_end=getattr(cs, "shift_end", None),
            now=now,
            sla_minutes=sla_minutes,
            base_sequence=_base_sequence,
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

        # V3.19c sub B: observational read-shadow diff log. Zero wpływu na
        # scoring path — tylko zapisuje różnicę saved vs fresh plan sequence
        # dla orderów w bagu. Flag ENABLE_SAVED_PLANS_READ_SHADOW default True.
        if plan is not None and plan.sequence and bag_sim:
            try:
                from dispatch_v2 import plan_manager as _pm_shadow
                _active_bag = {str(o.order_id) for o in bag_sim}
                _pm_shadow.log_read_shadow_diff(
                    courier_id=str(cid),
                    fresh_sequence=list(plan.sequence),
                    active_bag_oids=_active_bag,
                    now=now,
                    extra={"new_order_id": str(new_order.order_id)},
                )
            except Exception:
                pass  # shadow log never breaks hot path

        bag_drop_coords = [b.delivery_coords for b in bag_sim]
        oldest = _oldest_in_bag_min(bag_sim, now)

        # Fix 2: last_wave_pos — efektywna pozycja startowa do liczenia dystansu
        # do NOWEGO pickupu. Po dostarczeniu bagu kurier będzie w delivery_coords
        # ostatniego orderu z plan.sequence. Używane TYLKO dla km_to_pickup i
        # S_dystans (scoring.road_km). R4/R9 route-deviation i R9 wait zostają
        # z oryginalnym courier_pos (liczą trasę bagu, nie nowego punktu startu).
        # Kurier bez baga → effective_start_pos == courier_pos (no-op).
        effective_start_pos = tuple(courier_pos)
        if bag_sim and plan is not None and plan.sequence:
            _bag_by_oid = {o.order_id: o for o in bag_sim}
            _bag_in_seq = [oid for oid in plan.sequence if oid in _bag_by_oid]
            if _bag_in_seq:
                effective_start_pos = tuple(_bag_by_oid[_bag_in_seq[-1]].delivery_coords)

        # F1.7 fix: travel_min = plan-based (uwzględnia bag + waiting na pickup_ready),
        # używane przez compute_assign_time. Display ETA jest osobne (drive_min).
        # Fix 2: km_to_pickup liczone od effective_start_pos (end-of-wave dla bag).
        km_to_pickup_haversine = haversine(effective_start_pos, pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK

        # scoring.score_candidate: road_km przekazujemy jawnie (S_dystans użyje
        # effective_start_pos → pickup), a bearing (S_kierunek) nadal z courier_pos.
        score_result = scoring.score_candidate(
            courier_pos=tuple(courier_pos),
            restaurant_pos=pickup_coords,
            bag_drop_coords=bag_drop_coords or None,
            bag_size=len(bag_sim),
            oldest_in_bag_min=oldest,
            road_km=km_to_pickup_haversine,
            fleet_context=fleet_context,
        )

        # drive_min: pure drive od COURIER_POS (nie effective_start_pos) do restauracji.
        # R9 wait invariant + eta_drive display — trzyma oryginalną semantykę.
        _drive_km_from_courier = haversine(tuple(courier_pos), pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK
        drive_min = (_drive_km_from_courier / fleet_speed_kmh) * 60.0 if fleet_speed_kmh > 0 else 0.0
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
        # V3.19h BUG-1: drop_proximity_factor mnożnik na bonus_l1.
        # Gold tier pattern: SR bundle TYLKO gdy dropy blisko. Std bierze SR ślepo
        # (Kacper S avg drop_spread 10km dla SR bundles — anti-pattern).
        # Factor:
        #   1.0 — dropy w tej samej strefie (osiedlu)
        #   0.5 — adjacent strefach (sąsiadujące per ACK właściciela)
        #   0.0 — odległe albo Unknown (defensive)
        # min per-pair factor użyty (konserwatywnie najgorsza para).
        v319h_bug1_drop_proximity_factor = 1.0
        v319h_bug1_sr_bundle_adjusted = bonus_l1
        if C.ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR and bundle_level1:
            # Zbierz dropy: new_order + wszystkie bag items z SR match
            _new_zone = C.drop_zone_from_address(
                order_event.get('delivery_address'),
                order_event.get('delivery_city'),
            )
            _zones = [_new_zone]
            for _b in bag_raw:
                if _b.get('status') != 'assigned':
                    continue
                if (_b.get('restaurant') or '').strip().lower() != new_rest_norm:
                    continue
                _bz = C.drop_zone_from_address(
                    _b.get('delivery_address'), _b.get('delivery_city')
                )
                _zones.append(_bz)
            # min factor across pairs (konserwatywnie)
            if len(_zones) >= 2:
                _factor_min = 1.0
                for _i in range(len(_zones)):
                    for _j in range(_i + 1, len(_zones)):
                        _f = C.drop_proximity_factor(_zones[_i], _zones[_j])
                        if _f < _factor_min:
                            _factor_min = _f
                v319h_bug1_drop_proximity_factor = _factor_min
            # Zastosuj mnożnik
            bonus_l1 = bonus_l1 * v319h_bug1_drop_proximity_factor
            v319h_bug1_sr_bundle_adjusted = bonus_l1
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
        # V3.19h BUG-2 wave continuation bonus dodany do final_score niżej
        # (wymaga free_at_dt computed after bag sim — order-of-execution).

        # Timing gap bonus: dopasowanie free_at (kurier wolny) do pickup_ready
        # (jedzenie gotowe). Zastępuje availability_bonus.
        #   gap = free_at_min - time_to_pickup_ready
        #   |gap| ≤  5  → +25  (idealne dopasowanie)
        #   |gap| ≤ 10  → +15  (dobre)
        #   |gap| ≤ 15  → +5   (akceptowalne)
        #   gap  >  15  → -3/min za każdą minutę >15 (kurier się spóźni)
        #   gap  < -15  → -2/min za każdą minutę <-15 (restauracja czeka)
        # pickup_ready_at=None → time_to_pickup_ready = travel_min (zakładamy
        # gotowość gdy kurier dotrze) → gap neutralny.
        # Bag pusty → free_at_min = 0 (już wolny).
        free_at_min = 0.0
        free_at_dt: Optional[datetime] = None
        if bag_sim and plan is not None and plan.predicted_delivered_at:
            bag_oids_set = {o.order_id for o in bag_sim}
            bag_in_seq = [oid for oid in (plan.sequence or []) if oid in bag_oids_set]
            if bag_in_seq:
                last_bag_oid = bag_in_seq[-1]
                _free_at_dt = plan.predicted_delivered_at.get(last_bag_oid)
                if _free_at_dt is not None:
                    if _free_at_dt.tzinfo is None:
                        _free_at_dt = _free_at_dt.replace(tzinfo=timezone.utc)
                    free_at_dt = _free_at_dt
                    free_at_min = max(0.0, (_free_at_dt - now).total_seconds() / 60.0)

        if pickup_ready_at is not None:
            _pra_utc = pickup_ready_at if pickup_ready_at.tzinfo else pickup_ready_at.replace(tzinfo=timezone.utc)
            time_to_pickup_ready = max(0.0, (_pra_utc - now).total_seconds() / 60.0)
        else:
            time_to_pickup_ready = travel_min

        gap_min = free_at_min - time_to_pickup_ready
        _abs_gap = abs(gap_min)
        if _abs_gap <= 5:
            timing_gap_bonus = 25.0
        elif _abs_gap <= 10:
            timing_gap_bonus = 15.0
        elif _abs_gap <= 15:
            timing_gap_bonus = 5.0
        elif gap_min > 15:
            timing_gap_bonus = -3.0 * (gap_min - 15)
        else:  # gap_min < -15
            timing_gap_bonus = -2.0 * (-gap_min - 15)

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

        # Wczytaj rule_weights (adaptive penalties R1/R5/R8)
        try:
            import json as _json
            _rw_path = "/root/.openclaw/workspace/dispatch_state/rule_weights.json"
            with open(_rw_path) as _f:
                _rw = _json.load(_f)
        except Exception:
            _rw = {}

        # R1 soft penalty (delivery spread violation)
        _r1_viol = metrics.get("r1_violation_km") or 0.0
        bonus_r1_soft_pen = _r1_viol * _rw.get("R1_spread_per_km", -8.0) if _r1_viol > 0 else 0.0

        # R5 soft penalty (mixed pickup spread violation)
        _r5_viol = metrics.get("r5_violation_km") or 0.0
        bonus_r5_soft_pen = _r5_viol * _rw.get("R5_pickup_per_km", -6.0) if _r5_viol > 0 else 0.0

        # R8 soft penalty (pickup span — oryginalna + violation)
        _r8_span = metrics.get("r8_pickup_span_min") or 0
        bonus_r8_soft_pen = (
            -(_r8_span - C.PICKUP_SPAN_SOFT_START_MIN) * C.PICKUP_SPAN_SOFT_PENALTY_PER_MIN
            if _r8_span > C.PICKUP_SPAN_SOFT_START_MIN else 0.0
        )
        _r8_viol = metrics.get("r8_violation_min") or 0.0
        bonus_r8_soft_pen += _r8_viol * _rw.get("R8_span_per_min", -1.5) if _r8_viol > 0 else 0.0

        # V3.19h BUG-2: wave continuation bonus.
        # Gold tier pattern: interleave pickup wave #2 przed ukończeniem wave #1.
        # Bonus gdy pickup_new pasuje do projected free_at (last bag drop).
        # Source of truth dla free_at_dt: plan.predicted_delivered_at[last_bag_oid]
        # (spójny sticky V3.19d / V3.19e pre_pickup / fresh TSP).
        # pickup_at: V3.19f first-choice czas_kuriera_warsaw → pickup_at_warsaw.
        bug2_interleave_gap_min = None
        bonus_bug2_continuation = 0.0
        if C.ENABLE_V319H_BUG2_WAVE_CONTINUATION:
            if free_at_dt is not None and pickup_at is not None:
                _pu_utc = pickup_at if pickup_at.tzinfo else pickup_at.replace(tzinfo=WARSAW)
                _pu_utc = _pu_utc.astimezone(timezone.utc)
                _fa_utc = free_at_dt if free_at_dt.tzinfo else free_at_dt.replace(tzinfo=timezone.utc)
                _gap_sec = (_pu_utc - _fa_utc).total_seconds()
                bug2_interleave_gap_min = round(_gap_sec / 60.0, 2)
                bonus_bug2_continuation = C.bug2_wave_continuation_bonus(
                    bug2_interleave_gap_min
                )
            # edge: bag empty albo pickup_at=None → gap=None, bonus=0 (default)

        # V3.19h BUG-4: tier × pora bag cap soft penalty (progressive scaling).
        # Orthogonal do R6 hard bag_time. Flag gated (default False).
        bug4_tier_cap_used = None
        bug4_cap_violation = None
        bonus_bug4_cap_soft = 0.0
        if C.ENABLE_V319H_BUG4_TIER_CAP_MATRIX:
            _tier = getattr(cs, "tier_bag", None) or "std"
            _cap_override = getattr(cs, "tier_cap_override", None)
            _pora = C.bug4_pora_now(now)
            if isinstance(_cap_override, dict) and _pora in _cap_override:
                _cap = _cap_override[_pora]
            else:
                _cap = C.BUG4_TIER_CAP_MATRIX.get(_tier, C.BUG4_TIER_CAP_MATRIX["std"])[_pora]
            _bag_after = len(bag_sim) + 1
            bug4_cap_violation = max(0, _bag_after - _cap)
            bug4_tier_cap_used = f"{_tier}/{_pora}/{_cap}"
            bonus_bug4_cap_soft = C.bug4_soft_penalty(bug4_cap_violation)

        # Suma penalties (BUG-4 soft penalty dodany do puli)
        bonus_penalty_sum = (bonus_r6_soft_pen or 0.0) + bonus_r1_soft_pen + bonus_r5_soft_pen + bonus_r8_soft_pen + bonus_r9_stopover + bonus_r9_wait_pen + bonus_bug4_cap_soft
        # V3.19h BUG-2: wave continuation to BONUS (positive). Dodajemy do bundle_bonus
        # (nie penalty_sum) żeby zachować czysty semantyczny split penalty vs bonus.
        # Integracja z final_score — patrz niżej.

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

        final_score = score_result["total"] + bundle_bonus + timing_gap_bonus + wave_bonus + bonus_penalty_sum + bonus_bug2_continuation

        # V3.19e Opcja B — R1' observability only, zero behavior change.
        # Dla propozycji z synthetic pos=last_assigned_pickup (kurier w drodze
        # do restauracji X) loguj hypothetical metric: czy floor drive_min >=
        # pickup_ready_delta_min by zmienił scoring? Raw pos_source (przed
        # post_wave override L654-663), bo post_wave zaciera sygnał.
        _pos_raw = getattr(cs, "pos_source", None)
        v319e_r1_prime_hypothetical = None
        if _pos_raw == "last_assigned_pickup":
            _drive_m = round(drive_min, 1)
            _ready_delta = round(time_to_pickup_ready, 1) if time_to_pickup_ready is not None else 0.0
            v319e_r1_prime_hypothetical = {
                "pos_source_raw": _pos_raw,
                "drive_min": _drive_m,
                "pickup_ready_delta_min": _ready_delta,
                "would_trigger_floor": _drive_m < _ready_delta,
                "hypothetical_min_eta_min": max(_drive_m, _ready_delta),
            }

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
            "timing_gap_bonus": round(timing_gap_bonus, 2),
            "timing_gap_min": round(gap_min, 1),
            "time_to_pickup_ready_min": round(time_to_pickup_ready, 1),
            "free_at_utc": free_at_dt.isoformat() if free_at_dt is not None else None,
            "wave_bonus": round(wave_bonus, 2),
            "pos_source": pos_source_effective,
            "free_at_min": round(free_at_min, 1),
            "sla_minutes_used": sla_minutes,
            # F2.1b/F2.1c penalties. R8 aktywne od F2.1c (T_KUR propagation step 1-4).
            "bonus_r6_soft_pen": (
                round(bonus_r6_soft_pen, 2)
                if bonus_r6_soft_pen is not None else None
            ),
            "bonus_r1_soft_pen": round(bonus_r1_soft_pen, 2),
            "bonus_r5_soft_pen": round(bonus_r5_soft_pen, 2),
            "bonus_r8_soft_pen": round(bonus_r8_soft_pen, 2),
            "r1_violation_km": metrics.get("r1_violation_km", 0.0),
            "r5_violation_km": metrics.get("r5_violation_km", 0.0),
            "r8_violation_min": metrics.get("r8_violation_min", 0.0),
            "bonus_r9_stopover": round(bonus_r9_stopover, 2),
            "bonus_r9_wait_pen": round(bonus_r9_wait_pen, 2),
            "bonus_penalty_sum": round(bonus_penalty_sum, 2),
            # Transparency OPCJA A (2026-04-19): order_id → (restaurant, delivery_address)
            # mapping dla route section w telegram_approver. Per-courier bag snapshot.
            "bag_context": [
                {
                    "order_id": str(b.get("order_id") or ""),
                    "restaurant": b.get("restaurant"),
                    "delivery_address": b.get("delivery_address"),
                }
                for b in bag_raw
                if b.get("order_id")
            ],
            # V3.19e Opcja B: R1' observability (None gdy pos!=last_assigned_pickup).
            # Post 5 dni shadow: jeśli would_trigger_floor rate >5% → V3.19f floor impl.
            "v319e_r1_prime_hypothetical": v319e_r1_prime_hypothetical,
            # V3.19f: czas_kuriera 2-field passthrough z order_event do enriched_metrics.
            # Shadow serializer (Step 5) propaguje do shadow_decisions.jsonl dla offline
            # diagnostyki rozjazdu HH:MM vs ISO (sanity check w state layer).
            "czas_kuriera_warsaw": order_event.get("czas_kuriera_warsaw"),
            "czas_kuriera_hhmm": order_event.get("czas_kuriera_hhmm"),
            # V3.19h BUG-4: tier × pora cap soft penalty tracking.
            # tier_cap_used = "tier/pora/cap" string. violation = bag_after - cap (int).
            # bonus_bug4_cap_soft = progressive penalty applied do bonus_penalty_sum.
            "v319h_bug4_tier_cap_used": bug4_tier_cap_used,
            "v319h_bug4_cap_violation": bug4_cap_violation,
            "bonus_bug4_cap_soft": round(bonus_bug4_cap_soft, 2),
            # V3.19h BUG-1: SR bundle × drop_proximity_factor.
            # factor (1.0 same zone / 0.5 adjacent / 0.0 distant/Unknown).
            # sr_bundle_adjusted = bonus_l1 po mnożnik (oryginalny bonus_l1 w enriched).
            "v319h_bug1_drop_proximity_factor": v319h_bug1_drop_proximity_factor,
            "v319h_bug1_sr_bundle_adjusted": round(v319h_bug1_sr_bundle_adjusted, 2),
            # V3.19h BUG-2: wave continuation bonus tracking.
            # gap_min = pickup_new - free_at_dt (minutes). None gdy edge (no bag/pickup).
            # continuation_bonus = helper bug2_wave_continuation_bonus(gap_min).
            "v319h_bug2_interleave_gap_min": bug2_interleave_gap_min,
            "v319h_bug2_continuation_bonus": round(bonus_bug2_continuation, 2),
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

    # V3.16: no_gps + empty bag demotion (patrz _demote_blind_empty).
    feasible = _demote_blind_empty(feasible, order_id)

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
    # F2.1c: verdict PROPOSE (nie KOORD) — Telegram musi to zobaczyć, Adrian decyduje
    with_plan = [c for c in candidates if c.plan is not None]
    with_plan.sort(key=lambda c: (c.plan.sla_violations, c.plan.total_duration_min))
    if with_plan:
        best = with_plan[0]
        best.best_effort = True
        return PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"best_effort (0 feasible, best_violations={best.plan.sla_violations})",
            best=best,
            candidates=with_plan[:TOP_N_CANDIDATES],
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
        )

    # R29 SOLO fallback: zamiast SKIP — spróbuj przydzielić SOLO (pusty bag, ignoruje R1/R5/R8)
    solo_best = None
    solo_best_score = -999
    for cid, cs in fleet_snapshot.items():
        courier_pos = getattr(cs, "pos", None)
        if courier_pos is None:
            continue
        try:
            sv, sr, sm, sp = check_feasibility_v2(
                courier_pos=tuple(courier_pos),
                bag=[],  # pusty bag = solo
                new_order=new_order,
                now=now,
                sla_minutes=35,
            )
            if sv in ("YES", "MAYBE") and sp is not None:
                sc = sm.get("pickup_dist_km", 999)
                # Prostszy scoring: bliższy = lepszy
                solo_score = 100 - sc * 10
                if solo_score > solo_best_score:
                    solo_best_score = solo_score
                    solo_best = Candidate(
                        courier_id=cid,
                        name=getattr(cs, "name", cid),
                        score=round(solo_score, 2),
                        feasibility_verdict=sv,
                        feasibility_reason=f"solo_fallback ({sr})",
                        plan=sp,
                        metrics={**sm, "solo_fallback": True, "pos_source": getattr(cs, "pos_source", "no_gps")},
                    )
        except Exception:
            pass

    if solo_best is not None:
        return PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"solo_fallback (R1/R5/R8 ignored, fleet_n={len(candidates)})",
            best=solo_best,
            candidates=candidates,
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
        )

    # R29 absolutny fallback: nikt nie przechodzi nawet solo — KOORD
    return PipelineResult(
        order_id=order_id,
        verdict="KOORD",
        reason=f"no_solo_candidates (fleet_n={len(candidates)}) — wszyscy odrzuceni nawet solo",
        best=None,
        candidates=candidates,
        pickup_ready_at=pickup_ready_at,
        restaurant=restaurant,
        delivery_address=delivery_address,
    )
