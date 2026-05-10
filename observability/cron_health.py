"""Cron health tracker — per-unit success/failure ledger w JSON.

Master plan TOP-15 #4 (META top-1, OBSERVABILITY A+B+D, OPS R2, STATE_OWNERSHIP F2).
Eliminuje silent cron timer leak class (`overrides_reset` martwy 4 dni cicho 03-07.05).

Schema: /root/.openclaw/workspace/dispatch_state/cron_health.json
{
  "units": {
    "<unit_name>": {
      "type": "long_running" | "cron_timer",
      "last_success": "<iso ts utc>" | null,
      "last_failure": "<iso ts utc>" | null,
      "last_failure_result": "failed" | "timeout" | "killed" | null,
      "last_failure_exit": <int> | null,
      "consecutive_failures": <int>,
      "expected_max_silence_h": <float> | null,  # null dla long-running
      "status": "ok" | "stale" | "failed" | "active" | "unknown",
      "last_alert_ts": "<iso ts utc>" | null,    # dedup per unit
      "last_updated": "<iso ts utc>"
    }
  },
  "_meta": {"schema_version": 1, "last_write_ts": "<iso ts utc>"}
}

Pattern:
- Atomic write (tempfile + fsync + os.replace, reuse z core/flags_io)
- fcntl.LOCK_EX wokół RMW (cross-process serialization)
- Defensive: never crashes caller (try/except wokół I/O), zwraca bool
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CRON_HEALTH_PATH = Path("/root/.openclaw/workspace/dispatch_state/cron_health.json")
SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """Atomic JSON write with mode preservation. Cleanup tempfile on any exception."""
    path = Path(path)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)

    mode = 0o644
    if path.exists():
        mode = path.stat().st_mode & 0o777

    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".cron_health_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def _empty_state() -> dict[str, Any]:
    return {
        "units": {},
        "_meta": {"schema_version": SCHEMA_VERSION, "last_write_ts": _now_iso()},
    }


def load_health(path: Path | None = None) -> dict[str, Any]:
    """Pure read. Returns empty schema if missing/corrupt.

    path=None → module-level CRON_HEALTH_PATH (runtime lookup, monkeypatch-friendly).
    """
    path = Path(path if path is not None else CRON_HEALTH_PATH)
    if not path.exists():
        return _empty_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        if not content:
            return _empty_state()
        data = json.loads(content)
        if "units" not in data:
            data["units"] = {}
        if "_meta" not in data:
            data["_meta"] = {"schema_version": SCHEMA_VERSION, "last_write_ts": _now_iso()}
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def _locked_rmw(path: Path | None, mutator) -> dict[str, Any]:
    """Cross-process locked RMW with LOCK_EX.

    path=None → module-level CRON_HEALTH_PATH.
    """
    path = Path(path if path is not None else CRON_HEALTH_PATH)
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / (path.name + ".lock")

    with open(lock_path, "a") as lock_f:
        fcntl.flock(lock_f, fcntl.LOCK_EX)
        try:
            data = load_health(path)
            data = mutator(data)
            data["_meta"]["last_write_ts"] = _now_iso()
            _atomic_write_json(path, data)
            return data
        finally:
            fcntl.flock(lock_f, fcntl.LOCK_UN)


def _ensure_unit(data: dict[str, Any], unit: str, unit_type: str = "cron_timer") -> dict[str, Any]:
    """Bootstrap unit entry with defaults if missing."""
    if unit not in data["units"]:
        data["units"][unit] = {
            "type": unit_type,
            "last_success": None,
            "last_failure": None,
            "last_failure_result": None,
            "last_failure_exit": None,
            "consecutive_failures": 0,
            "expected_max_silence_h": None,
            "status": "unknown",
            "last_alert_ts": None,
            "last_updated": _now_iso(),
        }
    return data["units"][unit]


def record_run_success(
    unit: str,
    *,
    unit_type: str = "cron_timer",
    expected_max_silence_h: float | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Mark unit as successfully completed (clears consecutive_failures).

    Wywoływany na koniec timer service ExecStart przed exit:
        python -m dispatch_v2.observability.cron_health record-success <unit>
    """
    def _mut(data: dict[str, Any]) -> dict[str, Any]:
        entry = _ensure_unit(data, unit, unit_type=unit_type)
        entry["last_success"] = _now_iso()
        entry["consecutive_failures"] = 0
        entry["status"] = "ok" if unit_type == "cron_timer" else "active"
        if expected_max_silence_h is not None:
            entry["expected_max_silence_h"] = expected_max_silence_h
        entry["last_updated"] = _now_iso()
        return data
    return _locked_rmw(path, _mut)


