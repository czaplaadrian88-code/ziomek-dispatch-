#!/usr/bin/env python3
"""Independent AUTO-canary monitor: heartbeat plus fail-closed consistency checks."""
from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from dispatch_v2 import authority_card as AC
from dispatch_v2.tools import ledger_io


HEARTBEAT_PATH = "/var/lib/ziomek-authority/state/monitor-heartbeat.json"
AUTO_STATE_PATH = (
    "/root/.openclaw/workspace/dispatch_state/auto_assign_state.json"
)
SHADOW_PATH = ledger_io.LEDGER["shadow"]
INTERVAL_SECONDS = 30.0
MAX_HEARTBEAT_AGE_SECONDS = 60.0
SHADOW_LOOKBACK_SECONDS = 3600.0


def _parse_ts(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def write_heartbeat(path: str, value: dict) -> None:
    """Atomic temp -> fsync -> rename -> fsync(dir), mode 0600."""
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    tmp = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = -1
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(tmp)
        except OSError:
            pass


def heartbeat_fresh(
    path: str,
    now: Optional[datetime] = None,
    max_age_seconds: float = MAX_HEARTBEAT_AGE_SECONDS,
) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    try:
        with open(path, encoding="utf-8", errors="strict") as stream:
            value = json.load(stream)
    except Exception:
        return False, "monitor_heartbeat_stale"
    if not isinstance(value, dict):
        return False, "monitor_verdict_not_ok"
    checks = value.get("checks")
    if (
        not isinstance(checks, dict)
        or checks.get("verdict") != "OK"
    ):
        return False, "monitor_verdict_not_ok"
    ts = _parse_ts(value.get("ts"))
    pid = value.get("pid")
    if ts is None or isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False, "monitor_heartbeat_stale"
    age = (now.astimezone(timezone.utc) - ts).total_seconds()
    if age < -5.0 or age > float(max_age_seconds):
        return False, "monitor_heartbeat_stale"
    return True, "ok"


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8", errors="strict") as stream:
            value = json.load(stream)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _shadow_rows(path: str, cutoff: datetime) -> Iterator[dict]:
    if os.path.abspath(path) == os.path.abspath(SHADOW_PATH):
        yield from ledger_io.iter_shadow_decisions(
            cutoff, max_bytes=2 * 1024 * 1024, include_observations=True
        )
        return
    try:
        with open(path, encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (TypeError, ValueError):
                    continue
                ts = _parse_ts(row.get("ts")) if isinstance(row, dict) else None
                if ts is not None and ts >= cutoff:
                    yield row
    except OSError:
        return


def run_cycle(
    *,
    now: Optional[datetime] = None,
    heartbeat_path: str = HEARTBEAT_PATH,
    authority_state_path: str = AC.STATE_PATH,
    auto_state_path: str = AUTO_STATE_PATH,
    shadow_path: str = SHADOW_PATH,
) -> dict:
    """Read decisions/state, latch on any divergence, then publish heartbeat."""
    now = now or datetime.now(timezone.utc)
    reasons: list[str] = []
    with AC.state_lock(authority_state_path):
        card_state = AC.load_state(authority_state_path)
        auto_state = _load_json(auto_state_path)
        card_total = card_state.get("executed_total")
        raw_auto_total = auto_state.get("executed_total", 0)
        auto_total = (
            int(raw_auto_total)
            if isinstance(raw_auto_total, int)
            and not isinstance(raw_auto_total, bool)
            and raw_auto_total >= 0
            else None
        )
        # Oba liczniki znaczą „budżet skonsumowany": sukces potwierdzony LUB
        # runner_outcome_unknown. Tylko twarda odmowa pre-send nie zwiększa obu.
        if card_total != auto_total:
            reasons.append("counter_divergence")

        covered = {
            str(oid) for oid in (auto_state.get("executed_order_ids") or [])
        }
        cutoff = now - timedelta(seconds=SHADOW_LOOKBACK_SECONDS)
        receipts = [
            row for row in _shadow_rows(shadow_path, cutoff)
            if row.get("record_type") == "auto_executed"
            or row.get("auto_executed") is True
        ]
        uncovered = sorted({
            str(row.get("order_id"))
            for row in receipts
            if row.get("order_id") not in (None, "")
            and str(row.get("order_id")) not in covered
        })
        if uncovered:
            reasons.append("auto_executed_uncovered")
        if card_state.get("auto_off_latch") is True:
            reasons.append("latch_on")
        if reasons and card_state.get("auto_off_latch") is not True:
            AC.latch_auto_off(
                authority_state_path,
                "monitor_" + reasons[0],
                now,
            )

    result = {
        "ts": now.astimezone(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "checks": {
            "verdict": "ALARM" if reasons else "OK",
            "reasons": reasons,
            "card_executed_total": card_total,
            "executor_executed_total": auto_total,
            "auto_executed_receipts": len(receipts),
            "uncovered_order_ids": uncovered,
        },
    }
    write_heartbeat(heartbeat_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=INTERVAL_SECONDS)
    parser.add_argument("--heartbeat-path", default=HEARTBEAT_PATH)
    args = parser.parse_args()
    interval = min(max(float(args.interval), 1.0), INTERVAL_SECONDS)
    while True:
        result = run_cycle(heartbeat_path=args.heartbeat_path)
        print(json.dumps(result, sort_keys=True), flush=True)
        if args.once:
            return int(result["checks"]["verdict"] != "OK")
        time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
