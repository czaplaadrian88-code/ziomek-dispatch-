"""A-2 iteration 4/5 negative oracles for the iter3/iter4 blind findings.

The root/child conftests isolate flags, state, logs and notifications before
this module is imported.  Every plan test additionally pins the module-level
plan paths and all derived sidecars to ``tmp_path`` and asserts that boundary
before its first write.
"""
from __future__ import annotations

import ast
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
    return int(_hwm_payload()["last_issued"])


def _hwm_payload() -> dict:
    return json.loads(PM.version_hwm_path().read_text(encoding="utf-8"))


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


def _prepare_uncovered_off_window(monkeypatch) -> tuple[int, bytes, bytes]:
    """Build the blind's exact stale-CAS overlap while HWM stays frozen."""
    _set_plan_guard(monkeypatch, True)
    PM.save_plan("c1", _plan_body("on-c1"))
    current = PM.save_plan("c2", _plan_body("on-c2"))
    PM.save_plan("c3", _plan_body("on-c3"))
    hwm_before_off = _hwm_value()

    _set_plan_guard(monkeypatch, False)
    # .prev contains c1+c2. With the old frozen-HWM recovery, sorted rebasing
    # gives c2 exactly the token issued by the third OFF write below.
    for index in range(3):
        current = PM.save_plan(
            "c2",
            _plan_body(f"off-window-{index}"),
            expected_version=int(current["plan_version"]),
        )
    stale_off_token = int(current["plan_version"])
    assert stale_off_token > hwm_before_off
    assert _hwm_value() == hwm_before_off
    return (
        stale_off_token,
        SP.previous_path(PM.PLANS_FILE).read_bytes(),
        PM.version_hwm_path().read_bytes(),
    )


@pytest.mark.parametrize("lost_main", ["corrupt", "missing"])
def test_off_window_recovery_without_continuity_fails_closed(
    plan_store, monkeypatch, lost_main
):
    """N-1: a frozen HWM can never authorize recovery after an OFF window."""
    stale_off_token, prev_before, hwm_after_off = (
        _prepare_uncovered_off_window(monkeypatch)
    )
    if lost_main == "corrupt":
        PM.PLANS_FILE.write_text("{ corrupt after OFF window", encoding="utf-8")
        main_after_loss = PM.PLANS_FILE.read_bytes()
    else:
        PM.PLANS_FILE.unlink()
        main_after_loss = None

    _set_plan_guard(monkeypatch, True)
    # A mutating public entrypoint must fail at the same boundary. In
    # particular the stale CAS token issued in the OFF window is never replayed.
    with pytest.raises(PM.PlanVersionStateError, match="continuity"):
        PM.save_plan(
            "c2",
            _plan_body("stale-off-cas-replay"),
            expected_version=stale_off_token,
        )
    with pytest.raises(PM.PlanVersionStateError, match="continuity"):
        PM.load_plans()

    assert SP.previous_path(PM.PLANS_FILE).read_bytes() == prev_before
    assert PM.version_hwm_path().read_bytes() == hwm_after_off
    if main_after_loss is None:
        assert not PM.PLANS_FILE.exists()
    else:
        assert PM.PLANS_FILE.read_bytes() == main_after_loss


def test_mutation_removed_off_invalidation_accepts_stale_cas(
    plan_store, monkeypatch
):
    """Mutation proof: deleting the one OFF invalidation reopens N-1/ABA."""
    monkeypatch.setattr(
        PM, "_invalidate_version_hwm_continuity", lambda: None
    )
    stale_off_token, _prev_before, _hwm_after_off = (
        _prepare_uncovered_off_window(monkeypatch)
    )
    PM.PLANS_FILE.write_text("{ mutant corrupt main", encoding="utf-8")

    _set_plan_guard(monkeypatch, True)
    recovered = PM.load_plans()
    assert int(recovered["c2"]["plan_version"]) == stale_off_token
    accepted = PM.save_plan(
        "c2",
        _plan_body("mutant-stale-cas-accepted"),
        expected_version=stale_off_token,
    )
    assert int(accepted["plan_version"]) > stale_off_token


