"""Własna, rotowana historia opublikowanych cykli live ETA.

To obserwacyjny writer TIME-C, odseparowany od decision_eta_log. Domyślnie OFF;
brak historii nigdy nie wpływa na publikację snapshotu ani decyzję dispatchera.
Rekordy nie zawierają nazw, adresów ani współrzędnych.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from dispatch_v2 import common as C


SCHEMA = "live_eta_history.v1"
FLAG = "ENABLE_LIVE_ETA_HISTORY_LOG"
LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/live_eta_history.jsonl"
)

_log = logging.getLogger("live_eta_history")
_stats_lock = threading.Lock()
_stats = {"written": 0, "errors": 0, "skipped_off": 0}


def _bump(name: str, amount: int = 1) -> None:
    with _stats_lock:
        _stats[name] = int(_stats.get(name, 0)) + int(amount)


def get_stats() -> dict[str, int]:
    with _stats_lock:
        return dict(_stats)


def _iso(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def _records(snapshots: Mapping[str, Mapping[str, object]]) -> list[dict]:
    records: list[dict] = []
    recorded_at = datetime.now(timezone.utc).isoformat()
    for raw_cid, snapshot in sorted(snapshots.items(), key=lambda item: str(item[0])):
        if not isinstance(snapshot, Mapping):
            continue
        courier_id = str(raw_cid)
        generated_at = _iso(snapshot.get("generated_at"))
        for stop in snapshot.get("stops") or []:
            if not isinstance(stop, Mapping):
                continue
            kind = stop.get("kind")
            if kind not in {"pickup", "dropoff"}:
                continue
            stop_id = str(stop.get("stop_id") or "")
            eta_at = _iso(stop.get("eta_at"))
            for raw_order_id in stop.get("order_ids") or []:
                order_id = str(raw_order_id)
                records.append(
                    {
                        "schema": SCHEMA,
                        "recorded_at": recorded_at,
                        "generated_at": generated_at,
                        "cycle_id": snapshot.get("cycle_id"),
                        "courier_id": courier_id,
                        "order_id": order_id,
                        "stop_id": stop_id,
                        "stop_kind": kind,
                        "eta_at": eta_at,
                        "plan_version": snapshot.get("plan_version"),
                        "sequence_hash": snapshot.get("sequence_hash"),
                        "writer": "live_eta_daemon",
                        "writer_role": "authoritative",
                    }
                )
    return records


def record_live_eta_cycle(
    snapshots: Mapping[str, Mapping[str, object]],
) -> bool:
    """Dopisz jeden opublikowany cykl, wyłącznie gdy dedykowana flaga jest ON."""
    try:
        if not C.decision_flag(FLAG):
            _bump("skipped_off")
            return False
        records = _records(snapshots)
        if not records:
            return False
        from dispatch_v2.core.jsonl_appender import append_jsonl_batch

        written = append_jsonl_batch(LOG_PATH, records)
        _bump("written", int(written))
        return True
    except Exception as exc:  # observability cannot invalidate ETA publication
        _bump("errors")
        _log.warning(
            "LIVE_ETA_HISTORY_FAIL error=%s:%s",
            type(exc).__name__,
            exc,
        )
        return False


def _reset_stats_for_tests() -> None:
    with _stats_lock:
        _stats.update({"written": 0, "errors": 0, "skipped_off": 0})
