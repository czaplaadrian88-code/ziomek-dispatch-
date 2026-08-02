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
from pathlib import Path

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import decision_eta_log, live_eta, route_order
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


def test_live_cycle_extends_existing_decision_eta_log_per_order_and_stop(
    tmp_path, monkeypatch
):
    target = tmp_path / "decision_eta_log.jsonl"
    monkeypatch.setattr(decision_eta_log, "LOG_PATH", target)
    monkeypatch.setattr(C, "decision_flag", lambda name: name == decision_eta_log.FLAG)
    decision_eta_log._reset_stats_for_tests()
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
    assert decision_eta_log.record_live_eta_cycle({"75": snapshot})
    rows = [json.loads(line) for line in target.read_text().splitlines()]
    assert [row["order_id"] for row in rows] == ["A", "B"]
    assert all(row["decision_kind"] == "live_eta_cycle" for row in rows)
    assert all(row["writer"] == "live_eta_daemon" for row in rows)
    assert all(row["writer_role"] == "authoritative" for row in rows)
    assert all(row["eta_at"] == "2026-08-02T08:05:00Z" for row in rows)
    assert all(row["plan_version"] == 11 for row in rows)
    assert all(row["sequence_hash"] == snapshot["sequence_hash"] for row in rows)
    serialized = json.dumps(rows)
    for forbidden in ("courier_name", "address", "coords", "latitude", "longitude"):
        assert forbidden not in serialized


def test_history_hook_runs_once_only_after_new_cycle_publish(tmp_path, monkeypatch):
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "snapshot.lock")
    calls: list[dict] = []
    monkeypatch.setattr(
        decision_eta_log,
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


def test_timeline_reads_rotations_and_hides_observer_by_default(tmp_path):
    target = tmp_path / "decision_eta_log.jsonl"
    rotated = tmp_path / "decision_eta_log.jsonl.1"
    base = {
        "schema": decision_eta_log.SCHEMA,
        "decision_kind": "live_eta_cycle",
        "order_id": "A",
        "stop_kind": "dropoff",
        "stop_id": "dropoff:A",
        "plan_version": 11,
        "sequence_hash": "a" * 64,
        "writer": "live_eta_daemon",
    }
    rotated.write_text(json.dumps({
        **base,
        "decision_ts": "2026-08-02T07:59:00Z",
        "eta_at": "2026-08-02T08:10:00Z",
        "writer_role": "observer",
    }) + "\n")
    target.write_text(json.dumps({
        **base,
        "decision_ts": "2026-08-02T08:00:00Z",
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


def test_decision_eta_history_has_shared_retention_and_rename_rotation():
    path = str(decision_eta_log.LOG_PATH)
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
