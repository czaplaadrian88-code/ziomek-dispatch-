"""V3.26 Fix 7 same-restaurant grouping tests (sprint 2026-04-25 sobota).

Per Adrian's specification:
- Group orders z tej samej restauracji TYLKO gdy czas_kuriera ±5 min
  AND drop quadrants compatible (same lub adjacent w
  BIALYSTOK_DISTRICT_ADJACENCY).
- 3+ orderów same restaurant → greedy partial grouping
- Edge cases: różne czasy / distant quadrants → separate pickups
"""
import sys
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.same_restaurant_grouper import (  # noqa: E402
    GroupedOrders,
    SingletonOrder,
    are_orders_groupable,
    group_orders_by_restaurant,
)


# Mock adjacency map (subset of BIALYSTOK_DISTRICT_ADJACENCY)
MOCK_ADJACENCY = {
    "Centrum": {"Mickiewicza", "Bojary", "Sienkiewicza", "Piaski"},
    "Mickiewicza": {"Centrum", "Dojlidy", "Skorupy"},
    "Bojary": {"Centrum", "Mickiewicza", "Sienkiewicza"},
    "Sienkiewicza": {"Centrum", "Bojary"},
    "Antoniuk": {"Bacieczki", "Wysoki Stoczek"},
    "Bacieczki": {"Antoniuk", "Choroszcz"},
    "Choroszcz": {"Bacieczki"},
    "Bema": {"Nowe Miasto", "Kawaleryjskie"},
    "Nowe Miasto": {"Bema", "Kawaleryjskie"},
    "Kawaleryjskie": {"Bema", "Nowe Miasto"},
    "Piaski": {"Centrum"},
}


def _resolve_zone(addr: str) -> Optional[str]:
    """Mock drop_zone_from_address — pattern match."""
    if not addr:
        return None
    a = addr.lower()
    if "centrum" in a or "kilińskiego" in a or "łąkowa" in a:
        return "Centrum"
    if "mickiewicza" in a:
        return "Mickiewicza"
    if "bojary" in a or "sikorskiego" in a:
        return "Bojary"
    if "sienkiewicza" in a:
        return "Sienkiewicza"
    if "antoniuk" in a or "bacieczki" in a or "narodowych" in a:
        return "Bacieczki"
    if "choroszcz" in a or "brodowicza" in a:
        return "Choroszcz"
    if "bema" in a or "absolwentów" in a:
        return "Bema"
    if "nowe miasto" in a:
        return "Nowe Miasto"
    return "Centrum"  # fallback default


@dataclass
class _MockOrder:
    order_id: str
    restaurant: str
    pickup_coords: Tuple[float, float] = (53.13, 23.16)
    delivery_address: str = ""
    pickup_ready_at: Optional[datetime] = None


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


