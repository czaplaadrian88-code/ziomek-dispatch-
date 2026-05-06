"""TASK A CZASÓWKI PROACTIVE — state file helpers (2026-05-05).

Public API contract (sister agent imports these):
  - locked_write_proposals_state() — fcntl.LOCK_EX context, atomic temp→fsync→rename
  - read_proposals_state() — LOCK_SH + 3-retry backoff (50/100/200 ms)
  - new_state_record(oid, osrec, now_utc) — initialize empty record per schema
  - cleanup_stale(state, now) — remove finalized/post-pickup entries

State file shape: top-level "orders" dict keyed by order_id (string),
plus "updated_at" iso-utc.

Schema example (per TASK A spec 2026-05-05):
  {
    "orders": {
      "470559": {
        "first_seen_ts": "2026-05-05T10:00:00+00:00",
        "czas_odbioru_ts": "2026-05-05T13:00:00+00:00",
        "id_kurier_holding": "26",
        "restaurant": "Mama Thai",
        "delivery_address": "Mickiewicza 17",
        "delivery_city": "Białystok",
        "triggers_fired": {
          "50": {
            "ts": "...",
            "proposed_cid": "413",
            "proposed_name": "Mateusz O",
            "score": 78.4,
            "decision": "CZEKAJ",          // TAK | NIE | CZEKAJ | NO_CANDIDATE | LAST_CHANCE_TAK | ...
            "decision_ts": "..."
          }
        },
        "excluded_candidates": [],          // list of cid strings (NIE → exclude)
        "final_assignment_cid": null,
        "final_assignment_ts": null
      }
    },
    "updated_at": "..."
  }

Atomic-write pattern mirrors dispatch_v2/shift_notifications/state.py
(fcntl.flock LOCK_EX on companion .lock file + tempfile.mkstemp + fdopen
+ fsync + os.rename).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

from dispatch_v2.common import setup_logger

STATE_PATH: Path = Path(
    "/root/.openclaw/workspace/dispatch_state/czasowka_proposals_state.json"
)
LOCK_PATH: Path = Path(str(STATE_PATH) + ".lock")
LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"

_log = setup_logger("czasowka_proactive.state", LOG_DIR + "czasowka_proactive.log")

# Cleanup thresholds
_CLEANUP_FINAL_ASSIGNMENT_HOURS = 4
_CLEANUP_POST_PICKUP_HOURS = 1


def _empty_state() -> dict:
    return {"orders": {}, "updated_at": None}


def _atomic_write(path: Path, data: dict) -> None:
    """Zapis temp -> fsync -> rename (atomic na POSIX). Mirrors
    shift_notifications.state._atomic_write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp_czasowka_", suffix=".json"
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
    path = STATE_PATH
    for attempt in range(3):
        try:
            with open(path) as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_SH)
                try:
                    data = json.load(f)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            if not isinstance(data, dict):
                _log.warning("_read_raw: state file is not a dict, returning empty")
                return _empty_state()
            data.setdefault("orders", {})
            return data
        except FileNotFoundError:
            if attempt == 2:
                return _empty_state()
            time.sleep(0.05 * (2 ** attempt))
        except json.JSONDecodeError as e:
            _log.warning(f"_read_raw: JSONDecodeError {e}; returning empty state")
            return _empty_state()
        except Exception as e:
            _log.warning(
                f"_read_raw: unexpected {type(e).__name__}: {e}; returning empty"
            )
            return _empty_state()
    return _empty_state()


@contextmanager
def locked_write_proposals_state() -> Iterator[dict]:
    """fcntl.LOCK_EX context. Yields a mutable dict. On exit, atomically writes
    back. First call creates empty {"orders": {}} skeleton if file missing.
    NEVER raises on lock contention — blocks.

    The yielded dict is normalized to always have an "orders" key. The
    "updated_at" field is set automatically on commit.
    """
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(LOCK_PATH)
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        # Read current state under exclusive lock (read inline — already holding LOCK_EX)
        try:
            with open(STATE_PATH) as f:
                data = json.load(f)
            if not isinstance(data, dict):
                data = _empty_state()
            data.setdefault("orders", {})
        except FileNotFoundError:
            data = _empty_state()
        except json.JSONDecodeError as e:
            _log.warning(f"locked_write: JSONDecodeError {e}; resetting to empty")
            data = _empty_state()
        yield data
        # On exit: stamp updated_at + atomic write back
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_write(STATE_PATH, data)
    finally:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_fd.close()


def read_proposals_state() -> dict:
    """Read with LOCK_SH + 3-retry backoff. Returns
    {"orders": {}, "updated_at": None} on missing/corrupt. Never raises."""
    return _read_raw()


def new_state_record(oid: str, osrec: dict, now_utc: datetime) -> dict:
    """Initialize empty record per schema for a freshly-seen czasówka.

    Args:
      oid: order id string
      osrec: orders_state entry dict (panel order shape — czas_odbioru_timestamp,
             pickup_at_warsaw, restaurant, delivery_*, etc.)
      now_utc: current UTC timestamp (used for first_seen_ts)
    """
    czas_odbioru_ts = (
        osrec.get("czas_odbioru_timestamp")
        or osrec.get("pickup_at_warsaw")
        or osrec.get("pickup_at")
    )
    return {
        "first_seen_ts": now_utc.isoformat(),
        "czas_odbioru_ts": (str(czas_odbioru_ts) if czas_odbioru_ts is not None else None),
        "id_kurier_holding": str(osrec.get("courier_id") or osrec.get("id_kurier") or ""),
        "restaurant": osrec.get("restaurant"),
        "delivery_address": osrec.get("delivery_address"),
        "delivery_city": osrec.get("delivery_city"),
        "triggers_fired": {},
        "excluded_candidates": [],
        "final_assignment_cid": None,
        "final_assignment_ts": None,
    }


def _parse_iso(ts: object) -> Optional[datetime]:
    """Parse iso-format timestamp string. Returns None on parse failure or None.

    Tolerates 'Z' suffix and naive timestamps (treats as UTC for safety —
    czasowka_proposals_state stores everything in UTC iso-format).
    """
    if ts is None:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def cleanup_stale(state: dict, now: datetime) -> int:
    """Remove orders where:
      - final_assignment_ts != None AND final_assignment_ts < now - 4h, OR
      - czas_odbioru_ts < now - 1h (post-pickup, regardless of assignment)

    Mutates `state["orders"]` in-place. Returns count of removed entries.
    """
    if not isinstance(state, dict):
        return 0
    orders = state.setdefault("orders", {})
    if not isinstance(orders, dict):
        state["orders"] = {}
        return 0

    cutoff_final = now - timedelta(hours=_CLEANUP_FINAL_ASSIGNMENT_HOURS)
    cutoff_pickup = now - timedelta(hours=_CLEANUP_POST_PICKUP_HOURS)

    to_remove = []
    for oid, rec in list(orders.items()):
        if not isinstance(rec, dict):
            to_remove.append(oid)
            continue

        final_ts = _parse_iso(rec.get("final_assignment_ts"))
        if final_ts is not None and final_ts < cutoff_final:
            to_remove.append(oid)
            continue

        pickup_ts = _parse_iso(rec.get("czas_odbioru_ts"))
        if pickup_ts is not None and pickup_ts < cutoff_pickup:
            to_remove.append(oid)
            continue

    for oid in to_remove:
        del orders[oid]
    return len(to_remove)
