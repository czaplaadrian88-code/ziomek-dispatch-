"""Czysty reducer lifecycle zlecen dla durable outbox A360-E1.

Brak I/O, zegara, flag, geokodu, registry kurierow i efektow zewnetrznych.
Wszystkie czasy pochodza z jednej kanonicznej koperty lub z jej payloadu.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable, Mapping, Optional

from dispatch_v2.event_envelope import EventEnvelope, normalize_created_at
from dispatch_v2.order_fsm import (
    FsmOutcome,
    FsmVerdict,
    parse_order_timestamp,
    rejection_code,
    validate_order_event,
)


DURABLE_RECEIPTS_FIELD = "durable_event_receipts"


class ReductionRejected(ValueError):
    """Bezpieczny blad FSM/reducera, gotowy do failure journal."""

    def __init__(self, error_code: str):
        self.error_code = error_code if error_code in {
            "illegal_transition",
            "stale_event",
            "invalid_timestamp",
            "invalid_payload",
        } else "invalid_payload"
        self.failure_class = (
            "illegal"
            if self.error_code in {"illegal_transition", "stale_event"}
            else "permanent"
        )
        super().__init__(f"order reduction rejected: code={self.error_code}")


class ReceiptCapacityExceeded(RuntimeError):
    failure_class = "transient"
    error_code = "concurrent_state_change"


@dataclass(frozen=True)
class ReductionResult:
    record: Optional[dict[str, Any]]
    verdict: FsmVerdict
    domain_changed: bool
    receipt_changed: bool
    duplicate: bool


_NEW_ORDER_FIELDS = (
    "restaurant",
    "pickup_address",
    "pickup_city",
    "delivery_address",
    "delivery_city",
    "pickup_time_minutes",
    "first_seen",
    "address_id",
    "pickup_coords",
    "delivery_coords",
    "pickup_at_warsaw",
    "prep_minutes",
    "order_type",
    "uwagi",
    "uwagi_pickup_parsed",
    "delivery_deadline_uwagi",
    "czas_kuriera_warsaw",
    "czas_kuriera_hhmm",
    "decision_deadline",
    "zmiana_czasu_odbioru",
    "created_at_utc",
)


def _receipt_ids(record: Optional[Mapping[str, Any]]) -> set[str]:
    if not record:
        return set()
    raw = record.get(DURABLE_RECEIPTS_FIELD)
    if not isinstance(raw, list):
        return set()
    return {
        str(item.get("event_id"))
        for item in raw
        if isinstance(item, Mapping) and item.get("event_id")
    }


def _validate_ck(iso_value: Any, hhmm_value: Any) -> None:
    if iso_value in (None, "") and hhmm_value in (None, ""):
        return
    if iso_value in (None, "") or hhmm_value in (None, ""):
        raise ReductionRejected("invalid_timestamp")
    parsed, _was_naive = parse_order_timestamp(iso_value)
    if parsed is None:
        raise ReductionRejected("invalid_timestamp")
    try:
        # HH:MM dotyczy oryginalnej osi wall-clock, nie znormalizowanego UTC.
        from datetime import datetime

        original = datetime.fromisoformat(str(iso_value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReductionRejected("invalid_timestamp") from exc
    if original.strftime("%H:%M") != str(hhmm_value):
        raise ReductionRejected("invalid_timestamp")


def _event_order(envelope: EventEnvelope) -> tuple[str, str]:
    return (envelope.created_at, envelope.event_id)


def _current_order(record: Optional[Mapping[str, Any]]) -> Optional[tuple[str, str]]:
    if not record:
        return None
    raw = record.get("durable_last_event_order")
    if not isinstance(raw, Mapping):
        return None
    created_at = raw.get("created_at")
    event_id = raw.get("event_id")
    if created_at in (None, "") or event_id in (None, ""):
        return None
    try:
        return (normalize_created_at(created_at), str(event_id))
    except ValueError:
        raise ReductionRejected("invalid_timestamp")


def _append_receipt(
    record: dict[str, Any],
    envelope: EventEnvelope,
    *,
    max_receipts_per_order: int,
) -> None:
    if type(max_receipts_per_order) is not int or max_receipts_per_order < 1:
        raise ValueError("max_receipts_per_order must be an integer >= 1")
    raw = record.get(DURABLE_RECEIPTS_FIELD)
    receipts = copy.deepcopy(raw) if isinstance(raw, list) else []
    if any(
        isinstance(item, Mapping) and item.get("event_id") == envelope.event_id
        for item in receipts
    ):
        return
    if len(receipts) >= max_receipts_per_order:
        raise ReceiptCapacityExceeded(
            "durable receipt capacity reached; validated compaction is required"
        )
    receipts.append({
        "event_id": envelope.event_id,
        "created_at": envelope.created_at,
        "policy_version": envelope.policy_version,
    })
    record[DURABLE_RECEIPTS_FIELD] = receipts
    record["fsm_last_event_id"] = envelope.event_id
    record["fsm_last_event_at"] = envelope.created_at
    record["durable_last_event_order"] = {
        "created_at": envelope.created_at,
        "event_id": envelope.event_id,
    }


def compact_receipts(
    record: Mapping[str, Any],
    *,
    acknowledged_event_ids: Iterable[str],
    older_than: Any,
    keep_at_most: int,
) -> dict[str, Any]:
    """Czysta kompaktacja tylko ACK-owanych receipts; nigdy nie zgaduje ACK."""
    if type(keep_at_most) is not int or keep_at_most < 1:
        raise ValueError("keep_at_most must be an integer >= 1")
    cutoff = normalize_created_at(older_than)
    acknowledged = {str(value) for value in acknowledged_event_ids}
    result = copy.deepcopy(dict(record))
    raw = result.get(DURABLE_RECEIPTS_FIELD)
    receipts = raw if isinstance(raw, list) else []
    removable = [
        item
        for item in receipts
        if isinstance(item, Mapping)
        and str(item.get("event_id")) in acknowledged
        and normalize_created_at(item.get("created_at")) <= cutoff
    ]
    remove_needed = max(0, len(receipts) - keep_at_most)
    if remove_needed > len(removable):
        raise ReceiptCapacityExceeded(
            "cannot compact unacknowledged or retention-young receipts"
        )
    remove_ids = {
        str(item.get("event_id"))
        for item in removable[:remove_needed]
    }
    result[DURABLE_RECEIPTS_FIELD] = [
        item
        for item in receipts
        if not (
            isinstance(item, Mapping)
            and str(item.get("event_id")) in remove_ids
        )
    ]
    return result


def _reduce_domain(
    current: Optional[Mapping[str, Any]],
    envelope: EventEnvelope,
) -> Optional[dict[str, Any]]:
    event_type = envelope.event_type
    payload = envelope.payload
    record = copy.deepcopy(dict(current or {}))
    oid = envelope.order_id
    if not oid:
        raise ReductionRejected("invalid_payload")

    if event_type == "NEW_ORDER":
        record.update({key: copy.deepcopy(payload.get(key)) for key in _NEW_ORDER_FIELDS})
        record["status"] = "planned"
        record["commitment_level"] = "planned"
        record["first_seen"] = payload.get("first_seen") or envelope.created_at
        record["bag_time_alerted"] = False
    elif event_type == "COURIER_ASSIGNED":
        _validate_ck(
            payload.get("czas_kuriera_warsaw"),
            payload.get("czas_kuriera_hhmm"),
        )
        target = "assigned"
        if record.get("status") in {"picked_up", "delivered"}:
            target = str(record["status"])
        record.update({
            "status": target,
            "commitment_level": target if target in {"picked_up", "delivered"} else "assigned",
            "courier_id": envelope.courier_id,
            "assigned_at": payload.get("assigned_at") or envelope.created_at,
            "proposed_delivery_time": payload.get("proposed_time"),
            "bag_time_alerted": False,
        })
        if payload.get("czas_kuriera_warsaw") not in (None, ""):
            record["czas_kuriera_warsaw"] = payload["czas_kuriera_warsaw"]
            record["czas_kuriera_hhmm"] = payload["czas_kuriera_hhmm"]
    elif event_type == "COURIER_REJECTED_PROPOSAL":
        record.update({
            "status": "planned",
            "commitment_level": "planned",
            "courier_id": None,
            "last_rejected_by": envelope.courier_id,
            "rejection_reason": payload.get("reason"),
            "bag_time_alerted": False,
        })
    elif event_type == "COURIER_PICKED_UP":
        timestamp = payload.get("timestamp")
        picked, _was_naive = parse_order_timestamp(timestamp)
        if picked is None:
            raise ReductionRejected("invalid_timestamp")
        record.update({
            "status": "picked_up",
            "commitment_level": "picked_up",
            "picked_up_at": timestamp,
            "expected_delivery_by": (picked + timedelta(minutes=35)).isoformat(),
            "assigned_check_ts": envelope.created_at,
        })
        if payload.get("pickup_coords") is not None:
            record["pickup_coords"] = copy.deepcopy(payload["pickup_coords"])
    elif event_type == "COURIER_DELIVERED":
        timestamp = payload.get("timestamp")
        delivered, _was_naive = parse_order_timestamp(timestamp)
        if delivered is None:
            raise ReductionRejected("invalid_timestamp")
        del delivered
        record.update({
            "status": "delivered",
            "commitment_level": "planned",
            "delivered_at": timestamp,
            "final_location": payload.get("final_location"),
            "bag_time_alerted": False,
        })
        if payload.get("delivery_address") is not None:
            record["delivery_address"] = payload["delivery_address"]
        if payload.get("delivery_coords") is not None:
            record["delivery_coords"] = copy.deepcopy(payload["delivery_coords"])
    elif event_type == "ORDER_RETURNED_TO_POOL":
        record.update({
            "status": "returned_to_pool",
            "commitment_level": "planned",
            "courier_id": None,
            "return_reason": payload.get("reason"),
            "bag_time_alerted": False,
        })
    elif event_type == "ORDER_CANCELLED":
        record.update({
            "status": "cancelled",
            "commitment_level": "planned",
            "courier_id": None,
            "cancellation_reason": payload.get("reason"),
            "bag_time_alerted": False,
        })
    elif event_type == "CZAS_KURIERA_UPDATED":
        _validate_ck(payload.get("new_ck_iso"), payload.get("new_ck_hhmm"))
        record["czas_kuriera_warsaw"] = payload.get("new_ck_iso")
        record["czas_kuriera_hhmm"] = payload.get("new_ck_hhmm")
        record["v319g_ck_change_count"] = int(
            record.get("v319g_ck_change_count") or 0
        ) + 1
    elif event_type == "PICKUP_TIME_UPDATED":
        new_pickup = payload.get("new_pickup_at_warsaw")
        if parse_order_timestamp(new_pickup)[0] is None:
            raise ReductionRejected("invalid_timestamp")
        record["pickup_at_warsaw"] = new_pickup
        record["pickup_time_change_count"] = int(
            record.get("pickup_time_change_count") or 0
        ) + 1
        for source_key, target_key in (
            ("new_prep_minutes", "prep_minutes"),
            ("new_decision_deadline", "decision_deadline"),
            ("new_zmiana_czasu_odbioru", "zmiana_czasu_odbioru"),
        ):
            if payload.get(source_key) is not None:
                record[target_key] = payload[source_key]
        if record.get("order_type") == "czasowka":
            record["czas_kuriera_warsaw"] = new_pickup
            from datetime import datetime

            parsed = datetime.fromisoformat(str(new_pickup).replace("Z", "+00:00"))
            record["czas_kuriera_hhmm"] = parsed.strftime("%H:%M")
    elif event_type == "ORDER_RESURRECTED":
        target = payload.get("new_status")
        if target not in {"assigned", "picked_up"}:
            raise ReductionRejected("invalid_payload")
        record.update({
            "status": target,
            "commitment_level": target,
            "delivered_at": None,
            "final_location": None,
            "bag_time_alerted": False,
        })
        if envelope.courier_id:
            record["courier_id"] = envelope.courier_id
    else:
        return None

    record["order_id"] = oid
    history = copy.deepcopy(record.get("history"))
    if not isinstance(history, list):
        history = []
    history.append({
        "at": envelope.created_at,
        "event": event_type,
        "status": record.get("status"),
    })
    record["history"] = history
    record["updated_at"] = envelope.created_at
    return record


def reduce_order_event(
    current: Optional[Mapping[str, Any]],
    envelope: EventEnvelope,
    *,
    max_receipts_per_order: int,
) -> ReductionResult:
    """Deterministycznie redukuje jedna kopertę i dopina ograniczony receipt."""
    seen = _receipt_ids(current)
    event = envelope.as_event()
    verdict = validate_order_event(event, current=current, seen_event_ids=seen)
    if envelope.event_id in seen:
        return ReductionResult(
            record=copy.deepcopy(dict(current or {})) or None,
            verdict=verdict,
            domain_changed=False,
            receipt_changed=False,
            duplicate=True,
        )
    previous_order = _current_order(current)
    if previous_order is not None and _event_order(envelope) <= previous_order:
        raise ReductionRejected("stale_event")
    if verdict.would_reject:
        raise ReductionRejected(rejection_code(verdict))

    if verdict.outcome is FsmOutcome.DUPLICATE:
        if current is None:
            return ReductionResult(None, verdict, False, False, True)
        record = copy.deepcopy(dict(current))
        _append_receipt(
            record,
            envelope,
            max_receipts_per_order=max_receipts_per_order,
        )
        return ReductionResult(record, verdict, False, True, True)

    record = _reduce_domain(current, envelope)
    if record is None:
        return ReductionResult(None, verdict, False, False, False)
    _append_receipt(
        record,
        envelope,
        max_receipts_per_order=max_receipts_per_order,
    )
    return ReductionResult(record, verdict, True, True, False)


__all__ = [
    "DURABLE_RECEIPTS_FIELD",
    "ReceiptCapacityExceeded",
    "ReductionRejected",
    "ReductionResult",
    "compact_receipts",
    "reduce_order_event",
]
