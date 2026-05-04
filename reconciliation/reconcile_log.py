"""Reconciliation structured logging — JSONL append-only z fsync.

Loguje per discrepancy + run summary. Dla audit trail + downstream analysis.

Schema per discrepancy line:
  {
    "ts": "2026-05-04T...+00:00",
    "run_id": "reconcile_<unix_ms>",
    "type": "PHANTOM" | "GHOST",
    "order_id": "470515",
    "courier_id": "393",
    "last_event_type": "COURIER_ASSIGNED",
    "last_event_age_h": 540.2,
    "state_status": "delivered" | null,
    "action": "resynced" | "alert_only_young" | "alert_only_ghost" | "would_resync_dry_run" |
              "skipped_dedup" | "alert_only_hard_cap_exceeded" | "emit_failed",
    "inferred_terminal_event": "COURIER_DELIVERED" | "ORDER_RETURNED_TO_POOL" | null,
    "emitted_event_id": "470515_COURIER_DELIVERED_phantom_resync" | null,
    "error": null | string
  }

Z3 atomic write: each line append + fsync (fcntl.flock dla multi-proc safety
gdy ktoś uruchomi reconcile równolegle z innym).
"""
from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


DEFAULT_LOG_PATH = Path("/root/.openclaw/workspace/dispatch_state/reconciliation_log.jsonl")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_run_id() -> str:
    return f"reconcile_{int(time.time() * 1000)}"


def append_records(
    records: List[Dict[str, Any]],
    log_path: Optional[Path] = None,
) -> int:
    """Append records do JSONL. Returns number written.

    Atomic: open append + flock LOCK_EX → write all → fsync → close.
    Multi-proc safe (kernel-level lock).
    """
    if log_path is None:
        log_path = DEFAULT_LOG_PATH
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not records:
        return 0

    written = 0
    with open(log_path, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                written += 1
            f.flush()
            os.fsync(f.fileno())
        finally:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
    return written


def build_records(
    actions: List[Dict[str, Any]],
    run_id: str,
    counts: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Convert auto_resync actions into structured log records."""
    ts = _now_iso()
    records = []
    for a in actions:
        rec = {
            "ts": ts,
            "run_id": run_id,
            "type": a.get("classification"),
            "order_id": a.get("order_id"),
            "courier_id": a.get("courier_id"),
            "last_event_type": a.get("last_event_type"),
            "last_event_ts": a.get("last_event_ts"),
            "last_event_age_h": a.get("last_event_age_h"),
            "state_status": a.get("state_status"),
            "phantom_subtype": a.get("phantom_subtype"),
            "action": a.get("action"),
            "inferred_terminal_event": a.get("inferred_terminal_event"),
            "inferred_reason": a.get("inferred_reason"),
            "emitted_event_id": a.get("emitted_event_id"),
            "would_emit": a.get("would_emit"),
            "error": a.get("error"),
        }
        records.append(rec)
    # Append run_summary record at end
    records.append({
        "ts": ts,
        "run_id": run_id,
        "type": "RUN_SUMMARY",
        "counts": counts,
    })
    return records


def query_recent_summary(
    log_path: Optional[Path] = None,
    hours: int = 24,
) -> Dict[str, Any]:
    """Aggregate counts dla health endpoint. Returns last hours stats."""
    if log_path is None:
        log_path = DEFAULT_LOG_PATH
    log_path = Path(log_path)
    summary = {
        "last_run_ts": None,
        "discrepancies_24h": {
            "phantoms": 0,
            "ghosts": 0,
            "auto_resyncs": 0,
            "manual_alerts": 0,
            "hard_cap_hits": 0,
        },
        "status": "ok",
    }
    if not log_path.exists():
        return summary

    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    last_ts = None
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for ln in f:
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                ts_str = rec.get("ts", "")
                try:
                    ts_dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts_dt.tzinfo is None:
                        ts_dt = ts_dt.replace(tzinfo=timezone.utc)
                    ts_unix = ts_dt.timestamp()
                except (ValueError, TypeError):
                    continue
                if ts_unix < cutoff:
                    continue
                if last_ts is None or ts_unix > last_ts:
                    last_ts = ts_unix
                    summary["last_run_ts"] = ts_str
                t = rec.get("type")
                if t == "PHANTOM":
                    summary["discrepancies_24h"]["phantoms"] += 1
                    if rec.get("action") == "resynced":
                        summary["discrepancies_24h"]["auto_resyncs"] += 1
                    elif rec.get("action", "").startswith("alert_only"):
                        summary["discrepancies_24h"]["manual_alerts"] += 1
                elif t == "GHOST":
                    summary["discrepancies_24h"]["ghosts"] += 1
                    summary["discrepancies_24h"]["manual_alerts"] += 1
                elif t == "RUN_SUMMARY":
                    counts = rec.get("counts", {})
                    if counts.get("hard_cap_hit"):
                        summary["discrepancies_24h"]["hard_cap_hits"] += 1
    except Exception:
        summary["status"] = "degraded"
        return summary

    # Status classification
    d = summary["discrepancies_24h"]
    if d["hard_cap_hits"] > 0:
        summary["status"] = "critical"
    elif d["ghosts"] > 0 or d["manual_alerts"] > 5:
        summary["status"] = "degraded"
    return summary
