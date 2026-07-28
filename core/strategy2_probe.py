"""Czysty rdzeń sondy Strategii 2: sloty +5 do created_at+90 × cała flota."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

SCHEMA = "strategy2_probe.v1"
STEP_MIN = 5
HORIZON_MIN = 90


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(
        tzinfo=timezone.utc)


def probe(
    *,
    order_id: str,
    created_at: datetime,
    declared_ready_at: datetime,
    now: datetime,
    fleet_ids: Iterable[str],
    evaluate_slot: Callable[[datetime, list[str]], list[str]],
) -> dict:
    created = _utc(created_at)
    declared = _utc(declared_ready_at)
    current = _utc(now)
    deadline = created + timedelta(minutes=HORIZON_MIN)
    fleet = [str(cid) for cid in fleet_ids]
    slot = max(declared, current) + timedelta(minutes=STEP_MIN)
    checked = 0
    while slot <= deadline:
        checked += 1
        feasible = [
            str(cid) for cid in (evaluate_slot(slot, list(fleet)) or [])
        ]
        if feasible:
            return {
                "schema": SCHEMA,
                "status": "EVALUATED",
                "order_id": str(order_id),
                "found": True,
                "slot_at": slot.isoformat(),
                "shift_min": round(
                    (slot - declared).total_seconds() / 60.0, 1),
                "courier_id": feasible[0],
                "feasible_courier_ids": feasible,
                "fleet_count": len(fleet),
                "slots_checked": checked,
                "deadline_at": deadline.isoformat(),
            }
        slot += timedelta(minutes=STEP_MIN)
    return {
        "schema": SCHEMA,
        "status": "EVALUATED",
        "order_id": str(order_id),
        "found": False,
        "slot_at": None,
        "shift_min": None,
        "courier_id": None,
        "feasible_courier_ids": [],
        "fleet_count": len(fleet),
        "slots_checked": checked,
        "deadline_at": deadline.isoformat(),
    }
