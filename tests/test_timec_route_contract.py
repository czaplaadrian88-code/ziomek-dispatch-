"""TIME-C Faza 1: wersjonowany kontrakt sekwencji trasy i ETA.

Oracle odtwarza wyścig: stary snapshot ETA pozostaje świeży czasowo, ale plan
i renderowana trasa zdążyły przejść do kolejnej generacji.  Enforcement OFF ma
zachować dotychczasowe ETA, a ON musi je odrzucić.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import ast
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import live_eta, live_eta_daemon, live_eta_history, route_order
from dispatch_v2.core import jsonl_rotation
from dispatch_v2.tools import decision_eta_timeline


NOW = datetime(2026, 8, 2, 8, 0, tzinfo=timezone.utc)

PHYSICAL_V1 = [
    {"stop_id": "pickup:A,B", "kind": "pickup", "order_ids": ["A", "B"]},
    {"stop_id": "dropoff:A", "kind": "dropoff", "order_ids": ["A"]},
    {"stop_id": "dropoff:B", "kind": "dropoff", "order_ids": ["B"]},
]
EXPANDED_V1 = [
    {"stop_id": "pickup:A,B", "kind": "pickup", "order_id": "A", "order_ids": ["A", "B"]},
    {"stop_id": "pickup:A,B", "kind": "pickup", "order_id": "B", "order_ids": ["A", "B"]},
    {"stop_id": "dropoff:A", "kind": "dropoff", "order_id": "A", "order_ids": ["A"]},
    {"stop_id": "dropoff:B", "kind": "dropoff", "order_id": "B", "order_ids": ["B"]},
]
PHYSICAL_V2 = [PHYSICAL_V1[0], PHYSICAL_V1[2], PHYSICAL_V1[1]]


def _old_snapshot() -> dict:
    return {
        "courier_id": "75",
        "generated_at": "2026-08-02T08:00:00Z",
        "plan_version": 11,
        "sequence_hash": route_order.route_sequence_hash(PHYSICAL_V1),
        "orders": {
            "A": {
                "pickup_at": "2026-08-02T08:05:00Z",
                "delivery_at": "2026-08-02T08:20:00Z",
            }
        },
    }


def test_hash_is_one_stable_contract_for_physical_and_expanded_sequence():
    physical = route_order.route_sequence_hash(PHYSICAL_V1)
    expanded = route_order.route_sequence_hash(EXPANDED_V1)
    assert physical == expanded
    assert physical == "b5c49f9b235c45e50754dd5ecbd0834a85f9c9d91671fe55fe57fb7a0c5e2700"
    assert route_order.route_sequence_hash(PHYSICAL_V2) != physical


def test_same_plan_version_different_sequence_has_different_hash_and_is_rejected():
    snapshot = _old_snapshot()
    old_hash = snapshot["sequence_hash"]
    new_hash = route_order.route_sequence_hash(PHYSICAL_V2)

    assert old_hash != new_hash
    accepted, contract = live_eta.bind_snapshot_to_route(
        snapshot,
        PHYSICAL_V2,
        current_plan_version=snapshot["plan_version"],
        enforce=True,
    )
    assert accepted is None
    assert contract["status"] == "sequence_hash_mismatch"


def test_negative_oracle_old_eta_new_plan_off_serves_on_rejects():
    snapshot = _old_snapshot()

    legacy, legacy_contract = live_eta.bind_snapshot_to_route(
        snapshot,
        PHYSICAL_V2,
        current_plan_version=12,
        enforce=False,
    )
    assert legacy is snapshot
    assert live_eta.eta_for(legacy, "A", "dropoff") == "2026-08-02T08:20:00Z"
    assert legacy_contract["status"] == "unchecked"

    accepted, contract = live_eta.bind_snapshot_to_route(
        snapshot,
        PHYSICAL_V2,
        current_plan_version=12,
        enforce=True,
    )
    assert accepted is None
    assert contract["status"] == "sequence_hash_mismatch"
    assert contract["snapshot_plan_version"] == 11
    assert contract["current_plan_version"] == 12


def test_same_sequence_but_old_plan_generation_is_rejected():
    accepted, contract = live_eta.bind_snapshot_to_route(
        _old_snapshot(),
        PHYSICAL_V1,
        current_plan_version=12,
        enforce=True,
    )
    assert accepted is None
    assert contract["status"] == "plan_version_mismatch"


def test_snapshot_writer_carries_plan_version_and_sequence_hash():
    stops = [
        {**PHYSICAL_V1[0], "coord": [53.1, 23.1]},
        {**PHYSICAL_V1[1], "coord": [53.2, 23.2]},
        {**PHYSICAL_V1[2], "coord": [53.3, 23.3]},
    ]
    snapshot = live_eta.calculate_live_eta(
        courier_id="75",
        start=[53.0, 23.0],
        stops=stops,
        now=NOW,
        duration_provider=lambda _points: [60, 60, 60],
        cycle_id=1,
        plan_version=11,
    )
    assert snapshot["plan_version"] == 11
    assert snapshot["sequence_hash"] == route_order.route_sequence_hash(PHYSICAL_V1)


def test_eta_binding_sequence_has_one_fixed_builder_policy(monkeypatch):
    calls = []

    def fake_builder(bag, plan_doc, *, plan_aware, trust_canon):
        calls.append((bag, plan_doc, plan_aware, trust_canon))
        return PHYSICAL_V1

    monkeypatch.setattr(route_order, "build_route_stops", fake_builder)
    bag = [{"order_id": "A"}]
    plan = {"plan_version": 11}
    assert route_order.build_eta_binding_sequence(bag, plan) is PHYSICAL_V1
    assert calls == [(bag, plan, True, True)]


def test_eta_binding_sequence_hash_parity_across_surface_bag_shapes():
    plan = {
        "plan_version": 11,
        "stops": [
            {"order_id": "A", "type": "pickup"},
            {"order_id": "B", "type": "pickup"},
            {"order_id": "A", "type": "dropoff"},
            {"order_id": "B", "type": "dropoff"},
        ],
    }
    rows = [
        {
            "order_id": "A",
            "status": "assigned",
            "restaurant": "R",
            "czas_kuriera_warsaw": "2026-08-02T08:05:00Z",
            "pickup_coords": [53.1, 23.1],
        },
        {
            "order_id": "B",
            "status": "assigned",
            "restaurant": "R",
            "czas_kuriera_warsaw": "2026-08-02T08:07:00Z",
            "pickup_coords": [53.1, 23.1],
        },
    ]
    object_rows = [SimpleNamespace(**row) for row in rows]

    hashes = {
        route_order.route_sequence_hash(
            route_order.build_eta_binding_sequence(shape, plan)
        )
        for shape in (rows, object_rows)
    }
    assert len(hashes) == 1


def test_off_binding_does_not_iterate_or_hash(monkeypatch):
    snapshot = _old_snapshot()

    def forbidden_hash(_stops):
        raise AssertionError("OFF computed a route hash")

    monkeypatch.setattr(route_order, "route_sequence_hash", forbidden_hash)
    accepted, contract = live_eta.bind_snapshot_to_route(
        snapshot,
        object(),
        current_plan_version=11,
        enforce=False,
    )
    assert accepted is snapshot
    assert contract["status"] == "unchecked"
    assert contract["current_sequence_hash"] is None


def test_mutation_removing_hash_guard_reopens_the_race(monkeypatch):
    """Mutation: wymuszenie równości hashy musi ponownie obsłużyć stare ETA."""
    snapshot = _old_snapshot()
    real_hash = route_order.route_sequence_hash

    def mutated_hash(_stops):
        return snapshot["sequence_hash"]

    monkeypatch.setattr(route_order, "route_sequence_hash", mutated_hash)
    def oracle() -> None:
        accepted, _contract = live_eta.bind_snapshot_to_route(
            deepcopy(snapshot),
            PHYSICAL_V2,
            current_plan_version=11,
            enforce=True,
        )
        assert accepted is None, "mutation served ETA from the old route"

    with pytest.raises(AssertionError, match="mutation served ETA"):
        oracle()
    monkeypatch.setattr(route_order, "route_sequence_hash", real_hash)


def test_live_cycle_writes_only_dedicated_history_per_order_and_stop(
    tmp_path, monkeypatch
):
    target = tmp_path / "live_eta_history.jsonl"
    old_target = tmp_path / "decision_eta_log.jsonl"
    monkeypatch.setattr(live_eta_history, "LOG_PATH", target)
    monkeypatch.setattr(C, "decision_flag", lambda name: name == live_eta_history.FLAG)
    live_eta_history._reset_stats_for_tests()
    snapshot = {
        "courier_id": "75",
        "cycle_id": 17,
        "generated_at": "2026-08-02T08:00:00Z",
        "plan_version": 11,
        "sequence_hash": route_order.route_sequence_hash(PHYSICAL_V1),
        "stops": [
            {
                **PHYSICAL_V1[0],
                "eta_at": "2026-08-02T08:05:00Z",
            }
        ],
    }
    assert live_eta_history.record_live_eta_cycle({"75": snapshot})
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["order_id"] for row in rows] == ["A", "B"]
    assert all(row["schema"] == live_eta_history.SCHEMA for row in rows)
    assert all(row["writer"] == "live_eta_daemon" for row in rows)
    assert all(row["writer_role"] == "authoritative" for row in rows)
    assert all(row["eta_at"] == "2026-08-02T08:05:00Z" for row in rows)
    assert all(row["plan_version"] == 11 for row in rows)
    assert all(row["sequence_hash"] == snapshot["sequence_hash"] for row in rows)
    assert not old_target.exists()
    serialized = json.dumps(rows)
    for forbidden in ("courier_name", "address", "coords", "latitude", "longitude"):
        assert forbidden not in serialized


def test_history_hook_runs_once_only_after_new_cycle_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "snapshot.lock")
    calls: list[dict] = []
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: name == live_eta_history.FLAG,
    )
    monkeypatch.setattr(
        live_eta_history,
        "record_live_eta_cycle",
        lambda snapshots: calls.append(dict(snapshots)) or True,
    )
    route = {
        "courier_id": "75",
        "start": [53.0, 23.0],
        "plan_version": 11,
        "sequence_hash": route_order.route_sequence_hash(PHYSICAL_V1[:1]),
        "stops": [{**PHYSICAL_V1[0], "coord": [53.1, 23.1]}],
    }
    for _ in range(2):
        live_eta.write_cycle(
            [route],
            now=NOW,
            duration_provider=lambda _points: [60],
        )
    assert len(calls) == 1
    assert calls[0]["75"]["plan_version"] == 11


def test_history_off_is_byte_parity_and_zero_writes(tmp_path, monkeypatch):
    route = {
        "courier_id": "75",
        "start": [53.0, 23.0],
        "plan_version": 11,
        "sequence_hash": route_order.route_sequence_hash(PHYSICAL_V1[:1]),
        "stops": [{**PHYSICAL_V1[0], "coord": [53.1, 23.1]}],
    }
    flag_state = {"enabled": False}
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: bool(flag_state["enabled"] and name == live_eta_history.FLAG),
    )
    history_path = tmp_path / "live_eta_history.jsonl"
    monkeypatch.setattr(live_eta_history, "LOG_PATH", history_path)

    off_snapshot = tmp_path / "off-snapshot.json"
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", off_snapshot)
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "off.lock")
    live_eta.write_cycle([route], now=NOW, duration_provider=lambda _points: [60])
    off_bytes = off_snapshot.read_bytes()
    assert not history_path.exists()

    flag_state["enabled"] = True
    on_snapshot = tmp_path / "on-snapshot.json"
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", on_snapshot)
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "on.lock")
    live_eta.write_cycle([route], now=NOW, duration_provider=lambda _points: [60])
    assert on_snapshot.read_bytes() == off_bytes
    assert history_path.exists()


def test_bad_stop_and_bad_route_are_isolated_from_healthy_courier(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "snapshot.lock")
    monkeypatch.setattr(C, "decision_flag", lambda _name: False)
    orders_state = {
        "900000001": {
            "status": "assigned",
            "courier_id": "A",
            "pickup_coords": [53.13, 23.16],
            "delivery_coords": [53.14, 23.17],
            "czas_kuriera_warsaw": "2026-08-02 14:10:00",
        },
        "900000002": {
            "status": "assigned",
            "courier_id": "B",
            "pickup_coords": [53.13, 23.16],
            "delivery_coords": None,
            "czas_kuriera_warsaw": "2026-08-02 14:20:00",
        },
    }
    gps = {
        "A": {"lat": 53.12, "lon": 23.15},
        "B": {"lat": 53.12, "lon": 23.15},
    }
    with caplog.at_level(logging.WARNING, logger="live_eta"):
        routes = live_eta_daemon.build_routes(
            {}, orders_state, gps, now=NOW, warm_source_enabled=False
        )
        by_cid = {route["courier_id"]: route for route in routes}
        assert by_cid["B"]["stops"] == []
        assert by_cid["B"]["sequence_hash"] == route_order.route_sequence_hash([])
        out = live_eta.write_cycle(
            routes,
            now=NOW,
            duration_provider=lambda points: [60.0] * (len(points) - 1),
        )
    assert sorted(out) == ["A", "B"]
    assert live_eta.SNAPSHOT_FILE.exists()
    assert "courier_id=B" in caplog.text
    assert "reason=bad_coords" in caplog.text

    malformed = {
        **by_cid["B"],
        "stops": [{**PHYSICAL_V1[0], "coord": [53.1, 23.1]}],
        "sequence_hash": "0" * 64,
    }
    next_now = NOW.replace(second=20)
    with caplog.at_level(logging.WARNING, logger="live_eta"):
        isolated = live_eta.write_cycle(
            [by_cid["A"], malformed],
            now=next_now,
            duration_provider=lambda points: [60.0] * (len(points) - 1),
        )
    assert sorted(isolated) == ["A"]
    assert "route fail-soft courier_id=B error=ValueError" in caplog.text


def test_timeline_reads_rotations_and_hides_observer_by_default(tmp_path):
    target = tmp_path / "live_eta_history.jsonl"
    rotated = tmp_path / "live_eta_history.jsonl.1"
    base = {
        "schema": live_eta_history.SCHEMA,
        "order_id": "A",
        "stop_kind": "dropoff",
        "stop_id": "dropoff:A",
        "plan_version": 11,
        "sequence_hash": "a" * 64,
        "writer": "live_eta_daemon",
    }
    rotated.write_text(json.dumps({
        **base,
        "generated_at": "2026-08-02T07:59:00Z",
        "eta_at": "2026-08-02T08:10:00Z",
        "writer_role": "observer",
    }) + "\n")
    target.write_text(json.dumps({
        **base,
        "generated_at": "2026-08-02T08:00:00Z",
        "eta_at": "2026-08-02T08:12:00Z",
        "writer_role": "authoritative",
    }) + "\n")

    visible = decision_eta_timeline.timeline(order_id="A", log_path=target)
    assert [event["value"] for event in visible] == ["2026-08-02T08:12:00Z"]
    all_rows = decision_eta_timeline.timeline(
        order_id="A", log_path=target, include_observers=True
    )
    assert [event["value"] for event in all_rows] == [
        "2026-08-02T08:10:00Z",
        "2026-08-02T08:12:00Z",
    ]


def test_live_eta_history_has_shared_retention_and_rename_rotation():
    path = str(live_eta_history.LOG_PATH)
    assert path in jsonl_rotation.JSONL_PATHS
    config = (
        Path(__file__).resolve().parents[1]
        / "deploy"
        / "dispatch-v2-jsonl-logrotate.conf"
    ).read_text(encoding="utf-8")
    assert path in config
    assert "rotate 30" in config
    assert "maxsize 100M" in config
    assert "copytruncate" not in "\n".join(
        line for line in config.splitlines() if not line.lstrip().startswith("#")
    )


def test_enable_route_eta_version_check_twin_is_registered_default_off():
    registry_path = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "flag_lifecycle_registry.json"
    )
    flags = json.loads(registry_path.read_text(encoding="utf-8"))["flags"]
    courier = flags["ENABLE_ROUTE_ETA_VERSION_CHECK"]
    panel = flags["ROUTE_ETA_VERSION_CHECK"]
    assert courier["default"] is False
    assert panel["default"] is False
    assert courier["twin_of"] == ["ROUTE_ETA_VERSION_CHECK"]
    assert panel["twin_of"] == ["ENABLE_ROUTE_ETA_VERSION_CHECK"]
    history = flags[live_eta_history.FLAG]
    assert live_eta_history.FLAG == "ENABLE_LIVE_ETA_HISTORY_LOG"
    assert C.ENABLE_LIVE_ETA_HISTORY_LOG is False
    assert "ENABLE_LIVE_ETA_HISTORY_LOG" in C.ETAP4_DECISION_FLAGS
    assert history["default"] is False
    assert "common.py:ETAP4_DECISION_FLAGS" in history["carriers"]


def test_ratchet_exactly_one_route_sequence_hash_definition():
    root = Path(route_order.__file__).resolve().parent
    definitions: list[str] = []
    for source in root.rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        definitions.extend(
            source.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "route_sequence_hash"
        )
    assert definitions == ["route_order.py"]
