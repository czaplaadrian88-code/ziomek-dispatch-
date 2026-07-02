"""route_simulator_v2 - Hybrid PDP-TSP per D19.

Strategy:
    bag_after_add <= 3 → brute-force permutations (optimal)
    bag_after_add >  3 → greedy insertion O(N²)

Constraints:
    - Pickup before delivery for new_order (if need_pickup).
    - Bag items assumed already picked_up (consistent with reconcile in v1).
    - Optional pickup_ready_at per order (prep_variance integration).
      If courier arrives before ready → wait at restaurant (only exception to D8).

No traffic multiplier — osrm_client handles fallback internally and reports
it per cell. Scoring/pricing layers read osrm_fallback_used from RoutePlanV2.

Pure function, zero state.
"""
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Optional
from dataclasses import dataclass, field
from itertools import permutations

from dispatch_v2 import osrm_client
from dispatch_v2.common import (
    ENABLE_DROP_TIME_CONSTRAINT,
    ENABLE_PICKED_UP_DROP_FLOOR,
    ENABLE_V319E_PRE_PICKUP_BAG,
)
import logging as _logging

_log = _logging.getLogger("route_simulator_v2")


DWELL_PICKUP_MIN = 1.0   # E1 2026-05-17: postój pod restauracją = czysta obsługa (chwyć torbę). Czekanie na jedzenie liczy pickup_ready_at osobno. Fallback gdy węzeł niestemplowany — produkcja używa C.dwell_for_tier.
DWELL_DROPOFF_MIN = 3.5  # 2026-05-17 kalibracja: GPS tier-1 dwell u klienta med 3.4-4.25 min (n=15-46). Było 2.0 (V3.27.3).
BRUTEFORCE_MAX_BAG_AFTER = 3  # per D19

# N5 krok 2 (2026-06-17): tolerancja punktualności committed (min) — kontekst per-decyzja.
# Ustawiana przez dispatch_pipeline z loadgov_ewma (strict 5 / loose 10 przy niedoborze)
# PRZED oceną kandydatów zlecenia (stała w obrębie zlecenia → bezpieczna dla thread-poola
# kandydatów). None = użyj strict default. Wzorzec jak _LOADGOV_STATE (module context).
_COMMITTED_PICKUP_TOL_MIN = None

def set_committed_pickup_tolerance(tol_min):
    """Pipeline ustawia tolerancję committed (min) per-decyzja. None → strict default."""
    global _COMMITTED_PICKUP_TOL_MIN
    _COMMITTED_PICKUP_TOL_MIN = tol_min

# V3.27 Bug Y tie-breaker (2026-04-25 wieczór): gdy 2+ permutacje mają
# |total_duration_diff| < 2 min od leader, secondary sort by first_drop arrival
# time ASC. Adrian's reasoning: "lepiej żeby jedno zamówienie jechało 3min,
# drugie 15min, niż jedno 13min, drugie 20min".
# Mental simulation #468508: post-X traffic_mult global ratio preserved (NIE
# rozdziela tied permutations). Tie-breaker rozdziela arbitrary tie-break.
V327_TIE_BREAKER_THRESHOLD_MIN = 2.0


def _first_drop_arrival_min(plan: "RoutePlanV2", now: datetime) -> float:
    """V3.27 Bug Y helper: time-from-now (min) do PIERWSZEGO drop w sekwencji.

    plan.sequence to delivery_order (only deliveries, w order). Pierwszy = sequence[0].
    Returns inf gdy plan ma brak sequence lub brak predicted_delivered_at[first].
    """
    if not plan or not plan.sequence or not plan.predicted_delivered_at:
        return float("inf")
    first_oid = plan.sequence[0]
    first_arrival = plan.predicted_delivered_at.get(first_oid)
    if first_arrival is None:
        return float("inf")
    if first_arrival.tzinfo is None:
        first_arrival = first_arrival.replace(tzinfo=timezone.utc)
    return (first_arrival - now).total_seconds() / 60.0


def _plan_trajectory_smoothness(
    plan: "RoutePlanV2",
    courier_pos: Optional[Tuple[float, float]],
    nodes_by_oid: Optional[Dict[str, dict]],
) -> Optional[float]:
    """P3-D6 2026-05-11: sequence-aware geometry tie-break helper.

    Trajectory smoothness = średnia cosine similarity między KOLEJNYMI legami trasy.
    Higher = straighter trajectory (less zigzag). Range [-1.0, 1.0].

    -  +1.0: wszystkie kolejne legi w tym samym kierunku (straight line)
    -   0.0: prostopadłe (90° turns)
    -  -1.0: opposite (180° turns, max zigzag)

    Wymaga ≥3 punktów (courier + ≥2 drops) żeby liczyć leg-to-leg cosine.
    Returns None gdy compute niemożliwy (caller fallback do legacy logic).
    """
    if courier_pos is None or nodes_by_oid is None or not plan.sequence:
        return None
    points: List[Tuple[float, float]] = [courier_pos]
    for oid in plan.sequence:
        node = nodes_by_oid.get(oid)
        if node is None:
            continue
        coords = node.get("coords")
        if coords is None:
            continue
        points.append(coords)
    if len(points) < 3:
        return None
    legs: List[Tuple[float, float]] = []
    for i in range(len(points) - 1):
        vx = points[i + 1][0] - points[i][0]
        vy = points[i + 1][1] - points[i][1]
        n = (vx * vx + vy * vy) ** 0.5
        if n > 1e-9:
            legs.append((vx / n, vy / n))
    if len(legs) < 2:
        return None
    cos_sum = 0.0
    for i in range(len(legs) - 1):
        cos_sum += legs[i][0] * legs[i + 1][0] + legs[i][1] * legs[i + 1][1]
    return cos_sum / (len(legs) - 1)


def _select_best_with_tie_breaker(
    plans: List["RoutePlanV2"],
    now: datetime,
    threshold_min: float = V327_TIE_BREAKER_THRESHOLD_MIN,
    nodes: Optional[List[dict]] = None,
) -> Optional["RoutePlanV2"]:
    """V3.27 Bug Y: select best plan z tie-breaker.

    1. Sort by primary key (sla_violations, total_duration_min) ASC
    2. Identify ties: plans with same sla_violations AND
       |total_duration_min - leader| < threshold_min
    3. If ≥2 ties AND ENABLE_V327_BUG_FIXES_BUNDLE flag True:
       3a. P3-D6 2026-05-11 NEW: secondary sort by trajectory_smoothness DESC
           (higher = less zigzag); requires `nodes` param (courier at [0]).
       3b. Tertiary fallback: first_drop_arrival_min ASC (legacy V3.27 Bug Y).
    4. Else return leader (legacy behavior).
    """
    if not plans:
        return None
    # Primary sort — O2 RE-SEQ (2026-06-27, ENABLE_O2_READY_ANCHOR_SWEEP ON) lub legacy
    # sla_violations (OFF). Flaga RAZ na selekcję (nie per plan). `_o2_primary(p)` = klucz
    # pierwszorzędny wspólny dla sortu I definicji ties niżej (spójność). OFF = byte-identyczne.
    from dispatch_v2 import common as _C_o2sel
    _o2_on = _C_o2sel.flag("ENABLE_O2_READY_ANCHOR_SWEEP",
                           getattr(_C_o2sel, "ENABLE_O2_READY_ANCHOR_SWEEP", False))
    if _o2_on:
        # cap-Z = TWARDY sufit świeżości niesionego (Opcja 3 Adriana): preferuj plany gdzie
        # max_carried_age ≤ Z; gdy ŻADEN się nie mieści (wymuszony carry) → cała pula (least-bad).
        _z = _C_o2sel.flag("O2_CAP_Z_MIN", getattr(_C_o2sel, "O2_CAP_Z_MIN", 35.0))
        _under_z = [p for p in plans if (p.max_carried_age or 0.0) <= _z]
        _pool = _under_z if _under_z else plans

        def _o2_primary(p):
            return p.o2_score if p.o2_score is not None else float("inf")
    else:
        _pool = plans

        def _o2_primary(p):
            return p.sla_violations
    plans_sorted = sorted(_pool, key=lambda p: (_o2_primary(p), p.total_duration_min))
    leader = plans_sorted[0]

    # Tie-breaker gated by flag (preserves baseline behavior gdy flag=False)
    try:
        from dispatch_v2.common import ENABLE_V327_BUG_FIXES_BUNDLE as _v327_flag
    except Exception:
        _v327_flag = False
    if not _v327_flag:
        return leader

    ties = [
        p for p in plans_sorted
        if _o2_primary(p) == _o2_primary(leader)
        and abs(p.total_duration_min - leader.total_duration_min) < threshold_min
    ]
    if len(ties) < 2:
        return leader

    # P3-D6 2026-05-11: geometry-aware tie-break — when `nodes` passed,
    # sort by trajectory smoothness DESC (higher = straighter trajectory).
    # Tech debt #29 + Lekcja #108: greedy fallback geometry-blind led to case
    # 472338 Ogniomistrz zigzag plan przejście. Sequence-aware metric (vs
    # set-invariant pairwise cosine) discriminuje permutacje.
    if nodes and len(nodes) >= 1 and nodes[0].get("kind") == "courier":
        courier_pos = nodes[0].get("coords")
        nodes_by_oid = {
            n["order_id"]: n
            for n in nodes
            if n.get("kind") == "delivery" and n.get("order_id") is not None
        }
        smoothness_vals = [
            (_plan_trajectory_smoothness(p, courier_pos, nodes_by_oid), p)
            for p in ties
        ]
        # Filter ties że computed smoothness (not None) — sort tylko po nich
        valid = [(s, p) for s, p in smoothness_vals if s is not None]
        if len(valid) >= 2:
            # Higher smoothness = better → DESC sort
            valid.sort(key=lambda sp: (-sp[0], _first_drop_arrival_min(sp[1], now)))
            return valid[0][1]
        # Fallback do legacy gdy <2 valid (np. wszystkie sequence z 1 drop)

    # V3.27 tie-breaker fallback: shortest first drop arrival ASC
    ties.sort(key=lambda p: _first_drop_arrival_min(p, now))
    return ties[0]


@dataclass
class OrderSim:
    order_id: str
    pickup_coords: Tuple[float, float]
    delivery_coords: Tuple[float, float]
    picked_up_at: Optional[datetime] = None
    status: str = "assigned"  # "assigned" | "picked_up"
    pickup_ready_at: Optional[datetime] = None


