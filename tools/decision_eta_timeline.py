"""Rotation-aware timeline ETA jednego zlecenia z live_eta_history.jsonl.

Wyświetla wyłącznie identyfikatory techniczne i czasy; nie czyta ani nie
renderuje nazw kurierów, adresów lub współrzędnych.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from dispatch_v2 import live_eta_history
from dispatch_v2.tools import _rotated_logs


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def timeline(
    *,
    order_id: str,
    log_path: str | Path = live_eta_history.LOG_PATH,
    kind: str | None = None,
    since: datetime | None = None,
    include_observers: bool = False,
    limit: int = 200,
) -> list[dict]:
    """Zwróć chronologiczne zdarzenia ETA ze wszystkich rotacji logu."""
    oid = str(order_id)
    events: list[dict] = []
    for record in _rotated_logs.iter_jsonl_records(str(log_path), since):
        if str(record.get("order_id")) != oid:
            continue
        role = str(record.get("writer_role") or "legacy")
        if role == "observer" and not include_observers:
            continue
        if record.get("schema") != live_eta_history.SCHEMA:
            continue
        candidates = [{
            "timestamp": record.get("generated_at") or record.get("recorded_at"),
            "value": record.get("eta_at"),
            "kind": record.get("stop_kind"),
            "plan_version": record.get("plan_version"),
            "sequence_hash": record.get("sequence_hash"),
            "writer": record.get("writer"),
            "writer_role": role,
            "stop_id": record.get("stop_id"),
            "record_kind": "live_eta_cycle",
        }]
        for event in candidates:
            timestamp = _as_utc(event.get("timestamp"))
            if since is not None and (timestamp is None or timestamp < since):
                continue
            if kind is not None and event.get("kind") != kind:
                continue
            events.append(event)
    events.sort(key=lambda event: event.get("timestamp") or "")
    return events[-max(1, int(limit)):]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Historia ETA jednego zlecenia")
    parser.add_argument("--order-id", required=True)
    parser.add_argument("--log", default=str(live_eta_history.LOG_PATH))
    parser.add_argument("--kind", choices=("pickup", "dropoff"))
    parser.add_argument("--since", help="ISO UTC; domyślnie cała retencja")
    parser.add_argument("--include-observers", action="store_true")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    since = _as_utc(args.since) if args.since else None
    if args.since and since is None:
        parser.error("--since must be an ISO timestamp")
    events = timeline(
        order_id=args.order_id,
        log_path=args.log,
        kind=args.kind,
        since=since,
        include_observers=args.include_observers,
        limit=args.limit,
    )
    if args.json:
        print(json.dumps(events, ensure_ascii=False, sort_keys=True))
    else:
        for event in events:
            digest = str(event.get("sequence_hash") or "-")[:12]
            print(
                f"{event.get('timestamp') or '-'} | {event.get('kind') or '-'} | "
                f"{event.get('value') or '-'} | plan={event.get('plan_version')} | "
                f"seq={digest} | {event.get('writer') or '-'} "
                f"({event.get('writer_role') or '-'})"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
