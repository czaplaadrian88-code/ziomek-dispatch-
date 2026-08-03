"""A-2 rework: corruption-safe route-plan persistence.

Oracles correspond directly to blind findings:
  D-1: missing main + healthy .prev must preserve both fleet and backup bytes;
  D-2: recovered versions enter a newer epoch and stale CAS tokens never revive;
  D-3: one shared owner implements .prev backup-on-write + strict JSON reads.

The multiprocess oracle launches two real Python writers. Each child arms a
fail-closed Telegram/notify/logging harness before importing state modules.
"""
from __future__ import annotations

import ast
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

# Worktree nie zawiera żywego flags.json. Pinujemy scratch zanim autouse fixture
# zaimportuje telegram_approver. To dzieje się przed pierwszym importem modułu
# stanu; mocniejszy harness procesów potomnych jest w a2_plan_mp_worker.py.
from dispatch_v2 import common as C

_IMPORT_SCRATCH = Path(tempfile.mkdtemp(prefix="a2_import_safety_"))
_IMPORT_FLAGS = _IMPORT_SCRATCH / "flags.json"
_IMPORT_FLAGS.write_text("{}\n", encoding="utf-8")
C.FLAGS_PATH = _IMPORT_FLAGS
C._flags_cache = None
C._flags_mtime = 0

from dispatch_v2 import telegram_utils as _telegram_utils

_telegram_utils.send_admin_alert = lambda *_args, **_kwargs: True

from dispatch_v2 import plan_manager as PM

try:
    from dispatch_v2 import state_persistence as SP
except ImportError:  # RED collection against rejected candidate (D-3)
    SP = None


def _body(tag: str = "base") -> dict:
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": tag},
        "start_ts": "2026-08-03T12:00:00+00:00",
        "stops": [{
            "order_id": tag,
            "type": "dropoff",
            "coords": {"lat": 53.14, "lng": 23.16},
            "dwell_min": 1.0,
            "status_at_plan_time": "assigned",
        }],
        "optimization_method": "incremental",
    }


def _prev_path() -> Path:
    return Path(str(PM.PLANS_FILE) + ".prev")


def _hwm_path() -> Path:
    return Path(str(PM.PLANS_FILE) + ".version_hwm")


def _clear_cache() -> None:
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    _clear_cache()
    return tmp_path


def _flag_on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", True, raising=False)