def test_first_off_write_invalidates_continuity_before_legacy_main_once(
    plan_store, monkeypatch
):
    """Ratchet: one sidecar invalidation, ordered before byte-legacy main."""
    _set_plan_guard(monkeypatch, True)
    current = PM.save_plan("c1", _plan_body("on"))
    hwm_path = PM.version_hwm_path()
    hwm_before = _hwm_payload()
    assert hwm_before["covers_all_issued"] is True
    hwm_inode_before = hwm_path.stat().st_ino
    main_before = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))

    _set_plan_guard(monkeypatch, False)
    fixed_now = "2026-08-03T22:30:00+00:00"
    monkeypatch.setattr(PM, "_now_iso", lambda: fixed_now)
    destinations: list[Path] = []
    real_replace = SP.os.replace

    def record_replace(source, destination):
        destination = Path(destination)
        if destination in {hwm_path, PM.PLANS_FILE}:
            destinations.append(destination)
        return real_replace(source, destination)

    monkeypatch.setattr(SP.os, "replace", record_replace)
    first_off = PM.save_plan(
        "c1", _plan_body("off-first"),
        expected_version=int(current["plan_version"]),
    )
    expected = dict(main_before)
    expected["c1"] = first_off
    assert PM.PLANS_FILE.read_bytes() == json.dumps(
        expected, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    assert destinations[:2] == [hwm_path, PM.PLANS_FILE]
    invalidated = _hwm_payload()
    assert invalidated["last_issued"] == hwm_before["last_issued"]
    assert invalidated["covers_all_issued"] is False
    assert hwm_path.stat().st_ino != hwm_inode_before

    invalidated_inode = hwm_path.stat().st_ino
    destinations.clear()
    PM.save_plan(
        "c1", _plan_body("off-second"),
        expected_version=int(first_off["plan_version"]),
    )
    assert destinations == [PM.PLANS_FILE]
    assert hwm_path.stat().st_ino == invalidated_inode


def test_off_invalidation_failure_aborts_before_legacy_main(
    plan_store, monkeypatch
):
    """Fail-closed ordering: no OFF token escapes without invalidation."""
    _set_plan_guard(monkeypatch, True)
    current = PM.save_plan("c1", _plan_body("on"))
    main_before = PM.PLANS_FILE.read_bytes()
    hwm_before = PM.version_hwm_path().read_bytes()
    real_atomic_write = SP.atomic_write_json

    def fail_hwm(path, data, **kwargs):
        if Path(path) == PM.version_hwm_path():
            raise OSError(errno.EIO, "synthetic HWM invalidation failure")
        return real_atomic_write(path, data, **kwargs)

    _set_plan_guard(monkeypatch, False)
    monkeypatch.setattr(SP, "atomic_write_json", fail_hwm)
    with pytest.raises(OSError, match="HWM invalidation failure"):
        PM.save_plan(
            "c1",
            _plan_body("must-not-land"),
            expected_version=int(current["plan_version"]),
        )
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert PM.version_hwm_path().read_bytes() == hwm_before


def test_continuity_marker_has_one_invalidator_and_one_write_choke_point():
    """Source ratchet: no competing OFF continuity writer or bypass."""
    tree = ast.parse(Path(PM.__file__).read_text(encoding="utf-8"))
    invalid_marker_writers = []
    invalidation_callers = []
    for function in (
        node for node in tree.body if isinstance(node, ast.FunctionDef)
    ):
        for call in (
            node for node in ast.walk(function) if isinstance(node, ast.Call)
        ):
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == "_write_version_hwm"
                and any(
                    keyword.arg == "covers_all_issued"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is False
                    for keyword in call.keywords
                )
            ):
                invalid_marker_writers.append(function.name)
            if (
                isinstance(call.func, ast.Name)
                and call.func.id == "_invalidate_version_hwm_continuity"
            ):
                invalidation_callers.append(function.name)

    assert invalid_marker_writers == ["_invalidate_version_hwm_continuity"]
    assert invalidation_callers == ["_write_raw"]


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


# ─── N-3 (iteration 6): the OFF kill-switch owes nothing to a readable
# sidecar.  Content that was read and rejected can never be a continuity
# proof, so the OFF writer treats it as already void instead of losing the
# plan write.  Being unable to *learn* or *record* the invalidation is a
# different failure and stays fail-closed.

_UNPARSABLE_SIDECAR_CONTENT = {
    "malformed_json": lambda hwm: "{ not json",
    "wrong_schema": lambda hwm: json.dumps(
        {"schema": "bogus.v9", "last_issued": hwm}
    ),
    "bad_value_type": lambda hwm: json.dumps(
        {"schema": PM._VERSION_HWM_SCHEMA, "last_issued": "not-an-int"}
    ),
    "below_epoch_floor": lambda hwm: json.dumps(
        {"schema": PM._VERSION_HWM_SCHEMA, "last_issued": 12}
    ),
    "bad_marker_type": lambda hwm: json.dumps({
        "schema": PM._VERSION_HWM_SCHEMA,
        "last_issued": hwm,
        "covers_all_issued": "yes",
    }),
}


def _on_era_then_unparsable_sidecar(monkeypatch, corruption: str) -> dict:
    """Leave an ON-era sidecar behind, then damage its bytes before OFF."""
    _set_plan_guard(monkeypatch, True)
    current = PM.save_plan("c1", _plan_body("on-c1"))
    assert _hwm_payload()["covers_all_issued"] is True

    _set_plan_guard(monkeypatch, False)
    PM.version_hwm_path().write_text(
        _UNPARSABLE_SIDECAR_CONTENT[corruption](_hwm_value()),
        encoding="utf-8",
    )
    _clear_plan_cache()
    return current


