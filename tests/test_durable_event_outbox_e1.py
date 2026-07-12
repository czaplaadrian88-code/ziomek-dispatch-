"""A360-E1: syntetyczny oracle envelope/outbox/receipts/crash/retention."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from dispatch_v2 import event_bus, event_outbox, replay_dead_letter
from dispatch_v2 import state_machine as sm
from dispatch_v2.event_envelope import (
    ENVELOPE_VERSION,
    IDENTITY_SCHEME,
    EnvelopeValidationError,
    EventEnvelope,
    canonical_payload_json,
    event_id_after_state_revision,
)
from dispatch_v2.migrations import durable_event_outbox, event_retry_metadata
from dispatch_v2.order_event_reducer import (
    ReceiptCapacityExceeded,
    ReductionRejected,
    compact_receipts,
    reduce_order_event,
)
from dispatch_v2.reconciliation import auto_resync, phantom_detector
from dispatch_v2.tools import rebuild_state_from_events


UTC = timezone.utc
T0 = datetime(2026, 7, 12, 9, 0, tzinfo=UTC)


def _legacy_db(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT NOT NULL,
            processed_at TEXT,
            status TEXT DEFAULT 'pending'
        );
        CREATE TABLE processed_events (
            event_id TEXT PRIMARY KEY,
            processed_at TEXT NOT NULL
        );
        CREATE TABLE audit_log (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()


def _contract(
    policy_id: str = "synthetic-retention-v1",
    *,
    max_receipts: int = 8,
    event_seconds: int = 60,
    receipt_seconds: int = 60,
    dedup_seconds: int = 180,
    replay_seconds: int = 60,
) -> event_outbox.RetentionContract:
    return event_outbox.RetentionContract(
        policy_id=policy_id,
        event_retention_seconds=event_seconds,
        receipt_retention_seconds=receipt_seconds,
        dedup_retention_seconds=dedup_seconds,
        max_replay_age_seconds=replay_seconds,
        max_receipts_per_order=max_receipts,
        status="test_only",
        created_at=T0.isoformat(),
    )


@pytest.fixture
def durable_store(monkeypatch, tmp_path):
    monkeypatch.setenv("DISPATCH_UNDER_PYTEST", "1")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "orders_state.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(state_dir))

    db_path = tmp_path / "events.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_retry_metadata.apply_to_connection(conn)
    durable_event_outbox.apply_to_connection(conn)
    contract = _contract()
    event_outbox.register_retention_contract(conn, contract)
    conn.close()

    monkeypatch.setattr(event_bus, "_db_path", lambda: str(db_path))
    monkeypatch.setattr(event_bus, "_audit_log_initialized", True)
    monkeypatch.setattr(event_outbox, "DURABLE_EVENT_OUTBOX_ENABLED", False)
    return state_dir, db_path, contract


def _payload(event_type: str) -> dict:
    return {
        "NEW_ORDER": {"restaurant": "Synthetic R", "delivery_address": "Synthetic D"},
        "COURIER_ASSIGNED": {"source": "panel_diff"},
        "COURIER_PICKED_UP": {"timestamp": "2026-07-12T09:02:00+00:00"},
        "COURIER_DELIVERED": {"timestamp": "2026-07-12T09:03:00+00:00"},
        "ORDER_RETURNED_TO_POOL": {"reason": "synthetic", "source": "reconcile"},
        "PANEL_UNREACHABLE": {"failure_count": 3},
    }[event_type]


def _envelope(
    event_type: str,
    suffix: str,
    *,
    created_at: datetime | None = None,
    payload: dict | None = None,
    order_id: str | None = "synthetic-order",
    courier_id: str | None = None,
    source: str = "synthetic_producer",
) -> EventEnvelope:
    if courier_id is None and event_type in {
        "COURIER_ASSIGNED",
        "COURIER_PICKED_UP",
    }:
        courier_id = "synthetic-courier"
    event_id = f"synthetic:{event_type}:{suffix}"
    return EventEnvelope.from_parts(
        event_id=event_id,
        event_type=event_type,
        order_id=order_id,
        courier_id=courier_id,
        payload=payload if payload is not None else _payload(event_type),
        created_at=created_at or T0,
        source=source,
        envelope_version=ENVELOPE_VERSION,
        policy_version="order_fsm.synthetic.v1",
        producer_key=event_id,
        identity_scheme=IDENTITY_SCHEME,
    )


def _state_only(envelope: EventEnvelope) -> tuple[event_outbox.EffectIntent, ...]:
    return (
        event_outbox.EffectIntent(
            consumer_id=event_outbox.STATE_CONSUMER,
            effect_type="reduce_order_state",
            depends_on_consumer=None,
            retry_contract="idempotent",
            payload={"event_id": envelope.event_id},
        ),
    )


def _publish(
    conn: sqlite3.Connection,
    envelope: EventEnvelope,
    contract: event_outbox.RetentionContract,
    *,
    intents=None,
    delivery_kind: str = "queue",
):
    return event_outbox.publish(
        conn,
        envelope,
        delivery_kind=delivery_kind,
        retention_policy_id=contract.policy_id,
        intents=intents,
        allow_test_policy=True,
        write_legacy_compat=True,
    )


def _claim(
    conn: sqlite3.Connection,
    envelope: EventEnvelope,
    consumer: str,
    *,
    worker: str = "synthetic-worker-a",
    at: datetime | None = None,
):
    default_at = datetime.fromisoformat(envelope.created_at) + timedelta(seconds=1)
    return event_outbox.claim_effect(
        conn,
        event_id=envelope.event_id,
        consumer_id=consumer,
        worker_ref=worker,
        claimed_at=at or default_at,
        lease_seconds=10,
    )


def _apply_state(
    conn: sqlite3.Connection,
    envelope: EventEnvelope,
    contract: event_outbox.RetentionContract,
    *,
    worker: str = "synthetic-worker-a",
    at: datetime | None = None,
    hook=None,
):
    claim_at = at or (datetime.fromisoformat(envelope.created_at) + timedelta(seconds=1))
    if isinstance(claim_at, str):
        claim_at = datetime.fromisoformat(claim_at)
    claim = _claim(conn, envelope, event_outbox.STATE_CONSUMER, worker=worker, at=claim_at)
    assert claim is not None
    return sm.commit_durable_state_claim(
        conn,
        claim,
        retention_contract=contract,
        started_at=claim_at + timedelta(seconds=1),
        acknowledged_at=claim_at + timedelta(seconds=2),
        after_state_hook=hook,
    )


def test_envelope_requires_explicit_identity_and_aware_time():
    with pytest.raises(EnvelopeValidationError, match="created_at"):
        EventEnvelope.from_parts(
            event_id="synthetic-id",
            event_type="NEW_ORDER",
            order_id="synthetic-order",
            courier_id=None,
            payload=_payload("NEW_ORDER"),
            created_at=None,
            source="synthetic",
            envelope_version=ENVELOPE_VERSION,
            policy_version="synthetic.v1",
            producer_key="synthetic-key",
            identity_scheme=IDENTITY_SCHEME,
        )
    with pytest.raises(EnvelopeValidationError, match="timezone"):
        _envelope("NEW_ORDER", "naive", created_at=datetime(2026, 7, 12, 9, 0))

    payload = _payload("NEW_ORDER")
    digest = hashlib.sha256(canonical_payload_json(payload).encode()).hexdigest()
    with pytest.raises(EnvelopeValidationError, match="content hash"):
        EventEnvelope.from_parts(
            event_id=digest,
            event_type="NEW_ORDER",
            order_id="synthetic-order",
            courier_id=None,
            payload=payload,
            created_at=T0,
            source="synthetic",
            envelope_version=ENVELOPE_VERSION,
            policy_version="synthetic.v1",
            producer_key=digest,
            identity_scheme=IDENTITY_SCHEME,
        )


def test_envelope_roundtrip_has_no_hydration_fallback():
    envelope = _envelope("NEW_ORDER", "roundtrip")
    assert EventEnvelope.from_record(envelope.as_event()) == envelope
    missing = envelope.as_event()
    missing.pop("created_at")
    with pytest.raises(EnvelopeValidationError, match="created_at"):
        EventEnvelope.from_record(missing)


def test_revision_qualified_source_identity_is_stable_and_requires_backfill():
    with pytest.raises(EnvelopeValidationError, match="durable state revision"):
        event_id_after_state_revision("synthetic_ASSIGN", {"status": "planned"})
    first_state = {
        "durable_last_event_order": {
            "created_at": T0.isoformat(),
            "event_id": "synthetic-prior-event",
        }
    }
    first = event_id_after_state_revision("synthetic_ASSIGN", first_state)
    assert first == event_id_after_state_revision("synthetic_ASSIGN", first_state)
    assert first.startswith("synthetic_ASSIGN:after:")
    second_state = {
        "durable_last_event_order": {
            "created_at": (T0 + timedelta(minutes=1)).isoformat(),
            "event_id": "synthetic-second-prior-event",
        }
    }
    assert event_id_after_state_revision("synthetic_ASSIGN", second_state) != first


def test_migration_dry_run_apply_twice_and_conflict_rollback(tmp_path):
    db_path = tmp_path / "migration.db"
    _legacy_db(db_path)
    before = db_path.read_bytes()
    plan = durable_event_outbox.inspect(str(db_path))
    assert db_path.read_bytes() == before
    assert plan["ready"] is False
    assert plan["e0_prerequisite_ready"] is False
    assert plan["retention_policy_count"] == 0

    conn = sqlite3.connect(db_path, isolation_level=None)
    with pytest.raises(RuntimeError, match="E0"):
        durable_event_outbox.apply_to_connection(conn)
    event_retry_metadata.apply_to_connection(conn)
    durable_event_outbox.apply_to_connection(conn)
    assert durable_event_outbox.inspect_connection(conn)["ready"] is True
    second = durable_event_outbox.apply_to_connection(conn)
    assert second["before"]["ready"] is True
    conn.close()

    conflict = tmp_path / "conflict.db"
    _legacy_db(conflict)
    conn = sqlite3.connect(conflict, isolation_level=None)
    event_retry_metadata.apply_to_connection(conn)
    conn.execute("CREATE TABLE event_envelopes(event_id TEXT PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="incompatible"):
        durable_event_outbox.apply_to_connection(conn)
    tables = {
        row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "event_outbox" not in tables
    conn.close()


@pytest.mark.parametrize(
    ("table", "removed", "replacement", "contract_part"),
    (
        (
            "event_retention_policies",
            "policy_id TEXT PRIMARY KEY",
            "policy_id TEXT",
            "primary_key",
        ),
        (
            "event_retention_policies",
            "CHECK(event_retention_seconds > 0)",
            "",
            "check_constraint",
        ),
        (
            "event_envelopes",
            ",\n            FOREIGN KEY(retention_policy_id)\n"
            "                REFERENCES event_retention_policies(policy_id) "
            "ON DELETE RESTRICT",
            "",
            "foreign_key",
        ),
    ),
)
def test_migration_rejects_same_columns_without_full_ddl_contract(
    tmp_path,
    table,
    removed,
    replacement,
    contract_part,
):
    db_path = tmp_path / f"same-columns-{contract_part}.db"
    _legacy_db(db_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_retry_metadata.apply_to_connection(conn)
    statement = dict(durable_event_outbox.TABLE_STATEMENTS)[table]
    assert removed in statement
    mutated = statement.replace(removed, replacement, 1)
    conn.execute(mutated)
    actual_columns = tuple(
        str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')
    )
    assert actual_columns == durable_event_outbox.EXPECTED_COLUMNS[table]

    inspection = durable_event_outbox.inspect_connection(conn)
    assert inspection["ready"] is False
    assert table in inspection["invalid_tables"]
    assert "expected_ddl" in inspection["invalid_tables"][table]
    with pytest.raises(RuntimeError, match="incompatible"):
        durable_event_outbox.apply_to_connection(conn)
    assert conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='event_outbox'"
    ).fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "mutated_index_sql",
    (
        "CREATE UNIQUE INDEX idx_event_dedup_expiry "
        "ON event_dedup_ledger(dedup_until,event_id)",
        "CREATE INDEX idx_event_dedup_expiry "
        "ON event_dedup_ledger(dedup_until DESC,event_id)",
        "CREATE INDEX idx_event_dedup_expiry "
        "ON event_dedup_ledger(dedup_until COLLATE NOCASE,event_id)",
        "CREATE INDEX idx_event_dedup_expiry "
        "ON event_dedup_ledger(dedup_until,event_id) "
        "WHERE dedup_until IS NOT NULL",
    ),
)
def test_migration_rejects_same_index_columns_with_different_semantics(
    durable_store,
    mutated_index_sql,
):
    _state_dir, db_path, _contract = durable_store
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("DROP INDEX idx_event_dedup_expiry")
    conn.execute(mutated_index_sql)
    assert tuple(
        str(row[2])
        for row in conn.execute('PRAGMA index_info("idx_event_dedup_expiry")')
    ) == ("dedup_until", "event_id")
    inspection = durable_event_outbox.inspect_connection(conn)
    assert inspection["ready"] is False
    invalid = inspection["invalid_indexes"]["idx_event_dedup_expiry"]
    assert invalid["expected_ddl"] != invalid["actual_ddl"]
    with pytest.raises(RuntimeError, match="incompatible"):
        durable_event_outbox.apply_to_connection(conn)
    conn.close()


@pytest.mark.parametrize("orphan_kind", ("outbox", "receipt"))
def test_migration_rejects_redacted_preexisting_foreign_key_orphan(
    durable_store,
    orphan_kind,
):
    _state_dir, db_path, _contract = durable_store
    marker = "SYNTHETIC-ORPHAN-EVENT-MARKER"
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute("PRAGMA foreign_keys=OFF")
    if orphan_kind == "outbox":
        conn.execute(
            """INSERT INTO event_outbox(
                   event_id,consumer_id,effect_type,effect_payload,
                   effect_idempotency_key,depends_on_consumer,retry_contract,
                   status,created_at,available_at,updated_at
               ) VALUES (?,?,?,?,?,NULL,'idempotent','pending',?,?,?)""",
            (
                marker,
                "order_state",
                "reduce_order_state",
                "{}",
                "synthetic-orphan-effect-key",
                T0.isoformat(),
                T0.isoformat(),
                T0.isoformat(),
            ),
        )
        violated_table = "event_outbox"
    else:
        conn.execute(
            """INSERT INTO event_consumer_receipts(
                   event_id,consumer_id,status,attempt_count,updated_at
               ) VALUES (?,?,'pending',0,?)""",
            (marker, "order_state", T0.isoformat()),
        )
        violated_table = "event_consumer_receipts"
    inspection = durable_event_outbox.inspect_connection(conn)
    assert inspection["ready"] is False
    assert inspection["foreign_key_violations"] == {
        "checked": True,
        "count": 1,
        "tables": [violated_table],
    }
    assert marker not in json.dumps(inspection)
    with pytest.raises(RuntimeError, match="foreign-key integrity"):
        durable_event_outbox.apply_to_connection(conn)
    conn.close()


def test_publish_is_one_transaction_with_receipts_per_consumer(durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "COURIER_DELIVERED",
        "publish",
        source="panel_watcher:synthetic_delivery",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    result = _publish(conn, envelope, contract)
    assert result.inserted and not result.duplicate
    assert result.consumers == (
        "delivery_geocode",
        "order_state",
        "plan",
        "sla",
    )
    assert conn.execute("SELECT COUNT(*) FROM event_envelopes").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM event_dedup_ledger").fetchone()[0] == 1
    rows = conn.execute(
        "SELECT consumer_id,status,attempt_count FROM event_consumer_receipts ORDER BY consumer_id"
    ).fetchall()
    assert rows == [
        ("delivery_geocode", "pending", 0),
        ("order_state", "pending", 0),
        ("plan", "pending", 0),
        ("sla", "pending", 0),
    ]
    assert conn.execute(
        "SELECT status FROM events WHERE event_id=?", (envelope.event_id,)
    ).fetchone() == ("durable",)
    duplicate = _publish(conn, envelope, contract)
    assert duplicate.duplicate and not duplicate.inserted
    conn.close()


def test_producer_retry_after_uncertain_commit_keeps_first_canonical_envelope(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    first = _envelope("NEW_ORDER", "producer-restart", created_at=T0)
    conn = sqlite3.connect(db_path, isolation_level=None)
    assert _publish(conn, first, contract, intents=_state_only(first)).inserted
    conn.close()  # syntetyczny restart po commicie, przed odpowiedzia dla source

    retry = _envelope(
        "NEW_ORDER",
        "producer-restart",
        created_at=T0 + timedelta(minutes=3),
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    duplicate = _publish(conn, retry, contract, intents=_state_only(retry))
    assert duplicate.duplicate and not duplicate.inserted
    assert event_outbox.load_envelope(conn, first.event_id).created_at == first.created_at

    changed_payload = dict(first.payload)
    changed_payload["restaurant"] = "Mutated source payload"
    conflict = _envelope(
        "NEW_ORDER",
        "producer-restart",
        created_at=T0 + timedelta(minutes=4),
        payload=changed_payload,
    )
    with pytest.raises(event_outbox.EnvelopeConflict):
        _publish(conn, conflict, contract, intents=_state_only(conflict))

    same_source_key = EventEnvelope.from_parts(
        event_id="synthetic:different-presentation-id",
        event_type=first.event_type,
        order_id=first.order_id,
        courier_id=first.courier_id,
        payload=first.payload,
        created_at=T0 + timedelta(minutes=5),
        source=first.source,
        envelope_version=first.envelope_version,
        policy_version=first.policy_version,
        producer_key=first.producer_key,
        identity_scheme=first.identity_scheme,
    )
    with pytest.raises(event_outbox.EnvelopeConflict, match="idempotency key"):
        _publish(
            conn,
            same_source_key,
            contract,
            intents=_state_only(same_source_key),
        )
    conn.close()


def test_publish_rolls_back_envelope_outbox_compat_on_mid_transaction_failure(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("COURIER_DELIVERED", "atomic-rollback")
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.execute(
        """CREATE TRIGGER synthetic_receipt_abort
           BEFORE INSERT ON event_consumer_receipts
           WHEN NEW.consumer_id='sla'
           BEGIN SELECT RAISE(ABORT, 'synthetic'); END"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        _publish(conn, envelope, contract)
    for table in (
        "event_envelopes",
        "event_dedup_ledger",
        "event_outbox",
        "event_consumer_receipts",
        "events",
        "audit_log",
    ):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    conn.close()