def _flag_off(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_PLAN_CORRUPT_RAISE", False, raising=False)


def _corrupt_main(store: Path) -> None:
    (store / "courier_plans.json").write_text(
        "{ this is : not json ]", encoding="utf-8"
    )
    _clear_cache()


def _raw_plan(tag: str, version: int | str) -> dict:
    value = _body(tag)
    value.update({
        "plan_version": version,
        "created_at": "2026-08-03T12:00:00+00:00",
        "last_modified_at": "2026-08-03T12:00:00+00:00",
        "invalidated_at": None,
        "invalidation_reason": None,
    })
    return value


# D-1 negative oracle: the rejected candidate returns {} when main is missing,
# then overwrites both fleet and .prev with a one-courier document.
def test_d1_missing_main_first_write_preserves_fleet_and_prev_bytes(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    previous = {
        "7": _raw_plan("fleet-7", 7),
        "9": _raw_plan("fleet-9", 11),
    }
    _prev_path().write_text(
        json.dumps(previous, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    prev_before = _prev_path().read_bytes()
    assert not PM.PLANS_FILE.exists()

    PM.save_plan("9", _body("after-loss"))

    healed = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    assert set(healed) == {"7", "9"}
    assert healed["7"]["start_pos"]["source"] == "fleet-7"
    assert healed["9"]["start_pos"]["source"] == "after-loss"
    assert _prev_path().read_bytes() == prev_before


def test_backup_on_write_is_predecessor_not_postwrite_clone(store, monkeypatch):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("v1"))
    first_main = PM.PLANS_FILE.read_bytes()
    assert not _prev_path().exists()

    second = PM.save_plan(
        "9", _body("v2"), expected_version=first["plan_version"]
    )
    assert second["plan_version"] > first["plan_version"]
    assert _prev_path().read_bytes() == first_main
    assert _prev_path().read_bytes() != PM.PLANS_FILE.read_bytes()


def test_predecessor_directory_fsync_precedes_main_rename(store, monkeypatch):
    """A durable main may advance only after the predecessor entry is durable."""
    assert SP is not None, "canonical owner missing"
    PM.PLANS_FILE.write_text('{"generation":1}', encoding="utf-8")
    events = []
    real_replace = SP.os.replace
    real_fsync_parent = SP.fsync_parent

    def record_replace(source, destination):
        destination = Path(destination)
        if destination in {_prev_path(), PM.PLANS_FILE}:
            events.append(("rename", destination.name))
        return real_replace(source, destination)

    def record_predecessor_fsync(path):
        events.append(("dir_fsync", Path(path).name))
        real_fsync_parent(path)

    def record_main_fsync(path):
        events.append(("dir_fsync", Path(path).name))
        real_fsync_parent(path)

    monkeypatch.setattr(SP.os, "replace", record_replace)
    monkeypatch.setattr(SP, "fsync_parent", record_predecessor_fsync)
    SP.atomic_write_json(
        PM.PLANS_FILE,
        {"generation": 2},
        ensure_directory_durable=record_main_fsync,
    )

    assert events == [
        ("rename", _prev_path().name),
        ("dir_fsync", _prev_path().name),
        ("rename", PM.PLANS_FILE.name),
        ("dir_fsync", PM.PLANS_FILE.name),
    ]


def test_main_and_predecessor_temps_are_fsynced_before_first_rename(
    store, monkeypatch
):
    """Both temp payloads must be durable before predecessor becomes visible."""
    assert SP is not None, "canonical owner missing"
    PM.PLANS_FILE.write_text('{"generation":1}', encoding="utf-8")
    events = []
    real_fsync = SP.os.fsync
    real_replace = SP.os.replace

    def record_fsync(descriptor):
        events.append(("fsync", descriptor))
        return real_fsync(descriptor)

    def record_replace(source, destination):
        events.append(("rename", Path(destination).name))
        return real_replace(source, destination)

    monkeypatch.setattr(SP.os, "fsync", record_fsync)
    monkeypatch.setattr(SP.os, "replace", record_replace)
    SP.atomic_write_json(PM.PLANS_FILE, {"generation": 2})

    first_rename = next(
        index for index, event in enumerate(events) if event[0] == "rename"
    )
    assert events[first_rename] == ("rename", _prev_path().name)
    assert sum(event[0] == "fsync" for event in events[:first_rename]) == 2


def test_backup_failure_aborts_main_and_retry_skips_reserved_gap(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    assert SP is not None, "canonical owner missing"
    first = PM.save_plan("9", _body("v1"))
    main_before = PM.PLANS_FILE.read_bytes()
    real_replace = SP.os.replace

    def fail_predecessor_replace(source, destination):
        if Path(destination) == _prev_path():
            raise OSError("synthetic predecessor rename failure")
        return real_replace(source, destination)

    monkeypatch.setattr(SP.os, "replace", fail_predecessor_replace)
    with pytest.raises(OSError, match="predecessor rename"):
        PM.save_plan(
            "9", _body("must-not-land"),
            expected_version=int(first["plan_version"]),
        )
    assert PM.PLANS_FILE.read_bytes() == main_before
    assert not _prev_path().exists()
    reserved_after_failure = json.loads(
        _hwm_path().read_text(encoding="utf-8")
    )["last_issued"]

    monkeypatch.setattr(SP.os, "replace", real_replace)
    retry = PM.save_plan(
        "9", _body("retry"), expected_version=int(first["plan_version"])
    )
    assert int(retry["plan_version"]) > int(reserved_after_failure)
    assert _prev_path().read_bytes() == main_before


# D-2: lost main had a newer token than .prev. Recovery must never expose the
# old token again, and an old CAS token must not be accepted (ABA).
def test_d2_recovery_rebases_version_above_lost_main_and_rejects_stale_cas(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("v1"))
    first_main = PM.PLANS_FILE.read_bytes()
    second = PM.save_plan(
        "9", _body("v2"), expected_version=first["plan_version"]
    )
    lost_version = int(second["plan_version"])

    # Exact crash-window fixture: predecessor is v1 while the lost main was v2.
    _prev_path().write_bytes(first_main)
    _corrupt_main(store)
    recovered = PM.load_plan("9")
    assert recovered is not None
    assert int(recovered["plan_version"]) > lost_version

    with pytest.raises(PM.ConcurrencyError):
        PM.save_plan(
            "9", _body("stale-writer"),
            expected_version=int(first["plan_version"]),
        )
    assert PM.load_plan("9")["start_pos"]["source"] == "v1"


def test_d2_hwm_is_durable_before_main_and_crash_gap_is_never_reused(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    assert SP is not None, "canonical owner missing"
    real_atomic = SP.atomic_write_json

    def fail_plan_main(path, data, **kwargs):
        if Path(path) == PM.PLANS_FILE:
            raise OSError("synthetic main crash after HWM")
        return real_atomic(path, data, **kwargs)

    monkeypatch.setattr(SP, "atomic_write_json", fail_plan_main)
    with pytest.raises(OSError, match="main crash after HWM"):
        PM.save_plan("9", _body("crashed"))
    assert not PM.PLANS_FILE.exists()
    reserved = int(json.loads(
        _hwm_path().read_text(encoding="utf-8")
    )["last_issued"])

    monkeypatch.setattr(SP, "atomic_write_json", real_atomic)
    retry = PM.save_plan("9", _body("retry"))
    assert int(retry["plan_version"]) > reserved
    assert int(retry["plan_version"]) <= (1 << 53) - 1


def test_d2_epoch_main_without_hwm_reconciles_before_next_token(
    store, monkeypatch
):
    """Iter4 re-flip contract: adopt healthy main durably, then advance."""
    _flag_on(monkeypatch)
    saved = PM.save_plan("9", _body("epoch"))
    issued = int(saved["plan_version"])
    _hwm_path().unlink()
    _clear_cache()

    assert PM.load_plans()["9"]["plan_version"] == issued
    reconciled = int(json.loads(
        _hwm_path().read_text(encoding="utf-8")
    )["last_issued"])
    assert reconciled == issued
    advanced = PM.save_plan("9", _body("after-reflip"), expected_version=issued)
    assert int(advanced["plan_version"]) > reconciled


def test_d2_warm_cache_cannot_hide_or_skip_missing_hwm_reconciliation(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    saved = PM.save_plan("9", _body("epoch"))
    _clear_cache()
    assert PM._read_raw_shared()["9"]["plan_version"] == saved["plan_version"]
    assert PM._perf_plans_cache["data"] is not None
    key_before = PM._perf_plans_cache["key"]

    _hwm_path().unlink()

    assert PM._read_raw_shared()["9"]["plan_version"] == saved["plan_version"]
    assert int(json.loads(
        _hwm_path().read_text(encoding="utf-8")
    )["last_issued"]) == int(saved["plan_version"])
    assert PM._perf_plans_cache["key"] != key_before


def test_d1_recovery_heals_main_and_later_prev_change_cannot_override_it(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("previous-a"))
    PM.save_plan(
        "9", _body("lost-main"), expected_version=first["plan_version"]
    )
    _corrupt_main(store)

    initial = PM._read_raw_shared()
    assert initial["9"]["start_pos"]["source"] == "previous-a"
    assert json.loads(PM.PLANS_FILE.read_text(encoding="utf-8")) == initial

    replacement = {"9": _raw_plan("previous-b", int(first["plan_version"]))}
    replacement_tmp = store / "replacement-prev.json"
    replacement_tmp.write_text(json.dumps(replacement), encoding="utf-8")
    os.replace(replacement_tmp, _prev_path())

    refreshed = PM._read_raw_shared()
    assert refreshed["9"]["start_pos"]["source"] == "previous-a"


def test_recovered_snapshot_is_recorded_only_with_covering_hwm(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("v1"))
    first_main = PM.PLANS_FILE.read_bytes()
    PM.save_plan(
        "9", _body("v2"), expected_version=first["plan_version"]
    )
    _prev_path().write_bytes(first_main)
    _corrupt_main(store)

    snapshot, hwm = PM.snapshot_for_recording({"9"})
    assert snapshot["9"]["start_pos"]["source"] == "v1"
    assert hwm is not None
    assert int(hwm["last_issued"]) >= int(snapshot["9"]["plan_version"])
    assert json.loads(PM.PLANS_FILE.read_text(encoding="utf-8")) == snapshot


def test_flag_off_after_epoch_is_legacy_and_does_not_touch_sidecars(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("on"))
    first_hwm = int(json.loads(
        _hwm_path().read_text(encoding="utf-8")
    )["last_issued"])
    assert not _prev_path().exists()

    _flag_off(monkeypatch)
    second = PM.save_plan(
        "9", _body("off"), expected_version=int(first["plan_version"])
    )
    second_hwm = int(json.loads(
        _hwm_path().read_text(encoding="utf-8")
    )["last_issued"])
    assert int(second["plan_version"]) == int(first["plan_version"]) + 1
    assert second_hwm == first_hwm
    assert not _prev_path().exists()


def _wait_for(path: Path, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timeout waiting for child marker: {path}")
        time.sleep(0.01)


def _spawn_worker(
    *, mode: str, store: Path, scratch: Path, ready: Path, go: Path,
    result: Path, expected: int | None = None,
    issued_marker: Path | None = None,
    tag: str | None = None,
) -> subprocess.Popen:
    worker = Path(__file__).with_name("a2_plan_mp_worker.py")
    command = [
        sys.executable,
        str(worker),
        "--mode", mode,
        "--state-dir", str(store),
        "--scratch", str(scratch),
        "--ready", str(ready),
        "--go", str(go),
        "--result", str(result),
    ]
    if expected is not None:
        command.extend(("--expected", str(expected)))
    if issued_marker is not None:
        command.extend(("--issued-marker", str(issued_marker)))
    if tag is not None:
        command.extend(("--tag", tag))
    env = os.environ.copy()
    env["DISPATCH_UNDER_PYTEST"] = "1"
    env["PYTEST_CURRENT_TEST"] = "a2_multiprocess_parent"
    env.pop("ALLOW_TELEGRAM_IN_TEST", None)
    env.pop("ALLOW_FILE_LOG_IN_TEST", None)
    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def test_d2_two_process_writers_recovery_between_never_reuses_cas_token(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("v1"))
    first_main = PM.PLANS_FILE.read_bytes()
    second = PM.save_plan(
        "9", _body("v2"), expected_version=first["plan_version"]
    )
    lost_version = int(second["plan_version"])
    stale_token = int(first["plan_version"])
    _prev_path().write_bytes(first_main)
    _corrupt_main(store)

    stale_ready, stale_go = store / "stale.ready", store / "stale.go"
    recover_ready, recover_go = store / "recover.ready", store / "recover.go"
    stale_result, recover_result = store / "stale.json", store / "recover.json"
    stale = _spawn_worker(
        mode="stale", store=store, scratch=store / "scratch-stale",
        ready=stale_ready, go=stale_go, result=stale_result,
        expected=stale_token,
    )
    recovery = _spawn_worker(
        mode="recovery", store=store, scratch=store / "scratch-recovery",
        ready=recover_ready, go=recover_go, result=recover_result,
    )
    try:
        _wait_for(stale_ready)
        _wait_for(recover_ready)
        stale_go.touch()
        stale_out, stale_err = stale.communicate(timeout=20)
        assert stale.returncode == 0, (stale_out, stale_err)

        recover_go.touch()
        recover_out, recover_err = recovery.communicate(timeout=20)
        assert recovery.returncode == 0, (recover_out, recover_err)
    finally:
        for process in (stale, recovery):
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    stale_value = json.loads(stale_result.read_text(encoding="utf-8"))
    recover_value = json.loads(recover_result.read_text(encoding="utf-8"))
    assert stale_value["status"] == "conflict"
    assert int(recover_value["expected"]) > lost_version
    assert recover_value["status"] == "saved"
    assert int(recover_value["saved_version"]) > int(recover_value["expected"])

    final = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))["9"]
    assert final["start_pos"]["source"] == "recovery"
    assert int(final["plan_version"]) == int(recover_value["saved_version"])

    # Mechanical safety ratchet: worker must arm transports/log before state import.
    worker_source = Path(__file__).with_name("a2_plan_mp_worker.py").read_text(
        encoding="utf-8"
    )
    assert worker_source.index("_arm_safety(scratch)") < worker_source.index(
        "from dispatch_v2 import plan_manager as PM"
    )
    assert worker_source.index("PYTHONPYCACHEPREFIX") < worker_source.index(
        "from dispatch_v2 import common"
    )
    assert worker_source.index(
        "_pin_plan_store(PM, SP, state_dir)"
    ) < worker_source.index('if args.mode == "recovery_pause_after_commit"')
    assert "fail-closed selftest did not block live path" in worker_source


def test_d2_two_recovery_writers_real_multiprocess_race_has_one_winner(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("v1"))
    first_main = PM.PLANS_FILE.read_bytes()
    second = PM.save_plan(
        "9", _body("v2"), expected_version=first["plan_version"]
    )
    lost_version = int(second["plan_version"])
    _prev_path().write_bytes(first_main)
    _corrupt_main(store)

    race_go = store / "race.go"
    workers = []
    for label in ("a", "b"):
        process = _spawn_worker(
            mode="recovery",
            store=store,
            scratch=store / f"scratch-race-{label}",
            ready=store / f"race-{label}.ready",
            go=race_go,
            result=store / f"race-{label}.json",
        )
        workers.append((label, process))

    try:
        for label, _process in workers:
            _wait_for(store / f"race-{label}.ready")
        # One marker releases both independent processes against the same
        # fcntl lock and the same recovered CAS token.
        race_go.touch()
        for label, process in workers:
            stdout, stderr = process.communicate(timeout=20)
            assert process.returncode == 0, (label, stdout, stderr)
    finally:
        for _label, process in workers:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=5)

    results = [
        json.loads((store / f"race-{label}.json").read_text(encoding="utf-8"))
        for label, _process in workers
    ]
    assert sorted(value["status"] for value in results) == ["conflict", "saved"]
    winner = next(value for value in results if value["status"] == "saved")
    loser = next(value for value in results if value["status"] == "conflict")
    assert int(winner["expected"]) == int(loser["expected"])
    assert int(winner["expected"]) > lost_version
    assert int(winner["saved_version"]) > int(winner["expected"])
    assert int(loser["current_version"]) == int(winner["saved_version"])
    predecessor = json.loads(_prev_path().read_text(encoding="utf-8"))["9"]
    assert predecessor["start_pos"]["source"] == "v1"
    assert int(predecessor["plan_version"]) == int(winner["expected"])

    final = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))["9"]
    assert int(final["plan_version"]) == int(winner["saved_version"])


def test_iter3_emfile_kill9_with_waiting_writer_never_accepts_old_token(
    store, monkeypatch
):
    """Real processes: EMFILE -> recovery commit -> SIGKILL -> waiting writer."""
    _flag_on(monkeypatch)
    first = PM.save_plan("9", _body("v1"))
    first_main = PM.PLANS_FILE.read_bytes()
    second = PM.save_plan(
        "9", _body("lost-v2"), expected_version=first["plan_version"]
    )
    lost_version = int(second["plan_version"])
    old_token = int(first["plan_version"])
    _prev_path().write_bytes(first_main)
    _corrupt_main(store)

    crash_ready = store / "crash.ready"
    crash_escape = store / "crash.escape"
    issued_marker = store / "crash.issued.json"
    crash = _spawn_worker(
        mode="recovery_pause_after_commit",
        store=store,
        scratch=store / "scratch-crash",
        ready=crash_ready,
        go=crash_escape,
        result=store / "crash.unused.json",
        issued_marker=issued_marker,
    )
    legal = None
    try:
        _wait_for(issued_marker)
        _wait_for(crash_ready)
        issued = json.loads(issued_marker.read_text(encoding="utf-8"))
        recovery_token = int(issued["token"])
        assert issued["emfile_count"] == 1
        assert int(issued["hwm"]) >= recovery_token > lost_version

        committed_before_kill = json.loads(
            PM.PLANS_FILE.read_text(encoding="utf-8")
        )["9"]
        assert int(committed_before_kill["plan_version"]) == recovery_token
        assert committed_before_kill["start_pos"]["source"] == "v1"

        legal_ready = store / "legal.ready"
        legal_go = store / "legal.go"
        legal_result = store / "legal.json"
        legal = _spawn_worker(
            mode="stale",
            store=store,
            scratch=store / "scratch-legal",
            ready=legal_ready,
            go=legal_go,
            result=legal_result,
            expected=recovery_token,
            tag="LEGAL-AFTER-KILL",
        )
        _wait_for(legal_ready)
        legal_go.touch()
        time.sleep(0.10)
        assert legal.poll() is None, "writer should be waiting on recovery EX lock"

        crash.kill()  # subprocess.kill() is SIGKILL on POSIX
        crash_stdout, crash_stderr = crash.communicate(timeout=10)
        assert crash.returncode == -signal.SIGKILL, (
            crash_stdout, crash_stderr
        )

        legal_stdout, legal_stderr = legal.communicate(timeout=20)
        assert legal.returncode == 0, (legal_stdout, legal_stderr)
        legal_value = json.loads(legal_result.read_text(encoding="utf-8"))
        assert legal_value["status"] == "saved"
        assert int(legal_value["saved_version"]) > recovery_token

        for label, stale_token in (
            ("synthetic", recovery_token),
            ("pre-recovery", old_token),
        ):
            ready = store / f"{label}.ready"
            go = store / f"{label}.go"
            result = store / f"{label}.json"
            stale = _spawn_worker(
                mode="stale",
                store=store,
                scratch=store / f"scratch-{label}",
                ready=ready,
                go=go,
                result=result,
                expected=stale_token,
                tag=f"STALE-{label}",
            )
            try:
                _wait_for(ready)
                go.touch()
                stdout, stderr = stale.communicate(timeout=20)
                assert stale.returncode == 0, (label, stdout, stderr)
            finally:
                if stale.poll() is None:
                    stale.kill()
                    stale.communicate(timeout=5)
            stale_value = json.loads(result.read_text(encoding="utf-8"))
            assert stale_value["status"] == "conflict", (label, stale_value)

        final = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))["9"]
        assert final["start_pos"]["source"] == "LEGAL-AFTER-KILL"
        assert int(final["plan_version"]) == int(legal_value["saved_version"])
        assert _hwm_path().exists()
        assert int(json.loads(_hwm_path().read_text(
            encoding="utf-8"
        ))["last_issued"]) >= int(final["plan_version"])
    finally:
        for process in (crash, legal):
            if process is not None and process.poll() is None:
                process.kill()
                process.communicate(timeout=5)


