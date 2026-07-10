"""Bezpieczne narzedzie inspekcji i jawnego requeue DLQ (Z-P0-05 Faza A).

Default jest read-only. Requeue wymaga lacznie:

* ``--requeue EVENT_ID``;
* ``--reason`` (slad audytowy);
* jawnego wyboru ``--reset-attempts`` albo ``--preserve-attempts``;
* ``--confirm-requeue``.

Narzędzie zachowuje event ID i payload. Nie wykonuje eventu inline -- po
przyszlym wlaczeniu consumera event przejdzie przez jego zwykla sciezke.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dispatch_v2 import event_retry


def _db_uri(db_path: str, *, mode: str) -> str:
    return f"{Path(db_path).expanduser().resolve().as_uri()}?mode={mode}"


def list_dead_letters(
    db_path: str,
    *,
    limit: int = 100,
    event_type: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Read-only listing; brak pliku nie tworzy pustej bazy."""
    conn = sqlite3.connect(
        _db_uri(db_path, mode="ro"), uri=True, timeout=5.0
    )
    conn.row_factory = sqlite3.Row
    try:
        event_retry.require_retry_schema(conn)
        params: list[Any] = [event_retry.DEAD_LETTER_STATUS]
        type_clause = ""
        if event_type:
            type_clause = " AND event_type=?"
            params.append(event_type)
        params.append(max(1, int(limit)))
        rows = conn.execute(
            """SELECT event_id, event_type, order_id, courier_id, created_at,
                      attempt_count, last_error, last_failed_at,
                      dead_lettered_at, replay_count, last_replayed_at,
                      last_replay_reason
               FROM events WHERE status=?"""
            + type_clause
            + " ORDER BY dead_lettered_at ASC, created_at ASC, event_id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def requeue(
    db_path: str,
    event_id: str,
    *,
    reason: str,
    reset_attempt_count: bool,
    confirmed: bool = False,
    replayed_at: Optional[datetime] = None,
) -> bool:
    """Jawny requeue. ``confirmed=False`` jest gwarantowanym no-op."""
    if not confirmed:
        return False
    conn = sqlite3.connect(
        _db_uri(db_path, mode="rw"),
        uri=True,
        timeout=10.0,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    try:
        return event_retry.requeue_dead_letter(
            conn,
            event_id,
            reset_attempt_count=reset_attempt_count,
            reason=reason,
            replayed_at=replayed_at or datetime.now(timezone.utc),
            enabled=True,
        )
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Z-P0-05 DLQ inspect/requeue")
    parser.add_argument("--db", required=True, help="jawna sciezka do events.db")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--event-type")
    parser.add_argument("--requeue", metavar="EVENT_ID")
    parser.add_argument("--reason")
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--reset-attempts", action="store_true")
    choice.add_argument("--preserve-attempts", action="store_true")
    parser.add_argument("--confirm-requeue", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.requeue:
            if not args.confirm_requeue:
                parser.error("--requeue requires --confirm-requeue")
            if not args.reason:
                parser.error("--requeue requires --reason")
            if not (args.reset_attempts or args.preserve_attempts):
                parser.error(
                    "--requeue requires --reset-attempts or --preserve-attempts"
                )
            changed = requeue(
                args.db,
                args.requeue,
                reason=args.reason,
                reset_attempt_count=bool(args.reset_attempts),
                confirmed=True,
            )
            print(json.dumps({"ok": changed, "event_id": args.requeue}))
            return 0 if changed else 3

        rows = list_dead_letters(
            args.db, limit=args.limit, event_type=args.event_type
        )
        print(json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False))
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
