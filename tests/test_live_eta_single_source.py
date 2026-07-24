from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dispatch_v2 import live_eta, live_eta_daemon

NOW = datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc)
ROUTE = {
    "courier_id": "75",
    "start": [53.12, 23.15],
    "stops": [
        {
            "kind": "pickup",
            "order_ids": ["101"],
            "coord": [53.13, 23.16],
            "floor_at": ["2026-07-23T12:04:00Z"],
        },
        {
            "kind": "dropoff",
            "order_ids": ["101"],
            "coord": [53.14, 23.17],
        },
    ],
}


def _redirect_store(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(live_eta, "SNAPSHOT_FILE", tmp_path / "snapshot.json")
    monkeypatch.setattr(live_eta, "LOCK_FILE", tmp_path / "snapshot.lock")


def _provider(legs):
    calls = {"n": 0}

    def provide(_points):
        calls["n"] += 1
        return legs

    return calls, provide


def _write(tmp_path, monkeypatch, route, provider, now=NOW):
    _redirect_store(tmp_path, monkeypatch)
    return live_eta.write_cycle(
        [route], now=now, duration_provider=provider
    )["75"]


def _surface_bytes(snapshot):
    values = {
        name: {
            "pickup_at": live_eta.eta_for(snapshot, "101", "pickup"),
            "delivery_at": live_eta.eta_for(snapshot, "101", "dropoff"),
        }
        for name in ("console_fleet", "console_orders", "tile", "map", "app")
    }
    return {
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        for value in values.values()
    }


def test_same_snapshot_is_byte_identical_on_every_surface(tmp_path, monkeypatch):
    _, provider = _provider([60, 120])
    snapshot = _write(tmp_path, monkeypatch, ROUTE, provider)
    assert len(_surface_bytes(snapshot)) == 1
    assert live_eta.eta_for(snapshot, "101", "pickup") == "2026-07-23T12:04:00Z"
    assert live_eta.eta_for(snapshot, "101", "dropoff") == "2026-07-23T12:08:00Z"


def test_same_cycle_and_inputs_are_calculated_once(tmp_path, monkeypatch):
    _redirect_store(tmp_path, monkeypatch)
    calls, provider = _provider([60, 120])
    first = live_eta.write_cycle([ROUTE], now=NOW, duration_provider=provider)
    second = live_eta.write_cycle([ROUTE], now=NOW, duration_provider=provider)
    assert first == second
    assert calls["n"] == 1


def test_one_physical_stop_gives_same_eta_to_every_order_at_address():
    snapshot = live_eta.calculate_live_eta(
        courier_id="75",
        start=[53.12, 23.15],
        stops=[
            {
                "kind": "dropoff",
                "order_ids": ["101", "102"],
                "coord": [53.14, 23.17],
            }
        ],
        now=NOW,
        duration_provider=lambda _points: [120],
        cycle_id=1,
    )
    assert snapshot["orders"]["101"]["delivery_at"] == (
        snapshot["orders"]["102"]["delivery_at"]
    )


def test_position_or_clock_change_updates_all_surfaces_together(tmp_path, monkeypatch):
    _, first_provider = _provider([60, 120])
    first = _write(tmp_path, monkeypatch, ROUTE, first_provider)
    changed = {**ROUTE, "start": [53.11, 23.14]}
    _, delayed_provider = _provider([300, 420])
    delayed = _write(
        tmp_path,
        monkeypatch,
        changed,
        delayed_provider,
        NOW + timedelta(seconds=10),
    )
    assert live_eta.eta_for(first, "101", "delivery") != live_eta.eta_for(
        delayed, "101", "delivery"
    )
    assert len(_surface_bytes(delayed)) == 1


def test_route_change_in_same_cycle_updates_every_surface(tmp_path, monkeypatch):
    _, provider = _provider([60, 120])
    first = _write(tmp_path, monkeypatch, ROUTE, provider)
    changed = {
        **ROUTE,
        "stops": [
            ROUTE["stops"][0],
            {
                "kind": "dropoff",
                "order_ids": ["101"],
                "coord": [53.18, 23.21],
            },
        ],
    }
    _, changed_provider = _provider([60, 600])
    second = _write(tmp_path, monkeypatch, changed, changed_provider)
    assert live_eta.eta_for(first, "101", "delivery") != live_eta.eta_for(
        second, "101", "delivery"
    )
    assert len(_surface_bytes(second)) == 1


def test_eta_is_live_not_frozen_when_courier_is_delayed(tmp_path, monkeypatch):
    _, provider = _provider([600, 300])
    first = _write(tmp_path, monkeypatch, ROUTE, provider)
    second = _write(
        tmp_path, monkeypatch, ROUTE, provider, NOW + timedelta(seconds=10)
    )
    assert live_eta.eta_for(second, "101", "delivery") > live_eta.eta_for(
        first, "101", "delivery"
    )


def test_mutation_local_renderer_recalculation_breaks_parity(tmp_path, monkeypatch):
    """Mutation oracle: powrót lokalnego ``surface_now + duration`` musi czerwienić."""
    _, provider = _provider([60, 120])
    snapshot = _write(tmp_path, monkeypatch, ROUTE, provider)
    canonical = live_eta.eta_for(snapshot, "101", "delivery")
    mutated_local = (NOW + timedelta(seconds=1 + 60 + 120 + 120)).isoformat()
    assert canonical != mutated_local


def test_authoritative_builder_uses_plan_order_state_floors_and_gps(monkeypatch):
    monkeypatch.setattr(
        live_eta_daemon, "_available_floor", lambda _cid, _now: "2026-07-23T12:03:00Z"
    )
    routes = live_eta_daemon.build_routes(
        {
            "75": {
                "invalidated_at": None,
                "stops": [
                    {"order_id": "101", "type": "pickup", "dwell_min": 3},
                    {"order_id": "101", "type": "dropoff", "dwell_min": 2},
                ],
            }
        },
        {
            "101": {
                "courier_id": "75",
                "status": "assigned",
                "restaurant": "R",
                "pickup_coords": [53.13, 23.16],
                "delivery_coords": [53.14, 23.17],
                "czas_kuriera_warsaw": "2026-07-23T12:04:00Z",
                "pickup_at_warsaw": "2026-07-23T12:02:00Z",
            }
        },
        {"75": {"lat": 53.12, "lon": 23.15}},
        now=NOW,
    )
    assert routes[0]["start"] == (53.12, 23.15)
    assert [stop["kind"] for stop in routes[0]["stops"]] == ["pickup", "dropoff"]
    assert routes[0]["stops"][0]["floor_at"] == [
        "2026-07-23T12:04:00Z",
        "2026-07-23T12:02:00Z",
        "2026-07-23T12:03:00Z",
    ]
    assert routes[0]["stops"][0]["dwell_s"] == 180.0


def test_ratchet_exactly_one_calculator_and_one_runtime_writer():
    root = Path(live_eta.__file__).parent
    definitions = []
    writer_calls = []
    for source in root.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "calculate_live_eta":
                    definitions.append(source.name)
            if isinstance(node, ast.Call):
                rendered = ast.unparse(node.func)
                if rendered.endswith("write_cycle"):
                    writer_calls.append(source.name)
    assert definitions == ["live_eta.py"]
    assert writer_calls == ["live_eta_daemon.py"]
    assert not (root / "live_eta_cache.py").exists()
    for source_name in ("plan_recheck.py", "shadow_dispatcher.py"):
        source = (root / source_name).read_text(encoding="utf-8")
        assert "live_eta_cache.write" not in source
        assert "live_order_eta.json" not in source


def test_stale_snapshot_not_served_after_daemon_death(tmp_path, monkeypatch):
    """Martwy/zawieszony live_eta_daemon NIE może w nieskończoność serwować starej godziny
    (Sol cross-check 2026-07-24). Odczyt po TTL musi dać None/pusto → konsument fallback."""
    _redirect_store(tmp_path, monkeypatch)
    store = {
        "schema_version": live_eta.SCHEMA_VERSION,
        "generated_at": live_eta._iso_utc(NOW),
        "entries": {"75": {"snapshot": {"courier_id": "75", "orders": {}}}},
    }
    live_eta.SNAPSHOT_FILE.write_text(json.dumps(store), encoding="utf-8")
    # świeży odczyt w oknie — serwowany
    monkeypatch.setattr(live_eta, "_utc_now", lambda: NOW + timedelta(seconds=5))
    assert live_eta.read_latest("75") is not None
    assert "75" in live_eta.read_all()
    # daemon martwy: odczyt długo po ostatnim cyklu — stale ⇒ None/pusto
    monkeypatch.setattr(live_eta, "_utc_now", lambda: NOW + timedelta(minutes=10))
    assert live_eta.read_latest("75") is None
    assert live_eta.read_all() == {}


def test_staleness_boundary_and_malformed_generated_at(tmp_path, monkeypatch):
    """Edge-case'y guardu świeżości (Sol cross-check 2026-07-24): granica TTL, brak/malformed/
    naive/future ``generated_at``."""
    _redirect_store(tmp_path, monkeypatch)

    def seed(generated_value):
        store = {
            "schema_version": live_eta.SCHEMA_VERSION,
            "entries": {"75": {"snapshot": {"courier_id": "75", "orders": {}}}},
        }
        if generated_value is not None:
            store["generated_at"] = generated_value
        live_eta.SNAPSHOT_FILE.write_text(json.dumps(store), encoding="utf-8")

    ttl = live_eta.STALE_AFTER_SECONDS
    seed(live_eta._iso_utc(NOW))
    # dokładnie na granicy TTL — serwowany (warunek to ">", nie ">=")
    monkeypatch.setattr(live_eta, "_utc_now", lambda: NOW + timedelta(seconds=ttl))
    assert live_eta.read_latest("75") is not None
    # tuż za granicą — stale
    monkeypatch.setattr(live_eta, "_utc_now", lambda: NOW + timedelta(seconds=ttl + 1))
    assert live_eta.read_latest("75") is None
    # future generated_at (zegar konsumenta przed publikacją) — serwowany, nie „stale"
    monkeypatch.setattr(live_eta, "_utc_now", lambda: NOW - timedelta(seconds=5))
    assert live_eta.read_latest("75") is not None
    # brak generated_at — traktuj jak brak danych (pusto)
    seed(None)
    monkeypatch.setattr(live_eta, "_utc_now", lambda: NOW)
    assert live_eta.read_latest("75") is None and live_eta.read_all() == {}
    # malformed generated_at — fail-safe pusto
    seed("nie-jest-data")
    assert live_eta.read_latest("75") is None
