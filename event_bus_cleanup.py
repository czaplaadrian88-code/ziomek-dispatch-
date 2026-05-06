"""Opcja C (2026-05-07) — daily event_bus retention runner.

Wywołuje:
- event_bus.cleanup(retention_hours=48) — usuwa processed events + processed_events
- event_bus.cleanup_audit_log(retention_days=90) — usuwa stare audit_log entries

Run via systemd dispatch-event-bus-cleanup.timer (daily 04:00 UTC, off-peak).

CLI:
    python -m dispatch_v2.event_bus_cleanup            # production run
    python -m dispatch_v2.event_bus_cleanup --dry-run  # log counts, no DELETE
"""
import argparse
import sys

from dispatch_v2 import event_bus
from dispatch_v2.common import setup_logger

_log = setup_logger("event_bus_cleanup", "/root/.openclaw/workspace/scripts/logs/event_bus_cleanup.log")


def _dry_run_audit() -> int:
    """Liczy ile audit_log entries byłoby usuniętych przy retention=90d, bez DELETE."""
    with event_bus._conn() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_log WHERE created_at < datetime('now', '-90 days')"
        )
        return cur.fetchone()["cnt"]


def _dry_run_events() -> dict:
    """Liczy processed_events + events processed > 48h, bez DELETE."""
    with event_bus._conn() as conn:
        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM processed_events WHERE processed_at < datetime('now', '-48 hours')"
        )
        pe = cur.fetchone()["cnt"]
        cur = conn.execute(
            "SELECT COUNT(*) as cnt FROM events WHERE status='processed' AND processed_at < datetime('now', '-48 hours')"
        )
        ev = cur.fetchone()["cnt"]
        return {"processed_events": pe, "events_processed": ev}


def main() -> int:
    parser = argparse.ArgumentParser(description="Daily event_bus retention runner")
    parser.add_argument("--dry-run", action="store_true", help="log counts without DELETE")
    args = parser.parse_args()

    # Ensure audit_log table init w razie pierwszego prod run
    event_bus._ensure_audit_log_initialized()

    if args.dry_run:
        ev = _dry_run_events()
        au = _dry_run_audit()
        _log.info(
            f"DRY_RUN would_delete: processed_events={ev['processed_events']} "
            f"events_processed={ev['events_processed']} audit_log={au}"
        )
        return 0

    try:
        deleted_queue = event_bus.cleanup(retention_hours=48)
        deleted_audit = event_bus.cleanup_audit_log(retention_days=90)
        _log.info(
            f"DAILY_CLEANUP_DONE queue={deleted_queue} audit_log={deleted_audit}"
        )
        return 0
    except Exception as e:
        _log.error(f"DAILY_CLEANUP_FAIL: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
