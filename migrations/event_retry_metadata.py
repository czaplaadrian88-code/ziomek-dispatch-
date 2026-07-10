"""Addytywna migracja metadanych retry/DLQ dla ``events``.

Bez argumentu ``--apply`` CLI wykonuje wylacznie inspekcje read-only. Import
modulu jest czysty: nie otwiera bazy, nie czyta konfiguracji runtime i nie
tworzy tabel/indeksow.

Przyklady (uruchomienie produkcyjne wymaga osobnego ACK)::

    python -m dispatch_v2.migrations.event_retry_metadata --db /tmp/events.db
    python -m dispatch_v2.migrations.event_retry_metadata --db /tmp/events.db --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_error", "TEXT"),
    ("next_attempt_at", "TEXT"),
    ("last_failed_at", "TEXT"),
    ("dead_lettered_at", "TEXT"),
    ("replay_count", "INTEGER NOT NULL DEFAULT 0"),
    ("last_replayed_at", "TEXT"),
    ("last_replay_reason", "TEXT"),
)

MIGRATION_INDEXES: tuple[tuple[str, str], ...] = (
    (
        "idx_events_retry_due",
        "CREATE INDEX IF NOT EXISTS idx_events_retry_due "
        "ON events(status, next_attempt_at, created_at)",
    ),
    (
        "idx_events_dead_letter",
        "CREATE INDEX IF NOT EXISTS idx_events_dead_letter "
        "ON events(status, dead_lettered_at)",
    ),
)

EXPECTED_COLUMN_SPECS: dict[str, tuple[str, int, str | None]] = {
    "attempt_count": ("INTEGER", 1, "0"),
    "last_error": ("TEXT", 0, None),
    "next_attempt_at": ("TEXT", 0, None),
    "last_failed_at": ("TEXT", 0, None),
    "dead_lettered_at": ("TEXT", 0, None),
    "replay_count": ("INTEGER", 1, "0"),
    "last_replayed_at": ("TEXT", 0, None),
    "last_replay_reason": ("TEXT", 0, None),
}

EXPECTED_INDEX_COLUMNS: dict[str, tuple[str, ...]] = {
    "idx_events_retry_due": ("status", "next_attempt_at", "created_at"),
    "idx_events_dead_letter": ("status", "dead_lettered_at"),
}


def _db_uri(db_path: str, *, mode: str) -> str:
    """Bezpieczny SQLite URI; znaki ``?``/``#`` w nazwie sa percent-encoded."""
    return f"{Path(db_path).expanduser().resolve().as_uri()}?mode={mode}"


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _columns(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "events"):
        return set()
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(events)")}


def _indexes(conn: sqlite3.Connection) -> set[str]:
    if not _table_exists(conn, "events"):
        return set()
    return {str(row[1]) for row in conn.execute("PRAGMA index_list(events)")}


