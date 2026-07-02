"""Shared loader/helpers for Sprint 2 analytical scripts."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LEARNING_LOG = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")
EVENTS_DB = Path("/root/.openclaw/workspace/dispatch_state/events.db")

SPRINT1_DEPLOY_UTC = datetime(2026, 4, 30, 9, 5, 0, tzinfo=timezone.utc)   # 11:05 Warsaw
V319I_DEPLOY_UTC   = datetime(2026, 4, 30, 9, 49, 0, tzinfo=timezone.utc)  # 11:49 Warsaw

WARSAW = ZoneInfo("Europe/Warsaw")  # DST-safe CET/CEST — L2 audyt 2.0 (był fixed +2)


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def to_warsaw(dt):
    if dt is None:
        return None
    return dt.astimezone(WARSAW).replace(tzinfo=None)


def load_entries(since_utc=None, until_utc=None, path=LEARNING_LOG):
    """Yield parsed JSONL entries in [since_utc, until_utc]."""
    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            ts = parse_ts(e.get("ts"))
            if ts is None:
                continue
            if since_utc and ts < since_utc:
                continue
            if until_utc and ts > until_utc:
                continue
            yield e


def now_utc():
    return datetime.now(tz=timezone.utc)


def fmt_warsaw(dt):
    if dt is None:
        return "?"
    return to_warsaw(dt).strftime("%Y-%m-%d %H:%M:%S")
