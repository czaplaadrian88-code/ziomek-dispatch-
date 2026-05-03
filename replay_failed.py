"""V3.28 Fix 4 (incident 03.05.2026): failed event replay infrastructure.

CLI tool dla replay events z status='failed' w events.db. Use case:
- Verify że post-fix code path absorbuje pre-fix crash (np. Fix 2 SetRange OOD)
- Audit historical failures dla pattern analysis
- Recovery: --apply flip status=processed dla pass cases (atomic UPDATE)

Cardinal rule 10 (atomic writes): --apply UPDATE w sqlite IMMEDIATE transaction
z rollback on exception.

Cardinal rule 4 (evidence preservation): default --offline (read-only).
--apply requires explicit flag.

Usage:
    # Single oid replay (default offline)
    python3 -m dispatch_v2.replay_failed --oid 470208

    # Batch replay since date (offline)
    python3 -m dispatch_v2.replay_failed --status failed --since "2026-04-01"

    # Apply (flip status=processed dla PASS cases)
    python3 -m dispatch_v2.replay_failed --status failed --since "2026-04-01" --apply

    # JSON output
    python3 -m dispatch_v2.replay_failed --oid 470208 --output /tmp/replay.json
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("replay_failed")
if not log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


DEFAULT_EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"


def query_failed_events(
    db_path: str,
    oid: Optional[str] = None,
    status: str = "failed",
    since: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Query events.db dla failed events. Returns list of dict rows.

    Filters:
    - oid: single order_id (overrides status/since)
    - status: filter status (default 'failed')
    - since: ISO timestamp lower bound dla created_at
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        if oid:
            cur.execute(
                "SELECT event_id, event_type, order_id, courier_id, payload, "
                "created_at, processed_at, status FROM events WHERE order_id=?",
                (oid,),
            )
        else:
            params: List[Any] = [status]
            sql = (
                "SELECT event_id, event_type, order_id, courier_id, payload, "
                "created_at, processed_at, status FROM events WHERE status=?"
            )
            if since:
                sql += " AND created_at >= ?"
                params.append(since)
            sql += " ORDER BY created_at ASC"
            cur.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]
        return rows
    finally:
        conn.close()


def replay_event(row: Dict[str, Any], offline: bool = True) -> Dict[str, Any]:
    """Replay single event row przez current dispatch_v2 code.

    offline=True: read-only, ZERO side effects (recommended default).
    offline=False: caller (apply_status_flip) handles atomic DB update.

    Returns dict z polami:
    - event_id, order_id, event_type, original_status
    - verdict: 'PASS' | 'FAIL: <error_class>: <error_msg>' | 'SKIP: <reason>'
    - result: PipelineResult dict (gdy PASS) lub None
    """
    eid = row.get("event_id", "?")
    oid = row.get("order_id", "?")
    event_type = row.get("event_type", "?")
    original_status = row.get("status", "?")

    out: Dict[str, Any] = {
        "event_id": eid,
        "order_id": oid,
        "event_type": event_type,
        "original_status": original_status,
        "verdict": None,
        "result": None,
    }

    # Tylko NEW_ORDER replay supported (assess_order is the entry point)
    if event_type != "NEW_ORDER":
        out["verdict"] = f"SKIP: unsupported_event_type={event_type}"
        return out

    try:
        payload_raw = row.get("payload")
        if not payload_raw:
            out["verdict"] = "SKIP: empty_payload"
            return out
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        # Add order_id do payload (assess_order wymaga w order_event dict)
        if "order_id" not in payload:
            payload["order_id"] = oid

        # Build current fleet snapshot
        from dispatch_v2.courier_resolver import build_fleet_snapshot
        fleet = build_fleet_snapshot()

        # Process via assess_order (current code path = post-fix)
        from dispatch_v2 import dispatch_pipeline
        result = dispatch_pipeline.assess_order(
            order_event=payload,
            fleet_snapshot=fleet,
            restaurant_meta=None,
            now=datetime.now(timezone.utc),
        )
        # Convert PipelineResult dataclass do dict (best-effort)
        result_dict = {
            "verdict": getattr(result, "verdict", None),
            "reason": getattr(result, "reason", None),
            "best_courier_id": (
                getattr(result.best, "courier_id", None) if getattr(result, "best", None) else None
            ),
            "candidates_count": len(getattr(result, "candidates", []) or []),
        }
        out["verdict"] = "PASS"
        out["result"] = result_dict
    except Exception as e:
        out["verdict"] = f"FAIL: {type(e).__name__}: {str(e)[:200]}"
        out["traceback"] = traceback.format_exc()[:1000]

    return out


def apply_status_flip(
    db_path: str,
    pass_event_ids: List[str],
) -> Dict[str, Any]:
    """Atomic UPDATE events status='processed' dla PASS event_ids.

    Cardinal rule 10: IMMEDIATE transaction, rollback on exception.
    Returns stats {flipped: N, errors: [...]}.
    """
    if not pass_event_ids:
        return {"flipped": 0, "errors": []}

    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level="IMMEDIATE")
    cur = conn.cursor()
    flipped = 0
    errors: List[str] = []
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        for eid in pass_event_ids:
            try:
                cur.execute(
                    "UPDATE events SET status='processed', processed_at=? "
                    "WHERE event_id=? AND status='failed'",
                    (now_iso, eid),
                )
                flipped += cur.rowcount
            except Exception as e:
                errors.append(f"{eid}: {type(e).__name__}: {e}")
        conn.commit()
    except Exception as e:
        conn.rollback()
        errors.append(f"transaction_rollback: {type(e).__name__}: {e}")
        return {"flipped": 0, "errors": errors}
    finally:
        conn.close()

    return {"flipped": flipped, "errors": errors}


def replay_batch(
    db_path: str = DEFAULT_EVENTS_DB,
    oid: Optional[str] = None,
    status: str = "failed",
    since: Optional[str] = None,
    apply: bool = False,
) -> Dict[str, Any]:
    """High-level batch replay z optional --apply.

    Returns summary dict: total, pass, fail, skip, results, applied_stats.
    """
    rows = query_failed_events(db_path, oid=oid, status=status, since=since)
    log.info(f"replay_batch: query returned {len(rows)} events")

    results: List[Dict[str, Any]] = []
    pass_event_ids: List[str] = []
    pass_count = 0
    fail_count = 0
    skip_count = 0

    for row in rows:
        r = replay_event(row, offline=True)
        results.append(r)
        if r["verdict"] == "PASS":
            pass_count += 1
            pass_event_ids.append(r["event_id"])
        elif r["verdict"].startswith("FAIL"):
            fail_count += 1
        elif r["verdict"].startswith("SKIP"):
            skip_count += 1

    summary = {
        "total": len(rows),
        "pass": pass_count,
        "fail": fail_count,
        "skip": skip_count,
        "pass_rate_pct": (pass_count / len(rows) * 100.0) if rows else 0.0,
        "results": results,
    }

    if apply and pass_event_ids:
        log.info(f"replay_batch: --apply flipping {len(pass_event_ids)} PASS event_ids")
        apply_stats = apply_status_flip(db_path, pass_event_ids)
        summary["applied"] = apply_stats
    else:
        summary["applied"] = None

    return summary


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="replay_failed",
        description="V3.28 Fix 4: failed event replay tool",
    )
    ap.add_argument("--oid", help="Single order_id replay (overrides status/since)")
    ap.add_argument("--status", default="failed", help="Filter status (default 'failed')")
    ap.add_argument("--since", help="ISO timestamp lower bound for created_at")
    ap.add_argument(
        "--apply", action="store_true",
        help="Atomic flip status=processed dla PASS cases (default OFF)",
    )
    ap.add_argument("--db", default=DEFAULT_EVENTS_DB, help="events.db path")
    ap.add_argument("--output", help="Write JSON summary to file")
    args = ap.parse_args()

    if not Path(args.db).exists():
        log.error(f"events.db NOT FOUND: {args.db}")
        return 2

    summary = replay_batch(
        db_path=args.db,
        oid=args.oid,
        status=args.status,
        since=args.since,
        apply=args.apply,
    )

    log.info(
        f"REPLAY_SUMMARY total={summary['total']} pass={summary['pass']} "
        f"fail={summary['fail']} skip={summary['skip']} "
        f"pass_rate={summary['pass_rate_pct']:.1f}%"
    )
    if summary.get("applied"):
        log.info(f"REPLAY_APPLIED flipped={summary['applied']['flipped']} errors={summary['applied']['errors']}")

    if args.output:
        with open(args.output, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        log.info(f"REPLAY_OUTPUT written to {args.output}")
    else:
        # Print summary z first 5 results (avoid spam dla batch>>1)
        print(json.dumps({
            "total": summary["total"],
            "pass": summary["pass"],
            "fail": summary["fail"],
            "skip": summary["skip"],
            "pass_rate_pct": summary["pass_rate_pct"],
            "applied": summary.get("applied"),
            "results_first_5": summary["results"][:5],
        }, indent=2, default=str))

    return 0


if __name__ == "__main__":
    sys.exit(main())
