#!/usr/bin/env python3
"""Odizolowany writer dla A-2 race oracle.

WAŻNE: bezpieczniki Telegram/notify i logging są uzbrajane przed importem
``plan_manager``. Ten plik jest uruchamiany jako osobny proces, więc nie polega
na fixture'ach pytest procesu-rodzica.
"""
from __future__ import annotations

import argparse
import errno
import json
import logging
import os
import sys
import time
import types
from pathlib import Path


def _arm_safety(scratch: Path) -> None:
    scratch.mkdir(parents=True, exist_ok=True)
    os.environ["DISPATCH_UNDER_PYTEST"] = "1"
    os.environ["PYTEST_CURRENT_TEST"] = "a2_plan_mp_worker"
    pycache = scratch / "pycache"
    pycache.mkdir(parents=True, exist_ok=True)
    os.environ["PYTHONPYCACHEPREFIX"] = str(pycache)
    sys.pycache_prefix = str(pycache)
    os.environ["DISPATCH_STATE_DIR"] = str(scratch / "dispatch_state-env")
    os.environ["ZIOMEK_LOGS_DIR"] = str(scratch / "logs")
    os.environ.pop("ALLOW_TELEGRAM_IN_TEST", None)
    os.environ.pop("ALLOW_FILE_LOG_IN_TEST", None)

    # Custom repro nie może odziedziczyć produkcyjnych FileHandlerów. Ustawienie
    # force=True następuje ZANIM zaimportujemy jakikolwiek moduł stanu.
    logging.basicConfig(
        filename=str(scratch / "worker.log"),
        level=logging.DEBUG,
        force=True,
    )

    # Worktree nie zawiera żywego flags.json. Pinujemy pusty scratch zanim
    # telegram_approver wykona swoje import-time odczyty flag.
    from dispatch_v2 import common

    flags_path = scratch / "flags.json"
    flags_path.write_text("{}\n", encoding="utf-8")
    common.FLAGS_PATH = flags_path
    common._flags_cache = None
    common._flags_mtime = 0

    # Importujemy wyłącznie warstwę transportu, uzbrajamy wszystkie choke-pointy,
    # dopiero potem wolno zaimportować moduł stanu.
    from dispatch_v2 import notify_router, telegram_approver, telegram_utils

    def _blocked(*_args, **_kwargs):
        return True

    telegram_utils.send_admin_alert = _blocked
    telegram_approver.tg_request = _blocked
    notify_router.route = _blocked
    notify_router.FEED_PATH = scratch / "notify_feed.jsonl"

    # save_plan ma log-only hook ładowany lazy. Stub eliminuje wszystkie
    # zależności obserwacyjne z krytycznej próby persistence.
    decision_log = types.ModuleType("dispatch_v2.decision_eta_log")
    decision_log.record_plan_commit = _blocked
    sys.modules["dispatch_v2.decision_eta_log"] = decision_log


def _pin_plan_store(PM, SP, state_dir: Path) -> None:
    """Pin and guard every plan path before the worker's first state write."""
    state_dir.mkdir(parents=True, exist_ok=True)
    allowed = state_dir.resolve()
    live = Path("/root/.openclaw/workspace/dispatch_state").resolve()
    PM.PLANS_FILE = state_dir / "courier_plans.json"
    PM.LOCK_FILE = state_dir / "courier_plans.lock"
    paths = (
        Path(PM.PLANS_FILE).resolve(),
        Path(PM.LOCK_FILE).resolve(),
        SP.previous_path(PM.PLANS_FILE).resolve(),
        PM.version_hwm_path().resolve(),
    )
    for path in paths:
        if not path.is_relative_to(allowed) or path.is_relative_to(live):
            raise RuntimeError(
                f"A2-WORKER fail-closed: plan path escaped sandbox: {path}"
            )

    def guard(name, original):
        def checked(path, *args, **kwargs):
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(allowed):
                raise RuntimeError(
                    f"A2-WORKER write-guard: {name} targeted {resolved}"
                )
            return original(path, *args, **kwargs)
        return checked

    SP.atomic_write_json = guard("atomic_write_json", SP.atomic_write_json)
    SP.legacy_atomic_write_json = guard(
        "legacy_atomic_write_json", SP.legacy_atomic_write_json
    )
    try:
        SP.atomic_write_json(live / "never-write.json", {})
    except RuntimeError as exc:
        if "write-guard" not in str(exc):
            raise
    else:
        raise RuntimeError("A2-WORKER fail-closed selftest did not block live path")


def _body(tag: str) -> dict:
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


def _atomic_result(path: Path, value: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _wait(path: Path, timeout_s: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not path.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError(f"timeout waiting for {path}")
        time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("stale", "recovery", "recovery_pause_after_commit"),
        required=True,
    )
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--go", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--expected", type=int)
    parser.add_argument("--issued-marker")
    parser.add_argument("--tag")
    args = parser.parse_args()

    scratch = Path(args.scratch)
    _arm_safety(scratch)

    # PIERWSZY import modułu stanu następuje dopiero po pełnym harnessie wyżej.
    from dispatch_v2 import plan_manager as PM
    from dispatch_v2 import state_persistence as SP

    state_dir = Path(args.state_dir)
    _pin_plan_store(PM, SP, state_dir)
    PM._corrupt_guard_on = lambda: True
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None

    if args.mode == "recovery_pause_after_commit":
        if not args.issued_marker:
            raise ValueError("--issued-marker is required for recovery pause")
        real_open = open
        fired = {"count": 0}

        def one_emfile(path, *open_args, **open_kwargs):
            if fired["count"] == 0 and Path(path) == PM.PLANS_FILE:
                fired["count"] += 1
                raise OSError(errno.EMFILE, "synthetic one-shot EMFILE")
            return real_open(path, *open_args, **open_kwargs)

        SP.open = one_emfile
        real_persist = PM._persist_recovered_view

        def persist_then_pause(plans):
            real_persist(plans)
            plan = plans.get("9") or {}
            _atomic_result(Path(args.issued_marker), {
                "token": int(plan["plan_version"]),
                "emfile_count": fired["count"],
                "hwm": json.loads(PM.version_hwm_path().read_text(
                    encoding="utf-8"
                ))["last_issued"],
            })
            Path(args.ready).touch()
            # Parent intentionally sends SIGKILL while this process still owns
            # the EX plan lock. Reaching ``go`` is only a cleanup escape hatch.
            _wait(Path(args.go), timeout_s=60.0)

        PM._persist_recovered_view = persist_then_pause
        PM.load_plan("9")
        raise RuntimeError("recovery pause unexpectedly returned")

    expected = args.expected
    if args.mode == "recovery":
        recovered = PM.load_plan("9")
        if recovered is None:
            raise RuntimeError("recovery writer did not see courier 9")
        expected = int(recovered["plan_version"])

    Path(args.ready).touch()
    _wait(Path(args.go))
    try:
        saved = PM.save_plan(
            "9",
            _body(args.tag or args.mode),
            expected_version=expected,
        )
        result = {
            "status": "saved",
            "expected": expected,
            "saved_version": int(saved["plan_version"]),
        }
    except PM.ConcurrencyError as exc:
        result = {
            "status": "conflict",
            "expected": expected,
            "current_version": int(exc.current_version),
        }
    _atomic_result(Path(args.result), result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
