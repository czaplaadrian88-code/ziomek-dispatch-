"""Iteracja 2 case 491870: fizyczny stop, NO-RETURN i OFF-parity.

Współrzędne są syntetyczne. Topologia, rozrzut około 28,6 m i kolejność
pickup/pickup/drop/drop/pickup/drop odtwarzają incydent bez utrwalania PII.
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2 import osrm_client
from dispatch_v2 import plan_recheck as P
from dispatch_v2 import route_order
from dispatch_v2 import same_restaurant_grouper as G


FLAG = "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER"
NOW = datetime(2026, 8, 2, 18, 0, tzinfo=timezone.utc)
START = (52.0, 20.0)
P_A = [52.0, 20.0]
P_C = [52.00023, 20.00017]
P_B = [52.0030, 20.0030]
MASTER_ROUTE_CORPUS_SHA256 = (
    "7ead3e9780053c7a8dfe9912a1b5c72701df2a1591f4fbf69fb2d468483e1240"
)
ITER2_REPLAY_CORPUS = json.loads(
    (Path(__file__).parent / "golden" / "case_491870_iter2_replay.json").read_text(
        encoding="utf-8"
    )
)["cases"]


def _haversine_m(a, b):
    radius_m = 6_371_000.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    hav = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    )
    return 2.0 * radius_m * math.asin(math.sqrt(hav))


def _table(points_a, points_b):
    # Stałe 30 km/h; deterministycznie, bez sieci i żywego OSRM.
    return [
        [{"duration_s": _haversine_m(a, b) / 8.333} for b in points_b]
        for a in points_a
    ]


@pytest.fixture(autouse=True)
def _isolated_flags_and_osrm(monkeypatch):
    monkeypatch.setattr(osrm_client, "table", _table)
    monkeypatch.setattr(P._lex_ledger, "record_decision", lambda *args, **kwargs: None)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    monkeypatch.setattr(C, FLAG, False, raising=False)
    monkeypatch.setattr(P, FLAG, False, raising=False)


def _stop(oid, kind):
    return {
        "order_id": oid,
        "type": kind,
        "dwell_min": 1.0 if kind == "pickup" else 3.5,
    }


def _ids(seq):
    return [(str(stop["order_id"]), stop["type"]) for stop in seq]


def _late_slot_sequence():
    return [
        _stop("A", "pickup"),
        _stop("B", "pickup"),
        _stop("A", "dropoff"),
        _stop("B", "dropoff"),
        _stop("C", "pickup"),
        _stop("C", "dropoff"),
    ]


def _orders(
    *,
    c_committed_min=4.0,
    same_physical=True,
    far_dropoffs=True,
    c_pickup_coords=P_C,
):
    if far_dropoffs:
        deliveries = ([51.99, 19.99], [51.989, 19.988], [52.002, 20.002])
    else:
        deliveries = ([52.00035, 20.00025], [52.0012, 20.0011], [52.0005, 20.0004])
    c_address = (
        "ul. Jana Kilińskiego 12/lok 1U"
        if same_physical
        else "Zupełnie Inna 12"
    )
    return {
        "A": {
            "status": "assigned",
            "restaurant": "Punkt A",
            "pickup_address": "Kilińskiego 12 lok. 1U",
            "pickup_city": "Białystok",
            "czas_kuriera_warsaw": "2026-08-02T20:00:00+02:00",
            "pickup_coords": P_A,
            "delivery_coords": deliveries[0],
        },
        "B": {
            "status": "assigned",
            "restaurant": "Punkt B",
            "pickup_address": "Inna 3",
            "pickup_city": "Białystok",
            "czas_kuriera_warsaw": "2026-08-02T20:03:00+02:00",
            "pickup_coords": P_B,
            "delivery_coords": deliveries[1],
        },
        "C": {
            "status": "assigned",
            "restaurant": "Punkt C",
            "pickup_address": c_address,
            "pickup_city": "Białystok",
            "czas_kuriera_warsaw": (
                NOW + timedelta(minutes=c_committed_min)
            ).astimezone(C.WARSAW).isoformat(),
            "pickup_coords": c_pickup_coords,
            "delivery_coords": deliveries[2],
        },
    }


def _set_new_path(monkeypatch, enabled):
    monkeypatch.setattr(C, FLAG, enabled, raising=False)
    monkeypatch.setattr(P, FLAG, enabled, raising=False)
    monkeypatch.setattr(P, "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", True)
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", False)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", True)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", False)
    monkeypatch.setattr(P, "ENABLE_LEX_WINDOW_GUARDS_V2", True)
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", False)


def _physical_no_return(seq, orders):
    return P._detect_departed_pickup_revisit(
        seq,
        orders,
        physical_stops=True,
    )


def test_flag_default_off_registered_and_hot_refreshable(monkeypatch):
    assert C.ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER is False
    assert FLAG in C.ETAP4_DECISION_FLAGS
    assert FLAG in P._D3_FALA_A_FLAGS

    monkeypatch.setattr(C, "load_flags", lambda: {FLAG: True})
    P._refresh_d3_fala_a_flags()
    assert P.ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER is True

    monkeypatch.setattr(C, "load_flags", lambda: {})
    monkeypatch.setattr(C, FLAG, False, raising=False)
    monkeypatch.setattr(P, FLAG, False, raising=False)
    P._refresh_d3_fala_a_flags()
    assert P.ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER is False


def test_red_491870_physical_stop_is_address_key_not_gps_radius(monkeypatch):
    _set_new_path(monkeypatch, True)
    orders = _orders()
    assert 27.0 < _haversine_m(P_A, P_C) < 30.0

    assert route_order.same_physical_pickup_stop(orders["A"], orders["C"])
    assert _physical_no_return(_late_slot_sequence(), orders)

    # Dwa różne adresy nie stają się jednym stopem nawet w odległości 28,6 m.
    distinct = _orders(same_physical=False)
    assert not route_order.same_physical_pickup_stop(distinct["A"], distinct["C"])
    assert not _physical_no_return(_late_slot_sequence(), distinct)


def test_repro_491870_on_produces_hard_safe_candidate_despite_soft_guards(
    monkeypatch, caplog
):
    _set_new_path(monkeypatch, True)
    monkeypatch.setattr(
        P._lex_guards,
        "evaluate",
        lambda *args, **kwargs: P._lex_guards.GuardResult(
            False,
            False,
            {},
            "g1_delay",
        ),
    )
    orders = _orders(far_dropoffs=True)
    before = _late_slot_sequence()

    after = P._apply_canon_order_invariants(before, orders, START, NOW)
    positions = {item: index for index, item in enumerate(_ids(after))}

    assert _ids(after) != _ids(before), "ON ma wyprodukować realnego kandydata"
    assert _physical_no_return(after, orders) == []
    assert positions[("C", "pickup")] < positions[("A", "dropoff")]
    for oid in ("A", "B", "C"):
        assert positions[(oid, "pickup")] < positions[(oid, "dropoff")]
    assert "HARD_NO_RETURN" in caplog.text


def test_off_keeps_491870_sequence_byte_for_byte(monkeypatch):
    _set_new_path(monkeypatch, False)
    before = _late_slot_sequence()
    after = P._apply_canon_order_invariants(before, _orders(), START, NOW)
    assert json.dumps(after, sort_keys=True, separators=(",", ":")) == json.dumps(
        before, sort_keys=True, separators=(",", ":")
    )


def test_original_hard_no_return_oracle_restored_and_mutation_kills_writer(monkeypatch):
    orders = {
        "X": {
            "status": "assigned",
            "restaurant": "R",
            "pickup_coords": [52.0, 20.0],
            "delivery_coords": [52.01, 20.01],
            "czas_kuriera_warsaw": "2026-08-02T20:00:00+02:00",
        },
        "Y": {
            "status": "assigned",
            "restaurant": "R",
            "pickup_coords": [52.0, 20.0],
            "delivery_coords": [52.02, 20.02],
            "czas_kuriera_warsaw": "2026-08-02T20:21:00+02:00",
        },
    }
    before = [
        _stop("X", "pickup"),
        _stop("X", "dropoff"),
        _stop("Y", "pickup"),
        _stop("Y", "dropoff"),
    ]
    monkeypatch.setattr(P, "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", True)
    monkeypatch.setattr(P, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", True)
    fixed = P._apply_canon_order_invariants(before, orders)
    assert P._detect_departed_pickup_revisit(fixed, orders) == []

    real = P._coalesce_same_pickup_nodes
    monkeypatch.setattr(P, "_coalesce_same_pickup_nodes", lambda seq, _orders: list(seq))
    mutated = P._apply_canon_order_invariants(before, orders)
    assert P._detect_departed_pickup_revisit(mutated, orders), (
        "mutation usuwająca istniejący writer musi ponownie odsłonić defekt"
    )
    monkeypatch.setattr(P, "_coalesce_same_pickup_nodes", real)


def test_pickup_merge_min_is_load_bearing_physical_stop_guard(monkeypatch):
    _set_new_path(monkeypatch, True)
    at_limit = _orders(c_committed_min=float(route_order.PICKUP_MERGE_MIN))
    above = _orders(c_committed_min=float(route_order.PICKUP_MERGE_MIN) + 1.0 / 60.0)

    assert route_order.same_physical_pickup_stop(at_limit["A"], at_limit["C"])
    assert not route_order.same_physical_pickup_stop(above["A"], above["C"])

    # Mutation progu: 10 -> 0 musi rozdzielić legalny stop case'u.
    monkeypatch.setattr(route_order, "PICKUP_MERGE_MIN", 0)
    assert not route_order.same_physical_pickup_stop(at_limit["A"], at_limit["C"])


def test_time_b_stop_keeps_exact_committed_per_order(monkeypatch):
    _set_new_path(monkeypatch, True)
    orders = _orders()
    bag = [{"order_id": oid, **record} for oid, record in orders.items()]
    final = P._apply_canon_order_invariants(
        _late_slot_sequence(),
        orders,
        START,
        NOW,
    )
    plan = {"stops": final}
    stops = route_order.build_route_stops(
        bag,
        plan,
        plan_aware=True,
        trust_canon=True,
        physical_contract=True,
    )
    merged = next(
        stop
        for stop in stops
        if stop["kind"] == "pickup" and set(stop["order_ids"]) == {"A", "C"}
    )
    assert merged["committed_by_order"] == {
        "A": orders["A"]["czas_kuriera_warsaw"],
        "C": orders["C"]["czas_kuriera_warsaw"],
    }
    assert "committed_at" not in merged


def test_time_b_grouper_consumes_same_stop_and_pickup_merge_min(monkeypatch):
    _set_new_path(monkeypatch, True)
    records = _orders(c_committed_min=float(route_order.PICKUP_MERGE_MIN))

    def simulated(oid):
        record = records[oid]
        return SimpleNamespace(
            order_id=oid,
            restaurant=record["restaurant"],
            pickup_address=record["pickup_address"],
            pickup_city=record["pickup_city"],
            pickup_coords=record["pickup_coords"],
            czas_kuriera_warsaw=record["czas_kuriera_warsaw"],
            pickup_ready_at=datetime.fromisoformat(
                record["czas_kuriera_warsaw"]
            ),
            delivery_address=f"Drop {oid}",
        )

    grouped = G.group_orders_by_restaurant(
        [simulated("A"), simulated("C")],
        lambda _address: "Q",
        {"Q": set()},
        time_tolerance_min=float(route_order.PICKUP_MERGE_MIN),
    )
    assert len(grouped) == 1
    assert isinstance(grouped[0], G.GroupedOrders)
    assert {order.order_id for order in grouped[0].orders} == {"A", "C"}

    records = _orders(
        c_committed_min=float(route_order.PICKUP_MERGE_MIN) + 1.0 / 60.0
    )
    split = G.group_orders_by_restaurant(
        [simulated("A"), simulated("C")],
        lambda _address: "Q",
        {"Q": set()},
        time_tolerance_min=float(route_order.PICKUP_MERGE_MIN) + 1.0,
    )
    assert len(split) == 2
    assert all(isinstance(item, G.SingletonOrder) for item in split)


def test_pipeline_propagates_physical_key_inputs_only_when_flag_on(monkeypatch):
    from dispatch_v2 import dispatch_pipeline as DP

    record = {
        "order_id": "A",
        "status": "assigned",
        "pickup_coords": [53.13, 23.16],
        "delivery_coords": [53.14, 23.17],
        "czas_kuriera_warsaw": "2026-08-02T20:00:00+02:00",
        "pickup_address": "Testowa 12",
        "pickup_city": "Białystok",
        "restaurant": "Punkt A",
        "restaurant_address": "Testowa 12",
    }

    _set_new_path(monkeypatch, False)
    off = DP._bag_dict_to_ordersim(record)
    assert not hasattr(off, "pickup_address")
    assert not hasattr(off, "pickup_city")

    _set_new_path(monkeypatch, True)
    on = DP._bag_dict_to_ordersim(record)
    assert on.pickup_address == "Testowa 12"
    assert on.pickup_city == "Białystok"
    assert on.restaurant == "Punkt A"


def test_f5_fourth_change_is_gated_and_never_uses_180m_identity(monkeypatch):
    base = SimpleNamespace(
        order_id="B",
        pickup_coords=(52.0, 20.0),
        pickup_address="Kilińskiego 12 lok. 1U",
        pickup_city="Białystok",
        picked_up_at="2026-08-02T18:00:00+00:00",
        czas_kuriera_warsaw=None,
    )
    new = SimpleNamespace(
        order_id="N",
        pickup_coords=(52.00108, 20.0),  # około 120 m: pasmo 80..180
        pickup_address="ul. Jana Kilińskiego 12/lok 1U",
        pickup_city="Białystok",
    )
    plan = SimpleNamespace(
        pickup_at={"N": "2026-08-02T18:20:00+00:00"},
        predicted_delivered_at={"B": "2026-08-02T18:40:00+00:00"},
    )

    _set_new_path(monkeypatch, False)
    assert F.detect_return_to_restaurant([base], new, plan) is None

    _set_new_path(monkeypatch, True)
    assert F.detect_return_to_restaurant([base], new, plan) == "B"
    new.pickup_address = "Inna 12"
    new.pickup_coords = (52.00018, 20.0)  # około 20 m, ale inny klucz fizyczny
    assert F.detect_return_to_restaurant([base], new, plan) is None
    assert P.RELAX_COLOC_PICKUP_M == 180.0


def _route_corpus_projection():
    corpus = json.loads(
        (Path(__file__).parent / "golden" / "route_order_corpus.json").read_text()
    )
    physical_contract = C.decision_flag(FLAG)
    route_kwargs = {
        "plan_aware": True,
        "trust_canon": True,
    }
    if physical_contract:
        route_kwargs["physical_contract"] = True
    return [
        {
            "id": case["id"],
            "order": route_order.order_podjazdy(
                case["bag"],
                case.get("plan_doc"),
                **route_kwargs,
            ),
            "stops": route_order.build_route_stops(
                case["bag"],
                case.get("plan_doc"),
                **route_kwargs,
            ),
        }
        for case in corpus["cases"]
    ]


def test_off_equals_6317f4553_on_27_case_time_b_corpus_byte_for_byte(monkeypatch):
    _set_new_path(monkeypatch, False)
    projection = _route_corpus_projection()
    raw = json.dumps(
        projection,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(projection) == 27
    assert hashlib.sha256(raw).hexdigest() == MASTER_ROUTE_CORPUS_SHA256


def test_off_keeps_existing_carried_wb2_route_byte_for_byte(monkeypatch):
    _set_new_path(monkeypatch, False)
    fixture = (
        Path(__file__).with_name("fixtures")
        / "wb2_incident_492_20260727T160912Z.json"
    )
    data = json.loads(fixture.read_text(encoding="utf-8"))
    matrix = data["leg_matrix_min"]
    monkeypatch.setattr(
        osrm_client,
        "table",
        lambda points_a, points_b: [
            [
                {"duration_s": matrix[i][j] * 60.0}
                for j in range(len(points_b))
            ]
            for i in range(len(points_a))
        ],
    )

    after = P._lex_committed_window_reorder(
        [dict(stop) for stop in data["stops"]],
        data["orders_state"],
        tuple(data["start_pos"]),
        datetime.fromisoformat(data["now"]),
    )
    raw = json.dumps(after, sort_keys=True, separators=(",", ":"))
    assert raw == (
        '[{"dwell_min":3.5,"order_id":"490595","type":"dropoff"},'
        '{"dwell_min":3.5,"order_id":"490601","type":"dropoff"},'
        '{"dwell_min":1.0,"order_id":"490612","type":"pickup"},'
        '{"dwell_min":3.5,"order_id":"490612","type":"dropoff"}]'
    )


def test_on_corpus_replay_preserves_membership_committed_and_time_b_guard(monkeypatch):
    _set_new_path(monkeypatch, True)
    projection = _route_corpus_projection()
    assert len(projection) == 27

    for case in projection:
        seen = []
        for stop in case["stops"]:
            seen.extend((stop["kind"], oid) for oid in stop["order_ids"])
            if stop["kind"] != "pickup":
                continue
            assert set(stop["committed_by_order"]) == set(stop["order_ids"])
            values = [
                route_order._iso(value)
                for value in stop["committed_by_order"].values()
            ]
            if len(values) > 1:
                assert all(value is not None for value in values), case["id"]
                spread = (max(values) - min(values)).total_seconds() / 60.0
                assert spread <= route_order.PICKUP_MERGE_MIN, case["id"]
        assert len(seen) == len(set(seen)), case["id"]


@pytest.mark.parametrize(
    "case",
    ITER2_REPLAY_CORPUS,
    ids=[case["id"] for case in ITER2_REPLAY_CORPUS],
)
def test_on_491870_replay_corpus_repairs_hard_and_respects_time_b(
    monkeypatch,
    case,
):
    _set_new_path(monkeypatch, True)
    if case["soft_mode"] == "reject_all":
        monkeypatch.setattr(
            P._lex_guards,
            "evaluate",
            lambda *args, **kwargs: P._lex_guards.GuardResult(
                False,
                False,
                {},
                "g1_delay",
            ),
        )
    orders = _orders(
        c_committed_min=case["committed_offset_min"],
        same_physical=case["same_physical"],
        far_dropoffs=case["far_dropoffs"],
        c_pickup_coords=case.get("c_pickup_coords", P_C),
    )

    assert route_order.same_physical_pickup_point(
        orders["A"], orders["C"]
    ) is case["expect_same_point"]
    assert route_order.same_physical_pickup_stop(
        orders["A"], orders["C"]
    ) is case["expect_same_stop"]

    if not case["exercise_plan"]:
        return
    before = _late_slot_sequence()
    assert _physical_no_return(before, orders)
    after = P._apply_canon_order_invariants(before, orders, START, NOW)
    assert _ids(after) != _ids(before)
    assert _physical_no_return(after, orders) == []
    positions = {item: index for index, item in enumerate(_ids(after))}
    for oid in ("A", "B", "C"):
        assert positions[(oid, "pickup")] < positions[(oid, "dropoff")]


def test_r6_per_order_guard_and_victim_swap_mutation():
    guard = P._r6_candidate_not_worse
    assert guard({"A": 36.0, "B": 34.0}, {"A": 34.0, "B": 36.0}, 35.0) is False
    assert guard({"A": 36.0}, {"A": 35.9}, 35.0) is True
    assert guard({"A": 36.0}, {"A": 36.0001}, 35.0) is False
    assert guard({"A": 34.0}, {}, 35.0) is False
