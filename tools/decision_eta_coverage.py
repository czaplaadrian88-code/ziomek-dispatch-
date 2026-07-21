#!/usr/bin/env python3
"""Daily coverage/contract gate for decision-time ETA snapshots.

The denominator is the canonical shadow decision ledger.  The numerator is a
unique ``shadow_dispatcher`` snapshot joined by event id.  Other decision
sources (czasowka, reassignment, resweep and plan commits) are reported as
additional counts but do not inflate primary coverage.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from dispatch_v2.tools import _rotated_logs


WARSAW = ZoneInfo("Europe/Warsaw")
DEFAULT_DECISIONS = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DEFAULT_ETA_LOG = "/root/.openclaw/workspace/dispatch_state/decision_eta_log.jsonl"
REQUIRED_TOP_LEVEL = {
    "schema", "decision_id", "decision_ts", "recorded_at", "decision_kind",
    "source", "order_id", "selected_cid", "outcome", "candidate_pool_scope",
    "candidate_count", "candidates", "model", "calibration",
}
REQUIRED_CANDIDATE = {"cid", "selected", "position_source", "legs"}
REQUIRED_LEG = {"order_id", "pickup_eta_at", "delivery_eta_at", "missing"}


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, WARSAW).astimezone(timezone.utc)
    end = (datetime.combine(day, time.min, WARSAW) + timedelta(days=1)).astimezone(timezone.utc)
    return start, end


def _in_window(value: Any, start: datetime, end: datetime) -> bool:
    parsed = _parse_ts(value)
    return parsed is not None and start <= parsed < end


def validate_record(record: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = REQUIRED_TOP_LEVEL - set(record)
    if missing:
        errors.append("missing_top:" + ",".join(sorted(missing)))
    if record.get("schema") != "decision_eta.v1":
        errors.append("bad_schema")
    if _parse_ts(record.get("decision_ts")) is None:
        errors.append("bad_decision_ts")
    candidates = record.get("candidates")
    if not isinstance(candidates, list):
        errors.append("candidates_not_list")
        return errors
    if record.get("candidate_count") != len(candidates):
        errors.append("candidate_count_mismatch")
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            errors.append("candidate_not_object")
            continue
        if REQUIRED_CANDIDATE - set(candidate):
            errors.append("candidate_contract")
        legs = candidate.get("legs")
        if not isinstance(legs, list):
            errors.append("legs_not_list")
            continue
        for leg in legs:
            if not isinstance(leg, Mapping) or REQUIRED_LEG - set(leg):
                errors.append("leg_contract")
    return errors


def calculate(
    *, day: date, decisions_path: str, eta_log_path: str,
) -> dict[str, Any]:
    start, end = _day_bounds(day)
    decision_ids: set[str] = set()
    decision_duplicates = 0
    for record in _rotated_logs.iter_jsonl_records(decisions_path, start):
        if not _in_window(record.get("ts"), start, end):
            continue
        event_id = record.get("event_id")
        if event_id in (None, ""):
            continue
        event_id = str(event_id)
        if event_id in decision_ids:
            decision_duplicates += 1
        decision_ids.add(event_id)

    primary_logged: set[str] = set()
    primary_duplicates = 0
    invalid = 0
    sources: Counter[str] = Counter()
    eta_records = 0
    for record in _rotated_logs.iter_jsonl_records(eta_log_path, start):
        if not _in_window(record.get("decision_ts"), start, end):
            continue
        eta_records += 1
        sources[str(record.get("source") or "unknown")] += 1
        if validate_record(record):
            invalid += 1
            continue
        if record.get("source") != "shadow_dispatcher":
            continue
        raw_id = str(record.get("decision_id") or "")
        event_id = raw_id.removeprefix("shadow_dispatcher:")
        if event_id in primary_logged:
            primary_duplicates += 1
        primary_logged.add(event_id)

    matched = decision_ids & primary_logged
    missing = decision_ids - primary_logged
    orphan = primary_logged - decision_ids
    denominator = len(decision_ids)
    coverage = len(matched) / denominator if denominator else None
    return {
        "schema": "decision_eta.coverage.v1",
        "day_warsaw": day.isoformat(),
        "window_utc": [start.isoformat(), end.isoformat()],
        "decision_count": denominator,
        "decision_duplicate_rows": decision_duplicates,
        "eta_record_count": eta_records,
        "eta_records_by_source": dict(sorted(sources.items())),
        "valid_primary_logged": len(primary_logged),
        "primary_duplicate_rows": primary_duplicates,
        "matched_decisions": len(matched),
        "missing_decisions": len(missing),
        "orphan_primary_records": len(orphan),
        "invalid_eta_records": invalid,
        "coverage": round(coverage, 6) if coverage is not None else None,
        "coverage_pct": round(coverage * 100.0, 3) if coverage is not None else None,
    }


def _day(value: str | None) -> date:
    if value:
        return date.fromisoformat(value)
    return datetime.now(WARSAW).date()


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--day", help="dzień Europe/Warsaw, YYYY-MM-DD")
    parser.add_argument("--decisions", default=DEFAULT_DECISIONS)
    parser.add_argument("--eta-log", default=DEFAULT_ETA_LOG)
    parser.add_argument(
        "--min-coverage", type=float, default=1.0,
        help="bramka 0..1; domyślnie 1.0, bo kontrakt wymaga każdej decyzji",
    )
    parser.add_argument("--out", help="opcjonalny plik JSON z agregatem")
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not math.isfinite(args.min_coverage) or not 0.0 <= args.min_coverage <= 1.0:
        parser.error("--min-coverage musi być w zakresie 0..1")

    report = calculate(
        day=_day(args.day),
        decisions_path=args.decisions,
        eta_log_path=args.eta_log,
    )
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2)
    print(payload)
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")

    if report["decision_count"] == 0:
        return 2  # HOLD: no denominator
    if report["invalid_eta_records"]:
        return 1
    if report["coverage"] is None or report["coverage"] < args.min_coverage:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
