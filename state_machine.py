"""State Machine zlecen - jedyne zrodlo prawdy o stanie kazdego zlecenia.

Kluczowe wlasciwosci:
- Atomic writes: temp -> fsync -> rename
- File lock: fcntl.flock zapobiega race condition miedzy procesami
- History per zlecenie: pelny audit trail
- Integracja z event bus: update_from_event() konsumuje eventy
- Statusy: planned -> assigned -> picked_up -> delivered (+ returned_to_pool)
- Commitment levels: planned / assigned / arrived_at_pickup / picked_up / en_route / near_delivery
"""
import fcntl
import hashlib
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from dispatch_v2.common import (
    ENABLE_R_DECLARED_TRIPWIRE,
    R_DECLARED_TRIPWIRE_TOLERANCE_MIN,
    coords_in_bialystok_bbox,
    decision_flag,
    flag,
    is_czasowka_order as _common_is_czasowka_order,
    load_config,
    now_iso,
    now_utc,
    setup_logger,
)
from dispatch_v2.committed_pickup_authority import (
    ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD,
    ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD,
    CK_CHANGE_REVISION_OBSERVATION_FIELD,
    CK_CHANGE_REVISION_STATE_FIELD,
    COMMITTED_PICKUP_COUPLED_FIELDS,
    COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
    MANUAL_CK_AUTHORITY_FLAG,
    NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD,
    NEW_ORDER_TIME_INTENT_FIELD,
    NEW_ORDER_TIME_INTENT_ID_FIELD,
    PASSIVE_CK_SOURCES,
    RETIRED_CZASOWKA_CK_ONLY_SOURCES,
    RUTCOM_FORWARD_AUTHORITY_FLAG,
    CommittedPickupPolicySnapshot,
    CommittedPickupResolution,
    ResolutionOutcome,
    committed_pickup_effect_applied,
    committed_time_contract_is_complete,
    deserialize_committed_time_policy,
    deserialize_coordinator_event_policy,
    new_order_time_intent_is_valid,
    normalize_pickup_revision,
    pickup_event_has_authority_artifact,
    pickup_payload_requires_coordinator_receipt,
    project_time_event_order,
    project_time_observation_order,
    resolve_czasowka_assignment_ck,
    resolve_czasowka_committed_observation,
    resolve_czasowka_pickup_observation as _resolve_pickup_observation,
    state_has_committed_pickup_artifact,
    time_event_cas_is_versioned,
    time_event_cas_status,
    validate_committed_pickup_event,
    validate_committed_time_policy_source,
)
from dispatch_v2.core.jsonl_appender import append_jsonl
from dispatch_v2.order_fsm import FsmOutcome, FsmVerdict, validate_order_event
from dispatch_v2 import state_persistence as _state_store

_WARSAW_TZ = ZoneInfo("Europe/Warsaw")

# Kanoniczna allowlista merge-only eventu naprawiającego rekord utworzony przez
# historyczny cold-start COURIER_ASSIGNED sprzed pełnej inicjalizacji mógł
# utrwalić rekord bez detali. Pola
# lifecycle/identity (status, courier_id, first_seen, markery) są świadomie poza
# kontraktem i żaden caller nie utrzymuje drugiej kopii tej listy.
ORDER_DETAILS_ENRICHMENT_FIELDS = (
    "restaurant",
    "pickup_address",
    "pickup_city",
    "delivery_address",
    "delivery_city",
    "pickup_at_warsaw",
    "prep_minutes",
    "order_type",
    "address_id",
    "pickup_coords",
    "delivery_coords",
    "uwagi",
    "uwagi_pickup_parsed",
    "decision_deadline",
    "zmiana_czasu_odbioru",
    "created_at_utc",
)
ORDER_DETAILS_ENRICHMENT_REQUIRED_FIELDS = (
    "restaurant",
    "pickup_address",
    "delivery_address",
)

_TERMINAL_ORDER_STATUSES = frozenset(
    {"delivered", "returned_to_pool", "cancelled"}
)

# Trwaly, fail-closed claim jedynej poczatkowej proby AUTO_KOORD. To pole
# nalezy do agregatu zlecenia, bo procesowy set nie przetrwalby restartu, a
# marker eventu NEW_ORDER jest zuzywany zanim kontrakt czasu moze sie domknac.
AUTO_KOORD_INITIAL_ATTEMPT_FIELD = "auto_koord_initial_attempt"


class CorruptedTimestampError(ValueError):
    """V3.19f: HH:MM string nie zgadza się z ISO datetime po parse.

    Wykrywane przez _verify_czas_kuriera_consistency:
      assert warsaw_dt.strftime("%H:%M") == raw_hhmm
    Jeśli False → log ERROR + skip persist + raise ten wyjątek.

    Sygnał korupcji parsera (panel_client._czas_kuriera_to_datetime edge
    case, malformed input, albo downstream corruption). Lepiej fail-fast
    niż tichy persist bzdury do orders_state.
    """
    pass


class StateReadError(RuntimeError):
    """Faza 1 (incydent 2026-05-18 14:47 — orders_state.json clobber):
    _read_state nie zwrócił definitywnego stanu (FileNotFoundError mimo że
    plik powinien istnieć, albo JSONDecodeError).

    RMW writer (upsert_order / set_status / touch_check_cursor / delete_order)
    MUSI przerwać zapis przy tym wyjątku — zapis pustego/niekompletnego stanu
    nadpisałby cały orders_state.json (total state loss). Fail-loud, nie
    fail-catastrophic (Lekcja #32 silent except + #81 fail-loud sentinel).

    Lepiej zgubić aplikację jednego eventu (event zostaje w events.db,
    append-only → odtwarzalny) niż skasować stan całej floty.
    """
    pass


class MissingOrderPreconditionError(RuntimeError):
    """Lifecycle update tried to materialize an order without its base record."""

    pass


def _verify_czas_kuriera_consistency(
    warsaw_iso: Optional[str],
    raw_hhmm: Optional[str],
    oid: str,
) -> bool:
    """V3.19f sanity: ISO strftime('%H:%M') MUSI == raw HH:MM.

    Zwraca True gdy consistency OK albo oba pola None (no-op).
    Zwraca False + log ERROR gdy mismatch — caller powinien skip persist
    i raise CorruptedTimestampError.

    Edge cases:
    - oba None → True (nic do weryfikacji)
    - tylko jedno None → False (partial data, zły sygnał)
    - ISO parse fail → False (corrupted)
    - wraparound OK: strftime('%H:%M') daje tę samą godzinę niezależnie
      od zmienionej daty (+1/-1 day), więc sanity check is stabilny pod
      6h wraparound guard z V3.19f parse layer.
    """
    if warsaw_iso is None and raw_hhmm is None:
        return True
    if warsaw_iso is None or raw_hhmm is None:
        _log.error(
            f"CZAS_KURIERA partial data for oid={oid}: "
            f"warsaw_iso={warsaw_iso!r} hhmm={raw_hhmm!r}"
        )
        return False
    try:
        dt = datetime.fromisoformat(warsaw_iso)
    except (ValueError, TypeError) as e:
        _log.error(
            f"CZAS_KURIERA ISO parse fail for oid={oid}: "
            f"warsaw_iso={warsaw_iso!r} err={e}"
        )
        return False
    expected = dt.strftime("%H:%M")
    if expected != raw_hhmm:
        _log.error(
            f"CZAS_KURIERA MISMATCH for oid={oid}: "
            f"ISO→HH:MM={expected!r} != raw_hhmm={raw_hhmm!r} "
            f"(warsaw_iso={warsaw_iso})"
        )
        return False
    return True


# ── Czasówka committed-pickup authority (Adrian 2026-06-24, root #483023) ──
# Umówiony czas CZASÓWKI = pickup_at_warsaw (twarda deklaracja restauracji).
# Gastro przestempluje pole `czas_kuriera` przy KAŻDEJ zmianie statusu
# (panel_kurier.py: "stempluje czas_odbioru/czas_doreczenia ze zmiany statusu")
# → pasywny re-odczyt panelu (panel_re_check / pre_proposal_recheck) wpuszczał
# ten śmieć jako zmianę committed (#483023: 16:22→15:04, 5 s po assignie).
# Dla czasówek NIE ingestujemy pasywnego czas_kuriera. Umówiony czas zmienia
# się TYLKO przez kanoniczny PICKUP_TIME_UPDATED z policy ownera. Historyczne
# CK-only kanały są jawnie wygaszone przez RETIRED_CZASOWKA_CK_ONLY_SOURCES;
# źródła pasywne (re-odczyt gastro) przechodzą wyłącznie wspólny resolver.
_CK_PASSIVE_SOURCES = PASSIVE_CK_SOURCES

def _is_czasowka_order(o: Optional[dict]) -> bool:
    """Delegacja do jednego kanonicznego klasyfikatora common.py."""
    return _common_is_czasowka_order(o)


def _czasowka_reclaim_live_authorized(event: dict) -> bool:
    """Receipt-bound marker wygrywa; bez niego wymagaj biezacej flagi LIVE."""
    if "czasowka_reclaim_live_authorized" in event:
        return event.get("czasowka_reclaim_live_authorized") is True
    return decision_flag("ENABLE_CZASOWKA_RECLAIM_LIVE")


def resolve_czasowka_ck_observation(
    existing: Optional[dict],
    ck_payload: Optional[dict],
    *,
    policy_snapshot: Optional[CommittedPickupPolicySnapshot] = None,
) -> CommittedPickupResolution:
    """Powiąż flagi i jednorazowy receipt ze wspólnym czystym resolverem."""
    existing = existing or {}
    payload = dict(ck_payload or {})
    is_czasowka = _is_czasowka_order(
        project_time_observation_order(existing, payload)
    )
    if payload.get("source") == "coordinator_force":
        from dispatch_v2 import coordinator_time_recheck as receipt_store

        oid = str(payload.get("oid") or existing.get("order_id") or "")
        receipt = payload.get("authority_receipt")
        claimed_event = receipt_store.get_claimed_event(
            receipt, order_id=oid
        )
        if is_czasowka and claimed_event is not None:
            try:
                claimed_policy = deserialize_coordinator_event_policy(
                    claimed_event
                )
            except (TypeError, ValueError):
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.SUPPRESS,
                    reason="claimed_receipt_policy_missing",
                )
            if not claimed_policy.coordinator_time_authority_enabled:
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.SUPPRESS,
                    reason="claimed_receipt_policy_off",
                )
            validation = validate_committed_pickup_event(
                existing,
                claimed_event,
                is_czasowka=_is_czasowka_order(
                    project_time_event_order(existing, claimed_event)
                ),
                # Exact claim jest dziennikiem transakcji sprzed outboxa.
                # Rollback blokuje nowe claimy, ale nie gubi juz zwiazanej
                # intencji po crashu w oknie claim -> SQLite outbox.
                passive_guard_enabled=(
                    claimed_policy.passive_guard_enabled
                ),
                manual_passthrough_enabled=(
                    claimed_policy.manual_passthrough_enabled
                ),
                rutcom_forward_authority_enabled=(
                    claimed_policy.rutcom_forward_authority_enabled
                ),
                coordinator_receipt_verified=True,
            )
            if validation.outcome is ResolutionOutcome.APPLY:
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.APPLY,
                    reason="coordinator_receipt",
                    event=claimed_event,
                )
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason=f"claimed_receipt_rejected:{validation.reason}",
            )
        if not receipt_store.verify_pending_receipt(
            receipt, order_id=oid
        ):
            if is_czasowka:
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.SUPPRESS,
                    reason="receipt_not_pending",
                )
            return resolve_czasowka_committed_observation(
                existing,
                payload,
                is_czasowka=False,
                passive_guard_enabled=False,
                manual_passthrough_enabled=False,
                rutcom_forward_authority_enabled=False,
            )
        receipt_policy = receipt_store.receipt_policy_snapshot(receipt)
        if receipt_policy is None:
            if is_czasowka:
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.SUPPRESS,
                    reason="receipt_policy_missing",
                )
            manual_enabled = False
            forward_enabled = False
            passive_enabled = False
        else:
            manual_enabled = receipt_policy.manual_passthrough_enabled
            forward_enabled = (
                receipt_policy.rutcom_forward_authority_enabled
            )
            passive_enabled = receipt_policy.passive_guard_enabled
        base_receipt = receipt_store.receipt_base(receipt)
        payload["authority_receipt"] = base_receipt
        preliminary = resolve_czasowka_committed_observation(
            existing,
            payload,
            is_czasowka=is_czasowka,
            passive_guard_enabled=passive_enabled,
            manual_passthrough_enabled=manual_enabled,
            rutcom_forward_authority_enabled=forward_enabled,
            coordinator_receipt_verified=True,
        )
        if (
            preliminary.outcome is not ResolutionOutcome.APPLY
            or preliminary.event is None
        ):
            return preliminary
        claimed = receipt_store.claim_receipt(
            receipt,
            order_id=oid,
            event=preliminary.event,
        )
        if claimed is None:
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="receipt_claim_failed",
            )
        return preliminary

    if policy_snapshot is not None:
        if type(policy_snapshot) is not CommittedPickupPolicySnapshot:
            raise TypeError(
                "policy_snapshot must be CommittedPickupPolicySnapshot"
            )
        validate_committed_time_policy_source(
            policy_snapshot, payload.get("source")
        )
        manual_enabled = policy_snapshot.manual_passthrough_enabled
        forward_enabled = policy_snapshot.rutcom_forward_authority_enabled
        passive_enabled = policy_snapshot.passive_guard_enabled
    else:
        manual_enabled = decision_flag(MANUAL_CK_AUTHORITY_FLAG)
        forward_enabled = decision_flag(
            RUTCOM_FORWARD_AUTHORITY_FLAG
        )
        passive_enabled = flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)

    return resolve_czasowka_committed_observation(
        existing,
        payload,
        is_czasowka=is_czasowka,
        passive_guard_enabled=passive_enabled,
        manual_passthrough_enabled=manual_enabled,
        rutcom_forward_authority_enabled=forward_enabled,
    )


def _czasowka_raw_ck_writer_is_retired(
    existing: Optional[dict],
    resolution: CommittedPickupResolution,
    *,
    policy_snapshot: Optional[CommittedPickupPolicySnapshot] = None,
) -> bool:
    """Jedna polityka handlera i postcondition dla wygaszonych raw CK writerów."""
    if policy_snapshot is not None and type(
        policy_snapshot
    ) is not CommittedPickupPolicySnapshot:
        raise TypeError("policy_snapshot must be CommittedPickupPolicySnapshot")
    forward_enabled = (
        policy_snapshot.rutcom_forward_authority_enabled
        if policy_snapshot is not None
        else decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG)
    )
    return bool(
        resolution.outcome is ResolutionOutcome.NOT_APPLICABLE
        and _is_czasowka_order(existing)
        and (
            forward_enabled
            or state_has_committed_pickup_artifact(existing)
        )
    )


def _assignment_ck_resolution(
    existing: Optional[dict],
    event: dict,
) -> CommittedPickupResolution:
    """Resolve one receipt-bound assignment policy for handler and oracle."""
    has_forward = ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD in event
    has_passive = ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD in event
    if has_forward and has_passive:
        forward_raw = event.get(ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD)
        passive_raw = event.get(ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD)
        if isinstance(forward_raw, bool) and isinstance(passive_raw, bool):
            forward_enabled = forward_raw
            passive_enabled = passive_raw
        else:
            # Partial/corrupt durable policy can still apply the lifecycle
            # assignment, but must never regain a competing CK writer.
            forward_enabled = True
            passive_enabled = True
    elif not has_forward and not has_passive:
        # Compatibility for pre-v16 direct/unfinished events.  Forward rollout
        # mechanically requires zero unfinished rows before the live flip.
        forward_enabled = decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG)
        passive_enabled = flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)
    else:
        forward_enabled = True
        passive_enabled = True
    return resolve_czasowka_assignment_ck(
        existing,
        is_czasowka=_is_czasowka_order(existing),
        passive_guard_enabled=passive_enabled,
        rutcom_forward_authority_enabled=forward_enabled,
    )


