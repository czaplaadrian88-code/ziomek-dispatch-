"""Bezpieczne narzedzie inspekcji i jawnego requeue DLQ (Z-P0-05 Faza A).

Default jest read-only. Requeue wymaga lacznie:

* ``--requeue EVENT_ID``;
* ``--reason`` (slad audytowy);
* jawnego wyboru ``--reset-attempts`` albo ``--preserve-attempts``;
* ``--confirm-requeue``.

Narzędzie zachowuje event ID i payload w bazie, ale listing zwraca tylko
ponownie hashowany digest korelacyjny i zamkniete metadata. Nie wykonuje eventu
inline -- po przyszlym wlaczeniu consumera event przejdzie zwykla sciezke.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from dispatch_v2 import event_outbox, event_retry
from dispatch_v2.order_fsm import (
    FORMAL_FSM_EVENT_TYPES,
    NON_STATE_EVENT_TYPES,
    validate_order_event,
)


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
            """SELECT event_id, event_type, attempt_count,
                      failure_class, error_code, replay_count,
                      last_replay_reason, idempotency_key
               FROM events WHERE status=?"""
            + type_clause
            + " ORDER BY dead_lettered_at ASC, created_at ASC, event_id ASC LIMIT ?",
            tuple(params),
        ).fetchall()
        result = []
        known_event_types = FORMAL_FSM_EVENT_TYPES | NON_STATE_EVENT_TYPES
        for row in rows:
            raw_event_id = row["event_id"]
            descriptor = event_retry.normalize_failure_metadata(
                row["failure_class"], row["error_code"]
            )
            try:
                attempt_count = max(0, int(row["attempt_count"] or 0))
            except (TypeError, ValueError):
                attempt_count = 0
            try:
                replay_count = max(0, int(row["replay_count"] or 0))
            except (TypeError, ValueError):
                replay_count = 0
            event_name = str(row["event_type"] or "")
            result.append({
                "event_ref": event_retry.event_reference(
                    raw_event_id,
                    stored_idempotency_key=row["idempotency_key"],
                ),
                "event_type": (
                    event_name if event_name in known_event_types else "unknown"
                ),
                "attempt_count": attempt_count,
                "failure_class": descriptor.failure_class.value,
                "error_code": descriptor.error_code,
                "replay_count": replay_count,
                "last_replay_reason": event_retry.sanitize_replay_reason(
                    row["last_replay_reason"]
                ),
            })
        return result
    finally:
        conn.close()


def list_durable_failures(
    db_path: str,
    *,
    limit: int = 100,
    consumer_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Read-only inspekcja E1; retry/requeue pozostaja celowo poza zakresem."""
    conn = sqlite3.connect(
        _db_uri(db_path, mode="ro"), uri=True, timeout=5.0
    )
    try:
        return event_outbox.list_failure_journal(
            conn,
            limit=limit,
            consumer_id=consumer_id,
        )
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
    current_state: Optional[dict[str, Any]] = None,
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
        row = conn.execute(
            """SELECT event_id,event_type,order_id,courier_id,payload,created_at
               FROM events WHERE event_id=? AND status=?""",
            (event_id, event_retry.DEAD_LETTER_STATUS),
        ).fetchone()
        if row is None:
            return False
        event_type = str(row["event_type"])
        if (
            event_type not in FORMAL_FSM_EVENT_TYPES
            and event_type not in NON_STATE_EVENT_TYPES
        ):
            raise ValueError("DLQ event type is not supported for replay")
        if event_type in FORMAL_FSM_EVENT_TYPES:
            if current_state is None:
                raise ValueError("current state snapshot is required for FSM replay")
            try:
                payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError) as exc:
                raise ValueError("DLQ payload is not valid JSON") from exc
            event = {
                "event_id": row["event_id"],
                "event_type": event_type,
                "order_id": row["order_id"],
                "courier_id": row["courier_id"],
                "payload": payload,
                "created_at": row["created_at"],
            }
            current_order = current_state.get(str(row["order_id"]))
            verdict = validate_order_event(event, current=current_order)
            if verdict.would_reject:
                raise ValueError("DLQ event is not legal in current FSM state")
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
    parser.add_argument(
        "--durable-failures",
        action="store_true",
        help="read-only lista durable failure journal; bez requeue",
    )
    parser.add_argument(
        "--consumer-id",
        help="opcjonalny filtr consumera dla --durable-failures",
    )
    parser.add_argument(
        "--state",
        help="jawny read-only orders_state snapshot wymagany dla --requeue",
    )
    parser.add_argument(
        "--policy-options",
        action="store_true",
        help="pokaz wersjonowane opcje polityki; niczego nie wybiera",
    )
    parser.add_argument("--requeue", metavar="EVENT_ID")
    parser.add_argument(
        "--reason",
        choices=sorted(event_retry.SAFE_REPLAY_REASON_CODES),
        help="zamkniety kod powodu replayu",
    )
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--reset-attempts", action="store_true")
    choice.add_argument("--preserve-attempts", action="store_true")
    parser.add_argument("--confirm-requeue", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.policy_options:
            print(json.dumps({
                "selected_policy_id": event_retry.SELECTED_RETRY_POLICY_ID,
                "automatic_retry_enabled": event_retry.AUTOMATIC_RETRY_ENABLED,
                "options": event_retry.policy_options_summary(),
            }, ensure_ascii=False))
            return 0
        if args.durable_failures:
            if args.requeue or args.policy_options or args.event_type:
                parser.error(
                    "--durable-failures nie laczy sie z requeue/policy/event-type"
                )
            rows = list_durable_failures(
                args.db,
                limit=args.limit,
                consumer_id=args.consumer_id,
            )
            print(json.dumps({
                "count": len(rows),
                "items": rows,
                "requeue_enabled": False,
            }, ensure_ascii=False))
            return 0
        if args.consumer_id:
            parser.error("--consumer-id wymaga --durable-failures")
        if args.requeue:
            if not args.confirm_requeue:
                parser.error("--requeue requires --confirm-requeue")
            if not args.reason:
                parser.error("--requeue requires --reason")
            if not args.state:
                parser.error("--requeue requires --state")
            if not (args.reset_attempts or args.preserve_attempts):
                parser.error(
                    "--requeue requires --reset-attempts or --preserve-attempts"
                )
            state = json.loads(Path(args.state).read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise ValueError("state snapshot must be a JSON object")
            changed = requeue(
                args.db,
                args.requeue,
                reason=args.reason,
                reset_attempt_count=bool(args.reset_attempts),
                confirmed=True,
                current_state=state,
            )
            print(json.dumps({
                "ok": changed,
                "event_ref": event_retry.event_reference(args.requeue),
            }))
            return 0 if changed else 3

        rows = list_dead_letters(
            args.db, limit=args.limit, event_type=args.event_type
        )
        print(json.dumps({"count": len(rows), "items": rows}, ensure_ascii=False))
        return 0
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        descriptor = event_retry.classify_failure(exc)
        print(json.dumps({
            "ok": False,
            "error_class": descriptor.failure_class.value,
            "error_code": descriptor.error_code,
        }, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    sys.exit(main())
