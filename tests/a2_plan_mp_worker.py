#!/usr/bin/env python3
"""Odizolowany writer dla A-2 race oracle.

WAŻNE: bezpieczniki Telegram/notify i logging są uzbrajane przed importem
``plan_manager``. Ten plik jest uruchamiany jako osobny proces, więc nie polega
na fixture'ach pytest procesu-rodzica.
"""
from __future__ import annotations

import argparse
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
    parser.add_argument("--mode", choices=("stale", "recovery"), required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--go", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--expected", type=int)
    args = parser.parse_args()

    scratch = Path(args.scratch)
    _arm_safety(scratch)

    # PIERWSZY import modułu stanu następuje dopiero po pełnym harnessie wyżej.
    from dispatch_v2 import plan_manager as PM

    state_dir = Path(args.state_dir)
    PM.PLANS_FILE = state_dir / "courier_plans.json"
    PM.LOCK_FILE = state_dir / "courier_plans.lock"
    PM._corrupt_guard_on = lambda: True
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None

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
            _body(args.mode),
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