def _new_order_time_authority_enabled(event: dict) -> bool:
    """Read one receipt-bound policy for initial pickup/CK persistence."""
    snapshot = event.get(NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD)
    if isinstance(snapshot, bool):
        return snapshot
    if snapshot is not None:
        # A malformed durable marker must not recover a competing raw writer.
        return True
    try:
        return bool(decision_flag(RUTCOM_FORWARD_AUTHORITY_FLAG))
    except Exception:
        # Direct/legacy callers without a durable snapshot fail closed for the
        # ambiguous time tuple. Forward deploy drains every old NEW_ORDER row.
        return True


def resolve_czasowka_pickup_observation(
    existing: Optional[dict],
    pickup_payload: Optional[dict],
    *,
    policy_snapshot: Optional[CommittedPickupPolicySnapshot] = None,
) -> CommittedPickupResolution:
    """Powiąż pickup z tym samym exact-claimem co CK koordynatora.

    Zwykły panelowy pickup pozostaje czystą obserwacją. Tylko powrót do
    zapamiętanego stale-baseline wymaga dodatniego receiptu; claim jednego
    eventu sprawia, że ten sam klik nie może autoryzować równocześnie CK i
    przeciwnego pickupu z jednego response Rutcom.
    """
    existing = existing or {}
    payload = dict(pickup_payload or {})
    is_czasowka = _is_czasowka_order(
        project_time_observation_order(existing, payload)
    )
    if payload.get("source") == "coordinator_force":
        # The receipt proves who requested this refresh; it does not change the
        # business class of the observed order.  A projection that remains
        # elastic belongs to the existing legacy pickup writer in
        # panel_watcher, regardless of the receipt's rollout snapshot.  Return
        # before policy/claim handling so ON cannot promote it to committed
        # authority and OFF cannot swallow the legacy refresh.
        if not is_czasowka:
            return _resolve_pickup_observation(
                existing,
                payload,
                is_czasowka=False,
            )
        from dispatch_v2 import coordinator_time_recheck as receipt_store

        oid = str(payload.get("oid") or existing.get("order_id") or "")
        receipt = payload.get("authority_receipt")
        claimed_event = receipt_store.get_claimed_event(
            receipt, order_id=oid
        )
        if is_czasowka and claimed_event is not None:
            try:
                claimed_policy = deserialize_coordinator_event_policy(
                    claimed_event
                )
            except (TypeError, ValueError):
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.SUPPRESS,
                    reason="claimed_receipt_policy_missing",
                )
            if not claimed_policy.coordinator_time_authority_enabled:
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.SUPPRESS,
                    reason="claimed_receipt_policy_off",
                )
            validation = validate_committed_pickup_event(
                existing,
                claimed_event,
                is_czasowka=_is_czasowka_order(
                    project_time_event_order(existing, claimed_event)
                ),
                passive_guard_enabled=(
                    claimed_policy.passive_guard_enabled
                ),
                manual_passthrough_enabled=(
                    claimed_policy.manual_passthrough_enabled
                ),
                rutcom_forward_authority_enabled=(
                    claimed_policy.rutcom_forward_authority_enabled
                ),
                coordinator_receipt_verified=True,
            )
            proof = (claimed_event.get("payload") or {}).get(
                "committed_authority_proof"
            )
            if (
                validation.outcome is ResolutionOutcome.APPLY
                and isinstance(proof, dict)
                and proof.get("observation_kind") == "rutcom_pickup"
            ):
                return CommittedPickupResolution(
                    outcome=ResolutionOutcome.APPLY,
                    reason=str(
                        (claimed_event.get("payload") or {}).get(
                            "committed_authority"
                        )
                    ),
                    event=claimed_event,
                )
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="receipt_claimed_for_other_event",
            )
        if not receipt_store.verify_pending_receipt(
            receipt, order_id=oid
        ):
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="receipt_not_pending",
            )
        receipt_policy = receipt_store.receipt_policy_snapshot(receipt)
        if receipt_policy is None:
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="receipt_policy_missing",
            )
        forward_enabled = receipt_policy.rutcom_forward_authority_enabled
        passive_enabled = receipt_policy.passive_guard_enabled
        # coordinator_force jest źródłem zarezerwowanym: brak flagi nie może
        # zamienić go w NOT_APPLICABLE, bo watcher potraktowałby to jako zgodę
        # na legacy fallback bez receiptu. Claim już istniejący został obsłużony
        # wyżej; nowa intencja przy rollbacku pozostaje fail-closed.
        if not forward_enabled:
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="pickup_authority_off",
            )
        if not passive_enabled:
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="authority_requires_passive_guard",
            )
        payload["authority_receipt"] = receipt_store.receipt_base(receipt)
        preliminary = _resolve_pickup_observation(
            existing,
            payload,
            is_czasowka=is_czasowka,
            coordinator_receipt_verified=True,
        )
        if (
            preliminary.outcome is not ResolutionOutcome.APPLY
            or preliminary.event is None
        ):
            return preliminary
        if receipt_store.claim_receipt(
            receipt,
            order_id=oid,
            event=preliminary.event,
        ) is None:
            return CommittedPickupResolution(
                outcome=ResolutionOutcome.SUPPRESS,
                reason="receipt_claim_failed",
            )
        return preliminary

    if policy_snapshot is not None:
        if type(policy_snapshot) is not CommittedPickupPolicySnapshot:
            raise TypeError(
                "policy_snapshot must be CommittedPickupPolicySnapshot"
            )
        validate_committed_time_policy_source(
            policy_snapshot, payload.get("source")
        )
        forward_enabled = policy_snapshot.rutcom_forward_authority_enabled
        passive_enabled = policy_snapshot.passive_guard_enabled
    else:
        forward_enabled = decision_flag(
            RUTCOM_FORWARD_AUTHORITY_FLAG
        )
        passive_enabled = flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)

    # Istniejaca flaga manual passthrough autoryzuje wylacznie krawedz CK
    # False->True. Zwykly pickup Rutcom przechodzi nowym kontraktem dopiero po
    # wlaczeniu nowej flagi; inaczej caller zachowuje exact legacy path.
    if is_czasowka and not forward_enabled:
        return CommittedPickupResolution(
            outcome=ResolutionOutcome.NOT_APPLICABLE,
            reason="pickup_authority_off",
        )
    if is_czasowka and not passive_enabled:
        return CommittedPickupResolution(
            outcome=ResolutionOutcome.SUPPRESS,
            reason="authority_requires_passive_guard",
        )

    return _resolve_pickup_observation(
        existing,
        payload,
        is_czasowka=is_czasowka,
    )


def build_czasowka_manual_ck_pickup_event(
    existing: Optional[dict],
    ck_payload: Optional[dict],
) -> Optional[dict]:
    """Kompatybilny alias; polityka istnieje tylko w centralnym resolverze."""
    resolution = resolve_czasowka_ck_observation(existing, ck_payload)
    if resolution.outcome is ResolutionOutcome.APPLY:
        return resolution.event
    return None


def _ck_backward_delta(
    old_ck_iso: Optional[str],
    new_ck_iso: Optional[str],
) -> Optional[float]:
    """Elastyk forward-only (Adrian 2026-06-24, opcja B). Committed czas_kuriera
    elastyka NIE cofamy pasywnym re-odczytem gastro („przyjazd wcześniej niż
    umówiono" = wobble ETA = śmieć; 5/75 zmian w 5 dni). Forward zostaje
    (koordynatorski +15 / realne spóźnienie). Czasówki mają osobny, mocniejszy
    guard (pickup_at authority) — to dotyczy TYLKO nie-czasówek.

    Zwraca signed delta_min (<0) gdy `new` wcześniejszy niż `old` (= cofnięcie
    do zablokowania). None gdy: brak wartości (np. first_acceptance), parse fail,
    albo ruch do przodu/równy (= dozwolony). None == „przepuść"."""
    if not old_ck_iso or not new_ck_iso:
        return None
    try:
        old_dt = datetime.fromisoformat(old_ck_iso)
        new_dt = datetime.fromisoformat(new_ck_iso)
    except (ValueError, TypeError):
        return None
    delta = (new_dt - old_dt).total_seconds() / 60.0
    return delta if delta < 0 else None


# Zamkniete statusy zlecenia
ORDER_STATUSES = {
    "planned",          # widoczne, jeszcze nieprzypisane
    "assigned",         # przypisane kurierowi (propozycja zatwierdzona)
    "picked_up",        # kurier odebral z restauracji
    "delivered",        # dostarczone
    "returned_to_pool", # wrocilo do puli (partial split / tear-down)
    "cancelled",        # anulowane (klient/restauracja)
}

# Commitment levels (6 poziomow, opinia #6)
COMMITMENT_LEVELS = {
    "planned": 1.0,
    "assigned": 1.2,
    "arrived_at_pickup": 1.5,
    "picked_up": 2.0,
    "en_route_delivery": 2.5,
    "near_delivery": 3.0,
}

if os.environ.get("DISPATCH_UNDER_PYTEST"):
    # Hermetyczne testy obserwera nie dotykaja nawet katalogu logow runtime.
    _log = logging.getLogger("state_machine")
else:
    _log = setup_logger(
        "state_machine", "/root/.openclaw/workspace/scripts/logs/dispatch.log"
    )


# Z-P1-01 Phase A: the formal FSM is an observer only.  Enforcement is
# deliberately hard-OFF (not a runtime flag) until the shadow matrix and
# historical replay have been reviewed.  Nothing in the legacy path branches
# on this value; changing it alone cannot enable enforcement.
ORDER_FSM_OBSERVER_ENABLED = True
ORDER_FSM_ENFORCEMENT_ENABLED = False
_FSM_CURRENT_UNSET = object()
_LIFECYCLE_APPLY_LOCAL = threading.local()
_LIFECYCLE_APPLY_THREAD_LOCK = threading.RLock()
_LIFECYCLE_DOWNSTREAM_LOCAL = threading.local()
_LIFECYCLE_DOWNSTREAM_THREAD_LOCK = threading.RLock()


@contextmanager
def lifecycle_apply_lock():
    """Cross-process, reentrant lock dla outbox version-check -> state apply.

    Osobny sidecar zapobiega deadlockowi z ``_locked_write``. Reentrancja jest
    potrzebna, bo kanoniczny mutator bierze ten lock ponownie. Zakres obejmuje
    outbox precheck i zapis orders_state, ale CELOWO nie obejmuje wolnego
    plan/recanon downstream (osobna kolejka/lock ponizej).
    """
    depth = int(getattr(_LIFECYCLE_APPLY_LOCAL, "depth", 0) or 0)
    if depth:
        _LIFECYCLE_APPLY_LOCAL.depth = depth + 1
        try:
            yield
        finally:
            _LIFECYCLE_APPLY_LOCAL.depth -= 1
        return

    # flock serializuje procesy, ale jego semantyka nie gwarantuje wzajemnego
    # wykluczenia dwoch watkow tego samego procesu. RLock domyka ten przypadek;
    # thread-local depth zachowuje reentrancje bez ponownego flock().
    with _LIFECYCLE_APPLY_THREAD_LOCK:
        lock_path = f"{_state_path()}.lifecycle_apply.lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        # os.open daje prawdziwy deskryptor także w testach, które mockują
        # builtins.open dla plików panelu/stanu.
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            _LIFECYCLE_APPLY_LOCAL.depth = 1
            _LIFECYCLE_APPLY_LOCAL.fd = lock_fd
            yield
        finally:
            _LIFECYCLE_APPLY_LOCAL.depth = 0
            _LIFECYCLE_APPLY_LOCAL.fd = None
            try:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


@contextmanager
def lifecycle_downstream_lock():
    """Osobny FIFO-consumer lock; yield True tylko dla outer consumera."""
    depth = int(getattr(_LIFECYCLE_DOWNSTREAM_LOCAL, "depth", 0) or 0)
    if depth:
        _LIFECYCLE_DOWNSTREAM_LOCAL.depth = depth + 1
        try:
            yield False
        finally:
            _LIFECYCLE_DOWNSTREAM_LOCAL.depth -= 1
        return

    with _LIFECYCLE_DOWNSTREAM_THREAD_LOCK:
        lock_path = f"{_state_path()}.lifecycle_downstream.lock"
        Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        locked = False
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            locked = True
            _LIFECYCLE_DOWNSTREAM_LOCAL.depth = 1
            yield True
        finally:
            _LIFECYCLE_DOWNSTREAM_LOCAL.depth = 0
            try:
                if locked:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            finally:
                os.close(lock_fd)


def _lifecycle_state_mutation(fn):
    """Kazdy writer orders_state uczestniczy w wersjonowanym protokole C3.

    Sam ``_locked_write`` chroni atomowy RMW pliku, ale nie obejmuje odczytu
    wersji wykonanego w durable outboxie. Ten wspolny, reentrant wrapper sprawia,
    ze bezposredni writer nie moze wejsc pomiedzy check wersji i lifecycle apply.
    """
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with lifecycle_apply_lock():
            return fn(*args, **kwargs)

    return wrapped


def _observe_order_event(event, current=_FSM_CURRENT_UNSET) -> Optional[FsmVerdict]:
    """Run the pure formal FSM validator without affecting legacy behavior.

    Fail-open is intentional in Phase A: validator/read/logging failures are
    diagnostics and must never block, mutate, or replace the existing event
    handler.  Illegal/invalid events get one structured WARNING; explicit
    reconcile/correction exceptions get INFO; ordinary legal events stay DEBUG.
    """
    if not ORDER_FSM_OBSERVER_ENABLED:
        return None
    try:
        if current is _FSM_CURRENT_UNSET:
            oid = event.get("order_id") if isinstance(event, dict) else None
            current = get_order(str(oid)) if oid else None
        verdict = validate_order_event(event, current=current)
        issue_codes = ",".join(verdict.issue_codes) or "none"
        message = (
            "ORDER_FSM_OBSERVER mode=log_only enforcement=hard_off "
            f"would_reject={int(verdict.would_reject)} "
            f"oid={verdict.order_id or '-'} event={verdict.event_type} "
            f"from={verdict.from_status} to={verdict.to_status or '-'} "
            f"outcome={verdict.outcome.value} source={verdict.source or '-'} "
            f"event_id={verdict.event_id or '-'} issues={issue_codes}"
        )
        if verdict.would_reject:
            _log.warning(message)
        elif verdict.outcome in {
            FsmOutcome.RECONCILE_EXCEPTION,
            FsmOutcome.CORRECTION_EXCEPTION,
        }:
            _log.info(message)
        else:
            _log.debug(message)
        return verdict
    except Exception as exc:
        # The observer is never authoritative in Phase A.  In particular, a
        # malformed event must retain exactly the exception/partial-write
        # behavior of the legacy handler below.
        try:
            _log.warning(
                "ORDER_FSM_OBSERVER_FAIL mode=log_only enforcement=hard_off "
                f"error={type(exc).__name__}:{exc}"
            )
        except Exception:
            pass
        return None


def _state_path() -> str:
    """Ścieżka orders_state.json.

    Faza 2b (2026-05-18, diagnoza D2): honoruje override env DISPATCH_STATE_DIR.
    Testy `test_v3275_*` ustawiały tę zmienną wierząc, że izoluje stan — ale
    _state_path jej NIGDY nie czytał → test robił `os.remove` na PRODUKCYJNYM
    `orders_state.json` (incydent 2026-05-18: kasacja stanu floty + residuum
    fixture'ów typu order 469087). Override = realna izolacja per-test."""
    override_dir = os.environ.get("DISPATCH_STATE_DIR")
    if override_dir:
        return os.path.join(override_dir, "orders_state.json")
    path = load_config()["paths"]["orders_state"]
    # Faza 2b guard (klasa Lekcji #75 — leak izolacji testu): pod pytest ŻADEN
    # test nie może operować na produkcyjnym orders_state.json. Brak
    # DISPATCH_STATE_DIR + brak monkeypatcha _state_path = test nieizolowany
    # → raise zamiast pozwolić skasować/zatruć stan całej floty. Świadomy
    # wyjątek (np. read-only smoke na realnym pliku): ALLOW_PROD_STATE_IN_TEST=1.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ALLOW_PROD_STATE_IN_TEST"):
        raise RuntimeError(
            f"_state_path: pod pytest zwrócono ścieżkę PRODUKCYJNĄ ({path}) — "
            f"test nieizolowany, ryzyko skasowania/zatrucia stanu floty. Napraw: "
            f"env DISPATCH_STATE_DIR=<tmpdir> albo monkeypatch "
            f"state_machine._state_path. Świadomy override: ALLOW_PROD_STATE_IN_TEST=1."
        )
    return path