def _normalise_default(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    while text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    return text.strip("'\"")


def _invalid_columns(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    rows = {
        str(row[1]): row
        for row in conn.execute("PRAGMA table_info(events)").fetchall()
    }
    invalid: dict[str, dict[str, Any]] = {}
    for name, expected in EXPECTED_COLUMN_SPECS.items():
        row = rows.get(name)
        if row is None:
            continue
        actual = (
            str(row[2] or "").upper(),
            int(row[3]),
            _normalise_default(row[4]),
        )
        if actual != expected:
            invalid[name] = {"expected": expected, "actual": actual}
    return invalid


def _invalid_indexes(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    event_indexes = {
        str(row[1]): row
        for row in conn.execute("PRAGMA index_list(events)").fetchall()
    }
    invalid: dict[str, dict[str, Any]] = {}
    for name, expected_columns in EXPECTED_INDEX_COLUMNS.items():
        master = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?",
            (name,),
        ).fetchone()
        if master is None:
            continue
        owner = str(master[0])
        listed = event_indexes.get(name)
        if owner != "events" or listed is None:
            invalid[name] = {
                "expected_table": "events",
                "actual_table": owner,
            }
            continue
        actual_columns = tuple(
            str(row[2])
            for row in conn.execute(f'PRAGMA index_info("{name}")').fetchall()
        )
        unique = int(listed[2])
        partial = int(listed[4]) if len(listed) > 4 else 0
        if actual_columns != expected_columns or unique or partial:
            invalid[name] = {
                "expected_columns": expected_columns,
                "actual_columns": actual_columns,
                "unique": unique,
                "partial": partial,
            }
    return invalid


def inspect_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    """Zwraca plan migracji bez zapisu."""
    existing_columns = _columns(conn)
    existing_indexes = _indexes(conn)
    table_exists = bool(existing_columns) or _table_exists(conn, "events")
    missing_columns = [name for name, _ in MIGRATION_COLUMNS if name not in existing_columns]
    invalid_columns = _invalid_columns(conn) if table_exists else {}
    invalid_indexes = _invalid_indexes(conn) if table_exists else {}
    missing_indexes = [
        name
        for name, _ in MIGRATION_INDEXES
        if name not in existing_indexes and name not in invalid_indexes
    ]
    return {
        "events_table_exists": table_exists,
        "missing_columns": missing_columns,
        "missing_indexes": missing_indexes,
        "invalid_columns": invalid_columns,
        "invalid_indexes": invalid_indexes,
        "ready": (
            table_exists
            and not missing_columns
            and not missing_indexes
            and not invalid_columns
            and not invalid_indexes
        ),
    }


def inspect(db_path: str) -> dict[str, Any]:
    """Read-only inspekcja pliku. Nie utworzy brakujacej bazy."""
    uri = _db_uri(db_path, mode="ro")
    conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    try:
        return inspect_connection(conn)
    finally:
        conn.close()


def apply_to_connection(conn: sqlite3.Connection) -> dict[str, Any]:
    """Atomowo i idempotentnie dodaje kolumny oraz indeksy do test/ACK DB."""
    before = inspect_connection(conn)
    if not before["events_table_exists"]:
        raise RuntimeError("events table does not exist")
    if before["invalid_columns"] or before["invalid_indexes"]:
        raise RuntimeError(f"incompatible retry metadata schema: {before}")
    if conn.in_transaction:
        raise RuntimeError("migration requires a connection outside a transaction")

    conn.execute("BEGIN IMMEDIATE")
    try:
        locked_before = inspect_connection(conn)
        if locked_before["invalid_columns"] or locked_before["invalid_indexes"]:
            raise RuntimeError(
                f"incompatible retry metadata schema: {locked_before}"
            )
        existing = _columns(conn)
        for name, declaration in MIGRATION_COLUMNS:
            if name not in existing:
                conn.execute(f"ALTER TABLE events ADD COLUMN {name} {declaration}")
                existing.add(name)
        for _, statement in MIGRATION_INDEXES:
            conn.execute(statement)
        inside = inspect_connection(conn)
        if not inside["ready"]:
            raise RuntimeError(f"retry metadata migration incomplete: {inside}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    after = inspect_connection(conn)
    if not after["ready"]:
        raise RuntimeError(f"retry metadata migration incomplete: {after}")
    return {"before": before, "after": after}


def apply(db_path: str) -> dict[str, Any]:
    conn = sqlite3.connect(
        _db_uri(db_path, mode="rw"),
        uri=True,
        timeout=10.0,
        isolation_level=None,
    )
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        return apply_to_connection(conn)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Z-P0-05 retry metadata migration")
    parser.add_argument("--db", required=True, help="jawna sciezka do events.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="wykonaj addytywna migracje (default: read-only inspect)",
    )
    args = parser.parse_args(argv)
    try:
        result = apply(args.db) if args.apply else inspect(args.db)
    except (OSError, sqlite3.Error, RuntimeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, "applied": bool(args.apply), "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