@pytest.mark.parametrize("corruption", sorted(_UNPARSABLE_SIDECAR_CONTENT))
def test_off_write_survives_unparsable_sidecar(
    plan_store, monkeypatch, corruption
):
    """N-3: a damaged sidecar must not immobilize plan writes while OFF."""
    current = _on_era_then_unparsable_sidecar(monkeypatch, corruption)
    sidecar_before = PM.version_hwm_path().read_bytes()

    saved = PM.save_plan(
        "c1",
        _plan_body("off-after-unparsable-sidecar"),
        expected_version=int(current["plan_version"]),
    )
    assert int(saved["plan_version"]) == int(current["plan_version"]) + 1
    on_disk = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    assert on_disk["c1"]["stops"][0]["order_id"] == (
        "off-after-unparsable-sidecar"
    )
    # Nothing is invented: rewriting an unparsable last_issued could only
    # fabricate or lower the burned HWM, so the damaged bytes are left alone.
    assert PM.version_hwm_path().read_bytes() == sidecar_before
    with pytest.raises((PM.PlanVersionStateError, ValueError)):
        PM._read_version_hwm_state()

    # The proof stays void for every reader, so recovery is still fail-closed.
    PM.PLANS_FILE.write_text("{ lost main after OFF window", encoding="utf-8")
    _clear_plan_cache()
    _set_plan_guard(monkeypatch, True)
    with pytest.raises((PM.PlanVersionStateError, ValueError)):
        PM.load_plans()
    with pytest.raises((PM.PlanVersionStateError, ValueError)):
        PM.save_plan(
            "c1",
            _plan_body("stale-off-cas-replay"),
            expected_version=int(saved["plan_version"]),
        )


def test_off_write_still_aborts_when_sidecar_read_is_an_io_failure(
    plan_store, monkeypatch
):
    """Boundary: unread bytes may still prove continuity, so abort stands."""
    _set_plan_guard(monkeypatch, True)
    current = PM.save_plan("c1", _plan_body("on"))
    main_before = PM.PLANS_FILE.read_bytes()
    hwm_before = PM.version_hwm_path().read_bytes()
    real_read = SP.read_json_object

    def fail_sidecar_read(path, **kwargs):
        if Path(path) == PM.version_hwm_path():
            raise OSError(errno.EACCES, "synthetic sidecar read failure")
        return real_read(path, **kwargs)

    _set_plan_guard(monkeypatch, False)
    monkeypatch.setattr(SP, "read_json_object", fail_sidecar_read)
    with pytest.raises(OSError, match="sidecar read failure"):
        PM.save_plan(
            "c1",
            _plan_body("must-not-land"),
            expected_version=int(current["plan_version"]),
        )
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert PM.version_hwm_path().read_bytes() == hwm_before


def test_mutation_reraising_unparsable_sidecar_loses_the_off_write(
    plan_store, monkeypatch
):
    """Mutation proof: restoring propagation costs the OFF plan write."""
    def pre_fix_invalidate() -> None:
        state = PM._read_version_hwm_state()
        if state is None or state.covers_all_issued is False:
            return
        PM._write_version_hwm(state.last_issued, covers_all_issued=False)

    current = _on_era_then_unparsable_sidecar(monkeypatch, "malformed_json")
    main_before = PM.PLANS_FILE.read_bytes()
    monkeypatch.setattr(
        PM, "_invalidate_version_hwm_continuity", pre_fix_invalidate
    )
    with pytest.raises(ValueError):
        PM.save_plan(
            "c1",
            _plan_body("lost-by-mutant"),
            expected_version=int(current["plan_version"]),
        )
    assert PM.PLANS_FILE.read_bytes() == main_before


def test_off_invalidation_classifies_content_only_never_io():
    """Source ratchet: widening the catch to I/O would re-arm the trap."""
    tree = ast.parse(Path(PM.__file__).read_text(encoding="utf-8"))
    target = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_invalidate_version_hwm_continuity"
    )
    handlers = [
        node for node in ast.walk(target)
        if isinstance(node, ast.ExceptHandler)
    ]
    assert handlers, "the OFF invalidator must classify unreadable sidecars"
    caught: list[str] = []
    for handler in handlers:
        assert handler.type is not None, "a bare except would swallow I/O"
        nodes = (
            handler.type.elts
            if isinstance(handler.type, ast.Tuple)
            else [handler.type]
        )
        for node in nodes:
            assert isinstance(node, ast.Name), "only named content errors"
            caught.append(node.id)
    assert set(caught) == {"PlanVersionStateError", "ValueError"}
