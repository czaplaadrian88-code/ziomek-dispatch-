"""pending_pool — warstwa persystencji puli pending dla rolling late-binding (Faza 0 obserwacja).

Atomic write + fcntl locking wzorowane na plan_manager.py.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dispatch_v2 import common

_log = logging.getLogger("pending_pool")

POOL_PATH = Path("/root/.openclaw/workspace/dispatch_state/pending_pool.json")
LOCK_PATH = Path("/root/.openclaw/workspace/dispatch_state/pending_pool.lock")
LOG_PATH = Path("/root/.openclaw/workspace/dispatch_state/pending_pool_log.jsonl")


def _ensure_parent() -> None:
    POOL_PATH.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: object) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            return None
    return None


@contextmanager
def _locked(exclusive: bool):
    _ensure_parent()
    LOCK_PATH.touch(exist_ok=True)
    mode = "r+b"
    with open(LOCK_PATH, mode) as lockfh:
        fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lockfh.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, data: Any) -> None:
    _ensure_parent()
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception as _e:
        _log.error(f"atomic write fail path={path} ({type(_e).__name__}: {_e})")
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _read_raw() -> Dict[str, Any]:
    if not POOL_PATH.exists():
        return {}
    try:
        with open(POOL_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            _log.warning("pending_pool.json is not an object; treating as empty")
            return {}
        return data
    except json.JSONDecodeError as e:
        _log.warning(f"pending_pool.json corrupt: {e}")
        return {}


def _write_raw(data: Dict[str, Any]) -> None:
    _atomic_write(POOL_PATH, data)


# ---- public API ----

def load_pool() -> Dict[str, Any]:
    """Load entire pool dict (read-only copy). Shared lock."""
    with _locked(exclusive=False):
        return _read_raw()


def upsert_order(
    order_id: str,
    created_at: str,
    pickup_ready_at: str,
    frozen: bool = False,
    tentative_cid: Optional[str] = None,
    churn_count: Optional[int] = None,
    frozen_at: Optional[str] = None,
) -> None:
    """Insert or update an order entry in the pool.

    For a new entry:
      - freeze_at = compute_freeze_at(created_at, pickup_ready_at)
      - churn_count = 0
      - frozen = False
      - removed_reason = None

    For an existing entry:
      - created_at is NOT overwritten.
      - All other supplied fields are updated.
      - updated_at is always set to now UTC.

    Logs action=upsert.
    """
    oid = str(order_id)
    now_iso = _now_iso()
    with _locked(exclusive=True):
        pool = _read_raw()
        existing = pool.get(oid)
        if existing is None:
            freeze_at = compute_freeze_at(created_at, pickup_ready_at)
            entry = {
                "order_id": oid,
                "created_at": created_at,
                "pickup_ready_at": pickup_ready_at,
                "freeze_at": freeze_at,
                "tentative_cid": tentative_cid,
                "churn_count": churn_count if churn_count is not None else 0,
                "frozen": frozen,
                "frozen_at": frozen_at,
                "removed_reason": None,
                "updated_at": now_iso,
            }
        else:
            entry = dict(existing)
            # never overwrite created_at
            entry["pickup_ready_at"] = pickup_ready_at
            entry["freeze_at"] = compute_freeze_at(entry["created_at"], pickup_ready_at)
            if tentative_cid is not None:
                entry["tentative_cid"] = tentative_cid
            if churn_count is not None:
                entry["churn_count"] = churn_count
            if frozen is not None:
                entry["frozen"] = frozen
            if frozen_at is not None:
                entry["frozen_at"] = frozen_at
            entry["updated_at"] = now_iso
        pool[oid] = entry
        _write_raw(pool)
    log_event("upsert", oid)


def remove_order(order_id: str, reason: str) -> None:
    """Remove an order entry from the pool. No-op if absent.

    Logs action=remove with reason.
    """
    oid = str(order_id)
    with _locked(exclusive=True):
        pool = _read_raw()
        if oid not in pool:
            return
        del pool[oid]
        _write_raw(pool)
    log_event("remove", oid, extra_dict={"reason": reason})


def get_active() -> List[Dict[str, Any]]:
    """Return list of entries where frozen is False."""
    pool = load_pool()
    return [v for v in pool.values() if not v.get("frozen", False)]


def compute_freeze_at(
    created_at: Any,
    pickup_ready_at: Any,
    lead_min: Optional[float] = None,
) -> str:
    """Return ISO-UTC string = max(created_at, pickup_ready_at - lead_min).

    Accepts str ISO or datetime objects.
    lead_min defaults to common.FREEZE_LEAD_MIN.
    """
    if lead_min is None:
        lead_min = common.FREEZE_LEAD_MIN
    created_dt = _parse_iso(created_at)
    pickup_dt = _parse_iso(pickup_ready_at)
    if created_dt is None or pickup_dt is None:
        raise ValueError(f"cannot parse dates: created_at={created_at!r}, pickup_ready_at={pickup_ready_at!r}")
    pickup_minus = pickup_dt - timedelta(minutes=lead_min)
    freeze_dt = max(created_dt, pickup_minus)
    return freeze_dt.isoformat()


def log_event(
    action: str,
    order_id: str,
    extra_dict: Optional[Dict[str, Any]] = None,
) -> None:
    """Append a JSON line to LOG_PATH. Never raises."""
    try:
        entry = {
            "ts": _now_iso(),
            "action": action,
            "order_id": order_id,
        }
        if extra_dict:
            entry.update(extra_dict)
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning(f"log_event fail action={action} order_id={order_id}: {e}")
