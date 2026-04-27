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
from dataclasses import dataclass
from itertools import permutations

from dispatch_v2 import osrm_client
from dispatch_v2.common import (
    ENABLE_DROP_TIME_CONSTRAINT,
    ENABLE_PICKED_UP_DROP_FLOOR,
    ENABLE_V319E_PRE_PICKUP_BAG,
)
import logging as _logging

_log = _logging.getLogger("route_simulator_v2")


DWELL_PICKUP_MIN = 2.0   # V3.27.3 rollback 27.04 (was 3.0 V3.27.2; pre-V3.27.2 was 2.0). Symmetric 2.0/2.0.
DWELL_DROPOFF_MIN = 2.0  # V3.27.3 rollback 27.04 (was 3.0 V3.27.2; pre-V3.27.2 was 1.0). Symmetric 2.0/2.0.
BRUTEFORCE_MAX_BAG_AFTER = 3  # per D19

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


def _select_best_with_tie_breaker(
    plans: List["RoutePlanV2"],
    now: datetime,
    threshold_min: float = V327_TIE_BREAKER_THRESHOLD_MIN,
) -> Optional["RoutePlanV2"]:
    """V3.27 Bug Y: select best plan z tie-breaker.

    1. Sort by primary key (sla_violations, total_duration_min) ASC
    2. Identify ties: plans with same sla_violations AND
       |total_duration_min - leader| < threshold_min
    3. If ≥2 ties AND ENABLE_V327_BUG_FIXES_BUNDLE flag True:
       secondary sort by first_drop_arrival_min ASC, return first
    4. Else return leader (legacy behavior).
    """
    if not plans:
        return None
    # Primary sort
    plans_sorted = sorted(
        plans,
        key=lambda p: (p.sla_violations, p.total_duration_min),
    )
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
        if p.sla_violations == leader.sla_violations
        and abs(p.total_duration_min - leader.total_duration_min) < threshold_min
    ]
    if len(ties) < 2:
        return leader

    # V3.27 tie-breaker: shortest first drop arrival ASC
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


