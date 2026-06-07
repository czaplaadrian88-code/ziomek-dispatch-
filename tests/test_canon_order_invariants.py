"""F6 — twarde niezmienniki kolejności w DECYZJI kanonu.

Te same reguły co build_view (carried-first + odbiory wg committed), ale w
plan_recheck → kanon poprawny i IDENTYCZNY na wszystkich powierzchniach
(apka/panele/Telegram), niezależnie od pilności R6. Fail-safe 'dostawa po odbiorze'.
"""
from dispatch_v2 import plan_recheck as PR


def _seq(stops):
    return [(s["type"], s["order_id"]) for s in stops]


def _ck(hhmm):
    return f"2026-06-07T{hhmm}:00+02:00"


def test_carried_dropoff_to_front():
    # niesione B na końcu → na przód (Wysocki: Magnoliowa odebrana, dawana jako #6)
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:30")},
              "B": {"status": "picked_up"}}
    stops = [{"order_id": "A", "type": "pickup"}, {"order_id": "A", "type": "dropoff"},
             {"order_id": "B", "type": "dropoff"}]
    out = PR._apply_canon_order_invariants(stops, orders)
    assert _seq(out) == [("dropoff", "B"), ("pickup", "A"), ("dropoff", "A")]


def test_pickups_sorted_by_committed():
    # Romańczuk: Grill 18:15 przed Rany 18:06 → posortuj na Rany pierwszy
    orders = {"RANY": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:06")},
              "GRILL": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:15")}}
    stops = [{"order_id": "GRILL", "type": "pickup"}, {"order_id": "RANY", "type": "pickup"},
             {"order_id": "GRILL", "type": "dropoff"}, {"order_id": "RANY", "type": "dropoff"}]
    out = PR._apply_canon_order_invariants(stops, orders)
    assert _seq(out)[:2] == [("pickup", "RANY"), ("pickup", "GRILL")]


def test_combined_carried_then_committed():
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:15")},
              "B": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:06")},
              "C": {"status": "picked_up"}}
    # pickupy A,B konsekutywne (nie przeplecione dropoffem) → reorder zadziała
    stops = [{"order_id": "C", "type": "dropoff"},
             {"order_id": "A", "type": "pickup"}, {"order_id": "B", "type": "pickup"},
             {"order_id": "A", "type": "dropoff"}, {"order_id": "B", "type": "dropoff"}]
    out = PR._apply_canon_order_invariants(stops, orders)
    assert _seq(out)[0] == ("dropoff", "C")           # carried front
    assert _seq(out)[1:3] == [("pickup", "B"), ("pickup", "A")]  # B(18:06) < A(18:15)


def test_failsafe_blocks_delivery_before_pickup():
    # przeplecione (p:A d:A p:B d:B) — swap odbiorów złamałby 'dostawa po odbiorze'
    # → fail-safe trzyma oryginał (ta sama ostrożność co build_view).
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:20")},
              "B": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:05")}}
    stops = [{"order_id": "A", "type": "pickup"}, {"order_id": "A", "type": "dropoff"},
             {"order_id": "B", "type": "pickup"}, {"order_id": "B", "type": "dropoff"}]
    out = PR._apply_canon_order_invariants(stops, orders)
    assert _seq(out) == _seq(stops)  # bez zmian (fail-safe)


def test_already_correct_is_noop():
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:06")},
              "B": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:15")}}
    stops = [{"order_id": "A", "type": "pickup"}, {"order_id": "B", "type": "pickup"},
             {"order_id": "A", "type": "dropoff"}, {"order_id": "B", "type": "dropoff"}]
    assert _seq(PR._apply_canon_order_invariants(stops, orders)) == _seq(stops)


def test_no_carried_no_pickup_reorder_needed():
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:06")}}
    stops = [{"order_id": "A", "type": "pickup"}, {"order_id": "A", "type": "dropoff"}]
    assert _seq(PR._apply_canon_order_invariants(stops, orders)) == _seq(stops)
