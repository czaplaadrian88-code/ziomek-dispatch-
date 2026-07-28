"""R3 — uczciwe pokrycie źródeł żywego ETA.

Oracle kontraktu:
* LIVE wyłącznie z GPS nie starszego niż 120 s,
* WARM wyłącznie z ``history[].at`` nie starszego niż 180 s i tylko za flagą,
* PLANNED oznacza stop bez wyceny daemona,
* jeden brak współrzędnych nie kasuje poprawnych stopów całej trasy,
* OFF zachowuje legacy snapshot bajt w bajt.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dispatch_v2 import live_eta, live_eta_daemon

NOW = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)


def _gps(age_s: int) -> dict:
    return {
        "lat": 53.12,
        "lon": 23.15,
        "timestamp": (NOW - timedelta(seconds=age_s)).isoformat(),
    }


def _order(
    oid: str,
    *,
    pickup=(53.13, 23.16),
    delivery=(53.14, 23.17),
    event_age_s: int | None = None,
) -> dict:
    history = []
    if event_age_s is not None:
        history.append(
            {
                "event": "COURIER_PICKED_UP",
                "at": (NOW - timedelta(seconds=event_age_s)).isoformat(),
            }
        )
    return {
        "order_id": oid,
        "courier_id": "75",
        "status": "assigned",
        "restaurant": f"R-{oid}",
        "pickup_coords": list(pickup) if pickup is not None else None,
        "delivery_coords": list(delivery) if delivery is not None else None,
        "history": history,
    }


def _deterministic_sequence(monkeypatch, sequence) -> None:
    route_stops = [
        {
            "stop_id": live_eta_daemon.route_order.stop_id_for(kind, order_ids),
            "kind": kind,
            "order_ids": list(order_ids),
        }
        for kind, order_ids in sequence
    ]
    monkeypatch.setattr(
        live_eta_daemon.route_order,
        "build_route_stops",
        lambda *_args, **_kwargs: route_stops,
    )
    monkeypatch.setattr(live_eta_daemon, "_available_floor", lambda *_args: None)


def _snapshot(route: dict) -> dict:
    legs = iter(([60.0], [120.0], [180.0], [240.0]))

    def provider(_points):
        return next(legs)

    return live_eta.calculate_live_eta(
        courier_id=route["courier_id"],
        start=route["start"],
        start_source=route.get("start_source"),
        stops=route["stops"],
        now=NOW,
        duration_provider=provider,
        cycle_id=1,
        source_contract=True,
    )


def test_red_one_bad_address_does_not_zero_rest_of_route(monkeypatch):
    """Negatywny oracle P3: zły środkowy stop, oba poprawne nadal wycenione."""
    _deterministic_sequence(
        monkeypatch,
        [("pickup", ["good"]), ("pickup", ["bad"]), ("dropoff", ["good"])],
    )
    routes = live_eta_daemon.build_routes(
        {},
        {
            "good": _order("good"),
            "bad": _order("bad", pickup=None),
        },
        {"75": _gps(30)},
        now=NOW,
        warm_source_enabled=True,
    )
    snapshot = _snapshot(routes[0])

    assert [stop["source"] for stop in snapshot["stops"]] == [
        "live",
        "planned",
        "planned",
    ]
    assert snapshot["stops"][0]["eta_at"] is not None
    assert snapshot["stops"][1]["eta_at"] is None
    assert snapshot["stops"][1]["unpriced_reason"] == "bad_coords"
    assert snapshot["stops"][2]["eta_at"] is not None


def test_red_gps_14h_old_is_never_live(monkeypatch):
    """Pozycja sprzed 14 h nie może przejść przez klasyfikator LIVE."""
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])
    routes = live_eta_daemon.build_routes(
        {},
        {"101": _order("101")},
        {"75": _gps(14 * 3600)},
        now=NOW,
        warm_source_enabled=True,
    )
    snapshot = _snapshot(routes[0])
    assert routes[0]["start"] is None
    assert all(stop["source"] == "planned" for stop in snapshot["stops"])
    assert not any(stop["source"] == "live" for stop in snapshot["stops"])


def test_live_boundary_is_120_seconds_inclusive(monkeypatch):
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])
    at_limit = live_eta_daemon.build_routes(
        {},
        {"101": _order("101")},
        {"75": _gps(120)},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    over_limit = live_eta_daemon.build_routes(
        {},
        {"101": _order("101")},
        {"75": _gps(121)},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    assert at_limit["start_source"] == "live"
    assert over_limit["start_source"] == "planned"


def test_future_gps_is_not_live(monkeypatch):
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])
    gps = _gps(-1)
    route = live_eta_daemon.build_routes(
        {},
        {"101": _order("101")},
        {"75": gps},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    assert route["start"] is None
    assert route["start_source"] == "planned"


def test_warm_on_uses_only_recent_last_event_and_never_calls_it_live(monkeypatch):
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])
    route = live_eta_daemon.build_routes(
        {},
        {"101": _order("101", event_age_s=180)},
        {},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    snapshot = _snapshot(route)
    assert route["start_source"] == "warm"
    assert {stop["source"] for stop in snapshot["stops"]} == {"warm"}
    assert "live" not in {stop["source"] for stop in snapshot["stops"]}


def test_warm_event_older_than_180_seconds_is_planned(monkeypatch):
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])
    route = live_eta_daemon.build_routes(
        {},
        {"101": _order("101", event_age_s=181)},
        {},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    assert route["start"] is None
    assert route["start_source"] == "planned"
    assert _snapshot(route)["stops"][0]["source"] == "planned"


def test_future_last_event_is_not_warm(monkeypatch):
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])
    route = live_eta_daemon.build_routes(
        {},
        {"101": _order("101", event_age_s=-1)},
        {},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    assert route["start"] is None
    assert route["start_source"] == "planned"


def test_flag_off_keeps_legacy_eta_with_additive_stop_identity(monkeypatch):
    """OFF zachowuje ETA, a ADR-010 addytywnie dopina stop_id."""
    _deterministic_sequence(
        monkeypatch, [("pickup", ["101"]), ("dropoff", ["101"])]
    )
    route = live_eta_daemon.build_routes(
        {},
        {"101": _order("101")},
        {"75": {"lat": 53.12, "lon": 23.15}},
        now=NOW,
        warm_source_enabled=False,
    )[0]
    snapshot = live_eta.calculate_live_eta(
        courier_id="75",
        start=route["start"],
        stops=route["stops"],
        now=NOW,
        duration_provider=lambda _points: [60.0, 120.0],
        cycle_id=1,
        source_contract=False,
    )
    assert json.dumps(snapshot, sort_keys=True, separators=(",", ":")) == (
        '{"courier_id":"75","cycle_id":1,"generated_at":"2026-07-28T01:00:00Z",'
        '"orders":{"101":{"delivery_at":"2026-07-28T01:05:00Z",'
        '"pickup_at":"2026-07-28T01:01:00Z"}},"schema_version":1,'
        '"stops":[{"eta_at":"2026-07-28T01:01:00Z","eta_hhmm":"03:01",'
        '"kind":"pickup","order_ids":["101"],"position":0,"stop_id":"pickup:101"},'
        '{"eta_at":"2026-07-28T01:05:00Z","eta_hhmm":"03:05",'
        '"kind":"dropoff","order_ids":["101"],"position":1,"stop_id":"dropoff:101"}]}'
    )


def test_write_cycle_publishes_additive_source_without_breaking_old_reader(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "snapshot.lock")
    route = {
        "courier_id": "75",
        "start": [53.12, 23.15],
        "start_source": "warm",
        "source_contract": True,
        "stops": [
            {
                "stop_id": "pickup:101",
                "kind": "pickup",
                "order_ids": ["101"],
                "coord": [53.13, 23.16],
            }
        ],
    }
    snapshot = live_eta.write_cycle(
        [route],
        now=NOW,
        duration_provider=lambda _points: [60.0],
    )["75"]
    assert snapshot["stops"][0]["source"] == "warm"
    assert live_eta.eta_for(snapshot, "101", "pickup") == (
        "2026-07-28T01:01:00Z"
    )
    assert "source" not in snapshot["orders"]["101"]


def test_flag_is_read_from_hermetic_tmp_flags_not_host(
    tmp_path, monkeypatch
):
    """Wzorzec c1a32e082: pin obu stanów przez tmp flags.json, zero hosta."""
    from dispatch_v2 import common

    flags_path = tmp_path / "flags.json"
    monkeypatch.setattr(common, "FLAGS_PATH", flags_path)
    _deterministic_sequence(monkeypatch, [("pickup", ["101"])])

    flags_path.write_text(
        json.dumps({"ENABLE_LIVE_ETA_WARM_SOURCE": False}), encoding="utf-8"
    )
    common._flags_cache = None
    common._flags_mtime = 0
    off = live_eta_daemon.build_routes(
        {},
        {"101": _order("101", event_age_s=30)},
        {},
        now=NOW,
    )[0]

    flags_path.write_text(
        json.dumps({"ENABLE_LIVE_ETA_WARM_SOURCE": True}), encoding="utf-8"
    )
    common._flags_cache = None
    common._flags_mtime = 0
    on = live_eta_daemon.build_routes(
        {},
        {"101": _order("101", event_age_s=30)},
        {},
        now=NOW,
    )[0]
    assert "start_source" not in off
    assert on["start_source"] == "warm"


def test_mutation_all_or_nothing_would_reopen_the_defect(monkeypatch):
    """Mutation: powrót ``if bad: stops=[]`` musi odróżniać się od oracle R3."""
    _deterministic_sequence(
        monkeypatch,
        [("pickup", ["good"]), ("pickup", ["bad"]), ("dropoff", ["good"])],
    )
    route = live_eta_daemon.build_routes(
        {},
        {
            "good": _order("good"),
            "bad": _order("bad", pickup=None),
        },
        {"75": _gps(30)},
        now=NOW,
        warm_source_enabled=True,
    )[0]
    fixed = _snapshot(route)
    mutated = {**fixed, "stops": [], "orders": {}}
    assert len(fixed["stops"]) == 3
    assert fixed != mutated
    assert sum(stop["eta_at"] is not None for stop in fixed["stops"]) == 2


def test_ratchet_warm_snapshot_has_no_engine_consumer():
    """WARM jest display-only: silnik nie może zacząć czytać snapshotu live ETA."""
    root = Path(live_eta.__file__).parent
    forbidden = []
    for source in root.glob("*.py"):
        if source.name in {"live_eta.py", "live_eta_daemon.py"}:
            continue
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            call = ast.unparse(node.func)
            if call.endswith(
                (
                    "live_eta.read_latest",
                    "live_eta.read_all",
                    "live_eta.eta_for",
                )
            ):
                forbidden.append(f"{source.name}:{node.lineno}:{call}")
    assert forbidden == []


def test_flag_strip_lifecycle_and_doc_ratchets():
    from dispatch_v2 import common

    root = Path(live_eta.__file__).parent
    flag = "ENABLE_LIVE_ETA_WARM_SOURCE"
    assert flag in common.ETAP4_DECISION_FLAGS
    assert common.ENABLE_LIVE_ETA_WARM_SOURCE is False
    registry = json.loads(
        (root / "tools" / "flag_lifecycle_registry.json").read_text(
            encoding="utf-8"
        )
    )
    entry = registry["flags"][flag]
    assert entry["default"] is False
    assert entry["lifecycle"] == "planned"
    assert entry["owner"]["service"] == "dispatch-live-eta.service"
    assert flag in (root / "ZIOMEK_LOGIC_REFERENCE.md").read_text(
        encoding="utf-8"
    )


def test_read_only_coverage_counts_each_source_and_unpriced():
    from dispatch_v2.tools import live_eta_coverage

    report = live_eta_coverage.summarize(
        {
            "entries": {
                "75": {
                    "snapshot": {
                        "stops": [
                            {"source": "live", "eta_at": "2026-07-28T01:01:00Z"},
                            {"source": "warm", "eta_at": "2026-07-28T01:02:00Z"},
                            {"source": "planned", "eta_at": None},
                        ]
                    }
                }
            }
        }
    )
    assert report["stops_total"] == 3
    assert report["sources"] == {"live": 1, "warm": 1, "planned": 1}
    assert report["unpriced"] == 1
    assert report["coverage_priced_pct"] == 66.7
