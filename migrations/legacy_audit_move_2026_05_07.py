"""Opcja C (2026-05-07) — legacy audit move migration.

Cel: Przenieść istniejące eventy z 4 audit typów (COURIER_ASSIGNED,
CZAS_KURIERA_UPDATED, PANEL_UNREACHABLE, ORDER_RETURNED_TO_POOL) z tabeli
`events` (status='pending') do tabeli `audit_log`. Po migracji:
- audit_log zawiera pełną historię (legacy + nowe emit_audit)
- events zawiera tylko queue typy (NEW_ORDER + SLA + ...)
- WORKER_STUCK alert mierzy real queue backlog (~0)

CLI:
    python -m dispatch_v2.migrations.legacy_audit_move_2026_05_07 --dry-run
    python -m dispatch_v2.migrations.legacy_audit_move_2026_05_07 --apply
    python -m dispatch_v2.migrations.legacy_audit_move_2026_05_07 --apply --db /path/to/events.db

Idempotent: INSERT OR IGNORE na PK + DELETE WHERE status='pending'.
Atomic: BEGIN IMMEDIATE...COMMIT, ROLLBACK na error.

Pre-condition: events.db existing + .bak istnieje przed apply.
Post-condition: events pending audit-types == 0; audit_log count grew by N.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional

# Path setup dla import event_bus dla AUDIT_EVENT_TYPES
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import event_bus  # noqa: E402
from dispatch_v2.common import load_config, setup_logger  # noqa: E402

_log = setup_logger(
    "legacy_audit_move",
    "/root/.openclaw/workspace/scripts/logs/legacy_audit_move_2026_05_07.log",
)

AUDIT_TYPES = sorted(event_bus.AUDIT_EVENT_TYPES)


def _placeholders(types: list) -> str:
    return ",".join("?" * len(types))


def _count_pending_audit(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        f"SELECT COUNT(*) FROM events WHERE status='pending' AND event_type IN ({_placeholders(AUDIT_TYPES)})",
        tuple(AUDIT_TYPES),
    )
    return cur.fetchone()[0]


def _count_audit_log(conn: sqlite3.Connection) -> int:
    cur = conn.execute("SELECT COUNT(*) FROM audit_log")
    return cur.fetchone()[0]


def _per_type_breakdown(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        f"""SELECT event_type, COUNT(*) FROM events
            WHERE status='pending' AND event_type IN ({_placeholders(AUDIT_TYPES)})
            GROUP BY event_type""",
        tuple(AUDIT_TYPES),
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def dry_run(db_path: str) -> int:
    """Zwraca count audit-pending w events.db, NIE modyfikuje."""
    conn = sqlite3.connect(db_path)
    try:
        # Verify audit_log table exists
        try:
            audit_pre = _count_audit_log(conn)
        except sqlite3.OperationalError:
            _log.error("DRY_RUN: audit_log table NIE ISTNIEJE — Etap 1 _init_audit_log_table NIE wywołane jeszcze")
            return 1

        events_pending_audit = _count_pending_audit(conn)
        breakdown = _per_type_breakdown(conn)

        _log.info(
            f"DRY_RUN db={db_path}\n"
            f"  events pending audit-types: {events_pending_audit}\n"
            f"  audit_log existing: {audit_pre}\n"
            f"  breakdown: {breakdown}\n"
            f"  WOULD_MIGRATE {events_pending_audit} rows: events → audit_log\n"
            f"  WOULD_DELETE {events_pending_audit} rows from events table"
        )
        return 0
    finally:
        conn.close()


def apply(db_path: str) -> int:
    """Atomic migration: INSERT OR IGNORE do audit_log + DELETE z events."""
    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    try:
        try:
            audit_pre = _count_audit_log(conn)
        except sqlite3.OperationalError:
            _log.error("APPLY: audit_log table NIE ISTNIEJE — RUN abort")
            return 1

        events_pending_audit_pre = _count_pending_audit(conn)
        breakdown_pre = _per_type_breakdown(conn)
        _log.info(
            f"APPLY START db={db_path} "
            f"events_pending_audit={events_pending_audit_pre} "
            f"audit_log_pre={audit_pre} breakdown={breakdown_pre}"
        )

        if events_pending_audit_pre == 0:
            _log.info("APPLY no-op: events ma 0 audit-pending. Already migrated lub idempotent re-run.")
            return 0

        t0 = time.time()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # 1. Copy do audit_log (idempotent przez PK INSERT OR IGNORE)
            ins = conn.execute(
                f"""INSERT OR IGNORE INTO audit_log
                    (event_id, event_type, order_id, courier_id, payload, created_at)
                    SELECT event_id, event_type, order_id, courier_id, payload, created_at
                    FROM events
                    WHERE status='pending' AND event_type IN ({_placeholders(AUDIT_TYPES)})""",
                tuple(AUDIT_TYPES),
            )
            inserted = ins.rowcount

            # 2. DELETE z events tylko te, które już są w audit_log (safety)
            #    UWAGA: status='pending' filter zapobiega usunięciu świeżych processed.
            del_cur = conn.execute(
                f"""DELETE FROM events
                    WHERE status='pending'
                      AND event_type IN ({_placeholders(AUDIT_TYPES)})
                      AND event_id IN (SELECT event_id FROM audit_log)""",
                tuple(AUDIT_TYPES),
            )
            deleted = del_cur.rowcount

            # 3. Sanity: deleted == events_pending_audit_pre (assuming zero collisions)
            if deleted != events_pending_audit_pre:
                _log.warning(
                    f"APPLY mismatch: deleted={deleted} != pre={events_pending_audit_pre}. "
                    f"Możliwe race z live emit() — but acceptable, rollback NIE."
                )

            conn.execute("COMMIT;")
            elapsed_ms = (time.time() - t0) * 1000.0

            audit_post = _count_audit_log(conn)
            events_pending_audit_post = _count_pending_audit(conn)

            _log.info(
                f"APPLY DONE elapsed_ms={elapsed_ms:.1f} "
                f"inserted={inserted} deleted={deleted} "
                f"audit_log_pre={audit_pre} audit_log_post={audit_post} "
                f"events_pending_audit_post={events_pending_audit_post}"
            )

            # Post-condition verify
            if events_pending_audit_post != 0:
                _log.error(
                    f"POST_VERIFY FAIL: events pending audit-types = {events_pending_audit_post} "
                    f"(expected 0). Możliwy race ze świeżym emit() — sprawdź event_bus call sites."
                )
                return 2

            audit_delta = audit_post - audit_pre
            if audit_delta < inserted:
                _log.error(
                    f"POST_VERIFY FAIL: audit_log delta={audit_delta} < inserted={inserted}. "
                    f"Sprawdź audit_log integrity."
                )
                return 2

            _log.info(f"POST_VERIFY OK delta_audit_log={audit_delta} pending_remaining=0")
            return 0
        except Exception as e:
            conn.execute("ROLLBACK;")
            _log.error(f"APPLY ROLLBACK error: {type(e).__name__}: {e}")
            return 3
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Opcja C legacy audit move migration")
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--dry-run", action="store_true", help="count only, no DELETE")
    grp.add_argument("--apply", action="store_true", help="execute migration")
    parser.add_argument("--db", type=str, default=None, help="events.db path (default: prod)")
    args = parser.parse_args()

    db_path = args.db or load_config()["paths"]["events_db"]
    _log.info(f"START mode={'apply' if args.apply else 'dry-run'} db={db_path} audit_types={AUDIT_TYPES}")

    if args.dry_run:
        return dry_run(db_path)
    if args.apply:
        return apply(db_path)
    return 1


if __name__ == "__main__":
    sys.exit(main())