@dataclass
class RoutePlanV2:
    sequence: List[str]                       # delivery order (order_id)
    predicted_delivered_at: Dict[str, datetime]
    pickup_at: Dict[str, datetime]            # only for orders picked up during this plan
    total_duration_min: float
    strategy: str                             # "bruteforce" | "greedy"
    sla_violations: int
    osrm_fallback_used: bool
    # F2.2 C1 (2026-04-18): per-order elapsed pickup→drop in minutes.
    # None = incomplete computation → C2 gate MUST fail-closed (reject).
    per_order_delivery_times: Optional[Dict[str, float]] = None
    # V3.27.3 (2026-04-27): chain-aware arrival timestamp at pickup restaurant
    # PRZED wait jump (max with ready_at) i PRZED DWELL_PICKUP_MIN. Ground truth
    # raw drive arrival time. Used by wait_courier penalty (max(0, ready - arrival)).
    # Empty dict default — backward compat dla test fixtures z keyword args.
    arrival_at: Dict[str, datetime] = field(default_factory=dict)
    # O2 RE-SEQ (2026-06-27, ENABLE_O2_READY_ANCHOR_SWEEP, review 02.07): liczone ZAWSZE
    # (cheap, z per_order_delivery_times = ready-anchor); UŻYWANE tylko gdy flaga ON w
    # sweep/select. o2_score = overage (Σ max(0, age_ready − cap)) [FAZA 1; czas_late =
    # FAZA 2 osobno, brak deadline na OrderSim]. max_carried_age = max wieku NIESIONEGO
    # (status picked_up) do twardego cap-Z. None = nieliczone (brak per_order_times).
    o2_score: Optional[float] = None
    max_carried_age: Optional[float] = None
    # O2 cap-Z RESEQ (2026-07-02, ENABLE_O2_CAPZ_RESEQ, review 02.07): drive-only
    # minuty (Σ leg_min bez dwell/wait) — parytet z bundle_calib `m.drive_min`
    # (kolektor liczy detour = drive_min różnica). Liczone ZAWSZE w `_plan_from_sequence`
    # (tanie), UŻYWANE tylko przez cap-Z reseq gdy flaga ON. `o2_capz` = metryka obs
    # decyzji reseq (considered/applied/blocked_by_cap/detour_min/overage_saved_min),
    # ustawiana na WYBRANYM planie w `_capz_reseq_plan`; None gdy flaga OFF / brak reseq.
    drive_min: Optional[float] = None
    o2_capz: Optional[dict] = None


