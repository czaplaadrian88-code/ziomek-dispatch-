"""Oracles for the route-order backend DTO -> Kotlin projection monitor."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass
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

FLAGS = {
    "app_route_from_console": True,
    "route_order_unified": True,
    "plan_aware_podjazdy": True,
    "build_view_trust_canon_order": True,
    "eta_version_check": False,
}


@dataclass(frozen=True)
class FakeRouteConfig:
    app_route_from_console: bool
    route_order_unified: bool
    plan_aware_podjazdy: bool
    build_view_trust_canon_order: bool
    eta_version_check: bool = False

    ENV_BY_FIELD = MON.ROUTE_CONFIG_ENV

    @classmethod
    def from_environ(cls, environ):
        return cls(
            **{
                field: environ.get(env_name, "0") == "1"
                for field, env_name in MON.ROUTE_CONFIG_ENV.items()
            }
        )

    def as_dict(self):
        return {
            field: getattr(self, field)
            for field in MON.ROUTE_CONFIG_ENV
        }


ROUTE_CONFIG = FakeRouteConfig(**FLAGS)


def _stop_id(kind, order_ids):
    return f"{kind}:{','.join(sorted(order_ids))}"


def _contract_step(kind, order_ids, committed_by=None):
    members = sorted(order_ids)
    return [
        kind,
        _stop_id(kind, members),
        members,
        (
            [
                [
                    order_id,
                    committed_by[order_id],
                    committed_by[order_id],
                ]
                for order_id in members
            ]
            if kind == "pickup"
            else []
        ),
    ]


def _dto(order_ids=("1",), stops=None, *, restaurant="Rukola", coords=None):
    place = {"name": restaurant, "address": "Lipowa 5"}
    if coords is not None:
        place |= {"lat": coords[0], "lon": coords[1]}
    orders = [
        {
            "order_id": oid,
            "restaurant": dict(place),
            "pickup_time": f"12:0{index}",
            "pickup_committed_at": f"2026-07-28T12:0{index}:00+02:00",
        }
        for index, oid in enumerate(order_ids)
    ]
    return {
        "orders": orders,
        "stop_sequence": stops
        if stops is not None
        else (
            [
                {
                    "order_id": oid,
                    "kind": "pickup",
                    "stop_id": _stop_id("pickup", order_ids),
                    "order_ids": list(order_ids),
                    "committed_at": orders[index]["pickup_committed_at"],
                }
                for index, oid in enumerate(order_ids)
            ]
            + [
                {
                    "order_id": oid,
                    "kind": "dropoff",
                    "stop_id": _stop_id("dropoff", [oid]),
                    "order_ids": [oid],
                }
                for oid in order_ids
            ]
        ),
    }


def _evaluate(bags, dto_builder, canonical):
    return MON.evaluate(
        bags,
        {},
        orders_snapshot={
            str(item["order_id"]): item
            for bag in bags.values()
            for item in bag
        },
        route_config=ROUTE_CONFIG,
        canon_builder=lambda _bag, _plan: canonical,
        dto_builder=dto_builder,
        canon_projector=lambda value: value,
        live_flags=FLAGS,
        corpus_flags=FLAGS,
        observed_at="2026-07-28T00:00:00+00:00",
    )


def test_tristate_ok_oracle():
    canonical = MON.project_kotlin_build_steps(_dto())
    result, exit_code = _evaluate(
        {"501": [{"order_id": "1"}]},
        lambda _cid, _orders, _plans, _config: _dto(),
        canonical,
    )
    assert (result["verdict"], exit_code) == ("OK", MON.EXIT_OK)
    assert result["heartbeat"]["coverage"]["coverage_ratio"] == 1.0

    order_ids = ("A", "B")
    canonical = MON.project_kotlin_build_steps(_dto(order_ids))
    dto = _dto(order_ids, coords=(53.121879, 23.146168))
    # Mutation: backend splits membership despite the canonical shared stop.
    for step in dto["stop_sequence"][:2]:
        step["stop_id"] = _stop_id("pickup", [step["order_id"]])
        step["order_ids"] = [step["order_id"]]
    result, exit_code = _evaluate(
        {"501": [{"order_id": order_id} for order_id in order_ids]},
        lambda _cid, _orders, _plans, _config: dto,
        canonical,
    )
    assert (result["verdict"], exit_code) == (
        "BROKEN",
        MON.EXIT_PARITY_BROKEN,
    )
    assert result["heartbeat"]["coverage"]["mismatch_bags"] == 1
    assert result["heartbeat"]["coverage"]["grouping_only_difference_bags"] == 0


def test_mutation_probe_zero_work_to_ok_is_rejected():
    result, exit_code = _evaluate(
        {}, lambda _cid, _orders, _plans, _config: _dto(), []
    )
    assert (result["verdict"], exit_code) == (
        "EXPECTED_NO_DATA",
        MON.EXIT_EXPECTED_NO_DATA,
    )
    assert result["reason"] == "no qualifying active courier bags"
    assert result["heartbeat"]["coverage"]["coverage_ratio"] is None


def test_mutation_probe_bypassed_kotlin_projection_is_broken(monkeypatch):
    # De-flake (root cause, proven by 3000x isolation stress = ~0.5% fail):
    # evaluate() stamps heartbeat.run_id with a random uuid4 whose hex
    # (chars 0-9a-f) occasionally contains the substring "501" (~0.47%/run),
    # which tripped `assert "501" not in serialized`. This is NOT order
    # dependence -- the collision reproduces standalone. run_id is incidental
    # to this oracle: its intent is that raw courier/order identifiers are
    # hashed (redacted) and never leak into the artifact. Pin run_id to a
    # deterministic, collision-free value so the whole-result redaction scan is
    # hermetic without weakening the assertion or the mutation probe.
    monkeypatch.setattr(
        MON.uuid,
        "uuid4",
        lambda: MON.uuid.UUID("00000000-0000-4000-8000-000000000000"),
    )
    order_id = "900001"
    canonical = MON.project_kotlin_build_steps(_dto((order_id,)))
    divergent = _dto(
        (order_id,),
        stops=[
            {
                "order_id": order_id,
                "kind": "dropoff",
                "stop_id": _stop_id("dropoff", [order_id]),
                "order_ids": [order_id],
            }
        ],
    )
    result, exit_code = _evaluate(
        {"501": [{"order_id": order_id}]},
        lambda _cid, _orders, _plans, _config: divergent,
        canonical,
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

    order_ids = ("A", "B")
    canonical = MON.project_kotlin_build_steps(_dto(order_ids))
    dto = _dto(
        order_ids,
        stops=[
            {
                "order_id": "B",
                "kind": "pickup",
                "stop_id": _stop_id("pickup", ["B"]),
                "order_ids": ["B"],
                "committed_at": "2026-07-28T12:01:00+02:00",
            },
            {
                "order_id": "A",
                "kind": "pickup",
                "stop_id": _stop_id("pickup", ["A"]),
                "order_ids": ["A"],
                "committed_at": "2026-07-28T12:00:00+02:00",
            },
            {
                "order_id": "A",
                "kind": "dropoff",
                "stop_id": _stop_id("dropoff", ["A"]),
                "order_ids": ["A"],
            },
            {
                "order_id": "B",
                "kind": "dropoff",
                "stop_id": _stop_id("dropoff", ["B"]),
                "order_ids": ["B"],
            },
        ],
        coords=(53.121879, 23.146168),
    )

    result, exit_code = _evaluate(
        {"501": [{"order_id": order_id} for order_id in order_ids]},
        lambda _cid, _orders, _plans, _config: dto,
        canonical,
    )

    assert (result["verdict"], exit_code) == (
        "BROKEN",
        MON.EXIT_PARITY_BROKEN,
    )
    assert result["heartbeat"]["coverage"]["mismatch_bags"] == 1
    assert result["heartbeat"]["coverage"]["grouping_only_difference_bags"] == 0
    assert result["mismatches"][0]["grouping_only_difference"] is False


def test_mutation_group_min_or_max_committed_is_broken():
    order_ids = ("A", "B")
    dto = _dto(order_ids)
    canonical = MON.project_kotlin_build_steps(dto)
    for synthetic in (
        "2026-07-28T12:00:00+02:00",
        "2026-07-28T12:01:00+02:00",
    ):
        mutated = json.loads(json.dumps(dto))
        for step in mutated["stop_sequence"][:2]:
            step["committed_at"] = synthetic
        result, exit_code = _evaluate(
            {"501": [{"order_id": order_id} for order_id in order_ids]},
            lambda _cid, _orders, _plans, _config, value=mutated: value,
            canonical,
        )
        assert (result["verdict"], exit_code) == (
            "BROKEN",
            MON.EXIT_PARITY_BROKEN,
        )


def test_qualifying_bag_backend_error_is_broken_not_no_data():
    def fail(_cid, _orders, _plans, _config):
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
        "grouping_only_difference_bags": 0,
        "error_bags": 1,
    }


def test_route_config_drift_is_separate_verdict_and_short_circuits_routes():
    calls = []

    result, exit_code = MON.evaluate(
        {},
        {},
        orders_snapshot={},
        route_config=FakeRouteConfig(
            **(FLAGS | {"app_route_from_console": False})
        ),
        canon_builder=lambda _bag, _plan: [],
        dto_builder=lambda *_args: calls.append(_args) or _dto(),
        canon_projector=lambda value: value,
        live_flags=FLAGS | {"app_route_from_console": False},
        corpus_flags=FLAGS,
        observed_at="2026-07-28T00:00:00+00:00",
    )
    assert (result["verdict"], exit_code) == (
        "CONFIG_DRIFT",
        MON.EXIT_CONFIG_DRIFT,
    )
    assert result["verdict_class"] == "INFRA_BROKEN"
    assert result["flag_drift"] is True
    assert calls == []
    assert "routes differ" not in result["reason"]
    assert "CONFIG_DRIFT" in result["open_gates_line"]


def test_corpus_pins_all_courier_api_route_flags():
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    assert corpus["meta"]["courier_api_route_config"] == FLAGS


def test_process_environ_is_read_from_systemd_main_pid(tmp_path):
    proc = tmp_path / "proc"
    environ_path = proc / "4312" / "environ"
    environ_path.parent.mkdir(parents=True)
    environ_path.write_bytes(
        b"UNRELATED=x\0"
        b"ENABLE_APP_ROUTE_FROM_CONSOLE=1\0"
        b"ENABLE_ROUTE_ORDER_UNIFIED=1\0"
        b"ENABLE_PLAN_AWARE_PODJAZDY=1\0"
        b"ENABLE_BUILD_VIEW_TRUST_CANON_ORDER=1\0"
    )
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="4312\n", stderr="")

    environ = MON._read_service_environ(proc_root=proc, run=run)
    assert FakeRouteConfig.from_environ(environ) == ROUTE_CONFIG
    assert commands[0][0] == [
        "systemctl",
        "show",
        "courier-api.service",
        "--property=MainPID",
        "--value",
        "--no-pager",
    ]


def test_missing_process_environ_is_infra_broken_not_local_env(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("ENABLE_APP_ROUTE_FROM_CONSOLE", "1")

    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout="4312\n", stderr="")

    with pytest.raises(MON.ProcessConfigError, match="cannot read"):
        MON._read_service_environ(proc_root=tmp_path / "proc", run=run)
    result, exit_code = MON._infra_result(
        MON.ProcessConfigError("cannot read process environ")
    )
    assert (result["verdict"], result["verdict_class"], exit_code) == (
        "BROKEN",
        "INFRA_BROKEN",
        MON.EXIT_INFRA_BROKEN,
    )
    assert result["live_flags"] == {}


def test_one_snapshot_is_passed_to_both_sides_despite_file_mutation(tmp_path):
    orders_path = tmp_path / "orders.json"
    plans_path = tmp_path / "plans.json"
    original_order = {"order_id": "1", "courier_id": "501", "status": "assigned"}
    orders_path.write_text(json.dumps({"1": original_order}), encoding="utf-8")
    plans_path.write_text(json.dumps({"501": {"stops": []}}), encoding="utf-8")

    orders_snapshot, plans_snapshot = MON._load_state_snapshots(
        orders_path, plans_path
    )
    orders_path.write_text(
        json.dumps({"2": {"order_id": "2", "courier_id": "501"}}),
        encoding="utf-8",
    )
    plans_path.write_text(json.dumps({"501": {"stops": ["mutated"]}}), encoding="utf-8")
    seen = []

    def dto_builder(_cid, orders_arg, plans_arg, route_config_arg):
        seen.append((orders_arg, plans_arg, route_config_arg))
        return _dto()

    canonical = MON.project_kotlin_build_steps(_dto())
    result, exit_code = MON.evaluate(
        {"501": [original_order]},
        plans_snapshot,
        orders_snapshot=orders_snapshot,
        route_config=ROUTE_CONFIG,
        canon_builder=lambda bag, plan: (
            canonical
            if bag[0]["order_id"] == "1" and plan == {"stops": []}
            else [_contract_step("dropoff", ["mutated"])]
        ),
        dto_builder=dto_builder,
        canon_projector=lambda value: value,
        live_flags=FLAGS,
        corpus_flags=FLAGS,
        observed_at="2026-07-28T00:00:00+00:00",
    )
    assert (result["verdict"], exit_code) == ("OK", MON.EXIT_OK)
    assert seen == [(orders_snapshot, plans_snapshot, ROUTE_CONFIG)]


def test_kotlin_projection_real_dto_structures():
    # Membership comes directly from stop_id/order_ids.
    dto = _dto(("1", "2"))
    assert MON.project_kotlin_build_steps(dto) == [
        _contract_step(
            "pickup",
            ["1", "2"],
            {
                "1": "2026-07-28T12:00:00+02:00",
                "2": "2026-07-28T12:01:00+02:00",
            },
        ),
        _contract_step("dropoff", ["1"]),
        _contract_step("dropoff", ["2"]),
    ]

    # Same coordinates or names cannot change membership.
    changed = _dto(("1", "2"), coords=(53.121879, 23.146168))
    changed["orders"][1]["restaurant"]["name"] = "Mama Thai"
    assert MON.project_kotlin_build_steps(changed) == (
        MON.project_kotlin_build_steps(dto)
    )


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
                "pickup_committed_at": pickup_iso,
            }
        )
    stops = []
    for kind, ids in case["expected_proj"]:
        stop_id = _stop_id(kind, ids)
        for oid in ids:
            step = {
                "order_id": oid,
                "kind": kind,
                "stop_id": stop_id,
                "order_ids": list(ids),
            }
            if kind == "pickup":
                step["committed_at"] = bag_by_id[oid].get(
                    "czas_kuriera_warsaw"
                )
            stops.append(step)
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
        actual = MON.project_kotlin_build_steps(_dto_from_corpus_case(case))
        expected = MON.project_canonical_stops(
            [
                {
                    "kind": kind,
                    "stop_id": _stop_id(kind, ids),
                    "order_ids": ids,
                    "committed_by_order": {
                        oid: next(
                            item.get("czas_kuriera_warsaw")
                            for item in case["bag"]
                            if str(item["order_id"]) == str(oid)
                        )
                        for oid in ids
                    },
                }
                for kind, ids in case["expected_proj"]
            ]
        )
        assert actual == expected, case["id"]


def test_result_file_is_atomic_0600_and_contains_heartbeat(tmp_path):
    result, _ = _evaluate(
        {}, lambda _cid, _orders, _plans, _config: _dto(), []
    )
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
    compact = " ".join(source.split())
    assert "build_view_from_snapshots" in source
    assert (
        "dto_builder( courier_id, orders_snapshot, plans, route_config )"
        in compact
    )
    assert "_read_service_environ" in source
    assert "_dropin_on" not in source
    assert "os.environ.get(\"ENABLE_" not in source
    assert "fleet_state" not in source
    assert "_build_route" not in source
    assert "_pickup_together" not in source
    assert "_restaurant_key" not in source
    assert "PICKUP_MERGE_MIN" not in source
    assert "stop_id" in source and "order_ids" in source


def test_backend_contract_requires_snapshot_builder_and_explicit_config():
    class Module:
        RouteConfig = FakeRouteConfig

        @staticmethod
        def build_view_from_snapshots(
            courier_id, orders_snapshot, plans_snapshot, route_config
        ):
            assert courier_id == "501"
            assert orders_snapshot == {"1": {"order_id": "1"}}
            assert plans_snapshot == {"501": {"stops": []}}
            assert route_config == ROUTE_CONFIG
            return _dto()

    builder, route_config_type = MON._backend_contract(Module)
    assert route_config_type is FakeRouteConfig
    assert builder(
        "501",
        {"1": {"order_id": "1"}},
        {"501": {"stops": []}},
        ROUTE_CONFIG,
    )["stop_sequence"]

    class LegacyModule:
        @staticmethod
        def build_view(_courier_id):
            return _dto()

    with pytest.raises(RuntimeError, match="build_view_from_snapshots"):
        MON._backend_contract(LegacyModule)


def test_backend_loader_prefers_courier_api_over_worktree_config_namespace(
    tmp_path
):
    """Negatywny oracle: dispatch_v2/config nie może podszyć się pod courier config."""
    wrong_root = tmp_path / "dispatch-worktree"
    (wrong_root / "config").mkdir(parents=True)
    courier_root = tmp_path / "courier_api"
    courier_root.mkdir()
    (courier_root / "config.py").write_text(
        "COURIER_ACTIVE_STATUSES = ('assigned',)\n", encoding="utf-8"
    )
    (courier_root / "courier_orders.py").write_text(
        "import config\n"
        "CONFIG_ORIGIN = config.__file__\n"
        "ACTIVE = config.COURIER_ACTIVE_STATUSES\n",
        encoding="utf-8",
    )
    probe = (
        "import importlib.util, json\n"
        "from pathlib import Path\n"
        f"tool = Path({str(TOOL)!r})\n"
        "spec = importlib.util.spec_from_file_location('parity_probe', tool)\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        f"mod._COURIER_API = Path({str(courier_root)!r})\n"
        "loaded = mod._load_backend_module()\n"
        "print(json.dumps({'orders': loaded.__file__, "
        "'config': loaded.CONFIG_ORIGIN, 'active': loaded.ACTIVE}))\n"
    )
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = (
        str(wrong_root)
        + os.pathsep
        + child_env.get("PYTHONPATH", "")
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        timeout=30,
        env=child_env,
    )
    assert completed.returncode == 0, completed.stderr
    loaded = json.loads(completed.stdout)
    assert Path(loaded["config"]).resolve() == (courier_root / "config.py").resolve()
    assert Path(loaded["orders"]).resolve() == (
        courier_root / "courier_orders.py"
    ).resolve()
    assert loaded["active"] == ["assigned"]


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
