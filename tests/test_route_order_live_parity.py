"""Oracles for the route-order backend DTO -> Kotlin projection monitor."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "route_order_live_parity_check.py"
CORPUS = ROOT / "tests" / "golden" / "route_order_corpus.json"
PANEL_PY = Path(
    "/root/.openclaw/workspace/nadajesz_clone/panel/backend/.venv/bin/python"
)

_spec = importlib.util.spec_from_file_location("route_parity_monitor", TOOL)
assert _spec and _spec.loader
MON = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MON)

FLAGS = {"plan_aware": True, "trust_canon": True, "panel": {"x": True}}


def _dto(order_ids=("1",), stops=None, *, restaurant="Rukola", coords=None):
    place = {"name": restaurant, "address": "Lipowa 5"}
    if coords is not None:
        place |= {"lat": coords[0], "lon": coords[1]}
    orders = [
        {
            "order_id": oid,
            "restaurant": dict(place),
            "pickup_time": f"12:0{index}",
        }
        for index, oid in enumerate(order_ids)
    ]
    return {
        "orders": orders,
        "stop_sequence": stops
        if stops is not None
        else [
            {"order_id": oid, "kind": kind}
            for kind in ("pickup", "dropoff")
            for oid in order_ids
        ],
    }


def _evaluate(bags, dto_builder, canonical):
    return MON.evaluate(
        bags,
        {},
        canon_builder=lambda _bag, _plan: canonical,
        dto_builder=dto_builder,
        canon_projector=lambda value: value,
        live_flags=FLAGS,
        corpus_flags=FLAGS,
        observed_at="2026-07-28T00:00:00+00:00",
    )


def test_tristate_ok_oracle():
    canonical = [["pickup", ["1"]], ["dropoff", ["1"]]]
    result, exit_code = _evaluate(
        {"501": [{"order_id": "1"}]}, lambda _cid: _dto(), canonical
    )
    assert (result["verdict"], exit_code) == ("OK", MON.EXIT_OK)
    assert result["heartbeat"]["coverage"]["coverage_ratio"] == 1.0


def test_mutation_probe_zero_work_to_ok_is_rejected():
    result, exit_code = _evaluate({}, lambda _cid: _dto(), [])
    assert (result["verdict"], exit_code) == (
        "EXPECTED_NO_DATA",
        MON.EXIT_EXPECTED_NO_DATA,
    )
    assert result["reason"] == "no qualifying active courier bags"
    assert result["heartbeat"]["coverage"]["coverage_ratio"] is None


def test_mutation_probe_bypassed_kotlin_projection_is_broken():
    order_id = "900001"
    canonical = [["pickup", [order_id]], ["dropoff", [order_id]]]
    divergent = _dto(
        (order_id,),
        stops=[{"order_id": order_id, "kind": "dropoff"}],
    )
    result, exit_code = _evaluate(
        {"501": [{"order_id": order_id}]}, lambda _cid: divergent, canonical
    )
    assert (result["verdict"], exit_code) == (
        "BROKEN",
        MON.EXIT_PARITY_BROKEN,
    )
    assert result["heartbeat"]["coverage"]["mismatch_bags"] == 1
    assert "ALARM: route parity BROKEN" in result["open_gates_line"]
    serialized = json.dumps(result)
    assert "501" not in serialized
    assert order_id not in serialized  # identifiers are hashed in artifacts


def test_qualifying_bag_backend_error_is_broken_not_no_data():
    def fail(_cid):
        raise RuntimeError("injected DTO failure")

    result, exit_code = _evaluate(
        {"501": [{"order_id": "1"}]}, fail, [["pickup", ["1"]]]
    )
    assert (result["verdict"], exit_code) == (
        "BROKEN",
        MON.EXIT_INFRA_BROKEN,
    )
    assert result["heartbeat"]["coverage"] == {
        "qualifying_bags": 1,
        "checked_bags": 0,
        "coverage_ratio": 0.0,
        "mismatch_bags": 0,
        "error_bags": 1,
    }


def test_ENABLE_PLAN_AWARE_PODJAZDY_on_off_drift_is_broken_without_data():
    result, exit_code = MON.evaluate(
        {},
        {},
        canon_builder=lambda _bag, _plan: [],
        dto_builder=lambda _cid: _dto(),
        canon_projector=lambda value: value,
        live_flags=FLAGS | {"plan_aware": False},
        corpus_flags=FLAGS | {"plan_aware": True},
        observed_at="2026-07-28T00:00:00+00:00",
    )
    assert (result["verdict"], exit_code) == (
        "BROKEN",
        MON.EXIT_PARITY_BROKEN,
    )
    assert result["flag_drift"] is True


def test_kotlin_projection_real_dto_structures():
    # Same restaurant/close time: one pickup tile, then separate deliveries.
    dto = _dto(("1", "2"))
    assert MON.project_kotlin_build_steps(dto) == [
        ["pickup", ["1", "2"]],
        ["dropoff", ["1"]],
        ["dropoff", ["2"]],
    ]

    # Same coordinates win over different names exactly like restaurantKey.kt.
    dto = _dto(("1", "2"), coords=(53.121879, 23.146168))
    dto["orders"][1]["restaurant"]["name"] = "Mama Thai"
    assert MON.project_kotlin_build_steps(dto)[0] == ["pickup", ["1", "2"]]

    # More than ten minutes: two pickup steps.
    dto["orders"][1]["pickup_time"] = "12:11"
    assert MON.project_kotlin_build_steps(dto)[:2] == [
        ["pickup", ["1"]],
        ["pickup", ["2"]],
    ]


def _dto_from_corpus_case(case):
    orders = []
    bag_by_id = {str(item["order_id"]): item for item in case["bag"]}
    for oid, item in bag_by_id.items():
        pickup_iso = item.get("czas_kuriera_warsaw")
        pickup_time = pickup_iso[11:16] if pickup_iso else None
        coords = item.get("pickup_coords")
        restaurant = {
            "name": item.get("restaurant"),
            "address": item.get("pickup_address"),
            "lat": coords[0] if coords else None,
            "lon": coords[1] if coords else None,
        }
        orders.append(
            {
                "order_id": oid,
                "restaurant": restaurant,
                "pickup_time": pickup_time,
            }
        )
    stops = [
        {"order_id": oid, "kind": kind}
        for kind, ids in case["expected_proj"]
        for oid in ids
    ]
    return {"orders": orders, "stop_sequence": stops}


def test_kotlin_projection_reuses_existing_route_order_golden_min_three_cases():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    selected = {
        "syn_single_order",
        "syn_same_restaurant_bundle",
        "syn_carried_first",
    }
    cases = [case for case in corpus["cases"] if case["id"] in selected]
    assert len(cases) == 3
    for case in cases:
        assert MON.project_kotlin_build_steps(_dto_from_corpus_case(case)) == (
            case["expected_proj"]
        ), case["id"]


def test_result_file_is_atomic_0600_and_contains_heartbeat(tmp_path):
    result, _ = _evaluate({}, lambda _cid: _dto(), [])
    target = tmp_path / "route-parity.json"
    MON._atomic_write_json(target, result)
    persisted = json.loads(target.read_text(encoding="utf-8"))
    assert persisted["verdict"] == "EXPECTED_NO_DATA"
    assert persisted["heartbeat"]["observed_at_utc"]
    assert target.stat().st_mode & 0o777 == 0o600
    assert not [
        path for path in tmp_path.iterdir() if path.name.startswith(".route-parity")
    ]


def test_structural_ratchet_uses_backend_dto_and_not_panel_twin():
    source = TOOL.read_text(encoding="utf-8")
    assert "_load_backend_dto_builder()" in source
    assert "project_kotlin_build_steps(dto_builder(courier_id))" in source
    assert "fleet_state" not in source
    assert "_build_route" not in source


def test_real_backend_builder_known_writer_is_suppressed_and_restored():
    calls = []

    class Earnings:
        @staticmethod
        def record_day(*args):
            calls.append(args)

    class Module:
        earnings_history = Earnings()

    original = Module.earnings_history.record_day

    def builder(_cid):
        Module.earnings_history.record_day("would-write")
        return _dto()

    safe_builder = MON._read_only_backend_builder(Module, builder)
    assert safe_builder("501")["stop_sequence"]
    assert calls == []
    assert Module.earnings_history.record_day is original


@pytest.mark.skipif(
    os.environ.get("ENABLE_ROUTE_ORDER_LIVE_PARITY", "1") != "1",
    reason="live read-only smoke is separate from hermetic regression",
)
def test_live_route_order_parity_read_only_smoke():
    assert PANEL_PY.exists(), f"missing panel venv: {PANEL_PY}"
    completed = subprocess.run(
        [str(PANEL_PY), str(TOOL), "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    verdict = json.loads(completed.stdout)
    assert completed.returncode in {
        MON.EXIT_OK,
        MON.EXIT_EXPECTED_NO_DATA,
    }, json.dumps(verdict, ensure_ascii=False, indent=1)
    assert verdict["verdict"] in {"OK", "EXPECTED_NO_DATA"}