def simulate_bag_route_v2(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: Optional[datetime] = None,
    sla_minutes: int = 35,
    base_sequence: Optional[List[str]] = None,
) -> RoutePlanV2:
    """Hybrid simulator. Never returns None (osrm_client has fallback).

    V3.19d: gdy `base_sequence` podane (lista bag order_ids w preferowanej
    kolejności) — bag dropoffs są lockowane w tej kolejności; simulator
    iteruje tylko pozycje insertion new_order zamiast pełnego TSP. Jeśli
    base_sequence jest mismatched z bag → fallback do fresh TSP.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

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
            return 9999.0
        dur_s = cell.get("duration_s") or 0
        return dur_s / 60.0

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
    return _select_best_with_tie_breaker(all_sticky_plans, now)


# ---- internals ----

def _simulate_sequence(
    nodes: List[dict],
    leg_min,
    seq: List[int],
    now: datetime,
) -> Tuple[float, Dict[str, datetime], Dict[str, datetime]]:
    """Walk nodes in order. Returns (total_min, delivered_at, pickup_at)."""
    current = 0  # courier
    t = now
    delivered_at: Dict[str, datetime] = {}
    pickup_at: Dict[str, datetime] = {}
    for idx in seq:
        t = t + timedelta(minutes=leg_min(current, idx))
        node = nodes[idx]
        if node["kind"] == "pickup":
            ready = getattr(node["ref"], "pickup_ready_at", None)
            if ready is not None:
                if ready.tzinfo is None:
                    ready = ready.replace(tzinfo=timezone.utc)
                ready_utc = ready.astimezone(timezone.utc)
                if t < ready_utc:
                    t = ready_utc  # wait at restaurant (prep_variance)
            t = t + timedelta(minutes=DWELL_PICKUP_MIN)
            # V3.26 Fix 7: super-pickup z group_oids zapisuje pickup_at dla
            # WSZYSTKICH oidów w grupie przy single visit (kurier zabiera all
            # orders na raz z restauracji). Single DWELL_PICKUP_MIN dla całej grupy.
            group_oids = node.get("group_oids")
            if group_oids:
                for grp_oid in group_oids:
                    pickup_at[grp_oid] = t
            else:
                pickup_at[node["order_id"]] = t
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
                    min_drop_possible = ref_pra_utc + timedelta(minutes=DWELL_PICKUP_MIN)
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
                        + timedelta(minutes=DWELL_DROPOFF_MIN)
                    )
                    if t < floor_t:
                        t = floor_t
            t = t + timedelta(minutes=DWELL_DROPOFF_MIN)
            delivered_at[node["order_id"]] = t
        current = idx
    total_min = (t - now).total_seconds() / 60.0
    return total_min, delivered_at, pickup_at


def _count_sla_violations(
    delivered_at: Dict[str, datetime],
    pickup_at: Dict[str, datetime],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: datetime,
    sla_minutes: int,
) -> int:
    v = 0
    for o in list(bag) + [new_order]:
        pred = delivered_at.get(o.order_id)
        if pred is None:
            continue
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


def _compute_per_order_delivery_minutes(
    delivered_at: Dict[str, datetime],
    pickup_at: Dict[str, datetime],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: datetime,
) -> Optional[Dict[str, float]]:
    """Per-order elapsed pickup→drop in minutes (F2.2 C1).

    Iterates bag + new_order (matches _count_sla_violations scope). Pickup reference
    resolution order: pickup_at dict (this plan) → o.picked_up_at (prior) → now (last resort).
    Returns None if any order lacks delivered_at (fail-closed for C2 hard gate).
    """
    result: Dict[str, float] = {}
    for o in list(bag) + [new_order]:
        pred = delivered_at.get(o.order_id)
        if pred is None:
            return None
        if o.order_id in pickup_at:
            pu = pickup_at[o.order_id]
        elif o.picked_up_at is not None:
            pu = o.picked_up_at
            if pu.tzinfo is None:
                pu = pu.replace(tzinfo=timezone.utc)
            pu = pu.astimezone(timezone.utc)
        else:
            pu = now
        result[o.order_id] = round((pred - pu).total_seconds() / 60.0, 2)
    return result


def _plan_from_sequence(
    seq: List[int],
    nodes: List[dict],
    leg_min,
    new_order: OrderSim,
    bag: List[OrderSim],
    now: datetime,
    sla_minutes: int,
) -> RoutePlanV2:
    total, delivered_at, pickup_at = _simulate_sequence(nodes, leg_min, seq, now)
    violations = _count_sla_violations(delivered_at, pickup_at, bag, new_order, now, sla_minutes)
    per_order_times = _compute_per_order_delivery_minutes(delivered_at, pickup_at, bag, new_order, now)
    delivery_order = [nodes[i]["order_id"] for i in seq if nodes[i]["kind"] == "delivery"]
    return RoutePlanV2(
        sequence=delivery_order,
        predicted_delivered_at=delivered_at,
        pickup_at=pickup_at,
        total_duration_min=round(total, 1),
        strategy="",
        sla_violations=violations,
        osrm_fallback_used=False,
        per_order_delivery_times=per_order_times,
    )


def _bruteforce_plan(
    nodes, leg_min, bag_delivery_idxs,
    bag_pickup_idxs_by_oid,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    # V3.19e: to_place uwzględnia pickup-nodes dla pending bag items.
    to_place: List[int] = list(bag_delivery_idxs) + [new_delivery_idx]
    if new_pickup_idx is not None:
        to_place.append(new_pickup_idx)
    to_place.extend(bag_pickup_idxs_by_oid.values())

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
    return _select_best_with_tie_breaker(all_valid_plans, now)


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
    return _select_best_with_tie_breaker(all_step2_plans, now)


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

    # Build time matrix z leg_min callable (drive_min between nodes)
    time_matrix: List[List[float]] = [[0.0] * N for _ in range(N)]
    for i in range(N):
        for j in range(N):
            if i == j:
                continue
            try:
                time_matrix[i][j] = max(0.0, float(leg_min(i, j)))
            except Exception:
                time_matrix[i][j] = 9999.0
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
                    try:
                        open_min = max(0.0, (ready - now).total_seconds() / 60.0)
                        close_min = open_min + _common.V327_PICKUP_TIME_WINDOW_CLOSE_MIN
                        time_windows.append((open_min, close_min))
                    except Exception:
                        time_windows.append((0.0, _common.V327_DROP_TIME_WINDOW_MAX_MIN))
                else:
                    time_windows.append((0.0, _common.V327_DROP_TIME_WINDOW_MAX_MIN))
            else:
                time_windows.append((0.0, _common.V327_DROP_TIME_WINDOW_MAX_MIN))

    solution = tsp_solver.solve_tsp_with_constraints(
        num_stops=N,
        pickup_drop_pairs=pickup_drop_pairs,
        distance_matrix_km=distance_matrix,
        time_matrix_min=time_matrix,
        time_windows=time_windows,
        max_route_min=120.0,
        time_limit_ms=int(_ot_ms),
    )

    # V3.27.1 BUG-2 fallback: gdy INFEASIBLE z time windows, retry bez constraints
    # (ochrona przed regresją vs. baseline; flag flip nigdy nie powinien zwiększyć
    # liczby orderów które fail completely — gorsza sekwencja > brak proposal).
    if (solution is None or not getattr(solution, "sequence", None)) and time_windows is not None:
        _log.warning(
            f"V3.27.1 OR-Tools INFEASIBLE z time windows "
            f"(N={N}, pairs={len(pickup_drop_pairs)}), retry bez constraints"
        )
        solution = tsp_solver.solve_tsp_with_constraints(
            num_stops=N,
            pickup_drop_pairs=pickup_drop_pairs,
            distance_matrix_km=distance_matrix,
            time_matrix_min=time_matrix,
            time_windows=None,
            max_route_min=120.0,
            time_limit_ms=int(_ot_ms),
        )

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
    return plan
