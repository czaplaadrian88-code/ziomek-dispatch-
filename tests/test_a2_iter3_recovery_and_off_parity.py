"""A-2 iteration 3 regression oracles from the rejected blind review.

These tests are intentionally narrow:

* every recovery CAS token is durably reserved before it can escape;
* one transient EMFILE is retried against the healthy main;
* PLAN feature-OFF ignores ON sidecars, including after rollback;
* orders_state persistence v2 is a separate default-OFF switch and OFF keeps
  the exact legacy writer/reader contract.

All state, flags, notifications, and logs are redirected before state modules
are imported.  No test path can reach production state.
"""
from __future__ import annotations

import copy
import ast
import errno
import json
import types
from pathlib import Path

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import state_machine as SM
from dispatch_v2 import state_persistence as SP


def _blocked(*_args, **_kwargs):
    return True


def _plan_body(tag: str) -> dict:
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": tag},
        "start_ts": "2026-08-03T12:00:00+00:00",
        "stops": [{
            "order_id": tag,
            "type": "dropoff",
            "coords": {"lat": 53.14, "lng": 23.16},
        }],
        "optimization_method": "incremental",
    }


def _clear_plan_cache() -> None:
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None


@pytest.fixture
def plan_store(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", True, raising=False)
    decision_log = types.ModuleType("dispatch_v2.decision_eta_log")
    decision_log.record_plan_commit = _blocked
    monkeypatch.setitem(
        __import__("sys").modules,
        "dispatch_v2.decision_eta_log",
        decision_log,
    )
    _clear_plan_cache()
    return tmp_path


def _hwm_value() -> int:
    return int(json.loads(PM.version_hwm_path().read_text(
        encoding="utf-8"
    ))["last_issued"])


def test_recovery_persists_hwm_before_issuing_any_token_and_closes_blind_aba(
    plan_store, monkeypatch
):
    for index in range(8):
        PM.save_plan(f"c{index}", _plan_body(f"seed-{index}"))
    healthy_main = PM.PLANS_FILE.read_bytes()
    hwm_before = _hwm_value()

    PM.PLANS_FILE.write_text("{ transient", encoding="utf-8")
    _clear_plan_cache()
    recovered = PM.load_plans()
    held_token = int(recovered["c0"]["plan_version"])
    issued_tokens = {
        int(plan["plan_version"])
        for plan in recovered.values()
        if isinstance(plan, dict)
    }

    # Persist-then-issue: all values visible in the returned view are already
    # below the durable fence.  The rejected candidate leaves HWM unchanged.
    assert issued_tokens
    assert _hwm_value() >= max(issued_tokens) > hwm_before

    # Recreate the blind's adversarial "main becomes healthy again" window.
    # Even an out-of-band restoration cannot make the allocator reissue a
    # token that has already escaped from recovery.
    PM.PLANS_FILE.write_bytes(healthy_main)
    _clear_plan_cache()
    legitimate = PM.save_plan("c0", _plan_body("LEGITIMATE"))
    assert int(legitimate["plan_version"]) not in issued_tokens
    with pytest.raises(PM.ConcurrencyError):
        PM.save_plan(
            "c0",
            _plan_body("STALE-CLOBBER"),
            expected_version=held_token,
        )
    final = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))["c0"]
    assert final["start_pos"]["source"] == "LEGITIMATE"


def test_one_transient_emfile_retries_healthy_main_without_recovery(
    plan_store, monkeypatch
):
    for index in range(3):
        PM.save_plan(f"c{index}", _plan_body(f"seed-{index}"))
    expected = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    hwm_before = _hwm_value()
    real_open = open
    fired = {"value": False}

    def flaky_open(path, *args, **kwargs):
        if not fired["value"] and Path(path) == PM.PLANS_FILE:
            fired["value"] = True
            raise OSError(errno.EMFILE, "synthetic transient EMFILE")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(SP, "open", flaky_open, raising=False)
    _clear_plan_cache()
    assert PM.load_plans() == expected
    assert fired["value"] is True
    assert _hwm_value() == hwm_before