def test_mutation_probe_missing_dependency_is_rejected_before_any_write(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("COURIER_ASSIGNED", "mutated-dependency")
    mutated = event_outbox.EffectIntent(
        consumer_id="plan",
        effect_type="plan_on_assignment",
        depends_on_consumer="order_state",
        retry_contract="confirm_before_retry",
        payload={"event_id": envelope.event_id},
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    with pytest.raises(ValueError, match="dependency"):
        _publish(conn, envelope, contract, intents=(mutated,))
    assert conn.execute("SELECT COUNT(*) FROM event_envelopes").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "field",
    ("effect_type", "payload", "depends_on_consumer", "retry_contract"),
)
def test_duplicate_rejects_each_canonical_intent_field_mismatch(
    durable_store,
    field,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("COURIER_ASSIGNED", f"intent-conflict-{field}")
    state_intent = _state_only(envelope)[0]
    plan_intent = event_outbox.EffectIntent(
        consumer_id="plan",
        effect_type="plan_on_assignment",
        depends_on_consumer="order_state",
        retry_contract="confirm_before_retry",
        payload={"event_id": envelope.event_id},
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=(state_intent, plan_intent))
    changes = {
        "effect_type": {"effect_type": "mutated_effect"},
        "payload": {"payload": {"event_id": envelope.event_id, "mutated": True}},
        "depends_on_consumer": {"depends_on_consumer": None},
        "retry_contract": {"retry_contract": "idempotent"},
    }[field]
    mutated_plan = replace(plan_intent, **changes)
    with pytest.raises(event_outbox.EnvelopeConflict, match="intent topology"):
        _publish(
            conn,
            envelope,
            contract,
            intents=(state_intent, mutated_plan),
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM event_outbox WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 2
    stored = conn.execute(
        """SELECT effect_type,effect_payload,depends_on_consumer,retry_contract
           FROM event_outbox WHERE event_id=? AND consumer_id='plan'""",
        (envelope.event_id,),
    ).fetchone()
    assert stored == (
        "plan_on_assignment",
        json.dumps(
            {"event_id": envelope.event_id},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "order_state",
        "confirm_before_retry",
    )
    conn.close()


@pytest.mark.parametrize(
    "contract_mutation",
    ("missing_receipt", "missing_dedup", "changed_dedup_until"),
)
def test_duplicate_rejects_missing_or_changed_child_contract(
    durable_store,
    contract_mutation,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"child-{contract_mutation}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    if contract_mutation == "missing_receipt":
        conn.execute(
            "DELETE FROM event_consumer_receipts WHERE event_id=?",
            (envelope.event_id,),
        )
        expected_error = "receipt"
    elif contract_mutation == "missing_dedup":
        conn.execute(
            "DELETE FROM event_dedup_ledger WHERE event_id=?",
            (envelope.event_id,),
        )
        expected_error = "dedup"
    else:
        conn.execute(
            """UPDATE event_dedup_ledger SET dedup_until=?
               WHERE event_id=?""",
            ((T0 + timedelta(days=999)).isoformat(), envelope.event_id),
        )
        expected_error = "dedup"
    before = conn.total_changes
    with pytest.raises(event_outbox.EnvelopeConflict, match=expected_error):
        _publish(conn, envelope, contract, intents=_state_only(envelope))
    assert conn.total_changes == before
    assert conn.execute(
        "SELECT COUNT(*) FROM event_envelopes WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize(
    ("outbox_status", "receipt_status"),
    (("pending", "failed"), ("failed", "pending")),
)
def test_claim_fails_closed_on_outbox_receipt_status_divergence(
    durable_store,
    outbox_status,
    receipt_status,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "NEW_ORDER", f"claim-divergence-{outbox_status}-{receipt_status}"
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    conn.execute(
        "UPDATE event_outbox SET status=? WHERE event_id=? AND consumer_id=?",
        (outbox_status, envelope.event_id, event_outbox.STATE_CONSUMER),
    )
    conn.execute(
        """UPDATE event_consumer_receipts SET status=?
           WHERE event_id=? AND consumer_id=?""",
        (receipt_status, envelope.event_id, event_outbox.STATE_CONSUMER),
    )
    with pytest.raises(event_outbox.ReceiptStateError, match="inconsistent"):
        _claim(conn, envelope, event_outbox.STATE_CONSUMER)
    assert conn.execute(
        """SELECT o.status,r.status FROM event_outbox o
           JOIN event_consumer_receipts r
             ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
           WHERE o.event_id=? AND o.consumer_id=?""",
        (envelope.event_id, event_outbox.STATE_CONSUMER),
    ).fetchone() == (outbox_status, receipt_status)
    conn.close()


@pytest.mark.parametrize("cas_table", ("event_outbox", "event_consumer_receipts"))
def test_claim_requires_both_cas_updates_and_rolls_back_first(cas_table, durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"claim-cas-{cas_table}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    conn.execute(
        f"""CREATE TRIGGER synthetic_claim_cas_ignore
            BEFORE UPDATE ON {cas_table}
            WHEN NEW.status='claimed'
            BEGIN SELECT RAISE(IGNORE); END"""
    )
    with pytest.raises(event_outbox.ReceiptStateError, match="compare-and-swap"):
        _claim(conn, envelope, event_outbox.STATE_CONSUMER)
    assert conn.execute(
        """SELECT o.status,r.status FROM event_outbox o
           JOIN event_consumer_receipts r
             ON r.event_id=o.event_id AND r.consumer_id=o.consumer_id
           WHERE o.event_id=? AND o.consumer_id=?""",
        (envelope.event_id, event_outbox.STATE_CONSUMER),
    ).fetchone() == ("pending", "pending")
    conn.close()


def test_mutation_probe_state_consumer_rejects_same_id_with_changed_contract(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "mutated-contract")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(conn, envelope, "order_state")
    assert claim is not None
    mutated = event_outbox.RetentionContract(
        policy_id=contract.policy_id,
        event_retention_seconds=contract.event_retention_seconds,
        receipt_retention_seconds=contract.receipt_retention_seconds,
        dedup_retention_seconds=contract.dedup_retention_seconds,
        max_replay_age_seconds=contract.max_replay_age_seconds,
        max_receipts_per_order=contract.max_receipts_per_order + 1,
        status=contract.status,
        created_at=contract.created_at,
    )
    with pytest.raises(ValueError, match="does not match"):
        sm.commit_durable_state_claim(
            conn,
            claim,
            retention_contract=mutated,
            started_at=T0 + timedelta(seconds=2),
            acknowledged_at=T0 + timedelta(seconds=3),
        )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "claimed"
    conn.close()


def test_dependency_blocks_plan_until_state_ack_and_receipts_are_independent(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "COURIER_ASSIGNED",
        "dependency",
        created_at=T0 + timedelta(minutes=1),
        source="panel_watcher:synthetic_assignment",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract)
    assert _claim(conn, envelope, "plan") is None
    state_claim = _claim(conn, envelope, "order_state")
    assert state_claim is not None
    base = datetime.fromisoformat(envelope.created_at)
    attempt = event_outbox.begin_attempt(conn, state_claim, started_at=base + timedelta(seconds=2))
    event_outbox.acknowledge_effect(
        conn,
        state_claim,
        attempt_number=attempt,
        acknowledged_at=base + timedelta(seconds=3),
    )
    plan_claim = _claim(conn, envelope, "plan", at=base + timedelta(seconds=4))
    assert plan_claim is not None
    assert event_outbox.receipt_status(conn, envelope.event_id, "plan")["status"] == "claimed"
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "coordinator_activation"
    )["status"] == "pending"
    conn.close()


def test_two_workers_race_only_one_claims(durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "race")
    publisher = sqlite3.connect(db_path, isolation_level=None)
    _publish(publisher, envelope, contract, intents=_state_only(envelope))
    publisher.close()

    barrier = threading.Barrier(2)

    def contend(worker):
        conn = sqlite3.connect(db_path, isolation_level=None, timeout=2.0)
        try:
            barrier.wait(timeout=2.0)
            return _claim(conn, envelope, "order_state", worker=worker)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(contend, ("worker-a", "worker-b")))
    assert sum(claim is not None for claim in claims) == 1


def test_two_state_events_for_one_order_are_serialized_by_previous_ack(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    first = _envelope("NEW_ORDER", "ordered-first", created_at=T0)
    second = _envelope(
        "COURIER_ASSIGNED",
        "ordered-second",
        created_at=T0 + timedelta(minutes=1),
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, first, contract, intents=_state_only(first))
    _publish(conn, second, contract, intents=_state_only(second))
    assert _claim(conn, second, "order_state", worker="too-early") is None
    first_claim = _claim(conn, first, "order_state", worker="ordered-worker")
    assert first_claim is not None
    sm.commit_durable_state_claim(
        conn,
        first_claim,
        retention_contract=contract,
        started_at=T0 + timedelta(seconds=2),
        acknowledged_at=T0 + timedelta(seconds=3),
    )
    assert _claim(
        conn,
        second,
        "order_state",
        worker="after-ack",
        at=T0 + timedelta(minutes=1, seconds=1),
    ) is not None
    conn.close()


def test_two_plan_effects_for_one_order_are_serialized_even_after_state_acks(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    assigned = _envelope(
        "COURIER_ASSIGNED",
        "plan-order-assigned",
        created_at=T0,
        payload={"source": "panel_diff"},
        source="panel_watcher:panel_diff_assignment",
    )
    picked = _envelope(
        "COURIER_PICKED_UP",
        "plan-order-picked",
        created_at=T0 + timedelta(minutes=1),
        source="panel_watcher:reconcile_pickup",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, assigned, contract)
    _publish(conn, picked, contract)
    _apply_state(conn, assigned, contract, at=T0 + timedelta(seconds=1))
    _apply_state(
        conn,
        picked,
        contract,
        at=T0 + timedelta(minutes=1, seconds=1),
    )
    assert _claim(
        conn,
        picked,
        "plan",
        worker="later-plan-worker",
        at=T0 + timedelta(minutes=2),
    ) is None
    assert _claim(
        conn,
        assigned,
        "plan",
        worker="first-plan-worker",
        at=T0 + timedelta(minutes=2),
    ) is not None
    conn.close()


def test_crash_after_attempt_before_state_restarts_from_persisted_outbox(
    durable_store,
):
    state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "before-state-crash")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(conn, envelope, "order_state")
    assert claim is not None
    event_outbox.begin_attempt(
        conn,
        claim,
        started_at=T0 + timedelta(seconds=2),
    )
    conn.close()
    assert json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    ) == {}

    conn = sqlite3.connect(db_path, isolation_level=None)
    recovered = event_outbox.recover_expired_claims(
        conn,
        recovered_at=T0 + timedelta(seconds=12),
    )
    assert recovered["idempotent_requeued"] == 1
    applied = _apply_state(
        conn,
        envelope,
        contract,
        worker="worker-after-restart",
        at=T0 + timedelta(seconds=13),
    )
    assert applied.domain_changed and applied.acknowledged
    record = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-order"]
    assert [item["event"] for item in record["history"]] == ["NEW_ORDER"]
    conn.close()


def test_lease_boundary_is_half_open_and_recovery_wins_at_exact_expiry(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "lease-boundary")
    conn_a = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn_a, envelope, contract, intents=_state_only(envelope))
    claimed_at = T0 + timedelta(seconds=1)
    claim = _claim(
        conn_a,
        envelope,
        "order_state",
        worker="boundary-old",
        at=claimed_at,
    )
    assert claim is not None
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    with pytest.raises(event_outbox.ReceiptStateError, match="lease expired"):
        event_outbox.begin_attempt(conn_a, claim, started_at=expiry)

    conn_b = sqlite3.connect(db_path, isolation_level=None)
    assert event_outbox.recover_expired_claims(
        conn_b, recovered_at=expiry
    ) == {"released": 1, "idempotent_requeued": 0, "effect_unknown": 0}
    with pytest.raises(event_outbox.ReceiptStateError):
        event_outbox.begin_attempt(conn_a, claim, started_at=expiry)
    replacement = _claim(
        conn_b,
        envelope,
        "order_state",
        worker="boundary-new",
        at=expiry + timedelta(microseconds=1),
    )
    assert replacement is not None
    assert event_outbox.begin_attempt(
        conn_b,
        replacement,
        started_at=expiry + timedelta(seconds=1),
    ) == 1
    conn_a.close()
    conn_b.close()


def test_reclaimed_lease_rejects_stale_claim_even_with_same_worker_ref(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "same-worker-new-lease")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    old_claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="reused-worker-ref",
        at=T0 + timedelta(seconds=1),
    )
    expiry = datetime.fromisoformat(old_claim.lease_expires_at)
    event_outbox.recover_expired_claims(conn, recovered_at=expiry)
    new_claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="reused-worker-ref",
        at=expiry + timedelta(seconds=1),
    )
    assert new_claim.lease_expires_at != old_claim.lease_expires_at
    with pytest.raises(event_outbox.ReceiptStateError, match="lease token"):
        event_outbox.begin_attempt(
            conn,
            old_claim,
            started_at=expiry + timedelta(seconds=2),
        )
    assert event_outbox.begin_attempt(
        conn,
        new_claim,
        started_at=expiry + timedelta(seconds=2),
    ) == 1
    conn.close()


def test_begin_attempt_rejects_time_before_claim_and_accepts_exact_claim_time(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "attempt-time-order")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim_at = T0 + timedelta(seconds=2)
    claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="attempt-time-worker",
        at=claim_at,
    )
    with pytest.raises(event_outbox.ReceiptStateError, match="before the claim"):
        event_outbox.begin_attempt(
            conn,
            claim,
            started_at=claim_at - timedelta(microseconds=1),
        )
    assert event_outbox.begin_attempt(conn, claim, started_at=claim_at) == 1
    conn.close()


