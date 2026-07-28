"""OWNER 2026-07-28: stop identity + exact per-order committed presentation."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2 import live_eta, route_order


PLAN = {
    "stops": [
        {"type": "pickup", "order_id": "490836"},
        {"type": "pickup", "order_id": "490832"},
        {"type": "dropoff", "order_id": "490836"},
        {"type": "dropoff", "order_id": "490832"},
    ]
}
GOLDEN_BAG = [
    {
        "order_id": "490836",
        "status": "assigned",
        "restaurant": "Grill Kebab",
        "czas_kuriera_warsaw": "2026-07-28T21:26:00+02:00",
    },
    {
        "order_id": "490832",
        "status": "assigned",
        "restaurant": "Grill Kebab",
        "czas_kuriera_warsaw": "2026-07-28T21:52:00+02:00",
    },
]


def _pickup_stops(bag=GOLDEN_BAG, plan=PLAN):
    return [
        stop
        for stop in route_order.build_route_stops(
            bag, plan, plan_aware=True, trust_canon=True
        )
        if stop["kind"] == "pickup"
    ]


def test_golden_490836_490832_is_two_stops_with_exact_times():
    pickups = _pickup_stops()
    assert [(stop["stop_id"], stop["order_ids"]) for stop in pickups] == [
        ("pickup:490836", ["490836"]),
        ("pickup:490832", ["490832"]),
    ]
    assert {
        oid: committed
        for stop in pickups
        for oid, committed in stop["committed_by_order"].items()
    } == {
        "490836": "2026-07-28T21:26:00+02:00",
        "490832": "2026-07-28T21:52:00+02:00",
    }


def test_close_pickups_keep_one_stop_but_two_exact_committed_values():
    bag = [
        {**GOLDEN_BAG[0]},
        {
            **GOLDEN_BAG[1],
            "czas_kuriera_warsaw": "2026-07-28T21:34:00+02:00",
        },
    ]
    pickup = _pickup_stops(bag)[0]
    assert pickup["stop_id"] == "pickup:490832,490836"
    assert pickup["order_ids"] == ["490836", "490832"]
    assert pickup["committed_by_order"] == {
        "490836": "2026-07-28T21:26:00+02:00",
        "490832": "2026-07-28T21:34:00+02:00",
    }


def test_time_guard_uses_internal_spread_not_chained_pairwise_gaps():
    bag = [
        {
            "order_id": oid,
            "status": "assigned",
            "restaurant": "R",
            "czas_kuriera_warsaw": f"2026-07-28T21:{minute:02d}:00+02:00",
        }
        for oid, minute in (("A", 0), ("B", 10), ("C", 20))
    ]
    runs = route_order.pickup_runs(bag)
    assert [[order["order_id"] for order in run] for run in runs] == [
        ["A", "B"],
        ["C"],
    ]


def test_mutation_removing_spread_guard_recreates_the_defect(monkeypatch):
    assert len(_pickup_stops()) == 2
    monkeypatch.setattr(route_order, "_pickup_spread_ok", lambda _orders: True)
    mutated = _pickup_stops()
    assert len(mutated) == 1
    assert mutated[0]["order_ids"] == ["490836", "490832"]


def test_mutation_min_or_max_group_time_fails_exact_per_order_oracle():
    expected = {
        "490836": "2026-07-28T21:26:00+02:00",
        "490832": "2026-07-28T21:34:00+02:00",
    }
    for synthetic in (
        {oid: "2026-07-28T21:26:00+02:00" for oid in expected},
        {oid: "2026-07-28T21:34:00+02:00" for oid in expected},
    ):
        assert synthetic != expected


def test_live_eta_never_merges_distinct_stop_ids_with_same_coordinates():
    snapshot = live_eta.calculate_live_eta(
        courier_id="492",
        start=[53.13, 23.16],
        stops=[
            {
                "stop_id": "pickup:490836",
                "kind": "pickup",
                "order_ids": ["490836"],
                "coord": [53.132464, 23.165517],
                "floor_at": ["2026-07-28T19:26:00Z"],
            },
            {
                "stop_id": "pickup:490832",
                "kind": "pickup",
                "order_ids": ["490832"],
                "coord": [53.132464, 23.165517],
                "floor_at": ["2026-07-28T19:52:00Z"],
            },
        ],
        now=datetime(2026, 7, 28, 19, 20, tzinfo=timezone.utc),
        duration_provider=lambda _points: [60, 0],
        cycle_id=1,
    )
    assert [stop["stop_id"] for stop in snapshot["stops"]] == [
        "pickup:490836",
        "pickup:490832",
    ]


def test_latest_ready_controls_route_departure_not_presented_committed():
    snapshot = live_eta.calculate_live_eta(
        courier_id="492",
        start=[53.13, 23.16],
        stops=[
            {
                "stop_id": "pickup:490832,490836",
                "kind": "pickup",
                "order_ids": ["490836", "490832"],
                "coord": [53.132464, 23.165517],
                "floor_at": [
                    "2026-07-28T19:26:00Z",
                    "2026-07-28T19:34:00Z",
                ],
                "dwell_s": 60,
            },
            {
                "stop_id": "dropoff:490836",
                "kind": "dropoff",
                "order_ids": ["490836"],
                "coord": [53.14, 23.17],
                "dwell_s": 60,
            },
        ],
        now=datetime(2026, 7, 28, 19, 20, tzinfo=timezone.utc),
        duration_provider=lambda _points: [60, 120],
        cycle_id=1,
    )
    assert snapshot["stops"][0]["eta_at"] == "2026-07-28T19:34:00Z"
    assert snapshot["stops"][1]["eta_at"] == "2026-07-28T19:37:00Z"


def test_structural_ratchets_forbid_coordinate_membership_and_group_time():
    stop_id_source = inspect.getsource(route_order.stop_id_for)
    normalization_source = inspect.getsource(live_eta._normalize_stops)
    presentation_sources = (
        inspect.getsource(route_order.build_route_stops),
        inspect.getsource(route_order.build_stop_sequence),
    )
    assert "coord" not in stop_id_source
    assert 'out[-1]["coord"]' not in normalization_source
    for presentation_source in presentation_sources:
        for aggregator in ("min(", "max(", "median("):
            assert aggregator not in presentation_source


def test_cross_repo_patches_remove_competing_writer_and_propagate_contract():
    root = Path(__file__).resolve().parents[1]
    panel_patch = (
        root / "patches" / "PICKUP_GROUP_TIMEGUARD_PANEL.patch"
    ).read_text(encoding="utf-8")
    courier_patch = (
        root / "patches" / "PICKUP_GROUP_TIMEGUARD_COURIER_API.patch"
    ).read_text(encoding="utf-8")

    assert "-def _pickup_runs(" in panel_patch
    assert "-def _plan_pickup_clusters(" in panel_patch
    assert "+        route_stops = _route_order.build_route_stops(" in panel_patch
    assert "committed_by_order" in panel_patch

    assert "+            stop_sequence = _route_podjazdy.build_stop_sequence(" in courier_patch
    assert '"pickup_committed_at": o.get("czas_kuriera_warsaw")' in courier_patch
    assert 'step["committed_at"] = contract["committed_at"]' in courier_patch

    added_contract_lines = "\n".join(
        line[1:]
        for patch in (panel_patch, courier_patch)
        for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    for aggregator in ("min(", "max(", "median("):
        assert aggregator not in added_contract_lines
