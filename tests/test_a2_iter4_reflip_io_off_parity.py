"""A-2 iteration 4 negative oracles for the iter3 blind findings.

The root/child conftests isolate flags, state, logs and notifications before
this module is imported.  Every plan test additionally pins the module-level
plan paths and all derived sidecars to ``tmp_path`` and asserts that boundary
before its first write.
"""
from __future__ import annotations

import errno
import json
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import state_machine as SM
from dispatch_v2 import state_persistence as SP


_LIVE_STATE = Path("/root/.openclaw/workspace/dispatch_state").resolve()


def _clear_plan_cache() -> None:
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None


def _assert_plan_sandbox(root: Path) -> None:
    allowed = root.resolve()
    paths = (
        Path(PM.PLANS_FILE).resolve(),
        Path(PM.LOCK_FILE).resolve(),
        SP.previous_path(PM.PLANS_FILE).resolve(),
        PM.version_hwm_path().resolve(),
    )
    for path in paths:
        assert path.is_relative_to(allowed), (
            f"fail-closed: plan path escaped test sandbox: {path}"
        )
        assert not path.is_relative_to(_LIVE_STATE), (
            f"fail-closed: plan path targets live state: {path}"
        )


@pytest.fixture
def plan_store(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    _assert_plan_sandbox(tmp_path)

    # A save hook must never import a real notification/logging surface here.
    decision_log = types.ModuleType("dispatch_v2.decision_eta_log")
    decision_log.record_plan_commit = lambda *_args, **_kwargs: True
    monkeypatch.setitem(sys.modules, "dispatch_v2.decision_eta_log", decision_log)
    monkeypatch.setattr(PM, "_perf_lazy_on", lambda: False)
    _clear_plan_cache()
    yield tmp_path
    _clear_plan_cache()


def _set_plan_guard(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(
        C, "ENABLE_PLAN_CORRUPT_RAISE", enabled, raising=False
    )
    _clear_plan_cache()


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


def _hwm_value() -> int:
    payload = json.loads(PM.version_hwm_path().read_text(encoding="utf-8"))
    return int(payload["last_issued"])


def _prepare_reflip(monkeypatch, *, delete_hwm: bool) -> tuple[int, bytes, bytes]:
    _set_plan_guard(monkeypatch, True)
    first = PM.save_plan("c1", _plan_body("on-c1"))
    PM.save_plan("c2", _plan_body("on-c2"))
    hwm_after_on = _hwm_value()

    _set_plan_guard(monkeypatch, False)
    current = first
    for index in range(3):
        current = PM.save_plan(
            "c1",
            _plan_body(f"off-{index}"),
            expected_version=int(current["plan_version"]),
        )
    off_version = int(current["plan_version"])
    assert off_version > hwm_after_on
    main_before_reflip = PM.PLANS_FILE.read_bytes()
    prev_before_reflip = SP.previous_path(PM.PLANS_FILE).read_bytes()
    if delete_hwm:
        PM.version_hwm_path().unlink()
    else:
        assert _hwm_value() == hwm_after_on

    _set_plan_guard(monkeypatch, True)
    return off_version, main_before_reflip, prev_before_reflip


@pytest.mark.parametrize("delete_hwm", [False, True], ids=["keep-hwm", "delete-hwm"])
@pytest.mark.parametrize(
    "entrypoint",
    ["load_plans", "load_plan", "snapshot", "save_existing", "save_new"],
)
def test_on_off_on_reconciles_legal_off_versions_for_every_entrypoint(
    plan_store, monkeypatch, delete_hwm, entrypoint
):
    """F-1: shadow rollout -> rollback -> re-enable is lossless both ways."""
    off_version, main_before, prev_before = _prepare_reflip(
        monkeypatch, delete_hwm=delete_hwm
    )

    if entrypoint == "load_plans":
        assert PM.load_plans()["c1"]["start_pos"]["source"] == "off-2"
    elif entrypoint == "load_plan":
        assert PM.load_plan("c1")["start_pos"]["source"] == "off-2"
    elif entrypoint == "snapshot":
        plans, hwm = PM.snapshot_for_recording()
        assert plans["c1"]["start_pos"]["source"] == "off-2"
        assert hwm is not None and int(hwm["last_issued"]) >= off_version
    elif entrypoint == "save_existing":
        saved = PM.save_plan(
            "c1", _plan_body("re-on-existing"), expected_version=off_version
        )
        assert int(saved["plan_version"]) > off_version
    elif entrypoint == "save_new":
        saved = PM.save_plan("c3", _plan_body("re-on-new"))
        assert int(saved["plan_version"]) > off_version
    else:  # pragma: no cover - closed parametrization
        raise AssertionError(entrypoint)

    disk = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    observed = max(int(plan["plan_version"]) for plan in disk.values())
    assert _hwm_value() >= observed
    assert disk["c2"]["start_pos"]["source"] == "on-c2"
    if entrypoint in {"load_plans", "load_plan", "snapshot"}:
        assert PM.PLANS_FILE.read_bytes() == main_before
        assert SP.previous_path(PM.PLANS_FILE).read_bytes() == prev_before
        assert _hwm_value() == off_version


def test_exhausted_transient_eio_never_recovers_over_healthy_main(
    plan_store, monkeypatch
):
    """F-2: I/O unavailability is not content corruption or recovery input."""
    _set_plan_guard(monkeypatch, True)
    first = PM.save_plan("c1", _plan_body("older-prev"))
    PM.save_plan(
        "c1", _plan_body("newest-only-in-main"),
        expected_version=int(first["plan_version"]),
    )
    main_before = PM.PLANS_FILE.read_bytes()
    prev_before = SP.previous_path(PM.PLANS_FILE).read_bytes()
    hwm_before = PM.version_hwm_path().read_bytes()
    stat_before = PM.PLANS_FILE.stat()
    identity_before = (
        stat_before.st_ino,
        stat_before.st_size,
        stat_before.st_mtime_ns,
        stat_before.st_ctime_ns,
    )

    real_read_value = SP._read_value
    attempts = {"main": 0, "previous": 0}

    def unavailable_main(path, *args, **kwargs):
        if Path(path) == PM.PLANS_FILE:
            attempts["main"] += 1
            raise OSError(errno.EIO, "synthetic EIO beyond retry budget")
        if Path(path) == SP.previous_path(PM.PLANS_FILE):
            attempts["previous"] += 1
        return real_read_value(path, *args, **kwargs)

    monkeypatch.setattr(SP, "_read_value", unavailable_main)
    monkeypatch.setattr(SP.time, "sleep", lambda _delay: None)
    _clear_plan_cache()
    with pytest.raises(OSError) as raised:
        PM.load_plans()
    assert raised.value.errno == errno.EIO
    assert attempts["main"] == 3
    assert attempts["previous"] == 0

    stat_after = PM.PLANS_FILE.stat()
    identity_after = (
        stat_after.st_ino,
        stat_after.st_size,
        stat_after.st_mtime_ns,
        stat_after.st_ctime_ns,
    )
    assert identity_after == identity_before
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert SP.previous_path(PM.PLANS_FILE).read_bytes() == prev_before
    assert PM.version_hwm_path().read_bytes() == hwm_before


def test_reflip_never_reconciles_malformed_hwm(plan_store, monkeypatch):
    """A valid OFF drift is healable; malformed HWM content remains fail-closed."""
    _prepare_reflip(monkeypatch, delete_hwm=False)
    PM.version_hwm_path().write_text(
        json.dumps({"schema": "wrong", "last_issued": 1}),
        encoding="utf-8",
    )
    main_before = PM.PLANS_FILE.read_bytes()
    hwm_before = PM.version_hwm_path().read_bytes()
    _clear_plan_cache()

    with pytest.raises(PM.PlanVersionStateError, match="invalid version HWM schema"):
        PM.load_plans()
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert PM.version_hwm_path().read_bytes() == hwm_before


def test_reflip_hwm_reconciliation_requires_exclusive_reread(
    plan_store, monkeypatch
):
    """A shared reader may detect drift but may never write its sidecar."""
    _prepare_reflip(monkeypatch, delete_hwm=False)
    real_locked = PM._locked
    real_write_hwm = PM._write_version_hwm
    lock_mode = {"exclusive": None}
    sequence: list[bool] = []

    @contextmanager
    def tracked_lock(exclusive: bool):
        with real_locked(exclusive):
            previous = lock_mode["exclusive"]
            lock_mode["exclusive"] = exclusive
            sequence.append(exclusive)
            try:
                yield
            finally:
                lock_mode["exclusive"] = previous

    def write_hwm_only_under_ex(value: int) -> None:
        assert lock_mode["exclusive"] is True
        real_write_hwm(value)

    monkeypatch.setattr(PM, "_locked", tracked_lock)
    monkeypatch.setattr(PM, "_write_version_hwm", write_hwm_only_under_ex)
    assert PM.load_plans()["c1"]["start_pos"]["source"] == "off-2"
    assert sequence[:2] == [False, True]


def test_mutation_old_strict_epoch_validation_reopens_f1(
    plan_store, monkeypatch
):
    """Mutation proof: replacing reconciliation with iter3 validation is RED."""
    off_version, main_before, _prev_before = _prepare_reflip(
        monkeypatch, delete_hwm=False
    )

    def iter3_strict_validation(plans, *, allow_reconcile):
        del allow_reconcile
        observed = PM._max_plan_version(plans)
        hwm = PM._read_version_hwm()
        if hwm is None and observed >= PM._VERSION_EPOCH_FLOOR:
            raise PM.PlanVersionStateError(
                "epoch plan exists but durable version HWM is missing"
            )
        if hwm is not None and observed > hwm:
            raise PM.PlanVersionStateError(
                f"plan version {observed} exceeds durable HWM {hwm}"
            )

    monkeypatch.setattr(
        PM, "_validate_or_reconcile_main_epoch", iter3_strict_validation
    )
    _clear_plan_cache()
    with pytest.raises(PM.PlanVersionStateError):
        PM.load_plans()
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert _hwm_value() < off_version


def test_unreadable_predecessor_is_io_failure_not_recovery(
    plan_store, monkeypatch
):
    """The content/I/O boundary also applies to the predecessor read."""
    _set_plan_guard(monkeypatch, True)
    first = PM.save_plan("c1", _plan_body("healthy-prev"))
    PM.save_plan(
        "c1", _plan_body("lost-main"),
        expected_version=int(first["plan_version"]),
    )
    PM.PLANS_FILE.write_text("{ corrupt main", encoding="utf-8")
    main_before = PM.PLANS_FILE.read_bytes()
    prev_before = SP.previous_path(PM.PLANS_FILE).read_bytes()
    hwm_before = PM.version_hwm_path().read_bytes()
    real_read_value = SP._read_value
    attempts = {"previous": 0}

    def unavailable_previous(path, *args, **kwargs):
        if Path(path) == SP.previous_path(PM.PLANS_FILE):
            attempts["previous"] += 1
            raise OSError(errno.EIO, "synthetic predecessor EIO")
        return real_read_value(path, *args, **kwargs)

    monkeypatch.setattr(SP, "_read_value", unavailable_previous)
    monkeypatch.setattr(SP.time, "sleep", lambda _delay: None)
    _clear_plan_cache()
    with pytest.raises(OSError) as raised:
        PM.load_plans()
    assert raised.value.errno == errno.EIO
    assert attempts["previous"] == 3
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert SP.previous_path(PM.PLANS_FILE).read_bytes() == prev_before
    assert PM.version_hwm_path().read_bytes() == hwm_before


def test_mutation_classifying_io_as_recovery_input_reopens_f2(
    plan_store, monkeypatch
):
    """Mutation proof: the old broad fallback destroys the newest generation."""
    _set_plan_guard(monkeypatch, True)
    first = PM.save_plan("c1", _plan_body("older-prev"))
    PM.save_plan(
        "c1", _plan_body("newest-only-in-main"),
        expected_version=int(first["plan_version"]),
    )
    real_read_value = SP._read_value
    attempts = {"main": 0}

    def unavailable_main(path, *args, **kwargs):
        if Path(path) == PM.PLANS_FILE:
            attempts["main"] += 1
            raise OSError(errno.EIO, "synthetic mutant EIO")
        return real_read_value(path, *args, **kwargs)

    monkeypatch.setattr(SP, "_is_previous_recovery_input", lambda _exc: True)
    monkeypatch.setattr(SP, "_read_value", unavailable_main)
    monkeypatch.setattr(SP.time, "sleep", lambda _delay: None)
    _clear_plan_cache()
    recovered = PM.load_plans()
    disk = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    assert attempts["main"] == 6
    assert recovered["c1"]["start_pos"]["source"] == "older-prev"
    assert disk["c1"]["start_pos"]["source"] == "older-prev"


@pytest.fixture
def orders_store(tmp_path, monkeypatch):
    state_path = tmp_path / "orders_state.json"
    resolved = state_path.resolve()
    assert resolved.is_relative_to(tmp_path.resolve())
    assert not resolved.is_relative_to(_LIVE_STATE)
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", False, raising=False
    )
    alerts: list[str] = []
    monkeypatch.setattr(SM, "_alert_state_read_failure", alerts.append)
    state_path.write_text(
        json.dumps({"1": {"order_id": "1", "status": "NEW"}}),
        encoding="utf-8",
    )
    return state_path, alerts


@pytest.mark.parametrize("operation", ["get_all_strict", "upsert_order"])
def test_orders_state_off_propagates_io_error_without_operator_alert(
    orders_store, monkeypatch, operation
):
    """F-3: merge with the orders persistence flag OFF remains a no-op."""
    state_path, alerts = orders_store
    before = state_path.read_bytes()
    real_read_value = SP._read_value

    def unavailable_state(path, *args, **kwargs):
        if Path(path) == state_path:
            raise OSError(errno.EIO, "synthetic orders-state EIO")
        return real_read_value(path, *args, **kwargs)

    monkeypatch.setattr(SP, "_read_value", unavailable_state)
    if operation == "get_all_strict":
        call = SM.get_all_strict
    else:
        call = lambda: SM.upsert_order(
            "2", {"order_id": "2", "status": "NEW"}
        )
    with pytest.raises(OSError) as raised:
        call()
    assert raised.value.errno == errno.EIO
    assert alerts == []
    assert state_path.read_bytes() == before


def test_orders_state_on_keeps_fail_closed_alert_contract(
    orders_store, monkeypatch
):
    state_path, alerts = orders_store
    monkeypatch.setattr(
        C, "ENABLE_ORDERS_STATE_PERSISTENCE_V2", True, raising=False
    )
    real_read_value = SP._read_value

    def unavailable_state(path, *args, **kwargs):
        if Path(path) == state_path:
            raise OSError(errno.EIO, "synthetic orders-state EIO")
        return real_read_value(path, *args, **kwargs)

    monkeypatch.setattr(SP, "_read_value", unavailable_state)
    monkeypatch.setattr(SP.time, "sleep", lambda _delay: None)
    with pytest.raises(SM.StateReadError):
        SM.get_all_strict()
    assert len(alerts) == 1


def test_mutation_removing_off_io_passthrough_reopens_f3(
    orders_store, monkeypatch
):
    """Mutation proof: deleting the OFF gate re-arms alert + wrapper."""
    state_path, alerts = orders_store
    real_read_value = SP._read_value

    def unavailable_state(path, *args, **kwargs):
        if Path(path) == state_path:
            raise OSError(errno.EIO, "synthetic orders-state EIO")
        return real_read_value(path, *args, **kwargs)

    monkeypatch.setattr(SP, "_read_value", unavailable_state)
    monkeypatch.setattr(
        SM, "_legacy_strict_read_propagates", lambda _exc: False
    )
    with pytest.raises(SM.StateReadError):
        SM.get_all_strict()
    assert len(alerts) == 1


def test_plan_off_strict_list_root_preserves_exact_legacy_exception(
    plan_store, monkeypatch
):
    """F-4 parity ratchet required for the requested 41/41 OFF matrix."""
    _set_plan_guard(monkeypatch, False)
    PM.PLANS_FILE.write_text('["list root"]', encoding="utf-8")
    _clear_plan_cache()
    assert PM.load_plans() == {}
    with pytest.raises(ValueError) as raised:
        PM.load_plans(_raise_on_corrupt=True)
    assert type(raised.value) is ValueError
    assert str(raised.value) == "courier_plans.json is not an object"
