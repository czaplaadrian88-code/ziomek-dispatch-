"""Pure, side-effect-free order lifecycle validator (Z-P1-01, Phase A).

This module deliberately does *not* apply transitions.  It describes what the
formal order FSM would accept and returns a structured verdict which can be
observed by the legacy state machine.  Phase A is log-only: callers must not use
the verdict to mutate state or suppress the legacy path.

The validator understands three kinds of transitions:

* core lifecycle edges (for example ``planned -> assigned``),
* explicit reconciliation catch-up edges for known, tagged sources,
* explicit correction edges, including delivered-order resurrection.

``event_id`` and ``created_at`` are optional today because the legacy inline
call sites do not consistently pass them.  They are nevertheless part of the
contract so retry/DLQ and replay can preserve identity and ordering later.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo


ABSENT_STATE = "<absent>"

ORDER_STATES = frozenset({
    "planned",
    "assigned",
    "picked_up",
    "delivered",
    "returned_to_pool",
    "cancelled",
})

LIFECYCLE_EVENT_TARGETS = {
    "NEW_ORDER": "planned",
    "COURIER_ASSIGNED": "assigned",
    "COURIER_REJECTED_PROPOSAL": "planned",
    "COURIER_PICKED_UP": "picked_up",
    "COURIER_DELIVERED": "delivered",
    "ORDER_RETURNED_TO_POOL": "returned_to_pool",
    "ORDER_RECLAIMED_TO_CZASOWKA": "planned",
}

DATA_ONLY_EVENT_TYPES = frozenset({
    "CZAS_KURIERA_UPDATED",
    "PICKUP_TIME_UPDATED",
})

# Event-bus messages which are not order lifecycle mutations.  Keeping them
# explicit prevents a global health event from being reported as a bad order.
NON_STATE_EVENT_TYPES = frozenset({
    "ORDER_READY",
    "KOORDYNATOR_DEADLINE",
    "GPS_STALE",
    "PANEL_UNREACHABLE",
    "HEARTBEAT_STALL",
    "SHIFT_END_APPROACHING",
    "CONFIG_RELOAD",
})

# Internal correction event emitted by the durable resurrection chokepoint.
CORRECTION_EVENT_TYPES = frozenset({"ORDER_RESURRECTED"})

# Sources which are allowed to bridge an event gap discovered from a stronger
# snapshot (panel details, courier ground truth, or reconciliation worker).
RECONCILE_SOURCES = frozenset({
    "reconcile",
    "reconciliation_inferred",
    "packs_ghost_detect",
    "ground_truth_fallback",
    "cold_start_scan",
    "packs_fallback",
    "panel",
    "panel_diff",
    "parcel_status_inbox",
})

CORRECTION_SOURCES = frozenset({
    "panel_status_restored",
    "delivered_resurrection",
    "manual_correction",
    "coordinator_correction",
    "handoff",
    "parcel_handoff",
    # Real Path-B/cold-start writers can re-emit ASSIGNED after pickup; legacy
    # deliberately updates courier fields while preserving picked_up status.
    "panel_diff",
    "panel_reassign",
    "packs_fallback",
    "cold_start_scan",
})

# Unambiguous, documented lifecycle graph.  Idempotent self-repetitions are
# handled separately, because they depend on event identity/fact equality.
CORE_TRANSITIONS = frozenset({
    (ABSENT_STATE, "NEW_ORDER"),
    ("planned", "COURIER_ASSIGNED"),
    ("assigned", "COURIER_ASSIGNED"),
    ("assigned", "COURIER_REJECTED_PROPOSAL"),
    ("assigned", "COURIER_PICKED_UP"),
    ("assigned", "ORDER_RETURNED_TO_POOL"),
    ("assigned", "ORDER_RECLAIMED_TO_CZASOWKA"),
    ("picked_up", "COURIER_DELIVERED"),
    ("picked_up", "ORDER_RETURNED_TO_POOL"),
    ("returned_to_pool", "COURIER_ASSIGNED"),
})

# Catch-up transitions are intentionally narrower than the legacy handler and
# require an explicit source tag.  They cover known event-loss/reconcile paths
# without making every forward jump legal.
RECONCILE_TRANSITIONS = frozenset({
    (ABSENT_STATE, "COURIER_ASSIGNED"),
    (ABSENT_STATE, "COURIER_PICKED_UP"),
    (ABSENT_STATE, "COURIER_DELIVERED"),
    (ABSENT_STATE, "ORDER_RETURNED_TO_POOL"),
    ("planned", "COURIER_PICKED_UP"),
    ("planned", "COURIER_DELIVERED"),
    ("planned", "ORDER_RETURNED_TO_POOL"),
    ("assigned", "COURIER_DELIVERED"),
})

_REQUIRED_FIELDS = {
    "NEW_ORDER": ("payload.restaurant", "payload.delivery_address"),
    "COURIER_ASSIGNED": ("courier_id",),
    "COURIER_REJECTED_PROPOSAL": ("courier_id",),
    "COURIER_PICKED_UP": ("courier_id", "payload.timestamp"),
    "COURIER_DELIVERED": ("payload.timestamp",),
    "ORDER_RETURNED_TO_POOL": ("payload.reason",),
    "ORDER_RECLAIMED_TO_CZASOWKA": (
        "payload.previous_courier_id",
        "payload.reclaim_generation",
        "payload.reclaimed_at",
        "payload.reason",
        "payload.expected_assignment_event_id",
        "payload.expected_pickup_at_warsaw",
    ),
    "CZAS_KURIERA_UPDATED": (
        "payload.new_ck_iso",
        "payload.new_ck_hhmm",
    ),
    "PICKUP_TIME_UPDATED": ("payload.new_pickup_at_warsaw",),
    "ORDER_RESURRECTED": ("payload.new_status", "payload.reason"),
}

_WARSAW = ZoneInfo("Europe/Warsaw")


class FsmOutcome(str, Enum):
    """Classification produced by :func:`validate_order_event`."""

    LEGAL = "legal"
    DUPLICATE = "duplicate"
    DATA_ONLY = "data_only"
    RECONCILE_EXCEPTION = "reconcile_exception"
    CORRECTION_EXCEPTION = "correction_exception"
    NON_STATE = "non_state"
    ILLEGAL = "illegal"


@dataclass(frozen=True)
class FsmIssue:
    code: str
    message: str
    field: Optional[str] = None
    severity: str = "error"  # error => future enforcement would reject


@dataclass(frozen=True)
class FsmVerdict:
    event_type: str
    order_id: Optional[str]
    from_status: str
    to_status: Optional[str]
    source: Optional[str]
    event_id: Optional[str]
    outcome: FsmOutcome
    transition_allowed: bool
    issues: tuple[FsmIssue, ...] = ()

    @property
    def would_reject(self) -> bool:
        """Whether a future enforcing FSM would reject this event."""
        return (not self.transition_allowed) or any(
            issue.severity == "error" for issue in self.issues
        )

    @property
    def issue_codes(self) -> tuple[str, ...]:
        return tuple(issue.code for issue in self.issues)


def _clean_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _payload(event: Mapping[str, Any], issues: list[FsmIssue]) -> Mapping[str, Any]:
    raw = event.get("payload", {})
    if raw is None:
        return {}
    if isinstance(raw, Mapping):
        return raw
    issues.append(FsmIssue(
        "payload_not_mapping",
        f"payload must be a mapping, got {type(raw).__name__}",
        field="payload",
    ))
    return {}


def _source(event: Mapping[str, Any], payload: Mapping[str, Any]) -> Optional[str]:
    return _clean_text(
        payload.get("source")
        or payload.get("deliv_source")
        or event.get("source")
    )


def _is_source(source: Optional[str], allowed: frozenset[str]) -> bool:
    if not source:
        return False
    normalized = source.lower()
    return normalized in allowed or any(
        normalized.startswith(prefix + ":") for prefix in allowed
    )


def parse_order_timestamp(value: Any) -> tuple[Optional[datetime], bool]:
    """Parse a supported timestamp onto an aware UTC axis.

    Returns ``(datetime_or_none, was_naive)``.  Naive panel timestamps are
    interpreted as Europe/Warsaw for legacy compatibility, but the verdict
    carries a warning so producers can migrate to explicit offsets.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None, False
    else:
        return None, False
    was_naive = parsed.tzinfo is None
    if was_naive:
        parsed = parsed.replace(tzinfo=_WARSAW)
    return parsed.astimezone(timezone.utc), was_naive


