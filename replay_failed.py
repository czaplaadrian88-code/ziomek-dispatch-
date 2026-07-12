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
    python3 -m dispatch_v2.replay_failed --oid synthetic-order

    # Batch replay since date (offline)
    python3 -m dispatch_v2.replay_failed --status failed --since "2026-04-01"

    # Apply (flip status=processed dla PASS cases)
    python3 -m dispatch_v2.replay_failed --status failed --since "2026-04-01" --apply

    # JSON output
    python3 -m dispatch_v2.replay_failed --oid synthetic-order --output /tmp/replay.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import secrets
import sqlite3
import stat
import sys
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dispatch_v2 import event_retry

log = logging.getLogger("replay_failed")
if not log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    log.addHandler(h)
    log.setLevel(logging.INFO)


DEFAULT_EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"


class _NonStoringTextSink:
    """Minimalny sink kompatybilny z print; niczego nie buforuje."""

    encoding = "utf-8"
    errors = "replace"

    @property
    def buffer(self):
        return self

    def write(self, value) -> int:
        return len(value)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False


@contextmanager
def _suppress_replay_channels():
    """Wasko wycisza logging/stdout/stderr zaleznosci replayu i je przywraca."""
    previous_disable = logging.root.manager.disable
    sink = _NonStoringTextSink()
    logging.disable(logging.CRITICAL)
    try:
        with redirect_stdout(sink), redirect_stderr(sink):
            yield
    finally:
        logging.disable(previous_disable)


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

    Zwraca tylko zredagowany wynik: digest korelacyjny, outcome, zamknieta
    klasa/kod bledu i agregat liczby kandydatow. Surowe identyfikatory, payload,
    reason, exception text i traceback nigdy nie opuszczaja tej funkcji.
    """
    del offline
    eid = row.get("event_id", "<missing>")
    oid = row.get("order_id")
    event_type = row.get("event_type")

    out: Dict[str, Any] = {
        "event_ref": event_retry.event_reference(eid),
        "outcome": None,
        "error_class": None,
        "error_code": None,
        "candidates_count": 0,
    }

    # Tylko NEW_ORDER replay supported (assess_order is the entry point)
    if event_type != "NEW_ORDER":
        out["outcome"] = "skip"
        out["error_code"] = "unsupported_event_type"
        return out

    try:
        payload_raw = row.get("payload")
        if not payload_raw:
            out["outcome"] = "skip"
            out["error_class"] = "permanent"
            out["error_code"] = "invalid_payload"
            return out
        payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
        if not isinstance(payload, dict):
            raise TypeError("event payload must be a mapping")
        # Add order_id do payload (assess_order wymaga w order_event dict)
        if "order_id" not in payload:
            payload["order_id"] = oid

        # Build current fleet snapshot.
        # Fix 2026-05-07 (mirror czasowka_scheduler:289 + Sprint A 06.05 commit 69223b3):
        # użyj dispatchable_fleet() — wzbogaca CourierState o shift_end z grafiku V3.24-A.
        # Raw build_fleet_snapshot() zostawia shift_end=None → feasibility_v2:300 Fail-CLOSED
        # hard-rejectuje wszystkich → debug tool kłamie pod post-mortem (artificial NO_CANDIDATE).
        with _suppress_replay_channels():
            from dispatch_v2 import courier_resolver
            fleet = {
                cs.courier_id: cs
                for cs in courier_resolver.dispatchable_fleet()
            }

            # Process via fasada core.decide (K09; delegacja 1:1 do assess_order)
            from dispatch_v2.core.decide import decide as _decide
            from dispatch_v2.core.world_state import WorldState
            result = _decide(
                WorldState(fleet_snapshot=fleet, now=datetime.now(timezone.utc)),
                payload,
            )
        out["outcome"] = "pass"
        out["candidates_count"] = len(getattr(result, "candidates", []) or [])
    except Exception as e:
        descriptor = event_retry.classify_failure(e)
        out["outcome"] = "fail"
        out["error_class"] = descriptor.failure_class.value
        out["error_code"] = descriptor.error_code

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

    conn = sqlite3.connect(db_path, timeout=10.0, isolation_level=None)
    errors: List[str] = []
    try:
        flipped = event_retry.mark_replays_processed(
            conn,
            pass_event_ids,
            processed_at=datetime.now(timezone.utc),
        )
    except Exception as e:
        errors.append(f"transaction_rollback:{type(e).__name__}")
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
        if r["outcome"] == "pass":
            pass_count += 1
            # Raw ID pozostaje tylko w pamieci i trafia wprost do status flip.
            pass_event_ids.append(str(row.get("event_id")))
        elif r["outcome"] == "fail":
            fail_count += 1
        elif r["outcome"] == "skip":
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


def _open_directory_componentwise(parent: Path) -> int:
    """Otwiera istniejacy parent komponentami dirfd; symlink/``..`` = blad."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise RuntimeError("secure output requires O_NOFOLLOW")
    nofollow = os.O_NOFOLLOW
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | nofollow
    parts = parent.parts
    if parent.is_absolute():
        current_fd = os.open("/", flags)
        components = parts[1:]
    else:
        current_fd = os.open(".", flags)
        components = parts
    try:
        for component in components:
            if component in {"", "."}:
                continue
            if component == "..":
                raise ValueError("output parent cannot contain '..'")
            next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except Exception:
        os.close(current_fd)
        raise


def write_secure_output(output_path: str, value: Dict[str, Any]) -> None:
    """Tworzy nowy JSON atomowo jako 0600, bez symlinkow w calej sciezce.

    Kazdy komponent parenta jest otwierany wzgledem poprzedniego ``dirfd`` z
    ``O_NOFOLLOW`` i musi juz istniec. Existing leaf (regular/symlink/hardlink)
    jest twardym bledem. Kompletny, fsyncowany plik tymczasowy jest publikowany
    pojedynczym hard-linkiem, po czym temp znika i final musi miec ``nlink=1``.
    """
    target = Path(output_path)
    if target.name in {"", ".", ".."}:
        raise ValueError("output must name a new regular file")
    parent = target.parent if str(target.parent) else Path(".")
    nofollow = os.O_NOFOLLOW
    directory_fd = _open_directory_componentwise(parent)
    temporary_name = f".{target.name}.tmp-{secrets.token_hex(8)}"
    temporary_created = False
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow
        fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
        temporary_created = True
        try:
            os.fchmod(fd, 0o600)
            encoded = json.dumps(
                value,
                indent=2,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
            with os.fdopen(fd, "wb", closefd=True) as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
        except Exception:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
        os.link(
            temporary_name,
            target.name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        try:
            os.unlink(temporary_name, dir_fd=directory_fd)
        except Exception:
            try:
                os.unlink(target.name, dir_fd=directory_fd)
            except OSError:
                pass
            raise
        temporary_created = False
        final_stat = os.stat(
            target.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if not stat.S_ISREG(final_stat.st_mode) or final_stat.st_nlink != 1:
            os.unlink(target.name, dir_fd=directory_fd)
            raise RuntimeError("secure output leaf invariant failed")
        os.fsync(directory_fd)
    finally:
        if temporary_created:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except OSError:
                pass
        os.close(directory_fd)


def main(argv: Optional[List[str]] = None) -> int:
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
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        log.error("REPLAY_DB_NOT_FOUND")
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
        try:
            write_secure_output(args.output, summary)
        except (OSError, ValueError, TypeError) as exc:
            log.error(
                "REPLAY_OUTPUT_ERROR "
                f"error_class={type(exc).__name__}"
            )
            return 2
        log.info("REPLAY_OUTPUT_WRITTEN")
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