def test_late_ack_before_recovery_wins_but_after_recovery_is_rejected(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "late-ack-wins")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="ack-boundary-worker",
        at=T0 + timedelta(seconds=1),
    )
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    assert event_outbox.acknowledge_effect(
        conn,
        claim,
        attempt_number=attempt,
        acknowledged_at=expiry + timedelta(seconds=1),
    )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "acknowledged"
    assert event_outbox.recover_expired_claims(
        conn, recovered_at=expiry
    ) == {"released": 0, "idempotent_requeued": 0, "effect_unknown": 0}

    recovered_envelope = _envelope("NEW_ORDER", "recovery-beats-late-ack")
    _publish(conn, recovered_envelope, contract, intents=_state_only(recovered_envelope))
    recovered_claim = _claim(
        conn,
        recovered_envelope,
        "order_state",
        worker="recovered-ack-worker",
        at=T0 + timedelta(seconds=1),
    )
    recovered_attempt = event_outbox.begin_attempt(
        conn, recovered_claim, started_at=T0 + timedelta(seconds=2)
    )
    recovered_expiry = datetime.fromisoformat(recovered_claim.lease_expires_at)
    assert event_outbox.recover_expired_claims(
        conn, recovered_at=recovered_expiry
    )["idempotent_requeued"] == 1
    with pytest.raises(event_outbox.ReceiptStateError, match="executing"):
        event_outbox.acknowledge_effect(
            conn,
            recovered_claim,
            attempt_number=recovered_attempt,
            acknowledged_at=recovered_expiry + timedelta(seconds=1),
        )
    conn.close()


