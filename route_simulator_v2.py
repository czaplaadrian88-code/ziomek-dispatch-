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


def simulate_bag_route_v2(
    courier_pos: Tuple[float, float],
    bag: List[OrderSim],
    new_order: OrderSim,
    now: Optional[datetime] = None,
    sla_minutes: int = 35,
) -> RoutePlanV2:
    """Hybrid simulator. Never returns None (osrm_client has fallback)."""
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

    bag_after_add = len(bag) + 1

    if bag_after_add <= BRUTEFORCE_MAX_BAG_AFTER:
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
    delivery_order = [nodes[i]["order_id"] for i in seq if nodes[i]["kind"] == "delivery"]
    return RoutePlanV2(
        sequence=delivery_order,
        predicted_delivered_at=delivered_at,
        pickup_at=pickup_at,
        total_duration_min=round(total, 1),
        strategy="",
        sla_violations=violations,
        osrm_fallback_used=False,
    )


def _bruteforce_plan(
    nodes, leg_min, bag_delivery_idxs,
    new_pickup_idx, new_delivery_idx,
    new_order, bag, now, sla_minutes,
) -> RoutePlanV2:
    to_place: List[int] = list(bag_delivery_idxs) + [new_delivery_idx]
    if new_pickup_idx is not None:
        to_place.append(new_pickup_idx)

    best: Optional[RoutePlanV2] = None
    best_key = (10 ** 9, float("inf"))
    for perm in permutations(to_place):
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
    best: Optional[RoutePlanV2] = None
    best_key = (10 ** 9, float("inf"))
    n = len(seq_base)
    for d_pos in range(n + 1):
        pickup_positions = [None] if new_pickup_idx is None else list(range(0, d_pos + 1))
        for p_pos in pickup_positions:
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
