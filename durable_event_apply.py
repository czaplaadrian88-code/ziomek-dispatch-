"""Crash-consistent bridge: durable event -> orders_state -> downstream.

C3-01 wykazal, ze deterministyczny ``event_id`` deduplikuje tylko pierwszy
zapis. Ten modul opiera retry na atomowym outboxie event_bus, nigdy na samym
``emit() is None``. Dokladny state_event, expected ``updated_at``/storage token
i fazy state/downstream sa trwale. Cross-process lock wersjonuje zapis stanu, a
osobny FIFO-consumer wykonuje potencjalnie wolny plan/recanon juz poza lockiem.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from dispatch_v2 import event_bus, state_machine


_log = logging.getLogger("durable_event_apply")
_UNKNOWN_STATE_VERSION = "__STATE_READ_UNAVAILABLE__"
_UNKNOWN_STATE_MARKER = "__STATE_MARKER_UNAVAILABLE__"


@dataclass(frozen=True)
class DurableApplyOutcome:
    event_id: str
    event_key: str
    event_created: bool
    state_ready: bool
    state_transitioned: bool
    downstream_executed: bool
    superseded: bool = False
    failure_stage: Optional[str] = None
    state_event: Optional[dict] = None
    duplicate_of_event_id: Optional[str] = None
    # Oddziela wykonanie callbacku od terminalizacji receipt. Jawny
    # ``downstream_fn=None`` nie wykonuje callbacku, ale nadal moze poprawnie
    # przeprowadzic pending -> applied i musi wejsc do metryki sweepa.
    downstream_transitioned: bool = False


def _actual_event_id(
    event_key: str,
    expected_version: Optional[str],
    expected_marker: Optional[str],
    expected_token: Optional[str],
    predecessor_event_id: Optional[str],
    event: dict,
) -> str:
    """Generacja ID: retry tej samej wersji/payloadu jest stale, nowy cykl nie."""
    material = {
        "event_key": event_key,
        "expected_state_version": expected_version,
        "expected_state_marker": expected_marker,
        "expected_state_token": expected_token,
        "predecessor_event_id": predecessor_event_id,
        "state_event": event,
    }
    raw = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    suffix = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{event_key}_v{suffix}"


def _same_requested_intent(existing: object, requested: dict) -> bool:
    """Compare caller intent while ignoring the generated durable event_id."""
    if not isinstance(existing, dict):
        return False
    return all(
        existing.get(field) == requested.get(field)
        for field in ("event_type", "order_id", "courier_id", "payload")
    )


def _state_retry_in_cooldown(
    row: dict, retry_updated_before: Optional[str]
) -> bool:
    """Czy ten konkretny, już próbowany state receipt jest jeszcze świeży."""
    return bool(
        retry_updated_before is not None
        and int(row.get("state_attempts") or 0) > 0
        and str(row.get("updated_at") or "") > retry_updated_before
    )


def _invalid_state_event_reason(row: dict) -> Optional[str]:
    """Return a permanent payload error; None means the receipt is runnable."""
    event_id = str(row.get("event_id") or "")
    event_key = str(row.get("event_key") or "")
    order_id = str(row.get("order_id") or "")
    state_event = row.get("state_event")
    if not event_id:
        return "missing event_id"
    if not event_key:
        return "missing event_key"
    if not order_id:
        return "missing order_id"
    if not isinstance(state_event, dict):
        return "payload is not an object"
    if not str(state_event.get("event_type") or ""):
        return "missing event_type"
    if str(state_event.get("event_id") or "") != event_id:
        return "event_id mismatch"
    if str(state_event.get("order_id") or "") != order_id:
        return "order_id mismatch"
    if not isinstance(state_event.get("payload"), dict):
        return "payload field is not an object"
    return None


def _previous_courier_for_event(
    state_event: dict, current: Optional[dict]
) -> str:
    """Resolve immutable cleanup provenance before the state transition."""
    if state_event.get("previous_courier_id"):
        return str(state_event["previous_courier_id"])
    event_type = str(state_event.get("event_type") or "")
    current_cid = str((current or {}).get("courier_id") or "")
    if event_type == "COURIER_ASSIGNED":
        assigned_cid = str(state_event.get("courier_id") or "")
        return current_cid if current_cid and current_cid != assigned_cid else ""
    if event_type == "ORDER_RETURNED_TO_POOL":
        # Raw panel CID może już wskazywać trzecią wartość. Provenance planu,
        # który stan faktycznie opuszcza w tej atomowej tranzycji, pochodzi z
        # current; raw CID pozostaje osobno w samym evencie.
        return current_cid or str(state_event.get("courier_id") or "")
    return ""


def _current_version(current: Optional[dict]) -> Optional[str]:
    if not current:
        return None
    value = current.get("updated_at")
    return str(value) if value is not None else None


def _current_marker(current: Optional[dict]) -> Optional[str]:
    if not current:
        return None
    value = current.get("last_lifecycle_event_id")
    return str(value) if value is not None else None


def _state_marker_field(event: dict) -> str:
    event_type = "".join(
        ch.lower() if ch.isalnum() else "_"
        for ch in str(event.get("event_type") or "")
    ).strip("_")
    return f"last_lifecycle_event_id_{event_type}" if event_type else ""


def _has_exact_marker(event: dict, current: Optional[dict]) -> bool:
    marker_field = _state_marker_field(event)
    return bool(
        current
        and marker_field
        and event.get("event_id")
        and str(current.get(marker_field) or "") == str(event.get("event_id"))
    )


def _ensure_recovered_marker_durable(event_id: str) -> bool:
    """Przed receiptem recovery utrwal katalog po potencjalnym crash-window."""
    try:
        state_machine.ensure_state_directory_durable()
        return True
    except Exception as exc:
        error = f"state_durability:{type(exc).__name__}: {exc}"
        event_bus.record_state_apply_error(event_id, error)
        _log.error(
            "DURABLE_APPLY cannot fsync recovered state directory "
            f"event_id={event_id}: {error}"
        )
        return False


def _read_storage_token(event_id: str) -> Optional[str]:
    """Odczytaj zegarowo niezalezny token lub utrwal blad i zostaw pending."""
    try:
        return state_machine.state_storage_token()
    except Exception as exc:
        error = f"state_token:{type(exc).__name__}: {exc}"
        event_bus.record_state_apply_error(event_id, error)
        _log.error(
            "DURABLE_APPLY state storage token unavailable "
            f"event_id={event_id}: {error}; retry pending"
        )
        return None


def _outcome_from_row(
    row: dict,
    *,
    event_created: bool,
    state_ready: bool,
    state_transitioned: bool,
    downstream_executed: bool,
    downstream_transitioned: bool = False,
    superseded: bool = False,
    failure_stage: Optional[str] = None,
    duplicate_of_event_id: Optional[str] = None,
) -> DurableApplyOutcome:
    return DurableApplyOutcome(
        event_id=str(row.get("event_id") or ""),
        event_key=str(row.get("event_key") or ""),
        event_created=event_created,
        state_ready=state_ready,
        state_transitioned=state_transitioned,
        downstream_executed=downstream_executed,
        downstream_transitioned=downstream_transitioned,
        superseded=superseded,
        failure_stage=failure_stage,
        state_event=row.get("state_event") if isinstance(row.get("state_event"), dict) else None,
        duplicate_of_event_id=duplicate_of_event_id,
    )


def _process_state_row(
    row: dict,
    *,
    event_created: bool,
    state_update_fn: Callable[[dict], object],
    effect_status_fn: Callable[[dict, Optional[dict]], str],
    get_order_fn: Callable[[str], Optional[dict]],
) -> DurableApplyOutcome:
    event_id = str(row.get("event_id") or "")
    state_event = row.get("state_event")
    invalid_reason = _invalid_state_event_reason(row)
    if invalid_reason is not None:
        event_bus.mark_state_apply_invalid(event_id, invalid_reason)
        row = event_bus.get_state_apply_outbox(event_id) or row
        return _outcome_from_row(
            row,
            event_created=event_created,
            state_ready=False,
            state_transitioned=False,
            downstream_executed=False,
            superseded=True,
            failure_stage="outbox_payload",
        )

    state_status = row.get("state_status")
    if state_status == "superseded":
        return _outcome_from_row(
            row,
            event_created=event_created,
            state_ready=False,
            state_transitioned=False,
            downstream_executed=False,
            superseded=True,
        )

    predecessor_id = str(row.get("predecessor_event_id") or "")
    predecessor_applied = False
    predecessor_same_key = False
    if state_status != "applied" and predecessor_id:
        predecessor = event_bus.get_state_apply_outbox(predecessor_id)
        predecessor_status = (
            str(predecessor.get("state_status") or "") if predecessor else "missing"
        )
        predecessor_same_key = bool(
            predecessor
            and str(predecessor.get("event_key") or "")
            == str(row.get("event_key") or "")
        )
        if predecessor_status == "pending":
            event_bus.record_state_apply_error(
                event_id, f"waiting for predecessor {predecessor_id}"
            )
            row = event_bus.get_state_apply_outbox(event_id) or row
            return _outcome_from_row(
                row,
                event_created=event_created,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                failure_stage="state_predecessor",
            )
        if predecessor_status not in ("applied", "superseded"):
            event_bus.record_state_apply_error(
                event_id,
                f"unresolvable predecessor {predecessor_id}: {predecessor_status}",
            )
            row = event_bus.get_state_apply_outbox(event_id) or row
            return _outcome_from_row(
                row,
                event_created=event_created,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                failure_stage="state_predecessor",
            )
        predecessor_applied = predecessor_status == "applied"

    transitioned = False
    if state_status != "applied":
        oid = str(state_event.get("order_id") or "")
        try:
            current = get_order_fn(oid) if oid else None
        except Exception as exc:
            error = f"state_read:{type(exc).__name__}: {exc}"
            event_bus.record_state_apply_error(event_id, error)
            _log.error(
                f"DURABLE_APPLY state read failed oid={oid} event_id={event_id}: "
                f"{type(exc).__name__}: {exc}; retry pending"
            )
            row = event_bus.get_state_apply_outbox(event_id) or row
            return _outcome_from_row(
                row,
                event_created=event_created,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                failure_stage="state_read",
            )
        previous_courier_id = _previous_courier_for_event(state_event, current)
        if previous_courier_id and not state_event.get("previous_courier_id"):
            bound = event_bus.bind_state_apply_previous_courier(
                event_id, previous_courier_id
            )
            if bound is None or not isinstance(bound.get("state_event"), dict):
                event_bus.record_state_apply_error(
                    event_id, "cannot persist previous courier provenance"
                )
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    failure_stage="outbox_metadata",
                )
            row = bound
            state_event = bound["state_event"]
        expected = row.get("expected_state_version")
        expected_marker = row.get("expected_state_marker")
        expected_token = row.get("expected_state_token")
        actual = _current_version(current)
        actual_marker = _current_marker(current)
        unknown_expected = expected == _UNKNOWN_STATE_VERSION
        predecessor_is_current = bool(
            predecessor_applied
            and predecessor_id
            and actual_marker == predecessor_id
        )
        # Tylko exact marker predecessora może zastąpić nieznany baseline.
        # Dowolny *inny* marker mógł powstać już po utrwaleniu tego receiptu;
        # traktowanie go jako rzekomego A->B omijałoby storage-token fence i
        # pozwalało staremu A nadpisać późniejsze C. Legalny cykl A->B zapisany
        # przed receiptem nadal przechodzi przez niezmieniony expected_token.
        predecessor_allows_apply = predecessor_is_current
        if _has_exact_marker(state_event, current):
            # Marker jest czescia tego samego atomowego JSON rename co efekt
            # state. Ma pierwszenstwo nawet gdy pozniejszy legalny event
            # przesunal FSM do stanu, dla ktorego oracle zwraca superseded.
            # Bez tego crash po state commit, przed receiptem, gubil downstream
            # starszego eventu po nadejsciu T2.
            if not _ensure_recovered_marker_durable(event_id):
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    failure_stage="state_durability",
                )
            event_bus.mark_state_apply_applied(event_id)
            row = event_bus.get_state_apply_outbox(event_id) or row
            return _outcome_from_row(
                row,
                event_created=event_created,
                state_ready=row.get("state_status") == "applied",
                state_transitioned=False,
                downstream_executed=False,
            )
        token_unchanged = False
        if unknown_expected and not predecessor_allows_apply:
            actual_token = _read_storage_token(event_id)
            if actual_token is None:
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    failure_stage="state_token",
                )
            if not expected_token:
                # Event jest trwaly, ale oba niezalezne odczyty baseline zawiodly
                # przed jego commitem. Pozniejszy snapshot nie dowodzi, ze nie
                # bylo writera posredniego, wiec nie wolno ani aplikowac, ani
                # bezpowrotnie oznaczac intencji jako superseded. Osobne lane'y
                # state/downstream pozwalaja zachowac go pending bez blokowania
                # callbackow innych zlecen.
                event_bus.record_state_apply_error(
                    event_id,
                    "missing expected state storage token; "
                    "baseline cannot be proven",
                )
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    failure_stage="state_token_indeterminate",
                )
            token_unchanged = str(actual_token) == str(expected_token)
        try:
            effect = effect_status_fn(state_event, current)
        except Exception as exc:
            error = f"state_oracle:{type(exc).__name__}: {exc}"
            event_bus.record_state_apply_error(event_id, error)
            row = event_bus.get_state_apply_outbox(event_id) or row
            return _outcome_from_row(
                row,
                event_created=event_created,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                failure_stage="state_oracle",
            )
        if effect == "applied":
            if unknown_expected and predecessor_applied and predecessor_same_key:
                # Oba odczyty przed persist zawiodly, wiec fail-closed powstal
                # successor. Recovery widzi jednak, ze dokladny efekt requestu
                # juz istnieje. To retry poprzednika, nie nowy cykl: successor
                # zostaje trwale pominiety, a caller ma domknac stary callback.
                event_bus.mark_state_apply_superseded(
                    event_id,
                    f"same_key_predecessor_effect_is_current {predecessor_id}",
                )
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    superseded=True,
                    duplicate_of_event_id=predecessor_id,
                )
            baseline_unchanged = bool(
                not unknown_expected
                and expected == actual
                and expected_marker == actual_marker
            )
            if baseline_unchanged or (unknown_expected and token_unchanged):
                # Exact marker dowodzi crashu po commicie state. Rowna wersja
                # obsluguje stan, ktory juz spelnial efekt przed ta generacja.
                event_bus.mark_state_apply_applied(event_id)
            else:
                # Sam zgodny status nie dowodzi, ze zastosowano TEN event;
                # nowszy writer mogl dojsc do tego samego statusu z innym T2.
                event_bus.mark_state_apply_superseded(
                    event_id,
                    "same_effect_newer_version_without_exact_marker "
                    f"expected={expected!r} actual={actual!r}",
                )
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    superseded=True,
                )
        elif effect == "superseded":
            event_bus.mark_state_apply_superseded(event_id, "fsm_superseded")
            row = event_bus.get_state_apply_outbox(event_id) or row
            return _outcome_from_row(
                row,
                event_created=event_created,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                superseded=True,
            )
        else:
            if unknown_expected and not token_unchanged and not predecessor_allows_apply:
                # Nieznany strict-read + zmienione bajty nie pozwalaja odroznic
                # ortogonalnego RMW od pozniejszego lifecycle eventu. Nie
                # nadpisuj ani nie zgaduj superseded: operator/retry musi
                # rozstrzygnac stan, a intencja pozostaje w outboxie.
                event_bus.record_state_apply_error(
                    event_id,
                    "state storage token changed after unreadable pre-emit state",
                )
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    failure_stage="state_version",
                )
            if (
                not unknown_expected
                and not predecessor_is_current
                and expected_marker != actual_marker
            ):
                # Zmiana samej wersji moze byc ortogonalnym RMW. Zmiana
                # globalnego durable markera dowodzi jednak pozniejszego
                # lifecycle commitu i starego T1 nie wolno nadpisac.
                event_bus.mark_state_apply_superseded(
                    event_id,
                    "lifecycle_marker_changed "
                    f"expected={expected_marker!r} actual={actual_marker!r}",
                )
                row = event_bus.get_state_apply_outbox(event_id) or row
                return _outcome_from_row(
                    row,
                    event_created=event_created,
                    state_ready=False,
                    state_transitioned=False,
                    downstream_executed=False,
                    superseded=True,
                )
            # Dla znanej wersji sama zmiana updated_at nie jest semantycznym
            # wyparciem: touch/waiting_at i inne ortogonalne RMW legalnie
            # zmieniaja wersje. Trzymany lifecycle lock zamyka nowe TOCTOU, a
            # trojstanowy oracle powyzej jest jedynym prawem do superseded.
            try:
                state_update_fn(state_event)
            except Exception as exc:
                try:
                    current_after = get_order_fn(oid) if oid else None
                    post = effect_status_fn(state_event, current_after)
                except Exception as post_exc:
                    error = (
                        f"{type(exc).__name__}: {exc}; postcheck:"
                        f"{type(post_exc).__name__}: {post_exc}"
                    )
                    event_bus.record_state_apply_error(event_id, error)
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        failure_stage="state_postcheck",
                    )
                post_actual = _current_version(current_after)
                post_token = (
                    _read_storage_token(event_id)
                    if unknown_expected and not predecessor_allows_apply
                    else None
                )
                if unknown_expected and not predecessor_allows_apply and post_token is None:
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        failure_stage="state_token",
                    )
                post_changed = (
                    (
                        True
                        if predecessor_allows_apply
                        else str(post_token) != str(expected_token)
                    )
                    if unknown_expected
                    else expected != post_actual
                )
                if post == "applied":
                    if _has_exact_marker(state_event, current_after):
                        if not _ensure_recovered_marker_durable(event_id):
                            row = event_bus.get_state_apply_outbox(event_id) or row
                            return _outcome_from_row(
                                row,
                                event_created=event_created,
                                state_ready=False,
                                state_transitioned=False,
                                downstream_executed=False,
                                failure_stage="state_durability",
                            )
                        event_bus.mark_state_apply_applied(event_id)
                        transitioned = True
                        _log.warning(
                            "DURABLE_APPLY apply raised after exact commit "
                            f"oid={oid} event_id={event_id}: "
                            f"{type(exc).__name__}: {exc}"
                        )
                    elif post_changed is True:
                        event_bus.mark_state_apply_superseded(
                            event_id,
                            "apply_exception_effect_from_other_writer "
                            f"expected={expected!r} actual={post_actual!r}",
                        )
                        row = event_bus.get_state_apply_outbox(event_id) or row
                        return _outcome_from_row(
                            row,
                            event_created=event_created,
                            state_ready=False,
                            state_transitioned=False,
                            downstream_executed=False,
                            superseded=True,
                        )
                    else:
                        event_bus.record_state_apply_error(
                            event_id,
                            "apply raised; matching effect lacks exact marker",
                        )
                        row = event_bus.get_state_apply_outbox(event_id) or row
                        return _outcome_from_row(
                            row,
                            event_created=event_created,
                            state_ready=False,
                            state_transitioned=False,
                            downstream_executed=False,
                            failure_stage="state_postcheck",
                        )
                elif post == "superseded":
                    event_bus.mark_state_apply_superseded(
                        event_id,
                        f"apply_exception_then_superseded:{type(exc).__name__}",
                    )
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        superseded=True,
                    )
                else:
                    error = f"{type(exc).__name__}: {exc}"
                    event_bus.record_state_apply_error(event_id, error)
                    _log.error(
                        f"DURABLE_APPLY state failed oid={oid} event_id={event_id}: "
                        f"{error}; retry pending"
                    )
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        failure_stage="state_apply",
                    )
            else:
                try:
                    current_after = get_order_fn(oid) if oid else None
                    post = effect_status_fn(state_event, current_after)
                except Exception as exc:
                    error = f"postcheck:{type(exc).__name__}: {exc}"
                    event_bus.record_state_apply_error(event_id, error)
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=transitioned,
                        downstream_executed=False,
                        failure_stage="state_postcheck",
                    )
                post_actual = _current_version(current_after)
                post_token = (
                    _read_storage_token(event_id)
                    if unknown_expected and not predecessor_allows_apply
                    else None
                )
                if unknown_expected and not predecessor_allows_apply and post_token is None:
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        failure_stage="state_token",
                    )
                post_changed = (
                    (
                        True
                        if predecessor_allows_apply
                        else str(post_token) != str(expected_token)
                    )
                    if unknown_expected
                    else expected != post_actual
                )
                if post == "applied":
                    if _has_exact_marker(state_event, current_after):
                        event_bus.mark_state_apply_applied(event_id)
                        transitioned = True
                    elif post_changed is True:
                        event_bus.mark_state_apply_superseded(
                            event_id,
                            "nonthrowing_effect_from_other_writer "
                            f"expected={expected!r} actual={post_actual!r}",
                        )
                        row = event_bus.get_state_apply_outbox(event_id) or row
                        return _outcome_from_row(
                            row,
                            event_created=event_created,
                            state_ready=False,
                            state_transitioned=False,
                            downstream_executed=False,
                            superseded=True,
                        )
                    else:
                        event_bus.record_state_apply_error(
                            event_id,
                            "matching postcondition lacks exact marker",
                        )
                        row = event_bus.get_state_apply_outbox(event_id) or row
                        return _outcome_from_row(
                            row,
                            event_created=event_created,
                            state_ready=False,
                            state_transitioned=False,
                            downstream_executed=False,
                            failure_stage="state_postcheck",
                        )
                elif post == "superseded":
                    event_bus.mark_state_apply_superseded(
                        event_id, "nonthrowing_apply_superseded"
                    )
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        superseded=True,
                    )
                else:
                    # C3 blind review: non-throwing no-op NIE jest sukcesem.
                    event_bus.record_state_apply_error(
                        event_id, "postcondition pending after non-throwing update"
                    )
                    row = event_bus.get_state_apply_outbox(event_id) or row
                    return _outcome_from_row(
                        row,
                        event_created=event_created,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        failure_stage="state_postcondition",
                    )

    row = event_bus.get_state_apply_outbox(event_id) or row
    return _outcome_from_row(
        row,
        event_created=event_created,
        state_ready=row.get("state_status") == "applied",
        state_transitioned=transitioned,
        downstream_executed=False,
    )


def _finish_downstream(
    state_outcome: DurableApplyOutcome,
    downstream_fn: Optional[Callable[[dict], object]],
    *,
    max_rows: int = 100,
    updated_before: Optional[str] = None,
    include_event_versions: Optional[dict[str, tuple[str, int]]] = None,
    exclude_event_ids: Optional[set[str]] = None,
    expected_first_event_id: Optional[str] = None,
    expected_first_version: Optional[tuple[str, int]] = None,
    defer_when_no_eligible_row: bool = False,
) -> DurableApplyOutcome:
    """Domknij target po fair kolejce gotowych callbacków.

    Callback jest at-least-once: crash po jego efekcie, ale przed receiptem,
    oznacza swiadome powtorzenie. Osobny cross-process lock nie pozwala dwom
    konsumentom wykonac tego samego pending row jednoczesnie. Reentrant call z
    callbacku tylko zostawia nowy target pending; zewnetrzny consumer albo
    kolejny drain bezpiecznie go podejmie bez rekursji.
    """
    if (
        not state_outcome.event_id
        or not state_outcome.state_ready
        or state_outcome.superseded
    ):
        return state_outcome

    target_id = state_outcome.event_id
    with state_machine.lifecycle_downstream_lock() as owns_consumer:
        target = event_bus.get_state_apply_outbox(target_id)
        if target is None:
            return replace(
                state_outcome,
                downstream_executed=False,
                failure_stage="downstream",
            )
        if not owns_consumer:
            # Ten sam watek jest juz wewnatrz callbacku starszego eventu. Nie
            # wywoluj go rekursywnie; pending receipt jest trwalym zleceniem.
            return _outcome_from_row(
                target,
                event_created=state_outcome.event_created,
                state_ready=target.get("state_status") == "applied",
                state_transitioned=state_outcome.state_transitioned,
                downstream_executed=False,
                superseded=target.get("state_status") == "superseded",
                failure_stage=(
                    "downstream"
                    if target.get("downstream_status") == "pending"
                    else None
                ),
            )

        for _ in range(max(1, int(max_rows))):
            target = event_bus.get_state_apply_outbox(target_id)
            if target is None:
                return replace(
                    state_outcome,
                    downstream_executed=False,
                    failure_stage="downstream",
                )
            if target.get("state_status") == "superseded":
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=False,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                    superseded=True,
                )
            if target.get("downstream_status") == "applied":
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=target.get("state_status") == "applied",
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                )
            if target.get("state_status") != "applied":
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=False,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                    failure_stage="downstream",
                )
            if (
                expected_first_event_id is not None
                and expected_first_version is not None
                and (
                    str(target.get("updated_at") or ""),
                    int(target.get("downstream_attempts") or 0),
                )
                != expected_first_version
            ):
                # Outer selector działa przed cross-process lockiem. Sam zegar
                # nie jest tokenem CAS (może stać/cofnąć się); licznik prób
                # wykrywa konkurencyjny begin/error nawet przy identycznym ISO.
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=True,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                )

            oldest = event_bus.get_oldest_pending_downstream(
                updated_before=updated_before,
                include_event_versions=include_event_versions,
                exclude_event_ids=exclude_event_ids,
            )
            if oldest is None:
                if expected_first_event_id is not None or defer_when_no_eligible_row:
                    # Sweeper wybral target przed lockiem. Foreground mogl go
                    # w miedzyczasie domknac albo odswiezyc po bledzie; brak
                    # aktualnie uprawnionego row nie jest awaria receiptu.
                    target = event_bus.get_state_apply_outbox(target_id) or target
                    return _outcome_from_row(
                        target,
                        event_created=state_outcome.event_created,
                        state_ready=target.get("state_status") == "applied",
                        state_transitioned=state_outcome.state_transitioned,
                        downstream_executed=False,
                        superseded=target.get("state_status") == "superseded",
                    )
                error = "downstream:pending target missing from FIFO"
                event_bus.record_state_apply_downstream_error(target_id, error)
                _log.error(
                    f"DURABLE_APPLY {error} event_id={target_id}"
                )
                target = event_bus.get_state_apply_outbox(target_id) or target
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=True,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                    failure_stage="downstream",
                )

            oldest_id = str(oldest.get("event_id") or "")
            if (
                expected_first_event_id is not None
                and oldest_id != expected_first_event_id
            ):
                # Selekcja sprzed locka stracila aktualnosc. Nie wykonuj B i
                # nie przypisuj jego wyniku/metryk do A; kolejna iteracja
                # sweepa wybierze aktualny head juz jako wlasny target.
                target = event_bus.get_state_apply_outbox(target_id) or target
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=target.get("state_status") == "applied",
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                    superseded=target.get("state_status") == "superseded",
                )
            oldest_event = oldest.get("state_event")
            invalid_reason = _invalid_state_event_reason(oldest)
            if invalid_reason is not None:
                event_bus.mark_state_apply_invalid(oldest_id, invalid_reason)
                _log.error(
                    "DURABLE_APPLY downstream FIFO skipped invalid state_event "
                    f"event_id={oldest_id or '<missing>'}; target={target_id}; "
                    f"reason={invalid_reason}"
                )
                if expected_first_event_id is not None:
                    invalid = event_bus.get_state_apply_outbox(oldest_id) or oldest
                    return _outcome_from_row(
                        invalid,
                        event_created=state_outcome.event_created,
                        state_ready=False,
                        state_transitioned=state_outcome.state_transitioned,
                        downstream_executed=False,
                        superseded=True,
                    )
                continue

            if oldest.get("state_status") != "applied":
                # Query kontraktowo zwraca tylko ready rows. Fail loud zamiast
                # przywracać head-of-line blocking przez niegotowy state lane.
                event_bus.record_state_apply_downstream_error(
                    oldest_id, "downstream selector returned non-applied state"
                )
                return replace(
                    state_outcome,
                    downstream_executed=False,
                    failure_stage="downstream",
                )

            if downstream_fn is None and oldest_id != target_id:
                # None jest jawna deklaracja no-op TYLKO dla targetu tego
                # call-site'u. Nie wolno nia cicho zamknac starszego receipt,
                # ktory mogl wymagac prawdziwego routera plan/recanon.
                _log.error(
                    "DURABLE_APPLY explicit no-op cannot consume older FIFO row "
                    f"event_id={oldest_id}; target={target_id} remains pending"
                )
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=True,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                    failure_stage="downstream",
                )

            try:
                if downstream_fn is not None:
                    attempt_no = event_bus.begin_state_apply_downstream(oldest_id)
                    if attempt_no is None:
                        raise RuntimeError("downstream attempt compare-and-set failed")
                    callback_event = oldest_event
                    if (
                        isinstance(oldest_event, dict)
                        and oldest_event.get("event_type") == "PICKUP_TIME_UPDATED"
                    ):
                        # ``created_at`` receiptu jest trwalym observed_at
                        # pierwszej detekcji. Nie wkladamy zegara call-site'u do
                        # payloadu (retry tego samego przejscia musi zachowac
                        # identyczna intencje/hash), ale downstream zawsze
                        # dostaje ten sam receipt-bound timestamp, takze po crashu.
                        callback_event = dict(oldest_event)
                        callback_event["durable_observed_at"] = oldest.get(
                            "created_at"
                        )
                    downstream_fn(callback_event)
                marked = event_bus.mark_state_apply_downstream(oldest_id)
            except Exception as exc:
                error = f"downstream:{type(exc).__name__}: {exc}"
                try:
                    event_bus.record_state_apply_downstream_error(oldest_id, error)
                except Exception:
                    _log.exception(
                        "DURABLE_APPLY could not persist downstream error "
                        f"event_id={oldest_id}"
                    )
                _log.error(
                    "DURABLE_APPLY downstream failed "
                    f"event_id={oldest_id} target={target_id}: {error}"
                )
                target = event_bus.get_state_apply_outbox(target_id) or target
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=True,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=False,
                    failure_stage="downstream",
                )

            if not marked:
                completed = event_bus.get_state_apply_outbox(oldest_id)
                if not completed or completed.get("downstream_status") != "applied":
                    error = "downstream:receipt compare-and-set failed"
                    event_bus.record_state_apply_downstream_error(oldest_id, error)
                    _log.error(
                        f"DURABLE_APPLY {error} event_id={oldest_id}; "
                        f"target={target_id} blocked"
                    )
                    target = event_bus.get_state_apply_outbox(target_id) or target
                    return _outcome_from_row(
                        target,
                        event_created=state_outcome.event_created,
                        state_ready=True,
                        state_transitioned=state_outcome.state_transitioned,
                        downstream_executed=False,
                        failure_stage="downstream",
                    )

            if oldest_id == target_id:
                target = event_bus.get_state_apply_outbox(target_id) or target
                return _outcome_from_row(
                    target,
                    event_created=state_outcome.event_created,
                    state_ready=True,
                    state_transitioned=state_outcome.state_transitioned,
                    downstream_executed=downstream_fn is not None,
                    downstream_transitioned=bool(marked),
                )

        _log.error(
            "DURABLE_APPLY downstream FIFO budget exhausted "
            f"before target={target_id} max_rows={max_rows}"
        )
        target = event_bus.get_state_apply_outbox(target_id) or target
        return _outcome_from_row(
            target,
            event_created=state_outcome.event_created,
            state_ready=True,
            state_transitioned=state_outcome.state_transitioned,
            downstream_executed=False,
            failure_stage="downstream",
        )


def _emit_and_apply_state_phase(
    event_type: str,
    *,
    order_id: str,
    courier_id: Optional[str],
    payload: Optional[dict],
    state_payload: Optional[dict],
    event_key: str,
    emit_fn: Callable[..., Optional[str]],
    state_update_fn: Callable[[dict], object],
    effect_status_fn: Callable[[dict, Optional[dict]], str],
    get_order_fn: Callable[[str], Optional[dict]],
    state_event_metadata: Optional[dict] = None,
    retry_updated_before: Optional[str] = None,
) -> DurableApplyOutcome:
    """Faza pod lifecycle lockiem: utrwal event i domknij exact state."""
    oid = str(order_id)
    requested_event = {
        "event_type": event_type,
        "order_id": oid,
        "courier_id": courier_id,
        "payload": (payload or {}) if state_payload is None else (state_payload or {}),
    }
    if state_event_metadata:
        reserved = set(requested_event) | {"event_id", "previous_courier_id"}
        collisions = reserved.intersection(state_event_metadata)
        if collisions:
            raise ValueError(
                "state_event_metadata overrides reserved fields: "
                + ", ".join(sorted(collisions))
            )
        requested_event.update(state_event_metadata)
    plan_callback_events = {
        "COURIER_ASSIGNED",
        "COURIER_DELIVERED",
        "COURIER_PICKED_UP",
        "ORDER_RETURNED_TO_POOL",
        "ORDER_RESURRECTED",
        "CZAS_KURIERA_UPDATED",
        "PICKUP_TIME_UPDATED",
    }
    recanon_events = {
        "COURIER_ASSIGNED",
        "COURIER_DELIVERED",
        "COURIER_PICKED_UP",
        "ORDER_RETURNED_TO_POOL",
    }
    if event_type in plan_callback_events:
        # Flagi autoryzuja NOWA intencje, nie jej pojedyncza probe. Markery sa
        # czescia exact state_event w tej samej transakcji SQLite co receipt:
        # pozniejszy flip nie moze przerwac callbacku po trwalym remove_stops,
        # a przed recanon/receiptem.
        # Jawne false odroznia nowy, fail-closed receipt od legacy row bez
        # markera (legacy zachowuje odczyt biezacej flagi przez None).
        requested_event["saved_plans_authorized"] = False
        try:
            from dispatch_v2.common import ENABLE_SAVED_PLANS
            requested_event["saved_plans_authorized"] = bool(
                ENABLE_SAVED_PLANS
            )
        except Exception:
            pass

        if event_type in recanon_events:
            requested_event["recanon_on_write_authorized"] = False
            try:
                from dispatch_v2.common import decision_flag

                requested_event["recanon_on_write_authorized"] = decision_flag(
                    "ENABLE_RECANON_ON_WRITE"
                )
            except Exception:
                pass

        if event_type in {"COURIER_ASSIGNED", "COURIER_PICKED_UP"}:
            requested_event["immediate_redecide_authorized"] = False
            try:
                from dispatch_v2.common import decision_flag

                redecide_flag = (
                    "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE"
                    if event_type == "COURIER_ASSIGNED"
                    else "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP"
                )
                requested_event["immediate_redecide_authorized"] = decision_flag(
                    redecide_flag
                )
            except Exception:
                pass

        if event_type == "COURIER_ASSIGNED":
            requested_event["panel_agree_authorized"] = False
            try:
                from dispatch_v2.common import flag

                requested_event["panel_agree_authorized"] = flag(
                    "ENABLE_PANEL_AGREE",
                    os.environ.get("ENABLE_PANEL_AGREE", "1") != "0",
                )
            except Exception:
                pass

        if event_type in {
            "COURIER_ASSIGNED",
            "ORDER_RETURNED_TO_POOL",
            "ORDER_RESURRECTED",
        }:
            requested_event["invalidate_plan_on_bag_change_authorized"] = False
            try:
                from dispatch_v2.common import flag

                requested_event[
                    "invalidate_plan_on_bag_change_authorized"
                ] = flag("ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE", True)
            except Exception:
                pass

        if event_type in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}:
            requested_event["committed_invalidates_view_authorized"] = False
            try:
                from dispatch_v2.common import flag

                requested_event[
                    "committed_invalidates_view_authorized"
                ] = flag("ENABLE_COMMITTED_INVALIDATES_VIEW", True)
            except Exception:
                pass

        if event_type == "PICKUP_TIME_UPDATED":
            # Shadow reclaim jest wlasnoscia TEJ durable intencji. Flip po
            # enqueue nie moze dopisac starego eventu ani przerwac rozpoczetego
            # callbacku. LIVE marker tez jest utrwalony, lecz w tym etapie nie
            # ma zadnego konsumenta emitujacego write/event reclaim.
            requested_event["czasowka_reclaim_shadow_authorized"] = False
            requested_event["czasowka_reclaim_live_authorized"] = False
            try:
                from dispatch_v2.common import decision_flag

                requested_event["czasowka_reclaim_shadow_authorized"] = (
                    decision_flag("ENABLE_CZASOWKA_RECLAIM_SHADOW")
                )
                requested_event["czasowka_reclaim_live_authorized"] = (
                    decision_flag("ENABLE_CZASOWKA_RECLAIM_LIVE")
                )
            except Exception:
                pass

        if event_type in {"COURIER_ASSIGNED", "ORDER_RETURNED_TO_POOL"}:
            marker = (
                "reassign_old_plan_release_authorized"
                if event_type == "COURIER_ASSIGNED"
                else "return_previous_cleanup_authorized"
            )
            # Caller może związać snapshot CID i autoryzację jednym odczytem
            # ticka. Nie mieszaj go z drugim hot-readem w fazie receipt, bo
            # flip OFF->ON pomiędzy nimi tworzył marker ON bez snapshot CID.
            if marker not in requested_event:
                try:
                    from dispatch_v2.common import decision_flag

                    if decision_flag("ENABLE_REASSIGN_OLD_PLAN_RELEASE"):
                        requested_event[marker] = True
                except Exception:
                    # Brak wiarygodnego odczytu = brak rozszerzenia zachowania.
                    # Sam lifecycle event nadal musi zostac utrwalony i zastosowany.
                    pass

    with state_machine.lifecycle_apply_lock():
        predecessor_event_id: Optional[str] = None
        # An older pending T1/A and its already persisted successor T2/B may
        # share one semantic key. Exact B retry must resume T2, not select T1
        # by rowid and fork T3. Fall back to the historical oldest unresolved
        # row only when no pending generation matches the requested intent.
        pending_for_key = event_bus.list_pending_state_applies_for_key(
            event_key, oid
        )
        matching_pending = next(
            (
                candidate
                for candidate in reversed(pending_for_key)
                if _same_requested_intent(
                    candidate.get("state_event"), requested_event
                )
            ),
            None,
        )
        pending_tail = event_bus.get_latest_pending_state_apply_for_order(oid)
        requested_unresolved = (
            matching_pending
            or event_bus.get_unresolved_state_apply(event_key, oid)
        )
        matching_is_tail = bool(
            requested_unresolved
            and pending_tail
            and str(requested_unresolved.get("event_id") or "")
            == str(pending_tail.get("event_id") or "")
        )
        if (
            requested_unresolved is not None
            and pending_tail is not None
            and not matching_is_tail
        ):
            # A1 -> B(pending) -> A jest nowa intencja A2, nie retry A1.
            # Dotyczy zarowno A1 state=pending, jak i A1 state=applied z
            # oczekujacym callbackiem. Arrival order pod lifecycle lockiem jest
            # generational tokenem; wznowienie A1 zgubiloby ostatnia obserwacje,
            # a drain zakonczylby stanem B.
            requested_unresolved = None
            predecessor_event_id = (
                str((pending_tail or {}).get("event_id") or "") or None
            )
        if requested_unresolved is not None:
            if requested_unresolved.get("state_status") == "pending":
                # Retry exact payloadu wznawia T1. Inny payload pod tym samym
                # semantic key jest osobna intencja: najpierw rozstrzygnij T1,
                # ale nie gub T2 nawet gdy T1 zostanie superseded dopiero teraz.
                same_intent = _same_requested_intent(
                    requested_unresolved.get("state_event"), requested_event
                )
                state_retry_deferred = _state_retry_in_cooldown(
                    requested_unresolved, retry_updated_before
                )
                if state_retry_deferred:
                    unresolved_outcome = _outcome_from_row(
                        requested_unresolved,
                        event_created=False,
                        state_ready=False,
                        state_transitioned=False,
                        downstream_executed=False,
                        failure_stage="state_cooldown",
                    )
                else:
                    unresolved_outcome = _process_state_row(
                        requested_unresolved,
                        event_created=False,
                        state_update_fn=state_update_fn,
                        effect_status_fn=effect_status_fn,
                        get_order_fn=get_order_fn,
                    )
                if same_intent:
                    return unresolved_outcome
                predecessor_event_id = (
                    str(requested_unresolved.get("event_id") or "") or None
                )
                if not state_retry_deferred and unresolved_outcome.state_ready:
                    try:
                        after_unresolved = get_order_fn(oid)
                        same_epoch = bool(
                            _current_marker(after_unresolved)
                            == unresolved_outcome.event_id
                        )
                        if (
                            same_epoch
                            and effect_status_fn(requested_event, after_unresolved)
                            == "applied"
                        ):
                            # T1 dopiero co zastosowal ten sam efekt w tym samym
                            # lifecycle epoch. Rozny provenance/payload nie tworzy
                            # drugiego callbacku bez realnego cyklu stanu.
                            return unresolved_outcome
                    except Exception:
                        # Brak oracle nie moze zgubic one-shot T2. Dalsza sciezka
                        # utrwali successor fail-closed z zaleznoscia od T1.
                        pass

            # ``applied/downstream=pending`` moze byc retry tej samej intencji
            # ALBO stara generacja po pelnym cyklu A->B->A. Aktualny stan jest
            # rozstrzygajacy: zgodny efekt domyka stary receipt, brak efektu
            # wymaga utrwalenia nowej generacji pod tym samym semantic key.
            if requested_unresolved.get("state_status") != "pending":
                unresolved_effect = None
                try:
                    unresolved_current = get_order_fn(oid)
                    unresolved_effect = effect_status_fn(
                        requested_event, unresolved_current
                    )
                except Exception as exc:
                    _log.error(
                        "DURABLE_APPLY unresolved-generation oracle unavailable "
                        f"oid={oid} key={event_key}: {type(exc).__name__}: {exc}; "
                        "persisting fail-closed successor"
                    )
                if unresolved_effect == "applied":
                    return _process_state_row(
                        requested_unresolved,
                        event_created=False,
                        state_update_fn=state_update_fn,
                        effect_status_fn=effect_status_fn,
                        get_order_fn=get_order_fn,
                    )
                predecessor_event_id = (
                    str(requested_unresolved.get("event_id") or "") or None
                )

        older = event_bus.get_pending_state_apply_for_order(oid)
        if older is not None:
            # Najpierw sprobuj domknac T1, o ile nie trwa cooldown workera.
            # T2 MUSI mimo to powstac trwale (one-shot call-site nie moze go
            # zgubic), ale jego state apply dostaje jawna zaleznosc od T1.
            if str(older.get("event_id") or "") != str(
                predecessor_event_id or ""
            ) and not _state_retry_in_cooldown(
                older, retry_updated_before
            ):
                _process_state_row(
                    older,
                    event_created=False,
                    state_update_fn=state_update_fn,
                    effect_status_fn=effect_status_fn,
                    get_order_fn=get_order_fn,
                )
            # Po pierwszym T1 w kolejce moga juz czekac T2...Tn. Nowa intencja
            # zalezy od ostatniej trwalej intencji, inaczej fair retry moglby
            # zastosowac T3 przed T2 po odblokowaniu wspolnego T1.
            pending_tail = event_bus.get_latest_pending_state_apply_for_order(oid)
            if pending_tail is not None:
                predecessor_event_id = str(pending_tail.get("event_id") or "") or None

        state_read_failed = False
        try:
            current = get_order_fn(oid)
        except Exception as exc:
            # Najwazniejszy trigger C3-01: event musi zostac utrwalony nawet,
            # gdy strict-read state chwilowo odmawia aplikacji. Specjalna wersja
            # pozwala recovery pozniej rozstrzygnac bez zgadywania po zegarze.
            current = None
            state_read_failed = True
            _log.error(
                f"DURABLE_APPLY pre-emit state read failed oid={oid}: "
                f"{type(exc).__name__}: {exc}; persisting pending outbox"
            )
        latest = event_bus.get_latest_state_apply(event_key, oid)
        latest_is_closed = (
            not state_read_failed
            and latest is not None
            and latest.get("state_status") == "applied"
            and latest.get("downstream_status") == "applied"
        )
        latest_effect_applied = False
        if latest_is_closed:
            try:
                latest_effect_applied = (
                    effect_status_fn(requested_event, current) == "applied"
                )
            except Exception as exc:
                _log.error(
                    f"DURABLE_APPLY duplicate oracle failed oid={oid}: "
                    f"{type(exc).__name__}: {exc}; persisting pending generation"
                )
        if latest_is_closed and latest_effect_applied:
            # Zwykly duplikat po domknieciu obu faz; nie tworz nowej generacji.
            return _outcome_from_row(
                latest,
                event_created=False,
                state_ready=True,
                state_transitioned=False,
                downstream_executed=False,
            )

        expected_version = (
            _UNKNOWN_STATE_VERSION
            if state_read_failed
            else _current_version(current)
        )
        expected_marker = (
            _UNKNOWN_STATE_MARKER
            if state_read_failed
            else _current_marker(current)
        )
        expected_token = None
        if state_read_failed:
            try:
                expected_token = state_machine.state_storage_token()
            except Exception as exc:
                # Sam event nadal musi byc trwaly. NULL token wymusza później
                # fail-closed pending zamiast ryzykownego overwrite.
                _log.error(
                    "DURABLE_APPLY pre-emit state token unavailable "
                    f"oid={oid}: {type(exc).__name__}: {exc}; persisting pending"
                )
        if event_bus.has_matching_legacy_source_without_outbox(
            event_key,
            event_type,
            oid,
            courier_id,
            payload or {},
        ):
            # Pre-C3 writers used K directly. Reusing it lets emit/emit_audit
            # atomically attach the missing outbox instead of forking K_v… .
            actual_id = event_key
        else:
            actual_id = _actual_event_id(
                event_key,
                expected_version,
                expected_marker,
                expected_token,
                predecessor_event_id,
                requested_event,
            )
        state_event = dict(requested_event)
        state_event["event_id"] = actual_id
        if not state_read_failed:
            previous_courier_id = _previous_courier_for_event(
                state_event, current
            )
            if previous_courier_id:
                # Provenance cleanupu należy do TEJ SAMEJ transakcji SQLite co
                # source event i receipt. Post-emit bind pozostaje wyłącznie
                # ścieżką recovery dla legacy/unknown-read rows.
                state_event["previous_courier_id"] = previous_courier_id
        try:
            created_id = emit_fn(
                event_type,
                order_id=oid,
                courier_id=courier_id,
                payload=payload or {},
                event_id=actual_id,
                state_event=state_event,
                event_key=event_key,
                expected_state_version=expected_version,
                expected_state_marker=expected_marker,
                expected_state_token=expected_token,
                predecessor_event_id=predecessor_event_id,
            )
        except Exception as exc:
            _log.error(
                f"DURABLE_APPLY emit failed oid={oid} event={event_type} "
                f"key={event_key}: {type(exc).__name__}: {exc}"
            )
            return DurableApplyOutcome(
                event_id=actual_id,
                event_key=event_key,
                event_created=False,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                failure_stage="emit",
                state_event=state_event,
            )

        row = event_bus.get_state_apply_outbox(actual_id)
        if row is None:
            # Fail-closed: fake/legacy emitter bez atomowego outboxu nie moze
            # dostac prawa do state apply. Testy produkcyjnego kontraktu uzywaja
            # prawdziwego SQLite, a adaptery musza jawnie mockowac caly helper.
            _log.error(
                f"DURABLE_APPLY outbox missing oid={oid} event_id={actual_id}"
            )
            return DurableApplyOutcome(
                event_id=actual_id,
                event_key=event_key,
                event_created=created_id is not None,
                state_ready=False,
                state_transitioned=False,
                downstream_executed=False,
                failure_stage="outbox_missing",
                state_event=state_event,
            )

        state_outcome = _process_state_row(
            row,
            event_created=created_id is not None,
            state_update_fn=state_update_fn,
            effect_status_fn=effect_status_fn,
            get_order_fn=get_order_fn,
        )
        if state_outcome.duplicate_of_event_id:
            # Nie zwracaj superseded successora jako targetu: jego istnienie
            # wynika tylko z chwilowej niedostepnosci oracle. Retry ma nadal
            # wykonac dokladny, trwaly callback nierozwiazanej generacji.
            duplicate = event_bus.get_state_apply_outbox(
                state_outcome.duplicate_of_event_id
            )
            if duplicate is not None:
                return _process_state_row(
                    duplicate,
                    event_created=False,
                    state_update_fn=state_update_fn,
                    effect_status_fn=effect_status_fn,
                    get_order_fn=get_order_fn,
                )
        return state_outcome


def emit_and_apply(
    event_type: str,
    *,
    order_id: str,
    courier_id: Optional[str],
    payload: Optional[dict],
    state_payload: Optional[dict],
    event_key: str,
    emit_fn: Callable[..., Optional[str]],
    state_update_fn: Callable[[dict], object],
    effect_status_fn: Callable[[dict, Optional[dict]], str],
    get_order_fn: Callable[[str], Optional[dict]],
    downstream_fn: Optional[Callable[[dict], object]],
    state_event_metadata: Optional[dict] = None,
    sweeper_enabled: Optional[bool] = None,
) -> DurableApplyOutcome:
    """Utrwal i zastosuj state, potem domknij downstream poza state lockiem."""
    try:
        from dispatch_v2.common import STATE_OUTBOX_SWEEPER_MIN_AGE_S

        if sweeper_enabled is None:
            from dispatch_v2.common import decision_flag

            sweeper_enabled = decision_flag("ENABLE_STATE_OUTBOX_SWEEPER")
    except Exception:
        sweeper_enabled = False
    sweeper_enabled = bool(sweeper_enabled)
    retry_updated_before = (
        (
            datetime.now(timezone.utc)
            - timedelta(
                seconds=max(0.0, float(STATE_OUTBOX_SWEEPER_MIN_AGE_S))
            )
        ).isoformat()
        if sweeper_enabled
        else None
    )
    state_outcome = _emit_and_apply_state_phase(
        event_type,
        order_id=order_id,
        courier_id=courier_id,
        payload=payload,
        state_payload=state_payload,
        event_key=event_key,
        emit_fn=emit_fn,
        state_update_fn=state_update_fn,
        effect_status_fn=effect_status_fn,
        get_order_fn=get_order_fn,
        state_event_metadata=state_event_metadata,
        retry_updated_before=retry_updated_before,
    )
    # Gdy niezalezny sweeper jest ON, jego X-sekundowy cooldown musi
    # obowiązywać także foreground FIFO. Inaczej każdy nowy lifecycle event
    # natychmiast ponawiałby callback, który worker właśnie oznaczył błędem.
    # Dokładny target bez wcześniejszej próby pozostaje uprawniony od razu;
    # retry targetu z attempts>0 także respektuje cooldown.
    if sweeper_enabled and state_outcome.state_ready:
        target = event_bus.get_state_apply_outbox(state_outcome.event_id)
        if target is not None:
            target_updated_at = str(target.get("updated_at") or "")
            include_versions = (
                {
                    state_outcome.event_id: (
                        target_updated_at,
                        int(target.get("downstream_attempts") or 0),
                    )
                }
                if (
                    target_updated_at
                    and int(target.get("downstream_attempts") or 0) == 0
                )
                else None
            )
            return _finish_downstream(
                state_outcome,
                downstream_fn,
                updated_before=retry_updated_before,
                include_event_versions=include_versions,
                defer_when_no_eligible_row=True,
            )
    return _finish_downstream(state_outcome, downstream_fn)


def drain_pending(
    *,
    state_update_fn: Callable[[dict], object],
    effect_status_fn: Callable[[dict, Optional[dict]], str],
    get_order_fn: Callable[[str], Optional[dict]],
    downstream_fn: Optional[Callable[[dict], object]],
    limit: int = 100,
    min_age_seconds: float = 0.0,
) -> dict:
    """Resume state, potem FIFO downstream bez trzymania locka orders_state.

    ``min_age_seconds`` liczy zastoj od ``updated_at``. Zero zachowuje
    natychmiastowy kontrakt dla pracy istniejacej na początku wywolania;
    dodatni prog sluzy workerowi i daje foregroundowi grace period oraz staly
    cooldown po nieudanej probie.
    """
    # Nawet przy progu 0 pracuj na migawce z początku wywołania. Dzięki temu
    # drain nie przejmuje świeżego callbacku utworzonego przez foreground już
    # podczas bieżącej rundy; rows zastosowane przez jego własny state lane są
    # wpuszczane niżej dokładnym tokenem (event_id, updated_at).
    updated_before = (
        datetime.now(timezone.utc)
        - timedelta(seconds=max(0.0, float(min_age_seconds)))
    ).isoformat()

    counts = {
        "seen": 0,
        "state_ready": 0,
        "downstream": 0,
        "superseded": 0,
        "failed": 0,
        "completed": 0,
    }
    completed_ids: set[str] = set()
    same_sweep_ready_versions: dict[str, tuple[str, int]] = {}
    deferred_downstream_ids: set[str] = set()

    for initial in event_bus.list_unresolved_state_applies(
        limit=limit, updated_before=updated_before
    ):
        event_id = str(initial.get("event_id") or "")
        selected_updated_at = str(initial.get("updated_at") or "")
        selected_state_attempts = int(initial.get("state_attempts") or 0)
        selected_downstream_attempts = int(
            initial.get("downstream_attempts") or 0
        )
        with state_machine.lifecycle_apply_lock():
            row = event_bus.get_state_apply_outbox(event_id)
            if row is None:
                counts["failed"] += 1
                continue
            if row.get("state_status") not in ("pending", "applied"):
                continue
            if row.get("state_status") == "applied" and row.get("downstream_status") != "pending":
                continue
            if updated_before is not None and (
                str(row.get("updated_at") or "") != selected_updated_at
                or int(row.get("state_attempts") or 0)
                != selected_state_attempts
                or int(row.get("downstream_attempts") or 0)
                != selected_downstream_attempts
                or str(row.get("updated_at") or "") > updated_before
            ):
                # Lista powstaje przed cross-process lockiem. Inny consumer
                # mogl juz wykonac probe i odswiezyc cooldown; optimistic
                # version check zapobiega natychmiastowemu podwojnemu retry.
                continue
            counts["seen"] += 1
            outcome = _process_state_row(
                row,
                event_created=False,
                state_update_fn=state_update_fn,
                effect_status_fn=effect_status_fn,
                get_order_fn=get_order_fn,
            )
            counts["state_ready"] += int(outcome.state_ready)
            counts["superseded"] += int(outcome.superseded)
            counts["failed"] += int(outcome.failure_stage is not None)
            refreshed = event_bus.get_state_apply_outbox(event_id)
            if (
                outcome.state_ready
                and refreshed
                and refreshed.get("state_status") == "applied"
                and refreshed.get("downstream_status") == "pending"
            ):
                # Receipt byl stale na wejsciu do sweepa. Mark state odswieza
                # updated_at, ale nie moze przez to sztucznie odroczyc drugiej
                # fazy o kolejne X sekund w tej samej kontrolowanej probie.
                refreshed_updated_at = str(refreshed.get("updated_at") or "")
                if refreshed_updated_at:
                    # Wyjatek od age gate jest wazny tylko dla dokladnej wersji
                    # receiptu utworzonej przez ten sweep. Kazda konkurencyjna
                    # proba zmienia updated_at i uniewaznia ten token.
                    same_sweep_ready_versions[event_id] = (
                        refreshed_updated_at,
                        int(refreshed.get("downstream_attempts") or 0),
                    )
            if outcome.superseded:
                completed_ids.add(event_id)

    # Jedna awaria najstarszego callbacku celowo zatrzymuje tylko causal FIFO,
    # nie lane zapisu state. Nie hammeruj tego samego row wielokrotnie w ticku.
    for _ in range(max(1, int(limit))):
        oldest = event_bus.get_oldest_pending_downstream(
            updated_before=updated_before,
            include_event_versions=same_sweep_ready_versions,
            exclude_event_ids=deferred_downstream_ids,
        )
        if oldest is None:
            break
        selected_version = (
            str(oldest.get("updated_at") or ""),
            int(oldest.get("downstream_attempts") or 0),
        )
        outcome = _finish_downstream(
            _outcome_from_row(
                oldest,
                event_created=False,
                state_ready=True,
                state_transitioned=False,
                downstream_executed=False,
            ),
            downstream_fn,
            max_rows=1,
            updated_before=updated_before,
            include_event_versions=same_sweep_ready_versions,
            exclude_event_ids=deferred_downstream_ids,
            expected_first_event_id=str(oldest.get("event_id") or ""),
            expected_first_version=selected_version,
        )
        counts["downstream"] += int(outcome.downstream_executed)
        if outcome.downstream_transitioned or outcome.superseded:
            # Licz tylko terminalizacje wykonane przez ten consumer. Samo
            # zaobserwowanie receiptu domknietego przez konkurenta nie moze
            # zawyzac operacyjnej metryki sweepa.
            completed_ids.add(outcome.event_id)
        if outcome.failure_stage is not None:
            counts["failed"] += 1
            break
        refreshed = event_bus.get_state_apply_outbox(outcome.event_id)
        if not refreshed:
            break
        if refreshed.get("downstream_status") == "applied":
            same_sweep_ready_versions.pop(outcome.event_id, None)
            continue
        current_version = (
            str(refreshed.get("updated_at") or ""),
            int(refreshed.get("downstream_attempts") or 0),
        )
        # Brak postępu oznacza, że selekcja straciła aktualność pod lockiem.
        # Wyklucz tylko ten receipt do końca bieżącego sweepa: nie hammeruj go
        # przy stojącym zegarze, ale nadal obsłuż inne ordery.
        deferred_downstream_ids.add(outcome.event_id)
        if current_version != selected_version:
            same_sweep_ready_versions.pop(outcome.event_id, None)
    counts["completed"] = len(completed_ids)
    return counts
