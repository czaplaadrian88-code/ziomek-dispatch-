"""Cron health watchdog — periodic stale check + Telegram alert.

Master plan TOP-15 #4. Triggered by systemd timer:

    # /etc/systemd/system/dispatch-watchdog.timer
    [Timer]
    OnUnitActiveSec=4h
    OnBootSec=15min
    Persistent=true

Runs co 4h. Dla każdego unitu z `cron_health.json`:
    - Jeśli is_stale(unit, threshold) → STALE alert
    - Skip jeśli dedup window aktywne (30min)
    - Skip long_running units (continuous, NIE cron-style)

Plus może bootstrappować units z _UNIT_METADATA (alert_onfailure) jeśli
brakują w cron_health.json — zapewnia że pierwszy run rejestruje state.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from dispatch_v2.observability import cron_health
from dispatch_v2.observability.alert_onfailure import (
    _UNIT_METADATA,
    _send_telegram,
)


def _format_stale_alert(unit: str, hours_silent: float, threshold_h: float) -> str:
    """Build STALE alert message (Polish-friendly per feedback rule)."""
    meta = _UNIT_METADATA.get(unit, {})
    sev = meta.get("severity", "P2")
    sev_emoji = "🔴" if sev == "P1" else "🟡" if sev == "P2" else "🟢"
    playbook = meta.get("playbook", "(brak runbooka)")
    hint = meta.get("hint", "manual diagnose")

    return f"""{sev_emoji} {sev} STALE — {unit} milczy {hours_silent:.1f}h (próg {threshold_h:.1f}h)

🔧 Service: {unit}
🤐 Cisza: {hours_silent:.1f} godzin (oczekiwane <={threshold_h:.1f}h)
🕐 Status: cron timer nie odpalał lub nie zaktualizował success-ledger

📚 Runbook: {playbook}

💡 Co robić:
- systemctl status {unit}
- systemctl list-timers {unit.replace('.service', '.timer')}
- {hint}"""


def run_once(
    now: datetime | None = None,
    path = None,
) -> dict[str, int]:
    """Single watchdog pass. Returns counters: {checked, stale, alerted, dedup_skipped}.

    path: optional override for cron_health.json location (default = production path).
    """
    now_dt = now or datetime.now(timezone.utc)
    health_path = path or cron_health.CRON_HEALTH_PATH
    health = cron_health.load_health(health_path)

    counters = {
        "checked": 0,
        "stale": 0,
        "alerted": 0,
        "dedup_skipped": 0,
    }

    # Iterate registered units
    for unit, entry in list(health["units"].items()):
        unit_type = entry.get("type", "cron_timer")
        if unit_type == "long_running":
            continue
        counters["checked"] += 1

        threshold = entry.get("expected_max_silence_h")
        # Fallback do _UNIT_METADATA jeśli entry nie ma threshold
        if threshold is None:
            meta = _UNIT_METADATA.get(unit, {})
            threshold = meta.get("expected_max_silence_h")
        if threshold is None:
            continue  # Unknown threshold → skip

        if cron_health.is_stale(unit, expected_max_silence_h=threshold, now=now_dt, path=health_path):
            counters["stale"] += 1

            if cron_health.is_alert_dedup_active(unit, dedup_window_min=30, now=now_dt, path=health_path):
                counters["dedup_skipped"] += 1
                continue

            # Compute hours silent dla messaging
            last_success_str = entry.get("last_success")
            if last_success_str:
                try:
                    last_dt = datetime.fromisoformat(last_success_str)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    hours_silent = (now_dt - last_dt).total_seconds() / 3600.0
                except (ValueError, TypeError):
                    hours_silent = -1
            else:
                hours_silent = -1

            text = _format_stale_alert(unit, hours_silent, threshold)
            sent = _send_telegram(text)
            if sent:
                counters["alerted"] += 1
                try:
                    cron_health.record_alert_sent(unit, path=health_path)
                except Exception as e:
                    print(f"[watchdog] record_alert_sent failed: {e}", file=sys.stderr)

    return counters


def main(argv: list[str] | None = None) -> int:
    counters = run_once()
    print(
        f"[watchdog] checked={counters['checked']} stale={counters['stale']} "
        f"alerted={counters['alerted']} dedup_skipped={counters['dedup_skipped']}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
