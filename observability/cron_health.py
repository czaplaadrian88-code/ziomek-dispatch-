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

import argparse
import fcntl
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CRON_HEALTH_PATH = Path("/root/.openclaw/workspace/dispatch_state/cron_health.json")
SCHEMA_VERSION = 1


# ── Canonical stale-threshold registry (FALA1 watchdog-close, 2026-07-02) ────────────
# Single source of truth for `expected_max_silence_h` of cron-timer units whose ledger
# entry would otherwise be thr=None — the watchdog skips those (silent-gap blind spot,
# audyt 2.0 §2/§6.7). Values = timer cadence + margin (see FALA1_watchdog_raport.md):
#
#   unit                                    cadence (timer)          threshold  rationale
#   dispatch-cod-weekly.service             weekly Mon 06:00 (168h)   192.0h    168h + 24h margin (1 missed Mon)
#   dispatch-cod-panel-ingest.service       weekly Mon 08:30 (168h)   192.0h    168h + 24h margin
#   dispatch-faza7-kpi.service              daily 06:00 (24h)          25.0h    24h + 1h margin (== _UNIT_METADATA)
#   dispatch-restic-backup.service          daily 03:30 (24h)          25.0h    24h + 1h margin
#   dispatch-retro-learning.service         daily 04:30 (24h)          25.0h    24h + 1h margin
#   dispatch-downstream-crosscheck.service  every 5 min                1.0h     12x cadence stall-net (watchdog runs 4h)
#   dispatch-liveness-probe.service         every 2 min                1.0h     30x cadence stall-net
#
# The (unmodified) watchdog reads thresholds from the ledger's expected_max_silence_h.
# These reach the ledger via record_run_success/_failure backfill (per success tick) or
# the `--sync-thresholds` CLI at deploy. Keep values consistent with
# alert_onfailure._UNIT_METADATA (watchdog's secondary fallback).
_DEFAULT_STALE_THRESHOLDS_H: dict[str, float] = {
    "dispatch-cod-weekly.service": 192.0,
    "dispatch-cod-panel-ingest.service": 192.0,
    "dispatch-faza7-kpi.service": 25.0,
    "dispatch-restic-backup.service": 25.0,
    "dispatch-retro-learning.service": 25.0,
    "dispatch-downstream-crosscheck.service": 1.0,
    "dispatch-liveness-probe.service": 1.0,
}


def default_threshold_for(unit: str) -> float | None:
    """Canonical stale threshold [h] for a registered unit, else None.

    Public so the read-only dry-run scan resolves the exact value that
    record_run_success / --sync-thresholds writes into the ledger.
    """
    return _DEFAULT_STALE_THRESHOLDS_H.get(unit)


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
            "expected_max_silence_h": default_threshold_for(unit),
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
        elif entry.get("expected_max_silence_h") is None:
            # Backfill from canonical registry so the (unmodified) watchdog can
            # stale-check this unit. record_oneshot_success.sh passes no threshold,
            # so this is how ExecStopPost ticks populate expected_max_silence_h.
            _default = default_threshold_for(unit)
            if _default is not None:
                entry["expected_max_silence_h"] = _default
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
        if entry.get("expected_max_silence_h") is None:
            # Backfill threshold even on the failure path so a unit that only ever
            # fails (never records success) is still stale-checkable by the watchdog.
            _default = default_threshold_for(unit)
            if _default is not None:
                entry["expected_max_silence_h"] = _default
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


def sync_thresholds(path: Path | None = None) -> list[str]:
    """Write canonical registry thresholds into the ledger (idempotent).

    Ensures every unit in _DEFAULT_STALE_THRESHOLDS_H exists in the ledger with its
    expected_max_silence_h set, so the unmodified watchdog (which reads the ledger)
    can stale-check it. Threshold-only: never seeds last_success/last_failure, so it
    cannot mask a real failure or manufacture a false success. Returns units changed.

    Deploy step:
        python -m dispatch_v2.observability.cron_health --sync-thresholds
    """
    changed: list[str] = []

    def _mut(data: dict[str, Any]) -> dict[str, Any]:
        for unit, thr in _DEFAULT_STALE_THRESHOLDS_H.items():
            existed = unit in data["units"]
            entry = _ensure_unit(data, unit, unit_type="cron_timer")
            if not existed:
                entry["expected_max_silence_h"] = thr
                entry["last_updated"] = _now_iso()
                changed.append(f"{unit} (registered)")
            elif entry.get("expected_max_silence_h") != thr:
                entry["expected_max_silence_h"] = thr
                entry["last_updated"] = _now_iso()
                changed.append(f"{unit} (threshold set)")
        return data

    _locked_rmw(path, _mut)
    return changed


