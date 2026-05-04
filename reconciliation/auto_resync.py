"""Safety-gated auto-resync — emit terminal events dla phantom orders.

Hard rules (Z3):
  - Tylko PHANTOM (events.db active + state terminal/missing) — GHOST never auto.
  - Age threshold (default 4h): phantom OLDER than threshold → auto-resync.
                                 Phantom YOUNGER → alert only (manual review).
  - Hard cap per run (default 5): jeśli >cap phantoms eligible → STOP, alert critical.
                                  Defensive: prevents auto-massacre events.db przy bigger bug.
  - Idempotent: emit() używa deterministic event_id, double-call = no-op.

Inputs:
  discrepancies: list[dict] z phantom_detector.detect_all()
  emit_fn: callable(event_type, order_id, courier_id, payload, event_id) → event_id|None
  state_update_fn: callable(event_dict) → state_record|None  (consume event)

Returns:
  dict z aggregate counts: auto_resyncs, alerts_only, ghosts, skipped_young, hard_cap_hit
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


def auto_resync_phantoms(
    discrepancies: List[Dict[str, Any]],
    emit_fn: Callable[..., Optional[str]],
    state_update_fn: Callable[[Dict[str, Any]], Any],
    age_threshold_hours: float = 4.0,
    hard_cap_per_run: int = 5,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Resync phantoms (events.db terminal events) z safety gates.

    dry_run: jeśli True, NIC NIE emituje — tylko zwraca co BY zrobione.
             Used dla pierwszego deploy (AUTO_RESYNC_ENABLED=false alert-only).

    Returns aggregate stats + per-order action records (dla reconcile_log).
    """
    counts = {
        "phantoms_total": 0,
        "ghosts_total": 0,
        "auto_resyncs": 0,
        "alerts_only_young": 0,    # phantom <4h, manual review needed
        "alerts_only_hard_cap": 0,  # eligible >cap → safety stop
        "skipped_ghost": 0,         # ghost — never auto-resync
        "hard_cap_hit": False,
        "dry_run": dry_run,
    }
    actions: List[Dict[str, Any]] = []
    eligible_for_auto: List[Dict[str, Any]] = []

    # First pass: classify into eligible/young/ghost
    for d in discrepancies:
        cls = d.get("classification")
        if cls == "GHOST":
            counts["ghosts_total"] += 1
            counts["skipped_ghost"] += 1
            actions.append({
                **d,
                "action": "alert_only_ghost",
            })
            continue
        if cls != "PHANTOM":
            continue  # unknown — skip defensively
        counts["phantoms_total"] += 1
        age_h = d.get("last_event_age_h") or 0
        if age_h < age_threshold_hours:
            counts["alerts_only_young"] += 1
            actions.append({
                **d,
                "action": "alert_only_young",
            })
            continue
        eligible_for_auto.append(d)

    # Hard cap defense — prevent auto-massacre on bigger bug
    if len(eligible_for_auto) > hard_cap_per_run:
        counts["hard_cap_hit"] = True
        for d in eligible_for_auto:
            actions.append({
                **d,
                "action": "alert_only_hard_cap_exceeded",
            })
        counts["alerts_only_hard_cap"] = len(eligible_for_auto)
        return {"counts": counts, "actions": actions}

    # Second pass: emit terminal events for eligible phantoms
    for d in eligible_for_auto:
        oid = d["order_id"]
        cid = d.get("courier_id")
        inferred = d["inferred_terminal_event"]
        reason = d.get("inferred_reason", "phantom_resync")

        if dry_run:
            actions.append({
                **d,
                "action": "would_resync_dry_run",
                "would_emit": inferred,
            })
            continue

        # Build deterministic event_id — phantom_resync namespace
        event_id = f"{oid}_{inferred}_phantom_resync"

        # Build payload
        if inferred == "COURIER_DELIVERED":
            payload = {
                "timestamp": d.get("last_event_ts"),  # use last known event ts as proxy
                "source": "reconciliation_inferred",
                "phantom_age_h": d.get("last_event_age_h"),
                "inferred_reason": reason,
            }
        else:  # ORDER_RETURNED_TO_POOL
            # Map state_status → reason
            ss = d.get("state_status") or ""
            r = "cancelled" if ss == "cancelled" else (
                "returned_to_pool" if ss == "returned_to_pool" else "undelivered"
            )
            payload = {
                "reason": r,
                "source": "reconciliation_inferred",
                "phantom_age_h": d.get("last_event_age_h"),
            }

        try:
            ev = emit_fn(
                event_type=inferred,
                order_id=oid,
                courier_id=cid,
                payload=payload,
                event_id=event_id,
            )
        except Exception as e:
            actions.append({
                **d,
                "action": "emit_failed",
                "error": f"{type(e).__name__}: {e}",
            })
            continue

        if not ev:
            # Idempotent dedup — already emitted in prior run
            actions.append({
                **d,
                "action": "skipped_dedup",
            })
            continue

        # Update state machine
        try:
            state_update_fn({
                "event_type": inferred,
                "order_id": oid,
                "courier_id": cid,
                "payload": payload,
            })
        except Exception as e:
            # Event emitted but state update failed — LOG but count as resync
            # (next reconcile cycle will see consistent state)
            actions.append({
                **d,
                "action": "resynced_state_update_failed",
                "error": f"{type(e).__name__}: {e}",
                "emitted_event_id": ev,
            })
            counts["auto_resyncs"] += 1
            continue

        actions.append({
            **d,
            "action": "resynced",
            "emitted_event_id": ev,
        })
        counts["auto_resyncs"] += 1

    return {"counts": counts, "actions": actions}
