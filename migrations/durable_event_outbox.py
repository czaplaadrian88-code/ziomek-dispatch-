"""Addytywny schemat A360-E1: envelope, outbox i receipts per consumer.

Import jest czysty. CLI bez ``--apply`` otwiera jawnie wskazana baze w trybie
read-only. Kod nie zna sciezki produkcyjnej i nie wybiera polityki retencji.
Nie istnieje destrukcyjna migracja w dol; evidence zostaje zachowane.
Mutujace ``--apply`` wymaga jawnego ``--synthetic-sandbox`` i celu pod ``/tmp``;
znane live DB, hardlinki i symlinki sa odrzucane przed otwarciem SQLite.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from dispatch_v2 import event_retry
from dispatch_v2.migrations import event_retry_metadata
from dispatch_v2.migrations.synthetic_target_guard import (
    require_synthetic_connection,
    require_synthetic_migration_target,
)


MIGRATION_ID = "a360_e1_durable_event_outbox_v1"

TABLE_STATEMENTS: tuple[tuple[str, str], ...] = (
    (
        "event_retention_policies",
        """CREATE TABLE event_retention_policies (
            policy_id TEXT PRIMARY KEY,
            event_retention_seconds INTEGER NOT NULL CHECK(event_retention_seconds > 0),
            receipt_retention_seconds INTEGER NOT NULL CHECK(receipt_retention_seconds > 0),
            dedup_retention_seconds INTEGER NOT NULL CHECK(dedup_retention_seconds > 0),
            max_replay_age_seconds INTEGER NOT NULL CHECK(max_replay_age_seconds >= 0),
            max_receipts_per_order INTEGER NOT NULL CHECK(max_receipts_per_order > 0),
            created_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('test_only','approved','retired')),
            CHECK(dedup_retention_seconds >= event_retention_seconds + max_replay_age_seconds),
            CHECK(dedup_retention_seconds >= receipt_retention_seconds + max_replay_age_seconds)
        )""",
    ),
    (
        "event_envelopes",
        """CREATE TABLE event_envelopes (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            order_id TEXT,
            courier_id TEXT,
            payload TEXT NOT NULL,
            payload_sha256 TEXT NOT NULL,
            created_at TEXT NOT NULL,
            source TEXT NOT NULL,
            envelope_version TEXT NOT NULL,
            policy_version TEXT NOT NULL,
            producer_key TEXT NOT NULL,
            identity_scheme TEXT NOT NULL,
            delivery_kind TEXT NOT NULL CHECK(delivery_kind IN ('queue','audit')),
            retention_policy_id TEXT NOT NULL,
            FOREIGN KEY(retention_policy_id)
                REFERENCES event_retention_policies(policy_id) ON DELETE RESTRICT
        )""",
    ),
    (
        "event_dedup_ledger",
        """CREATE TABLE event_dedup_ledger (
            idempotency_key TEXT PRIMARY KEY,
            event_id TEXT NOT NULL UNIQUE,
            payload_sha256 TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            dedup_until TEXT NOT NULL,
            retention_policy_id TEXT NOT NULL,
            terminal_at TEXT,
            FOREIGN KEY(event_id) REFERENCES event_envelopes(event_id) ON DELETE RESTRICT,
            FOREIGN KEY(retention_policy_id)
                REFERENCES event_retention_policies(policy_id) ON DELETE RESTRICT
        )""",
    ),
    (
        "event_outbox",
        """CREATE TABLE event_outbox (
            event_id TEXT NOT NULL,
            consumer_id TEXT NOT NULL,
            effect_type TEXT NOT NULL,
            effect_payload TEXT NOT NULL,
            effect_idempotency_key TEXT NOT NULL UNIQUE,
            depends_on_consumer TEXT,
            retry_contract TEXT NOT NULL
                CHECK(retry_contract IN ('idempotent','confirm_before_retry')),
            status TEXT NOT NULL
                CHECK(status IN ('pending','claimed','executing','acknowledged',
                                 'failed','effect_unknown','quarantined')),
            created_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(event_id, consumer_id),
            FOREIGN KEY(event_id) REFERENCES event_envelopes(event_id) ON DELETE RESTRICT,
            FOREIGN KEY(event_id, depends_on_consumer)
                REFERENCES event_outbox(event_id, consumer_id)
                ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED
        )""",
    ),
    (
        "event_consumer_receipts",
        """CREATE TABLE event_consumer_receipts (
            event_id TEXT NOT NULL,
            consumer_id TEXT NOT NULL,
            status TEXT NOT NULL
                CHECK(status IN ('pending','claimed','executing','acknowledged',
                                 'failed','effect_unknown','quarantined')),
            attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
            lease_owner TEXT,
            lease_expires_at TEXT,
            last_attempt_at TEXT,
            acknowledged_at TEXT,
            last_error_code TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(event_id, consumer_id),
            FOREIGN KEY(event_id, consumer_id)
                REFERENCES event_outbox(event_id, consumer_id) ON DELETE RESTRICT
        )""",
    ),
    (
        "event_consumer_attempts",
        """CREATE TABLE event_consumer_attempts (
            event_id TEXT NOT NULL,
            consumer_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL CHECK(attempt_number > 0),
            worker_ref TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            outcome TEXT NOT NULL
                CHECK(outcome IN ('executing','acknowledged','failed','effect_unknown')),
            error_code TEXT,
            PRIMARY KEY(event_id, consumer_id, attempt_number),
            FOREIGN KEY(event_id, consumer_id)
                REFERENCES event_outbox(event_id, consumer_id) ON DELETE RESTRICT
        )""",
    ),
    (
        "event_failure_journal",
        """CREATE TABLE event_failure_journal (
            failure_id TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            consumer_id TEXT NOT NULL,
            attempt_number INTEGER NOT NULL CHECK(attempt_number >= 0),
            failed_at TEXT NOT NULL,
            failure_class TEXT NOT NULL
                CHECK(failure_class IN ('transient','permanent','illegal')),
            error_code TEXT NOT NULL,
            disposition TEXT NOT NULL
                CHECK(disposition IN ('failed','effect_unknown','quarantined')),
            FOREIGN KEY(event_id) REFERENCES event_envelopes(event_id) ON DELETE RESTRICT,
            FOREIGN KEY(event_id, consumer_id)
                REFERENCES event_outbox(event_id, consumer_id) ON DELETE RESTRICT
        )""",
    ),
)

INDEX_STATEMENTS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "idx_event_envelopes_order_time",
        "event_envelopes",
        "CREATE INDEX idx_event_envelopes_order_time "
        "ON event_envelopes(order_id, created_at, event_id)",
        ("order_id", "created_at", "event_id"),
    ),
    (
        "idx_event_dedup_expiry",
        "event_dedup_ledger",
        "CREATE INDEX idx_event_dedup_expiry "
        "ON event_dedup_ledger(dedup_until, event_id)",
        ("dedup_until", "event_id"),
    ),
    (
        "idx_event_outbox_ready",
        "event_outbox",
        "CREATE INDEX idx_event_outbox_ready "
        "ON event_outbox(status, available_at, created_at, event_id)",
        ("status", "available_at", "created_at", "event_id"),
    ),
    (
        "idx_event_receipts_lease",
        "event_consumer_receipts",
        "CREATE INDEX idx_event_receipts_lease "
        "ON event_consumer_receipts(status, lease_expires_at)",
        ("status", "lease_expires_at"),
    ),
    (
        "idx_event_failures_event",
        "event_failure_journal",
        "CREATE INDEX idx_event_failures_event "
        "ON event_failure_journal(event_id, consumer_id, failed_at)",
        ("event_id", "consumer_id", "failed_at"),
    ),
)

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    name: () for name, _statement in TABLE_STATEMENTS
}
EXPECTED_COLUMNS.update({
    "event_retention_policies": (
        "policy_id", "event_retention_seconds", "receipt_retention_seconds",
        "dedup_retention_seconds", "max_replay_age_seconds",
        "max_receipts_per_order", "created_at", "status",
    ),
    "event_envelopes": (
        "event_id", "event_type", "order_id", "courier_id", "payload",
        "payload_sha256", "created_at", "source", "envelope_version",
        "policy_version", "producer_key", "identity_scheme", "delivery_kind",
        "retention_policy_id",
    ),
    "event_dedup_ledger": (
        "idempotency_key", "event_id", "payload_sha256", "first_seen_at",
        "dedup_until", "retention_policy_id", "terminal_at",
    ),
    "event_outbox": (
        "event_id", "consumer_id", "effect_type", "effect_payload",
        "effect_idempotency_key", "depends_on_consumer", "retry_contract",
        "status", "created_at", "available_at", "updated_at",
    ),
    "event_consumer_receipts": (
        "event_id", "consumer_id", "status", "attempt_count", "lease_owner",
        "lease_expires_at", "last_attempt_at", "acknowledged_at",
        "last_error_code", "updated_at",
    ),
    "event_consumer_attempts": (
        "event_id", "consumer_id", "attempt_number", "worker_ref",
        "started_at", "finished_at", "outcome", "error_code",
    ),
    "event_failure_journal": (
        "failure_id", "event_id", "consumer_id", "attempt_number",
        "failed_at", "failure_class", "error_code", "disposition",
    ),
})


def _db_uri(db_path: str, *, mode: str) -> str:
    return f"{Path(db_path).expanduser().resolve().as_uri()}?mode={mode}"


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }


def _columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")'))


def _normalise_ddl(value: Any) -> str:
    return " ".join(str(value or "").strip().rstrip(";").split()).lower()


def _index_spec(
    conn: sqlite3.Connection, name: str
) -> tuple[Optional[str], tuple[str, ...]]:
    owner = conn.execute(
        "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    if owner is None:
        return None, ()
    columns = tuple(
        str(row[2]) for row in conn.execute(f'PRAGMA index_info("{name}")')
    )
    return str(owner[0]), columns


def _foreign_key_check(
    conn: sqlite3.Connection,
    *,
    tables: set[str],
    schema_complete: bool,
) -> dict[str, Any]:
    """Redagowany FK audit: tylko count i kanoniczne nazwy tabel."""
    if not schema_complete:
        return {"checked": False, "count": 0, "tables": []}
    count = 0
    violated_tables: set[str] = set()
    for table, _statement in TABLE_STATEMENTS:
        rows = conn.execute(f'PRAGMA foreign_key_check("{table}")').fetchall()
        if rows:
            count += len(rows)
            violated_tables.add(table)
    return {
        "checked": True,
        "count": count,
        "tables": sorted(violated_tables & tables),
    }


def inspect_connection(
    conn: sqlite3.Connection,
    *,
    verify_e0: bool = True,
    verify_data: bool = True,
) -> dict[str, Any]:
    # Pelny dry-run/apply weryfikuje rowniez backfill E0. Hot-path E1 moze
    # sprawdzic tylko wlasna strukture po tym, jak migracja przeszla bramkę;
    # nie wykonujemy COUNT calej legacy tabeli przy kazdym publish/claim.
    e0_prerequisite = (
        event_retry_metadata.inspect_connection(conn)
        if verify_e0
        else {"ready": True, "runtime_check_skipped": True}
    )
    existing = _tables(conn)
    missing_tables = [name for name, _statement in TABLE_STATEMENTS if name not in existing]
    invalid_tables: dict[str, Any] = {}
    expected_statements = dict(TABLE_STATEMENTS)
    for name, expected in EXPECTED_COLUMNS.items():
        if name not in existing:
            continue
        actual = _columns(conn, name)
        if actual != expected:
            invalid_tables[name] = {"expected": expected, "actual": actual}
            continue
        ddl_row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        actual_ddl = _normalise_ddl(ddl_row[0] if ddl_row else None)
        expected_ddl = _normalise_ddl(expected_statements[name])
        if actual_ddl != expected_ddl:
            invalid_tables[name] = {
                "expected_ddl": expected_ddl,
                "actual_ddl": actual_ddl,
            }

    missing_indexes: list[str] = []
    invalid_indexes: dict[str, Any] = {}
    for name, table, _statement, expected_columns in INDEX_STATEMENTS:
        owner, actual_columns = _index_spec(conn, name)
        if owner is None:
            missing_indexes.append(name)
        else:
            ddl_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
                (name,),
            ).fetchone()
            actual_ddl = _normalise_ddl(ddl_row[0] if ddl_row else None)
            expected_ddl = _normalise_ddl(_statement)
            if (
                owner != table
                or actual_columns != expected_columns
                or actual_ddl != expected_ddl
            ):
                invalid_indexes[name] = {
                    "expected_table": table,
                    "actual_table": owner,
                    "expected_columns": expected_columns,
                    "actual_columns": actual_columns,
                    "expected_ddl": expected_ddl,
                    "actual_ddl": actual_ddl,
                }
    policy_count = 0
    if "event_retention_policies" in existing and not invalid_tables.get(
        "event_retention_policies"
    ):
        policy_count = int(
            conn.execute("SELECT COUNT(*) FROM event_retention_policies").fetchone()[0]
        )
    foreign_key_violations = _foreign_key_check(
        conn,
        tables=existing,
        schema_complete=(
            verify_data and not missing_tables and not invalid_tables
        ),
    )
    ready = e0_prerequisite["ready"] and not (
        missing_tables or invalid_tables or missing_indexes or invalid_indexes
    ) and foreign_key_violations["count"] == 0
    return {
        "migration_id": MIGRATION_ID,
        "e0_prerequisite_ready": bool(e0_prerequisite["ready"]),
        "e0_prerequisite": e0_prerequisite,
        "missing_tables": missing_tables,
        "missing_indexes": missing_indexes,
        "invalid_tables": invalid_tables,
        "invalid_indexes": invalid_indexes,
        "foreign_key_violations": foreign_key_violations,
        "retention_policy_count": policy_count,
        "policy_selected": False,
        "ready": ready,
    }


def inspect(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(_db_uri(db_path, mode="ro"), uri=True, timeout=5.0)
    try:
        return inspect_connection(conn)
    finally:
        conn.close()


def apply_to_connection(
    conn: sqlite3.Connection,
    *,
    synthetic_sandbox: bool = False,
) -> dict[str, Any]:
    """Tworzy caly schemat atomowo; nie dodaje zadnego kontraktu/policy."""
    require_synthetic_connection(conn, synthetic_sandbox=synthetic_sandbox)
    if conn.in_transaction:
        raise RuntimeError("migration requires a connection outside a transaction")
    before = inspect_connection(conn)
    if not before["e0_prerequisite_ready"]:
        raise RuntimeError(
            "E0 retry/idempotency migration prerequisite is not complete"
        )
    if before["invalid_tables"] or before["invalid_indexes"]:
        raise RuntimeError(f"incompatible durable outbox schema: {before}")
    if before["foreign_key_violations"]["count"]:
        raise RuntimeError("durable outbox foreign-key integrity check failed")

    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        locked = inspect_connection(conn)
        if locked["invalid_tables"] or locked["invalid_indexes"]:
            raise RuntimeError(f"incompatible durable outbox schema: {locked}")
        if locked["foreign_key_violations"]["count"]:
            raise RuntimeError("durable outbox foreign-key integrity check failed")
        existing = _tables(conn)
        for name, statement in TABLE_STATEMENTS:
            if name not in existing:
                conn.execute(statement)
                existing.add(name)
        for name, _table, statement, _columns_expected in INDEX_STATEMENTS:
            owner, _actual = _index_spec(conn, name)
            if owner is None:
                conn.execute(statement)
        inside = inspect_connection(conn)
        if not inside["ready"]:
            raise RuntimeError(f"durable outbox migration incomplete: {inside}")
        conn.execute("COMMIT")
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    after = inspect_connection(conn)
    if not after["ready"]:
        raise RuntimeError(f"durable outbox migration incomplete: {after}")
    return {"before": before, "after": after}


def apply(
    db_path: str,
    *,
    synthetic_sandbox: bool = False,
) -> dict[str, Any]:
    guarded_path = require_synthetic_migration_target(
        db_path,
        synthetic_sandbox=synthetic_sandbox,
    )
    conn = sqlite3.connect(
        _db_uri(str(guarded_path), mode="rw"),
        uri=True,
        timeout=10.0,
        isolation_level=None,
    )
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        return apply_to_connection(conn, synthetic_sandbox=True)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A360-E1 durable outbox migration")
    parser.add_argument("--db", required=True, help="jawna sciezka do events.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="wykonaj addytywna migracje (default: read-only inspect)",
    )
    parser.add_argument(
        "--synthetic-sandbox",
        action="store_true",
        help="wymagane z --apply; cel musi byc kanonicznym plikiem pod /tmp",
    )
    args = parser.parse_args(argv)
    try:
        result = (
            apply(args.db, synthetic_sandbox=args.synthetic_sandbox)
            if args.apply
            else inspect(args.db)
        )
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        descriptor = event_retry.classify_failure(exc)
        print(json.dumps({
            "ok": False,
            "error_class": descriptor.failure_class.value,
            "error_code": descriptor.error_code,
        }, ensure_ascii=False))
        return 2
    print(json.dumps({
        "ok": True,
        "applied": bool(args.apply),
        "result": result,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