@contextmanager
def _locked_write():
    """Kontekst: otwiera lock file, trzyma exclusive lock, zwraca sciezke state file.
    Dopiero po yield mozna zapisywac atomic."""
    state_path = Path(_state_path())
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(state_path) + ".lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        yield state_path
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def ensure_state_directory_durable(path: Optional[Path] = None) -> None:
    """Utrwal wpis katalogowy aktualnego pliku orders_state."""
    path = Path(_state_path()) if path is None else Path(path)
    _state_store.fsync_parent(path)


def _guarded_write(path: Path, new_state: dict, old_count: int, op: str):
    """Faza 1 count-regression guard: zapis state z weryfikacją liczności.

    upsert_order / set_status / touch_check_cursor NIGDY nie zmniejszają
    liczby zleceń (tylko dodają/aktualizują). delete_order zmniejsza o
    dokładnie 1. Każde inne zmniejszenie = oznaka clobberu (np. czytany stan
    był niekompletny) → raise StateReadError, NIE zapisuj.

    Defense-in-depth: łapie KAŻDY przyszły bug kurczący stan, nie tylko znany
    wektor _read_state→{}. Kill-switch: ENABLE_STATE_WRITE_GUARD=false
    (flags.json) — wyłącza count guard, ale nie wspólną politykę durability."""
    if not flag("ENABLE_STATE_WRITE_GUARD", True):
        _state_store.atomic_write_json(
            path,
            new_state,
            indent=2,
            ensure_directory_durable=ensure_state_directory_durable,
            logger=_log,
        )
        return
    new_count = len(new_state)
    if op == "delete":
        ok = new_count >= old_count - 1
    else:  # upsert / set_status / touch — add/update only, count nie maleje
        ok = new_count >= old_count
    if not ok:
        detail = (f"_guarded_write: regresja liczności state {old_count}->{new_count} "
                  f"przy op={op!r} — zapis ZABLOKOWANY (możliwy clobber orders_state)")
        _alert_state_read_failure(detail)
        raise StateReadError(detail)
    _state_store.atomic_write_json(
        path,
        new_state,
        indent=2,
        ensure_directory_durable=ensure_state_directory_durable,
        logger=_log,
    )


# Faza 1: throttled alert gdy state RMW odmawia zapisu (clobber prevention).
_STATE_READ_ALERT_COOLDOWN_S = 300.0
_last_state_read_alert_ts = 0.0


def _alert_state_read_failure(detail: str) -> None:
    """Faza 1: loud, throttled (5 min) admin alert gdy RMW writer przerywa
    zapis (orders_state nieczytelny ALBO regresja liczności).

    Lazy import telegram_utils — state_machine to moduł niskopoziomowy, nie
    ciągnie zależności na sztywno. send_admin_alert sam refuse'uje pod pytest
    (Lekcja #75). Best-effort: nigdy nie raise (alert nie może zablokować
    głównej ścieżki ani zamaskować pierwotnego StateReadError)."""
    global _last_state_read_alert_ts
    now = time.monotonic()
    if now - _last_state_read_alert_ts < _STATE_READ_ALERT_COOLDOWN_S:
        return
    _last_state_read_alert_ts = now
    _log.error(f"STATE WRITE GUARD: {detail}")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(
            f"🛑 STATE WRITE GUARD — RMW writer przerwany\n\n{detail}\n\n"
            f"Stan NIE został nadpisany (ochrona przed clobberem orders_state). "
            f"Eventy zostają w events.db (append-only → odtwarzalne). "
            f"Sprawdź dispatch_state/orders_state.json i logi state_machine."
        )
    except Exception as e:
        _log.warning(f"_alert_state_read_failure: alert nieudany: "
                     f"{type(e).__name__}: {e}")


def _read_state() -> dict:
    """Czyta state przez kanonicznego ownera + retry (P0.5b Fix #2).

    Problem: watcher 20s + sla_tracker 10s odczytują concurrent. Podczas atomic
    rename pojawia sie okno gdzie plik chwilowo nie istnieje LUB jest partial.
    Fix: 3 retry z exponential backoff (50/100/200 ms) + fcntl.LOCK_SH.

    Zwraca {} jesli plik nie istnieje po 3 retries (nie traci state silently —
    loguje warning). JSONDecodeError → zwraca {} + error log.
    """
    path = Path(_state_path())
    result = _state_store.read_json_object(
        path,
        legacy_empty=True,
        retry_delays=(0.05, 0.10),
    )
    if result.source == "legacy_empty_missing":
        _log.warning(f"_read_state: {path} not found after 3 attempts")
    elif result.source == "legacy_empty_corrupt":
        exc = result.main_error
        _log.error(
            f"state JSON unreadable at {path}: {type(exc).__name__}: {exc}. "
            "Zwracam pusty state."
        )
    return result.data


def state_storage_token() -> str:
    """Token snapshotu *treści* pliku, niezależny od zegara.

    Używany wyłącznie w rzadkiej ścieżce, gdy strict JSON read zawiódł przed
    emisją durable eventu. Hash pozwala później dowieść, że żaden writer nie
    zmienił surowego state; mtime/updated_at nie są oracle przy korekcie zegara.
    """
    path = Path(_state_path())
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except FileNotFoundError:
        return "missing:sha256"
    return f"sha256:{digest.hexdigest()}"


def _read_state_strict() -> dict:
    """Faza 1: zwraca state ALBO raise StateReadError. Wyłącznie dla RMW
    writerów (upsert/set_status/touch/delete).

    W przeciwieństwie do _read_state() NIGDY nie zwraca {} przez fallback —
    cichy {} z RMW nadpisałby cały orders_state.json. Pusty wynik dozwolony
    tylko przy świadomym bootstrapie (plik nigdy nie istniał)."""
    path = Path(_state_path())
    try:
        result = _state_store.read_json_object(
            path,
            allow_bootstrap=True,
            retry_delays=(0.05, 0.10),
        )
    except (OSError, ValueError) as last_err:
        detail = (f"_read_state_strict: {path} nieczytelny po retry "
                  f"({type(last_err).__name__}: {last_err}) — RMW przerwany, "
                  f"NIE nadpisuję orders_state (ochrona przed clobberem)")
        _alert_state_read_failure(detail)
        raise StateReadError(detail) from last_err
    if result.source == "bootstrap":
        _log.warning(
            f"_read_state_strict: {path} nie istnieje — bootstrap "
            "(świeża instalacja)"
        )
    return result.data


def get_all() -> dict:
    """Zwraca caly state. Uzywaj ostroznie - kopiuj jesli modyfikujesz."""
    return _read_state()


def get_all_strict() -> dict:
    """Fail-closed pełny snapshot dla granic state→zewnętrzny writer."""
    return _read_state_strict()


def get_order(order_id: str) -> Optional[dict]:
    """Zwraca pojedyncze zlecenie lub None."""
    return _read_state().get(order_id)


def get_order_strict(order_id: str) -> Optional[dict]:
    """Fail-closed odczyt jednego zlecenia dla granic read→write.

    Zwykli read-only konsumenci zachowuja historyczny kontrakt ``get_order``
    (fallback do pustego stanu). Durable event bridge nie moze jednak pomylic
    chwilowo brakujacego/uszkodzonego pliku z prawdziwym brakiem rekordu, bo
    taki falszywy ``None`` staje sie wersja oczekiwana outboxa. Dlatego tylko
    granica C3 uzywa strict readera wspolnego z kanonicznymi RMW writerami.
    """
    return _read_state_strict().get(order_id)


def get_by_status(status: str) -> list:
    """Zwraca liste zlecen w danym statusie."""
    state = _read_state()
    return [o for o in state.values() if o.get("status") == status]


def get_by_courier(courier_id: str, statuses: Optional[list] = None) -> list:
    """Zwraca zlecenia przypisane kurierowi. Opcjonalny filtr statusow."""
    state = _read_state()
    result = [o for o in state.values() if o.get("courier_id") == courier_id]
    if statuses:
        result = [o for o in result if o.get("status") in statuses]
    return result


def _pickup_authority_flags(
    event: dict,
    *,
    durable_authorized: bool,
) -> tuple[bool, bool, bool, bool, bool]:
    """Exact outbox attestation zamraża tylko autorytet tego konkretnego eventu."""
    payload = event.get("payload") or {}
    authority = payload.get("committed_authority")
    receipt_verified = False
    if durable_authorized:
        # Exact event został autoryzowany przed zapisem outboxa. Recovery
        # odtwarza dokładne booleany policy lease, nigdy typ authority ani
        # bieżący store flag. Brak/korupcja snapshotu failuje closed.
        try:
            durable_policy = deserialize_committed_time_policy(
                event.get(COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
            )
        except (TypeError, ValueError):
            return False, False, False, False, False
        passive_enabled = durable_policy.passive_guard_enabled
        manual_enabled = durable_policy.manual_passthrough_enabled
        forward_enabled = durable_policy.rutcom_forward_authority_enabled
        receipt_verified = True
    else:
        proof = payload.get("committed_authority_proof")
        observation = (
            proof.get("observation") if isinstance(proof, dict) else None
        )
        needs_receipt = bool(
            authority == "coordinator_receipt"
            or pickup_payload_requires_coordinator_receipt(payload)
            or (
                isinstance(observation, dict)
                and observation.get("source") == "coordinator_force"
            )
        )
        if needs_receipt:
            from dispatch_v2 import coordinator_time_recheck

            receipt_verified = coordinator_time_recheck.verify_claimed_event(
                event
            )
            if receipt_verified:
                # The claim is only a durable journal. Its exact v6 receipt,
                # not current flags and not claim existence, owns authority.
                try:
                    claimed_policy = deserialize_coordinator_event_policy(
                        event
                    )
                except (TypeError, ValueError):
                    return False, False, False, False, False
                return (
                    claimed_policy.passive_guard_enabled,
                    claimed_policy.manual_passthrough_enabled,
                    claimed_policy.rutcom_forward_authority_enabled,
                    True,
                    False,
                )
            return False, False, False, False, False
        passive_enabled = flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True)
        manual_enabled = decision_flag(MANUAL_CK_AUTHORITY_FLAG)
        forward_enabled = decision_flag(
            RUTCOM_FORWARD_AUTHORITY_FLAG
        )
    return (
        passive_enabled,
        manual_enabled,
        forward_enabled,
        receipt_verified,
        durable_authorized,
    )


def _legacy_time_claim_status(event: dict) -> str:
    """Rozpoznaj exact coordinator claim bez nadawania business authority.

    ``verified`` służy wyłącznie do CAS/recovery już związanej legacy intencji.
    Błąd odczytu kolejki pozostaje odróżniony od braku claimu, aby chwilowa
    awaria dowodu nie zamieniła się w terminalne ``superseded``.
    """
    payload = event.get("payload")
    if (
        event.get("event_type")
        not in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}
        or not isinstance(payload, dict)
        or payload.get("source") != "coordinator_force"
        or payload.get("committed_authority") is not None
    ):
        return "not_applicable"
    try:
        from dispatch_v2 import coordinator_time_recheck

        return (
            "verified"
            if coordinator_time_recheck.verify_claimed_event(event)
            else "unverified"
        )
    except Exception as exc:
        _log.error(
            "COORDINATOR_TIME_CLAIM_READ_FAILED "
            f"oid={event.get('order_id')} type={event.get('event_type')} "
            f"error={type(exc).__name__}: {exc}"
        )
        return "read_error"


def _legacy_time_claim_gate(event: dict) -> tuple[str, Optional[str]]:
    """One fail-closed gate before every non-authority coordinator time CAS.

    The exact queue claim is transport authority for both historical time event
    types.  A complete causal envelope cannot replace it.  A missing claim is
    terminally stale; an unreadable queue remains retryable but must never be
    interpreted by a state handler as permission to write.
    """
    claim_status = _legacy_time_claim_status(event)
    if claim_status == "read_error":
        return claim_status, "pending"
    if claim_status == "unverified":
        return claim_status, "superseded"
    return claim_status, None


def _pickup_time_event_status(event: dict, current: dict) -> str:
    """Jeden oracle apply/CAS/generacji dla kazdego PICKUP_TIME_UPDATED."""
    payload = event.get("payload") or {}
    target = payload.get("new_pickup_at_warsaw")
    try:
        if not target:
            raise ValueError("missing new_pickup_at_warsaw")
        target_dt = datetime.fromisoformat(str(target))
    except (ValueError, TypeError):
        return "superseded"

    pending_initial_intent = current.get(NEW_ORDER_TIME_INTENT_FIELD)
    if pending_initial_intent is not None:
        # A NEW_ORDER shell is an exclusive write lease for the exact durable
        # initial intent. No sibling legacy pickup event may consume/erase it,
        # even if that event would otherwise pass the ordinary pickup CAS.
        if (
            not new_order_time_intent_is_valid(
                pending_initial_intent,
                order_id=current.get("order_id"),
            )
            or payload.get(NEW_ORDER_TIME_INTENT_ID_FIELD)
            != pending_initial_intent.get("intent_id")
        ):
            return "superseded"

    authority = payload.get("committed_authority")
    if not authority:
        if pickup_event_has_authority_artifact(event):
            # Dowolny zachowany marker authority rezerwuje całą kopertę. Utrata
            # jednego pola nie może zdegradować proof-bound eventu do legacy.
            return "superseded"
        if (
            _is_czasowka_order(current)
            and pickup_payload_requires_coordinator_receipt(payload)
        ):
            # ``coordinator_force`` bez proofu jest zarezerwowany tylko dla
            # czasówki. Ten sam source jest legalnym deliberate legacy eventem
            # elastyka i nie może być klasyfikowany bez kontekstu zamówienia.
            return "superseded"
        legacy_claim, claim_effect = _legacy_time_claim_gate(event)
        if claim_effect is not None:
            return claim_effect
        cas_status = time_event_cas_status(
            current,
            event,
            allow_unversioned_ck_claim=(legacy_claim == "verified"),
        )
        if cas_status is not None:
            return cas_status
        if legacy_claim == "verified":
            expected_revision = normalize_pickup_revision(
                payload.get("pickup_time_revision_at_observation")
            )
            current_revision = normalize_pickup_revision(
                current.get("pickup_time_revision", 0)
            )
            if expected_revision is None or current_revision is None:
                return "superseded"
            if current.get("pickup_at_warsaw") == target:
                return (
                    "applied"
                    if current_revision == expected_revision + 1
                    else "superseded"
                )
            if (
                current.get("status") not in {"planned", "assigned"}
                or current.get("picked_up_at") is not None
                or current.get("delivered_at") is not None
                or current.get("pickup_at_warsaw")
                != payload.get("old_pickup_at_warsaw")
                or current_revision != expected_revision
                or str(current.get("courier_id") or "")
                != str(payload.get("courier_id_at_observation") or "")
                or str(current.get("assignment_event_id") or "")
                != str(
                    payload.get("assignment_event_id_at_observation") or ""
                )
            ):
                return "superseded"
            return "pending"
        # Exact legacy oracle z base: dark deploy nie zmienia ani CAS, ani
        # mirror-postcondition, ani retry już istniejących pickup eventów.
        if current.get("status") in {
            "delivered",
            "returned_to_pool",
            "cancelled",
        }:
            return "superseded"
        return (
            "applied"
            if current.get("pickup_at_warsaw") == target
            else "pending"
        )

    durable_authorized = False
    if "committed_authority_attestation" in event:
        from dispatch_v2.committed_pickup_apply import (
            verify_durable_authority_attestation,
        )

        durable_authorized = verify_durable_authority_attestation(event)
        if not durable_authorized:
            return "superseded"

    revision_raw = payload.get("pickup_time_revision_at_observation")
    expected_revision = normalize_pickup_revision(
        0 if revision_raw is None else revision_raw
    )
    current_revision = normalize_pickup_revision(
        current.get("pickup_time_revision", 0)
    )
    if expected_revision is None or current_revision is None:
        return "superseded"
    if committed_pickup_effect_applied(current, payload):
        return "applied"

    if (
        current.get("status") not in {"planned", "assigned"}
        or current.get("picked_up_at") is not None
        or current.get("delivered_at") is not None
    ):
        return "superseded"
    if current.get("pickup_at_warsaw") != payload.get(
        "old_pickup_at_warsaw"
    ):
        return "superseded"
    if current_revision != expected_revision:
        return "superseded"
    if str(current.get("courier_id") or "") != str(
        payload.get("courier_id_at_observation") or ""
    ):
        return "superseded"
    if str(current.get("assignment_event_id") or "") != str(
        payload.get("assignment_event_id_at_observation") or ""
    ):
        return "superseded"

    (
        passive_enabled,
        manual_enabled,
        forward_enabled,
        receipt_verified,
        durable_authorized,
    ) = _pickup_authority_flags(
        event,
        durable_authorized=durable_authorized,
    )
    validation = validate_committed_pickup_event(
        current,
        event,
        is_czasowka=_is_czasowka_order(
            project_time_event_order(current, event)
        ),
        passive_guard_enabled=passive_enabled,
        manual_passthrough_enabled=manual_enabled,
        rutcom_forward_authority_enabled=forward_enabled,
        coordinator_receipt_verified=receipt_verified,
        durable_attestation_verified=durable_authorized,
    )
    if validation.outcome is not ResolutionOutcome.APPLY:
        return "superseded"
    return "pending"