def _path_value(
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
    path: str,
) -> Any:
    if path.startswith("payload."):
        return payload.get(path.split(".", 1)[1])
    return event.get(path)


def _validate_required_fields(
    event_type: str,
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
    issues: list[FsmIssue],
) -> None:
    for path in _REQUIRED_FIELDS.get(event_type, ()):
        value = _path_value(event, payload, path)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(FsmIssue(
                "missing_required_field",
                f"{event_type} requires {path}",
                field=path,
            ))


def _validate_timestamp_field(
    value: Any,
    path: str,
    issues: list[FsmIssue],
) -> Optional[datetime]:
    if value is None or value == "":
        return None
    parsed, was_naive = parse_order_timestamp(value)
    if parsed is None:
        issues.append(FsmIssue(
            "timestamp_unparseable",
            f"cannot parse timestamp in {path}",
            field=path,
        ))
        return None
    if was_naive:
        issues.append(FsmIssue(
            "timestamp_naive",
            f"{path} has no timezone; interpreted as Europe/Warsaw",
            field=path,
            severity="warning",
        ))
    return parsed


def _validate_timestamps(
    event_type: str,
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
    current: Optional[Mapping[str, Any]],
    issues: list[FsmIssue],
) -> None:
    paths: tuple[str, ...] = ()
    if event_type == "NEW_ORDER":
        paths = (
            "payload.first_seen",
            "payload.pickup_at_warsaw",
            "payload.created_at_utc",
            "payload.czas_kuriera_warsaw",
        )
    elif event_type == "COURIER_ASSIGNED":
        paths = ("payload.assigned_at", "payload.czas_kuriera_warsaw")
    elif event_type in {"COURIER_PICKED_UP", "COURIER_DELIVERED"}:
        paths = ("payload.timestamp",)
    elif event_type == "CZAS_KURIERA_UPDATED":
        paths = ("payload.new_ck_iso",)
    elif event_type == "PICKUP_TIME_UPDATED":
        paths = ("payload.new_pickup_at_warsaw",)
    elif event_type == "ORDER_RECLAIMED_TO_CZASOWKA":
        paths = ("payload.reclaimed_at", "payload.expected_pickup_at_warsaw")

    parsed: dict[str, datetime] = {}
    for path in paths:
        value = _path_value(event, payload, path)
        dt = _validate_timestamp_field(value, path, issues)
        if dt is not None:
            parsed[path] = dt

    created_at = event.get("created_at")
    event_created = _validate_timestamp_field(
        created_at, "created_at", issues
    ) if created_at not in (None, "") else None

    if event_type == "CZAS_KURIERA_UPDATED":
        ck_dt = parsed.get("payload.new_ck_iso")
        hhmm = _clean_text(payload.get("new_ck_hhmm"))
        if ck_dt is not None and hhmm is not None:
            # Compare on the original wall-clock representation, not UTC.
            try:
                original = datetime.fromisoformat(
                    str(payload.get("new_ck_iso")).replace("Z", "+00:00")
                )
                expected_hhmm = original.strftime("%H:%M")
            except (TypeError, ValueError):
                expected_hhmm = None
            if expected_hhmm is not None and expected_hhmm != hhmm:
                issues.append(FsmIssue(
                    "timestamp_hhmm_mismatch",
                    f"new_ck_iso wall time {expected_hhmm} != new_ck_hhmm {hhmm}",
                    field="payload.new_ck_hhmm",
                ))

    if event_type == "COURIER_DELIVERED" and current:
        delivery_dt = parsed.get("payload.timestamp")
        pickup_raw = current.get("picked_up_at")
        pickup_dt, _ = parse_order_timestamp(pickup_raw)
        if pickup_raw and pickup_dt is None:
            issues.append(FsmIssue(
                "current_timestamp_unparseable",
                "current picked_up_at cannot be parsed",
                field="current.picked_up_at",
                severity="warning",
            ))
        if delivery_dt is not None and pickup_dt is not None and delivery_dt < pickup_dt:
            issues.append(FsmIssue(
                "timestamp_before_pickup",
                "delivery timestamp is earlier than picked_up_at",
                field="payload.timestamp",
            ))

    # Retry/DLQ can deliver an older event after a newer event.  The legacy
    # record does not store this anchor yet; honour it when replay/new writers do.
    if current and event_created is not None:
        last_raw = current.get("fsm_last_event_at")
        last_dt, _ = parse_order_timestamp(last_raw)
        if last_raw and last_dt is None:
            issues.append(FsmIssue(
                "current_event_time_unparseable",
                "current fsm_last_event_at cannot be parsed",
                field="current.fsm_last_event_at",
                severity="warning",
            ))
        if last_dt is not None and event_created < last_dt:
            issues.append(FsmIssue(
                "stale_event",
                "event.created_at is older than current fsm_last_event_at",
                field="created_at",
            ))


