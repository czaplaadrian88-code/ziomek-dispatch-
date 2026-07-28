"""Kanoniczny producent dowodów zakresu karty ``auto.canary.v1``.

Moduł jest czystą, log-only projekcją finalnej decyzji i snapshotów użytych do
jej policzenia. Nie zgaduje brakujących faktów: każdy nieudowodniony predykat
ma jawne ``{"absent": "..."}``, które runtime gate interpretuje fail-closed.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from dispatch_v2 import live_eta


SCHEMA = "authority_scope.v1"


def _absent(reason: str) -> Dict[str, str]:
    return {"absent": reason}


def _is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
    )


def _new_unassigned(
    order_event: Mapping[str, Any],
    order_state: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(order_state, Mapping):
        return _absent("decision-time orders_state row is unavailable")
    history = order_state.get("history")
    if not isinstance(history, list) or any(
        not isinstance(row, Mapping) for row in history
    ):
        return _absent("orders_state has no durable lifecycle history")
    event_id = order_event.get("event_id")
    if (
        not isinstance(event_id, str)
        or not event_id
        or order_state.get("last_lifecycle_event_id_new_order") != event_id
    ):
        return _absent(
            "NEW_ORDER event is not bound to the decision-time state snapshot"
        )
    if "courier_id" not in order_state:
        return _absent("orders_state row has no current assignment field")
    event_type = order_event.get("event_type")
    status_id = order_event.get("status_id")
    state_status = order_state.get("status")
    if (
        not isinstance(event_type, str)
        or isinstance(status_id, bool)
        or not isinstance(status_id, int)
        or not isinstance(state_status, str)
    ):
        return _absent("event type or status evidence is unavailable")

    assigned_events = sum(
        1 for row in history if row.get("event") == "COURIER_ASSIGNED"
    )
    current_courier = order_state.get("courier_id")
    return {
        "event_type": event_type,
        "status_id": status_id,
        "state_status": state_status,
        "prior_assignment_count": assigned_events,
        "currently_assigned": current_courier not in (None, ""),
        "sources": {
            "event_type": "event_bus.event_type",
            "status_id": "event_bus.payload.status_id",
            "assignment_history": "orders_state.history",
            "current_assignment": "orders_state.courier_id",
        },
    }


def _empty_bag(best_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    bag_size = best_metrics.get("bag_size_before")
    bag_context = best_metrics.get("bag_context")
    generation = best_metrics.get("plan_expected_version")
    if (
        isinstance(bag_size, bool)
        or not isinstance(bag_size, int)
        or not isinstance(bag_context, list)
        or any(not isinstance(row, Mapping) for row in bag_context)
        or isinstance(generation, bool)
        or not isinstance(generation, int)
    ):
        return _absent(
            "winner bag size, active-order snapshot, or generation is unavailable"
        )
    active_order_ids = []
    for row in bag_context:
        oid = row.get("order_id")
        if oid in (None, ""):
            return _absent("winner bag snapshot contains an order without oid")
        active_order_ids.append(str(oid))
    return {
        "bag_size": bag_size,
        "active_order_ids": active_order_ids,
        "generation": generation,
        "sources": {
            "bag": "Candidate.metrics.bag_context",
            "generation": "Candidate.metrics.plan_expected_version",
        },
    }


def _solo_plan(best: Any) -> Dict[str, Any]:
    plan = getattr(best, "plan", None)
    sequence = getattr(plan, "sequence", None) if plan is not None else None
    pickup_at = getattr(plan, "pickup_at", None) if plan is not None else None
    if not isinstance(sequence, list) or not isinstance(pickup_at, Mapping):
        return _absent("winner RoutePlanV2 sequence or pickup map is unavailable")
    if any(oid in (None, "") for oid in sequence):
        return _absent("winner RoutePlanV2 sequence contains an empty oid")
    if any(oid in (None, "") for oid in pickup_at):
        return _absent("winner RoutePlanV2 pickup map contains an empty oid")
    return {
        "n_pickups": len({str(oid) for oid in pickup_at}),
        "n_deliveries": len([str(oid) for oid in sequence]),
        "sources": {
            "pickups": "RoutePlanV2.pickup_at",
            "deliveries": "RoutePlanV2.sequence",
        },
    }


def _exclusions(
    predicate_one: Mapping[str, Any],
    best: Any,
    best_metrics: Mapping[str, Any],
) -> Dict[str, Any]:
    if "absent" in predicate_one:
        reassign = _absent(
            "reassignment cannot be excluded without prior-assignment evidence"
        )
    else:
        reassign = {
            "value": not (
                predicate_one.get("event_type") == "NEW_ORDER"
                and predicate_one.get("prior_assignment_count") == 0
            ),
            "source": "event_bus.event_type+orders_state.history",
        }

    best_effort = getattr(best, "best_effort", None)
    least_damage = (
        {
            "value": best_effort,
            "source": "Candidate.best_effort",
        }
        if isinstance(best_effort, bool)
        else _absent("final selection has no best_effort marker")
    )
    parcel = best_metrics.get("paczka_is")
    parcel_evidence = (
        {"value": parcel, "source": "common.is_paczka_order"}
        if isinstance(parcel, bool)
        else _absent("canonical package classifier result is unavailable")
    )
    return {
        "reassign": reassign,
        "alarm": _absent(
            "no authoritative normal/alarm mode is bound to this decision"
        ),
        "least_damage": least_damage,
        "parcel": parcel_evidence,
        "multi_brand": _absent(
            "decision input has no canonical multi-brand classification"
        ),
        "shared_pickup": _absent(
            "decision input has no canonical shared-pickup classification"
        ),
        "coordinator_override": _absent(
            "decision input has no complete coordinator-override context"
        ),
    }


def _winner_position(best_metrics: Mapping[str, Any]) -> Dict[str, Any]:
    pos_source = best_metrics.get("pos_source")
    age_seconds = best_metrics.get("pos_age_sec")
    if not isinstance(pos_source, str) or not _is_number(age_seconds):
        return _absent(
            "winner position source or exact decision-time age is unavailable"
        )
    age = float(age_seconds)
    contract = live_eta.classify_position_contract(pos_source, age).upper()
    return {
        "pos_source": pos_source,
        "age_seconds": age,
        "contract": contract,
        "sources": {
            "position": "CourierState.pos_source",
            "age": "CourierState.pos_age_min*60",
            "contract": "live_eta.classify_position_contract",
        },
    }


def build_authority_scope(
    result: Any,
    order_event: Optional[Mapping[str, Any]],
    order_state: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Zbuduj siedem dowodów karty z finalnego wyniku i snapshotu decyzji."""
    order_event = order_event if isinstance(order_event, Mapping) else {}
    best = getattr(result, "best", None)
    best_metrics = getattr(best, "metrics", None)
    best_metrics = best_metrics if isinstance(best_metrics, Mapping) else {}

    predicate_one = _new_unassigned(order_event, order_state)
    predicates = {
        "1_new_unassigned": predicate_one,
        "2_empty_bag": (
            _empty_bag(best_metrics)
            if best is not None
            else _absent("decision has no winner")
        ),
        "3_solo_plan": (
            _solo_plan(best)
            if best is not None
            else _absent("decision has no winner")
        ),
        "4_mode": _absent(
            "no authoritative normal/alarm mode is bound to this decision"
        ),
        "5_exclusions": _exclusions(
            predicate_one, best, best_metrics
        ),
        "6_winner_position": (
            _winner_position(best_metrics)
            if best is not None
            else _absent("decision has no winner")
        ),
        "7_no_gps_parity": _absent(
            "policy parity requires a separate structural attestation; "
            "a single decision record cannot prove it"
        ),
    }
    return {"schema": SCHEMA, "predicates": predicates}


def attach_authority_scope(
    result: Any,
    order_event: Optional[Mapping[str, Any]],
    order_state: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Policz raz i podłącz ten sam blok do rekordu oraz LOCATION A+B."""
    try:
        block = build_authority_scope(result, order_event, order_state)
    except Exception as exc:
        # Telemetria nie może zmienić wyniku dispatchu. Błąd producenta jest
        # nadal maszynowo widoczny i fail-closed dla wszystkich 7 predykatów.
        reason = f"authority_scope producer error: {type(exc).__name__}"
        block = {
            "schema": SCHEMA,
            "predicates": {
                "1_new_unassigned": _absent(reason),
                "2_empty_bag": _absent(reason),
                "3_solo_plan": _absent(reason),
                "4_mode": _absent(reason),
                "5_exclusions": _absent(reason),
                "6_winner_position": _absent(reason),
                "7_no_gps_parity": _absent(reason),
            },
        }
    result.authority_scope = block
    best = getattr(result, "best", None)
    metrics = getattr(best, "metrics", None)
    if isinstance(metrics, dict):
        metrics["authority_scope"] = block
    return block