def test_groupable_same_time_same_quadrant_yes():
    o1 = _MockOrder("100", "Mama Thai", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    o2 = _MockOrder("101", "Mama Thai", delivery_address="centrum 5",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    assert are_orders_groupable(o1, o2, _resolve_zone, MOCK_ADJACENCY) is True


def test_groupable_within_5_min_adjacent_quadrant_yes():
    o1 = _MockOrder("100", "Sushi", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    o2 = _MockOrder("101", "Sushi", delivery_address="mickiewicza 5",  # adjacent z Centrum
                    pickup_ready_at=_utc("2026-04-25T11:03:00+00:00"))  # +3 min
    assert are_orders_groupable(o1, o2, _resolve_zone, MOCK_ADJACENCY) is True


def test_groupable_time_too_far_no():
    o1 = _MockOrder("100", "Doner", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T10:04:00+00:00"))
    o2 = _MockOrder("101", "Doner", delivery_address="centrum 5",
                    pickup_ready_at=_utc("2026-04-25T10:28:00+00:00"))  # +24 min > 5
    assert are_orders_groupable(o1, o2, _resolve_zone, MOCK_ADJACENCY) is False


def test_groupable_quadrants_distant_no():
    o1 = _MockOrder("100", "Doner", delivery_address="absolwentów 4",  # Bema
                    pickup_ready_at=_utc("2026-04-25T10:04:00+00:00"))
    o2 = _MockOrder("101", "Doner", delivery_address="brodowicza 1",  # Choroszcz
                    pickup_ready_at=_utc("2026-04-25T10:04:00+00:00"))  # same time
    # Bema NOT in Choroszcz neighbors, Choroszcz NOT in Bema neighbors
    assert are_orders_groupable(o1, o2, _resolve_zone, MOCK_ADJACENCY) is False


def test_468404_doner_orders_NOT_grouped():
    """Adrian's #468404 case: 468401 czas_kuriera 11:09 → Choroszcz +
    468402 czas_kuriera 10:04 → Absolwentów (Bema). NIE grupować
    (24+ min apart + distant quadrants)."""
    o468401 = _MockOrder("468401", "Doner Kebab", delivery_address="brodowicza 1",
                         pickup_ready_at=_utc("2026-04-25T11:09:00+00:00"))
    o468402 = _MockOrder("468402", "Doner Kebab", delivery_address="absolwentów 4",
                         pickup_ready_at=_utc("2026-04-25T10:04:00+00:00"))
    result = group_orders_by_restaurant(
        [o468401, o468402], _resolve_zone, MOCK_ADJACENCY
    )
    # Expected: 2 SingletonOrder (NIE GroupedOrders)
    assert len(result) == 2
    types = {type(g).__name__ for g in result}
    assert types == {"SingletonOrder"}, f"expected only SingletonOrder, got {types}"


def test_two_compatible_orders_grouped():
    """2 orders Mama Thai obie 11:00 obie Centrum → GROUP."""
    o1 = _MockOrder("100", "Mama Thai", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    o2 = _MockOrder("101", "Mama Thai", delivery_address="centrum 5",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    result = group_orders_by_restaurant([o1, o2], _resolve_zone, MOCK_ADJACENCY)
    assert len(result) == 1
    grp = result[0]
    assert isinstance(grp, GroupedOrders)
    assert len(grp.orders) == 2
    assert grp.restaurant == "Mama Thai"


def test_three_orders_partial_grouping():
    """3 orders Rany Julek mixed: 11:00 centrum, 11:03 centrum, 11:30 antoniuk.
    Greedy: group(1+2), singleton(3)."""
    o1 = _MockOrder("100", "Rany Julek", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    o2 = _MockOrder("101", "Rany Julek", delivery_address="centrum 5",
                    pickup_ready_at=_utc("2026-04-25T11:03:00+00:00"))
    o3 = _MockOrder("102", "Rany Julek", delivery_address="antoniuk 1",
                    pickup_ready_at=_utc("2026-04-25T11:30:00+00:00"))
    result = group_orders_by_restaurant(
        [o1, o2, o3], _resolve_zone, MOCK_ADJACENCY
    )
    assert len(result) == 2
    grouped = [g for g in result if isinstance(g, GroupedOrders)]
    singletons = [g for g in result if isinstance(g, SingletonOrder)]
    assert len(grouped) == 1
    assert len(singletons) == 1
    assert len(grouped[0].orders) == 2
    assert singletons[0].order.order_id == "102"


def test_different_restaurants_not_grouped():
    """Even if czas + drops match, różne restauracje → osobne."""
    o1 = _MockOrder("100", "Sushi 80", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    o2 = _MockOrder("101", "Mama Thai", delivery_address="centrum 5",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    result = group_orders_by_restaurant([o1, o2], _resolve_zone, MOCK_ADJACENCY)
    assert len(result) == 2
    types = {type(g).__name__ for g in result}
    assert types == {"SingletonOrder"}


def test_empty_bag_returns_empty():
    assert group_orders_by_restaurant([], _resolve_zone, MOCK_ADJACENCY) == []


def test_singleton_order_sole_passes_through():
    o1 = _MockOrder("100", "Solo", delivery_address="centrum 1",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    result = group_orders_by_restaurant([o1], _resolve_zone, MOCK_ADJACENCY)
    assert len(result) == 1
    assert isinstance(result[0], SingletonOrder)
    assert result[0].order.order_id == "100"


def test_pickup_ready_at_none_returns_false():
    """Brak czas_kuriera → NIE grupować (defensive)."""
    o1 = _MockOrder("100", "X", delivery_address="centrum 1",
                    pickup_ready_at=None)
    o2 = _MockOrder("101", "X", delivery_address="centrum 5",
                    pickup_ready_at=_utc("2026-04-25T11:00:00+00:00"))
    assert are_orders_groupable(o1, o2, _resolve_zone, MOCK_ADJACENCY) is False


if __name__ == "__main__":
    test_groupable_same_time_same_quadrant_yes()
    print("test_groupable_same_time_same_quadrant_yes: PASS")
    test_groupable_within_5_min_adjacent_quadrant_yes()
    print("test_groupable_within_5_min_adjacent_quadrant_yes: PASS")
    test_groupable_time_too_far_no()
    print("test_groupable_time_too_far_no: PASS")
    test_groupable_quadrants_distant_no()
    print("test_groupable_quadrants_distant_no: PASS")
    test_468404_doner_orders_NOT_grouped()
    print("test_468404_doner_orders_NOT_grouped: PASS")
    test_two_compatible_orders_grouped()
    print("test_two_compatible_orders_grouped: PASS")
    test_three_orders_partial_grouping()
    print("test_three_orders_partial_grouping: PASS")
    test_different_restaurants_not_grouped()
    print("test_different_restaurants_not_grouped: PASS")
    test_empty_bag_returns_empty()
    print("test_empty_bag_returns_empty: PASS")
    test_singleton_order_sole_passes_through()
    print("test_singleton_order_sole_passes_through: PASS")
    test_pickup_ready_at_none_returns_false()
    print("test_pickup_ready_at_none_returns_false: PASS")
    print("ALL 11/11 PASS")