def test_mutation_removed_hwm_commit_reissues_recovery_token(
    plan_store, monkeypatch
):
    """Mutation proof: deleting the durable reservation reopens blind N-1."""
    for index in range(8):
        PM.save_plan(f"c{index}", _plan_body(f"seed-{index}"))
    healthy_main = PM.PLANS_FILE.read_bytes()
    hwm_before = _hwm_value()

    # Physical runtime mutant of the exact persist-before-issue operation.
    monkeypatch.setattr(PM, "_write_version_hwm", lambda _value: None)
    PM.PLANS_FILE.write_text("{ transient", encoding="utf-8")
    _clear_plan_cache()
    recovered = PM.load_plans()
    held = int(recovered["c0"]["plan_version"])
    assert _hwm_value() == hwm_before

    PM.PLANS_FILE.write_bytes(healthy_main)
    _clear_plan_cache()
    legitimate = PM.save_plan("c0", _plan_body("LEGITIMATE-MUTANT"))
    # This equality is the rejected blind's ABA precondition. The non-mutated
    # oracle above asserts the opposite, so removing the fix turns it RED.
    assert int(legitimate["plan_version"]) == held


def test_recovery_hwm_rename_precedes_recovered_main_rename(
    plan_store, monkeypatch
):
    first = PM.save_plan("9", _plan_body("v1"))
    PM.save_plan(
        "9", _plan_body("lost-v2"),
        expected_version=int(first["plan_version"]),
    )
    PM.PLANS_FILE.write_text("{ corrupt", encoding="utf-8")
    _clear_plan_cache()
    events = []
    real_replace = SP.os.replace

    def record_replace(source, destination):
        destination = Path(destination)
        if destination in {PM.version_hwm_path(), PM.PLANS_FILE}:
            events.append(destination)
        return real_replace(source, destination)

    monkeypatch.setattr(SP.os, "replace", record_replace)
    PM.load_plans()
    assert events.index(PM.version_hwm_path()) < events.index(PM.PLANS_FILE)


def test_plan_off_after_on_is_byte_legacy_and_sidecars_are_inert(
    plan_store, monkeypatch
):
    first = PM.save_plan("9", _plan_body("on"))
    current = PM.save_plan(
        "9", _plan_body("on-2"), expected_version=int(first["plan_version"])
    )
    main_before = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    prev_before = SP.previous_path(PM.PLANS_FILE).read_bytes()

    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", False, raising=False)

    # Blind N-3: a stale predecessor from ON must not change strict legacy
    # missing-main behaviour after rollback.
    main_bytes = PM.PLANS_FILE.read_bytes()
    PM.PLANS_FILE.unlink()
    _clear_plan_cache()
    assert PM.load_plans(_raise_on_corrupt=True) == {}
    PM.PLANS_FILE.write_bytes(main_bytes)

    # Removing an ON-only HWM after rollback must not disable the old writer.
    PM.version_hwm_path().unlink()
    fixed_now = "2026-08-03T18:00:00+00:00"
    monkeypatch.setattr(PM, "_now_iso", lambda: fixed_now)
    saved = PM.save_plan(
        "9", _plan_body("off"), expected_version=int(current["plan_version"])
    )
    assert int(saved["plan_version"]) == int(current["plan_version"]) + 1

    expected = copy.deepcopy(main_before)
    expected["9"] = saved
    expected_bytes = json.dumps(
        expected, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert PM.PLANS_FILE.read_bytes() == expected_bytes
    assert not PM.version_hwm_path().exists()
    assert SP.previous_path(PM.PLANS_FILE).read_bytes() == prev_before


@pytest.fixture
def orders_store(tmp_path, monkeypatch):
    state_path = tmp_path / "orders_state.json"
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(SM, "_alert_state_read_failure", lambda _detail: None)
    return state_path


def _order(index: int) -> dict:
    return {
        "order_id": str(index),
        "status": "assigned",
        "restaurant": "Żuraw",
    }


def _state_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def test_orders_state_persistence_v2_defaults_off_and_off_is_exact_legacy(
    orders_store, monkeypatch
):
    assert C.ENABLE_ORDERS_STATE_PERSISTENCE_V2 is False
    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", False, raising=False
    )
    original = {"1": _order(1)}
    replacement = {"1": _order(1), "2": _order(2)}
    orders_store.write_bytes(_state_bytes(original))

    # A directory at .prev makes the predecessor backup fail.  Legacy OFF is
    # best-effort and still commits the byte-identical main.
    predecessor = SP.previous_path(orders_store)
    predecessor.mkdir()
    SM._guarded_write(orders_store, replacement, old_count=1, op="upsert")
    assert orders_store.read_bytes() == _state_bytes(replacement)


