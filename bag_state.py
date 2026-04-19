"""bag_state.py — V3.18 unified bag state projection.

Single source of truth for courier bag contents at time t.
Adresuje fragmentację widoków bagu (orders_state.cid vs panel_packs vs
plan.sequence vs ad-hoc bag_size) przez immutable CourierBagState +
OrderInBag dataclasses.

Warsaw TZ obowiązkowe dla wszystkich timestampów (computed_at, pickup_time,
predicted_drop_time, added_at).
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

# Panel status mapping (id_status_zamowienia):
#   2 = nowe/nieprzypisane
#   3 = dojazd (kurier w drodze do restauracji)
#   4 = oczekiwanie (kurier pod restauracją, czeka na pickup)
#   5 = odebrane (picked up)
#   6 = opoznienie
#   7 = doreczone (terminal, NIE w bagu)
#   8 = nieodebrano, 9 = anulowane (terminal)
PICKED_UP_STATUSES = frozenset({5, 6})
ACTIVE_STATUSES = frozenset({3, 4, 5, 6})


@dataclass(frozen=True)
class OrderInBag:
    """Single order w bagu kuriera. Immutable projection."""
    order_id: str
    restaurant_address: str
    restaurant_coords: Optional[Tuple[float, float]]
    drop_address: str
    drop_coords: Optional[Tuple[float, float]]
    pickup_time: Optional[datetime]          # Warsaw-aware, z czas_odbioru_timestamp
    predicted_drop_time: Optional[datetime]  # Warsaw-aware, >= pickup + drive (constraint)
    status: int                               # 3/4/5/6 (active)
    added_at: Optional[datetime]             # Warsaw-aware, kiedy trafił do bagu

    @property
    def is_picked_up(self) -> bool:
        return self.status in PICKED_UP_STATUSES

    @property
    def needs_pickup(self) -> bool:
        """True iff order is active and still needs pickup (status 3/4)."""
        return self.status in ACTIVE_STATUSES and not self.is_picked_up


@dataclass(frozen=True)
class CourierBagState:
    """Stan bagu kuriera w momencie computed_at. Source of truth dla wszystkich consumerów."""
    courier_id: str
    nick: str
    pos_source: str                          # gps/no_gps/pre_shift/panel_packs_fallback
    position: Optional[Tuple[float, float]]
    orders: Tuple[OrderInBag, ...]           # immutable (tuple, nie list)
    computed_at: datetime                     # Warsaw-aware

    @property
    def bag_size(self) -> int:
        return len(self.orders)

    @property
    def is_free(self) -> bool:
        """True iff zero active orders w bagu. Source dla telegram 'wolny' tag (V3.18 Bug 3)."""
        return len(self.orders) == 0

    @property
    def has_unpicked_orders(self) -> bool:
        """True if any bag order needs pickup (status 3/4). Trigger dla Bug 1 constraint."""
        return any(o.needs_pickup for o in self.orders)

    @property
    def picked_up_orders(self) -> Tuple[OrderInBag, ...]:
        return tuple(o for o in self.orders if o.is_picked_up)

    @property
    def pending_pickup_orders(self) -> Tuple[OrderInBag, ...]:
        return tuple(o for o in self.orders if o.needs_pickup)


def _ensure_warsaw(dt: Optional[datetime]) -> Optional[datetime]:
    """Normalizuje datetime do Warsaw-aware. None → None."""
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=WARSAW)
    return dt.astimezone(WARSAW)


def build_courier_bag_state(
    courier_id: str,
    nick: str,
    pos_source: str,
    position: Optional[Tuple[float, float]],
    orders_raw: List[dict],
    now: Optional[datetime] = None,
) -> CourierBagState:
    """Build immutable CourierBagState z raw data.

    orders_raw: list[dict] z kluczami (wszystkie opcjonalne, None dozwolone):
      order_id (str|int), restaurant_address (str), restaurant_coords (tuple),
      drop_address (str), drop_coords (tuple), pickup_time (datetime),
      predicted_drop_time (datetime), status (int), added_at (datetime)

    now: Warsaw-aware datetime; default datetime.now(WARSAW).
    """
    if now is None:
        now = datetime.now(timezone.utc).astimezone(WARSAW)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=WARSAW)
    else:
        now = now.astimezone(WARSAW)

    orders: List[OrderInBag] = []
    for raw in orders_raw:
        if not isinstance(raw, dict):
            continue
        try:
            status = int(raw.get("status", 3))
        except (ValueError, TypeError):
            status = 3
        # Wykluczamy terminal statusy (7/8/9 NIE w bagu, mimo że caller mógłby przekazać)
        if status not in ACTIVE_STATUSES:
            continue
        orders.append(OrderInBag(
            order_id=str(raw.get("order_id", "")),
            restaurant_address=str(raw.get("restaurant_address", "")),
            restaurant_coords=raw.get("restaurant_coords"),
            drop_address=str(raw.get("drop_address", "")),
            drop_coords=raw.get("drop_coords"),
            pickup_time=_ensure_warsaw(raw.get("pickup_time")),
            predicted_drop_time=_ensure_warsaw(raw.get("predicted_drop_time")),
            status=status,
            added_at=_ensure_warsaw(raw.get("added_at")),
        ))

    return CourierBagState(
        courier_id=str(courier_id),
        nick=nick,
        pos_source=pos_source,
        position=position,
        orders=tuple(orders),
        computed_at=now,
    )