def test_strict_caller_raises_before_recovery(store, monkeypatch):
    _flag_on(monkeypatch)
    _prev_path().write_text(
        json.dumps({"9": _raw_plan("healthy-prev", 1)}), encoding="utf-8"
    )
    _corrupt_main(store)
    with pytest.raises((json.JSONDecodeError, ValueError)):
        PM.load_plans(_raise_on_corrupt=True)
    with pytest.raises((json.JSONDecodeError, ValueError)):
        PM.load_plan("9", _raise_on_corrupt=True)


def test_strict_caller_missing_main_does_not_recover_previous(
    store, monkeypatch
):
    _flag_on(monkeypatch)
    _prev_path().write_text(
        json.dumps({"9": _raw_plan("healthy-prev", 1)}), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError):
        PM.load_plans(_raise_on_corrupt=True)


def test_flag_off_preserves_legacy_without_prev_or_version_hwm(store, monkeypatch):
    _flag_off(monkeypatch)
    saved = PM.save_plan("9", _body("legacy"))
    assert saved["plan_version"] == 1
    assert not _prev_path().exists()
    assert not _hwm_path().exists()
    _corrupt_main(store)
    assert PM.load_plans() == {}


def test_flag_off_preserves_legacy_unicode_failure(store, monkeypatch):
    _flag_off(monkeypatch)
    PM.PLANS_FILE.write_bytes(b"\xff\xfe\x00")
    _clear_cache()
    with pytest.raises(UnicodeDecodeError):
        PM.load_plans()