def test_orders_state_flag_selects_array_read_contract(
    orders_store, monkeypatch
):
    orders_store.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", False, raising=False
    )
    assert SM._read_state() == [1, 2, 3]

    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", True, raising=False
    )
    assert SM._read_state() == {}


def test_orders_state_off_after_on_uses_legacy_writer_byte_for_byte(
    orders_store, monkeypatch
):
    first = {"1": _order(1)}
    second = {"1": _order(1), "2": _order(2)}
    third = {**second, "3": _order(3)}
    orders_store.write_bytes(_state_bytes(first))

    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", True, raising=False
    )
    SM._guarded_write(orders_store, second, old_count=1, op="upsert")
    assert SP.previous_path(orders_store).exists()

    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", False, raising=False
    )
    real_atomic = SP.atomic_write_json

    def forbidden_v2(*_args, **_kwargs):
        raise AssertionError("orders_state v2 writer reached while flag is OFF")

    monkeypatch.setattr(SP, "atomic_write_json", forbidden_v2)
    SM._guarded_write(orders_store, third, old_count=2, op="upsert")
    assert orders_store.read_bytes() == _state_bytes(third)
    monkeypatch.setattr(SP, "atomic_write_json", real_atomic)


def test_orders_state_corrupt_and_backup_failure_are_selected_by_flag(
    orders_store, monkeypatch
):
    replacement = {"1": _order(1), "2": _order(2)}
    predecessor = SP.previous_path(orders_store)

    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", False, raising=False
    )
    orders_store.write_text("{ corrupt", encoding="utf-8")
    predecessor.write_bytes(_state_bytes({"9": _order(9)}))
    SM._guarded_write(orders_store, replacement, old_count=1, op="upsert")
    assert orders_store.read_bytes() == _state_bytes(replacement)

    predecessor.unlink()
    orders_store.write_bytes(_state_bytes({"1": _order(1)}))
    predecessor.mkdir()
    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", True, raising=False
    )
    with pytest.raises(IsADirectoryError):
        SM._guarded_write(orders_store, replacement, old_count=1, op="upsert")
    assert orders_store.read_bytes() == _state_bytes({"1": _order(1)})


def test_orders_state_all_write_sites_use_one_flagged_choke_point():
    source = Path(SM.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    direct_owner_calls = []
    calls_by_function = {}
    for function in (
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ):
        names = []
        for node in ast.walk(function):
            if not isinstance(node, ast.Call):
                continue
            callee = ast.unparse(node.func)
            names.append(callee)
            if callee in {
                "_state_store.atomic_write_json",
                "_state_store.legacy_atomic_write_json",
            }:
                direct_owner_calls.append((function.name, callee))
        calls_by_function[function.name] = names

    assert sorted(direct_owner_calls) == sorted([
        ("_write_state", "_state_store.atomic_write_json"),
        ("_write_state", "_state_store.legacy_atomic_write_json"),
    ])
    assert "_write_state" in calls_by_function["_guarded_write"]
    assert "_write_state" in calls_by_function["prune_terminal_orders"]


def test_orders_state_v2_is_registered_as_default_off():
    root = Path(PM.__file__).parent
    registry = json.loads((root / "tools/flag_lifecycle_registry.json").read_text(
        encoding="utf-8"
    ))
    entry = registry["flags"]["ENABLE_ORDERS_STATE_PERSISTENCE_V2"]
    assert entry["default"] is False
    assert entry["lifecycle"] == "planned"
    assert "state_machine.py" in " ".join(entry["consumers"])
