"""F6 — twarde niezmienniki kolejności w DECYZJI kanonu.

Te same reguły co build_view (carried-first + odbiory wg committed), ale w
plan_recheck → kanon poprawny i IDENTYCZNY na wszystkich powierzchniach
(apka/panele/Telegram), niezależnie od pilności R6. Fail-safe 'dostawa po odbiorze'.
"""
import pytest

from dispatch_v2 import plan_recheck as PR


@pytest.fixture(autouse=True)
def _isolate_hard_invariants(monkeypatch):
    """D.3 fala A (2026-07-02): ten plik testuje TWARDE niezmienniki F6 (carried-first
    + odbiory wg committed) w IZOLACJI. Miękkie optymalizatory (LEX-okno, non-carried
    reorder, relax, coloc, carried-age-tz) domyślnie True po migracji do flags.json →
    pinujemy do pre-migracyjnego test-defaultu (False), by nie zakłócały czystej
    asercji niezmiennika. Każdy optymalizator ma własne testy. Produkcja nietknięta."""
    for _f in ("ENABLE_LEX_COMMITTED_WINDOW", "ENABLE_LEX_COMMITTED_WINDOW_SHADOW",
               "ENABLE_CARRIED_FIRST_RELAX", "ENABLE_RELAX_COLOC_PICKUP",
               "ENABLE_NONCARRIED_DROPOFF_REORDER", "ENABLE_CARRIED_AGE_TZ_FIX",
               "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP"):
        monkeypatch.setattr(PR, _f, False, raising=False)


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


def test_interleaved_bag_repaired_not_abandoned():
    # przeplecione (p:A d:A p:B d:B) — swap odbiorów wepchnąłby d:A przed p:A.
    # Stary fail-safe rezygnował z całego sortu → inwersja odbiorów zostawała
    # w apce (case Mateusz O 11.06: Zapiecek 16:23 przed Kebab Król 16:21).
    # Teraz: repair pass przenosi d:A tuż za p:A — sort committed UTRZYMANY
    # i 'dostawa po odbiorze' UTRZYMANA.
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:20")},
              "B": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:05")}}
    stops = [{"order_id": "A", "type": "pickup"}, {"order_id": "A", "type": "dropoff"},
             {"order_id": "B", "type": "pickup"}, {"order_id": "B", "type": "dropoff"}]
    out = PR._apply_canon_order_invariants(stops, orders)
    assert _seq(out) == [("pickup", "B"), ("pickup", "A"),
                         ("dropoff", "A"), ("dropoff", "B")]


def test_interleaved_three_orders_repaired():
    # 3 zlecenia, środkowe przeplecione — wszystkie odbiory wg committed,
    # każda dostawa nadal po swoim odbiorze.
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:30")},
              "B": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:10")},
              "C": {"status": "assigned", "czas_kuriera_warsaw": _ck("18:20")}}
    stops = [{"order_id": "A", "type": "pickup"}, {"order_id": "A", "type": "dropoff"},
             {"order_id": "C", "type": "pickup"}, {"order_id": "C", "type": "dropoff"},
             {"order_id": "B", "type": "pickup"}, {"order_id": "B", "type": "dropoff"}]
    out = PR._apply_canon_order_invariants(stops, orders)
    pickups = [oid for typ, oid in _seq(out) if typ == "pickup"]
    assert pickups == ["B", "C", "A"]
    pos = {(typ, oid): i for i, (typ, oid) in enumerate(_seq(out))}
    for oid in ("A", "B", "C"):
        assert pos[("pickup", oid)] < pos[("dropoff", oid)]


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
