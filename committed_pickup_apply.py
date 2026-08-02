"""Wspolny durable transport zdarzen czasu odbioru.

Panel watcher i pre-proposal nie moga miec dwoch semantyk crash/retry. Ten
modul utrwala dokladny event, stosuje state i domyka lifecycle downstream przez
ten sam outbox. Polityka autorytetu pozostaje w
``committed_pickup_authority.py``; tutaj jest tylko transport.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Mapping

from dispatch_v2 import common as C
from dispatch_v2 import durable_event_apply, event_bus, lifecycle_downstream
from dispatch_v2.committed_pickup_authority import (
    CK_CHANGE_REVISION_OBSERVATION_FIELD,
    COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
    NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD,
    NEW_ORDER_TIME_INTENT_FIELD,
    NEW_ORDER_TIME_INTENT_ID_FIELD,
    CommittedPickupPolicySnapshot,
    ResolutionOutcome,
    TIME_EVENT_CAS_SCHEMA_FIELD,
    deserialize_committed_time_policy,
    deserialize_coordinator_event_policy,
    project_time_event_order,
    serialize_committed_time_policy,
    time_event_cas_artifact_present,
    time_event_cas_is_versioned,
    validate_committed_pickup_event,
    validate_committed_time_policy_source,
    validate_new_order_time_intent_event,
)

AUTHORITY_ATTESTATION_SCHEMA = "committed_pickup_outbox_attestation.v1"


def _authority_core(event: Mapping[str, object]) -> dict:
    """Semantyczna intencja używana wyłącznie do exact-resume dedupe."""
    payload = event.get("payload")
    return {
        "event_type": event.get("event_type"),
        "order_id": str(event.get("order_id") or ""),
        "courier_id": (
            str(event.get("courier_id"))
            if event.get("courier_id") is not None
            else None
        ),
        "payload": dict(payload) if isinstance(payload, Mapping) else None,
    }


def _authority_sealed_core(event: Mapping[str, object]) -> dict:
    """Cała trwała koperta poza event_id i samą atestacją.

    Markery autoryzujące downstream są częścią decyzji i muszą być związane
    hashem tak samo jak payload. ``event_id`` powstaje dopiero po sealowaniu.
    """
    excluded = {"event_id", "committed_authority_attestation"}
    return {
        str(key): value
        for key, value in event.items()
        if key not in excluded
    }


def _authority_core_sha256(event: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            _authority_sealed_core(event),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _authority_attestation(event: Mapping[str, object]) -> dict:
    payload = event.get("payload") or {}
    return {
        "schema": AUTHORITY_ATTESTATION_SCHEMA,
        "authority": payload.get("committed_authority"),
        "event_key": payload.get("committed_pickup_event_key"),
        "core_sha256": _authority_core_sha256(event),
    }


def _seal_authority_event(event: dict) -> dict:
    """Dodaj atestację po zamrożeniu markerów przez durable bridge."""
    return {"committed_authority_attestation": _authority_attestation(event)}


def verify_durable_authority_attestation(
    event: Mapping[str, object],
) -> bool:
    """Marker jest ważny wyłącznie jako exact state_event istniejącego outboxa."""
    event_id = str(event.get("event_id") or "")
    attestation = event.get("committed_authority_attestation")
    if not event_id or not isinstance(attestation, Mapping):
        return False
    if dict(attestation) != _authority_attestation(event):
        return False
    row = event_bus.get_state_apply_outbox(event_id)
    stored = row.get("state_event") if isinstance(row, dict) else None
    return bool(isinstance(stored, dict) and stored == dict(event))


def _verified_new_order_time_intent_policy(
    current: Mapping[str, object] | None,
    event: Mapping[str, object] | None,
) -> CommittedPickupPolicySnapshot | None:
    """Bind and read one immutable NEW_ORDER receipt in a single DB read.

    The intent hash protects accidental corruption, but it is deliberately not
    a signature: anyone able to alter state could recompute it. Authority comes
    from the independent event-bus row whose event id was atomically persisted
    in orders_state by NEW_ORDER.
    """
    if not isinstance(current, Mapping) or not isinstance(event, Mapping):
        return None
    if not validate_new_order_time_intent_event(current, event):
        return None
    order_id = str(current.get("order_id") or "")
    marker = str(current.get("last_lifecycle_event_id_new_order") or "")
    intent = current.get(NEW_ORDER_TIME_INTENT_FIELD)
    if not order_id or not marker or not isinstance(intent, Mapping):
        return None
    row = event_bus.get_state_apply_outbox(marker)
    stored = row.get("state_event") if isinstance(row, Mapping) else None
    if not isinstance(stored, Mapping):
        return None
    try:
        policy = deserialize_committed_time_policy(
            stored.get(COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
        )
    except (TypeError, ValueError):
        return None
    valid = bool(
        str(row.get("event_id") or "") == marker
        and str(row.get("event_key") or "")
        and str(row.get("order_id") or "") == order_id
        and row.get("state_status") == "applied"
        and stored.get("event_type") == "NEW_ORDER"
        and str(stored.get("event_id") or "") == marker
        and str(stored.get("order_id") or "") == order_id
        and stored.get(NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD) is True
        and policy.producer == "panel_watcher"
        and policy.initial_time_authority_enabled is True
        and stored.get(NEW_ORDER_TIME_INTENT_FIELD) == intent
        and (event.get("payload") or {}).get(
            NEW_ORDER_TIME_INTENT_ID_FIELD
        )
        == intent.get("intent_id")
    )
    return policy if valid else None


def verify_new_order_time_intent_receipt(
    current: Mapping[str, object] | None,
    event: Mapping[str, object] | None,
) -> bool:
    """Return whether the exact durable NEW_ORDER receipt is valid."""
    return _verified_new_order_time_intent_policy(current, event) is not None


def _new_order_time_intent_policy(
    current: Mapping[str, object] | None,
    event: Mapping[str, object] | None,
) -> CommittedPickupPolicySnapshot | None:
    """Return the policy from the exact NEW_ORDER receipt, never live flags."""
    return _verified_new_order_time_intent_policy(current, event)


def time_update_event_key(order_id: str, event: Mapping[str, object]) -> str:
    """Stabilny klucz intencji czasu, wspolny dla wszystkich producerow."""
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("time update event requires mapping payload")
    if event_type == "PICKUP_TIME_UPDATED" and payload.get(
        "committed_authority"
    ):
        key = payload.get("committed_pickup_event_key")
        hint = event.get("event_id_hint")
        if not isinstance(key, str) or not key.startswith(
            f"{order_id}_PICKUP_TIME_UPDATED_COMMITTED_"
        ):
            raise ValueError("committed pickup event requires canonical event key")
        if hint is not None and hint != key:
            raise ValueError("committed pickup event hint/key mismatch")
        return key

    transition_fields = {
        "CZAS_KURIERA_UPDATED": (
            "old_ck_iso",
            "old_ck_hhmm",
            "new_ck_iso",
            "new_ck_hhmm",
            "source",
        ),
        "PICKUP_TIME_UPDATED": (
            "old_pickup_at_warsaw",
            "new_pickup_at_warsaw",
            "old_prep_minutes",
            "new_prep_minutes",
            "new_decision_deadline",
            "new_zmiana_czasu_odbioru",
            "source",
            "assignment_event_id_at_observation",
            "courier_id_at_observation",
        ),
    }
    if event_type not in transition_fields:
        raise ValueError(f"unsupported time update event_type: {event_type!r}")
    selected_fields = transition_fields[event_type]
    if time_event_cas_artifact_present(event_type, payload):
        if not time_event_cas_is_versioned(event_type, payload):
            raise ValueError("malformed time CAS envelope")
        cas_fields = (
            TIME_EVENT_CAS_SCHEMA_FIELD,
            "status_at_observation",
            "assignment_event_id_at_observation",
            "courier_id_at_observation",
            (
                CK_CHANGE_REVISION_OBSERVATION_FIELD
                if event_type == "CZAS_KURIERA_UPDATED"
                else "pickup_time_revision_at_observation"
            ),
        )
        selected_fields = tuple(dict.fromkeys(selected_fields + cas_fields))
    transition = {field: payload.get(field) for field in selected_fields}
    digest = hashlib.sha256(
        json.dumps(
            transition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    suffix = event.get("event_id_suffix")
    if suffix:
        discriminator = str(suffix)
    else:
        delta_raw = payload.get("delta_min")
        if delta_raw is None:
            # Pierwsze przyjęcie nie ma matematycznego baseline'u. To legalna,
            # stabilna domena klucza, a nie liczba 0 ani wyjątek float(None).
            discriminator = "_NO_BASELINE"
        else:
            if isinstance(delta_raw, bool):
                raise ValueError("time update delta must be finite numeric")
            try:
                delta = float(delta_raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "time update delta must be finite numeric"
                ) from exc
            if not math.isfinite(delta):
                raise ValueError("time update delta must be finite numeric")
            discriminator = f"_{int(delta * 10)}"
    return f"{order_id}_{event_type}{discriminator}_to_{digest}"


def apply_event(
    event: Mapping[str, object],
    *,
    authority_policy: CommittedPickupPolicySnapshot | None = None,
):
    """Utrwal, zastosuj i domknij downstream jednego eventu czasu."""
    from dispatch_v2 import state_machine

    event = dict(event)
    event_type = str(event.get("event_type") or "")
    if event_type not in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}:
        raise ValueError(f"unsupported committed time event: {event_type!r}")
    order_id = str(event.get("order_id") or "")
    if not order_id:
        raise ValueError("time event requires order_id")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("time event requires payload")
    if authority_policy is not None and type(
        authority_policy
    ) is not CommittedPickupPolicySnapshot:
        raise TypeError(
            "authority_policy must be CommittedPickupPolicySnapshot"
        )

    # Granica transportu jest ostatnim miejscem, w którym surowy CK może
    # istnieć. Jeżeli wspólny resolver rozpozna legalną zmianę committed
    # czasówki, outbox od początku utrwala dokładny PICKUP_TIME_UPDATED — nie
    # dopiero jego inną, wewnętrzną interpretację w state_machine.
    if event_type == "CZAS_KURIERA_UPDATED":
        # Fail-closed: chwilowy błąd orders_state nie może zostać pomylony z
        # prawdziwym brakiem OID i utrwalić legalnej czasówki jako raw CK.
        # Ten sam strict reader wiąże później wersję state w durable outboxie.
        current = state_machine.get_order_strict(order_id)
        if authority_policy is not None and not isinstance(current, dict):
            raise ValueError(
                "pre_proposal authority requires existing strict state"
            )
        if isinstance(current, dict):
            resolved = state_machine.resolve_czasowka_ck_observation(
                current,
                dict(payload),
                policy_snapshot=authority_policy,
            )
            if (
                resolved.outcome is ResolutionOutcome.APPLY
                and isinstance(resolved.event, Mapping)
            ):
                event = dict(resolved.event)
                event_type = str(event.get("event_type") or "")
                order_id = str(event.get("order_id") or "")
                payload = event.get("payload")
                if not isinstance(payload, Mapping):
                    raise ValueError(
                        "canonical committed pickup requires payload"
                    )
            elif (
                authority_policy is not None
                and resolved.reason != "not_czasowka"
            ):
                raise ValueError(
                    "pre_proposal authority observation rejected: "
                    f"{resolved.reason}"
                )

    event_key = time_update_event_key(order_id, event)
    state_event_sealer = None
    effective_policy = authority_policy
    authority = payload.get("committed_authority")
    if authority:
        # Exact outbox jest pierwszym trwałym receipt'em tej intencji. Resume
        # musi nastąpić PRZED rewalidacją snapshotu: po poprawnym state apply
        # pickup revision jest już +1 i ponowne sprawdzenie proofu rewizji 0
        # błędnie uwięziłoby claim koordynatora przed jego ACK.
        durable_row = event_bus.get_latest_state_apply(event_key, order_id)
        durable_event = (
            durable_row.get("state_event")
            if isinstance(durable_row, dict)
            else None
        )
        if (
            isinstance(durable_event, Mapping)
            and str(durable_row.get("event_key") or "") == event_key
            and _authority_core(durable_event) == _authority_core(event)
            and verify_durable_authority_attestation(durable_event)
        ):
            return durable_event_apply.resume_exact(
                str(durable_row.get("event_id") or ""),
                state_update_fn=state_machine.update_from_event,
                effect_status_fn=state_machine.event_effect_status,
                get_order_fn=state_machine.get_order_strict,
                downstream_fn=lifecycle_downstream.apply,
            )

        current = state_machine.get_order_strict(order_id)
        receipt_verified = False
        proof = payload.get("committed_authority_proof")
        observation = (
            proof.get("observation") if isinstance(proof, Mapping) else None
        )
        if (
            authority == "coordinator_receipt"
            or (
                isinstance(observation, Mapping)
                and observation.get("source") == "coordinator_force"
            )
        ):
            from dispatch_v2 import coordinator_time_recheck

            receipt_verified = coordinator_time_recheck.verify_claimed_event(
                event
            )
        initial_intent_claimed = (
            payload.get(NEW_ORDER_TIME_INTENT_ID_FIELD) is not None
        )
        initial_intent_policy = _new_order_time_intent_policy(
            current,
            event,
        )
        initial_intent_verified = initial_intent_policy is not None
        if initial_intent_claimed and not initial_intent_verified:
            raise ValueError(
                "committed pickup NEW_ORDER receipt rejected"
            )
        coordinator_authority = bool(
            authority == "coordinator_receipt"
            or (
                isinstance(observation, Mapping)
                and observation.get("source") == "coordinator_force"
            )
        )
        if coordinator_authority:
            try:
                coordinator_policy = deserialize_coordinator_event_policy(
                    event
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "coordinator authority requires receipt policy"
                ) from exc
            effective_policy = coordinator_policy
        elif initial_intent_claimed:
            effective_policy = initial_intent_policy
        if effective_policy is None:
            raise ValueError(
                "committed pickup authority requires captured policy"
            )
        observed_source = payload.get("observed_source")
        validate_committed_time_policy_source(
            effective_policy, observed_source
        )
        if coordinator_authority:
            policy_authority_enabled = (
                effective_policy.coordinator_time_authority_enabled
            )
        else:
            policy_authority_enabled = effective_policy.authority_enabled
        if not policy_authority_enabled:
            if coordinator_authority:
                raise ValueError(
                    "coordinator policy cannot apply authority"
                )
            raise ValueError(
                "authority-disabled policy cannot apply committed authority"
            )
        passive_enabled = effective_policy.passive_guard_enabled
        manual_enabled = effective_policy.manual_passthrough_enabled
        forward_enabled = (
            effective_policy.rutcom_forward_authority_enabled
        )
        claim_authorized = bool(
            receipt_verified or initial_intent_verified
        )
        if not passive_enabled:
            raise ValueError(
                "committed pickup authority requires passive guard"
            )
        validation = validate_committed_pickup_event(
            current,
            event,
            is_czasowka=C.is_czasowka_order(
                project_time_event_order(current, event)
            ),
            # Exact queue claim is a journal, not a replacement authority bit.
            # Its v6 receipt carries the click-time lease through rollback.
            passive_guard_enabled=passive_enabled,
            manual_passthrough_enabled=manual_enabled,
            rutcom_forward_authority_enabled=forward_enabled,
            coordinator_receipt_verified=receipt_verified,
        )
        if (
            validation.outcome is not ResolutionOutcome.APPLY
            and not claim_authorized
        ):
            raise ValueError(
                f"committed pickup proof rejected: {validation.reason}"
            )
        # Exact claim jest już trwałą, kolejka-bound intencją. Jeżeli legalny
        # writer wygrał CAS po claimie, ale przed outboxem, nie wolno zostawić
        # nieusuwalnego headu ani próbować nadpisać nowszej prawdy. Utrwalamy
        # dokładny event z atestacją; wspólny FSM oracle oznaczy go terminalnie
        # ``superseded``. Bez exact receiptu ten sam stale event nadal failuje.
        state_event_sealer = _seal_authority_event
    elif effective_policy is not None:
        validate_committed_time_policy_source(
            effective_policy, payload.get("source")
        )

    state_event_metadata = None
    if effective_policy is not None:
        state_event_metadata = {
            COMMITTED_TIME_POLICY_SNAPSHOT_FIELD: (
                serialize_committed_time_policy(effective_policy)
            )
        }

    return durable_event_apply.emit_and_apply(
        event_type,
        order_id=order_id,
        courier_id=(
            str(event.get("courier_id"))
            if event.get("courier_id") is not None
            else None
        ),
        payload=dict(payload),
        state_payload=None,
        event_key=event_key,
        emit_fn=event_bus.emit_audit,
        state_update_fn=state_machine.update_from_event,
        effect_status_fn=state_machine.event_effect_status,
        get_order_fn=state_machine.get_order_strict,
        downstream_fn=lifecycle_downstream.apply,
        state_event_metadata=state_event_metadata,
        state_event_sealer=state_event_sealer,
        sweeper_enabled=None,
    )


__all__ = [
    "AUTHORITY_ATTESTATION_SCHEMA",
    "apply_event",
    "time_update_event_key",
    "verify_durable_authority_attestation",
    "verify_new_order_time_intent_receipt",
]