def _known_seen_ids(
    current: Optional[Mapping[str, Any]],
    seen_event_ids: Optional[Iterable[str]],
) -> set[str]:
    seen = {str(v) for v in (seen_event_ids or ()) if v is not None}
    if not current:
        return seen
    last = current.get("fsm_last_event_id")
    if last:
        seen.add(str(last))
    many = current.get("fsm_event_ids")
    if isinstance(many, (list, tuple, set, frozenset)):
        seen.update(str(v) for v in many if v is not None)
    return seen


def _same_timestamp(left: Any, right: Any) -> bool:
    if left == right and left not in (None, ""):
        return True
    left_dt, _ = parse_order_timestamp(left)
    right_dt, _ = parse_order_timestamp(right)
    return left_dt is not None and right_dt is not None and left_dt == right_dt


def _semantic_duplicate(
    event_type: str,
    event: Mapping[str, Any],
    payload: Mapping[str, Any],
    current: Optional[Mapping[str, Any]],
) -> bool:
    if not current:
        return False
    status = _clean_text(current.get("status"))
    if event_type == "NEW_ORDER" and status == "planned":
        compared = ("restaurant", "delivery_address")
        return all(
            payload.get(key) == current.get(key) for key in compared
        )
    if event_type == "COURIER_ASSIGNED" and status == "assigned":
        return _clean_text(event.get("courier_id")) == _clean_text(
            current.get("courier_id")
        )
    if event_type == "COURIER_REJECTED_PROPOSAL" and status == "planned":
        return _clean_text(event.get("courier_id")) == _clean_text(
            current.get("last_rejected_by")
        )
    if event_type == "COURIER_PICKED_UP" and status == "picked_up":
        return _same_timestamp(payload.get("timestamp"), current.get("picked_up_at"))
    if event_type == "COURIER_DELIVERED" and status == "delivered":
        return _same_timestamp(payload.get("timestamp"), current.get("delivered_at"))
    if event_type == "ORDER_RETURNED_TO_POOL" and status == "returned_to_pool":
        return payload.get("reason") == current.get("return_reason")
    if event_type == "CZAS_KURIERA_UPDATED":
        return (
            payload.get("new_ck_iso") == current.get("czas_kuriera_warsaw")
            and payload.get("new_ck_hhmm") == current.get("czas_kuriera_hhmm")
        )
    if event_type == "PICKUP_TIME_UPDATED":
        return payload.get("new_pickup_at_warsaw") == current.get("pickup_at_warsaw")
    if event_type == "ORDER_RECLAIMED_TO_CZASOWKA":
        return (
            status == "planned"
            and _clean_text(current.get("courier_id")) == "26"
            and _clean_text(current.get("reclaim_generation"))
            == _clean_text(payload.get("reclaim_generation"))
        )
    return False


