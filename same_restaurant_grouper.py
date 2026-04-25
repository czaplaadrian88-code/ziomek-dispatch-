"""V3.26 Fix 7 same-restaurant grouping (sprint 2026-04-25 sobota).

Adrian's specification: pre-processing przed TSP. Grupujemy ordery z tej samej
restauracji TYLKO gdy:
1. czas_kuriera tolerance ±5 minut
2. Drop quadrant compatibility (same lub adjacent w BIALYSTOK_DISTRICT_ADJACENCY)

Edge cases:
- 3+ orderów same restaurant: greedy partial grouping
  (largest valid group first, reszta osobno)
- Restaurant z różnymi czasami: NIE grupujemy
- Restaurant z czasami ±5 min ale dropy distant: NIE grupujemy

Eliminates dual-pickup runs dla compatible orders. Preserves separate pickups
dla incompatible (np. #468404: Doner Kebab Choroszcz vs Absolwentów = 24 min
apart + distant quadrants → NIE grupujemy).

Pure module. Zero side effects.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional, Sequence, Tuple
import logging

log = logging.getLogger("same_restaurant_grouper")

# Default time tolerance + driver default
GROUP_TIME_TOLERANCE_MIN = 5.0


@dataclass
class GroupedOrders:
    """Group N>=2 orders z tej samej restauracji + compatible drops."""
    restaurant: str
    pickup_coords: Tuple[float, float]
    czas_kuriera: Optional[datetime]  # min czas spośród grupy (anchor)
    orders: List[Any]  # OrderSim-like list (>= 2)


@dataclass
class SingletonOrder:
    """Standalone order — sam, NIE grupowany."""
    order: Any  # OrderSim-like


def _to_aware_utc(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    if not isinstance(ts, datetime):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def are_orders_groupable(
    o1: Any,
    o2: Any,
    drop_zone_resolver,
    adjacency_map: dict,
    time_tolerance_min: float = GROUP_TIME_TOLERANCE_MIN,
) -> bool:
    """True jeśli oba: czas_kuriera ±tolerance AND drop quadrants compatible.

    Args:
        o1, o2: OrderSim-like z atrybutami pickup_ready_at + delivery_address (str).
        drop_zone_resolver: callable (address: str) → district_name lub None.
        adjacency_map: dict[district → set[neighbors]] (BIALYSTOK_DISTRICT_ADJACENCY).
        time_tolerance_min: max delta between czas_kuriera (default 5 min).
    """
    t1 = _to_aware_utc(getattr(o1, "pickup_ready_at", None))
    t2 = _to_aware_utc(getattr(o2, "pickup_ready_at", None))
    if t1 is None or t2 is None:
        return False
    delta_min = abs((t1 - t2).total_seconds()) / 60.0
    if delta_min > time_tolerance_min:
        return False

    addr1 = getattr(o1, "delivery_address", None) or getattr(o1, "drop_address", None)
    addr2 = getattr(o2, "delivery_address", None) or getattr(o2, "drop_address", None)
    if not addr1 or not addr2:
        return False
    try:
        q1 = drop_zone_resolver(addr1)
        q2 = drop_zone_resolver(addr2)
    except Exception:
        return False
    if q1 is None or q2 is None:
        return False
    if q1 == q2:
        return True
    # Adjacency check (symmetric): q1 in q2's neighbors OR q2 in q1's neighbors
    n1 = adjacency_map.get(q1, set())
    n2 = adjacency_map.get(q2, set())
    return q2 in n1 or q1 in n2


def _build_valid_groups_for_restaurant(
    orders_same_restaurant: List[Any],
    drop_zone_resolver,
    adjacency_map: dict,
    time_tolerance_min: float = GROUP_TIME_TOLERANCE_MIN,
) -> List[Any]:
    """Greedy partial grouping dla 3+ orderów same restaurant.
    Take seed (earliest czas_kuriera), expand grupę dopóki wszystkie compatible.
    Reszta orderów → recursive call.
    Returns list of GroupedOrders + SingletonOrder.
    """
    if not orders_same_restaurant:
        return []

    # Sort ascending by czas_kuriera (earliest first → seed)
    def _sort_key(o):
        t = _to_aware_utc(getattr(o, "pickup_ready_at", None))
        return t if t is not None else datetime.max.replace(tzinfo=timezone.utc)

    sorted_orders = sorted(orders_same_restaurant, key=_sort_key)
    result: List[Any] = []
    remaining = list(sorted_orders)

    while remaining:
        seed = remaining[0]
        group: List[Any] = [seed]
        unmatched: List[Any] = []
        for cand in remaining[1:]:
            # Compatibility z WSZYSTKIMI w group (transitive check)
            ok = all(
                are_orders_groupable(g, cand, drop_zone_resolver, adjacency_map, time_tolerance_min)
                for g in group
            )
            if ok:
                group.append(cand)
            else:
                unmatched.append(cand)

        if len(group) >= 2:
            # Build GroupedOrders. Restaurant + pickup_coords z seed (all same).
            pickup_coords = getattr(seed, "pickup_coords", None)
            restaurant = (
                getattr(seed, "restaurant", None)
                or getattr(seed, "restaurant_address", None)
            )
            czas = _to_aware_utc(getattr(seed, "pickup_ready_at", None))
            result.append(GroupedOrders(
                restaurant=restaurant,
                pickup_coords=pickup_coords,
                czas_kuriera=czas,
                orders=group,
            ))
        else:
            result.append(SingletonOrder(order=seed))

        remaining = unmatched

    return result


def group_orders_by_restaurant(
    bag_orders: Sequence[Any],
    drop_zone_resolver,
    adjacency_map: dict,
    time_tolerance_min: float = GROUP_TIME_TOLERANCE_MIN,
) -> List[Any]:
    """Pre-processing przed TSP — group bag orders z same restaurant gdy compatible.

    Args:
        bag_orders: OrderSim-like list. Każdy ma `restaurant` (str) + `pickup_ready_at`
                   (datetime) + `delivery_address` (str).
        drop_zone_resolver: callable (address) → district_name. Z common.drop_zone_from_address.
        adjacency_map: BIALYSTOK_DISTRICT_ADJACENCY dict.
        time_tolerance_min: ±5 min default per Adrian's spec.

    Returns:
        List of GroupedOrders | SingletonOrder. Order preserved per restaurant
        chronologically (earliest czas_kuriera first).

    Empty bag_orders → empty list.
    """
    if not bag_orders:
        return []

    # Bucket by restaurant
    by_restaurant: dict = {}
    for o in bag_orders:
        r = (
            getattr(o, "restaurant", None)
            or getattr(o, "restaurant_address", None)
            or "<UNKNOWN>"
        )
        by_restaurant.setdefault(r, []).append(o)

    result: List[Any] = []
    for restaurant, ords in by_restaurant.items():
        if len(ords) == 1:
            result.append(SingletonOrder(order=ords[0]))
            continue
        # 2+ orders same restaurant — try to group
        groups = _build_valid_groups_for_restaurant(
            ords, drop_zone_resolver, adjacency_map, time_tolerance_min
        )
        result.extend(groups)

    return result
