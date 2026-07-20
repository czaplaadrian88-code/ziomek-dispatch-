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
              "skipped_dedup" | "skipped_superseded" | "state_update_failed" |
              "resynced_downstream_pending" |
              "resynced_downstream_already_applied" | "durable_apply_failed" |
              "alert_only_hard_cap_exceeded" | "emit_failed",
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

# Self-heal (2026-05-31): okno 24h liczy GHOSTy z historycznego logu bez sprawdzenia
# czy rozjazd NADAL istnieje. Pojedynczy przejściowy ghost (TOCTOU race w jednym runie
# reconcile — worker wczytał state PRZED zapisem `delivered`) trzymał downstream_status
# degraded przez pełne 24h → godzinny Telegram spam. Self-heal re-waliduje każdy
# policzony ghost przeciw bieżącemu orders_state.json; rozwiązany → nie liczy.
DEFAULT_ORDERS_STATE_PATH = Path("/root/.openclaw/workspace/dispatch_state/orders_state.json")
# Ghost = events.db terminal + state aktywny. Rozwiązany iff bieżący state NIE jest aktywny
# (dogonił do terminal) ALBO order zniknął ze state (oba → classify zwróciłby None).
_ACTIVE_STATE_STATUSES = frozenset({"assigned", "picked_up"})


def _self_heal_enabled() -> bool:
    """Hot-reload flag gate. Fail-open (default True) gdy common niedostępny."""
    try:
        from dispatch_v2.common import flag as _flag
        return _flag("RECONCILIATION_HEALTH_SELF_HEAL", True)
    except Exception:
        return True


def _load_orders_state_snapshot(path: Path) -> Optional[Dict[str, Any]]:
    """Świeży odczyt orders_state.json dla self-heal. None = nie mogę zweryfikować
    (brak pliku/parse fail) → caller zachowawczo NIE self-heal'uje (nie maskuj real)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _resolved_ghost_oids(
    ghost_oids: set,
    orders_state: Dict[str, Any],
) -> set:
    """Zwraca podzbiór ghost oidów które już NIE są rozjazdem wg bieżącego stanu.

    Ghost ma events terminal (append-only → zostaje terminal) + state aktywny.
    Resolved iff: order zniknął ze state (None) LUB status nie jest już aktywny.
    Syntetyczne `__none_*` oidy (order_id=None) pomijamy — nie da się zweryfikować.
    """
    resolved = set()
    for oid in ghost_oids:
        if isinstance(oid, str) and oid.startswith("__none_"):
            continue
        rec = orders_state.get(oid)
        if rec is None:
            resolved.add(oid)
            continue
        if rec.get("status") not in _ACTIVE_STATE_STATUSES:
            resolved.add(oid)
    return resolved


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
    self_heal: Optional[bool] = None,
    orders_state_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Aggregate counts dla health endpoint. Returns last hours stats.

    self_heal: None → czytaj flagę RECONCILIATION_HEALTH_SELF_HEAL (default True);
               explicit bool override (dla testów). Gdy True, GHOSTy które dogoniły
               do terminal w bieżącym orders_state.json NIE są liczone (eliminuje
               degraded od przejściowego TOCTOU race). Manual_alerts pochodzące
               z tych ghostów też odpadają (GHOST liczy się w obu zbiorach).
    orders_state_path: None → domyślny path (override dla testów).
    """
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
    # E3b follow-up (Lekcja #153): licz DISTINCT order_id, NIE detection-events.
    # Reconciliation re-loguje ten sam nierozwiazany order co run; per-event liczenie
    # pozwolilo jednemu young phantom (re-detected 8x w incydencie 476621) przebic
    # manual_alerts>5 => falszywy degraded. Dedupe = "ile ROZNYCH orderow wymaga uwagi".
    phantom_oids = set()
    resync_oids = set()
    manual_alert_oids = set()
    ghost_oids = set()
    hard_cap_hits = 0
    none_seq = 0  # order_id=None trzymamy distinct (defensive — NIE maskuj real degraded)
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
                oid = rec.get("order_id")
                if oid is None:
                    oid = f"__none_{none_seq}"
                    none_seq += 1
                if t == "PHANTOM":
                    phantom_oids.add(oid)
                    # Durable resync ma trzy jawne warianty wyniku. Wszystkie
                    # oznaczaja, ze state zostal naprawiony; suffix opisuje tylko
                    # stan drugiej fazy downstream, nie zmienia klasy health.
                    if rec.get("action") in {
                        "resynced",
                        "resynced_downstream_pending",
                        "resynced_downstream_already_applied",
                    }:
                        resync_oids.add(oid)
                    elif rec.get("action", "").startswith("alert_only"):
                        manual_alert_oids.add(oid)
                elif t == "GHOST":
                    ghost_oids.add(oid)
                    manual_alert_oids.add(oid)
                elif t == "RUN_SUMMARY":
                    counts = rec.get("counts", {})
                    if counts.get("hard_cap_hit"):
                        hard_cap_hits += 1
    except Exception:
        summary["status"] = "degraded"
        return summary

    # Self-heal (2026-05-31): odrzuć GHOSTy które już dogoniły do terminal w bieżącym
    # stanie (przejściowy TOCTOU race). Zachowawczo: gdy nie mogę wczytać state → skip.
    do_self_heal = _self_heal_enabled() if self_heal is None else self_heal
    if do_self_heal and ghost_oids:
        os_path = orders_state_path or DEFAULT_ORDERS_STATE_PATH
        orders_state = _load_orders_state_snapshot(os_path)
        if orders_state is not None:
            resolved = _resolved_ghost_oids(ghost_oids, orders_state)
            if resolved:
                ghost_oids -= resolved
                manual_alert_oids -= resolved

    d = summary["discrepancies_24h"]
    d["phantoms"] = len(phantom_oids)
    d["auto_resyncs"] = len(resync_oids)
    d["manual_alerts"] = len(manual_alert_oids)
    d["ghosts"] = len(ghost_oids)
    d["hard_cap_hits"] = hard_cap_hits

    # Status classification
    if d["hard_cap_hits"] > 0:
        summary["status"] = "critical"
    elif d["ghosts"] > 0 or d["manual_alerts"] > 5:
        summary["status"] = "degraded"
    return summary