def event_effect_status(
    event: dict,
    current=_FSM_CURRENT_UNSET,
) -> str:
    """Stan postcondition: ``applied`` / ``pending`` / ``superseded``.

    Sam bool nie wystarcza: terminal nowszy od oczekującego eventu nie jest ani
    "brakiem apply", ani dowodem, że wolno odtworzyć stary event. ``superseded``
    zatrzymuje stale/out-of-order retry. Funkcja jest read-only; weryfikację
    wersji ``updated_at`` robi durable outbox.
    """
    durable_policy = None
    if COMMITTED_TIME_POLICY_SNAPSHOT_FIELD in event:
        try:
            durable_policy = deserialize_committed_time_policy(
                event.get(COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
            )
            policy_payload = event.get("payload")
            if event.get("event_type") == "NEW_ORDER":
                if (
                    durable_policy.producer != "panel_watcher"
                    or event.get(NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD)
                    is not durable_policy.initial_time_authority_enabled
                ):
                    return "superseded"
            else:
                if (
                    event.get("event_type")
                    not in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}
                    or not isinstance(policy_payload, dict)
                ):
                    return "superseded"
                validate_committed_time_policy_source(
                    durable_policy,
                    (
                        policy_payload.get("observed_source")
                        if policy_payload.get("committed_authority") is not None
                        else policy_payload.get("source")
                    ),
                )
        except (TypeError, ValueError):
            return "superseded"
    oid = event.get("order_id")
    if not oid:
        return "pending"
    if current is _FSM_CURRENT_UNSET:
        current = get_order(str(oid))
    if not current:
        if event.get("event_type") == "ORDER_DETAILS_ENRICHED":
            return "superseded"
        payload = event.get("payload")
        if (
            event.get("event_type") == "PICKUP_TIME_UPDATED"
            and pickup_event_has_authority_artifact(event)
        ):
            # Claim może przeżyć długi crash i legalny prune terminalnego
            # zlecenia. Brak rekordu oznacza wtedy, że historycznej intencji nie
            # wolno już odtwarzać; exact outbox ma ją domknąć jako superseded.
            # Błąd odczytu nie trafia tutaj — durable layer rozróżnia go i
            # pozostawia receipt pending.
            return "superseded"
        event_type = str(event.get("event_type") or "")
        if event_type in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}:
            payload = event.get("payload")
            if time_event_cas_is_versioned(event_type, payload):
                # A versioned observation proves that an aggregate existed at
                # capture time. Once it is pruned, neither handler can recreate
                # it; retry is impossible work and must terminalize.
                return "superseded"
        legacy_claim, claim_effect = _legacy_time_claim_gate(event)
        if legacy_claim == "verified":
            # Exact claim może przeżyć terminalizację i prune rekordu. Każdy
            # claimowalny typ czasu kończymy tym samym oracle, nie tylko nową
            # kopertę authority.
            return "superseded"
        if claim_effect is not None:
            # Brak exact claimu jest terminalnie stale również przed istnieniem
            # agregatu. Tylko błąd odczytu kolejki pozostaje retryable pending.
            return claim_effect
        return "pending"

    etype = event.get("event_type")
    payload = event.get("payload") or {}
    status = current.get("status")
    if etype == "NEW_ORDER":
        if _new_order_time_authority_enabled(event):
            expected_intent = event.get(NEW_ORDER_TIME_INTENT_FIELD)
            if committed_time_contract_is_complete(current):
                return "applied"
            if (
                new_order_time_intent_is_valid(
                    expected_intent,
                    order_id=oid,
                )
                and current.get(NEW_ORDER_TIME_INTENT_FIELD)
                == expected_intent
            ):
                return "applied"
            return "pending"
        # Późniejsze lifecycle states także dowodzą, że legacy NEW_ORDER był zastosowany.
        return "applied"
    if etype == "ORDER_DETAILS_ENRICHED":
        # Merge-only event nie tworzy rekordu i nie dotyka terminalnego lifecycle.
        # Dla każdego pola pre-existing truth wygrywa; jeśli było puste, handler
        # dokleja wartość z payloadu. Sam brak pustek jest więc postcondition.
        if status in _TERMINAL_ORDER_STATUSES:
            return "superseded"
        if not all(
            payload.get(key) not in (None, "", [], {})
            for key in ORDER_DETAILS_ENRICHMENT_REQUIRED_FIELDS
        ):
            return "superseded"
        if all(
            current.get(key) not in (None, "", [], {})
            for key in ORDER_DETAILS_ENRICHMENT_REQUIRED_FIELDS
        ):
            return "applied"
        return "pending"
    if etype == "COURIER_ASSIGNED":
        # Nie odtwarzaj starego assignment po zamknięciu lub pickupie innego
        # kuriera. Legalny nowy reassign dostaje osobną generację outbox.
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        matches = (
            status in ("assigned", "picked_up")
            and str(current.get("courier_id") or "")
            == str(event.get("courier_id") or "")
        )
        ck_iso = payload.get("czas_kuriera_warsaw")
        ck_hhmm = payload.get("czas_kuriera_hhmm")
        ck_valid = _verify_czas_kuriera_consistency(ck_iso, ck_hhmm, str(oid))
        ck_resolution = _assignment_ck_resolution(current, event)
        ck_write_expected = (
            ck_resolution.outcome is not ResolutionOutcome.SUPPRESS
        )
        # Handler przy uszkodzonym CK nadal trwale stosuje SAM assignment, ale
        # odrzuca oba pola czasu i podnosi CorruptedTimestampError. Oracle musi
        # wtedy oceniac postcondition assignmentu bez wadliwych pol; exact marker
        # rozstrzyga crash po tym czesciowym, swiadomym commicie.
        if matches and ck_valid and ck_write_expected and ck_iso is not None:
            matches = current.get("czas_kuriera_warsaw") == ck_iso
        if matches and ck_valid and ck_write_expected and ck_hhmm is not None:
            matches = current.get("czas_kuriera_hhmm") == ck_hhmm
        if matches:
            return "applied"
        if status == "picked_up":
            return "superseded"
        return "pending"
    if etype == "COURIER_PICKED_UP":
        event_courier = str(event.get("courier_id") or "")
        current_courier = str(current.get("courier_id") or "")
        if event_courier and event_courier != current_courier:
            return "superseded"
        if status == "picked_up":
            if (
                payload.get("source") == "parcel_status_inbox"
                and str(
                    current.get("last_lifecycle_event_id_courier_picked_up") or ""
                )
                != str(event.get("event_id") or "")
            ):
                # The inbox key contains its source generation timestamp, but
                # business state intentionally does not adopt that timestamp.
                # Status+same courier therefore cannot prove this *new* row;
                # only its exact durable marker can acknowledge a crash retry.
                return "superseded"
            return "applied"
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        return "pending"
    if etype == "COURIER_DELIVERED":
        if payload.get("source") == "parcel_status_inbox":
            event_courier = str(event.get("courier_id") or "")
            current_courier = str(current.get("courier_id") or "")
            if event_courier and event_courier != current_courier:
                return "superseded"
        if status == "delivered":
            if (
                payload.get("source") == "parcel_status_inbox"
                and str(
                    current.get("last_lifecycle_event_id_courier_delivered") or ""
                )
                != str(event.get("event_id") or "")
            ):
                return "superseded"
            return "applied"
        if status in ("returned_to_pool", "cancelled"):
            return "superseded"
        return "pending"
    if etype == "ORDER_RESURRECTED":
        desired = str(payload.get("new_status") or "picked_up")
        if desired not in ("assigned", "picked_up"):
            desired = "picked_up"
        event_courier = str(event.get("courier_id") or "")
        current_courier = str(current.get("courier_id") or "")
        if event_courier and event_courier != current_courier:
            return "superseded"
        if status == desired:
            return "applied"
        if status == "delivered":
            return "pending"
        return "superseded"
    if etype == "ORDER_RETURNED_TO_POOL":
        if status == "returned_to_pool":
            return "applied"
        if status in ("delivered", "cancelled"):
            return "superseded"
        return "pending"
    if etype == "CZAS_KURIERA_UPDATED":
        if status in ("delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        new_ck_iso = payload.get("new_ck_iso")
        new_ck_hhmm = payload.get("new_ck_hhmm")
        if not _verify_czas_kuriera_consistency(
            new_ck_iso, new_ck_hhmm, str(oid)
        ):
            # Trwale wadliwy payload nie moze pozostac poison-rowem pending i
            # blokowac causal/downstream lanes. Event jest zachowany w audycie, ale
            # jego state/downstream zostaja terminalnie pominiete.
            return "superseded"
        source = payload.get("source")
        resolution = None
        if _is_czasowka_order(
            project_time_observation_order(current, payload)
        ):
            resolution = resolve_czasowka_ck_observation(
                current,
                payload,
                policy_snapshot=durable_policy,
            )
        if (
            resolution is not None
            and (source in _CK_PASSIVE_SOURCES or source == "coordinator_force")
        ):
            # Legalny committed event jest kanonizowany do PICKUP_TIME_UPDATED
            # przed outboxem. Raw CK nie może zaliczyć cudzego efektu samą
            # równością czasu/provenance; resolver rozstrzyga go od początku.
            if resolution.outcome is ResolutionOutcome.APPLY:
                # Od tej wersji każdy producent kanonizuje legalny raw CK
                # PRZED outboxem. Stary durable raw row nie ma proof-bound
                # attestation i nie może sam stać się drugim transportem.
                return "superseded" if event.get("event_id") else "pending"
            if resolution.outcome is ResolutionOutcome.SUPPRESS:
                return "superseded"
        if (
            resolution is not None
            and _czasowka_raw_ck_writer_is_retired(
                current,
                resolution,
                policy_snapshot=durable_policy,
            )
        ):
            return "superseded"
        legacy_claim, claim_effect = _legacy_time_claim_gate(event)
        if claim_effect is not None:
            return claim_effect
        cas_status = time_event_cas_status(
            current,
            event,
            allow_unversioned_ck_claim=(legacy_claim == "verified"),
        )
        if cas_status is not None:
            return cas_status
        if (
            flag("ENABLE_ELASTYK_CK_NO_BACKWARD", True)
            and not _is_czasowka_order(current)
            and source in _CK_PASSIVE_SOURCES
            and _ck_backward_delta(
                current.get("czas_kuriera_warsaw"), new_ck_iso
            ) is not None
        ):
            return "superseded"
        return "applied" if (
            current.get("czas_kuriera_warsaw") == new_ck_iso
            and current.get("czas_kuriera_hhmm") == new_ck_hhmm
        ) else "pending"
    if etype == "PICKUP_TIME_UPDATED":
        return _pickup_time_event_status(event, current)
    if etype == "ORDER_RECLAIMED_TO_CZASOWKA":
        if not _czasowka_reclaim_live_authorized(event):
            return "superseded"
        previous_cid = str(payload.get("previous_courier_id") or "")
        generation = str(payload.get("reclaim_generation") or "")
        expected_assignment = str(
            payload.get("expected_assignment_event_id") or generation
        )
        expected_pickup = payload.get("expected_pickup_at_warsaw")
        if (
            status == "planned"
            and str(current.get("courier_id") or "") == "26"
            and str(current.get("previous_courier_id") or "") == previous_cid
            and str(current.get("reclaim_generation") or "") == generation
            and current.get("reclaimed_at") == payload.get("reclaimed_at")
            and current.get("reason") == payload.get("reason")
        ):
            return "applied"
        if status in ("picked_up", "delivered", "returned_to_pool", "cancelled"):
            return "superseded"
        if (
            status != "assigned"
            or current.get("picked_up_at") is not None
            or bool(str(current.get("czas_kuriera_warsaw") or "").strip())
        ):
            return "superseded"
        if not previous_cid or previous_cid == "26":
            return "superseded"
        if str(current.get("courier_id") or "") != previous_cid:
            return "superseded"
        if (
            not expected_assignment
            or str(current.get("assignment_event_id") or "")
            != expected_assignment
        ):
            return "superseded"
        if expected_pickup and current.get("pickup_at_warsaw") != expected_pickup:
            return "superseded"
        return "pending"
    return "pending"


def event_effect_is_applied(
    event: dict,
    current=_FSM_CURRENT_UNSET,
) -> bool:
    """Kompatybilny bool-oracle; stale terminal NIE jest "applied"."""
    return event_effect_status(event, current=current) == "applied"


def _sanitize_ingest_coords(order_id: str, data: dict) -> dict:
    """L2.1 sentinel-ingest (2026-07-01, K5a): JEDEN chokepoint walidacji coords
    na wejściu do orders_state — pokrywa NEW_ORDER (oba branche), COURIER_PICKED_UP,
    COURIER_DELIVERED, parcel_lane_merge i każdego przyszłego writera przez upsert.

    Wartość niepoprawna ((0,0)/NaN/poza-bbox — `coords_in_bialystok_bbox`) →
    klucz USUWANY z data (merge {**existing, **data} zachowuje ewentualne DOBRE
    istniejące coords — wzorzec sink-guard 2026-06-13) + log.warning. Flaga OFF
    = pass-through legacy. Zwraca data (kopię przy modyfikacji)."""
    if not decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        return data
    bad = [
        k for k in ("pickup_coords", "delivery_coords")
        if k in data and data[k] is not None
        and not coords_in_bialystok_bbox(data[k])
    ]
    if not bad:
        return data
    data = dict(data)
    for k in bad:
        _log.warning(
            f"COORD_INGEST_GUARD upsert {order_id}: {k}={data[k]!r} "
            f"odrzucone (sentinel/poza-bbox) — klucz pominięty"
        )
        del data[k]
    return data


# ── R-DECLARED tripwire (L7.1, audyt 2026-06-30 root R7-I-E) ──────────────────
# Reguła R-DECLARED-TIME (HARD): `czas_kuriera >= czas_odbioru_timestamp` — dziś
# NIE ma runtime-inwariantu (tylko komentarze; egzekucja pośrednia przez SOFT
# R27 → zmiana R27 cicho ją łamie). JEDEN obserwacyjny tripwire w chokepoincie
# zapisu (upsert_order = jedyny funnel commitowanego czas_kuriera do orders_state
# — pokrywa NEW_ORDER / COURIER_ASSIGNED / CZAS_KURIERA_UPDATED / PICKUP_TIME_
# UPDATED / resurrect / każdego przyszłego writera). Fail-loud LOG + append JSONL,
# NIGDY reject/zmiana `merged` (always-propose). OFF = zero kodu ścieżki.
#
# Edge/throttle per-oid: ta sama para (czas_kuriera, czas_odbioru) logowana RAZ
# (nie spamuje co tick/re-upsert). Zmiana którejkolwiek wartości = nowy stan
# naruszenia = ponowny wpis. Pamięć throttle = module-level (proces długożyjący:
# shadow/panel-watcher/plan-recheck); cap bezpieczeństwa vs nieograniczony wzrost.
_R_DECLARED_LOGGED: dict = {}       # oid -> (ck_iso, pickup_iso) ostatnio zalogowane naruszenie
_R_DECLARED_LOGGED_CAP = 10000      # audyt: żadnych nieograniczonych cache — reset przy przepełnieniu


def _to_warsaw_axis(s: Optional[str]) -> Optional[datetime]:
    """Parsuje ISO timestamp na WSPÓLNĄ oś porównania (aware, Europe/Warsaw).

    `czas_kuriera_warsaw` = aware ISO z offsetem (+02:00). `pickup_at_warsaw`
    (= czas_odbioru_timestamp) też aware Warsaw w praktyce, ale bywa naive w
    historycznych/alternatywnych ścieżkach → naive traktujemy jako Warsaw-local
    przez ZoneInfo (NIGDY fixed-offset — DST by złamał; ratchet TZ). Zwraca
    aware datetime (porównanie instant-owe, poprawne pod DST) lub None."""
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_WARSAW_TZ)
    return dt


