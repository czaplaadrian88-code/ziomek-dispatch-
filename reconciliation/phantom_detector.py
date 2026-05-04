"""Phantom/Ghost detection — pure functions, zero side effects.

Compares events.db (last event per order) z orders_state.json (panel reality).

Discrepancy classification:
  PHANTOM: events.db ostatnio active (ASSIGNED/PICKED_UP), ale state mówi
           terminal lub state.json NIE zawiera ordera (cleaned).
  GHOST:   events.db ostatnio terminal (DELIVERED/RETURNED), ale state.json
           mówi active (assigned/picked_up).

Outputs:
  list[dict] z polami:
    order_id, courier_id, last_event_type, last_event_ts, last_event_age_h,
    state_status (None gdy missing), classification (PHANTOM|GHOST),
    inferred_terminal_event ("COURIER_DELIVERED"|"ORDER_RETURNED_TO_POOL"|None)
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Status sets ze state_machine
TERMINAL_STATE_STATUSES = frozenset({"delivered", "cancelled", "returned_to_pool"})
ACTIVE_STATE_STATUSES = frozenset({"assigned", "picked_up"})

# Event types ze events.db
ACTIVE_EVENT_TYPES = frozenset({"COURIER_ASSIGNED", "COURIER_PICKED_UP"})
TERMINAL_EVENT_TYPES = frozenset({"COURIER_DELIVERED", "ORDER_RETURNED_TO_POOL"})


def _parse_ts(ts: str) -> Optional[datetime]:
    """Parse ISO-8601 timestamp (Z or +00:00). Returns None on failure."""
    if not ts:
        return None
    try:
        s = ts.replace("Z", "+00:00") if ts.endswith("Z") else ts
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def get_last_events_per_order(
    db_path: str,
    since_dt: Optional[datetime] = None,
) -> Dict[str, Tuple[str, Optional[str], str]]:
    """Return {order_id: (event_type, courier_id, created_at_iso)} dla last event per oid.

    since_dt limit: tylko orderery z events od tego czasu (default: brak limitu).

    Z3: read-only, idempotent, no caching (fresh każde wywołanie).
    """
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        if since_dt:
            since_iso = since_dt.astimezone(timezone.utc).isoformat()
            rows = conn.execute(
                "SELECT order_id, event_type, courier_id, created_at "
                "FROM events WHERE created_at >= ? ORDER BY order_id, created_at",
                (since_iso,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT order_id, event_type, courier_id, created_at "
                "FROM events ORDER BY order_id, created_at"
            ).fetchall()
    finally:
        conn.close()

    last: Dict[str, Tuple[str, Optional[str], str]] = {}
    for oid, et, cid, ts in rows:
        last[oid] = (et, cid, ts)
    return last


def classify_discrepancy(
    last_event: Tuple[str, Optional[str], str],
    state_record: Optional[Dict[str, Any]],
    now_dt: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Classify single order. Returns None gdy NIE ma rozjazdu.

    Inputs:
      last_event: (event_type, courier_id, created_at_iso) z events.db
      state_record: dict z orders_state.json lub None gdy missing
      now_dt: testable (default: datetime.now(utc))
    """
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    et, cid, ts_iso = last_event
    last_dt = _parse_ts(ts_iso)
    age_h = (now_dt - last_dt).total_seconds() / 3600.0 if last_dt else None

    # --- Case 1: PHANTOM detection ---
    # events.db active + state mówi terminal/missing
    if et in ACTIVE_EVENT_TYPES:
        if state_record is None:
            # Missing from state — assume delivered (97% empirical pattern)
            return {
                "order_id": None,  # caller fills
                "courier_id": cid,
                "last_event_type": et,
                "last_event_ts": ts_iso,
                "last_event_age_h": age_h,
                "state_status": None,
                "classification": "PHANTOM",
                "phantom_subtype": "MISSING_FROM_STATE",
                "inferred_terminal_event": "COURIER_DELIVERED",
                "inferred_reason": "state_missing_assume_delivered",
            }
        state_status = state_record.get("status")
        if state_status in TERMINAL_STATE_STATUSES:
            # State says terminal, events.db says active → mismatch
            inferred = (
                "COURIER_DELIVERED" if state_status == "delivered"
                else "ORDER_RETURNED_TO_POOL"  # cancelled or returned_to_pool
            )
            return {
                "order_id": None,
                "courier_id": cid,
                "last_event_type": et,
                "last_event_ts": ts_iso,
                "last_event_age_h": age_h,
                "state_status": state_status,
                "classification": "PHANTOM",
                "phantom_subtype": "STATE_TERMINAL",
                "inferred_terminal_event": inferred,
                "inferred_reason": f"state_status={state_status}",
            }
        # state active too — no mismatch
        return None

    # --- Case 2: GHOST detection ---
    # events.db terminal + state mówi active
    if et in TERMINAL_EVENT_TYPES:
        if state_record is None:
            return None  # both terminal-ish, no mismatch
        state_status = state_record.get("status")
        if state_status in ACTIVE_STATE_STATUSES:
            return {
                "order_id": None,
                "courier_id": cid,
                "last_event_type": et,
                "last_event_ts": ts_iso,
                "last_event_age_h": age_h,
                "state_status": state_status,
                "classification": "GHOST",
                "phantom_subtype": None,
                "inferred_terminal_event": None,  # ghost never auto-resync
                "inferred_reason": "events_terminal_state_active",
            }
        return None

    # --- Other event types: NEW_ORDER, CZAS_KURIERA_UPDATED, etc. ---
    # NIE są terminal NIE active — skip (e.g., NEW_ORDER bez follow-up COURIER_ASSIGNED)
    return None


def detect_all(
    events_db_path: str,
    orders_state: Dict[str, Dict[str, Any]],
    since_days: int = 30,
    now_dt: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Pełna detekcja — zwraca listę discrepancies sortowaną age desc.

    since_days: limit jak głęboko w events.db szukamy (default 30 dni — pokrywa
    typowy phantom horizon ale NIE zalewa cały events.db).
    """
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)

    since_dt = now_dt - timedelta(days=since_days)
    last = get_last_events_per_order(events_db_path, since_dt=since_dt)

    out = []
    for oid, evt_tuple in last.items():
        state_rec = orders_state.get(oid)
        verdict = classify_discrepancy(evt_tuple, state_rec, now_dt=now_dt)
        if verdict is not None:
            verdict["order_id"] = oid
            out.append(verdict)

    # Sort by age desc (oldest first → safer to auto-resync first)
    out.sort(key=lambda v: -(v["last_event_age_h"] or 0))
    return out
