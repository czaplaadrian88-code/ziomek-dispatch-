"""Transakcyjny store kopert i efektow A360-E1 (bez uruchomionego workera).

Modul dostarcza prymitywy publish/claim/attempt/ACK/recovery. Nie zawiera petli,
timera, backoffu ani wybranej polityki. Domyslny runtime pozostaje twardo OFF.
Efekt zewnetrzny przerwany pomiedzy wykonaniem a ACK przechodzi w
``effect_unknown``; nie udajemy exactly-once poza granica idempotentnego stanu.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

from dispatch_v2 import event_retry
from dispatch_v2.event_envelope import EventEnvelope, normalize_created_at
from dispatch_v2.migrations import durable_event_outbox


DURABLE_EVENT_OUTBOX_ENABLED = False
"""Twardy source default. Nie jest runtime flaga i nigdzie nie jest flipowany."""

STATE_CONSUMER = "order_state"


class DurableSchemaRequired(RuntimeError):
    pass


class RetentionPolicyRequired(RuntimeError):
    pass


class EnvelopeConflict(RuntimeError):
    pass


class ReceiptStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class RetentionContract:
    policy_id: str
    event_retention_seconds: int
    receipt_retention_seconds: int
    dedup_retention_seconds: int
    max_replay_age_seconds: int
    max_receipts_per_order: int
    status: str
    created_at: str

    def __post_init__(self) -> None:
        if not str(self.policy_id or "").strip():
            raise ValueError("policy_id is required")
        values = (
            self.event_retention_seconds,
            self.receipt_retention_seconds,
            self.dedup_retention_seconds,
            self.max_receipts_per_order,
        )
        if any(type(value) is not int or value <= 0 for value in values):
            raise ValueError("retention values and receipt capacity must be positive integers")
        if type(self.max_replay_age_seconds) is not int or self.max_replay_age_seconds < 0:
            raise ValueError("max_replay_age_seconds must be an integer >= 0")
        required_dedup = max(
            self.event_retention_seconds,
            self.receipt_retention_seconds,
        ) + self.max_replay_age_seconds
        if self.dedup_retention_seconds < required_dedup:
            raise ValueError("dedup retention is shorter than the replay-safe horizon")
        if self.status not in {"test_only", "approved", "retired"}:
            raise ValueError("unsupported retention policy status")
        object.__setattr__(self, "created_at", normalize_created_at(self.created_at))


@dataclass(frozen=True)
class EffectIntent:
    consumer_id: str
    effect_type: str
    depends_on_consumer: Optional[str]
    retry_contract: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if not self.consumer_id or not self.effect_type:
            raise ValueError("consumer_id and effect_type are required")
        if self.retry_contract not in {"idempotent", "confirm_before_retry"}:
            raise ValueError("unsupported retry_contract")


@dataclass(frozen=True)
class PublishResult:
    event_id: str
    inserted: bool
    duplicate: bool
    consumers: tuple[str, ...]


@dataclass(frozen=True)
class EffectClaim:
    envelope: EventEnvelope
    consumer_id: str
    effect_type: str
    effect_payload: dict[str, Any]
    effect_idempotency_key: str
    retry_contract: str
    worker_ref: str
    lease_expires_at: str


def _utc(value: datetime | str) -> datetime:
    normalized = normalize_created_at(value)
    return datetime.fromisoformat(normalized)


def _begin(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("durable outbox helper requires no active transaction")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")


def require_schema(conn: sqlite3.Connection) -> None:
    result = durable_event_outbox.inspect_connection(
        conn,
        verify_e0=False,
        verify_data=False,
    )
    if not result["ready"]:
        raise DurableSchemaRequired("durable event outbox migration is not complete")


def register_retention_contract(
    conn: sqlite3.Connection,
    contract: RetentionContract,
) -> bool:
    """Jawny insert kontraktu. Nie wybiera go i niczego nie aktywuje."""
    require_schema(conn)
    if contract.status == "test_only" and not os.environ.get("DISPATCH_UNDER_PYTEST"):
        raise RetentionPolicyRequired("test_only retention policy requires pytest isolation")
    _begin(conn)
    try:
        existing = conn.execute(
            "SELECT * FROM event_retention_policies WHERE policy_id=?",
            (contract.policy_id,),
        ).fetchone()
        values = (
            contract.policy_id,
            contract.event_retention_seconds,
            contract.receipt_retention_seconds,
            contract.dedup_retention_seconds,
            contract.max_replay_age_seconds,
            contract.max_receipts_per_order,
            contract.created_at,
            contract.status,
        )
        if existing is None:
            conn.execute(
                """INSERT INTO event_retention_policies(
                       policy_id,event_retention_seconds,receipt_retention_seconds,
                       dedup_retention_seconds,max_replay_age_seconds,
                       max_receipts_per_order,created_at,status
                   ) VALUES (?,?,?,?,?,?,?,?)""",
                values,
            )
            inserted = True
        else:
            actual = tuple(existing) if isinstance(existing, sqlite3.Row) else tuple(existing)
            if actual != values:
                raise EnvelopeConflict("retention policy ID has different content")
            inserted = False
        conn.execute("COMMIT")
        return inserted
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def load_retention_contract(
    conn: sqlite3.Connection,
    policy_id: str,
    *,
    allow_test_policy: bool = False,
) -> RetentionContract:
    require_schema(conn)
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM event_retention_policies WHERE policy_id=?",
            (str(policy_id),),
        ).fetchone()
    finally:
        conn.row_factory = old_factory
    if row is None:
        raise RetentionPolicyRequired("retention policy does not exist")
    contract = RetentionContract(**dict(row))
    if contract.status == "retired":
        raise RetentionPolicyRequired("retention policy is retired")
    if contract.status == "test_only" and not (
        allow_test_policy and os.environ.get("DISPATCH_UNDER_PYTEST")
    ):
        raise RetentionPolicyRequired("test_only retention policy is not runtime-approved")
    return contract


def default_effect_intents(envelope: EventEnvelope) -> tuple[EffectIntent, ...]:
    """Czysta macierz consumerow; state zawsze jest jedynym ownerem redukcji."""
    event = envelope.event_type
    intents: list[EffectIntent] = []
    state_events = {
        "NEW_ORDER",
        "COURIER_ASSIGNED",
        "COURIER_REJECTED_PROPOSAL",
        "COURIER_PICKED_UP",
        "COURIER_DELIVERED",
        "ORDER_RETURNED_TO_POOL",
        "ORDER_CANCELLED",
        "CZAS_KURIERA_UPDATED",
        "PICKUP_TIME_UPDATED",
        "ORDER_RESURRECTED",
    }
    # Czasowka re-emituje NEW_ORDER jako jawny sygnal re-evaluation dla shadow;
    # order istnieje juz w state. To nie jest drugi lifecycle writer.
    state_owned = not (
        event == "NEW_ORDER"
        and envelope.source.startswith("czasowka_scheduler:")
    )
    if event in state_events and state_owned:
        intents.append(EffectIntent(
            consumer_id=STATE_CONSUMER,
            effect_type="reduce_order_state",
            depends_on_consumer=None,
            retry_contract="idempotent",
            payload={"event_id": envelope.event_id},
        ))

    def downstream(consumer: str, effect: str) -> None:
        intents.append(EffectIntent(
            consumer_id=consumer,
            effect_type=effect,
            depends_on_consumer=(
                STATE_CONSUMER if event in state_events and state_owned else None
            ),
            retry_contract="confirm_before_retry",
            payload={"event_id": envelope.event_id},
        ))

    panel_source = envelope.source.startswith("panel_watcher:")
    if event == "NEW_ORDER":
        downstream("shadow_dispatch", "assess_new_order")
        if panel_source:
            downstream("auto_koord", "consider_auto_koord")
    elif event == "COURIER_ASSIGNED":
        downstream("coordinator_activation", "consider_coordinator_activation")
        if panel_source:
            downstream("plan", "plan_on_assignment")
            downstream("assignment_audit", "check_panel_assignment")
    elif event == "COURIER_PICKED_UP":
        downstream("sla", "record_pickup")
        if panel_source:
            downstream("plan", "plan_on_pickup")
    elif event == "COURIER_DELIVERED":
        downstream("sla", "record_delivery")
        downstream("delivery_geocode", "enrich_delivery_coordinates")
        if panel_source:
            downstream("plan", "plan_on_delivery")
    elif event in {"ORDER_RETURNED_TO_POOL", "ORDER_CANCELLED", "COURIER_REJECTED_PROPOSAL"}:
        if panel_source:
            downstream("plan", "plan_on_removal")
    elif event in {"CZAS_KURIERA_UPDATED", "PICKUP_TIME_UPDATED"}:
        if panel_source:
            downstream("plan", "plan_on_committed_change")
    return tuple(intents)


def _effect_key(envelope: EventEnvelope, consumer_id: str) -> str:
    raw = "\x1f".join((envelope.idempotency_key, consumer_id))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _canonical_intent_row(
    envelope: EventEnvelope,
    intent: EffectIntent,
) -> tuple[Any, ...]:
    effect_payload = json.dumps(
        intent.payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return (
        intent.consumer_id,
        intent.effect_type,
        effect_payload,
        _effect_key(envelope, intent.consumer_id),
        intent.depends_on_consumer,
        intent.retry_contract,
    )


def _envelope_tuple(envelope: EventEnvelope, delivery_kind: str, policy_id: str) -> tuple[Any, ...]:
    return (
        envelope.event_id,
        envelope.event_type,
        envelope.order_id,
        envelope.courier_id,
        envelope.payload_json,
        envelope.payload_sha256,
        envelope.created_at,
        envelope.source,
        envelope.envelope_version,
        envelope.policy_version,
        envelope.producer_key,
        envelope.identity_scheme,
        delivery_kind,
        policy_id,
    )


def _validate_intents(intents: Sequence[EffectIntent]) -> None:
    consumers = [intent.consumer_id for intent in intents]
    if len(consumers) != len(set(consumers)):
        raise ValueError("consumer IDs must be unique per event")
    known = set(consumers)
    for intent in intents:
        dependency = intent.depends_on_consumer
        if dependency and dependency not in known:
            raise ValueError("outbox dependency is not part of the event")
        if dependency == intent.consumer_id:
            raise ValueError("outbox consumer cannot depend on itself")


def _assert_event_status_topology(
    conn: sqlite3.Connection,
    event_id: str,
) -> None:
    """Fail-closed parity dla kompletnej pary outbox/receipt jednego eventu."""
    mismatch = conn.execute(
        """SELECT 1
           FROM event_outbox o
           LEFT JOIN event_consumer_receipts r
             ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
           WHERE o.event_id=?
             AND (r.event_id IS NULL OR r.status!=o.status)
           UNION ALL
           SELECT 1
           FROM event_consumer_receipts r
           LEFT JOIN event_outbox o
             ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
           WHERE r.event_id=? AND o.event_id IS NULL
           LIMIT 1""",
        (event_id, event_id),
    ).fetchone()
    if mismatch is not None:
        raise ReceiptStateError(
            "outbox and consumer receipt topology/status are inconsistent"
        )


def _assert_retention_integrity(
    conn: sqlite3.Connection,
    policy_id: str,
) -> None:
    """Pelny, tani wzgledem payloadu audit child topology przed DELETE."""
    missing_dedup = conn.execute(
        """SELECT 1
           FROM event_envelopes e
           LEFT JOIN event_dedup_ledger d ON d.event_id=e.event_id
           WHERE e.retention_policy_id=?
             AND (
                 d.event_id IS NULL
                 OR d.retention_policy_id!=e.retention_policy_id
             )
           LIMIT 1""",
        (policy_id,),
    ).fetchone()
    if missing_dedup is not None:
        raise EnvelopeConflict("retention encountered an incomplete dedup contract")
    mismatch = conn.execute(
        """SELECT 1
           FROM event_envelopes e
           JOIN event_outbox o ON o.event_id=e.event_id
           LEFT JOIN event_consumer_receipts r
             ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
           WHERE e.retention_policy_id=?
             AND (r.event_id IS NULL OR r.status!=o.status)
           UNION ALL
           SELECT 1
           FROM event_envelopes e
           JOIN event_consumer_receipts r ON r.event_id=e.event_id
           LEFT JOIN event_outbox o
             ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
           WHERE e.retention_policy_id=? AND o.event_id IS NULL
           LIMIT 1""",
        (policy_id, policy_id),
    ).fetchone()
    if mismatch is not None:
        raise ReceiptStateError(
            "retention encountered incomplete or divergent consumer topology"
        )
    terminal_mismatch = conn.execute(
        """SELECT 1
           FROM event_envelopes e
           JOIN event_dedup_ledger d ON d.event_id=e.event_id
           WHERE e.retention_policy_id=?
             AND (
                 (
                     d.terminal_at IS NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM event_consumer_receipts r
                         WHERE r.event_id=e.event_id
                           AND r.status NOT IN ('acknowledged','quarantined')
                     )
                 )
                 OR (
                     d.terminal_at IS NOT NULL
                     AND EXISTS (
                         SELECT 1 FROM event_consumer_receipts r
                         WHERE r.event_id=e.event_id
                           AND r.status NOT IN ('acknowledged','quarantined')
                     )
                 )
                 OR (
                     d.terminal_at IS NOT NULL
                     AND EXISTS (
                         SELECT 1 FROM event_consumer_attempts a
                         WHERE a.event_id=e.event_id AND a.outcome='executing'
                     )
                 )
             )
           LIMIT 1""",
        (policy_id,),
    ).fetchone()
    if terminal_mismatch is not None:
        raise ReceiptStateError("retention terminal marker/status invariant failed")


def _insert_legacy_compat(
    conn: sqlite3.Connection,
    envelope: EventEnvelope,
    *,
    delivery_kind: str,
    mirror_audit: bool,
) -> None:
    tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if delivery_kind == "queue":
        if "events" not in tables:
            raise DurableSchemaRequired("legacy events compatibility table is missing")
        columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(events)")
        }
        fields = [
            "event_id", "event_type", "order_id", "courier_id", "payload",
            "created_at", "status",
        ]
        values: list[Any] = [
            envelope.event_id,
            envelope.event_type,
            envelope.order_id,
            envelope.courier_id,
            envelope.payload_json,
            envelope.created_at,
            "durable",
        ]
        if "idempotency_key" in columns:
            fields.append("idempotency_key")
            values.append(envelope.idempotency_key)
        placeholders = ",".join("?" for _ in fields)
        conn.execute(
            f"INSERT INTO events({','.join(fields)}) VALUES ({placeholders})",
            tuple(values),
        )
    if delivery_kind == "audit" or mirror_audit:
        if "audit_log" not in tables:
            raise DurableSchemaRequired("legacy audit compatibility table is missing")
        conn.execute(
            """INSERT INTO audit_log(
                   event_id,event_type,order_id,courier_id,payload,created_at
               ) VALUES (?,?,?,?,?,?)""",
            (
                envelope.event_id,
                envelope.event_type,
                envelope.order_id,
                envelope.courier_id,
                envelope.payload_json,
                envelope.created_at,
            ),
        )


def publish(
    conn: sqlite3.Connection,
    envelope: EventEnvelope,
    *,
    delivery_kind: str,
    retention_policy_id: str,
    intents: Optional[Sequence[EffectIntent]] = None,
    allow_test_policy: bool = False,
    mirror_audit: bool = False,
    write_legacy_compat: bool = True,
) -> PublishResult:
    """Atomowo zapisuje envelope, dedup, outbox i receipts wszystkich consumerow."""
    if delivery_kind not in {"queue", "audit"}:
        raise ValueError("delivery_kind must be queue or audit")
    selected = tuple(intents) if intents is not None else default_effect_intents(envelope)
    _validate_intents(selected)

    _begin(conn)
    try:
        contract = load_retention_contract(
            conn,
            retention_policy_id,
            allow_test_policy=allow_test_policy,
        )
        expected = _envelope_tuple(envelope, delivery_kind, contract.policy_id)
        dedup_until = (
            _utc(envelope.created_at)
            + timedelta(seconds=contract.dedup_retention_seconds)
        ).isoformat()
        existing = conn.execute(
            """SELECT event_id,event_type,order_id,courier_id,payload,
                      payload_sha256,created_at,source,envelope_version,
                      policy_version,producer_key,identity_scheme,delivery_kind,
                      retention_policy_id
               FROM event_envelopes WHERE event_id=?""",
            (envelope.event_id,),
        ).fetchone()
        if existing is not None:
            # ``created_at`` rekordu jest czasem PIERWSZEJ przyjetej obserwacji.
            # Retry producenta po niepewnym wyniku commita moze nastapic pozniej,
            # ale nie moze nadpisac kanonicznej koperty. Wszystkie pozostale
            # pola (wlacznie z payloadem/source/policy) musza byc identyczne.
            existing_without_retry_time = tuple(existing[:6]) + tuple(existing[7:])
            expected_without_retry_time = expected[:6] + expected[7:]
            if existing_without_retry_time != expected_without_retry_time:
                raise EnvelopeConflict("event_id already has a different canonical envelope")
            actual_intents = tuple(
                tuple(row)
                for row in conn.execute(
                    """SELECT consumer_id,effect_type,effect_payload,
                              effect_idempotency_key,depends_on_consumer,
                              retry_contract
                       FROM event_outbox WHERE event_id=? ORDER BY consumer_id""",
                    (envelope.event_id,),
                ).fetchall()
            )
            expected_intents = tuple(sorted(
                (_canonical_intent_row(envelope, intent) for intent in selected),
                key=lambda row: row[0],
            ))
            if actual_intents != expected_intents:
                raise EnvelopeConflict(
                    "duplicate envelope has a different canonical intent topology"
                )
            expected_consumers = tuple(row[0] for row in expected_intents)
            actual_receipt_consumers = tuple(
                str(row[0])
                for row in conn.execute(
                    """SELECT consumer_id FROM event_consumer_receipts
                       WHERE event_id=? ORDER BY consumer_id""",
                    (envelope.event_id,),
                ).fetchall()
            )
            if actual_receipt_consumers != expected_consumers:
                raise EnvelopeConflict(
                    "duplicate envelope has an incomplete consumer receipt topology"
                )
            stored_created_at = str(existing[6])
            stored_dedup_until = (
                _utc(stored_created_at)
                + timedelta(seconds=contract.dedup_retention_seconds)
            ).isoformat()
            dedup_row = conn.execute(
                """SELECT idempotency_key,payload_sha256,first_seen_at,
                          dedup_until,retention_policy_id,terminal_at
                   FROM event_dedup_ledger WHERE event_id=?""",
                (envelope.event_id,),
            ).fetchone()
            expected_dedup = (
                envelope.idempotency_key,
                envelope.payload_sha256,
                stored_created_at,
                stored_dedup_until,
                contract.policy_id,
            )
            if dedup_row is None or tuple(dedup_row[:5]) != expected_dedup:
                raise EnvelopeConflict(
                    "duplicate envelope has an incomplete dedup contract"
                )
            _assert_event_status_topology(conn, envelope.event_id)
            terminal_at = dedup_row[5]
            nonterminal = int(conn.execute(
                """SELECT COUNT(*) FROM event_consumer_receipts
                   WHERE event_id=?
                     AND status NOT IN ('acknowledged','quarantined')""",
                (envelope.event_id,),
            ).fetchone()[0])
            if not selected:
                if terminal_at != stored_created_at:
                    raise EnvelopeConflict(
                        "zero-consumer duplicate has an invalid terminal marker"
                    )
            elif (nonterminal == 0) != (terminal_at is not None):
                raise EnvelopeConflict(
                    "duplicate envelope terminal marker contradicts receipts"
                )
            conn.execute("COMMIT")
            return PublishResult(
                envelope.event_id,
                False,
                True,
                expected_consumers,
            )

        dedup_conflict = conn.execute(
            "SELECT event_id FROM event_dedup_ledger WHERE idempotency_key=?",
            (envelope.idempotency_key,),
        ).fetchone()
        if dedup_conflict is not None:
            raise EnvelopeConflict("idempotency key belongs to another event")

        conn.execute(
            """INSERT INTO event_envelopes(
                   event_id,event_type,order_id,courier_id,payload,payload_sha256,
                   created_at,source,envelope_version,policy_version,producer_key,
                   identity_scheme,delivery_kind,retention_policy_id
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            expected,
        )
        conn.execute(
            """INSERT INTO event_dedup_ledger(
                   idempotency_key,event_id,payload_sha256,first_seen_at,
                   dedup_until,retention_policy_id,terminal_at
               ) VALUES (?,?,?,?,?,?,?)""",
            (
                envelope.idempotency_key,
                envelope.event_id,
                envelope.payload_sha256,
                envelope.created_at,
                dedup_until,
                contract.policy_id,
                envelope.created_at if not selected else None,
            ),
        )
        for intent in selected:
            (
                _consumer_id,
                _effect_type,
                effect_payload,
                effect_key,
                _dependency,
                _retry_contract,
            ) = _canonical_intent_row(envelope, intent)
            conn.execute(
                """INSERT INTO event_outbox(
                       event_id,consumer_id,effect_type,effect_payload,
                       effect_idempotency_key,depends_on_consumer,retry_contract,
                       status,created_at,available_at,updated_at
                   ) VALUES (?,?,?,?,?,?,?,'pending',?,?,?)""",
                (
                    envelope.event_id,
                    intent.consumer_id,
                    intent.effect_type,
                    effect_payload,
                    effect_key,
                    intent.depends_on_consumer,
                    intent.retry_contract,
                    envelope.created_at,
                    envelope.created_at,
                    envelope.created_at,
                ),
            )
            conn.execute(
                """INSERT INTO event_consumer_receipts(
                       event_id,consumer_id,status,attempt_count,updated_at
                   ) VALUES (?,?,'pending',0,?)""",
                (envelope.event_id, intent.consumer_id, envelope.created_at),
            )
        if write_legacy_compat:
            _insert_legacy_compat(
                conn,
                envelope,
                delivery_kind=delivery_kind,
                mirror_audit=mirror_audit,
            )
        conn.execute("COMMIT")
        return PublishResult(
            envelope.event_id,
            True,
            False,
            tuple(sorted(intent.consumer_id for intent in selected)),
        )
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def load_envelope(conn: sqlite3.Connection, event_id: str) -> EventEnvelope:
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM event_envelopes WHERE event_id=?", (event_id,)
        ).fetchone()
    finally:
        conn.row_factory = old_factory
    if row is None:
        raise KeyError("durable envelope not found")
    return EventEnvelope.from_record(dict(row))


