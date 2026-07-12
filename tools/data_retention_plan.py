#!/usr/bin/env python3
"""Metadata-only, dry-run retention planner for private ledgers.

There is intentionally no ``--apply`` and no unlink helper in this module.
Retention duration is required on every invocation because business decision
B-05 is still open.  File contents are never opened.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


_DATED = (
    re.compile(r"^world_record-(?P<day>\d{8})\.jsonl(?:\.\d+)?(?:\.gz)?$"),
    re.compile(r"^shadow_decisions-(?P<day>\d{8})(?:T\d{6}Z)?\.jsonl(?:\.gz)?$"),
)


def _date_from_name(name: str) -> date | None:
    for pattern in _DATED:
        match = pattern.fullmatch(name)
        if match:
            try:
                return datetime.strptime(match.group("day"), "%Y%m%d").date()
            except ValueError:
                return None
    return None


def plan_retention(root: str | os.PathLike[str], *, retention_days: int,
                   as_of: date | None = None) -> dict[str, Any]:
    if retention_days < 1:
        raise ValueError("retention_days must be >= 1")
    root_path = Path(root)
    cutoff = (as_of or datetime.now(timezone.utc).date()) - timedelta(days=retention_days)
    candidates: list[dict[str, Any]] = []
    kept = 0
    refused = 0
    if not root_path.is_dir():
        return {
            "schema": "private_retention_plan.v1", "mode": "would-delete",
            "retention_days": retention_days, "cutoff_day": cutoff.isoformat(),
            "candidates": [], "candidate_count": 0, "would_delete_bytes": 0,
            "kept_count": 0, "refused_count": 0, "root_missing": True,
        }
    for entry in sorted(root_path.rglob("*")):
        try:
            st = entry.lstat()
        except OSError:
            refused += 1
            continue
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            if entry.is_symlink() or not stat.S_ISDIR(st.st_mode):
                refused += 1
            continue
        day = _date_from_name(entry.name)
        if day is None:
            refused += 1
            continue
        if day < cutoff:
            candidates.append({
                "relative_name": str(entry.relative_to(root_path)),
                "ledger_day": day.isoformat(),
                "size": st.st_size,
                "mode": f"{stat.S_IMODE(st.st_mode):04o}",
                "reason": "older_than_retention_cutoff",
            })
        else:
            kept += 1
    return {
        "schema": "private_retention_plan.v1", "mode": "would-delete",
        "retention_days": retention_days, "cutoff_day": cutoff.isoformat(),
        "candidates": candidates, "candidate_count": len(candidates),
        "would_delete_bytes": sum(item["size"] for item in candidates),
        "kept_count": kept, "refused_count": refused, "root_missing": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Metadata-only private-ledger retention plan")
    parser.add_argument("--root", required=True)
    parser.add_argument("--retention-days", required=True, type=int)
    parser.add_argument("--as-of", help="YYYY-MM-DD UTC (test/reproducibility)")
    args = parser.parse_args(argv)
    try:
        as_of = date.fromisoformat(args.as_of) if args.as_of else None
        report = plan_retention(args.root, retention_days=args.retention_days, as_of=as_of)
    except (ValueError, OSError) as exc:
        print(json.dumps({"schema": "private_retention_plan.v1", "error": type(exc).__name__}))
        return 2
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
