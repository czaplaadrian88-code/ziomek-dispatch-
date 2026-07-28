"""Podjazdy wg planu z bezwarunkowym guardem spreadu committed."""
from dispatch_v2 import route_podjazdy as rp


def _bag():
    # carried Mama Thai + dwa przypisane odbiory Halva (19:33) i Eat Point (19:54) — 21 min od siebie
    return [
        {"order_id": "660", "status": "picked_up", "restaurant": "Mama Thai", "czas_kuriera_warsaw": "2026-06-22T18:53:00+02:00"},
        {"order_id": "665", "status": "assigned", "restaurant": "Halva", "czas_kuriera_warsaw": "2026-06-22T19:33:00+02:00"},
        {"order_id": "673", "status": "assigned", "restaurant": "Eat Point", "czas_kuriera_warsaw": "2026-06-22T19:54:00+02:00"},
    ]


# Plan Ziomka: odbierz Halva, odbierz Eat Point, DOPIERO potem dowieź oba (bundle).
_PLAN = {"stops": [
    {"type": "dropoff", "order_id": "660"},
    {"type": "pickup", "order_id": "665"},
    {"type": "pickup", "order_id": "673"},
    {"type": "dropoff", "order_id": "665"},
    {"type": "dropoff", "order_id": "673"},
]}


def test_plan_aware_splits_plan_cluster_above_time_guard():
    order = rp.order_podjazdy(_bag(), _PLAN, plan_aware=True)
    assert order == [
        ("dropoff", ["660"]),
        ("pickup", ["665"]),
        ("dropoff", ["665"]),
        ("pickup", ["673"]),
        ("dropoff", ["673"]),
    ]


def test_flag_off_falls_back_to_time_split():
    # 21 min > PICKUP_MERGE_MIN → dwa podjazdy → backtrack (stare zachowanie zachowane przy OFF)
    order = rp.order_podjazdy(_bag(), _PLAN, plan_aware=False)
    assert order == [
        ("dropoff", ["660"]),
        ("pickup", ["665"]),
        ("dropoff", ["665"]),
        ("pickup", ["673"]),
        ("dropoff", ["673"]),
    ]


def test_partial_plan_falls_back_to_time_split():
    # plan nie pokrywa odbioru 673 → nie ufamy klastrom, wracamy do podziału czasowego
    plan_no_673 = {"stops": [{"type": "pickup", "order_id": "665"}, {"type": "dropoff", "order_id": "665"}]}
    order = rp.order_podjazdy(_bag(), plan_no_673, plan_aware=True)
    assert order[0] == ("dropoff", ["660"])
    # 665 i 673 rozdzielone (time-split), nie skiełkowane w jeden podjazd
    assert ("pickup", ["665", "673"]) not in order
    assert ("pickup", ["665"]) in order and ("pickup", ["673"]) in order


def test_no_plan_is_time_split():
    order = rp.order_podjazdy(_bag(), None, plan_aware=True)
    assert order == [
        ("dropoff", ["660"]),
        ("pickup", ["665"]),
        ("dropoff", ["665"]),
        ("pickup", ["673"]),
        ("dropoff", ["673"]),
    ]


def test_same_restaurant_pickups_above_guard_are_separate_stops():
    bag = [
        {"order_id": "701", "status": "assigned", "restaurant": "Halva", "czas_kuriera_warsaw": "2026-06-22T19:33:00+02:00"},
        {"order_id": "702", "status": "assigned", "restaurant": "Halva", "czas_kuriera_warsaw": "2026-06-22T20:10:00+02:00"},
    ]
    plan = {"stops": [
        {"type": "pickup", "order_id": "701"},
        {"type": "pickup", "order_id": "702"},
        {"type": "dropoff", "order_id": "701"},
        {"type": "dropoff", "order_id": "702"},
    ]}
    order = rp.order_podjazdy(bag, plan, plan_aware=True)
    assert order == [
        ("pickup", ["701"]),
        ("dropoff", ["701"]),
        ("pickup", ["702"]),
        ("dropoff", ["702"]),
    ]