def record_run_failure(
    unit: str,
    *,
    result: str = "failed",
    exit_code: int | None = None,
    unit_type: str = "cron_timer",
    path: Path | None = None,
) -> dict[str, Any]:
    """Mark unit failure (increments consecutive_failures).

    Wywoływany przez OnFailure handler (alert_onfailure.py).
    """
    def _mut(data: dict[str, Any]) -> dict[str, Any]:
        entry = _ensure_unit(data, unit, unit_type=unit_type)
        entry["last_failure"] = _now_iso()
        entry["last_failure_result"] = result
        entry["last_failure_exit"] = exit_code
        entry["consecutive_failures"] = int(entry.get("consecutive_failures", 0)) + 1
        entry["status"] = "failed"
        entry["last_updated"] = _now_iso()
        return data
    return _locked_rmw(path, _mut)


def record_alert_sent(unit: str, path: Path | None = None) -> dict[str, Any]:
    """Mark że alert został wysłany dla tego unitu (dedup window tracking)."""
    def _mut(data: dict[str, Any]) -> dict[str, Any]:
        entry = _ensure_unit(data, unit)
        entry["last_alert_ts"] = _now_iso()
        entry["last_updated"] = _now_iso()
        return data
    return _locked_rmw(path, _mut)


def is_stale(
    unit: str,
    *,
    expected_max_silence_h: float | None = None,
    now: datetime | None = None,
    path: Path | None = None,
) -> bool:
    """Check czy unit jest stale (last_success > expected_max_silence_h ago).

    Returns False jeśli unit type=long_running (continuous) lub never registered
    lub expected_max_silence_h jest None (no threshold configured).
    """
    data = load_health(path)
    entry = data["units"].get(unit)
    if entry is None:
        return False
    if entry.get("type") == "long_running":
        return False

    threshold = expected_max_silence_h or entry.get("expected_max_silence_h")
    if threshold is None:
        return False

    last_success_str = entry.get("last_success")
    if last_success_str is None:
        # Never succeeded → stale only jeśli zarejestrowany >threshold ago
        last_updated_str = entry.get("last_updated")
        if last_updated_str is None:
            return False
        last_success_str = last_updated_str

    try:
        last_dt = datetime.fromisoformat(last_success_str)
    except (ValueError, TypeError):
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)

    now_dt = now or datetime.now(timezone.utc)
    silence_h = (now_dt - last_dt).total_seconds() / 3600.0
    return silence_h > threshold


def is_alert_dedup_active(
    unit: str,
    *,
    dedup_window_min: int = 30,
    now: datetime | None = None,
    path: Path | None = None,
) -> bool:
    """Returns True jeśli alert dla unitu był wysłany <dedup_window_min temu."""
    data = load_health(path)
    entry = data["units"].get(unit)
    if entry is None:
        return False
    last_alert_str = entry.get("last_alert_ts")
    if last_alert_str is None:
        return False
    try:
        last_dt = datetime.fromisoformat(last_alert_str)
    except (ValueError, TypeError):
        return False
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    now_dt = now or datetime.now(timezone.utc)
    minutes_since = (now_dt - last_dt).total_seconds() / 60.0
    return minutes_since < dedup_window_min
