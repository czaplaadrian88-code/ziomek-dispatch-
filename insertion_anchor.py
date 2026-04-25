"""V3.26 Bug A complete (sprint 2026-04-25): insertion anchor compute.

Helper module dla anchor-based distance scoring + UX clarification.

Adrian's specification: dla bag z istniejącym planem TSP-optimized i nowego
proposal insertowanego w środku, "anchor" to stop CHRONOLOGICALLY przed
new pickup w plan. Distance kuriera do new_pickup powinno być computed
od anchor location (gdzie kurier rzeczywiście będzie tuż przed pickup),
NIE od fictional "last drop" (chronological end) plan.sequence.

Pure function (zero side effects, easy unit test).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple


@dataclass
class InsertionAnchor:
    """Stop chronologicznie PRZED new order pickup w plan."""
    location: Tuple[float, float]
    timestamp: datetime
    restaurant_name: Optional[str]
    is_pickup: bool
    order_id: str  # event order_id (NIE new order — bag order or its drop)


def _to_aware_utc(ts: Any) -> Optional[datetime]:
    """Coerce ISO string lub naive/aware datetime → aware UTC datetime, None on fail."""
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


def compute_insertion_anchor(
    plan: Any,
    new_order_id: str,
    bag_orders: List[Any],
) -> Optional[InsertionAnchor]:
    """Find chronologically previous stop before new_order_id pickup w plan.

    Args:
        plan: RoutePlanV2-like obiekt z attrs sequence, pickup_at, predicted_delivered_at.
              pickup_at: dict[order_id → datetime/iso], predicted_delivered_at: same.
        new_order_id: ID new proposal order (must appear in plan.pickup_at).
        bag_orders: list of OrderSim-like objects (existing bag) — used for
                    location + name lookup. Doesn't need new_order_id entry.

    Returns:
        InsertionAnchor jeśli previous event found.
        None gdy:
          - plan is None lub plan.sequence empty
          - new_order_id NIE w plan.pickup_at
          - new_order_id pickup is FIRST event chronologicznie (no anchor)
          - anchor order's location lookup fails
    """
    if plan is None:
        return None
    sequence = getattr(plan, "sequence", None) or []
    pickup_at = getattr(plan, "pickup_at", None) or {}
    delivered_at = getattr(plan, "predicted_delivered_at", None) or {}

    if not sequence:
        return None
    new_oid_str = str(new_order_id)
    if new_oid_str not in {str(k) for k in pickup_at.keys()}:
        return None

    # Build chronological events list. Tie-break: pickup before drop (same ts
    # — rare but deterministic; same restaurant chain wave).
    events: List[Tuple[datetime, str, str]] = []
    for oid, ts in pickup_at.items():
        ts_aware = _to_aware_utc(ts)
        if ts_aware is None:
            continue
        events.append((ts_aware, "pickup", str(oid)))
    for oid, ts in delivered_at.items():
        ts_aware = _to_aware_utc(ts)
        if ts_aware is None:
            continue
        events.append((ts_aware, "drop", str(oid)))

    events.sort(key=lambda e: (e[0], 0 if e[1] == "pickup" else 1))

    # Find new pickup event index
    new_idx = None
    for i, (_, kind, oid) in enumerate(events):
        if kind == "pickup" and oid == new_oid_str:
            new_idx = i
            break

    if new_idx is None or new_idx == 0:
        return None  # not found OR insertion at chronological start (no anchor)

    anchor_ts, anchor_kind, anchor_oid = events[new_idx - 1]

    # Resolve anchor location + restaurant_name from bag_orders
    bag_by_oid = {str(getattr(o, "order_id", "")): o for o in bag_orders}
    anchor_order = bag_by_oid.get(anchor_oid)
    if anchor_order is None:
        return None

    if anchor_kind == "pickup":
        location = getattr(anchor_order, "pickup_coords", None) or getattr(
            anchor_order, "restaurant_coords", None
        )
        # OrderSim w bag uses pickup_coords; bag dict format uses restaurant_coords.
        name = (
            getattr(anchor_order, "restaurant", None)
            or getattr(anchor_order, "restaurant_address", None)
        )
    else:  # drop
        location = getattr(anchor_order, "delivery_coords", None) or getattr(
            anchor_order, "drop_coords", None
        )
        name = (
            getattr(anchor_order, "drop_address", None)
            or getattr(anchor_order, "delivery_address", None)
        )

    if location is None:
        return None
    try:
        location_tuple = (float(location[0]), float(location[1]))
    except (IndexError, TypeError, ValueError):
        return None

    return InsertionAnchor(
        location=location_tuple,
        timestamp=anchor_ts,
        restaurant_name=name,
        is_pickup=(anchor_kind == "pickup"),
        order_id=anchor_oid,
    )