def _claim_row(
    conn: sqlite3.Connection,
    *,
    event_id: Optional[str],
    consumer_id: str,
    worker_ref: str,
    claimed_at: datetime | str,
    lease_seconds: int,
) -> Optional[EffectClaim]:
    require_schema(conn)
    if type(lease_seconds) is not int or lease_seconds <= 0:
        raise ValueError("lease_seconds must be a positive integer")
    worker = str(worker_ref or "").strip()
    if not worker:
        raise ValueError("worker_ref is required")
    at = normalize_created_at(claimed_at)
    lease_until = (_utc(at) + timedelta(seconds=lease_seconds)).isoformat()
    params: list[Any] = [consumer_id, at]
    event_clause = ""
    if event_id is not None:
        event_clause = " AND o.event_id=?"
        params.append(event_id)
    _begin(conn)
    try:
        integrity_params: list[Any] = [consumer_id]
        outbox_event_clause = ""
        receipt_event_clause = ""
        if event_id is not None:
            outbox_event_clause = " AND o.event_id=?"
            receipt_event_clause = " AND r.event_id=?"
            integrity_params.append(event_id)
        integrity_params.append(consumer_id)
        if event_id is not None:
            integrity_params.append(event_id)
        mismatch = conn.execute(
            """SELECT event_id,outbox_status,receipt_status
               FROM (
                   SELECT o.event_id AS event_id,o.status AS outbox_status,
                          r.status AS receipt_status
                   FROM event_outbox o
                   LEFT JOIN event_consumer_receipts r
                     ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
                   WHERE o.consumer_id=?"""
            + outbox_event_clause
            + """ UNION ALL
                   SELECT r.event_id AS event_id,o.status AS outbox_status,
                          r.status AS receipt_status
                   FROM event_consumer_receipts r
                   LEFT JOIN event_outbox o
                     ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
                   WHERE r.consumer_id=? AND o.event_id IS NULL"""
            + receipt_event_clause
            + """
               )
               WHERE outbox_status IS NULL OR receipt_status IS NULL
                  OR outbox_status!=receipt_status
               LIMIT 1""",
            tuple(integrity_params),
        ).fetchone()
        if mismatch is not None:
            raise ReceiptStateError(
                "outbox and consumer receipt status are inconsistent"
            )
        row = conn.execute(
            """SELECT o.event_id,o.consumer_id,o.effect_type,o.effect_payload,
                      o.effect_idempotency_key,o.retry_contract
               FROM event_outbox o
               JOIN event_consumer_receipts r
                 ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
               JOIN event_envelopes e ON e.event_id=o.event_id
               WHERE o.consumer_id=? AND o.status='pending'
                 AND r.status='pending'
                 AND o.available_at<=?
                 AND (
                     o.depends_on_consumer IS NULL OR EXISTS (
                         SELECT 1 FROM event_consumer_receipts dep
                         WHERE dep.event_id=o.event_id
                           AND dep.consumer_id=o.depends_on_consumer
                           AND dep.status='acknowledged'
                     )
                 )"""
            + """ AND NOT EXISTS (
                     SELECT 1
                     FROM event_envelopes prior
                     JOIN event_consumer_receipts prior_receipt
                       ON prior_receipt.event_id=prior.event_id
                      AND prior_receipt.consumer_id=o.consumer_id
                     WHERE prior.order_id=e.order_id
                       AND (
                           prior.created_at<e.created_at OR
                           (prior.created_at=e.created_at
                            AND prior.event_id<e.event_id)
                       )
                       AND prior_receipt.status NOT IN (
                           'acknowledged','quarantined'
                       )
                 )"""
            + event_clause
            + " ORDER BY o.created_at,o.event_id LIMIT 1",
            tuple(params),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        eid = str(row[0])
        cur = conn.execute(
            """UPDATE event_outbox SET status='claimed',updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='pending'""",
            (at, eid, consumer_id),
        )
        if cur.rowcount != 1:
            raise ReceiptStateError("outbox claim compare-and-swap failed")
        receipt_cur = conn.execute(
            """UPDATE event_consumer_receipts
               SET status='claimed',lease_owner=?,lease_expires_at=?,updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='pending'""",
            (worker, lease_until, at, eid, consumer_id),
        )
        if receipt_cur.rowcount != 1:
            raise ReceiptStateError("receipt claim compare-and-swap failed")
        envelope = load_envelope(conn, eid)
        claim = EffectClaim(
            envelope=envelope,
            consumer_id=consumer_id,
            effect_type=str(row[2]),
            effect_payload=json.loads(row[3]),
            effect_idempotency_key=str(row[4]),
            retry_contract=str(row[5]),
            worker_ref=worker,
            lease_expires_at=lease_until,
        )
        conn.execute("COMMIT")
        return claim
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def claim_effect(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    consumer_id: str,
    worker_ref: str,
    claimed_at: datetime | str,
    lease_seconds: int,
) -> Optional[EffectClaim]:
    return _claim_row(
        conn,
        event_id=event_id,
        consumer_id=consumer_id,
        worker_ref=worker_ref,
        claimed_at=claimed_at,
        lease_seconds=lease_seconds,
    )


def begin_attempt(
    conn: sqlite3.Connection,
    claim: EffectClaim,
    *,
    started_at: datetime | str,
) -> int:
    """Zapis przed efektem. Crash po nim nie jest automatycznie uznany za retry-safe."""
    at = normalize_created_at(started_at)
    _begin(conn)
    try:
        _assert_event_status_topology(conn, claim.envelope.event_id)
        row = conn.execute(
            """SELECT status,attempt_count,lease_owner,lease_expires_at,
                      updated_at
               FROM event_consumer_receipts
               WHERE event_id=? AND consumer_id=?""",
            (claim.envelope.event_id, claim.consumer_id),
        ).fetchone()
        if row is None or row[0] != "claimed" or row[2] != claim.worker_ref:
            raise ReceiptStateError("receipt is not claimed by this worker")
        if str(row[3]) != claim.lease_expires_at:
            raise ReceiptStateError("effect claim lease token no longer owns receipt")
        if _utc(at) < _utc(str(row[4])):
            raise ReceiptStateError("attempt starts before the claim timestamp")
        if _utc(str(row[3])) <= _utc(at):
            raise ReceiptStateError("effect claim lease expired")
        attempt = int(row[1]) + 1
        conn.execute(
            """INSERT INTO event_consumer_attempts(
                   event_id,consumer_id,attempt_number,worker_ref,started_at,outcome
               ) VALUES (?,?,?,?,?,'executing')""",
            (
                claim.envelope.event_id,
                claim.consumer_id,
                attempt,
                claim.worker_ref,
                at,
            ),
        )
        receipt_cur = conn.execute(
            """UPDATE event_consumer_receipts
               SET status='executing',attempt_count=?,last_attempt_at=?,updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='claimed'
                 AND lease_owner=?""",
            (
                attempt,
                at,
                at,
                claim.envelope.event_id,
                claim.consumer_id,
                claim.worker_ref,
            ),
        )
        outbox_cur = conn.execute(
            """UPDATE event_outbox SET status='executing',updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='claimed'""",
            (at, claim.envelope.event_id, claim.consumer_id),
        )
        if receipt_cur.rowcount != 1 or outbox_cur.rowcount != 1:
            raise ReceiptStateError("attempt compare-and-swap failed")
        conn.execute("COMMIT")
        return attempt
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def acknowledge_effect(
    conn: sqlite3.Connection,
    claim: EffectClaim,
    *,
    attempt_number: int,
    acknowledged_at: datetime | str,
) -> bool:
    at = normalize_created_at(acknowledged_at)
    _begin(conn)
    try:
        _assert_event_status_topology(conn, claim.envelope.event_id)
        row = conn.execute(
            """SELECT r.status,r.attempt_count,r.lease_owner,r.lease_expires_at,
                      a.started_at,a.outcome,a.worker_ref
               FROM event_consumer_receipts r
               LEFT JOIN event_consumer_attempts a
                 ON a.event_id=r.event_id AND a.consumer_id=r.consumer_id
                AND a.attempt_number=?
               WHERE r.event_id=? AND r.consumer_id=?""",
            (
                int(attempt_number),
                claim.envelope.event_id,
                claim.consumer_id,
            ),
        ).fetchone()
        if row is None:
            raise ReceiptStateError("receipt is missing")
        if row[0] == "acknowledged":
            conn.execute("COMMIT")
            return False
        if (
            row[0] != "executing"
            or int(row[1]) != int(attempt_number)
            or row[2] != claim.worker_ref
            or row[3] != claim.lease_expires_at
            or row[4] is None
            or _utc(at) < _utc(str(row[4]))
            or row[5] != "executing"
            or row[6] != claim.worker_ref
        ):
            raise ReceiptStateError(
                "attempt does not own an executing receipt or time moved backwards"
            )
        attempt_cur = conn.execute(
            """UPDATE event_consumer_attempts
               SET outcome='acknowledged',finished_at=?
               WHERE event_id=? AND consumer_id=? AND attempt_number=?
                 AND outcome='executing'""",
            (at, claim.envelope.event_id, claim.consumer_id, attempt_number),
        )
        receipt_cur = conn.execute(
            """UPDATE event_consumer_receipts
               SET status='acknowledged',acknowledged_at=?,lease_owner=NULL,
                   lease_expires_at=NULL,last_error_code=NULL,updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='executing'
                 AND attempt_count=? AND lease_owner=?""",
            (
                at,
                at,
                claim.envelope.event_id,
                claim.consumer_id,
                int(attempt_number),
                claim.worker_ref,
            ),
        )
        outbox_cur = conn.execute(
            """UPDATE event_outbox
               SET status='acknowledged',updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='executing'""",
            (at, claim.envelope.event_id, claim.consumer_id),
        )
        if (
            attempt_cur.rowcount != 1
            or receipt_cur.rowcount != 1
            or outbox_cur.rowcount != 1
        ):
            raise ReceiptStateError("acknowledgement compare-and-swap failed")
        remaining = int(conn.execute(
            """SELECT COUNT(*) FROM event_consumer_receipts
               WHERE event_id=?
                 AND status NOT IN ('acknowledged','quarantined')""",
            (claim.envelope.event_id,),
        ).fetchone()[0])
        if remaining == 0:
            conn.execute(
                "UPDATE event_dedup_ledger SET terminal_at=? WHERE event_id=?",
                (at, claim.envelope.event_id),
            )
        conn.execute("COMMIT")
        return True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _failure_id(
    event_id: str,
    consumer_id: str,
    attempt_number: int,
    failed_at: str,
    error_code: str,
) -> str:
    raw = "\x1f".join(
        (event_id, consumer_id, str(attempt_number), failed_at, error_code)
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_failure(
    conn: sqlite3.Connection,
    *,
    claim: EffectClaim,
    error: Any,
    failed_at: datetime | str,
    disposition: str,
    attempt_number: int,
) -> bool:
    """Journal tylko aktualnego, nadal waznego executing attemptu."""
    if disposition not in {"failed", "effect_unknown", "quarantined"}:
        raise ValueError("unsupported failure disposition")
    descriptor = event_retry.classify_failure(error)
    at = normalize_created_at(failed_at)
    event_id = claim.envelope.event_id
    consumer_id = claim.consumer_id
    number = int(attempt_number)
    if number <= 0:
        raise ValueError("attempt_number must be positive")
    _begin(conn)
    try:
        _assert_event_status_topology(conn, event_id)
        row = conn.execute(
            """SELECT r.status,r.attempt_count,r.lease_owner,r.lease_expires_at,
                      a.started_at,a.outcome,a.worker_ref
               FROM event_consumer_receipts r
               LEFT JOIN event_consumer_attempts a
                 ON a.event_id=r.event_id AND a.consumer_id=r.consumer_id
                AND a.attempt_number=?
               WHERE r.event_id=? AND r.consumer_id=?""",
            (number, event_id, consumer_id),
        ).fetchone()
        if row is None:
            raise ReceiptStateError("failure consumer receipt is missing")
        if (
            row[0] != "executing"
            or int(row[1]) != number
            or row[2] != claim.worker_ref
            or row[3] != claim.lease_expires_at
            or row[4] is None
            or _utc(at) < _utc(str(row[4]))
            or row[5] != "executing"
            or row[6] != claim.worker_ref
        ):
            raise ReceiptStateError(
                "failure attempt is stale, unowned, missing, or time moved backwards"
            )
        failure_id = _failure_id(
            event_id, consumer_id, number, at, descriptor.error_code
        )
        cur = conn.execute(
            """INSERT OR IGNORE INTO event_failure_journal(
                   failure_id,event_id,consumer_id,attempt_number,failed_at,
                   failure_class,error_code,disposition
               ) VALUES (?,?,?,?,?,?,?,?)""",
            (
                failure_id,
                event_id,
                consumer_id,
                number,
                at,
                descriptor.failure_class.value,
                descriptor.error_code,
                disposition,
            ),
        )
        if cur.rowcount != 1:
            raise ReceiptStateError("failure journal compare-and-swap failed")
        if number > 0:
            attempt_cur = conn.execute(
                """UPDATE event_consumer_attempts
                   SET outcome=?,finished_at=?,error_code=?
                   WHERE event_id=? AND consumer_id=? AND attempt_number=?
                     AND outcome='executing'""",
                (
                    disposition if disposition != "quarantined" else "failed",
                    at,
                    descriptor.error_code,
                    event_id,
                    consumer_id,
                    number,
                ),
            )
        else:
            attempt_cur = None
        receipt_cur = conn.execute(
            """UPDATE event_consumer_receipts
               SET status=?,lease_owner=NULL,lease_expires_at=NULL,
                   last_error_code=?,updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='executing'
                 AND attempt_count=? AND lease_owner=?""",
            (
                disposition,
                descriptor.error_code,
                at,
                event_id,
                consumer_id,
                number,
                claim.worker_ref,
            ),
        )
        outbox_cur = conn.execute(
            """UPDATE event_outbox SET status=?,updated_at=?
               WHERE event_id=? AND consumer_id=? AND status='executing'""",
            (disposition, at, event_id, consumer_id),
        )
        if (
            attempt_cur is None
            or attempt_cur.rowcount != 1
            or receipt_cur.rowcount != 1
            or outbox_cur.rowcount != 1
        ):
            raise ReceiptStateError("failure compare-and-swap failed")
        if consumer_id == STATE_CONSUMER and disposition == "quarantined":
            # Efekty zalezne nie moga zostac wiecznie ``pending`` za receipt,
            # ktory nigdy nie dostanie ACK. Kazdy dostaje osobny terminalny
            # receipt i journal entry, bez udawania wykonania/attemptu.
            invalid_dependent = conn.execute(
                """SELECT 1
                   FROM event_outbox o
                   JOIN event_consumer_receipts r
                     ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
                   WHERE o.event_id=? AND o.depends_on_consumer=?
                     AND (o.status!='pending' OR r.status!='pending')
                   LIMIT 1""",
                (event_id, STATE_CONSUMER),
            ).fetchone()
            if invalid_dependent is not None:
                raise ReceiptStateError(
                    "state quarantine dependent is not an unclaimed pending pair"
                )
            dependents = conn.execute(
                """SELECT o.consumer_id
                   FROM event_outbox o
                   JOIN event_consumer_receipts r
                     ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
                   WHERE o.event_id=? AND o.depends_on_consumer=?
                     AND o.status='pending' AND r.status='pending'""",
                (event_id, STATE_CONSUMER),
            ).fetchall()
            for dependent_row in dependents:
                dependent = str(dependent_row[0])
                dependent_failure_id = _failure_id(
                    event_id,
                    dependent,
                    0,
                    at,
                    descriptor.error_code,
                )
                dependent_journal_cur = conn.execute(
                    """INSERT OR IGNORE INTO event_failure_journal(
                           failure_id,event_id,consumer_id,attempt_number,
                           failed_at,failure_class,error_code,disposition
                       ) VALUES (?,?,?,?,?,?,?,'quarantined')""",
                    (
                        dependent_failure_id,
                        event_id,
                        dependent,
                        0,
                        at,
                        descriptor.failure_class.value,
                        descriptor.error_code,
                    ),
                )
                receipt_cur = conn.execute(
                    """UPDATE event_consumer_receipts
                       SET status='quarantined',last_error_code=?,updated_at=?
                       WHERE event_id=? AND consumer_id=? AND status='pending'""",
                    (descriptor.error_code, at, event_id, dependent),
                )
                outbox_cur = conn.execute(
                    """UPDATE event_outbox SET status='quarantined',updated_at=?
                       WHERE event_id=? AND consumer_id=? AND status='pending'""",
                    (at, event_id, dependent),
                )
                if (
                    dependent_journal_cur.rowcount != 1
                    or receipt_cur.rowcount != 1
                    or outbox_cur.rowcount != 1
                ):
                    raise ReceiptStateError(
                        "dependent quarantine compare-and-swap failed"
                    )
        remaining = int(conn.execute(
            """SELECT COUNT(*) FROM event_consumer_receipts
               WHERE event_id=? AND status NOT IN ('acknowledged','quarantined')""",
            (event_id,),
        ).fetchone()[0])
        if remaining == 0:
            conn.execute(
                "UPDATE event_dedup_ledger SET terminal_at=? WHERE event_id=?",
                (at, event_id),
            )
        conn.execute("COMMIT")
        return True
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def recover_expired_claims(
    conn: sqlite3.Connection,
    *,
    recovered_at: datetime | str,
) -> dict[str, int]:
    """Crash recovery bez retry policy: claimed->pending; executing zalezy od kontraktu."""
    at = normalize_created_at(recovered_at)
    _begin(conn)
    counts = {"released": 0, "idempotent_requeued": 0, "effect_unknown": 0}
    try:
        pair_corruption = conn.execute(
            """SELECT 1
               FROM event_consumer_receipts r
               LEFT JOIN event_outbox o
                 ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
               WHERE r.status IN ('claimed','executing')
                 AND (o.event_id IS NULL OR o.status!=r.status)
               UNION ALL
               SELECT 1
               FROM event_outbox o
               LEFT JOIN event_consumer_receipts r
                 ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
               WHERE o.status IN ('claimed','executing')
                 AND (r.event_id IS NULL OR r.status!=o.status)
               LIMIT 1""",
        ).fetchone()
        if pair_corruption is not None:
            raise ReceiptStateError(
                "expired recovery found incomplete or divergent outbox/receipt pair"
            )
        lease_corruption = conn.execute(
            """SELECT 1
               FROM event_consumer_receipts r
               JOIN event_outbox o
                 ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
               WHERE r.status IN ('claimed','executing')
                 AND (
                     r.lease_expires_at IS NULL
                     OR r.lease_owner IS NULL OR trim(r.lease_owner)=''
                     OR (
                         r.status='executing'
                         AND (
                             NOT EXISTS (
                                 SELECT 1 FROM event_consumer_attempts a
                                 WHERE a.event_id=r.event_id
                                   AND a.consumer_id=r.consumer_id
                                   AND a.attempt_number=r.attempt_count
                                   AND a.outcome='executing'
                                   AND a.worker_ref=r.lease_owner
                             )
                             OR EXISTS (
                                 SELECT 1 FROM event_consumer_attempts other
                                 WHERE other.event_id=r.event_id
                                   AND other.consumer_id=r.consumer_id
                                   AND other.outcome='executing'
                                   AND other.attempt_number!=r.attempt_count
                             )
                         )
                     )
                     OR (
                         r.status='claimed'
                         AND EXISTS (
                             SELECT 1 FROM event_consumer_attempts a
                             WHERE a.event_id=r.event_id
                               AND a.consumer_id=r.consumer_id
                               AND a.outcome='executing'
                         )
                     )
                 )
               LIMIT 1""",
        ).fetchone()
        if lease_corruption is not None:
            raise ReceiptStateError(
                "expired recovery found a missing, stale, or unowned attempt"
            )
        rows = conn.execute(
            """SELECT r.event_id,r.consumer_id,r.status,r.attempt_count,
                      r.lease_owner,r.lease_expires_at,o.retry_contract
               FROM event_consumer_receipts r
               JOIN event_outbox o
                 ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
               WHERE r.status IN ('claimed','executing')
                 AND r.lease_expires_at IS NOT NULL
                 AND r.lease_expires_at<=?""",
            (at,),
        ).fetchall()
        for (
            event_id,
            consumer_id,
            status,
            attempt_count,
            lease_owner,
            lease_expires_at,
            retry_contract,
        ) in rows:
            attempt_cur = None
            if status == "claimed":
                target = "pending"
                count_key = "released"
            elif retry_contract == "idempotent":
                target = "pending"
                count_key = "idempotent_requeued"
                attempt_cur = conn.execute(
                    """UPDATE event_consumer_attempts
                       SET outcome='effect_unknown',finished_at=?,error_code='unexpected_failure'
                       WHERE event_id=? AND consumer_id=? AND attempt_number=?
                         AND outcome='executing' AND worker_ref=?""",
                    (
                        at,
                        event_id,
                        consumer_id,
                        int(attempt_count),
                        lease_owner,
                    ),
                )
            else:
                target = "effect_unknown"
                count_key = "effect_unknown"
                descriptor = event_retry.FailureDescriptor(
                    event_retry.FailureClass.PERMANENT, "unexpected_failure"
                )
                failure_id = _failure_id(
                    str(event_id), str(consumer_id), int(attempt_count), at,
                    descriptor.error_code,
                )
                journal_cur = conn.execute(
                    """INSERT OR IGNORE INTO event_failure_journal(
                           failure_id,event_id,consumer_id,attempt_number,failed_at,
                           failure_class,error_code,disposition
                       ) VALUES (?,?,?,?,?,?,?,'effect_unknown')""",
                    (
                        failure_id,
                        event_id,
                        consumer_id,
                        int(attempt_count),
                        at,
                        descriptor.failure_class.value,
                        descriptor.error_code,
                    ),
                )
                if journal_cur.rowcount != 1:
                    raise ReceiptStateError(
                        "expired recovery failure journal compare-and-swap failed"
                    )
                attempt_cur = conn.execute(
                    """UPDATE event_consumer_attempts
                       SET outcome='effect_unknown',finished_at=?,error_code=?
                       WHERE event_id=? AND consumer_id=? AND attempt_number=?
                         AND outcome='executing' AND worker_ref=?""",
                    (
                        at,
                        descriptor.error_code,
                        event_id,
                        consumer_id,
                        int(attempt_count),
                        lease_owner,
                    ),
                )
            if status == "executing" and (
                attempt_cur is None or attempt_cur.rowcount != 1
            ):
                raise ReceiptStateError(
                    "expired recovery attempt compare-and-swap failed"
                )
            receipt_cur = conn.execute(
                """UPDATE event_consumer_receipts
                   SET status=?,lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                   WHERE event_id=? AND consumer_id=? AND status=?
                     AND attempt_count=? AND lease_owner=?
                     AND lease_expires_at=?""",
                (
                    target,
                    at,
                    event_id,
                    consumer_id,
                    status,
                    int(attempt_count),
                    lease_owner,
                    lease_expires_at,
                ),
            )
            outbox_cur = conn.execute(
                """UPDATE event_outbox SET status=?,updated_at=?
                   WHERE event_id=? AND consumer_id=? AND status=?""",
                (target, at, event_id, consumer_id, status),
            )
            if receipt_cur.rowcount != 1 or outbox_cur.rowcount != 1:
                raise ReceiptStateError(
                    "expired recovery outbox/receipt compare-and-swap failed"
                )
            counts[count_key] += 1
        conn.execute("COMMIT")
        return counts
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def retention_candidates(
    conn: sqlite3.Connection,
    *,
    policy_id: str,
    evaluated_at: datetime | str,
    allow_test_policy: bool = False,
) -> list[str]:
    """Read-only lista bezpiecznych do kompaktowania eventow sukcesu."""
    contract = load_retention_contract(
        conn, policy_id, allow_test_policy=allow_test_policy
    )
    _assert_retention_integrity(conn, contract.policy_id)
    now_iso = normalize_created_at(evaluated_at)
    event_cutoff = (
        _utc(now_iso) - timedelta(seconds=contract.event_retention_seconds)
    ).isoformat()
    rows = conn.execute(
        """SELECT e.event_id
           FROM event_envelopes e
           JOIN event_dedup_ledger d ON d.event_id=e.event_id
           WHERE e.retention_policy_id=?
             AND e.created_at<=? AND d.dedup_until<=?
             AND d.terminal_at IS NOT NULL
             AND NOT EXISTS (
                 SELECT 1
                 FROM event_outbox o
                 LEFT JOIN event_consumer_receipts r
                   ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
                 WHERE o.event_id=e.event_id
                   AND (
                       r.event_id IS NULL OR r.status!=o.status
                       OR o.status NOT IN ('acknowledged','quarantined')
                   )
             )
             AND NOT EXISTS (
                 SELECT 1
                 FROM event_consumer_receipts r
                 LEFT JOIN event_outbox o
                   ON o.event_id=r.event_id AND o.consumer_id=r.consumer_id
                 WHERE r.event_id=e.event_id AND o.event_id IS NULL
             )
             AND NOT EXISTS (
                 SELECT 1 FROM event_failure_journal f
                 WHERE f.event_id=e.event_id
             )
             AND NOT EXISTS (
                 SELECT 1 FROM event_outbox own_state
                 WHERE own_state.event_id=e.event_id
                   AND own_state.consumer_id='order_state'
             )
           ORDER BY e.created_at,e.event_id""",
        (
            contract.policy_id,
            event_cutoff,
            now_iso,
        ),
    ).fetchall()
    return [str(row[0]) for row in rows]


def compact_retention(
    conn: sqlite3.Connection,
    *,
    policy_id: str,
    evaluated_at: datetime | str,
    enabled: bool = False,
    allow_test_policy: bool = False,
) -> int:
    """Jawny opt-in; default no-op. Failure journal nigdy nie jest kasowany."""
    candidates = retention_candidates(
        conn,
        policy_id=policy_id,
        evaluated_at=evaluated_at,
        allow_test_policy=allow_test_policy,
    )
    if not enabled or not candidates:
        return 0
    _begin(conn)
    try:
        # Revalidate pod ta sama blokada co DELETE. Dry-run lista mogla sie
        # zestarzec pomiedzy SELECT a BEGIN IMMEDIATE.
        locked_candidates = set(retention_candidates(
            conn,
            policy_id=policy_id,
            evaluated_at=evaluated_at,
            allow_test_policy=allow_test_policy,
        ))
        selected = [event_id for event_id in candidates if event_id in locked_candidates]
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        for event_id in selected:
            outbox_count = int(conn.execute(
                "SELECT COUNT(*) FROM event_outbox WHERE event_id=?",
                (event_id,),
            ).fetchone()[0])
            receipt_count = int(conn.execute(
                "SELECT COUNT(*) FROM event_consumer_receipts WHERE event_id=?",
                (event_id,),
            ).fetchone()[0])
            if outbox_count != receipt_count:
                raise ReceiptStateError(
                    "retention child count changed after candidate validation"
                )
            conn.execute(
                "DELETE FROM event_consumer_attempts WHERE event_id=?",
                (event_id,),
            )
            receipt_cur = conn.execute(
                "DELETE FROM event_consumer_receipts WHERE event_id=?",
                (event_id,),
            )
            if receipt_cur.rowcount != receipt_count:
                raise ReceiptStateError(
                    "retention receipt delete compare-and-swap failed"
                )
            dependent_cur = conn.execute(
                """DELETE FROM event_outbox
                   WHERE event_id=? AND depends_on_consumer IS NOT NULL""",
                (event_id,),
            )
            root_cur = conn.execute(
                """DELETE FROM event_outbox
                   WHERE event_id=? AND depends_on_consumer IS NULL""",
                (event_id,),
            )
            if (
                dependent_cur.rowcount + root_cur.rowcount != outbox_count
            ):
                raise ReceiptStateError(
                    "retention child delete compare-and-swap failed"
                )
            dedup_cur = conn.execute(
                "DELETE FROM event_dedup_ledger WHERE event_id=?",
                (event_id,),
            )
            if dedup_cur.rowcount != 1:
                raise EnvelopeConflict("retention dedup delete compare-and-swap failed")
            if "events" in tables:
                conn.execute(
                    "DELETE FROM events WHERE event_id=? AND status='durable'",
                    (event_id,),
                )
            if "audit_log" in tables:
                conn.execute("DELETE FROM audit_log WHERE event_id=?", (event_id,))
            envelope_cur = conn.execute(
                "DELETE FROM event_envelopes WHERE event_id=?",
                (event_id,),
            )
            if envelope_cur.rowcount != 1:
                raise EnvelopeConflict("retention envelope delete compare-and-swap failed")
        conn.execute("COMMIT")
        return len(selected)
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def receipt_status(
    conn: sqlite3.Connection,
    event_id: str,
    consumer_id: str,
) -> Optional[dict[str, Any]]:
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT * FROM event_consumer_receipts
               WHERE event_id=? AND consumer_id=?""",
            (event_id, consumer_id),
        ).fetchone()
    finally:
        conn.row_factory = old_factory
    return dict(row) if row is not None else None


def list_failure_journal(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    consumer_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Read-only, zredagowany widok durable failure journal.

    Surowy event ID pozostaje w bazie jako klucz relacyjny. Widok operatorski
    zwraca tylko stabilny digest korelacyjny oraz zamkniete metadata; nie
    uruchamia requeue ani nie modyfikuje receiptow.
    """
    require_schema(conn)
    capped_limit = max(1, min(int(limit), 1000))
    params: list[Any] = []
    clause = ""
    if consumer_id is not None:
        consumer = str(consumer_id or "").strip()
        if not consumer:
            raise ValueError("consumer_id filter cannot be empty")
        clause = " WHERE consumer_id=?"
        params.append(consumer)
    params.append(capped_limit)
    old_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT event_id,consumer_id,attempt_number,failed_at,
                      failure_class,error_code,disposition
               FROM event_failure_journal"""
            + clause
            + " ORDER BY failed_at,event_id,consumer_id LIMIT ?",
            tuple(params),
        ).fetchall()
    finally:
        conn.row_factory = old_factory
    return [
        {
            "event_ref": event_retry.event_reference(row["event_id"]),
            "consumer_id": str(row["consumer_id"]),
            "attempt_number": max(0, int(row["attempt_number"])),
            "failed_at": str(row["failed_at"]),
            "failure_class": str(row["failure_class"]),
            "error_code": str(row["error_code"]),
            "disposition": str(row["disposition"]),
        }
        for row in rows
    ]


__all__ = [
    "DURABLE_EVENT_OUTBOX_ENABLED",
    "STATE_CONSUMER",
    "DurableSchemaRequired",
    "EffectClaim",
    "EffectIntent",
    "EnvelopeConflict",
    "PublishResult",
    "ReceiptStateError",
    "RetentionContract",
    "RetentionPolicyRequired",
    "acknowledge_effect",
    "begin_attempt",
    "claim_effect",
    "compact_retention",
    "default_effect_intents",
    "load_envelope",
    "load_retention_contract",
    "list_failure_journal",
    "publish",
    "receipt_status",
    "record_failure",
    "recover_expired_claims",
    "register_retention_contract",
    "require_schema",
    "retention_candidates",
]