def _effective_target(
    event_type: str,
    payload: Mapping[str, Any],
    from_status: str,
) -> Optional[str]:
    if event_type in DATA_ONLY_EVENT_TYPES or event_type in NON_STATE_EVENT_TYPES:
        return None if from_status == ABSENT_STATE else from_status
    if event_type == "ORDER_RESURRECTED":
        return _clean_text(payload.get("new_status"))
    # Legacy COURIER_ASSIGNED deliberately preserves picked_up/delivered while
    # updating courier fields.  Report its effective status, then classify the
    # event as correction or illegal below.
    if event_type == "COURIER_ASSIGNED" and from_status in {"picked_up", "delivered"}:
        return from_status
    return LIFECYCLE_EVENT_TARGETS.get(event_type)


def validate_order_event(
    event: Mapping[str, Any],
    current: Optional[Mapping[str, Any]] = None,
    *,
    seen_event_ids: Optional[Iterable[str]] = None,
) -> FsmVerdict:
    """Return a formal, non-mutating FSM verdict for ``event``.

    ``current`` is the current order record or ``None`` when the order does not
    exist.  No input mapping is modified.  Validation issues are accumulated so
    Phase-A logs show all producer-contract problems in one record.
    """
    if not isinstance(event, Mapping):
        return FsmVerdict(
            event_type="<invalid>",
            order_id=None,
            from_status=ABSENT_STATE,
            to_status=None,
            source=None,
            event_id=None,
            outcome=FsmOutcome.ILLEGAL,
            transition_allowed=False,
            issues=(FsmIssue(
                "event_not_mapping",
                f"event must be a mapping, got {type(event).__name__}",
            ),),
        )

    issues: list[FsmIssue] = []
    event_type = _clean_text(event.get("event_type")) or "<missing>"
    order_id = _clean_text(event.get("order_id"))
    event_id = _clean_text(event.get("event_id"))
    payload = _payload(event, issues)
    source = _source(event, payload)

    raw_status = _clean_text(current.get("status")) if current else None
    from_status = raw_status or ABSENT_STATE
    if raw_status is not None and raw_status not in ORDER_STATES:
        issues.append(FsmIssue(
            "unknown_current_status",
            f"current status {raw_status!r} is outside ORDER_STATES",
            field="current.status",
        ))

    if event_type in NON_STATE_EVENT_TYPES:
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=from_status if current else None,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.NON_STATE,
            transition_allowed=True,
            issues=tuple(issues),
        )

    known_order_event = (
        event_type in LIFECYCLE_EVENT_TARGETS
        or event_type in DATA_ONLY_EVENT_TYPES
        or event_type in CORRECTION_EVENT_TYPES
    )
    if not known_order_event:
        issues.append(FsmIssue(
            "unsupported_order_event",
            f"event type {event_type!r} is not part of the order FSM",
            field="event_type",
        ))
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=None,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.ILLEGAL,
            transition_allowed=False,
            issues=tuple(issues),
        )

    if order_id is None:
        issues.append(FsmIssue(
            "missing_order_id",
            f"{event_type} requires order_id",
            field="order_id",
        ))

    # Identity wins over ordering: an already-applied event is a byte-level
    # no-op even when retry delivers it after newer transitions.  Do this before
    # required-field/timestamp validation so replay does not DLQ a known event
    # merely because its old producer contract was incomplete.
    to_status = _effective_target(event_type, payload, from_status)
    seen = _known_seen_ids(current, seen_event_ids)
    if event_id is not None and event_id in seen:
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=from_status if current else to_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.DUPLICATE,
            transition_allowed=True,
            issues=tuple(issues),
        )

    _validate_required_fields(event_type, event, payload, issues)
    _validate_timestamps(event_type, event, payload, current, issues)

    if _semantic_duplicate(event_type, event, payload, current):
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=from_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.DUPLICATE,
            transition_allowed=True,
            issues=tuple(issues),
        )

    if event_type in DATA_ONLY_EVENT_TYPES:
        allowed = current is not None and raw_status in ORDER_STATES
        if not allowed:
            issues.append(FsmIssue(
                "data_event_without_order",
                f"{event_type} cannot create an order",
                field="order_id",
            ))
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.DATA_ONLY if allowed else FsmOutcome.ILLEGAL,
            transition_allowed=allowed,
            issues=tuple(issues),
        )

    edge = (from_status, event_type)
    if edge in CORE_TRANSITIONS:
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.LEGAL,
            transition_allowed=True,
            issues=tuple(issues),
        )

    if edge in RECONCILE_TRANSITIONS and _is_source(source, RECONCILE_SOURCES):
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.RECONCILE_EXCEPTION,
            transition_allowed=True,
            issues=tuple(issues),
        )

    if (
        event_type == "COURIER_ASSIGNED"
        and from_status == "picked_up"
        and _is_source(source, CORRECTION_SOURCES)
    ):
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=from_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.CORRECTION_EXCEPTION,
            transition_allowed=True,
            issues=tuple(issues),
        )

    if (
        event_type == "ORDER_RESURRECTED"
        and from_status == "delivered"
        and to_status in {"assigned", "picked_up"}
        and _is_source(source, CORRECTION_SOURCES)
    ):
        return FsmVerdict(
            event_type=event_type,
            order_id=order_id,
            from_status=from_status,
            to_status=to_status,
            source=source,
            event_id=event_id,
            outcome=FsmOutcome.CORRECTION_EXCEPTION,
            transition_allowed=True,
            issues=tuple(issues),
        )

    if edge in RECONCILE_TRANSITIONS and not source:
        issues.append(FsmIssue(
            "missing_reconcile_source",
            "catch-up transition requires an explicit reconciliation source",
            field="payload.source",
        ))
    elif event_type == "ORDER_RESURRECTED" and not source:
        issues.append(FsmIssue(
            "missing_correction_source",
            "resurrection requires an explicit correction source",
            field="payload.source",
        ))
    issues.append(FsmIssue(
        "illegal_transition",
        f"transition {from_status} --{event_type}--> {to_status} is not allowed",
    ))
    return FsmVerdict(
        event_type=event_type,
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        source=source,
        event_id=event_id,
        outcome=FsmOutcome.ILLEGAL,
        transition_allowed=False,
        issues=tuple(issues),
    )


__all__ = [
    "ABSENT_STATE",
    "CORE_TRANSITIONS",
    "CORRECTION_EVENT_TYPES",
    "DATA_ONLY_EVENT_TYPES",
    "FsmIssue",
    "FsmOutcome",
    "FsmVerdict",
    "LIFECYCLE_EVENT_TARGETS",
    "NON_STATE_EVENT_TYPES",
    "ORDER_STATES",
    "RECONCILE_SOURCES",
    "RECONCILE_TRANSITIONS",
    "parse_order_timestamp",
    "validate_order_event",
]
