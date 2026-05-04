"""Log rotation utility — daily rotation z retention enforcement.

Strategy:
  Logs są named *_YYYYMMDD.jsonl (per-day file per type).
  Rotation = scan log dir, delete files older than RETENTION_DAYS.

Cron-safe: idempotent, defensive, no race condition (delete is atomic).

Usage:
  python -m dispatch_v2.observability.log_rotation --retention-days 14
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dispatch_v2.observability.candidate_logger import DEFAULT_LOG_DIR

_DATE_RE = re.compile(r"^(?P<prefix>[a-z_]+)_(?P<date>\d{8})\.jsonl$")


def rotate(
    log_dir: Optional[Path] = None,
    retention_days: int = 14,
    now_dt: Optional[datetime] = None,
) -> dict:
    """Delete log files older than retention_days. Returns counts dict."""
    log_dir = Path(log_dir or DEFAULT_LOG_DIR)
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    cutoff = now_dt.date() - timedelta(days=retention_days)

    counts = {"scanned": 0, "deleted": 0, "kept": 0, "skipped_unparseable": 0}
    if not log_dir.exists():
        return counts

    for f in log_dir.iterdir():
        if not f.is_file():
            continue
        counts["scanned"] += 1
        m = _DATE_RE.match(f.name)
        if not m:
            counts["skipped_unparseable"] += 1
            continue
        try:
            file_date = datetime.strptime(m.group("date"), "%Y%m%d").date()
        except ValueError:
            counts["skipped_unparseable"] += 1
            continue
        if file_date < cutoff:
            try:
                f.unlink()
                counts["deleted"] += 1
            except Exception:
                # Defensive — log but don't crash
                counts["skipped_unparseable"] += 1
        else:
            counts["kept"] += 1
    return counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--retention-days", type=int, default=14)
    parser.add_argument("--log-dir", type=Path, default=None)
    args = parser.parse_args()
    counts = rotate(log_dir=args.log_dir, retention_days=args.retention_days)
    print(counts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
