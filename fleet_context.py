"""fleet_context.py — V3.18 fleet-wide aggregate context.

Zapewnia scoring.py + wave_scoring.py widok "co reszta floty robi" dla
overload penalty (Bug 2). Per-courier scoring bez fleet context widzi
kuriera w izolacji i nie wie że dostaje 6. order podczas gdy inni
mają 2.

Warsaw TZ obowiązkowe dla snapshot_at.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from dispatch_v2.bag_state import CourierBagState

WARSAW = ZoneInfo("Europe/Warsaw")


@dataclass(frozen=True)
class FleetContext:
    """Aggregate snapshot floty w danym momencie."""
    active_couriers: int                    # kuriery "aktywni" (bag>0 lub pos_source=gps)
    avg_bag: float                           # średnia wielkość bagu wśród aktywnych (0.0 jeśli empty fleet)
    max_bag: int                             # max bag w całej flocie
    bag_distribution: Dict[int, int]         # {bag_size: count} — readonly view
    total_couriers_snapshot: int            # wszystkie kuriery w snapshocie (incl. inactive)
    snapshot_at: datetime                    # Warsaw-aware

    @property
    def is_empty(self) -> bool:
        return self.active_couriers == 0

    def overload_delta(self, courier_bag_size: int) -> int:
        """Zwraca (bag - avg - threshold). >0 = overloaded relative to fleet.

        Zero gdy fleet empty (brak ref).
        """
        if self.is_empty:
            return 0
        return int(courier_bag_size - self.avg_bag)


def build_fleet_context(
    bag_states: Iterable[CourierBagState],
    now: Optional[datetime] = None,
) -> FleetContext:
    """Build FleetContext z iterable CourierBagState.

    active_couriers filter: bag_size > 0 OR pos_source == "gps". Wyklucza
    pre_shift kurierów z pustym bagiem (wciągają avg w dół fałszywie).
    """
    if now is None:
        now = datetime.now(timezone.utc).astimezone(WARSAW)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=WARSAW)
    else:
        now = now.astimezone(WARSAW)

    total = 0
    active_bags = []
    distribution: Dict[int, int] = {}
    max_bag = 0

    for cbs in bag_states:
        total += 1
        b = cbs.bag_size
        if b > max_bag:
            max_bag = b
        distribution[b] = distribution.get(b, 0) + 1
        is_active = (b > 0) or (cbs.pos_source == "gps")
        if is_active:
            active_bags.append(b)

    avg = (sum(active_bags) / len(active_bags)) if active_bags else 0.0

    return FleetContext(
        active_couriers=len(active_bags),
        avg_bag=round(avg, 2),
        max_bag=max_bag,
        bag_distribution=dict(distribution),
        total_couriers_snapshot=total,
        snapshot_at=now,
    )
