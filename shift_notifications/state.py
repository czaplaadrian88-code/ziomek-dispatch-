"""TASK B SHIFT NOTIFICATIONS — state file helpers (2026-05-04).

Public API contract (sister agent imports these):
  - locked_write_confirmations() — fcntl.LOCK_EX context, atomic temp→fsync→rename
  - read_confirmations() — LOCK_SH + 3-retry backoff (50/100/200 ms)
  - find_record_for_cid(records, today_iso, cid)
  - append_learning_log(event)

State file shape: single JSON object with two top-level dicts
'start_notified' and 'end_notified', each keyed by 'YYYY-MM-DD:Full Name'.

Atomic-write pattern reuses state_machine.py:111-141 (fcntl.flock LOCK_EX
on .lock file + tempfile.mkstemp + fdopen + fsync + os.rename).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from dispatch_v2.common import setup_logger

STATE_FILE: Path = Path("/root/.openclaw/workspace/dispatch_state/shift_confirmations.json")
LEARNING_LOG: Path = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")
LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"

_log = setup_logger("shift_notifications.state", LOG_DIR + "shift_notifications.log")


def _empty_state() -> dict:
    return {"start_notified": {}, "end_notified": {}}


def _atomic_write(path: Path, data: dict) -> None:
    """Zapis temp -> fsync -> rename (atomic na POSIX). Mirrors state_machine._atomic_write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp_shift_", suffix=".json"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _read_raw() -> dict:
    """Read with LOCK_SH + 3-retry exponential backoff (50/100/200 ms).
    Returns empty skeleton on missing/corrupt — never raises."""
    path = STATE_FILE
    for attempt in range(3):
        try:
            with open(path) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            if not isinstance(data, dict):
                _log.warning(f"_read_raw: state file is not a dict, returning empty")
                return _empty_state()
            # Normalize — ensure both top-level keys exist
            data.setdefault("start_notified", {})
            data.setdefault("end_notified", {})
            return data
        except FileNotFoundError:
            if attempt == 2:
                return _empty_state()
            time.sleep(0.05 * (2 ** attempt))
        except json.JSONDecodeError as e:
            _log.warning(f"_read_raw: JSONDecodeError {e}; returning empty state")
            return _empty_state()
        except Exception as e:
            _log.warning(f"_read_raw: unexpected {type(e).__name__}: {e}; returning empty")
            return _empty_state()
    return _empty_state()


@contextmanager
def locked_write_confirmations() -> Iterator[dict]:
    """fcntl.LOCK_EX context. Yields a mutable dict. On exit, atomically writes back.
    First call creates empty {} skeleton if file missing. NEVER raises on lock contention — blocks.
    """
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(STATE_FILE) + ".lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        # Read current state under exclusive lock (read inline — already holding LOCK_EX)
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = _empty_state()
            data.setdefault("start_notified", {})
            data.setdefault("end_notified", {})
        except FileNotFoundError:
            data = _empty_state()
        except json.JSONDecodeError as e:
            _log.warning(f"locked_write: JSONDecodeError {e}; resetting to empty")
            data = _empty_state()
        yield data
        # On exit: atomic write back
        _atomic_write(STATE_FILE, data)
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


def read_confirmations() -> dict:
    """Read with LOCK_SH + 3-retry backoff. Returns {'start_notified':{},'end_notified':{}}
    on missing/corrupt. Never raises."""
    return _read_raw()


def find_record_for_cid(records: dict, today_iso: str, cid: str) -> Optional[dict]:
    """records is a flat dict like {f'{date}:{full_name}': {..., 'cid': '515', ...}}.
    Returns the matching value or None.
    cid: stringified courier_id. today_iso: 'YYYY-MM-DD'.
    Searches only entries whose key startswith today_iso + ':'.
    """
    if not isinstance(records, dict):
        return None
    cid_str = str(cid)
    prefix = f"{today_iso}:"
    for key, val in records.items():
        if not isinstance(key, str) or not key.startswith(prefix):
            continue
        if not isinstance(val, dict):
            continue
        rec_cid = val.get("cid")
        if rec_cid is None:
            continue
        if str(rec_cid) == cid_str:
            return val
    return None


def append_learning_log(event: dict) -> None:
    """Append-only JSONL. Adds 'ts' (UTC ISO) if missing.
    Best-effort: log warnings on IO error but never raise.
    """
    if not isinstance(event, dict):
        _log.warning(f"append_learning_log: event is not dict ({type(event).__name__}), skip")
        return
    rec = dict(event)
    if "ts" not in rec:
        rec["ts"] = datetime.now(timezone.utc).isoformat()
    try:
        LEARNING_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LEARNING_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning(f"append_learning_log fail: {type(e).__name__}: {e}")
