"""Negatywny oracle root-fixu case 491870/cid492.

Incydent ma dwa rozłączne warunki: (1) fizycznie wspólny punkt odbioru był
niewidoczny dla Z-RULE, bo klucz współrzędnych miał skalę około 1 m; (2) F6
sortował pickupy wyłącznie wewnątrz zastanych slotów, a reorderery nie mogły
ruszyć pickupu przy ``n_carried=0``.  Drugi test używa legalnego termicznie
wariantu tej samej topologii. Rzeczywisty przeciążony worek 491870 nie może być
"naprawiony" permutacją, która łamie R6; dla niego końcowym skutkiem jest Alarm.

Współrzędne są syntetyczne. Odstęp P_A↔P_C wynosi około 28,6 m, jak w case,
ale nie utrwala żadnego realnego adresu/GPS.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from dispatch_v2 import osrm_client
from dispatch_v2 import common as C
from dispatch_v2 import plan_recheck as P
from dispatch_v2 import route_order
from dispatch_v2 import route_podjazdy


NOW = datetime(2026, 8, 2, 18, 0, tzinfo=timezone.utc)
START = (52.0, 20.0)
P_A = [52.0, 20.0]
P_C = [52.00023, 20.00017]
P_B = [52.0030, 20.0030]


def _haversine_m(a, b):
    radius_m = 6_371_000.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * radius_m * math.asin(math.sqrt(hav))


def _table(points_a, points_b):
    # Stałe 30 km/h; deterministycznie, bez żywego OSRM.
    return [[{"duration_s": _haversine_m(a, b) / 8.333} for b in points_b]
            for a in points_a]


@pytest.fixture(autouse=True)
def _fake_osrm(monkeypatch):
    monkeypatch.setattr(osrm_client, "table", _table)


def _stop(oid, kind):
    return {"order_id": oid, "type": kind,
            "dwell_min": 1.0 if kind == "pickup" else 3.5}


def _ids(seq):
    return [(str(stop["order_id"]), stop["type"]) for stop in seq]


def _orders(*, far_dropoffs=False):
    # Commitments są już rosnąco. F6 nie musi ich zamieniać, a jednak C pozostaje
    # w przypadkowym późnym slocie za dwiema dostawami.
    if far_dropoffs:
        deliveries = ([51.99, 19.99], [51.989, 19.988], [52.002, 20.002])
    else:
        deliveries = ([52.00035, 20.00025], [52.0012, 20.0011], [52.0005, 20.0004])
    return {
        "A": {"status": "assigned", "czas_kuriera_warsaw": "2026-08-02T20:00:00+02:00",
              "pickup_coords": P_A, "delivery_coords": deliveries[0]},
        "B": {"status": "assigned", "czas_kuriera_warsaw": "2026-08-02T20:03:00+02:00",
              "pickup_coords": P_B, "delivery_coords": deliveries[1]},
        "C": {"status": "assigned", "czas_kuriera_warsaw": "2026-08-02T20:04:00+02:00",
              "pickup_coords": P_C, "delivery_coords": deliveries[2]},
    }


def _late_slot_sequence():
    return [
        _stop("A", "pickup"),
        _stop("B", "pickup"),
        _stop("A", "dropoff"),
        _stop("B", "dropoff"),
        _stop("C", "pickup"),
        _stop("C", "dropoff"),
    ]


def _flags_for_reorder(monkeypatch, *, enabled):
    monkeypatch.setattr(P, "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", True)
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "ENABLE_RELAX_COLOC_PICKUP", True)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", True)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", False)
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", False)
    monkeypatch.setattr(
        P, "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER", enabled, raising=False
    )


def test_red_491870_same_physical_pickup_is_visible_to_no_return_rule():
    orders = _orders(far_dropoffs=True)
    assert 27.0 < _haversine_m(P_A, P_C) < 30.0

    violations = P._detect_departed_pickup_revisit(_late_slot_sequence(), orders)

    assert any(set(order_ids) == {"A", "C"}
               for _first, _revisit, order_ids in violations), (
        "case 491870: dwa pickupy ~28,6 m od siebie pozostają dwiema "
        "definicjami punktu i Z-RULE nie widzi powrotu"
    )


def test_red_491870_slot_shape_allows_legal_pickup_forward_at_n_carried_zero(monkeypatch):
    orders = _orders(far_dropoffs=False)
    before = _late_slot_sequence()

    _flags_for_reorder(monkeypatch, enabled=True)

    after = P._apply_canon_order_invariants(before, orders, START, NOW)
    positions = {item: index for index, item in enumerate(_ids(after))}

    assert positions[("C", "pickup")] < positions[("A", "dropoff")], (
        "case 491870: F6 pozostawił pickup C w późnym slocie; reorderery nie "
        "potrafią przesunąć pickupu do przodu przy n_carried=0"
    )
    for oid in ("A", "B", "C"):
        assert positions[(oid, "pickup")] < positions[(oid, "dropoff")]


def test_noncarried_pickup_reorder_is_real_on_off_flag(monkeypatch):
    orders = _orders(far_dropoffs=False)
    before = _late_slot_sequence()

    _flags_for_reorder(monkeypatch, enabled=False)
    off = P._apply_canon_order_invariants(before, orders, START, NOW)
    _flags_for_reorder(monkeypatch, enabled=True)
    on = P._apply_canon_order_invariants(before, orders, START, NOW)

    assert _ids(off) == _ids(before), "OFF musi zachować sloty F6 bajt-w-bajt"
    assert _ids(on) != _ids(off), "flaga musi mieć realne ON≠OFF"
    on_pos = {item: index for index, item in enumerate(_ids(on))}
    assert on_pos[("C", "pickup")] < on_pos[("A", "dropoff")]
    assert C.ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER is False
    assert "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER" in C.ETAP4_DECISION_FLAGS
    assert "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER" in P._D3_FALA_A_FLAGS


def test_noncarried_pickup_flag_hot_reload_and_missing_key_preserves_default(monkeypatch):
    name = "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER"
    monkeypatch.setattr(P, name, False)
    monkeypatch.setattr(C, "load_flags", lambda: {name: True})
    P._refresh_d3_fala_a_flags()
    assert getattr(P, name) is True

    monkeypatch.setattr(P, name, False)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    P._refresh_d3_fala_a_flags()
    assert getattr(P, name) is False


def test_r6_per_order_guard_rejects_victim_swap_and_worsening():
    guard = P._r6_candidate_not_worse
    # Mutation klasy „count 1→1": A jest naprawione, ale B staje się nową ofiarą.
    assert guard({"A": 36.0, "B": 34.0}, {"A": 34.0, "B": 36.0}, 35.0) is False
    # Order już ponad capem może się poprawić albo zostać równy, nigdy pogorszyć.
    assert guard({"A": 36.0}, {"A": 35.9}, 35.0) is True
    assert guard({"A": 36.0}, {"A": 36.0001}, 35.0) is False
    # Brak metryki kandydata = fail-closed, nie domniemanie bezpieczeństwa.
    assert guard({"A": 34.0}, {}, 35.0) is False


def test_pickup_point_contract_boundary_complete_link_and_twin_parity():
    origin = {"order_id": "A", "pickup_coords": [52.0, 20.0], "restaurant": "X"}
    near = {"order_id": "B", "pickup_coords": [52.0014, 20.0], "restaurant": "Y"}
    chain = {"order_id": "C", "pickup_coords": [52.0028, 20.0], "restaurant": "Z"}

    assert route_order.pickup_point_distance_m(origin, near) < 180.0
    assert route_order.same_pickup_point(origin, near) is True
    assert route_order.same_pickup_point(near, chain) is True
    assert route_order.same_pickup_point(origin, chain) is False
    groups = route_order.group_same_pickup_points([origin, near, chain])
    assert [[item["order_id"] for item in group] for group in groups] == [["A", "B"], ["C"]]
    # Geometria ma pierwszeństwo nad identyczną nazwą; fallback nazwy działa
    # tylko gdy przynajmniej jedna strona nie ma poprawnej geometrii.
    far_same_name = {"pickup_coords": [53.0, 21.0], "restaurant": "X"}
    assert route_order.same_pickup_point(origin, far_same_name) is False
    assert route_order.same_pickup_point(
        {"pickup_address": "  Punkt Testowy  "},
        {"restaurant_address": "punkt testowy"},
    ) is True

    # route_podjazdy jest wyłącznie kompatybilnym re-exportem, nie drugim ownerem.
    assert route_podjazdy.same_pickup_point is route_order.same_pickup_point
    assert route_podjazdy.group_same_pickup_points is route_order.group_same_pickup_points
    assert route_podjazdy.PICKUP_POINT_RADIUS_M == route_order.PICKUP_POINT_RADIUS_M
