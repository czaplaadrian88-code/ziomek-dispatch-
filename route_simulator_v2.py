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
from dispatch_v2.common import ENABLE_DROP_TIME_CONSTRAINT, ENABLE_PICKED_UP_DROP_FLOOR


DWELL_PICKUP_MIN = 2.0
DWELL_DROPOFF_MIN = 1.0
BRUTEFORCE_MAX_BAG_AFTER = 3  # per D19


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

    # Build node list: [courier, *bag_deliveries, (new_pickup?), new_delivery]
    nodes: List[dict] = [{"kind": "courier", "coords": courier_pos, "order_id": None, "ref": None}]
    bag_delivery_idxs: List[int] = []
    for o in bag:
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
    use_sticky = False
    sticky_bag_idxs: Optional[List[int]] = None
    if base_sequence is not None and bag:
        bag_oid_to_idx = {o.order_id: bag_delivery_idxs[i] for i, o in enumerate(bag)}
        if set(base_sequence) == set(bag_oid_to_idx.keys()) \
                and len(base_sequence) == len(bag_oid_to_idx):
            sticky_bag_idxs = [bag_oid_to_idx[oid] for oid in base_sequence]
            use_sticky = True

    bag_after_add = len(bag) + 1

    if use_sticky:
        plan = _sticky_sequence_plan(
            nodes, leg_min, sticky_bag_idxs,
            new_pickup_idx, new_delivery_idx,
            new_order, bag, now, sla_minutes,
        )
        plan.strategy = "sticky"
    elif bag_after_add <= BRUTEFORCE_MAX_BAG_AFTER:
        plan = _bruteforce_plan(
            nodes, leg_min, bag_delivery_idxs,
            new_pickup_idx, new_delivery_idx,
            new_order, bag, now, sla_minutes,
        )
        plan.strategy = "bruteforce"
    else:
        plan = _greedy_plan(
            nodes, leg_min, bag_delivery_idxs,
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
    best: Optional[RoutePlanV2] = None
    best_key = (10 ** 9, float("inf"))
    n = len(sticky_bag_idxs)

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
            key = (plan.sla_violations, plan.total_duration_min)
            if key < best_key:
                best = plan
                best_key = key
    return best


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
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    to_place: List[int] = list(bag_delivery_idxs) + [new_delivery_idx]
    if new_pickup_idx is not None:
        to_place.append(new_pickup_idx)

    # Lock first stop: gdy kurier wiezie jedzenie (bag niepusty), pierwszy
    # node MUSI być dostarczeniem czegoś z baga — żadnych zawrotów do nowej
    # restauracji z jedzeniem w torbie. Bag items są w simulatorze traktowane
    # jako już picked_up (nie mają pickup nodes).
    bag_set = set(bag_delivery_idxs)
    lock_first = bool(bag_set)

    best: Optional[RoutePlanV2] = None
    best_key = (10 ** 9, float("inf"))
    for perm in permutations(to_place):
        if lock_first and perm[0] not in bag_set:
            continue
        if new_pickup_idx is not None:
            pi = perm.index(new_pickup_idx)
            di = perm.index(new_delivery_idx)
            if pi > di:
                continue
        plan = _plan_from_sequence(list(perm), nodes, leg_min, new_order, bag, now, sla_minutes)
        key = (plan.sla_violations, plan.total_duration_min)
        if key < best_key:
            best = plan
            best_key = key
    return best


def _greedy_plan(
    nodes, leg_min, bag_delivery_idxs,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    # Step 1: nearest-neighbor ordering of existing bag starting from courier.
    seq_base: List[int] = []
    remaining = list(bag_delivery_idxs)
    current = 0
    while remaining:
        nxt = min(remaining, key=lambda idx: leg_min(current, idx))
        seq_base.append(nxt)
        remaining.remove(nxt)
        current = nxt

    # Step 2: try every (pickup_pos, delivery_pos) insertion for new_order.
    # Lock first stop: jeśli bag niepusty, nowy pickup NIE może być przed pierwszą
    # bagową dostawą (kurier wiezie jedzenie → najpierw dostarcz).
    lock_first = len(seq_base) > 0
    best: Optional[RoutePlanV2] = None
    best_key = (10 ** 9, float("inf"))
    n = len(seq_base)
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
            key = (plan.sla_violations, plan.total_duration_min)
            if key < best_key:
                best = plan
                best_key = key
    return best
