"""R2 proposal freshness: assignment-time measurement truth.

The hot path is deliberately split into prepare -> durable assignment -> commit:

* ``prepare_assignment_episode`` runs the canonical selector against the
  dispatchable fleet immediately before the assignment state mutation.
* ``commit_assignment_episode`` appends only while holding the lifecycle lock
  and only when the exact assignment generation still owns the order.

Both callers are fail-safe.  This module never changes an assignment, a pending
proposal, console output, or Telegram output.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver
from dispatch_v2 import state_machine
from dispatch_v2.c7_normal_path import _default_code_sha
from dispatch_v2.core.decide import decide
from dispatch_v2.core.jsonl_appender import append_jsonl_once
from dispatch_v2.core.world_state import WorldState

_log = logging.getLogger("proposal_freshness")

FLAG = "ENABLE_ASSIGNMENT_EPISODE_LOG"
SCHEMA = "assignment_episode.v1"
ASSIGNMENT_EPISODE_PATH = C.STATE_DIR / "assignment_episode.jsonl"

# One canonical allowlist for state -> decision-event projections.  It excludes
# prior proposal/render fields and therefore cannot accidentally reuse the
# NEW_ORDER-time winner.
ORDER_EVENT_FIELDS = (
    "order_id",
    "restaurant",
    "delivery_address",
    "pickup_coords",
    "delivery_coords",
    "czas_kuriera_warsaw",
    "pickup_at_warsaw",
    "pickup_at",
    "address_id",
    "order_type",
    "created_at_utc",
    "created_at",
    "delivery_city",
    "uwagi_pickup_parsed",
    "prep_minutes",
)


def order_event_from_state(record: dict) -> dict:
    """Project only canonical decision inputs; never copy cached proposals."""
    return {
        key: record.get(key)
        for key in ORDER_EVENT_FIELDS
        if record.get(key) is not None
    }


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dispatchable_fleet() -> Dict[str, Any]:
    return {
        str(courier.courier_id): courier
        for courier in courier_resolver.dispatchable_fleet()
    }


def _solve_fresh(order_event: dict, fleet: Dict[str, Any], now: datetime):
    # Assignment asks "who wins now", so the one-time early-bird suppression
    # must not hide a candidate merely because the order is no longer new.
    return decide(
        WorldState(fleet_snapshot=fleet, now=now),
        order_event,
        _bypass_early_bird=True,
    )


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _bag_ids(courier: Any) -> list[str]:
    values = []
    for item in getattr(courier, "bag", None) or []:
        if isinstance(item, dict):
            oid = item.get("order_id") or item.get("zid")
        else:
            oid = getattr(item, "order_id", None)
        if oid not in (None, ""):
            values.append(str(oid))
    return sorted(values)


def _fleet_snapshot(fleet: Dict[str, Any]) -> dict:
    """Return a PII-free short summary plus a signature of decision state."""
    summary = []
    signature_rows = []
    for cid in sorted(str(key) for key in fleet):
        courier = fleet[cid]
        bag_ids = _bag_ids(courier)
        pos = getattr(courier, "pos", None)
        if pos is None:
            lat = getattr(courier, "lat", None)
            lng = getattr(courier, "lng", None)
            pos = (lat, lng) if lat is not None and lng is not None else None
        pos_for_hash = None
        if isinstance(pos, (tuple, list)) and len(pos) >= 2:
            try:
                pos_for_hash = [round(float(pos[0]), 5), round(float(pos[1]), 5)]
            except (TypeError, ValueError):
                pos_for_hash = None
        row = {
            "cid": cid,
            "bag_size": len(bag_ids),
            "pos_source": str(getattr(courier, "pos_source", "none") or "none"),
        }
        summary.append(row)
        signature_rows.append(
            {
                **row,
                "bag_order_ids": bag_ids,
                "position": pos_for_hash,
                "pos_age_min": getattr(courier, "pos_age_min", None),
                "shift_start": _iso(getattr(courier, "shift_start", None)),
                "shift_end": _iso(getattr(courier, "shift_end", None)),
            }
        )
    packed = json.dumps(
        signature_rows, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return {
        "available_count": len(summary),
        "available_cids": [row["cid"] for row in summary],
        "couriers": summary,
        "generation": "sha256:" + hashlib.sha256(packed).hexdigest(),
    }


def _candidate_summary(result: Any) -> dict:
    best = getattr(result, "best", None)
    winner = (
        str(getattr(best, "courier_id"))
        if best is not None and getattr(best, "courier_id", None) is not None
        else None
    )
    runner = None
    for candidate in getattr(result, "candidates", None) or []:
        cid = getattr(candidate, "courier_id", None)
        if cid is not None and str(cid) != str(winner):
            runner = candidate
            break
    winner_score = getattr(best, "score", None) if best is not None else None
    runner_score = getattr(runner, "score", None) if runner is not None else None
    margin = None
    if winner_score is not None and runner_score is not None:
        margin = round(float(winner_score) - float(runner_score), 3)
    return {
        "winner_cid": winner,
        "runner_up_cid": (
            str(getattr(runner, "courier_id")) if runner is not None else None
        ),
        "winner_score": (
            round(float(winner_score), 3) if winner_score is not None else None
        ),
        "runner_up_score": (
            round(float(runner_score), 3) if runner_score is not None else None
        ),
        "score_margin": margin,
        "verdict": str(getattr(result, "verdict", "UNKNOWN")),
        "routing": str(getattr(result, "auto_route", "ACK")),
        "pool_total": int(getattr(result, "pool_total_count", 0) or 0),
        "pool_feasible": int(getattr(result, "pool_feasible_count", 0) or 0),
        "selection_scope": (
            "full_pool_pre_top_n"
            if getattr(result, "full_pool_candidates", None) is not None
            else "canonical_top_n"
        ),
    }


def prepare_assignment_episode(
    order_id: str,
    assignment_payload: Optional[dict],
    *,
    assignment_observed_at: Optional[datetime] = None,
    expected_assignment_event_id: Optional[str] = None,
) -> Optional[dict]:
    """Compute the proposal from current state and current dispatchable fleet."""
    now = assignment_observed_at or _utc_now()
    current = state_machine.get_order_strict(str(order_id)) or {}
    # A durable foreground retry after the exact assignment was already
    # committed is not a new assignment-time observation.  Do not backfill it
    # with a later fleet and falsely label that result as assignment-time truth.
    if (
        expected_assignment_event_id
        and str(current.get("assignment_event_id") or "")
        == str(expected_assignment_event_id)
        and str(
            current.get("last_lifecycle_event_id_courier_assigned") or ""
        )
        == str(expected_assignment_event_id)
    ):
        return None
    merged = dict(current)
    merged.update(assignment_payload or {})
    merged["order_id"] = str(order_id)
    order_event = order_event_from_state(merged)
    fleet = _dispatchable_fleet()
    result = _solve_fresh(order_event, fleet, now)
    return {
        "schema": SCHEMA,
        "order_id": str(order_id),
        "proposal_computed_at": now.isoformat(),
        "fleet": _fleet_snapshot(fleet),
        "proposal": _candidate_summary(result),
        "code_sha": _default_code_sha(),
        "flag_fingerprint": C.flag_fingerprint(),
    }


def _assignment_generation_matches(
    current: Optional[dict], assignment_event_id: str, assigned_cid: str
) -> bool:
    if not isinstance(current, dict):
        return False
    return bool(
        current.get("status") in {"assigned", "picked_up", "en_route_delivery"}
        and str(current.get("courier_id") or "") == str(assigned_cid)
        and str(current.get("assignment_event_id") or "")
        == str(assignment_event_id)
        and str(
            current.get("last_lifecycle_event_id_courier_assigned") or ""
        )
        == str(assignment_event_id)
    )


def commit_assignment_episode(
    prepared: Optional[dict], assignment_event_id: str, assigned_cid: str
) -> bool:
    """CAS the exact state generation and durably append it at most once."""
    if not prepared:
        return False
    order_id = str(prepared.get("order_id") or "")
    if not order_id or not assignment_event_id or assigned_cid in (None, ""):
        return False
    with state_machine.lifecycle_apply_lock():
        current = state_machine.get_order_strict(order_id)
        if not _assignment_generation_matches(
            current, str(assignment_event_id), str(assigned_cid)
        ):
            return False
        record = dict(prepared)
        proposal = record.get("proposal") or {}
        winner_cid = proposal.get("winner_cid")
        record.update(
            {
                "assignment_at": current.get("assigned_at"),
                "assignment_generation": str(assignment_event_id),
                "actual_assigned_cid": str(assigned_cid),
                "agreement": (
                    winner_cid is not None
                    and str(winner_cid) == str(assigned_cid)
                ),
                "cas": {
                    "matched": True,
                    "state_assignment_event_id": str(
                        current.get("assignment_event_id")
                    ),
                    "state_lifecycle_marker": str(
                        current.get(
                            "last_lifecycle_event_id_courier_assigned"
                        )
                    ),
                },
                "recorded_at": _utc_now().isoformat(),
            }
        )
        return append_jsonl_once(
            Path(ASSIGNMENT_EPISODE_PATH),
            record,
            dedupe_key="assignment_generation",
            dedupe_value=str(assignment_event_id),
            scan_rotated=True,
        )
