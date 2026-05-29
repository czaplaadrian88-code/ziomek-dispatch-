"""OnFailure systemd handler — Telegram alert dla failed dispatch-* unit.

Master plan TOP-15 #4. Triggered by systemd OnFailure= directive:

    [Unit]
    OnFailure=dispatch-onfailure-alert@%n.service

    # /etc/systemd/system/dispatch-onfailure-alert@.service
    [Service]
    Type=oneshot
    ExecStart=/root/.openclaw/venvs/dispatch/bin/python -m \\
        dispatch_v2.observability.alert_onfailure %i

%i = unit name with .service suffix (e.g. dispatch-shadow.service).

8 alert fields (per master plan §B):
    Service / Result / Exit / LastSuccess / Logs / Playbook / Severity / Hint

Defensive:
- Dedup 30-min cooldown per unit (no spam tej samej awarii)
- Records do cron_health.record_run_failure() przed Telegram send
- try/except wokół Telegram — zawsze record state nawet jeśli alert fails
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone

from dispatch_v2.observability import cron_health


# Per-unit metadata: severity + playbook + hint
# Master plan: w przyszłości migrate do declarative config dispatch_state/cron_health_config.json
_UNIT_METADATA = {
    "dispatch-shadow.service": {
        "severity": "P1",
        "playbook": "dispatch_v2/CLAUDE.md → 'NIE restartuj systemd bez py_compile'",
        "hint": "journalctl -u dispatch-shadow -n 50; sprawdź ortools warm-up + login refresh",
        "type": "long_running",
    },
    "dispatch-panel-watcher.service": {
        "severity": "P1",
        "playbook": "panel_watcher → reconcile loop / packs fallback",
        "hint": "journalctl -u dispatch-panel-watcher -n 50; HTTP 419? CSRF expiry",
        "type": "long_running",
    },
    "dispatch-telegram.service": {
        "severity": "P1",
        "playbook": "Adrian MANDATORY ACK przed restart",
        "hint": "NIE restartuj automatycznie; sprawdź pending callbacks: ls /root/.openclaw/workspace/dispatch_state/pending_proposals.json",
        "type": "long_running",
    },
    "dispatch-overrides-reset.service": {
        "severity": "P1",
        "playbook": "manual_overrides_daily_reset → daily 06:00 Warsaw",
        "hint": "Lekcja META top-1: 4-day silent leak 03-07.05. Sprawdź czy timer enabled + last fire.",
        "type": "cron_timer",
        "expected_max_silence_h": 25.0,
    },
    "dispatch-r04-evaluator.service": {
        "severity": "P2",
        "playbook": "R-04 v2.0 GRADUATION — daily 03:00 Warsaw",
        "hint": "tier_suggestions.json staleness check; Step 2 obs window 14.05",
        "type": "cron_timer",
        "expected_max_silence_h": 25.0,
    },
    "dispatch-shift-notify.service": {
        "severity": "P2",
        "playbook": "TASK B Phase 0+1 — 1-min oneshot worker",
        "hint": "Worker reads flags fresh per tick; flag flip = natychmiastowy efekt bez restart",
        "type": "cron_timer",
        "expected_max_silence_h": 0.1,
    },
    "dispatch-czasowka.service": {
        "severity": "P1",
        "playbook": "czasowka_scheduler → 1-min triggers (T-60/T-50/T-40)",
        "hint": "dispatchable_fleet vs build_fleet_snapshot consumer (Sprint A 06.05)",
        "type": "cron_timer",
        "expected_max_silence_h": 0.1,
    },
    "dispatch-cod-weekly-preflight.service": {
        "severity": "P2",
        "playbook": "F2.1d COD Weekly — Mon 08:00 Warsaw",
        "hint": "Telegram reminder re-enable post 11.05",
        "type": "cron_timer",
        "expected_max_silence_h": 168.0,
    },
    "dispatch-daily-accounting.service": {
        "severity": "P2",
        "playbook": "V3.25 Daily Accounting — Tue/Wed/Thu/Fri/Mon 06:00 Warsaw",
        "hint": "gspread API; sheets.googleapis.com reachable?",
        "type": "cron_timer",
        "expected_max_silence_h": 96.0,
    },
    "dispatch-event-bus-cleanup.service": {
        "severity": "P3",
        "playbook": "events.db cleanup — 04:00 UTC",
        "hint": "MP-#5 peak-aware cleanup gate (W1 task)",
        "type": "cron_timer",
        "expected_max_silence_h": 25.0,
    },
    "dispatch-faza7-kpi.service": {
        "severity": "P2",
        "playbook": "G2 Faza 7 daily KPI — 04:00 UTC=06:00 Warsaw. ExecStartPre regeneruje backfill (read-only), ExecStart liczy bramki + digest Telegram.",
        "hint": "exit 2 = brak backfillu (ExecStartPre padł — sprawdź backfill_decisions_outcomes). Zamarznięta linia 'Enriched GT' w digescie = martwy shadow-enricher.",
        "type": "cron_timer",
        "expected_max_silence_h": 25.0,
    },
    "dispatch-state-reconcile.service": {
        "severity": "P2",
        "playbook": "Reconciliation worker — periodic ghost detection",
        "hint": "ghosts_total > 0? sprawdź recent COURIER_ASSIGNED events",
        "type": "cron_timer",
        "expected_max_silence_h": 1.0,
    },
    "dispatch-plan-recheck.service": {
        "severity": "P2",
        "playbook": "V3.19c plan_recheck — 5-min ticks",
        "hint": "courier_plans.json + plan_recheck_log.jsonl",
        "type": "cron_timer",
        "expected_max_silence_h": 0.2,
    },
    "dispatch-monitor-419.service": {
        "severity": "P3",
        "playbook": "Panel client HTTP 419 monitoring",
        "hint": "CSRF expiry; rare event",
        "type": "cron_timer",
        "expected_max_silence_h": 24.0,
    },
    "dispatch-sla-tracker.service": {
        "severity": "P2",
        "playbook": "sla_tracker — R6 BAG_TIME alerts (currently suppressed via flag)",
        "hint": "sprawdź flags ENABLE_BAG_TIME_ALERTS",
        "type": "long_running",
    },
    "dispatch-gps.service": {
        "severity": "P2",
        "playbook": "courier-api FastAPI :8767",
        "hint": "PWA disabled fallback :8766; check /api/health",
        "type": "long_running",
    },
}


def _journal_tail(unit: str, n: int = 20) -> str:
    """Get last N lines z journalctl dla unit."""
    try:
        result = subprocess.run(
            ["journalctl", "-u", unit, "-n", str(n), "--no-pager"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return f"(journalctl failed: {result.returncode})"
        return result.stdout.strip() or "(no logs)"
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return f"(journalctl error: {e})"


def _systemctl_status(unit: str) -> tuple[str, int | None]:
    """Get unit status (Result, ExitStatus) via systemctl show."""
    try:
        result = subprocess.run(
            ["systemctl", "show", unit, "-p", "Result,ExecMainStatus", "--value"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return ("unknown", None)
        lines = result.stdout.strip().split("\n")
        # systemctl show -p X,Y --value zwraca w kolejności jak w -p (Result, ExecMainStatus)
        result_str = lines[0] if len(lines) > 0 else "unknown"
        exit_str = lines[1] if len(lines) > 1 else "0"
        try:
            exit_code = int(exit_str)
        except ValueError:
            exit_code = None
        return (result_str.strip() or "unknown", exit_code)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ("unknown", None)


def _format_alert(unit: str) -> str:
    """Build 8-field alert message dla Adrian (PL-friendly per feedback rule)."""
    meta = _UNIT_METADATA.get(unit, {
        "severity": "P2",
        "playbook": "(brak runbooka — dodaj do _UNIT_METADATA)",
        "hint": "journalctl -u {unit} -n 50; manual diagnose",
        "type": "unknown",
    })

    result, exit_code = _systemctl_status(unit)
    health = cron_health.load_health()
    entry = health["units"].get(unit, {})
    last_success = entry.get("last_success") or "nigdy nie było"
    consecutive = entry.get("consecutive_failures", 0)

    logs = _journal_tail(unit, n=15)

    sev = meta["severity"]
    sev_emoji = "🔴" if sev == "P1" else "🟡" if sev == "P2" else "🟢"

    msg = f"""{sev_emoji} {sev} — Awaria serwisu {unit}

