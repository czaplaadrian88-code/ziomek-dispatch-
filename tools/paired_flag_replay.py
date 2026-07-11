#!/usr/bin/env python3
"""Paired OFF/ON replay on the same frozen world-record corpus.

``world_replay`` intentionally restores the flags captured in every record.
Consequently, changing the process' live flags file cannot exercise a new flag
on historical data.  This tool injects one explicit boolean into an in-memory
copy of each record and compares the two replay results directly.

The report is aggregate-only: it never serializes order or courier IDs, event
timestamps, reasons, scores, or exception messages.  The underlying replay
keeps its normal no-network/no-write sandbox.
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import copy
import io
import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Optional

from dispatch_v2.tools import world_replay as WR
from dispatch_v2.tools import world_replay_gate as GATE


SCHEMA = "paired_flag_replay.v1"
_FLAG_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
_CORE_FIELDS = frozenset(GATE.CORE_FIELDS)


@contextlib.contextmanager
def _suppress_transitive_output():
    """Keep the aggregate-only contract across the whole replay call graph.

    ``world_replay.replay_one`` imports the production pipeline, whose loggers
    and legacy ``print`` calls can include operational identifiers.  Redacting
    only this tool's final JSON is therefore insufficient.  Silence Python
    stdout/stderr and all logging while a frozen record is evaluated, then
    restore the caller's logging threshold even when replay raises.
    """
    previous_disable = logging.root.manager.disable
    stdout_sink = io.StringIO()
    stderr_sink = io.StringIO()
    with (
        contextlib.redirect_stdout(stdout_sink),
        contextlib.redirect_stderr(stderr_sink),
    ):
        logging.disable(logging.CRITICAL)
        try:
            yield
        finally:
            logging.disable(previous_disable)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def with_flag(record: dict, flag_name: str, enabled: bool) -> dict:
    """Return a shallow record clone with an isolated flags snapshot."""
    if not _FLAG_RE.fullmatch(flag_name):
        raise ValueError("flag name must be uppercase ASCII with underscores")
    clone = copy.copy(record)
    clone["flags"] = dict(record.get("flags") or {})
    clone["flags"][flag_name] = bool(enabled)
    return clone


def run_paired(
    *,
    flag_name: str,
    since: datetime,
    until: datetime,
    first: str,
    record_dir: str = WR.RECORD_DIR,
    replay_one: Callable[[dict], tuple[dict, int]] = WR.replay_one,
    records_override: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Compare flag OFF and ON for every record, aggregate-only.

    ``first`` must be run in both orders for a rollout verdict, because replayed
    solvers and process-local caches can otherwise hide a warm-up/order effect.
    """
    if first not in {"off", "on"}:
        raise ValueError("first must be 'off' or 'on'")
    if until <= since:
        raise ValueError("until must be later than since")

    skipped_no_now = 0
    skipped_pre_wr1 = 0
    if records_override is None:
        records, skipped_no_now, skipped_pre_wr1 = GATE._iter_window_records(
            record_dir, since, until
        )
    else:
        records = list(records_override)

    first_enabled = first == "on"
    fieldsets: collections.Counter[str] = collections.Counter()
    errors: collections.Counter[str] = collections.Counter()
    exact = 0
    critical = 0
    miss_mismatch = 0
    off_misses = 0
    on_misses = 0

    for record in records:
        try:
            with _suppress_transitive_output():
                first_result, first_miss = replay_one(
                    with_flag(record, flag_name, first_enabled)
                )
                second_result, second_miss = replay_one(
                    with_flag(record, flag_name, not first_enabled)
                )
        except Exception as exc:  # exception messages can contain identifiers
            errors[type(exc).__name__] += 1
            continue

        off = second_result if first_enabled else first_result
        on = first_result if first_enabled else second_result
        off_miss = second_miss if first_enabled else first_miss
        on_miss = first_miss if first_enabled else second_miss
        off_misses += int(off_miss)
        on_misses += int(on_miss)
        if off_miss != on_miss:
            miss_mismatch += 1

        changed = sorted(key for key in off if off[key] != on.get(key))
        changed.extend(sorted(key for key in on if key not in off))
        if not changed:
            exact += 1
            continue
        fieldsets["+".join(changed)] += 1
        if _CORE_FIELDS.intersection(changed):
            critical += 1

    return {
        "schema": SCHEMA,
        "window": {"since": since.isoformat(), "until": until.isoformat()},
        "flag": flag_name,
        "first": first,
        "n": len(records),
        "exact": exact,
        "diffs": sum(fieldsets.values()),
        "critical": critical,
        "miss_mismatch": miss_mismatch,
        "off_misses": off_misses,
        "on_misses": on_misses,
        "errors": dict(sorted(errors.items())),
        "fieldsets": dict(sorted(fieldsets.items())),
        "flag_injected_records": len(records),
        "skipped_no_now": skipped_no_now,
        "skipped_pre_wr1": skipped_pre_wr1,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--flag", required=True)
    parser.add_argument("--since", required=True)
    parser.add_argument("--until", required=True)
    parser.add_argument("--first", required=True, choices=("off", "on"))
    parser.add_argument("--record-dir", default=WR.RECORD_DIR)
    args = parser.parse_args(argv)

    try:
        report = run_paired(
            flag_name=args.flag,
            since=_parse_dt(args.since),
            until=_parse_dt(args.until),
            first=args.first,
            record_dir=args.record_dir,
        )
    except Exception as exc:
        # Fail loud without echoing paths, record contents, or exception text.
        print(json.dumps({"schema": SCHEMA, "error": type(exc).__name__}))
        return 2
    print(json.dumps(report, sort_keys=True))
    if report["n"] == 0 or report["errors"]:
        return 2
    return 0 if report["diffs"] == 0 and report["miss_mismatch"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