def simulate_bag_route_v2(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: Optional[datetime] = None,
    sla_minutes: int = 35,
    base_sequence: Optional[List[str]] = None,
    earliest_departure: Optional[datetime] = None,
    dwell_pickup: float = DWELL_PICKUP_MIN,
    dwell_dropoff: float = DWELL_DROPOFF_MIN,
    drive_speed_mult: float = 1.0,
) -> RoutePlanV2:
    """Hybrid simulator. Never returns None (osrm_client has fallback).

    V3.19d: gdy `base_sequence` podane (lista bag order_ids w preferowanej
    kolejności) — bag dropoffs są lockowane w tej kolejności; simulator
    iteruje tylko pozycje insertion new_order zamiast pełnego TSP. Jeśli
    base_sequence jest mismatched z bag → fallback do fresh TSP.

    V3.28 ETAP 2 (2026-05-08): earliest_departure wymusza start planu od
    późniejszego momentu (np. shift_start dla pre_shift kuriera). Gdy
    earliest_departure > now, downstream timestamps (pickup_at, delivered_at)
    bazują na earliest_departure zamiast real now — eliminuje fikcyjny plan
    "kurier startuje teraz" dla kuriera który zaczyna shift za N min.
    Flag-gated w feasibility caller (ENABLE_PRE_SHIFT_DEPARTURE_CLAMP).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if earliest_departure is not None:
        if earliest_departure.tzinfo is None:
            earliest_departure = earliest_departure.replace(tzinfo=timezone.utc)
        if earliest_departure > now:
            now = earliest_departure

    need_pickup = new_order.status != "picked_up"

    # Build node list: [courier, *bag_nodes, (new_pickup?), new_delivery]
    # V3.19e: dla bag items z status!="picked_up" (pickup jeszcze nie nastąpił),
    # simulator dodaje pickup-node przed delivery-node. Gated przez flagę.
    #
    # V3.26 Fix 7 (2026-04-25): same-restaurant grouping pre-pass. Gdy flag
    # ENABLE_V326_SAME_RESTAURANT_GROUPING=True, multiple pending bag pickups z
    # tej samej restauracji compatible (czas ±5min + drop quadrants) merge'ują
    # do super-pickup node z attribute group_oids (list). _simulate_sequence
    # zapisze pickup_at[oid] dla każdego oid z grupy przy single visit.
    nodes: List[dict] = [{"kind": "courier", "coords": courier_pos, "order_id": None, "ref": None}]
    bag_delivery_idxs: List[int] = []
    bag_pickup_idxs_by_oid: Dict[str, int] = {}  # V3.19e: {oid: pickup_node_idx} only dla pending bag items

    # Fix 7 grouping decision
    _use_grouping = False
    _grouped_pending_pickups: Dict[Tuple[str, ...], List] = {}  # {group_oids_tuple: [orders]}
    try:
        from dispatch_v2 import common as _C7
        if getattr(_C7, "ENABLE_V326_SAME_RESTAURANT_GROUPING", False):
            from dispatch_v2.same_restaurant_grouper import (
                group_orders_by_restaurant,
                GroupedOrders,
            )
            pending_bag = [
                o for o in bag
                if ENABLE_V319E_PRE_PICKUP_BAG and getattr(o, "status", "picked_up") != "picked_up"
            ]
            if len(pending_bag) >= 2:
                groups = group_orders_by_restaurant(
                    pending_bag,
                    _C7.drop_zone_from_address,
                    _C7.BIALYSTOK_DISTRICT_ADJACENCY,
                    time_tolerance_min=float(getattr(_C7, "V326_GROUPING_TIME_TOLERANCE_MIN", 5.0)),
                )
                # Map order_id → group key (tuple of oids)
                _group_oid_map: Dict[str, Tuple[str, ...]] = {}
                for g in groups:
                    if isinstance(g, GroupedOrders) and len(g.orders) >= 2:
                        oids = tuple(getattr(o, "order_id", "") for o in g.orders)
                        _grouped_pending_pickups[oids] = g.orders
                        for oid in oids:
                            _group_oid_map[oid] = oids
                _use_grouping = bool(_grouped_pending_pickups)
                if _use_grouping:
                    # Build super-pickup nodes per group + individual pickup nodes for ungrouped
                    _emitted_groups: set = set()
                    for o in bag:
                        if not (ENABLE_V319E_PRE_PICKUP_BAG and
                                getattr(o, "status", "picked_up") != "picked_up"):
                            # picked_up bag — only delivery node (legacy)
                            nodes.append({
                                "kind": "delivery", "coords": o.delivery_coords,
                                "order_id": o.order_id, "ref": o,
                            })
                            bag_delivery_idxs.append(len(nodes) - 1)
                            continue
                        oid = o.order_id
                        if oid in _group_oid_map:
                            grp_key = _group_oid_map[oid]
                            if grp_key not in _emitted_groups:
                                # Emit super-pickup once per group
                                grp_orders = _grouped_pending_pickups[grp_key]
                                seed = grp_orders[0]
                                nodes.append({
                                    "kind": "pickup",
                                    "coords": seed.pickup_coords,
                                    "order_id": None,  # super-pickup — no single oid
                                    "ref": seed,
                                    "group_oids": list(grp_key),  # Fix 7 marker
                                })
                                super_pickup_idx = len(nodes) - 1
                                for grp_oid in grp_key:
                                    bag_pickup_idxs_by_oid[grp_oid] = super_pickup_idx
                                _emitted_groups.add(grp_key)
                            # delivery node per oid (always individual)
                            nodes.append({
                                "kind": "delivery", "coords": o.delivery_coords,
                                "order_id": o.order_id, "ref": o,
                            })
                            bag_delivery_idxs.append(len(nodes) - 1)
                        else:
                            # Singleton pending — legacy nodes (1 pickup + 1 delivery)
                            nodes.append({
                                "kind": "pickup", "coords": o.pickup_coords,
                                "order_id": o.order_id, "ref": o,
                            })
                            bag_pickup_idxs_by_oid[o.order_id] = len(nodes) - 1
                            nodes.append({
                                "kind": "delivery", "coords": o.delivery_coords,
                                "order_id": o.order_id, "ref": o,
                            })
                            bag_delivery_idxs.append(len(nodes) - 1)
    except Exception as _e7:
        _log.warning(f"Fix 7 grouping disabled (exception): {type(_e7).__name__}: {_e7}")
        _use_grouping = False

    if not _use_grouping:
        # Legacy nodes building (zero behavior change vs pre-Fix 7)
        for o in bag:
            if ENABLE_V319E_PRE_PICKUP_BAG and getattr(o, "status", "picked_up") != "picked_up":
                nodes.append({
                    "kind": "pickup", "coords": o.pickup_coords,
                    "order_id": o.order_id, "ref": o,
                })
                bag_pickup_idxs_by_oid[o.order_id] = len(nodes) - 1
            nodes.append({"kind": "delivery", "coords": o.delivery_coords, "order_id": o.order_id, "ref": o})
            bag_delivery_idxs.append(len(nodes) - 1)

    new_pickup_idx: Optional[int] = None
    if need_pickup:
        nodes.append({"kind": "pickup", "coords": new_order.pickup_coords, "order_id": new_order.order_id, "ref": new_order})
        new_pickup_idx = len(nodes) - 1
    nodes.append({"kind": "delivery", "coords": new_order.delivery_coords, "order_id": new_order.order_id, "ref": new_order})
    new_delivery_idx = len(nodes) - 1

    points = [n["coords"] for n in nodes]
    matrix = osrm_client.table(points, points)
    fallback_used = any(
        bool((cell or {}).get("osrm_fallback")) for row in matrix for cell in row
    )

    def leg_min(i: int, j: int) -> float:
        cell = matrix[i][j]
        if cell is None:
            return 9999.0  # sentinel fallback — NIE skalowany tempem
        dur_s = cell.get("duration_s") or 0
        # Sprint 3 (2026-05-17): tier-aware — mnożnik tempa kuriera na nodze jazdy.
        return (dur_s / 60.0) * drive_speed_mult

    # V3.19d: sticky sequence path — bag order locked by saved plan.
    # Validation: set(base_sequence) == {bag order_ids}. Mismatch → fallback.
    # V3.19e: fallback do fresh TSP gdy bag ma pending items (pickup nodes
    # wymagają permutacji — nie można lock'ować sekwencji dropów bez
    # uwzględnienia pickup-positions).
    use_sticky = False
    sticky_bag_idxs: Optional[List[int]] = None
    has_pending_bag = bool(bag_pickup_idxs_by_oid)
    if base_sequence is not None and bag and not has_pending_bag:
        bag_oid_to_idx = {o.order_id: bag_delivery_idxs[i] for i, o in enumerate(bag)}
        if set(base_sequence) == set(bag_oid_to_idx.keys()) \
                and len(base_sequence) == len(bag_oid_to_idx):
            sticky_bag_idxs = [bag_oid_to_idx[oid] for oid in base_sequence]
            use_sticky = True

    bag_after_add = len(bag) + 1

    # V3.26 Fix 6 (2026-04-25): OR-Tools TSP solver za flagą ENABLE_V326_OR_TOOLS_TSP.
    # Replaces bruteforce + greedy z industry-standard constraint solver.
    # Time-bounded 200ms per kandydat. Fallback do greedy gdy solver INFEASIBLE
    # (rare — np. tight time windows).
    #
    # V3.27 Phase 1A+G (2026-04-25 wieczór): skip OR-Tools dla trivial cases
    # (bag_after_add < V327_MIN_OR_TOOLS_BAG_AFTER). OR-Tools hits time_limit
    # ceiling 200ms regardless of problem size — bruteforce z 1-24 perms instant.
    use_ortools = False
    try:
        from dispatch_v2.common import (
            ENABLE_V326_OR_TOOLS_TSP as _ot_flag,
            V327_MIN_OR_TOOLS_BAG_AFTER as _v327_min_ot,
        )
        use_ortools = bool(_ot_flag) and bag_after_add >= int(_v327_min_ot)
    except Exception:
        use_ortools = False

    # Tier-aware DWELL (2026-05-17): stempel wartości dwell na każdym węźle.
    # Dzięki temu dwell podróżuje z `nodes` do wszystkich plannerów i do
    # _simulate_sequence / _dwell_min_for_arriving BEZ zmiany ich sygnatur.
    # Route-constant — cała trasa to jeden kurier = jeden tier.
    for _n in nodes:
        _n["dwell_pickup"] = dwell_pickup
        _n["dwell_dropoff"] = dwell_dropoff

    if use_sticky:
        plan = _sticky_sequence_plan(
            nodes, leg_min, sticky_bag_idxs,
            new_pickup_idx, new_delivery_idx,
            new_order, bag, now, sla_minutes,
        )
        plan.strategy = "sticky"
    elif use_ortools:
        plan = _ortools_plan(
            nodes, leg_min, bag_delivery_idxs,
            bag_pickup_idxs_by_oid,
            new_pickup_idx, new_delivery_idx,
            new_order, bag, now, sla_minutes,
        )
        if plan is None:
            # OR-Tools fallback: gdy INFEASIBLE → greedy safety net
            _log.warning(
                f"OR-Tools INFEASIBLE for bag_size={len(bag)}, "
                f"falling back to greedy"
            )
            plan = _greedy_plan(
                nodes, leg_min, bag_delivery_idxs,
                bag_pickup_idxs_by_oid,
                new_pickup_idx, new_delivery_idx,
                new_order, bag, now, sla_minutes,
            )
            plan.strategy = "greedy_fallback"
        else:
            # V3.27.6 FIX 2c (2026-04-28): _ortools_plan może pre-set strategy
            # do "ortools_rejected_v3274" gdy frozen ck violation detected.
            # Respect pre-set value, NIE override (Adrian's eyeball signal).
            if not plan.strategy:
                plan.strategy = "ortools"
    elif bag_after_add <= BRUTEFORCE_MAX_BAG_AFTER:
        plan = _bruteforce_plan(
            nodes, leg_min, bag_delivery_idxs,
            bag_pickup_idxs_by_oid,
            new_pickup_idx, new_delivery_idx,
            new_order, bag, now, sla_minutes,
        )
        plan.strategy = "bruteforce"
    else:
        plan = _greedy_plan(
            nodes, leg_min, bag_delivery_idxs,
            bag_pickup_idxs_by_oid,
            new_pickup_idx, new_delivery_idx,
            new_order, bag, now, sla_minutes,
        )
        plan.strategy = "greedy"

    # O2 cap-Z RESEQ (2026-07-02, ENABLE_O2_CAPZ_RESEQ default OFF): OBOK O2_READY_ANCHOR_SWEEP.
    # Wołane na WYBRANYM planie KAŻDEJ strategii (ortools/greedy/bruteforce/greedy_fallback)
    # = JEDNO źródło reguły, OBIE ścieżki selekcji pokryte (finding rst-greedy-step15-not-o2).
    # Tylko worki (len(bag)>=1); sticky (locked saved-plan) pomijamy (unik churnu z plan_manager).
    # OFF = plan bez zmian (early return w helperze). Konsumenci trójki (feasibility SLA/R6 gate,
    # plan_recheck._sweep) dziedziczą reseq'owaną sekwencję przez TEN return.
    if plan is not None and len(bag) >= 1 and getattr(plan, "strategy", "") != "sticky":
        plan, _ = _capz_reseq_plan(
            plan, nodes, leg_min, bag_delivery_idxs, bag_pickup_idxs_by_oid,
            new_pickup_idx, new_delivery_idx, new_order, bag, now, sla_minutes)

    plan.osrm_fallback_used = fallback_used
    return plan


def _sticky_sequence_plan(
    nodes, leg_min, sticky_bag_idxs,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    """V3.19d: bag dropoffs lockowane w `sticky_bag_idxs` kolejności.
    Iteruje tylko pozycje insertion new_order. lock_first: gdy bag niepusty,
    pickup/dropoff new_order NIE może być na pozycji 0 (kurier z jedzeniem
    w bagu nie zawraca do nowej restauracji).
    """
    lock_first = bool(sticky_bag_idxs)
    n = len(sticky_bag_idxs)

    # V3.27 Bug Y tie-breaker: collect all valid sticky insertions, post-process.
    all_sticky_plans: List[RoutePlanV2] = []
    for d_pos in range(n + 1):
        pickup_positions = [None] if new_pickup_idx is None else list(range(0, d_pos + 1))
        for p_pos in pickup_positions:
            if lock_first and p_pos == 0:
                continue
            if lock_first and p_pos is None and d_pos == 0:
                continue
            candidate = list(sticky_bag_idxs)
            candidate.insert(d_pos, new_delivery_idx)
            if p_pos is not None:
                candidate.insert(p_pos, new_pickup_idx)
            plan = _plan_from_sequence(candidate, nodes, leg_min, new_order, bag, now, sla_minutes)
            all_sticky_plans.append(plan)
    return _select_best_with_tie_breaker(all_sticky_plans, now, nodes=nodes)


# ---- internals ----

def _simulate_sequence(
    nodes: List[dict],
    leg_min,
    seq: List[int],
    now: datetime,
) -> Tuple[float, Dict[str, datetime], Dict[str, datetime], Dict[str, datetime]]:
    """Walk nodes in order. Returns (total_min, delivered_at, pickup_at, arrival_at).

    V3.27.3 (2026-04-27): arrival_at[oid] = raw drive arrival timestamp at pickup
    restaurant PRZED wait jump (max with ready_at) i PRZED DWELL_PICKUP_MIN.
    Used by wait_courier penalty: wait_min = max(0, ready_at - arrival_at).
    """
    current = 0  # courier
    t = now
    delivered_at: Dict[str, datetime] = {}
    pickup_at: Dict[str, datetime] = {}
    arrival_at: Dict[str, datetime] = {}
    for idx in seq:
        t = t + timedelta(minutes=leg_min(current, idx))
        node = nodes[idx]
        if node["kind"] == "pickup":
            # V3.27.3: capture raw drive arrival PRZED wait + dwell
            arrival_t = t
            ready = getattr(node["ref"], "pickup_ready_at", None)
            if ready is not None:
                if ready.tzinfo is None:
                    ready = ready.replace(tzinfo=timezone.utc)
                ready_utc = ready.astimezone(timezone.utc)
                if t < ready_utc:
                    t = ready_utc  # wait at restaurant (prep_variance)
            t = t + timedelta(minutes=node.get("dwell_pickup", DWELL_PICKUP_MIN))
            # V3.26 Fix 7: super-pickup z group_oids zapisuje pickup_at dla
            # WSZYSTKICH oidów w grupie przy single visit (kurier zabiera all
            # orders na raz z restauracji). Single dwell pickup dla całej grupy.
            group_oids = node.get("group_oids")
            if group_oids:
                for grp_oid in group_oids:
                    pickup_at[grp_oid] = t
                    arrival_at[grp_oid] = arrival_t
            else:
                pickup_at[node["order_id"]] = t
                arrival_at[node["order_id"]] = arrival_t
        elif node["kind"] == "delivery":
            # V3.18 Bug 1: dla bag itemów z status != "picked_up" (kurier jeszcze
            # nie odebrał z restauracji) predicted drop NIE MOŻE być przed
            # pickup_ready_at + DWELL_PICKUP_MIN (fizycznie niemożliwe).
            # Flag ENABLE_DROP_TIME_CONSTRAINT=False → legacy zachowanie (no-op).
            ref = node["ref"]
            ref_status = getattr(ref, "status", "assigned") if ref is not None else "assigned"
            if ENABLE_DROP_TIME_CONSTRAINT and ref is not None:
                ref_pra = getattr(ref, "pickup_ready_at", None)
                if ref_status != "picked_up" and ref_pra is not None:
                    if ref_pra.tzinfo is None:
                        ref_pra = ref_pra.replace(tzinfo=timezone.utc)
                    ref_pra_utc = ref_pra.astimezone(timezone.utc)
                    # Minimalny realny drop = pickup_ready + pickup dwell
                    # (drive from restaurant to drop nie znany tu, ale >=0).
                    min_drop_possible = ref_pra_utc + timedelta(
                        minutes=node.get("dwell_pickup", DWELL_PICKUP_MIN))
                    if t < min_drop_possible:
                        t = min_drop_possible
            # V3.19a: symetryczny floor dla picked_up — kurier już odebrał z
            # restauracji, więc minimalny realny drop = picked_up_at +
            # osrm_drive(pickup→drop) + DWELL_DROPOFF_MIN. Adresuje R1:
            # courier_resolver ustawia synthetic pos = drop_coords dla picked_up
            # bag, więc leg_min(courier, drop) ≈ 0 → t ≈ now bez floora.
            if ENABLE_PICKED_UP_DROP_FLOOR and ref is not None and ref_status == "picked_up":
                ref_picked = getattr(ref, "picked_up_at", None)
                ref_pickup = getattr(ref, "pickup_coords", None)
                ref_drop = getattr(ref, "delivery_coords", None)
                if (
                    ref_picked is not None
                    and ref_pickup and ref_drop
                    and tuple(ref_pickup) != (0.0, 0.0)
                    and tuple(ref_drop) != (0.0, 0.0)
                ):
                    if ref_picked.tzinfo is None:
                        ref_picked = ref_picked.replace(tzinfo=timezone.utc)
                    ref_picked_utc = ref_picked.astimezone(timezone.utc)
                    osrm_result = osrm_client.route(tuple(ref_pickup), tuple(ref_drop))
                    drive_s = (osrm_result or {}).get("duration_s") or 0
                    floor_t = (
                        ref_picked_utc
                        + timedelta(seconds=drive_s)
                        + timedelta(minutes=node.get("dwell_dropoff", DWELL_DROPOFF_MIN))
                    )
                    if t < floor_t:
                        t = floor_t
            t = t + timedelta(minutes=node.get("dwell_dropoff", DWELL_DROPOFF_MIN))
            delivered_at[node["order_id"]] = t
        current = idx
    total_min = (t - now).total_seconds() / 60.0
    return total_min, delivered_at, pickup_at, arrival_at


def _count_sla_violations(
    delivered_at: Dict[str, datetime],
    pickup_at: Dict[str, datetime],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: datetime,
    sla_minutes: int,
) -> int:
    # S1 (2026-07-02): kotwica NOW z JEDNEGO źródła (sla_anchor) za flagą
    # ENABLE_SLA_ANCHOR_UNIFIED. OFF = inline bajt-w-bajt; ON = ta sama arytmetyka
    # przez `sla_anchor.now_anchor` (bliźniak z feasibility SLA-loop = to samo źródło).
    from dispatch_v2 import common as _C_sa
    _unified = _C_sa.flag("ENABLE_SLA_ANCHOR_UNIFIED",
                          getattr(_C_sa, "ENABLE_SLA_ANCHOR_UNIFIED", False))
    v = 0
    for o in list(bag) + [new_order]:
        pred = delivered_at.get(o.order_id)
        if pred is None:
            continue
        if _unified:
            from dispatch_v2 import sla_anchor as _SA
            # Krok 2 (2026-07-02, ENABLE_SLA_GATE_READY_ANCHOR, finding feas-r6-sla-anchor-gap):
            # kotwica SLA NOW→READY (od gotowości jedzenia) — WYŁĄCZNIE przez źródło sla_anchor
            # (kind='ready'). Działa tylko na ścieżce unified (S1 ON). OFF = NOW-anchor bez zmian.
            _ready_gate = _C_sa.flag("ENABLE_SLA_GATE_READY_ANCHOR",
                                     getattr(_C_sa, "ENABLE_SLA_GATE_READY_ANCHOR", False))
            if _ready_gate:
                pu = _SA.anchor(o, kind="ready", now=now, plan_pickup_at=pickup_at,
                                is_new=(o is new_order))
            else:
                pu = _SA.now_anchor(o, pickup_at, now)
            elapsed = _SA.elapsed_min(pred, pu)
        else:
            if o.order_id in pickup_at:
                pu = pickup_at[o.order_id]
            elif o.picked_up_at is not None:
                pu = o.picked_up_at
                if pu.tzinfo is None:
                    pu = pu.replace(tzinfo=timezone.utc)
                pu = pu.astimezone(timezone.utc)
            else:
                pu = now
            elapsed = (pred - pu).total_seconds() / 60.0
        if elapsed > sla_minutes:
            v += 1
    return v


def r6_thermal_anchor(order, is_new, plan_pickup_at, now):
    """INV-R6-ANCHOR-CONSISTENCY (audyt 2026-06-24, spec odporności §6.A4): JEDNO źródło
    kotwicy termicznej R6 — route_simulator (`_compute_per_order_delivery_minutes`) ORAZ
    feasibility (`check_feasibility_v2`) MUSZĄ liczyć R6 tym samym anchorem, inaczej twarda
    bramka ≠ ETA/scoring (dryft POD vs gate). Reguła (doktryna Adriana 2026-05-10):
      - picked_up (picked_up_at lub status=='picked_up', NIE new) → picked_up_at (realny pickup),
      - inaczej (w tym new_order) → pickup_ready_at (jedzenie czeka od gotowości) → tsp pickup_at → now.
    Zwraca (anchor_utc, src, is_picked). prep_bias (feasibility-only, gate-stricter) NAKŁADA
    wołający PO — to świadoma asymetria (bramka ostrzejsza), nie dryft kotwicy bazowej."""
    is_picked = (not is_new) and (
        getattr(order, "picked_up_at", None) is not None
        or getattr(order, "status", None) == "picked_up")
    anchor = None
    src = "now"
    if is_picked:
        pu = getattr(order, "picked_up_at", None)
        if pu is not None:
            anchor = (pu if pu.tzinfo else pu.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            src = "picked_up_at"
    else:
        pra = getattr(order, "pickup_ready_at", None)
        if pra is not None:
            anchor = (pra if pra.tzinfo else pra.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            src = "pickup_ready_at"
        elif plan_pickup_at and getattr(order, "order_id", None) in plan_pickup_at:
            pu = plan_pickup_at[order.order_id]
            anchor = (pu if pu.tzinfo else pu.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            src = "tsp_pickup_at"
    if anchor is None:
        anchor = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return anchor, src, is_picked


def _compute_per_order_delivery_minutes(
    delivered_at: Dict[str, datetime],
    pickup_at: Dict[str, datetime],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: datetime,
) -> Optional[Dict[str, float]]:
    """Per-order elapsed thermal_anchor→drop in minutes (F2.2 C1 + V3.28 ANCHOR FIX).

    Doktryna Adriana 2026-05-10: thermal carry liczona od momentu gdy jedzenie
    JEST GOTOWE w restauracji (pickup_ready_at) — nie od TSP-projected pickup.
    TSP może planować pickup later (kurier zajęty), maskując 70+ min real thermal.

    Anchor selection:
      - new_order: pickup_ready_at (real ready time)
      - bag order, NOT picked_up: pickup_ready_at (jedzenie czeka od ready)
      - bag order, ALREADY picked_up: picked_up_at (real pickup, soft tracking)
    Fallback: tsp pickup_at → now (last resort).

    Returns None if any order lacks delivered_at (fail-closed for C2 hard gate).
    """
    result: Dict[str, float] = {}
    for o in list(bag) + [new_order]:
        pred = delivered_at.get(o.order_id)
        if pred is None:
            return None
        if pred.tzinfo is None:
            pred = pred.replace(tzinfo=timezone.utc)
        is_new = o is new_order
        # INV-R6-ANCHOR-CONSISTENCY: wspólna kotwica (1:1 z feasibility check_feasibility_v2).
        anchor, _anchor_src, _is_picked = r6_thermal_anchor(o, is_new, pickup_at, now)
        result[o.order_id] = round((pred - anchor).total_seconds() / 60.0, 2)
    return result


def _compute_o2_metrics(per_order_times, bag, new_order, cap_min):
    """O2 FAZA 1 (2026-06-27): z `per_order_delivery_times` (ready-anchor = r6_thermal_anchor,
    JUŻ policzone) licz:
      o2_score        = overage = Σ max(0, age_ready − cap_min)   [CIĄGŁY objektyw świeżości]
      max_carried_age = max wieku po NIESIONYCH (status picked_up) [do twardego cap-Z]
    Wzór max_carried_age 1:1 z bundle_calib._max_carried_age (parytet). czas_late = FAZA 2
    (brak deadline na OrderSim). Zwraca (None, None) gdy brak per_order_times (fail-closed
    jak C2 gate). Czysta arytmetyka, zero I/O — bezpieczne compute-always per plan."""
    if not per_order_times:
        return None, None
    picked = {
        getattr(o, "order_id", None) for o in bag
        if getattr(o, "picked_up_at", None) is not None
        or getattr(o, "status", None) == "picked_up"
    }
    overage = 0.0
    carried_ages = []
    for oid, age in per_order_times.items():
        if age is None:
            continue
        overage += max(0.0, age - cap_min)
        if oid in picked:
            carried_ages.append(age)
    max_carried = max(carried_ages) if carried_ages else 0.0
    return round(overage, 1), round(max_carried, 1)


def _plan_from_sequence(
    seq: List[int],
    nodes: List[dict],
    leg_min,
    new_order: OrderSim,
    bag: List[OrderSim],
    now: datetime,
    sla_minutes: int,
) -> RoutePlanV2:
    total, delivered_at, pickup_at, arrival_at = _simulate_sequence(nodes, leg_min, seq, now)
    violations = _count_sla_violations(delivered_at, pickup_at, bag, new_order, now, sla_minutes)
    per_order_times = _compute_per_order_delivery_minutes(delivered_at, pickup_at, bag, new_order, now)
    # O2 FAZA 1 (2026-06-27): compute-always (cheap, per_order_times = ready-anchor już liczony).
    # cap z MODULE-const (env-overridable, ustawiany przy flipie 02.07) — NIE C.flag, by uniknąć
    # I/O per plan w bruteforce. UŻYWANE tylko gdy ENABLE_O2_READY_ANCHOR_SWEEP ON (sweep/select).
    from dispatch_v2 import common as _C_o2
    _o2_score, _max_carried = _compute_o2_metrics(
        per_order_times, bag, new_order,
        getattr(_C_o2, "O2_OVERAGE_CAP_MIN", 35.0))
    delivery_order = [nodes[i]["order_id"] for i in seq if nodes[i]["kind"] == "delivery"]
    # O2 cap-Z RESEQ (2026-07-02): drive-only minuty (Σ leg_min, bez dwell/wait) —
    # kotwica DETOURU 1:1 z bundle_calib `_walk_calib` `drive` (kolektor: detour =
    # drive_min różnica). Tani dodatkowy przebieg; UŻYWANE tylko przez cap-Z reseq.
    _drive_only = 0.0
    _cur = 0
    for _idx in seq:
        _drive_only += leg_min(_cur, _idx)
        _cur = _idx
    return RoutePlanV2(
        sequence=delivery_order,
        predicted_delivered_at=delivered_at,
        pickup_at=pickup_at,
        total_duration_min=round(total, 1),
        strategy="",
        sla_violations=violations,
        osrm_fallback_used=False,
        per_order_delivery_times=per_order_times,
        arrival_at=arrival_at,
        o2_score=_o2_score,
        max_carried_age=_max_carried,
        drive_min=round(_drive_only, 2),
    )


def _is_paczka_ordersim(o) -> bool:
    """Paczka-exempt dla cap-Z reseq — SPÓJNE z feasibility_v2._is_paczka_sim
    (`common.is_paczka_order` na address_id/order_type), gated TĄ SAMĄ flagą
    `ENABLE_PACZKA_R6_THERMAL_EXEMPT`. OrderSim bez tych atrybutów → False
    (jedzeniówka, jak dziś). Finding feas-o2-paczka-blind: paczki bez termiki
    → nie liczą się do świeżości/cap-Z. Jawne (nie milczące pominięcie)."""
    from dispatch_v2 import common as _Cp
    if not _Cp.flag("ENABLE_PACZKA_R6_THERMAL_EXEMPT",
                    getattr(_Cp, "ENABLE_PACZKA_R6_THERMAL_EXEMPT", False)):
        return False
    try:
        return bool(_Cp.is_paczka_order({
            "address_id": getattr(o, "address_id", None),
            "order_type": getattr(o, "order_type", None),
        }))
    except Exception:
        return False


def _capz_bag_metrics(plan, bag, new_order, cap_min):
    """(overage_food, max_carried_food) z `plan.per_order_delivery_times` (ready-anchor,
    już policzone), WYŁĄCZAJĄC paczki (finding feas-o2-paczka-blind). NIE dotyka
    `_compute_o2_metrics` (istniejąca ENABLE_O2_READY_ANCHOR_SWEEP nietknięta) — osobne,
    paczka-świadome liczenie WYŁĄCZNIE dla cap-Z reseq. Wzór 1:1 z bundle_calib._max_carried_age
    (max wieku NIESIONYCH picked_up) + overage (Σ max(0, age−cap)). (None,None) gdy brak per_order."""
    pt = plan.per_order_delivery_times
    if not pt:
        return None, None
    paczka = {getattr(o, "order_id", None) for o in list(bag) + [new_order]
              if _is_paczka_ordersim(o)}
    picked = {getattr(o, "order_id", None) for o in bag
              if getattr(o, "picked_up_at", None) is not None
              or getattr(o, "status", None) == "picked_up"}
    overage = 0.0
    carried = []
    for oid, age in pt.items():
        if age is None or oid in paczka:
            continue
        overage += max(0.0, age - cap_min)
        if oid in picked:
            carried.append(age)
    return round(overage, 1), (round(max(carried), 1) if carried else 0.0)


def _capz_reseq_plan(baseline_plan, nodes, leg_min, bag_delivery_idxs,
                     bag_pickup_idxs_by_oid, new_pickup_idx, new_delivery_idx,
                     new_order, bag, now, sla_minutes):
    """Krok 1 O2 cap-Z reseq (ENABLE_O2_CAPZ_RESEQ, default OFF) — OBOK istniejącej
    ENABLE_O2_READY_ANCHOR_SWEEP (której zachowania NIE zmienia; ta = surowy sweep,
    reseq = wąska reguła Opcji 3 Adriana). Guarded swap: preferuj przeplot zmniejszający
    overage świeżości TYLKO gdy JEDNOCZEŚNIE:
      (a) drive-detour vs baseline ≤ O2_CAPZ_DETOUR_MAX_MIN  (z under_z review 02.07, p90 Z=20 ≈ 7.93),
      (b) max wiek NIESIONEJ jedzeniówki ≤ O2_CAPZ_Z_MIN (=20, rekom. review = max ochrona niesionego),
      (c) overage niższy od baseline o ≥ O2_CAPZ_MIN_GAIN_MIN (argmin overage, materialność jak review),
      (d) sla_violations NIE większe (SOFT nie osłabia HARD; carried-first zachowane przez lock_first
          enumeracji + brak wzrostu breachy).
    Brak kandydata pod capami → baseline BEZ ZMIAN. OFF = baseline byte-identyczny (early return).
    Enumeracja `_enumerate_valid_plans` (ta sama pula co bruteforce, carried-first via lock_first)
    → pokrywa OBIE ścieżki selekcji (greedy+ortools) bo wołane z ogona simulate_bag_route_v2 na
    WYBRANYM planie każdej strategii (finding rst-greedy-step15-not-o2). Zwraca (plan, metric|None)."""
    from dispatch_v2 import common as _Cz
    if not _Cz.flag("ENABLE_O2_CAPZ_RESEQ", getattr(_Cz, "ENABLE_O2_CAPZ_RESEQ", False)):
        return baseline_plan, None
    metric = {"considered": 0, "applied": 0, "blocked_by_cap": 0,
              "detour_min": 0.0, "overage_saved_min": 0.0}
    cap_min = float(getattr(_Cz, "O2_OVERAGE_CAP_MIN", 35.0))
    z_min = float(_Cz.flag("O2_CAPZ_Z_MIN", getattr(_Cz, "O2_CAPZ_Z_MIN", 20.0)))
    detour_max = float(_Cz.flag("O2_CAPZ_DETOUR_MAX_MIN",
                                getattr(_Cz, "O2_CAPZ_DETOUR_MAX_MIN", 8.0)))
    min_gain = float(_Cz.flag("O2_CAPZ_MIN_GAIN_MIN",
                              getattr(_Cz, "O2_CAPZ_MIN_GAIN_MIN", 2.0)))
    max_stops = int(getattr(_Cz, "O2_CAPZ_MAX_STOPS", 8))
    # size guard — enumeracja permutacji wykładnicza; poza limitem = kolejność BEZ ZMIAN
    # (kolektor bundle_calib: >5 zleceń = heurystyka; silnik konserwatywnie keep = ≤ review improved).
    n_stops = len(bag_delivery_idxs) + len(set(bag_pickup_idxs_by_oid.values())) + 1
    if new_pickup_idx is not None:
        n_stops += 1
    if n_stops > max_stops:
        baseline_plan.o2_capz = metric
        return baseline_plan, metric
    base_over, _base_mca = _capz_bag_metrics(baseline_plan, bag, new_order, cap_min)
    if base_over is None:
        baseline_plan.o2_capz = metric
        return baseline_plan, metric
    cands = _enumerate_valid_plans(
        nodes, leg_min, bag_delivery_idxs, bag_pickup_idxs_by_oid,
        new_pickup_idx, new_delivery_idx, new_order, bag, now, sla_minutes)
    base_drive = baseline_plan.drive_min if baseline_plan.drive_min is not None else 0.0
    best = None  # (key, plan, over, detour)
    for p in cands:
        over, mca = _capz_bag_metrics(p, bag, new_order, cap_min)
        if over is None:
            continue
        metric["considered"] += 1
        if (mca or 0.0) > z_min:                                 # (b) cap-Z carried
            continue
        detour = (p.drive_min if p.drive_min is not None else 0.0) - base_drive
        if detour > detour_max:                                  # (a) detour ≤ limit
            metric["blocked_by_cap"] += 1
            continue
        if over > base_over - min_gain:                          # (c) materialna redukcja
            continue
        if p.sla_violations > baseline_plan.sla_violations:      # (d) HARD nie osłabione
            continue
        key = (over, round(p.total_duration_min, 3), tuple(p.sequence))
        if best is None or key < best[0]:
            best = (key, p, over, detour)
    if best is None:
        baseline_plan.o2_capz = metric
        return baseline_plan, metric
    _key, chosen, chosen_over, chosen_detour = best
    metric["applied"] = 1
    metric["detour_min"] = round(chosen_detour, 2)
    metric["overage_saved_min"] = round(base_over - chosen_over, 1)
    chosen.o2_capz = metric
    chosen.strategy = baseline_plan.strategy   # reseq ≠ nowa strategia (zachowaj etykietę)
    return chosen, metric


def _enumerate_valid_plans(
    nodes, leg_min, bag_delivery_idxs,
    bag_pickup_idxs_by_oid,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> List[RoutePlanV2]:
    """Wszystkie poprawne przeploty stopów jako plany (pickup-before-delivery per
    para; lock_first gdy niesione; super-odbior RAZ). Wyekstrahowane z `_bruteforce_plan`
    (byte-parytet: bruteforce = `_select_best_with_tie_breaker(_enumerate_valid_plans(...))`).
    Reużywane przez `_capz_reseq_plan` (cap-Z reseq — potrzebuje puli kandydatów NIEZALEŻNIE
    od strategii, w tym ORTOOLS które zwraca 1 plan)."""
    # V3.19e: to_place uwzględnia pickup-nodes dla pending bag items.
    to_place: List[int] = list(bag_delivery_idxs) + [new_delivery_idx]
    if new_pickup_idx is not None:
        to_place.append(new_pickup_idx)
    # B4 FIX (audyt 2026-06-28): super-odbior (group_oids) jest WSPOLDZIELONY — mapa
    # bag_pickup_idxs_by_oid kieruje N oidow grupy na ten SAM super_pickup_idx, wiec
    # .values() ma duplikaty -> bruteforce wkladal super-odbior N razy (double-pickup).
    # dict.fromkeys = dedup z zachowaniem kolejnosci -> super-odbior RAZ. Naprawia greedy
    # path 2 (OR-Tools INFEASIBLE ~38/d) + panic-rollback OR-Tools off. OR-Tools on -> ta
    # sciezka nie biegnie (runtime-neutralne live). Twin z _greedy_plan nizej.
    to_place.extend(dict.fromkeys(bag_pickup_idxs_by_oid.values()))

    # Mapping delivery_idx → pickup_idx dla ordinances z pickup-before-delivery:
    #  - pending bag items (V3.19e): (pickup_idx, delivery_idx) par z bag_pickup_idxs_by_oid
    #  - new_order jeśli need_pickup: (new_pickup_idx, new_delivery_idx)
    delivery_to_pickup: Dict[int, int] = {}
    for d_idx in bag_delivery_idxs:
        oid = nodes[d_idx]["order_id"]
        if oid in bag_pickup_idxs_by_oid:
            delivery_to_pickup[d_idx] = bag_pickup_idxs_by_oid[oid]
    if new_pickup_idx is not None:
        delivery_to_pickup[new_delivery_idx] = new_pickup_idx

    # Lock first stop: gdy kurier wiezie jedzenie (picked_up drops w bagu),
    # pierwszy node MUSI być dostarczeniem picked_up item — żadnych zawrotów
    # do nowej restauracji z jedzeniem w torbie. V3.19e: pending bag drops
    # NIE liczą się jako "jedzenie w torbie" (kurier go jeszcze nie ma).
    picked_up_drop_set = {
        d_idx for d_idx in bag_delivery_idxs
        if nodes[d_idx]["order_id"] not in bag_pickup_idxs_by_oid
    }
    lock_first = bool(picked_up_drop_set)

    # V3.27 Bug Y tie-breaker: collect all valid plans, post-process selection.
    all_valid_plans: List[RoutePlanV2] = []
    for perm in permutations(to_place):
        if lock_first and perm[0] not in picked_up_drop_set:
            continue
        # Pickup-before-delivery dla każdej pary (new_order + pending bag items)
        valid = True
        for d_idx, p_idx in delivery_to_pickup.items():
            if perm.index(p_idx) > perm.index(d_idx):
                valid = False
                break
        if not valid:
            continue
        plan = _plan_from_sequence(list(perm), nodes, leg_min, new_order, bag, now, sla_minutes)
        all_valid_plans.append(plan)
    return all_valid_plans


def _bruteforce_plan(
    nodes, leg_min, bag_delivery_idxs,
    bag_pickup_idxs_by_oid,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    all_valid_plans = _enumerate_valid_plans(
        nodes, leg_min, bag_delivery_idxs, bag_pickup_idxs_by_oid,
        new_pickup_idx, new_delivery_idx, new_order, bag, now, sla_minutes,
    )
    return _select_best_with_tie_breaker(all_valid_plans, now, nodes=nodes)


def _greedy_plan(
    nodes, leg_min, bag_delivery_idxs,
    bag_pickup_idxs_by_oid,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    # V3.19e: rozdziel picked_up (already-picked) od pending (pickup needed).
    picked_up_deliv = [
        i for i in bag_delivery_idxs
        if nodes[i]["order_id"] not in bag_pickup_idxs_by_oid
    ]
    pending_pairs = [
        (bag_pickup_idxs_by_oid[nodes[i]["order_id"]], i)
        for i in bag_delivery_idxs
        if nodes[i]["order_id"] in bag_pickup_idxs_by_oid
    ]  # [(pickup_idx, delivery_idx), ...]

    # Step 1: NN ordering przez picked_up deliveries z courier pos.
    # Pending items insertowane w Step 1.5 razem z pickup-nodami.
    seq_base: List[int] = []
    remaining = list(picked_up_deliv)
    current = 0
    while remaining:
        nxt = min(remaining, key=lambda idx: leg_min(current, idx))
        seq_base.append(nxt)
        remaining.remove(nxt)
        current = nxt

    # lock_first_picked: jeśli bag ma picked_up items, pierwszy node musi być
    # drop jednego z nich (courier wiezie jedzenie).
    lock_first_picked = bool(picked_up_deliv)

    # Step 1.5 (V3.19e): dla każdej pending pary (pickup, delivery), znajdź
    # best (p_pos, d_pos) insertion z constraintem p_pos ≤ d_pos. Commit po
    # znalezieniu optimum, iteruj dalej. Greedy per-item (nie globalnie optimal,
    # ale O(k*N^2) zamiast bruteforce).
    for p_idx, d_idx in pending_pairs:
        n = len(seq_base)
        # B4 FIX (audyt 2026-06-28): super-odbior (group_oids) WSPOLDZIELONY przez kilka
        # zamowien grupy (bag_pickup_idxs_by_oid: N oidow -> ten SAM super_pickup_idx).
        # Gdy juz wstawiony przez wczesniejsza pare grupy -> wstaw TYLKO dostawe (po
        # istniejacym odbiorze), NIE dubluj odbioru (= double-pickup). _simulate_sequence
        # liczy single dwell dla group_oids gdy wezel jest w sekwencji RAZ.
        if p_idx in seq_base:
            p_existing = seq_base.index(p_idx)
            best_d_pos = None
            best_d_key = (10 ** 9, float("inf"))
            for d_pos in range(p_existing + 1, n + 1):
                candidate = list(seq_base)
                candidate.insert(d_pos, d_idx)
                plan = _plan_from_sequence(
                    candidate, nodes, leg_min, new_order, bag, now, sla_minutes
                )
                key = (plan.sla_violations, plan.total_duration_min)
                if key < best_d_key:
                    best_d_key = key
                    best_d_pos = d_pos
            if best_d_pos is not None:
                seq_base.insert(best_d_pos, d_idx)
            continue
        best_insertion: Optional[tuple] = None
        best_ins_key = (10 ** 9, float("inf"))
        for d_pos in range(n + 1):
            for p_pos in range(d_pos + 1):
                if lock_first_picked and p_pos == 0:
                    continue
                candidate = list(seq_base)
                # insert d first, then p (position p ≤ d → d's final idx nie shift)
                candidate.insert(d_pos, d_idx)
                candidate.insert(p_pos, p_idx)
                plan = _plan_from_sequence(
                    candidate, nodes, leg_min, new_order, bag, now, sla_minutes
                )
                key = (plan.sla_violations, plan.total_duration_min)
                if key < best_ins_key:
                    best_ins_key = key
                    best_insertion = (p_pos, d_pos)
        if best_insertion is not None:
            p_pos, d_pos = best_insertion
            seq_base.insert(d_pos, d_idx)
            seq_base.insert(p_pos, p_idx)

    # Step 2: try every (pickup_pos, delivery_pos) insertion for new_order.
    # Lock first stop: jeśli bag niepusty (picked_up albo po inserowanych
    # pending), nowy pickup NIE może być przed pierwszą dostawą.
    lock_first = len(seq_base) > 0
    n = len(seq_base)
    # V3.27 Bug Y tie-breaker: collect all valid Step-2 insertions, post-process.
    all_step2_plans: List[RoutePlanV2] = []
    for d_pos in range(n + 1):
        pickup_positions = [None] if new_pickup_idx is None else list(range(0, d_pos + 1))
        for p_pos in pickup_positions:
            if lock_first and p_pos == 0:
                continue
            candidate = list(seq_base)
            candidate.insert(d_pos, new_delivery_idx)
            if p_pos is not None:
                # p_pos ≤ d_pos → pickup lands before delivery after insertion
                candidate.insert(p_pos, new_pickup_idx)
            plan = _plan_from_sequence(candidate, nodes, leg_min, new_order, bag, now, sla_minutes)
            all_step2_plans.append(plan)
    return _select_best_with_tie_breaker(all_step2_plans, now, nodes=nodes)


def _dwell_min_for_arriving(node: dict) -> float:
    """V3.28 FAZA 3 ścieżka A 2026-05-11: DWELL component of time_matrix[i][j].

    Service time consumed at arriving node (j). Used to align solver semantyka
    z `_simulate_sequence` pickup_at storage convention (post-DWELL = "leaves
    restaurant" / "after delivery handoff"). FAZA 0 audit (n=2767, 12 dni)
    empirycznie confirmed: bag>=2 reject rate 34-100% explained by DWELL
    accumulation not seen by solver.

    Tier-aware (2026-05-17): czyta dwell ostemplowany na węźle przez
    simulate_bag_route_v2 (per tier kuriera). Fallback na moduł-default gdy
    węzeł niestemplowany (defensywnie).
    """
    kind = node.get("kind")
    if kind == "pickup":
        return node.get("dwell_pickup", DWELL_PICKUP_MIN)
    if kind == "delivery":
        return node.get("dwell_dropoff", DWELL_DROPOFF_MIN)
    return 0.0  # courier depot or unknown — no service time


def _ortools_plan(
    nodes, leg_min, bag_delivery_idxs,
    bag_pickup_idxs_by_oid,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> Optional[RoutePlanV2]:
    """V3.26 Fix 6: OR-Tools TSP solver dla pickup-and-delivery problem.

    Replaces bruteforce + greedy z industry-standard constraint solver.
    Time-bounded 200ms (configurable). Returns None gdy solver INFEASIBLE —
    caller falls back do greedy (route_simulator_v2.simulate_bag_route_v2).

    Pickup-drop pairs:
    - Pending bag items: (bag_pickup_idx, bag_delivery_idx) per oid
    - New order: (new_pickup_idx, new_delivery_idx) gdy need_pickup
    - Already-picked bag drops: NIE pair (just final visits, no precedence)
    """
    from dispatch_v2 import tsp_solver
    from dispatch_v2.common import V326_OR_TOOLS_TIME_LIMIT_MS as _ot_ms

    N = len(nodes)
    if N <= 1:
        return None

    # Build time matrix z leg_min callable (drive_min between nodes).
    # V3.28 FAZA 3 ścieżka A (2026-05-11): time_matrix[i][j] = travel + DWELL_at_j.
    # Pre-fix: solver dostawał travel only, post-process dolicza DWELL → asymetria
    # ~4N min ukrytego time per N stops. Window check post-solve assertion fail.
    # Math (bag=N stops): 2*N pickups+drops × DWELL=2min = 4N min pre-fix unseen,
    # now visible to solver → respects [ck-5, ck+5] frozen window correctly.
    # FAZA 0 audit n=2767/12d confirmed quantitative model.
    from dispatch_v2 import common as _common_faza3
    _v328_faza3_dwell = getattr(_common_faza3, "ENABLE_V328_TIME_MATRIX_DWELL", False)
    time_matrix: List[List[float]] = [[0.0] * N for _ in range(N)]
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            try:
                travel = max(0.0, float(leg_min(i, j)))
            except Exception:
                time_matrix[i][j] = 9999.0
                continue
            if _v328_faza3_dwell:
                travel += _dwell_min_for_arriving(nodes[j])
            time_matrix[i][j] = travel
    # Distance matrix proxy = time matrix (solver minimizes; same units fine).
    distance_matrix = time_matrix

    # Build pickup-drop pairs
    pickup_drop_pairs: List[Tuple[int, int]] = []
    for d_idx in bag_delivery_idxs:
        oid = nodes[d_idx]["order_id"]
        if oid in bag_pickup_idxs_by_oid:
            p_idx = bag_pickup_idxs_by_oid[oid]
            pickup_drop_pairs.append((p_idx, d_idx))
    if new_pickup_idx is not None:
        pickup_drop_pairs.append((new_pickup_idx, new_delivery_idx))

    # V3.27.1 BUG-2: time windows constraint dla TSP (flag-gated, default False).
    # Pickup nodes: open=max(0, ready-now), close=open+60min hard. Delivery/courier:
    # luźne okno (max 120min — effective no constraint). Pre-V3.27.1 przekazywaliśmy
    # time_windows=None co sprawiało że TSP minimalizował czysty distance ignorując
    # pickup_ready (case #468733 Chicago Pizza: 53min wait zaakceptowane przez
    # solver bo był najkrótszy distance-min). _simulate_sequence post-conversion
    # ADD wait time ale NIE reorderuje sekwencji — solver MUSI dostać constraint.
    from dispatch_v2 import common as _common
    time_windows = None
    if _common.ENABLE_V327_TSP_TIME_WINDOWS and now is not None:
        time_windows = []
        for idx in range(N):
            node = nodes[idx]
            if node.get("kind") == "pickup":
                ref = node.get("ref")
                ready = getattr(ref, "pickup_ready_at", None) if ref is not None else None
                if ready is not None:
                    # V3.27.6 (2026-04-28): capture ck_raw + parsed_dt PRZED try
                    # block, żeby loud warning w except miał pełen kontekst.
                    _ck_raw = getattr(ref, "czas_kuriera_warsaw", None) if ref is not None else None
                    _ck_oid = getattr(ref, "order_id", "?") if ref is not None else "?"
                    _ck_parse_err = None
                    try:
                        open_min = max(0.0, (ready - now).total_seconds() / 60.0)
                        # V3.27.4 (2026-04-27 wieczór): frozen czas_kuriera detection.
                        # Order ma committed czas_kuriera (first_acceptance lub manual
                        # panel update) → R27 ±5 hard window zamiast 60-min.
                        # Per Adrian zasada: "czas_kuriera po przypisaniu = nietykalny".
                        # V3.27.6 FIX 1 Path C (2026-04-28): explicit string-form
                        # rejection. Pre-fix sprawdzał tylko `is not None` —
                        # padający na "None"/"null"/"" string formy. Pattern z
                        # V3.27.5 Path A (canonical signal robustness).
                        ck_present = (
                            _ck_raw is not None
                            and str(_ck_raw).strip() not in ("", "None", "null", "NULL", "None\n")
                        )
                        czas_kuriera_committed = (
                            _common.ENABLE_V3274_FROZEN_PICKUP_WINDOW
                            and ref is not None
                            and ck_present
                        )
                        if czas_kuriera_committed:
                            # E3 sprint 2026-05-17: frozen czas_kuriera = KOTWICA
                            # restauracyjna, nie sztywny box ±5. Dolna granica
                            # (open-5) trzyma pickup ~przy zadeklarowanym czasie
                            # (kurier nie odbiera przed gotowością). GÓRNA granica
                            # LUŹNA — reachability (kurier nie zdąży w ±5) NIE może
                            # wywołać INFEASIBLE. Pre-E3 box ±5 kasował całą
                            # optymalizację OR-Tools (diagnoza order 474266: 7.5k
                            # INFEASIBLE/dzień → greedy ślepy). Kotwica USTAWIA
                            # trasę, nie ją kasuje; realny zegar liczy
                            # _simulate_sequence post-solve.
                            window_open = max(0.0, open_min - _common.V3274_FROZEN_PICKUP_WINDOW_MIN)
                            window_close = _common.V327_DROP_TIME_WINDOW_MAX_MIN
                            time_windows.append((window_open, window_close))
                        else:
                            close_min = open_min + _common.V327_PICKUP_TIME_WINDOW_CLOSE_MIN
                            time_windows.append((open_min, close_min))
                    except Exception as _tw_exc:
                        # V3.27.6 FIX 2a (2026-04-28): replace silent except.
                        # Loguj oid, ck_raw value, ck type, parse error repr,
                        # fallback window applied. Daje empirical signal H2 (type/
                        # format mutation) bez osobnego probe sprintu.
                        _fb = (0.0, _common.V327_DROP_TIME_WINDOW_MAX_MIN)
                        time_windows.append(_fb)
                        _log.warning(
                            f"V3274_TIMEWINDOW_FALLBACK oid={_ck_oid} "
                            f"ck_type={type(_ck_raw).__name__} ck_repr={repr(_ck_raw)[:80]} "
                            f"ready={ready!r} now={now!r} except={type(_tw_exc).__name__}: "
                            f"{repr(_tw_exc)[:120]} fallback_window={_fb}"
                        )
                else:
                    time_windows.append((0.0, _common.V327_DROP_TIME_WINDOW_MAX_MIN))
            else:
                time_windows.append((0.0, _common.V327_DROP_TIME_WINDOW_MAX_MIN))

    # Sprint OBJ F2 (2026-05-18): koszt SPAN (idle) — zastępuje zepsuty P3-D1.
    # P3-D1 (per-edge idle estimate augmentujący cost_matrix) był strukturalnie
    # wadliwy: karał pojedynczą krawędź zamiast skumulowanego przyjazdu, każdą
    # krawędź jednakowo (brak gradientu), perwersyjnie nagradzał dłuższy dojazd,
    # a magnitudy dominowały objective ~6:1 (diagnoza 474253). F2 wycenia idle
    # natywnie przez SetSpanCostCoefficient w solverze (patrz tsp_solver) — span
    # makespan zawiera slack czekania. cost_matrix usunięty: solver liczy cost
    # z distance_matrix (= czysty time_matrix). Default OFF (env override).
    _span_cost_coeff = 0.0
    if _common.decision_flag("ENABLE_OBJ_SPAN_COST"):
        _span_cost_coeff = float(getattr(_common, "OBJ_SPAN_COST_COEFF", 0.0))

    # Sprint OBJ F1 (2026-05-17): R6 soft upper bound per węzeł delivery
    # (flag-gated, default OFF — deploy bez zmiany). Deadline CumulVar dostawy =
    # anchor+sla; anchor = picked_up_at (odebrane — stare jedzenie, deadline
    # blisko/0 → solver front-loaduje) lub pickup_ready_at (pending/new).
    delivery_soft_deadlines = None
    try:
        if _common.decision_flag("ENABLE_OBJ_R6_SOFT_DEADLINE") and now is not None:
            _r6_coeff = float(getattr(_common, "OBJ_R6_DEADLINE_PENALTY_COEFF", 0.0))
            _sla_f = float(sla_minutes)
            _dsd: List[Optional[Tuple[float, float]]] = [None] * N
            for _i in range(N):
                _node = nodes[_i]
                if _node.get("kind") != "delivery":
                    continue
                _ref = _node.get("ref")
                if _ref is None:
                    continue
                _picked = (getattr(_ref, "status", "assigned") == "picked_up"
                           or getattr(_ref, "picked_up_at", None) is not None)
                _anchor = (getattr(_ref, "picked_up_at", None) if _picked
                           else getattr(_ref, "pickup_ready_at", None))
                if _anchor is None:
                    continue
                if _anchor.tzinfo is None:
                    _anchor = _anchor.replace(tzinfo=timezone.utc)
                _deadline = (_anchor.astimezone(timezone.utc) - now
                             ).total_seconds() / 60.0 + _sla_f
                _dsd[_i] = (_deadline, _r6_coeff)
            delivery_soft_deadlines = _dsd
    except Exception as _dsd_e:
        _log.warning(
            f"OBJ_F1_DEADLINE_BUILD_FAIL {type(_dsd_e).__name__}: {_dsd_e}")
        delivery_soft_deadlines = None

    # Sprint OBJ FOOD-AGE ADDITIVE (2026-06-14 redesign): food-age = DRUGI soft
    # bound dostawy, ADDYTYWNY do R6 (NIE zastępuje — wcześniejsza wersja
    # zastępująca regresowała SLA 9.4% / thermal −5.48 na replay-korpusie n=891).
    # Kotwica = czas gotowości (sla=0), coeff gentle → liniowa kara za wiek
    # niesionego jedzenia. R6 (ready+sla, coeff 100) chroni SLA; food-age nudguje
    # świeżość TYLKO gdzie R6 obojętne (obie dostawy < deadline — case Jakuba).
    # Solver: osobny wymiar "FoodAge" == Time (mirror realnego harmonogramu).
    delivery_food_age_penalties = None
    try:
        if _common.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE") and now is not None:
            _fa_coeff = float(getattr(_common, "OBJ_DELIVERY_FOOD_AGE_COEFF", 0.0))
            if _fa_coeff > 0:
                _fap: List[Optional[Tuple[float, float]]] = [None] * N
                for _i in range(N):
                    _node = nodes[_i]
                    if _node.get("kind") != "delivery":
                        continue
                    _ref = _node.get("ref")
                    if _ref is None:
                        continue
                    _picked = (getattr(_ref, "status", "assigned") == "picked_up"
                               or getattr(_ref, "picked_up_at", None) is not None)
                    _anchor = (getattr(_ref, "picked_up_at", None) if _picked
                               else getattr(_ref, "pickup_ready_at", None))
                    if _anchor is None:
                        continue
                    if _anchor.tzinfo is None:
                        _anchor = _anchor.replace(tzinfo=timezone.utc)
                    _fa_bound = (_anchor.astimezone(timezone.utc) - now
                                 ).total_seconds() / 60.0  # sla=0 (od gotowości)
                    _fap[_i] = (_fa_bound, _fa_coeff)
                delivery_food_age_penalties = _fap
    except Exception as _fa_e:
        _log.warning(
            f"OBJ_FOOD_AGE_BUILD_FAIL {type(_fa_e).__name__}: {_fa_e}")
        delivery_food_age_penalties = None

    # Sprint OBJ FRESH (2026-05-30): świeżość odbioru — soft upper bound per
    # węzeł pickup, bound = (ready_at − now) + THRESHOLD min. Flag-gated, default
    # OFF (deploy-safe; LIVE przez env). Karze projektowany odbiór dopiero gdy
    # przekroczy gotowość o > próg → celuje w ogon ~18% (replay 2026-05-30),
    # nie rusza mediany clamped-to-ready. Soft — nie wpływa na feasibility.
    pickup_freshness_penalties = None
    try:
        if _common.decision_flag("ENABLE_OBJ_PICKUP_FRESHNESS") and now is not None:
            _pf_thr = float(getattr(_common, "OBJ_PICKUP_FRESHNESS_THRESHOLD_MIN", 8.0))
            _pf_coeff = float(getattr(_common, "OBJ_PICKUP_FRESHNESS_PENALTY_COEFF", 20.0))
            if _pf_coeff > 0:
                _pf: List[Optional[Tuple[float, float]]] = [None] * N
                for _i in range(N):
                    _node = nodes[_i]
                    if _node.get("kind") != "pickup":
                        continue
                    _ref = _node.get("ref")
                    _ready = getattr(_ref, "pickup_ready_at", None) if _ref is not None else None
                    if _ready is None:
                        continue
                    if _ready.tzinfo is None:
                        _ready = _ready.replace(tzinfo=timezone.utc)
                    _open_min = max(0.0, (_ready.astimezone(timezone.utc) - now
                                          ).total_seconds() / 60.0)
                    _pf[_i] = (_open_min + _pf_thr, _pf_coeff)
                pickup_freshness_penalties = _pf
    except Exception as _pf_e:
        _log.warning(
            f"OBJ_FRESH_BUILD_FAIL {type(_pf_e).__name__}: {_pf_e}")
        pickup_freshness_penalties = None

    # N5 krok 2 (2026-06-17): KARA PUNKTUALNOŚCI COMMITTED — soft upper bound na
    # pickupach z czas_kuriera (obietnica dla restauracji). bound = (czas_kuriera−now)
    # + tolerancja (load-aware: 5 strict / 10 niedobór, z _COMMITTED_PICKUP_TOL_MIN
    # ustawionego przez pipeline z loadgov_ewma). Flag-gated, default OFF. Soft —
    # NIGDY INFEASIBLE (lekcja 7500/d). Cel: solver nie ślizga committed dla skrótu.
    pickup_committed_penalties = None
    try:
        if _common.decision_flag("ENABLE_OBJ_COMMITTED_PICKUP_PENALTY") and now is not None:
            _pc_coeff = float(getattr(_common, "OBJ_COMMITTED_PICKUP_PENALTY_COEFF", 0.0))
            _pc_tol = _COMMITTED_PICKUP_TOL_MIN
            if _pc_tol is None:
                _pc_tol = float(getattr(_common, "OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN", 5.0))
            if _pc_coeff > 0:
                _pc: List[Optional[Tuple[float, float]]] = [None] * N
                for _i in range(N):
                    _node = nodes[_i]
                    if _node.get("kind") != "pickup":
                        continue
                    _ref = _node.get("ref")
                    _ck_raw = getattr(_ref, "czas_kuriera_warsaw", None) if _ref is not None else None
                    if _ck_raw is None or str(_ck_raw).strip() in ("", "None", "null", "NULL"):
                        continue
                    _ck_dt = _common.parse_panel_timestamp(_ck_raw)
                    if _ck_dt is None:
                        continue
                    if _ck_dt.tzinfo is None:
                        _ck_dt = _ck_dt.replace(tzinfo=timezone.utc)
                    _bound_min = (_ck_dt.astimezone(timezone.utc) - now
                                  ).total_seconds() / 60.0 + _pc_tol
                    _pc[_i] = (_bound_min, _pc_coeff)
                pickup_committed_penalties = _pc
    except Exception as _pc_e:
        _log.warning(
            f"OBJ_COMMITTED_PICKUP_BUILD_FAIL {type(_pc_e).__name__}: {_pc_e}")
        pickup_committed_penalties = None

    # ESKALACJA (Adrian 2026-06-22 D1): tier-2 soft bound na pickupach committed,
    # próg ck+T2 (T2>tol), coeff ostry. Łączy się z tier-1 (osobny wymiar w solverze)
    # → kara WYPUKŁA: slope rośnie za 2. progiem ("mocno rosnąca od +6"). Tylko gdy
    # tier-1 aktywny. Flag-gated, default OFF. Soft — NIGDY INFEASIBLE.
    pickup_committed_penalties_t2 = None
    try:
        if (getattr(_common, "ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION", False)
                and pickup_committed_penalties is not None and now is not None):
            _pc2_coeff = float(getattr(_common, "OBJ_COMMITTED_PICKUP_PENALTY_COEFF_T2", 0.0))
            _pc2_t2 = float(getattr(_common, "OBJ_COMMITTED_PICKUP_ESCALATION_T2_MIN", 10.0))
            if _pc2_coeff > 0:
                _pc2: List[Optional[Tuple[float, float]]] = [None] * N
                for _i in range(N):
                    _node = nodes[_i]
                    if _node.get("kind") != "pickup":
                        continue
                    _ref = _node.get("ref")
                    _ck_raw = getattr(_ref, "czas_kuriera_warsaw", None) if _ref is not None else None
                    if _ck_raw is None or str(_ck_raw).strip() in ("", "None", "null", "NULL"):
                        continue
                    _ck_dt = _common.parse_panel_timestamp(_ck_raw)
                    if _ck_dt is None:
                        continue
                    if _ck_dt.tzinfo is None:
                        _ck_dt = _ck_dt.replace(tzinfo=timezone.utc)
                    _bound2_min = (_ck_dt.astimezone(timezone.utc) - now
                                   ).total_seconds() / 60.0 + _pc2_t2
                    _pc2[_i] = (_bound2_min, _pc2_coeff)
                pickup_committed_penalties_t2 = _pc2
    except Exception as _pc2_e:
        _log.warning(
            f"OBJ_COMMITTED_PICKUP_ESCALATION_BUILD_FAIL {type(_pc2_e).__name__}: {_pc2_e}")
        pickup_committed_penalties_t2 = None

    # FOOD-AGE HARD-SLA (2026-06-17): twarde bound dla zleceń JUŻ-ODEBRANYCH
    # (delivery node bez węzła pickup). bound=(picked_up_at−now)+sla [min od startu].
    # Pending/new (w parach) chronione twardym spanem → None. Kotwica = METRYKA
    # (_count_sla_violations używa picked_up_at dla odebranych). Gated flagą.
    _hard_sla = _common.decision_flag("ENABLE_OBJ_FOOD_AGE_HARD_SLA")
    delivery_sla_hard_bounds = None
    if _hard_sla and delivery_food_age_penalties is not None and now is not None:
        try:
            _hb: List[Optional[float]] = [None] * N
            for _i in range(N):
                _node = nodes[_i]
                if _node.get("kind") != "delivery":
                    continue
                _ref = _node.get("ref")
                if _ref is None:
                    continue
                _picked = (getattr(_ref, "status", "assigned") == "picked_up"
                           or getattr(_ref, "picked_up_at", None) is not None)
                if not _picked:
                    continue
                _pu = getattr(_ref, "picked_up_at", None)
                if _pu is None:
                    continue
                if _pu.tzinfo is None:
                    _pu = _pu.replace(tzinfo=timezone.utc)
                _hb[_i] = ((_pu.astimezone(timezone.utc) - now).total_seconds() / 60.0
                           + float(sla_minutes))
            delivery_sla_hard_bounds = _hb
        except Exception as _hb_e:
            _log.warning(
                f"OBJ_FOODAGE_HARDSLA_BOUNDS_FAIL {type(_hb_e).__name__}: {_hb_e}")
            delivery_sla_hard_bounds = None

    def _solve(fa_pen, hard_span, hard_bounds, warm_routes, ot_ms=None):
        """Solve + V3.27.1 BUG-2 retry bez time-windows. Hermetyzuje powtórkę.
        ot_ms: limit czasu (lewar latencji — ON warm-startowany dostaje krótszy)."""
        _ms = int(ot_ms if ot_ms is not None else _ot_ms)
        _sol = tsp_solver.solve_tsp_with_constraints(
            num_stops=N,
            pickup_drop_pairs=pickup_drop_pairs,
            distance_matrix_km=distance_matrix,
            time_matrix_min=time_matrix,
            time_windows=time_windows,
            max_route_min=120.0,
            time_limit_ms=_ms,
            delivery_soft_deadlines=delivery_soft_deadlines,
            pickup_freshness_penalties=pickup_freshness_penalties,
            pickup_committed_penalties=pickup_committed_penalties,
            pickup_committed_penalties_t2=pickup_committed_penalties_t2,
            delivery_food_age_penalties=fa_pen,
            span_cost_coeff=_span_cost_coeff,
            delivery_sla_hard_span=hard_span,
            delivery_sla_hard_bounds=hard_bounds,
            sla_minutes_hard=float(sla_minutes),
            warm_start_routes=warm_routes,
        )
        if (_sol is None or not getattr(_sol, "sequence", None)) and time_windows is not None:
            _log.warning(
                f"V3.27.1 OR-Tools INFEASIBLE z time windows "
                f"(N={N}, pairs={len(pickup_drop_pairs)}), retry bez constraints"
            )
            _sol = tsp_solver.solve_tsp_with_constraints(
                num_stops=N,
                pickup_drop_pairs=pickup_drop_pairs,
                distance_matrix_km=distance_matrix,
                time_matrix_min=time_matrix,
                time_windows=None,
                max_route_min=120.0,
                time_limit_ms=int(_ot_ms),
                delivery_soft_deadlines=delivery_soft_deadlines,
                pickup_freshness_penalties=pickup_freshness_penalties,
                pickup_committed_penalties=pickup_committed_penalties,
            pickup_committed_penalties_t2=pickup_committed_penalties_t2,
                delivery_food_age_penalties=fa_pen,
                span_cost_coeff=_span_cost_coeff,
                delivery_sla_hard_span=hard_span,
                delivery_sla_hard_bounds=hard_bounds,
                sla_minutes_hard=float(sla_minutes),
                warm_start_routes=None,
            )
        return _sol

    if _hard_sla and delivery_food_age_penalties is not None:
        # HYBRYDA (PHASE1_DESIGN_LOCK §3): base (bez food-age) → ON (food-age +
        # twardy span + warm-start sekwencją base) → drabina fallbacku gwarantująca
        # realny SLA(ON) ≤ SLA(base). base = dzisiejszy plan (źródło warm-startu +
        # fallback). +1 solve TYLKO tu (warm-startowany, krótki).
        base_sol = _solve(None, False, None, None)
        base_plan = (_plan_from_sequence(base_sol.sequence, nodes, leg_min,
                                         new_order, bag, now, sla_minutes)
                     if base_sol is not None and base_sol.sequence else None)
        _warm = ([list(base_sol.sequence)]
                 if base_sol is not None and base_sol.sequence else None)
        # lewar latencji: ON warm-startowany dostaje krótszy limit (numeric-override).
        _on_ms = _common.load_flags().get(
            "OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS",
            getattr(_common, "OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS", 100.0))
        on_sol = _solve(delivery_food_age_penalties, True,
                        delivery_sla_hard_bounds, _warm, ot_ms=_on_ms)
        on_plan = (_plan_from_sequence(on_sol.sequence, nodes, leg_min,
                                       new_order, bag, now, sla_minutes)
                   if on_sol is not None and on_sol.sequence else None)
        if on_plan is None:
            plan, solution = base_plan, base_sol
        elif base_plan is not None and (on_plan.sla_violations or 0) > (base_plan.sla_violations or 0):
            _log.warning(
                f"OBJ_FOODAGE_HARDSLA_FALLBACK_OFF realny sla_on={on_plan.sla_violations}"
                f">{base_plan.sla_violations}=base (rozjazd time↔realizacja) → plan base"
            )
            plan, solution = base_plan, base_sol
        else:
            plan, solution = on_plan, on_sol
        if plan is None or solution is None or not getattr(solution, "sequence", None):
            return None
    else:
        solution = _solve(delivery_food_age_penalties, False, None, None)
        if solution is None or not solution.sequence:
            if solution is not None:
                _log.warning(
                    f"OR-Tools no sequence: status={solution.solver_status} "
                    f"elapsed={solution.elapsed_ms}ms warnings={solution.warnings}"
                )
            return None
        # Convert sequence → RoutePlanV2 via _plan_from_sequence (re-uses standard
        # _simulate_sequence z DWELL stops + pickup_ready_at wait + SLA violations).
        plan = _plan_from_sequence(
            solution.sequence, nodes, leg_min, new_order, bag, now, sla_minutes
        )

    # V3.27.6 FIX 2b (2026-04-28): post-solve assertion dla frozen ck pickups.
    # Diagnoses runtime divergence vs synthetic: czy plan.pickup_at[oid] dla
    # frozen ck order respektuje [ck-5, ck+5] window. Dwustopniowy log:
    #   - TOLERANCE: walked ∈ (close, close+0.5] → warning, NIE reject
    #   - VIOLATION: walked > close+0.5 → warning + reject + greedy fallback
    # Reject path zwraca strategy="ortools_rejected_v3274" (osobny enum dla
    # eyeball Adrian + events.db category, NIE myli z standard greedy_fallback).
    try:
        if time_windows is not None and plan is not None and hasattr(plan, "pickup_at"):
            _violations = []
            _tolerances = []
            for node_idx, node in enumerate(nodes):
                if node.get("kind") != "pickup":
                    continue
                if node_idx >= len(time_windows):
                    continue
                tw = time_windows[node_idx]
                if tw is None:
                    continue
                _wo, _wc = tw
                _ref = node.get("ref")
                _ck = getattr(_ref, "czas_kuriera_warsaw", None) if _ref is not None else None
                # Tylko frozen ck nodes (V3.27.4 case) — nie sprawdzaj new_order
                # 60-min path bo to inny scope.
                if _ck is None:
                    continue
                _oid = getattr(_ref, "order_id", node.get("order_id", "?"))
                _pa = plan.pickup_at.get(_oid) if plan.pickup_at else None
                if _pa is None:
                    continue
                if _pa.tzinfo is None:
                    _pa_utc = _pa.replace(tzinfo=timezone.utc)
                else:
                    _pa_utc = _pa.astimezone(timezone.utc)
                walked_min = (_pa_utc - now).total_seconds() / 60.0
                if walked_min > _wc + 0.5:
                    _violations.append((_oid, walked_min, _wc))
                elif walked_min > _wc:
                    _tolerances.append((_oid, walked_min, _wc))
            for _oid, _w, _c in _tolerances:
                _log.warning(
                    f"V3274_OR_TOOLS_TOLERANCE oid={_oid} walked_min={_w:.2f} "
                    f"close_min={_c:.2f} delta=+{_w - _c:.2f}min "
                    f"(within 0.5min, NIE reject) solver_status={solution.solver_status}"
                )
            if _violations:
                # E2 sprint 2026-05-17: NIE odrzucamy planu OR-Tools do greedy.
                # Pre-E2 ta ścieżka kasowała optymalizację OR-Tools przy KAŻDYM
                # przekroczeniu okna frozen i wracała do geometrycznie ślepego
                # greedy (lock_first przypinał odebrany order na #1). Diagnoza
                # order 474266: 9.2k V3274-reject/dzień, 2233 propozycji/dzień na
                # ortools_rejected_v3274 — OR-Tools de facto wyłączony na flocie.
                # Okno frozen ma USTAWIAĆ trasę, nie ją kasować. Odchył logujemy
                # dla observability; plan OR-Tools zostaje (caller nada strategy
                # "ortools"). Realny zegar + SLA liczy _simulate_sequence.
                _log.warning(
                    f"V3274_OR_TOOLS_VIOLATION overshoot (E2: plan OR-Tools "
                    f"zostaje, NIE reject) violations={_violations} "
                    f"solver_status={solution.solver_status} "
                    f"elapsed={solution.elapsed_ms}ms"
                )
    except Exception as _v_exc:
        _log.warning(
            f"V3274_OR_TOOLS_VIOLATION_CHECK exc={type(_v_exc).__name__}: "
            f"{repr(_v_exc)[:120]}"
        )

    return plan