def test_flag_off_does_not_validate_unrelated_legacy_version(store, monkeypatch):
    _flag_off(monkeypatch)
    plans = {
        "7": _raw_plan("legacy-other", "historical-weird-version"),
        "9": _raw_plan("target", 1),
    }
    PM.PLANS_FILE.write_text(json.dumps(plans), encoding="utf-8")

    saved = PM.save_plan("9", _body("updated"), expected_version=1)

    assert saved["plan_version"] == 2
    persisted = json.loads(PM.PLANS_FILE.read_text(encoding="utf-8"))
    assert persisted["7"]["plan_version"] == "historical-weird-version"
    assert not _prev_path().exists()
    assert not _hwm_path().exists()


def test_flag_off_writer_adds_no_backup_validation_read(store, monkeypatch):
    _flag_off(monkeypatch)
    PM.PLANS_FILE.write_text(
        json.dumps({"9": _raw_plan("legacy", 1)}), encoding="utf-8"
    )
    real_read_bytes = Path.read_bytes

    def forbidden_owner_read(path):
        if path == PM.PLANS_FILE:
            raise AssertionError("A-2 OFF writer must not inspect replaced main")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", forbidden_owner_read)
    saved = PM.save_plan("9", _body("updated"), expected_version=1)

    assert saved["plan_version"] == 2
    assert not _prev_path().exists()
    assert not _hwm_path().exists()


