"""Reconcile worker — main entry point dla systemd timer.

Orchestration:
  1. Load flags (hot-reload check)
  2. Detect phantoms/ghosts (phantom_detector)
  3. Auto-resync (gated by AUTO_RESYNC_ENABLED + age + hard_cap)
  4. Log structured records (reconcile_log)
  5. Telegram alert gdy critical/manual review needed (gated)

CLI:
  python -m dispatch_v2.reconciliation.reconcile_worker [--dry-run] [--lookback-days N]

Exit codes:
  0 — clean run (zero discrepancies OR all auto-resynced OK)
  1 — error w worker (exception)
  2 — alert level (manual review needed, hard_cap hit)

Z3:
  Self-contained — minimal dependencies on external modules.
  Defensive — wszystkie failures isolated, partial success > full crash.
  Idempotent — multiple runs same input = same output (kdy nic się nie zmieniło).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Module imports
from dispatch_v2.common import flag, setup_logger
from dispatch_v2 import event_bus, state_machine
from dispatch_v2.reconciliation import phantom_detector, auto_resync, reconcile_log

# Config paths
ORDERS_STATE_PATH = Path("/root/.openclaw/workspace/dispatch_state/orders_state.json")
EVENTS_DB_PATH = Path("/root/.openclaw/workspace/dispatch_state/events.db")
LOG_FILE = "/root/.openclaw/workspace/scripts/logs/reconcile.log"

# Flag defaults
FLAG_DEFAULTS = {
    "RECONCILIATION_ENABLED": False,
    "RECONCILIATION_AUTO_RESYNC_ENABLED": False,
    "RECONCILIATION_INTERVAL_MIN": 30,
    "RECONCILIATION_AUTO_AGE_THRESHOLD_HOURS": 4,
    "RECONCILIATION_HARD_CAP_PER_RUN": 5,
    "RECONCILIATION_TELEGRAM_ALERT_ENABLED": False,
    "RECONCILIATION_LOOKBACK_DAYS": 30,
}

_log = setup_logger("reconciliation", LOG_FILE)


def _resolve_bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "") or os.environ.get("BOT_TOKEN", "")


def _resolve_alert_chat_id() -> Optional[int]:
    raw = os.environ.get("RECONCILIATION_ALERT_CHAT_ID") or os.environ.get("CZASOWKA_ALERT_CHAT_ID")
    if not raw:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _tg_send(text: str) -> bool:
    """Best-effort Telegram alert. Failure NIE crashes worker."""
    token = _resolve_bot_token()
    chat_id = _resolve_alert_chat_id()
    if not token or chat_id is None:
        _log.warning("Telegram alert SKIPPED — no token or chat_id")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            r = json.loads(body)
            if r.get("ok"):
                _log.info(f"Telegram alert sent chat={chat_id}")
                return True
            _log.warning(f"Telegram alert failed: {r.get('description')}")
            return False
    except Exception as e:
        _log.warning(f"Telegram alert exception: {type(e).__name__}: {e}")
        return False


def _format_alert(counts: Dict[str, Any], actions: list) -> str:
    """Compact alert message (mobile-friendly)."""
    lines = ["🔄 Reconciliation alert"]
    if counts.get("hard_cap_hit"):
        lines.append(f"⚠️ HARD_CAP_HIT — {counts.get('phantoms_total')} phantoms, cap exceeded")
        lines.append("Manual review wymagany. Auto-resync STOPPED.")
    else:
        lines.append(
            f"phantoms={counts.get('phantoms_total', 0)} "
            f"ghosts={counts.get('ghosts_total', 0)} "
            f"resynced={counts.get('auto_resyncs', 0)} "
            f"alerts={counts.get('alerts_only_young', 0)}"
        )
    # Sample non-resync actions
    interesting = [a for a in actions if a.get("action", "").startswith("alert_only") or a.get("classification") == "GHOST"]
    if interesting:
        lines.append("Manual review:")
        for a in interesting[:5]:
            oid = a.get("order_id")
            cls = a.get("classification")
            age = a.get("last_event_age_h", 0)
            lines.append(f"  {cls} oid={oid} age={age:.1f}h")
        if len(interesting) > 5:
            lines.append(f"  ... +{len(interesting)-5} more (see reconcile_log)")
    return "\n".join(lines)


def _load_orders_state() -> Dict[str, Dict[str, Any]]:
    """Load orders_state.json. Defensive: returns {} on failure."""
    try:
        with open(ORDERS_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        _log.warning(f"orders_state.json not found at {ORDERS_STATE_PATH}")
        return {}
    except json.JSONDecodeError as e:
        _log.error(f"orders_state.json parse fail: {e}")
        return {}
    except Exception as e:
        _log.error(f"orders_state.json read fail: {type(e).__name__}: {e}")
        return {}


def run(
    dry_run: bool = False,
    lookback_days_override: Optional[int] = None,
    hard_cap_override: Optional[int] = None,
) -> Dict[str, Any]:
    """Main run — orchestration. Returns summary dict.

    dry_run: jeśli True, NIE emituje events ani nie alerts. Tylko detekcja + log.
             Alternatywnie dla AUTO_RESYNC_ENABLED=false flag (alert-only mode).
    """
    if not flag("RECONCILIATION_ENABLED", default=FLAG_DEFAULTS["RECONCILIATION_ENABLED"]):
        _log.debug("RECONCILIATION_ENABLED=False — no-op")
        return {"status": "disabled", "skipped": True}

    auto_enabled = flag(
        "RECONCILIATION_AUTO_RESYNC_ENABLED",
        default=FLAG_DEFAULTS["RECONCILIATION_AUTO_RESYNC_ENABLED"],
    )
    age_threshold_h = flag(
        "RECONCILIATION_AUTO_AGE_THRESHOLD_HOURS",
        default=FLAG_DEFAULTS["RECONCILIATION_AUTO_AGE_THRESHOLD_HOURS"],
    )
    hard_cap = hard_cap_override or flag(
        "RECONCILIATION_HARD_CAP_PER_RUN",
        default=FLAG_DEFAULTS["RECONCILIATION_HARD_CAP_PER_RUN"],
    )
    lookback_days = lookback_days_override or flag(
        "RECONCILIATION_LOOKBACK_DAYS",
        default=FLAG_DEFAULTS["RECONCILIATION_LOOKBACK_DAYS"],
    )
    tg_alert_enabled = flag(
        "RECONCILIATION_TELEGRAM_ALERT_ENABLED",
        default=FLAG_DEFAULTS["RECONCILIATION_TELEGRAM_ALERT_ENABLED"],
    )

    # Effective dry_run: CLI flag OR auto disabled
    effective_dry = dry_run or (not auto_enabled)

    run_id = reconcile_log.make_run_id()
    _log.info(
        f"START run_id={run_id} dry_run={effective_dry} lookback_days={lookback_days} "
        f"age_threshold_h={age_threshold_h} hard_cap={hard_cap}"
    )

    # 1. Detect
    orders_state = _load_orders_state()
    discrepancies = phantom_detector.detect_all(
        events_db_path=str(EVENTS_DB_PATH),
        orders_state=orders_state,
        since_days=lookback_days,
    )
    _log.info(f"DETECT discrepancies={len(discrepancies)}")

    # 2. Auto-resync (gated)
    result = auto_resync.auto_resync_phantoms(
        discrepancies=discrepancies,
        emit_fn=event_bus.emit,
        state_update_fn=state_machine.update_from_event,
        age_threshold_hours=age_threshold_h,
        hard_cap_per_run=hard_cap,
        dry_run=effective_dry,
    )

    # 3. Log
    records = reconcile_log.build_records(
        actions=result["actions"],
        run_id=run_id,
        counts=result["counts"],
    )
    written = reconcile_log.append_records(records)
    _log.info(f"LOG written={written}")

    counts = result["counts"]
    total_alerts = counts.get("alerts_only_young", 0) + counts.get("alerts_only_hard_cap", 0)
    _log.info(
        f"END run_id={run_id} phantoms={counts['phantoms_total']} "
        f"ghosts={counts['ghosts_total']} resynced={counts['auto_resyncs']} "
        f"alerts={total_alerts} hard_cap_hit={counts['hard_cap_hit']}"
    )

    # 4. Telegram alert (gated, conditional)
    should_alert = (
        tg_alert_enabled
        and (
            counts["hard_cap_hit"]
            or total_alerts >= 5
            or counts["ghosts_total"] > 0
        )
    )
    if should_alert:
        msg = _format_alert(counts, result["actions"])
        _tg_send(msg)

    # Determine exit code
    status = "ok"
    if counts["hard_cap_hit"]:
        status = "critical"
    elif counts["ghosts_total"] > 0 or total_alerts > 0:
        status = "alert"

    return {
        "status": status,
        "run_id": run_id,
        "counts": counts,
        "log_records_written": written,
        "dry_run_effective": effective_dry,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Force dry-run (no emit, no alert)")
    parser.add_argument("--lookback-days", type=int, default=None, help="Override RECONCILIATION_LOOKBACK_DAYS flag")
    parser.add_argument("--hard-cap", type=int, default=None, help="Override RECONCILIATION_HARD_CAP_PER_RUN flag")
    args = parser.parse_args()

    try:
        result = run(
            dry_run=args.dry_run,
            lookback_days_override=args.lookback_days,
            hard_cap_override=args.hard_cap,
        )
    except Exception as e:
        _log.error(f"WORKER FATAL: {type(e).__name__}: {e}", exc_info=True)
        return 1

    if result.get("status") == "critical":
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