def test_late_failure_before_recovery_wins(durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "late-failure-wins")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="late-failure-worker",
        at=T0 + timedelta(seconds=1),
    )
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    assert event_outbox.record_failure(
        conn,
        claim=claim,
        error=ValueError("synthetic"),
        failed_at=expiry + timedelta(seconds=1),
        disposition="failed",
        attempt_number=attempt,
    )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "failed"
    assert event_outbox.recover_expired_claims(
        conn, recovered_at=expiry + timedelta(seconds=2)
    ) == {"released": 0, "idempotent_requeued": 0, "effect_unknown": 0}
    conn.close()


@pytest.mark.parametrize("missing_side", ("outbox", "receipt"))
def test_recovery_rejects_missing_outbox_receipt_pair(
    durable_store,
    missing_side,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"recovery-missing-{missing_side}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="recovery-missing-worker",
        at=T0 + timedelta(seconds=1),
    )
    conn.execute("PRAGMA foreign_keys=OFF")
    table = (
        "event_outbox" if missing_side == "outbox" else "event_consumer_receipts"
    )
    conn.execute(
        f"DELETE FROM {table} WHERE event_id=? AND consumer_id='order_state'",
        (envelope.event_id,),
    )
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    before = conn.total_changes
    with pytest.raises(event_outbox.ReceiptStateError, match="incomplete|divergent"):
        event_outbox.recover_expired_claims(conn, recovered_at=expiry)
    assert conn.total_changes == before
    assert conn.execute(
        "SELECT COUNT(*) FROM event_failure_journal WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 0
    conn.close()


def test_recovery_rejects_divergent_outbox_receipt_status(durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "recovery-status-divergence")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(conn, envelope, "order_state", at=T0 + timedelta(seconds=1))
    conn.execute(
        """UPDATE event_outbox SET status='pending'
           WHERE event_id=? AND consumer_id='order_state'""",
        (envelope.event_id,),
    )
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    with pytest.raises(event_outbox.ReceiptStateError, match="divergent"):
        event_outbox.recover_expired_claims(conn, recovered_at=expiry)
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "claimed"
    assert conn.execute(
        """SELECT status FROM event_outbox
           WHERE event_id=? AND consumer_id='order_state'""",
        (envelope.event_id,),
    ).fetchone()[0] == "pending"
    conn.close()


@pytest.mark.parametrize("missing_lease_field", ("lease_owner", "lease_expires_at"))
def test_recovery_rejects_active_status_without_complete_lease(
    durable_store,
    missing_lease_field,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"recovery-no-{missing_lease_field}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    _claim(conn, envelope, "order_state", at=T0 + timedelta(seconds=1))
    conn.execute(
        f"""UPDATE event_consumer_receipts SET {missing_lease_field}=NULL
            WHERE event_id=? AND consumer_id='order_state'""",
        (envelope.event_id,),
    )
    with pytest.raises(event_outbox.ReceiptStateError, match="missing|unowned"):
        event_outbox.recover_expired_claims(
            conn,
            recovered_at=T0 + timedelta(days=1),
        )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "claimed"
    conn.close()


@pytest.mark.parametrize("attempt_mutation", ("missing", "stale", "wrong_owner"))
def test_recovery_rejects_missing_or_stale_current_attempt(
    durable_store,
    attempt_mutation,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"recovery-attempt-{attempt_mutation}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(conn, envelope, "order_state", at=T0 + timedelta(seconds=1))
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    if attempt_mutation == "missing":
        conn.execute(
            """DELETE FROM event_consumer_attempts
               WHERE event_id=? AND consumer_id='order_state'
                 AND attempt_number=?""",
            (envelope.event_id, attempt),
        )
    elif attempt_mutation == "stale":
        conn.execute(
            """UPDATE event_consumer_attempts SET outcome='failed'
               WHERE event_id=? AND consumer_id='order_state'
                 AND attempt_number=?""",
            (envelope.event_id, attempt),
        )
    else:
        conn.execute(
            """UPDATE event_consumer_attempts SET worker_ref='different-worker'
               WHERE event_id=? AND consumer_id='order_state'
                 AND attempt_number=?""",
            (envelope.event_id, attempt),
        )
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    with pytest.raises(event_outbox.ReceiptStateError, match="attempt"):
        event_outbox.recover_expired_claims(conn, recovered_at=expiry)
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "executing"
    assert conn.execute(
        "SELECT COUNT(*) FROM event_failure_journal WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "cas_table",
    ("event_consumer_attempts", "event_consumer_receipts", "event_outbox"),
)
def test_recovery_requires_attempt_receipt_and_outbox_cas(
    durable_store,
    cas_table,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"recovery-cas-{cas_table}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(conn, envelope, "order_state", at=T0 + timedelta(seconds=1))
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    conn.execute(
        f"""CREATE TRIGGER synthetic_recovery_cas_ignore
            BEFORE UPDATE ON {cas_table}
            BEGIN SELECT RAISE(IGNORE); END"""
    )
    expiry = datetime.fromisoformat(claim.lease_expires_at)
    with pytest.raises(event_outbox.ReceiptStateError, match="compare-and-swap"):
        event_outbox.recover_expired_claims(conn, recovered_at=expiry)
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "executing"
    assert conn.execute(
        """SELECT status FROM event_outbox
           WHERE event_id=? AND consumer_id='order_state'""",
        (envelope.event_id,),
    ).fetchone()[0] == "executing"
    assert conn.execute(
        """SELECT outcome FROM event_consumer_attempts
           WHERE event_id=? AND consumer_id='order_state'
             AND attempt_number=?""",
        (envelope.event_id, attempt),
    ).fetchone()[0] == "executing"
    conn.close()


@pytest.mark.parametrize("finish_kind", ("ack", "failure"))
def test_finish_time_cannot_precede_attempt_start(durable_store, finish_kind):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", f"finish-time-{finish_kind}")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(
        conn,
        envelope,
        "order_state",
        worker=f"finish-time-{finish_kind}-worker",
        at=T0 + timedelta(seconds=1),
    )
    started_at = T0 + timedelta(seconds=3)
    attempt = event_outbox.begin_attempt(conn, claim, started_at=started_at)
    backwards = started_at - timedelta(microseconds=1)
    with pytest.raises(event_outbox.ReceiptStateError, match="time moved backwards"):
        if finish_kind == "ack":
            event_outbox.acknowledge_effect(
                conn,
                claim,
                attempt_number=attempt,
                acknowledged_at=backwards,
            )
        else:
            event_outbox.record_failure(
                conn,
                claim=claim,
                error=ValueError("synthetic"),
                failed_at=backwards,
                disposition="failed",
                attempt_number=attempt,
            )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "executing"
    assert conn.execute(
        "SELECT COUNT(*) FROM event_failure_journal WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 0
    conn.close()


def test_acknowledge_fails_closed_before_idempotent_return_on_status_divergence(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "ack-idempotent-divergence")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    claim = _claim(conn, envelope, "order_state", at=T0 + timedelta(seconds=1))
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    assert event_outbox.acknowledge_effect(
        conn,
        claim,
        attempt_number=attempt,
        acknowledged_at=T0 + timedelta(seconds=3),
    )
    conn.execute(
        """UPDATE event_outbox SET status='pending'
           WHERE event_id=? AND consumer_id='order_state'""",
        (envelope.event_id,),
    )
    with pytest.raises(event_outbox.ReceiptStateError, match="inconsistent"):
        event_outbox.acknowledge_effect(
            conn,
            claim,
            attempt_number=attempt,
            acknowledged_at=T0 + timedelta(seconds=4),
        )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "acknowledged"
    conn.close()


def test_stale_worker_cannot_journal_over_pending_claimed_or_newer_attempt(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "stale-failure-worker")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    old_claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="stale-worker",
        at=T0 + timedelta(seconds=1),
    )
    old_attempt = event_outbox.begin_attempt(
        conn, old_claim, started_at=T0 + timedelta(seconds=2)
    )
    expiry = datetime.fromisoformat(old_claim.lease_expires_at)
    event_outbox.recover_expired_claims(conn, recovered_at=expiry)

    with pytest.raises(event_outbox.ReceiptStateError):
        event_outbox.record_failure(
            conn,
            claim=old_claim,
            error=ValueError("synthetic"),
            failed_at=expiry,
            disposition="failed",
            attempt_number=old_attempt,
        )
    new_claim = _claim(
        conn,
        envelope,
        "order_state",
        worker="new-worker",
        at=expiry + timedelta(seconds=1),
    )
    assert new_claim is not None
    with pytest.raises(event_outbox.ReceiptStateError):
        event_outbox.record_failure(
            conn,
            claim=old_claim,
            error=ValueError("synthetic"),
            failed_at=expiry + timedelta(seconds=2),
            disposition="failed",
            attempt_number=old_attempt,
        )
    new_attempt = event_outbox.begin_attempt(
        conn,
        new_claim,
        started_at=expiry + timedelta(seconds=2),
    )
    with pytest.raises(event_outbox.ReceiptStateError):
        event_outbox.record_failure(
            conn,
            claim=old_claim,
            error=ValueError("synthetic"),
            failed_at=expiry + timedelta(seconds=3),
            disposition="failed",
            attempt_number=old_attempt,
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM event_failure_journal WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 0
    status = event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )
    assert (status["status"], status["attempt_count"], status["lease_owner"]) == (
        "executing",
        2,
        "new-worker",
    )
    assert event_outbox.record_failure(
        conn,
        claim=new_claim,
        error=ValueError("synthetic"),
        failed_at=expiry + timedelta(seconds=3),
        disposition="failed",
        attempt_number=new_attempt,
    )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "failed"
    conn.close()


def test_crash_after_state_before_ack_restarts_without_second_state_effect(
    durable_store,
):
    state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "state-crash")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=_state_only(envelope))

    def crash():
        raise RuntimeError("synthetic crash after state commit")

    with pytest.raises(RuntimeError, match="synthetic crash"):
        _apply_state(conn, envelope, contract, hook=crash)
    persisted = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-order"]
    assert [entry["event"] for entry in persisted["history"]] == ["NEW_ORDER"]
    assert event_outbox.receipt_status(
        conn, envelope.event_id, "order_state"
    )["status"] == "executing"

    conn.close()  # rzeczywisty restart procesu/uchwytu SQLite
    conn = sqlite3.connect(db_path, isolation_level=None)

    recovered = event_outbox.recover_expired_claims(
        conn, recovered_at=T0 + timedelta(seconds=12)
    )
    assert recovered == {"released": 0, "idempotent_requeued": 1, "effect_unknown": 0}
    result = _apply_state(
        conn,
        envelope,
        contract,
        worker="worker-restart",
        at=T0 + timedelta(seconds=13),
    )
    assert result.duplicate and not result.domain_changed
    persisted = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-order"]
    assert [entry["event"] for entry in persisted["history"]] == ["NEW_ORDER"]
    receipt = event_outbox.receipt_status(conn, envelope.event_id, "order_state")
    assert receipt["status"] == "acknowledged"
    assert receipt["attempt_count"] == 2
    conn.close()


def test_external_effect_crash_becomes_unknown_not_automatic_retry(durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "COURIER_ASSIGNED",
        "external-crash",
        created_at=T0 + timedelta(minutes=1),
        source="panel_watcher:synthetic_assignment",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract)

    state_claim = _claim(conn, envelope, "order_state")
    assert state_claim is not None
    base = datetime.fromisoformat(envelope.created_at)
    state_attempt = event_outbox.begin_attempt(conn, state_claim, started_at=base + timedelta(seconds=2))
    event_outbox.acknowledge_effect(
        conn, state_claim, attempt_number=state_attempt, acknowledged_at=base + timedelta(seconds=3)
    )
    plan_claim = _claim(conn, envelope, "plan", at=base + timedelta(seconds=4))
    assert plan_claim is not None
    event_outbox.begin_attempt(conn, plan_claim, started_at=base + timedelta(seconds=5))

    recovered = event_outbox.recover_expired_claims(
        conn, recovered_at=base + timedelta(seconds=15)
    )
    assert recovered["effect_unknown"] == 1
    assert event_outbox.receipt_status(conn, envelope.event_id, "plan")["status"] == "effect_unknown"
    assert _claim(
        conn, envelope, "plan", worker="unsafe-retry", at=base + timedelta(seconds=16)
    ) is None
    assert conn.execute(
        """SELECT disposition,error_code FROM event_failure_journal
           WHERE event_id=? AND consumer_id='plan'""",
        (envelope.event_id,),
    ).fetchone() == ("effect_unknown", "unexpected_failure")
    conn.close()


def test_stale_different_event_id_is_quarantined_and_state_is_unchanged(
    durable_store,
):
    state_dir, db_path, contract = durable_store
    conn = sqlite3.connect(db_path, isolation_level=None)
    new = _envelope("NEW_ORDER", "stale-new", created_at=T0)
    assigned = _envelope("COURIER_ASSIGNED", "stale-assigned", created_at=T0 + timedelta(minutes=2))
    for envelope in (new, assigned):
        _publish(conn, envelope, contract, intents=_state_only(envelope))
        _apply_state(conn, envelope, contract, at=envelope.created_at)

    stale = _envelope("COURIER_PICKED_UP", "stale-picked", created_at=T0 + timedelta(minutes=1))
    _publish(conn, stale, contract, intents=_state_only(stale))
    with pytest.raises(ReductionRejected, match="stale_event"):
        _apply_state(conn, stale, contract, at=T0 + timedelta(minutes=3))
    current = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-order"]
    assert current["status"] == "assigned"
    assert [entry["event"] for entry in current["history"]] == [
        "NEW_ORDER",
        "COURIER_ASSIGNED",
    ]
    assert event_outbox.receipt_status(conn, stale.event_id, "order_state")["status"] == "quarantined"
    conn.close()


def test_state_quarantine_terminally_journals_each_blocked_consumer(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "COURIER_DELIVERED",
        "dependency-quarantine",
        source="panel_watcher:reconcile_delivery",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    result = _publish(conn, envelope, contract)
    assert set(result.consumers) == {"order_state", "sla", "delivery_geocode", "plan"}
    with pytest.raises(ReductionRejected):
        _apply_state(conn, envelope, contract)
    rows = conn.execute(
        """SELECT consumer_id,status FROM event_consumer_receipts
           WHERE event_id=? ORDER BY consumer_id""",
        (envelope.event_id,),
    ).fetchall()
    assert rows == [
        ("delivery_geocode", "quarantined"),
        ("order_state", "quarantined"),
        ("plan", "quarantined"),
        ("sla", "quarantined"),
    ]
    assert conn.execute(
        "SELECT COUNT(*) FROM event_failure_journal WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 4
    assert conn.execute(
        "SELECT terminal_at FROM event_dedup_ledger WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] is not None
    conn.close()


@pytest.mark.parametrize(
    "dependent_mutation",
    ("missing_receipt", "status_divergence", "receipt_cas", "outbox_cas"),
)
def test_state_quarantine_requires_complete_pending_dependent_pairs(
    durable_store,
    dependent_mutation,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "COURIER_DELIVERED",
        f"dependent-integrity-{dependent_mutation}",
        source="panel_watcher:reconcile_delivery",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract)
    claim = _claim(conn, envelope, event_outbox.STATE_CONSUMER)
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    if dependent_mutation == "missing_receipt":
        conn.execute(
            """DELETE FROM event_consumer_receipts
               WHERE event_id=? AND consumer_id='plan'""",
            (envelope.event_id,),
        )
    elif dependent_mutation == "status_divergence":
        conn.execute(
            """UPDATE event_outbox SET status='failed'
               WHERE event_id=? AND consumer_id='plan'""",
            (envelope.event_id,),
        )
    else:
        table = (
            "event_consumer_receipts"
            if dependent_mutation == "receipt_cas"
            else "event_outbox"
        )
        conn.execute(
            f"""CREATE TRIGGER synthetic_dependent_cas_ignore
                BEFORE UPDATE ON {table}
                WHEN OLD.consumer_id='plan' AND NEW.status='quarantined'
                BEGIN SELECT RAISE(IGNORE); END"""
        )
    with pytest.raises(event_outbox.ReceiptStateError):
        event_outbox.record_failure(
            conn,
            claim=claim,
            error=ReductionRejected("illegal_transition"),
            failed_at=T0 + timedelta(seconds=3),
            disposition="quarantined",
            attempt_number=attempt,
        )
    assert event_outbox.receipt_status(
        conn, envelope.event_id, event_outbox.STATE_CONSUMER
    )["status"] == "executing"
    assert conn.execute(
        """SELECT status FROM event_outbox
           WHERE event_id=? AND consumer_id='order_state'""",
        (envelope.event_id,),
    ).fetchone()[0] == "executing"
    assert conn.execute(
        "SELECT COUNT(*) FROM event_failure_journal WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 0
    conn.close()


def test_synthetic_on_golden_lifecycle_and_correction_use_only_state_primitive(
    durable_store,
):
    state_dir, db_path, contract = durable_store
    sequence = (
        _envelope("NEW_ORDER", "golden-new", created_at=T0),
        _envelope(
            "COURIER_ASSIGNED",
            "golden-assigned",
            created_at=T0 + timedelta(minutes=1),
        ),
        _envelope(
            "COURIER_PICKED_UP",
            "golden-picked",
            created_at=T0 + timedelta(minutes=2),
        ),
        _envelope(
            "COURIER_DELIVERED",
            "golden-delivered",
            created_at=T0 + timedelta(minutes=3),
        ),
        _envelope(
            "ORDER_RESURRECTED",
            "golden-resurrected",
            created_at=T0 + timedelta(minutes=4),
            courier_id="synthetic-courier",
            payload={
                "new_status": "assigned",
                "reason": "panel_status_restored",
                "source": "panel_status_restored",
            },
            source="panel_watcher:panel_status_resurrection",
        ),
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    for envelope in sequence:
        _publish(conn, envelope, contract, intents=_state_only(envelope))
        result = _apply_state(
            conn,
            envelope,
            contract,
            at=datetime.fromisoformat(envelope.created_at) + timedelta(seconds=1),
        )
        assert result.acknowledged

    record = json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["synthetic-order"]
    assert record["status"] == "assigned"
    assert record["delivered_at"] is None
    assert [item["event"] for item in record["history"]] == [
        "NEW_ORDER",
        "COURIER_ASSIGNED",
        "COURIER_PICKED_UP",
        "COURIER_DELIVERED",
        "ORDER_RESURRECTED",
    ]
    assert len(record["durable_event_receipts"]) == 1
    assert record["durable_event_receipts"][0]["event_id"] == sequence[-1].event_id
    assert conn.execute(
        "SELECT COUNT(*) FROM event_consumer_receipts WHERE status='acknowledged'"
    ).fetchone()[0] == len(sequence)
    conn.close()


def test_audit_only_failure_has_durable_journal_without_queue_row(durable_store):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "PANEL_UNREACHABLE",
        "audit-failure",
        order_id=None,
        source="panel_watcher:health",
    )
    intent = event_outbox.EffectIntent(
        consumer_id="audit_observer",
        effect_type="observe_panel_failure",
        depends_on_consumer=None,
        retry_contract="confirm_before_retry",
        payload={"event_id": envelope.event_id},
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=(intent,), delivery_kind="audit")
    claim = _claim(conn, envelope, "audit_observer")
    assert claim is not None
    attempt = event_outbox.begin_attempt(conn, claim, started_at=T0 + timedelta(seconds=2))
    marker = "SYNTHETIC-SENSITIVE-MARKER"
    event_outbox.record_failure(
        conn,
        claim=claim,
        error=ValueError(marker),
        failed_at=T0 + timedelta(seconds=3),
        disposition="quarantined",
        attempt_number=attempt,
    )
    assert conn.execute(
        "SELECT COUNT(*) FROM events WHERE event_id=?", (envelope.event_id,)
    ).fetchone()[0] == 0
    row = conn.execute(
        """SELECT failure_class,error_code,disposition
           FROM event_failure_journal WHERE event_id=?""",
        (envelope.event_id,),
    ).fetchone()
    assert row == ("permanent", "invalid_payload", "quarantined")
    assert marker not in json.dumps(row)
    conn.close()

    listed = replay_dead_letter.list_durable_failures(str(db_path))
    assert len(listed) == 1
    assert listed[0]["consumer_id"] == "audit_observer"
    assert listed[0]["disposition"] == "quarantined"
    assert envelope.event_id not in json.dumps(listed)


def test_default_consumer_matrix_has_independent_receipts_and_state_dependency():
    cases = (
        ("NEW_ORDER", "panel_watcher:new_order", {"order_state", "shadow_dispatch", "auto_koord"}),
        ("NEW_ORDER", "parcel_lane_merge:snapshot", {"order_state", "shadow_dispatch"}),
        ("NEW_ORDER", "czasowka_scheduler:evaluation", {"shadow_dispatch"}),
        ("COURIER_ASSIGNED", "panel_watcher:panel_diff_assignment", {"order_state", "coordinator_activation", "plan", "assignment_audit"}),
        ("COURIER_ASSIGNED", "parcel_assign:manual_assignment", {"order_state", "coordinator_activation"}),
        ("COURIER_PICKED_UP", "panel_watcher:reconcile_pickup", {"order_state", "sla", "plan"}),
        ("COURIER_PICKED_UP", "reconciliation:auto_resync", {"order_state", "sla"}),
        ("COURIER_DELIVERED", "panel_watcher:reconcile_delivery", {"order_state", "sla", "delivery_geocode", "plan"}),
        ("COURIER_DELIVERED", "reconciliation:auto_resync", {"order_state", "sla", "delivery_geocode"}),
        ("ORDER_RETURNED_TO_POOL", "panel_watcher:reconcile_return", {"order_state", "plan"}),
        ("ORDER_CANCELLED", "parcel_lane_merge:snapshot_retirement", {"order_state"}),
        ("CZAS_KURIERA_UPDATED", "panel_watcher:committed_time_update", {"order_state", "plan"}),
        ("PICKUP_TIME_UPDATED", "panel_watcher:pickup_time_update", {"order_state", "plan"}),
        ("ORDER_RESURRECTED", "panel_watcher:panel_status_resurrection", {"order_state"}),
        ("PANEL_UNREACHABLE", "panel_watcher:panel_unreachable", set()),
    )
    for index, (event_type, source, expected) in enumerate(cases):
        payload = {
            "NEW_ORDER": _payload("NEW_ORDER"),
            "COURIER_ASSIGNED": {"source": "panel_diff"},
            "COURIER_PICKED_UP": _payload("COURIER_PICKED_UP"),
            "COURIER_DELIVERED": _payload("COURIER_DELIVERED"),
            "ORDER_RETURNED_TO_POOL": _payload("ORDER_RETURNED_TO_POOL"),
            "ORDER_CANCELLED": {"reason": "snapshot_missing", "source": "parcel_lane_gone"},
            "CZAS_KURIERA_UPDATED": {"new_ck_iso": "2026-07-12T11:00:00+02:00", "new_ck_hhmm": "11:00"},
            "PICKUP_TIME_UPDATED": {"new_pickup_at_warsaw": "2026-07-12T11:00:00+02:00"},
            "ORDER_RESURRECTED": {"new_status": "assigned", "reason": "panel_status_restored", "source": "panel_status_restored"},
            "PANEL_UNREACHABLE": _payload("PANEL_UNREACHABLE"),
        }[event_type]
        envelope = _envelope(
            event_type,
            f"matrix-{index}",
            payload=payload,
            courier_id=(
                "synthetic-courier"
                if event_type in {"COURIER_ASSIGNED", "COURIER_PICKED_UP", "ORDER_RESURRECTED"}
                else None
            ),
            order_id=None if event_type == "PANEL_UNREACHABLE" else "synthetic-order",
            source=source,
        )
        intents = event_outbox.default_effect_intents(envelope)
        assert {intent.consumer_id for intent in intents} == expected
        assert len(intents) == len(expected)
        for intent in intents:
            if intent.consumer_id == "order_state":
                assert intent.depends_on_consumer is None
                assert intent.retry_contract == "idempotent"
            elif source.startswith("czasowka_scheduler:"):
                assert intent.depends_on_consumer is None
                assert intent.retry_contract == "confirm_before_retry"
            else:
                assert intent.depends_on_consumer == "order_state"
                assert intent.retry_contract == "confirm_before_retry"


def test_durable_rebuild_uses_only_exact_envelopes_and_pure_reducer(
    durable_store, tmp_path
):
    _state_dir, db_path, contract = durable_store
    new = _envelope("NEW_ORDER", "rebuild-new", created_at=T0)
    assigned = _envelope(
        "COURIER_ASSIGNED",
        "rebuild-assigned",
        created_at=T0 + timedelta(minutes=1),
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, new, contract, intents=_state_only(new))
    _publish(conn, assigned, contract, intents=_state_only(assigned))
    shadow_only = _envelope(
        "NEW_ORDER",
        "rebuild-shadow-only",
        created_at=T0 - timedelta(minutes=1),
        source="czasowka_scheduler:evaluation",
    )
    _publish(conn, shadow_only, contract)
    conn.execute(
        """INSERT INTO events(
               event_id,event_type,order_id,payload,created_at,status,
               idempotency_key
           ) VALUES ('legacy-only','NEW_ORDER','legacy-order','{}',?,'pending',?)""",
        (
            (T0 + timedelta(seconds=30)).isoformat(),
            hashlib.sha256(b"legacy-only").hexdigest(),
        ),
    )
    conn.close()

    assert rebuild_state_from_events._default_since(
        str(db_path), durable=True
    ) == new.created_at
    rows = rebuild_state_from_events._read_events(
        str(db_path), since="1970-01-01", durable=True
    )
    assert [row["event_id"] for row in rows] == [new.event_id, assigned.event_id]
    target = tmp_path / "rebuild" / "orders_state.json"
    target.parent.mkdir()
    state, ok, skipped, fail, errors = rebuild_state_from_events._replay_durable(
        rows,
        str(target),
        max_receipts_per_order=contract.max_receipts_per_order,
    )
    assert (ok, skipped, fail, errors) == (2, 0, 0, [])
    assert state["synthetic-order"]["status"] == "assigned"
    assert len(state["synthetic-order"]["durable_event_receipts"]) == 1
    assert (
        state["synthetic-order"]["durable_event_receipts"][0]["event_id"]
        == assigned.event_id
    )
    assert "legacy-order" not in state

    missing_time = dict(rows[0])
    missing_time.pop("created_at")
    broken_target = tmp_path / "broken.json"
    broken = rebuild_state_from_events._replay_durable(
        [missing_time],
        str(broken_target),
        max_receipts_per_order=contract.max_receipts_per_order,
    )
    assert broken[1:4] == (0, 0, 1)
    assert "missing envelope fields: created_at" in broken[4][0][2]
    assert json.loads(broken_target.read_text(encoding="utf-8")) == {}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(broken_target.stat().st_mode) == 0o600


def test_rebuild_atomic_writer_is_0600_and_fails_closed_on_links_or_collision(
    tmp_path,
    monkeypatch,
):
    directory = tmp_path / "atomic"
    directory.mkdir()
    victim = directory / "victim.json"
    victim.write_text("do-not-touch", encoding="utf-8")

    target_link = directory / "target-link.json"
    target_link.symlink_to(victim)
    with pytest.raises(FileExistsError, match="target already exists"):
        rebuild_state_from_events._safe_atomic_write_state(
            target_link, {"synthetic": True}
        )
    assert target_link.is_symlink()
    assert victim.read_text(encoding="utf-8") == "do-not-touch"

    temp_link_target = directory / "temp-link-target.json"
    Path(str(temp_link_target) + ".tmp").symlink_to(victim)
    with pytest.raises(FileExistsError):
        rebuild_state_from_events._safe_atomic_write_state(
            temp_link_target, {"synthetic": True}
        )
    assert not temp_link_target.exists()
    assert victim.read_text(encoding="utf-8") == "do-not-touch"

    collision_target = directory / "collision-target.json"
    collision_temp = Path(str(collision_target) + ".tmp")
    collision_temp.write_text("foreign-temp", encoding="utf-8")
    with pytest.raises(FileExistsError):
        rebuild_state_from_events._safe_atomic_write_state(
            collision_target, {"synthetic": True}
        )
    assert not collision_target.exists()
    assert collision_temp.read_text(encoding="utf-8") == "foreign-temp"

    partial_target = directory / "partial-target.json"
    with pytest.raises(TypeError):
        rebuild_state_from_events._safe_atomic_write_state(
            partial_target, {"not_json": object()}
        )
    assert not partial_target.exists()
    assert not Path(str(partial_target) + ".tmp").exists()

    linked_directory = tmp_path / "linked-directory"
    linked_directory.symlink_to(directory, target_is_directory=True)
    with pytest.raises(RuntimeError, match="cannot contain symlinks"):
        rebuild_state_from_events._safe_atomic_write_state(
            linked_directory / "through-link.json", {"synthetic": True}
        )
    assert not (directory / "through-link.json").exists()

    good_target = directory / "good.json"
    rebuild_state_from_events._safe_atomic_write_state(
        good_target, {"synthetic": True}
    )
    assert json.loads(good_target.read_text(encoding="utf-8")) == {
        "synthetic": True
    }
    assert stat.S_IMODE(good_target.stat().st_mode) == 0o600

    race_target = directory / "race-target.json"
    real_link = os.link

    def create_foreign_target_then_link(source, destination, **kwargs):
        target_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
            dir_fd=kwargs["dst_dir_fd"],
        )
        try:
            os.write(target_fd, b"foreign-race")
            os.fsync(target_fd)
        finally:
            os.close(target_fd)
        return real_link(source, destination, **kwargs)

    monkeypatch.setattr(
        rebuild_state_from_events.os,
        "link",
        create_foreign_target_then_link,
    )
    with pytest.raises(FileExistsError):
        rebuild_state_from_events._safe_atomic_write_state(
            race_target, {"must_not_overwrite": True}
        )
    assert race_target.read_text(encoding="utf-8") == "foreign-race"
    assert not Path(str(race_target) + ".tmp").exists()


def test_reconciliation_durable_handoff_reuses_one_envelope_without_clock_fallback():
    discrepancy = {
        "order_id": "synthetic-order",
        "courier_id": "synthetic-courier",
        "last_event_ts": "2026-07-12T08:00:00+00:00",
        "last_event_age_h": 5.0,
        "state_status": "delivered",
        "classification": "PHANTOM",
        "phantom_subtype": "STATE_TERMINAL",
        "inferred_terminal_event": "COURIER_DELIVERED",
        "inferred_reason": "synthetic",
    }
    sentinel = object()
    factory_calls = []
    emit_calls = []
    apply_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return sentinel

    result = auto_resync.auto_resync_phantoms(
        [discrepancy],
        lambda **kwargs: emit_calls.append(kwargs) or kwargs["event_id"],
        lambda _event: pytest.fail("legacy state update must not run"),
        hard_cap_per_run=5,
        durable_enabled=True,
        envelope_factory=factory,
        envelope_policy_version="order_fsm.synthetic.v1",
        observed_at=T0,
        apply_state_event_fn=lambda event, **kwargs: (
            apply_calls.append((event, kwargs))
            or SimpleNamespace(
                quarantined=False,
                error_code=None,
                should_run_followups=True,
            )
        ),
    )
    assert result["counts"]["auto_resyncs"] == 1
    assert len(factory_calls) == 1
    assert factory_calls[0]["created_at"] == T0
    assert emit_calls[0]["envelope"] is sentinel
    assert apply_calls[0][1]["envelope"] is sentinel


def test_phantom_reader_excludes_outbox_gap_and_includes_audit_event_after_state_ack(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope(
        "COURIER_ASSIGNED",
        "reconcile-reader",
        payload={"source": "panel_diff"},
        source="panel_watcher:panel_diff_assignment",
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, delivery_kind="audit")
    assert phantom_detector.get_last_events_per_order(str(db_path)) == {}
    _apply_state(conn, envelope, contract)
    assert phantom_detector.get_last_events_per_order(str(db_path)) == {
        "synthetic-order": (
            "COURIER_ASSIGNED",
            "synthetic-courier",
            envelope.created_at,
        )
    }
    conn.close()


def test_static_producer_call_graph_requires_durable_handoff_at_every_direct_call():
    root = Path(__file__).resolve().parents[1]
    expected_emit_counts = {
        "panel_watcher.py": 17,
        "dispatch_pipeline.py": 1,
        "czasowka_scheduler.py": 1,
        "parcel_assign.py": 1,
        "parcel_lane_merge.py": 3,
    }
    expected_state_counts = {
        "panel_watcher.py": 16,
        "dispatch_pipeline.py": 1,
        "parcel_assign.py": 1,
        "parcel_lane_merge.py": 3,
    }

    def call_name(node):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
        return ""

    def has_handoff(node):
        for keyword in node.keywords:
            if keyword.arg == "envelope":
                return True
            if keyword.arg is None:
                rendered = ast.unparse(keyword.value)
                if (
                    "durable_envelope_kwargs" in rendered
                    or "_eb_envelope_kwargs" in rendered
                ):
                    return True
        return False

    for filename, expected_count in expected_emit_counts.items():
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and call_name(node) in {"emit", "emit_audit", "_eb_emit_audit"}
        ]
        assert len(calls) == expected_count, filename
        assert all(has_handoff(call) for call in calls), filename

    for filename, expected_count in expected_state_counts.items():
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and call_name(node) in {"apply_state_event", "_eb_apply_state"}
        ]
        assert len(calls) == expected_count, filename
        assert all(has_handoff(call) for call in calls), filename

    direct_factory_calls = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and call_name(node) == "create_order_envelope":
                direct_factory_calls.append(path.relative_to(root).as_posix())
    # Jedyny eager call jest zamkniety wewnatrz lazy helpera. Produkcyjne
    # call-site'y wywoluja maybe_create_order_envelope, ktory przy OFF wraca
    # przed walidacja payloadu/czasu.
    assert direct_factory_calls == ["event_bus.py"]
    assert "maybe_create_order_envelope as _eb_create_envelope" in (
        root / "dispatch_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "envelope_factory=event_bus.maybe_create_order_envelope" in (
        root / "reconciliation" / "reconcile_worker.py"
    ).read_text(encoding="utf-8")


def test_receipt_capacity_never_drops_dedup_and_compacts_only_acknowledged():
    contract = _contract(max_receipts=2)
    first = _envelope("NEW_ORDER", "cap-new", created_at=T0)
    second = _envelope("COURIER_ASSIGNED", "cap-assigned", created_at=T0 + timedelta(minutes=1))
    third = _envelope("COURIER_PICKED_UP", "cap-picked", created_at=T0 + timedelta(minutes=2))
    current = reduce_order_event(None, first, max_receipts_per_order=2).record
    current = reduce_order_event(current, second, max_receipts_per_order=2).record
    with pytest.raises(ReceiptCapacityExceeded):
        reduce_order_event(current, third, max_receipts_per_order=2)
    with pytest.raises(ReceiptCapacityExceeded):
        compact_receipts(
            current,
            acknowledged_event_ids=(),
            older_than=T0 + timedelta(minutes=1),
            keep_at_most=1,
        )
    compacted = compact_receipts(
        current,
        acknowledged_event_ids=(first.event_id, second.event_id),
        older_than=T0 + timedelta(minutes=1),
        keep_at_most=1,
    )
    final = reduce_order_event(compacted, third, max_receipts_per_order=2).record
    assert len(final["durable_event_receipts"]) == 2
    assert final["status"] == "picked_up"
    assert contract.max_receipts_per_order == 2


def test_retention_contract_blocks_short_dedup_and_compacts_only_after_horizon(
    durable_store,
):
    _state_dir, db_path, _default_contract = durable_store
    with pytest.raises(ValueError, match="dedup retention"):
        _contract(
            "unsafe",
            event_seconds=20,
            receipt_seconds=20,
            dedup_seconds=20,
            replay_seconds=5,
        )
    contract = _contract(
        "bounded-test-v1",
        event_seconds=10,
        receipt_seconds=10,
        dedup_seconds=20,
        replay_seconds=5,
    )
    envelope = _envelope("NEW_ORDER", "retention")
    intent = event_outbox.EffectIntent(
        consumer_id="synthetic_sink",
        effect_type="synthetic_effect",
        depends_on_consumer=None,
        retry_contract="idempotent",
        payload={"event_id": envelope.event_id},
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_outbox.register_retention_contract(conn, contract)
    _publish(conn, envelope, contract, intents=(intent,))
    claim = _claim(conn, envelope, "synthetic_sink")
    attempt = event_outbox.begin_attempt(conn, claim, started_at=T0 + timedelta(seconds=2))
    event_outbox.acknowledge_effect(
        conn, claim, attempt_number=attempt, acknowledged_at=T0 + timedelta(seconds=3)
    )
    assert event_outbox.retention_candidates(
        conn,
        policy_id=contract.policy_id,
        evaluated_at=T0 + timedelta(seconds=19),
        allow_test_policy=True,
    ) == []
    assert event_outbox.compact_retention(
        conn,
        policy_id=contract.policy_id,
        evaluated_at=T0 + timedelta(seconds=21),
        enabled=False,
        allow_test_policy=True,
    ) == 0
    assert conn.execute("SELECT COUNT(*) FROM event_envelopes WHERE event_id=?", (envelope.event_id,)).fetchone()[0] == 1
    assert event_outbox.compact_retention(
        conn,
        policy_id=contract.policy_id,
        evaluated_at=T0 + timedelta(seconds=21),
        enabled=True,
        allow_test_policy=True,
    ) == 1
    assert conn.execute("SELECT COUNT(*) FROM event_envelopes WHERE event_id=?", (envelope.event_id,)).fetchone()[0] == 0
    assert _publish(conn, envelope, contract, intents=(intent,)).inserted is True
    conn.close()


@pytest.mark.parametrize(
    ("closure_type", "closure_payload"),
    (
        (
            "COURIER_DELIVERED",
            {"timestamp": "2026-07-12T09:00:30+00:00"},
        ),
        (
            "ORDER_CANCELLED",
            {"reason": "synthetic", "source": "synthetic"},
        ),
        (
            "ORDER_RESURRECTED",
            {
                "new_status": "assigned",
                "reason": "synthetic",
                "source": "synthetic",
            },
        ),
    ),
)
def test_retention_never_compacts_order_state_history_without_snapshot(
    durable_store,
    closure_type,
    closure_payload,
):
    _state_dir, db_path, _default_contract = durable_store
    contract = _contract(
        f"state-history-hold-{closure_type}",
        event_seconds=10,
        receipt_seconds=10,
        dedup_seconds=20,
        replay_seconds=5,
    )
    new = _envelope("NEW_ORDER", "retention-active", created_at=T0)
    closure = _envelope(
        closure_type,
        f"retention-{closure_type}",
        created_at=T0 + timedelta(seconds=30),
        payload=closure_payload,
        courier_id=(
            "synthetic-courier" if closure_type == "ORDER_RESURRECTED" else None
        ),
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_outbox.register_retention_contract(conn, contract)
    _publish(conn, new, contract, intents=_state_only(new))
    new_claim = _claim(conn, new, event_outbox.STATE_CONSUMER)
    new_attempt = event_outbox.begin_attempt(
        conn, new_claim, started_at=T0 + timedelta(seconds=2)
    )
    event_outbox.acknowledge_effect(
        conn,
        new_claim,
        attempt_number=new_attempt,
        acknowledged_at=T0 + timedelta(seconds=3),
    )
    assert event_outbox.retention_candidates(
        conn,
        policy_id=contract.policy_id,
        evaluated_at=T0 + timedelta(seconds=25),
        allow_test_policy=True,
    ) == []

    _publish(conn, closure, contract, intents=_state_only(closure))
    closure_claim = _claim(
        conn,
        closure,
        event_outbox.STATE_CONSUMER,
        at=T0 + timedelta(seconds=31),
    )
    closure_attempt = event_outbox.begin_attempt(
        conn,
        closure_claim,
        started_at=T0 + timedelta(seconds=32),
    )
    event_outbox.acknowledge_effect(
        conn,
        closure_claim,
        attempt_number=closure_attempt,
        acknowledged_at=T0 + timedelta(seconds=33),
    )
    assert event_outbox.retention_candidates(
        conn,
        policy_id=contract.policy_id,
        evaluated_at=T0 + timedelta(seconds=51),
        allow_test_policy=True,
    ) == []
    assert event_outbox.compact_retention(
        conn,
        policy_id=contract.policy_id,
        evaluated_at=T0 + timedelta(seconds=51),
        enabled=True,
        allow_test_policy=True,
    ) == 0
    assert conn.execute(
        "SELECT COUNT(*) FROM event_envelopes WHERE order_id='synthetic-order'"
    ).fetchone()[0] == 2
    conn.close()


@pytest.mark.parametrize(
    "retention_mutation",
    (
        "missing_receipt",
        "pending_without_receipt",
        "status_divergence",
        "missing_terminal_at",
        "pending_with_terminal_at",
    ),
)
def test_retention_fails_closed_on_child_or_terminal_corruption(
    durable_store,
    retention_mutation,
):
    _state_dir, db_path, _default_contract = durable_store
    contract = _contract(
        f"retention-corruption-{retention_mutation}",
        event_seconds=10,
        receipt_seconds=10,
        dedup_seconds=20,
        replay_seconds=5,
    )
    envelope = _envelope("PANEL_UNREACHABLE", f"retention-{retention_mutation}")
    intent = event_outbox.EffectIntent(
        consumer_id="synthetic_sink",
        effect_type="synthetic_effect",
        depends_on_consumer=None,
        retry_contract="idempotent",
        payload={"event_id": envelope.event_id},
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_outbox.register_retention_contract(conn, contract)
    _publish(conn, envelope, contract, intents=(intent,), delivery_kind="audit")
    if retention_mutation not in {
        "pending_with_terminal_at",
        "pending_without_receipt",
    }:
        claim = _claim(conn, envelope, "synthetic_sink")
        attempt = event_outbox.begin_attempt(
            conn, claim, started_at=T0 + timedelta(seconds=2)
        )
        event_outbox.acknowledge_effect(
            conn,
            claim,
            attempt_number=attempt,
            acknowledged_at=T0 + timedelta(seconds=3),
        )
    if retention_mutation in {"missing_receipt", "pending_without_receipt"}:
        conn.execute(
            "DELETE FROM event_consumer_receipts WHERE event_id=?",
            (envelope.event_id,),
        )
    elif retention_mutation == "status_divergence":
        conn.execute(
            "UPDATE event_outbox SET status='pending' WHERE event_id=?",
            (envelope.event_id,),
        )
    elif retention_mutation == "missing_terminal_at":
        conn.execute(
            "UPDATE event_dedup_ledger SET terminal_at=NULL WHERE event_id=?",
            (envelope.event_id,),
        )
    else:
        conn.execute(
            "UPDATE event_dedup_ledger SET terminal_at=? WHERE event_id=?",
            (T0.isoformat(), envelope.event_id),
        )
    with pytest.raises(
        (event_outbox.ReceiptStateError, event_outbox.EnvelopeConflict)
    ):
        event_outbox.retention_candidates(
            conn,
            policy_id=contract.policy_id,
            evaluated_at=T0 + timedelta(seconds=30),
            allow_test_policy=True,
        )
    with pytest.raises(
        (event_outbox.ReceiptStateError, event_outbox.EnvelopeConflict)
    ):
        event_outbox.compact_retention(
            conn,
            policy_id=contract.policy_id,
            evaluated_at=T0 + timedelta(seconds=30),
            enabled=True,
            allow_test_policy=True,
        )
    assert conn.execute(
        "SELECT COUNT(*) FROM event_envelopes WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == 1
    conn.close()


@pytest.mark.parametrize(
    "delete_cas_table",
    ("event_consumer_receipts", "event_outbox", "event_dedup_ledger"),
)
def test_compact_retention_requires_child_and_dedup_delete_cas(
    durable_store,
    delete_cas_table,
):
    _state_dir, db_path, _default_contract = durable_store
    contract = _contract(
        f"retention-delete-cas-{delete_cas_table}",
        event_seconds=10,
        receipt_seconds=10,
        dedup_seconds=20,
        replay_seconds=5,
    )
    envelope = _envelope("PANEL_UNREACHABLE", f"delete-cas-{delete_cas_table}")
    intent = event_outbox.EffectIntent(
        consumer_id="synthetic_sink",
        effect_type="synthetic_effect",
        depends_on_consumer=None,
        retry_contract="idempotent",
        payload={"event_id": envelope.event_id},
    )
    conn = sqlite3.connect(db_path, isolation_level=None)
    event_outbox.register_retention_contract(conn, contract)
    _publish(conn, envelope, contract, intents=(intent,), delivery_kind="audit")
    claim = _claim(conn, envelope, "synthetic_sink")
    attempt = event_outbox.begin_attempt(
        conn, claim, started_at=T0 + timedelta(seconds=2)
    )
    event_outbox.acknowledge_effect(
        conn,
        claim,
        attempt_number=attempt,
        acknowledged_at=T0 + timedelta(seconds=3),
    )
    conn.execute(
        f"""CREATE TRIGGER synthetic_retention_delete_cas_ignore
            BEFORE DELETE ON {delete_cas_table}
            BEGIN SELECT RAISE(IGNORE); END"""
    )
    with pytest.raises(
        (event_outbox.ReceiptStateError, event_outbox.EnvelopeConflict)
    ):
        event_outbox.compact_retention(
            conn,
            policy_id=contract.policy_id,
            evaluated_at=T0 + timedelta(seconds=30),
            enabled=True,
            allow_test_policy=True,
        )
    for table in (
        "event_envelopes",
        "event_dedup_ledger",
        "event_outbox",
        "event_consumer_receipts",
        "event_consumer_attempts",
    ):
        assert conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE event_id=?",
            (envelope.event_id,),
        ).fetchone()[0] == 1
    conn.close()


def test_zero_consumer_event_is_terminal_at_publish_for_safe_retention(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("PANEL_UNREACHABLE", "zero-consumer-terminal")
    conn = sqlite3.connect(db_path, isolation_level=None)
    _publish(conn, envelope, contract, intents=(), delivery_kind="audit")
    assert conn.execute(
        "SELECT terminal_at FROM event_dedup_ledger WHERE event_id=?",
        (envelope.event_id,),
    ).fetchone()[0] == envelope.created_at
    assert _publish(
        conn,
        envelope,
        contract,
        intents=(),
        delivery_kind="audit",
    ).duplicate
    conn.execute(
        "UPDATE event_dedup_ledger SET terminal_at=NULL WHERE event_id=?",
        (envelope.event_id,),
    )
    with pytest.raises(event_outbox.EnvelopeConflict, match="terminal marker"):
        _publish(
            conn,
            envelope,
            contract,
            intents=(),
            delivery_kind="audit",
        )
    conn.close()


def test_event_bus_off_ignores_durable_arguments_and_on_requires_envelope(
    durable_store, monkeypatch
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "adapter")
    monkeypatch.setattr(event_bus, "now_iso", lambda: "2026-07-12T09:00:00+00:00")
    assert event_bus.emit(
        envelope.event_type,
        order_id=envelope.order_id,
        payload=envelope.payload,
        event_id=envelope.event_id,
        envelope=envelope,
        retention_policy_id="ignored-while-off",
    ) == envelope.event_id
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT created_at,status FROM events WHERE event_id=?", (envelope.event_id,)
    ).fetchone() == ("2026-07-12T09:00:00+00:00", "pending")
    assert conn.execute("SELECT COUNT(*) FROM event_envelopes").fetchone()[0] == 0
    conn.close()

    second = _envelope("NEW_ORDER", "adapter-on")
    monkeypatch.setattr(event_outbox, "DURABLE_EVENT_OUTBOX_ENABLED", True)
    with pytest.raises(ValueError, match="call-site envelope"):
        event_bus.emit(
            second.event_type,
            order_id=second.order_id,
            payload=second.payload,
            event_id=second.event_id,
            retention_policy_id=contract.policy_id,
        )
    assert event_bus.emit(
        second.event_type,
        order_id=second.order_id,
        payload=second.payload,
        event_id=second.event_id,
        envelope=second,
        retention_policy_id=contract.policy_id,
        allow_test_policy=True,
    ) == second.event_id


def test_lazy_envelope_factory_never_validates_off_and_fails_closed_on(
    durable_store,
    monkeypatch,
):
    state_dir, db_path, contract = durable_store
    calls = []

    def explode(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("synthetic envelope validation")

    monkeypatch.setattr(event_bus, "create_order_envelope", explode)
    kwargs = {
        "event_id": "lazy-off-event",
        "event_type": "NEW_ORDER",
        "order_id": "lazy-off-order",
        "courier_id": None,
        "payload": _payload("NEW_ORDER"),
        "created_at": None,
        "source": "synthetic:lazy",
        "policy_version": "synthetic.v1",
        "producer_key": "lazy-off-event",
    }
    assert event_bus.maybe_create_order_envelope(**kwargs) is None
    assert calls == []
    monkeypatch.setattr(event_bus, "now_iso", lambda: T0.isoformat())
    emitted = event_bus.emit(
        "NEW_ORDER",
        order_id="lazy-off-order",
        payload=_payload("NEW_ORDER"),
        event_id="lazy-off-event",
        **event_bus.durable_envelope_kwargs(None),
    )
    result = event_bus.apply_state_event(
        {
            "event_type": "NEW_ORDER",
            "order_id": "lazy-off-order",
            "courier_id": None,
            "payload": _payload("NEW_ORDER"),
        },
        event_id="lazy-off-event",
        emitted=bool(emitted),
        envelope=None,
    )
    assert result.record["status"] == "planned"
    assert json.loads(
        (state_dir / "orders_state.json").read_text(encoding="utf-8")
    )["lazy-off-order"]["status"] == "planned"
    conn = sqlite3.connect(db_path)
    assert conn.execute(
        "SELECT status FROM events WHERE event_id='lazy-off-event'"
    ).fetchone() == ("pending",)
    assert conn.execute("SELECT COUNT(*) FROM event_envelopes").fetchone()[0] == 0
    conn.close()

    monkeypatch.setattr(event_outbox, "DURABLE_EVENT_OUTBOX_ENABLED", True)
    with pytest.raises(RuntimeError, match="synthetic envelope validation"):
        event_bus.maybe_create_order_envelope(**kwargs)
    assert len(calls) == 1
    with pytest.raises(ValueError, match="call-site envelope"):
        event_bus.durable_envelope_kwargs(None)


def test_runtime_schema_checks_skip_fk_scan_but_explicit_inspection_runs_it(
    durable_store,
):
    _state_dir, db_path, contract = durable_store
    envelope = _envelope("NEW_ORDER", "runtime-schema-cost")
    conn = sqlite3.connect(db_path, isolation_level=None)
    traced = []
    conn.set_trace_callback(traced.append)
    event_outbox.require_schema(conn)
    event_outbox.load_retention_contract(
        conn,
        contract.policy_id,
        allow_test_policy=True,
    )
    _publish(conn, envelope, contract, intents=_state_only(envelope))
    assert _claim(conn, envelope, event_outbox.STATE_CONSUMER) is not None
    assert not any("foreign_key_check" in sql.lower() for sql in traced)

    traced.clear()
    inspected = durable_event_outbox.inspect_connection(conn)
    assert inspected["foreign_key_violations"]["checked"] is True
    assert any("foreign_key_check" in sql.lower() for sql in traced)
    conn.close()


def test_no_worker_or_policy_is_enabled_by_source():
    assert event_outbox.DURABLE_EVENT_OUTBOX_ENABLED is False
    assert not hasattr(event_outbox, "SELECTED_RETENTION_POLICY_ID")
    assert not hasattr(event_outbox, "run")
    assert not hasattr(event_outbox, "main")
