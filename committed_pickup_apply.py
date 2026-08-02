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
    MANUAL_CK_AUTHORITY_FLAG,
    RUTCOM_FORWARD_AUTHORITY_FLAG,
    ResolutionOutcome,
    TIME_EVENT_CAS_SCHEMA_FIELD,
    time_event_cas_artifact_present,
    time_event_cas_is_versioned,
    validate_committed_pickup_event,
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


def apply_event(event: Mapping[str, object]):
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

    # Granica transportu jest ostatnim miejscem, w którym surowy CK może
    # istnieć. Jeżeli wspólny resolver rozpozna legalną zmianę committed
    # czasówki, outbox od początku utrwala dokładny PICKUP_TIME_UPDATED — nie
    # dopiero jego inną, wewnętrzną interpretację w state_machine.
    if event_type == "CZAS_KURIERA_UPDATED":
        # Fail-closed: chwilowy błąd orders_state nie może zostać pomylony z
        # prawdziwym brakiem OID i utrwalić legalnej czasówki jako raw CK.
        # Ten sam strict reader wiąże później wersję state w durable outboxie.
        current = state_machine.get_order_strict(order_id)
        if isinstance(current, dict):
            resolved = state_machine.resolve_czasowka_ck_observation(
                current,
                dict(payload),
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

    event_key = time_update_event_key(order_id, event)
    state_event_sealer = None
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

        passive_enabled = C.flag(
            "ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True
        )
        current = state_machine.get_order_strict(order_id)
        manual_enabled = C.decision_flag(MANUAL_CK_AUTHORITY_FLAG)
        forward_enabled = C.decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG)
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
        claim_authorized = bool(receipt_verified)
        if not (passive_enabled or claim_authorized):
            raise ValueError(
                "committed pickup authority requires passive guard"
            )
        validation = validate_committed_pickup_event(
            current,
            event,
            is_czasowka=C.is_czasowka_order(current),
            # Exact queue claim jest pierwszym trwalym journalem transakcji.
            # Domyka crash-window claim->outbox nawet po hot rollbacku; bez
            # claimu biezace flagi nadal sa bezwzglednie wymagane.
            passive_guard_enabled=(passive_enabled or claim_authorized),
            manual_passthrough_enabled=manual_enabled,
            rutcom_forward_authority_enabled=(
                forward_enabled or claim_authorized
            ),
            coordinator_receipt_verified=receipt_verified,
        )
        if (
            validation.outcome is not ResolutionOutcome.APPLY
            and not receipt_verified
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
        state_event_metadata=None,
        state_event_sealer=state_event_sealer,
        sweeper_enabled=None,
    )


__all__ = [
    "AUTHORITY_ATTESTATION_SCHEMA",
    "apply_event",
    "time_update_event_key",
    "verify_durable_authority_attestation",
]