def _r_declared_tripwire(order_id: str, merged: dict, event: Optional[str]) -> None:
    """L7.1: obserwacyjny strażnik R-DECLARED-TIME na zmergowanym rekordzie.

    NIGDY nie modyfikuje `merged` ani nie wpływa na decyzję — tylko log + JSONL.
    Flaga OFF = natychmiastowy return (bajt-parytet ścieżki decyzji). Defensywny:
    żaden wyjątek stąd nie może przerwać zapisu stanu (opakowane w callerze)."""
    if not flag("ENABLE_R_DECLARED_TRIPWIRE", ENABLE_R_DECLARED_TRIPWIRE):
        return
    ck = _to_warsaw_axis(merged.get("czas_kuriera_warsaw"))
    pickup = _to_warsaw_axis(merged.get("pickup_at_warsaw"))
    if ck is None or pickup is None:
        return  # brak którejkolwiek deklaracji — nic do sprawdzenia
    delta_min = (ck - pickup).total_seconds() / 60.0
    # Reguła: czas_kuriera >= czas_odbioru. Naruszenie = ck wcześniejszy niż
    # pickup poza tolerancją (default 0.0 = ścisła nierówność).
    if delta_min >= -R_DECLARED_TRIPWIRE_TOLERANCE_MIN:
        return
    ck_iso = merged.get("czas_kuriera_warsaw")
    pickup_iso = merged.get("pickup_at_warsaw")
    sig = (ck_iso, pickup_iso)
    if _R_DECLARED_LOGGED.get(order_id) == sig:
        return  # to samo naruszenie już zalogowane — throttle (edge-triggered)
    if len(_R_DECLARED_LOGGED) >= _R_DECLARED_LOGGED_CAP:
        _R_DECLARED_LOGGED.clear()  # reset bezpieczeństwa (najwyżej pojedynczy re-log)
    _R_DECLARED_LOGGED[order_id] = sig
    record = {
        "ts": now_iso(),
        "oid": order_id,
        "event": event,                       # źródło zapisu (NEW_ORDER / COURIER_ASSIGNED / ...)
        "status": merged.get("status"),
        "order_type": merged.get("order_type"),
        "czas_kuriera_hhmm": merged.get("czas_kuriera_hhmm"),
        "czas_kuriera_warsaw": ck_iso,
        "czas_odbioru_timestamp": pickup_iso,  # = pickup_at_warsaw
        "delta_min": round(delta_min, 2),
        "courier_id": merged.get("courier_id"),
    }
    _log.warning(
        f"R_DECLARED_VIOLATION oid={order_id} event={event} "
        f"czas_kuriera={merged.get('czas_kuriera_hhmm')} ({ck_iso}) < "
        f"czas_odbioru={pickup_iso} Δ={delta_min:+.1f}min "
        f"(R-DECLARED-TIME HARD — obserwacyjny, decyzja NIEzmieniana)"
    )
    try:
        log_path = os.path.join(os.path.dirname(_state_path()), "r_declared_tripwire.jsonl")
        append_jsonl(log_path, record)
    except Exception as _e:
        _log.debug(f"R_DECLARED tripwire jsonl append skip oid={order_id}: {_e}")


def _merge_new_order_time_intent_backfill(
    order_id: str,
    existing: dict,
    incoming: dict,
) -> tuple[dict, bool]:
    """Backfill only the pending receipt, never replay NEW_ORDER lifecycle."""
    intent = incoming.get(NEW_ORDER_TIME_INTENT_FIELD)
    if not new_order_time_intent_is_valid(intent, order_id=order_id):
        return dict(existing), False
    current_intent = existing.get(NEW_ORDER_TIME_INTENT_FIELD)
    if current_intent == intent or committed_time_contract_is_complete(existing):
        return dict(existing), False
    if current_intent is not None:
        _log.error(
            "NEW_ORDER initial intent conflict refused "
            f"oid={order_id}"
        )
        return dict(existing), False
    merged = dict(existing)
    merged[NEW_ORDER_TIME_INTENT_FIELD] = dict(intent)
    return merged, True


@_lifecycle_state_mutation
def claim_initial_auto_koord_attempt(
    order_id: str,
    *,
    trigger: str,
    decision_reason: str,
) -> bool:
    """Atomically claim the one-life initial AUTO_KOORD attempt.

    Claim jest zapisywany przed obcym panelem. Sama obecność pola (także
    malformed/``None``) blokuje kolejną próbę, dzięki czemu ręczne oddanie
    zlecenia przez Koordynatora nigdy nie uzbraja ponownego auto-przypisania.
    Marker celowo nie zmienia historii ani ``updated_at``: nie jest eventem
    biznesowym, tylko trwałym fence'em side-effectu.
    """
    oid = str(order_id)
    with _locked_write() as path:
        state = _read_state_strict()
        old_count = len(state)
        current = state.get(oid)
        if not isinstance(current, dict):
            return False
        if AUTO_KOORD_INITIAL_ATTEMPT_FIELD in current:
            return False
        claimed = dict(current)
        claimed[AUTO_KOORD_INITIAL_ATTEMPT_FIELD] = {
            "schema": "auto_koord_initial_attempt.v1",
            "claimed_at": now_iso(),
            "trigger": str(trigger),
            "decision_reason": str(decision_reason),
        }
        state[oid] = claimed
        _guarded_write(
            path,
            state,
            old_count,
            op="auto_koord_initial_attempt",
        )
        _log.info(
            "AUTO_KOORD initial attempt claimed oid=%s trigger=%s reason=%s",
            oid,
            trigger,
            decision_reason,
        )
        return True


@_lifecycle_state_mutation
def upsert_order(
    order_id: str,
    data: dict,
    event: Optional[str] = None,
    *,
    require_existing: bool = False,
) -> dict:
    """Dodaje lub aktualizuje zlecenie. Zapisuje history entry.
    Zwraca zaktualizowany rekord."""
    data = _sanitize_ingest_coords(order_id, data)
    with _locked_write() as path:
        state = _read_state_strict()        # Faza 1: raise StateReadError zamiast cichego {}
        old_count = len(state)
        existing = state.get(order_id, {})
        if event == "NEW_ORDER":
            incoming_marker = data.get(
                "last_lifecycle_event_id_new_order"
            )
            existing_marker = existing.get(
                "last_lifecycle_event_id_new_order"
            )
            if existing_marker:
                # NEW_ORDER jest zdarzeniem tworzącym: pierwszy zastosowany
                # event pozostaje źródłem predykatu authority_scope. Retry tego
                # samego event_id nie dopisuje historii ani nie dotyka pliku;
                # późniejszy NEW_ORDER nie może cofnąć już żywego zlecenia.
                if incoming_marker != existing_marker:
                    _log.warning(
                        "NEW_ORDER duplicate marker refused "
                        f"oid={order_id} first={existing_marker} "
                        f"incoming={incoming_marker}"
                    )
                merged_existing, intent_changed = (
                    _merge_new_order_time_intent_backfill(
                        order_id,
                        existing,
                        data,
                    )
                )
                if intent_changed:
                    state[order_id] = merged_existing
                    _guarded_write(
                        path,
                        state,
                        old_count,
                        op="new_order_intent_backfill",
                    )
                return merged_existing
            if existing:
                # Rekord sprzed ery markerów jest już żywym agregatem. NEW_ORDER
                # może tu wyłącznie uzupełnić brakujący dowód first-write; nie
                # wolno mu ponownie zastosować create-payloadu, dopisać historii
                # ani odświeżyć updated_at, bo cofnąłby assignment/lifecycle.
                if not incoming_marker:
                    _log.warning(
                        "NEW_ORDER duplicate without marker refused "
                        f"oid={order_id}"
                    )
                    return dict(existing)
                marked_existing, _intent_changed = (
                    _merge_new_order_time_intent_backfill(
                        order_id,
                        existing,
                        data,
                    )
                )
                marked_existing[
                    "last_lifecycle_event_id_new_order"
                ] = str(incoming_marker)
                state[order_id] = marked_existing
                _guarded_write(
                    path,
                    state,
                    old_count,
                    op="new_order_marker_backfill",
                )
                _log.info(
                    "NEW_ORDER marker backfilled without lifecycle merge "
                    f"oid={order_id} marker={incoming_marker}"
                )
                return marked_existing
            data = dict(data)
            # Jawne None jest dowodem „nieprzypisany”; brak klucza nie może
            # autoryzować AUTO. TYLKO setdefault przy pierwszym create:
            # NEW_ORDER niosący już przypisanie (recanon/import) musi je
            # ZACHOWAĆ — nadpisanie na None czyniłoby zlecenie fałszywie
            # „nieprzypisanym” w dowodach scope (kierunek fail-open).
            data.setdefault("courier_id", None)
        # A caller-side existence check is not authoritative: durable retry or
        # a concurrent prune can change state before apply. Keep this
        # precondition inside the single locked orders_state write funnel.
        if require_existing and not existing:
            raise MissingOrderPreconditionError(
                f"{event or 'upsert'} refused for absent order {order_id}"
            )
        merged = {**existing, **data, "order_id": order_id}

        # History
        history = existing.get("history", [])
        if event:
            history.append({"at": now_iso(), "event": event, "status": merged.get("status")})
        merged["history"] = history
        merged["updated_at"] = now_iso()

        state[order_id] = merged
        _guarded_write(path, state, old_count, op="upsert")
        _log.info(f"upsert {order_id} status={merged.get('status')} event={event}")
        # L7.1 R-DECLARED tripwire — obserwacyjny, PO commicie zapisu; nigdy nie
        # zmienia `merged` ani decyzji. Defensywnie: żaden błąd stąd nie wpływa
        # na zwrot (already-persisted record).
        try:
            _r_declared_tripwire(order_id, merged, event)
        except Exception as _tw_e:
            _log.debug(f"R_DECLARED tripwire skip oid={order_id}: {_tw_e}")
        return merged


@_lifecycle_state_mutation
def set_status(order_id: str, status: str, extra: Optional[dict] = None, event: Optional[str] = None) -> Optional[dict]:
    """Zmiana statusu + dodatkowe pola."""
    if status not in ORDER_STATUSES:
        raise ValueError(f"Nieznany status: {status}. Dozwolone: {ORDER_STATUSES}")
    data = {"status": status}
    if extra:
        data.update(extra)
    return upsert_order(order_id, data, event=event)