def scan_stale(
    now: datetime | None = None,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    """Read-only preview of what the watchdog would flag stale (no writes, no Telegram).

    For every registered non-long-running unit, resolve the threshold the same way
    watchdog.run_once will after deploy (ledger value → canonical registry →
    alert_onfailure._UNIT_METADATA) and compute staleness. One row per unit.
    """
    now_dt = now or datetime.now(timezone.utc)
    data = load_health(path)

    # Mirror watchdog.run_once secondary fallback. Import is side-effect free.
    try:
        from dispatch_v2.observability.alert_onfailure import _UNIT_METADATA
    except Exception:  # pragma: no cover - defensive
        _UNIT_METADATA = {}

    rows: list[dict[str, Any]] = []
    for unit in sorted(data["units"]):
        entry = data["units"][unit]
        if entry.get("type") == "long_running":
            continue

        thr = entry.get("expected_max_silence_h")
        source = "ledger"
        if thr is None:
            thr = default_threshold_for(unit)
            source = "registry"
        if thr is None:
            thr = _UNIT_METADATA.get(unit, {}).get("expected_max_silence_h")
            source = "metadata"

        ls = entry.get("last_success") or entry.get("last_updated")
        hours_silent: float | None = None
        if ls:
            try:
                dt = datetime.fromisoformat(ls)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                hours_silent = (now_dt - dt).total_seconds() / 3600.0
            except (ValueError, TypeError):
                hours_silent = None

        stale = (
            is_stale(unit, expected_max_silence_h=thr, now=now_dt, path=path)
            if thr is not None
            else False
        )
        rows.append({
            "unit": unit,
            "threshold_h": thr,
            "source": source if thr is not None else "none",
            "stale": stale,
            "hours_silent": hours_silent,
            "status": entry.get("status"),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint: python -m dispatch_v2.observability.cron_health <action>."""
    parser = argparse.ArgumentParser(
        prog="python -m dispatch_v2.observability.cron_health",
        description="cron_health ledger CLI — record success, sync thresholds, dry-run stale scan.",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--record-success", metavar="UNIT",
        help="Mark UNIT as succeeded now (oneshot ExecStartPost/ExecStopPost; idempotent).",
    )
    group.add_argument(
        "--sync-thresholds", action="store_true",
        help="Write canonical registry thresholds into the ledger (idempotent, deploy step).",
    )
    group.add_argument(
        "--dry-run", action="store_true",
        help="Read-only preview of watchdog stale verdicts (no writes, no alerts).",
    )
    parser.add_argument("--type", default="cron_timer", help="unit_type for --record-success.")
    parser.add_argument("--threshold", type=float, default=None,
                        help="explicit expected_max_silence_h for --record-success.")
    parser.add_argument("--path", default=None, help="override cron_health.json path.")
    args = parser.parse_args(argv)

    path = Path(args.path) if args.path else None

    if args.record_success:
        record_run_success(
            args.record_success, unit_type=args.type,
            expected_max_silence_h=args.threshold, path=path,
        )
        print(f"[cron_health] recorded success: {args.record_success}", file=sys.stderr)
        return 0

    if args.sync_thresholds:
        changed = sync_thresholds(path=path)
        print(
            f"[cron_health] sync-thresholds: {len(changed)} changed: "
            f"{', '.join(changed) if changed else '(none — already in sync)'}",
            file=sys.stderr,
        )
        return 0

    if args.dry_run:
        rows = scan_stale(path=path)
        stale = [r for r in rows if r["stale"]]
        no_thr = [r for r in rows if r["threshold_h"] is None]
        print(f"[cron_health --dry-run] checked={len(rows)} "
              f"would_alert_stale={len(stale)} no_threshold={len(no_thr)}")
        for r in sorted(rows, key=lambda x: (not x["stale"], x["unit"])):
            flag = "STALE" if r["stale"] else "ok   "
            hrs = f"{r['hours_silent']:.1f}h" if r["hours_silent"] is not None else "?"
            thr = str(r["threshold_h"]) if r["threshold_h"] is not None else "None"
            print(f"  {flag} {r['unit']:44} silent={hrs:>8} thr={thr:>6} "
                  f"({r['source']}) status={r['status']}")
        return 0

    return 2  # pragma: no cover - argparse enforces a required action


if __name__ == "__main__":
    sys.exit(main())
