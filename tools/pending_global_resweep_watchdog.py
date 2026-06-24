"""pending_global_resweep_watchdog — strażnik przeglądu GO/NO-GO.

Odpala się jednorazowo ~15 min PO `dispatch-pending-resweep-review` (one-shot 26.06
07:00 UTC). Sprawdza przez systemd, czy przegląd FAKTYCZNIE się odpalił dziś i z
sukcesem. CICHO gdy OK (raport GO/NO-GO już poszedł osobno). Telegram TYLKO gdy NIE
odpalił lub błąd — zamienia dwuznaczną ciszę w jawny sygnał „coś nie zadziałało".

Komplementarny do `OnFailure=dispatch-onfailure-alert` (ten łapie crash; watchdog
łapie też „timer w ogóle nie ruszył"). Read-only.

Uruchomienie: python -m dispatch_v2.tools.pending_global_resweep_watchdog
"""
from __future__ import annotations
import sys
import subprocess
from datetime import datetime, timezone
from typing import Optional

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

REVIEW_UNIT = "dispatch-pending-resweep-review.service"


def evaluate(exec_status: str, exit_ts: str, result: str, today_ymd: str) -> Optional[str]:
    """Czysta logika — zwraca komunikat alertu albo None (cisza = OK).

    exec_status: ExecMainStatus (kod wyjścia main; '0' = sukces).
    exit_ts:     ExecMainExitTimestamp (np. 'Fri 2026-06-26 07:00:03 UTC' lub '' gdy nie ruszył).
    result:      Result ('success' | 'failed' | ...).
    today_ymd:   'YYYY-MM-DD' (UTC) dnia watchdoga.
    """
    ran_today = bool(exit_ts) and (today_ymd in exit_ts)
    ok = (str(exec_status).strip() == "0") and (str(result).strip() == "success")
    if ran_today and ok:
        return None  # cisza — przegląd odpalił z sukcesem, raport poszedł osobno
    if not ran_today:
        return (f"⚠ Przegląd GO/NO-GO pending_resweep NIE odpalił dziś ({today_ymd}) — "
                f"timer dispatch-pending-resweep-review mógł nie ruszyć. "
                f"Ręcznie: systemctl start {REVIEW_UNIT}")
    return (f"⚠ Przegląd GO/NO-GO pending_resweep zakończył się BŁĘDEM "
            f"(status={exec_status}, result={result}). Sprawdź log "
            f"logs/pending_global_resweep_review.log")


def _systemd_prop(unit: str, prop: str) -> str:
    try:
        out = subprocess.run(
            ["systemctl", "show", unit, "-p", prop, "--value"],
            capture_output=True, text=True, timeout=15)
        return (out.stdout or "").strip()
    except Exception:  # noqa: BLE001 — watchdog nie może się wywrócić
        return ""


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    exec_status = _systemd_prop(REVIEW_UNIT, "ExecMainStatus")
    exit_ts = _systemd_prop(REVIEW_UNIT, "ExecMainExitTimestamp")
    result = _systemd_prop(REVIEW_UNIT, "Result")
    alert = evaluate(exec_status, exit_ts, result, today)
    print(f"watchdog: status={exec_status!r} exit_ts={exit_ts!r} result={result!r} "
          f"today={today} -> {'ALERT' if alert else 'OK (cicho)'}")
    if alert:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(alert, source="pending_resweep_watchdog", priority="high")
        except Exception as e:  # noqa: BLE001
            print(f"telegram send fail: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
