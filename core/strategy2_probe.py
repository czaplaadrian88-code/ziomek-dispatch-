"""Czysty rdzeń sondy Strategii 2: sloty +5 do created_at+90 × cała flota."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from dispatch_v2.core import carry_freshness as _carry_contract

SCHEMA = "strategy2_probe.v1"
STEP_MIN = 5
HORIZON_MIN = 90


def classify_courier_status(
    feasibility_verdict: Any,
    hard35_status: Any,
) -> str:
    """Jedyny owner terminalnego statusu kuriera w slocie S2."""
    verdict = str(feasibility_verdict or "")
    carry = str(hard35_status or "")
    if verdict == "NO":
        # Inny HARD jest kompletnym dowodem braku bezpiecznego planu; pomiar
        # carry nie jest wtedy wymagany i nie może unieważniać całego slotu.
        return "NO_SAFE_PLAN"
    if verdict != "MAYBE":
        return "UNEVALUABLE"
    if carry in {
        _carry_contract.HARD35_LE35,
        _carry_contract.HARD35_EXEMPT,
    }:
        return "SAFE_LE35"
    if carry == _carry_contract.HARD35_OVER35:
        return "NO_SAFE_PLAN"
    return "UNEVALUABLE"


def _complete_courier_result(row: dict) -> bool:
    if not isinstance(row, dict):
        return False
    expected = classify_courier_status(
        row.get("feasibility_verdict"),
        row.get("hard35_status"),
    )
    return expected != "UNEVALUABLE" and row.get("status") == expected


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
    evaluate_slot: Callable[[datetime, list[str]], dict[str, Any]],
) -> dict:
    created = _utc(created_at)
    declared = _utc(declared_ready_at)
    current = _utc(now)
    deadline = created + timedelta(minutes=HORIZON_MIN)
    fleet = [str(cid) for cid in fleet_ids]
    slot = max(declared, current) + timedelta(minutes=STEP_MIN)
    evidence = {
        "created_at": created.isoformat(),
        "declared_ready_at": declared.isoformat(),
        "probe_started_at": current.isoformat(),
        "step_min": STEP_MIN,
        "horizon_min": HORIZON_MIN,
    }
    checked = 0
    evaluations = []
    while slot <= deadline:
        checked += 1
        try:
            slot_result = evaluate_slot(slot, list(fleet))
        except Exception as exc:
            slot_result = {
                "status": "UNEVALUABLE",
                "couriers": [{
                    "courier_id": cid,
                    "status": "UNEVALUABLE",
                    "error_type": type(exc).__name__,
                } for cid in fleet],
            }
        couriers = (
            slot_result.get("couriers")
            if isinstance(slot_result, dict)
            else None
        )
        by_cid = {}
        if isinstance(couriers, list):
            for row in couriers:
                if not isinstance(row, dict):
                    continue
                cid = str(row.get("courier_id") or "")
                if cid and cid not in by_cid:
                    by_cid[cid] = dict(row)
        complete = (
            isinstance(slot_result, dict)
            and slot_result.get("status") == "EVALUATED"
            and set(by_cid) == set(fleet)
            and len(by_cid) == len(fleet)
            and all(
                _complete_courier_result(row)
                for row in by_cid.values()
            )
        )
        ordered_rows = [
            by_cid.get(cid, {
                "courier_id": cid,
                "status": "UNEVALUABLE",
                "error_type": "missing_courier_evaluation",
            })
            for cid in fleet
        ]
        evaluations.append({
            "slot_at": slot.isoformat(),
            "couriers": ordered_rows,
        })
        if not complete:
            return {
                "schema": SCHEMA,
                **evidence,
                "status": "UNEVALUABLE",
                "order_id": str(order_id),
                "found": False,
                "reason": "incomplete_slot_evaluation",
                "slot_at": None,
                "shift_min": None,
                "courier_id": None,
                "feasible_courier_ids": [],
                "fleet_courier_ids": fleet,
                "fleet_count": len(fleet),
                "slots_checked": checked,
                "deadline_at": deadline.isoformat(),
                "evaluations": evaluations,
            }
        feasible = [
            cid for cid in fleet
            if by_cid[cid].get("status") == "SAFE_LE35"
        ]
        if feasible:
            return {
                "schema": SCHEMA,
                **evidence,
                "status": "EVALUATED",
                "order_id": str(order_id),
                "found": True,
                "slot_at": slot.isoformat(),
                "shift_min": round(
                    (slot - declared).total_seconds() / 60.0, 1),
                "courier_id": feasible[0],
                "feasible_courier_ids": feasible,
                "fleet_courier_ids": fleet,
                "fleet_count": len(fleet),
                "slots_checked": checked,
                "deadline_at": deadline.isoformat(),
                "evaluations": evaluations,
            }
        slot += timedelta(minutes=STEP_MIN)
    return {
        "schema": SCHEMA,
        **evidence,
        "status": "EVALUATED",
        "order_id": str(order_id),
        "found": False,
        "slot_at": None,
        "shift_min": None,
        "courier_id": None,
        "feasible_courier_ids": [],
        "fleet_courier_ids": fleet,
        "fleet_count": len(fleet),
        "slots_checked": checked,
        "deadline_at": deadline.isoformat(),
        "evaluations": evaluations,
    }
