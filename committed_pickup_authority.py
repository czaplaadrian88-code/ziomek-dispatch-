"""Kanoniczny autorytet umowionego czasu odbioru czasowki.

Rutcom wystawia dwa rownolegle pola czasu: ``pickup_at_warsaw`` oraz
``czas_kuriera``. Drugie pole moze byc zarowno umowionym czasem z restauracja,
jak i technicznym re-stampem statusu. Ten modul jest jedynym ownerem polityki,
ktora rozstrzyga obserwacje obu pol i buduje dowod autorytetu dla jedynego
kanonicznego writera ``PICKUP_TIME_UPDATED``.

Modul jest czysty: nie czyta flag, plikow ani zegara. Producent przekazuje
snapshot, efektywne flagi i czas obserwacji. State machine ponownie waliduje
utrwalony proof, wiec sama etykieta ``committed_authority`` niczego nie
autoryzuje.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Mapping, Optional


PASSIVE_CK_SOURCES = frozenset(
    {"first_acceptance", "panel_re_check", "pre_proposal_recheck"}
)
RECEIPT_REQUIRED_PICKUP_SOURCES = frozenset({"coordinator_force"})
# CK-only source nie może współistnieć z atomowym ownerem pickup+CK. Dwa stare
# kanały były wyłącznie kontraktem/komentarzem (brak producenta na HEAD), a
# first_acceptance pozostaje tylko dla elastyka oraz exact OFF-parity. Dla
# czasówki po flipie forward-authority wszystkie trzy są jawnie wygaszone.
RETIRED_CZASOWKA_CK_ONLY_SOURCES = frozenset(
    {"coordinator_edit", "first_acceptance", "ziomek_late_extension"}
)
MANUAL_CK_AUTHORITY_FLAG = "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"
RUTCOM_FORWARD_AUTHORITY_FLAG = "ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY"
COMMITTED_PICKUP_AUTHORITY_FLAGS = (
    MANUAL_CK_AUTHORITY_FLAG,
    RUTCOM_FORWARD_AUTHORITY_FLAG,
)
COMMITTED_PICKUP_EVENT_ID_MARKER = "_PICKUP_TIME_UPDATED_COMMITTED_"
COMMITTED_TIME_POLICY_SNAPSHOT_FIELD = "committed_time_policy_snapshot"
AUTHORITY_PROOF_SCHEMA = "committed_pickup_authority.v1"
TIME_EVENT_CAS_SCHEMA = "time_update_cas.v1"
TIME_EVENT_CAS_SCHEMA_FIELD = "time_event_cas_schema"
CK_CHANGE_REVISION_STATE_FIELD = "v319g_ck_change_count"
CK_CHANGE_REVISION_OBSERVATION_FIELD = (
    "ck_change_revision_at_observation"
)
COMMITTED_CK_DELTA_THRESHOLD_MIN = 3.0
CK_DERIVED_AUTHORITIES = frozenset(
    {
        "rutcom_forward_commitment",
        "rutcom_manual_marker",
        "coordinator_receipt",
    }
)
ALL_COMMITTED_AUTHORITIES = CK_DERIVED_AUTHORITIES | {"rutcom_pickup_field"}

_ACTIVE_ORDER_STATES = frozenset({"planned", "assigned"})
_ACTIVE_RUTCOM_STATUS_IDS = frozenset({2, 3, 4, 6})
_AUTOMATIC_FORWARD_RUTCOM_STATUS_IDS = frozenset({2})
_MANUAL_RUTCOM_STATUS_IDS_BY_ORDER_STATE = {
    "planned": frozenset({2}),
    "assigned": frozenset({3, 4, 6}),
}
_COORDINATOR_RECEIPT_SCHEMAS = frozenset(
    {
        "coordinator_time_recheck.v6",
    }
)
_COORDINATOR_RECEIPT_SOURCES = frozenset(
    {"coordinator_panel", "coordinator_console"}
)
_COORDINATOR_RECEIPT_FUTURE_SKEW = timedelta(seconds=30)
_PICKUP_OBSERVATION_SOURCES = frozenset(
    {
        "panel_pickup_recheck",
        "coordinator_force",
        "new_order_initial_intent",
    }
)
_SEMANTIC_EVENT_KEYS = frozenset(
    {"event_type", "order_id", "courier_id", "payload"}
)
_DURABLE_EVENT_KEYS = _SEMANTIC_EVENT_KEYS | frozenset(
    {
        "event_id",
        "committed_authority_attestation",
        "saved_plans_authorized",
        "committed_invalidates_view_authorized",
        "czasowka_reclaim_shadow_authorized",
        "czasowka_reclaim_live_authorized",
        COMMITTED_TIME_POLICY_SNAPSHOT_FIELD,
    }
)
_AUTHORITY_PAYLOAD_EXACT_KEYS = frozenset(
    {
        "observed_source",
        "observed_at",
        "manual_ck_edit_passthrough",
    }
)
COMMITTED_PICKUP_STATE_FIELDS = frozenset(
    {
        "committed_pickup_authority",
        "committed_pickup_observed_source",
        "committed_pickup_observed_at",
        "committed_pickup_authority_receipt_id",
        "committed_pickup_panel_baseline_at_observation",
        "committed_ck_panel_baseline_at_observation",
        "committed_pickup_authority_proof_schema",
        "committed_pickup_event_key",
    }
)
# Jedna mapa pól aktualizowanych w tej samej transakcji co pickup+CK. Policy,
# proof/CAS, postcondition i state writer używają dokładnie tego kontraktu;
# dopisanie nowego pola tylko w jednej warstwie ma wtedy mechanicznie czerwienić.
COMMITTED_PICKUP_COUPLED_FIELDS = (
    # Legacy records inferred czasowka identity from prep>=60.  Authority may
    # atomically adopt a lower live prep, so it must materialize the explicit
    # identity in the same proof/CAS/write/postcondition transaction.
    ("order_type", "old_order_type", "new_order_type"),
    ("prep_minutes", "old_prep_minutes", "new_prep_minutes"),
    (
        "decision_deadline",
        "old_decision_deadline",
        "new_decision_deadline",
    ),
    (
        "zmiana_czasu_odbioru",
        "old_zmiana_czasu_odbioru",
        "new_zmiana_czasu_odbioru",
    ),
)


@dataclass(frozen=True)
class CommittedPickupPolicySnapshot:
    """Immutable in-process authority policy captured before mutable I/O."""

    manual_passthrough_enabled: bool
    rutcom_forward_authority_enabled: bool
    passive_guard_enabled: bool
    producer: str = "pre_proposal_recheck"

    def __post_init__(self) -> None:
        for field_name in (
            "manual_passthrough_enabled",
            "rutcom_forward_authority_enabled",
            "passive_guard_enabled",
        ):
            if type(getattr(self, field_name)) is not bool:
                raise TypeError(f"{field_name} must be exact bool")
        if self.producer not in _COMMITTED_TIME_POLICY_SOURCES:
            raise ValueError(
                f"unsupported committed time policy producer: {self.producer!r}"
            )

    @property
    def authority_enabled(self) -> bool:
        return bool(
            self.manual_passthrough_enabled
            or self.rutcom_forward_authority_enabled
        )

    @property
    def initial_time_authority_enabled(self) -> bool:
        """Whether NEW_ORDER may hand its raw tuple to the canonical owner."""
        return bool(
            self.rutcom_forward_authority_enabled
            and self.passive_guard_enabled
        )

    @property
    def coordinator_time_authority_enabled(self) -> bool:
        """Whether one queue click may authorize committed pickup time.

        The manual-marker flag belongs to panel/Rutcom observations and must
        never turn a coordinator receipt into authority.  A queue claim is a
        durable journal only; its exact click-time forward+guard policy is the
        business lease.
        """
        return bool(
            self.rutcom_forward_authority_enabled
            and self.passive_guard_enabled
        )


_COMMITTED_TIME_POLICY_SOURCES = {
    "pre_proposal_recheck": frozenset({"pre_proposal_recheck"}),
    "panel_watcher": frozenset(
        {
            "first_acceptance",
            "new_order_initial_intent",
            "panel_pickup_recheck",
            "panel_re_check",
        }
    ),
    "coordinator_queue": frozenset({"coordinator_force"}),
}
COMMITTED_TIME_POLICY_SNAPSHOT_SCHEMA = "committed_pickup.policy_snapshot.v1"
_COMMITTED_TIME_POLICY_SNAPSHOT_KEYS = frozenset(
    {
        "schema",
        "producer",
        "manual_passthrough_enabled",
        "rutcom_forward_authority_enabled",
        "passive_guard_enabled",
    }
)


def validate_committed_time_policy_source(
    policy: CommittedPickupPolicySnapshot,
    source: object,
) -> None:
    """Bind a captured policy lease to one registered producer source."""
    if type(policy) is not CommittedPickupPolicySnapshot:
        raise TypeError("policy must be CommittedPickupPolicySnapshot")
    if source not in _COMMITTED_TIME_POLICY_SOURCES[policy.producer]:
        raise ValueError(
            "committed time policy producer/source mismatch: "
            f"{policy.producer!r}/{source!r}"
        )


def serialize_committed_time_policy(
    policy: CommittedPickupPolicySnapshot,
) -> dict:
    """Create the exact durable representation used by raw time events."""
    if type(policy) is not CommittedPickupPolicySnapshot:
        raise TypeError("policy must be CommittedPickupPolicySnapshot")
    return {
        "schema": COMMITTED_TIME_POLICY_SNAPSHOT_SCHEMA,
        "producer": policy.producer,
        "manual_passthrough_enabled": policy.manual_passthrough_enabled,
        "rutcom_forward_authority_enabled": (
            policy.rutcom_forward_authority_enabled
        ),
        "passive_guard_enabled": policy.passive_guard_enabled,
    }


def deserialize_committed_time_policy(
    value: object,
) -> CommittedPickupPolicySnapshot:
    """Validate an exact durable policy; partial markers never downgrade."""
    if not isinstance(value, Mapping):
        raise ValueError("committed time policy snapshot must be an object")
    if frozenset(value) != _COMMITTED_TIME_POLICY_SNAPSHOT_KEYS:
        raise ValueError("committed time policy snapshot has invalid shape")
    if value.get("schema") != COMMITTED_TIME_POLICY_SNAPSHOT_SCHEMA:
        raise ValueError("committed time policy snapshot has invalid schema")
    return CommittedPickupPolicySnapshot(
        producer=value.get("producer"),
        manual_passthrough_enabled=value.get(
            "manual_passthrough_enabled"
        ),
        rutcom_forward_authority_enabled=value.get(
            "rutcom_forward_authority_enabled"
        ),
        passive_guard_enabled=value.get("passive_guard_enabled"),
    )


def deserialize_coordinator_receipt_policy(
    receipt: object,
) -> CommittedPickupPolicySnapshot:
    """Return the exact click-time policy carried by a v6 receipt.

    Pre-v6 envelopes intentionally have no policy lease.  They remain
    readable for dark-deploy legacy elastic work, but can never acquire new
    committed-time authority after the rollout flag changes.
    """
    if not isinstance(receipt, Mapping):
        raise ValueError("coordinator receipt must be an object")
    if receipt.get("schema") != "coordinator_time_recheck.v6":
        raise ValueError("coordinator receipt has no policy snapshot")
    policy = deserialize_committed_time_policy(
        receipt.get(COMMITTED_TIME_POLICY_SNAPSHOT_FIELD)
    )
    if policy.producer != "coordinator_queue":
        raise ValueError("coordinator receipt policy has wrong producer")
    validate_committed_time_policy_source(policy, "coordinator_force")
    return policy


def deserialize_coordinator_event_policy(
    event: object,
) -> CommittedPickupPolicySnapshot:
    """Read the queue-owned policy bound into one canonical authority event."""
    if not isinstance(event, Mapping):
        raise ValueError("coordinator authority event must be an object")
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        raise ValueError("coordinator authority payload must be an object")
    proof = payload.get("committed_authority_proof")
    observation = (
        proof.get("observation") if isinstance(proof, Mapping) else None
    )
    if not isinstance(observation, Mapping) or observation.get(
        "source"
    ) != "coordinator_force":
        raise ValueError("coordinator authority proof source is invalid")
    return deserialize_coordinator_receipt_policy(
        observation.get("authority_receipt")
    )


def project_time_event_order(
    current: Mapping[str, object] | None,
    event_or_payload: Mapping[str, object] | None,
) -> dict:
    """Project coupled fields exactly as the canonical pickup writer will.

    Classification at preflight and at apply must use the same post-event
    aggregate. Otherwise an unfinished legacy pickup can change prep 20→60
    after being classified as elastic and create split pickup/CK truth.
    """
    projected = dict(current or {})
    candidate = event_or_payload or {}
    payload = candidate.get("payload")
    if isinstance(payload, Mapping):
        candidate = payload
    for state_field, _old_key, new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
        new_value = candidate.get(new_key)
        if new_value is not None:
            projected[state_field] = new_value
    return projected


def project_time_observation_order(
    current: Mapping[str, object] | None,
    observation: Mapping[str, object] | None,
) -> dict:
    """Project a producer observation through the canonical coupled writer."""
    observation = observation or {}
    return project_time_event_order(
        current,
        {
            "new_order_type": observation.get("observed_order_type"),
            "new_prep_minutes": observation.get("observed_prep_minutes"),
            "new_decision_deadline": observation.get(
                "observed_decision_deadline"
            ),
            "new_zmiana_czasu_odbioru": observation.get(
                "new_zmiana_czasu_odbioru"
            ),
        },
    )


ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD = (
    "czasowka_assignment_ck_forward_authority_enabled"
)
ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD = (
    "czasowka_assignment_ck_passive_guard_enabled"
)
NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD = (
    "czasowka_new_order_time_authority_enabled"
)
NEW_ORDER_TIME_INTENT_SCHEMA = "committed_pickup.new_order_intent.v1"
NEW_ORDER_TIME_INTENT_FIELD = "pending_committed_time_intent"
NEW_ORDER_TIME_INTENT_ID_FIELD = "committed_new_order_time_intent_id"
_NEW_ORDER_TIME_INTENT_BODY_FIELDS = (
    "schema",
    "order_id",
    "forward_authority_enabled",
    "pickup_at_warsaw",
    "czas_kuriera_warsaw",
    "czas_kuriera_hhmm",
    "status_id",
    "prep_minutes",
    "decision_deadline",
    "zmiana_czasu_odbioru",
    "observed_at",
)
_NEW_ORDER_TIME_INTENT_FIELDS = frozenset(
    (*_NEW_ORDER_TIME_INTENT_BODY_FIELDS, "intent_id")
)
_AUTHORITY_EVENT_EXACT_KEYS = frozenset(
    {
        ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD,
        ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD,
        "committed_authority_attestation",
    }
)


class ResolutionOutcome(str, Enum):
    """Wynik klasyfikacji obserwacji committed pickup."""

    APPLY = "apply"
    SUPPRESS = "suppress"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CommittedPickupResolution:
    outcome: ResolutionOutcome
    reason: str
    event: Optional[dict] = None


def pickup_payload_requires_coordinator_receipt(
    payload: Mapping[str, object] | None,
) -> bool:
    """Contextual reserved-source oracle, including normalized envelopes.

    The pickup producer normalizes source to the authority reason while
    preserving the raw transport in observed_source. Both fields therefore
    belong to the same receipt boundary for a czasowka. ``coordinator_force``
    is also a valid legacy source for an elastic order, so callers must apply
    this predicate only after resolving the order class (or conservatively in
    the code-rollback preflight).
    """
    payload = payload or {}
    return any(
        payload.get(field) in RECEIPT_REQUIRED_PICKUP_SOURCES
        for field in ("source", "observed_source")
    )


def _has_committed_pickup_event_identity(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and COMMITTED_PICKUP_EVENT_ID_MARKER in value
    )


def _payload_has_authority_artifact(payload: Mapping[str, object]) -> bool:
    """Schema reservation is based on key presence, never truthiness."""
    return any(
        str(key).startswith("committed_")
        or key in _AUTHORITY_PAYLOAD_EXACT_KEYS
        for key in payload
    ) or payload.get("source") in ALL_COMMITTED_AUTHORITIES


def _event_has_authority_artifact(event: Mapping[str, object]) -> bool:
    return bool(
        any(
            key in _AUTHORITY_EVENT_EXACT_KEYS
            for key in event
        )
        or _has_committed_pickup_event_identity(event.get("event_id"))
        or _has_committed_pickup_event_identity(event.get("event_id_hint"))
    )


def pickup_event_has_authority_artifact(
    event: Mapping[str, object] | None,
) -> bool:
    """Czy PICKUP_TIME_UPDATED niesie choć jeden ślad nowego authority.

    To jest wspólny fail-closed oracle dla state apply i rollbacku. Częściowe
    uszkodzenie koperty nie może zmienić semantyki z proof-bound authority na
    legacy tylko dlatego, że zniknął jeden klucz ``committed_authority``.
    """
    if not isinstance(event, Mapping):
        return False
    if event.get("event_type") != "PICKUP_TIME_UPDATED":
        return False
    if _event_has_authority_artifact(event):
        return True
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return True
    return _payload_has_authority_artifact(payload)


def state_has_committed_pickup_artifact(
    order: Mapping[str, object] | None,
) -> bool:
    """Czy rekord nadal wymaga v4 ownera pickup+CK, także po hot-OFF.

    Wartość pusta, lecz różna od ``None``, jest traktowana fail-closed jako
    częściowo uszkodzona provenance. Jawny legacy write czyści wszystkie pola
    razem do ``None``.
    """
    if not isinstance(order, Mapping):
        return False
    return bool(
        order.get(NEW_ORDER_TIME_INTENT_FIELD) is not None
        or any(
            field in order and order.get(field) is not None
            for field in COMMITTED_PICKUP_STATE_FIELDS
        )
    )


def is_committed_pickup_artifact(
    event: Mapping[str, object] | None,
) -> bool:
    """Conservative rollback classifier for durable authority artifacts."""
    if event is None:
        # Caller klasyfikuje wyłącznie niedomknięte durable rows. Brak
        # state_event oznacza korupcję dowodu, nie dowód że wiersz jest legacy.
        return True
    if not isinstance(event, Mapping):
        # Corrupt unfinished state_event nie może zostać uznany za bezpieczny
        # podczas cofania kodu, bo jego utraconego typu/authority nie da się
        # już dowieść. Fail closed jest jedyną odwracalną decyzją.
        return True
    if not event:
        return True
    if _event_has_authority_artifact(event):
        return True
    if COMMITTED_TIME_POLICY_SNAPSHOT_FIELD in event:
        # Raw time work carrying a v23 policy lease is safe to resume only in
        # code that understands that lease.  Presence, even malformed, blocks
        # a code-contract revert instead of silently restoring live flags.
        return True
    event_type = event.get("event_type")
    if not isinstance(event_type, str) or not event_type.strip():
        return True
    payload = event.get("payload")
    if payload is not None and not isinstance(payload, Mapping):
        return True
    if isinstance(payload, Mapping) and _payload_has_authority_artifact(payload):
        return True
    # Pre-v4 code może ponownie przetłumaczyć każdy niedomknięty raw CK według
    # starszej polityki. Rollback klasyfikuje więc wszystkie takie wiersze jako
    # blocker, także wariant manual-marker bez nowych pól authority.
    if event_type == "CZAS_KURIERA_UPDATED":
        return True
    if (
        event_type == "PICKUP_TIME_UPDATED"
        and pickup_payload_requires_coordinator_receipt(payload)
    ):
        # Rollback nie ma orders_state contextu. Deliberate elastic pickup jest
        # legalny w v4, ale stary kod nie potrafi dowieść klasy wiersza po
        # częściowej korupcji, więc terminalizacja przed code revert jest
        # świadomie konserwatywna.
        return True
    return pickup_event_has_authority_artifact(event)


def is_committed_pickup_outbox_artifact(
    row: Mapping[str, object] | None,
) -> bool:
    """Canonical row-level rollback classifier for unfinished durable work.

    Outbox identity is part of the authority contract. Looking only inside
    decoded ``state_event`` lets partial JSON corruption erase the last proof
    while the canonical event key still identifies a committed transaction.
    Every malformed binding therefore blocks a code-contract revert.
    """
    if not isinstance(row, Mapping) or not row:
        return True
    event = row.get("state_event")
    if not isinstance(event, Mapping) or not event:
        return True

    row_event_id = str(row.get("event_id") or "").strip()
    row_event_key = str(row.get("event_key") or "").strip()
    row_order_id = _clean_id(row.get("order_id"))
    event_type = str(event.get("event_type") or "").strip()
    event_id = str(event.get("event_id") or "").strip()
    event_order_id = _clean_id(event.get("order_id"))
    payload = event.get("payload")
    if (
        not row_event_id
        or not row_event_key
        or not row_order_id
        or not event_type
        or not event_id
        or not event_order_id
        or not isinstance(payload, Mapping)
        or event_id != row_event_id
        or event_order_id != row_order_id
    ):
        return True
    if (
        _has_committed_pickup_event_identity(row_event_id)
        or _has_committed_pickup_event_identity(row_event_key)
    ):
        return True
    return is_committed_pickup_artifact(event)


def is_forward_authority_outbox_artifact(
    row: Mapping[str, object] | None,
    current_order: Mapping[str, object] | None,
    *,
    is_czasowka: bool,
) -> bool:
    """Fence every pending time writer whose semantics change at forward ON.

    Code rollback remains deliberately conservative for every unfinished raw
    CK row because a pre-authority reader cannot reconstruct its class. A
    forward flip changes both CK and pickup writers of ``czasowka`` and runs
    while writers are quiesced with a strict current-state snapshot. Only a
    fully bound time receipt for an explicitly elastic aggregate is provably
    unaffected; malformed, missing, authority-bearing or ambiguously
    classified work still blocks.
    """
    if not isinstance(row, Mapping) or not row:
        return True
    event = row.get("state_event")
    if not isinstance(event, Mapping) or not event:
        return True
    payload = event.get("payload")
    event_type = event.get("event_type")
    if event_type not in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}:
        return is_committed_pickup_outbox_artifact(row)
    if not isinstance(payload, Mapping):
        return True
    row_order_id = _clean_id(row.get("order_id"))
    event_order_id = _clean_id(event.get("order_id"))
    if (
        not row_order_id
        or row_order_id != event_order_id
        or str(row.get("event_id") or "").strip()
        != str(event.get("event_id") or "").strip()
    ):
        return True
    # Forward rollout changes every time writer of a czasowka, not merely raw
    # CK.  A pending legacy PICKUP_TIME_UPDATED can win revision 0 and make the
    # already accepted authority event stale.  Only a fully resolved explicit
    # elastic aggregate proves that either raw time event keeps its semantics.
    if (
        not isinstance(current_order, Mapping)
        or _clean_id(current_order.get("order_id")) != row_order_id
        or current_order.get("order_type") != "elastic"
        or is_czasowka
        or state_has_committed_pickup_artifact(current_order)
        or _event_has_authority_artifact(event)
        or _payload_has_authority_artifact(payload)
    ):
        return True
    if event_type == "PICKUP_TIME_UPDATED":
        return False
    old_iso = payload.get("old_ck_iso")
    old_hhmm = payload.get("old_ck_hhmm")
    new_iso = payload.get("new_ck_iso")
    new_hhmm = payload.get("new_ck_hhmm")
    old_is_null = old_iso is None and old_hhmm is None
    old_dt = None if old_is_null else _parse_aware(old_iso)
    new_dt = _parse_aware(new_iso)
    if (
        (not old_is_null and old_dt is None)
        or new_dt is None
        or (not old_is_null and old_dt.strftime("%H:%M") != old_hhmm)
        or new_dt.strftime("%H:%M") != new_hhmm
        or not isinstance(payload.get("source"), str)
        or not str(payload.get("source") or "").strip()
    ):
        return True
    delta = payload.get("delta_min")
    if delta is not None and (
        isinstance(delta, bool) or not isinstance(delta, (int, float))
    ):
        return True
    return False


def _resolution(
    outcome: ResolutionOutcome,
    reason: str,
    event: Optional[dict] = None,
) -> CommittedPickupResolution:
    return CommittedPickupResolution(outcome=outcome, reason=reason, event=event)


def _parse_aware(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def committed_time_contract_is_complete(
    order: Mapping[str, object] | None,
) -> bool:
    """Validate the one pickup/CK tuple consumed by planning and the app.

    Populated strings are not enough: both ISO values must be timezone-aware,
    denote the same instant and agree with the HH:MM projection.  Legacy rows
    without authority provenance may be a valid rollout baseline.  If any
    provenance is present, its structural identity must be complete as well;
    partial authority state never becomes evidence for a safe flip.
    """
    if not isinstance(order, Mapping):
        return False
    if order.get(NEW_ORDER_TIME_INTENT_FIELD) is not None:
        # The tuple is not complete until the exact initial intent is consumed
        # or superseded in the same atomic pickup+CK state write.
        return False
    pickup = _parse_aware(order.get("pickup_at_warsaw"))
    ck = _parse_aware(order.get("czas_kuriera_warsaw"))
    ck_hhmm = order.get("czas_kuriera_hhmm")
    if (
        pickup is None
        or ck is None
        or not isinstance(ck_hhmm, str)
        or ck.strftime("%H:%M") != ck_hhmm
        or pickup != ck
    ):
        return False
    if not state_has_committed_pickup_artifact(order):
        return True
    if any(field not in order for field in COMMITTED_PICKUP_STATE_FIELDS):
        return False
    return bool(
        order.get("committed_pickup_authority") in ALL_COMMITTED_AUTHORITIES
        and order.get("committed_pickup_authority_proof_schema")
        == AUTHORITY_PROOF_SCHEMA
        and _parse_aware(order.get("committed_pickup_observed_at"))
        is not None
        and isinstance(order.get("committed_pickup_observed_source"), str)
        and bool(str(order.get("committed_pickup_observed_source") or "").strip())
        and _has_committed_pickup_event_identity(
            order.get("committed_pickup_event_key")
        )
    )


def _clean_id(value: object) -> str:
    return str(value or "").strip()


def _new_order_time_intent_id(body: Mapping[str, object]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            dict(body),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return f"{NEW_ORDER_TIME_INTENT_SCHEMA}:{digest}"


def build_new_order_time_intent(
    order_id: object,
    payload: Mapping[str, object] | None,
    *,
    observed_at: object,
) -> dict:
    """Seal the raw initial Rutcom tuple before NEW_ORDER sanitizes it."""
    payload = payload or {}
    body = {
        "schema": NEW_ORDER_TIME_INTENT_SCHEMA,
        "order_id": _clean_id(order_id),
        "forward_authority_enabled": True,
        "pickup_at_warsaw": payload.get("pickup_at_warsaw"),
        "czas_kuriera_warsaw": payload.get("czas_kuriera_warsaw"),
        "czas_kuriera_hhmm": payload.get("czas_kuriera_hhmm"),
        "status_id": payload.get("status_id"),
        "prep_minutes": payload.get("prep_minutes"),
        "decision_deadline": payload.get("decision_deadline"),
        "zmiana_czasu_odbioru": payload.get("zmiana_czasu_odbioru"),
        "observed_at": observed_at,
    }
    return {**body, "intent_id": _new_order_time_intent_id(body)}


def new_order_time_intent_is_valid(
    intent: Mapping[str, object] | None,
    *,
    order_id: object,
) -> bool:
    """Exact schema/hash/order binding for a durable initial-time receipt."""
    if not isinstance(intent, Mapping) or set(intent) != (
        _NEW_ORDER_TIME_INTENT_FIELDS
    ):
        return False
    body = {
        field: intent.get(field)
        for field in _NEW_ORDER_TIME_INTENT_BODY_FIELDS
    }
    return bool(
        intent.get("schema") == NEW_ORDER_TIME_INTENT_SCHEMA
        and intent.get("forward_authority_enabled") is True
        and _clean_id(intent.get("order_id")) == _clean_id(order_id)
        and _clean_id(order_id)
        and _parse_aware(intent.get("observed_at")) is not None
        and intent.get("intent_id") == _new_order_time_intent_id(body)
    )


def _valid_coordinator_receipt(
    receipt: object,
    *,
    order_id: str,
    observed_at: Optional[datetime],
    verified_origin: bool,
) -> bool:
    if not isinstance(receipt, Mapping):
        return False
    if not verified_origin:
        return False
    if receipt.get("schema") not in _COORDINATOR_RECEIPT_SCHEMAS:
        return False
    if _clean_id(receipt.get("order_id")) != _clean_id(order_id):
        return False
    request_id = _clean_id(receipt.get("request_id"))
    if not request_id or len(request_id) > 128:
        return False
    if receipt.get("source") not in _COORDINATOR_RECEIPT_SOURCES:
        return False
    try:
        deserialize_coordinator_receipt_policy(receipt)
    except (TypeError, ValueError):
        return False
    requested_at = _parse_aware(receipt.get("requested_at"))
    eligibility_raw = (
        receipt.get("eligible_at")
        if receipt.get("schema") == "coordinator_time_recheck.v6"
        else receipt.get("requested_at")
    )
    eligible_at = _parse_aware(eligibility_raw)
    if requested_at is None or eligible_at is None or observed_at is None:
        return False
    # Exact live queue membership (``verified_origin``) is the durable lease.
    # Wall-clock age cannot revoke an unclaimed click after a watcher crash or
    # temporary board absence.  The clock remains an audit/causality fence: an
    # observation cannot precede the receipt beyond the allowed skew.
    if eligible_at < requested_at:
        return False
    return observed_at - eligible_at >= -_COORDINATOR_RECEIPT_FUTURE_SKEW


def resolve_czasowka_assignment_ck(
    existing: Mapping[str, object] | None,
    *,
    is_czasowka: bool,
    passive_guard_enabled: bool,
    rutcom_forward_authority_enabled: bool,
) -> CommittedPickupResolution:
    """Single policy owner for the CK carried by COURIER_ASSIGNED.

    Assignment itself remains legal.  Only its parallel CK write is retired
    once Rutcom forward authority owns pickup+CK, or suppressed by the legacy
    passive guard when a committed CK already exists.  Handler and terminal
    oracle consume this exact decision; neither keeps an inline copy.
    """
    existing = existing or {}
    if not is_czasowka:
        return _resolution(ResolutionOutcome.NOT_APPLICABLE, "not_czasowka")
    if rutcom_forward_authority_enabled:
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "rutcom_forward_authority_owns_ck",
        )
    if passive_guard_enabled and existing.get("czas_kuriera_warsaw"):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "passive_guard_preserves_existing_ck",
        )
    return _resolution(ResolutionOutcome.APPLY, "legacy_assignment_ck")


def normalize_pickup_revision(value: object) -> Optional[int]:
    """Kanoniczna, monotoniczna rewizja czasu; bool nie jest liczbą rewizji."""
    if isinstance(value, bool):
        return None
    try:
        revision = int(value)
    except (TypeError, ValueError):
        return None
    return revision if revision >= 0 else None


def build_time_event_cas_snapshot(
    existing: Mapping[str, object] | None,
    event_type: str,
) -> dict:
    """Build the one causal envelope shared by producer, FSM and retry oracle."""
    existing = existing or {}
    snapshot = {
        TIME_EVENT_CAS_SCHEMA_FIELD: TIME_EVENT_CAS_SCHEMA,
        "status_at_observation": existing.get("status"),
        "courier_id_at_observation": existing.get("courier_id"),
        "assignment_event_id_at_observation": existing.get(
            "assignment_event_id"
        ),
    }
    if event_type == "PICKUP_TIME_UPDATED":
        snapshot["pickup_time_revision_at_observation"] = (
            normalize_pickup_revision(
                existing.get("pickup_time_revision", 0)
            )
        )
        return snapshot
    if event_type == "CZAS_KURIERA_UPDATED":
        snapshot[CK_CHANGE_REVISION_OBSERVATION_FIELD] = (
            normalize_pickup_revision(
                existing.get(CK_CHANGE_REVISION_STATE_FIELD, 0)
            )
        )
        return snapshot
    raise ValueError(f"unsupported time CAS event type: {event_type!r}")


def time_event_cas_is_versioned(
    event_type: str,
    payload: Mapping[str, object] | None,
) -> bool:
    """True only for a complete v1 envelope with a valid monotonic revision."""
    if not isinstance(payload, Mapping):
        return False
    if payload.get(TIME_EVENT_CAS_SCHEMA_FIELD) != TIME_EVENT_CAS_SCHEMA:
        return False
    if not all(
        field in payload
        for field in (
            "status_at_observation",
            "courier_id_at_observation",
            "assignment_event_id_at_observation",
        )
    ):
        return False
    revision_field = {
        "CZAS_KURIERA_UPDATED": CK_CHANGE_REVISION_OBSERVATION_FIELD,
        "PICKUP_TIME_UPDATED": "pickup_time_revision_at_observation",
    }.get(event_type)
    return bool(
        revision_field
        and revision_field in payload
        and normalize_pickup_revision(payload.get(revision_field)) is not None
    )


def time_event_cas_artifact_present(
    event_type: str,
    payload: Mapping[str, object] | None,
) -> bool:
    """Reserve every v14 CAS trace so corruption cannot downgrade to legacy."""
    if not isinstance(payload, Mapping):
        return False
    if (
        TIME_EVENT_CAS_SCHEMA_FIELD in payload
        or "status_at_observation" in payload
    ):
        return True
    return bool(
        event_type == "CZAS_KURIERA_UPDATED"
        and any(
            field in payload
            for field in (
                CK_CHANGE_REVISION_OBSERVATION_FIELD,
                "courier_id_at_observation",
                "assignment_event_id_at_observation",
            )
        )
    )


def time_event_cas_status(
    current: Mapping[str, object] | None,
    event: Mapping[str, object] | None,
    *,
    allow_unversioned_ck_claim: bool = False,
) -> Optional[str]:
    """Canonical ``applied/pending/superseded`` CAS for non-authority time events.

    ``None`` means a genuinely historical, unversioned event and preserves its
    pre-v14 behavior. A v13 pickup already carries the revision snapshot even
    without the explicit schema, so it is upgraded in place. An exact queue
    claim for old CK gets a conservative old-value fence; every v14 producer
    emits the full revision-bound schema, which also defeats ABA.
    """
    current = current or {}
    event = event or {}
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return None
    event_type = str(event.get("event_type") or "")
    cas_artifact_present = time_event_cas_artifact_present(event_type, payload)
    if cas_artifact_present and not time_event_cas_is_versioned(
        event_type, payload
    ):
        return "superseded"

    versioned = time_event_cas_is_versioned(event_type, payload)
    v13_pickup = bool(
        event_type == "PICKUP_TIME_UPDATED"
        and "pickup_time_revision_at_observation" in payload
    )
    if not versioned and not v13_pickup:
        if not (
            event_type == "CZAS_KURIERA_UPDATED"
            and allow_unversioned_ck_claim
        ):
            return None
        if str(current.get("courier_id") or "") != str(
            event.get("courier_id") or payload.get("courier_id") or ""
        ):
            return "superseded"
        target_iso = payload.get("new_ck_iso")
        target_hhmm = payload.get("new_ck_hhmm")
        if (
            current.get("czas_kuriera_warsaw") == target_iso
            and current.get("czas_kuriera_hhmm") == target_hhmm
        ):
            return "applied"
        return (
            "pending"
            if current.get("czas_kuriera_warsaw") == payload.get("old_ck_iso")
            and current.get("czas_kuriera_hhmm") == payload.get("old_ck_hhmm")
            else "superseded"
        )

    observed_courier = payload.get("courier_id_at_observation")
    observed_assignment = payload.get("assignment_event_id_at_observation")
    if (
        str(current.get("courier_id") or "")
        != str(observed_courier or "")
        or str(event.get("courier_id") or "")
        != str(observed_courier or "")
        or str(current.get("assignment_event_id") or "")
        != str(observed_assignment or "")
    ):
        return "superseded"
    if versioned and current.get("status") != payload.get(
        "status_at_observation"
    ):
        return "superseded"

    if event_type == "PICKUP_TIME_UPDATED":
        expected = normalize_pickup_revision(
            payload.get("pickup_time_revision_at_observation")
        )
        current_revision = normalize_pickup_revision(
            current.get("pickup_time_revision", 0)
        )
        if expected is None or current_revision is None:
            return "superseded"
        if current.get("pickup_at_warsaw") == payload.get(
            "new_pickup_at_warsaw"
        ):
            return (
                "applied"
                if current_revision == expected + 1
                else "superseded"
            )
        if (
            current.get("status") not in {"planned", "assigned"}
            or current.get("picked_up_at") is not None
            or current.get("delivered_at") is not None
        ):
            return "superseded"
        return (
            "pending"
            if current_revision == expected
            and current.get("pickup_at_warsaw")
            == payload.get("old_pickup_at_warsaw")
            else "superseded"
        )

    if event_type == "CZAS_KURIERA_UPDATED":
        expected = normalize_pickup_revision(
            payload.get(CK_CHANGE_REVISION_OBSERVATION_FIELD)
        )
        current_revision = normalize_pickup_revision(
            current.get(CK_CHANGE_REVISION_STATE_FIELD, 0)
        )
        if expected is None or current_revision is None:
            return "superseded"
        if (
            current.get("czas_kuriera_warsaw") == payload.get("new_ck_iso")
            and current.get("czas_kuriera_hhmm") == payload.get("new_ck_hhmm")
        ):
            return (
                "applied"
                if current_revision == expected + 1
                else "superseded"
            )
        return (
            "pending"
            if current_revision == expected
            and current.get("czas_kuriera_warsaw") == payload.get("old_ck_iso")
            and current.get("czas_kuriera_hhmm") == payload.get("old_ck_hhmm")
            else "superseded"
        )
    return "superseded"


def _same_instant(left: object, right: object) -> bool:
    left_dt = _parse_aware(left)
    right_dt = _parse_aware(right)
    return left_dt is not None and right_dt is not None and left_dt == right_dt


def _base_precondition(
    existing: Mapping[str, object],
    observation: Mapping[str, object],
) -> Optional[CommittedPickupResolution]:
    if existing.get("status") not in _ACTIVE_ORDER_STATES:
        return _resolution(ResolutionOutcome.SUPPRESS, "order_not_active")
    if (
        existing.get("picked_up_at") is not None
        or existing.get("delivered_at") is not None
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "order_already_collected",
        )

    oid = _clean_id(observation.get("oid") or existing.get("order_id"))
    if not oid:
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_order_id")
    existing_oid = _clean_id(existing.get("order_id"))
    observed_oid = _clean_id(observation.get("oid"))
    if existing_oid and observed_oid and existing_oid != observed_oid:
        return _resolution(ResolutionOutcome.SUPPRESS, "order_identity_mismatch")

    current_courier = _clean_id(existing.get("courier_id"))
    observed_courier = _clean_id(observation.get("courier_id"))
    observed_generation_courier = _clean_id(
        observation.get("courier_id_at_observation")
    )
    if (
        current_courier != observed_courier
        or current_courier != observed_generation_courier
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "courier_generation_changed",
        )

    current_assignment = _clean_id(existing.get("assignment_event_id"))
    observed_assignment = _clean_id(
        observation.get("assignment_event_id_at_observation")
    )
    if current_assignment != observed_assignment:
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "assignment_generation_changed",
        )
    current_revision = normalize_pickup_revision(
        existing.get("pickup_time_revision", 0)
    )
    observed_revision = normalize_pickup_revision(
        observation.get("pickup_time_revision_at_observation", 0)
    )
    if current_revision is None or observed_revision != current_revision:
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "pickup_revision_changed",
        )
    coupled_old_keys = tuple(
        old_key for _state_field, old_key, _new_key
        in COMMITTED_PICKUP_COUPLED_FIELDS
    )
    present_old_keys = tuple(
        key for key in coupled_old_keys if key in observation
    )
    if present_old_keys and len(present_old_keys) != len(coupled_old_keys):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "invalid_coupled_state_snapshot",
        )
    for state_field, old_key, _new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
        if (
            old_key in observation
            and existing.get(state_field) != observation.get(old_key)
        ):
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                f"{state_field}_changed",
            )
    # Committed pickup zmienia jednocześnie pickup i CK. Dlatego jego causal
    # proof musi wiązać obie monotoniczne generacje, a nie tylko bieżącą
    # wartość CK. Sama równość A po legalnym cyklu A→C→A nie dowodzi, że
    # obserwacja nadal opisuje bieżący stan.
    if CK_CHANGE_REVISION_OBSERVATION_FIELD in observation:
        current_ck_revision = normalize_pickup_revision(
            existing.get(CK_CHANGE_REVISION_STATE_FIELD, 0)
        )
        observed_ck_revision = normalize_pickup_revision(
            observation.get(CK_CHANGE_REVISION_OBSERVATION_FIELD)
        )
        if (
            current_ck_revision is None
            or observed_ck_revision != current_ck_revision
        ):
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "ck_revision_changed",
            )
    # Pickup-derived authority także wiąże równoległy snapshot CK. Bez tego
    # claim A→B mógł po legalnym CK A→C wrócić i zmirrorować oba pola do B.
    # Klucze są opcjonalne tylko dla surowej obserwacji przed zbudowaniem
    # eventu; kanoniczny proof zawsze je dostaje w ``_build_pickup_event``.
    has_old_ck_iso = "old_ck_iso" in observation
    has_old_ck_hhmm = "old_ck_hhmm" in observation
    if has_old_ck_iso or has_old_ck_hhmm:
        if not (has_old_ck_iso and has_old_ck_hhmm):
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "invalid_ck_generation_snapshot",
            )
        expected_ck_raw = observation.get("old_ck_iso")
        expected_ck_hhmm = observation.get("old_ck_hhmm")
        current_ck_raw = existing.get("czas_kuriera_warsaw")
        current_ck_hhmm = existing.get("czas_kuriera_hhmm")
        if expected_ck_raw is None or expected_ck_hhmm is None:
            if not (
                expected_ck_raw is None
                and expected_ck_hhmm is None
                and current_ck_raw is None
                and current_ck_hhmm is None
            ):
                return _resolution(
                    ResolutionOutcome.SUPPRESS,
                    "observed_ck_changed",
                )
        else:
            expected_ck = _parse_aware(expected_ck_raw)
            current_ck = _parse_aware(current_ck_raw)
            if (
                expected_ck is None
                or current_ck is None
                or not isinstance(expected_ck_hhmm, str)
                or expected_ck.strftime("%H:%M") != expected_ck_hhmm
                or current_ck.strftime("%H:%M") != current_ck_hhmm
                or expected_ck != current_ck
            ):
                return _resolution(
                    ResolutionOutcome.SUPPRESS,
                    "observed_ck_changed",
                )
    return None


def committed_pickup_event_id(
    order_id: str,
    *,
    courier_id: object,
    payload_without_event_key: Mapping[str, object],
) -> str:
    """Klucz pełnego efektu i proofu; tylko identyczny retry może się zduplikować."""
    semantic = {
        "event_type": "PICKUP_TIME_UPDATED",
        "courier_id": _clean_id(courier_id),
        "order_id": _clean_id(order_id),
        "payload": dict(payload_without_event_key),
    }
    digest = hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    return f"{_clean_id(order_id)}_PICKUP_TIME_UPDATED_COMMITTED_{digest}"


def _authority_proof(
    observation: Mapping[str, object],
    *,
    order_id: str,
    authority: str,
    observation_kind: str,
) -> dict:
    receipt = observation.get("authority_receipt")
    proof_observation = {
        "oid": order_id,
        "courier_id": observation.get("courier_id"),
        "courier_id_at_observation": observation.get(
            "courier_id_at_observation"
        ),
        "assignment_event_id_at_observation": observation.get(
            "assignment_event_id_at_observation"
        ),
        "pickup_time_revision_at_observation": observation.get(
            "pickup_time_revision_at_observation"
        ),
        CK_CHANGE_REVISION_OBSERVATION_FIELD: observation.get(
            CK_CHANGE_REVISION_OBSERVATION_FIELD
        ),
        "source": observation.get("source"),
        "observed_at": observation.get("observed_at"),
        "observed_status_id": observation.get("observed_status_id"),
        "observed_pickup_at_warsaw": observation.get(
            "observed_pickup_at_warsaw"
        ),
        "old_ck_iso": observation.get("old_ck_iso"),
        "old_ck_hhmm": observation.get("old_ck_hhmm"),
        "new_ck_iso": observation.get("new_ck_iso"),
        "new_ck_hhmm": observation.get("new_ck_hhmm"),
        "new_pickup_at_warsaw": observation.get("new_pickup_at_warsaw"),
        "new_zmiana_czasu_odbioru": observation.get(
            "new_zmiana_czasu_odbioru"
        ),
        "observed_prep_minutes": observation.get("observed_prep_minutes"),
        "observed_decision_deadline": observation.get(
            "observed_decision_deadline"
        ),
        "authority_receipt": (
            dict(receipt) if isinstance(receipt, Mapping) else None
        ),
    }
    if observation.get(NEW_ORDER_TIME_INTENT_ID_FIELD) is not None:
        proof_observation[NEW_ORDER_TIME_INTENT_ID_FIELD] = observation.get(
            NEW_ORDER_TIME_INTENT_ID_FIELD
        )
    for _state_field, old_key, _new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
        proof_observation[old_key] = observation.get(old_key)
    return {
        "schema": AUTHORITY_PROOF_SCHEMA,
        "authority": authority,
        "observation_kind": observation_kind,
        "order_id": order_id,
        "observation": proof_observation,
    }


def _build_pickup_event(
    existing: Mapping[str, object],
    observation: Mapping[str, object],
    *,
    reason: str,
    new_pickup: str,
    observation_kind: str,
) -> dict:
    oid = _clean_id(observation.get("oid") or existing.get("order_id"))
    # Event zawsze idzie lane'em bieżącej, sprawdzonej generacji. Nigdy nie
    # przenosimy starego courier_id z worka/pre-proposal do downstream.
    courier_id = existing.get("courier_id")
    old_pickup_raw = existing.get("pickup_at_warsaw")
    old_pickup = (
        None if old_pickup_raw is None else str(old_pickup_raw)
    )
    assignment_event_id = existing.get("assignment_event_id")
    receipt = observation.get("authority_receipt")
    receipt_id = (
        _clean_id(receipt.get("request_id"))
        if isinstance(receipt, Mapping)
        else ""
    )
    old_pickup_dt = _parse_aware(old_pickup)
    new_pickup_dt = _parse_aware(new_pickup)
    # Null jest legalnym, causal baseline'em wyłącznie dla pierwszego
    # panelowego snapshotu po NEW_ORDER. Resolver odrzuca każdy niepusty,
    # nieparsowalny baseline przed wejściem tutaj.
    assert old_pickup is None or old_pickup_dt is not None
    assert new_pickup_dt is not None

    normalized_observation = dict(observation)
    normalized_observation["oid"] = oid
    normalized_observation["courier_id"] = courier_id
    normalized_observation["courier_id_at_observation"] = existing.get(
        "courier_id"
    )
    normalized_observation[
        "assignment_event_id_at_observation"
    ] = assignment_event_id
    normalized_observation[
        "pickup_time_revision_at_observation"
    ] = normalize_pickup_revision(existing.get("pickup_time_revision", 0))
    normalized_observation[CK_CHANGE_REVISION_OBSERVATION_FIELD] = (
        normalize_pickup_revision(
            existing.get(CK_CHANGE_REVISION_STATE_FIELD, 0)
        )
    )
    if "old_ck_iso" not in normalized_observation:
        normalized_observation["old_ck_iso"] = existing.get(
            "czas_kuriera_warsaw"
        )
    if "old_ck_hhmm" not in normalized_observation:
        normalized_observation["old_ck_hhmm"] = existing.get(
            "czas_kuriera_hhmm"
        )
    for state_field, old_key, _new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
        normalized_observation[old_key] = existing.get(state_field)
    proof = _authority_proof(
        normalized_observation,
        order_id=oid,
        authority=reason,
        observation_kind=observation_kind,
    )
    payload = {
        "oid": oid,
        "courier_id": courier_id,
        "old_pickup_at_warsaw": old_pickup,
        "new_pickup_at_warsaw": new_pickup,
        "old_ck_iso": normalized_observation.get("old_ck_iso"),
        "old_ck_hhmm": normalized_observation.get("old_ck_hhmm"),
        "delta_min": (
            None
            if old_pickup_dt is None
            else round(
                (new_pickup_dt - old_pickup_dt).total_seconds() / 60.0,
                2,
            )
        ),
        "source": reason,
        "observed_source": observation.get("source"),
        "observed_at": observation.get("observed_at"),
        "committed_authority": reason,
        "committed_authority_receipt_id": receipt_id or None,
        "committed_pickup_panel_baseline_at_observation": observation.get(
            "observed_pickup_at_warsaw"
        ),
        "committed_ck_panel_baseline_at_observation": observation.get(
            "new_ck_iso"
        ),
        "committed_authority_proof": proof,
        "assignment_event_id_at_observation": assignment_event_id,
        "courier_id_at_observation": existing.get("courier_id"),
        "pickup_time_revision_at_observation": normalize_pickup_revision(
            existing.get("pickup_time_revision", 0)
        ),
        CK_CHANGE_REVISION_OBSERVATION_FIELD: normalized_observation.get(
            CK_CHANGE_REVISION_OBSERVATION_FIELD
        ),
    }
    if observation.get(NEW_ORDER_TIME_INTENT_ID_FIELD) is not None:
        payload[NEW_ORDER_TIME_INTENT_ID_FIELD] = observation.get(
            NEW_ORDER_TIME_INTENT_ID_FIELD
        )
    coupled_new_values = {
        "new_order_type": "czasowka",
        "new_prep_minutes": observation.get("observed_prep_minutes"),
        "new_decision_deadline": observation.get(
            "observed_decision_deadline"
        ),
        "new_zmiana_czasu_odbioru": observation.get(
            "new_zmiana_czasu_odbioru"
        ),
    }
    for _state_field, old_key, new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
        payload[old_key] = normalized_observation.get(old_key)
        payload[new_key] = coupled_new_values[new_key]
    if reason == "rutcom_manual_marker":
        payload["manual_ck_edit_passthrough"] = True

    event_key = committed_pickup_event_id(
        oid,
        courier_id=courier_id,
        payload_without_event_key=payload,
    )
    payload["committed_pickup_event_key"] = event_key

    return {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": oid,
        "courier_id": courier_id,
        "payload": payload,
        "event_id_hint": event_key,
    }


def resolve_czasowka_committed_observation(
    existing: Mapping[str, object] | None,
    observation: Mapping[str, object] | None,
    *,
    is_czasowka: bool,
    passive_guard_enabled: bool,
    manual_passthrough_enabled: bool,
    rutcom_forward_authority_enabled: bool,
    coordinator_receipt_verified: bool = False,
) -> CommittedPickupResolution:
    """Rozstrzygnij obserwacje Rutcom ``czas_kuriera`` bez efektow I/O."""
    existing = existing or {}
    observation = observation or {}
    source = observation.get("source")

    if not is_czasowka:
        return _resolution(ResolutionOutcome.NOT_APPLICABLE, "not_czasowka")
    if source not in PASSIVE_CK_SOURCES and source != "coordinator_force":
        return _resolution(ResolutionOutcome.NOT_APPLICABLE, "source_not_owned")
    if source == "first_acceptance" and not rutcom_forward_authority_enabled:
        # Exact OFF parity: before this authority existed, the first complete
        # CK snapshot was a raw legacy event.  NOT_APPLICABLE returns ownership
        # to that existing path; every ON decision remains canonical here.
        return _resolution(
            ResolutionOutcome.NOT_APPLICABLE,
            "legacy_first_acceptance",
        )
    if not passive_guard_enabled and (
        manual_passthrough_enabled
        or rutcom_forward_authority_enabled
        or source == "coordinator_force"
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "authority_requires_passive_guard",
        )
    if not passive_guard_enabled:
        return _resolution(ResolutionOutcome.NOT_APPLICABLE, "guard_disabled")

    blocked = _base_precondition(existing, observation)
    if blocked is not None:
        return blocked

    oid = _clean_id(observation.get("oid") or existing.get("order_id"))
    old_pickup_raw = existing.get("pickup_at_warsaw")
    old_pickup = _parse_aware(old_pickup_raw)
    observed_pickup = _parse_aware(
        observation.get("observed_pickup_at_warsaw")
    )
    current_ck = _parse_aware(existing.get("czas_kuriera_warsaw"))
    observed_old_ck = _parse_aware(observation.get("old_ck_iso"))
    observed_old_ck_hhmm = observation.get("old_ck_hhmm")
    new_ck = _parse_aware(observation.get("new_ck_iso"))
    new_ck_hhmm = observation.get("new_ck_hhmm")
    initial_pickup_baseline = old_pickup_raw is None
    if (
        observed_pickup is None
        or (old_pickup is None and not initial_pickup_baseline)
    ):
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_pickup_snapshot")
    previous_parallel_baseline = existing.get(
        "committed_pickup_panel_baseline_at_observation"
    )
    recognized_parallel_baseline = bool(
        existing.get("committed_pickup_authority") in CK_DERIVED_AUTHORITIES
        and previous_parallel_baseline
        and _same_instant(
            observation.get("observed_pickup_at_warsaw"),
            previous_parallel_baseline,
        )
    )
    if (
        not initial_pickup_baseline
        and old_pickup != observed_pickup
        and not recognized_parallel_baseline
    ):
        return _resolution(ResolutionOutcome.SUPPRESS, "observed_pickup_changed")
    null_ck_baseline = (
        existing.get("czas_kuriera_warsaw") is None
        and existing.get("czas_kuriera_hhmm") is None
        and observation.get("old_ck_iso") is None
        and observed_old_ck_hhmm is None
    )
    if not null_ck_baseline and (
        current_ck is None
        or observed_old_ck is None
        or not isinstance(observed_old_ck_hhmm, str)
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "invalid_observed_ck_baseline",
        )
    if (
        not null_ck_baseline
        and observed_old_ck.strftime("%H:%M") != observed_old_ck_hhmm
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "observed_ck_iso_hhmm_mismatch",
        )
    if not null_ck_baseline and observed_old_ck != current_ck:
        return _resolution(ResolutionOutcome.SUPPRESS, "observed_ck_changed")
    if new_ck is None or not isinstance(new_ck_hhmm, str):
        return _resolution(ResolutionOutcome.SUPPRESS, "invalid_committed_time")
    if new_ck.strftime("%H:%M") != new_ck_hhmm:
        return _resolution(ResolutionOutcome.SUPPRESS, "ck_iso_hhmm_mismatch")
    previous_ck_parallel_baseline = existing.get(
        "committed_ck_panel_baseline_at_observation"
    )
    if (
        existing.get("committed_pickup_authority") == "rutcom_pickup_field"
        and previous_ck_parallel_baseline
        and _same_instant(
            observation.get("new_ck_iso"), previous_ck_parallel_baseline
        )
    ):
        # Ten sam Rutcom response może jednocześnie nieść nowy pickup i stary
        # CK. Po zaakceptowaniu pickup zapamiętujemy pełną parę obserwacji;
        # kolejny diff tego samego, niezmienionego response nie może odwrócić
        # decyzji i wyprodukować sztucznego drugiego commitmentu.
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "parallel_ck_snapshot_stale",
        )
    if current_ck is not None and new_ck == current_ck:
        return _resolution(ResolutionOutcome.SUPPRESS, "no_committed_change")
    if (
        current_ck is not None
        and abs((new_ck - current_ck).total_seconds()) / 60.0
        < COMMITTED_CK_DELTA_THRESHOLD_MIN
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "committed_delta_below_threshold",
        )

    try:
        observed_status_id = int(observation.get("observed_status_id"))
    except (TypeError, ValueError):
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_rutcom_status")
    if observed_status_id not in _ACTIVE_RUTCOM_STATUS_IDS:
        return _resolution(ResolutionOutcome.SUPPRESS, "rutcom_status_not_active")

    observed_at = _parse_aware(observation.get("observed_at"))
    if source == "coordinator_force":
        if not rutcom_forward_authority_enabled:
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "forward_authority_off",
            )
        if not _valid_coordinator_receipt(
            observation.get("authority_receipt"),
            order_id=oid,
            observed_at=observed_at,
            verified_origin=coordinator_receipt_verified,
        ):
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "missing_authority_receipt",
            )
        return _resolution(
            ResolutionOutcome.APPLY,
            "coordinator_receipt",
            _build_pickup_event(
                existing,
                observation,
                reason="coordinator_receipt",
                new_pickup=str(observation.get("new_ck_iso")),
                observation_kind="rutcom_ck",
            ),
        )

    manual_marker_edge = (
        existing.get("zmiana_czasu_odbioru") is False
        and observation.get("new_zmiana_czasu_odbioru") is True
    )
    if manual_passthrough_enabled and manual_marker_edge:
        allowed = _MANUAL_RUTCOM_STATUS_IDS_BY_ORDER_STATE.get(
            str(existing.get("status"))
        )
        if not allowed or observed_status_id not in allowed:
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "manual_status_mismatch",
            )
        return _resolution(
            ResolutionOutcome.APPLY,
            "rutcom_manual_marker",
            _build_pickup_event(
                existing,
                observation,
                reason="rutcom_manual_marker",
                new_pickup=str(observation.get("new_ck_iso")),
                observation_kind="rutcom_ck",
            ),
        )

    if not rutcom_forward_authority_enabled:
        return _resolution(ResolutionOutcome.SUPPRESS, "forward_authority_off")
    pickup_floor = old_pickup or observed_pickup
    if (
        current_ck is not None
        and new_ck <= current_ck
    ) or new_ck < pickup_floor:
        return _resolution(ResolutionOutcome.SUPPRESS, "passive_not_forward")
    if observed_status_id not in _AUTOMATIC_FORWARD_RUTCOM_STATUS_IDS:
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "forward_status_not_authoritative",
        )
    if observed_at is None:
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_observed_at")
    if new_ck <= observed_at:
        return _resolution(ResolutionOutcome.SUPPRESS, "forward_not_future")

    return _resolution(
        ResolutionOutcome.APPLY,
        "rutcom_forward_commitment",
        _build_pickup_event(
            existing,
            observation,
            reason="rutcom_forward_commitment",
            new_pickup=str(observation.get("new_ck_iso")),
            observation_kind="rutcom_ck",
        ),
    )


def resolve_czasowka_pickup_observation(
    existing: Mapping[str, object] | None,
    observation: Mapping[str, object] | None,
    *,
    is_czasowka: bool,
    coordinator_receipt_verified: bool = False,
) -> CommittedPickupResolution:
    """Rozstrzygnij rownolegle pole Rutcom ``pickup_at`` dla czasowki."""
    existing = existing or {}
    observation = observation or {}
    if not is_czasowka:
        return _resolution(ResolutionOutcome.NOT_APPLICABLE, "not_czasowka")
    if observation.get("source") not in _PICKUP_OBSERVATION_SOURCES:
        return _resolution(ResolutionOutcome.NOT_APPLICABLE, "source_not_owned")

    blocked = _base_precondition(existing, observation)
    if blocked is not None:
        return blocked

    current_pickup_raw = existing.get("pickup_at_warsaw")
    current_pickup = _parse_aware(current_pickup_raw)
    new_pickup_raw = observation.get("new_pickup_at_warsaw")
    new_pickup = _parse_aware(new_pickup_raw)
    initial_pickup_baseline = current_pickup_raw is None
    if (
        new_pickup is None
        or (current_pickup is None and not initial_pickup_baseline)
    ):
        return _resolution(ResolutionOutcome.SUPPRESS, "invalid_pickup_time")
    if current_pickup is not None and current_pickup == new_pickup:
        return _resolution(ResolutionOutcome.SUPPRESS, "no_pickup_change")

    baseline = existing.get(
        "committed_pickup_panel_baseline_at_observation"
    )
    observed_at = _parse_aware(observation.get("observed_at"))
    coordinator_override = False
    if observation.get("source") == "coordinator_force":
        coordinator_override = _valid_coordinator_receipt(
            observation.get("authority_receipt"),
            order_id=_clean_id(
                observation.get("oid") or existing.get("order_id")
            ),
            observed_at=observed_at,
            verified_origin=coordinator_receipt_verified,
        )
        if not coordinator_override:
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "missing_authority_receipt",
            )
    if (
        existing.get("committed_pickup_authority") in CK_DERIVED_AUTHORITIES
        and baseline
        and _same_instant(new_pickup_raw, baseline)
    ):
        # Receipt potwierdza intencję świeżego odczytu, nie wybór jednego z
        # dwóch sprzecznych pól Rutcom. Po CK-derived commit zapamiętany pickup
        # baseline jest znanym równoległym, starym snapshotem i nigdy nie może
        # cofnąć kanonu. Jawna korekta idzie przez CK/manual-marker; nowa,
        # odmienna wartość pickup nadal przechodzi własnym ownerem poniżej.
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "parallel_pickup_snapshot_stale",
        )

    observed_status = observation.get("observed_status_id")
    if observed_status not in (None, ""):
        try:
            status_id = int(observed_status)
        except (TypeError, ValueError):
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "invalid_rutcom_status",
            )
        if status_id not in _ACTIVE_RUTCOM_STATUS_IDS:
            return _resolution(
                ResolutionOutcome.SUPPRESS,
                "rutcom_status_not_active",
            )

    return _resolution(
        ResolutionOutcome.APPLY,
        "rutcom_pickup_field",
        _build_pickup_event(
            existing,
            observation,
            reason="rutcom_pickup_field",
            new_pickup=str(new_pickup_raw),
            observation_kind="rutcom_pickup",
        ),
    )


def resolve_czasowka_initial_time_intent(
    existing: Mapping[str, object] | None,
    intent: Mapping[str, object] | None,
) -> CommittedPickupResolution:
    """Resolve one durable NEW_ORDER tuple into exactly one canonical event.

    The intent itself is the receipt-bound ON policy.  This function is pure
    and never re-reads runtime flags: an ON-to-OFF flip after NEW_ORDER cannot
    revive the raw CK writer or replace the original tuple with a later panel
    restamp.
    """
    existing = existing or {}
    oid = _clean_id(existing.get("order_id"))
    if not new_order_time_intent_is_valid(intent, order_id=oid):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "invalid_new_order_time_intent",
        )
    intent = dict(intent or {})
    intent_id = intent.get("intent_id")
    common = {
        "oid": oid,
        "courier_id": existing.get("courier_id"),
        "courier_id_at_observation": existing.get("courier_id"),
        "assignment_event_id_at_observation": existing.get(
            "assignment_event_id"
        ),
        "pickup_time_revision_at_observation": normalize_pickup_revision(
            existing.get("pickup_time_revision", 0)
        ),
        CK_CHANGE_REVISION_OBSERVATION_FIELD: normalize_pickup_revision(
            existing.get(CK_CHANGE_REVISION_STATE_FIELD, 0)
        ),
        "observed_at": intent.get("observed_at"),
        "observed_status_id": intent.get("status_id"),
        "observed_pickup_at_warsaw": intent.get("pickup_at_warsaw"),
        "observed_prep_minutes": intent.get("prep_minutes"),
        "observed_decision_deadline": intent.get("decision_deadline"),
        "new_zmiana_czasu_odbioru": intent.get(
            "zmiana_czasu_odbioru"
        ),
        NEW_ORDER_TIME_INTENT_ID_FIELD: intent_id,
    }
    ck_observation = {
        **common,
        "source": "first_acceptance",
        "old_ck_iso": existing.get("czas_kuriera_warsaw"),
        "old_ck_hhmm": existing.get("czas_kuriera_hhmm"),
        "new_ck_iso": intent.get("czas_kuriera_warsaw"),
        "new_ck_hhmm": intent.get("czas_kuriera_hhmm"),
    }
    ck_resolution = resolve_czasowka_committed_observation(
        existing,
        ck_observation,
        is_czasowka=True,
        passive_guard_enabled=True,
        manual_passthrough_enabled=False,
        rutcom_forward_authority_enabled=True,
    )
    if ck_resolution.outcome is ResolutionOutcome.APPLY:
        return ck_resolution

    pickup_observation = {
        **common,
        "source": "new_order_initial_intent",
        "old_ck_iso": existing.get("czas_kuriera_warsaw"),
        "old_ck_hhmm": existing.get("czas_kuriera_hhmm"),
        "new_ck_iso": intent.get("czas_kuriera_warsaw"),
        "new_ck_hhmm": intent.get("czas_kuriera_hhmm"),
        "new_pickup_at_warsaw": intent.get("pickup_at_warsaw"),
    }
    return resolve_czasowka_pickup_observation(
        existing,
        pickup_observation,
        is_czasowka=True,
    )


def validate_new_order_time_intent_event(
    current: Mapping[str, object] | None,
    event: Mapping[str, object] | None,
) -> bool:
    """Verify that an authority event is the exact pending NEW_ORDER intent."""
    if not isinstance(current, Mapping) or not isinstance(event, Mapping):
        return False
    intent = current.get(NEW_ORDER_TIME_INTENT_FIELD)
    payload = event.get("payload")
    if not isinstance(intent, Mapping) or not isinstance(payload, Mapping):
        return False
    if payload.get(NEW_ORDER_TIME_INTENT_ID_FIELD) != intent.get("intent_id"):
        return False
    resolution = resolve_czasowka_initial_time_intent(current, intent)
    return bool(
        resolution.outcome is ResolutionOutcome.APPLY
        and isinstance(resolution.event, Mapping)
        and dict(resolution.event) == dict(event)
    )


def _event_envelope_matches(
    event: Mapping[str, object],
    expected_event: Mapping[str, object],
    *,
    durable_attestation_verified: bool,
) -> bool:
    """Proof wiąże całą kopertę; transport może dodać tylko atestowany zestaw.

    Przed outboxem event musi być bajtowo równy eventowi z resolvera, łącznie z
    ``event_id_hint``. Po zapisie outboxa durable bridge zastępuje hint własnym
    ``event_id`` i dwoma markerami transportowymi. Są akceptowane wyłącznie,
    gdy exact rekord SQLite został już niezależnie zweryfikowany przez caller.
    Żaden alias lifecycle ani dowolne pole top-level nie może zostać przemycone.
    """
    if set(event) == set(expected_event):
        return dict(event) == dict(expected_event)
    if not durable_attestation_verified or set(event) != _DURABLE_EVENT_KEYS:
        return False
    if any(
        event.get(key) != expected_event.get(key)
        for key in _SEMANTIC_EVENT_KEYS
    ):
        return False
    return bool(
        isinstance(event.get("event_id"), str)
        and event.get("event_id")
        and isinstance(event.get("committed_authority_attestation"), Mapping)
        and isinstance(event.get("saved_plans_authorized"), bool)
        and isinstance(
            event.get("committed_invalidates_view_authorized"), bool
        )
        and isinstance(
            event.get("czasowka_reclaim_shadow_authorized"), bool
        )
        and isinstance(
            event.get("czasowka_reclaim_live_authorized"), bool
        )
    )


def validate_committed_pickup_event(
    existing: Mapping[str, object] | None,
    event: Mapping[str, object] | None,
    *,
    is_czasowka: bool,
    passive_guard_enabled: bool,
    manual_passthrough_enabled: bool,
    rutcom_forward_authority_enabled: bool,
    coordinator_receipt_verified: bool = False,
    durable_attestation_verified: bool = False,
) -> CommittedPickupResolution:
    """Zweryfikuj proof eventu; sama etykieta authority jest niewystarczajaca."""
    existing = existing or {}
    event = event or {}
    payload = event.get("payload")
    if not isinstance(payload, Mapping):
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_event_payload")
    authority = payload.get("committed_authority")
    if authority not in ALL_COMMITTED_AUTHORITIES:
        return _resolution(ResolutionOutcome.SUPPRESS, "unknown_authority")
    if not is_czasowka:
        return _resolution(ResolutionOutcome.SUPPRESS, "not_czasowka")
    if not passive_guard_enabled:
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "authority_requires_passive_guard",
        )
    if (
        authority == "rutcom_pickup_field"
        and not rutcom_forward_authority_enabled
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            "pickup_authority_off",
        )
    proof = payload.get("committed_authority_proof")
    if not isinstance(proof, Mapping):
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_authority_proof")
    if proof.get("schema") != AUTHORITY_PROOF_SCHEMA:
        return _resolution(ResolutionOutcome.SUPPRESS, "invalid_proof_schema")
    if proof.get("authority") != authority:
        return _resolution(ResolutionOutcome.SUPPRESS, "proof_authority_mismatch")
    oid = _clean_id(event.get("order_id") or payload.get("oid"))
    if _clean_id(proof.get("order_id")) != oid:
        return _resolution(ResolutionOutcome.SUPPRESS, "proof_order_mismatch")
    observation = proof.get("observation")
    if not isinstance(observation, Mapping):
        return _resolution(ResolutionOutcome.SUPPRESS, "missing_proof_observation")

    if authority == "rutcom_pickup_field":
        resolved = resolve_czasowka_pickup_observation(
            existing,
            observation,
            is_czasowka=is_czasowka,
            coordinator_receipt_verified=coordinator_receipt_verified,
        )
    else:
        resolved = resolve_czasowka_committed_observation(
            existing,
            observation,
            is_czasowka=is_czasowka,
            passive_guard_enabled=passive_guard_enabled,
            manual_passthrough_enabled=manual_passthrough_enabled,
            rutcom_forward_authority_enabled=(
                rutcom_forward_authority_enabled
            ),
            coordinator_receipt_verified=coordinator_receipt_verified,
        )
    if (
        resolved.outcome is not ResolutionOutcome.APPLY
        or resolved.reason != authority
        or not isinstance(resolved.event, Mapping)
    ):
        return _resolution(
            ResolutionOutcome.SUPPRESS,
            f"proof_policy_rejected:{resolved.reason}",
        )

    expected_event = resolved.event
    # Proof autoryzuje caly efekt, nie tylko etykiete i docelowy czas. Wiazemy
    # takze courier lane, prep/deadline/marker oraz brak nadmiarowych kluczy;
    # inaczej poprawny proof mozna byloby wykorzystac do przemytu innej mutacji.
    # ``event_id_hint`` jest transportowym inputem producenta i po zapisie
    # outboxu zostaje zastapiony kanonicznym ``event_id``; semantyczny klucz
    # pozostaje zwiazany w payloadzie i jest sprawdzany przez transport.
    if not _event_envelope_matches(
        event,
        expected_event,
        durable_attestation_verified=durable_attestation_verified,
    ):
        return _resolution(ResolutionOutcome.SUPPRESS, "proof_event_mismatch")
    return _resolution(ResolutionOutcome.APPLY, str(authority), dict(event))


def committed_pickup_effect_applied(
    current: Mapping[str, object] | None,
    payload: Mapping[str, object] | None,
) -> bool:
    """Exact postcondition: pickup, CK oraz cala provenance sa jednym efektem."""
    current = current or {}
    payload = payload or {}
    target = payload.get("new_pickup_at_warsaw")
    target_dt = _parse_aware(target)
    if target_dt is None:
        return False
    proof = payload.get("committed_authority_proof")
    proof_schema = proof.get("schema") if isinstance(proof, Mapping) else None
    observed_revision = normalize_pickup_revision(
        payload.get("pickup_time_revision_at_observation")
    )
    current_revision = normalize_pickup_revision(
        current.get("pickup_time_revision")
    )
    observed_ck_revision = normalize_pickup_revision(
        payload.get(CK_CHANGE_REVISION_OBSERVATION_FIELD)
    )
    current_ck_revision = normalize_pickup_revision(
        current.get(CK_CHANGE_REVISION_STATE_FIELD)
    )
    if (
        observed_revision is None
        or current_revision is None
        or observed_ck_revision is None
        or current_ck_revision is None
    ):
        return False
    for state_field, old_key, new_key in COMMITTED_PICKUP_COUPLED_FIELDS:
        if old_key not in payload or new_key not in payload:
            return False
        new_value = payload.get(new_key)
        expected = new_value if new_value is not None else payload.get(old_key)
        if current.get(state_field) != expected:
            return False
    return bool(
        current.get("pickup_at_warsaw") == target
        and current.get("czas_kuriera_warsaw") == target
        and current.get("czas_kuriera_hhmm") == target_dt.strftime("%H:%M")
        and current.get("committed_pickup_authority")
        == payload.get("committed_authority")
        and current.get("committed_pickup_observed_source")
        == payload.get("observed_source")
        and current.get("committed_pickup_observed_at")
        == payload.get("observed_at")
        and current.get("committed_pickup_authority_receipt_id")
        == payload.get("committed_authority_receipt_id")
        and current.get("committed_pickup_panel_baseline_at_observation")
        == payload.get("committed_pickup_panel_baseline_at_observation")
        and current.get("committed_ck_panel_baseline_at_observation")
        == payload.get("committed_ck_panel_baseline_at_observation")
        and current.get("committed_pickup_authority_proof_schema")
        == proof_schema
        and current.get("committed_pickup_event_key")
        == payload.get("committed_pickup_event_key")
        and (
            payload.get(NEW_ORDER_TIME_INTENT_ID_FIELD) is None
            or current.get(NEW_ORDER_TIME_INTENT_FIELD) is None
        )
        and current_revision == observed_revision + 1
        and current_ck_revision == observed_ck_revision + 1
    )


__all__ = [
    "ALL_COMMITTED_AUTHORITIES",
    "ASSIGNMENT_CK_FORWARD_SNAPSHOT_FIELD",
    "ASSIGNMENT_CK_PASSIVE_SNAPSHOT_FIELD",
    "AUTHORITY_PROOF_SCHEMA",
    "CK_CHANGE_REVISION_OBSERVATION_FIELD",
    "CK_CHANGE_REVISION_STATE_FIELD",
    "CK_DERIVED_AUTHORITIES",
    "COMMITTED_CK_DELTA_THRESHOLD_MIN",
    "COMMITTED_PICKUP_COUPLED_FIELDS",
    "COMMITTED_PICKUP_EVENT_ID_MARKER",
    "COMMITTED_PICKUP_STATE_FIELDS",
    "COMMITTED_TIME_POLICY_SNAPSHOT_FIELD",
    "COMMITTED_TIME_POLICY_SNAPSHOT_SCHEMA",
    "CommittedPickupPolicySnapshot",
    "CommittedPickupResolution",
    "NEW_ORDER_TIME_INTENT_FIELD",
    "NEW_ORDER_TIME_INTENT_ID_FIELD",
    "NEW_ORDER_TIME_INTENT_SCHEMA",
    "NEW_ORDER_TIME_AUTHORITY_SNAPSHOT_FIELD",
    "PASSIVE_CK_SOURCES",
    "RECEIPT_REQUIRED_PICKUP_SOURCES",
    "RETIRED_CZASOWKA_CK_ONLY_SOURCES",
    "ResolutionOutcome",
    "TIME_EVENT_CAS_SCHEMA",
    "TIME_EVENT_CAS_SCHEMA_FIELD",
    "build_time_event_cas_snapshot",
    "build_new_order_time_intent",
    "committed_pickup_effect_applied",
    "committed_pickup_event_id",
    "committed_time_contract_is_complete",
    "deserialize_committed_time_policy",
    "deserialize_coordinator_event_policy",
    "deserialize_coordinator_receipt_policy",
    "is_committed_pickup_artifact",
    "is_committed_pickup_outbox_artifact",
    "is_forward_authority_outbox_artifact",
    "normalize_pickup_revision",
    "new_order_time_intent_is_valid",
    "pickup_payload_requires_coordinator_receipt",
    "project_time_event_order",
    "project_time_observation_order",
    "resolve_czasowka_committed_observation",
    "resolve_czasowka_initial_time_intent",
    "resolve_czasowka_assignment_ck",
    "resolve_czasowka_pickup_observation",
    "state_has_committed_pickup_artifact",
    "serialize_committed_time_policy",
    "time_event_cas_artifact_present",
    "time_event_cas_is_versioned",
    "time_event_cas_status",
    "validate_committed_pickup_event",
    "validate_committed_time_policy_source",
    "validate_new_order_time_intent_event",
]