def test_flag_off_record_snapshot_keeps_legacy_lockless_path(store, monkeypatch):
    _flag_off(monkeypatch)
    plans = {"9": _raw_plan("legacy", 4)}
    PM.PLANS_FILE.write_text(json.dumps(plans), encoding="utf-8")

    @contextmanager
    def forbidden_lock(*_args, **_kwargs):
        raise AssertionError("A-2 OFF world-record must not acquire plan lock")
        yield  # pragma: no cover - makes this an explicit context manager

    monkeypatch.setattr(PM, "_locked", forbidden_lock)
    snapshot, hwm = PM.snapshot_for_recording({"9"})

    assert snapshot == plans
    assert snapshot is not plans
    assert hwm is None


# D-3 source ratchet: rejected code defined two divergent owners. The new owner
# is a leaf module; consumers may wrap domain errors, but not persistence logic.
def test_d3_single_canonical_state_persistence_owner():
    root = Path(PM.__file__).parent
    owner = root / "state_persistence.py"
    assert owner.exists(), "missing canonical state persistence owner"

    owner_source = owner.read_text(encoding="utf-8")
    assert "def previous_path(" in owner_source
    assert "def read_json_object(" in owner_source
    assert "def atomic_write_json(" in owner_source

    forbidden_defs = {
        "_prev_path", "_read_prev", "_snapshot_prev", "_backup_prev",
        "_atomic_write",
    }
    for name in ("plan_manager.py", "state_machine.py"):
        source = (root / name).read_text(encoding="utf-8")
        tree = ast.parse(source)
        defs = {
            node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert not defs.intersection(forbidden_defs), (name, defs & forbidden_defs)
        assert "state_persistence" in source
        direct_prev_literals = {
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.endswith(".prev")
        }
        assert not direct_prev_literals, (name, direct_prev_literals)

    for name in (
        "tools/address_pin_aggregator.py",
        "tools/czasowka_uwagi_oracle.py",
        "tools/rebuild_state_from_events.py",
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert "previous_path" in source, name

    replay_source = (root / "tools/world_replay.py").read_text(encoding="utf-8")
    assert "state_persistence as _state_store" in replay_source
    assert replay_source.count("_state_store.atomic_write_json(") >= 2
    assert '_redirect(_pm, "PLANS_FILE"' not in replay_source
    assert "open(hwm_path" not in replay_source


def test_version_writers_use_single_allocator_ratchet():
    source = Path(PM.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    plan_value_assignments = []
    allocator_assignments = []
    for function in (
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    ):
        for node in ast.walk(function):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "plan_version"
                ):
                    plan_value_assignments.append(
                        (function.name, ast.unparse(node.value))
                    )
                if isinstance(target, ast.Name) and target.id in {
                    "new_version", "new_ver"
                }:
                    allocator_assignments.append(
                        (function.name, ast.unparse(node.value))
                    )
    assert plan_value_assignments
    assert all(
        value in {"next_value", "new_version", "new_ver"}
        for _, value in plan_value_assignments
    ), plan_value_assignments
    assert allocator_assignments
    assert all(
        value.startswith("_next_plan_version(")
        for _, value in allocator_assignments
    ), allocator_assignments