🔧 Service: {unit}
📛 Result: {result}
🔢 Exit: {exit_code if exit_code is not None else 'unknown'}
🕐 Ostatni sukces: {last_success}
🔁 Consecutive failures: {consecutive}

📜 Ostatnie logi (15 linii):
```
{logs[-1500:]}
```

📚 Runbook: {meta['playbook']}

💡 Co robić: {meta['hint']}"""
    return msg


def _send_telegram(text: str) -> bool:
    """Defensive Telegram send via existing utility. Never raises."""
    try:
        from dispatch_v2 import telegram_utils
        return telegram_utils.send_admin_alert(text)
    except Exception as e:
        print(f"[alert_onfailure] Telegram send failed: {type(e).__name__}: {e}", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    """Entry point for systemd ExecStart=python -m dispatch_v2.observability.alert_onfailure %i."""
    args = argv or sys.argv[1:]
    if not args:
        print("Usage: alert_onfailure <unit_name>", file=sys.stderr)
        return 2

    unit = args[0].strip()
    if not unit:
        print("Empty unit name", file=sys.stderr)
        return 2

    # Get metadata
    meta = _UNIT_METADATA.get(unit, {})
    unit_type = meta.get("type", "cron_timer")
    expected_max_silence_h = meta.get("expected_max_silence_h")

    # Record failure (always)
    result_str, exit_code = _systemctl_status(unit)
    try:
        cron_health.record_run_failure(
            unit,
            result=result_str,
            exit_code=exit_code,
            unit_type=unit_type,
        )
    except Exception as e:
        print(f"[alert_onfailure] cron_health record failed: {e}", file=sys.stderr)

    # Dedup check
    if cron_health.is_alert_dedup_active(unit, dedup_window_min=30):
        print(f"[alert_onfailure] {unit}: dedup window active (<30min), skip alert", file=sys.stderr)
        return 0

    # Format + send
    text = _format_alert(unit)
    sent = _send_telegram(text)

    if sent:
        try:
            cron_health.record_alert_sent(unit)
        except Exception as e:
            print(f"[alert_onfailure] record_alert_sent failed: {e}", file=sys.stderr)

    print(f"[alert_onfailure] {unit}: result={result_str} exit={exit_code} sent={sent}", file=sys.stderr)
    return 0 if sent else 1


if __name__ == "__main__":
    sys.exit(main())