@_lifecycle_state_mutation
def update_from_event(
    event: dict,
    *,
    authority_policy: Optional[CommittedPickupPolicySnapshot] = None,
) -> Optional[dict]:
    """Konsumuje event z event busa i aktualizuje state machine.
    Zwraca zaktualizowany rekord lub None."""
    durable_policy = None
    if COMMITTED_TIME_POLICY_SNAPSHOT_FIELD in event:
        try:
            durable_policy = deserialize_committed_time_policy(
                event.get(COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
            )
        except (TypeError, ValueError) as exc:
            _log.error(
                "TIME_POLICY_SNAPSHOT_INVALID oid=%s error=%s",
                event.get("order_id"),
                exc,
            )
            return None
    if authority_policy is not None:
        if type(authority_policy) is not CommittedPickupPolicySnapshot:
            raise TypeError(
                "authority_policy must be CommittedPickupPolicySnapshot"
            )
        if durable_policy is not None and durable_policy != authority_policy:
            raise ValueError(
                "in-process and durable committed time policies differ"
            )
    else:
        authority_policy = durable_policy
    if authority_policy is not None:
        policy_payload = event.get("payload")
        if event.get("event_type") == "NEW_ORDER":
            if (
                authority_policy.producer != "panel_watcher"
                or event.get(NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD)
                is not authority_policy.initial_time_authority_enabled
            ):
                raise ValueError("NEW_ORDER requires panel_watcher policy")
        else:
            if (
                event.get("event_type")
                not in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}
                or not isinstance(policy_payload, dict)
            ):
                raise ValueError(
                    "authority_policy is reserved for time events"
                )
            policy_source = (
                policy_payload.get("observed_source")
                if policy_payload.get("committed_authority") is not None
                else policy_payload.get("source")
            )
            validate_committed_time_policy_source(
                authority_policy, policy_source
            )

    # Z-P1-01 Phase A: formal FSM shadow.  It is intentionally fail-open and
    # log-only; legacy behavior below (including current fallbacks/exceptions)
    # remains the sole writer until a separately approved enforcement phase.
    _observe_order_event(event)
    etype = event["event_type"]
    oid = event.get("order_id")
    payload = event.get("payload", {})
    if not oid:
        return None

    durable_event_id = event.get("event_id")

    def _marked(fields: dict) -> dict:
        """Powiaz exact outbox event z tym samym atomowym zapisem stanu."""
        if not durable_event_id:
            return fields
        marked = dict(fields)
        marked["last_lifecycle_event_id"] = str(durable_event_id)
        marker_type = "".join(
            ch.lower() if ch.isalnum() else "_" for ch in str(etype)
        ).strip("_")
        if marker_type:
            # Marker per typ nie ginie po ortogonalnym evencie (np. ASSIGNED,
            # potem CZAS_KURIERA_UPDATED przed receiptem outboxa).
            marked[f"last_lifecycle_event_id_{marker_type}"] = str(durable_event_id)
        alias_type = "".join(
            ch.lower() if ch.isalnum() else "_"
            for ch in str(event.get("state_marker_alias_event_type") or "")
        ).strip("_")
        if alias_type:
            # Raw CK defense tłumaczy efekt na PICKUP_TIME_UPDATED, ale receipt
            # outboxa nadal ma typ CZAS_KURIERA_UPDATED. Oba markery powstają w
            # tym samym atomowym rename, więc crash recovery nie gubi callbacku.
            marked[f"last_lifecycle_event_id_{alias_type}"] = str(
                durable_event_id
            )
        return marked

    if etype == "NEW_ORDER":
        # V3.19f: sanity check czas_kuriera consistency przed persist.
        initial_time_owned = bool(
            _is_czasowka_order(payload)
            and _new_order_time_authority_enabled(event)
        )
        # Under the new owner NEW_ORDER creates only the aggregate shell. The
        # exact Rutcom tuple is resolved immediately by PICKUP_TIME_UPDATED;
        # raw pickup and CK can never become two independently persisted truths.
        pickup_at_warsaw = (
            None if initial_time_owned else payload.get("pickup_at_warsaw")
        )
        ck_iso = (
            None if initial_time_owned else payload.get("czas_kuriera_warsaw")
        )
        ck_hhmm = (
            None if initial_time_owned else payload.get("czas_kuriera_hhmm")
        )
        initial_intent_fields = {}
        if initial_time_owned:
            raw_intent = event.get(NEW_ORDER_TIME_INTENT_FIELD)
            if new_order_time_intent_is_valid(raw_intent, order_id=oid):
                initial_intent_fields[NEW_ORDER_TIME_INTENT_FIELD] = dict(
                    raw_intent
                )
            else:
                # The shell stays mechanically incomplete and blocks rollout
                # or code rollback.  Missing/corrupt receipt can never revive
                # the legacy raw CK writer as an accidental recovery path.
                initial_intent_fields[NEW_ORDER_TIME_INTENT_FIELD] = {
                    "schema": "invalid_new_order_time_intent",
                }
        if not _verify_czas_kuriera_consistency(ck_iso, ck_hhmm, oid):
            # Skip persist czas_kuriera fields; log ERROR w helper; raise signal.
            # Inne pola persistowane bez zmian (order dalej trafia do state).
            ck_iso = None
            ck_hhmm = None
            _result = upsert_order(oid, _marked({
                **initial_intent_fields,
                "status": "planned",
                "commitment_level": "planned",
                "restaurant": payload.get("restaurant"),
                "pickup_address": payload.get("pickup_address"),
                "delivery_address": payload.get("delivery_address"),
                "pickup_time_minutes": payload.get("pickup_time_minutes"),
                "first_seen": payload.get("first_seen") or now_iso(),
                "address_id": payload.get("address_id"),
                "pickup_coords": payload.get("pickup_coords"),
                "delivery_coords": payload.get("delivery_coords"),
                "pickup_at_warsaw": pickup_at_warsaw,
                "prep_minutes": payload.get("prep_minutes"),
                "order_type": payload.get("order_type"),
                "bag_time_alerted": False,
                # Tech debt #19a/b/c (2026-05-07) — audit + SLA fields:
                "decision_deadline": payload.get("decision_deadline"),
                "zmiana_czasu_odbioru": payload.get("zmiana_czasu_odbioru"),
                "created_at_utc": payload.get("created_at_utc"),
                "reclaim_exempt": payload.get("reclaim_exempt") is True,
                "reclaim_exempt_reason": payload.get("reclaim_exempt_reason"),
            }), event="NEW_ORDER")
            raise CorruptedTimestampError(
                f"NEW_ORDER {oid}: czas_kuriera sanity fail, "
                f"persisted bez czas_kuriera fields"
            )
        return upsert_order(oid, _marked({
            **initial_intent_fields,
            "status": "planned",
            "commitment_level": "planned",
            "restaurant": payload.get("restaurant"),
            "pickup_address": payload.get("pickup_address"),
            "delivery_address": payload.get("delivery_address"),
            "pickup_time_minutes": payload.get("pickup_time_minutes"),
            "first_seen": payload.get("first_seen") or now_iso(),
            "address_id": payload.get("address_id"),
            "pickup_coords": payload.get("pickup_coords"),
            "delivery_coords": payload.get("delivery_coords"),
            "pickup_at_warsaw": pickup_at_warsaw,
            "prep_minutes": payload.get("prep_minutes"),
            "order_type": payload.get("order_type"),
            "uwagi": payload.get("uwagi"),
            "uwagi_pickup_parsed": payload.get("uwagi_pickup_parsed"),
            # CZASÓWKA-W-UWAGACH SHADOW (2026-06-28, sesja 20): deklarowany deadline
            # DOSTAWY sparsowany z `uwagi` (panel_client, za flagą). ADDITYWNE — żaden
            # konsument decyzyjny go jeszcze nie czyta. None gdy flaga OFF / brak frazy.
            "delivery_deadline_uwagi": payload.get("delivery_deadline_uwagi"),
            # V3.19f: czas_kuriera 2-field persist (ISO Warsaw + raw HH:MM).
            "czas_kuriera_warsaw": ck_iso,
            "czas_kuriera_hhmm": ck_hhmm,
            "bag_time_alerted": False,  # F2.1b step 5: R6 pre-warning gate init
            # Tech debt #19a/b/c (2026-05-07) — audit + SLA fields:
            # decision_deadline (czas_na_decyzje), zmiana_czasu_odbioru (panel
            # zmienił pickup time flag), created_at_utc (single age anchor).
            "decision_deadline": payload.get("decision_deadline"),
            "zmiana_czasu_odbioru": payload.get("zmiana_czasu_odbioru"),
            "created_at_utc": payload.get("created_at_utc"),
            "reclaim_exempt": payload.get("reclaim_exempt") is True,
            "reclaim_exempt_reason": payload.get("reclaim_exempt_reason"),
        }), event="NEW_ORDER")

    if etype == "ORDER_DETAILS_ENRICHED":
        # Fill-missing-only pod lifecycle_apply_lock (update_from_event i
        # upsert_order są reentrant). Dzięki temu równoległy lifecycle writer nie
        # może wejść między odczyt a merge, a replay nie zmienia updated_at/history.
        existing = get_order_strict(oid)
        if not existing:
            raise MissingOrderPreconditionError(
                f"ORDER_DETAILS_ENRICHED refused for absent order {oid}"
            )
        if existing.get("status") in _TERMINAL_ORDER_STATUSES:
            return existing
        missing_details = {
            key: payload.get(key)
            for key in ORDER_DETAILS_ENRICHMENT_FIELDS
            if existing.get(key) in (None, "", [], {})
            and payload.get(key) not in (None, "", [], {})
        }
        if not missing_details:
            return existing
        return upsert_order(
            oid,
            missing_details,
            event="ORDER_DETAILS_ENRICHED",
            require_existing=True,
        )

    if etype == "COURIER_ASSIGNED":
        def _persist_assignment_and_availability(fields: dict) -> dict:
            """Commit zlecenia, a dopiero potem oznacz CID jako jawnie ON.

            Jeden chokepoint obejmuje zwykłe przypisanie, czasówkę i ścieżkę
            zachowania statusu terminalnego. Błąd sidecara nie cofa poprawnie
            zapisanego eventu zlecenia, ale jest jawnie logowany.
            """
            marked = _marked(fields)
            if payload.get("source") == "parcel_assign":
                result = upsert_order(
                    oid,
                    marked,
                    event="COURIER_ASSIGNED",
                    require_existing=True,
                )
            else:
                result = upsert_order(
                    oid, marked, event="COURIER_ASSIGNED"
                )
            try:
                _availability_enabled = decision_flag(
                    "ENABLE_CID_AVAILABILITY_CONTRACT"
                )
            except Exception:
                _availability_enabled = False
            if _availability_enabled:
                from dispatch_v2 import courier_availability as _availability
                # Precedencja po czasie zdarzenia: opóźniony assignment nie może
                # wskrzesić nowszego jawnego OFF. Przekazujemy realny czas eventu,
                # nie now(). Brak/nie-ISO created_at → None → writer użyje now().
                _assign_at = None
                _created_raw = event.get("created_at")
                if isinstance(_created_raw, str) and _created_raw:
                    try:
                        _assign_at = datetime.fromisoformat(_created_raw)
                    except ValueError:
                        _assign_at = None
                try:
                    _availability.set_operator_availability(
                        event.get("courier_id"),
                        _availability.AvailabilityState.OPERATOR_ON,
                        _availability.AvailabilityProvenance.ASSIGNMENT_EVENT,
                        at=_assign_at,
                    )
                except Exception as _availability_error:
                    # R-POOL-TRUTH fail-closed: błąd zapisu availability NIE może
                    # raportować sukcesu assignmentu (dawniej fail-open/split-brain).
                    _log.error(
                        "R_POOL_TRUTH assignment availability write failed "
                        f"oid={oid} cid={event.get('courier_id')}: "
                        f"{type(_availability_error).__name__}"
                    )
                    raise
            return result

        # V3.28 P4 — auto-activation koordynatora (Adrian doktryna 2026-05-10).
        # Bartek O. (cid=123) ma flag `coordinator: true` w courier_tiers.json.
        # Pierwsze COURIER_ASSIGNED dnia → activate (może już dziś jeździć).
        # Późniejsze ASSIGNED zachowują state (idempotent).
        try:
            _ev_cid = str(event.get("courier_id") or "")
            if _ev_cid:
                from dispatch_v2.courier_resolver import _load_courier_tiers
                from dispatch_v2 import coordinator_activations as _coord_act
                _tiers = _load_courier_tiers()
                _tinfo = _tiers.get(_ev_cid) if isinstance(_tiers, dict) else None
                if isinstance(_tinfo, dict) and _tinfo.get("coordinator") is True:
                    _changed = _coord_act.activate(_ev_cid, source=f"first_assignment_{oid}")
                    if _changed:
                        _log.info(
                            f"P4 COORDINATOR_ACTIVATED cid={_ev_cid} ({_tinfo.get('name','?')}) "
                            f"trigger=first_assignment oid={oid}"
                        )
        except Exception as _e:
            _log.warning(f"P4 coordinator auto-activate fail oid={oid}: {_e}")

        # V3.19f: update czas_kuriera przy re-assignment (panel "+15min" button
        # może zmienić commitment). Sanity check przed update.
        # V3.27.5 Path B (2026-04-27): preserve terminal status (picked_up,
        # delivered) na subsequent COURIER_ASSIGNED. Pre-fix: panel_diff
        # COURIER_ASSIGNED post-PICKED_UP nadpisywał status="picked_up" → "assigned",
        # tworząc inconsistency (status=assigned + picked_up_at SET) — TASK H
        # diagnoza 2026-04-27 wykryła 13.4% rate (185/1384 picked-up orders 7d).
        # Race condition: PICKED_UP (reconcile) + COURIER_ASSIGNED (panel_diff)
        # fire same panel_watcher cycle, ASSIGNED ~12-18s later → status revert.
        ck_iso = payload.get("czas_kuriera_warsaw")
        ck_hhmm = payload.get("czas_kuriera_hhmm")
        # V3.27.5 Path B: check current status — preserve terminal states
        prev = get_order(oid) or {}
        prev_status = prev.get("status")
        if prev_status in ("picked_up", "delivered"):
            # Order już terminal — preserve status. Update tylko legitimate
            # re-assignment fields (courier_id, czas_kuriera) jeśli zmienione.
            new_status = prev_status
            _log.warning(
                f"COURIER_ASSIGNED {oid} ignored status revert: "
                f"prev_status={prev_status}, source={event.get('source','?')}, "
                f"courier_id_new={event.get('courier_id')} courier_id_old={prev.get('courier_id')} "
                f"(V3.27.5 Path B preserve terminal)"
            )
        else:
            new_status = "assigned"
        merged = {
            "status": new_status,
            "commitment_level": new_status if new_status in ("picked_up", "delivered") else "assigned",
            "courier_id": event.get("courier_id"),
            "assigned_at": now_iso(),
            "proposed_delivery_time": payload.get("proposed_time"),
            "bag_time_alerted": False,  # F2.1b step 5: reset on new assignment / reassignment
        }
        # CZASOWKA-RECLAIM: causality boundary. Durable event_id jest
        # generacja assignmentu; pickup snapshot dowodzi, ze pozniejszy event
        # czasu powstal PO przypisaniu, nie odwrotnie. Legacy inline assignment
        # bez event_id pozostaje fail-closed dla reclaimu.
        if durable_event_id:
            merged["assignment_event_id"] = str(durable_event_id)
            merged["pickup_at_at_assignment"] = prev.get("pickup_at_warsaw")
        # Jawny wyjatek operatora; brak klucza zachowuje poprzednia wartosc.
        if "reclaim_exempt" in payload:
            merged["reclaim_exempt"] = payload.get("reclaim_exempt") is True
            merged["reclaim_exempt_reason"] = payload.get(
                "reclaim_exempt_reason"
            )
        # L4 (2026-07-02, F1) CHOKEPOINT: NOWE POLE effective_pickup_at =
        # max(deklarowany czas odbioru, available_from) OBOK deklaracji. Deklaracja
        # restauracji (czas_kuriera/pickup_at) NIETYKALNA (Q2, frozen R27 ±5) — tu
        # tylko SURFACUJEMY realny najwcześniejszy odbiór respektujący start zmiany
        # kuriera (available_from=max(now,shift_start) z courier_resolver). Bez
        # konsumentów na razie (pas renderów = fala L3). Gated; OFF = pole nie powstaje.
        if decision_flag("ENABLE_AVAILABLE_FROM_SINGLE_SOURCE"):
            try:
                from dispatch_v2 import courier_resolver as _CR_af
                _now_af = now_utc()
                _af_dt, _af_src = _CR_af.resolve_available_from_by_cid(
                    event.get("courier_id"), _now_af)
                _decl_raw = ck_iso or prev.get("czas_kuriera_warsaw")
                _decl_dt = None
                if _decl_raw:
                    try:
                        _decl_dt = datetime.fromisoformat(str(_decl_raw).replace("Z", "+00:00"))
                        if _decl_dt.tzinfo is None:  # parytet PR._parse_dt: naive→UTC (real=aware +02:00)
                            _decl_dt = _decl_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        _decl_dt = None
                if _decl_dt is not None and _af_dt is not None:
                    _eff = max(_decl_dt, _af_dt)
                    _eff_src = "available_from" if _af_dt > _decl_dt else "declared"
                elif _af_dt is not None:
                    _eff, _eff_src = _af_dt, "available_from"
                else:
                    _eff, _eff_src = _decl_dt, "declared"
                if _eff is not None:
                    # NIE nadpisujemy czas_kuriera_warsaw/pickup_at — osobne pole.
                    merged["effective_pickup_at"] = _eff.astimezone(timezone.utc).isoformat()
                    merged["effective_pickup_source"] = _eff_src
                    merged["effective_pickup_af_source"] = _af_src
            except Exception as _eff_e:
                _log.debug(f"L4 effective_pickup_at skip oid={oid}: {_eff_e}")
        if ck_iso is not None or ck_hhmm is not None:
            if _verify_czas_kuriera_consistency(ck_iso, ck_hhmm, oid):
                ck_resolution = _assignment_ck_resolution(prev, event)
                if ck_resolution.outcome is ResolutionOutcome.SUPPRESS:
                    # Assignment remains a legal lifecycle transition.  The
                    # canonical authority resolver decides only whether its
                    # parallel CK projection is part of the exact effect.
                    _log.info(
                        "CK_ASSIGNMENT_WRITER_SUPPRESSED "
                        f"oid={oid} keep={prev.get('czas_kuriera_hhmm')!r} "
                        f"ignore={ck_hhmm!r} reason={ck_resolution.reason}"
                    )
                    return _persist_assignment_and_availability(merged)
                merged["czas_kuriera_warsaw"] = ck_iso
                merged["czas_kuriera_hhmm"] = ck_hhmm
                _result = _persist_assignment_and_availability(merged)
                return _result
            else:
                # Skip persist czas_kuriera; log ERROR done; raise after upsert.
                _result = _persist_assignment_and_availability(merged)
                raise CorruptedTimestampError(
                    f"COURIER_ASSIGNED {oid}: czas_kuriera sanity fail, "
                    f"persisted bez czas_kuriera update"
                )
        return _persist_assignment_and_availability(merged)

    if etype == "CZAS_KURIERA_UPDATED":
        # V3.19g1: panel_watcher detected czas_kuriera change (|Δt| ≥ 3min)
        # for already-assigned order. Update ck fields ONLY, preserve status,
        # courier_id, commitment_level, etc. Sanity check via V3.19f helper.
        new_ck_iso = payload.get("new_ck_iso")
        new_ck_hhmm = payload.get("new_ck_hhmm")
        if not _verify_czas_kuriera_consistency(new_ck_iso, new_ck_hhmm, oid):
            _log.error(
                f"CZAS_KURIERA_UPDATED {oid}: sanity fail "
                f"(iso={new_ck_iso!r} hhmm={new_ck_hhmm!r}), skipping persist"
            )
            return None
        existing = get_order(oid)
        if existing is None:
            _log.warning(f"CZAS_KURIERA_UPDATED for unknown oid={oid}, skipping")
            return None
        # Czasowka: wspolny resolver jest jedynym wlascicielem rozroznienia
        # legalnego committed czasu Rutcom od statusowego re-stampu. Kazdy
        # zaakceptowany CK zostaje przetlumaczony na PICKUP_TIME_UPDATED, nigdy
        # nie zapisuje tylko pola czytanego przez aplikacje.
        _src = payload.get("source")
        _authority = resolve_czasowka_ck_observation(
            existing,
            payload,
            policy_snapshot=authority_policy,
        )
        if _authority.outcome is ResolutionOutcome.APPLY:
            if durable_event_id:
                # Legalny committed CK jest kanonizowany przed outboxem przez
                # committed_pickup_apply. Historyczny raw durable row nie ma
                # exact attestation kanonicznej koperty; zamykamy go fail-closed
                # zamiast produkować drugi, niezweryfikowany transport state.
                _log.error(
                    "CK_COMMITTED_RAW_DURABLE_REJECTED "
                    f"oid={oid} event_id={durable_event_id}"
                )
                return None
            _log.info(
                f"CK_COMMITTED_AUTHORITY_APPLIED oid={oid} czasówka ck "
                f"{existing.get('czas_kuriera_hhmm')}→{new_ck_hhmm} src={_src} "
                f"authority={_authority.reason} → PICKUP_TIME_UPDATED"
            )
            return update_from_event(dict(_authority.event))
        if _czasowka_raw_ck_writer_is_retired(
            existing,
            _authority,
            policy_snapshot=authority_policy,
        ):
            # Po aktywacji authority czasówki nie może już zmienić drugi,
            # CK-only writer — także gdy stary CK jest pusty. Brak baseline nie
            # nadaje authority; każda nowa prawda idzie atomowym pickup+CK.
            if _src in RETIRED_CZASOWKA_CK_ONLY_SOURCES:
                _log.warning(
                    "CK_COMMITTED_RETIRED_WRITER_BLOCKED "
                    f"oid={oid} source={_src!r} "
                    f"reason={_authority.reason}"
                )
            else:
                # Nieznany przyszły raw CK także nie może zostać cichym drugim
                # writerem. Rejestracja wymaga rozszerzenia jednego policy
                # ownera i oracle, nie fallbacku w state handlerze.
                _log.warning(
                    "CK_COMMITTED_UNREGISTERED_WRITER_BLOCKED "
                    f"oid={oid} source={_src!r} "
                    f"reason={_authority.reason}"
                )
            return None
        if _authority.outcome is ResolutionOutcome.SUPPRESS:
            _log.info(
                f"CK_COMMITTED_SUPPRESSED oid={oid} czasówka ck "
                f"{existing.get('czas_kuriera_hhmm')}→{new_ck_hhmm} src={_src} "
                f"reason={_authority.reason} (skip persist)"
            )
            return None
        legacy_claim, claim_effect = _legacy_time_claim_gate(event)
        if claim_effect is not None:
            return None
        cas_status = time_event_cas_status(
            existing,
            event,
            allow_unversioned_ck_claim=(legacy_claim == "verified"),
        )
        if cas_status == "applied":
            return existing
        if cas_status == "superseded":
            _log.info(
                f"CZAS_KURIERA_UPDATED_STALE oid={oid} src={_src!r} (skip)"
            )
            return None
        # Elastyk (non-czasówka) forward-only (Adrian 2026-06-24, opcja B):
        # pasywny re-odczyt gastro NIE może COFNĄĆ committed czas_kuriera
        # („przyjazd wcześniej niż umówiono" = wobble ETA). Forward przechodzi
        # (koordynatorski +15 / realne spóźnienie). Deliberatne sources nie są
        # w _CK_PASSIVE_SOURCES → przechodzą w każdym kierunku.
        if (flag("ENABLE_ELASTYK_CK_NO_BACKWARD", True)
                and not _is_czasowka_order(existing)
                and _src in _CK_PASSIVE_SOURCES):
            _bwd = _ck_backward_delta(existing.get("czas_kuriera_warsaw"), new_ck_iso)
            if _bwd is not None:
                _log.info(
                    f"CK_ELASTYK_BACKWARD_BLOCKED oid={oid} ck "
                    f"{existing.get('czas_kuriera_hhmm')}→{new_ck_hhmm} Δ={_bwd:+.1f}min "
                    f"src={_src} — elastyk forward-only, nie cofamy (skip persist)"
                )
                return None
        prev_count = int(existing.get("v319g_ck_change_count") or 0)
        next_count = prev_count + 1
        if time_event_cas_is_versioned(etype, payload):
            expected_count = normalize_pickup_revision(
                payload.get(CK_CHANGE_REVISION_OBSERVATION_FIELD)
            )
            if expected_count is None or expected_count != prev_count:
                return None
            next_count = expected_count + 1
        update_fields = {
            "czas_kuriera_warsaw": new_ck_iso,
            "czas_kuriera_hhmm": new_ck_hhmm,
            "v319g_ck_change_count": next_count,
        }
        _delta = payload.get("delta_min")
        _delta_str = f"Δ={_delta:+.1f}min" if _delta is not None else "Δ=null(first_ack)"
        _log.info(
            f"V3.19g1 oid={oid} ck {payload.get('old_ck_hhmm')} → {new_ck_hhmm} "
            f"{_delta_str} src={payload.get('source')}"
        )
        return upsert_order(
            oid, _marked(update_fields), event="CZAS_KURIERA_UPDATED"
        )

    if etype == "PICKUP_TIME_UPDATED":
        # Root cause oid 474577 (2026-05-19): pickup_at_warsaw zapisywany RAZ
        # w NEW_ORDER, nigdy nie odświeżany dla czasówek status=planned →
        # koordynator zmienił czas odbioru na życzenie restauracji, Ziomek
        # czytał stary (czasowka_scheduler._minutes_to_pickup → FORCE_ASSIGN
        # spam). panel_watcher._diff_pickup_time wykrył zmianę pickup_at_warsaw
        # (|Δt| ≥ próg). Odśwież pola czasu odbioru, preserve status/courier/
        # czas_kuriera/commitment (orthogonal — czas_kuriera ma własny handler).
        new_pickup = payload.get("new_pickup_at_warsaw")
        if not new_pickup:
            _log.error(
                f"PICKUP_TIME_UPDATED {oid}: brak new_pickup_at_warsaw, skip"
            )
            return None
        # Sanity: musi parsować się jako ISO datetime (Lekcja #81 fail-loud).
        try:
            datetime.fromisoformat(new_pickup)
        except (ValueError, TypeError) as e:
            _log.error(
                f"PICKUP_TIME_UPDATED {oid}: pickup_at_warsaw parse fail "
                f"({new_pickup!r}): {e}, skip"
            )
            return None
        existing = get_order(oid)
        if existing is None:
            _log.warning(f"PICKUP_TIME_UPDATED for unknown oid={oid}, skipping")
            return None
        pending_initial_intent = existing.get(NEW_ORDER_TIME_INTENT_FIELD)
        if pending_initial_intent is not None and (
            not new_order_time_intent_is_valid(
                pending_initial_intent,
                order_id=oid,
            )
            or payload.get(NEW_ORDER_TIME_INTENT_ID_FIELD)
            != pending_initial_intent.get("intent_id")
        ):
            _log.warning(
                "PICKUP_TIME_UPDATED blocked by pending NEW_ORDER intent "
                f"oid={oid}"
            )
            return None
        committed_authority = payload.get("committed_authority")
        # ``pending`` is normally permission to apply, except when the exact
        # coordinator claim cannot currently be read.  Run the same canonical
        # gate immediately before the state writer; status/oracle alone cannot
        # encode both retryability and write permission in three public states.
        _legacy_claim, claim_effect = _legacy_time_claim_gate(event)
        if claim_effect is not None:
            return None
        pickup_effect = _pickup_time_event_status(event, existing)
        if pickup_effect == "applied":
            return existing
        if pickup_effect != "pending":
            _log.info(
                f"PICKUP_TIME_UPDATED_STALE oid={oid} authority="
                f"{committed_authority or 'legacy'} status="
                f"{existing.get('status')} (skip)"
            )
            return None
        prev_count = int(existing.get("pickup_time_change_count") or 0)
        update_fields = {
            "pickup_at_warsaw": new_pickup,
            "pickup_time_change_count": prev_count + 1,
        }
        current_revision = normalize_pickup_revision(
            existing.get("pickup_time_revision", 0)
        )
        if current_revision is None:
            return None
        if (
            committed_authority is not None
            or time_event_cas_is_versioned(etype, payload)
            or "pickup_time_revision_at_observation" in payload
        ):
            expected_revision = normalize_pickup_revision(
                payload.get("pickup_time_revision_at_observation")
            )
            if (
                expected_revision is None
                or expected_revision != current_revision
            ):
                return None
            update_fields["pickup_time_revision"] = expected_revision + 1
        else:
            # Revision należy do kanonicznego pickup, nie do jednej flagi.
            # Każdy legalny legacy write przesuwa fence, także zanim pierwszy
            # authority event zdążył wejść do state. Inaczej pending A→B może
            # wrócić po legacy cyklu A→C→A z niezmienioną rewizją 0.
            update_fields["pickup_time_revision"] = current_revision + 1
        # Mirror committed pickup → czas_kuriera (Adrian 2026-06-24): dla czasówki
        # umówiony czas rządzi pickup_at, ale apka/kurier pokazują czas_kuriera —
        # więc musi nadążać za LEGALNĄ zmianą odbioru (koordynator/restauracja,
        # dowolny kierunek). To jest kanał,
        # którym committed czasówki ma się zmieniać (zamiast pasywnego czas_kuriera).
        prospective_order = project_time_event_order(existing, payload)
        if (
            _is_czasowka_order(prospective_order)
            and (
                committed_authority is not None
                or flag("ENABLE_PICKUP_TIME_MIRRORS_CK", True)
            )
        ):
            current_ck_revision = normalize_pickup_revision(
                existing.get(CK_CHANGE_REVISION_STATE_FIELD, 0)
            )
            if current_ck_revision is None:
                return None
            if committed_authority is not None:
                expected_ck_revision = normalize_pickup_revision(
                    payload.get(CK_CHANGE_REVISION_OBSERVATION_FIELD)
                )
                if (
                    expected_ck_revision is None
                    or expected_ck_revision != current_ck_revision
                ):
                    return None
            update_fields[CK_CHANGE_REVISION_STATE_FIELD] = (
                current_ck_revision + 1
            )
            try:
                _np_dt = datetime.fromisoformat(new_pickup)
                update_fields["czas_kuriera_warsaw"] = new_pickup
                update_fields["czas_kuriera_hhmm"] = _np_dt.strftime("%H:%M")
                # Consumption of the initial receipt is part of this exact
                # atomic pickup+CK write.  No crash can leave a complete tuple
                # with a replayable stale NEW_ORDER intent.
                if pending_initial_intent is not None:
                    update_fields[NEW_ORDER_TIME_INTENT_FIELD] = None
            except (ValueError, TypeError):
                pass  # new_pickup już zwalidowany wyżej; defensywnie
        # Wszystkie pola sprzężone są własnością jednego kontraktu policy/CAS.
        # None oznacza „zachowaj snapshot”, więc writer nie tworzy drugiej listy.
        for state_field, _old_key, new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
            new_value = payload.get(new_key)
            if new_value is not None:
                update_fields[state_field] = new_value
        if committed_authority is not None:
            proof = payload.get("committed_authority_proof")
            update_fields.update({
                "committed_pickup_authority": committed_authority,
                "committed_pickup_observed_source": payload.get(
                    "observed_source"
                ),
                "committed_pickup_observed_at": payload.get("observed_at"),
                "committed_pickup_authority_receipt_id": payload.get(
                    "committed_authority_receipt_id"
                ),
                "committed_pickup_panel_baseline_at_observation": payload.get(
                    "committed_pickup_panel_baseline_at_observation"
                ),
                "committed_ck_panel_baseline_at_observation": payload.get(
                    "committed_ck_panel_baseline_at_observation"
                ),
                "committed_pickup_authority_proof_schema": (
                    proof.get("schema")
                    if isinstance(proof, dict)
                    else None
                ),
                "committed_pickup_event_key": payload.get(
                    "committed_pickup_event_key"
                ),
            })
        elif (
            _is_czasowka_order(existing)
            and state_has_committed_pickup_artifact(existing)
        ):
            # Legacy durable event moze jeszcze dojechac po deployu. Jesli
            # legalnie zmienia pickup, nie wolno zostawic provenance poprzedniej
            # prawdy; wszystkie nowe producery czasowki niosa proof.
            update_fields.update({
                "committed_pickup_authority": None,
                "committed_pickup_observed_source": None,
                "committed_pickup_observed_at": None,
                "committed_pickup_authority_receipt_id": None,
                "committed_pickup_panel_baseline_at_observation": None,
                "committed_ck_panel_baseline_at_observation": None,
                "committed_pickup_authority_proof_schema": None,
                "committed_pickup_event_key": None,
            })
        _p_delta = payload.get("delta_min")
        _p_delta_str = (
            f"Δ={_p_delta:+.1f}min" if _p_delta is not None else "Δ=null(late)"
        )
        _log.info(
            f"PICKUP_TIME_UPDATED oid={oid} pickup "
            f"{payload.get('old_pickup_at_warsaw')} → {new_pickup} "
            f"{_p_delta_str} src={payload.get('source')}"
        )
        return upsert_order(
            oid, _marked(update_fields), event="PICKUP_TIME_UPDATED"
        )

    if etype == "ORDER_RECLAIMED_TO_CZASOWKA":
        # Uspiony kontrakt etapu LIVE. W tym sprincie nie istnieje caller ani
        # downstream wywolujacy ten event; flaga default OFF daje twarde no-op.
        if not _czasowka_reclaim_live_authorized(event):
            return None
        existing = get_order(oid)
        if not isinstance(existing, dict):
            return None
        previous_cid = str(payload.get("previous_courier_id") or "")
        generation = str(payload.get("reclaim_generation") or "")
        expected_assignment = str(
            payload.get("expected_assignment_event_id") or generation
        )
        expected_pickup = payload.get("expected_pickup_at_warsaw")
        if (
            existing.get("status") != "assigned"
            or existing.get("picked_up_at") is not None
            or bool(str(existing.get("czas_kuriera_warsaw") or "").strip())
            or not previous_cid
            or previous_cid == "26"
            or str(existing.get("courier_id") or "") != previous_cid
            or not expected_assignment
            or str(existing.get("assignment_event_id") or "")
            != expected_assignment
            or (expected_pickup and existing.get("pickup_at_warsaw") != expected_pickup)
        ):
            return None
        reclaimed_at = payload.get("reclaimed_at")
        reason = payload.get("reason")
        if not generation or not reclaimed_at or not reason:
            return None
        return upsert_order(
            oid,
            _marked({
                "status": "planned",
                "commitment_level": "planned",
                "courier_id": "26",
                "previous_courier_id": previous_cid,
                "reclaim_generation": generation,
                "reclaimed_at": reclaimed_at,
                "reason": reason,
                "reclaim_reason": reason,
                "bag_time_alerted": False,
            }),
            event="ORDER_RECLAIMED_TO_CZASOWKA",
        )

    if etype == "COURIER_PICKED_UP":
        # F2.1b step 5: CELOWO NIE resetujemy bag_time_alerted tutaj.
        # Panel_watcher może reemit COURIER_PICKED_UP przez reconcile retry po
        # tym jak sla_tracker już ustawił flag=True. Reset w tym handlerze
        # spowodowałby duplicate alerty (flag→False, następny tick→kolejny alert).
        # Reset jest w ASSIGNED/DELIVERED/REJECTED/RETURNED — bezpieczne punkty.
        picked = payload.get("timestamp") or now_iso()
        # expected_delivery_by = picked + 35 min (SLA)
        try:
            # panel timestamps sa naive Warsaw, dorzuc UTC jako fallback
            if "T" in picked or "Z" in picked:
                picked_dt = datetime.fromisoformat(picked.replace("Z", "+00:00"))
            else:
                # "2026-04-11 18:01:47" = naive Warsaw
                from zoneinfo import ZoneInfo
                picked_dt = datetime.strptime(picked, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Warsaw"))
        except Exception:
            picked_dt = datetime.now(timezone.utc)
        expected = (picked_dt + timedelta(minutes=35)).isoformat()
        pickup_coords = payload.get("pickup_coords")
        update_fields = {
            "status": "picked_up",
            "commitment_level": "picked_up",
            "picked_up_at": picked,
            "expected_delivery_by": expected,
            "assigned_check_ts": now_iso(),
        }
        if pickup_coords:
            update_fields["pickup_coords"] = pickup_coords
        return upsert_order(oid, _marked(update_fields), event="COURIER_PICKED_UP")

    if etype == "COURIER_DELIVERED":
        deliv_addr = payload.get("delivery_address") or payload.get("final_location")
        deliv_city = payload.get("delivery_city")
        deliv_coords = None
        if deliv_addr:
            try:
                from dispatch_v2.geocoding import geocode
                r = geocode(deliv_addr, city=deliv_city)
                if r:
                    deliv_coords = [round(float(r[0]), 6), round(float(r[1]), 6)]
            except Exception as _e:
                pass  # geocode fail nie blokuje zapisu delivered
        # FIX 2026-06-13 (sink guard, B3/B5): dwa defekty u ujścia.
        # (1) `payload.get("timestamp", now_iso())` zwraca None gdy klucz ISTNIEJE
        #     z wartością None — a reconcile/panel_diff/packs_ghost podają
        #     {"timestamp": raw.get("czas_doreczenia")} BEZ fallbacku → delivered_at
        #     = null → build_delivered wyklucza → znika z "Doręczone" + utarg 0.
        #     `or now_iso()` łapie też None-value (default działa tylko dla braku klucza).
        # (2) Gdy geocode zawiódł (brak delivery_city → no_city) deliv_coords=None
        #     NADPISYWAŁO poprawne coords z NEW_ORDER → piny mapy znikały całej flocie.
        #     upsert_order MERGE'uje ({**existing, **data}), więc pominięcie klucza
        #     delivery_coords zachowuje istniejące — nie nadpisujemy dobrych None'em.
        delivered_update = {
            "status": "delivered",
            "commitment_level": "planned",  # reset, kurier wolny
            "delivered_at": payload.get("timestamp") or now_iso(),
            "final_location": payload.get("final_location"),
            "delivery_address": deliv_addr,
            "bag_time_alerted": False,  # F2.1b step 5: housekeeping reset at end-of-life
        }
        if deliv_coords:
            delivered_update["delivery_coords"] = deliv_coords
        return upsert_order(
            oid, _marked(delivered_update), event="COURIER_DELIVERED"
        )

    if etype == "ORDER_RESURRECTED":
        existing = get_order(oid) or {}
        if existing.get("status") != "delivered":
            return None
        new_status = str(payload.get("new_status") or "picked_up")
        if new_status not in ("assigned", "picked_up"):
            new_status = "picked_up"
        correction = {
            "status": new_status,
            "commitment_level": new_status,
            "delivered_at": None,
            "final_location": None,
            "bag_time_alerted": False,
            # New correction epoch revokes a crash-pending DELIVERED marker.
            "last_lifecycle_event_id_courier_delivered": None,
        }
        if event.get("courier_id"):
            correction["courier_id"] = str(event.get("courier_id"))
        _log.warning(
            f"RESURRECT {oid} delivered→{new_status} "
            f"reason={payload.get('reason')} cid={event.get('courier_id')}"
        )
        return upsert_order(
            oid, _marked(correction), event="ORDER_RESURRECTED"
        )

    if etype == "ORDER_RETURNED_TO_POOL":
        return upsert_order(oid, _marked({
            "status": "returned_to_pool",
            "commitment_level": "planned",
            "courier_id": None,
            "return_reason": payload.get("reason"),
            "bag_time_alerted": False,  # F2.1b step 5: reset — next courier starts clean
        }), event="ORDER_RETURNED_TO_POOL")

    if etype == "COURIER_REJECTED_PROPOSAL":
        # Wraca do planned, bez kuriera
        return upsert_order(oid, _marked({
            "status": "planned",
            "commitment_level": "planned",
            "courier_id": None,
            "last_rejected_by": event.get("courier_id"),
            "rejection_reason": payload.get("reason"),
            "bag_time_alerted": False,  # F2.1b step 5: reset on rejection — next courier starts clean
        }), event="COURIER_REJECTED_PROPOSAL")

    # Pozostale eventy nie zmieniaja stanu zlecen
    return None


@_lifecycle_state_mutation
def touch_check_cursor(order_id: str) -> bool:
    """Cicha aktualizacja cursora round-robin dla round-robin watchera.
    Ustawia assigned_check_ts=now_iso dla ordera. Nie loguje historii.
    Uzywane przez panel_watcher picked_up reconcile do rotacji candidate'ow.
    Zwraca True jesli order istnial, False inaczej."""
    with _locked_write():
        state = _read_state_strict()        # Faza 1: raise StateReadError zamiast cichego {}
        old_count = len(state)
        if order_id not in state:
            return False
        state[order_id]["assigned_check_ts"] = now_iso()
        _guarded_write(Path(_state_path()), state, old_count, op="touch")
        return True


@_lifecycle_state_mutation
def delete_order(order_id: str) -> bool:
    """Fizyczne usuniecie (tylko do testow lub purge).

    TASK 2 Część A (2026-05-04) Z3 safety guard: order MUSI mieć status terminal
    (delivered/cancelled/returned_to_pool) PRZED delete. Inaczej events.db nie ma
    closure event → phantom. Caller emituje terminal event najpierw, potem delete.
    """
    TERMINAL_STATUSES = ("delivered", "cancelled", "returned_to_pool")
    with _locked_write() as path:
        state = _read_state_strict()        # Faza 1: raise StateReadError zamiast cichego {}
        old_count = len(state)
        if order_id in state:
            current_status = state[order_id].get("status")
            if current_status not in TERMINAL_STATUSES:
                raise RuntimeError(
                    f"delete_order({order_id}) refused: status={current_status!r} not terminal "
                    f"(must be in {TERMINAL_STATUSES}). Emit terminal event first to avoid events.db phantom."
                )
            del state[order_id]
            _guarded_write(path, state, old_count, op="delete")
            _log.info(f"delete {order_id} (status={current_status})")
            return True
        return False


# ──────────────────────────────────────────────────────────────────────────
# STATE-RMW-02 (audyt 2026-06-03): bulk-prune terminalnych zleceń.
#
# Problem: orders_state.json rośnie monotonicznie (~+0.5 MB/dzień; 3693 zleceń =
# 8.4 MB, z czego 99.3% terminalnych), a KAŻDY RMW writer czyta+zapisuje+fsync
# CAŁY plik pod LOCK_EX → koszt każdego upsertu = O(cały stan) + rosnąca
# rywalizacja o lock z czytelnikami (watcher/sla_tracker/reconcile).
#
# Fix: nocny prune usuwa zlecenia TERMINALNE (delivered/cancelled/
# returned_to_pool) starsze niż retention_hours wg `updated_at` (tz-aware ISO,
# 100% pokrycia — NIE `delivered_at`, który jest naiwnym czasem Warsaw z lukami).
#
# Bezpieczeństwo: bulk-write OMIJA `_guarded_write` (dopuszcza max 1 delete na
# wywołanie → naiwna pętla = ~3500 pełnych zapisów 8 MB pod LOCK_EX = godziny
# I/O). Zamiast tego: jeden `_read_state_strict` + canonical atomic writer pod
# samym współdzielonym `_locked_write` (serializacja z upsert/set_status/touch/
# delete) + TWARDY sanity-guard PRZED zapisem (zastępuje _guarded_write):
#   1. żaden kandydat nie może być nie-terminalny,
#   2. żaden aktywny order nie znika (regresja liczby aktywnych = abort),
#   3. spójność liczby + zakaz całkowitego wyzerowania,
#   → inaczej raise StateReadError + throttled admin alert.
# Odzysk pełnego payloadu pruned-zlecenia: snapshot (.prev / /snapshots, ~7 dni)
# lub events.db. Audit_log (90 dni) zachowuje closure eventy (forensyka).
TERMINAL_STATUSES_PRUNE = ("delivered", "cancelled", "returned_to_pool")


def _parse_updated_at_utc(value) -> Optional[datetime]:
    """Parsuje `updated_at` (ISO) → tz-aware UTC datetime, albo None gdy się nie da.
    Naiwny (bez tz) traktowany jako UTC (now_iso() zawsze pisze tz-aware)."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@_lifecycle_state_mutation
def prune_terminal_orders(retention_hours: float = 12.0, dry_run: bool = True) -> dict:
    """Usuwa terminalne zlecenia starsze niż retention_hours (anchor: `updated_at`).

    Zwraca raport (zawsze, też w dry-run):
      {old_count, active_count, pruned_count, new_count, retention_hours,
       dry_run, skipped_no_updated_at, sample}

    dry_run=True → tylko liczy i loguje, NIC nie zapisuje (zero ryzyka).
    Bezpieczeństwo: patrz docstring sekcji. Aktywne (planned/assigned/picked_up)
    NIGDY nie są usuwane — pełna ochrona przez status-filter + sanity-guard.
    """
    cutoff = now_utc() - timedelta(hours=retention_hours)
    with _locked_write() as path:
        state = _read_state_strict()        # Faza 1: raise zamiast cichego {}
        old_count = len(state)
        active_count = sum(
            1 for r in state.values()
            if r.get("status") not in TERMINAL_STATUSES_PRUNE
        )
        skipped_no_updated_at = 0
        to_prune = []
        for oid, rec in state.items():
            if rec.get("status") not in TERMINAL_STATUSES_PRUNE:
                continue
            dt = _parse_updated_at_utc(rec.get("updated_at"))
            if dt is None:
                skipped_no_updated_at += 1   # brak wiarygodnego anchora → NIE ruszaj
                continue
            if dt < cutoff:
                to_prune.append(oid)

        report = {
            "old_count": old_count,
            "active_count": active_count,
            "pruned_count": len(to_prune),
            "new_count": old_count - len(to_prune),
            "retention_hours": retention_hours,
            "dry_run": dry_run,
            "skipped_no_updated_at": skipped_no_updated_at,
            "sample": to_prune[:10],
        }

        if not to_prune:
            _log.info(
                f"prune_terminal_orders: nic do usunięcia "
                f"(old={old_count}, active={active_count}, retention={retention_hours}h)"
            )
            return report

        prune_set = set(to_prune)
        new_state = {k: v for k, v in state.items() if k not in prune_set}
        new_count = len(new_state)
        active_after = sum(
            1 for r in new_state.values()
            if r.get("status") not in TERMINAL_STATUSES_PRUNE
        )

        # ── TWARDY sanity-guard (zastępuje _guarded_write dla bulk-delete) ──
        non_terminal_in_prune = [
            oid for oid in to_prune
            if state[oid].get("status") not in TERMINAL_STATUSES_PRUNE
        ]
        if (
            non_terminal_in_prune                       # 1. tknięto nie-terminalny
            or active_after != active_count             # 2. zniknął aktywny order
            or new_count != old_count - len(to_prune)   # 3. niespójność liczby
            or (old_count > 0 and new_count == 0)        # 4. całkowite wyzerowanie
        ):
            detail = (
                f"prune_terminal_orders SANITY ABORT: old={old_count} new={new_count} "
                f"prune={len(to_prune)} active={active_count}→{active_after} "
                f"non_terminal_in_prune={len(non_terminal_in_prune)} — zapis ZABLOKOWANY "
                f"(ochrona przed utratą aktywnych/clobberem)"
            )
            _alert_state_read_failure(detail)
            raise StateReadError(detail)

        if dry_run:
            _log.info(
                f"prune_terminal_orders DRY-RUN: usunąłbym {len(to_prune)} terminalnych "
                f">{retention_hours}h ({old_count}→{new_count}); active={active_count} "
                f"nietknięte; skipped_no_ts={skipped_no_updated_at}"
            )
            return report

        _state_store.atomic_write_json(
            path,
            new_state,
            indent=2,
            ensure_directory_durable=ensure_state_directory_durable,
            logger=_log,
        )
        _log.info(
            f"prune_terminal_orders: usunięto {len(to_prune)} terminalnych "
            f"({old_count}→{new_count}); active={active_count} nietknięte; "
            f"skipped_no_ts={skipped_no_updated_at}"
        )
        return report


def compute_oldest_picked_up_age_min(bag, now_utc):
    """Wiek (minuty) najstarszego ordera w statusie 'picked_up' w bagu kuriera.

    Implementacja D4 V3.1: SLA kuriera liczy sie od picked_up_at (nie od assigned_at).
    Ordery w statusie 'assigned' nie karcony time_penalty w scoringu - kurier ich
    jeszcze nie ma fizycznie, restauracja jeszcze prepuje.

    Parsowanie timestampow: akceptowane formaty:
      1. datetime z tzinfo
      2. ISO string "YYYY-MM-DDTHH:MM:SS+HH:MM" lub z "Z"
      3. naive Warsaw "YYYY-MM-DD HH:MM:SS" (format panelu gastro.nadajesz.pl)

    Args:
        bag: lista dict orderow (np. z get_by_courier). Kazdy order ma min. "status".
             Dla statusu "picked_up" wymagany jest "picked_up_at".
        now_utc: datetime z tzinfo UTC. Caller MUSI podac - zero ukrytych defaults
                 dla deterministycznosci (replay historical data, A/B testy).

    Returns:
        float minut lub None gdy bag nie ma zadnego ordera w statusie "picked_up"
        z poprawnym picked_up_at timestampem.

    Raises:
        ValueError: gdy now_utc jest naive (bez tzinfo).

    Example:
        >>> from datetime import datetime, timezone, timedelta
        >>> now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        >>> bag = [
        ...     {"status": "picked_up", "picked_up_at": "2026-04-12T11:45:00+00:00"},
        ...     {"status": "assigned"},
        ... ]
        >>> compute_oldest_picked_up_age_min(bag, now)
        15.0
    """
    if now_utc is None:
        raise ValueError("now_utc required - caller must pass explicit timestamp")
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (got naive datetime)")

    if not bag:
        return None

    now_utc_norm = now_utc.astimezone(timezone.utc)
    oldest_age_min = None

    for order in bag:
        if not isinstance(order, dict):
            continue
        if order.get("status") != "picked_up":
            continue
        picked_ts = order.get("picked_up_at")
        if not picked_ts:
            continue

        picked_dt = _parse_picked_up_at(picked_ts)
        if picked_dt is None:
            continue

        age_min = (now_utc_norm - picked_dt).total_seconds() / 60.0
        if oldest_age_min is None or age_min > oldest_age_min:
            oldest_age_min = age_min

    return oldest_age_min


def _parse_picked_up_at(value):
    """Wrapper na common.parse_panel_timestamp dla kompatybilnosci wewnetrznej."""
    from dispatch_v2.common import parse_panel_timestamp
    return parse_panel_timestamp(value)


def stats() -> dict:
    """Statystyki state machine."""
    state = _read_state()
    by_status = {}
    by_courier = {}
    for o in state.values():
        s = o.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        c = o.get("courier_id")
        if c and s in ("assigned", "picked_up"):
            by_courier[c] = by_courier.get(c, 0) + 1
    return {
        "total": len(state),
        "by_status": by_status,
        "active_per_courier": by_courier,
    }
